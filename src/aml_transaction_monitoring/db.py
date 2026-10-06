"""Database connection helper.

Reads credentials from environment variables (loaded from `.env`), so no
password ever appears in code.
"""

import os

import psycopg
from dotenv import load_dotenv

# Always 127.0.0.1, never "localhost": on this Mac "localhost" reaches a
# different PostgreSQL server and the login fails.
DB_HOST = "127.0.0.1"

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
        host=DB_HOST,
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
