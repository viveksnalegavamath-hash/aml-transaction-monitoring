"""Database connection helper.

Reads credentials from environment variables (loaded from `.env`), so no
password ever appears in code.
"""

import os

import psycopg
from dotenv import load_dotenv

# 127.0.0.1, never "localhost": on this Mac "localhost" reaches a different
# PostgreSQL server and the login fails. Inside Docker Compose the API sets
# POSTGRES_HOST to the database service name instead.
DEFAULT_HOST = "127.0.0.1"

REQUIRED_SETTINGS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
)


def get_connection() -> psycopg.Connection:
    """Open a connection to the project database using settings from `.env`.

    Raises:
        RuntimeError: if a required setting is missing (names only, never values).
    """
    load_dotenv()
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing settings in .env: {', '.join(missing)}")

    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", DEFAULT_HOST),
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def get_reader_connection(role: str) -> psycopg.Connection:
    """Open a READ-ONLY connection as the API's database role.

    The role (created by api.provision) can read only the alert and feature
    tables, never the evaluation schema. Its password is API_DB_PASSWORD
    (`.env`); the session is also marked read-only, as a second guard.

    Raises:
        RuntimeError: if a required setting is missing (names only, never values).
    """
    load_dotenv()
    needed = ("API_DB_PASSWORD", "POSTGRES_DB", "POSTGRES_PORT")
    missing = [name for name in needed if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing settings: {', '.join(missing)}")
    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", DEFAULT_HOST),
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=role,
        password=os.environ["API_DB_PASSWORD"],
        connect_timeout=5,
    )
    conn.read_only = True
    return conn
