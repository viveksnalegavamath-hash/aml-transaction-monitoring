-- R07 Behaviour change. Spec and thresholds: rules/R07_behaviour_change.yaml.
-- Contract: see the header of R01.sql.
--
-- Version 2 (tuned 25 Oct 2026): spike clause only. The dormancy clause of
-- version 1 was removed after tuning showed it worse than random (see the
-- change log).
--
-- Each account's money in and money out as one stream; every transaction is
-- a possible trigger. One sorted pass gives, per transaction:
--   * the account's first transaction in the data (its history start);
--   * the volume over the recent window, and over the baseline period just
--     before it (window frames include both ends).
-- Only the candidates then look up the transactions behind the alert (the
-- recent window's).

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

candidates as (

    select
        *,
        baseline_gbp * %(spike_days)s / %(baseline_days)s  as baseline_avg_gbp
    from rolling
    where first_ts <= transaction_ts - make_interval(days => %(min_history_days)s)
      and first_ts <= transaction_ts - make_interval(days => %(spike_days)s + %(baseline_days)s)
      and baseline_gbp > 0
      and recent_gbp >= %(min_volume_gbp)s
      and recent_gbp > %(spike_multiple)s * baseline_gbp * %(spike_days)s / %(baseline_days)s

)

select
    c.account_id,
    c.transaction_ts                                        as trigger_ts,
    c.transaction_ts - make_interval(days => %(spike_days)s) as window_start,
    ids.transaction_ids,
    jsonb_build_object(
        'recent_gbp', c.recent_gbp,
        'recent_n', c.recent_n,
        'baseline_avg_gbp', round(c.baseline_avg_gbp, 2),
        'ratio', round(c.recent_gbp / c.baseline_avg_gbp, 1)
    )                                                       as metrics
from candidates as c
cross join lateral (

    select array_agg(f.transaction_id) as transaction_ids
    from (
        select transaction_id from {fct}
        where receiver_account = c.account_id
          and transaction_ts >= c.transaction_ts - make_interval(days => %(spike_days)s)
          and transaction_ts <= c.transaction_ts
        union
        select transaction_id from {fct}
        where sender_account = c.account_id
          and transaction_ts >= c.transaction_ts - make_interval(days => %(spike_days)s)
          and transaction_ts <= c.transaction_ts
    ) as f

) as ids
