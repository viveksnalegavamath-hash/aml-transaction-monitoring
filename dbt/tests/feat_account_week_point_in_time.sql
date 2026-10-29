-- Point-in-time check of feat_account_week, by brute force. For a fixed
-- subset of accounts (md5 of the ID starting with '00': about 1 in 256),
-- every windowed feature is recomputed the slow, obvious way, from
-- transactions dated ON OR BEFORE the week's end only (and within the
-- window), and compared with the table. A feature that saw a later
-- transaction, or a window off by one day, shows up here. Any row returned
-- is a mismatch.
--
-- "New" counterparty = no contact with that counterparty before the window.
-- That depends only on contacts before the window, so the pair's first
-- contact ever can be used; likewise the account's first day.
-- Amounts are compared with a tolerance for the table's 4-byte floats
-- (about 7 significant digits); counts must match exactly.

with checked as (

    select * from {{ ref('feat_account_week') }}
    where left(md5(account_id), 2) = '00'

),

flows as (

    select
        receiver_account as account_id, sender_account as counterparty_id, true as is_in,
        transaction_date as day, amount_gbp, payment_type, is_cross_border,
        sender_country_internal_rating = 'high'
            or sender_country_fatf_status <> 'not_listed' as is_high_risk_country
    from {{ ref('fct_transactions') }}
    where left(md5(receiver_account), 2) = '00'
    union all
    select
        sender_account, receiver_account, false,
        transaction_date, amount_gbp, payment_type, is_cross_border,
        receiver_country_internal_rating = 'high'
            or receiver_country_fatf_status <> 'not_listed'
    from {{ ref('fct_transactions') }}
    where left(md5(sender_account), 2) = '00'

),

pair_first as (

    select account_id, counterparty_id, is_in, min(day) as first_contact
    from flows
    group by account_id, counterparty_id, is_in

),

account_first as (

    select account_id, min(day) as first_day
    from flows
    group by account_id

),

brute as (

    select
        c.account_id,
        c.week_end,
        w.n,
        {% for d, is_in in [('in', 'true'), ('out', 'false')] %}
        count(*) filter (where f.is_in = {{ is_in }})                          as {{ d }}_n,
        coalesce(sum(f.amount_gbp) filter (where f.is_in = {{ is_in }}), 0)    as {{ d }}_gbp,
        coalesce(max(f.amount_gbp) filter (where f.is_in = {{ is_in }}), 0)    as {{ d }}_max_gbp,
        count(distinct f.counterparty_id) filter (where f.is_in = {{ is_in }}) as {{ d }}_cp,
        count(distinct f.counterparty_id) filter (
            where f.is_in = {{ is_in }} and p.first_contact >= c.week_end - w.n + 1
        )                                                                       as {{ d }}_new_cp,
        coalesce(sum(f.amount_gbp) filter (
            where f.is_in = {{ is_in }} and f.payment_type in ('cash_deposit', 'cash_withdrawal')
        ), 0)                                                                   as {{ d }}_cash_gbp,
        coalesce(sum(f.amount_gbp) filter (where f.is_in = {{ is_in }} and f.is_cross_border), 0)
                                                                                as {{ d }}_cross_border_gbp,
        coalesce(sum(f.amount_gbp) filter (where f.is_in = {{ is_in }} and f.is_high_risk_country), 0)
                                                                                as {{ d }}_high_risk_gbp,
        {% endfor %}
        min(a.first_day)                                                        as first_day
    from checked as c
    cross join (values (7), (30), (90)) as w (n)
    join flows as f
      on f.account_id = c.account_id
     and f.day between c.week_end - w.n + 1 and c.week_end
    join pair_first as p
      on p.account_id = f.account_id
     and p.counterparty_id = f.counterparty_id
     and p.is_in = f.is_in
    join account_first as a
      on a.account_id = c.account_id
    group by c.account_id, c.week_end, w.n

)

{% for n in [7, 30, 90] %}
select t.account_id, t.week_end, {{ n }} as window_days
from checked as t
left join brute as b
  on b.account_id = t.account_id and b.week_end = t.week_end and b.n = {{ n }}
where b.account_id is null
   or t.history_days <> b.week_end - b.first_day
  {% for d in ['in', 'out'] %}
   or t.{{ d }}_n_{{ n }} <> b.{{ d }}_n
   or t.{{ d }}_counterparties_{{ n }} <> b.{{ d }}_cp
   or t.{{ d }}_new_counterparties_{{ n }} <> b.{{ d }}_new_cp
   or abs(t.{{ d }}_gbp_{{ n }} - b.{{ d }}_gbp) > 0.01 + 1e-6 * b.{{ d }}_gbp
   or abs(t.{{ d }}_max_gbp_{{ n }} - b.{{ d }}_max_gbp) > 0.01 + 1e-6 * b.{{ d }}_max_gbp
   or abs(t.{{ d }}_cash_share_{{ n }} - coalesce(b.{{ d }}_cash_gbp / nullif(b.{{ d }}_gbp, 0), 0)) > 1e-5
   or abs(t.{{ d }}_cross_border_share_{{ n }}
          - coalesce(b.{{ d }}_cross_border_gbp / nullif(b.{{ d }}_gbp, 0), 0)) > 1e-5
   or abs(t.{{ d }}_high_risk_share_{{ n }}
          - coalesce(b.{{ d }}_high_risk_gbp / nullif(b.{{ d }}_gbp, 0), 0)) > 1e-5
  {% endfor %}
{% if not loop.last %}
union all
{% endif %}
{% endfor %}
