-- R06 Large vs profile, MEDIAN variant (tuning experiment, step 7).
-- Same contract and parameters as rules/sql/R06.sql, but the baseline is the
-- median of the account's payments sent in the look-back window instead of
-- the mean. PostgreSQL has no median over a sliding window, so each payment
-- above the amount threshold looks back with a lateral subquery (index on
-- sender and time). Strictly earlier payments only, like exclude group.

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

large as (

    select transaction_id, sender_account as account_id, transaction_ts, amount_gbp
    from {fct}
    where amount_gbp > %(min_amount_gbp)s

)

select
    s.account_id,
    s.transaction_ts                                            as trigger_ts,
    s.transaction_ts - make_interval(days => %(baseline_days)s) as window_start,
    array[s.transaction_id]                                     as transaction_ids,
    jsonb_build_object(
        'amount_gbp', s.amount_gbp,
        'n_baseline', b.n_baseline,
        'baseline_avg_gbp', round(b.baseline_gbp::numeric, 2),
        'ratio', round(s.amount_gbp / b.baseline_gbp::numeric, 2),
        'first_seen', f.first_ts
    )                                                           as metrics
from large as s
join first_seen as f using (account_id)
cross join lateral (

    select
        count(*)                                                as n_baseline,
        percentile_cont(0.5) within group (order by p.amount_gbp) as baseline_gbp
    from {fct} as p
    where p.sender_account = s.account_id
      and p.transaction_ts >= s.transaction_ts - make_interval(days => %(baseline_days)s)
      and p.transaction_ts < s.transaction_ts

) as b
where b.n_baseline >= %(min_baseline_payments)s
  and s.amount_gbp > b.baseline_gbp::numeric * %(multiple)s
  and f.first_ts <= s.transaction_ts - make_interval(days => %(min_history_days)s)
