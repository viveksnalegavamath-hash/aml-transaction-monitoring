"""Tests for safe .env edits and secret rotation (no database, no real .env)."""

from pathlib import Path

import pytest

from aml_transaction_monitoring.api.provision import ensure_secrets
from aml_transaction_monitoring.envfile import (
    ALPHABET,
    env_key,
    mode,
    new_secret,
    other_lines,
    replace_values,
    write_private,
)
from aml_transaction_monitoring.rotate_secrets import ROTATED

ENV = """POSTGRES_USER=aml_admin
POSTGRES_PASSWORD=old-admin
# a comment
POSTGRES_DB=aml

API_DB_PASSWORD=old-reader
OTHER_TOKEN=keep-me"""


def test_new_secrets_are_long_random_letters_and_digits() -> None:
    values = {new_secret() for _ in range(50)}
    assert len(values) == 50
    assert all(len(v) == 32 and set(v) <= set(ALPHABET) for v in values)


def test_env_key() -> None:
    assert env_key("A=1") == "A"
    assert env_key("export B = 2") == "B"
    assert env_key("# C=3") is None
    assert env_key("   ") is None


def test_replace_values_changes_only_the_rotated_lines() -> None:
    new = replace_values(ENV, {"POSTGRES_PASSWORD": "n1", "API_DB_PASSWORD": "n2"})
    assert "POSTGRES_PASSWORD=n1\n" in new and "API_DB_PASSWORD=n2\n" in new
    assert "old-admin" not in new and "old-reader" not in new
    assert other_lines(new, ROTATED) == other_lines(ENV, ROTATED)
    assert "OTHER_TOKEN=keep-me" in new and "# a comment" in new
    assert len(new.splitlines()) == len(ENV.splitlines())
    assert new.endswith("OTHER_TOKEN=keep-me")  # untouched lines kept byte for byte


def test_missing_keys_are_appended_after_a_newline() -> None:
    new = replace_values("A=1", {"API_KEY": "k"})
    assert new == "A=1\nAPI_KEY=k\n"


def test_private_files_are_created_with_mode_600(tmp_path: Path) -> None:
    path = tmp_path / ".env.rotating"
    write_private(path, "A=1\n")
    assert mode(path) == 0o600
    assert path.read_text() == "A=1\n"


def test_provision_fills_an_empty_secret_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("API_KEY", "API_DB_PASSWORD"):
        monkeypatch.setenv(name, "placeholder")  # restored after the test
    env = tmp_path / ".env"
    env.write_text("POSTGRES_DB=aml\nAPI_KEY=\n# keep\n")
    assert sorted(ensure_secrets(env)) == ["API_DB_PASSWORD", "API_KEY"]
    lines = env.read_text().splitlines()
    assert [env_key(line) for line in lines] == [
        "POSTGRES_DB",
        "API_KEY",
        None,
        "API_DB_PASSWORD",
    ]
    assert all(
        len(line.split("=", 1)[1]) == 32 for line in lines if line.startswith("API_")
    )
    assert mode(env) == 0o600
    before = env.read_text()
    assert ensure_secrets(env) == []  # second run keeps the values
    assert env.read_text() == before
