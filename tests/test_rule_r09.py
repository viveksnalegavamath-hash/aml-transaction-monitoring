"""R09 Cash intensity on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R09_cash_intensity.yaml: cash deposits of more than
£5,000 and more than 50% of all money received, within 30 days.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R09 = load_rules(only=["R09"])[0]


def cash_in(db, ts: str, amount: float, account: str = "C1") -> str:
    return db.add(
        ts,
        sender_account="X",
        receiver_account=account,
        amount_gbp=amount,
        payment_type="cash_deposit",
    )


def transfer_in(db, ts: str, amount: float, account: str = "C1") -> str:
    return db.add(ts, sender_account="Y", receiver_account=account, amount_gbp=amount)


def test_fires_when_cash_dominates_inflows(rule_db) -> None:
    ids = [
        cash_in(rule_db, "2023-03-01 10:00", 3000),
        cash_in(rule_db, "2023-03-20 10:00", 3000),
    ]
    transfer_in(rule_db, "2023-03-10 10:00", 4000)  # 6,000 of 10,000 = 60% cash
    [hit] = find_candidates(rule_db.conn, R09, rule_db.schema)
    assert (hit.account_id, str(hit.trigger_ts)) == ("C1", "2023-03-20 10:00:00")
    assert hit.metrics == {
        "cash_gbp": 6000,
        "n_cash": 2,
        "inflow_gbp": 10000,
        "cash_share": 0.6,
    }
    assert sorted(hit.transaction_ids) == sorted(ids)  # the transfer is not evidence


def test_does_not_fire_at_or_below_cash_amount(rule_db) -> None:
    cash_in(rule_db, "2023-03-01 10:00", 2500)
    cash_in(rule_db, "2023-03-20 10:00", 2500)  # 5,000: 100% cash, not above £5,000
    assert find_candidates(rule_db.conn, R09, rule_db.schema) == []


def test_does_not_fire_at_or_below_half_of_inflows(rule_db) -> None:
    transfer_in(rule_db, "2023-03-01 10:00", 6000)
    cash_in(rule_db, "2023-03-02 10:00", 6000)  # 6,000 of 12,000: exactly 50%
    assert find_candidates(rule_db.conn, R09, rule_db.schema) == []


def test_does_not_fire_when_cash_is_outside_window(rule_db) -> None:
    cash_in(rule_db, "2023-01-01 10:00", 4000)
    cash_in(rule_db, "2023-02-05 10:00", 4000)  # 35 days later: 4,000 in the window
    assert find_candidates(rule_db.conn, R09, rule_db.schema) == []


def test_cash_withdrawals_and_money_sent_do_not_count(rule_db) -> None:
    rule_db.add("2023-03-01 10:00", sender_account="C1", receiver_account="ATM",
                amount_gbp=9000, payment_type="cash_withdrawal")  # fmt: skip
    rule_db.add(
        "2023-03-02 10:00", sender_account="C1", receiver_account="Z", amount_gbp=9000
    )
    assert find_candidates(rule_db.conn, R09, rule_db.schema) == []


def test_does_not_combine_different_accounts(rule_db) -> None:
    cash_in(rule_db, "2023-03-01 10:00", 3000, account="C1")
    cash_in(rule_db, "2023-03-02 10:00", 3000, account="C2")
    assert find_candidates(rule_db.conn, R09, rule_db.schema) == []
