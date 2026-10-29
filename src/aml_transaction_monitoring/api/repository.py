"""Read-only data access for the API, as the database role in config/api.yaml.

The role can read only marts.alerts, marts.ml_alerts and
features.feat_account_week (api.provision), never the labels.
"""

from datetime import date
from typing import Any, Protocol

import numpy as np
import psycopg
from psycopg.rows import dict_row

from aml_transaction_monitoring.db import get_reader_connection
from aml_transaction_monitoring.ml.features import load_features

RULE_ALERTS_SQL = """
select alert_id, rule_id, rule_version, alert_date, trigger_ts, reason, status
from marts.alerts
where account_id = %s
order by trigger_ts desc, rule_id
limit %s
"""

ML_ALERTS_SQL = """
select alert_id, model, week_end, score, rank_in_week, reasons
from marts.ml_alerts
where account_id = %s
order by week_end desc, model
limit %s
"""


class Repository(Protocol):
    """What the API reads; a fake implements it in the tests."""

    def ping(self) -> bool: ...

    def features(
        self, account_id: str, week_end: date, columns: list[str]
    ) -> np.ndarray | None: ...

    def alerts(
        self, account_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...


class PostgresRepository:
    """The real thing: one short read-only connection per request."""

    def __init__(self, role: str, rule_ids: list[str]) -> None:
        self.role = role
        self.rule_ids = rule_ids

    def _connect(self) -> psycopg.Connection:
        return get_reader_connection(self.role)

    def ping(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("select 1")
            return True
        except (psycopg.Error, RuntimeError):
            return False

    def features(
        self, account_id: str, week_end: date, columns: list[str]
    ) -> np.ndarray | None:
        """The account-week's feature row (in `columns` order), or None if inactive."""
        with self._connect() as conn:
            found = load_features(
                conn,
                "features",
                "marts",
                week_end,
                week_end,
                self.rule_ids,
                account_id=account_id,
            )
        return found.matrix(columns)[0] if len(found) else None

    def alerts(
        self, account_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(rule alerts, ML alerts) for one account, newest first."""
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            rule_rows = cur.execute(RULE_ALERTS_SQL, (account_id, limit)).fetchall()
            ml_rows = cur.execute(ML_ALERTS_SQL, (account_id, limit)).fetchall()
        return rule_rows, ml_rows
