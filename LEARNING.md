# Learning log

Design decisions, what I learnt, and why.

## Setup

- PostgreSQL runs in Docker, bound to 127.0.0.1: same setup on my laptop and on AWS, and unreachable from the network.
- uv manages Python: one locked environment (uv.lock), no clash with Anaconda.
- dbt Core 1.x, because dbt v2 does not support PostgreSQL yet.
- Secrets live only in .env (git-ignored); gitleaks blocks any commit containing a secret.
- Raw data is git-ignored; the project uses synthetic and public data only.
