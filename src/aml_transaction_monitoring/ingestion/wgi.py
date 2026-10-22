"""Download and read the World Bank Worldwide Governance Indicators (WGI).

WGI data are published under CC BY 4.0. The three indicators and the data
year are set in `config/country_risk.yaml`. Downloading saves the World Bank
API's JSON responses unchanged into `data/raw/wgi/`, so the build always reads
a saved file that can be traced back to its source, like the raw CSVs.

Usage (download only; the build reads the saved files):
    uv run python -m aml_transaction_monitoring.ingestion.wgi
"""

import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT, RAW_DIR

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "country_risk.yaml"
WGI_DIR = RAW_DIR / "wgi"
WGI_SOURCE_ID = "3"  # World Bank API data source 3 = Worldwide Governance Indicators
TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class WgiValue:
    """One indicator value for one economy, as published."""

    wb_code: str  # World Bank 2-letter code; empty for a few economies
    country_name: str
    indicator_id: str
    indicator_name: str
    data_year: int
    value: float | None
    wgi_version: str  # the source's "last updated" date


def wgi_file(indicator: str, year: int, wgi_dir: Path = WGI_DIR) -> Path:
    """Return where the saved API response for one indicator lives."""
    return wgi_dir / f"wgi_{indicator}_{year}.json"


def download_wgi(config: dict[str, Any], wgi_dir: Path = WGI_DIR) -> list[Path]:
    """Save the API response for each configured indicator; return the file paths."""
    wgi = config["wgi"]
    wgi_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for indicator in wgi["indicators"]:
        url = wgi["api_url"].format(indicator=indicator, year=wgi["data_year"])
        logger.info("Downloading %s", url)
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
        parse_wgi_response(json.loads(body), indicator, wgi["data_year"])  # fail early
        path = wgi_file(indicator, wgi["data_year"], wgi_dir)
        path.write_bytes(body)
        logger.info("Saved %s", path.relative_to(PROJECT_ROOT))
        paths.append(path)
    return paths


def parse_wgi_response(response: Any, indicator: str, year: int) -> list[WgiValue]:
    """Turn one saved API response into values, checking it is what we asked for.

    Raises:
        ValueError: if the response is an API error, not WGI, has more than
            one page, or holds another indicator or year.
    """
    if not (isinstance(response, list) and len(response) == 2):
        raise ValueError(f"{indicator}: not a data response: {str(response)[:200]}")
    meta, rows = response
    if meta.get("sourceid") != WGI_SOURCE_ID:
        raise ValueError(f"{indicator}: source is {meta.get('sourceid')!r}, not WGI")
    if meta.get("pages") != 1 or len(rows) != meta.get("total"):
        raise ValueError(f"{indicator}: response is incomplete ({meta})")
    values = []
    for row in rows:
        if row["indicator"]["id"] != indicator or row["date"] != str(year):
            raise ValueError(
                f"{indicator}: found {row['indicator']['id']} for {row['date']}"
            )
        values.append(
            WgiValue(
                wb_code=row["country"]["id"],
                country_name=row["country"]["value"],
                indicator_id=indicator,
                indicator_name=row["indicator"]["value"],
                data_year=year,
                value=row["value"],
                wgi_version=meta["lastupdated"],
            )
        )
    return values


def read_wgi(config: dict[str, Any], wgi_dir: Path = WGI_DIR) -> list[WgiValue]:
    """Read every configured indicator from its saved file.

    Raises:
        FileNotFoundError: if an indicator has not been downloaded yet.
        ValueError: if the files come from different WGI releases.
    """
    year = config["wgi"]["data_year"]
    values = []
    for indicator in config["wgi"]["indicators"]:
        path = wgi_file(indicator, year, wgi_dir)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found: run `uv run python -m "
                "aml_transaction_monitoring.ingestion.wgi` first"
            )
        response = json.loads(path.read_text(encoding="utf-8"))
        values.extend(parse_wgi_response(response, indicator, year))
    versions = sorted({v.wgi_version for v in values})
    if len(versions) != 1:
        raise ValueError(f"WGI files come from different releases: {versions}")
    return values


def main() -> None:
    """Download the configured WGI indicators into data/raw/wgi/."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    download_wgi(config)


if __name__ == "__main__":
    main()
