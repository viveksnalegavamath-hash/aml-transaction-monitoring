"""Tests for the rules engine: spec checks, label guard, suppression, writing alerts."""

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from psycopg import sql

from aml_transaction_monitoring.rules.engine import (
    RULES_DIR,
    SQL_DIR,
    Candidate,
    alert_id,
    check_rule_sql,
    load_rule,
    load_rules,
    run_rules,
    suppress,
    to_alert,
)

GOOD_SQL = "select * from {fct} where amount_gbp > %(min_amount)s"


def candidate(account: str, ts: str) -> Candidate:
    return Candidate(
        account, datetime.fromisoformat(ts), datetime.fromisoformat(ts), ["T1"], {}
    )


# --- Spec and SQL checks (no database) ----------------------------------------


def test_every_rule_spec_loads() -> None:
    rules = load_rules()
    assert rules and rules[0].id == "R01"


def test_placeholder_without_parameter_fails() -> None:
    with pytest.raises(ValueError, match="no YAML parameter: \\['min_amount'\\]"):
        check_rule_sql("RX", GOOD_SQL, {})


def test_parameter_not_used_fails() -> None:
    with pytest.raises(ValueError, match="not used in the SQL: \\['extra'\\]"):
        check_rule_sql("RX", GOOD_SQL, {"min_amount": 1, "extra": 2})


@pytest.mark.parametrize(
    "bad_sql",
    [
        "select * from {fct} join evaluation.eval_transaction_labels using (transaction_id)",
        "select is_laundering from {fct}",
        "select * from {fct} -- typology hint",
    ],
)
def test_sql_reading_the_label_is_refused(bad_sql: str) -> None:
    with pytest.raises(ValueError, match="must not mention"):
        check_rule_sql("RX", bad_sql, {})


def test_sql_must_use_the_fct_placeholder() -> None:
    with pytest.raises(ValueError, match="fct placeholder"):
        check_rule_sql("RX", "select * from marts.fct_transactions", {})


def test_every_rule_sql_file_is_label_free() -> None:
    for path in SQL_DIR.glob("*.sql"):
        assert "laundering" not in path.read_text(encoding="utf-8").lower(), path.name


def test_spec_with_missing_field_fails(tmp_path: Path) -> None:
    text = (RULES_DIR / "R01_structuring.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "R01_structuring.yaml"
    bad.write_text(text.replace("suppression_days: 7", ""), encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields \\['suppression_days'\\]"):
        load_rule(bad)


def test_threshold_must_be_a_number(tmp_path: Path) -> None:
    text = (RULES_DIR / "R01_structuring.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "R01_structuring.yaml"
    bad.write_text(
        text.replace("min_deposits: 3", 'min_deposits: "3"'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="must be numbers"):
        load_rule(bad)


def test_file_name_must_match_rule_id(tmp_path: Path) -> None:
    shutil.copy(RULES_DIR / "R01_structuring.yaml", tmp_path / "R02_structuring.yaml")
    with pytest.raises(ValueError, match="must start with R01_"):
        load_rule(tmp_path / "R02_structuring.yaml")


# --- Suppression and alert building (no database) --------------------------------


def test_repeats_within_suppression_period_are_dropped() -> None:
    kept = suppress(
        [
            candidate("A", "2023-01-01 10:00"),
            candidate("A", "2023-01-03 10:00"),  # 2 days later: dropped
            candidate("A", "2023-01-08 10:00"),  # 7 days after the kept one: kept
            candidate("B", "2023-01-02 10:00"),  # other account: kept
        ],
        days=7,
    )
    assert [(c.account_id, c.trigger_ts.day) for c in kept] == [
        ("A", 1),
        ("A", 8),
        ("B", 2),
    ]


def test_alert_id_is_deterministic() -> None:
    ts = datetime.fromisoformat("2023-01-01 10:00")
    assert alert_id("R01", "A", ts) == alert_id("R01", "A", ts)
    assert alert_id("R01", "A", ts) != alert_id("R01", "B", ts)


def test_reason_shows_the_actual_numbers() -> None:
    rule = load_rules(only=["R01"])[0]
    hit = Candidate(
        "A",
        datetime.fromisoformat("2023-01-08"),
        datetime.fromisoformat("2023-01-01"),
        ["T2", "T1", "T1"],
        {"n_deposits": 3, "total_gbp": 27300.0},
    )
    alert = to_alert(rule, hit)
    assert alert.reason == (
        "3 cash deposits of £8,000–£9,999.99 within 7 days, totalling £27,300.00."
    )
    assert alert.transaction_ids == ["T1", "T2"]


# --- End to end on a throwaway schema (database) -----------------------------------


def test_run_writes_alerts_links_and_run_log(rule_db) -> None:
    for day in (2, 4, 6, 7):  # 4 deposits: candidates on days 6 and 7, one alert
        rule_db.add(
            f"2023-01-0{day} 10:00", payment_type="cash_deposit", amount_gbp=9000
        )
    alerts = run_rules(rule_db.conn, load_rules(only=["R01"]), rule_db.schema)
    assert len(alerts) == 1

    def one(query: str) -> tuple:
        return rule_db.conn.execute(
            sql.SQL(query).format(s=sql.Identifier(rule_db.schema))
        ).fetchone()

    assert one("SELECT count(*), min(status), min(rule_id) FROM {s}.alerts") == (
        1,
        "New",
        "R01",
    )
    assert one("SELECT count(*) FROM {s}.alert_transactions") == (3,)
    assert one("SELECT dataset, candidates, alerts FROM {s}.rule_runs") == (
        rule_db.schema,
        {"R01": 2},
        {"R01": 1},
    )

    # Running again replaces R01's alerts instead of adding duplicates.
    run_rules(rule_db.conn, load_rules(only=["R01"]), rule_db.schema)
    assert one("SELECT count(*) FROM {s}.alerts") == (1,)
    assert one("SELECT count(*) FROM {s}.rule_runs") == (2,)
