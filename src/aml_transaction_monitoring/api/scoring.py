"""Score one account-week with the served model and explain it (top three SHAP reasons)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shap

from aml_transaction_monitoring.api.model_store import ServedModel, load
from aml_transaction_monitoring.ml.model import (
    Reason,
    contributions,
    predict_score,
    top_reasons,
)


@dataclass
class Scorer:
    """The served model plus a TreeSHAP explainer built once at start-up."""

    served: ServedModel
    explainer: shap.TreeExplainer
    reasons: dict[str, Any]
    rule_names: dict[str, str]

    @classmethod
    def load(
        cls, model_dir: Path, reasons: dict[str, Any], rule_names: dict[str, str]
    ) -> "Scorer":
        served = load(model_dir)
        return cls(served, shap.TreeExplainer(served.model), reasons, rule_names)

    @property
    def columns(self) -> list[str]:
        return self.served.columns

    def score(self, values: np.ndarray) -> tuple[float, list[Reason]]:
        """Score and top three reasons for one feature row (in `columns` order)."""
        x = np.asarray(values, dtype=np.float32).reshape(1, -1)
        score = float(predict_score(self.served.model, x)[0])
        shap_row = contributions(self.served.model, x, self.explainer)[0]
        return score, top_reasons(
            shap_row, x[0], self.columns, self.reasons, self.rule_names
        )
