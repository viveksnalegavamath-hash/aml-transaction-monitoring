-- SAML-D transactions, cleaned and typed. One row per raw row.
-- The suspicious label (is_laundering, laundering_type) is deliberately NOT
-- selected: it lives only in the evaluation model.
-- Left joins on purpose: an unmapped name becomes NULL and fails a not_null
-- test, instead of the row silently disappearing.

with source as (

    select * from {{ source('raw', 'saml_d') }}

),

currencies as (

    select * from {{ ref('saml_d_currencies') }}

),

countries as (

    select * from {{ source('ref', 'country_name_map') }}

),

cleaned as (

    select
        {{ saml_d_transaction_id('s') }}                 as transaction_id,
        (s.date || ' ' || s.time)::timestamp              as transaction_ts,
        s.date::date                                      as transaction_date,
        s.sender_account,
        s.receiver_account,
        s.amount::numeric                                 as amount,
        pay_ccy.iso_code                                  as payment_currency,
        rec_ccy.iso_code                                  as received_currency,
        sender_ctry.iso_code                              as sender_country,
        receiver_ctry.iso_code                            as receiver_country,
        -- "Cash Deposit" -> cash_deposit, "Cross-border" -> cross_border
        lower(regexp_replace(s.payment_type, '[^A-Za-z0-9]+', '_', 'g'))
                                                          as payment_type
    from source as s
    left join currencies as pay_ccy
        on pay_ccy.saml_d_currency = s.payment_currency
    left join currencies as rec_ccy
        on rec_ccy.saml_d_currency = s.received_currency
    left join countries as sender_ctry
        on sender_ctry.name_key = {{ country_name_key('s.sender_bank_location') }}
    left join countries as receiver_ctry
        on receiver_ctry.name_key = {{ country_name_key('s.receiver_bank_location') }}

)

select
    *,
    -- Derived from the two countries, not from payment_type (see data/README.md Q6).
    sender_country <> receiver_country                    as is_cross_border,
    -- The 417 rows whose countries differ but whose type is not cross-border.
    (sender_country <> receiver_country) <> (payment_type = 'cross_border')
                                                          as payment_type_disagrees
from cleaned
