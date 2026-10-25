-- R03 One-to-many. Spec and thresholds: rules/R03_one_to_many.yaml.
-- Contract: see the header of R01.sql.
--
-- The mirror image of R02: for each outgoing payment (the trigger) a lateral
-- subquery looks back over the sending account's payments in the window and
-- counts distinct receivers. Only accounts that ever paid enough distinct
-- receivers are examined.

with busy_accounts as (

    select sender_account
    from {fct}
    group by sender_account
    having count(distinct receiver_account) >= %(min_receivers)s

),

triggers as (

    select f.sender_account as account_id, f.transaction_ts
    from {fct} as f
    join busy_accounts using (sender_account)

)

select distinct on (t.account_id, t.transaction_ts)
    t.account_id,
    t.transaction_ts                                        as trigger_ts,
    t.transaction_ts - make_interval(days => %(window_days)s) as window_start,
    w.transaction_ids,
    jsonb_build_object(
        'n_receivers', w.n_receivers,
        'n_transactions', w.n_transactions,
        'total_gbp', w.total_gbp
    )                                                       as metrics
from triggers as t
cross join lateral (

    select
        count(distinct j.receiver_account)  as n_receivers,
        count(*)                            as n_transactions,
        sum(j.amount_gbp)                   as total_gbp,
        array_agg(j.transaction_id)         as transaction_ids
    from {fct} as j
    where j.sender_account = t.account_id
      and j.transaction_ts >= t.transaction_ts - make_interval(days => %(window_days)s)
      and j.transaction_ts <= t.transaction_ts

) as w
where w.n_receivers >= %(min_receivers)s
  and w.total_gbp >= %(min_total_gbp)s  -- added in version 2 (tuning, 25 Oct 2026)
order by t.account_id, t.transaction_ts
