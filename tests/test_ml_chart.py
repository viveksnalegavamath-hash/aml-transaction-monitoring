"""Tests for the README chart (reads the committed hold-out report; no database)."""

import io

import pytest

from aml_transaction_monitoring.evaluation.ml_chart import (
    MAX_GAP_POINTS,
    THEMES,
    draw,
    largest_gap,
    md_tables,
    number,
    read_report,
)


def test_numbers_are_read_with_separators_and_percent_signs() -> None:
    assert (number("5,053"), number("58.6%"), number(" 99.8% ")) == (5053, 58.6, 99.8)


def test_markdown_tables_are_read_per_section() -> None:
    text = """## First

| Approach | Alerts / week |
| --- | --- |
| Rules | 5,053 |

## Second

| A | B |
| --- | --- |
| 1 | 2 |
| 3 | 4 |
"""
    tables = md_tables(text)
    assert tables["First"] == [{"Approach": "Rules", "Alerts / week": "5,053"}]
    assert [r["B"] for r in tables["Second"]] == ["2", "4"]


def test_points_match_the_hold_out_report() -> None:
    data = read_report()
    assert data.suspicious_accounts == 2_490
    assert data.rules == (5_053, 58.6)
    assert data.model[0] == (250, 67.4) and data.model[-1] == (5_053, 99.8)
    assert [volume for volume, _ in data.model] == sorted(v for v, _ in data.model)
    assert data.match_volume == 197


def test_ml_only_is_close_enough_to_leave_out_of_the_chart() -> None:
    assert largest_gap(read_report()) <= MAX_GAP_POINTS


@pytest.mark.parametrize("theme", list(THEMES))
def test_chart_renders_in_both_themes(theme: str) -> None:
    fig = draw(read_report(), theme)
    (ax,) = fig.axes
    assert ax.get_xscale() == "log"
    assert ax.get_ylim() == (0, 105)
    image = io.BytesIO()
    fig.savefig(image, format="png")
    assert image.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"
