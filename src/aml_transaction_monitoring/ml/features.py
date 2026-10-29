"""Load ML features for account-weeks. No labels are read here.

One row per active account-week (an account with a transaction sent or
received in a Thursday-to-Wednesday week), scored at the end of the week
(`week_end`). Three groups of columns:

- behaviour: every column of `features.feat_account_week` (dbt) except the
  keys and the artefact columns: rolling 7/30/90-day counts, totals,
  counterparties, cash / cross-border / high-risk shares and amounts against
  the account's own history;
- rule_hits: from the rules engine's alerts, per enabled rule, the alerts on
  the account dated in the 7 days up to week_end, and the number of distinct
  rules alerting in the 30 days up to it;
- artefact: the SAML-D "receiver is a customer" artefact. Never a model
  feature; loaded only to measure its effect separately.

Everything describes the account up to the end of its week only: the dbt
table is tested for this (`feat_account_week_point_in_time`) and rule hits
count only alerts dated on or before week_end.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

TABLE = "feat_account_week"
KEY_COLUMNS = ("account_id", "week_end")
ARTEFACT_PREFIX = "artefact_"
GROUPS = ("behaviour", "rule_hits", "artefact")
BATCH_SIZE = 50_000

COLUMNS_SQL = """
select column_name, data_type
from information_schema.columns
where table_schema = %s and table_name = %s
order by ordinal_position
"""

# Rule hits are counted in a CTE on the alerts side, then joined: grouping the
# wide feature rows themselves would be far slower.
LOAD_SQL = """
with hits as (
    select
        f.account_id,
        f.week_end,
        {rule_counts},
        count(distinct a.rule_id) as rules_30d
    from {features}.{table} as f
    join {marts}.alerts as a
      on a.account_id = f.account_id
     and a.alert_date between f.week_end - 29 and f.week_end
    where f.week_end between %(start)s and %(end)s
    group by f.account_id, f.week_end
)

select f.account_id, f.week_end, {columns}
from {features}.{table} as f
left join hits as h using (account_id, week_end)
{scope}
where f.week_end between %(start)s and %(end)s
order by f.week_end, f.account_id collate "C"
"""

COUNT_SQL = """
select count(*)
from {features}.{table} as f
{scope}
where f.week_end between %(start)s and %(end)s
"""

# On the dev sample only sampled accounts have complete histories.
SAMPLE_SCOPE = (
    "join {marts}.dim_account as d on d.account_id = f.account_id and d.in_dev_sample"
)


@dataclass
class Features:
    """Feature rows: keys (account_id, week_end) and a float32 matrix."""

    keys: pd.DataFrame
    values: np.ndarray
    names: list[str]

    def __len__(self) -> int:
        return len(self.keys)

    def matrix(self, names: list[str]) -> np.ndarray:
        """The columns `names`, in that order."""
        index = {n: i for i, n in enumerate(self.names)}
        missing = [n for n in names if n not in index]
        if missing:
            raise KeyError(f"Unknown feature columns: {missing}")
        return self.values[:, [index[n] for n in names]]


def rule_hit_column(rule_id: str) -> str:
    return f"rule_{rule_id}_7d"


def table_columns(conn: psycopg.Connection, schema: str) -> dict[str, str]:
    """{column: data type} of the feature table, in table order."""
    with conn.cursor() as cur:
        cur.execute(COLUMNS_SQL, (schema, TABLE))
        columns = dict(cur.fetchall())
    if not columns:
        raise RuntimeError(f"{schema}.{TABLE} not found: run dbt build first")
    return columns


def feature_groups(table: Iterable[str], rule_ids: list[str]) -> dict[str, list[str]]:
    """{group: column names} for the three feature groups."""
    return {
        "behaviour": [
            c
            for c in table
            if c not in KEY_COLUMNS and not c.startswith(ARTEFACT_PREFIX)
        ],
        "rule_hits": [rule_hit_column(r) for r in rule_ids] + ["rules_30d"],
        "artefact": [c for c in table if c.startswith(ARTEFACT_PREFIX)],
    }


def columns_for(feature_set: list[str], groups: dict[str, list[str]]) -> list[str]:
    """The columns of a feature set, e.g. ["behaviour", "rule_hits"]."""
    unknown = [g for g in feature_set if g not in GROUPS]
    if unknown:
        raise ValueError(f"Unknown feature groups: {unknown}")
    return [c for g in feature_set for c in groups[g]]


def load_features(
    conn: psycopg.Connection,
    features_schema: str,
    marts_schema: str,
    start: date,
    end: date,
    rule_ids: list[str],
    sample_only: bool = False,
) -> Features:
    """Every feature column (all three groups) for weeks ending in [start, end].

    Rows are sorted by week_end, then account_id. Streams from a server-side
    cursor into a preallocated float32 matrix, so memory holds the matrix
    rather than millions of Python rows.
    """
    types = table_columns(conn, features_schema)
    groups = feature_groups(types, rule_ids)
    table_cols = groups["behaviour"] + groups["artefact"]
    # Booleans (an artefact flag) cannot be cast to real directly.
    selected = [
        sql.SQL("f.{}::int::real" if types[c] == "boolean" else "f.{}::real").format(
            sql.Identifier(c)
        )
        for c in table_cols
    ] + [
        sql.SQL("coalesce(h.{}, 0)::real").format(sql.Identifier(c))
        for c in groups["rule_hits"]
    ]
    rule_counts = sql.SQL(",\n        ").join(
        sql.SQL(
            "count(*) filter (where a.rule_id = {} and a.alert_date > f.week_end - 7) as {}"
        ).format(sql.Literal(r), sql.Identifier(rule_hit_column(r)))
        for r in rule_ids
    )
    names = {
        "features": sql.Identifier(features_schema),
        "marts": sql.Identifier(marts_schema),
        "table": sql.Identifier(TABLE),
    }
    scope = (
        sql.SQL(SAMPLE_SCOPE).format(marts=names["marts"])
        if sample_only
        else sql.SQL("")
    )
    params = {"start": start, "end": end}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql.SQL(COUNT_SQL).format(scope=scope, **names), params)
            n_rows = cur.fetchone()[0]
        n_cols = len(selected)
        values = np.empty((n_rows, n_cols), dtype=np.float32)
        accounts: list[str] = []
        weeks: list[date] = []
        query = sql.SQL(LOAD_SQL).format(
            rule_counts=rule_counts,
            columns=sql.SQL(", ").join(selected),
            scope=scope,
            **names,
        )
        row = 0
        with conn.cursor(name="ml_features") as cur:
            cur.itersize = BATCH_SIZE
            cur.execute(query, params)
            while batch := cur.fetchmany(BATCH_SIZE):
                accounts.extend(r[0] for r in batch)
                weeks.extend(r[1] for r in batch)
                values[row : row + len(batch)] = [r[2:] for r in batch]
                row += len(batch)
    if row != n_rows:
        raise RuntimeError(f"Expected {n_rows} feature rows, read {row}")
    keys = pd.DataFrame(
        {
            "account_id": np.array(accounts, dtype=object),
            "week_end": pd.to_datetime(weeks).values.astype("datetime64[D]"),
        }
    )
    logger.info(
        "Loaded %s account-weeks x %s columns (%.0f MB) for weeks ending %s to %s",
        f"{n_rows:,}",
        n_cols,
        values.nbytes / 1e6,
        start,
        end,
    )
    return Features(keys, values, table_cols + groups["rule_hits"])
