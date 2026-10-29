"""README chart: suspicious accounts caught against alerts per week, rules vs ML.

Reads only the hold-out report `docs/results/ml_evaluation.md` (scored once):
no database, no labels. Plots the tuned rules' single operating point and
the rules + ML model at every weekly volume the report gives (the volume
curve, the rules' distinct account-weeks and the rules' alert volume), plus
a guide where the model first catches as many suspicious accounts as the
rules. ML only is not drawn: it lies within half a point of rules + ML at
every volume (checked here, so the caption stays true).

Writes a light and a dark version to docs/images/; the README shows the one
matching the reader's theme. Colours: the dataviz reference palette, slot 1
(blue) for the model, slot 2 (orange) for the rules, validated for colour
vision deficiency in both modes.

Usage:
    uv run python -m aml_transaction_monitoring.evaluation.ml_chart
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from matplotlib import rc_context
from matplotlib.figure import Figure
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

logger = logging.getLogger(__name__)

REPORT = PROJECT_ROOT / "docs" / "results" / "ml_evaluation.md"
OUT_DIR = PROJECT_ROOT / "docs" / "images"
MAX_GAP_POINTS = 0.5  # ML only vs rules + ML, in percentage points
# A system sans, as the README is read on many systems; DejaVu Sans ships with matplotlib.
FONTS = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
STYLE = {"font.family": "sans-serif", "font.sans-serif": FONTS}

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "primary": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "model": "#2a78d6",
        "rules": "#eb6834",
    },
    "dark": {
        "surface": "#1a1a19",
        "primary": "#f0efec",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "model": "#3987e5",
        "rules": "#d95926",
    },
}


@dataclass(frozen=True)
class ChartData:
    """Operating points from the hold-out report (alerts per week, % of accounts caught)."""

    suspicious_accounts: int
    rules: tuple[float, float]
    model: list[tuple[float, float]]  # rules + ML, sorted by alerts per week
    ml_only: list[tuple[float, float]]
    match_volume: (
        int  # rules + ML alerts per week that catch as many accounts as the rules
    )


def number(text: str) -> float:
    """'5,053' -> 5053.0; '58.6%' -> 58.6."""
    return float(text.replace(",", "").replace("%", "").strip())


def md_tables(text: str) -> dict[str, list[dict[str, str]]]:
    """{section heading: table rows as dicts} for every '## ' section with a table."""
    tables: dict[str, list[dict[str, str]]] = {}
    heading = ""
    header: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading, header = line[3:].strip(), None
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not all(set(c) <= {"-", " "} for c in cells):
                tables.setdefault(heading, []).append(
                    dict(zip(header, cells, strict=True))
                )
        else:
            header = None
    return tables


def section(
    tables: dict[str, list[dict[str, str]]], start: str
) -> list[dict[str, str]]:
    (name,) = [h for h in tables if h.startswith(start)]
    return tables[name]


def read_report(path: Path = REPORT) -> ChartData:
    """Parse the operating points out of the hold-out report."""
    text = path.read_text(encoding="utf-8")
    tables = md_tables(text)
    headline = {r["Approach"]: r for r in section(tables, "Same weekly alert volume")}
    sensitivity = section(tables, "Sensitivity")
    curve = section(tables, "Fewer alerts")

    def points(label: str, headline_row: str) -> list[tuple[float, float]]:
        found = [
            (number(r["Alerts / week"]), number(r[f"{label}: account recall"]))
            for r in curve
        ]
        found += [
            (number(r["Alerts / week"]), number(r["Account recall"]))
            for r in sensitivity
            if r["Approach"].startswith(f"{label} at ")
        ]
        row = headline[headline_row]
        found.append((number(row["Alerts / week"]), number(row["Account recall"])))
        return sorted(found)

    rules_row = headline["Rules only (tuned)"]
    total = re.search(r"\(([\d,]+) suspicious of", text)
    match = re.search(r"Rules \+ ML: \*\*([\d,]+) a week\*\*", text)
    if not total or not match:
        raise ValueError(f"{path.name}: base rate or matching volume not found")
    return ChartData(
        suspicious_accounts=int(number(total.group(1))),
        rules=(number(rules_row["Alerts / week"]), number(rules_row["Account recall"])),
        model=points("Rules + ML", "Rules + ML, XGBoost"),
        ml_only=points("ML only", "ML only, XGBoost"),
        match_volume=int(number(match.group(1))),
    )


def largest_gap(data: ChartData) -> float:
    """Largest difference between ML only and rules + ML at the same volume (points)."""
    model = dict(data.model)
    return max(abs(model[volume] - recall) for volume, recall in data.ml_only)


def draw(data: ChartData, theme: str) -> Figure:
    """The chart in one theme (light or dark)."""
    c = THEMES[theme]
    with rc_context(STYLE):
        fig = Figure(figsize=(8, 4.6), dpi=200, facecolor=c["surface"])
        ax = fig.add_axes((0.09, 0.12, 0.87, 0.66), facecolor=c["surface"])
        fig.text(
            0.09,
            0.93,
            "Suspicious accounts caught vs alerts per week",
            color=c["primary"],
            fontsize=12.5,
            fontweight="bold",
        )
        fig.text(
            0.09,
            0.875,
            f"Hold-out 1 Jun – 23 Aug 2023 · {data.suspicious_accounts:,} suspicious"
            " accounts · synthetic data (SAML-D)",
            color=c["secondary"],
            fontsize=8.5,
        )

        rules_alerts, rules_recall = data.rules
        # Guide at the rules' recall: where the model first catches as many accounts.
        ax.plot(
            [data.match_volume, rules_alerts],
            [rules_recall, rules_recall],
            color=c["muted"],
            linewidth=1,
            solid_capstyle="round",
            zorder=2,
        )
        # The matching volume is the same model at a smaller volume: its recall
        # is at least the rules' (the smallest volume that reaches it).
        curve = sorted([(data.match_volume, rules_recall), *data.model])
        xs, ys = zip(*curve, strict=True)
        ax.plot(
            xs,
            ys,
            color=c["model"],
            linewidth=2,
            solid_joinstyle="round",
            solid_capstyle="round",
            marker="o",
            markersize=6.5,
            markeredgecolor=c["surface"],
            markeredgewidth=2,
            label="Rules + ML (XGBoost): top alerts of each week",
            zorder=3,
        )
        ax.plot(
            [data.match_volume],
            [rules_recall],
            linestyle="none",
            marker="o",
            markersize=6.5,
            markerfacecolor=c["surface"],
            markeredgecolor=c["model"],
            markeredgewidth=2,
            zorder=4,
        )
        ax.plot(
            [rules_alerts],
            [rules_recall],
            linestyle="none",
            marker="D",
            markersize=7.5,
            color=c["rules"],
            markeredgecolor=c["surface"],
            markeredgewidth=2,
            label="Tuned rules",
            zorder=4,
        )

        # Selective direct labels, in text colours (never the series colour).
        label = {"fontsize": 8, "color": c["primary"]}
        ax.annotate(
            f"Rules: {rules_recall:.1f}% at {rules_alerts:,.0f} a week",
            (rules_alerts, rules_recall),
            xytext=(4, -12),
            textcoords="offset points",
            ha="right",
            va="top",
            **label,
        )
        top_alerts, top_recall = data.model[-1]
        ax.annotate(
            f"ML: {top_recall:.1f}% at the same volume",
            (top_alerts, top_recall),
            xytext=(4, -12),
            textcoords="offset points",
            ha="right",
            va="top",
            **label,
        )
        ax.annotate(
            f"Same recall with {data.match_volume:,} a week"
            f" (−{1 - data.match_volume / rules_alerts:.0%})",
            (data.match_volume, rules_recall),
            xytext=(-4, -12),
            textcoords="offset points",
            ha="left",
            va="top",
            **label,
        )

        ax.set_xscale("log")
        ax.set_xlim(150, 7_000)
        ax.set_ylim(0, 105)
        ax.xaxis.set_major_locator(FixedLocator([200, 500, 1_000, 2_000, 5_000]))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.yaxis.set_major_locator(FixedLocator([0, 25, 50, 75, 100]))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.set_xlabel("Alerts per week (log scale)", color=c["secondary"], fontsize=8.5)
        ax.set_ylabel("Suspicious accounts caught", color=c["secondary"], fontsize=8.5)
        ax.grid(True, axis="both", color=c["grid"], linewidth=0.8, linestyle="-")
        ax.set_axisbelow(True)
        ax.tick_params(colors=c["muted"], labelsize=8, length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(c["axis"])

        legend = fig.legend(
            loc="upper left",
            bbox_to_anchor=(0.085, 0.84),
            ncols=2,
            frameon=False,
            fontsize=8,
            handlelength=1.6,
            columnspacing=2.0,
            borderaxespad=0,
        )
        for text in legend.get_texts():
            text.set_color(c["primary"])
    return fig


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    data = read_report()
    gap = largest_gap(data)
    if gap > MAX_GAP_POINTS:
        raise RuntimeError(
            f"ML only differs from rules + ML by {gap:.1f} points: plot both"
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        path = OUT_DIR / f"alerts_vs_recall_{theme}.png"
        # Fonts are resolved when the image is drawn, so save inside the style too.
        with rc_context(STYLE):
            draw(data, theme).savefig(path, facecolor=THEMES[theme]["surface"])
        logger.info("Wrote %s", path.relative_to(PROJECT_ROOT))


if __name__ == "__main__":
    main()
