-- Profiling of raw.saml_d: what is actually in the data, before any cleaning.
-- Findings are written up in data/README.md.
--
-- All raw columns are text, so numbers are cast inside each query.
-- is_laundering / laundering_type are used here only to DESCRIBE the data;
-- they are never used in rules or model features.
--
-- Run (from the project root):
--   docker exec -i aml-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/profiling/saml_d_profile.sql

\pset footer off

\echo '== Q1. Row count and date range =='
SELECT
    count(*)              AS rows,
    min(date)             AS first_date,
    max(date)             AS last_date,
    count(DISTINCT date)  AS distinct_days
FROM raw.saml_d;

\echo '== Q2. Missing values and badly formatted values per column =='
SELECT
    count(*) FILTER (WHERE coalesce(time, '') = '')                   AS time_missing,
    count(*) FILTER (WHERE time !~ '^\d{2}:\d{2}:\d{2}$')             AS time_bad_format,
    count(*) FILTER (WHERE coalesce(date, '') = '')                   AS date_missing,
    count(*) FILTER (WHERE date !~ '^\d{4}-\d{2}-\d{2}$')             AS date_bad_format,
    count(*) FILTER (WHERE coalesce(sender_account, '') = '')         AS sender_missing,
    count(*) FILTER (WHERE coalesce(receiver_account, '') = '')       AS receiver_missing,
    count(*) FILTER (WHERE coalesce(amount, '') = '')                 AS amount_missing,
    count(*) FILTER (WHERE amount !~ '^-?\d+(\.\d+)?$')               AS amount_not_numeric,
    count(*) FILTER (WHERE coalesce(payment_currency, '') = '')       AS pay_ccy_missing,
    count(*) FILTER (WHERE coalesce(received_currency, '') = '')      AS rec_ccy_missing,
    count(*) FILTER (WHERE coalesce(sender_bank_location, '') = '')   AS sender_loc_missing,
    count(*) FILTER (WHERE coalesce(receiver_bank_location, '') = '') AS receiver_loc_missing,
    count(*) FILTER (WHERE coalesce(payment_type, '') = '')           AS payment_type_missing,
    count(*) FILTER (WHERE is_laundering NOT IN ('0', '1'))           AS label_not_0_1,
    count(*) FILTER (WHERE coalesce(laundering_type, '') = '')        AS typology_missing
FROM raw.saml_d;

\echo '== Q3. Amount distribution (payment currency, not yet converted to GBP) =='
SELECT
    count(*) FILTER (WHERE amount::numeric <= 0)                                 AS zero_or_negative,
    min(amount::numeric)                                                         AS min,
    percentile_cont(0.25) WITHIN GROUP (ORDER BY amount::numeric)                AS p25,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY amount::numeric)                AS median,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY amount::numeric)                AS p75,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY amount::numeric)                AS p99,
    max(amount::numeric)                                                         AS max,
    count(*) FILTER (WHERE amount::numeric BETWEEN 8000 AND 9999.99)             AS just_below_10k
FROM raw.saml_d;

\echo '== Q4. Payment types =='
SELECT
    payment_type,
    count(*)                                              AS rows,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)    AS pct,
    round(avg(amount::numeric), 2)                        AS avg_amount
FROM raw.saml_d
GROUP BY payment_type
ORDER BY rows DESC;

\echo '== Q5. Currencies: summary, then top 15 pairs (payment -> received) =='
SELECT
    count(DISTINCT payment_currency)                                              AS payment_currencies,
    count(DISTINCT received_currency)                                             AS received_currencies,
    count(DISTINCT (payment_currency, received_currency))                         AS pairs,
    round(100.0 * count(*) FILTER (WHERE payment_currency = 'UK pounds'
                                     AND received_currency = 'UK pounds') / count(*), 2) AS gbp_to_gbp_pct,
    round(100.0 * count(*) FILTER (WHERE payment_currency <> 'UK pounds') / count(*), 2) AS paid_not_in_gbp_pct,
    round(100.0 * count(*) FILTER (WHERE payment_currency <> received_currency) / count(*), 2) AS currency_changes_pct
FROM raw.saml_d;

SELECT
    payment_currency,
    received_currency,
    count(*)                                              AS rows,
    round(100.0 * count(*) / sum(count(*)) OVER (), 3)    AS pct
FROM raw.saml_d
GROUP BY payment_currency, received_currency
ORDER BY rows DESC
LIMIT 15;

\echo '== Q6. Cross-border share, and whether it agrees with payment_type =='
SELECT
    (sender_bank_location <> receiver_bank_location)      AS locations_differ,
    (payment_type = 'Cross-border')                       AS type_is_cross_border,
    count(*)                                              AS rows,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)    AS pct
FROM raw.saml_d
GROUP BY 1, 2
ORDER BY rows DESC;

\echo '== Q6b. Bank locations (sender and receiver) =='
SELECT location, sum(as_sender) AS as_sender, sum(as_receiver) AS as_receiver
FROM (
    SELECT sender_bank_location AS location, 1 AS as_sender, 0 AS as_receiver FROM raw.saml_d
    UNION ALL
    SELECT receiver_bank_location, 0, 1 FROM raw.saml_d
) AS l
GROUP BY location
ORDER BY sum(as_sender) + sum(as_receiver) DESC;

\echo '== Q7. Self-transfers (sender = receiver) =='
SELECT
    count(*) FILTER (WHERE sender_account = receiver_account)  AS self_transfers,
    round(100.0 * count(*) FILTER (WHERE sender_account = receiver_account) / count(*), 3) AS pct
FROM raw.saml_d;

\echo '== Q8. Suspicious rate overall and by typology (label used for description only) =='
SELECT
    coalesce(laundering_type, 'ALL')                                         AS laundering_type,
    CASE WHEN laundering_type IS NOT NULL THEN max(is_laundering) END        AS is_laundering,
    count(*)                                                                 AS rows,
    round(100.0 * count(*) FILTER (WHERE is_laundering = '1') / count(*), 3) AS suspicious_pct,
    round(avg(amount::numeric), 2)                                           AS avg_amount
FROM raw.saml_d
GROUP BY ROLLUP (laundering_type)
ORDER BY (laundering_type IS NULL) DESC, max(is_laundering) DESC, rows DESC;

\echo '== Q9. Accounts and transactions per account =='
WITH per_account AS (
    SELECT account, count(*) AS txns
    FROM (
        SELECT sender_account AS account FROM raw.saml_d
        UNION ALL
        SELECT receiver_account FROM raw.saml_d
    ) AS a
    GROUP BY account
)
SELECT
    (SELECT count(DISTINCT sender_account) FROM raw.saml_d)     AS distinct_senders,
    (SELECT count(DISTINCT receiver_account) FROM raw.saml_d)   AS distinct_receivers,
    count(*)                                                    AS distinct_accounts,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY txns)           AS median_txns_per_account,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY txns)          AS p99_txns_per_account,
    max(txns)                                                   AS max_txns_per_account
FROM per_account;

\echo '== Q10. Exact duplicate rows (all 12 columns identical) =='
SELECT
    count(*)                                   AS rows,
    count(*) - (SELECT count(*) FROM (SELECT DISTINCT * FROM raw.saml_d) AS d) AS duplicate_rows
FROM raw.saml_d;
