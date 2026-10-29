"""The suspicious label must never reach staging, mart or feature models (no database needed).

Only `dbt/models/evaluation/` may read the label columns or the typology seed.
Comments are ignored, so a model may say in a comment that it leaves the label out.
"""

import re
from pathlib import Path

import pytest

DBT_MODELS = Path(__file__).resolve().parents[1] / "dbt" / "models"
LABEL_REFERENCES = re.compile(
    r"laundering|eval_transaction_labels|saml_d_typologies", re.IGNORECASE
)
SQL_COMMENTS = re.compile(r"--[^\n]*|\{#.*?#\}|/\*.*?\*/", re.DOTALL)


def strip_comments(sql: str) -> str:
    """Remove SQL (--, /* */) and Jinja ({# #}) comments."""
    return SQL_COMMENTS.sub("", sql)


def model_files(folder: str) -> list[Path]:
    return sorted((DBT_MODELS / folder).glob("*.sql"))


def test_comments_are_stripped() -> None:
    sql = "select a -- not is_laundering\nfrom t {# laundering #} /* laundering */"
    assert not LABEL_REFERENCES.search(strip_comments(sql))


def test_label_in_code_is_found() -> None:
    assert LABEL_REFERENCES.search(strip_comments("select s.is_laundering from s"))


@pytest.mark.parametrize("folder", ["staging", "marts", "features"])
def test_models_never_read_the_label(folder: str) -> None:
    files = model_files(folder)
    assert files, f"no models found in dbt/models/{folder}"
    for path in files:
        code = strip_comments(path.read_text(encoding="utf-8"))
        match = LABEL_REFERENCES.search(code)
        assert match is None, f"{path.name} reads the label: {match.group(0)!r}"


def test_macros_never_read_the_label() -> None:
    # Macros (e.g. the dev-sample rule) are shared by every model.
    files = sorted((DBT_MODELS.parent / "macros").glob("*.sql"))
    assert files
    for path in files:
        code = strip_comments(path.read_text(encoding="utf-8"))
        match = LABEL_REFERENCES.search(code)
        assert match is None, f"{path.name} reads the label: {match.group(0)!r}"


def test_evaluation_model_does_read_the_label() -> None:
    code = " ".join(
        strip_comments(p.read_text(encoding="utf-8")) for p in model_files("evaluation")
    )
    assert LABEL_REFERENCES.search(code)
