{{ config(indexes=[{'columns': ['transaction_id'], 'unique': true}]) }}

-- The answer key: suspicious label and typology per transaction.
-- EVALUATION ONLY. Rules, features and models must never read this table;
-- they join to it only when measuring precision and recall.
-- Same transaction_id macro as stg_saml_d, so the IDs match.

select
    {{ saml_d_transaction_id('s') }}    as transaction_id,
    s.is_laundering = '1'               as is_laundering,
    typology.typology,
    typology.is_suspicious_typology
from {{ source('raw', 'saml_d') }} as s
left join {{ ref('saml_d_typologies') }} as typology
    on typology.laundering_type = s.laundering_type
