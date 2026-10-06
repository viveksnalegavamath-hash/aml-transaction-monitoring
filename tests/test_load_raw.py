"""Tests for the raw CSV loader's header check (no database needed)."""

from pathlib import Path

import pytest

from aml_transaction_monitoring.ingestion.load_raw import (
    SAML_D_COLUMNS,
    read_header,
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
    csv_file.write_text("﻿Time,Date\n10:35:19,2022-10-07\n", encoding="utf-8")
    assert read_header(csv_file) == ["Time", "Date"]
