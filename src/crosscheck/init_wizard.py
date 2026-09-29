"""``crosscheck init``: guided setup that writes configuration, secrets, systemd units and a first token.

Everything the wizard asks can also be passed as flags (``--yes`` for no questions). The wizard never
prints secrets except the one-time MCP token at the end.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths
from .util import REPO_RE, write_private


@dataclass
class Answers:
    role: str = "single"  # single | controller | runner
    backend: str = "qemu"  # qemu | proxmox | fake
    repos: list[str] = field(default_factory=list)
    github_auth: str = "token"  # token | app
    github_token: str | None = None
    github_app: dict | None = None  # id, pem, webhook_secret, slug
    anthropic_key: str | None = None
    openai_key: str | None = None
    operator_model: str = "claude-opus-5-5"
    public_url: str | None = None
    runner_ssh_host: str | None = None
    smoke_mode: str = "classes"
    systemd: bool = True
    executor: str = "systemd"


def ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    if secret:
        import getpass

        value = getpass.getpass(f"{prompt}{suffix}: ")
    else:
        value = input(f"{prompt}{suffix}: ")
    return value.strip() or (default or "")


def choose(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    print(prompt)
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}) {label}{'  (default)' if key == default else ''}")
    raw = input("> ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    return raw if raw in {k for k, _ in options} else default


def detect() -> dict:
    return {
        "root": paths.is_root(),
        "kvm": Path("/dev/kvm").exists(),
        "proxmox": shutil.which("pveversion") is not None,
        "systemd": Path("/run/systemd/system").exists(),
        "qemu": shutil.which("qemu-system-x86_64") is not None,
        "tailscale": shutil.which("tailscale") is not None,
        "cloudflared": shutil.which("cloudflared") is not None,
    }


# ---------------------------------------------------------------------- GitHub App manifest flow
def app_manifest(name: str, redirect_url: str, public_url: str | None) -> dict:
    manifest = {
        "name": name,
        "url": "https://github.com/ProfessorEngineergit/Crosscheck",
        "redirect_url": redirect_url,
        "public": False,
        "default_permissions": {
            "contents": "read",
            "pull_requests": "write",
            "checks": "write",
            "metadata": "read",
            "statuses": "write",
        },
        "default_events": ["pull_request", "pull_request_review", "issue_comment"],
    }
    if public_url:
        manifest["hook_attributes"] = {"url": public_url.rstrip("/") + "/webhook", "active": True}
    return manifest


def run_manifest_flow(public_url: str | None, org: str | None = None, port: int = 8765, timeout: float = 900) -> dict:
    """Serve a one-page form that creates the GitHub App, then exchange the code. Returns app data."""
    from .github import GitHub

    state = secrets.token_urlsafe(16)
    host = _lan_ip()
    redirect = f"http://{host}:{port}/callback"
    manifest = app_manifest(f"Crosscheck {socket.gethostname()[:20]} {secrets.token_hex(2)}", redirect, public_url)
    target = (
        f"https://github.com/organizations/{org}/settings/apps/new" if org else "https://github.com/settings/apps/new"
    )
    result: dict = {}
    done = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == f"/{state}":
                form = (
                    f"<form action='{target}?state={state}' method='post'>"
                    f"<input type='hidden' name='manifest' value='{_attr(json.dumps(manifest))}'>"
                    "<button class='cc-btn cc-btn--big cc-btn--block'>Create GitHub App</button></form>"
                )
                intro = (
                    "<p>One click. GitHub shows every permission before anything exists. We ask for the "
                    "minimum: read code, write checks, statuses and PR comments. No admin rights, no secrets, "
                    "no crypto mining.</p>"
                )
                self._send(200, setup_page("Create the Crosscheck GitHub App", intro + form))
            elif u.path == "/callback" and q.get("state", [""])[0] == state and q.get("code"):
                try:
                    data = GitHub.convert_manifest(q["code"][0])
                    result.update(
                        {
                            "id": data["id"],
                            "slug": data["slug"],
                            "pem": data["pem"],
                            "webhook_secret": data.get("webhook_secret"),
                        }
                    )
                    link = _attr(f"https://github.com/apps/{data['slug']}/installations/new")
                    self._send(
                        200,
                        setup_page(
                            "App created",
                            "<p>Last step: install it on the repositories Crosscheck should watch.</p>"
                            f"<p><a class='cc-btn cc-btn--big' href='{link}'>Install on repositories →</a></p>"
                            "<p class='cc-muted'>Then close this tab and head back to the terminal.</p>",
                        ),
                    )
                finally:
                    done.set()
            else:
                self._send(404, "not found")

        def _send(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)  # noqa: S104 - reachable from your phone
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://{host}:{port}/{state}"
    print(f"\nOpen this link on a device in the same network (or scan the code):\n  {url}\n")
    print_qr(url)
    done.wait(timeout)
    server.shutdown()
    if not result:
        raise RuntimeError("GitHub App was not created in time")
    return result


def setup_page(title: str, body: str) -> str:
    from .web.ui import STATIC

    css = (STATIC / "crosscheck.css").read_text()
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'><title>Crosscheck setup</title>"
        f"<style>{css}</style></head><body><div class='cc-wrap'><header class='cc-header'>"
        "<span class='cc-logo'>crosscheck<b>/setup</b></span>"
        "<span class='cc-tagline'>five questions, no boss fight</span>"
        f"</header><section class='cc-card cc-card--hero cc-narrow'><h1 class='cc-h1'>{title}</h1>{body}"
        "</section></div></body></html>"
    )


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace("'", "&#39;").replace("<", "&lt;")


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def print_qr(text: str) -> None:
    try:
        import segno

        segno.make(text, error="l").terminal(compact=True)
    except Exception:  # noqa: S110 - the QR code is a convenience
        pass


# ---------------------------------------------------------------------- writing
def write_all(a: Answers, cfg_dir: Path, data_dir: Path) -> dict[str, Path]:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    secrets_dir = cfg_dir / "secrets"
    github: dict = {"auth": a.github_auth, "repos": a.repos, "poll_interval_s": 60}
    if a.github_auth == "app" and a.github_app:
        write_private(secrets_dir / "github-app.pem", a.github_app["pem"])
        github.update({"app_id": int(a.github_app["id"]), "private_key_file": str(secrets_dir / "github-app.pem")})
        if a.github_app.get("webhook_secret"):
            write_private(secrets_dir / "webhook-secret", a.github_app["webhook_secret"])
            github["webhook_secret_file"] = str(secrets_dir / "webhook-secret")
    elif a.github_token:
        write_private(secrets_dir / "github-token", a.github_token)
        github["token_file"] = str(secrets_dir / "github-token")
    agents: dict = {
        "operator": {
            "adapter": "claude" if a.anthropic_key else "none",
            "model": a.operator_model,
            "effort": "low",
            "max_steps": 60,
        }
    }
    if a.anthropic_key:
        write_private(secrets_dir / "anthropic.key", a.anthropic_key)
        agents["anthropic_key_file"] = str(secrets_dir / "anthropic.key")
    if a.openai_key:
        write_private(secrets_dir / "openai.key", a.openai_key)
        agents["openai_key_file"] = str(secrets_dir / "openai.key")
    hostd = {
        "backend": a.backend,
        "state_dir": str(paths.hostd_state_dir()),
        "run_dir": str(paths.hostd_run_dir()),
        "isolation": {"executor": a.executor},
    }
    if a.role in ("single", "runner"):
        hostd_path = cfg_dir / "hostd.yaml"
        existing = yaml.safe_load(hostd_path.read_text()) if hostd_path.exists() else {}
        merged = {**hostd, **{k: v for k, v in (existing or {}).items() if k in ("templates", "calibration")}}
        hostd_path.write_text(yaml.safe_dump(merged, sort_keys=False))
        written["hostd"] = hostd_path
    if a.role in ("single", "controller"):
        runner: dict = {"transport": "unix", "unix_socket": str(paths.hostd_socket())}
        if a.role == "controller":
            runner = {
                "transport": "ssh",
                "ssh": {
                    "host": a.runner_ssh_host,
                    "user": "root",
                    "key_file": str(secrets_dir / "runner_ed25519"),
                    "known_hosts_file": str(cfg_dir / "runner_known_hosts"),
                },
            }
        if a.backend == "fake" or not a.systemd:
            runner = {"transport": "local"}
        controller = {
            "version": 1,
            "data_dir": str(data_dir),
            "github": github,
            "runner": runner,
            "mcp": {
                "host": "127.0.0.1",
                "port": 8750,
                "public_url": a.public_url,
                "allowed_hosts": [urllib.parse.urlsplit(a.public_url).hostname] if a.public_url else [],
            },
            "agents": agents,
            "policy": {
                "topology": "two-devices" if a.role == "controller" else "single-machine",
                "defaults": {
                    "smoke": {"mode": a.smoke_mode, "classes": ["maintainer", "trusted"], "others": "approved"}
                },
            },
        }
        if runner.get("transport") == "local":
            controller["hostd"] = hostd
        path = cfg_dir / "controller.yaml"
        path.write_text(yaml.safe_dump(controller, sort_keys=False))
        os.chmod(path, 0o640)
        written["controller"] = path
    return written


def systemd_units(role: str, exe: str, cfg_dir: Path) -> dict[str, str]:
    units = {}
    if role in ("single", "runner"):
        units["crosscheck-hostd.service"] = f"""[Unit]
Description=Crosscheck hostd (creates, drives and destroys disposable VMs; holds no secrets)
After=network-online.target
Wants=network-online.target

[Service]
Environment=CROSSCHECK_CONFIG_DIR={cfg_dir}
ExecStartPre=/bin/sh -c 'echo 2 > /sys/kernel/mm/ksm/run || true'
ExecStart={exe} hostd --unix /run/crosscheck/hostd.sock --group crosscheck
Restart=on-failure
RuntimeDirectory=crosscheck
RuntimeDirectoryPreserve=yes

[Install]
WantedBy=multi-user.target
"""
    if role in ("single", "controller"):
        after = "crosscheck-hostd.service" if role == "single" else "network-online.target"
        units["crosscheck-controller.service"] = f"""[Unit]
Description=Crosscheck controller (policy, GitHub, agents, MCP bridge)
After={after} network-online.target
Wants=network-online.target

[Service]
User=crosscheck
Group=crosscheck
Environment=CROSSCHECK_CONFIG_DIR={cfg_dir}
ExecStart={exe} serve
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/crosscheck
StateDirectory=crosscheck

[Install]
WantedBy=multi-user.target
"""
    return units


def install_units(units: dict[str, str]) -> None:
    for name, text in units.items():
        Path(f"/etc/systemd/system/{name}").write_text(text)
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    for name in units:
        subprocess.run(["systemctl", "enable", "--now", name], check=False)


def ensure_user() -> None:
    if subprocess.run(["id", "crosscheck"], capture_output=True).returncode != 0:
        subprocess.run(
            ["useradd", "--system", "--home-dir", "/var/lib/crosscheck", "--shell", "/usr/sbin/nologin", "crosscheck"],
            check=False,
        )


def fix_permissions(cfg_dir: Path, data_dir: Path) -> None:
    if not paths.is_root():
        return
    import grp
    import pwd

    try:
        uid, gid = pwd.getpwnam("crosscheck").pw_uid, grp.getgrnam("crosscheck").gr_gid
    except KeyError:
        return
    for p in [cfg_dir, *cfg_dir.rglob("*")]:
        os.chown(p, 0, gid)
        os.chmod(p, 0o750 if p.is_dir() else 0o640)
    for p in [data_dir, *data_dir.rglob("*")]:
        os.chown(p, uid, gid)


# ---------------------------------------------------------------------- interactive flow
def interactive(a: Answers, env: dict) -> Answers:
    from .web.banner import banner

    print(banner("setup, level 1: five questions, no boss fight. Enter accepts the default."))
    print("Detected: " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in env.items()))
    a.role = choose(
        "\nWhat does this machine do?",
        [
            ("single", "Everything on this machine (simplest, the monorepo of setups)"),
            ("controller", "Controller only; VMs live on a separate runner host (safest)"),
            ("runner", "Runner host only (the muscle; a controller elsewhere is the brain)"),
        ],
        a.role,
    )
    if a.role in ("single", "runner"):
        a.backend = "proxmox" if env["proxmox"] else "qemu"
        print(f"VM backend: {a.backend}")
    if a.role == "runner":
        return a
    if a.role == "controller":
        a.runner_ssh_host = ask("Runner host name or address")
    while not a.repos:
        raw = ask("Which repositories should we be suspicious of? (owner/name, comma separated)")
        a.repos = [r.strip() for r in raw.split(",") if REPO_RE.match(r.strip())]
    a.github_auth = choose(
        "\nHow should Crosscheck talk to GitHub?",
        [
            ("app", "GitHub App, created with one click (checks, comments, webhooks)"),
            ("token", "Fine-grained personal access token (the artisanal way)"),
        ],
        "app",
    )
    if a.github_auth == "token":
        a.github_token = ask(
            "Token (Contents: read, Pull requests: read/write, Commit statuses: read/write)", secret=True
        )
    a.anthropic_key = (
        ask(
            "Anthropic API key for the operator agent (Enter to skip: a script clicks around instead. "
            "Less smart, never hallucinates)",
            secret=True,
        )
        or None
    )
    a.public_url = (
        ask(
            "Public HTTPS URL for cloud agents and webhooks, e.g. via Tailscale Funnel "
            "(Enter to skip: LAN only, like it's 1999)"
        )
        or None
    )
    if a.github_auth == "app":
        a.github_app = run_manifest_flow(a.public_url)
    a.smoke_mode = choose(
        "\nWhen should PRs be started in a VM automatically?",
        [
            ("classes", "Maintainers and trusted friends automatically, strangers after your approval"),
            ("all", "Every PR, even from strangers on the internet (same isolation, bigger power bill)"),
            ("approved", "Only after you approve the exact commit"),
            ("manual", "Only when asked (label, /crosscheck run, or your agent)"),
        ],
        a.smoke_mode,
    )
    return a


def main(args) -> int:
    env = detect()
    a = Answers(
        role=args.role or "single",
        backend=args.backend or ("proxmox" if env["proxmox"] else "qemu"),
        repos=args.repo or [],
        public_url=args.public_url,
        runner_ssh_host=args.runner_host,
        smoke_mode=args.smoke_mode or "classes",
    )
    if args.github_token_file:
        a.github_auth, a.github_token = "token", Path(args.github_token_file).read_text().strip()
    if args.anthropic_key_file:
        a.anthropic_key = Path(args.anthropic_key_file).read_text().strip()
    if args.openai_key_file:
        a.openai_key = Path(args.openai_key_file).read_text().strip()
    a.systemd = env["systemd"] and env["root"] and not args.no_systemd
    a.executor = "systemd" if a.systemd else "direct"
    if not args.yes and not sys.stdin.isatty():
        print(
            "No terminal for questions. Run 'sudo crosscheck init' in a terminal, or pass --yes with flags "
            "(for example --repo owner/name --github-token-file FILE).",
            file=sys.stderr,
        )
        return 2
    if not args.yes:
        a = interactive(a, env)
    elif a.github_auth == "token" and not a.github_token and a.role != "runner":
        print("--yes needs --github-token-file (or run without --yes for the GitHub App flow)", file=sys.stderr)
        return 2
    cfg_dir, data_dir = paths.config_dir(), paths.data_dir()
    if a.systemd:
        ensure_user()
    written = write_all(a, cfg_dir, data_dir)
    for name, path in written.items():
        print(f"wrote {name} config: {path}")
    if a.systemd:
        fix_permissions(cfg_dir, data_dir)
        exe = shutil.which("crosscheck") or sys.argv[0]
        install_units(systemd_units(a.role, exe, cfg_dir))
        print("installed and started systemd services")
    else:
        print("no systemd/root: start manually with 'crosscheck serve' (and 'crosscheck hostd --unix ...' if used)")
    if a.role != "runner":
        from .agent_setup import instructions
        from .config import load_controller_config
        from .store import Store

        cfg = load_controller_config(cfg_dir / "controller.yaml")
        token = Store(cfg.store_dir).create_token("first-agent", ["read", "artifacts", "request"])
        if a.systemd:
            fix_permissions(cfg_dir, data_dir)
        url = a.public_url or f"http://127.0.0.1:{cfg.mcp.port}"
        print("\n" + instructions(url, token))
    print("\nSetup complete. Achievement unlocked: Sandbox Architect.\n")
    if a.role in ("single", "runner"):
        print("Next quest:  sudo crosscheck images build linux      (10 to 40 min, perfect for a coffee)")
        print("Then:        sudo crosscheck doctor --calibrate      (how fast is this box, really?)")
        print("Boss fight:  sudo crosscheck doctor --escape-test    (a VM tries to break out. Spoiler: it can't)")
    if a.role == "runner":
        print(
            "Allow the controller's key with a forced command (see docs/08-installation.md):\n"
            "  /etc/ssh/sshd_config.d/crosscheck.conf: PermitRootLogin forced-commands-only\n"
            '  /root/.ssh/authorized_keys: command="/usr/local/bin/crosscheck hostd --stdio",restrict ssh-ed25519 ...'
        )
    return 0
