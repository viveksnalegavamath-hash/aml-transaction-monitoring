"""Where trained models are kept (data/ml/, git-ignored). No labels here.

Training (the ML experiment) writes one pickle per kept model and a
selection manifest per dataset. The API reads an export of the selected
model in XGBoost's own JSON format (api.model_store), never a pickle.
"""

from pathlib import Path

from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

MODEL_DIR = PROJECT_ROOT / "data" / "ml"
API_MODEL_DIR = MODEL_DIR / "api"


def model_path(dataset: str, key: str) -> Path:
    """Pickle of one trained model, e.g. full_rules_ml-xgboost.pkl."""
    return MODEL_DIR / f"{dataset}_{key}.pkl"


def selection_path(dataset: str) -> Path:
    """Manifest of the kept and selected models for a dataset ("full" or "sample")."""
    return MODEL_DIR / f"{dataset}_selection.json"
