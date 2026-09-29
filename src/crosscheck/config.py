"""Controller, hostd and repository configuration.

There are three configuration sources, with a strict order of authority:

1. ``controller.yaml`` on the controller (admin policy, secrets file paths). Upper bound.
2. ``hostd.yaml`` on the runner host (backend, templates, calibration). No secrets.
3. ``crosscheck.yaml`` in a repository, **read from the PR's base commit only**. It can
   only tighten the admin policy, never loosen it.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path(os.environ.get("CROSSCHECK_CONFIG_DIR", "/etc/crosscheck"))
DEFAULT_DATA_DIR = Path(os.environ.get("CROSSCHECK_DATA_DIR", "/var/lib/crosscheck"))


class ConfigError(ValueError):
    pass


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


# --------------------------------------------------------------------------- admin policy

TRUST_CLASSES = ("maintainer", "trusted", "known", "stranger", "bot", "blocked")
STAGES = ("static", "smoke", "deep")

DEFAULT_POLICY: dict[str, Any] = {
    "topology": "single-machine",
    "classes": {
        "trusted": {"users": [], "teams": []},
        "blocked": {"users": []},
        "bot": {"users": ["dependabot[bot]", "renovate[bot]", "github-actions[bot]"]},
    },
    "defaults": {
        "static": {"mode": "all"},
        "smoke": {"mode": "classes", "classes": ["maintainer", "trusted"], "others": "approved"},
        "deep": {"mode": "manual"},
    },
    "repos": {},
    "limits": {
        "maintainer": {
            "platforms": 99,
            "presets": 99,
            "max_minutes": 60,
            "parallel": 2,
            "per_day": 0,
            "hold": True,
            "gpu": ["virgl", "passthrough"],
            "egress_observe": True,
            "cloud_burst": True,
            "nested_virt": True,
        },
        "trusted": {
            "platforms": 99,
            "presets": 3,
            "max_minutes": 45,
            "parallel": 1,
            "per_day": 30,
            "hold": True,
            "gpu": [],
            "egress_observe": False,
            "cloud_burst": False,
            "nested_virt": True,
        },
        "known": {
            "platforms": 2,
            "presets": 2,
            "max_minutes": 30,
            "parallel": 1,
            "per_day": 10,
            "hold": False,
            "gpu": [],
            "egress_observe": False,
            "cloud_burst": False,
            "nested_virt": False,
        },
        "stranger": {
            "platforms": 1,
            "presets": 1,
            "max_minutes": 20,
            "parallel": 1,
            "per_day": 5,
            "hold": False,
            "gpu": [],
            "egress_observe": False,
            "cloud_burst": False,
            "nested_virt": False,
        },
        "bot": {
            "platforms": 1,
            "presets": 1,
            "max_minutes": 20,
            "parallel": 1,
            "per_day": 20,
            "hold": False,
            "gpu": [],
            "egress_observe": False,
            "cloud_burst": False,
            "nested_virt": False,
        },
        "blocked": {
            "platforms": 0,
            "presets": 0,
            "max_minutes": 0,
            "parallel": 0,
            "per_day": 0,
            "hold": False,
            "gpu": [],
            "egress_observe": False,
            "cloud_burst": False,
            "nested_virt": False,
        },
    },
    "deep": {"allowed_requesters": ["maintainer"], "monthly_budget_usd": 20.0, "confirm_above_usd": 2.0},
    "retention": {"reports_days": 90, "screenshots_days": 30, "logs_days": 14, "build_artifacts_days": 0},
}


@dataclass
class GithubConfig:
    auth: str = "token"  # "app" or "token"
    app_id: int | None = None
    private_key_file: str | None = None
    token_file: str | None = None
    webhook_secret_file: str | None = None
    poll_interval_s: int = 60
    repos: list[str] = field(default_factory=list)
    api_url: str = "https://api.github.com"


@dataclass
class RunnerConfig:
    transport: str = "unix"  # "unix" (hostd service on this machine), "ssh", or "local"
    unix_socket: str = "/run/crosscheck/hostd.sock"
    ssh_host: str | None = None
    ssh_user: str = "root"  # restricted by a forced command, see docs/08
    ssh_port: int = 22
    ssh_key_file: str | None = None
    ssh_known_hosts_file: str | None = None


@dataclass
class McpConfig:
    host: str = "127.0.0.1"
    port: int = 8750
    public_url: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)


@dataclass
class AgentsConfig:
    anthropic_key_file: str | None = None
    openai_key_file: str | None = None
    local_base_url: str | None = None  # OpenAI-compatible endpoint (Ollama, vLLM)
    operator: dict = field(
        default_factory=lambda: {"adapter": "claude", "model": "claude-opus-5-5", "effort": "low", "max_steps": 60}
    )
    security: dict = field(default_factory=lambda: {"adapter": "claude-code", "second_opinion": None})
    vulnerability: dict = field(default_factory=lambda: {"adapter": "codex"})


@dataclass
class ControllerConfig:
    data_dir: Path = DEFAULT_DATA_DIR
    github: GithubConfig = field(default_factory=GithubConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    policy: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_POLICY))
    hostd: dict = field(default_factory=dict)  # used when runner.transport == "local"
    source: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict, source: Path | None = None) -> ControllerConfig:
        raw = raw or {}
        if raw.get("version", 1) != 1:
            raise ConfigError("unsupported controller config version")
        cfg = cls(source=source)
        cfg.data_dir = Path(raw.get("data_dir", DEFAULT_DATA_DIR))
        cfg.github = _dataclass_from(GithubConfig, raw.get("github"))
        runner = dict(raw.get("runner") or {})
        ssh = runner.pop("ssh", {}) or {}
        cfg.runner = _dataclass_from(RunnerConfig, {**runner, **{f"ssh_{k}": v for k, v in ssh.items()}})
        cfg.mcp = _dataclass_from(McpConfig, raw.get("mcp"))
        agents = raw.get("agents") or {}
        base_agents = AgentsConfig()
        cfg.agents = AgentsConfig(
            anthropic_key_file=agents.get("anthropic_key_file"),
            openai_key_file=agents.get("openai_key_file"),
            local_base_url=agents.get("local_base_url"),
            operator=deep_merge(base_agents.operator, agents.get("operator")),
            security=deep_merge(base_agents.security, agents.get("security")),
            vulnerability=deep_merge(base_agents.vulnerability, agents.get("vulnerability")),
        )
        cfg.policy = deep_merge(DEFAULT_POLICY, raw.get("policy"))
        cfg.hostd = raw.get("hostd") or {}
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.github.auth not in ("app", "token"):
            raise ConfigError("github.auth must be 'app' or 'token'")
        if self.runner.transport not in ("local", "unix", "ssh"):
            raise ConfigError("runner.transport must be 'unix', 'ssh' or 'local'")
        if self.runner.transport == "ssh" and not self.runner.ssh_host:
            raise ConfigError("runner.ssh.host is required for transport 'ssh'")
        for stage in STAGES:
            mode = self.policy["defaults"][stage]["mode"]
            if mode not in ("all", "approved", "classes", "manual", "off"):
                raise ConfigError(f"policy.defaults.{stage}.mode: unknown mode {mode!r}")
        for name in self.policy["limits"]:
            if name not in TRUST_CLASSES:
                raise ConfigError(f"policy.limits: unknown trust class {name!r}")

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"


def _dataclass_from(kind, raw: dict | None):
    raw = raw or {}
    known = {f for f in kind.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown keys for {kind.__name__}: {sorted(unknown)}")
    return kind(**raw)


def load_controller_config(path: str | Path | None = None) -> ControllerConfig:
    p = Path(path) if path else DEFAULT_CONFIG_DIR / "controller.yaml"
    if not p.exists():
        raise ConfigError(f"controller config not found: {p} (run 'crosscheck init')")
    return ControllerConfig.from_dict(yaml.safe_load(p.read_text()) or {}, source=p)


# --------------------------------------------------------------------------- hostd

DEFAULT_HOSTD: dict[str, Any] = {
    "backend": "qemu",
    "state_dir": "/var/lib/crosscheck-hostd",
    "run_dir": "/run/crosscheck",
    "qemu_binary": "qemu-system-x86_64",
    "qemu_img_binary": "qemu-img",
    "uid_base": 810000,
    "max_parallel": 1,
    "max_vm_memory_mb": 16384,
    "templates": {},
    "calibration": {"host_score": None, "host_cores": None},
    "egress": {"always_allow": []},
    "isolation": {"executor": "systemd", "seccomp": True, "encrypt_overlays": True},
    "proxmox": {"node": None, "storage": "local-lvm", "template_ids": {}, "vmid_range": [9100, 9999]},
}


def hostd_config(raw: dict | None) -> dict:
    cfg = deep_merge(DEFAULT_HOSTD, raw)
    if cfg["backend"] not in ("qemu", "proxmox", "fake"):
        raise ConfigError("hostd.backend must be qemu, proxmox or fake")
    if cfg["isolation"]["executor"] not in ("systemd", "direct"):
        raise ConfigError("hostd.isolation.executor must be systemd or direct")
    return cfg


def load_hostd_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG_DIR / "hostd.yaml"
    raw = yaml.safe_load(p.read_text()) if p.exists() else {}
    return hostd_config(raw)


# --------------------------------------------------------------------------- repository config

DEFAULT_REPO_CONFIG: dict[str, Any] = {
    "version": 1,
    "channel": "latest",
    "project": {"profile": "auto", "build": None, "launch": {}, "window_title": None},
    "platforms": {"linux": {"required": True}},
    "matrix": {
        "default": ["mainstream"],
        "label_run": ["office", "mainstream"],
        "label_matrix": ["potato", "office", "mainstream", "highend", "insane"],
    },
    "trust": {"smoke": {}, "deep": {}},
    "egress": {"allow": []},
    "smoke": {
        "max_steps": 40,
        "max_minutes": 10,
        "steps": [
            {
                "do": "Wait until the main window has fully loaded.",
                "expect": "The application's main window is visible and not showing an error.",
                "required": True,
            },
            {
                "do": "Open each top-level menu once and close it again with Escape.",
                "expect": "Menus open and close without errors or blank areas.",
                "required": False,
            },
            {
                "do": "Open the settings or preferences, if the app has them, then close them.",
                "expect": "A settings view appears, nothing is cut off.",
                "required": False,
            },
        ],
    },
    "checks": {"secrets": True, "sast": True, "dependencies": True, "canary_secrets": True},
    "performance": {"budgets": {}, "regression": {"max_slowdown_percent": 20, "fail_on_regression": False}},
    "agents": {},
}

STRICTNESS = {"all": 0, "auto": 0, "approved": 1, "manual": 2, "off": 3}


def repo_config(raw: dict | None) -> tuple[dict, list[str]]:
    """Merge a repository's crosscheck.yaml over defaults. Returns (config, warnings)."""
    warnings: list[str] = []
    raw = raw or {}
    if raw.get("version", 1) != 1:
        warnings.append("unsupported crosscheck.yaml version, using defaults")
        raw = {}
    cfg = deep_merge(DEFAULT_REPO_CONFIG, raw)
    # user-provided smoke steps replace the default list instead of merging
    if isinstance(raw.get("smoke"), dict) and "steps" in raw["smoke"]:
        cfg["smoke"]["steps"] = raw["smoke"]["steps"]
    steps = []
    for step in cfg["smoke"]["steps"][:50]:
        if not isinstance(step, dict) or not step.get("do"):
            warnings.append("ignored a smoke step without 'do'")
            continue
        entry = {
            "do": str(step["do"])[:500],
            "expect": str(step.get("expect", ""))[:500],
            "required": bool(step.get("required", False)),
        }
        actions = step.get("actions")
        if isinstance(actions, list):
            entry["actions"] = [a for a in actions[:20] if isinstance(a, dict) and isinstance(a.get("action"), str)]
            entry["expect_change"] = bool(step.get("expect_change", False))
            entry["settle_s"] = min(float(step.get("settle_s", 0.5) or 0.5), 10.0)
        steps.append(entry)
    cfg["smoke"]["steps"] = steps
    for stage, per_class in (cfg.get("trust") or {}).items():
        for klass, mode in (per_class or {}).items():
            if mode not in STRICTNESS:
                warnings.append(f"trust.{stage}.{klass}: unknown mode {mode!r} ignored")
    return cfg, warnings
