"""Build the country-risk reference table, `ref.country_risk`.

One row per country, with two separate views of risk in separate columns:

- FATF status, parsed from the saved FATF pages (`raw.fatf_lists`).
- Internal rating from the World Bank Worldwide Governance Indicators
  (`raw.wgi`): the average of three indicators, ranked and split into thirds
  (high / medium / low risk).

Every row carries the FATF list date and source, and the WGI version and
source. The same run writes `ref.country_name_map` (SAML-D and FATF country
names -> ISO code) from the settings, for dbt staging to join on. Hard stop: if any SAML-D bank location, KYC nationality or FATF
jurisdiction has no row (or a SAML-D or KYC country has no internal rating),
the whole load is rolled back with a list of the gaps.

Usage (download WGI first with `... .ingestion.wgi`):
    uv run python -m aml_transaction_monitoring.ingestion.country_risk
"""

import logging
from dataclasses import astuple, dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql

from aml_transaction_monitoring.db import get_connection
from aml_transaction_monitoring.ingestion.fatf_lists import (
    FatfPage,
    normalise_name,
    parse_fatf_page,
)
from aml_transaction_monitoring.ingestion.load_raw import RAW_DIR, create_load_log
from aml_transaction_monitoring.ingestion.wgi import (
    CONFIG_PATH,
    WgiValue,
    read_wgi,
    wgi_file,
)

logger = logging.getLogger(__name__)

FATF_DIR = RAW_DIR / "fatf"

# The table has one column per indicator, so the settings must name these three.
WGI_INDICATOR_NAMES = ("control_of_corruption", "rule_of_law", "regulatory_quality")
FATF_STATUS_BY_LIST = {
    "call_for_action": "call_for_action",
    "increased_monitoring": "increased_monitoring",
}
NOT_LISTED = "not_listed"

SAML_D_LOCATIONS_SQL = """
    SELECT sender_bank_location FROM raw.saml_d
    UNION
    SELECT receiver_bank_location FROM raw.saml_d
"""
KYC_NATIONALITIES_SQL = "SELECT DISTINCT nationality FROM raw.kyc_customers"


@dataclass(frozen=True)
class CountryRisk:
    """One row of ref.country_risk. Field order = table column order."""

    iso_code: str
    country_name: str
    fatf_status: str
    fatf_measure: str | None
    fatf_list_date: date
    fatf_source: str
    wgi_control_of_corruption: float | None
    wgi_rule_of_law: float | None
    wgi_regulatory_quality: float | None
    wgi_average: float | None
    internal_rating: str | None
    wgi_data_year: int
    wgi_version: str
    wgi_source: str


COLUMNS = tuple(f.name for f in fields(CountryRisk))

CREATE_FATF_SQL = """
    CREATE TABLE IF NOT EXISTS raw.fatf_lists (
        list_name     text NOT NULL,
        jurisdiction  text NOT NULL,
        measure       text NOT NULL,
        list_date     date NOT NULL,
        source_url    text NOT NULL,
        source_file   text NOT NULL,
        PRIMARY KEY (list_name, jurisdiction)
    )
"""

CREATE_WGI_SQL = """
    CREATE TABLE IF NOT EXISTS raw.wgi (
        wb_code         text    NOT NULL,
        country_name    text    NOT NULL,
        indicator_id    text    NOT NULL,
        indicator_name  text    NOT NULL,
        data_year       integer NOT NULL,
        value           double precision,
        wgi_version     text    NOT NULL,
        source_file     text    NOT NULL,
        PRIMARY KEY (indicator_id, country_name)
    )
"""

CREATE_COUNTRY_RISK_SQL = """
    CREATE TABLE IF NOT EXISTS ref.country_risk (
        iso_code                   char(2) PRIMARY KEY,
        country_name               text    NOT NULL,
        fatf_status                text    NOT NULL
            CHECK (fatf_status IN ('call_for_action', 'increased_monitoring', 'not_listed')),
        fatf_measure               text
            CHECK (fatf_measure IN
                ('countermeasures', 'enhanced_due_diligence', 'increased_monitoring')),
        fatf_list_date             date    NOT NULL,
        fatf_source                text    NOT NULL,
        wgi_control_of_corruption  double precision,
        wgi_rule_of_law            double precision,
        wgi_regulatory_quality     double precision,
        wgi_average                double precision,
        internal_rating            text CHECK (internal_rating IN ('high', 'medium', 'low')),
        wgi_data_year              integer NOT NULL,
        wgi_version                text    NOT NULL,
        wgi_source                 text    NOT NULL,
        CHECK ((fatf_status = 'not_listed') = (fatf_measure IS NULL))
    )
"""

# Country names (SAML-D, FATF) -> ISO code, copied from the settings so dbt
# can join on it; the YAML stays the only place the mapping is written.
CREATE_NAME_MAP_SQL = """
    CREATE TABLE IF NOT EXISTS ref.country_name_map (
        name_key      text    PRIMARY KEY,
        country_name  text    NOT NULL,
        iso_code      char(2) NOT NULL REFERENCES ref.country_risk (iso_code)
    )
"""
NAME_MAP_COMMENT = (
    "From config/country_risk.yaml country_names. name_key = name in lower case, "
    "single spaces, straight apostrophes (fatf_lists.normalise_name)."
)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read and validate the country-risk settings."""
    with path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    validate_config(config)
    return config


def _is_iso_code(code: Any) -> bool:
    return (
        isinstance(code, str) and len(code) == 2 and code.isalpha() and code.isupper()
    )


def validate_config(config: dict[str, Any]) -> None:
    """Stop with a clear error if the settings are inconsistent.

    Raises:
        ValueError: describing the first problem found.
    """
    unknown_lists = [
        p["list"]
        for p in config["fatf"]["pages"]
        if p["list"] not in FATF_STATUS_BY_LIST
    ]
    if unknown_lists:
        raise ValueError(f"Unknown FATF lists: {unknown_lists}")
    wgi = config["wgi"]
    if tuple(wgi["indicators"].values()) != WGI_INDICATOR_NAMES:
        raise ValueError(
            f"wgi.indicators must be named {WGI_INDICATOR_NAMES} (one table column "
            f"each); changing them needs a table change: {wgi['indicators']}"
        )
    if len(wgi["rating_bands"]) != 3:
        raise ValueError("wgi.rating_bands must have three bands (thirds)")
    codes = {**config["country_names"], **wgi["economies_without_code"]}
    bad = {name: code for name, code in codes.items() if not _is_iso_code(code)}
    if bad:
        raise ValueError(f"Not ISO 2-letter codes (quote them in YAML): {bad}")
    keys = [normalise_name(n) for n in config["country_names"]]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ValueError(f"country_names listed twice: {duplicates}")


def name_to_iso(config: dict[str, Any]) -> dict[str, str]:
    """Return {normalised country name: ISO code} from the settings."""
    return {normalise_name(n): code for n, code in config["country_names"].items()}


def name_map_rows(config: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (name_key, country name as written, ISO code) for ref.country_name_map."""
    return [
        (normalise_name(name), name, code)
        for name, code in config["country_names"].items()
    ]


def read_fatf_pages(
    config: dict[str, Any], fatf_dir: Path = FATF_DIR
) -> list[FatfPage]:
    """Parse every saved FATF page named in the settings."""
    return [
        parse_fatf_page(
            (fatf_dir / p["file"]).read_text(encoding="utf-8"),
            p["list"],
            config["fatf"]["ignore_headings"],
        )
        for p in config["fatf"]["pages"]
    ]


def fatf_by_iso(
    pages: list[FatfPage], names: dict[str, str]
) -> dict[str, tuple[str, str, FatfPage]]:
    """Return {ISO code: (FATF status, measure, page)} for every listed jurisdiction.

    Raises:
        ValueError: if a jurisdiction has no ISO code in the settings, is on
            more than one list, or is also shown as removed.
    """
    unmapped = [n for p in pages for n, _ in p.listed if normalise_name(n) not in names]
    if unmapped:
        raise ValueError(
            f"FATF jurisdictions with no ISO code in country_names: {unmapped}"
        )
    listed: dict[str, tuple[str, str, FatfPage]] = {}
    for page in pages:
        for name, measure in page.listed:
            iso = names[normalise_name(name)]
            if iso in listed:
                raise ValueError(f"{name} ({iso}) is on more than one FATF list")
            listed[iso] = (FATF_STATUS_BY_LIST[page.list_name], measure, page)
    removed = {names.get(normalise_name(n)) for p in pages for n in p.removed}
    both = sorted(removed & listed.keys())
    if both:
        raise ValueError(f"Shown as both listed and removed: {both}")
    return listed


def wgi_by_iso(
    values: list[WgiValue], config: dict[str, Any]
) -> tuple[dict[str, dict[str, float | None]], dict[str, str]]:
    """Group WGI values by ISO code.

    Returns:
        ({ISO code: {indicator name: value}}, {ISO code: WGI country name}).

    Raises:
        ValueError: if a row has no code and is neither mapped nor skipped in
            the settings, or one ISO code has two names.
    """
    wgi = config["wgi"]
    without_code = wgi["economies_without_code"]
    skip = set(wgi["skip_without_code"])
    by_iso: dict[str, dict[str, float | None]] = {}
    country_names: dict[str, str] = {}
    unknown = set()
    for v in values:
        iso = v.wb_code or without_code.get(v.country_name)
        if not iso:
            if v.country_name not in skip:
                unknown.add(v.country_name)
            continue
        if country_names.setdefault(iso, v.country_name) != v.country_name:
            raise ValueError(
                f"{iso} has two names: {country_names[iso]}, {v.country_name}"
            )
        by_iso.setdefault(iso, {})[wgi["indicators"][v.indicator_id]] = v.value
    if unknown:
        raise ValueError(
            f"WGI rows without an ISO code (add to economies_without_code or "
            f"skip_without_code): {sorted(unknown)}"
        )
    return by_iso, country_names


def assign_thirds(averages: dict[str, float], bands: list[str]) -> dict[str, str]:
    """Rank countries by average, lowest first, and split them into equal thirds.

    The lowest third gets `bands[0]` (high risk: weakest governance). Ties are
    broken by ISO code, so the result never depends on input order.
    """
    ranked = sorted(averages, key=lambda iso: (averages[iso], iso))
    n = len(ranked)
    return {iso: bands[i * 3 // n] for i, iso in enumerate(ranked)}


def build_country_risk(
    pages: list[FatfPage], wgi_values: list[WgiValue], config: dict[str, Any]
) -> list[CountryRisk]:
    """Combine the FATF lists and WGI into one row per country, sorted by ISO code.

    Countries come from WGI plus any FATF jurisdiction WGI does not cover.
    A country needs all three indicators to get an internal rating.

    Raises:
        ValueError: if the FATF pages have different list dates, or from the
            FATF and WGI checks above.
    """
    names = name_to_iso(config)
    listed = fatf_by_iso(pages, names)
    list_dates = sorted({p.list_date for p in pages})
    if len(list_dates) != 1:
        raise ValueError(f"FATF pages have different list dates: {list_dates}")
    all_sources = " | ".join(p.source_url for p in pages)

    wgi = config["wgi"]
    indicators, wgi_names = wgi_by_iso(wgi_values, config)
    averages = {
        iso: sum(vals[n] for n in WGI_INDICATOR_NAMES) / len(WGI_INDICATOR_NAMES)
        for iso, vals in indicators.items()
        if all(vals.get(n) is not None for n in WGI_INDICATOR_NAMES)
    }
    ratings = assign_thirds(averages, wgi["rating_bands"])
    config_names = {
        code: name for name, code in reversed(config["country_names"].items())
    }

    rows = []
    for iso in sorted(indicators.keys() | listed.keys()):
        status, measure, page = listed.get(iso, (NOT_LISTED, None, None))
        vals = indicators.get(iso, {})
        rows.append(
            CountryRisk(
                iso_code=iso,
                country_name=wgi_names.get(iso) or config_names[iso],
                fatf_status=status,
                fatf_measure=measure,
                fatf_list_date=page.list_date if page else list_dates[0],
                # Listed: the page that lists it. Not listed: absent from both pages.
                fatf_source=page.source_url if page else all_sources,
                wgi_control_of_corruption=vals.get("control_of_corruption"),
                wgi_rule_of_law=vals.get("rule_of_law"),
                wgi_regulatory_quality=vals.get("regulatory_quality"),
                wgi_average=averages.get(iso),
                internal_rating=ratings.get(iso),
                wgi_data_year=wgi["data_year"],
                wgi_version=wgi_values[0].wgi_version,
                wgi_source=wgi["source_page"],
            )
        )
    return rows


def find_coverage_gaps(
    rows: list[CountryRisk],
    saml_d_locations: list[str],
    kyc_nationalities: list[str],
    fatf_codes: list[str],
    names: dict[str, str],
) -> list[str]:
    """List every country we need that has no row (or no internal rating).

    SAML-D locations and KYC nationalities drive transaction and customer
    risk, so they also need an internal rating. FATF jurisdictions need a
    row; a few (e.g. Virgin Islands (UK)) have no WGI data to rate them.

    Returns:
        One message per gap; empty if every country is covered.
    """
    by_iso = {r.iso_code: r for r in rows}
    gaps = []

    def check(label: str, iso: str, needs_rating: bool) -> None:
        if iso not in by_iso:
            gaps.append(f"{label} ({iso}) has no row in ref.country_risk")
        elif needs_rating and by_iso[iso].internal_rating is None:
            gaps.append(f"{label} ({iso}) has no internal rating")

    for location in sorted(saml_d_locations):
        iso = names.get(normalise_name(location))
        if iso is None:
            gaps.append(
                f"SAML-D location {location!r} has no ISO code in country_names"
            )
        else:
            check(f"SAML-D location {location!r}", iso, needs_rating=True)
    for code in sorted(kyc_nationalities):
        check("KYC nationality", code, needs_rating=True)
    for code in sorted(fatf_codes):
        check("FATF jurisdiction", code, needs_rating=False)
    return gaps


def load_country_risk(
    conn: psycopg.Connection,
    pages: list[FatfPage],
    config: dict[str, Any],
    wgi_values: list[WgiValue],
    rows: list[CountryRisk],
) -> int:
    """Replace raw.fatf_lists, raw.wgi, ref.country_risk and ref.country_name_map.

    Everything runs in one transaction.

    The coverage check runs inside the transaction: if any country is
    missing, nothing is saved and the previous tables stay as they were.

    Returns:
        Number of rows in ref.country_risk.

    Raises:
        RuntimeError: listing every coverage gap.
    """
    file_by_list = {p["list"]: p["file"] for p in config["fatf"]["pages"]}
    year = config["wgi"]["data_year"]
    wgi_files = [wgi_file(i, year).name for i in config["wgi"]["indicators"]]
    fatf_rows = [
        (
            p.list_name,
            name,
            measure,
            p.list_date,
            p.source_url,
            file_by_list[p.list_name],
        )
        for p in pages
        for name, measure in p.listed
    ]
    wgi_rows = [
        (
            v.wb_code,
            v.country_name,
            v.indicator_id,
            v.indicator_name,
            v.data_year,
            v.value,
            v.wgi_version,
            wgi_file(v.indicator_id, year).name,
        )
        for v in wgi_values
    ]
    removed = [n for p in pages for n in p.removed]
    fatf_note = (
        f"FATF lists dated {pages[0].list_date}; "
        + "; ".join(p.source_url for p in pages)
        + f"; shown as removed, not loaded: {', '.join(removed) or 'none'}"
    )
    wgi_note = (
        f"WGI version {wgi_values[0].wgi_version}, data year {year}, CC BY 4.0; "
        + ", ".join(config["wgi"]["indicators"])
    )
    ref_note = (
        f"country_risk config v{config['version']}; FATF {pages[0].list_date}; "
        f"WGI {wgi_values[0].wgi_version}"
    )
    placeholders = ", ".join(["%s"] * len(COLUMNS))

    with conn.transaction(), conn.cursor() as cur:
        create_load_log(cur)
        cur.execute("CREATE SCHEMA IF NOT EXISTS ref")
        for create in (
            CREATE_FATF_SQL,
            CREATE_WGI_SQL,
            CREATE_COUNTRY_RISK_SQL,
            CREATE_NAME_MAP_SQL,
        ):
            cur.execute(create)
        cur.execute(
            sql.SQL("COMMENT ON TABLE ref.country_name_map IS {}").format(
                sql.Literal(NAME_MAP_COMMENT)
            )
        )
        cur.execute(
            "TRUNCATE raw.fatf_lists, raw.wgi, ref.country_risk, ref.country_name_map"
        )
        cur.executemany(
            "INSERT INTO raw.fatf_lists VALUES (%s, %s, %s, %s, %s, %s)", fatf_rows
        )
        cur.executemany(
            "INSERT INTO raw.wgi VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", wgi_rows
        )
        cur.executemany(
            f"INSERT INTO ref.country_risk ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            [astuple(r) for r in rows],
        )
        # After ref.country_risk: the foreign key rejects a code with no row.
        map_rows = name_map_rows(config)
        cur.executemany(
            "INSERT INTO ref.country_name_map (name_key, country_name, iso_code)"
            " VALUES (%s, %s, %s)",
            map_rows,
        )

        cur.execute(SAML_D_LOCATIONS_SQL)
        locations = [r[0] for r in cur.fetchall()]
        cur.execute(KYC_NATIONALITIES_SQL)
        nationalities = [r[0] for r in cur.fetchall()]
        # Read from the pages again, not from `rows`, so the check is independent.
        names = name_to_iso(config)
        fatf_codes = list(fatf_by_iso(pages, names))
        gaps = find_coverage_gaps(rows, locations, nationalities, fatf_codes, names)
        if gaps:
            # Raising inside the transaction rolls back every table above.
            raise RuntimeError(
                "Country coverage check failed, nothing saved:\n  " + "\n  ".join(gaps)
            )
        logger.info(
            "Coverage check passed: %d SAML-D locations, %d KYC nationalities, "
            "%d FATF jurisdictions",
            len(locations),
            len(nationalities),
            len(fatf_codes),
        )

        log_sql = (
            "INSERT INTO raw.load_log (table_name, file_name, row_count, source_note)"
            " VALUES (%s, %s, %s, %s)"
        )
        cur.execute(
            log_sql,
            (
                "raw.fatf_lists",
                ", ".join(file_by_list.values()),
                len(fatf_rows),
                fatf_note,
            ),
        )
        cur.execute(log_sql, ("raw.wgi", ", ".join(wgi_files), len(wgi_rows), wgi_note))
        cur.execute(
            log_sql,
            ("ref.country_risk", "config/country_risk.yaml", len(rows), ref_note),
        )
        cur.execute(
            log_sql,
            (
                "ref.country_name_map",
                "config/country_risk.yaml",
                len(map_rows),
                f"country_risk config v{config['version']}; country_names",
            ),
        )

    logger.info(
        "Loaded %d FATF rows, %d WGI values, %d countries",
        len(fatf_rows),
        len(wgi_rows),
        len(rows),
    )
    return len(rows)


def main() -> None:
    """Parse the FATF pages and WGI files, build ref.country_risk and check coverage."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    config = load_config()
    pages = read_fatf_pages(config)
    for page in pages:
        logger.info(
            "%s (%s): %d listed, removed: %s",
            page.list_name,
            page.list_date,
            len(page.listed),
            ", ".join(page.removed) or "none",
        )
    wgi_values = read_wgi(config)
    rows = build_country_risk(pages, wgi_values, config)
    unrated = [
        f"{r.country_name} ({r.iso_code})" for r in rows if not r.internal_rating
    ]
    logger.info("Countries without an internal rating: %s", ", ".join(unrated))
    with get_connection() as conn:
        load_country_risk(conn, pages, config, wgi_values, rows)


if __name__ == "__main__":
    main()
