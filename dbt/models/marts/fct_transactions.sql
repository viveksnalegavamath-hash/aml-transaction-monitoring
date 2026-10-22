{{ config(
    indexes=[
        {'columns': ['transaction_id'], 'unique': true},
        {'columns': ['sender_account']},
        {'columns': ['receiver_account']},
        {'columns': ['transaction_ts']},
    ]
) }}

-- One row per transaction, enriched for rules, features and dashboards.
-- No suspicious label here: it is only in evaluation.eval_transaction_labels.

with transactions as (

    select * from {{ ref('stg_saml_d') }}

),

countries as (

    select * from {{ ref('dim_country') }}

),

customers as (

    select customer_id from {{ ref('dim_customer') }}

)

select
    t.transaction_id,
    t.transaction_ts,
    t.transaction_date,
    t.sender_account,
    t.receiver_account,
    t.amount,
    t.payment_currency,
    t.received_currency,
    -- SAML-D amounts are on one common scale, treated as GBP, whatever the
    -- payment currency (median ~7,000-7,600 in every currency; decided
    -- 22 Oct 2026, see data/README.md). No FX conversion is applied.
    t.amount                                as amount_gbp,
    t.payment_type,
    t.is_cross_border,
    t.payment_type_disagrees,
    t.sender_country,
    sender_ctry.fatf_status                 as sender_country_fatf_status,
    sender_ctry.internal_rating             as sender_country_internal_rating,
    t.receiver_country,
    receiver_ctry.fatf_status               as receiver_country_fatf_status,
    receiver_ctry.internal_rating           as receiver_country_internal_rating,
    sender_cust.customer_id is not null     as sender_is_customer,
    receiver_cust.customer_id is not null   as receiver_is_customer
from transactions as t
left join countries as sender_ctry
    on sender_ctry.iso_code = t.sender_country
left join countries as receiver_ctry
    on receiver_ctry.iso_code = t.receiver_country
left join customers as sender_cust
    on sender_cust.customer_id = t.sender_account
left join customers as receiver_cust
    on receiver_cust.customer_id = t.receiver_account
