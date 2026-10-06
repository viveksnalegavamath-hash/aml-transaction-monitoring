# Learning log

Design decisions, what I learnt, and why.

## Setup

- PostgreSQL runs in Docker, bound to 127.0.0.1: same setup on my laptop and on AWS, and unreachable from the network.
- uv manages Python: one locked environment (uv.lock), no clash with Anaconda.
- dbt Core 1.x, because dbt v2 does not support PostgreSQL yet.
- Secrets live only in .env (git-ignored); gitleaks blocks any commit containing a secret.
- Raw data is git-ignored; the project uses synthetic and public data only.

## Phase 1 — Raw data loading

- **Raw layer, untouched.** CSVs load into schema `raw` exactly as downloaded, every column as text. Why: lineage and audit — any number can be traced back to the source file, and cleaning can be redone without re-downloading. Typing and cleaning happen in dbt staging, where they are tested. Alternative: typed columns at load time — rejected, because one malformed value would fail the whole load and hide what the source really contained.
- **PostgreSQL `COPY`, client-side (`COPY ... FROM STDIN`).** Python streams the file from the Mac to the database in Docker in 1 MB chunks. Why: COPY is the fastest bulk-load path (millions of rows in about a minute), and client-side streaming works even though the server cannot see files on my Mac. Alternatives: pandas `to_sql` (row inserts, far slower and memory-hungry); server-side `COPY FROM '/path'` (would need the file mounted into the container).
- **Empty-and-load in one transaction.** `TRUNCATE`, `COPY` and the log entry commit together or not at all. Why: a failed or interrupted load never leaves the table empty or half-loaded — the previous data stays. In PostgreSQL, `TRUNCATE` is transactional, so it rolls back too. Alternative: `DROP` + recreate — same safety, but loses grants and any comments on the table.
- **Header check before loading.** The loader compares the CSV header with the expected column list and stops with a clear error naming missing, unexpected or reordered columns. Why: if a source changes its layout, we fail loudly instead of loading values into the wrong columns.
- **Audit table `raw.load_log`.** One row per load: target table, file name, row count, loaded-at time. Why: banks must evidence what data entered the system and when; it also lets us reconcile row counts later in dbt tests.
- **Connect via 127.0.0.1, credentials from `.env`.** `localhost` reached a different PostgreSQL on this Mac.
- **IBM HI-Small: duplicate "Account" headers renamed by position.** The file has two columns both called `Account` (sender's, then receiver's). The header check still expects the file's exact 11 headers; only the table columns are named `from_account` / `to_account`. Why: a table cannot have two columns with the same name, and the position tells us which is which. Other headers become lower case with underscores (`From Bank` → `from_bank`). Alternative: edit the CSV header — rejected, raw files are never edited.
- **Bank IDs kept as text.** IDs like `010` would lose their leading zero as numbers, and `010` and `10` might then wrongly look like the same bank.
- **UK Sanctions List: preamble line skipped, but kept as evidence.** Line 1 is `Report Date: 02-Oct-2026`; the header is line 2. The loader skips it before COPY and stores it in `raw.load_log.source_note`. Why: a bank must be able to show which version of the list a customer was screened against; the daily re-screening (Phase 4) also depends on knowing the list date. Alternative: just drop the line — rejected, it is the only record of the list version.
- **Column names: any run of spaces or punctuation becomes one underscore.** `D.O.B` → `d_o_b`, `Nationality(/ies)` → `nationality_ies`. Why: names with dots, brackets or slashes need double quotes in every SQL query. Business-friendly names (`date_of_birth`) come in dbt staging. SAML-D and IBM names are unchanged by this rule.
- **The sanctions list is "flattened": many rows per designation.** Rows repeat for each name/alias and each address (and possibly other multi-value details), all sharing a `Unique ID`. 58,502 rows cover only 6,370 designations. Staging must de-duplicate to one row per designation plus a separate name/alias list before screening, or one person would produce dozens of matches.
- **COPY names its columns.** `COPY raw.t (col1, col2, ...)` instead of relying on column order, so if a table built earlier has different columns, the load fails loudly instead of putting values in the wrong place.
