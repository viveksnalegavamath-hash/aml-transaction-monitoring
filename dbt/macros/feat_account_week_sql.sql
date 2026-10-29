{#
    The SELECT behind features.feat_account_week, for one chunk of accounts
    (see the model for what the features are). Every window is per account,
    so accounts can be built in separate chunks: each chunk is its own
    statement, and PostgreSQL frees its temporary sort files before the next
    one. Built in one statement, the full data needed an estimated 14 GB of
    temporary disk; in 8 chunks, about an eighth of that.
#}
{% macro account_chunk(account_column, chunks) -%}
    mod(('x' || right(md5({{ account_column }}), 8))::bit(32)::bigint, {{ chunks }})
{%- endmacro %}

{% macro feat_account_week_sql(chunk, chunks) %}
-- not materialized: read twice below; otherwise PostgreSQL would first copy
-- the whole transactions table to temporary disk.
with transactions as not materialized (

    select * from {{ ref('fct_transactions') }}

),

customers as (

    select customer_id from {{ ref('dim_customer') }}

),

-- Each transaction twice: from the receiver's side (money in) and from the
-- sender's side (money out).
flows as (

    select
        receiver_account                                        as account_id,
        sender_account                                          as counterparty_id,
        true                                                    as is_in,
        transaction_date                                        as day,
        round(amount_gbp * 100)::bigint                         as pence,
        payment_type in ('cash_deposit', 'cash_withdrawal')     as is_cash,
        is_cross_border,
        sender_country_internal_rating = 'high'
            or sender_country_fatf_status <> 'not_listed'       as is_high_risk_country,
        false                                                   as artefact_to_customer
    from transactions
    where {{ account_chunk('receiver_account', chunks) }} = {{ chunk }}

    union all

    select
        sender_account,
        receiver_account,
        false,
        transaction_date,
        round(amount_gbp * 100)::bigint,
        payment_type in ('cash_deposit', 'cash_withdrawal'),
        is_cross_border,
        receiver_country_internal_rating = 'high'
            or receiver_country_fatf_status <> 'not_listed',
        receiver_is_customer
    from transactions
    where {{ account_chunk('sender_account', chunks) }} = {{ chunk }}

),

-- One row per account and day with money in or out.
activity as (

    select
        account_id,
        day,
        count(*) filter (where is_in)                                           as in_n,
        coalesce(sum(pence) filter (where is_in), 0)                            as in_pence,
        max(pence) filter (where is_in)                                         as in_max_pence,
        coalesce(sum(pence) filter (where is_in and is_cash), 0)                as in_cash_pence,
        coalesce(sum(pence) filter (where is_in and is_cross_border), 0)        as in_cross_border_pence,
        coalesce(sum(pence) filter (where is_in and is_high_risk_country), 0)   as in_high_risk_pence,
        count(*) filter (where not is_in)                                       as out_n,
        coalesce(sum(pence) filter (where not is_in), 0)                        as out_pence,
        max(pence) filter (where not is_in)                                     as out_max_pence,
        coalesce(sum(pence) filter (where not is_in and is_cash), 0)            as out_cash_pence,
        coalesce(sum(pence) filter (where not is_in and is_cross_border), 0)    as out_cross_border_pence,
        coalesce(sum(pence) filter (where not is_in and is_high_risk_country), 0)
                                                                                as out_high_risk_pence,
        count(*) filter (where not is_in and artefact_to_customer)              as artefact_out_to_customer_n
    from flows
    group by account_id, day

),

-- One row per account, counterparty, direction and day of contact, with the
-- pair's previous and next contact day.
contacts as (

    select
        account_id,
        is_in,
        day,
        lag(day) over pair      as previous_day,
        lead(day) over pair     as next_day
    from (select distinct account_id, counterparty_id, is_in, day from flows) as d
    window pair as (partition by account_id, counterparty_id, is_in order by day)

),

-- Distinct counterparties over an n-day window, kept as running totals of
-- +1 / -1 changes (count(distinct) cannot slide along a window). A contact on
-- day d keeps the pair inside the window for window ends d to d + n - 1, so:
--   +1 on day d if the pair was not already inside (no contact in the n days
--      before: previous_day is null or d - previous_day >= n);
--   -1 on day d + n if no later contact keeps it inside (next_day is null or
--      next_day - d >= n).
-- New counterparties: pairs whose first contact is on that day.
entries as (

    select
        account_id,
        day,
        count(*) filter (where is_in and previous_day is null)                          as in_new_cp,
        count(*) filter (where not is_in and previous_day is null)                      as out_new_cp,
        count(*) filter (where is_in and (previous_day is null or day - previous_day >= 7))
                                                                                        as in_cp_7_change,
        count(*) filter (where is_in and (previous_day is null or day - previous_day >= 30))
                                                                                        as in_cp_30_change,
        count(*) filter (where is_in and (previous_day is null or day - previous_day >= 90))
                                                                                        as in_cp_90_change,
        count(*) filter (where not is_in and (previous_day is null or day - previous_day >= 7))
                                                                                        as out_cp_7_change,
        count(*) filter (where not is_in and (previous_day is null or day - previous_day >= 30))
                                                                                        as out_cp_30_change,
        count(*) filter (where not is_in and (previous_day is null or day - previous_day >= 90))
                                                                                        as out_cp_90_change
    from contacts
    group by account_id, day

),

exits as (

    select
        c.account_id,
        c.day + w.n                                                     as day,
        -count(*) filter (where c.is_in and w.n = 7)                    as in_cp_7_change,
        -count(*) filter (where c.is_in and w.n = 30)                   as in_cp_30_change,
        -count(*) filter (where c.is_in and w.n = 90)                   as in_cp_90_change,
        -count(*) filter (where not c.is_in and w.n = 7)                as out_cp_7_change,
        -count(*) filter (where not c.is_in and w.n = 30)               as out_cp_30_change,
        -count(*) filter (where not c.is_in and w.n = 90)               as out_cp_90_change
    from contacts as c
    cross join (values (7), (30), (90)) as w (n)
    where c.next_day is null or c.next_day - c.day >= w.n
    group by c.account_id, c.day + w.n

),

-- The scoring points: the last day of every week in which the account was active.
weeks as (

    select distinct account_id, {{ week_end('day') }} as day
    from activity

),

-- Every dated row the windows need: activity days (with their counterparty
-- entries), exit days and scoring points. Rows on the same day are peers in
-- a range window, so a scoring point sees that day's activity.
series as (

    select
        a.account_id, a.day, false as is_week_end,
        a.in_n, a.in_pence, a.in_max_pence, a.in_cash_pence, a.in_cross_border_pence,
        a.in_high_risk_pence, a.out_n, a.out_pence, a.out_max_pence, a.out_cash_pence,
        a.out_cross_border_pence, a.out_high_risk_pence, a.artefact_out_to_customer_n,
        e.in_new_cp, e.out_new_cp,
        e.in_cp_7_change, e.in_cp_30_change, e.in_cp_90_change,
        e.out_cp_7_change, e.out_cp_30_change, e.out_cp_90_change
    from activity as a
    join entries as e using (account_id, day)

    union all

    select
        account_id, day, false,
        0, 0, null, 0, 0, 0, 0, 0, null, 0, 0, 0, 0,
        0, 0,
        in_cp_7_change, in_cp_30_change, in_cp_90_change,
        out_cp_7_change, out_cp_30_change, out_cp_90_change
    from exits

    union all

    select
        account_id, day, true,
        0, 0, null, 0, 0, 0, 0, 0, null, 0, 0, 0, 0,
        0, 0,
        0, 0, 0,
        0, 0, 0
    from weeks

),

rolling as (

    select
        account_id,
        day                                         as week_end,
        is_week_end,
        first_value(day) over account               as first_day,

        sum(in_n) over last_7                       as in_n_7,
        sum(in_n) over last_30                      as in_n_30,
        sum(in_n) over last_90                      as in_n_90,
        sum(in_pence) over last_7                   as in_pence_7,
        sum(in_pence) over last_30                  as in_pence_30,
        sum(in_pence) over last_90                  as in_pence_90,
        max(in_max_pence) over last_7               as in_max_pence_7,
        max(in_max_pence) over last_30              as in_max_pence_30,
        max(in_max_pence) over last_90              as in_max_pence_90,
        sum(in_cash_pence) over last_7              as in_cash_pence_7,
        sum(in_cash_pence) over last_30             as in_cash_pence_30,
        sum(in_cash_pence) over last_90             as in_cash_pence_90,
        sum(in_cross_border_pence) over last_7      as in_cross_border_pence_7,
        sum(in_cross_border_pence) over last_30     as in_cross_border_pence_30,
        sum(in_cross_border_pence) over last_90     as in_cross_border_pence_90,
        sum(in_high_risk_pence) over last_7         as in_high_risk_pence_7,
        sum(in_high_risk_pence) over last_30        as in_high_risk_pence_30,
        sum(in_high_risk_pence) over last_90        as in_high_risk_pence_90,
        sum(in_new_cp) over last_7                  as in_new_cp_7,
        sum(in_new_cp) over last_30                 as in_new_cp_30,
        sum(in_new_cp) over last_90                 as in_new_cp_90,
        sum(in_cp_7_change) over so_far             as in_cp_7,
        sum(in_cp_30_change) over so_far            as in_cp_30,
        sum(in_cp_90_change) over so_far            as in_cp_90,

        sum(out_n) over last_7                      as out_n_7,
        sum(out_n) over last_30                     as out_n_30,
        sum(out_n) over last_90                     as out_n_90,
        sum(out_pence) over last_7                  as out_pence_7,
        sum(out_pence) over last_30                 as out_pence_30,
        sum(out_pence) over last_90                 as out_pence_90,
        max(out_max_pence) over last_7              as out_max_pence_7,
        max(out_max_pence) over last_30             as out_max_pence_30,
        max(out_max_pence) over last_90             as out_max_pence_90,
        sum(out_cash_pence) over last_7             as out_cash_pence_7,
        sum(out_cash_pence) over last_30            as out_cash_pence_30,
        sum(out_cash_pence) over last_90            as out_cash_pence_90,
        sum(out_cross_border_pence) over last_7     as out_cross_border_pence_7,
        sum(out_cross_border_pence) over last_30    as out_cross_border_pence_30,
        sum(out_cross_border_pence) over last_90    as out_cross_border_pence_90,
        sum(out_high_risk_pence) over last_7        as out_high_risk_pence_7,
        sum(out_high_risk_pence) over last_30       as out_high_risk_pence_30,
        sum(out_high_risk_pence) over last_90       as out_high_risk_pence_90,
        sum(out_new_cp) over last_7                 as out_new_cp_7,
        sum(out_new_cp) over last_30                as out_new_cp_30,
        sum(out_new_cp) over last_90                as out_new_cp_90,
        sum(out_cp_7_change) over so_far            as out_cp_7,
        sum(out_cp_30_change) over so_far           as out_cp_30,
        sum(out_cp_90_change) over so_far           as out_cp_90,

        sum(artefact_out_to_customer_n) over last_30 as artefact_out_to_customer_n_30
    from series
    window
        account as (partition by account_id order by day),
        last_7  as (account range between interval '6 days' preceding and current row),
        last_30 as (account range between interval '29 days' preceding and current row),
        last_90 as (account range between interval '89 days' preceding and current row),
        so_far  as (account range between unbounded preceding and current row)

),

scoring_points as (

    select * from rolling where is_week_end

)

select
    p.account_id,
    p.week_end,

    -- Account: days since its first transaction in the data (the data starts
    -- on 7 Oct 2022, so early weeks understate it for every account).
    (p.week_end - p.first_day)::real                                    as history_days,

    -- Money in, per window: count, total, largest payment, distinct and new
    -- counterparties, and shares of the total that were cash, cross-border,
    -- or from a high-risk country.
    {% for n in [7, 30, 90] %}
    p.in_n_{{ n }}::real                                                as in_n_{{ n }},
    (p.in_pence_{{ n }} / 100.0)::real                                  as in_gbp_{{ n }},
    (coalesce(p.in_max_pence_{{ n }}, 0) / 100.0)::real                 as in_max_gbp_{{ n }},
    p.in_cp_{{ n }}::real                                               as in_counterparties_{{ n }},
    p.in_new_cp_{{ n }}::real                                           as in_new_counterparties_{{ n }},
    coalesce(p.in_cash_pence_{{ n }}::float8 / nullif(p.in_pence_{{ n }}, 0), 0)::real
                                                                        as in_cash_share_{{ n }},
    coalesce(p.in_cross_border_pence_{{ n }}::float8 / nullif(p.in_pence_{{ n }}, 0), 0)::real
                                                                        as in_cross_border_share_{{ n }},
    coalesce(p.in_high_risk_pence_{{ n }}::float8 / nullif(p.in_pence_{{ n }}, 0), 0)::real
                                                                        as in_high_risk_share_{{ n }},
    {% endfor %}

    -- Money out, the same measures.
    {% for n in [7, 30, 90] %}
    p.out_n_{{ n }}::real                                               as out_n_{{ n }},
    (p.out_pence_{{ n }} / 100.0)::real                                 as out_gbp_{{ n }},
    (coalesce(p.out_max_pence_{{ n }}, 0) / 100.0)::real                as out_max_gbp_{{ n }},
    p.out_cp_{{ n }}::real                                              as out_counterparties_{{ n }},
    p.out_new_cp_{{ n }}::real                                          as out_new_counterparties_{{ n }},
    coalesce(p.out_cash_pence_{{ n }}::float8 / nullif(p.out_pence_{{ n }}, 0), 0)::real
                                                                        as out_cash_share_{{ n }},
    coalesce(p.out_cross_border_pence_{{ n }}::float8 / nullif(p.out_pence_{{ n }}, 0), 0)::real
                                                                        as out_cross_border_share_{{ n }},
    coalesce(p.out_high_risk_pence_{{ n }}::float8 / nullif(p.out_pence_{{ n }}, 0), 0)::real
                                                                        as out_high_risk_share_{{ n }},
    -- Share of all money moved that went out (about 0.5 = money passes through).
    (p.out_pence_{{ n }}::float8 / (p.in_pence_{{ n }} + p.out_pence_{{ n }}))::real
                                                                        as out_share_{{ n }},
    {% endfor %}

    -- Against the account's own history, as natural logs of ratios (0 = as
    -- usual, 1 = about 2.7 times usual). "Usual" = the rest of the 90-day
    -- window, days 8 to 90 (83 days): the weekly average total, and the
    -- average single payment. Plus one pound on both sides, so an empty
    -- history gives a finite value.
    {% for d in ['in', 'out'] %}
    ln((1 + p.{{ d }}_pence_7 / 100.0)
       / (1 + (p.{{ d }}_pence_90 - p.{{ d }}_pence_7) / 100.0 * 7 / 83))::real
                                                                        as {{ d }}_gbp_7_vs_history,
    ln((1 + coalesce(p.{{ d }}_max_pence_7, 0) / 100.0)
       / (1 + coalesce((p.{{ d }}_pence_90 - p.{{ d }}_pence_7) / 100.0
                       / nullif(p.{{ d }}_n_90 - p.{{ d }}_n_7, 0), 0)))::real
                                                                        as {{ d }}_max_vs_history,
    {% endfor %}

    -- NOT features (see the header): the SAML-D "receiver is a customer" artefact.
    (c.customer_id is not null)                                         as artefact_is_customer,
    coalesce(p.artefact_out_to_customer_n_30::float8 / nullif(p.out_n_30, 0), 0)::real
                                                                        as artefact_out_to_customer_share_30
from scoring_points as p
left join customers as c
    on c.customer_id = p.account_id
{% endmacro %}
