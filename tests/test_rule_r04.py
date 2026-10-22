"""R04 Rapid pass-through on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R04_rapid_pass_through.yaml: inflows of at least
£10,000, at least 80% sent out, within 48 hours.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R04 = load_rules(only=["R04"])[0]


def money_in(db, ts: str, amount: float, account: str = "P1") -> str:
    return db.add(ts, sender_account="X", receiver_account=account, amount_gbp=amount)


def money_out(db, ts: str, amount: float, account: str = "P1") -> str:
    return db.add(ts, sender_account=account, receiver_account="Y", amount_gbp=amount)


def test_fires_when_most_of_inflow_leaves_within_window(rule_db) -> None:
    ids = [
        money_in(rule_db, "2023-03-01 10:00", 10000),
        money_out(rule_db, "2023-03-02 09:00", 8500),
    ]
    [hit] = find_candidates(rule_db.conn, R04, rule_db.schema)
    assert hit.account_id == "P1"
    assert str(hit.trigger_ts) == "2023-03-02 09:00:00"
    assert hit.metrics["in_gbp"] == 10000
    assert hit.metrics["out_gbp"] == 8500
    assert hit.metrics["out_ratio"] == 0.85
    assert sorted(hit.transaction_ids) == sorted(ids)


def test_several_inflows_and_outflows_add_up(rule_db) -> None:
    ids = [
        money_in(rule_db, "2023-03-01 10:00", 6000),
        money_in(rule_db, "2023-03-01 12:00", 6000),
        money_out(rule_db, "2023-03-01 15:00", 5000),
        money_out(rule_db, "2023-03-02 15:00", 5000),  # 10,000 of 12,000 = 83%
    ]
    [hit] = find_candidates(rule_db.conn, R04, rule_db.schema)
    assert (hit.metrics["n_in"], hit.metrics["n_out"]) == (2, 2)
    assert sorted(hit.transaction_ids) == sorted(ids)


def test_does_not_fire_below_ratio(rule_db) -> None:
    money_in(rule_db, "2023-03-01 10:00", 10000)
    money_out(rule_db, "2023-03-01 18:00", 7900)  # 79%
    assert find_candidates(rule_db.conn, R04, rule_db.schema) == []


def test_does_not_fire_below_inflow_threshold(rule_db) -> None:
    money_in(rule_db, "2023-03-01 10:00", 9999)
    money_out(rule_db, "2023-03-01 18:00", 9999)  # 100%, but too small
    assert find_candidates(rule_db.conn, R04, rule_db.schema) == []


def test_does_not_fire_when_outflow_is_after_window(rule_db) -> None:
    money_in(rule_db, "2023-03-01 10:00", 10000)
    money_out(rule_db, "2023-03-03 11:00", 10000)  # 49 hours later
    assert find_candidates(rule_db.conn, R04, rule_db.schema) == []


def test_outflow_before_the_inflow_does_not_count(rule_db) -> None:
    money_out(rule_db, "2023-03-01 09:00", 9000)  # money out first...
    money_in(rule_db, "2023-03-01 10:00", 10000)  # ...then in
    money_out(rule_db, "2023-03-01 11:00", 100)
    assert find_candidates(rule_db.conn, R04, rule_db.schema) == []


def test_does_not_combine_different_accounts(rule_db) -> None:
    money_in(rule_db, "2023-03-01 10:00", 10000, account="P1")
    money_out(rule_db, "2023-03-01 18:00", 10000, account="P2")
    assert find_candidates(rule_db.conn, R04, rule_db.schema) == []
