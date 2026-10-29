"""Tests for the rules evaluation report and for keeping labels out of other code."""

import re
from datetime import date, datetime
from pathlib import Path

from psycopg import sql

from aml_transaction_monitoring.evaluation.rules_report import (
    ANY_RULE,
    BaseRate,
    fetch_base_rates,
    fetch_rows,
    fetch_rule_rows,
    load_config,
    matrix,
    summarise,
    weeks,
)
from aml_transaction_monitoring.rules.engine import (
    Alert,
    alert_id,
    load_rules,
    run_rules,
    write_alerts,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "aml_transaction_monitoring"
CONFIG = load_config()


def rule_row(rule: str, period: str, alerts: int, true_alerts: int, accounts: int, caught: int) -> dict:  # fmt: skip
    return {
        "rule_id": rule,
        "period": period,
        "alerts": alerts,
        "true_alerts": true_alerts,
        "suspicious_accounts": accounts,
        "caught_accounts": caught,
    }


def row(rule: str, period: str, typology: str, suspicious: int, caught: int) -> dict:
    return {
        "rule_id": rule,
        "period": period,
        "typology": typology,
        "suspicious": suspicious,
        "caught": caught,
    }


# --- Maths on hand-made counts (no database) -------------------------------------


def test_periods_and_detection_window_are_the_agreed_values() -> None:
    p = CONFIG["periods"]
    assert (p["tuning"]["start"], p["tuning"]["end"]) == (
        date(2022, 10, 7),
        date(2023, 5, 31),
    )
    assert (p["holdout"]["start"], p["holdout"]["end"]) == (
        date(2023, 6, 1),
        date(2023, 8, 23),
    )
    assert CONFIG["detection_days"] == 30


def test_weeks_count_both_end_dates() -> None:
    assert weeks({"start": date(2023, 6, 1), "end": date(2023, 6, 7)}) == 1


def test_precision_lift_and_both_recalls_per_rule() -> None:
    rule_rows = [
        rule_row("R02", "holdout", alerts=20, true_alerts=5, accounts=8, caught=2),
        rule_row(ANY_RULE, "holdout", alerts=0, true_alerts=0, accounts=8, caught=2),
    ]
    rows = [
        row("R02", "holdout", "fan_in", suspicious=10, caught=4),
        row("R02", "holdout", "smurfing", suspicious=30, caught=6),
        row(ANY_RULE, "holdout", "fan_in", suspicious=10, caught=4),
        row(ANY_RULE, "holdout", "smurfing", suspicious=30, caught=6),
    ]
    base_rates = {
        "holdout": BaseRate("holdout", active_accounts=1000, suspicious_accounts=8)
    }
    r02, any_rule = summarise(rule_rows, rows, base_rates, CONFIG)
    assert (r02.alerts, r02.precision, r02.account_recall, r02.transaction_recall) == (
        20,
        0.25,
        0.25,
        0.25,
    )
    assert base_rates["holdout"].rate == 0.008
    assert r02.lift == 0.25 / 0.008
    assert r02.alerts_per_week == 20 / weeks(CONFIG["periods"]["holdout"])
    # "any rule" has no alert count, precision or lift: alerts overlap across rules.
    assert (any_rule.alerts, any_rule.precision, any_rule.lift) == (None, None, None)
    assert (any_rule.account_recall, any_rule.transaction_recall) == (0.25, 0.25)


def test_rule_with_no_alerts_has_no_precision_or_lift() -> None:
    [r01] = summarise(
        [rule_row("R01", "tuning", 0, 0, accounts=5, caught=0)],
        [row("R01", "tuning", "structuring", 7, 0)],
        {"tuning": BaseRate("tuning", 100, 5)},
        CONFIG,
    )
    assert (r01.alerts, r01.precision, r01.lift) == (0, None, None)
    assert (r01.account_recall, r01.transaction_recall) == (0.0, 0.0)


def test_matrix_is_share_of_each_typology_caught() -> None:
    rows = [
        row("R02", "holdout", "fan_in", 10, 4),
        row("R02", "tuning", "fan_in", 10, 9),
    ]
    typologies, rules, cells = matrix(rows, "holdout")
    assert (typologies, rules, cells[("fan_in", "R02")]) == (["fan_in"], ["R02"], 0.4)


# --- Labels stay inside evaluation/ (no database) -----------------------------------


def test_only_evaluation_code_reads_the_label_table() -> None:
    # Both label tables: per transaction (rules) and per account-week (ML).
    label_table = re.compile(
        r"evaluation(_sample)?[\"']?\.[\"']?eval_\w+_labels|\{labels\}"
    )
    assert label_table.search("from {labels}.eval_account_week_labels")
    offenders = [
        p.relative_to(SRC)
        for p in SRC.rglob("*.py")
        if "evaluation" not in p.relative_to(SRC).parts
        and label_table.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# --- End to end: alerts first, labels joined after (database) ---------------------------


def make_dim_account(db, sampled: set[str] | None = None) -> None:
    """dim_account from the test transactions; all accounts sampled unless given."""
    db.conn.execute(
        sql.SQL(
            "CREATE TABLE {s}.dim_account AS"
            " SELECT account_id, coalesce(account_id = ANY(%s), true) AS in_dev_sample"
            " FROM (SELECT sender_account AS account_id FROM {s}.fct_transactions"
            "       UNION SELECT receiver_account FROM {s}.fct_transactions) AS a"
        ).format(s=sql.Identifier(db.schema)),
        [None if sampled is None else list(sampled)],
    )


def add_alerts(db, *alerts: tuple[str, str, str]) -> None:
    """Write (rule ID, account, trigger time) alerts through the engine's writer."""
    rule_ids = sorted({r for r, _, _ in alerts})
    rules = load_rules(only=rule_ids)
    made = []
    for rule_id, account, ts in alerts:
        when = datetime.fromisoformat(ts)
        made.append(
            Alert(
                alert_id=alert_id(rule_id, account, when),
                rule_id=rule_id,
                rule_version=1,
                account_id=account,
                alert_date=when.date(),
                trigger_ts=when,
                window_start=when,
                reason="test",
                metrics={},
                transaction_ids=[],  # scoring must not depend on linked transactions
            )
        )
    counts = {r: sum(a[0] == r for a in alerts) for r in rule_ids}
    write_alerts(db.conn, db.schema, rules, made, counts, datetime.now().astimezone())


def add_labels(db, labels: list[tuple[str, bool, str]]) -> None:
    """Create the label table now, after the alerts exist."""
    s = sql.Identifier(db.schema)
    db.conn.execute(
        sql.SQL(
            "CREATE TABLE {}.eval_transaction_labels"
            " (transaction_id text, is_laundering boolean, typology text)"
        ).format(s)
    )
    with db.conn.cursor() as cur:
        cur.executemany(
            sql.SQL(
                "INSERT INTO {}.eval_transaction_labels VALUES (%s, %s, %s)"
            ).format(s),
            labels,
        )


def evaluate(db, sample_only: bool = False) -> tuple[dict, list, dict]:
    """Return ({(rule, period): result}, typology rows, base rates)."""
    args = (db.conn, db.schema, db.schema, CONFIG, sample_only)
    rows = fetch_rows(*args)
    base_rates = fetch_base_rates(*args)
    results = summarise(fetch_rule_rows(*args), rows, base_rates, CONFIG)
    return {(r.rule_id, r.period): r for r in results}, rows, base_rates


def test_report_joins_labels_to_existing_alerts(rule_db) -> None:
    # Three in-band cash deposits S1 -> R1 (one R01 alert on R1) and a later payment.
    ids = [
        rule_db.add(f"2023-06-0{d} 10:00", payment_type="cash_deposit", amount_gbp=9000)
        for d in (2, 3, 4)
    ]
    later = rule_db.add("2023-06-20 10:00", amount_gbp=50)
    make_dim_account(rule_db)
    run_rules(rule_db.conn, load_rules(only=["R01"]), rule_db.schema)

    labels = [(ids[0], True, "structuring"), (ids[1], False, "normal_cash_deposits"),
              (ids[2], False, "normal_cash_deposits"), (later, True, "fan_in")]  # fmt: skip
    add_labels(rule_db, labels)

    results, rows, base_rates = evaluate(rule_db)
    r01 = results[("R01", "holdout")]
    assert (r01.alerts, r01.precision) == (
        1,
        1.0,
    )  # R1 had a suspicious deposit just before
    # Suspicious accounts S1 and R1; only R1 was alerted.
    assert (r01.caught_accounts, r01.suspicious_accounts) == (1, 2)
    # The later payment came after the alert, so it was not caught.
    assert (r01.caught_transactions, r01.suspicious_transactions) == (1, 2)
    assert (base_rates["holdout"].active_accounts, base_rates["holdout"].rate) == (
        2,
        1.0,
    )
    _, _, cells = matrix(rows, "holdout")
    assert (cells[("structuring", "R01")], cells[("fan_in", "R01")]) == (1.0, 0.0)


def test_transaction_caught_only_within_detection_days(rule_db) -> None:
    tid = rule_db.add("2023-06-01 10:00", sender_account="A1", receiver_account="X1")
    make_dim_account(rule_db)
    add_alerts(
        rule_db,
        ("R06", "A1", "2023-06-25 10:00"),  # 24 days after: caught
        ("R05", "A1", "2023-07-15 10:00"),  # 44 days after: too late
    )
    add_labels(rule_db, [(tid, True, "single_large")])
    results, _, _ = evaluate(rule_db)
    r05, r06 = results[("R05", "holdout")], results[("R06", "holdout")]
    assert (r06.caught_transactions, r06.precision) == (1, 1.0)
    assert (r05.caught_transactions, r05.precision) == (0, 0.0)
    # Account recall: both alerted the suspicious account A1 in the period,
    # even R05's alert, which came too late for the transaction.
    assert (r05.caught_accounts, r06.caught_accounts, r06.suspicious_accounts) == (
        1,
        1,
        2,
    )


def test_alert_on_the_receiver_catches_the_transaction(rule_db) -> None:
    tid = rule_db.add("2023-06-10 10:00", sender_account="X2", receiver_account="B2")
    rule_db.add("2023-06-10 11:00", sender_account="X3", receiver_account="Y3")
    make_dim_account(rule_db)
    add_alerts(
        rule_db, ("R02", "B2", "2023-06-12 10:00"), ("R03", "X3", "2023-06-12 10:00")
    )
    add_labels(rule_db, [(tid, True, "fan_in")])
    results, _, _ = evaluate(rule_db)
    assert results[("R02", "holdout")].caught_transactions == 1
    # An alert on an account with no part in the transaction catches nothing.
    r03 = results[("R03", "holdout")]
    assert (r03.caught_transactions, r03.caught_accounts, r03.precision) == (0, 0, 0.0)


def test_sample_mode_counts_only_sampled_accounts(rule_db) -> None:
    t1 = rule_db.add("2023-06-05 10:00", sender_account="P", receiver_account="Q")
    t2 = rule_db.add("2023-06-05 11:00", sender_account="N", receiver_account="N2")
    make_dim_account(rule_db, sampled={"P", "Q"})
    add_alerts(
        rule_db, ("R07", "P", "2023-06-06 10:00"), ("R07", "N", "2023-06-06 10:00")
    )
    add_labels(rule_db, [(t1, True, "fan_out"), (t2, True, "fan_out")])

    full, _, full_base = evaluate(rule_db, sample_only=False)
    r07 = full[("R07", "holdout")]
    assert (r07.alerts, r07.caught_accounts, r07.suspicious_accounts) == (2, 2, 4)
    assert (r07.caught_transactions, r07.suspicious_transactions) == (2, 2)
    assert full_base["holdout"].active_accounts == 4

    sample, _, sample_base = evaluate(rule_db, sample_only=True)
    r07 = sample[("R07", "holdout")]
    # N and N2 are not sampled: their alert, account and transaction drop out.
    assert (r07.alerts, r07.caught_accounts, r07.suspicious_accounts) == (1, 1, 2)
    assert (r07.caught_transactions, r07.suspicious_transactions) == (1, 1)
    assert sample_base["holdout"].active_accounts == 2
