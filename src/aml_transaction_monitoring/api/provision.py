"""Set up the API's secrets and its read-only database role. Safe to re-run.

1. Adds API_KEY and API_DB_PASSWORD to `.env` if they are missing: random
   values from `secrets`, appended without ever being printed (the file stays
   mode 600). Existing values are kept.
2. Creates or updates the role in config/api.yaml (default aml_api_reader):
   it can log in, read only the tables listed there, has every transaction
   read-only by default, a statement timeout and a connection limit. The
   password is sent as a SCRAM-SHA-256 verifier computed here, so the plain
   password never reaches the server (or its logs).
3. Checks, connected as the role: it can read each listed table; it has no
   access at all to the evaluation schema (the labels) and cannot write.

Prints names and PASS/FAIL only, never a secret.

Usage:
    uv run python -m aml_transaction_monitoring.api.provision
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from aml_transaction_monitoring.api.settings import load_config
from aml_transaction_monitoring.db import get_connection, get_reader_connection
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

logger = logging.getLogger(__name__)

ENV_PATH = PROJECT_ROOT / ".env"
SECRETS = ("API_KEY", "API_DB_PASSWORD")
SCRAM_ITERATIONS = 4096
LABEL_SCHEMA = "evaluation"


def scram_verifier(
    password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """PostgreSQL's stored form of a SCRAM-SHA-256 password (RFC 5802 / 7677).

    PostgreSQL accepts this in CREATE / ALTER ROLE ... PASSWORD and stores it
    as is, so the plain password is never sent to the server.
    """
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(b: bytes) -> str:
        return base64.b64encode(b).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def ensure_secrets(env_path: Path = ENV_PATH) -> list[str]:
    """Append any missing secret to `.env`; return the names added (never values)."""
    load_dotenv(env_path)
    added = []
    for name in SECRETS:
        if os.getenv(name):
            continue
        value = secrets.token_urlsafe(32)
        with env_path.open("a", encoding="utf-8") as f:
            # Leading newline: safe even if the file does not end with one.
            f.write(f"\n{name}={value}\n")
        os.environ[name] = value
        added.append(name)
    env_path.chmod(0o600)
    return added


def create_role(
    conn: psycopg.Connection, config: dict[str, Any], password: str
) -> None:
    """Create or update the read-only role and its grants, in one transaction."""
    role = sql.Identifier(config["reader_role"])
    tables = [sql.Identifier(*t.split(".")) for t in config["reader_tables"]]
    schemas = sorted({t.split(".")[0] for t in config["reader_tables"]})
    verifier = sql.Literal(scram_verifier(password))
    with conn.transaction():
        exists = conn.execute(
            "select 1 from pg_roles where rolname = %s", (config["reader_role"],)
        ).fetchone()
        action = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(
                action + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"
                " NOINHERIT CONNECTION LIMIT {} PASSWORD {}"
            ).format(role, sql.Literal(config["reader_connection_limit"]), verifier)
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role)
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
                role, sql.Literal(config["reader_statement_timeout"])
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(os.environ["POSTGRES_DB"]), role
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.SQL(", ").join(map(sql.Identifier, schemas)), role
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(sql.SQL(", ").join(tables), role)
        )
    logger.info(
        "%s role %s: read-only, SELECT on %s",
        "Updated" if exists else "Created",
        config["reader_role"],
        ", ".join(config["reader_tables"]),
    )


def check_role(config: dict[str, Any]) -> bool:
    """Connected as the role: tables readable, labels and writes refused."""
    ok = True
    with get_reader_connection(config["reader_role"]) as conn:
        for table in config["reader_tables"]:
            try:
                with conn.transaction():
                    conn.execute(
                        sql.SQL("SELECT 1 FROM {} LIMIT 1").format(
                            sql.Identifier(*table.split("."))
                        )
                    )
                logger.info("PASS can read %s", table)
            except psycopg.Error as e:
                ok = False
                logger.error("FAIL cannot read %s (%s)", table, type(e).__name__)
        with conn.transaction():
            (label_access,) = conn.execute(
                "select has_schema_privilege(current_user, %s, 'USAGE')",
                (LABEL_SCHEMA,),
            ).fetchone()
        if label_access:
            ok = False
            logger.error("FAIL can use the %s schema (labels)", LABEL_SCHEMA)
        else:
            logger.info("PASS no access to the %s schema (labels)", LABEL_SCHEMA)
        try:
            with conn.transaction():
                conn.execute("DELETE FROM marts.alerts WHERE false")
            ok = False
            logger.error("FAIL can write: DELETE FROM marts.alerts succeeded")
        except (
            psycopg.errors.InsufficientPrivilege,
            psycopg.errors.ReadOnlySqlTransaction,
        ):
            logger.info("PASS cannot write (DELETE FROM marts.alerts)")
    return ok


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = load_config()
    added = ensure_secrets()
    logger.info(
        "Secrets in .env: %s",
        ", ".join(f"{n} ({'added' if n in added else 'kept'})" for n in SECRETS),
    )
    with get_connection() as conn:
        create_role(conn, config, os.environ["API_DB_PASSWORD"])
    if not check_role(config):
        raise SystemExit("Role checks failed")
    logger.info("All role checks passed")


if __name__ == "__main__":
    main()
