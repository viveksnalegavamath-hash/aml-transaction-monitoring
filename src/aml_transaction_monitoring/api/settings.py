"""API settings: non-secret values from config/api.yaml, secrets from the environment."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / "config" / "api.yaml"
ML_CONFIG_PATH = PROJECT_ROOT / "config" / "ml.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Settings:
    """Everything the app needs at start-up."""

    api_key: str
    reader_role: str
    model_dir: Path
    rate_limit: int
    rate_window_seconds: float
    max_alerts: int
    reasons: dict[str, Any]

    @classmethod
    def from_env(cls) -> "Settings":
        """Read config/api.yaml and the environment (`.env` locally, Compose in Docker).

        Raises:
            RuntimeError: if API_KEY is not set: the API never runs without a key.
        """
        load_dotenv()
        config = load_config()
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise RuntimeError(
                "API_KEY is not set: run api.provision, or set it in Compose"
            )
        with ML_CONFIG_PATH.open(encoding="utf-8") as f:
            reasons = yaml.safe_load(f)["reasons"]
        model_dir = Path(os.getenv("MODEL_DIR") or PROJECT_ROOT / config["model_dir"])
        return cls(
            api_key=api_key,
            reader_role=config["reader_role"],
            model_dir=model_dir,
            rate_limit=config["rate_limit"]["requests"],
            rate_window_seconds=config["rate_limit"]["per_seconds"],
            max_alerts=config["max_alerts"],
            reasons=reasons,
        )
