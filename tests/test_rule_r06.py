"""R06 Large vs profile on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R06_large_vs_profile.yaml: a payment sent of more than
£10,000 and more than 3 times the account's average payment sent in the
previous 90 days, with at least 90 days of history in the data.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R06 = load_rules(only=["R06"])[0]


def send(db, ts: str, amount: float, account: str = "A1") -> str:
    return db.add(ts, sender_account=account, receiver_account="Y", amount_gbp=amount)


def with_history(db, account: str = "A1") -> None:
    """First payment more than 90 days before March 2023, then a 2,000 baseline."""
    send(db, "2022-10-15 09:00", 2000, account)
    send(db, "2023-02-01 09:00", 2000, account)
    send(db, "2023-02-15 09:00", 2000, account)


def test_fires_on_payment_far_above_own_average(rule_db) -> None:
    with_history(rule_db)
    tid = send(rule_db, "2023-03-01 10:00", 15000)
    [hit] = find_candidates(rule_db.conn, R06, rule_db.schema)
    assert (hit.account_id, hit.transaction_ids) == ("A1", [tid])
    assert hit.metrics["n_baseline"] == 2  # the October payment is outside the 90 days
    assert hit.metrics["baseline_avg_gbp"] == 2000
    assert hit.metrics["ratio"] == 7.5


def test_payment_is_not_part_of_its_own_baseline(rule_db) -> None:
    send(rule_db, "2022-10-15 09:00", 4000)
    send(rule_db, "2023-02-01 09:00", 4000)
    # > 3 x 4,000; with itself in the baseline it would not be
    send(rule_db, "2023-03-01 10:00", 12001)
    [hit] = find_candidates(rule_db.conn, R06, rule_db.schema)
    assert hit.metrics["baseline_avg_gbp"] == 4000


def test_does_not_fire_at_or_below_three_times_average(rule_db) -> None:
    send(rule_db, "2022-10-15 09:00", 4000)
    send(rule_db, "2023-02-01 09:00", 4000)
    send(rule_db, "2023-03-01 10:00", 12000)  # exactly 3 x
    assert find_candidates(rule_db.conn, R06, rule_db.schema) == []


def test_does_not_fire_at_or_below_amount_threshold(rule_db) -> None:
    send(rule_db, "2022-10-15 09:00", 100)
    send(rule_db, "2023-02-01 09:00", 100)
    send(rule_db, "2023-03-01 10:00", 10000)  # 100 x average, but not above £10,000
    assert find_candidates(rule_db.conn, R06, rule_db.schema) == []


def test_does_not_fire_before_90_days_of_history(rule_db) -> None:
    send(rule_db, "2022-12-05 09:00", 2000)  # first seen 86 days before
    send(rule_db, "2023-02-01 09:00", 2000)
    send(rule_db, "2023-03-01 10:00", 15000)
    assert find_candidates(rule_db.conn, R06, rule_db.schema) == []


def test_history_counts_payments_received(rule_db) -> None:
    rule_db.add(
        "2022-10-15 09:00", sender_account="X", receiver_account="A1", amount_gbp=50
    )
    send(rule_db, "2023-02-01 09:00", 2000)
    send(rule_db, "2023-03-01 10:00", 15000)
    [hit] = find_candidates(rule_db.conn, R06, rule_db.schema)
    assert hit.account_id == "A1"


def test_does_not_fire_without_payments_in_the_baseline(rule_db) -> None:
    send(rule_db, "2022-10-15 09:00", 2000)  # history, but more than 90 days ago
    send(rule_db, "2023-03-01 10:00", 15000)
    assert find_candidates(rule_db.conn, R06, rule_db.schema) == []


def test_does_not_compare_with_other_accounts(rule_db) -> None:
    with_history(rule_db, account="A1")
    send(rule_db, "2022-10-15 09:00", 15000, account="A2")
    send(rule_db, "2023-02-01 09:00", 15000, account="A2")
    send(rule_db, "2023-03-01 10:00", 15000, account="A2")  # usual for A2, not for A1
    assert find_candidates(rule_db.conn, R06, rule_db.schema) == []
