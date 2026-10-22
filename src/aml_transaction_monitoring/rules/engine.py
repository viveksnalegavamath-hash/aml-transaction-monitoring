"""Run the AML rules and write alerts.

Each rule is a YAML spec in `rules/` (every threshold lives there) plus a SQL
file in `rules/sql/`. The engine:

1. loads and checks every spec (required fields, every SQL placeholder has a
   YAML parameter and every parameter is used, no reference to the label);
2. runs each rule's SQL on `fct_transactions`, binding thresholds as query
   parameters (never string formatting) and the table as a quoted identifier;
3. suppresses repeat alerts for the same account within the rule's
   suppression period;
4. writes `alerts`, `alert_transactions` and a `rule_runs` audit row, in one
   transaction, replacing earlier alerts of the rules it ran.

Rules never read the evaluation schema or the suspicious label: the label is
joined only afterwards, by the evaluation code.

Usage:
    uv run python -m aml_transaction_monitoring.rules.engine            # full data
    uv run python -m aml_transaction_monitoring.rules.engine --sample   # dev sample
    uv run python -m aml_transaction_monitoring.rules.engine --rule R01
"""

import argparse
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql
from psycopg.types.json import Jsonb

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

logger = logging.getLogger(__name__)

RULES_DIR = PROJECT_ROOT / "rules"
SQL_DIR = Path(__file__).resolve().parent / "sql"
FULL_SCHEMA = "marts"
SAMPLE_SCHEMA = "marts_sample"

REQUIRED_FIELDS = (
    "id",
    "name",
    "version",
    "typology",
    "logic",
    "sql",
    "parameters",
    "suppression_days",
    "reason",
    "rationale",
    "expected_weekly_alerts",
    "owner",
    "last_tuned",
    "change_log",
)
PLACEHOLDER = re.compile(r"%\((\w+)\)s")
# Rules must never touch the label or the evaluation schema, even in comments.
FORBIDDEN = re.compile(
    r"evaluation|laundering|eval_transaction_labels|typolog", re.IGNORECASE
)
RESULT_COLUMNS = (
    "account_id",
    "trigger_ts",
    "window_start",
    "transaction_ids",
    "metrics",
)

CREATE_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS {schema}.rule_runs (
        run_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        started_at     timestamptz NOT NULL,
        finished_at    timestamptz NOT NULL DEFAULT now(),
        dataset        text        NOT NULL,
        rule_versions  jsonb       NOT NULL,
        candidates     jsonb       NOT NULL,
        alerts         jsonb       NOT NULL
    );
    CREATE TABLE IF NOT EXISTS {schema}.alerts (
        alert_id      text        PRIMARY KEY,
        rule_id       text        NOT NULL,
        rule_version  integer     NOT NULL,
        account_id    text        NOT NULL,
        alert_date    date        NOT NULL,
        trigger_ts    timestamp   NOT NULL,
        window_start  timestamp   NOT NULL,
        reason        text        NOT NULL,
        metrics       jsonb       NOT NULL,
        status        text        NOT NULL DEFAULT 'New',
        run_id        bigint      NOT NULL REFERENCES {schema}.rule_runs (run_id),
        created_at    timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS {schema}.alert_transactions (
        alert_id        text NOT NULL REFERENCES {schema}.alerts (alert_id) ON DELETE CASCADE,
        transaction_id  text NOT NULL,
        PRIMARY KEY (alert_id, transaction_id)
    );
    CREATE INDEX IF NOT EXISTS alerts_rule_account_idx
        ON {schema}.alerts (rule_id, account_id);
"""


@dataclass(frozen=True)
class Rule:
    """A checked rule spec and its SQL."""

    id: str
    name: str
    version: int
    parameters: dict[str, Any]
    suppression_days: int
    reason: str
    sql_text: str
    spec: dict[str, Any]


@dataclass(frozen=True)
class Candidate:
    """One row returned by a rule's SQL."""

    account_id: str
    trigger_ts: datetime
    window_start: datetime
    transaction_ids: list[str]
    metrics: dict[str, Any]


@dataclass(frozen=True)
class Alert:
    """A candidate that survived suppression, ready to write."""

    alert_id: str
    rule_id: str
    rule_version: int
    account_id: str
    alert_date: date
    trigger_ts: datetime
    window_start: datetime
    reason: str
    metrics: dict[str, Any]
    transaction_ids: list[str]


def check_rule_sql(rule_id: str, sql_text: str, parameters: dict[str, Any]) -> None:
    """Stop if the SQL reads the label or its placeholders do not match the YAML.

    Raises:
        ValueError: naming the problem.
    """
    if match := FORBIDDEN.search(sql_text):
        raise ValueError(f"{rule_id}: SQL must not mention {match.group(0)!r}")
    if "{fct}" not in sql_text:
        raise ValueError(
            f"{rule_id}: SQL must read the table through the fct placeholder"
        )
    placeholders = set(PLACEHOLDER.findall(sql_text))
    missing = sorted(placeholders - parameters.keys())
    unused = sorted(parameters.keys() - placeholders)
    if missing:
        raise ValueError(
            f"{rule_id}: SQL placeholders with no YAML parameter: {missing}"
        )
    if unused:
        raise ValueError(f"{rule_id}: YAML parameters not used in the SQL: {unused}")


def load_rule(path: Path, sql_dir: Path = SQL_DIR) -> Rule:
    """Read and check one rule spec and its SQL file.

    Raises:
        ValueError: if a field is missing or wrong, or the SQL fails its checks.
    """
    with path.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    missing = [k for k in REQUIRED_FIELDS if k not in spec]
    if missing:
        raise ValueError(f"{path.name}: missing fields {missing}")
    rule_id = spec["id"]
    if not path.name.startswith(f"{rule_id}_"):
        raise ValueError(f"{path.name}: file name must start with {rule_id}_")
    params = spec["parameters"]
    bad = {
        k: v
        for k, v in params.items()
        if not isinstance(v, int | float) or isinstance(v, bool)
    }
    if bad:
        raise ValueError(f"{rule_id}: parameters must be numbers: {bad}")
    if not (isinstance(spec["suppression_days"], int) and spec["suppression_days"] > 0):
        raise ValueError(f"{rule_id}: suppression_days must be a positive whole number")
    if not spec["change_log"]:
        raise ValueError(f"{rule_id}: change_log must have at least one entry")
    sql_text = (sql_dir / spec["sql"]).read_text(encoding="utf-8")
    check_rule_sql(rule_id, sql_text, params)
    return Rule(
        id=rule_id,
        name=spec["name"],
        version=spec["version"],
        parameters=params,
        suppression_days=spec["suppression_days"],
        reason=" ".join(spec["reason"].split()),
        sql_text=sql_text,
        spec=spec,
    )


def load_rules(
    rules_dir: Path = RULES_DIR, only: list[str] | None = None
) -> list[Rule]:
    """Load every rule spec in `rules_dir` (or only the given IDs), sorted by ID.

    Raises:
        ValueError: if a requested ID has no spec, or two specs share an ID.
    """
    rules = [load_rule(p) for p in sorted(rules_dir.glob("R[0-9][0-9]_*.yaml"))]
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate rule IDs: {ids}")
    if only:
        unknown = sorted(set(only) - set(ids))
        if unknown:
            raise ValueError(f"No rule spec for {unknown}")
        rules = [r for r in rules if r.id in only]
    return rules


def find_candidates(
    conn: psycopg.Connection, rule: Rule, schema: str
) -> list[Candidate]:
    """Run a rule's SQL on `<schema>.fct_transactions` and return its candidate rows."""
    query = sql.SQL(rule.sql_text).format(
        fct=sql.Identifier(schema, "fct_transactions")
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(query, rule.parameters)
        columns = tuple(d.name for d in cur.description)
        if columns != RESULT_COLUMNS:
            raise ValueError(
                f"{rule.id}: SQL returned {columns}, expected {RESULT_COLUMNS}"
            )
        return [Candidate(*row) for row in cur.fetchall()]


def suppress(candidates: list[Candidate], days: int) -> list[Candidate]:
    """Keep the first candidate per account, then none within `days` of the last kept one."""
    kept: list[Candidate] = []
    last_kept: dict[str, datetime] = {}
    for c in sorted(candidates, key=lambda c: (c.account_id, c.trigger_ts)):
        previous = last_kept.get(c.account_id)
        if previous is None or c.trigger_ts - previous >= timedelta(days=days):
            kept.append(c)
            last_kept[c.account_id] = c.trigger_ts
    return kept


def alert_id(rule_id: str, account_id: str, trigger_ts: datetime) -> str:
    """Deterministic alert ID: the same rule, account and trigger give the same ID."""
    return hashlib.md5(
        f"{rule_id}|{account_id}|{trigger_ts.isoformat()}".encode()
    ).hexdigest()


def to_alert(rule: Rule, candidate: Candidate) -> Alert:
    """Turn a kept candidate into an alert with a plain-English reason."""
    values = {**rule.parameters, **candidate.metrics}
    return Alert(
        alert_id=alert_id(rule.id, candidate.account_id, candidate.trigger_ts),
        rule_id=rule.id,
        rule_version=rule.version,
        account_id=candidate.account_id,
        alert_date=candidate.trigger_ts.date(),
        trigger_ts=candidate.trigger_ts,
        window_start=candidate.window_start,
        reason=rule.reason.format(**values),
        metrics=candidate.metrics,
        transaction_ids=sorted(set(candidate.transaction_ids)),
    )


def write_alerts(
    conn: psycopg.Connection,
    schema: str,
    rules: list[Rule],
    alerts: list[Alert],
    candidate_counts: dict[str, int],
    started_at: datetime,
) -> int:
    """Replace the alerts of `rules` in `<schema>` and log the run, in one transaction.

    Returns:
        The run ID.
    """
    s = sql.Identifier(schema)
    alert_counts = {r.id: sum(a.rule_id == r.id for a in alerts) for r in rules}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL(CREATE_TABLES_SQL).format(schema=s))
        cur.execute(
            sql.SQL(
                "INSERT INTO {}.rule_runs (started_at, dataset, rule_versions, candidates, alerts)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING run_id"
            ).format(s),
            (
                started_at,
                schema,
                Jsonb({r.id: r.version for r in rules}),
                Jsonb(candidate_counts),
                Jsonb(alert_counts),
            ),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            sql.SQL("DELETE FROM {}.alerts WHERE rule_id = ANY(%s)").format(s),
            ([r.id for r in rules],),
        )
        cur.executemany(
            sql.SQL(
                "INSERT INTO {}.alerts (alert_id, rule_id, rule_version, account_id, alert_date,"
                " trigger_ts, window_start, reason, metrics, run_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(s),
            [
                (
                    a.alert_id,
                    a.rule_id,
                    a.rule_version,
                    a.account_id,
                    a.alert_date,
                    a.trigger_ts,
                    a.window_start,
                    a.reason,
                    Jsonb(a.metrics),
                    run_id,
                )
                for a in alerts
            ],
        )
        cur.executemany(
            sql.SQL(
                "INSERT INTO {}.alert_transactions (alert_id, transaction_id) VALUES (%s, %s)"
            ).format(s),
            [(a.alert_id, t) for a in alerts for t in a.transaction_ids],
        )
    return run_id


def run_rules(conn: psycopg.Connection, rules: list[Rule], schema: str) -> list[Alert]:
    """Run `rules` on `<schema>.fct_transactions`, write the alerts and return them."""
    started_at = datetime.now().astimezone()
    alerts: list[Alert] = []
    candidate_counts: dict[str, int] = {}
    for rule in rules:
        candidates = find_candidates(conn, rule, schema)
        kept = suppress(candidates, rule.suppression_days)
        candidate_counts[rule.id] = len(candidates)
        alerts.extend(to_alert(rule, c) for c in kept)
        logger.info(
            "%s %s: %d candidates, %d alerts after %d-day suppression",
            rule.id,
            rule.name,
            len(candidates),
            len(kept),
            rule.suppression_days,
        )
    run_id = write_alerts(conn, schema, rules, alerts, candidate_counts, started_at)
    logger.info("Run %d: wrote %d alerts to %s.alerts", run_id, len(alerts), schema)
    return alerts


def main() -> None:
    """Run the rules from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample",
        action="store_true",
        help="run on the 10%% account sample (marts_sample); development only",
    )
    parser.add_argument(
        "--rule", action="append", help="run only this rule ID (repeatable)"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    schema = SAMPLE_SCHEMA if args.sample else FULL_SCHEMA
    rules = load_rules(only=args.rule)
    logger.info(
        "Running %s on %s.fct_transactions", ", ".join(r.id for r in rules), schema
    )
    with get_connection() as conn:
        alerts = run_rules(conn, rules, schema)
    if alerts:
        logger.info(
            "Example: %s %s - %s",
            alerts[0].rule_id,
            alerts[0].account_id,
            alerts[0].reason,
        )


if __name__ == "__main__":
    main()
