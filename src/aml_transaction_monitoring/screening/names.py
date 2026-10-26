"""Name normalisation, match scoring and match priority for sanctions screening.

All settings come from rules/S01_sanctions.yaml. Pure functions, no database.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from rapidfuzz import fuzz, process

from aml_transaction_monitoring.ingestion.load_raw import PROJECT_ROOT

SPEC_PATH = PROJECT_ROOT / "rules" / "S01_sanctions.yaml"
APOSTROPHES = re.compile(r"['’`´]")
NON_ALNUM = re.compile(r"[^a-z0-9]+")


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    """Read the S01 spec."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalise(name: str | None, drop_words: frozenset[str] = frozenset()) -> str:
    """Lower case, accents removed, apostrophes joined, other punctuation to spaces.

    `drop_words` (titles, legal suffixes) are removed as whole words. Word
    order is kept here; the scorers ignore it.

    >>> normalise("Dr. José O'Neil-Smith")
    'dr jose oneil smith'
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = APOSTROPHES.sub("", text)
    words = [w for w in NON_ALNUM.sub(" ", text).split() if w not in drop_words]
    return " ".join(words)


def normalise_name(name: str | None, spec: dict[str, Any], entity: bool) -> str:
    """Normalise a person's or entity's name for screening (list and customer alike).

    Entities lose legal words (Ltd, LLC, Group, ...), unless that would leave a
    single distinct word: "LLC SMITH" would become just "smith" and match every
    "Smith Group". Then the legal words are kept ("llc smith").
    """
    if not entity:
        return normalise(name, drop_words(spec, entity=False))
    stripped = normalise(name, drop_words(spec, entity=True))
    return stripped if len(set(stripped.split())) >= 2 else normalise(name)


def drop_words(spec: dict[str, Any], entity: bool) -> frozenset[str]:
    """Words removed before matching: titles for people, legal words for entities."""
    norm = spec["normalisation"]
    return frozenset(norm["entity_words"] if entity else norm["titles"])


def score_pair(a: str, b: str, spec: dict[str, Any]) -> float:
    """Name score of two normalised names, 0-100 (the matrix version, for one pair)."""
    p = spec["parameters"]
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) < p["min_fuzzy_length"]:
        return 100.0 if a == b else 0.0
    score = fuzz.token_sort_ratio(a, b)
    if min(len(set(a.split())), len(set(b.split()))) >= p["min_tokens_for_subset"]:
        score = max(score, fuzz.token_set_ratio(a, b))
    return float(score)


def score_matrix(
    queries: list[str], choices: list[str], spec: dict[str, Any]
) -> np.ndarray:
    """Scores of every query against every choice (uint8), same rules as score_pair.

    Values below the threshold are 0 (rapidfuzz score_cutoff), so the result
    is cheap to scan for matches. Runs on all CPU cores.
    """
    p = spec["parameters"]
    cutoff = p["match_threshold"]
    sort = process.cdist(queries, choices, scorer=fuzz.token_sort_ratio,
                         score_cutoff=cutoff, dtype=np.uint8, workers=-1)  # fmt: skip
    subset = process.cdist(queries, choices, scorer=fuzz.token_set_ratio,
                           score_cutoff=cutoff, dtype=np.uint8, workers=-1)  # fmt: skip
    # Distinct words: "James James" is one word, so it gets no subset score.
    q_words = np.array([len(set(q.split())) for q in queries])
    c_words = np.array([len(set(c.split())) for c in choices])
    subset_ok = (q_words[:, None] >= p["min_tokens_for_subset"]) & (
        c_words[None, :] >= p["min_tokens_for_subset"]
    )
    scores = np.where(subset_ok, np.maximum(sort, subset), sort)
    # Short list names (and empty queries) match only exactly.
    short = np.flatnonzero([len(c) < p["min_fuzzy_length"] for c in choices])
    if short.size:
        short_names = np.array(choices, dtype=object)[short]
        exact = np.array(queries, dtype=object)[:, None] == short_names[None, :]
        scores[:, short] = np.where(exact, 100, 0)
    empty = np.array([not q for q in queries])
    scores[empty, :] = 0
    return scores


@dataclass(frozen=True)
class Signals:
    """Date-of-birth and nationality evidence for one match, and its priority."""

    dob: str  # exact | year | conflict | unknown
    nationality: str  # match | conflict | unknown
    priority_score: float
    priority_band: str  # High | Medium | Low


def dob_signal(
    customer_dob: date | None,
    listed_dates: list[date],
    listed_years: list[int],
    tolerance: int,
) -> str:
    """How a customer's date of birth compares with the listed ones."""
    if customer_dob is None or not listed_years:
        return "unknown"
    if customer_dob in listed_dates:
        return "exact"
    if any(abs(customer_dob.year - y) <= tolerance for y in listed_years):
        return "year"
    return "conflict"


def nationality_signal(customer_country: str | None, listed: list[str]) -> str:
    """Whether the customer's nationality (or incorporation country) is listed."""
    if not customer_country or not listed:
        return "unknown"
    return "match" if customer_country in listed else "conflict"


def prioritise(
    name_score: float,
    dob: str,
    nationality: str,
    low_quality_alias: bool,
    spec: dict[str, Any],
) -> Signals:
    """Priority = name score adjusted by the evidence. Never drops a match."""
    pr = spec["priority"]
    points = {
        "exact": pr["dob_exact"],
        "year": pr["dob_year"],
        "conflict": pr["dob_conflict"],
    }
    nat = {"match": pr["nationality_match"], "conflict": pr["nationality_conflict"]}
    score = name_score + points.get(dob, 0) + nat.get(nationality, 0)
    if low_quality_alias:
        score += pr["low_quality_alias"]
    bands = pr["bands"]
    band = (
        "High"
        if score >= bands["high"]
        else "Medium"
        if score >= bands["medium"]
        else "Low"
    )
    return Signals(dob, nationality, score, band)
