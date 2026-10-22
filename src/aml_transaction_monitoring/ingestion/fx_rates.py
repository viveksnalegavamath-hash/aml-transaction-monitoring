"""Download and load HMRC monthly exchange rates into `raw.fx_rates_hmrc`.

Not applied to SAML-D, whose amounts are already on one scale (treated as
GBP); kept for IBM HI-Small. HMRC publishes one rate per currency per month, as currency units per £1, under
the Open Government Licence v3.0. Settings: `config/fx_rates.yaml`.

Each monthly CSV is saved unchanged into `data/raw/fx/` (once; an existing
file is never overwritten), then checked and loaded. Every month loads in one
transaction with a `raw.load_log` entry, like the other raw data.

Usage:
    uv run python -m aml_transaction_monitoring.ingestion.fx_rates
"""

import csv
import logging
import urllib.request
from dataclasses import astuple, dataclass, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg
import yaml

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ingestion.load_raw import (
    PROJECT_ROOT,
    RAW_DIR,
    create_load_log,
    validate_header,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "fx_rates.yaml"
FX_DIR = RAW_DIR / "fx"
TIMEOUT_SECONDS = 60

HMRC_COLUMNS = (
    "Country/Territories",
    "Currency",
    "Currency Code",
    "Currency Units per £1",
    "Start date",
    "End date",
)


@dataclass(frozen=True)
class FxRate:
    """One row of an HMRC monthly file. Field order = table column order."""

    country_territory: str
    currency_name: str
    currency_code: str
    units_per_gbp: Decimal
    start_date: date
    end_date: date
    source_file: str


COLUMNS = tuple(f.name for f in fields(FxRate))

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS raw.fx_rates_hmrc (
        country_territory  text    NOT NULL,
        currency_name      text    NOT NULL,
        currency_code      char(3) NOT NULL,
        units_per_gbp      numeric NOT NULL CHECK (units_per_gbp > 0),
        start_date         date    NOT NULL,
        end_date           date    NOT NULL,
        source_file        text    NOT NULL,
        PRIMARY KEY (country_territory, currency_code, start_date)
    )
"""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read the FX settings."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def months_between(first: str, last: str) -> list[str]:
    """Return every month from `first` to `last` inclusive, as "YYYY-MM".

    Raises:
        ValueError: if `first` is after `last`.
    """
    year, month = map(int, first.split("-"))
    end = tuple(map(int, last.split("-")))
    if (year, month) > end:
        raise ValueError(f"first_month {first} is after last_month {last}")
    months = []
    while (year, month) <= end:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def fx_file(month: str, fx_dir: Path = FX_DIR) -> Path:
    """Return where the saved HMRC file for one month lives."""
    return fx_dir / f"hmrc_monthly_{month}.csv"


def download_month(config: dict[str, Any], month: str, fx_dir: Path = FX_DIR) -> Path:
    """Save one month's HMRC file, unless it is already saved; return its path."""
    path = fx_file(month, fx_dir)
    if path.exists():
        logger.info("Already saved: %s", path.relative_to(PROJECT_ROOT))
        return path
    url = config["file_url"].format(month=month)
    logger.info("Downloading %s", url)
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
    fx_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%d/%m/%Y").date()  # noqa: DTZ007 - calendar date


def parse_month(path: Path, month: str) -> list[FxRate]:
    """Read and check one saved HMRC monthly file.

    Raises:
        ValueError: if the header is not HMRC's, a rate is not a positive
            number, a code is not 3 letters, or a row is not for `month`.
    """
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        validate_header(next(reader), HMRC_COLUMNS, path.name)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    expected_start = date.fromisoformat(f"{month}-01")
    rates = []
    for line, row in enumerate(rows, start=2):
        country, currency, code, rate_text, start_text, end_text = row
        try:
            rate = Decimal(rate_text)
        except InvalidOperation as e:
            raise ValueError(f"{path.name} line {line}: rate {rate_text!r}") from e
        start, end = _parse_date(start_text), _parse_date(end_text)
        code = code.strip()
        if not (len(code) == 3 and code.isalpha() and code.isupper()):
            raise ValueError(f"{path.name} line {line}: currency code {code!r}")
        if rate <= 0:
            raise ValueError(f"{path.name} line {line}: rate {rate} is not positive")
        if start != expected_start or end < start or end.month != start.month:
            raise ValueError(
                f"{path.name} line {line}: {start} to {end} is not the month {month}"
            )
        rates.append(
            FxRate(
                # Collapse double spaces in names (e.g. "Turkish  Lira").
                country_territory=" ".join(country.split()),
                currency_name=" ".join(currency.split()),
                currency_code=code,
                units_per_gbp=rate,
                start_date=start,
                end_date=end,
                source_file=path.name,
            )
        )
    if not rates:
        raise ValueError(f"{path.name}: no rates")
    return rates


def load_rates(
    conn: psycopg.Connection, rates: list[FxRate], config: dict[str, Any]
) -> int:
    """Replace `raw.fx_rates_hmrc` with `rates` and log the load, in one transaction.

    Returns:
        Number of rows loaded.
    """
    months = f"{config['first_month']} to {config['last_month']}"
    note = (
        f"HMRC monthly exchange rates {months}; {config['source_page']}; "
        f"{config['licence']}; one rate per currency per month"
    )
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    with conn.transaction(), conn.cursor() as cur:
        create_load_log(cur)
        cur.execute(CREATE_TABLE_SQL)
        cur.execute("TRUNCATE raw.fx_rates_hmrc")
        cur.executemany(
            f"INSERT INTO raw.fx_rates_hmrc ({', '.join(COLUMNS)})"
            f" VALUES ({placeholders})",
            [astuple(r) for r in rates],
        )
        cur.execute("SELECT count(*) FROM raw.fx_rates_hmrc")
        row_count = cur.fetchone()[0]
        files = sorted({r.source_file for r in rates})
        cur.execute(
            "INSERT INTO raw.load_log (table_name, file_name, row_count, source_note)"
            " VALUES (%s, %s, %s, %s)",
            ("raw.fx_rates_hmrc", f"{files[0]} ... {files[-1]}", row_count, note),
        )
    logger.info("Loaded %d rates from %d files", row_count, len(files))
    return row_count


def main() -> None:
    """Download any missing monthly files, then load every month."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = load_config()
    rates = []
    for month in months_between(config["first_month"], config["last_month"]):
        path = download_month(config, month)
        rates.extend(parse_month(path, month))
    with get_connection() as conn:
        load_rates(conn, rates, config)


if __name__ == "__main__":
    main()
