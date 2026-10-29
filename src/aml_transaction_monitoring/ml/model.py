"""Models, weekly alert selection and plain-English SHAP reasons. No labels here.

- Logistic regression baseline: sign(x) * ln(1 + |x|) (amounts and counts are
  heavy-tailed), standardised, classes weighted to balance.
- XGBoost with class weights (`scale_pos_weight`) and early stopping.
- Alerts: the top k scores of each week, the same capacity every week.
- Reasons: the three features that pushed an alert's score up the most (SHAP
  values; exact TreeSHAP for XGBoost, coefficient x standardised value for
  the linear model), each turned into a sentence from config/ml.yaml.
"""

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import shap
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from xgboost import XGBClassifier

RULE_HIT = re.compile(r"^rule_(R\d\d)_7d$")
VS_HISTORY = re.compile(r"^(in|out)_(gbp_7_vs_history|max_vs_history)$")
OUT_SHARE = re.compile(r"^out_share_(\d+)$")
WINDOWED = re.compile(r"^(in|out)_(.+)_(\d+)$")


def signed_log1p(x: np.ndarray) -> np.ndarray:
    """sign(x) * ln(1 + |x|): tames heavy tails, keeps 0 at 0 and the sign."""
    return np.sign(x) * np.log1p(np.abs(x))


def make_logistic(params: dict[str, Any], seed: int) -> Pipeline:
    """Logistic regression baseline with balanced class weights."""
    return Pipeline(
        [
            ("log", FunctionTransformer(signed_log1p)),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=params["C"],
                    max_iter=params["max_iter"],
                    class_weight="balanced",
                    random_state=seed,
                ),
            ),
        ]
    )


def positive_weight(y: np.ndarray, how: str) -> float:
    """scale_pos_weight: negatives / positives ("balanced") or its square root ("sqrt")."""
    positives = int(y.sum())
    if positives == 0:
        raise ValueError("No positive rows to weight")
    ratio = (len(y) - positives) / positives
    if how == "balanced":
        return ratio
    if how == "sqrt":
        return math.sqrt(ratio)
    raise ValueError(f"Unknown scale_pos_weight: {how!r}")


def make_xgboost(
    fixed: dict[str, Any], max_depth: int, scale_pos_weight: float, seed: int
) -> XGBClassifier:
    """XGBoost classifier; early stopping uses the eval_set given to fit()."""
    return XGBClassifier(
        **fixed,
        max_depth=max_depth,
        scale_pos_weight=scale_pos_weight,
        random_state=seed,
        n_jobs=-1,
    )


def predict_score(model: Pipeline | XGBClassifier, x: np.ndarray) -> np.ndarray:
    """Probability-like score of the positive class (higher = more suspicious)."""
    return model.predict_proba(x)[:, 1].astype(np.float32)


def top_k_per_week(week_end: np.ndarray, score: np.ndarray, k: int) -> np.ndarray:
    """Boolean mask of the k highest scores in each week (ties: earlier row first).

    The same capacity every week, as an alert team of fixed size would have.
    """
    frame = pd.DataFrame({"week": week_end, "score": score})
    rank = frame.groupby("week")["score"].rank(method="first", ascending=False)
    return (rank <= k).to_numpy()


def rank_in_week(week_end: np.ndarray, score: np.ndarray) -> np.ndarray:
    """1 = highest score of its week."""
    frame = pd.DataFrame({"week": week_end, "score": score})
    rank = frame.groupby("week")["score"].rank(method="first", ascending=False)
    return rank.to_numpy().astype(np.int64)


def contributions(model: Pipeline | XGBClassifier, x: np.ndarray) -> np.ndarray:
    """Per-feature SHAP values (log-odds), one row per row of x.

    XGBoost: exact TreeSHAP (shap.TreeExplainer). Logistic regression: the
    linear SHAP value, coefficient x standardised input (the standardised
    training mean is 0, so this is each feature's push from an average row).
    """
    if isinstance(model, XGBClassifier):
        return np.asarray(shap.TreeExplainer(model).shap_values(x), dtype=np.float32)
    scaled = model[:-1].transform(x)
    return (scaled * model[-1].coef_[0]).astype(np.float32)


@dataclass(frozen=True)
class Reason:
    """One reason for an alert: feature, its value, its SHAP value, and the sentence."""

    feature: str
    value: float
    shap: float
    text: str


def describe(
    feature: str,
    value: float,
    reasons: dict[str, Any],
    rule_names: dict[str, str],
) -> str:
    """Turn a feature and its value into a short plain-English sentence."""
    templates = reasons["templates"]
    directions = reasons["directions"]
    common = {"v": value, "s": "" if value == 1 else "s"}
    if feature in templates:
        return templates[feature].format(**common)
    if m := RULE_HIT.match(feature):
        rule = m.group(1)
        return templates["rule_hit"].format(
            rule=rule, rule_name=rule_names.get(rule, ""), **common
        )
    if m := VS_HISTORY.match(feature):
        direction, measure = m.groups()
        return templates[measure].format(
            ratio=math.exp(value), **directions[direction], **common
        )
    if m := OUT_SHARE.match(feature):
        return templates["out_share"].format(w=m.group(1), **common)
    if m := WINDOWED.match(feature):
        direction, measure, window = m.groups()
        if measure in templates:
            return templates[measure].format(
                w=window, **directions[direction], **common
            )
    raise KeyError(f"No reason template for feature {feature!r}")


def top_reasons(
    shap_row: np.ndarray,
    value_row: np.ndarray,
    names: list[str],
    reasons: dict[str, Any],
    rule_names: dict[str, str],
    n: int = 3,
) -> list[Reason]:
    """The n features that raised the score most (positive SHAP values only)."""
    order = np.argsort(-shap_row, kind="stable")[:n]
    return [
        Reason(
            feature=names[i],
            value=float(value_row[i]),
            shap=float(shap_row[i]),
            text=describe(names[i], float(value_row[i]), reasons, rule_names),
        )
        for i in order
        if shap_row[i] > 0
    ]
