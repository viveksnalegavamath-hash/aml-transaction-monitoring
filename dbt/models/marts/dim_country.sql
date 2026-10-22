-- One row per country: FATF status and internal rating kept separate.
-- A combined country risk score is a policy decision for Phase 2 scoring.

select * from {{ ref('stg_country_risk') }}
