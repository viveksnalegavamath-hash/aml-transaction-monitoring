"""Rotate the database passwords in `.env` and PostgreSQL, never showing a value.

Rotates POSTGRES_PASSWORD (the admin role) and API_DB_PASSWORD (the API's
read-only role) together. The method of the 25 Oct 2026 rotation
(LEARNING.md), with one improvement: PostgreSQL receives SCRAM-SHA-256
verifiers computed here, so the plain passwords never reach the server.

1. Read `.env` into memory; generate new random values (letters and digits).
2. Write the new `.env` to a temporary file beside it: mode 600, every other
   line unchanged.
3. Over ONE admin connection, opened with the old password: change both
   roles' passwords.
4. Check that each new password logs in and each old one is refused. If
   anything fails, put the old passwords back, delete the temporary file and
   stop: `.env` is untouched.
5. Swap the temporary file in (atomic rename), then check it: mode 600, the
   same other lines, the new values readable.

Prints key names, PASS / FAIL and counts only; database errors by type only.
The API key is not handled here: api.provision adds one when it is missing and
replaces it with --rotate-api-key.

Usage:
    uv run python -m aml_transaction_monitoring.rotate_secrets
"""

import io
import logging
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql

from aml_transaction_monitoring.api.provision import scram_verifier
from aml_transaction_monitoring.api.settings import load_config
from aml_transaction_monitoring.db import DEFAULT_HOST
from aml_transaction_monitoring.envfile import (
    mode,
    new_secret,
    other_lines,
    replace_values,
    staged_path,
    swap_in,
    write_private,
)
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

logger = logging.getLogger(__name__)

ENV_PATH = PROJECT_ROOT / ".env"
ROTATED = ("POSTGRES_PASSWORD", "API_DB_PASSWORD")


class RotationFailed(RuntimeError):
    """A check failed; the old passwords were restored and `.env` left as it was."""


def connect(
    settings: dict[str, str | None], user: str, password: str
) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.get("POSTGRES_HOST") or DEFAULT_HOST,
        port=int(settings["POSTGRES_PORT"] or 5432),
        dbname=settings["POSTGRES_DB"],
        user=user,
        password=password,
        connect_timeout=5,
    )


def can_log_in(settings: dict[str, str | None], user: str, password: str) -> bool:
    try:
        with connect(settings, user, password) as conn:
            conn.execute("select 1")
        return True
    except psycopg.OperationalError:
        return False


def set_password(admin: psycopg.Connection, role: str, password: str) -> None:
    admin.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(scram_verifier(password))
        )
    )


def rotate(env_path: Path = ENV_PATH) -> None:
    """Rotate both database passwords; see the module docstring for the steps."""
    text = env_path.read_text(encoding="utf-8")
    settings = dotenv_values(stream=io.StringIO(text))
    missing = [
        k for k in (*ROTATED, "POSTGRES_USER", "POSTGRES_DB") if not settings.get(k)
    ]
    if missing:
        raise RotationFailed(f"Missing in .env: {', '.join(missing)}")
    roles = {
        "POSTGRES_PASSWORD": settings["POSTGRES_USER"],
        "API_DB_PASSWORD": load_config()["reader_role"],
    }
    old = {key: settings[key] for key in ROTATED}
    new = {key: new_secret() for key in ROTATED}
    new_text = replace_values(text, new)
    if other_lines(new_text, ROTATED) != other_lines(text, ROTATED):
        raise RotationFailed("Rewriting .env would change other lines; stopped")
    staged = staged_path(env_path)
    write_private(staged, new_text)
    logger.info("New .env staged with mode %o (values not shown)", mode(staged))

    try:
        with connect(
            settings, roles["POSTGRES_PASSWORD"], old["POSTGRES_PASSWORD"]
        ) as admin:
            admin.autocommit = True
            try:
                for key, role in roles.items():
                    set_password(admin, role, new[key])
                    logger.info("Changed the password of role %s (%s)", role, key)
                failures = []
                for key, role in roles.items():
                    if can_log_in(settings, role, new[key]):
                        logger.info("PASS %s logs in with the new password", role)
                    else:
                        failures.append(f"{role} cannot log in with the new password")
                    if can_log_in(settings, role, old[key]):
                        failures.append(f"{role} still accepts the old password")
                    else:
                        logger.info("PASS %s refuses the old password", role)
                if failures:
                    raise RotationFailed("; ".join(failures))
            except Exception as e:
                for key, role in roles.items():
                    set_password(admin, role, old[key])
                logger.error("FAILED (%s): old passwords restored", type(e).__name__)
                raise
    except Exception:
        staged.unlink(missing_ok=True)
        logger.error(".env left unchanged")
        raise

    swap_in(staged, env_path)
    final = env_path.read_text(encoding="utf-8")
    values = dotenv_values(stream=io.StringIO(final))
    checks = {
        ".env is mode 600": mode(env_path) == 0o600,
        "every other line unchanged": other_lines(final, ROTATED)
        == other_lines(text, ROTATED),
        "new values in .env": all(values.get(k) == new[k] for k in ROTATED),
    }
    for what, ok in checks.items():
        logger.info("%s %s", "PASS" if ok else "FAIL", what)
    if not all(checks.values()):
        raise RotationFailed("Checks after the swap failed (passwords are already new)")
    logger.info("Rotated: %s", ", ".join(ROTATED))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        rotate()
    except RotationFailed as e:
        raise SystemExit(f"Rotation stopped: {e}") from None
    except psycopg.Error as e:
        raise SystemExit(
            f"Rotation stopped: database error {type(e).__name__}"
        ) from None


if __name__ == "__main__":
    main()
