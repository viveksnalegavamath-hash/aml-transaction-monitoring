-- One row per SYNTHETIC customer, with the risk of their nationality
-- (or country of incorporation). No customer risk rating yet: that is a
-- documented policy step in Phase 2.

select
    customer.*,
    country.fatf_status         as nationality_fatf_status,
    country.internal_rating     as nationality_internal_rating
from {{ ref('stg_kyc_customers') }} as customer
left join {{ ref('dim_country') }} as country
    on country.iso_code = customer.nationality_country
