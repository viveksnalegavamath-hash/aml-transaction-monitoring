"""Run the AML rules and write alerts.

Each rule is a YAML spec in `rules/` (every threshold lives there) plus a SQL
file in `rules/sql/`. The engine:

1. loads and checks every spec (required fields, every SQL placeholder has a
   YAML parameter and every parameter is used, no reference to the label);
   a spec with `enabled: false` is skipped unless its ID is asked for;
2. runs each rule's SQL on `fct_transactions` (and, if the rule needs KYC,
   `dim_customer`), binding thresholds as query parameters (never string
   formatting) and the tables as quoted identifiers;
3. streams each rule's candidates from a server-side cursor, sorted by
   account and time, and suppresses repeat alerts for the same account within
   the rule's suppression period while reading, so memory holds the alerts,
   not every candidate (the full data has millions of candidates);
4. writes each rule's `alerts`, `alert_transactions` and a `rule_runs` audit
   row as soon as the rule finishes, in one transaction per rule, replacing
   that rule's earlier alerts.

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
import time
from collections.abc import Iterable, Iterator
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
# Tables a rule may read: braces placeholder -> table in the same schema.
TABLES = {"fct": "fct_transactions", "dim_customer": "dim_customer"}
TABLE_PLACEHOLDER = re.compile(r"\{(\w+)\}")
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
    enabled: bool = True


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
    unknown_tables = sorted(set(TABLE_PLACEHOLDER.findall(sql_text)) - TABLES.keys())
    if unknown_tables:
        raise ValueError(f"{rule_id}: SQL reads unknown tables {unknown_tables}")
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
    enabled = spec.get("enabled", True)
    if enabled is not True and enabled is not False:
        raise ValueError(f"{rule_id}: enabled must be true or false")
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
        enabled=enabled,
    )


def load_rules(
    rules_dir: Path = RULES_DIR,
    only: list[str] | None = None,
    include_disabled: bool = False,
) -> list[Rule]:
    """Load the enabled rule specs in `rules_dir` (or only the given IDs), sorted by ID.

    Rules asked for by ID are loaded even if disabled; `include_disabled`
    loads every spec (e.g. for rule names in reports).

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
        return [r for r in rules if r.id in only]
    return [r for r in rules if r.enabled or include_disabled]


def iter_candidates(
    conn: psycopg.Connection, rule: Rule, schema: str, batch_size: int = 10_000
) -> Iterator[Candidate]:
    """Stream a rule's candidate rows, sorted by account and trigger time.

    A server-side (named) cursor makes PostgreSQL send `batch_size` rows at a
    time; a normal cursor would load the whole result into Python memory.
    `COLLATE "C"` sorts account IDs by code point, the same order as Python.
    The rule SQL is wrapped on its own lines, so a trailing comment cannot
    swallow the closing bracket.
    """
    rule_query = sql.SQL(rule.sql_text).format(
        **{name: sql.Identifier(schema, table) for name, table in TABLES.items()}
    )
    query = sql.SQL(
        "select * from (\n{}\n) as candidates"
        ' order by account_id collate "C", trigger_ts'
    ).format(rule_query)
    with (
        conn.transaction(),
        conn.cursor(name=f"candidates_{rule.id.lower()}") as cur,
    ):
        cur.itersize = batch_size
        cur.execute(query, rule.parameters)
        columns = tuple(d.name for d in cur.description)
        if columns != RESULT_COLUMNS:
            raise ValueError(
                f"{rule.id}: SQL returned {columns}, expected {RESULT_COLUMNS}"
            )
        for row in cur:
            yield Candidate(*row)


def find_candidates(
    conn: psycopg.Connection, rule: Rule, schema: str
) -> list[Candidate]:
    """All of a rule's candidate rows as a list (for tests and small data)."""
    return list(iter_candidates(conn, rule, schema))


def suppress_sorted(candidates: Iterable[Candidate], days: int) -> Iterator[Candidate]:
    """Keep the first candidate per account, then none within `days` of the last kept one.

    Candidates must arrive sorted by account and trigger time, so only the
    current account is held in memory.

    Raises:
        ValueError: if the candidates are not sorted.
    """
    window = timedelta(days=days)
    previous: tuple[str, datetime] | None = None
    last_account: str | None = None
    last_kept: datetime | None = None
    for c in candidates:
        key = (c.account_id, c.trigger_ts)
        if previous is not None and key < previous:
            raise ValueError("candidates must be sorted by account and trigger time")
        previous = key
        if c.account_id != last_account or c.trigger_ts - last_kept >= window:
            yield c
            last_account, last_kept = c.account_id, c.trigger_ts


def suppress(candidates: list[Candidate], days: int) -> list[Candidate]:
    """`suppress_sorted` for an unsorted list."""
    ordered = sorted(candidates, key=lambda c: (c.account_id, c.trigger_ts))
    return list(suppress_sorted(ordered, days))


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

    Alerts and their transaction links are loaded with COPY: the full data can
    have millions of links.

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
        with cur.copy(
            sql.SQL(
                "COPY {}.alerts (alert_id, rule_id, rule_version, account_id, alert_date,"
                " trigger_ts, window_start, reason, metrics, run_id) FROM STDIN"
            ).format(s)
        ) as copy:
            for a in alerts:
                copy.write_row(
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
                )
        with cur.copy(
            sql.SQL(
                "COPY {}.alert_transactions (alert_id, transaction_id) FROM STDIN"
            ).format(s)
        ) as copy:
            for a in alerts:
                for t in a.transaction_ids:
                    copy.write_row((a.alert_id, t))
    return run_id


def run_rules(
    conn: psycopg.Connection, rules: list[Rule], schema: str
) -> dict[str, int]:
    """Run `rules` on `<schema>`, writing each rule's alerts as soon as it finishes.

    Returns:
        {rule ID: number of alerts written}.
    """
    alert_counts: dict[str, int] = {}
    for rule in rules:
        started_at = datetime.now().astimezone()
        clock = time.monotonic()
        logger.info("%s %s: started", rule.id, rule.name)
        n_candidates = 0

        def counted(candidates: Iterator[Candidate]) -> Iterator[Candidate]:
            nonlocal n_candidates
            for c in candidates:
                n_candidates += 1
                yield c

        stream = counted(iter_candidates(conn, rule, schema))
        alerts = [
            to_alert(rule, c) for c in suppress_sorted(stream, rule.suppression_days)
        ]
        run_id = write_alerts(
            conn, schema, [rule], alerts, {rule.id: n_candidates}, started_at
        )
        alert_counts[rule.id] = len(alerts)
        logger.info(
            "%s %s: %d candidates, %d alerts after %d-day suppression;"
            " run %d written to %s.alerts in %.0f s",
            rule.id,
            rule.name,
            n_candidates,
            len(alerts),
            rule.suppression_days,
            run_id,
            schema,
            time.monotonic() - clock,
        )
        if alerts:
            logger.info(
                "%s example: %s - %s", rule.id, alerts[0].account_id, alerts[0].reason
            )
    return alert_counts


def main() -> None:
    """Run the rules from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample",
        action="store_true",
        help="run on the 10%% account sample (marts_sample); development only",
    )
    parser.add_argument(
        "--rule",
        action="append",
        help="run only this rule ID (repeatable; also runs disabled rules)",
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
        counts = run_rules(conn, rules, schema)
    logger.info("Done: %s", ", ".join(f"{r} {n:,} alerts" for r, n in counts.items()))


if __name__ == "__main__":
    main()
