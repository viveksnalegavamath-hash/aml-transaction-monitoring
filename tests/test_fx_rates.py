"""Tests for the HMRC FX rate loader (no database or network needed)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from aml_transaction_monitoring.ingestion.fx_rates import (
    load_config,
    months_between,
    parse_month,
)

HEADER = (
    "Country/Territories,Currency,Currency Code,Currency Units per £1,"
    "Start date,End date\n"
)


def write_csv(tmp_path: Path, body: str, header: str = HEADER) -> Path:
    path = tmp_path / "hmrc_monthly_2022-10.csv"
    path.write_text(header + body, encoding="utf-8")
    return path


def test_months_cover_the_whole_saml_d_period() -> None:
    config = load_config()
    months = months_between(config["first_month"], config["last_month"])
    assert months[0] == "2022-10" and months[-1] == "2023-08"
    assert len(months) == 11


def test_months_cross_the_year_end() -> None:
    assert months_between("2022-11", "2023-02") == [
        "2022-11",
        "2022-12",
        "2023-01",
        "2023-02",
    ]


def test_months_in_wrong_order_fail() -> None:
    with pytest.raises(ValueError, match="is after"):
        months_between("2023-08", "2022-10")


def test_rates_are_read_and_names_tidied(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "Turkey,Turkish  Lira,TRY,20.7666,01/10/2022,31/10/2022\n"
        "UAE,Dirham,AED,4.1621,01/10/2022,31/10/2022\n",
    )
    turkey, uae = parse_month(path, "2022-10")
    assert turkey.currency_name == "Turkish Lira"
    assert turkey.units_per_gbp == Decimal("20.7666")
    assert (uae.currency_code, uae.start_date, uae.end_date) == (
        "AED",
        date(2022, 10, 1),
        date(2022, 10, 31),
    )
    assert uae.source_file == "hmrc_monthly_2022-10.csv"


def test_changed_header_fails(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "", header="Country,Currency,Code,Rate,Start,End\n")
    with pytest.raises(ValueError, match="header does not match"):
        parse_month(path, "2022-10")


def test_row_for_another_month_fails(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "UAE,Dirham,AED,4.16,01/11/2022,30/11/2022\n")
    with pytest.raises(ValueError, match="is not the month 2022-10"):
        parse_month(path, "2022-10")


def test_non_numeric_rate_fails(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "UAE,Dirham,AED,n/a,01/10/2022,31/10/2022\n")
    with pytest.raises(ValueError, match="rate 'n/a'"):
        parse_month(path, "2022-10")


def test_zero_rate_fails(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "UAE,Dirham,AED,0,01/10/2022,31/10/2022\n")
    with pytest.raises(ValueError, match="not positive"):
        parse_month(path, "2022-10")


def test_empty_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no rates"):
        parse_month(write_csv(tmp_path, ""), "2022-10")
