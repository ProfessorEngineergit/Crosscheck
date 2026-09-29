"""Locate bundled data files (schema, presets) both from a wheel and from a source checkout."""

from __future__ import annotations

from functools import cache
from importlib import resources
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FALLBACK = {
    "report.schema.json": _REPO_ROOT / "schemas" / "report.schema.json",
    "hardware.yaml": _REPO_ROOT / "presets" / "hardware.yaml",
}


def data_path(name: str) -> Path:
    try:
        candidate = resources.files("crosscheck") / "data" / name
        if candidate.is_file():
            return Path(str(candidate))
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    fallback = _FALLBACK.get(name)
    if fallback and fallback.is_file():
        return fallback
    raise FileNotFoundError(f"bundled data file not found: {name}")


@cache
def load_yaml(name: str) -> dict:
    return yaml.safe_load(data_path(name).read_text())


def asset_path(*parts: str) -> Path:
    """Files shipped inside the package (guest runner, templates)."""
    return Path(str(resources.files("crosscheck").joinpath(*parts)))
