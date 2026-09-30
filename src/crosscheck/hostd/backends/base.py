"""Backend interface and shared host plumbing (commands, systemd services, network namespaces)."""

from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ...util import iso, now, parse_iso

GUEST_IP = "10.0.2.15"
GATEWAY_IP = "10.0.2.2"  # only the relay listens here; there is no route anywhere
PROXY_PORT = 3128


class BackendError(RuntimeError):
    pass


@dataclass
class VMRecord:
    vm_id: str
    run_id: str
    platform: str
    spec: dict
    run_dir: Path
    expires_at: str
    slot: int = 0
    state: str = "created"
    started_at: float | None = None
    screen: tuple[int, int] = (0, 0)
    extra: dict = field(default_factory=dict)

    def save(self) -> None:
        meta = {
            "vm_id": self.vm_id,
            "run_id": self.run_id,
            "platform": self.platform,
            "spec": self.spec,
            "expires_at": self.expires_at,
            "slot": self.slot,
            "state": self.state,
            "extra": self.extra,
        }
        (self.run_dir / "meta.json").write_text(json.dumps(meta))

    @classmethod
    def load(cls, run_dir: Path) -> VMRecord:
        meta = json.loads((run_dir / "meta.json").read_text())
        return cls(
            vm_id=meta["vm_id"],
            run_id=meta["run_id"],
            platform=meta["platform"],
            spec=meta["spec"],
            run_dir=run_dir,
            expires_at=meta["expires_at"],
            slot=meta.get("slot", 0),
            state=meta.get("state", "created"),
            extra=meta.get("extra", {}),
        )

    @property
    def expired(self) -> bool:
        return parse_iso(self.expires_at) < now()


class Sys:
    """Runs host commands. ``dry_run`` records instead of executing (tests, --dry-run)."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.log: list[list[str]] = []

    def run(
        self, argv: list[str], check: bool = True, input: bytes | None = None, timeout: float = 120
    ) -> subprocess.CompletedProcess:
        self.log.append(list(argv))
        if self.dry_run:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        proc = subprocess.run(argv, input=input, capture_output=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise BackendError(
                f"command failed ({proc.returncode}): {shlex.join(argv)}: {proc.stderr.decode(errors='replace')[-500:]}"
            )
        return proc

    def ok(self, argv: list[str]) -> bool:
        return self.run(argv, check=False).returncode == 0


def python_exe() -> str:
    return sys.executable


def systemd_service(
    unit: str,
    argv: list[str],
    *,
    uid: int | None = None,
    gid: int | None = None,
    netns: str | None = None,
    rw_paths: list[str] = (),
    cpu_quota: int | None = None,
    memory_max_mb: int | None = None,
    runtime_max_s: int | None = None,
    devices: list[str] = (),
    ip_deny_private: bool = False,
    ip_allow: list[str] = (),
    tasks_max: int = 64,
) -> list[str]:
    """Build a ``systemd-run`` command for a hardened transient service."""
    props = [
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes",
        "LockPersonality=yes",
        "RestrictRealtime=yes",
        "CollectMode=inactive-or-failed",
        f"TasksMax={tasks_max}",
        "MemorySwapMax=0",
        "KillMode=control-group",
        "TimeoutStopSec=5",
    ]
    if uid is not None:
        props += [f"User={uid}", f"Group={gid if gid is not None else uid}"]
    if netns:
        props.append(f"NetworkNamespacePath=/run/netns/{netns}")
    for p in rw_paths:
        props.append(f"ReadWritePaths={p}")
    if cpu_quota:
        props.append(f"CPUQuota={cpu_quota}%")
    if memory_max_mb:
        props.append(f"MemoryMax={memory_max_mb}M")
    if runtime_max_s:
        props.append(f"RuntimeMaxSec={runtime_max_s}")
    if devices:
        props.append("DevicePolicy=closed")
        props += [f"DeviceAllow={d} rw" for d in devices]
    else:
        props.append("PrivateDevices=yes")
    if ip_deny_private:
        props.append(
            "IPAddressDeny=localhost link-local multicast 10.0.0.0/8 172.16.0.0/12 "
            "192.168.0.0/16 100.64.0.0/10 fc00::/7 fe80::/10"
        )
        for a in ip_allow:
            props.append(f"IPAddressAllow={a}")
    cmd = ["systemd-run", "--quiet", "--no-block", f"--unit={unit}", "--service-type=exec"]
    for p in props:
        cmd += ["-p", p]
    return cmd + ["--"] + argv


def ppm_to_png(ppm: bytes) -> tuple[bytes, int, int]:
    img = Image.open(io.BytesIO(ppm))
    img.load()
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=False, compress_level=3)
    return out.getvalue(), img.width, img.height


def slot_ids(uid_base: int, slot: int) -> dict[str, int]:
    base = uid_base + slot * 4
    return {"gid": base, "vm": base + 1, "relay": base + 2, "egress": base + 3}


class Backend(ABC):
    """One implementation per virtualisation technology. All methods are called by hostd only."""

    name = "abstract"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sys = Sys(dry_run=bool(cfg.get("dry_run")))
        self.run_root = Path(cfg["run_dir"])
        self.vms: dict[str, VMRecord] = {}

    # ---- lifecycle
    @abstractmethod
    def create(self, rec: VMRecord, job_files: dict[str, bytes], egress_allow: list[str]) -> None: ...

    @abstractmethod
    def start(self, rec: VMRecord) -> None: ...

    @abstractmethod
    def screenshot(self, rec: VMRecord) -> tuple[bytes, int, int]: ...

    @abstractmethod
    def send_events(self, rec: VMRecord, events: list[dict]) -> None: ...

    @abstractmethod
    def state(self, rec: VMRecord) -> dict: ...

    @abstractmethod
    def set_cpu(self, rec: VMRecord, quota_percent: int) -> None: ...

    @abstractmethod
    def stop(self, rec: VMRecord) -> None: ...

    @abstractmethod
    def read_results(self, rec: VMRecord, max_bytes: int) -> bytes | None: ...

    @abstractmethod
    def destroy(self, rec: VMRecord) -> list[dict]: ...

    @abstractmethod
    def leftovers(self, run_id: str) -> list[str]: ...

    def egress_log(self, rec: VMRecord, limit: int = 2000) -> list[dict]:
        path = rec.run_dir / "egress.jsonl"
        out = []
        if path.exists():
            with open(path) as fh:
                for line in fh:
                    if len(out) >= limit:
                        break
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def info(self) -> dict:
        return {"backend": self.name}

    def known_run_dirs(self) -> list[Path]:
        if not self.run_root.exists():
            return []
        return [p for p in self.run_root.iterdir() if p.is_dir() and (p / "meta.json").exists()]

    def janitor(self) -> list[str]:
        """Destroy everything past its expiry, even without the controller."""
        destroyed = []
        for d in self.known_run_dirs():
            try:
                rec = self.vms.get(d.name) or VMRecord.load(d)
            except (OSError, ValueError, KeyError):
                shutil.rmtree(d, ignore_errors=True)
                continue
            if rec.expired:
                self.destroy(rec)
                self.vms.pop(rec.vm_id, None)
                destroyed.append(rec.vm_id)
        return destroyed

    @staticmethod
    def deletion(kind: str, ref: str, method: str) -> dict:
        return {"kind": kind, "ref": ref[:80], "method": method, "deleted_at": iso()}

    @staticmethod
    def wait_until(pred, timeout: float, interval: float = 0.2) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(interval)
        return pred()


def ensure_dir(path: Path, mode: int = 0o750, uid: int | None = None, gid: int | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    if uid is not None and os.geteuid() == 0:
        os.chown(path, uid, gid if gid is not None else uid)
