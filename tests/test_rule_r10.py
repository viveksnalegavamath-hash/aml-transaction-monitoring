"""R10 Income mismatch on tiny hand-made data: where it fires and where it does not.

Thresholds from rules/R10_income_mismatch.yaml: money received within 30 days
of more than 2 times the declared monthly income. R10 is disabled on SAML-D
(synthetic income), but its logic is tested like every other rule.
"""

import pytest
from psycopg import sql

from aml_transaction_monitoring.rules.engine import find_candidates, load_rules

R10 = load_rules(only=["R10"])[0]


@pytest.fixture
def kyc_db(rule_db):
    """rule_db plus a KYC table: K1 declares £24,000 a year (£2,000 a month)."""
    rule_db.conn.execute(
        sql.SQL(
            "CREATE TABLE {} (customer_id text PRIMARY KEY,"
            " declared_annual_income_gbp bigint NOT NULL)"
        ).format(sql.Identifier(rule_db.schema, "dim_customer"))
    )
    rule_db.conn.execute(
        sql.SQL("INSERT INTO {} VALUES ('K1', 24000)").format(
            sql.Identifier(rule_db.schema, "dim_customer")
        )
    )
    return rule_db


def pay_in(db, ts: str, amount: float, account: str = "K1") -> str:
    return db.add(ts, sender_account="X", receiver_account=account, amount_gbp=amount)


def test_fires_when_inflows_exceed_twice_monthly_income(kyc_db) -> None:
    ids = [
        pay_in(kyc_db, "2023-03-01 10:00", 2500),
        pay_in(kyc_db, "2023-03-20 10:00", 2000),
    ]
    [hit] = find_candidates(kyc_db.conn, R10, kyc_db.schema)
    assert (hit.account_id, str(hit.trigger_ts)) == ("K1", "2023-03-20 10:00:00")
    assert hit.metrics == {
        "inflow_gbp": 4500,
        "n_inflows": 2,
        "monthly_income_gbp": 2000,
        "ratio": 2.25,
    }
    assert sorted(hit.transaction_ids) == sorted(ids)


def test_does_not_fire_at_or_below_twice_monthly_income(kyc_db) -> None:
    pay_in(kyc_db, "2023-03-01 10:00", 2000)
    pay_in(kyc_db, "2023-03-20 10:00", 2000)  # exactly 2 x
    assert find_candidates(kyc_db.conn, R10, kyc_db.schema) == []


def test_does_not_fire_when_inflows_are_spread_beyond_window(kyc_db) -> None:
    pay_in(kyc_db, "2023-01-01 10:00", 3000)
    pay_in(kyc_db, "2023-02-05 10:00", 3000)  # 35 days later
    assert find_candidates(kyc_db.conn, R10, kyc_db.schema) == []


def test_money_sent_does_not_count(kyc_db) -> None:
    kyc_db.add(
        "2023-03-01 10:00", sender_account="K1", receiver_account="Z", amount_gbp=50000
    )
    assert find_candidates(kyc_db.conn, R10, kyc_db.schema) == []


def test_accounts_without_kyc_record_are_not_checked(kyc_db) -> None:
    pay_in(kyc_db, "2023-03-01 10:00", 1_000_000, account="NOKYC")
    assert find_candidates(kyc_db.conn, R10, kyc_db.schema) == []


def test_r10_is_disabled_by_default() -> None:
    assert "R10" not in [r.id for r in load_rules()]
    assert R10.enabled is False
