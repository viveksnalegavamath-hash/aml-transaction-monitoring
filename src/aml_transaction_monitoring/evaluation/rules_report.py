"""Evaluate rule alerts against the suspicious label.

Runs AFTER the rules engine: it reads the alerts that exist, then joins the
labels (`evaluation.eval_transaction_labels`) to them. Every rule is scored
the same way, by the alerted account and the alert time, never by which
transactions a rule happens to link to its alerts (`detection_days` in
config/evaluation.yaml). Per rule and period:

- alerts and alerts per week (analyst workload);
- precision: share of alerts whose account had a suspicious transaction (sent
  or received) in the detection_days up to the alert;
- lift: precision / base rate, where the base rate is the share of active
  accounts in the period that are suspicious (precision of random selection);
- account recall (headline): share of suspicious accounts (sender or receiver
  of a suspicious transaction in the period) with an alert in the period;
- transaction recall: share of suspicious transactions whose sender or
  receiver the rule alerted at the time or up to detection_days after;
- a rule x typology matrix of transaction recall, plus "any rule".

Never accuracy. Periods come from config/evaluation.yaml (tuning / holdout;
the holdout is the headline). With --sample only sampled accounts count
(`dim_account.in_dev_sample`; the others have incomplete histories in the
sample) and the report is only printed: files in docs/results/ are written
from the full data only.

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

# Labels are joined only here, to alerts the engine already wrote. Shared by
# the three queries below.
COMMON_SQL = """
with periods (period, start_date, end_date) as (
    values ('tuning', %(tuning_start)s::date, %(tuning_end)s::date),
           ('holdout', %(holdout_start)s::date, %(holdout_end)s::date)
),

-- Accounts the evaluation counts: all of them on the full data; on the dev
-- sample only sampled accounts, the ones with complete histories.
scope as (
    select account_id from {marts}.dim_account
    where in_dev_sample or not %(sample_only)s
),

alerts as (
    select a.alert_id, a.rule_id, a.account_id, a.alert_date, a.trigger_ts
    from {marts}.alerts as a
    join scope using (account_id)
),

-- Each suspicious transaction once per in-scope party (sender and receiver).
suspicious as (
    select distinct f.transaction_id, f.transaction_ts, e.typology, p.period, party.account_id
    from {labels}.eval_transaction_labels as e
    join {marts}.fct_transactions as f using (transaction_id)
    join periods as p on f.transaction_date between p.start_date and p.end_date
    cross join lateral (values (f.sender_account), (f.receiver_account)) as party (account_id)
    join scope on scope.account_id = party.account_id
    where e.is_laundering
),

-- Every rule that has been run on this dataset, even with no alerts, plus "any rule".
rules as (
    select distinct jsonb_object_keys(rule_versions) as rule_id from {marts}.rule_runs
    union
    select %(any_rule)s
)
"""

# One row per rule and period: alerts, true alerts, suspicious and caught accounts.
RULES_SQL = (
    COMMON_SQL
    + """,

alert_outcome as (
    select a.alert_id, a.rule_id, a.alert_date, count(s.transaction_id) > 0 as hit
    from alerts as a
    left join suspicious as s
      on s.account_id = a.account_id
     and s.transaction_ts <= a.trigger_ts
     and s.transaction_ts >= a.trigger_ts - make_interval(days => %(detection_days)s)
    group by a.alert_id, a.rule_id, a.alert_date
),

alert_stats as (
    select o.rule_id, p.period, count(*) as alerts, count(*) filter (where o.hit) as true_alerts
    from alert_outcome as o
    join periods as p on o.alert_date between p.start_date and p.end_date
    group by o.rule_id, p.period
),

alerted as (
    select distinct a.rule_id, p.period, a.account_id
    from alerts as a
    join periods as p on a.alert_date between p.start_date and p.end_date
),

alerted_any as (
    select rule_id, period, account_id from alerted
    union
    select %(any_rule)s, period, account_id from alerted
),

suspicious_accounts as (
    select distinct period, account_id from suspicious
),

account_recall as (
    select r.rule_id, s.period,
           count(*) as suspicious_accounts, count(c.account_id) as caught_accounts
    from suspicious_accounts as s
    cross join rules as r
    left join alerted_any as c
        on c.rule_id = r.rule_id and c.period = s.period and c.account_id = s.account_id
    group by r.rule_id, s.period
)

select r.rule_id, p.period,
       coalesce(a.alerts, 0) as alerts, coalesce(a.true_alerts, 0) as true_alerts,
       coalesce(c.suspicious_accounts, 0) as suspicious_accounts,
       coalesce(c.caught_accounts, 0) as caught_accounts
from rules as r
cross join periods as p
left join alert_stats as a on a.rule_id = r.rule_id and a.period = p.period
left join account_recall as c on c.rule_id = r.rule_id and c.period = p.period
order by r.rule_id, p.period
"""
)

# One row per rule, period and typology: suspicious and caught transactions.
TYPOLOGY_SQL = (
    COMMON_SQL
    + """,

caught_by_rule as (
    select distinct a.rule_id, s.transaction_id
    from suspicious as s
    join alerts as a
      on a.account_id = s.account_id
     and a.trigger_ts >= s.transaction_ts
     and a.trigger_ts <= s.transaction_ts + make_interval(days => %(detection_days)s)
),

caught as (
    select rule_id, transaction_id from caught_by_rule
    union
    select %(any_rule)s, transaction_id from caught_by_rule
),

suspicious_transactions as (
    select distinct transaction_id, period, typology from suspicious
)

select r.rule_id, s.period, s.typology,
       count(*) as suspicious, count(c.transaction_id) as caught
from suspicious_transactions as s
cross join rules as r
left join caught as c on c.rule_id = r.rule_id and c.transaction_id = s.transaction_id
group by r.rule_id, s.period, s.typology
order by r.rule_id, s.period, s.typology
"""
)

# One row per period: active accounts and suspicious accounts (the base rate).
BASE_RATE_SQL = (
    COMMON_SQL
    + """,

active as (
    select p.period, count(distinct party.account_id) as active_accounts
    from {marts}.fct_transactions as f
    join periods as p on f.transaction_date between p.start_date and p.end_date
    cross join lateral (values (f.sender_account), (f.receiver_account)) as party (account_id)
    join scope on scope.account_id = party.account_id
    group by p.period
)

select p.period, coalesce(a.active_accounts, 0) as active_accounts,
       (select count(distinct s.account_id) from suspicious as s
        where s.period = p.period) as suspicious_accounts
from periods as p
left join active as a using (period)
order by p.period
"""
)


@dataclass(frozen=True)
class BaseRate:
    """Share of active accounts in a period that are suspicious."""

    period: str
    active_accounts: int
    suspicious_accounts: int

    @property
    def rate(self) -> float | None:
        if not self.active_accounts:
            return None
        return self.suspicious_accounts / self.active_accounts


@dataclass(frozen=True)
class RuleResult:
    """Headline numbers for one rule (or "any rule") in one period."""

    rule_id: str
    period: str
    alerts: int | None  # None for "any rule": alerts overlap across rules
    alerts_per_week: float | None
    precision: float | None
    lift: float | None
    suspicious_accounts: int
    caught_accounts: int
    suspicious_transactions: int
    caught_transactions: int

    @property
    def account_recall(self) -> float | None:
        if not self.suspicious_accounts:
            return None
        return self.caught_accounts / self.suspicious_accounts

    @property
    def transaction_recall(self) -> float | None:
        if not self.suspicious_transactions:
            return None
        return self.caught_transactions / self.suspicious_transactions


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read the evaluation periods and detection window."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def weeks(period: dict[str, date]) -> float:
    """Length of a period in weeks, counting both end dates."""
    return ((period["end"] - period["start"]).days + 1) / 7


def _query(
    conn: psycopg.Connection,
    template: str,
    marts: str,
    labels: str,
    config: dict[str, Any],
    sample_only: bool,
) -> list[dict[str, Any]]:
    """Run one of the evaluation queries and return its rows as dicts."""
    p = config["periods"]
    params = {
        "tuning_start": p["tuning"]["start"],
        "tuning_end": p["tuning"]["end"],
        "holdout_start": p["holdout"]["start"],
        "holdout_end": p["holdout"]["end"],
        "detection_days": config["detection_days"],
        "sample_only": sample_only,
        "any_rule": ANY_RULE,
    }
    query = sql.SQL(template).format(
        marts=sql.Identifier(marts), labels=sql.Identifier(labels)
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(query, params)
        names = [d.name for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def fetch_rule_rows(
    conn: psycopg.Connection,
    marts: str,
    labels: str,
    config: dict[str, Any],
    sample_only: bool = False,
) -> list[dict[str, Any]]:
    """Return one row per rule and period: alert and account counts."""
    return _query(conn, RULES_SQL, marts, labels, config, sample_only)


def fetch_rows(
    conn: psycopg.Connection,
    marts: str,
    labels: str,
    config: dict[str, Any],
    sample_only: bool = False,
) -> list[dict[str, Any]]:
    """Return one row per rule, period and typology: transaction counts."""
    return _query(conn, TYPOLOGY_SQL, marts, labels, config, sample_only)


def fetch_base_rates(
    conn: psycopg.Connection,
    marts: str,
    labels: str,
    config: dict[str, Any],
    sample_only: bool = False,
) -> dict[str, BaseRate]:
    """Return {period: base rate}."""
    rows = _query(conn, BASE_RATE_SQL, marts, labels, config, sample_only)
    return {r["period"]: BaseRate(**r) for r in rows}


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


def summarise(
    rule_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    base_rates: dict[str, BaseRate],
    config: dict[str, Any],
) -> list[RuleResult]:
    """Combine rule rows, typology rows and base rates into one result per rule and period."""
    transactions: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        t = transactions.setdefault((r["rule_id"], r["period"]), [0, 0])
        t[0] += r["suspicious"]
        t[1] += r["caught"]
    results = []
    for r in sorted(
        rule_rows, key=lambda r: (r["rule_id"] == ANY_RULE, r["rule_id"], r["period"])
    ):
        rule_id, period = r["rule_id"], r["period"]
        is_any = rule_id == ANY_RULE
        alerts = None if is_any else r["alerts"]
        precision = None if is_any or not alerts else r["true_alerts"] / alerts
        base = base_rates[period].rate if period in base_rates else None
        suspicious, caught = transactions.get((rule_id, period), (0, 0))
        results.append(
            RuleResult(
                rule_id=rule_id,
                period=period,
                alerts=alerts,
                alerts_per_week=None
                if is_any
                else alerts / weeks(config["periods"][period]),
                precision=precision,
                lift=precision / base if precision is not None and base else None,
                suspicious_accounts=r["suspicious_accounts"],
                caught_accounts=r["caught_accounts"],
                suspicious_transactions=suspicious,
                caught_transactions=caught,
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


def pct(value: float | None, digits: int = 1) -> str:
    return "–" if value is None else f"{100 * value:.{digits}f}%"


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
    base_rates: dict[str, BaseRate],
    config: dict[str, Any],
    dataset: str,
    rule_names: dict[str, str],
    versions: dict[str, tuple[int, int]],
) -> str:
    """The report as markdown: headline table per period, then the typology matrix."""
    p = config["periods"]
    days = config["detection_days"]
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
            "Every rule is scored the same way, by the alerted account and the alert "
            "time, not by the transactions a rule links to its alerts. "
            "A suspicious account is the sender or receiver of a suspicious transaction."
        ),
        "",
        (
            f"- **Precision** = share of alerts whose account had a suspicious "
            f"transaction in the {days} days up to the alert."
        ),
        (
            "- **Base rate** = share of active accounts in the period that are "
            "suspicious: the precision of picking accounts at random. "
            "**Lift** = precision / base rate. The base rate looks at the whole "
            f"period, precision at {days} days, so lift is slightly understated "
            "(conservative)."
        ),
        (
            "- **Account recall** (headline) = share of suspicious accounts with an "
            "alert in the period."
        ),
        (
            "- **Transaction recall** = share of suspicious transactions whose sender "
            f"or receiver was alerted at the time or up to {days} days after."
        ),
        "- Never accuracy.",
    ]
    for period, title in (
        ("holdout", "Hold-out (headline)"),
        ("tuning", "Tuning period"),
    ):
        base = base_rates.get(period)
        body = [
            [
                r.rule_id,
                rule_names.get(r.rule_id, ""),
                "–" if r.alerts is None else f"{r.alerts:,}",
                "–" if r.alerts_per_week is None else f"{r.alerts_per_week:,.1f}",
                pct(r.precision),
                "–" if r.lift is None else f"{r.lift:,.1f}×",
                pct(r.account_recall),
                f"{r.caught_accounts:,} / {r.suspicious_accounts:,}",
                pct(r.transaction_recall),
                f"{r.caught_transactions:,} / {r.suspicious_transactions:,}",
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
            "Lift",
            "Account recall",
            "Accounts caught",
            "Transaction recall",
            "Transactions caught",
        ]
        out += ["", f"## {title}", ""]
        if base is not None:
            out += [
                (
                    f"Base rate: {pct(base.rate, 2)} ({base.suspicious_accounts:,} "
                    f"suspicious of {base.active_accounts:,} active accounts)."
                ),
                "",
            ]
        out += [md_table(header, body)]
        typologies, rules, cells = matrix(rows, period)
        grid = [[t] + [pct(cells.get((t, rule))) for rule in rules] for t in typologies]
        out += [
            "",
            f"### Rule × typology (transaction recall), {title.lower()}",
            "",
            md_table(["Typology", *rules], grid),
        ]
    return "\n".join(out) + "\n"


def write_csv(
    results: list[RuleResult], base_rates: dict[str, BaseRate], path: Path
) -> None:
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
                "base_rate",
                "lift",
                "account_recall",
                "caught_accounts",
                "suspicious_accounts",
                "transaction_recall",
                "caught_transactions",
                "suspicious_transactions",
            ]
        )
        for r in results:
            base = base_rates.get(r.period)
            writer.writerow(
                [
                    r.rule_id,
                    r.period,
                    r.alerts,
                    r.alerts_per_week,
                    r.precision,
                    None if base is None else base.rate,
                    r.lift,
                    r.account_recall,
                    r.caught_accounts,
                    r.suspicious_accounts,
                    r.transaction_recall,
                    r.caught_transactions,
                    r.suspicious_transactions,
                ]
            )


def main() -> None:
    """Build the report; write files only for the full data."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample",
        action="store_true",
        help="evaluate the dev sample, sampled accounts only (printed only)",
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
        rule_rows = fetch_rule_rows(conn, marts, labels, config, args.sample)
        rows = fetch_rows(conn, marts, labels, config, args.sample)
        base_rates = fetch_base_rates(conn, marts, labels, config, args.sample)
        versions = fetch_versions(conn, marts)
    if not versions:
        raise RuntimeError(
            f"No rule runs in {marts}.rule_runs: run the rules engine first"
        )
    results = summarise(rule_rows, rows, base_rates, config)
    report = render(results, rows, base_rates, config, marts, rule_names, versions)
    if args.sample:
        logger.info(
            "DEV SAMPLE (sampled accounts only) - not for reporting:\n%s", report
        )
        return
    out_dir = PROJECT_ROOT / config["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rules_evaluation.md").write_text(report, encoding="utf-8")
    write_csv(results, base_rates, out_dir / "rules_evaluation.csv")
    logger.info("Wrote %s and rules_evaluation.csv", out_dir / "rules_evaluation.md")


if __name__ == "__main__":
    main()
