"""Load raw CSV files into the `raw` schema, exactly as downloaded.

Every column is stored as text; typing and cleaning happen later in dbt
staging. Each load runs in one transaction (empty table, COPY, audit log
entry), so a failed load leaves the previous data in place.

Usage:
    uv run python -m aml_transaction_monitoring.ingestion.load_raw
"""

import csv
import logging
from pathlib import Path

import psycopg
from psycopg import sql

from aml_transaction_monitoring.db import get_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = PROJECT_ROOT / "data" / "raw"

CHUNK_SIZE = 1024 * 1024  # 1 MB per write to the COPY stream
PROGRESS_EVERY = 100 * CHUNK_SIZE  # log progress every 100 MB

SAML_D_COLUMNS = (
    "Time",
    "Date",
    "Sender_account",
    "Receiver_account",
    "Amount",
    "Payment_currency",
    "Received_currency",
    "Sender_bank_location",
    "Receiver_bank_location",
    "Payment_type",
    "Is_laundering",
    "Laundering_type",
)


def read_header(csv_path: Path) -> list[str]:
    """Return the column names from the first line of a CSV file."""
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def validate_header(
    actual: list[str], expected: tuple[str, ...], file_name: str
) -> None:
    """Stop with a clear error if the CSV header is not exactly the expected columns.

    Raises:
        ValueError: listing missing, unexpected or reordered columns.
    """
    if tuple(actual) == expected:
        return
    missing = [c for c in expected if c not in actual]
    unexpected = [c for c in actual if c not in expected]
    details = []
    if missing:
        details.append(f"missing {missing}")
    if unexpected:
        details.append(f"unexpected {unexpected}")
    if not details:
        details.append(f"same columns in a different order: {actual}")
    raise ValueError(
        f"{file_name}: header does not match the expected {len(expected)} columns ("
        + "; ".join(details)
        + ")"
    )


def create_load_log(cur: psycopg.Cursor) -> None:
    """Create the schema `raw` and the audit table `raw.load_log` if they do not exist."""
    cur.execute("CREATE SCHEMA IF NOT EXISTS raw")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS raw.load_log (
            load_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            table_name  text        NOT NULL,
            file_name   text        NOT NULL,
            row_count   bigint      NOT NULL,
            loaded_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def load_csv(
    conn: psycopg.Connection,
    csv_path: Path,
    table: str,
    expected_columns: tuple[str, ...],
) -> int:
    """Replace `raw.<table>` with the contents of a CSV file and log the load.

    Column names are the CSV header in lower case; every column is text.
    The empty-and-load runs in one transaction: if anything fails, the
    table keeps its previous rows and no log entry is written.

    Returns:
        Number of rows loaded.
    """
    validate_header(read_header(csv_path), expected_columns, csv_path.name)

    target = sql.Identifier("raw", table)
    column_defs = sql.SQL(", ").join(
        sql.SQL("{} text").format(sql.Identifier(c.lower())) for c in expected_columns
    )

    with conn.transaction(), conn.cursor() as cur:
        create_load_log(cur)
        cur.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(target, column_defs)
        )
        cur.execute(sql.SQL("TRUNCATE {}").format(target))

        logger.info("Streaming %s into raw.%s", csv_path.name, table)
        copy_sql = sql.SQL("COPY {} FROM STDIN WITH (FORMAT csv, HEADER true)").format(
            target
        )
        sent = 0
        with cur.copy(copy_sql) as copy, csv_path.open("rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                copy.write(chunk)
                sent += len(chunk)
                if sent % PROGRESS_EVERY == 0:
                    logger.info("  %d MB sent", sent // (1024 * 1024))

        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(target))
        row_count = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO raw.load_log (table_name, file_name, row_count) VALUES (%s, %s, %s)",
            (f"raw.{table}", csv_path.name, row_count),
        )

    logger.info("Loaded %d rows into raw.%s", row_count, table)
    return row_count


def main() -> None:
    """Load SAML-D into `raw.saml_d`."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    with get_connection() as conn:
        load_csv(conn, RAW_DIR / "SAML-D.csv", "saml_d", SAML_D_COLUMNS)


if __name__ == "__main__":
    main()
