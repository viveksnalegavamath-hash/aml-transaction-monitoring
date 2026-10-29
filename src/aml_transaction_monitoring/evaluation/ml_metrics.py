"""Labels and metrics for the ML comparison, with the rules report's definitions.

Reads the label (evaluation schema) and scores any set of alerts, from rules
or from a model, the same way as `rules_report`:

- precision: share of alerts whose account had a suspicious transaction (sent
  or received) in the detection_days up to the alert;
- base rate: share of active accounts in the period that are suspicious;
  lift = precision / base rate;
- account recall (headline): share of suspicious accounts (sender or receiver
  of a suspicious transaction dated in the period) with an alert in the period.

An ML alert is raised at the end of its week (week_end, 23:59:59.999999).
Model ranking is also measured at the account-week level: PR-AUC (average
precision) of the score against "the account had a suspicious transaction
that week" (`evaluation.eval_account_week_labels`). Never accuracy.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import psycopg
from psycopg import sql
from sklearn.metrics import average_precision_score

# Suspicious transactions per in-scope party, from detection_days before the
# period (precision looks back that far) to the period's end.
SUSPICIOUS_SQL = """
select distinct f.transaction_id, f.transaction_ts, f.transaction_date, e.typology,
       party.account_id
from {labels}.eval_transaction_labels as e
join {marts}.fct_transactions as f using (transaction_id)
cross join lateral (values (f.sender_account), (f.receiver_account)) as party (account_id)
join {marts}.dim_account as d
  on d.account_id = party.account_id and (d.in_dev_sample or not %(sample_only)s)
where e.is_laundering
  and f.transaction_date between %(lookback_start)s and %(end)s
"""

ACTIVE_SQL = """
select count(distinct party.account_id)
from {marts}.fct_transactions as f
cross join lateral (values (f.sender_account), (f.receiver_account)) as party (account_id)
join {marts}.dim_account as d
  on d.account_id = party.account_id and (d.in_dev_sample or not %(sample_only)s)
where f.transaction_date between %(start)s and %(end)s
"""

WEEK_LABELS_SQL = """
select account_id, week_end
from {labels}.eval_account_week_labels
where week_end between %(start)s and %(end)s
"""

RULE_ALERTS_SQL = """
select a.rule_id, a.account_id, a.alert_date, a.trigger_ts
from {marts}.alerts as a
join {marts}.dim_account as d
  on d.account_id = a.account_id and (d.in_dev_sample or not %(sample_only)s)
where a.alert_date between %(start)s and %(end)s
"""


@dataclass(frozen=True)
class Period:
    """A run of whole scoring weeks: first day (a Thursday) to last week_end."""

    name: str
    start: date
    end: date

    @property
    def weeks(self) -> float:
        return ((self.end - self.start).days + 1) / 7


def weeks_period(name: str, first_week_end: date, last_week_end: date) -> Period:
    """The period covered by the weeks ending first_week_end .. last_week_end."""
    return Period(name, first_week_end - timedelta(days=6), last_week_end)


@dataclass
class Truth:
    """What the label says about one period, for scoring alerts."""

    period: Period
    detection_days: int
    suspicious: pd.DataFrame  # account_id, transaction_ts, transaction_date, typology
    active_accounts: int

    @property
    def in_period(self) -> pd.DataFrame:
        s = self.suspicious
        return s[
            (s["transaction_date"] >= self.period.start)
            & (s["transaction_date"] <= self.period.end)
        ]

    @property
    def suspicious_accounts(self) -> set[str]:
        return set(self.in_period["account_id"])

    @property
    def base_rate(self) -> float:
        return len(self.suspicious_accounts) / self.active_accounts


def _fetch(
    conn: psycopg.Connection, template: str, params: dict, **schemas: str
) -> list[tuple]:
    """Run a query with its schema placeholders filled in as identifiers."""
    query = sql.SQL(template).format(
        **{key: sql.Identifier(name) for key, name in schemas.items()}
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def fetch_truth(
    conn: psycopg.Connection,
    marts: str,
    labels: str,
    period: Period,
    detection_days: int,
    sample_only: bool = False,
) -> Truth:
    """Suspicious transactions and active accounts for a period (in-scope accounts)."""
    params = {
        "start": period.start,
        "end": period.end,
        "lookback_start": period.start - timedelta(days=detection_days),
        "sample_only": sample_only,
    }
    rows = _fetch(conn, SUSPICIOUS_SQL, params, marts=marts, labels=labels)
    suspicious = pd.DataFrame(
        rows,
        columns=[
            "transaction_id",
            "transaction_ts",
            "transaction_date",
            "typology",
            "account_id",
        ],
    )
    (active,) = _fetch(conn, ACTIVE_SQL, params, marts=marts)[0]
    return Truth(period, detection_days, suspicious, active)


def fetch_week_labels(
    conn: psycopg.Connection, labels: str, start: date, end: date
) -> pd.DataFrame:
    """Positive account-weeks (account_id, week_end) for weeks ending in [start, end]."""
    rows = _fetch(conn, WEEK_LABELS_SQL, {"start": start, "end": end}, labels=labels)
    frame = pd.DataFrame(rows, columns=["account_id", "week_end"])
    frame["week_end"] = pd.to_datetime(frame["week_end"]).values.astype("datetime64[D]")
    return frame


def label_vector(keys: pd.DataFrame, positives: pd.DataFrame) -> np.ndarray:
    """1 for each key row (account_id, week_end) that is a positive account-week."""
    marked = keys.merge(
        positives.assign(_positive=1), on=["account_id", "week_end"], how="left"
    )
    return marked["_positive"].fillna(0).to_numpy(dtype=np.int8)


def fetch_rule_alerts(
    conn: psycopg.Connection, marts: str, period: Period, sample_only: bool = False
) -> pd.DataFrame:
    """Rule alerts dated in the period (rule_id, account_id, alert_date, trigger_ts)."""
    rows = _fetch(
        conn,
        RULE_ALERTS_SQL,
        {"start": period.start, "end": period.end, "sample_only": sample_only},
        marts=marts,
    )
    return pd.DataFrame(
        rows, columns=["rule_id", "account_id", "alert_date", "trigger_ts"]
    )


def week_alerts(keys: pd.DataFrame) -> pd.DataFrame:
    """ML alerts for the given account-weeks, raised at the very end of week_end."""
    week_end = [d.date() for d in pd.to_datetime(keys["week_end"])]
    return pd.DataFrame(
        {
            "account_id": keys["account_id"].to_numpy(),
            "alert_date": week_end,
            "trigger_ts": [datetime.combine(d, time.max) for d in week_end],
        }
    )


@dataclass(frozen=True)
class AlertScore:
    """Alert-level results for one set of alerts in one period."""

    alerts: int
    weeks: float
    true_alerts: int
    suspicious_accounts: int
    caught_accounts: int
    base_rate: float

    @property
    def alerts_per_week(self) -> float:
        return self.alerts / self.weeks

    @property
    def true_alerts_per_week(self) -> float:
        return self.true_alerts / self.weeks

    @property
    def precision(self) -> float | None:
        return self.true_alerts / self.alerts if self.alerts else None

    @property
    def lift(self) -> float | None:
        p = self.precision
        return p / self.base_rate if p is not None and self.base_rate else None

    @property
    def account_recall(self) -> float | None:
        if not self.suspicious_accounts:
            return None
        return self.caught_accounts / self.suspicious_accounts


def true_alert_mask(alerts: pd.DataFrame, truth: Truth) -> np.ndarray:
    """For each alert: did its account have a suspicious transaction in the
    detection_days up to the alert (inclusive)?"""
    if alerts.empty:
        return np.zeros(0, dtype=bool)
    indexed = alerts[["account_id", "trigger_ts"]].reset_index(drop=True)
    indexed["_row"] = np.arange(len(indexed))
    pairs = indexed.merge(
        truth.suspicious[["account_id", "transaction_ts"]], on="account_id"
    )
    window = timedelta(days=truth.detection_days)
    hit = (pairs["transaction_ts"] <= pairs["trigger_ts"]) & (
        pairs["transaction_ts"] >= pairs["trigger_ts"] - window
    )
    mask = np.zeros(len(indexed), dtype=bool)
    mask[pairs.loc[hit, "_row"].to_numpy()] = True
    return mask


def in_period(alerts: pd.DataFrame, period: Period) -> pd.DataFrame:
    return alerts[
        (alerts["alert_date"] >= period.start) & (alerts["alert_date"] <= period.end)
    ]


def score_alerts(alerts: pd.DataFrame, truth: Truth) -> AlertScore:
    """Precision, lift and account recall of alerts (account_id, alert_date, trigger_ts)."""
    alerts = in_period(alerts, truth.period)
    suspicious_accounts = truth.suspicious_accounts
    return AlertScore(
        alerts=len(alerts),
        weeks=truth.period.weeks,
        true_alerts=int(true_alert_mask(alerts, truth).sum()),
        suspicious_accounts=len(suspicious_accounts),
        caught_accounts=len(suspicious_accounts & set(alerts["account_id"])),
        base_rate=truth.base_rate,
    )


def recall_by_typology(
    alerts: pd.DataFrame, truth: Truth
) -> dict[str, tuple[int, int]]:
    """{typology: (caught accounts, suspicious accounts)} in the period.

    An account counts once per typology it took part in."""
    alerted = set(in_period(alerts, truth.period)["account_id"])
    pairs = truth.in_period[["typology", "account_id"]].drop_duplicates()
    caught = pairs["account_id"].isin(alerted)
    grouped = pairs.assign(caught=caught).groupby("typology")["caught"]
    return {t: (int(c.sum()), int(c.size)) for t, c in grouped}


def pr_auc(y: np.ndarray, score: np.ndarray) -> float:
    """Average precision (area under the precision-recall curve)."""
    return float(average_precision_score(y, score))
