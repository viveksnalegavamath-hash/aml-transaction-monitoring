"""FastAPI app: health, account-week scoring with SHAP reasons, alerts per account.

- GET  /health                  liveness: database reachable, model loaded (no key)
- POST /transactions/score      score one account-week with the served XGBoost
                                model and return its top three SHAP reasons
- GET  /alerts/{account_id}     rule and ML alerts for one account

Every endpoint except /health needs the X-API-Key header (API_KEY in .env)
and is rate-limited per client address. Data is read through a read-only
database role that cannot see the evaluation schema, so no label can reach a
response. Errors return a short message, never a stack trace; logs hold the
route, status and duration, never account IDs or secrets.

Run locally (Swagger docs at http://127.0.0.1:8000/docs). --no-access-log:
uvicorn's access log would print raw paths, which contain account IDs; this
app logs the route template instead.
    uv run uvicorn --factory aml_transaction_monitoring.api.app:create_app \\
        --host 127.0.0.1 --port 8000 --no-access-log
"""

import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Request, Security
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader

from aml_transaction_monitoring.api.repository import PostgresRepository, Repository
from aml_transaction_monitoring.api.schemas import (
    ACCOUNT_ID_PATTERN,
    AlertsResponse,
    Health,
    MLAlert,
    Reason,
    RuleAlert,
    ScoreRequest,
    ScoreResponse,
)
from aml_transaction_monitoring.api.scoring import Scorer
from aml_transaction_monitoring.api.security import RateLimiter, key_matches
from aml_transaction_monitoring.api.settings import Settings
from aml_transaction_monitoring.rules.engine import load_rules

logger = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    """Send the app's INFO logs to stderr once (uvicorn configures only its own)."""
    package = logging.getLogger("aml_transaction_monitoring")
    if not package.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        package.addHandler(handler)
        package.setLevel(logging.INFO)


DESCRIPTION = """
Transaction monitoring API on **synthetic** data (SAML-D). Read-only.

Send your key in the `X-API-Key` header (button **Authorize**). Scores and
alerts never include the suspicious label: the API's database role cannot read it.
"""


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    scorer: Scorer | None = None,
) -> FastAPI:
    """Build the app. Tests pass fakes; uvicorn calls it with no arguments (--factory).

    Raises:
        RuntimeError: if no API key is configured (the API never runs without one).
    """
    configure_logging()
    settings = settings or Settings.from_env()
    if repository is None or scorer is None:
        rules = load_rules()
        if repository is None:
            repository = PostgresRepository(settings.reader_role, [r.id for r in rules])
        if scorer is None:
            try:
                scorer = Scorer.load(
                    settings.model_dir, settings.reasons, {r.id: r.name for r in rules}
                )
            except FileNotFoundError:
                logger.warning(
                    "No exported model found: /transactions/score returns 503"
                )
    limiter = RateLimiter(settings.rate_limit, settings.rate_window_seconds)
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def rate_limited(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        wait = limiter.check(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(math.ceil(wait))},
            )

    def authorised(key: Annotated[str | None, Security(api_key_header)]) -> None:
        if not key_matches(key, settings.api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    protected = [Depends(rate_limited), Depends(authorised)]

    app = FastAPI(
        title="AML transaction monitoring API",
        version="1.0.0",
        description=DESCRIPTION,
    )

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        logger.info(
            "%s %s -> %d in %.0f ms",
            request.method,
            getattr(route, "path", "unmatched"),  # template, never the account ID
            response.status_code,
            1000 * (time.perf_counter() - started),
        )
        return response

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Database unavailable (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled %s", type(exc).__name__)  # no stack trace, no data
        return JSONResponse(status_code=500, content={"detail": "Internal error"})

    @app.get("/health", response_model=Health, tags=["service"])
    def health() -> Health:
        """Liveness: is the database reachable and the model loaded? No key needed."""
        database_ok = repository.ping()
        ok = database_ok and scorer is not None
        return Health(
            status="ok" if ok else "degraded",
            database="ok" if database_ok else "unavailable",
            model_loaded=scorer is not None,
        )

    @app.post(
        "/transactions/score",
        response_model=ScoreResponse,
        dependencies=protected,
        tags=["scoring"],
        responses={
            404: {"description": "No transactions for the account in that week"}
        },
    )
    def score(request: ScoreRequest) -> ScoreResponse:
        """Score one account-week with the served XGBoost model (rules + ML features),
        with its top three SHAP reasons in plain English."""
        if scorer is None:
            raise HTTPException(status_code=503, detail="Model not loaded")
        values = repository.features(
            request.account_id, request.week_end, scorer.columns
        )
        if values is None:
            raise HTTPException(
                status_code=404,
                detail="No transactions for this account in the week ending week_end",
            )
        value, reasons = scorer.score(values)
        return ScoreResponse(
            account_id=request.account_id,
            week_end=request.week_end,
            model=scorer.served.feature_set,
            model_run_id=scorer.served.run_id,
            score=value,
            reasons=[Reason(**vars(r)) for r in reasons],
        )

    @app.get(
        "/alerts/{account_id}",
        response_model=AlertsResponse,
        dependencies=protected,
        tags=["alerts"],
    )
    def alerts(
        account_id: Annotated[
            str, Path(pattern=ACCOUNT_ID_PATTERN, description="Account ID")
        ],
    ) -> AlertsResponse:
        """Rule alerts and ML alerts for one account, newest first."""
        rule_rows, ml_rows = repository.alerts(account_id, settings.max_alerts)
        return AlertsResponse(
            account_id=account_id,
            rule_alerts=[RuleAlert(**r) for r in rule_rows],
            ml_alerts=[
                MLAlert(**{**r, "reasons": [s for s in r["reasons"].split("; ") if s]})
                for r in ml_rows
            ],
        )

    return app
