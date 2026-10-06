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

IBM_HI_SMALL_COLUMNS = (
    "Timestamp",
    "From Bank",
    "Account",
    "To Bank",
    "Account",
    "Amount Received",
    "Receiving Currency",
    "Amount Paid",
    "Payment Currency",
    "Payment Format",
    "Is Laundering",
)
# The file has two columns both called "Account"; name them by position.
IBM_HI_SMALL_TABLE_COLUMNS = (
    "timestamp",
    "from_bank",
    "from_account",
    "to_bank",
    "to_account",
    "amount_received",
    "receiving_currency",
    "amount_paid",
    "payment_currency",
    "payment_format",
    "is_laundering",
)


def to_column_name(header: str) -> str:
    """Turn a CSV header into a database column name: lower case, spaces to underscores."""
    return header.strip().lower().replace(" ", "_")


def resolve_table_columns(
    expected_columns: tuple[str, ...], table_columns: tuple[str, ...] | None
) -> tuple[str, ...]:
    """Return the table's column names, one per CSV column, all unique.

    If `table_columns` is not given, names are derived from the CSV header.

    Raises:
        ValueError: if the counts differ or a name appears twice.
    """
    names = table_columns or tuple(to_column_name(c) for c in expected_columns)
    if len(names) != len(expected_columns):
        raise ValueError(
            f"{len(names)} table columns given for {len(expected_columns)} CSV columns"
        )
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Duplicate table column names: {duplicates}")
    return names


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
    table_columns: tuple[str, ...] | None = None,
) -> int:
    """Replace `raw.<table>` with the contents of a CSV file and log the load.

    Column names default to the CSV header in lower case with underscores;
    pass `table_columns` to name them explicitly. Every column is text.
    The empty-and-load runs in one transaction: if anything fails, the
    table keeps its previous rows and no log entry is written.

    Returns:
        Number of rows loaded.
    """
    validate_header(read_header(csv_path), expected_columns, csv_path.name)
    names = resolve_table_columns(expected_columns, table_columns)

    target = sql.Identifier("raw", table)
    column_defs = sql.SQL(", ").join(
        sql.SQL("{} text").format(sql.Identifier(n)) for n in names
    )
    column_list = sql.SQL(", ").join(sql.Identifier(n) for n in names)

    with conn.transaction(), conn.cursor() as cur:
        create_load_log(cur)
        cur.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(target, column_defs)
        )
        cur.execute(sql.SQL("TRUNCATE {}").format(target))

        logger.info("Streaming %s into raw.%s", csv_path.name, table)
        # Naming the columns makes COPY fail loudly if an existing table's
        # columns no longer match, instead of filling them by position.
        copy_sql = sql.SQL(
            "COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)"
        ).format(target, column_list)
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
    """Load SAML-D into `raw.saml_d` and IBM HI-Small into `raw.ibm_hi_small`."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    with get_connection() as conn:
        load_csv(conn, RAW_DIR / "SAML-D.csv", "saml_d", SAML_D_COLUMNS)
        load_csv(
            conn,
            RAW_DIR / "HI-Small_Trans.csv",
            "ibm_hi_small",
            IBM_HI_SMALL_COLUMNS,
            IBM_HI_SMALL_TABLE_COLUMNS,
        )


if __name__ == "__main__":
    main()
