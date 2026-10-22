"""R05 High-risk jurisdiction on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R05_high_risk_jurisdiction.yaml: any payment to a FATF
call-for-action country; at least £5,000 to a FATF increased-monitoring
country; at least £5,000 to or from a country with internal rating high.
"""

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R05 = load_rules(only=["R05"])[0]


def pay_to(
    db, country: str, amount: float, fatf: str = "not_listed", rating: str = "low"
) -> str:
    return db.add(
        "2023-03-01 10:00",
        sender_account="S1",
        receiver_account="X",
        amount_gbp=amount,
        is_cross_border=True,
        receiver_country=country,
        receiver_country_fatf_status=fatf,
        receiver_country_internal_rating=rating,
    )


def pay_from(
    db, country: str, amount: float, fatf: str = "not_listed", rating: str = "low"
) -> str:
    return db.add(
        "2023-03-01 10:00",
        sender_account="X",
        receiver_account="R1",
        amount_gbp=amount,
        is_cross_border=True,
        sender_country=country,
        sender_country_fatf_status=fatf,
        sender_country_internal_rating=rating,
    )


def test_fires_on_any_payment_to_call_for_action_country(rule_db) -> None:
    tid = pay_to(rule_db, "IR", 50, fatf="call_for_action", rating=None)
    [hit] = find_candidates(rule_db.conn, R05, rule_db.schema)
    assert (hit.account_id, hit.transaction_ids) == ("S1", [tid])
    assert hit.metrics == {
        "amount_gbp": 50,
        "direction": "to",
        "country": "IR",
        "matched": "FATF call for action",
    }


def test_fires_on_grey_list_payment_at_threshold(rule_db) -> None:
    pay_to(rule_db, "ZZ", 5000, fatf="increased_monitoring", rating="medium")
    [hit] = find_candidates(rule_db.conn, R05, rule_db.schema)
    assert hit.metrics["matched"] == "FATF increased monitoring"


def test_does_not_fire_on_grey_list_payment_below_threshold(rule_db) -> None:
    pay_to(rule_db, "ZZ", 4999.99, fatf="increased_monitoring", rating="medium")
    assert find_candidates(rule_db.conn, R05, rule_db.schema) == []


def test_fires_on_payment_to_high_rated_country_and_lists_every_clause(rule_db) -> None:
    pay_to(rule_db, "ZZ", 6000, fatf="increased_monitoring", rating="high")
    [hit] = find_candidates(rule_db.conn, R05, rule_db.schema)
    assert hit.account_id == "S1"
    assert hit.metrics["matched"] == "FATF increased monitoring; internal rating high"


def test_fires_on_receiver_for_payment_from_high_rated_country(rule_db) -> None:
    tid = pay_from(rule_db, "PK", 5000, rating="high")
    [hit] = find_candidates(rule_db.conn, R05, rule_db.schema)
    assert (hit.account_id, hit.transaction_ids) == ("R1", [tid])
    assert (hit.metrics["direction"], hit.metrics["country"]) == ("from", "PK")


def test_does_not_fire_below_high_rating_threshold(rule_db) -> None:
    pay_to(rule_db, "PK", 4999.99, rating="high")
    pay_from(rule_db, "PK", 4999.99, rating="high")
    assert find_candidates(rule_db.conn, R05, rule_db.schema) == []


def test_does_not_fire_for_low_or_medium_rated_countries(rule_db) -> None:
    pay_to(rule_db, "FR", 50000, rating="low")
    pay_to(rule_db, "IN", 50000, rating="medium")
    pay_from(rule_db, "IN", 50000, rating="medium")
    assert find_candidates(rule_db.conn, R05, rule_db.schema) == []


def test_fatf_clauses_apply_to_payments_sent_only(rule_db) -> None:
    pay_from(rule_db, "IR", 50000, fatf="call_for_action", rating=None)
    pay_from(rule_db, "ZZ", 50000, fatf="increased_monitoring", rating="medium")
    assert find_candidates(rule_db.conn, R05, rule_db.schema) == []
