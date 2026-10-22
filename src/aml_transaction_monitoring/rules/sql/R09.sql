-- R09 Cash intensity. Spec and thresholds: rules/R09_cash_intensity.yaml.
-- Contract: see the header of R01.sql.
--
-- Cash is paid into the receiver's account. Two stages, as in R04:
--   1. for accounts that ever received cash, one sorted pass over everything
--      they received gives rolling cash and total inflow over the window;
--      the triggers are cash deposits that meet both thresholds;
--   2. for those candidates only, look up the cash deposits behind the alert.

with cash_accounts as (

    select distinct receiver_account
    from {fct}
    where payment_type = 'cash_deposit'

),

rolling as (

    select
        f.receiver_account                                  as account_id,
        f.transaction_ts,
        f.payment_type,
        sum(f.amount_gbp) over w                            as inflow_gbp,
        sum(f.amount_gbp) filter (where f.payment_type = 'cash_deposit') over w
                                                            as cash_gbp
    from {fct} as f
    join cash_accounts using (receiver_account)
    window w as (
        partition by f.receiver_account
        order by f.transaction_ts
        range between make_interval(days => %(window_days)s) preceding and current row
    )

),

candidates as (

    select distinct account_id, transaction_ts, inflow_gbp, cash_gbp
    from rolling
    where payment_type = 'cash_deposit'
      and cash_gbp > %(min_cash_gbp)s
      and cash_gbp > %(min_cash_share)s * inflow_gbp

)

select
    c.account_id,
    c.transaction_ts                                            as trigger_ts,
    c.transaction_ts - make_interval(days => %(window_days)s)   as window_start,
    d.transaction_ids,
    jsonb_build_object(
        'cash_gbp', c.cash_gbp,
        'n_cash', d.n_cash,
        'inflow_gbp', c.inflow_gbp,
        'cash_share', round(c.cash_gbp / c.inflow_gbp, 4)
    )                                                           as metrics
from candidates as c
cross join lateral (

    select count(*) as n_cash, array_agg(f.transaction_id) as transaction_ids
    from {fct} as f
    where f.receiver_account = c.account_id
      and f.payment_type = 'cash_deposit'
      and f.transaction_ts >= c.transaction_ts - make_interval(days => %(window_days)s)
      and f.transaction_ts <= c.transaction_ts

) as d
