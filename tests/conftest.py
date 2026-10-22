"""Shared pytest fixtures.

`rule_db` gives a rule test its own throwaway schema with an empty
`fct_transactions` (same columns the rules use), on the project database. The
schema is dropped afterwards. Tests using it are skipped if the database is
not reachable.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
import pytest
from psycopg import sql

from aml_transaction_monitoring.db import get_connection

FCT_COLUMNS = """
    transaction_id                    text PRIMARY KEY,
    transaction_ts                    timestamp NOT NULL,
    transaction_date                  date NOT NULL,
    sender_account                    text NOT NULL,
    receiver_account                  text NOT NULL,
    amount_gbp                        numeric NOT NULL,
    payment_currency                  text NOT NULL,
    received_currency                 text NOT NULL,
    payment_type                      text NOT NULL,
    is_cross_border                   boolean NOT NULL,
    sender_country                    text NOT NULL,
    sender_country_fatf_status        text NOT NULL,
    sender_country_internal_rating    text,
    receiver_country                  text NOT NULL,
    receiver_country_fatf_status      text NOT NULL,
    receiver_country_internal_rating  text,
    sender_is_customer                boolean NOT NULL,
    receiver_is_customer              boolean NOT NULL
"""

DEFAULTS: dict[str, Any] = {
    "sender_account": "S1",
    "receiver_account": "R1",
    "amount_gbp": 100,
    "payment_currency": "GBP",
    "received_currency": "GBP",
    "payment_type": "debit_card",
    "is_cross_border": False,
    "sender_country": "GB",
    "sender_country_fatf_status": "not_listed",
    "sender_country_internal_rating": "low",
    "receiver_country": "GB",
    "receiver_country_fatf_status": "not_listed",
    "receiver_country_internal_rating": "low",
    "sender_is_customer": True,
    "receiver_is_customer": True,
}


@dataclass
class RuleDb:
    """A throwaway schema holding a tiny fct_transactions table."""

    conn: psycopg.Connection
    schema: str
    count: int = 0

    def add(self, ts: str, **values: Any) -> str:
        """Insert one transaction at `ts` ("YYYY-MM-DD HH:MM"); return its ID."""
        self.count += 1
        when = datetime.fromisoformat(ts)
        row = {
            **DEFAULTS,
            **values,
            "transaction_id": values.get("transaction_id", f"T{self.count:03d}"),
            "transaction_ts": when,
            "transaction_date": when.date(),
        }
        columns = list(row)
        self.conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(self.schema, "fct_transactions"),
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            ),
            [row[c] for c in columns],
        )
        return row["transaction_id"]


@pytest.fixture
def rule_db() -> Iterator[RuleDb]:
    try:
        conn = get_connection()
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"database not reachable: {e}")
    conn.autocommit = True
    schema = f"test_rules_{uuid.uuid4().hex[:8]}"
    try:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                sql.Identifier(schema, "fct_transactions"), sql.SQL(FCT_COLUMNS)
            )
        )
        yield RuleDb(conn, schema)
    finally:
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )
        conn.close()
