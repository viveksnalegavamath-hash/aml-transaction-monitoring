"""Tests for the REST API.

Most tests use a fake repository and a tiny model, so they run anywhere (CI
included). The last ones need the database, the read-only role and the
exported model, and are skipped when those are missing.
"""

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import pytest
import shap
import yaml
from fastapi.testclient import TestClient
from xgboost import XGBClassifier

from aml_transaction_monitoring.api.app import create_app
from aml_transaction_monitoring.api.model_store import ServedModel
from aml_transaction_monitoring.api.provision import scram_verifier
from aml_transaction_monitoring.api.repository import PostgresRepository
from aml_transaction_monitoring.api.scoring import Scorer
from aml_transaction_monitoring.api.security import RateLimiter, key_matches
from aml_transaction_monitoring.api.settings import Settings, load_config
from aml_transaction_monitoring.db import get_reader_connection

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "src" / "aml_transaction_monitoring" / "api"
REASONS = yaml.safe_load((ROOT / "config" / "ml.yaml").read_text())["reasons"]
KEY = "test-key-123"
HEADERS = {"X-API-Key": KEY}
COLUMNS = ["in_new_counterparties_7", "out_n_7", "rule_R06_7d"]
LABEL_WORDS = re.compile(r"launder|typolog|label|is_suspicious", re.IGNORECASE)


def settings(**changes: Any) -> Settings:
    values = {
        "api_key": KEY,
        "reader_role": "test_reader",
        "model_dir": Path("/nonexistent"),
        "rate_limit": 100,
        "rate_window_seconds": 60,
        "max_alerts": 10,
        "reasons": REASONS,
    }
    return Settings(**{**values, **changes})


class FakeRepository:
    """Stands in for the database."""

    def __init__(self, row: np.ndarray | None = None, fail: Exception | None = None):
        self.row = row if row is not None else np.array([3, 2, 1], dtype=np.float32)
        self.fail = fail
        self.database_up = True

    def ping(self) -> bool:
        return self.database_up

    def features(self, account_id: str, week_end: date, columns: list[str]) -> Any:
        if self.fail:
            raise self.fail
        return None if account_id == "999" else self.row

    def alerts(self, account_id: str, limit: int) -> tuple[list, list]:
        if self.fail:
            raise self.fail
        rule = {
            "alert_id": "a1",
            "rule_id": "R06",
            "rule_version": 2,
            "alert_date": date(2023, 8, 20),
            "trigger_ts": datetime.fromisoformat("2023-08-20 10:30"),
            "reason": "Sent £50,000.00, 12.0 times the 90-day mean",
            "status": "New",
        }
        ml = {
            "alert_id": "m1",
            "model": "rules_ml",
            "week_end": date(2023, 8, 23),
            "score": 0.97,
            "rank_in_week": 4,
            "reasons": "4 new payees in 7 days; 3 new senders in 7 days",
        }
        return [rule], [ml]


@pytest.fixture(scope="module")
def scorer() -> Scorer:
    rng = np.random.default_rng(3)
    x = rng.integers(0, 5, size=(300, 3)).astype(np.float32)
    y = (x[:, 0] + rng.normal(size=300) > 3).astype(int)
    model = XGBClassifier(n_estimators=20, max_depth=3, random_state=1).fit(x, y)
    served = ServedModel(model, COLUMNS, "rules_ml", "run-abc")
    return Scorer(
        served, shap.TreeExplainer(model), REASONS, {"R06": "Large vs profile"}
    )


def client(scorer: Scorer | None, repository: Any = None, **changes: Any) -> TestClient:
    app = create_app(settings(**changes), repository or FakeRepository(), scorer)
    return TestClient(app, raise_server_exceptions=False)


# --- Health and authentication -------------------------------------------------------


def test_health_needs_no_key(scorer: Scorer) -> None:
    response = client(scorer).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "model_loaded": True}


def test_health_reports_a_database_outage(scorer: Scorer) -> None:
    repository = FakeRepository()
    repository.database_up = False
    body = client(scorer, repository).get("/health").json()
    assert (body["status"], body["database"]) == ("degraded", "unavailable")


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_protected_endpoints_need_the_right_key(scorer: Scorer, headers: dict) -> None:
    api = client(scorer)
    assert api.get("/alerts/123", headers=headers).status_code == 401
    body = {"account_id": "123", "week_end": "2023-08-23"}
    assert (
        api.post("/transactions/score", json=body, headers=headers).status_code == 401
    )


def test_key_matches() -> None:
    assert key_matches("abc", "abc")
    assert not key_matches("abd", "abc")
    assert not key_matches(None, "abc")
    assert not key_matches("", "abc")


def test_api_never_starts_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aml_transaction_monitoring.api.settings.load_dotenv", lambda: None
    )
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY"):
        Settings.from_env()


# --- Scoring ---------------------------------------------------------------------------


def test_score_returns_the_score_and_top_three_reasons(scorer: Scorer) -> None:
    body = {"account_id": "5826952585", "week_end": "2023-08-23"}
    response = client(scorer).post("/transactions/score", json=body, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["account_id"] == "5826952585"
    assert (result["model"], result["model_run_id"]) == ("rules_ml", "run-abc")
    assert 0 <= result["score"] <= 1
    assert 1 <= len(result["reasons"]) <= 3
    assert all(r["shap"] > 0 and r["text"] for r in result["reasons"])
    # Same numbers as scoring the row directly.
    expected_score, expected_reasons = scorer.score(
        np.array([3, 2, 1], dtype=np.float32)
    )
    assert result["score"] == pytest.approx(expected_score)
    assert [r["text"] for r in result["reasons"]] == [r.text for r in expected_reasons]


@pytest.mark.parametrize(
    "body",
    [
        {"account_id": "123", "week_end": "2023-08-22"},  # a Tuesday
        {"account_id": "12a", "week_end": "2023-08-23"},  # not digits
        {"account_id": "123", "week_end": "2023-08-23", "label": 1},  # unknown field
        {"account_id": "123"},  # missing week
    ],
)
def test_score_rejects_bad_requests(scorer: Scorer, body: dict) -> None:
    response = client(scorer).post("/transactions/score", json=body, headers=HEADERS)
    assert response.status_code == 422


def test_score_404_when_the_account_was_not_active(scorer: Scorer) -> None:
    body = {"account_id": "999", "week_end": "2023-08-23"}
    response = client(scorer).post("/transactions/score", json=body, headers=HEADERS)
    assert response.status_code == 404


def test_score_503_without_a_model() -> None:
    body = {"account_id": "123", "week_end": "2023-08-23"}
    response = client(None).post("/transactions/score", json=body, headers=HEADERS)
    assert response.status_code == 503


# --- Alerts ----------------------------------------------------------------------------


def test_alerts_for_one_account(scorer: Scorer) -> None:
    response = client(scorer).get("/alerts/5826952585", headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert [a["rule_id"] for a in result["rule_alerts"]] == ["R06"]
    assert result["ml_alerts"][0]["reasons"] == [
        "4 new payees in 7 days",
        "3 new senders in 7 days",
    ]


def test_alerts_reject_an_invalid_account_id(scorer: Scorer) -> None:
    assert client(scorer).get("/alerts/abc", headers=HEADERS).status_code == 422


# --- Rate limiting -------------------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(scorer: Scorer) -> None:
    api = client(scorer, rate_limit=2)
    assert [api.get("/alerts/1", headers=HEADERS).status_code for _ in range(2)] == [
        200,
        200,
    ]
    limited = api.get("/alerts/1", headers=HEADERS)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert api.get("/health").status_code == 200  # health is never limited


def test_rate_limiter_sliding_window() -> None:
    now = [0.0]
    limiter = RateLimiter(limit=2, window=10, clock=lambda: now[0])
    assert limiter.check("a") == 0 and limiter.check("a") == 0
    assert limiter.check("a") == pytest.approx(10)
    assert limiter.check("b") == 0  # per client
    now[0] = 10.5
    assert limiter.check("a") == 0


# --- No labels, no internals ---------------------------------------------------------


def test_no_label_field_in_any_schema(scorer: Scorer) -> None:
    schemas = client(scorer).get("/openapi.json").json()["components"]["schemas"]
    fields = [f for s in schemas.values() for f in s.get("properties", {})]
    assert fields
    assert [f for f in fields if LABEL_WORDS.search(f)] == []


def test_api_package_never_touches_the_evaluation_code_or_labels() -> None:
    forbidden = re.compile(
        r"aml_transaction_monitoring\.evaluation|evaluation(_sample)?\.|"
        r"eval_\w+_labels|is_laundering",
    )
    for path in sorted(API_SRC.glob("*.py")):
        match = forbidden.search(path.read_text(encoding="utf-8"))
        assert match is None, f"{path.name}: {match.group(0)!r}"


def test_errors_hide_their_details(scorer: Scorer) -> None:
    api = client(scorer, FakeRepository(fail=RuntimeError("secret internal detail")))
    response = api.get("/alerts/1", headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal error"}
    assert "secret" not in response.text


def test_database_outage_returns_503(scorer: Scorer) -> None:
    api = client(scorer, FakeRepository(fail=psycopg.OperationalError("down")))
    assert api.get("/alerts/1", headers=HEADERS).status_code == 503


# --- Settings and provisioning ---------------------------------------------------------


def test_scram_verifier_has_postgres_format() -> None:
    verifier = scram_verifier("pw", salt=b"0123456789abcdef", iterations=4096)
    match = re.fullmatch(r"SCRAM-SHA-256\$4096:([^$]+)\$([^:]+):(.+)", verifier)
    assert match
    assert verifier == scram_verifier("pw", salt=b"0123456789abcdef")  # deterministic
    assert verifier != scram_verifier("pw2", salt=b"0123456789abcdef")
    assert "pw" not in verifier.split("$", 1)[1].split(":", 1)[0]


def test_reader_role_is_the_same_in_dbt_and_the_api_config() -> None:
    dbt_vars = yaml.safe_load((ROOT / "dbt" / "dbt_project.yml").read_text())["vars"]
    assert dbt_vars["api_reader_role"] == load_config()["reader_role"]


# --- Real database, role and model (skipped when not set up) ----------------------------


@pytest.fixture(scope="module")
def real_api() -> TestClient:
    try:
        real = Settings.from_env()
    except RuntimeError as e:
        pytest.skip(f"API not configured: {e}")
    if not (real.model_dir / "model.json").exists():
        pytest.skip("no exported model (api.model_store)")
    if not PostgresRepository(real.reader_role, []).ping():
        pytest.skip("database or read-only role not reachable")
    return TestClient(create_app(real), headers={"X-API-Key": real.api_key})


def test_reader_role_cannot_read_labels_or_write(real_api: TestClient) -> None:
    role = load_config()["reader_role"]
    with get_reader_connection(role) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select 1 from evaluation.eval_transaction_labels limit 1")
        conn.rollback()
        with pytest.raises(
            (
                psycopg.errors.ReadOnlySqlTransaction,
                psycopg.errors.InsufficientPrivilege,
            )
        ):
            conn.execute("delete from marts.alerts where false")


def test_api_score_matches_the_batch_hold_out_score(real_api: TestClient) -> None:
    role = load_config()["reader_role"]
    with get_reader_connection(role) as conn:
        account_id, week_end, stored_score, stored_reasons = conn.execute(
            "select account_id, week_end, score, reasons from marts.ml_alerts"
            " where model = 'rules_ml' order by week_end desc, rank_in_week limit 1"
        ).fetchone()
    response = real_api.post(
        "/transactions/score",
        json={"account_id": account_id, "week_end": week_end.isoformat()},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["score"] == pytest.approx(stored_score, abs=1e-6)
    assert "; ".join(r["text"] for r in result["reasons"]) == stored_reasons
    alerts = real_api.get(f"/alerts/{account_id}").json()
    assert any(a["week_end"] == week_end.isoformat() for a in alerts["ml_alerts"])
    assert not LABEL_WORDS.search(response.text + str(alerts.keys()))


def test_real_health(real_api: TestClient) -> None:
    assert real_api.get("/health").json()["status"] == "ok"


def test_secrets_are_never_in_responses(real_api: TestClient) -> None:
    body = real_api.get("/health").text + real_api.get("/openapi.json").text
    for name in ("API_KEY", "API_DB_PASSWORD"):
        value = os.environ.get(name)
        assert value and value not in body
