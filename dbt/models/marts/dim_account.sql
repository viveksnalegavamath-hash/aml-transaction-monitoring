{{ config(
    indexes=[
        {'columns': ['account_id'], 'unique': true},
    ]
) }}

-- One row per account that appears in the transactions, as sender or
-- receiver. History span in the data, and whether the account is in the dev
-- sample (same macro as the sample filter, so the rule lives in one place).
-- In a full build in_dev_sample shows which accounts a sample build keeps; in
-- a sample build, accounts with in_dev_sample false appear only as
-- counterparties of sampled accounts, so their history is incomplete.

with parties as (

    select sender_account as account_id, transaction_ts
    from {{ ref('fct_transactions') }}
    union all
    select receiver_account, transaction_ts
    from {{ ref('fct_transactions') }}

)

select
    account_id,
    min(transaction_ts)                 as first_seen_ts,
    max(transaction_ts)                 as last_seen_ts,
    {{ in_dev_sample('account_id') }}   as in_dev_sample
from parties
group by account_id
