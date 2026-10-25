"""R03 One-to-many on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R03_one_to_many.yaml (version 2): 10 distinct
receivers totalling at least £100,000, within 7 days. Amounts are chosen so
that each "does not fire" test is blocked only by the condition it is about.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R03 = load_rules(only=["R03"])[0]


def pay_out(
    db, day: int, receiver: str, amount: float = 10000, account: str = "D1"
) -> str:
    return db.add(
        f"2023-03-{day:02d} 10:00",
        sender_account=account,
        receiver_account=receiver,
        amount_gbp=amount,
    )


def test_fires_on_ten_receivers_within_window(rule_db) -> None:
    ids = [pay_out(rule_db, 1 + n % 7, f"R{n}") for n in range(10)]  # 10 x £10,000
    hits = find_candidates(rule_db.conn, R03, rule_db.schema)
    assert hits, "expected R03 to fire"
    last = max(hits, key=lambda h: h.trigger_ts)
    assert last.account_id == "D1"
    assert last.metrics["n_receivers"] == 10
    assert last.metrics["total_gbp"] == 100000
    assert sorted(last.transaction_ids) == sorted(ids)


def test_does_not_fire_with_nine_receivers(rule_db) -> None:
    for n in range(9):
        pay_out(rule_db, 1 + n % 7, f"R{n}", amount=12000)  # £108,000 total
    assert find_candidates(rule_db.conn, R03, rule_db.schema) == []


def test_repeat_payments_to_one_receiver_count_once(rule_db) -> None:
    for n in range(9):
        pay_out(rule_db, 1, f"R{n}")
    pay_out(rule_db, 2, "R0")  # 10 payments, still 9 receivers
    assert find_candidates(rule_db.conn, R03, rule_db.schema) == []


def test_does_not_fire_when_receivers_spread_beyond_window(rule_db) -> None:
    for n in range(5):
        pay_out(rule_db, 1, f"R{n}", amount=25000)
    for n in range(5, 10):
        pay_out(rule_db, 9, f"R{n}", amount=25000)  # 8 days later
    assert find_candidates(rule_db.conn, R03, rule_db.schema) == []


def test_does_not_combine_different_senders(rule_db) -> None:
    for n in range(10):
        pay_out(rule_db, 1, f"R{n}", amount=25000, account=f"D{n % 2}")
    assert find_candidates(rule_db.conn, R03, rule_db.schema) == []


def test_does_not_fire_below_minimum_total(rule_db) -> None:
    for n in range(10):
        pay_out(rule_db, 1 + n % 7, f"R{n}", amount=9999.99)  # £99,999.90 total
    assert find_candidates(rule_db.conn, R03, rule_db.schema) == []
