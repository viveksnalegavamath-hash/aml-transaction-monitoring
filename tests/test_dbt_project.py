"""Tests for dbt project settings (no database needed)."""

import hashlib
from pathlib import Path

import yaml

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


def load_project() -> dict:
    with (DBT_DIR / "dbt_project.yml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_normal_build_uses_full_data() -> None:
    project_vars = load_project()["vars"]
    assert project_vars["dev_sample"] is False
    assert project_vars["dev_sample_percent"] == 10


def test_anonymous_usage_stats_off() -> None:
    assert load_project()["flags"]["send_anonymous_usage_stats"] is False


def in_dev_sample(account_id: str, percent: int = 10) -> bool:
    """Python copy of the dbt macro in_dev_sample (dbt/macros/dev_sample.sql)."""
    return int(hashlib.md5(account_id.encode()).hexdigest()[:8], 16) % 100 < percent


def test_sample_rule_is_deterministic_and_about_ten_percent() -> None:
    ids = [str(n) for n in range(100_000_000, 100_020_000)]
    first = [in_dev_sample(i) for i in ids]
    assert first == [in_dev_sample(i) for i in ids]
    assert 0.09 < sum(first) / len(ids) < 0.11
