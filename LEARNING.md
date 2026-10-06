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
