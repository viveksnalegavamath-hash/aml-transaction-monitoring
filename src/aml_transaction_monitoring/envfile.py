"""Safe edits of `.env`: replace values in place, write atomically with mode 600.

Shared by api.provision (new API secrets) and rotate_secrets (password
rotation). Nothing here prints or logs a value.
"""

import os
import secrets
import stat
import string
from pathlib import Path

ALPHABET = string.ascii_letters + string.digits
SECRET_LENGTH = 32  # about 190 bits


def new_secret(length: int = SECRET_LENGTH) -> str:
    """Random letters and digits: safe in .env, Compose and connection strings."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def env_key(line: str) -> str | None:
    """The key a `.env` line sets, or None for blank lines and comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key.removeprefix("export ").strip()


def replace_values(text: str, values: dict[str, str]) -> str:
    """`.env` text with `values` set: lines for those keys replaced in place
    (an empty `KEY=` is filled, never duplicated), keys not present yet
    appended, every other line kept byte for byte."""
    out = []
    seen = set()
    for line in text.splitlines(keepends=True):
        key = env_key(line)
        if key in values:
            out.append(f"{key}={values[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in values if k not in seen]
    if missing and out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out += [f"{k}={values[k]}\n" for k in missing]
    return "".join(out)


def other_lines(text: str, keys: tuple[str, ...]) -> list[str]:
    """Every non-blank line that does not set one of `keys` (comments included)."""
    return [
        line.rstrip("\n")
        for line in text.splitlines()
        if line.strip() and env_key(line) not in keys
    ]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def staged_path(path: Path) -> Path:
    """Where a new version is written before the swap (git-ignored: `.env.*`)."""
    return path.with_name(path.name + ".new")


def write_private(path: Path, text: str) -> None:
    """Create `path` with mode 600 from the start (never briefly readable by others).
    Fails if it already exists, e.g. left over from an interrupted run."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def swap_in(staged: Path, path: Path) -> None:
    """Replace `path` with `staged` in one atomic rename."""
    os.replace(staged, path)
