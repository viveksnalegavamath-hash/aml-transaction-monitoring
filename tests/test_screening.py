"""S01 sanctions screening: normalisation, scoring, priority and test-set variations.

No database: these are the matching rules themselves, on hand-made names.
"""

import random
from datetime import date

import numpy as np

from aml_transaction_monitoring.screening.names import (
    dob_signal,
    drop_words,
    load_spec,
    nationality_signal,
    normalise,
    prioritise,
    score_matrix,
    score_pair,
)
from aml_transaction_monitoring.screening.screen import (
    formatting,
    missing_middle,
    spelling,
    word_order,
)

SPEC = load_spec()
PEOPLE = drop_words(SPEC, entity=False)
FIRMS = drop_words(SPEC, entity=True)


def score(a: str, b: str, words=PEOPLE) -> float:
    return score_pair(normalise(a, words), normalise(b, words), SPEC)


# --- Normalisation ---------------------------------------------------------------------


def test_normalise_case_accents_punctuation_and_titles() -> None:
    assert normalise("Dr. José O'Neil-SMITH", PEOPLE) == "jose oneil smith"


def test_normalise_drops_legal_words_for_entities_only() -> None:
    assert normalise("Acme Trading Co., Ltd.", FIRMS) == "acme trading"
    assert normalise("Acme Trading Co., Ltd.", PEOPLE) == "acme trading co ltd"


# --- Scoring ---------------------------------------------------------------------------


def test_word_order_does_not_matter() -> None:
    assert score("JAN Abdul Kabir MOHAMMAD", "Abdul Kabir MOHAMMAD JAN") == 100


def test_missing_middle_name_still_matches() -> None:
    assert score("Abdul MOHAMMAD JAN", "Abdul Kabir MOHAMMAD JAN") == 100


def test_spelling_variant_scores_above_threshold() -> None:
    assert score("Muhammad Hassan AKHUND", "MOHAMMAD HASSAN AKHUND") >= 90


def test_one_shared_word_is_not_a_match() -> None:
    # A single-word name must not match every list name containing that word.
    assert score("Ali", "Ali Hassan Mohammed") < 90


def test_repeated_word_counts_once_for_the_subset_score() -> None:
    # "James James" has one distinct word: no subset match to every James.
    assert score("James James", "JAMES KOANG CHUOL") < 90


def test_different_people_do_not_match() -> None:
    assert score("Aaron Adams", "Abdul Kabir MOHAMMAD JAN") < 90


def test_short_list_names_match_only_exactly() -> None:
    assert score("3M", "3M", FIRMS) == 100
    assert score("3MX", "3M", FIRMS) == 0


def test_matrix_agrees_with_pair_scores() -> None:
    queries = [
        normalise(q, PEOPLE) for q in ["Abdul Mohammad Jan", "Aaron Adams", "Ali"]
    ]
    choices = [
        normalise(c, PEOPLE) for c in ["Abdul Kabir Mohammad Jan", "Ali Hassan", "3M"]
    ]
    matrix = score_matrix(queries, choices, SPEC)
    threshold = SPEC["parameters"]["match_threshold"]
    for i, q in enumerate(queries):
        for j, c in enumerate(choices):
            pair = score_pair(q, c, SPEC)
            assert matrix[i, j] == (int(pair) if pair >= threshold else 0)
    assert matrix.dtype == np.uint8


# --- Priority: evidence raises or lowers, never drops ------------------------------------


def test_dob_signal() -> None:
    dates, years = [date(1972, 1, 30)], [1963, 1972]
    assert dob_signal(date(1972, 1, 30), dates, years, 1) == "exact"
    assert dob_signal(date(1973, 6, 1), dates, years, 1) == "year"
    assert dob_signal(date(1990, 6, 1), dates, years, 1) == "conflict"
    assert dob_signal(date(1990, 6, 1), [], [], 1) == "unknown"
    assert dob_signal(None, dates, years, 1) == "unknown"


def test_nationality_signal() -> None:
    assert nationality_signal("AF", ["AF", "PK"]) == "match"
    assert nationality_signal("GB", ["AF"]) == "conflict"
    assert nationality_signal("GB", []) == "unknown"


def test_evidence_moves_priority_but_never_drops_a_match() -> None:
    strong = prioritise(92, "exact", "match", False, SPEC)
    weak = prioritise(92, "conflict", "conflict", True, SPEC)
    assert (strong.priority_band, strong.priority_score) == ("High", 107)
    assert (weak.priority_band, weak.priority_score) == ("Low", 72)  # kept, just lower


# --- Test-set variations (deterministic, realistic) ---------------------------------------


def test_variations_are_realistic_and_reproducible() -> None:
    name = "MOHAMMAD HASSAN AKHUND"
    assert word_order(name) == "AKHUND MOHAMMAD HASSAN"
    assert spelling(name, random.Random(1)) == "MUHAMMAD HASSAN AKHUND"
    assert spelling(name, random.Random(1)) == spelling(name, random.Random(1))
    assert formatting(name, entity=False) == "Mohammad-Hassan Akhund"
    assert formatting("ACME TRADING", entity=True) == "Acmé-Trading, Ltd."
    assert missing_middle(name, random.Random(1)) == "MOHAMMAD AKHUND"
    assert missing_middle("ALI HASSAN", random.Random(1)) is None


def test_legal_words_kept_when_only_one_word_would_remain() -> None:
    from aml_transaction_monitoring.screening.names import normalise_name

    assert normalise_name("LLC SMITH", SPEC, entity=True) == "llc smith"
    assert normalise_name("Smith Group", SPEC, entity=True) == "smith group"
    assert normalise_name("Acme Trading Co., Ltd.", SPEC, entity=True) == "acme trading"
    # "LLC SMITH" no longer matches every Smith company, still matches "Smith LLC".
    llc_smith = normalise_name("LLC SMITH", SPEC, entity=True)
    assert (
        score_pair(normalise_name("Smith Group", SPEC, entity=True), llc_smith, SPEC)
        < 90
    )
    assert (
        score_pair(normalise_name("Smith LLC", SPEC, entity=True), llc_smith, SPEC)
        == 100
    )
