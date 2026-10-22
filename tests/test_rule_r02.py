"""R02 Many-to-one on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R02_many_to_one.yaml: 10 distinct senders, £20,000,
7 days.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R02 = load_rules(only=["R02"])[0]


def pay_in(db, day: int, sender: str, amount: float = 2500, account: str = "M1") -> str:
    return db.add(
        f"2023-03-{day:02d} 10:00",
        sender_account=sender,
        receiver_account=account,
        amount_gbp=amount,
    )


def test_fires_on_ten_senders_over_threshold_within_window(rule_db) -> None:
    ids = [pay_in(rule_db, 1 + n % 7, f"S{n}") for n in range(10)]  # 10 x £2,500
    hits = find_candidates(rule_db.conn, R02, rule_db.schema)
    assert hits, "expected R02 to fire"
    last = max(hits, key=lambda h: h.trigger_ts)
    assert last.account_id == "M1"
    assert last.metrics["n_senders"] == 10
    assert last.metrics["total_gbp"] == 25000
    assert sorted(last.transaction_ids) == sorted(ids)


def test_does_not_fire_with_nine_senders(rule_db) -> None:
    for n in range(9):
        pay_in(rule_db, 1 + n % 7, f"S{n}", amount=5000)
    assert find_candidates(rule_db.conn, R02, rule_db.schema) == []


def test_repeat_payments_from_one_sender_count_once(rule_db) -> None:
    for n in range(9):
        pay_in(rule_db, 1, f"S{n}", amount=3000)
    pay_in(rule_db, 2, "S0", amount=3000)  # 10 payments, still 9 senders
    assert find_candidates(rule_db.conn, R02, rule_db.schema) == []


def test_does_not_fire_below_total(rule_db) -> None:
    for n in range(12):
        pay_in(rule_db, 1 + n % 7, f"S{n}", amount=1000)  # 12 senders, £12,000
    assert find_candidates(rule_db.conn, R02, rule_db.schema) == []


def test_does_not_fire_when_senders_spread_beyond_window(rule_db) -> None:
    for n in range(5):
        pay_in(rule_db, 1, f"S{n}", amount=5000)
    for n in range(5, 10):
        pay_in(rule_db, 9, f"S{n}", amount=5000)  # 8 days later
    assert find_candidates(rule_db.conn, R02, rule_db.schema) == []


def test_does_not_combine_different_receivers(rule_db) -> None:
    for n in range(10):
        pay_in(rule_db, 1, f"S{n}", amount=5000, account=f"M{n % 2}")
    assert find_candidates(rule_db.conn, R02, rule_db.schema) == []
