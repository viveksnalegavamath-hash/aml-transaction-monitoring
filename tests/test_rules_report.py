"""Tests for the rules evaluation report and for keeping labels out of other code."""

import re
from datetime import date
from pathlib import Path

from psycopg import sql

from aml_transaction_monitoring.evaluation.rules_report import (
    ANY_RULE,
    fetch_rows,
    load_config,
    matrix,
    summarise,
    weeks,
)
from aml_transaction_monitoring.rules.engine import load_rules, run_rules

SRC = Path(__file__).resolve().parents[1] / "src" / "aml_transaction_monitoring"
CONFIG = load_config()


def row(
    rule: str,
    period: str,
    typology: str,
    suspicious: int,
    caught: int,
    alerts: int,
    true_alerts: int,
) -> dict:
    return {
        "rule_id": rule,
        "period": period,
        "typology": typology,
        "suspicious": suspicious,
        "caught": caught,
        "alerts": alerts,
        "true_alerts": true_alerts,
    }


# --- Maths on hand-made counts (no database) -------------------------------------


def test_periods_are_the_agreed_split() -> None:
    p = CONFIG["periods"]
    assert (p["tuning"]["start"], p["tuning"]["end"]) == (
        date(2022, 10, 7),
        date(2023, 5, 31),
    )
    assert (p["holdout"]["start"], p["holdout"]["end"]) == (
        date(2023, 6, 1),
        date(2023, 8, 23),
    )


def test_weeks_count_both_end_dates() -> None:
    assert weeks({"start": date(2023, 6, 1), "end": date(2023, 6, 7)}) == 1


def test_precision_and_recall_per_rule() -> None:
    rows = [
        row(
            "R02",
            "holdout",
            "fan_in",
            suspicious=10,
            caught=4,
            alerts=20,
            true_alerts=5,
        ),
        row(
            "R02",
            "holdout",
            "smurfing",
            suspicious=30,
            caught=6,
            alerts=20,
            true_alerts=5,
        ),
        row(
            ANY_RULE,
            "holdout",
            "fan_in",
            suspicious=10,
            caught=4,
            alerts=0,
            true_alerts=0,
        ),
        row(
            ANY_RULE,
            "holdout",
            "smurfing",
            suspicious=30,
            caught=6,
            alerts=0,
            true_alerts=0,
        ),
    ]
    r02, any_rule = summarise(rows, CONFIG)
    assert (r02.rule_id, r02.alerts, r02.precision, r02.recall) == (
        "R02",
        20,
        0.25,
        0.25,
    )
    assert r02.alerts_per_week == 20 / weeks(CONFIG["periods"]["holdout"])
    # "any rule" has no alert count or precision: alerts overlap across rules.
    assert (any_rule.alerts, any_rule.precision, any_rule.recall) == (None, None, 0.25)


def test_rule_with_no_alerts_has_no_precision() -> None:
    [r01] = summarise([row("R01", "tuning", "structuring", 7, 0, 0, 0)], CONFIG)
    assert (r01.alerts, r01.precision, r01.recall) == (0, None, 0.0)


def test_matrix_is_share_of_each_typology_caught() -> None:
    rows = [
        row("R02", "holdout", "fan_in", 10, 4, 1, 1),
        row("R02", "tuning", "fan_in", 10, 9, 1, 1),
    ]
    typologies, rules, cells = matrix(rows, "holdout")
    assert (typologies, rules, cells[("fan_in", "R02")]) == (["fan_in"], ["R02"], 0.4)


# --- Labels stay inside evaluation/ (no database) -----------------------------------


def test_only_evaluation_code_reads_the_label_table() -> None:
    label_table = re.compile(
        r"evaluation(_sample)?[\"']?\.[\"']?eval_transaction_labels|\{labels\}"
    )
    offenders = [
        p.relative_to(SRC)
        for p in SRC.rglob("*.py")
        if "evaluation" not in p.relative_to(SRC).parts
        and label_table.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# --- End to end: rules first, labels joined after (database) ---------------------------


def test_report_joins_labels_to_existing_alerts(rule_db) -> None:
    s = sql.Identifier(rule_db.schema)
    # Four in-band cash deposits (one alert covering three) and one unrelated payment.
    ids = [
        rule_db.add(f"2023-06-0{d} 10:00", payment_type="cash_deposit", amount_gbp=9000)
        for d in (2, 3, 4)
    ]
    other = rule_db.add("2023-06-20 10:00", amount_gbp=50)
    run_rules(rule_db.conn, load_rules(only=["R01"]), rule_db.schema)

    # Labels are created only now, after the alerts exist.
    rule_db.conn.execute(
        sql.SQL(
            "CREATE TABLE {}.eval_transaction_labels (transaction_id text, is_laundering boolean, typology text)"
        ).format(s)
    )
    labels = [(ids[0], True, "structuring"), (ids[1], False, "normal_cash_deposits"),
              (ids[2], False, "normal_cash_deposits"), (other, True, "fan_in")]  # fmt: skip
    with rule_db.conn.cursor() as cur:
        cur.executemany(
            sql.SQL(
                "INSERT INTO {}.eval_transaction_labels VALUES (%s, %s, %s)"
            ).format(s),
            labels,
        )

    rows = fetch_rows(rule_db.conn, rule_db.schema, rule_db.schema, CONFIG)
    results = {(r.rule_id, r.period): r for r in summarise(rows, CONFIG)}
    r01 = results[("R01", "holdout")]
    assert (r01.alerts, r01.precision) == (
        1,
        1.0,
    )  # the one alert covers a suspicious deposit
    assert (r01.caught, r01.suspicious) == (1, 2)  # the fan_in payment was missed
    _, _, cells = matrix(rows, "holdout")
    assert cells[("structuring", "R01")] == 1.0
    assert cells[("fan_in", "R01")] == 0.0
