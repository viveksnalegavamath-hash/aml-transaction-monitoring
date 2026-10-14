# AML Transaction Monitoring System

A company-grade anti-money-laundering (AML) transaction monitoring system built on synthetic, bank-realistic data: rules + machine learning detect suspicious activity, every alert is explained, and analysts work alerts through an API, an automated n8n workflow and a Power BI dashboard. Portfolio project for AML, KYC, RegTech and financial-crime data roles in the UK.

- Full plan, phases and checklists: `docs/PROJECT_PLAN.md` — read it before starting or planning any phase.
- Current status and the next step: `CLAUDE.local.md` (private, loaded automatically).

## How to work with Vivek

- He is new to software engineering. Before acting, say in plain English what we're doing, why, and what "done" looks like.
- One small step at a time: propose a plan, wait for his OK, then do it. Never make large silent changes.
- After each step, give one command to verify it and describe the expected output.
- When he must act himself (GUI apps, browser logins, password prompts), give one command per code block and tell him to wait for the `%` prompt before the next.
- Prefer writing files yourself over asking him to paste file contents.
- He reviews complete drafts, then sends one consolidated list of changes.
- Record every design decision in `LEARNING.md` (what, why, alternatives considered). It is his interview preparation.

## Stack (decided — ask before changing)

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 via uv: `uv run ...`, `uv add ...`. Never pip or Anaconda in this project |
| Database | PostgreSQL 16 in Docker (`docker-compose.yml`, service `db`, container `aml-postgres`) |
| Transformations | dbt Core 1.x + dbt-postgres 1.x (uv tool). Not dbt v2: it does not support Postgres |
| ML | scikit-learn, XGBoost, LightGBM, SHAP, MLflow |
| Network analytics | NetworkX, pyvis |
| API | FastAPI, Pydantic, uvicorn |
| Automation | n8n, self-hosted in Docker (Phase 4) |
| Cloud | AWS ECS Fargate + RDS + S3 (Phase 4). App Runner is closed to new customers |
| Dashboard | Power BI Desktop in a Windows VM (Phase 5) |
| Quality | ruff, pytest, GitHub Actions |
| Editor | PyCharm (free tier). Run Docker from the terminal, not PyCharm's run buttons |

## Data

- Main: SAML-D — Kaggle `berkanoztas/synthetic-transaction-monitoring-dataset-aml`. ~9.5M UK-focused synthetic transactions, 28 typologies (17 suspicious).
- Out-of-sample test: IBM AMLworld HI-Small — Kaggle `ealtman2019/ibm-transactions-for-anti-money-laundering-aml`, file `HI-Small_Trans.csv` only.
- Reference: UK Sanctions List CSV (FCDO), FATF lists (June 2026), OpenSanctions (non-commercial licence).
- Synthetic KYC customer table generated with Faker, fixed seed, clearly labelled synthetic.
- Raw files live in `data/raw/` (git-ignored). Never commit data. Develop on a 10% sample.

## Repository layout (target)

```text
src/aml_transaction_monitoring/   Python package
    ingestion/    loaders, KYC generator
    rules/        rules engine
    scoring/      customer risk + alert priority
    screening/    sanctions / PEP name screening
    ml/           features, models, SHAP, network
    api/          FastAPI app
rules/            R01–R10, S01 YAML specs (thresholds live here)
config/           other settings in YAML (e.g. synthetic KYC generator)
sql/              stand-alone SQL (profiling, checks)
dbt/              dbt project: staging + marts + tests
tests/            pytest
docs/             plan, model documentation, mock SAR, diagrams
automation/       n8n workflow JSON export
dashboards/       Power BI file + screenshots
notebooks/        exploration only
data/raw/         downloaded data (git-ignored)
```

## Commands

- Database: `docker compose up -d` · `docker compose ps` · `docker compose stop` · `docker compose logs db`
- Python: `uv run python ...` · tests `uv run pytest` · lint `uv run ruff check .` · format `uv run ruff format .`
- dbt: `dbt build` (run inside `dbt/`)
- Never run `docker compose down -v` or delete volumes without Vivek's explicit OK: it erases all database data.

## Security rules (strict, non-negotiable)

1. Never read, print, echo or display `.env` or any secret, and never ask Vivek to paste one. Code reads credentials from environment variables (python-dotenv); commands use variable expansion, never literal values.
2. Never hard-code passwords, tokens, API keys or credentialed connection strings. New settings go in `.env` (git-ignored) with a placeholder line added to `.env.example`.
3. Before every commit, show `git status` to Vivek and confirm no `.env`, data files, keys, notebooks with outputs containing data, or `.idea/` are staged. Run the secret scanner (gitleaks) once it is installed.
4. The database binds to `127.0.0.1` only. Never publish ports on `0.0.0.0`. Use a separate read-only database role for dashboard and API reads.
5. Synthetic and public data only. Never introduce real personal data.
6. Dependencies are pinned (`uv.lock` committed). Official Docker images only, pinned to a major version. Run a dependency vulnerability scan before each release.
7. API (Phase 4): API-key authentication, Pydantic input validation, rate limiting, no personal data or stack traces in responses or logs.
8. LLM assistant (Phase 4): synthetic data only, no secrets in prompts, every prompt and output logged for audit, human review before anything is acted on.
9. AWS (Phase 4): MFA, least-privilege IAM, no root access keys, secrets in AWS Secrets Manager, $10 budget alert.
10. Never force-push. Ask before every `git push`.

## Project rules

1. Rule thresholds and parameters live in YAML in `rules/`, never in code.
2. Every rule has a test where it fires and a test where it does not.
3. The suspicious / laundering label is used only in evaluation code — never in rules or model features.
4. Split train and test by time, never randomly.
5. Notebooks are for exploration; real logic lives in `src/`.
6. Type hints and docstrings on functions; `logging`, not `print`.
7. Reproducible: fixed random seeds, pinned versions, one command per step.
8. Small commits with clear messages, e.g. "Add R04 rapid pass-through rule".
9. Report precision, recall and PR-AUC — never accuracy.
