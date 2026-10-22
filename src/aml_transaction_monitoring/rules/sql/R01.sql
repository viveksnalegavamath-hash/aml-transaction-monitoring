-- R01 Structuring. Spec and thresholds: rules/R01_structuring.yaml.
--
-- Rule SQL contract (every rule; see rules/engine.py):
--   * The transactions table is the fct placeholder in braces, filled in by
--     the engine as a quoted identifier (full or sample schema).
--   * Each threshold is a named percent-placeholder matching a YAML
--     parameter, bound by psycopg (never pasted into the text). Do not write
--     placeholder syntax or braces in comments: psycopg would read them.
--   * Returns account_id, trigger_ts, window_start, transaction_ids, metrics:
--     one row per candidate alert; the engine suppresses repeats.
--
-- One candidate per qualifying deposit: the deposit that completes the pattern
-- is the trigger, and the window is the days before it.

with deposits as (

    select
        transaction_id,
        receiver_account    as account_id,  -- cash is paid into the receiver's account
        transaction_ts,
        amount_gbp
    from {fct}
    where payment_type = 'cash_deposit'
      and amount_gbp between %(min_amount_gbp)s and %(max_amount_gbp)s

),

windows as (

    select
        account_id,
        transaction_ts                  as trigger_ts,
        count(*) over w                 as n_deposits,
        sum(amount_gbp) over w          as total_gbp,
        array_agg(transaction_id) over w as transaction_ids
    from deposits
    window w as (
        partition by account_id
        order by transaction_ts
        range between make_interval(days => %(window_days)s) preceding and current row
    )

)

select
    account_id,
    trigger_ts,
    trigger_ts - make_interval(days => %(window_days)s)    as window_start,
    transaction_ids,
    jsonb_build_object('n_deposits', n_deposits, 'total_gbp', total_gbp) as metrics
from windows
where n_deposits >= %(min_deposits)s
