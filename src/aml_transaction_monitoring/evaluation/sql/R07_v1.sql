-- R07 Behaviour change, VERSION 1 (both clauses), kept as a tuning variant: the step 7 grid was generated from it.
-- Contract: see the header of R01.sql.
--
-- Each account's money in and money out as one stream; every transaction is
-- a possible trigger. One sorted pass gives, per transaction:
--   * the account's first transaction in the data (its history start);
--   * the previous transaction (for dormancy: no previous one means new);
--   * the volume over the recent window, and over the baseline period just
--     before it (window frames include both ends).
-- Only the candidates then look up the transactions behind the alert: the
-- recent window's for a spike (the look-up is skipped otherwise), the
-- trigger alone for a reactivation.

with flows as (

    select transaction_id, receiver_account as account_id, transaction_ts, amount_gbp
    from {fct}
    union all
    select transaction_id, sender_account, transaction_ts, amount_gbp
    from {fct}

),

rolling as (

    select
        transaction_id,
        account_id,
        transaction_ts,
        first_value(transaction_ts) over w  as first_ts,
        lag(transaction_ts) over w          as previous_ts,
        sum(amount_gbp) over (
            w range between make_interval(days => %(spike_days)s) preceding and current row
        )                                   as recent_gbp,
        count(*) over (
            w range between make_interval(days => %(spike_days)s) preceding and current row
        )                                   as recent_n,
        sum(amount_gbp) over (
            w range between make_interval(days => %(spike_days)s + %(baseline_days)s) preceding
                        and make_interval(days => %(spike_days)s) preceding
        )                                   as baseline_gbp
    from flows
    window w as (partition by account_id order by transaction_ts)

),

flagged as (

    select
        *,
        baseline_gbp * %(spike_days)s / %(baseline_days)s  as baseline_avg_gbp,
        coalesce(
            baseline_gbp > 0
            and recent_gbp > %(spike_multiple)s * baseline_gbp * %(spike_days)s / %(baseline_days)s
            and first_ts <= transaction_ts
                - make_interval(days => %(spike_days)s + %(baseline_days)s),
            false
        )                                                   as is_spike,
        coalesce(
            transaction_ts - previous_ts >= make_interval(days => %(dormant_days)s),
            false
        )                                                   as is_dormant
    from rolling
    where first_ts <= transaction_ts - make_interval(days => %(min_history_days)s)

),

candidates as (

    select
        *,
        floor(extract(epoch from transaction_ts - previous_ts) / 86400)::int as dormant_for_days
    from flagged
    where is_spike or is_dormant

)

select
    c.account_id,
    c.transaction_ts                                        as trigger_ts,
    least(
        case when c.is_spike
            then c.transaction_ts - make_interval(days => %(spike_days)s) end,
        case when c.is_dormant then c.previous_ts end
    )                                                       as window_start,
    case when c.is_spike then ids.transaction_ids
         else array[c.transaction_id] end                   as transaction_ids,
    jsonb_build_object(
        'is_spike', c.is_spike,
        'is_dormant', c.is_dormant,
        'recent_gbp', c.recent_gbp,
        'recent_n', c.recent_n,
        'baseline_avg_gbp', round(c.baseline_avg_gbp, 2),
        'dormant_for_days', case when c.is_dormant then c.dormant_for_days end,
        'finding', concat_ws('; ',
            case when c.is_spike then
                'volume of £' || to_char(c.recent_gbp, 'FM999,999,999,990.00')
                || ' in ' || c.recent_n || ' transactions, '
                || to_char(c.recent_gbp / c.baseline_avg_gbp, 'FM999,999,990.0')
                || ' times the previous average of £'
                || to_char(c.baseline_avg_gbp, 'FM999,999,999,990.00')
            end,
            case when c.is_dormant then
                'first activity after ' || c.dormant_for_days
                || ' days with none (last active ' || to_char(c.previous_ts, 'YYYY-MM-DD') || ')'
            end
        )
    )                                                       as metrics
from candidates as c
cross join lateral (

    select array_agg(f.transaction_id) as transaction_ids
    from (
        select transaction_id from {fct}
        where c.is_spike
          and receiver_account = c.account_id
          and transaction_ts >= c.transaction_ts - make_interval(days => %(spike_days)s)
          and transaction_ts <= c.transaction_ts
        union
        select transaction_id from {fct}
        where c.is_spike
          and sender_account = c.account_id
          and transaction_ts >= c.transaction_ts - make_interval(days => %(spike_days)s)
          and transaction_ts <= c.transaction_ts
    ) as f

) as ids
