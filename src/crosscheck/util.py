"""Small shared helpers."""

from __future__ import annotations

import datetime as _dt
import os
import re
import secrets
import string
from pathlib import Path

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str, length: int = 10) -> str:
    """Random lowercase ID with a type prefix, e.g. ``r_k3j9x0a1bc``."""
    return f"{prefix}_" + "".join(secrets.choice(_ALPHABET) for _ in range(length))


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def iso(ts: _dt.datetime | None = None) -> str:
    return (ts or now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")


def clean_text(value: str | None, limit: int = 2000) -> str:
    """Strip control and bidi-override characters and truncate.

    Used for every string that originates from a sandbox or from PR metadata before it is
    stored or shown anywhere.
    """
    if not value:
        return ""
    value = _CONTROL.sub("", str(value))
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


def write_private(path: Path, data: bytes | str, mode: int = 0o600) -> None:
    """Write a file that only the owner can read, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)


def read_secret(path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text().strip() or None


REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
