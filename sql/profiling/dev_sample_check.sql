-- Dev sample check: "10% account sample (≈19% of rows)" next to the full data.
-- EVALUATION ONLY: reads the suspicious label to describe the sample, never to
-- choose it (accounts are chosen by a hash of the account ID alone).
-- The sample is for development speed only; all reported results come from
-- the full data.
--
-- Needs both builds:  dbt build   and   dbt build --vars '{dev_sample: true}'
-- Run: in DBeaver, or psql against the aml database.

with full_data as (
    select
        'full data'                                     as dataset,
        count(*)                                        as transactions,
        count(distinct f.sender_account)                as senders,
        count(*) filter (where e.is_laundering)         as suspicious,
        count(distinct e.typology)                      as typologies,
        min(f.transaction_date)                         as first_date,
        max(f.transaction_date)                         as last_date
    from marts.fct_transactions as f
    join evaluation.eval_transaction_labels as e using (transaction_id)
),

sample_data as (
    select
        '10% account sample'                            as dataset,
        count(*)                                        as transactions,
        count(distinct f.sender_account)                as senders,
        count(*) filter (where e.is_laundering)         as suspicious,
        count(distinct e.typology)                      as typologies,
        min(f.transaction_date)                         as first_date,
        max(f.transaction_date)                         as last_date
    from marts_sample.fct_transactions as f
    join evaluation_sample.eval_transaction_labels as e using (transaction_id)
),

both_sets as (
    select * from full_data
    union all
    select * from sample_data
)

select
    dataset,
    transactions,
    round(100.0 * transactions / (select transactions from full_data), 1) as pct_of_rows,
    senders,
    suspicious,
    round(100.0 * suspicious / transactions, 3)                           as suspicious_rate_pct,
    typologies,
    first_date,
    last_date
from both_sets
order by transactions desc;
