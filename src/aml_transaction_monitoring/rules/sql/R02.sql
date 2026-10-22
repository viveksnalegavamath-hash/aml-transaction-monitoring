-- R02 Many-to-one. Spec and thresholds: rules/R02_many_to_one.yaml.
-- Contract: see the header of R01.sql.
--
-- PostgreSQL cannot count distinct values over a sliding window, so for each
-- incoming payment (the trigger) a lateral subquery looks back over the
-- receiving account's payments in the window. To keep that affordable, only
-- accounts that ever had enough distinct senders are examined.

with busy_accounts as (

    select receiver_account
    from {fct}
    group by receiver_account
    having count(distinct sender_account) >= %(min_senders)s

),

triggers as (

    select f.receiver_account as account_id, f.transaction_ts
    from {fct} as f
    join busy_accounts using (receiver_account)

)

select distinct on (t.account_id, t.transaction_ts)
    t.account_id,
    t.transaction_ts                                        as trigger_ts,
    t.transaction_ts - make_interval(days => %(window_days)s) as window_start,
    w.transaction_ids,
    jsonb_build_object(
        'n_senders', w.n_senders,
        'n_transactions', w.n_transactions,
        'total_gbp', w.total_gbp
    )                                                       as metrics
from triggers as t
cross join lateral (

    select
        count(distinct j.sender_account)    as n_senders,
        count(*)                            as n_transactions,
        sum(j.amount_gbp)                   as total_gbp,
        array_agg(j.transaction_id)         as transaction_ids
    from {fct} as j
    where j.receiver_account = t.account_id
      and j.transaction_ts >= t.transaction_ts - make_interval(days => %(window_days)s)
      and j.transaction_ts <= t.transaction_ts

) as w
where w.n_senders >= %(min_senders)s
  and w.total_gbp >= %(min_total_gbp)s
order by t.account_id, t.transaction_ts
