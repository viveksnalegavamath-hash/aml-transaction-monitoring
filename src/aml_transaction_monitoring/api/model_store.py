"""Export the selected model for the API, and load it there.

Training keeps its models as pickles (data/ml/, our own files). The API
loads XGBoost's own JSON format instead: loading it cannot run code
(unpickling can), and the format is stable across XGBoost versions. The
export checks, on real feature rows, that the JSON model scores exactly like
the trained one.

Usage (after the ML validate stage):
    uv run python -m aml_transaction_monitoring.api.model_store
"""

import json
import logging
import pickle
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from xgboost import XGBClassifier

from aml_transaction_monitoring.api.settings import load_config
from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ml.features import load_features
from aml_transaction_monitoring.ml.model import predict_score
from aml_transaction_monitoring.ml.store import (
    API_MODEL_DIR,
    model_path,
    selection_path,
)
from aml_transaction_monitoring.rules.engine import load_rules

logger = logging.getLogger(__name__)

MODEL_FILE = "model.json"
META_FILE = "meta.json"
CHECK_WEEK = date(2023, 8, 23)  # last week of the data
CHECK_ROWS = 5_000


@dataclass(frozen=True)
class ServedModel:
    """The model the API serves, with what it needs to score one row."""

    model: XGBClassifier
    columns: list[str]
    feature_set: str
    run_id: str


def export(
    feature_set: str, out_dir: Path = API_MODEL_DIR, dataset: str = "full"
) -> Path:
    """Write the selected model of `feature_set` as XGBoost JSON plus a meta file."""
    manifest = json.loads(selection_path(dataset).read_text(encoding="utf-8"))
    key = manifest["selected"][feature_set]
    info = manifest["models"][key]
    if info["family"] != "xgboost":
        raise ValueError(
            f"The API serves XGBoost models only; {key} is {info['family']}"
        )
    with model_path(dataset, key).open("rb") as f:
        trained = pickle.load(f)  # our own training output
    out_dir.mkdir(parents=True, exist_ok=True)
    trained.save_model(out_dir / MODEL_FILE)
    meta = {
        "feature_set": feature_set,
        "model_key": key,
        "run_id": info["run_id"],
        "params": info["params"],
        "best_iteration": info["best_iteration"],
        "columns": info["columns"],
        "dataset": dataset,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (out_dir / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    check_same_scores(trained, load(out_dir), dataset)
    logger.info("Exported %s (MLflow run %s) to %s", key, info["run_id"][:8], out_dir)
    return out_dir


def load(model_dir: Path) -> ServedModel:
    """Load the exported model (JSON) and its feature columns."""
    meta = json.loads((model_dir / META_FILE).read_text(encoding="utf-8"))
    model = XGBClassifier()
    model.load_model(model_dir / MODEL_FILE)
    return ServedModel(model, meta["columns"], meta["feature_set"], meta["run_id"])


def check_same_scores(trained: Any, served: ServedModel, dataset: str) -> None:
    """The JSON model must score real rows exactly like the trained one."""
    if dataset != "full":
        return
    rule_ids = [r.id for r in load_rules()]
    with get_connection() as conn:
        features = load_features(
            conn, "features", "marts", CHECK_WEEK, CHECK_WEEK, rule_ids
        )
    x = features.matrix(served.columns)[:CHECK_ROWS]
    difference = np.abs(
        predict_score(trained, x) - predict_score(served.model, x)
    ).max()
    if difference > 1e-6:
        raise RuntimeError(f"Exported model scores differ by up to {difference:.2e}")
    logger.info(
        "Check passed: JSON model scores %s real rows exactly like the trained one",
        f"{len(x):,}",
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    export(load_config()["feature_set"])


if __name__ == "__main__":
    main()
