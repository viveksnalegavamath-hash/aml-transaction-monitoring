"""Tests for the country-risk table: FATF parsing, WGI, ratings, coverage (no database)."""

import copy
import inspect
from datetime import date

import pytest

from aml_transaction_monitoring.ingestion import country_risk, fatf_lists, wgi
from aml_transaction_monitoring.ingestion.country_risk import (
    FATF_DIR,
    assign_thirds,
    build_country_risk,
    find_coverage_gaps,
    load_config,
    name_map_rows,
    name_to_iso,
    read_fatf_pages,
    validate_config,
)
from aml_transaction_monitoring.ingestion.fatf_lists import (
    COUNTERMEASURES,
    ENHANCED_DUE_DILIGENCE,
    INCREASED_MONITORING,
    normalise_name,
    parse_fatf_page,
)
from aml_transaction_monitoring.ingestion.wgi import WgiValue, parse_wgi_response

CFA_URL = "https://www.fatf-gafi.org/en/publications/x/call-for-action.html"
IM_URL = "https://www.fatf-gafi.org/en/publications/x/increased-monitoring.html"
IGNORE = ["Publication details", "Related materials"]


def make_page(title: str, headings: list[tuple[int, str]], url: str | None) -> str:
    """Build a minimal FATF-like page."""
    link = f'<link rel="canonical" href="{url}">' if url else ""
    body = "".join(
        f"<h{level}>{text}</h{level}><p>Text.</p>" for level, text in headings
    )
    return f"<html><head>{link}</head><body><h1>{title}</h1>{body}</body></html>"


CFA_HTML = make_page(
    "High-Risk Jurisdictions subject to a Call for Action - 19 June 2026",
    [
        (4, "Publication details"),
        (2, "Jurisdictions subject to a FATF call ... to apply countermeasures"),
        (3, "Democratic People's Republic of Korea (DPRK)"),
        (3, "Iran"),
        (2, "Jurisdiction subject to a FATF call ... to apply enhanced due diligence"),
        (3, " "),
        (3, "Myanmar"),
        (3, "Related materials"),
    ],
    CFA_URL,
)

IM_HTML = make_page(
    "Jurisdictions under Increased Monitoring - 19 June 2026",
    [
        (4, "Publication details"),
        (3, "angola"),
        (2, "Kuwait"),
        (3, "virgin islands (uk)"),
        (4, "Jurisdictions No Longer subject to Increased Monitoring By The FATF"),
        (3, "Algeria"),
        (3, "Related materials"),
    ],
    IM_URL,
)


@pytest.fixture(scope="module")
def config() -> dict:
    return load_config()


@pytest.fixture
def small_config(config: dict) -> dict:
    """The real settings, with WGI rows we control."""
    cfg = copy.deepcopy(config)
    cfg["wgi"]["economies_without_code"] = {"Jersey": "JE"}
    cfg["wgi"]["skip_without_code"] = ["Netherlands Antilles"]
    return cfg


@pytest.fixture
def pages() -> list:
    return [
        parse_fatf_page(CFA_HTML, "call_for_action", IGNORE),
        parse_fatf_page(IM_HTML, "increased_monitoring", IGNORE),
    ]


def wgi_values(country: str, code: str, cc: float | None, rl: float, rq: float) -> list:
    """Three WgiValues (one per indicator) for one economy."""
    return [
        WgiValue(code, country, ind, ind, 2025, val, "2026-09-25")
        for ind, val in (
            ("GOV_WGI_CC.EST", cc),
            ("GOV_WGI_RL.EST", rl),
            ("GOV_WGI_RQ.EST", rq),
        )
    ]


@pytest.fixture
def wgi_rows() -> list:
    """Six rated economies covering the FATF test pages, plus edge cases."""
    rows = []
    for country, code, score in [
        ("Korea, Dem. People's Rep.", "KP", -1.7),
        ("Iran, Islamic Rep.", "IR", -1.3),
        ("Myanmar", "MM", -1.5),
        ("Angola", "AO", -0.6),
        ("Kuwait", "KW", 0.1),
        ("United Kingdom", "GB", 1.4),
    ]:
        rows += wgi_values(country, code, score, score, score)
    rows += wgi_values("Jersey", "", 1.2, 1.2, 1.2)  # no code: mapped by name
    rows += wgi_values("Netherlands Antilles", "", None, None, None)  # skipped
    rows += wgi_values("New Caledonia", "NC", 0.1, 0.2, None)[:2]  # only two indicators
    return rows


# --- FATF page parsing --------------------------------------------------------


def test_call_for_action_measures_come_from_section_headings(pages: list) -> None:
    cfa = pages[0]
    assert cfa.listed == [
        ("Democratic People's Republic of Korea (DPRK)", COUNTERMEASURES),
        ("Iran", COUNTERMEASURES),
        ("Myanmar", ENHANCED_DUE_DILIGENCE),
    ]
    assert cfa.list_date == date(2026, 6, 19)
    assert cfa.source_url == CFA_URL


def test_removed_jurisdictions_are_not_listed(pages: list) -> None:
    im = pages[1]
    assert [name for name, _ in im.listed] == [
        "angola",
        "Kuwait",
        "virgin islands (uk)",
    ]
    assert all(measure == INCREASED_MONITORING for _, measure in im.listed)
    assert im.removed == ["Algeria"]


def test_wrong_page_for_list_fails() -> None:
    with pytest.raises(ValueError, match="is not the call_for_action list"):
        parse_fatf_page(IM_HTML, "call_for_action", IGNORE)


def test_page_without_canonical_url_fails() -> None:
    html = make_page(
        "Jurisdictions under Increased Monitoring - 19 June 2026", [], None
    )
    with pytest.raises(ValueError, match="canonical URL"):
        parse_fatf_page(html, "increased_monitoring", IGNORE)


def test_unexpected_heading_before_any_section_fails() -> None:
    html = make_page(
        "High-Risk Jurisdictions subject to a Call for Action - 19 June 2026",
        [(3, "Iran")],
        CFA_URL,
    )
    with pytest.raises(ValueError, match="before any measure section"):
        parse_fatf_page(html, "call_for_action", IGNORE)


def test_jurisdiction_listed_twice_fails() -> None:
    html = make_page(
        "Jurisdictions under Increased Monitoring - 19 June 2026",
        [(3, "Kenya"), (3, "KENYA")],
        IM_URL,
    )
    with pytest.raises(ValueError, match="listed twice"):
        parse_fatf_page(html, "increased_monitoring", IGNORE)


def test_name_matching_ignores_case_spaces_and_curly_apostrophes() -> None:
    assert normalise_name("  CÔTE  D’IVOIRE ") == normalise_name("Côte d'Ivoire")


@pytest.mark.skipif(
    not (FATF_DIR / "fatf_call_for_action_june_2026.html").exists(),
    reason="saved FATF pages not present (data/raw is git-ignored)",
)
def test_saved_june_2026_pages(config: dict) -> None:
    cfa, im = read_fatf_pages(config)
    names = name_to_iso(config)
    codes = {
        m: sorted(
            names[normalise_name(n)] for n, mm in cfa.listed + im.listed if mm == m
        )
        for m in (COUNTERMEASURES, ENHANCED_DUE_DILIGENCE, INCREASED_MONITORING)
    }
    assert codes[COUNTERMEASURES] == ["IR", "KP"]
    assert codes[ENHANCED_DUE_DILIGENCE] == ["MM"]
    assert len(codes[INCREASED_MONITORING]) == 22
    assert sorted(im.removed) == ["Algeria", "Namibia"]
    assert not {"DZ", "NA"} & {code for group in codes.values() for code in group}
    assert cfa.list_date == im.list_date == date(2026, 6, 19)


# --- WGI responses --------------------------------------------------------------


def _response(indicator: str = "GOV_WGI_CC.EST", year: str = "2025") -> list:
    row = {
        "indicator": {"id": indicator, "value": "Control of Corruption"},
        "country": {"id": "GB", "value": "United Kingdom"},
        "date": year,
        "value": 1.5,
    }
    meta = {"sourceid": "3", "pages": 1, "total": 1, "lastupdated": "2026-09-25"}
    return [meta, [row]]


def test_wgi_response_is_read() -> None:
    [value] = parse_wgi_response(_response(), "GOV_WGI_CC.EST", 2025)
    assert (value.wb_code, value.value, value.wgi_version) == ("GB", 1.5, "2026-09-25")


def test_wgi_api_error_fails() -> None:
    error = [{"message": [{"id": "120", "value": "Invalid value"}]}]
    with pytest.raises(ValueError, match="not a data response"):
        parse_wgi_response(error, "GOV_WGI_CC.EST", 2025)


def test_wgi_wrong_year_fails() -> None:
    with pytest.raises(ValueError, match="found GOV_WGI_CC.EST for 2024"):
        parse_wgi_response(_response(year="2024"), "GOV_WGI_CC.EST", 2025)


# --- Internal rating ------------------------------------------------------------


def test_thirds_lowest_governance_is_high_risk() -> None:
    averages = {c: float(i) for i, c in enumerate("ABCDEFGHI")}
    ratings = assign_thirds(averages, ["high", "medium", "low"])
    assert [ratings[c] for c in "ABCDEFGHI"] == ["high"] * 3 + ["medium"] * 3 + [
        "low"
    ] * 3


def test_thirds_ties_broken_by_code_not_input_order() -> None:
    forward = assign_thirds(
        {"AA": 0.0, "BB": 0.0, "CC": 0.0}, ["high", "medium", "low"]
    )
    backward = assign_thirds(
        {"CC": 0.0, "BB": 0.0, "AA": 0.0}, ["high", "medium", "low"]
    )
    assert forward == backward == {"AA": "high", "BB": "medium", "CC": "low"}


# --- Building ref.country_risk ----------------------------------------------------


def test_one_row_per_country_with_fatf_and_rating_separate(
    pages: list, wgi_rows: list, small_config: dict
) -> None:
    rows = {r.iso_code: r for r in build_country_risk(pages, wgi_rows, small_config)}
    assert sorted(rows) == ["AO", "GB", "IR", "JE", "KP", "KW", "MM", "NC", "VG"]
    assert (rows["IR"].fatf_status, rows["IR"].fatf_measure) == (
        "call_for_action",
        COUNTERMEASURES,
    )
    assert rows["MM"].fatf_measure == ENHANCED_DUE_DILIGENCE
    # Grey-listed but rated medium here: the two views stay independent.
    assert rows["KW"].fatf_status == "increased_monitoring"
    assert rows["KW"].internal_rating == "medium"
    assert rows["GB"].fatf_status == "not_listed" and rows["GB"].fatf_measure is None
    assert rows["GB"].internal_rating == "low"
    assert rows["KP"].internal_rating == "high"


def test_list_date_and_source_on_every_row(
    pages: list, wgi_rows: list, small_config: dict
) -> None:
    rows = {r.iso_code: r for r in build_country_risk(pages, wgi_rows, small_config)}
    assert all(r.fatf_list_date == date(2026, 6, 19) for r in rows.values())
    assert all(r.wgi_version == "2026-09-25" for r in rows.values())
    assert rows["IR"].fatf_source == CFA_URL
    assert rows["AO"].fatf_source == IM_URL
    assert rows["GB"].fatf_source == f"{CFA_URL} | {IM_URL}"


def test_fatf_jurisdiction_without_wgi_still_gets_a_row(
    pages: list, wgi_rows: list, small_config: dict
) -> None:
    rows = {r.iso_code: r for r in build_country_risk(pages, wgi_rows, small_config)}
    vg = rows["VG"]
    assert vg.country_name == "Virgin Islands (UK)"
    assert vg.fatf_status == "increased_monitoring"
    assert vg.internal_rating is None and vg.wgi_average is None


def test_country_missing_an_indicator_is_not_rated(
    pages: list, wgi_rows: list, small_config: dict
) -> None:
    rows = {r.iso_code: r for r in build_country_risk(pages, wgi_rows, small_config)}
    assert rows["NC"].internal_rating is None
    assert rows["JE"].internal_rating is not None  # mapped by name


def test_unmapped_fatf_jurisdiction_fails(
    pages: list, wgi_rows: list, small_config: dict
) -> None:
    del small_config["country_names"]["Kuwait"]
    with pytest.raises(
        ValueError, match="no ISO code in country_names: \\['Kuwait'\\]"
    ):
        build_country_risk(pages, wgi_rows, small_config)


def test_jurisdiction_on_two_lists_fails(wgi_rows: list, small_config: dict) -> None:
    cfa = parse_fatf_page(CFA_HTML, "call_for_action", IGNORE)
    im_with_iran = make_page(
        "Jurisdictions under Increased Monitoring - 19 June 2026",
        [(3, "Iran")],
        IM_URL,
    )
    im = parse_fatf_page(im_with_iran, "increased_monitoring", IGNORE)
    with pytest.raises(ValueError, match="on more than one FATF list"):
        build_country_risk([cfa, im], wgi_rows, small_config)


def test_pages_with_different_dates_fail(wgi_rows: list, small_config: dict) -> None:
    cfa = parse_fatf_page(CFA_HTML, "call_for_action", IGNORE)
    im = parse_fatf_page(
        IM_HTML.replace("19 June 2026", "24 October 2025"),
        "increased_monitoring",
        IGNORE,
    )
    with pytest.raises(ValueError, match="different list dates"):
        build_country_risk([cfa, im], wgi_rows, small_config)


def test_unknown_wgi_row_without_code_fails(
    pages: list, wgi_rows: list, small_config: dict
) -> None:
    rows = wgi_rows + wgi_values("Atlantis", "", 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="without an ISO code.*Atlantis"):
        build_country_risk(pages, rows, small_config)


# --- Hard stop: coverage -----------------------------------------------------------


@pytest.fixture
def built(pages: list, wgi_rows: list, small_config: dict) -> list:
    return build_country_risk(pages, wgi_rows, small_config)


def test_coverage_passes_when_every_country_has_a_row(
    built: list, small_config: dict
) -> None:
    gaps = find_coverage_gaps(
        built, ["UK"], ["GB", "JE"], ["KP", "VG"], name_to_iso(small_config)
    )
    assert gaps == []


def test_coverage_fails_for_saml_d_location_without_iso_code(
    built: list, small_config: dict
) -> None:
    gaps = find_coverage_gaps(
        built, ["UK", "Atlantis"], [], [], name_to_iso(small_config)
    )
    assert gaps == ["SAML-D location 'Atlantis' has no ISO code in country_names"]


def test_coverage_fails_for_kyc_nationality_without_row(
    built: list, small_config: dict
) -> None:
    gaps = find_coverage_gaps(built, [], ["GB", "PL"], [], name_to_iso(small_config))
    assert gaps == ["KYC nationality (PL) has no row in ref.country_risk"]


def test_coverage_fails_for_fatf_jurisdiction_without_row(
    built: list, small_config: dict
) -> None:
    gaps = find_coverage_gaps(built, [], [], ["YE"], name_to_iso(small_config))
    assert gaps == ["FATF jurisdiction (YE) has no row in ref.country_risk"]


def test_coverage_needs_a_rating_for_customer_countries_only(
    built: list, small_config: dict
) -> None:
    names = name_to_iso(small_config)
    assert find_coverage_gaps(built, [], ["VG"], [], names) == [
        "KYC nationality (VG) has no internal rating"
    ]
    assert find_coverage_gaps(built, [], [], ["VG"], names) == []


# --- Country name map (for dbt) -----------------------------------------------------

SAML_D_LOCATIONS = [
    "UK", "Pakistan", "Nigeria", "Germany", "France", "Japan", "Morocco", "Austria",
    "Spain", "Switzerland", "Turkey", "Albania", "Mexico", "UAE", "USA", "Italy",
    "India", "Netherlands",
]  # fmt: skip


def test_name_map_has_unique_normalised_keys(config: dict) -> None:
    rows = name_map_rows(config)
    keys = [key for key, _, _ in rows]
    assert len(keys) == len(set(keys)) == len(config["country_names"])
    assert all(key == normalise_name(name) for key, name, _ in rows)


def test_name_map_covers_every_saml_d_location(config: dict) -> None:
    mapped = {key: iso for key, _, iso in name_map_rows(config)}
    assert mapped[normalise_name("UK")] == "GB"
    assert mapped[normalise_name("UAE")] == "AE"
    assert all(normalise_name(loc) in mapped for loc in SAML_D_LOCATIONS)


# --- Settings ----------------------------------------------------------------------


def test_real_settings_are_valid(config: dict) -> None:
    validate_config(config)


def test_iso_code_must_be_two_capital_letters(config: dict) -> None:
    bad = copy.deepcopy(config)
    bad["country_names"]["Norway"] = False  # what unquoted NO becomes in YAML
    with pytest.raises(ValueError, match="Not ISO 2-letter codes"):
        validate_config(bad)


def test_indicators_must_match_table_columns(config: dict) -> None:
    bad = copy.deepcopy(config)
    bad["wgi"]["indicators"] = {"GOV_WGI_GE.EST": "government_effectiveness"}
    with pytest.raises(ValueError, match="needs a table change"):
        validate_config(bad)


# --- No label leakage ------------------------------------------------------------


def test_country_risk_never_mentions_the_label() -> None:
    for module in (country_risk, fatf_lists, wgi):
        text = inspect.getsource(module).lower()
        assert "laundering" not in text, f"{module.__name__} mentions the label"
