"""Default locations. Root installs use system paths; a normal user gets XDG paths (for trying things out)."""

from __future__ import annotations

import os
from pathlib import Path


def is_root() -> bool:
    return os.geteuid() == 0


def config_dir() -> Path:
    if os.environ.get("CROSSCHECK_CONFIG_DIR"):
        return Path(os.environ["CROSSCHECK_CONFIG_DIR"])
    if is_root():
        return Path("/etc/crosscheck")
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "crosscheck"


def data_dir() -> Path:
    if os.environ.get("CROSSCHECK_DATA_DIR"):
        return Path(os.environ["CROSSCHECK_DATA_DIR"])
    if is_root():
        return Path("/var/lib/crosscheck")
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "crosscheck"


def hostd_state_dir() -> Path:
    return Path("/var/lib/crosscheck-hostd") if is_root() else data_dir() / "hostd"


def hostd_run_dir() -> Path:
    if is_root():
        return Path("/run/crosscheck")
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/crosscheck-{os.getuid()}")) / "crosscheck"  # noqa: S108


def hostd_socket() -> Path:
    return Path("/run/crosscheck/hostd.sock") if is_root() else hostd_run_dir() / "hostd.sock"
