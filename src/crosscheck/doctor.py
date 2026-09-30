"""``crosscheck doctor``: check the host, the isolation, and the controller setup."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool | None  # None = warning / not applicable
    detail: str


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def host_checks(hostd_cfg: dict) -> list[Check]:
    out: list[Check] = []
    kvm = Path("/dev/kvm")
    out.append(
        Check(
            "KVM available",
            kvm.exists() and os.access(kvm, os.R_OK | os.W_OK) if kvm.exists() else False,
            "/dev/kvm present" if kvm.exists() else "no /dev/kvm: enable virtualisation in the BIOS/UEFI",
        )
    )
    for mod in ("kvm_intel", "kvm_amd"):
        p = Path(f"/sys/module/{mod}/parameters/nested")
        if p.exists():
            nested = p.read_text().strip() in ("Y", "1")
            out.append(
                Check(
                    "nested virtualisation (Android)",
                    nested if nested else None,
                    "enabled" if nested else f"disabled: set options {mod} nested=1 to run Android",
                )
            )
    qemu = shutil.which(hostd_cfg["qemu_binary"])
    if qemu:
        _code, text = _run([qemu, "--version"])
        m = re.search(r"version (\d+)\.(\d+)", text)
        ver = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        out.append(Check("QEMU", ver >= (7, 2), text.splitlines()[0] if text else "unknown version"))
        help_code, help_text = _run([qemu, "-sandbox", "help"])
        seccomp = "sandbox" in help_text or help_code == 0
        out.append(Check("QEMU seccomp sandbox", seccomp, "supported" if seccomp else "QEMU built without seccomp"))
    else:
        out.append(
            Check("QEMU", False, f"{hostd_cfg['qemu_binary']} not found: apt install qemu-system-x86 qemu-utils")
        )
    ksm = Path("/sys/kernel/mm/ksm/run")
    if ksm.exists():
        on = ksm.read_text().strip() != "0"
        out.append(
            Check(
                "KSM off (side channel)",
                not on,
                "off" if not on else "on: echo 2 > /sys/kernel/mm/ksm/run and disable ksmtuned",
            )
        )
    cmdline = Path("/proc/cmdline").read_text() if Path("/proc/cmdline").exists() else ""
    out.append(
        Check(
            "CPU mitigations",
            "mitigations=off" not in cmdline,
            "not disabled" if "mitigations=off" not in cmdline else "mitigations=off on the kernel command line",
        )
    )
    smt = Path("/sys/devices/system/cpu/smt/control")
    if smt.exists():
        val = smt.read_text().strip()
        out.append(Check("SMT", None if val == "on" else True, f"{val} (off is stronger against side channels)"))
    executor = hostd_cfg["isolation"]["executor"]
    if executor == "systemd":
        is_root = os.geteuid() == 0
        systemd = Path("/run/systemd/system").exists()
        out.append(Check("hostd runs as root", is_root, "needed for network namespaces and per-VM users"))
        out.append(Check("systemd", systemd, "PID 1 is systemd" if systemd else "systemd not running"))
    else:
        out.append(
            Check(
                "isolation executor",
                False,
                "executor 'direct' runs VMs as the current user without network namespaces. "
                "Only for trying things out, never for untrusted PRs",
            )
        )
    templates = hostd_cfg.get("templates") or {}
    if hostd_cfg["backend"] == "qemu":
        for kind in ("linux", "analysis", "review"):
            path = templates.get(kind)
            ok = bool(path) and Path(path).exists()
            out.append(
                Check(
                    f"image '{kind}'",
                    ok if kind == "linux" else (ok or None),
                    path if ok else f"missing: crosscheck images build {kind}",
                )
            )
    cal = hostd_cfg.get("calibration", {})
    out.append(
        Check(
            "calibration",
            bool(cal.get("host_score")) or None,
            f"host score {cal.get('host_score')}"
            if cal.get("host_score")
            else "not calibrated: crosscheck doctor --calibrate",
        )
    )
    return out


def controller_checks(cfg, store, gh) -> list[Check]:
    out: list[Check] = []
    out.append(Check("repositories", bool(cfg.github.repos), ", ".join(cfg.github.repos) or "none configured"))
    if gh:
        try:
            for repo in cfg.github.repos[:5]:
                gh.open_prs(repo, limit=1)
            out.append(Check("GitHub access", True, "app" if gh.is_app else "token"))
        except Exception as exc:
            out.append(Check("GitHub access", False, str(exc)[:200]))
    tokens = [t for t in store.list_tokens() if not t["revoked"]]
    out.append(
        Check(
            "MCP tokens", bool(tokens) or None, f"{len(tokens)} active" if tokens else "none: crosscheck agent connect"
        )
    )
    out.append(
        Check(
            "public URL",
            bool(cfg.mcp.public_url) or None,
            cfg.mcp.public_url or "not set: only local agents can connect, deep reviews are unavailable",
        )
    )
    topo = cfg.policy.get("topology")
    smoke_mode = cfg.policy["defaults"]["smoke"]["mode"]
    light = "green"
    if topo == "single-machine" and smoke_mode == "all":
        light = "yellow"
    out.append(Check("safety light", light == "green" or None, f"{light}: topology {topo}, smoke mode {smoke_mode}"))
    return out


def format_checks(checks: list[Check]) -> str:
    from .web.banner import AMBER, LIME, RED, c

    lines = []
    for ch in checks:
        mark = {True: c("[ok  ]", LIME), False: c("[FAIL]", RED), None: c("[warn]", AMBER)}[ch.ok]
        lines.append(f"{mark} {ch.name}: {ch.detail}")
    fails = sum(1 for ch in checks if ch.ok is False)
    warns = sum(1 for ch in checks if ch.ok is None)
    if fails:
        lines.append(f"\n{fails} problem(s). Fix these before letting strangers' code in.")
    elif warns:
        lines.append(f"\nNo failures, {warns} warning(s). Solid, with room for healthy paranoia.")
    else:
        lines.append("\nAll green. Tighter than a TLS handshake.")
    return "\n".join(lines)


ESCAPE_TESTS = [
    ("controller/host via gateway (SSH)", "10.0.2.2", 22),
    ("public DNS", "8.8.8.8", 53),
    ("public HTTPS by IP", "1.1.1.1", 443),
    ("cloud metadata", "169.254.169.254", 80),
    ("typical router 192.168.0.1", "192.168.0.1", 80),
    ("typical router 192.168.1.1", "192.168.1.1", 80),
    ("private 10.0.0.1", "10.0.0.1", 80),
]
