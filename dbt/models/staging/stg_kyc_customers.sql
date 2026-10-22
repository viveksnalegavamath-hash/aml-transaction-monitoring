-- SYNTHETIC KYC customers: already typed by the generator, so selection only.

select
    customer_id,
    customer_type,
    full_name,
    date_of_birth,
    nationality         as nationality_country,
    occupation,
    declared_annual_income_gbp,
    pep_flag            as is_pep,
    onboarding_date,
    is_synthetic
from {{ source('raw', 'kyc_customers') }}
