"""Command line interface. ``crosscheck --help`` for the overview."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

from . import __version__, paths


def _cfg(args):
    from .config import load_controller_config

    return load_controller_config(getattr(args, "config", None) or paths.config_dir() / "controller.yaml")


def _hostd_cfg(args):
    from .config import load_hostd_config

    return load_hostd_config(getattr(args, "hostd_config", None) or paths.config_dir() / "hostd.yaml")


# ---------------------------------------------------------------------- commands
def cmd_version(args) -> int:
    print(f"crosscheck {__version__}")
    return 0


def cmd_init(args) -> int:
    from .init_wizard import main

    return main(args)


def cmd_serve(args) -> int:
    import uvicorn

    from . import runtime
    from .http_app import build_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = _cfg(args)
    rt = runtime.build(cfg)
    if rt.gh is None:
        print("GitHub credentials are missing; run 'crosscheck init'", file=sys.stderr)
        return 2
    ctrl = rt.controller
    stop = threading.Event()

    def poller():
        while not stop.is_set():
            ctrl.poll_once()
            stop.wait(cfg.github.poll_interval_s)

    def housekeeping():
        while not stop.is_set():
            try:
                rt.store.apply_retention(cfg.policy["retention"])
                if rt.local_service is not None:
                    rt.local_service.rpc_janitor()
            except Exception:
                logging.exception("housekeeping failed")
            stop.wait(300)

    workers = max(1, int((ctrl.host or {}).get("max_parallel", 1)))
    for _ in range(workers):
        threading.Thread(target=ctrl.worker_loop, daemon=True).start()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=housekeeping, daemon=True).start()
    app = build_app(ctrl, rt.store, rt.keyproxy)
    try:
        uvicorn.run(app, host=cfg.mcp.host, port=cfg.mcp.port, log_level="warning")
    finally:
        stop.set()
        ctrl.stop()
    return 0


def cmd_hostd(args) -> int:
    from .hostd.server import janitor_loop, serve_stdio, serve_unix
    from .hostd.service import HostdService

    service = HostdService(_hostd_cfg(args))
    if args.stdio:
        serve_stdio(service)
        return 0
    if args.janitor:
        print(json.dumps(service.rpc_janitor()))
        return 0
    stop = threading.Event()
    threading.Thread(target=janitor_loop, args=(service, 60, stop), daemon=True).start()
    serve_unix(service, args.unix or str(paths.hostd_socket()), args.group)
    return 0


def cmd_doctor(args) -> int:
    import yaml

    from .doctor import controller_checks, format_checks, host_checks

    checks = []
    hcfg = None
    hpath = paths.config_dir() / "hostd.yaml"
    if hpath.exists() or not (paths.config_dir() / "controller.yaml").exists():
        hcfg = _hostd_cfg(args)
        if args.calibrate:
            from .bench import host_calibration

            cal = host_calibration()
            raw = yaml.safe_load(hpath.read_text()) if hpath.exists() else {}
            raw = raw or {}
            raw["calibration"] = cal
            hpath.parent.mkdir(parents=True, exist_ok=True)
            hpath.write_text(yaml.safe_dump(raw, sort_keys=False))
            hcfg["calibration"] = cal
            print(f"calibrated: host score {cal['host_score']}, {cal['host_cores']} cores")
        checks += host_checks(hcfg)
    cpath = paths.config_dir() / "controller.yaml"
    if cpath.exists():
        from . import runtime

        cfg = _cfg(args)
        from .store import Store

        checks += controller_checks(cfg, Store(cfg.store_dir), runtime.make_github(cfg))
    print(format_checks(checks))
    if args.escape_test:
        rc = escape_test(hcfg or _hostd_cfg(args))
        if rc:
            return rc
    return 1 if any(c.ok is False for c in checks) else 0


def escape_test(hcfg: dict) -> int:
    import base64

    from .doctor import ESCAPE_TESTS
    from .evaluate import evaluate
    from .hostd.service import HostdService
    from .presets import resolve

    svc = HostdService(hcfg)
    spec = resolve("office", "linux", host_score=1000, host_cores=4, max_memory_mb=4096, allowed_gpu=[], strategies=[])
    vm = svc.call(
        "create_vm",
        {
            "run_id": "r_escapetest",
            "platform": "linux",
            "spec": spec.to_dict(),
            "job": {"kind": "escape-test", "targets": ESCAPE_TESTS},
            "ttl_s": 600,
            "egress_allow": ["example.com"],
        },
    )
    try:
        svc.call("start_vm", {"vm_id": vm["vm_id"]})
        deadline = time.time() + 300
        result = None
        while time.time() < deadline:
            time.sleep(5)
            res = svc.call("read_results", {"vm_id": vm["vm_id"]})
            if res.get("archive_b64"):
                parsed = evaluate(base64.b64decode(res["archive_b64"]))
                result = (parsed.get("json") or {}).get("escape.json")
                if result:
                    break
    finally:
        svc.call("destroy_vm", {"vm_id": vm["vm_id"]})
    if not result:
        print("[FAIL] escape test: no result from the test VM (is the linux image built?)")
        return 1
    bad = 0
    for name, outcome in sorted(result.items()):
        ok = not str(outcome).startswith("reachable")
        bad += 0 if ok else 1
        print(f"[{'ok  ' if ok else 'FAIL'}] escape test, {name}: {outcome}")
    return 1 if bad else 0


def cmd_images(args) -> int:
    from .images.build import build, import_proxmox, register_template

    hcfg = _hostd_cfg(args)
    template = build(
        args.kind, hcfg, channel=args.channel, profiles=args.profiles.split(",") if args.profiles else None
    )
    if hcfg["backend"] == "proxmox":
        vmid = int(args.vmid or {"linux": 9000, "analysis": 9001, "review": 9002}[args.kind])
        import_proxmox(template, args.kind, vmid, hcfg["proxmox"]["storage"])
        import yaml

        p = paths.config_dir() / "hostd.yaml"
        raw = yaml.safe_load(p.read_text()) or {}
        raw.setdefault("proxmox", {}).setdefault("template_ids", {})[args.kind] = vmid
        p.write_text(yaml.safe_dump(raw, sort_keys=False))
    else:
        register_template(paths.config_dir() / "hostd.yaml", args.kind, template)
    print(f"registered '{args.kind}' image")
    return 0


def cmd_policy(args) -> int:
    import yaml

    cfg = _cfg(args)
    if args.action == "show":
        print(yaml.safe_dump(cfg.policy, sort_keys=False))
        return 0
    if args.action == "set":
        if args.stage not in ("static", "smoke", "deep") or args.mode not in (
            "all",
            "approved",
            "classes",
            "manual",
            "off",
        ):
            print("usage: crosscheck policy set <repo> <static|smoke|deep> <all|approved|classes|manual|off>")
            return 2
        raw = yaml.safe_load(cfg.source.read_text())
        raw.setdefault("policy", {}).setdefault("repos", {}).setdefault(args.repo, {})[args.stage] = {"mode": args.mode}
        cfg.source.write_text(yaml.safe_dump(raw, sort_keys=False))
        from .store import Store

        Store(cfg.store_dir).audit("cli", "policy set", f"{args.repo} {args.stage} {args.mode}")
        print(f"{args.repo}: {args.stage} = {args.mode} (restart the controller to apply)")
        return 0
    if args.action == "explain":
        from . import policy as pol
        from . import runtime

        rt = runtime.build(cfg)
        facts, _ = rt.controller.gather(args.repo, int(args.pr))
        repo_cfg, source, warnings = rt.controller.load_repo_cfg(args.repo, facts.base_sha)
        facts.approvals_on_head = rt.gh.approvals_on(args.repo, int(args.pr), facts.head_sha)
        d = pol.decide(facts, pol.Trigger(kind="push"), cfg.policy, repo_cfg)
        print(pol.explain(d))
        print(f"config source: {source}")
        for w in warnings:
            print(f"warning: {w}")
        return 0
    return 2


def cmd_token(args) -> int:
    from .store import ALL_SCOPES, Store

    cfg = _cfg(args)
    store = Store(cfg.store_dir)
    if args.action == "create":
        scopes = args.scopes.split(",") if args.scopes else ["read", "artifacts", "request"]
        token = store.create_token(args.name or "agent", scopes, expires_days=args.days)
        print(token)
        return 0
    if args.action == "list":
        for t in store.list_tokens():
            print(
                f"{t['token_id']} {t['name']} [{t['scopes']}] created {t['created_at']} "
                f"expires {t['expires_at']} {'REVOKED' if t['revoked'] else ''}"
            )
        return 0
    if args.action == "revoke":
        n = store.revoke_token(args.name, all_tokens=args.all)
        print(f"revoked {n} token(s)")
        return 0
    print("scopes: " + ", ".join(ALL_SCOPES))
    return 2


def cmd_agent(args) -> int:
    from . import agent_setup as ag

    if args.action == "connect":
        from .store import Store

        cfg = _cfg(args)
        scopes = args.scopes.split(",") if args.scopes else ["read", "artifacts", "request"]
        token = Store(cfg.store_dir).create_token(args.name or "agent", scopes)
        url = args.url or cfg.mcp.public_url or f"http://{cfg.mcp.host}:{cfg.mcp.port}"
        print(ag.instructions(url, token))
        return 0
    if args.action == "setup":
        if args.target == "claude-project":
            path = ag.setup_claude_project(Path(args.dir or "."))
            print(f"wrote {path}; commit it so cloud sessions load the Crosscheck plugin")
            return 0
        if not args.url or not args.token:
            print("--url and --token are required", file=sys.stderr)
            return 2
        fn = ag.setup_claude if args.target == "claude" else ag.setup_codex
        for line in fn(args.url, args.token, dry_run=args.dry_run):
            print(line)
        print("open a new terminal (or source ~/.config/crosscheck/env) so the variables are set")
        return 0
    return 2


def cmd_run(args) -> int:
    from . import policy as pol
    from . import runtime

    logging.basicConfig(level=logging.INFO)
    cfg = _cfg(args)
    rt = runtime.build(cfg)
    trig = pol.Trigger(kind="mcp", stage=args.stage, requested_by="cli", requester_class="maintainer")
    d = rt.controller.consider(
        args.repo,
        int(args.pr),
        trig,
        platforms=args.platforms.split(",") if args.platforms else None,
        presets=args.presets.split(",") if args.presets else None,
    )
    print(pol.explain(d))
    try:
        plan = rt.controller.queue.get_nowait()
    except Exception:
        return 0
    report = rt.controller.execute(plan)
    from .report import summarize

    print(summarize(report))
    return 0 if report["status"] in ("success", "neutral") else 1


def cmd_status(args) -> int:
    from .store import Store

    cfg = _cfg(args)
    for r in Store(cfg.store_dir).list_runs(limit=args.limit):
        print(
            f"{r['run_id']} {r['repo']}#{r['pr']} {r['head_sha'][:10]} {r['stage']:6} {r['status']:15} "
            f"{r['created_at']} {r.get('headline') or ''}"
        )
    return 0


def cmd_kill(args) -> int:
    from . import runtime

    cfg = _cfg(args)
    rt = runtime.build(cfg)
    objs = rt.hostd.call("kill_all")["objects"]
    n = rt.store.revoke_token(all_tokens=True) if args.revoke_tokens else 0
    rt.store.audit("cli", "kill switch", f"{len(objs)} objects destroyed, {n} tokens revoked")
    print(f"destroyed {len(objs)} VM objects; revoked {n} tokens")
    print("to pause polling as well: systemctl stop crosscheck-controller")
    return 0


def cmd_demo(args) -> int:
    from .demo import run_demo

    report = run_demo(port=args.port, serve=not args.no_serve, scenario=args.scenario)
    return 0 if report else 1


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crosscheck",
        description="Check every pull request on every platform and "
        "hardware class, in disposable VMs, from any chat session.",
    )
    p.add_argument("--config", help="controller.yaml (default: config dir)")
    p.add_argument("--hostd-config", help="hostd.yaml (default: config dir)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("version")
    s.set_defaults(fn=cmd_version)

    s = sub.add_parser("init", help="guided setup")
    s.add_argument("--role", choices=["single", "controller", "runner"])
    s.add_argument("--backend", choices=["qemu", "proxmox", "fake"])
    s.add_argument("--repo", action="append", help="owner/name, repeatable")
    s.add_argument("--github-token-file")
    s.add_argument("--anthropic-key-file")
    s.add_argument("--openai-key-file")
    s.add_argument("--public-url")
    s.add_argument("--runner-host")
    s.add_argument("--smoke-mode", choices=["all", "classes", "approved", "manual"])
    s.add_argument("--no-systemd", action="store_true")
    s.add_argument("--yes", action="store_true", help="no questions, use flags and defaults")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("serve", help="run the controller (poller, workers, MCP bridge)")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("hostd", help="run the runner-host daemon")
    s.add_argument("--stdio", action="store_true", help="serve one session on stdin/stdout (SSH forced command)")
    s.add_argument("--unix", help="serve on this Unix socket")
    s.add_argument("--group", default=None, help="group allowed to use the Unix socket")
    s.add_argument("--janitor", action="store_true", help="destroy expired VMs once and exit")
    s.set_defaults(fn=cmd_hostd)

    s = sub.add_parser("doctor", help="check host, isolation and setup")
    s.add_argument("--calibrate", action="store_true", help="measure host speed for hardware presets")
    s.add_argument("--escape-test", action="store_true", help="boot a test VM and try to break out")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("images", help="build VM images")
    s.add_argument("action", choices=["build"])
    s.add_argument("kind", choices=["linux", "analysis", "review"])
    s.add_argument("--channel", default="latest", choices=["latest", "lts"])
    s.add_argument("--profiles", help="comma list: node,python,rust,cpp,java")
    s.add_argument("--vmid", help="Proxmox template VM ID")
    s.set_defaults(fn=cmd_images)

    s = sub.add_parser("policy", help="show, explain or change the admin policy")
    s.add_argument("action", choices=["show", "explain", "set"])
    s.add_argument("repo", nargs="?")
    s.add_argument("stage", nargs="?", help="for set: static|smoke|deep; for explain: PR number")
    s.add_argument("mode", nargs="?")
    s.set_defaults(fn=lambda a: cmd_policy(_explain_args(a)))

    s = sub.add_parser("token", help="MCP tokens")
    s.add_argument("action", choices=["create", "list", "revoke", "scopes"])
    s.add_argument("name", nargs="?")
    s.add_argument("--scopes")
    s.add_argument("--days", type=int, default=180)
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_token)

    s = sub.add_parser("agent", help="connect Claude Code or Codex")
    s.add_argument("action", choices=["connect", "setup"])
    s.add_argument("target", nargs="?", choices=["claude", "codex", "claude-project"])
    s.add_argument("--url")
    s.add_argument("--token")
    s.add_argument("--name")
    s.add_argument("--scopes")
    s.add_argument("--dir")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_agent)

    s = sub.add_parser("run", help="run a PR now (as a maintainer request)")
    s.add_argument("repo")
    s.add_argument("pr")
    s.add_argument("--stage", default="smoke", choices=["static", "smoke", "deep"])
    s.add_argument("--platforms")
    s.add_argument("--presets")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="recent runs")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("kill", help="kill switch: destroy all runner VMs")
    s.add_argument("--revoke-tokens", action="store_true")
    s.set_defaults(fn=cmd_kill)

    s = sub.add_parser("demo", help="full run without GitHub, KVM or keys, then serve MCP locally")
    s.add_argument("--port", type=int, default=8750)
    s.add_argument("--scenario", default="ok", choices=["ok", "crash", "build-fail", "egress", "canary", "slow"])
    s.add_argument("--no-serve", action="store_true")
    s.set_defaults(fn=cmd_demo)
    return p


def _explain_args(a):
    if a.action == "explain":
        a.pr = a.stage
    return a


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
