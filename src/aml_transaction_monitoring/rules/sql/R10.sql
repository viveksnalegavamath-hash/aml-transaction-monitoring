-- R10 Income mismatch. Spec and thresholds: rules/R10_income_mismatch.yaml.
-- Contract: see the header of R01.sql. Also reads the KYC table through the
-- dim_customer placeholder (same schema).
--
-- Two stages, as in R04: rolling inflow over the window for customer
-- accounts only (one sorted pass), compared with the declared monthly
-- income; then, for the candidates, the payments behind the alert.

with rolling as (

    select
        f.receiver_account                  as account_id,
        f.transaction_ts,
        sum(f.amount_gbp) over w            as inflow_gbp,
        count(*) over w                     as n_inflows,
        k.declared_annual_income_gbp / 12.0 as monthly_income_gbp
    from {fct} as f
    join {dim_customer} as k on k.customer_id = f.receiver_account
    window w as (
        partition by f.receiver_account
        order by f.transaction_ts
        range between make_interval(days => %(window_days)s) preceding and current row
    )

),

candidates as (

    select distinct account_id, transaction_ts, inflow_gbp, n_inflows, monthly_income_gbp
    from rolling
    where inflow_gbp > %(income_multiple)s * monthly_income_gbp

)

select
    c.account_id,
    c.transaction_ts                                            as trigger_ts,
    c.transaction_ts - make_interval(days => %(window_days)s)   as window_start,
    d.transaction_ids,
    jsonb_build_object(
        'inflow_gbp', c.inflow_gbp,
        'n_inflows', c.n_inflows,
        'monthly_income_gbp', round(c.monthly_income_gbp, 2),
        'ratio', round(c.inflow_gbp / nullif(c.monthly_income_gbp, 0), 2)
    )                                                           as metrics
from candidates as c
cross join lateral (

    select array_agg(f.transaction_id) as transaction_ids
    from {fct} as f
    where f.receiver_account = c.account_id
      and f.transaction_ts >= c.transaction_ts - make_interval(days => %(window_days)s)
      and f.transaction_ts <= c.transaction_ts

) as d
