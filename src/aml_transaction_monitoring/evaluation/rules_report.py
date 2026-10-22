"""Evaluate rule alerts against the suspicious label.

Runs AFTER the rules engine: it reads the alerts that exist, then joins the
labels (`evaluation.eval_transaction_labels`) to them. Per rule and period:

- alerts and alerts per week (analyst workload);
- precision: share of alerts covering at least one suspicious transaction;
- recall: share of suspicious transactions covered by at least one alert;
- a rule x typology matrix: share of each typology's suspicious transactions
  caught by each rule, plus "any rule".

Never accuracy. Periods come from config/evaluation.yaml (tuning / holdout;
the holdout is the headline). With --sample the report is only printed:
files in docs/results/ are written from the full data only.

Usage:
    uv run python -m aml_transaction_monitoring.evaluation.rules_report
    uv run python -m aml_transaction_monitoring.evaluation.rules_report --sample
"""

import argparse
import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT
from aml_transaction_monitoring.rules.engine import load_rules

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "evaluation.yaml"
ANY_RULE = "any rule"

# Labels are joined only here, to alerts the engine already wrote.
METRICS_SQL = """
with periods (period, start_date, end_date) as (
    values ('tuning', %(tuning_start)s::date, %(tuning_end)s::date),
           ('holdout', %(holdout_start)s::date, %(holdout_end)s::date)
),

alert_outcome as (
    select a.alert_id, a.rule_id, a.alert_date, bool_or(e.is_laundering) as hit
    from {marts}.alerts as a
    join {marts}.alert_transactions as t using (alert_id)
    join {labels}.eval_transaction_labels as e using (transaction_id)
    group by a.alert_id, a.rule_id, a.alert_date
),

alert_stats as (
    select o.rule_id, p.period, count(*) as alerts, count(*) filter (where o.hit) as true_alerts
    from alert_outcome as o
    join periods as p on o.alert_date between p.start_date and p.end_date
    group by o.rule_id, p.period
),

suspicious as (
    select e.transaction_id, e.typology, p.period
    from {labels}.eval_transaction_labels as e
    join {marts}.fct_transactions as f using (transaction_id)
    join periods as p on f.transaction_date between p.start_date and p.end_date
    where e.is_laundering
),

caught as (
    select distinct a.rule_id, t.transaction_id
    from {marts}.alerts as a
    join {marts}.alert_transactions as t using (alert_id)
    union all
    select distinct %(any_rule)s, t.transaction_id
    from {marts}.alert_transactions as t
),

-- Every rule that has been run on this dataset, even with no alerts, plus "any rule".
rules as (
    select distinct jsonb_object_keys(rule_versions) as rule_id from {marts}.rule_runs
    union
    select %(any_rule)s
),

recall as (
    select r.rule_id, s.period, s.typology,
           count(*) as suspicious, count(c.transaction_id) as caught
    from suspicious as s
    cross join rules as r
    left join caught as c
        on c.rule_id = r.rule_id and c.transaction_id = s.transaction_id
    group by r.rule_id, s.period, s.typology
)

select r.rule_id, r.period, r.typology, r.suspicious, r.caught,
       coalesce(a.alerts, 0) as alerts, coalesce(a.true_alerts, 0) as true_alerts
from recall as r
left join alert_stats as a on a.rule_id = r.rule_id and a.period = r.period
order by r.rule_id, r.period, r.typology
"""


@dataclass(frozen=True)
class RuleResult:
    """Headline numbers for one rule (or "any rule") in one period."""

    rule_id: str
    period: str
    alerts: int | None  # None for "any rule": alerts overlap across rules
    alerts_per_week: float | None
    precision: float | None
    suspicious: int
    caught: int

    @property
    def recall(self) -> float | None:
        return self.caught / self.suspicious if self.suspicious else None


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read the evaluation periods."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def weeks(period: dict[str, date]) -> float:
    """Length of a period in weeks, counting both end dates."""
    return ((period["end"] - period["start"]).days + 1) / 7


def fetch_rows(
    conn: psycopg.Connection, marts: str, labels: str, config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return one row per rule, period and typology with counts."""
    p = config["periods"]
    params = {
        "tuning_start": p["tuning"]["start"],
        "tuning_end": p["tuning"]["end"],
        "holdout_start": p["holdout"]["start"],
        "holdout_end": p["holdout"]["end"],
        "any_rule": ANY_RULE,
    }
    query = sql.SQL(METRICS_SQL).format(
        marts=sql.Identifier(marts), labels=sql.Identifier(labels)
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(query, params)
        names = [d.name for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


VERSIONS_SQL = """
select distinct on (rule_id) rule_id, (rule_versions ->> rule_id)::int as version, run_id
from {marts}.rule_runs, jsonb_object_keys(rule_versions) as rule_id
order by rule_id, run_id desc
"""


def fetch_versions(conn: psycopg.Connection, marts: str) -> dict[str, tuple[int, int]]:
    """Return {rule ID: (version, run ID)} of the latest run of each rule."""
    query = sql.SQL(VERSIONS_SQL).format(marts=sql.Identifier(marts))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(query)
        return {
            rule_id: (version, run_id) for rule_id, version, run_id in cur.fetchall()
        }


def summarise(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[RuleResult]:
    """Combine typology rows into one result per rule and period."""
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        g = grouped.setdefault(
            (r["rule_id"], r["period"]),
            {
                "suspicious": 0,
                "caught": 0,
                "alerts": r["alerts"],
                "true_alerts": r["true_alerts"],
            },
        )
        g["suspicious"] += r["suspicious"]
        g["caught"] += r["caught"]
    results = []
    for (rule_id, period), g in sorted(
        grouped.items(), key=lambda kv: (kv[0][0] == ANY_RULE, kv[0])
    ):
        is_any = rule_id == ANY_RULE
        alerts = None if is_any else g["alerts"]
        results.append(
            RuleResult(
                rule_id=rule_id,
                period=period,
                alerts=alerts,
                alerts_per_week=None
                if is_any
                else alerts / weeks(config["periods"][period]),
                precision=None if is_any or not alerts else g["true_alerts"] / alerts,
                suspicious=g["suspicious"],
                caught=g["caught"],
            )
        )
    return results


def matrix(
    rows: list[dict[str, Any]], period: str
) -> tuple[list[str], list[str], dict]:
    """Return (typologies, rules, {(typology, rule): share caught}) for one period."""
    cells = {
        (r["typology"], r["rule_id"]): r["caught"] / r["suspicious"]
        for r in rows
        if r["period"] == period
    }
    typologies = sorted({t for t, _ in cells})
    rules = sorted({rule for _, rule in cells}, key=lambda x: (x == ANY_RULE, x))
    return typologies, rules, cells


def pct(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.1f}%"


def md_table(header: list[str], body: list[list[str]]) -> str:
    """A GitHub-flavoured markdown table."""
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def render(
    results: list[RuleResult],
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    dataset: str,
    rule_names: dict[str, str],
    versions: dict[str, tuple[int, int]],
) -> str:
    """The report as markdown: headline table per period, then the typology matrix."""
    p = config["periods"]
    out = [
        "# Rules evaluation",
        "",
        (
            f"Dataset: `{dataset}` · generated "
            f"{datetime.now().astimezone():%Y-%m-%d %H:%M %Z} · "
            "labels joined only after alerts exist."
        ),
        "",
        "Rule versions: "
        + ", ".join(
            f"{r} v{v} (run {run})" for r, (v, run) in sorted(versions.items())
        ),
        "",
        f"- **Hold-out (headline):** {p['holdout']['start']} to {p['holdout']['end']}",
        f"- **Tuning:** {p['tuning']['start']} to {p['tuning']['end']}",
        "",
        (
            "Precision = share of alerts covering at least one suspicious transaction. "
            "Recall = share of suspicious transactions covered by at least one alert. "
            "Never accuracy."
        ),
    ]
    for period, title in (
        ("holdout", "Hold-out (headline)"),
        ("tuning", "Tuning period"),
    ):
        body = [
            [
                r.rule_id,
                rule_names.get(r.rule_id, ""),
                "–" if r.alerts is None else f"{r.alerts:,}",
                "–" if r.alerts_per_week is None else f"{r.alerts_per_week:,.1f}",
                pct(r.precision),
                pct(r.recall),
                f"{r.caught:,} / {r.suspicious:,}",
            ]
            for r in results
            if r.period == period
        ]
        header = [
            "Rule",
            "Name",
            "Alerts",
            "Alerts / week",
            "Precision",
            "Recall",
            "Caught / suspicious",
        ]
        out += ["", f"## {title}", "", md_table(header, body)]
        typologies, rules, cells = matrix(rows, period)
        grid = [[t] + [pct(cells.get((t, rule))) for rule in rules] for t in typologies]
        out += [
            "",
            f"### Rule × typology, {title.lower()}",
            "",
            md_table(["Typology", *rules], grid),
        ]
    return "\n".join(out) + "\n"


def write_csv(results: list[RuleResult], path: Path) -> None:
    """Save the headline numbers as CSV."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "rule_id",
                "period",
                "alerts",
                "alerts_per_week",
                "precision",
                "recall",
                "caught",
                "suspicious",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.rule_id,
                    r.period,
                    r.alerts,
                    r.alerts_per_week,
                    r.precision,
                    r.recall,
                    r.caught,
                    r.suspicious,
                ]
            )


def main() -> None:
    """Build the report; write files only for the full data."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample", action="store_true", help="evaluate the dev sample (printed only)"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = load_config()
    marts, labels = (
        ("marts_sample", "evaluation_sample")
        if args.sample
        else ("marts", "evaluation")
    )
    rule_names = {r.id: r.name for r in load_rules()}
    with get_connection() as conn:
        rows = fetch_rows(conn, marts, labels, config)
        versions = fetch_versions(conn, marts)
    if not rows:
        raise RuntimeError(f"No alerts in {marts}.alerts: run the rules engine first")
    results = summarise(rows, config)
    report = render(results, rows, config, marts, rule_names, versions)
    if args.sample:
        logger.info("DEV SAMPLE - not for reporting:\n%s", report)
        return
    out_dir = PROJECT_ROOT / config["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rules_evaluation.md").write_text(report, encoding="utf-8")
    write_csv(results, out_dir / "rules_evaluation.csv")
    logger.info("Wrote %s and rules_evaluation.csv", out_dir / "rules_evaluation.md")


if __name__ == "__main__":
    main()
