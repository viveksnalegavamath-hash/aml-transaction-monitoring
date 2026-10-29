"""Request and response models. No field can carry the suspicious label.

Requests reject unknown fields; account IDs are digits only (SAML-D IDs);
a week is named by its last day, a Wednesday (the scoring day).
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACCOUNT_ID_PATTERN = r"^\d{1,20}$"


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    model_loaded: bool


class ScoreRequest(BaseModel):
    """One account-week to score: the account and the Wednesday ending the week."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"account_id": "5826952585", "week_end": "2023-08-23"}]
        },
    )

    account_id: str = Field(
        pattern=ACCOUNT_ID_PATTERN, description="Account ID (digits)."
    )
    week_end: date = Field(
        description="Last day of the Thursday-to-Wednesday week: must be a Wednesday."
    )

    @field_validator("week_end")
    @classmethod
    def must_be_wednesday(cls, value: date) -> date:
        if value.weekday() != 2:
            raise ValueError(
                "week_end must be a Wednesday (weeks run Thursday to Wednesday)"
            )
        return value


class Reason(BaseModel):
    """One of the top three reasons: the feature, its value, its SHAP value, the sentence."""

    feature: str
    value: float
    shap: float = Field(
        description="Push on the model's log-odds (positive = more suspicious)."
    )
    text: str


class ScoreResponse(BaseModel):
    account_id: str
    week_end: date
    model: str = Field(description="Feature set of the served model, e.g. rules_ml.")
    model_run_id: str = Field(description="MLflow run that trained the model.")
    score: float = Field(
        ge=0, le=1, description="Model score; higher = more suspicious."
    )
    reasons: list[Reason] = Field(max_length=3)


class RuleAlert(BaseModel):
    alert_id: str
    rule_id: str
    rule_version: int
    alert_date: date
    trigger_ts: datetime
    reason: str
    status: str


class MLAlert(BaseModel):
    alert_id: str
    model: str
    week_end: date
    score: float
    rank_in_week: int
    reasons: list[str]


class AlertsResponse(BaseModel):
    account_id: str
    rule_alerts: list[RuleAlert]
    ml_alerts: list[MLAlert]
