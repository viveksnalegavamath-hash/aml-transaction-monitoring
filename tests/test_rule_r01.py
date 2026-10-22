"""R01 Structuring on tiny hand-made data: where it fires and where it does not.

Thresholds come from rules/R01_structuring.yaml (£8,000–£9,999.99, 3 deposits,
7 days), so these tests also catch an accidental change to the spec.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R01 = load_rules(only=["R01"])[0]


def cash(db, ts: str, amount: float, account: str = "C1") -> str:
    return db.add(
        ts, payment_type="cash_deposit", amount_gbp=amount, receiver_account=account
    )


def test_fires_on_three_deposits_in_band_within_seven_days(rule_db) -> None:
    ids = [
        cash(rule_db, "2023-01-02 10:00", 9000),
        cash(rule_db, "2023-01-04 10:00", 9500),
        cash(rule_db, "2023-01-08 09:00", 8800),
    ]
    [hit] = find_candidates(rule_db.conn, R01, rule_db.schema)
    assert hit.account_id == "C1"
    assert sorted(hit.transaction_ids) == sorted(ids)
    assert hit.metrics["n_deposits"] == 3
    assert hit.metrics["total_gbp"] == 27300


def test_band_edges_are_included(rule_db) -> None:
    cash(rule_db, "2023-01-02 10:00", 8000)
    cash(rule_db, "2023-01-03 10:00", 9999.99)
    cash(rule_db, "2023-01-04 10:00", 9000)
    assert len(find_candidates(rule_db.conn, R01, rule_db.schema)) == 1


def test_does_not_fire_on_two_deposits(rule_db) -> None:
    cash(rule_db, "2023-01-02 10:00", 9000)
    cash(rule_db, "2023-01-03 10:00", 9000)
    assert find_candidates(rule_db.conn, R01, rule_db.schema) == []


def test_does_not_fire_when_spread_over_more_than_seven_days(rule_db) -> None:
    cash(rule_db, "2023-01-01 10:00", 9000)
    cash(rule_db, "2023-01-05 10:00", 9000)
    cash(rule_db, "2023-01-08 10:01", 9000)  # 7 days and 1 minute after the first
    assert find_candidates(rule_db.conn, R01, rule_db.schema) == []


def test_does_not_fire_below_or_above_the_band(rule_db) -> None:
    cash(rule_db, "2023-01-02 10:00", 7999.99)
    cash(rule_db, "2023-01-03 10:00", 10000)
    cash(rule_db, "2023-01-04 10:00", 9000)
    assert find_candidates(rule_db.conn, R01, rule_db.schema) == []


def test_does_not_fire_on_non_cash_payments(rule_db) -> None:
    for day in (2, 3, 4):
        rule_db.add(f"2023-01-0{day} 10:00", payment_type="cheque", amount_gbp=9000)
    assert find_candidates(rule_db.conn, R01, rule_db.schema) == []


def test_does_not_combine_different_accounts(rule_db) -> None:
    cash(rule_db, "2023-01-02 10:00", 9000, account="C1")
    cash(rule_db, "2023-01-03 10:00", 9000, account="C2")
    cash(rule_db, "2023-01-04 10:00", 9000, account="C3")
    assert find_candidates(rule_db.conn, R01, rule_db.schema) == []
