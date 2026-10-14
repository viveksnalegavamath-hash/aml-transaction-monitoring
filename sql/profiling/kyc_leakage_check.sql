-- Leakage check for the SYNTHETIC KYC table (raw.kyc_customers).
--
-- EVALUATION ONLY: this query uses the suspicious label to CHECK the KYC data,
-- never to build anything. If KYC attributes were generated independently of
-- the label, customers linked to suspicious transactions should look the same
-- as everyone else, apart from random sampling noise.
--
-- Run (from the project root):
--   docker exec -i aml-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/profiling/kyc_leakage_check.sql

\pset footer off

\echo '== KYC attributes: customers linked to suspicious transactions vs all others =='
WITH linked AS (
    SELECT sender_account AS account FROM raw.saml_d WHERE is_laundering = '1'
    UNION
    SELECT receiver_account FROM raw.saml_d WHERE is_laundering = '1'
)
SELECT
    (l.account IS NOT NULL)                                       AS linked_to_suspicious,
    count(*)                                                      AS customers,
    round(100.0 * avg((k.customer_type = 'business')::int), 2)    AS business_pct,
    round(100.0 * avg(k.pep_flag::int), 3)                        AS pep_pct,
    round(100.0 * avg((k.nationality = 'GB')::int), 2)            AS gb_pct,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY k.declared_annual_income_gbp)
        FILTER (WHERE k.customer_type = 'individual')             AS median_income_individual,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY k.declared_annual_income_gbp)
        FILTER (WHERE k.customer_type = 'business')               AS median_turnover_business,
    round(avg(DATE '2022-10-07' - k.onboarding_date) / 365.25, 2) AS avg_account_age_years
FROM raw.kyc_customers AS k
LEFT JOIN linked AS l ON l.account = k.customer_id
GROUP BY 1
ORDER BY 1;
