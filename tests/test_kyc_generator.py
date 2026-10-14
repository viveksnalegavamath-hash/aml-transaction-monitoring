"""Tests for the synthetic KYC generator (no database needed)."""

import copy
import inspect
import re
from datetime import date
from pathlib import Path

import pytest

from aml_transaction_monitoring.ingestion import kyc_generator
from aml_transaction_monitoring.ingestion.kyc_generator import (
    CONFIG_PATH,
    CUSTOMER_IDS_SQL,
    generate_customers,
    load_config,
    validate_config,
)

IDS = [f"{n:010d}" for n in range(1_000_000_000, 1_000_000_000 + 20_000, 7)]


@pytest.fixture(scope="module")
def config() -> dict:
    return load_config()


@pytest.fixture(scope="module")
def records(config: dict) -> list:
    return generate_customers(IDS, config)


def _age_in_years(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


# --- No label leakage ------------------------------------------------------


def test_generator_never_mentions_the_label() -> None:
    for path in (Path(inspect.getsourcefile(kyc_generator)), CONFIG_PATH):
        text = path.read_text(encoding="utf-8").lower()
        assert "laundering" not in text, f"{path} mentions the label"


def test_only_customer_ids_are_read_from_transactions() -> None:
    source = inspect.getsource(kyc_generator)
    assert source.count("raw.saml_d") == 1
    assert CUSTOMER_IDS_SQL == "SELECT DISTINCT sender_account FROM raw.saml_d"


def test_generator_takes_only_ids_and_settings() -> None:
    assert list(inspect.signature(generate_customers).parameters) == [
        "account_ids",
        "config",
    ]


# --- Reproducibility ---------------------------------------------------------


def test_same_seed_gives_identical_records(config: dict, records: list) -> None:
    assert generate_customers(list(reversed(IDS)), config) == records


def test_different_seed_gives_different_records(config: dict, records: list) -> None:
    other = copy.deepcopy(config)
    other["seed"] += 1
    assert generate_customers(IDS, other) != records


# --- Valid values -------------------------------------------------------------


def test_one_record_per_id_all_synthetic(records: list) -> None:
    assert sorted(r.customer_id for r in records) == sorted(IDS)
    assert all(r.is_synthetic for r in records)


def test_individuals_are_adults_at_onboarding(records: list) -> None:
    individuals = [r for r in records if r.customer_type == "individual"]
    assert individuals
    assert all(
        18 <= _age_in_years(r.date_of_birth, r.onboarding_date) <= 85
        for r in individuals
    )


def test_businesses_have_no_birth_date_and_are_never_peps(records: list) -> None:
    businesses = [r for r in records if r.customer_type == "business"]
    assert businesses
    assert all(r.date_of_birth is None and not r.pep_flag for r in businesses)


def test_onboarding_before_first_transaction(records: list) -> None:
    assert all(
        date(2000, 1, 1) <= r.onboarding_date < date(2022, 10, 7) for r in records
    )


def test_nationalities_are_iso_codes(records: list) -> None:
    assert all(re.fullmatch(r"[A-Z]{2}", r.nationality) for r in records)


def test_shares_close_to_settings(records: list) -> None:
    n = len(records)
    business_share = sum(r.customer_type == "business" for r in records) / n
    gb_share = sum(r.nationality == "GB" for r in records) / n
    assert 0.13 < business_share < 0.17
    assert 0.83 < gb_share < 0.87
    assert 0 < sum(r.pep_flag for r in records) < 20


def test_incomes_positive_and_rounded(records: list) -> None:
    for r in records:
        step = 500 if r.customer_type == "individual" else 1000
        assert r.declared_annual_income_gbp >= step
        assert r.declared_annual_income_gbp % step == 0


# --- Settings validation --------------------------------------------------------


def test_shares_must_add_up_to_one(config: dict) -> None:
    bad = copy.deepcopy(config)
    bad["customer_type_shares"]["business"] = 0.5
    with pytest.raises(ValueError, match="add up to 1"):
        validate_config(bad)


def test_nationality_must_be_iso_code(config: dict) -> None:
    bad = copy.deepcopy(config)
    bad["nationality"]["others"].append("Pakistan")
    with pytest.raises(ValueError, match="ISO 2-letter"):
        validate_config(bad)
