"""R07 Behaviour change on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R07_behaviour_change.yaml (version 2): 30-day volume
(sent and received) of at least £25,000 and more than 15 times the average
30-day volume of the 90 days before; at least 90 days of history. Version 1's
dormancy clause was removed in tuning.

Every transaction gets its own counterparty, so only the account under test
has a history. Amounts are chosen so that each "does not fire" test is
blocked only by the condition it is about.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R07 = load_rules(only=["R07"])[0]


def received(db, ts: str, amount: float, account: str = "B1") -> str:
    return db.add(
        ts,
        sender_account=f"C{db.count + 1}",
        receiver_account=account,
        amount_gbp=amount,
    )


def sent(db, ts: str, amount: float, account: str = "B1") -> str:
    return db.add(
        ts,
        sender_account=account,
        receiver_account=f"C{db.count + 1}",
        amount_gbp=amount,
    )


def baseline(
    db, first_ts: str = "2022-10-01 09:00", amount: float = 1000, account: str = "B1"
) -> None:
    """History from `first_ts`, then 3 x `amount` in the baseline period of a 1 March 2023 trigger.

    Baseline period = 120 to 30 days before 1 March 2023 = 1 Nov 2022 to 30 Jan
    2023, so the average per 30 days equals `amount` (when `first_ts` is
    before 1 Nov 2022).
    """
    received(db, first_ts, amount, account)
    received(db, "2022-11-15 09:00", amount, account)
    sent(db, "2022-12-15 09:00", amount, account)
    received(db, "2023-01-15 09:00", amount, account)


def test_fires_on_spike_in_volume_sent_and_received(rule_db) -> None:
    baseline(rule_db)
    ids = [
        received(rule_db, "2023-02-20 09:00", 13000),
        sent(rule_db, "2023-03-01 12:00", 13000),  # 26,000: >= 25,000 and > 15 x 1,000
    ]
    [hit] = find_candidates(rule_db.conn, R07, rule_db.schema)
    assert (hit.account_id, str(hit.trigger_ts)) == ("B1", "2023-03-01 12:00:00")
    assert hit.metrics == {
        "recent_gbp": 26000,
        "recent_n": 2,
        "baseline_avg_gbp": 1000,
        "ratio": 26.0,
    }
    assert sorted(hit.transaction_ids) == sorted(ids)


def test_does_not_fire_at_or_below_fifteen_times_baseline(rule_db) -> None:
    baseline(rule_db, amount=2000)  # average 2,000: 15 x = 30,000
    received(rule_db, "2023-02-20 09:00", 15000)
    sent(rule_db, "2023-03-01 12:00", 15000)  # exactly 15 x, above the volume minimum
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_does_not_fire_below_minimum_volume(rule_db) -> None:
    baseline(rule_db)  # average 1,000: 15 x = 15,000
    received(rule_db, "2023-02-20 09:00", 12499.99)
    sent(rule_db, "2023-03-01 12:00", 12500)  # 24,999.99: above 15 x, below £25,000
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_spike_needs_the_whole_baseline_inside_the_history(rule_db) -> None:
    # First seen 111 days before the trigger: 90+ days, but not the 120 needed.
    baseline(rule_db, first_ts="2022-11-10 09:00")
    received(rule_db, "2023-02-20 09:00", 13000)
    sent(rule_db, "2023-03-01 12:00", 13000)  # 26,000 > 15 x the 4,000 baseline's 1,333
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_reactivation_after_dormancy_no_longer_fires(rule_db) -> None:
    received(rule_db, "2022-10-01 09:00", 500)
    sent(rule_db, "2023-01-05 10:00", 30000)  # 96 days quiet: version 1 alerted here
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_first_ever_transactions_are_not_a_spike(rule_db) -> None:
    sent(rule_db, "2023-03-01 10:00", 50000)
    sent(rule_db, "2023-03-02 10:00", 50000)
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_empty_baseline_is_not_a_spike(rule_db) -> None:
    received(rule_db, "2022-10-01 09:00", 500)
    sent(rule_db, "2023-02-03 09:00", 30000)
    sent(rule_db, "2023-02-11 09:00", 30000)  # nothing in the baseline period: no spike
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []
