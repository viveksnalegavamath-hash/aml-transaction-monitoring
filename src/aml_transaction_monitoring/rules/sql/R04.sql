-- R04 Rapid pass-through. Spec and thresholds: rules/R04_rapid_pass_through.yaml.
-- Contract: see the header of R01.sql.
--
-- Each account's money in and money out as one stream. The trigger is an
-- outgoing payment. Two stages keep it affordable:
--   1. rolling sums over the window (cheap, one sort) keep only triggers that
--      could qualify: enough in, and enough out in the whole window;
--   2. for those few, a lateral look-back re-checks exactly, counting only
--      outflows from the first inflow in the window onwards (money must come
--      in before it goes out).

with flows as (

    select receiver_account as account_id, transaction_ts, amount_gbp as in_gbp, 0 as out_gbp
    from {fct}
    union all
    select sender_account, transaction_ts, 0, amount_gbp
    from {fct}

),

rolling as (

    select
        account_id,
        transaction_ts,
        out_gbp,
        sum(in_gbp) over w  as in_window,
        sum(out_gbp) over w as out_window
    from flows
    window w as (
        partition by account_id
        order by transaction_ts
        range between make_interval(hours => %(window_hours)s) preceding and current row
    )

),

triggers as (

    select distinct account_id, transaction_ts
    from rolling
    where out_gbp > 0
      and in_window >= %(min_inflow_gbp)s
      and out_window >= in_window * %(min_outflow_ratio)s

),

inflows as (

    select
        t.account_id,
        t.transaction_ts                    as trigger_ts,
        min(f.transaction_ts)               as first_in_ts,
        count(*)                            as n_in,
        sum(f.amount_gbp)                   as in_gbp,
        array_agg(f.transaction_id)         as in_ids
    from triggers as t
    join {fct} as f
      on f.receiver_account = t.account_id
     and f.transaction_ts >= t.transaction_ts - make_interval(hours => %(window_hours)s)
     and f.transaction_ts <= t.transaction_ts
    group by t.account_id, t.transaction_ts

),

checked as (

    select i.*, o.n_out, o.out_gbp, o.out_ids
    from inflows as i
    cross join lateral (

        select
            count(*)                        as n_out,
            coalesce(sum(f.amount_gbp), 0)  as out_gbp,
            array_agg(f.transaction_id)     as out_ids
        from {fct} as f
        where f.sender_account = i.account_id
          and f.transaction_ts >= i.first_in_ts
          and f.transaction_ts <= i.trigger_ts

    ) as o
    where i.in_gbp >= %(min_inflow_gbp)s
      and o.out_gbp >= i.in_gbp * %(min_outflow_ratio)s

)

select
    account_id,
    trigger_ts,
    trigger_ts - make_interval(hours => %(window_hours)s)  as window_start,
    in_ids || out_ids                                      as transaction_ids,
    jsonb_build_object(
        'n_in', n_in,
        'in_gbp', in_gbp,
        'n_out', n_out,
        'out_gbp', out_gbp,
        'out_ratio', round(out_gbp / in_gbp, 4)
    )                                                      as metrics
from checked
