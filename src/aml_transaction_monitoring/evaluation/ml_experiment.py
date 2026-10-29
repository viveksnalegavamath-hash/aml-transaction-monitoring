"""Train, validate and test the ML layer; compare it with the rules at the same alert volume.

The unit is an active account-week, scored at the end of the week; the label
(joined only here) is "the account sent or received a suspicious transaction
that week". Settings: config/ml.yaml. Two stages, so the hold-out is opened
only once, at the end:

validate  Train on the weeks scored in January-April 2023, validate on May
          2023. For each feature set (ML only = behaviour features; rules + ML
          = behaviour + rule hits): a logistic regression baseline and an
          XGBoost grid with class weights, early stopping on May. Every run is
          tracked in MLflow (mlruns/, git-ignored). The model kept per feature
          set and family is the one with the highest validation PR-AUC. A
          diagnostic model adds the SAML-D artefact columns, to measure their
          effect. Models go to data/ml/ (git-ignored), the table to
          docs/results/ml_validation.md.
holdout   Scores the hold-out weeks ONCE with the saved models: rules only,
          ML only and rules + ML at the rules' weekly alert volume (precision,
          lift, account recall, PR-AUC; never accuracy), plus the top three
          SHAP reasons in plain English for every ML alert, written to
          marts.ml_alerts. Report: docs/results/ml_evaluation.md. Refuses to
          run again once that report exists (--again overrides, on record).

With --sample everything runs on the dev sample (sampled accounts only), the
reports are printed, not saved, and the hold-out is never opened: the sample
holdout stage is a dry run on the validation weeks.

Usage:
    uv run python -m aml_transaction_monitoring.evaluation.ml_experiment validate
    uv run python -m aml_transaction_monitoring.evaluation.ml_experiment holdout
"""

import argparse
import csv
import hashlib
import json
import logging
import pickle
import resource
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import psycopg
import yaml
from psycopg import sql
from psycopg.types.json import Jsonb

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.evaluation.ml_metrics import (
    AlertScore,
    Period,
    Truth,
    fetch_rule_alerts,
    fetch_truth,
    fetch_week_labels,
    label_vector,
    pr_auc,
    recall_by_typology,
    score_alerts,
    week_alerts,
    weeks_period,
)
from aml_transaction_monitoring.evaluation.rules_report import (
    ANY_RULE,
    md_table,
    pct,
)
from aml_transaction_monitoring.evaluation.rules_report import (
    load_config as load_evaluation_config,
)
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT
from aml_transaction_monitoring.ml.features import (
    Features,
    columns_for,
    feature_groups,
    load_features,
    rule_hit_column,
    table_columns,
)
from aml_transaction_monitoring.ml.model import (
    contributions,
    make_logistic,
    make_xgboost,
    positive_weight,
    predict_score,
    rank_in_week,
    top_k_per_week,
    top_reasons,
)
from aml_transaction_monitoring.rules.engine import load_rules

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "ml.yaml"
MODEL_DIR = PROJECT_ROOT / "data" / "ml"
MLRUNS_DIR = PROJECT_ROOT / "mlruns"
EXPERIMENT = "aml-account-week"
CANDIDATE_SETS = ("ml_only", "rules_ml")
DIAGNOSTIC_SET = "artefact_diagnostic"
FAMILIES = ("logistic", "xgboost")
SET_LABELS = {
    "ml_only": "ML only",
    "rules_ml": "Rules + ML",
    DIAGNOSTIC_SET: "Rules + ML + artefact (diagnostic)",
}
FAMILY_LABELS = {"logistic": "logistic regression", "xgboost": "XGBoost"}

CREATE_ML_ALERTS_SQL = """
CREATE TABLE IF NOT EXISTS {schema}.ml_alerts (
    alert_id      text        PRIMARY KEY,
    model         text        NOT NULL,
    model_run_id  text        NOT NULL,
    account_id    text        NOT NULL,
    week_end      date        NOT NULL,
    score         real        NOT NULL,
    rank_in_week  integer     NOT NULL,
    reasons       text        NOT NULL,
    shap          jsonb       NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ml_alerts_model_week_idx ON {schema}.ml_alerts (model, week_end);
"""


@dataclass(frozen=True)
class Dataset:
    """Full data or dev sample: schemas and what may be written."""

    sample: bool

    @property
    def suffix(self) -> str:
        return "_sample" if self.sample else ""

    @property
    def features(self) -> str:
        return "features" + self.suffix

    @property
    def marts(self) -> str:
        return "marts" + self.suffix

    @property
    def labels(self) -> str:
        return "evaluation" + self.suffix

    @property
    def name(self) -> str:
        return "sample" if self.sample else "full"


@dataclass
class Split:
    """Feature rows of one part of the split, their labels and the label truth."""

    period: Period
    features: Features
    y: np.ndarray
    truth: Truth
    rule_alerts: pd.DataFrame

    @property
    def weekly_volume(self) -> int:
        """The rules' alerts per week in this period: the capacity ML is held to."""
        return round(len(self.rule_alerts) / self.period.weeks)


@dataclass
class Fitted:
    """One trained model and its validation results."""

    feature_set: str
    family: str
    params: dict[str, Any]
    columns: list[str]
    model: Any
    run_id: str
    pr_auc: float
    score: AlertScore
    best_iteration: int | None = None

    @property
    def key(self) -> str:
        return f"{self.feature_set}-{self.family}"


# --- Setup ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_mlflow() -> None:
    """Track runs locally in mlruns/ (git-ignored): SQLite store plus artifact folder."""
    MLRUNS_DIR.mkdir(exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{MLRUNS_DIR / 'mlflow.db'}")
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(
            EXPERIMENT, artifact_location=(MLRUNS_DIR / "artifacts").as_uri()
        )
    mlflow.set_experiment(EXPERIMENT)


def peak_memory_mb() -> float:
    """Peak resident memory of this process (macOS reports bytes, Linux KiB)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def week_end_range(part: dict[str, date]) -> tuple[date, date]:
    """First and last Wednesday (week_end) inside a date range."""
    first = part["start"] + timedelta(days=(2 - part["start"].weekday()) % 7)
    last = part["end"] - timedelta(days=(part["end"].weekday() - 2) % 7)
    return first, last


def holdout_period(evaluation: dict[str, Any]) -> Period:
    """The hold-out as whole weeks; it must start on a Thursday and end on a Wednesday."""
    h = evaluation["periods"]["holdout"]
    if h["start"].weekday() != 3 or h["end"].weekday() != 2:
        raise ValueError(
            "The hold-out must run Thursday to Wednesday (whole scoring weeks)"
        )
    return Period("holdout", h["start"], h["end"])


def load_split(
    conn: psycopg.Connection,
    ds: Dataset,
    name: str,
    first_week: date,
    last_week: date,
    rule_ids: list[str],
    detection_days: int,
) -> Split:
    """Features, account-week labels, label truth and rule alerts for weeks ending in a range."""
    period = weeks_period(name, first_week, last_week)
    features = load_features(
        conn, ds.features, ds.marts, first_week, last_week, rule_ids, ds.sample
    )
    y = label_vector(
        features.keys, fetch_week_labels(conn, ds.labels, first_week, last_week)
    )
    truth = fetch_truth(conn, ds.marts, ds.labels, period, detection_days, ds.sample)
    rule_alerts = fetch_rule_alerts(conn, ds.marts, period, ds.sample)
    logger.info(
        "%s: %s account-weeks, %s positive (%.3f%%), %s suspicious accounts, "
        "rules raise %s alerts/week",
        name,
        f"{len(y):,}",
        f"{int(y.sum()):,}",
        100 * y.mean(),
        f"{len(truth.suspicious_accounts):,}",
        f"{split_volume(rule_alerts, period):,}",
    )
    check_rule_hits(features, rule_alerts, rule_ids)
    return Split(period, features, y, truth, rule_alerts)


def split_volume(rule_alerts: pd.DataFrame, period: Period) -> int:
    return round(len(rule_alerts) / period.weeks)


def check_rule_hits(
    features: Features, rule_alerts: pd.DataFrame, rule_ids: list[str]
) -> None:
    """Every rule alert in the weeks must appear as a 7-day rule hit (and nothing else)."""
    hits = int(features.matrix([rule_hit_column(r) for r in rule_ids]).sum())
    expected = int(rule_alerts["rule_id"].isin(rule_ids).sum())
    if hits != expected:
        raise RuntimeError(
            f"Rule-hit features count {hits} alerts, the alerts table {expected}"
        )


# --- Self-check of the metric code against the rules report -----------------------


def self_check(
    conn: psycopg.Connection,
    ds: Dataset,
    period: Period,
    detection_days: int,
    output_dir: Path,
) -> None:
    """The ML metrics, applied to the rules' own alerts, must reproduce the rules
    report's precision and account recall for every rule and "any rule"."""
    path = output_dir / "rules_evaluation.csv"
    with path.open(encoding="utf-8") as f:
        published = {
            r["rule_id"]: r for r in csv.DictReader(f) if r["period"] == period.name
        }
    truth = fetch_truth(conn, ds.marts, ds.labels, period, detection_days, ds.sample)
    alerts = fetch_rule_alerts(conn, ds.marts, period, ds.sample)
    checks = {rule_id: alerts[alerts["rule_id"] == rule_id] for rule_id in published}
    checks[ANY_RULE] = alerts
    for rule_id, rule_alerts in checks.items():
        score = score_alerts(rule_alerts, truth)
        expected = published[rule_id]
        if score.caught_accounts != int(
            expected["caught_accounts"]
        ) or score.suspicious_accounts != int(expected["suspicious_accounts"]):
            raise RuntimeError(
                f"Self-check failed: account recall of {rule_id} ({period.name})"
            )
        if (
            rule_id != ANY_RULE
            and expected["precision"]
            and abs(score.precision - float(expected["precision"])) > 1e-9
        ):
            raise RuntimeError(
                f"Self-check failed: precision of {rule_id} ({period.name})"
            )
    logger.info(
        "Self-check passed: the ML metric code reproduces the rules report (%s, %d rules + any rule)",
        period.name,
        len(published) - 1,
    )


# --- Training -----------------------------------------------------------------------


def training_rows(
    y: np.ndarray, share: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Rows to train on and their weights: every positive, a seeded share of
    negatives weighted 1 / share (all rows, weight 1, when share is 1)."""
    if share >= 1:
        return np.arange(len(y)), np.ones(len(y), dtype=np.float32)
    rng = np.random.default_rng(seed)
    keep = (y == 1) | (rng.random(len(y)) < share)
    weights = np.where(y == 1, 1.0, 1.0 / share).astype(np.float32)
    rows = np.flatnonzero(keep)
    return rows, weights[rows]


def alerts_at_volume(split: Split, score: np.ndarray, k: int) -> pd.DataFrame:
    mask = top_k_per_week(split.features.keys["week_end"].to_numpy(), score, k)
    return week_alerts(split.features.keys[mask])


def fit_one(
    feature_set: str,
    family: str,
    params: dict[str, Any],
    columns: list[str],
    train: Split,
    validation: Split,
    config: dict[str, Any],
    ds: Dataset,
) -> Fitted:
    """Train one model, score May, log everything to MLflow."""
    seed = config["seed"]
    rows, weights = training_rows(train.y, config["train_negative_share"], seed)
    x_train = train.features.matrix(columns)[rows]
    y_train = train.y[rows]
    x_val = validation.features.matrix(columns)
    started = time.time()
    run_params = dict(params)
    if family == "logistic":
        model = make_logistic(config["logistic"], seed)
        model.fit(x_train, y_train, model__sample_weight=weights)
        best_iteration = None
    else:
        spw = positive_weight(train.y, params["scale_pos_weight"])
        run_params["scale_pos_weight_value"] = round(spw, 2)
        model = make_xgboost(config["xgboost"]["fixed"], params["max_depth"], spw, seed)
        model.fit(
            x_train,
            y_train,
            sample_weight=weights,
            eval_set=[(x_val, validation.y)],
            verbose=False,
        )
        best_iteration = int(model.best_iteration)
    score = predict_score(model, x_val)
    k = validation.weekly_volume
    alert_score = score_alerts(alerts_at_volume(validation, score, k), validation.truth)
    auc = pr_auc(validation.y, score)
    with mlflow.start_run(run_name=f"{feature_set}-{family}") as run:
        mlflow.set_tags(
            {"stage": "validate", "dataset": ds.name, "feature_set": feature_set}
        )
        mlflow.log_params(
            {
                "family": family,
                **run_params,
                "n_features": len(columns),
                "seed": seed,
                "train_weeks": f"{config['split']['train']['start']}..{config['split']['train']['end']}",
                "validation_weeks": (
                    f"{config['split']['validation']['start']}..{config['split']['validation']['end']}"
                ),
                "train_negative_share": config["train_negative_share"],
            }
        )
        metrics = {
            "val_pr_auc": auc,
            "val_account_recall": alert_score.account_recall or 0.0,
            "val_precision": alert_score.precision or 0.0,
            "val_lift": alert_score.lift or 0.0,
            "val_weekly_alerts": k,
            "train_rows": len(rows),
            "train_positives": int(y_train.sum()),
            "fit_seconds": time.time() - started,
        }
        if best_iteration is not None:
            metrics["best_iteration"] = best_iteration
        mlflow.log_metrics(metrics)
        mlflow.log_text("\n".join(columns), "features.txt")
        run_id = run.info.run_id
    logger.info(
        "%-20s %-8s %-40s PR-AUC %.4f | at %s/week: recall %s, precision %s, lift %.1fx%s",
        feature_set,
        family,
        json.dumps(params),
        auc,
        f"{k:,}",
        pct(alert_score.account_recall),
        pct(alert_score.precision),
        alert_score.lift or 0.0,
        f" | best iteration {best_iteration}" if best_iteration is not None else "",
    )
    return Fitted(
        feature_set,
        family,
        params,
        columns,
        model,
        run_id,
        auc,
        alert_score,
        best_iteration,
    )


def grid(config: dict[str, Any]) -> list[dict[str, Any]]:
    g = config["xgboost"]["grid"]
    return [
        {"max_depth": d, "scale_pos_weight": w}
        for d in g["max_depth"]
        for w in g["scale_pos_weight"]
    ]


def model_path(ds: Dataset, fitted_key: str) -> Path:
    return MODEL_DIR / f"{ds.name}_{fitted_key}.pkl"


def save_models(ds: Dataset, kept: dict[str, Fitted], selected: dict[str, str]) -> None:
    """Pickle every kept model and write the selection (read by the holdout stage)."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"dataset": ds.name, "selected": selected, "models": {}}
    for key, f in kept.items():
        with model_path(ds, key).open("wb") as out:
            pickle.dump(f.model, out)
        manifest["models"][key] = {
            "feature_set": f.feature_set,
            "family": f.family,
            "params": f.params,
            "columns": f.columns,
            "run_id": f.run_id,
            "val_pr_auc": f.pr_auc,
            "best_iteration": f.best_iteration,
        }
    (MODEL_DIR / f"{ds.name}_selection.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def validate(ds: Dataset, config: dict[str, Any], evaluation: dict[str, Any]) -> None:
    """Fit every candidate on Jan-Apr, choose on May; save the kept models and the table."""
    rule_ids = [r.id for r in load_rules()]
    detection_days = evaluation["detection_days"]
    output_dir = PROJECT_ROOT / evaluation["output_dir"]
    setup_mlflow()
    with get_connection() as conn:
        if not ds.sample:
            tuning = evaluation["periods"]["tuning"]
            self_check(
                conn,
                ds,
                Period("tuning", tuning["start"], tuning["end"]),
                detection_days,
                output_dir,
            )
        groups = feature_groups(table_columns(conn, ds.features), rule_ids)
        train = load_split(
            conn,
            ds,
            "train",
            *week_end_range(config["split"]["train"]),
            rule_ids,
            detection_days,
        )
        validation = load_split(
            conn,
            ds,
            "validation",
            *week_end_range(config["split"]["validation"]),
            rule_ids,
            detection_days,
        )
    logger.info("Data loaded; peak memory so far %.0f MB", peak_memory_mb())

    fitted: list[Fitted] = []
    for feature_set in CANDIDATE_SETS:
        columns = columns_for(config["feature_sets"][feature_set], groups)
        fitted.append(
            fit_one(
                feature_set,
                "logistic",
                {"C": config["logistic"]["C"]},
                columns,
                train,
                validation,
                config,
                ds,
            )
        )
        for params in grid(config):
            fitted.append(
                fit_one(
                    feature_set,
                    "xgboost",
                    params,
                    columns,
                    train,
                    validation,
                    config,
                    ds,
                )
            )
        logger.info("Peak memory so far %.0f MB", peak_memory_mb())

    # Keep the best of each family per feature set; select the best overall per set.
    kept: dict[str, Fitted] = {}
    for f in fitted:
        if f.key not in kept or f.pr_auc > kept[f.key].pr_auc:
            kept[f.key] = f
    metric = config["selection_metric"]
    if metric != "pr_auc":
        raise ValueError(f"Unsupported selection metric: {metric}")
    selected = {
        s: max((kept[f"{s}-{fam}"] for fam in FAMILIES), key=lambda f: f.pr_auc).key
        for s in CANDIDATE_SETS
    }

    # Diagnostic: the selected rules + ML model, refitted with the artefact columns.
    base = kept[selected["rules_ml"]]
    diagnostic = fit_one(
        DIAGNOSTIC_SET,
        base.family,
        base.params,
        columns_for(config["feature_sets"][DIAGNOSTIC_SET], groups),
        train,
        validation,
        config,
        ds,
    )
    kept[diagnostic.key] = diagnostic
    save_models(ds, kept, selected)

    rules_score = score_alerts(validation.rule_alerts, validation.truth)
    report = render_validation(
        fitted, kept, selected, diagnostic, rules_score, train, validation, ds
    )
    finish(report, ds, output_dir / "ml_validation.md")
    logger.info("Peak memory %.0f MB", peak_memory_mb())


def note(f: Fitted, kept: dict[str, Fitted], chosen: set[str]) -> str:
    """Selected, kept for the hold-out table (best of its family), or diagnostic."""
    if f.feature_set == DIAGNOSTIC_SET:
        return "diagnostic"
    if kept.get(f.key) is not f:
        return ""
    return "**selected**" if f.key in chosen else "kept (best of family)"


def render_validation(
    fitted: list[Fitted],
    kept: dict[str, Fitted],
    selected: dict[str, str],
    diagnostic: Fitted,
    rules_score: AlertScore,
    train: Split,
    validation: Split,
    ds: Dataset,
) -> str:
    """Markdown table of every candidate on the validation weeks."""
    k = validation.weekly_volume
    chosen = set(selected.values())
    body = [
        [
            SET_LABELS[f.feature_set],
            FAMILY_LABELS[f.family],
            ", ".join(f"{p}={v}" for p, v in f.params.items() if p != "C") or "–",
            "–" if f.best_iteration is None else str(f.best_iteration),
            f"{f.pr_auc:.4f}",
            pct(f.score.account_recall),
            pct(f.score.precision),
            f"{f.score.lift:.1f}×" if f.score.lift else "–",
            note(f, kept, chosen),
        ]
        for f in [*fitted, diagnostic]
    ]
    header = [
        "Feature set",
        "Model",
        "Settings",
        "Trees",
        "PR-AUC",
        "Account recall",
        "Precision",
        "Lift",
        "",
    ]
    t, v = train.period, validation.period
    out = [
        "# ML validation (model selection)",
        "",
        (
            f"Dataset: `{ds.features}` · generated "
            f"{datetime.now().astimezone():%Y-%m-%d %H:%M %Z} · labels joined only in evaluation code."
        ),
        "",
        (
            f"- **Train:** weeks scored {t.start + timedelta(days=6)} to {t.end} "
            f"({len(train.y):,} account-weeks, {int(train.y.sum()):,} positive)."
        ),
        (
            f"- **Validation (May 2023):** weeks scored {v.start + timedelta(days=6)} to {v.end} "
            f"({len(validation.y):,} account-weeks, {int(validation.y.sum()):,} positive; "
            f"{len(validation.truth.suspicious_accounts):,} suspicious accounts, "
            f"base rate {pct(validation.truth.base_rate, 2)})."
        ),
        "- The hold-out (Jun – Aug 2023) is not read here.",
        (
            f"- Each model's alerts: the top {k:,} account-weeks of each week, the rules' "
            "own weekly volume in these weeks. Rules alone at that volume: account recall "
            f"{pct(rules_score.account_recall)}, precision {pct(rules_score.precision)}, "
            f"lift {rules_score.lift:.1f}×."
        ),
        (
            "- **PR-AUC** is at account-week level (label: a suspicious transaction that week); "
            "selection = highest PR-AUC per feature set. Precision, lift and account recall "
            "use the rules report's definitions. Never accuracy."
        ),
        "",
        md_table(header, body),
        "",
        (
            f'Artefact diagnostic: adding the SAML-D "receiver is a customer" columns to the '
            f"selected rules + ML model changes validation PR-AUC from "
            f"{kept[selected['rules_ml']].pr_auc:.4f} to {diagnostic.pr_auc:.4f}. "
            "It is never a candidate (dataset artefact, and the customer list covers the "
            "future)."
        ),
    ]
    return "\n".join(out) + "\n"


def finish(report: str, ds: Dataset, path: Path) -> None:
    """Write a full-data report; print a sample one (never saved)."""
    if ds.sample:
        logger.info(
            "DEV SAMPLE (sampled accounts only) - not for reporting:\n%s", report
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    logger.info("Wrote %s", path)


# --- Hold-out -------------------------------------------------------------------------


@dataclass
class Row:
    """One line of the hold-out comparison."""

    name: str
    score: AlertScore
    pr_auc: float
    alerts: pd.DataFrame
    note: str = ""


def load_kept(ds: Dataset) -> tuple[dict[str, Any], dict[str, Any]]:
    """The selection manifest and the unpickled models (our own files, data/ml/)."""
    manifest = json.loads(
        (MODEL_DIR / f"{ds.name}_selection.json").read_text(encoding="utf-8")
    )
    models = {}
    for key in manifest["models"]:
        with model_path(ds, key).open("rb") as f:
            models[key] = pickle.load(f)
    return manifest, models


def alert_id(model: str, account_id: str, week_end: date) -> str:
    return hashlib.md5(f"{model}|{account_id}|{week_end}".encode()).hexdigest()


def explain_and_write(
    conn: psycopg.Connection,
    ds: Dataset,
    feature_set: str,
    info: dict[str, Any],
    model: Any,
    split: Split,
    score: np.ndarray,
    mask: np.ndarray,
    config: dict[str, Any],
    rule_names: dict[str, str],
) -> pd.DataFrame:
    """Top three SHAP reasons for each alert of one model; replace its rows in ml_alerts."""
    columns = info["columns"]
    x = split.features.matrix(columns)[mask]
    shap_values = contributions(model, x)
    keys = split.features.keys[mask].reset_index(drop=True)
    alert_scores = score[mask]
    ranks = rank_in_week(split.features.keys["week_end"].to_numpy(), score)[mask]
    rows = []
    for i in range(len(keys)):
        reasons = top_reasons(
            shap_values[i], x[i], columns, config["reasons"], rule_names
        )
        week_end = pd.Timestamp(keys.at[i, "week_end"]).date()
        rows.append(
            (
                alert_id(feature_set, keys.at[i, "account_id"], week_end),
                feature_set,
                info["run_id"],
                keys.at[i, "account_id"],
                week_end,
                float(alert_scores[i]),
                int(ranks[i]),
                "; ".join(r.text for r in reasons),
                [
                    {"feature": r.feature, "value": r.value, "shap": round(r.shap, 4)}
                    for r in reasons
                ],
            )
        )
    s = sql.Identifier(ds.marts)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL(CREATE_ML_ALERTS_SQL).format(schema=s))
        cur.execute(
            sql.SQL("DELETE FROM {}.ml_alerts WHERE model = %s").format(s),
            (feature_set,),
        )
        with cur.copy(
            sql.SQL(
                "COPY {}.ml_alerts (alert_id, model, model_run_id, account_id, week_end,"
                " score, rank_in_week, reasons, shap) FROM STDIN"
            ).format(s)
        ) as copy:
            for r in rows:
                copy.write_row((*r[:8], Jsonb(r[8])))
    logger.info(
        "Wrote %s %s alerts with reasons to %s.ml_alerts",
        f"{len(rows):,}",
        feature_set,
        ds.marts,
    )
    return pd.DataFrame(
        [(r[3], r[4], r[5], r[7]) for r in rows],
        columns=["account_id", "week_end", "score", "reasons"],
    )


def rules_week_score(split: Split, rule_ids: list[str]) -> np.ndarray:
    """Rules-only ranking for PR-AUC: distinct rules alerting in the week."""
    hits = split.features.matrix([rule_hit_column(r) for r in rule_ids])
    return (hits > 0).sum(axis=1).astype(np.float32)


def holdout(
    ds: Dataset, config: dict[str, Any], evaluation: dict[str, Any], again: bool
) -> None:
    """Score the hold-out once and write the comparison."""
    output_dir = PROJECT_ROOT / evaluation["output_dir"]
    report_path = output_dir / "ml_evaluation.md"
    if not ds.sample and report_path.exists() and not again:
        raise RuntimeError(
            f"{report_path} exists: the hold-out has already been scored. "
            "Scoring it again would make it a second validation set; use --again "
            "only to reproduce the same models, and say so."
        )
    if ds.sample:
        # Never open the hold-out on the sample (sampled accounts are part of it):
        # rehearse the code on the validation weeks instead.
        first, last = week_end_range(config["split"]["validation"])
        name = "validation (dry run)"
    else:
        period = holdout_period(evaluation)
        first, last = period.start + timedelta(days=6), period.end
        name = "holdout"
    rules = load_rules()
    rule_ids = [r.id for r in rules]
    rule_names = {r.id: r.name for r in rules}
    detection_days = evaluation["detection_days"]
    manifest, models = load_kept(ds)
    setup_mlflow()
    with get_connection() as conn:
        if not ds.sample:
            self_check(conn, ds, holdout_period(evaluation), detection_days, output_dir)
        split = load_split(conn, ds, name, first, last, rule_ids, detection_days)
        k = split.weekly_volume
        rules_any_week = rules_week_score(split, rule_ids) > 0
        k_distinct = round(rules_any_week.sum() / split.period.weeks)

        rows = [
            Row(
                "Rules only (tuned)",
                score_alerts(split.rule_alerts, split.truth),
                pr_auc(split.y, rules_week_score(split, rule_ids)),
                split.rule_alerts,
                f"{k_distinct:,} distinct account-weeks/week",
            )
        ]
        scores: dict[str, np.ndarray] = {}
        for key, info in manifest["models"].items():
            scores[key] = predict_score(
                models[key], split.features.matrix(info["columns"])
            )
        selected = manifest["selected"]
        order = [selected["ml_only"], selected["rules_ml"]] + [
            key for key in manifest["models"] if key not in selected.values()
        ]
        for key in order:
            info = manifest["models"][key]
            alerts = alerts_at_volume(split, scores[key], k)
            label = (
                f"{SET_LABELS[info['feature_set']]}, {FAMILY_LABELS[info['family']]}"
            )
            note = (
                "selected"
                if key in selected.values()
                else (
                    "diagnostic"
                    if info["feature_set"] == DIAGNOSTIC_SET
                    else "baseline"
                )
            )
            rows.append(
                Row(
                    label,
                    score_alerts(alerts, split.truth),
                    pr_auc(split.y, scores[key]),
                    alerts,
                    note,
                )
            )
        sensitivity = []
        for feature_set in CANDIDATE_SETS:
            key = selected[feature_set]
            alerts = alerts_at_volume(split, scores[key], k_distinct)
            sensitivity.append(
                Row(
                    f"{SET_LABELS[feature_set]} at {k_distinct:,}/week",
                    score_alerts(alerts, split.truth),
                    pr_auc(split.y, scores[key]),
                    alerts,
                )
            )

        examples = {}
        for feature_set in CANDIDATE_SETS:
            key = selected[feature_set]
            mask = top_k_per_week(
                split.features.keys["week_end"].to_numpy(), scores[key], k
            )
            examples[feature_set] = explain_and_write(
                conn,
                ds,
                feature_set,
                manifest["models"][key],
                models[key],
                split,
                scores[key],
                mask,
                config,
                rule_names,
            )

    with mlflow.start_run(run_name=f"holdout-{ds.name}"):
        mlflow.set_tags(
            {"stage": "holdout" if not ds.sample else "dry_run", "dataset": ds.name}
        )
        mlflow.log_params(
            {
                "weeks": f"{first}..{last}",
                "weekly_alerts": k,
                **{
                    f"run_{s}": manifest["models"][selected[s]]["run_id"]
                    for s in CANDIDATE_SETS
                },
            }
        )
        for i, row in enumerate(rows):
            prefix = f"row{i}"
            mlflow.log_metrics(
                {
                    f"{prefix}_pr_auc": row.pr_auc,
                    f"{prefix}_account_recall": row.score.account_recall or 0.0,
                    f"{prefix}_precision": row.score.precision or 0.0,
                    f"{prefix}_lift": row.score.lift or 0.0,
                }
            )
        mlflow.log_text(
            "\n".join(f"row{i}: {r.name}" for i, r in enumerate(rows)), "rows.txt"
        )

    report = render_holdout(
        rows, sensitivity, split, k, k_distinct, manifest, examples, ds, name
    )
    finish(report, ds, report_path)
    if not ds.sample:
        write_holdout_csv(rows + sensitivity, output_dir / "ml_evaluation.csv")
    logger.info("Peak memory %.0f MB", peak_memory_mb())


def render_holdout(
    rows: list[Row],
    sensitivity: list[Row],
    split: Split,
    k: int,
    k_distinct: int,
    manifest: dict[str, Any],
    examples: dict[str, pd.DataFrame],
    ds: Dataset,
    name: str,
) -> str:
    """The comparison as markdown: headline, sensitivity, overlap, typologies, examples."""
    truth = split.truth

    def line(r: Row) -> list[str]:
        s = r.score
        return [
            r.name,
            f"{s.alerts_per_week:,.0f}",
            pct(s.precision),
            "–" if s.lift is None else f"{s.lift:.1f}×",
            pct(s.account_recall),
            f"{s.caught_accounts:,} / {s.suspicious_accounts:,}",
            f"{r.pr_auc:.4f}",
            f"{s.true_alerts_per_week:,.0f}",
            r.note,
        ]

    header = [
        "Approach",
        "Alerts / week",
        "Precision",
        "Lift",
        "Account recall",
        "Accounts caught",
        "PR-AUC",
        "True alerts / week",
        "",
    ]
    rules_row = rows[0]
    hybrid = rows[2]
    rules_caught = set(in_period_accounts(rules_row.alerts, truth))
    hybrid_caught = set(in_period_accounts(hybrid.alerts, truth))
    suspicious = truth.suspicious_accounts
    overlap = [
        ["Caught by both", f"{len(suspicious & rules_caught & hybrid_caught):,}"],
        ["Rules only", f"{len(suspicious & (rules_caught - hybrid_caught)):,}"],
        ["Rules + ML only", f"{len(suspicious & (hybrid_caught - rules_caught)):,}"],
        ["Neither", f"{len(suspicious - rules_caught - hybrid_caught):,}"],
    ]
    by_typology = {r.name: recall_by_typology(r.alerts, truth) for r in rows[:3]}
    typologies = sorted(by_typology[rules_row.name])
    typology_body = [
        [t, f"{by_typology[rules_row.name][t][1]:,}"]
        + [
            pct(c / n if n else None)
            for c, n in (by_typology[r.name].get(t, (0, 0)) for r in rows[:3])
        ]
        for t in typologies
    ]
    example_lines = []
    for feature_set, frame in examples.items():
        top = frame.sort_values(["week_end", "score"], ascending=[False, False]).head(3)
        example_lines += [f"**{SET_LABELS[feature_set]}**", ""]
        example_lines += [
            f"- account {r.account_id}, week ending {r.week_end}, score {r.score:.3f}: {r.reasons}"
            for r in top.itertuples()
        ]
        example_lines.append("")
    selected = manifest["selected"]
    p = split.period
    out = [
        "# ML evaluation: rules vs ML vs rules + ML"
        + (" (DRY RUN)" if ds.sample else ""),
        "",
        (
            f"Dataset: `{ds.features}` · generated {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}"
            " · labels joined only in evaluation code · scored once, no changes after."
        ),
        "",
        f"- **Weeks:** {name}, {p.start} to {p.end} ({p.weeks:.0f} weeks).",
        (
            f"- **Base rate:** {pct(truth.base_rate, 2)} ({len(suspicious):,} suspicious of "
            f"{truth.active_accounts:,} active accounts)."
        ),
        (
            f"- **Same alert volume:** each model raises the top {k:,} account-weeks of every week, "
            "the tuned rules' average weekly alerts in these weeks. One rule alert = one alert, "
            f"so the rules review fewer distinct accounts: {k_distinct:,} account-weeks a week. "
            "The sensitivity table holds the models to that number."
        ),
        "- Models chosen on May 2023 before this run: "
        + ", ".join(
            f"{SET_LABELS[s]} = {FAMILY_LABELS[manifest['models'][selected[s]]['family']]} "
            f"(MLflow run `{manifest['models'][selected[s]]['run_id'][:8]}`)"
            for s in CANDIDATE_SETS
        )
        + ".",
        (
            "- Precision, lift and account recall as in the rules report (alerts scored by "
            "account and time; ML alerts are raised at the end of their week). PR-AUC at "
            "account-week level; for the rules, the score is the number of rules alerting "
            "that week. Never accuracy."
        ),
        "",
        "## Same weekly alert volume",
        "",
        md_table(header, [line(r) for r in rows]),
        "",
        f"## Sensitivity: models at the rules' distinct account-weeks ({k_distinct:,} a week)",
        "",
        md_table(header, [line(rules_row)] + [line(r) for r in sensitivity]),
        "",
        "## Suspicious accounts: rules vs rules + ML",
        "",
        md_table(["Suspicious accounts", "Count"], overlap),
        "",
        "## Account recall by typology",
        "",
        md_table(
            ["Typology", "Suspicious accounts", *[r.name for r in rows[:3]]],
            typology_body,
        ),
        "",
        "## Example alerts with their top three reasons (synthetic accounts)",
        "",
        *example_lines,
    ]
    return "\n".join(out).rstrip() + "\n"


def in_period_accounts(alerts: pd.DataFrame, truth: Truth) -> list[str]:
    a = alerts[
        (alerts["alert_date"] >= truth.period.start)
        & (alerts["alert_date"] <= truth.period.end)
    ]
    return list(a["account_id"])


def write_holdout_csv(rows: list[Row], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "approach",
                "alerts_per_week",
                "precision",
                "base_rate",
                "lift",
                "account_recall",
                "caught_accounts",
                "suspicious_accounts",
                "pr_auc",
                "true_alerts_per_week",
                "note",
            ]
        )
        for r in rows:
            s = r.score
            writer.writerow(
                [
                    r.name,
                    s.alerts_per_week,
                    s.precision,
                    s.base_rate,
                    s.lift,
                    s.account_recall,
                    s.caught_accounts,
                    s.suspicious_accounts,
                    r.pr_auc,
                    s.true_alerts_per_week,
                    r.note,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["validate", "holdout"])
    parser.add_argument(
        "--sample",
        action="store_true",
        help="dev sample, sampled accounts only; reports printed, hold-out never opened",
    )
    parser.add_argument(
        "--again",
        action="store_true",
        help="re-score the hold-out with the SAME saved models (reproduction only)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    ds = Dataset(sample=args.sample)
    config = load_config()
    evaluation = load_evaluation_config()
    if args.stage == "validate":
        validate(ds, config, evaluation)
    else:
        holdout(ds, config, evaluation, args.again)


if __name__ == "__main__":
    main()
