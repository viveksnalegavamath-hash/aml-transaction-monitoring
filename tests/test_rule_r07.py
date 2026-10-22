"""R07 Behaviour change on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R07_behaviour_change.yaml: 30-day volume (sent and
received) more than 5 times the average 30-day volume of the 90 days before,
or first activity after 90+ days dormant; at least 90 days of history.

Every transaction gets its own counterparty, so only the account under test
has a history.
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


def baseline(db, first_ts: str = "2022-10-01 09:00", account: str = "B1") -> None:
    """History from `first_ts`, then 3,000 in the baseline period of a 1 March 2023 trigger.

    Baseline period = 120 to 30 days before 1 March 2023 = 1 Nov 2022 to 30 Jan
    2023: an average of 1,000 per 30 days, so a spike needs more than 5,000.
    """
    received(db, first_ts, 1000, account)
    received(db, "2022-11-15 09:00", 1000, account)
    sent(db, "2022-12-15 09:00", 1000, account)
    received(db, "2023-01-15 09:00", 1000, account)


def test_fires_on_spike_in_volume_sent_and_received(rule_db) -> None:
    baseline(rule_db)
    ids = [
        received(rule_db, "2023-02-20 09:00", 3000),
        sent(rule_db, "2023-03-01 12:00", 3000),  # 6,000 in 30 days > 5 x 1,000
    ]
    [hit] = find_candidates(rule_db.conn, R07, rule_db.schema)
    assert (hit.account_id, str(hit.trigger_ts)) == ("B1", "2023-03-01 12:00:00")
    assert (hit.metrics["is_spike"], hit.metrics["is_dormant"]) == (True, False)
    assert (hit.metrics["recent_gbp"], hit.metrics["baseline_avg_gbp"]) == (6000, 1000)
    assert sorted(hit.transaction_ids) == sorted(ids)
    assert hit.metrics["finding"] == (
        "volume of £6,000.00 in 2 transactions, 6.0 times the previous average of £1,000.00"
    )


def test_does_not_fire_at_or_below_five_times_baseline(rule_db) -> None:
    baseline(rule_db)
    received(rule_db, "2023-02-20 09:00", 2500)
    sent(rule_db, "2023-03-01 12:00", 2500)  # exactly 5 x
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_spike_needs_the_whole_baseline_inside_the_history(rule_db) -> None:
    # First seen 111 days before the trigger: 90+ days, but not the 120 needed.
    baseline(rule_db, first_ts="2022-11-10 09:00")
    received(rule_db, "2023-02-20 09:00", 4000)
    sent(rule_db, "2023-03-01 12:00", 4000)  # 8,000 > 5 x the 4,000 baseline's 1,333
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_fires_on_first_activity_after_90_days_dormant(rule_db) -> None:
    received(rule_db, "2022-10-01 09:00", 500)
    tid = sent(rule_db, "2023-01-05 10:00", 500)  # 96 days later
    [hit] = find_candidates(rule_db.conn, R07, rule_db.schema)
    assert (hit.account_id, hit.transaction_ids) == ("B1", [tid])
    assert (hit.metrics["is_spike"], hit.metrics["is_dormant"]) == (False, True)
    assert hit.metrics["dormant_for_days"] == 96
    assert str(hit.window_start) == "2022-10-01 09:00:00"
    assert (
        hit.metrics["finding"]
        == "first activity after 96 days with none (last active 2022-10-01)"
    )


def test_does_not_fire_after_less_than_90_days_quiet(rule_db) -> None:
    received(rule_db, "2022-09-01 09:00", 500)
    received(rule_db, "2022-10-01 09:00", 500)
    sent(rule_db, "2022-12-29 09:00", 500)  # 89 days after the last activity
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_first_ever_transaction_is_new_not_dormant(rule_db) -> None:
    sent(rule_db, "2023-03-01 10:00", 50000)
    sent(rule_db, "2023-03-02 10:00", 50000)
    assert find_candidates(rule_db.conn, R07, rule_db.schema) == []


def test_empty_baseline_after_reactivation_is_not_a_spike(rule_db) -> None:
    received(rule_db, "2022-10-01 09:00", 500)
    first_back = sent(rule_db, "2023-02-03 09:00", 500)  # dormant 125 days: fires
    sent(rule_db, "2023-02-11 09:00", 500)  # baseline period empty: no spike
    [hit] = find_candidates(rule_db.conn, R07, rule_db.schema)
    assert hit.transaction_ids == [first_back]
