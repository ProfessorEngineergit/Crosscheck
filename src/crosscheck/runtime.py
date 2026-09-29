"""Wire configuration into running objects: store, GitHub client, hostd client, operator, controller."""

from __future__ import annotations

from dataclasses import dataclass

from .agents.scripted import ScriptedOperator
from .config import ControllerConfig, hostd_config
from .github import GitHub
from .hostd.client import LocalHostd, SshHostd, UnixHostd
from .keyproxy import KeyProxy
from .orchestrator import Controller
from .store import Store
from .util import read_secret


@dataclass
class Runtime:
    cfg: ControllerConfig
    store: Store
    gh: GitHub | None
    hostd: object
    keyproxy: KeyProxy
    controller: Controller
    local_service: object | None = None


def make_github(cfg: ControllerConfig) -> GitHub | None:
    g = cfg.github
    if g.auth == "app":
        key = read_secret(g.private_key_file)
        if not (g.app_id and key):
            return None
        return GitHub(g.api_url, app_id=g.app_id, private_key=key)
    token = read_secret(g.token_file)
    return GitHub(g.api_url, token=token) if token else None


def make_hostd(cfg: ControllerConfig):
    r = cfg.runner
    if r.transport == "ssh":
        return SshHostd(r.ssh_host, r.ssh_user, r.ssh_port, r.ssh_key_file, r.ssh_known_hosts_file), None
    if r.transport == "unix":
        return UnixHostd(r.unix_socket), None
    from .hostd.service import HostdService

    service = HostdService(hostd_config(cfg.hostd))
    return LocalHostd(service), service


def make_operator_factory(cfg: ControllerConfig):
    op = cfg.agents.operator
    key = read_secret(cfg.agents.anthropic_key_file)

    def factory(plan):
        if op.get("adapter") == "claude" and key:
            from .agents.claude import ClaudeOperator

            return ClaudeOperator(api_key=key, model=op.get("model", "claude-opus-5-5"), effort=op.get("effort", "low"))
        return ScriptedOperator()

    return factory


def build(cfg: ControllerConfig, gh: GitHub | None = None, hostd=None) -> Runtime:
    store = Store(cfg.store_dir)
    gh = gh if gh is not None else make_github(cfg)
    service = None
    if hostd is None:
        hostd, service = make_hostd(cfg)
    kp = KeyProxy(
        {"anthropic": read_secret(cfg.agents.anthropic_key_file), "openai": read_secret(cfg.agents.openai_key_file)}
    )
    controller = Controller(cfg, store, gh, hostd, operator_factory=make_operator_factory(cfg), keyproxy=kp)
    return Runtime(cfg, store, gh, hostd, kp, controller, service)
