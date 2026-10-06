"""Tests for the raw CSV loader's header and column handling (no database needed)."""

import re
from pathlib import Path

import pytest

from aml_transaction_monitoring.ingestion.load_raw import (
    IBM_HI_SMALL_COLUMNS,
    IBM_HI_SMALL_TABLE_COLUMNS,
    SAML_D_COLUMNS,
    UK_SANCTIONS_COLUMNS,
    read_header,
    read_preamble,
    resolve_table_columns,
    to_column_name,
    validate_header,
)


def test_matching_header_passes() -> None:
    validate_header(list(SAML_D_COLUMNS), SAML_D_COLUMNS, "SAML-D.csv")


def test_missing_column_fails() -> None:
    with pytest.raises(ValueError, match="missing \\['Laundering_type'\\]"):
        validate_header(list(SAML_D_COLUMNS[:-1]), SAML_D_COLUMNS, "SAML-D.csv")


def test_reordered_columns_fail() -> None:
    reordered = [SAML_D_COLUMNS[1], SAML_D_COLUMNS[0], *SAML_D_COLUMNS[2:]]
    with pytest.raises(ValueError, match="different order"):
        validate_header(reordered, SAML_D_COLUMNS, "SAML-D.csv")


def test_read_header_ignores_byte_order_mark(tmp_path: Path) -> None:
    csv_file = tmp_path / "bom.csv"
    csv_file.write_text("\ufeffTime,Date\n10:35:19,2022-10-07\n", encoding="utf-8")
    assert read_header(csv_file) == ["Time", "Date"]


def test_column_names_derived_from_header() -> None:
    assert resolve_table_columns(("Time", "From Bank"), None) == ("time", "from_bank")


def test_ibm_duplicate_account_headers_are_rejected_without_explicit_names() -> None:
    with pytest.raises(
        ValueError, match="Duplicate table column names: \\['account'\\]"
    ):
        resolve_table_columns(IBM_HI_SMALL_COLUMNS, None)


def test_ibm_accounts_renamed_by_position() -> None:
    names = resolve_table_columns(IBM_HI_SMALL_COLUMNS, IBM_HI_SMALL_TABLE_COLUMNS)
    assert names[2] == "from_account"
    assert names[4] == "to_account"


def test_wrong_number_of_table_columns_fails() -> None:
    with pytest.raises(ValueError, match="2 table columns given for 3 CSV columns"):
        resolve_table_columns(("A", "B", "C"), ("a", "b"))


def test_punctuation_in_headers_becomes_single_underscores() -> None:
    assert to_column_name("D.O.B") == "d_o_b"
    assert to_column_name("Nationality(/ies)") == "nationality_ies"
    assert to_column_name("Current owner/operator (s)") == "current_owner_operator_s"


def test_sanctions_column_names_are_unique_and_plain() -> None:
    names = resolve_table_columns(UK_SANCTIONS_COLUMNS, None)
    assert len(names) == 58
    assert all(re.fullmatch(r"[a-z0-9_]+", n) for n in names)


def test_preamble_skipped_and_kept(tmp_path: Path) -> None:
    csv_file = tmp_path / "list.csv"
    csv_file.write_text(
        "\ufeffReport Date: 02-Oct-2026\nName 1,Unique ID\nSMITH,UK001\n",
        encoding="utf-8",
    )
    assert read_header(csv_file, skip_lines=1) == ["Name 1", "Unique ID"]
    assert read_preamble(csv_file, skip_lines=1) == "Report Date: 02-Oct-2026"


def test_no_preamble_gives_none(tmp_path: Path) -> None:
    csv_file = tmp_path / "plain.csv"
    csv_file.write_text("Time,Date\n", encoding="utf-8")
    assert read_preamble(csv_file, skip_lines=0) is None
