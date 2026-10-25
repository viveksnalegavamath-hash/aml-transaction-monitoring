"""Tune rule thresholds on the tuning months only (above/below-the-line testing).

For each rule in config/tuning.yaml:

1. generate candidates ONCE on the full data, at the loosest values of the
   grid (the rule's real SQL with looser parameters; cached in data/tuning/);
2. for every option in the grid, keep the candidates that pass its stricter
   thresholds (a filter on the candidate metrics: tighter thresholds can only
   remove candidates), suppress repeats exactly as the engine does, and score
   the alerts on the TUNING MONTHS with the report's definitions (precision,
   lift, account recall; scored by alerted account and time);
3. write the table to docs/results/tuning_<rule>.md.

Self-check: the plan's values must reproduce the baseline report's tuning
numbers; the run stops if they do not. The hold-out is never read here.

`combine` scores a proposed set of values for all tuned rules together with
the untuned rules' baseline alerts: total alerts per week and "any rule"
account recall, tuning months only.

Usage:
    uv run python -m aml_transaction_monitoring.evaluation.tuning grid --rule R07
    uv run python -m aml_transaction_monitoring.evaluation.tuning combine \\
        --pick R07=plan,both,10,50000 ...
"""

import argparse
import csv
import itertools
import logging
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
import yaml

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.evaluation.rules_report import (
    fetch_base_rates,
    load_config,
    weeks,
)
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT
from aml_transaction_monitoring.rules.engine import (
    PLACEHOLDER,
    SQL_DIR,
    Rule,
    check_rule_sql,
    iter_candidates,
    load_rules,
)

logger = logging.getLogger(__name__)

TUNING_CONFIG = PROJECT_ROOT / "config" / "tuning.yaml"
CACHE_DIR = PROJECT_ROOT / "data" / "tuning"
VARIANT_SQL_DIR = Path(__file__).resolve().parent / "sql"
MARTS, LABELS = "marts", "evaluation"
PERIOD = "tuning"

# Which candidate metrics each rule's options filter on, and how.
METRICS = {
    "R03": ["n_receivers", "total_gbp"],
    "R06": ["amount_gbp", "baseline_avg_gbp"],
    "R07": ["is_spike", "is_dormant", "recent_gbp", "baseline_avg_gbp"],
}


def _r03(df: pd.DataFrame, o: dict[str, Any]) -> np.ndarray:
    return (df["n_receivers"] >= o["min_receivers"]) & (
        df["total_gbp"] >= o["min_total_gbp"]
    )


def _r06(df: pd.DataFrame, o: dict[str, Any]) -> np.ndarray:
    return (df["amount_gbp"] > o["min_amount_gbp"]) & (
        df["amount_gbp"] > o["multiple"] * df["baseline_avg_gbp"]
    )


def _r07(df: pd.DataFrame, o: dict[str, Any]) -> np.ndarray:
    spike = df["is_spike"] & (
        df["recent_gbp"] > o["spike_multiple"] * df["baseline_avg_gbp"].fillna(np.inf)
    )
    dormant = df["is_dormant"]
    clause = {"both": spike | dormant, "spike": spike, "dormant": dormant}[o["clauses"]]
    return clause & (df["recent_gbp"] >= o["min_volume_gbp"])


FILTERS: dict[str, Callable[[pd.DataFrame, dict[str, Any]], np.ndarray]] = {
    "R03": _r03,
    "R06": _r06,
    "R07": _r07,
}


@dataclass(frozen=True)
class Truth:
    """What the scoring needs from the labels, for the tuning period only."""

    suspicious_ts: dict[
        str, list[datetime]
    ]  # account -> sorted suspicious times (any period)
    suspicious_accounts: set[str]  # parties of suspicious transactions in the period
    base_rate: float
    start: date
    end: date
    detection: timedelta
    weeks: float


def load_tuning_config(path: Path = TUNING_CONFIG) -> dict[str, Any]:
    """Read the tuning grids."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


SUSPICIOUS_SQL = """
select f.transaction_ts, f.transaction_date, f.sender_account, f.receiver_account
from evaluation.eval_transaction_labels as e
join marts.fct_transactions as f using (transaction_id)
where e.is_laundering
"""


def load_truth(conn: psycopg.Connection, config: dict[str, Any]) -> Truth:
    """Labels for scoring: joined only here, to candidates the rules produced."""
    period = config["periods"][PERIOD]
    by_account: dict[str, list[datetime]] = {}
    accounts: set[str] = set()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(SUSPICIOUS_SQL)
        for ts, day, sender, receiver in cur:
            for account in {sender, receiver}:
                by_account.setdefault(account, []).append(ts)
                if period["start"] <= day <= period["end"]:
                    accounts.add(account)
    for times in by_account.values():
        times.sort()
    base = fetch_base_rates(conn, MARTS, LABELS, config)[PERIOD]
    return Truth(
        suspicious_ts=by_account,
        suspicious_accounts=accounts,
        base_rate=base.rate,
        start=period["start"],
        end=period["end"],
        detection=timedelta(days=config["detection_days"]),
        weeks=weeks(period),
    )


def variant_rule(rule: Rule, sql_file: str, superset: dict[str, Any]) -> Rule:
    """The rule with a tuning SQL variant and the superset's looser parameters."""
    path = (
        SQL_DIR / sql_file
        if (SQL_DIR / sql_file).exists()
        else VARIANT_SQL_DIR / sql_file
    )
    sql_text = path.read_text(encoding="utf-8")
    used = set(PLACEHOLDER.findall(sql_text))
    params = {k: v for k, v in {**rule.parameters, **superset}.items() if k in used}
    check_rule_sql(
        rule.id, sql_text, params
    )  # same label guard and checks as the engine
    return replace(rule, sql_text=sql_text, parameters=params)


def load_candidates(
    conn: psycopg.Connection, rule: Rule, variant: str, refresh: bool = False
) -> pd.DataFrame:
    """Candidates of a rule variant on the full data, cached as parquet."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{rule.id}_{variant}.parquet"
    if cache.exists() and not refresh:
        logger.info("%s %s: candidates from cache %s", rule.id, variant, cache.name)
        return pd.read_parquet(cache)
    names = METRICS[rule.id]
    columns: dict[str, list] = {
        "account_id": [],
        "trigger_ts": [],
        **{n: [] for n in names},
    }
    for c in iter_candidates(conn, rule, MARTS):
        columns["account_id"].append(c.account_id)
        columns["trigger_ts"].append(c.trigger_ts)
        for n in names:
            columns[n].append(c.metrics.get(n))
    df = pd.DataFrame(columns)
    df["account_id"] = df["account_id"].astype("category")
    for n in names:
        if not n.startswith("is_"):
            df[n] = df[n].astype(float)
    df.to_parquet(cache)
    logger.info("%s %s: %d candidates cached", rule.id, variant, len(df))
    return df


def suppress_mask(accounts: np.ndarray, ts: np.ndarray, days: int) -> np.ndarray:
    """The engine's suppression on rows sorted by account and time, as a keep mask.

    Plain Python integers (nanoseconds) in the loop: numpy scalar arithmetic
    is about 10 times slower, and the loop covers millions of rows.
    """
    keep = [False] * len(ts)
    window = days * 86_400 * 10**9
    last_account, last_kept = -1, 0
    for i, (account, t) in enumerate(
        zip(
            accounts.tolist(),
            ts.astype("datetime64[ns]").astype("int64").tolist(),
            strict=True,
        )
    ):
        if account != last_account or t - last_kept >= window:
            keep[i] = True
            last_account, last_kept = account, t
    return np.array(keep, dtype=bool)


def alerts_for(
    df: pd.DataFrame, rule: Rule, rule_id: str, option: dict
) -> pd.DataFrame:
    """The alerts (account, trigger time) an option produces after suppression."""
    sub = df[FILTERS[rule_id](df, option)]
    keep = suppress_mask(
        sub["account_id"].cat.codes.to_numpy(),
        sub["trigger_ts"].to_numpy(),
        rule.suppression_days,
    )
    return sub.loc[keep, ["account_id", "trigger_ts"]]


def score(alerts: pd.DataFrame, truth: Truth) -> dict[str, Any]:
    """Tuning-period metrics, same definitions as the rules report."""
    days = alerts["trigger_ts"].dt.date
    alerts = alerts[(days >= truth.start) & (days <= truth.end)]
    hits = 0
    for account, ts in zip(
        alerts["account_id"].astype(str), alerts["trigger_ts"], strict=True
    ):
        times = truth.suspicious_ts.get(account)
        if times and bisect_right(times, ts) > bisect_left(times, ts - truth.detection):
            hits += 1
    n = len(alerts)
    precision = hits / n if n else None
    caught = truth.suspicious_accounts & set(alerts["account_id"].astype(str))
    return {
        "alerts": n,
        "alerts_per_week": n / truth.weeks,
        "precision": precision,
        "lift": precision / truth.base_rate if precision is not None else None,
        "accounts_caught": len(caught),
        "account_recall": len(caught) / len(truth.suspicious_accounts),
        "caught": caught,
    }


def options(rule_id: str, tconf: dict[str, Any]) -> list[dict[str, Any]]:
    """Every (variant, grid values) combination for a rule."""
    grid = tconf[rule_id]["grid"]
    keys = list(grid)
    return [
        {"variant": v, **dict(zip(keys, values, strict=True))}
        for v in tconf[rule_id]["variants"]
        for values in itertools.product(*grid.values())
    ]


def grid_table(conn: psycopg.Connection, rule_id: str, refresh: bool) -> list[dict]:
    """Score every option of a rule; check the plan's values match the baseline."""
    config, tconf = load_config(), load_tuning_config()
    truth = load_truth(conn, config)
    rule = load_rules(only=[rule_id])[0]
    frames = {
        name: load_candidates(
            conn, variant_rule(rule, v["sql"], v["superset"]), name, refresh
        )
        for name, v in tconf[rule_id]["variants"].items()
    }
    baseline = tconf[rule_id]["baseline"]
    rows = []
    for option in options(rule_id, tconf):
        superset = tconf[rule_id]["variants"][option["variant"]]["superset"]
        if any(option.get(k, v) < v for k, v in superset.items()):
            continue  # looser than the candidates generated: not available
        result = score(
            alerts_for(frames[option["variant"]], rule, rule_id, option), truth
        )
        rows.append({**option, **result, "is_baseline": option == baseline})
    check_baseline(rule_id, next(r for r in rows if r["is_baseline"]))
    return rows


def check_baseline(rule_id: str, row: dict[str, Any]) -> None:
    """Stop unless the plan's values reproduce the baseline report's tuning alerts."""
    path = PROJECT_ROOT / load_config()["output_dir"] / "rules_evaluation_baseline.csv"
    with path.open(encoding="utf-8") as f:
        report = next(
            r
            for r in csv.DictReader(f)
            if r["rule_id"] == rule_id and r["period"] == PERIOD
        )
    expected = (int(report["alerts"]), int(report["caught_accounts"]))
    got = (row["alerts"], row["accounts_caught"])
    if got != expected:
        raise RuntimeError(
            f"{rule_id}: plan values give {got}, baseline report {expected}"
        )
    logger.info(
        "%s self-check passed: plan values reproduce the baseline %s", rule_id, got
    )


def pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def render(rule_id: str, rows: list[dict[str, Any]]) -> str:
    """Markdown table, baseline first, then by alerts per week."""
    base = next(r for r in rows if r["is_baseline"])
    keys = [k for k in rows[0] if k not in {"alerts", "alerts_per_week", "precision",
            "lift", "accounts_caught", "account_recall", "caught", "is_baseline"}]  # fmt: skip
    header = [*keys, "Alerts/week", "Δ alerts", "Precision", "Lift", "Account recall",
              "Accounts lost"]  # fmt: skip
    lines = [
        f"# Tuning {rule_id} — tuning months only (full data)",
        "",
        (
            "Baseline row (plan values) in bold. Δ alerts and accounts lost are "
            "against the baseline. Scored like the rules report: precision = alerts "
            "whose account had a suspicious transaction in the detection window, "
            "lift = precision / base rate, account recall = suspicious accounts alerted."
        ),
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for r in sorted(rows, key=lambda r: (not r["is_baseline"], -r["alerts_per_week"])):
        cells = [str(r[k]) for k in keys] + [
            f"{r['alerts_per_week']:,.0f}",
            f"{r['alerts_per_week'] / base['alerts_per_week'] - 1:+.0%}",
            pct(r["precision"]),
            "–" if r["lift"] is None else f"{r['lift']:.1f}×",
            pct(r["account_recall"]),
            f"{len(base['caught'] - r['caught']):,}",
        ]
        if r["is_baseline"]:
            cells = [f"**{c}**" for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def parse_pick(text: str, rule_id: str, tconf: dict[str, Any]) -> dict[str, Any]:
    """'variant,v1,v2,...' in the order of the rule's grid keys."""
    variant, *values = text.split(",")
    option = {"variant": variant}
    for key, value in zip(tconf[rule_id]["grid"], values, strict=True):
        option[key] = value if key == "clauses" else float(value)
    return option


OTHER_ALERTS_SQL = """
select account_id, trigger_ts from marts.alerts where rule_id = %s
"""


def combine(conn: psycopg.Connection, picks: dict[str, str | None]) -> str:
    """Score the proposal: tuned rules' picks plus every other rule's baseline alerts."""
    config, tconf = load_config(), load_tuning_config()
    truth = load_truth(conn, config)
    rows, alerted = [], set()
    for rule in load_rules():
        if rule.id in picks:
            pick = picks[rule.id]
            if pick is None:
                rows.append((rule.id, "disabled", 0.0, None))
                continue
            option = parse_pick(pick, rule.id, tconf)
            frame = pd.read_parquet(
                CACHE_DIR / f"{rule.id}_{option['variant']}.parquet"
            )
            result = score(alerts_for(frame, rule, rule.id, option), truth)
            label = pick
        else:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(OTHER_ALERTS_SQL, (rule.id,))
                frame = pd.DataFrame(
                    cur.fetchall(), columns=["account_id", "trigger_ts"]
                )
            frame["trigger_ts"] = pd.to_datetime(frame["trigger_ts"])
            result = score(frame, truth)
            label = "baseline"
        alerted |= result["caught"]
        rows.append((rule.id, label, result["alerts_per_week"], result["lift"]))
    total = sum(r[2] for r in rows)
    recall = len(alerted) / len(truth.suspicious_accounts)
    lines = ["| Rule | Values | Alerts/week | Lift |", "| --- | --- | --- | --- |"]
    lines += [
        f"| {r} | {v} | {w:,.0f} | {'–' if lift is None else f'{lift:.1f}×'} |"
        for r, v, w, lift in rows
    ]
    lines.append(f"| **Total** | | **{total:,.0f}** | |")
    lines.append(f'\n"Any rule" account recall (tuning months): **{pct(recall)}**')
    return "\n".join(lines)


def main() -> None:
    """Command line: grid tables per rule, or the combined proposal."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grid", help="score every option of one rule")
    g.add_argument("--rule", required=True, choices=sorted(FILTERS))
    g.add_argument(
        "--refresh", action="store_true", help="regenerate cached candidates"
    )
    c = sub.add_parser("combine", help="score a proposal across all rules")
    c.add_argument("--pick", action="append", default=[],
                   help="RULE=variant,v1,... in grid order, or RULE=disabled")  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    with get_connection() as conn:
        if args.command == "grid":
            rows = grid_table(conn, args.rule, args.refresh)
            out = PROJECT_ROOT / load_config()["output_dir"] / f"tuning_{args.rule}.md"
            out.write_text(render(args.rule, rows), encoding="utf-8")
            logger.info("Wrote %s (%d options)", out, len(rows))
        else:
            picks = {}
            for p in args.pick:
                rule_id, value = p.split("=", 1)
                picks[rule_id] = None if value == "disabled" else value
            logger.info("Proposal, tuning months only:\n%s", combine(conn, picks))


if __name__ == "__main__":
    main()
