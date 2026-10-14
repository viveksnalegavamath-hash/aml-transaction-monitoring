# Data: lineage and profiling

Raw files live in `data/raw/` and are never committed. They load unchanged into schema `raw` with `uv run python -m aml_transaction_monitoring.ingestion.load_raw`; every load is recorded in `raw.load_log`.

## Lineage

| File | Source | Downloaded | Licence | Loaded into | Rows |
| --- | --- | --- | --- | --- | --- |
| `SAML-D.csv` | Kaggle [`berkanoztas/synthetic-transaction-monitoring-dataset-aml`](https://www.kaggle.com/datasets/berkanoztas/synthetic-transaction-monitoring-dataset-aml); Oztas et al., IEEE ICEBE 2023, [doi:10.1109/ICEBE59045.2023.00028](https://doi.org/10.1109/ICEBE59045.2023.00028) | 5 Oct 2026 | CC BY-NC-SA 4.0 | `raw.saml_d` | 9,504,852 |
| `HI-Small_Trans.csv` | Kaggle [`ealtman2019/ibm-transactions-for-anti-money-laundering-aml`](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml); Altman et al., NeurIPS 2023 | 5 Oct 2026 | Community Data License Agreement – Sharing 1.0 | `raw.ibm_hi_small` | 5,078,345 |
| `UK-Sanctions-List.csv` | FCDO, [The UK Sanctions List](https://www.gov.uk/government/publications/the-uk-sanctions-list) — report date 02-Oct-2026 | 5 Oct 2026 | Open Government Licence v3.0 (confirmed on the GOV.UK page, 6 Oct 2026) | `raw.uk_sanctions_list` | 58,502 |

Kaggle licences were read from each dataset's metadata (`kaggle datasets metadata`). All transaction data is synthetic; the sanctions list is public official data.

## SAML-D profile

Queries: [`sql/profiling/saml_d_profile.sql`](../sql/profiling/saml_d_profile.sql) (runs in about 50 seconds on the full 9.5M rows). Profiled on the raw table, before any cleaning. The suspicious label is used here only to describe the data, never in rules or features.

### Findings

| # | Check | Result | What it means for the build |
| --- | --- | --- | --- |
| Q1 | Size and period | 9,504,852 rows; 2022-10-07 to 2023-08-23 (321 days) | About 10.5 months: enough for a time-based train / validation / test split and 90-day rule windows |
| Q2 | Missing / malformed values | **0** in every column; all times `HH:MM:SS`, all dates `YYYY-MM-DD`, all amounts numeric, label always 0 or 1 | Staging can cast types directly; dbt tests will still assert it stays true |
| Q3 | Amounts | No zero or negative amounts; min 3.73, median 6,114, p99 45,022, max 12,618,498 | Heavy right tail: rules and features should use medians / log amounts, not means. Amounts are in the *payment* currency, so mixed currencies here |
| Q4 | Payment types | 7 types: credit card, debit card, cheque, ACH (~21% each), cross-border 9.8%, cash withdrawal 3.2%, cash deposit 2.4% | Cash is 5.5% of rows: the population for R01 (structuring) and R09 (cash intensity) |
| Q5 | Currencies | 13 currencies, 169 pairs; 88.5% GBP → GBP; 4.3% paid in a non-GBP currency; 11.4% change currency | `amount_gbp` needs an FX conversion for 4.3% of rows. "Dirham" (UAE) and "Moroccan dirham" are different currencies — map to ISO codes AED and MAD |
| Q6 | Cross-border | 9.83% of rows have different sender and receiver bank countries; they match `payment_type = 'Cross-border'` except **417 rows** whose countries differ but whose type is not cross-border | Derive cross-border from the countries, not from the payment type; flag the 417 as a data-quality note |
| Q6b | Bank locations | 18 locations, as names (`UK`, `UAE`, `USA`, ...); UK is the sender in 96.6% of rows | Map to ISO 3166 codes in staging; `dim_country` joins FATF status on the ISO code |
| Q7 | Self-transfers | 0 | Unlike IBM HI-Small, which has some — no special handling needed for SAML-D |
| Q8 | Suspicious rate | **9,873 suspicious rows (0.104%)** across 17 suspicious typologies; 11 normal typologies; 28 in total, as documented | Heavy imbalance: report precision, recall, PR-AUC — never accuracy. Typology names are inconsistent (`Stacked Bipartite` vs `Layered_Fan_In`, `Deposit-Send`, typo `Normal_Foward`) — standardise in staging |
| Q9 | Accounts | 855,460 accounts; 292,715 ever send, 652,266 ever receive; median 12 transactions per account, p99 522, max 1,498 | Many accounts only receive. Decision needed for the KYC table: one customer per sending account, or per every account |
| Q10 | Exact duplicates | 0 | No de-duplication needed |

### Points to carry into Phase 2 (rules)

- **Structuring averages 5,246 per transaction** (payment currency, mostly GBP), below R01's starting band of £8,000–£9,999, which targets amounts kept just under £10,000. SAML-D's structuring may sit lower; check during R01 calibration rather than assume.
- **Single large transactions look like normal large ones by amount alone**: suspicious `Single_large` averages 253,473 vs 252,634 for `Normal_single_large`. R06 rightly compares against the customer's own history, not a fixed amount.
- **Over-invoicing is rare (54 rows) but huge** (average 4.8M) — a few rows will dominate any amount-based statistic.
- 971,664 rows (10.2%) fall between 8,000 and 9,999.99 across *all* payment types, so any "just below £10k" logic must be limited to cash.

## UK Sanctions List notes

- Line 1 is `Report Date: …`; the loader stores it in `raw.load_log.source_note` as the list version.
- The file is flattened: 58,502 rows cover **6,370 designations** (`unique_id`). Rows repeat per name/alias and per address. Staging must reduce it to one row per designation plus a names/aliases table before screening.

## IBM HI-Small notes

- The two `Account` columns are loaded as `from_account` / `to_account`; bank IDs stay text to keep leading zeros (`010`).
- Contains self-transfers (e.g. `Reinvestment`), and the file is not strictly in time order — sort by timestamp before any time-based split.
- Full profiling follows when it is used as the out-of-sample test set (Phase 3).
