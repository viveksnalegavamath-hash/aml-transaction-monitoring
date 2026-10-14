"""Generate the SYNTHETIC KYC customer table, `raw.kyc_customers`.

Every record is made up with Faker and NumPy from a fixed seed; none
describes a real person or company. One customer per account that sends
money in SAML-D.

No label leakage: the only thing read from the transaction data is the list
of customer IDs. Every attribute comes from the fixed distributions in
`config/kyc_generator.yaml`, never from transactions or from the suspicious
label. `tests/test_kyc_generator.py` enforces this.

Usage:
    uv run python -m aml_transaction_monitoring.ingestion.kyc_generator
"""

import logging
import math
from dataclasses import astuple, dataclass, fields
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import yaml
from faker import Faker
from psycopg import sql

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT, create_load_log

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "kyc_generator.yaml"
DAYS_PER_YEAR = 365.25

# The generator's one and only read from the transaction data.
CUSTOMER_IDS_SQL = "SELECT DISTINCT sender_account FROM raw.saml_d"


@dataclass(frozen=True)
class KycRecord:
    """One synthetic customer. Field order = table column order."""

    customer_id: str
    customer_type: str
    full_name: str
    date_of_birth: date | None
    nationality: str
    occupation: str
    declared_annual_income_gbp: int
    pep_flag: bool
    onboarding_date: date
    is_synthetic: bool = True


COLUMNS = tuple(f.name for f in fields(KycRecord))

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS raw.kyc_customers (
        customer_id                 text    PRIMARY KEY,
        customer_type               text    NOT NULL,
        full_name                   text    NOT NULL,
        date_of_birth               date,
        nationality                 char(2) NOT NULL,
        occupation                  text    NOT NULL,
        declared_annual_income_gbp  bigint  NOT NULL,
        pep_flag                    boolean NOT NULL,
        onboarding_date             date    NOT NULL,
        is_synthetic                boolean NOT NULL CHECK (is_synthetic)
    )
"""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read and validate the generator settings."""
    with path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    """Stop with a clear error if the settings are inconsistent.

    Raises:
        ValueError: describing the first problem found.
    """
    shares = config["customer_type_shares"]
    if set(shares) != {"individual", "business"}:
        raise ValueError(
            f"customer_type_shares must be individual and business: {shares}"
        )
    if not math.isclose(sum(shares.values()), 1.0):
        raise ValueError(f"customer_type_shares must add up to 1: {shares}")
    if not 0 <= config["pep_share_of_individuals"] <= 1:
        raise ValueError("pep_share_of_individuals must be between 0 and 1")
    if not 0 <= config["nationality"]["home_share"] <= 1:
        raise ValueError("nationality.home_share must be between 0 and 1")
    codes = [config["nationality"]["home"], *config["nationality"]["others"]]
    bad_codes = [c for c in codes if not (len(c) == 2 and c.isalpha() and c.isupper())]
    if bad_codes:
        raise ValueError(f"Nationalities must be ISO 2-letter codes: {bad_codes}")
    if config["onboarding"]["start"] >= config["onboarding"]["end"]:
        raise ValueError("onboarding.start must be before onboarding.end")
    ages = config["age_at_onboarding"]
    if not 18 <= ages["min_years"] < ages["max_years"]:
        raise ValueError("age_at_onboarding must have 18 <= min_years < max_years")
    for customer_type, occupations in config["occupations"].items():
        if any(o["weight"] <= 0 or o["median_income"] <= 0 for o in occupations):
            raise ValueError(
                f"{customer_type} occupations need positive weights and incomes"
            )


def fetch_customer_ids(conn: psycopg.Connection) -> list[str]:
    """Return the customer IDs: every account that sends money in SAML-D."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(CUSTOMER_IDS_SQL)
        return [row[0] for row in cur]


def _draw_occupations(
    rng: np.random.Generator, occupations: list[dict[str, Any]], n: int
) -> tuple[np.ndarray, np.ndarray]:
    """Draw an occupation per row by weight; return names and their median incomes."""
    weights = np.array([o["weight"] for o in occupations], dtype=float)
    picks = rng.choice(len(occupations), size=n, p=weights / weights.sum())
    names = np.array([o["name"] for o in occupations])[picks]
    medians = np.array([o["median_income"] for o in occupations], dtype=float)[picks]
    return names, medians


def _round_to(values: np.ndarray, step: int) -> np.ndarray:
    """Round to the nearest `step`, never below one step."""
    return np.maximum(np.round(values / step) * step, step).astype(int)


def generate_customers(
    account_ids: list[str], config: dict[str, Any]
) -> list[KycRecord]:
    """Generate one synthetic customer per account ID.

    Deterministic: IDs are sorted first, and NumPy and Faker are seeded from
    `config["seed"]`, so the same IDs and settings always give the same records.
    The account IDs are used only as keys; no attribute depends on them.
    """
    ids = sorted(account_ids)
    n = len(ids)
    seed = config["seed"]
    rng = np.random.default_rng(seed)
    fake = Faker(config["locale"])
    fake.seed_instance(seed)

    # Draw every attribute for every row, in a fixed order, so that results
    # never depend on how many rows of each type came before.
    shares = config["customer_type_shares"]
    is_individual = rng.random(n) < shares["individual"]

    nat = config["nationality"]
    nationality = np.where(
        rng.random(n) < nat["home_share"],
        nat["home"],
        rng.choice(nat["others"], size=n),
    )

    start, end = config["onboarding"]["start"], config["onboarding"]["end"]
    onboarding_offset = rng.integers(0, (end - start).days + 1, size=n)

    ages = config["age_at_onboarding"]
    age_days = rng.integers(
        math.ceil(ages["min_years"] * DAYS_PER_YEAR),
        math.floor(ages["max_years"] * DAYS_PER_YEAR) + 1,
        size=n,
    )

    pep = rng.random(n) < config["pep_share_of_individuals"]

    ind_occ, ind_median = _draw_occupations(rng, config["occupations"]["individual"], n)
    bus_occ, bus_median = _draw_occupations(rng, config["occupations"]["business"], n)

    z = rng.standard_normal(n)
    inc = config["income"]
    ind_income = _round_to(
        ind_median * np.exp(inc["individual"]["sigma"] * z),
        inc["individual"]["round_to"],
    )
    bus_income = _round_to(
        bus_median * np.exp(inc["business"]["sigma"] * z), inc["business"]["round_to"]
    )

    records = []
    for i, customer_id in enumerate(ids):
        individual = bool(is_individual[i])
        onboarding = start + timedelta(days=int(onboarding_offset[i]))
        records.append(
            KycRecord(
                customer_id=customer_id,
                customer_type="individual" if individual else "business",
                # First + last name only: titles like "Dr" would add noise to
                # sanctions name screening.
                full_name=(
                    f"{fake.first_name()} {fake.last_name()}"
                    if individual
                    else fake.company()
                ),
                date_of_birth=(
                    onboarding - timedelta(days=int(age_days[i]))
                    if individual
                    else None
                ),
                nationality=str(nationality[i]),
                occupation=str(ind_occ[i] if individual else bus_occ[i]),
                declared_annual_income_gbp=int(
                    ind_income[i] if individual else bus_income[i]
                ),
                pep_flag=bool(pep[i]) and individual,
                onboarding_date=onboarding,
            )
        )
    return records


def load_customers(
    conn: psycopg.Connection, records: list[KycRecord], config: dict[str, Any]
) -> int:
    """Replace `raw.kyc_customers` with `records` and log the load, in one transaction.

    Returns:
        Number of rows loaded.
    """
    note = (
        f"SYNTHETIC - kyc_generator config v{config['version']}, "
        f"seed {config['seed']}, Faker {version('faker')}"
    )
    comment = (
        f"SYNTHETIC - generated with Faker, seed {config['seed']}, not real people"
    )
    column_list = sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS)

    with conn.transaction(), conn.cursor() as cur:
        create_load_log(cur)
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(
            sql.SQL("COMMENT ON TABLE raw.kyc_customers IS {}").format(
                sql.Literal(comment)
            )
        )
        cur.execute("TRUNCATE raw.kyc_customers")
        copy_sql = sql.SQL("COPY raw.kyc_customers ({}) FROM STDIN").format(column_list)
        with cur.copy(copy_sql) as copy:
            for record in records:
                copy.write_row(astuple(record))

        cur.execute("SELECT count(*) FROM raw.kyc_customers")
        row_count = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO raw.load_log (table_name, file_name, row_count, source_note)"
            " VALUES (%s, %s, %s, %s)",
            ("raw.kyc_customers", "config/kyc_generator.yaml", row_count, note),
        )

    logger.info("Loaded %d synthetic customers into raw.kyc_customers", row_count)
    return row_count


def main() -> None:
    """Generate the synthetic KYC table and load it into `raw.kyc_customers`."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = load_config()
    with get_connection() as conn:
        ids = fetch_customer_ids(conn)
        logger.info(
            "Generating %d synthetic customers (seed %d)", len(ids), config["seed"]
        )
        records = generate_customers(ids, config)
        load_customers(conn, records, config)


if __name__ == "__main__":
    main()
