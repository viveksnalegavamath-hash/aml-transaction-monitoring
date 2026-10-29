"""Tests for the ML layer: split, features, models, reasons and metrics."""

import re
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg import sql
from xgboost import XGBClassifier

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.evaluation.ml_experiment import (
    Dataset,
    Split,
    holdout,
    holdout_period,
    load_config,
    smallest_matching_volume,
    training_rows,
    week_end_range,
)
from aml_transaction_monitoring.evaluation.ml_metrics import (
    Period,
    Truth,
    label_vector,
    recall_by_typology,
    score_alerts,
    week_alerts,
    weeks_period,
)
from aml_transaction_monitoring.evaluation.rules_report import (
    load_config as load_evaluation_config,
)
from aml_transaction_monitoring.ml.features import (
    Features,
    columns_for,
    feature_groups,
    load_features,
    table_columns,
)
from aml_transaction_monitoring.ml.model import (
    contributions,
    describe,
    make_logistic,
    positive_weight,
    rank_in_week,
    signed_log1p,
    top_k_per_week,
    top_reasons,
)

CONFIG = load_config()
EVALUATION = load_evaluation_config()
SRC = Path(__file__).resolve().parents[1] / "src" / "aml_transaction_monitoring"
RULE_NAMES = {"R06": "Large vs profile"}


def ts(text: str) -> datetime:
    """A naive timestamp, like the database's transaction_ts."""
    return datetime.fromisoformat(text)


# --- Split: train and validate inside the tuning months, hold-out untouched -------


def test_split_weeks_are_jan_to_apr_then_may() -> None:
    assert week_end_range(CONFIG["split"]["train"]) == (
        date(2023, 1, 4),
        date(2023, 4, 26),
    )
    assert week_end_range(CONFIG["split"]["validation"]) == (
        date(2023, 5, 3),
        date(2023, 5, 31),
    )


def test_train_and_validation_weeks_stay_inside_the_tuning_months() -> None:
    tuning = EVALUATION["periods"]["tuning"]
    for part in ("train", "validation"):
        first, last = week_end_range(CONFIG["split"][part])
        period = weeks_period(part, first, last)
        assert tuning["start"] <= period.start and period.end <= tuning["end"]
    # Validation comes after training: weeks never overlap.
    assert (
        week_end_range(CONFIG["split"]["train"])[1]
        < week_end_range(CONFIG["split"]["validation"])[0]
    )


def test_holdout_is_twelve_whole_weeks() -> None:
    period = holdout_period(EVALUATION)
    assert (period.start, period.end, period.weeks) == (
        date(2023, 6, 1),
        date(2023, 8, 23),
        12,
    )


def test_holdout_refuses_to_run_twice(tmp_path: Path) -> None:
    (tmp_path / "ml_evaluation.md").write_text("already scored")
    with pytest.raises(RuntimeError, match="already been scored"):
        holdout(
            Dataset(sample=False), CONFIG, {"output_dir": str(tmp_path)}, again=False
        )


# --- Feature sets ---------------------------------------------------------------------


def test_artefact_columns_only_in_the_diagnostic_set() -> None:
    table = ["account_id", "week_end", "in_n_7", "artefact_is_customer"]
    groups = feature_groups(table, ["R02", "R06"])
    assert groups["behaviour"] == ["in_n_7"]
    assert groups["rule_hits"] == ["rule_R02_7d", "rule_R06_7d", "rules_30d"]
    assert groups["artefact"] == ["artefact_is_customer"]
    sets = CONFIG["feature_sets"]
    for name in ("ml_only", "rules_ml"):
        assert "artefact_is_customer" not in columns_for(sets[name], groups)
    assert "rule_R02_7d" not in columns_for(sets["ml_only"], groups)
    assert "rule_R02_7d" in columns_for(sets["rules_ml"], groups)


def test_unknown_feature_group_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        columns_for(["behaviour", "labels"], {"behaviour": []})


def test_ml_package_never_touches_the_evaluation_code_or_labels() -> None:
    # No import of the evaluation package, no evaluation schema, no label table.
    forbidden = re.compile(
        r"aml_transaction_monitoring\.evaluation|evaluation(_sample)?[\"']?\.|"
        r"eval_\w+_labels|is_laundering",
        re.IGNORECASE,
    )
    files = sorted((SRC / "ml").glob("*.py"))
    assert files
    for path in files:
        match = forbidden.search(path.read_text(encoding="utf-8"))
        assert match is None, f"{path.name}: {match.group(0)!r}"
    assert forbidden.search(
        "from aml_transaction_monitoring.evaluation.ml_metrics import x"
    )


# --- Training rows and weights ----------------------------------------------------------


def test_training_rows_all_rows_when_share_is_one() -> None:
    y = np.array([0, 1, 0, 0])
    rows, weights = training_rows(y, 1.0, seed=1)
    assert rows.tolist() == [0, 1, 2, 3]
    assert weights.tolist() == [1, 1, 1, 1]


def test_down_sampling_keeps_every_positive_and_reweights_negatives() -> None:
    y = np.array([1] * 10 + [0] * 1000)
    rows, weights = training_rows(y, 0.25, seed=1)
    assert set(range(10)) <= set(rows.tolist())
    assert (weights[y[rows] == 1] == 1).all()
    assert (weights[y[rows] == 0] == 4).all()
    assert 150 < (y[rows] == 0).sum() < 350


def test_positive_weight() -> None:
    y = np.array([1] * 2 + [0] * 98)
    assert positive_weight(y, "balanced") == 49
    assert positive_weight(y, "sqrt") == pytest.approx(7)
    with pytest.raises(ValueError):
        positive_weight(np.zeros(5), "balanced")


# --- Models -------------------------------------------------------------------------------


def test_signed_log1p_keeps_zero_and_sign() -> None:
    x = np.array([-np.e + 1, 0.0, np.e - 1])
    assert signed_log1p(x).tolist() == pytest.approx([-1.0, 0.0, 1.0])


def tiny_data(seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(400, 3)).astype(np.float32)
    y = (x[:, 0] + 0.5 * x[:, 1] + rng.normal(scale=0.5, size=400) > 1).astype(int)
    return x, y


def test_tree_shap_values_add_up_to_the_model_output() -> None:
    x, y = tiny_data()
    model = XGBClassifier(n_estimators=20, max_depth=3, random_state=1).fit(x, y)
    shap_values = contributions(model, x[:5])
    margin = model.predict(x[:5], output_margin=True)
    base = margin - shap_values.sum(axis=1)
    # Same base value for every row: the SHAP values explain the whole difference.
    assert np.allclose(base, base[0], atol=1e-4)


def test_linear_contributions_are_coefficient_times_standardised_value() -> None:
    x, y = tiny_data()
    model = make_logistic({"C": 1.0, "max_iter": 500}, seed=1).fit(x, y)
    scaled = model[:-1].transform(x[:3])
    assert np.allclose(
        contributions(model, x[:3]), scaled * model[-1].coef_[0], atol=1e-6
    )


def test_top_k_per_week_takes_k_per_week() -> None:
    weeks = np.array(["2023-06-07"] * 4 + ["2023-06-14"] * 3, dtype="datetime64[D]")
    score = np.array([0.1, 0.9, 0.5, 0.7, 0.3, 0.2, 0.8])
    mask = top_k_per_week(weeks, score, k=2)
    assert mask.tolist() == [False, True, False, True, True, False, True]
    assert rank_in_week(weeks, score).tolist() == [4, 1, 3, 2, 2, 3, 1]


# --- Reasons ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("feature", "value", "text"),
    [
        ("out_new_counterparties_7", 1, "1 new payee in 7 days"),
        ("in_new_counterparties_30", 3, "3 new senders in 30 days"),
        ("in_n_90", 12, "12 payments received in 90 days"),
        ("out_gbp_30", 12345.6, "£12,346 sent in 30 days"),
        ("in_cash_share_90", 0.62, "62% of money received in 90 days was cash"),
        (
            "out_high_risk_share_7",
            0.5,
            "50% of money sent in 7 days was to high-risk countries",
        ),
        ("out_share_30", 0.48, "48% of money moved in 30 days went out again"),
        (
            "out_gbp_7_vs_history",
            np.log(4.0),
            "money sent this week was 4.0x the usual weekly amount",
        ),
        ("history_days", 1, "account first seen 1 day before"),
        ("rule_R06_7d", 1, "rule R06 (Large vs profile) alerted this week"),
        ("rules_30d", 2, "2 different rules alerted in 30 days"),
    ],
)
def test_reason_sentences(feature: str, value: float, text: str) -> None:
    assert describe(feature, value, CONFIG["reasons"], RULE_NAMES) == text


def test_unknown_feature_has_no_reason() -> None:
    with pytest.raises(KeyError):
        describe("mystery_7", 1, CONFIG["reasons"], RULE_NAMES)


def test_top_reasons_are_the_largest_positive_pushes() -> None:
    names = ["in_n_7", "out_n_7", "in_gbp_7", "out_gbp_7"]
    reasons = top_reasons(
        np.array([0.5, -2.0, 1.5, 0.0]),
        np.array([3, 1, 900, 0]),
        names,
        CONFIG["reasons"],
        RULE_NAMES,
    )
    assert [r.feature for r in reasons] == ["in_gbp_7", "in_n_7"]
    assert reasons[0].text == "£900 received in 7 days"


# --- Metrics (hand-made truth, no database) --------------------------------------------


def make_truth() -> Truth:
    period = Period("holdout", date(2023, 6, 1), date(2023, 6, 14))
    suspicious = pd.DataFrame(
        [
            # account, transaction time, typology
            ("A", ts("2023-06-02 10:00"), "fan_out"),
            ("B", ts("2023-05-05 12:00"), "structuring"),  # before the period
            ("C", ts("2023-06-10 09:00"), "fan_out"),
        ],
        columns=["account_id", "transaction_ts", "typology"],
    )
    suspicious["transaction_date"] = suspicious["transaction_ts"].dt.date
    return Truth(period, 30, suspicious, active_accounts=100)


def alerts(*rows: tuple[str, datetime]) -> pd.DataFrame:
    return pd.DataFrame(
        [(a, ts.date(), ts) for a, ts in rows],
        columns=["account_id", "alert_date", "trigger_ts"],
    )


def test_precision_lift_and_account_recall() -> None:
    truth = make_truth()
    score = score_alerts(
        alerts(
            ("A", ts("2023-06-07 23:00")),  # true: A's transaction 5 days before
            ("B", ts("2023-06-04 12:00")),  # true: 30 days to the minute after B's
            ("C", ts("2023-06-07 23:00")),  # false: C's transaction comes later
            ("D", ts("2023-06-07 23:00")),  # false: never suspicious
            ("A", ts("2023-06-20 00:00")),  # outside the period: not counted
        ),
        truth,
    )
    assert (score.alerts, score.true_alerts) == (4, 2)
    assert score.precision == 0.5
    # Suspicious accounts in the period: A and C (B's transaction is in May).
    assert (score.suspicious_accounts, score.caught_accounts) == (2, 2)
    assert score.base_rate == 0.02
    assert score.lift == pytest.approx(25)
    assert score.alerts_per_week == 2


def test_precision_look_back_stops_at_detection_days() -> None:
    truth = make_truth()
    late = score_alerts(alerts(("B", ts("2023-06-04 12:01"))), truth)
    assert late.true_alerts == 0


def test_recall_by_typology() -> None:
    truth = make_truth()
    found = recall_by_typology(alerts(("A", ts("2023-06-07 23:00"))), truth)
    assert found == {"fan_out": (1, 2)}


def test_ml_alerts_are_raised_at_the_very_end_of_the_week() -> None:
    keys = pd.DataFrame(
        {
            "account_id": ["A"],
            "week_end": np.array(["2023-06-07"], dtype="datetime64[D]"),
        }
    )
    alert = week_alerts(keys).iloc[0]
    assert alert["alert_date"] == date(2023, 6, 7)
    assert alert["trigger_ts"] == ts("2023-06-07 23:59:59.999999")


def test_label_vector_marks_only_positive_account_weeks() -> None:
    weeks = np.array(["2023-06-07", "2023-06-07", "2023-06-14"], dtype="datetime64[D]")
    keys = pd.DataFrame({"account_id": ["A", "B", "A"], "week_end": weeks})
    positives = pd.DataFrame(
        {
            "account_id": ["A"],
            "week_end": np.array(["2023-06-14"], dtype="datetime64[D]"),
        }
    )
    assert label_vector(keys, positives).tolist() == [0, 0, 1]


def test_weeks_period_runs_thursday_to_wednesday() -> None:
    period = weeks_period("x", date(2023, 6, 7), date(2023, 6, 14))
    assert (period.start, period.end, period.weeks) == (
        date(2023, 6, 1),
        date(2023, 6, 14),
        2,
    )


# --- Rule hits are point-in-time (database) ------------------------------------------


@pytest.fixture
def feature_db() -> Iterator[tuple[psycopg.Connection, str]]:
    """A throwaway schema with a tiny feature table, alerts and dim_account."""
    try:
        conn = get_connection()
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"database not reachable: {e}")
    conn.autocommit = True
    schema = f"test_ml_{uuid.uuid4().hex[:8]}"
    s = sql.Identifier(schema)
    try:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(s))
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.feat_account_week (account_id text, week_end date,"
                " in_n_7 real, artefact_is_customer boolean)"
            ).format(s)
        )
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.alerts (alert_id text, rule_id text, account_id text,"
                " alert_date date)"
            ).format(s)
        )
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.dim_account (account_id text, in_dev_sample boolean)"
            ).format(s)
        )
        yield conn, schema
    finally:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(s))
        conn.close()


def test_rule_hits_count_only_alerts_up_to_the_end_of_the_week(feature_db) -> None:
    conn, schema = feature_db
    s = sql.Identifier(schema)
    week = date(2023, 6, 14)
    conn.execute(
        sql.SQL("INSERT INTO {}.feat_account_week VALUES ('A', %s, 2, true)").format(s),
        (week,),
    )
    for i, (rule, day) in enumerate(
        [
            ("R06", week),  # last day of the week: counted
            ("R06", week - timedelta(days=6)),  # first day of the week: counted
            ("R06", week - timedelta(days=7)),  # previous week: not a 7-day hit
            ("R02", week - timedelta(days=29)),  # 30-day window: distinct rules only
            ("R03", week - timedelta(days=30)),  # outside 30 days
            ("R04", week + timedelta(days=1)),  # AFTER the week: never counted
        ]
    ):
        conn.execute(
            sql.SQL("INSERT INTO {}.alerts VALUES (%s, %s, 'A', %s)").format(s),
            (f"a{i}", rule, day),
        )
    assert list(table_columns(conn, schema)) == [
        "account_id",
        "week_end",
        "in_n_7",
        "artefact_is_customer",
    ]
    features = load_features(conn, schema, schema, week, week, ["R02", "R04", "R06"])
    row = dict(zip(features.names, features.values[0].tolist(), strict=True))
    assert row == {
        "in_n_7": 2,
        "artefact_is_customer": 1,
        "rule_R02_7d": 0,
        "rule_R04_7d": 0,
        "rule_R06_7d": 2,
        "rules_30d": 2,
    }


def test_every_feature_column_has_a_reason_template() -> None:
    try:
        conn = get_connection()
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"database not reachable: {e}")
    with conn:
        try:
            table = table_columns(conn, "features")
        except RuntimeError:
            pytest.skip("features.feat_account_week not built")
    groups = feature_groups(
        table, ["R01", "R02", "R03", "R04", "R05", "R06", "R07", "R09"]
    )
    for name in columns_for(["behaviour", "rule_hits", "artefact"], groups):
        assert describe(name, 2.0, CONFIG["reasons"], RULE_NAMES)


def test_smallest_matching_volume_finds_the_fewest_weekly_alerts() -> None:
    weeks = np.array(["2023-06-07"] * 4, dtype="datetime64[D]")
    keys = pd.DataFrame({"account_id": ["A", "B", "C", "D"], "week_end": weeks})
    features = Features(keys, np.zeros((4, 1), dtype=np.float32), ["x"])
    period = Period("holdout", date(2023, 6, 1), date(2023, 6, 7))
    suspicious = pd.DataFrame(
        {
            "account_id": ["A", "C"],
            "transaction_ts": [ts("2023-06-02 10:00"), ts("2023-06-03 10:00")],
            "typology": ["fan_out", "fan_out"],
        }
    )
    suspicious["transaction_date"] = suspicious["transaction_ts"].dt.date
    split = Split(
        period,
        features,
        np.array([1, 0, 1, 0]),
        Truth(period, 30, suspicious, 4),
        pd.DataFrame(),
    )
    # Ranking: A first, B second, C third: catching both needs 3 alerts a week.
    score = np.array([0.9, 0.8, 0.7, 0.1], dtype=np.float32)
    assert smallest_matching_volume(split, score, target_accounts=1, k_max=4) == 1
    assert smallest_matching_volume(split, score, target_accounts=2, k_max=4) == 3
    assert smallest_matching_volume(split, score, target_accounts=2, k_max=2) is None
