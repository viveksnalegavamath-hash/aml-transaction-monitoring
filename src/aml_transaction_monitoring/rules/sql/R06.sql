-- R06 Large vs profile. Spec and thresholds: rules/R06_large_vs_profile.yaml.
-- Contract: see the header of R01.sql.
--
-- Per payment sent. One sorted pass over each sender's payments gives the
-- average of the payments sent in the look-back window. The frame excludes
-- the payment itself and any other payment at the same moment (exclude
-- group), so a large payment never dilutes its own baseline.
-- History starts at the account's first transaction in the data, sent or
-- received.

with first_seen as (

    select account_id, min(first_ts) as first_ts
    from (
        select sender_account as account_id, min(transaction_ts) as first_ts
        from {fct}
        group by sender_account
        union all
        select receiver_account, min(transaction_ts)
        from {fct}
        group by receiver_account
    ) as both_sides
    group by account_id

),

sent as (

    select
        transaction_id,
        sender_account          as account_id,
        transaction_ts,
        amount_gbp,
        count(*) over w         as n_baseline,
        avg(amount_gbp) over w  as baseline_avg_gbp
    from {fct}
    window w as (
        partition by sender_account
        order by transaction_ts
        range between make_interval(days => %(baseline_days)s) preceding and current row
        exclude group
    )

)

select
    s.account_id,
    s.transaction_ts                                            as trigger_ts,
    s.transaction_ts - make_interval(days => %(baseline_days)s) as window_start,
    array[s.transaction_id]                                     as transaction_ids,
    jsonb_build_object(
        'amount_gbp', s.amount_gbp,
        'n_baseline', s.n_baseline,
        'baseline_avg_gbp', round(s.baseline_avg_gbp, 2),
        'ratio', round(s.amount_gbp / s.baseline_avg_gbp, 2),
        'first_seen', f.first_ts
    )                                                           as metrics
from sent as s
join first_seen as f using (account_id)
where s.amount_gbp > %(min_amount_gbp)s
  and s.n_baseline >= %(min_baseline_payments)s
  and s.amount_gbp > s.baseline_avg_gbp * %(multiple)s
  and f.first_ts <= s.transaction_ts - make_interval(days => %(min_history_days)s)
