"""Plain QEMU/KVM backend for any Linux host.

Production mode (``isolation.executor: systemd``) needs root for hostd, and gives every VM:
- its own network namespace containing one tap interface and nothing else,
- three transient systemd services with separate numeric UIDs (VM, relay, egress proxy),
- a LUKS-encrypted qcow2 overlay whose key is deleted once QEMU has opened the disk,
- hard CPU, memory, task and run-time limits, and a seccomp-sandboxed QEMU.

Development mode (``executor: direct``) runs QEMU as the current user without network at all.
It is only for trying things out and is reported as unsafe by ``crosscheck doctor``.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import signal
import subprocess
from pathlib import Path

from ..jobmedia import RESULTS_DISK_SERIAL, build_job_iso, create_results_disk, read_results_disk
from ..qmp import QMP, QMPError
from .base import (
    GATEWAY_IP,
    PROXY_PORT,
    Backend,
    BackendError,
    VMRecord,
    ensure_dir,
    ppm_to_png,
    python_exe,
    slot_ids,
    systemd_service,
)

SANDBOX = "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"


_V2 = ["cx16", "lahf-lm", "popcnt", "sse4.1", "sse4.2", "ssse3"]
_V3 = _V2 + ["avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"]
_V4 = _V3 + ["avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"]
# Microarchitecture levels as Proxmox defines them, expressed on upstream QEMU's qemu64 model.
LEVELS = {"x86-64-v2": _V2, "x86-64-v2-AES": [*_V2, "aes"], "x86-64-v3": [*_V3, "aes"], "x86-64-v4": [*_V4, "aes"]}


def cpu_arg(model: str, flags: list[str], kvm: bool) -> str:
    if model == "host" and not kvm:
        model = "max"
    parts = [model]
    if model in LEVELS:
        parts = ["qemu64"] + [f"{f}=on" for f in LEVELS[model]]
    for f in flags:
        f = f.strip()
        if f.startswith("-"):
            parts.append(f"{f[1:]}=off")
        elif f.startswith("+"):
            parts.append(f"{f[1:]}=on")
        elif f:
            parts.append(f)
    return ",".join(parts)


def drive_throttle(disk: dict) -> str:
    opts = []
    mb = 1024 * 1024
    if disk.get("mbps_read"):
        opts.append(f"throttling.bps-read={int(disk['mbps_read'] * mb)}")
    if disk.get("mbps_write"):
        opts.append(f"throttling.bps-write={int(disk['mbps_write'] * mb)}")
    if disk.get("iops_read"):
        opts.append(f"throttling.iops-read={int(disk['iops_read'])}")
    if disk.get("iops_write"):
        opts.append(f"throttling.iops-write={int(disk['iops_write'])}")
    return ("," + ",".join(opts)) if opts else ""


class QemuBackend(Backend):
    name = "qemu"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        iso_cfg = cfg["isolation"]
        self.systemd = iso_cfg["executor"] == "systemd"
        self.encrypt = bool(iso_cfg.get("encrypt_overlays", True))
        self.seccomp = bool(iso_cfg.get("seccomp", True))
        self.kvm = os.path.exists("/dev/kvm")
        self._procs: dict[str, subprocess.Popen] = {}
        self._qmp: dict[str, QMP] = {}

    def info(self) -> dict:
        return {
            "backend": self.name,
            "kvm": self.kvm,
            "executor": "systemd" if self.systemd else "direct",
            "encrypt_overlays": self.encrypt,
            "seccomp": self.seccomp,
        }

    # ------------------------------------------------------------------ helpers
    def _template(self, platform: str) -> Path:
        tpl = self.cfg["templates"].get(platform)
        if not tpl:
            raise BackendError(
                f"no template configured for platform {platform!r} (run 'crosscheck images build {platform}')"
            )
        path = Path(tpl)
        if not path.exists() and not self.sys.dry_run:
            raise BackendError(f"template not found: {path}")
        return path

    def _units(self, rec: VMRecord) -> dict[str, str]:
        return {
            "vm": f"crosscheck-vm-{rec.vm_id}",
            "relay": f"crosscheck-relay-{rec.vm_id}",
            "egress": f"crosscheck-egress-{rec.vm_id}",
        }

    def _netns(self, rec: VMRecord) -> str:
        return f"cc-{rec.vm_id}"

    def qemu_argv(self, rec: VMRecord) -> list[str]:
        spec = rec.spec
        d = rec.run_dir
        disp = spec["displays"][0] if spec.get("displays") else {"width": 1920, "height": 1080}
        argv = [
            self.cfg["qemu_binary"],
            "-name",
            f"crosscheck-{rec.vm_id}",
            "-nodefaults",
            "-no-user-config",
            "-machine",
            "q35",
            "-accel",
            "kvm" if self.kvm else "tcg",
            "-cpu",
            cpu_arg(spec["cpu_model"], spec["cpu_flags"], self.kvm),
            "-smp",
            str(spec["vcpus"]),
            "-m",
            f"{spec['memory_mb']}M",
            "-display",
            "none",
            "-vga",
            "none",
            "-device",
            f"VGA,vgamem_mb=64,edid=on,xres={disp['width']},yres={disp['height']}",
            "-device",
            "virtio-tablet-pci",
            "-device",
            "virtio-keyboard-pci",
            "-qmp",
            f"unix:{d / 'qmp.sock'},server=on,wait=off",
            "-rtc",
            "base=utc,clock=host",
            "-boot",
            "order=c",
        ]
        if self.seccomp:
            argv += ["-sandbox", SANDBOX]
        overlay = d / "overlay.qcow2"
        if self.encrypt:
            argv += ["-object", f"secret,id=sec0,file={d / 'overlay.key'},format=raw"]
            enc = ",encrypt.key-secret=sec0"
        else:
            enc = ""
        argv += [
            "-drive",
            f"if=none,id=d0,file={overlay},format=qcow2,discard=unmap,cache=none{enc}"
            f"{drive_throttle(spec.get('disk', {}))}",
            "-device",
            "virtio-blk-pci,drive=d0,bootindex=0",
            "-drive",
            f"if=none,id=job,file={d / 'job.iso'},format=raw,media=cdrom,readonly=on",
            "-device",
            "ide-cd,drive=job,bus=ide.0",
            "-drive",
            f"if=none,id=res,file={d / 'results.img'},format=raw,cache=none",
            "-device",
            f"virtio-blk-pci,drive=res,serial={RESULTS_DISK_SERIAL}",
        ]
        if self.systemd:
            argv += [
                "-netdev",
                "tap,id=n0,ifname=tap0,script=no,downscript=no",
                "-device",
                "virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56",
            ]
        else:
            argv += ["-nic", "none"]
        return argv

    # ------------------------------------------------------------------ lifecycle
    def create(self, rec: VMRecord, job_files: dict[str, bytes], egress_allow: list[str]) -> None:
        ids = slot_ids(self.cfg["uid_base"], rec.slot)
        ensure_dir(rec.run_dir, 0o770, 0, ids["gid"] if self.systemd else None)
        rec.extra["egress_allow"] = egress_allow
        rec.extra["ids"] = ids
        build_job_iso(rec.run_dir / "job.iso", job_files)
        create_results_disk(rec.run_dir / "results.img", self.cfg.get("results_disk_mb", 64))
        template = self._template(rec.platform)
        qimg = [self.cfg["qemu_img_binary"], "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(template)]
        if self.encrypt:
            key = rec.run_dir / "overlay.key"
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
            with os.fdopen(fd, "wb") as fh:
                fh.write(secrets.token_hex(32).encode())
            qimg += [
                "--object",
                f"secret,id=sec0,file={key},format=raw",
                "-o",
                "encrypt.format=luks,encrypt.key-secret=sec0",
            ]
        qimg.append(str(rec.run_dir / "overlay.qcow2"))
        self.sys.run(qimg)
        if self.systemd and os.geteuid() == 0:
            for name in ("job.iso", "results.img", "overlay.qcow2", "overlay.key"):
                p = rec.run_dir / name
                if p.exists():
                    os.chown(p, ids["vm"], ids["gid"])
        rec.state = "created"
        rec.save()

    def _setup_network(self, rec: VMRecord) -> None:
        ns = self._netns(rec)
        ids = rec.extra["ids"]
        run = self.sys.run
        run(["ip", "netns", "add", ns])
        inns = ["ip", "netns", "exec", ns]
        run(inns + ["sysctl", "-q", "-w", "net.ipv6.conf.all.disable_ipv6=1"])
        run(inns + ["sysctl", "-q", "-w", "net.ipv6.conf.default.disable_ipv6=1"])
        run(inns + ["ip", "link", "set", "lo", "up"])
        run(
            inns
            + ["ip", "tuntap", "add", "dev", "tap0", "mode", "tap", "user", str(ids["vm"]), "group", str(ids["gid"])]
        )
        run(inns + ["ip", "addr", "add", f"{GATEWAY_IP}/24", "dev", "tap0"])
        run(inns + ["ip", "link", "set", "tap0", "up"])

    def _egress_argv(self, rec: VMRecord) -> list[str]:
        import json

        allow = sorted(set(rec.extra.get("egress_allow", [])) | set(self.cfg["egress"].get("always_allow", [])))
        return [
            python_exe(),
            "-I",
            "-m",
            "crosscheck.hostd.egress",
            "--unix",
            str(rec.run_dir / "proxy.sock"),
            "--allow",
            ",".join(allow),
            "--log",
            str(rec.run_dir / "egress.jsonl"),
            "--profile",
            json.dumps(rec.spec.get("network", {})),
        ]

    def start(self, rec: VMRecord) -> None:
        spec = rec.spec
        ttl = max(60, int(rec.extra.get("ttl_s", 3600)))
        units = self._units(rec)
        if self.systemd:
            ids = rec.extra["ids"]
            self._setup_network(rec)
            self.sys.run(
                systemd_service(
                    units["egress"],
                    self._egress_argv(rec),
                    uid=ids["egress"],
                    gid=ids["gid"],
                    rw_paths=[str(rec.run_dir)],
                    runtime_max_s=ttl + 60,
                    memory_max_mb=256,
                    ip_deny_private=True,
                    ip_allow=self.cfg["egress"].get("dns_servers", ["127.0.0.53/32"]),
                )
            )
            self.sys.run(
                systemd_service(
                    units["relay"],
                    [
                        python_exe(),
                        "-I",
                        "-m",
                        "crosscheck.hostd.relay",
                        "--listen",
                        f"{GATEWAY_IP}:{PROXY_PORT}",
                        "--to",
                        str(rec.run_dir / "proxy.sock"),
                    ],
                    uid=ids["relay"],
                    gid=ids["gid"],
                    netns=self._netns(rec),
                    runtime_max_s=ttl + 60,
                    memory_max_mb=128,
                )
            )
            mem_cap = min(spec["memory_mb"], self.cfg["max_vm_memory_mb"]) + 512
            self.sys.run(
                systemd_service(
                    units["vm"],
                    self.qemu_argv(rec),
                    uid=ids["vm"],
                    gid=ids["gid"],
                    netns=self._netns(rec),
                    rw_paths=[str(rec.run_dir)],
                    cpu_quota=spec["cpu_quota_percent"],
                    memory_max_mb=mem_cap,
                    runtime_max_s=ttl,
                    devices=["/dev/kvm", "/dev/net/tun"],
                    tasks_max=256,
                )
            )
        else:
            if self.sys.dry_run:
                self.sys.log.append(self.qemu_argv(rec))
            else:
                log = open(rec.run_dir / "qemu.log", "wb")
                self._procs[rec.vm_id] = subprocess.Popen(
                    self.qemu_argv(rec), stdout=log, stderr=log, start_new_session=True
                )
        if not self.sys.dry_run:
            q = QMP(rec.run_dir / "qmp.sock")
            proc = self._procs.get(rec.vm_id)
            try:
                q.connect(wait=30, alive=(lambda: proc.poll() is None) if proc else None)
            except QMPError as exc:
                log_path = rec.run_dir / "qemu.log"
                tail = log_path.read_text(errors="replace")[-800:] if log_path.exists() else ""
                raise BackendError(f"VM did not come up: {exc} {tail}") from exc
            self._qmp[rec.vm_id] = q
        key = rec.run_dir / "overlay.key"
        if key.exists():
            key.unlink()  # QEMU has opened the encrypted overlay; the key now only exists in QEMU's memory
        rec.state = "running"
        rec.save()

    def _q(self, rec: VMRecord) -> QMP:
        q = self._qmp.get(rec.vm_id)
        if q is None:
            q = QMP(rec.run_dir / "qmp.sock")
            q.connect(wait=5)
            self._qmp[rec.vm_id] = q
        return q

    def screenshot(self, rec: VMRecord) -> tuple[bytes, int, int]:
        target = rec.run_dir / "screen.ppm"
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
        self._q(rec).screendump(str(target))
        png, w, h = ppm_to_png(target.read_bytes())
        target.unlink(missing_ok=True)
        rec.screen = (w, h)
        return png, w, h

    def send_events(self, rec: VMRecord, events: list[dict]) -> None:
        self._q(rec).send_events(events)

    def _cgroup(self, rec: VMRecord) -> Path:
        return Path("/sys/fs/cgroup/system.slice") / f"{self._units(rec)['vm']}.service"

    def state(self, rec: VMRecord) -> dict:
        running = False
        if self.systemd:
            running = self.sys.ok(["systemctl", "is-active", "--quiet", self._units(rec)["vm"]])
        else:
            p = self._procs.get(rec.vm_id)
            running = p is not None and p.poll() is None
        out: dict = {"running": running}
        cg = self._cgroup(rec)
        with contextlib.suppress(OSError, ValueError):
            for line in (cg / "cpu.stat").read_text().splitlines():
                if line.startswith("usage_usec"):
                    out["cpu_seconds"] = int(line.split()[1]) / 1e6
            out["memory_peak_mb"] = int((cg / "memory.peak").read_text()) // (1024 * 1024)
        if running:
            with contextlib.suppress(QMPError, OSError):
                out["qemu_status"] = self._q(rec).execute("query-status").get("status")
        return out

    def set_cpu(self, rec: VMRecord, quota_percent: int) -> None:
        if self.systemd:
            self.sys.run(
                [
                    "systemctl",
                    "set-property",
                    "--runtime",
                    self._units(rec)["vm"],
                    f"CPUQuota={max(5, int(quota_percent))}%",
                ]
            )

    def stop(self, rec: VMRecord) -> None:
        q = self._qmp.pop(rec.vm_id, None)
        if q:
            q.quit()
            q.close()
        if self.systemd:
            for unit in self._units(rec).values():
                self.sys.run(["systemctl", "stop", unit], check=False)
        else:
            p = self._procs.pop(rec.vm_id, None)
            if p and p.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=10)
        rec.state = "stopped"
        if rec.run_dir.exists():
            rec.save()

    def read_results(self, rec: VMRecord, max_bytes: int) -> bytes | None:
        path = rec.run_dir / "results.img"
        return read_results_disk(path, max_bytes) if path.exists() else None

    def destroy(self, rec: VMRecord) -> list[dict]:
        objs = []
        if rec.state != "stopped":
            with contextlib.suppress(Exception):
                self.stop(rec)
        objs.append(self.deletion("vm", rec.vm_id, "destroy"))
        if self.systemd:
            for unit in self._units(rec).values():
                self.sys.run(["systemctl", "reset-failed", unit], check=False)
            ns = self._netns(rec)
            if self.sys.run(["ip", "netns", "delete", ns], check=False).returncode == 0:
                objs.append(self.deletion("netns", ns, "destroy"))
                objs.append(self.deletion("tap", f"{ns}/tap0", "destroy"))
            objs.append(self.deletion("proxy", self._units(rec)["egress"], "destroy"))
        key = rec.run_dir / "overlay.key"
        key.unlink(missing_ok=True)
        if self.encrypt:
            objs.append(self.deletion("overlay-key", rec.vm_id, "crypto-shred"))
        for name, kind in (
            ("overlay.qcow2", "disk-overlay"),
            ("job.iso", "job-iso"),
            ("results.img", "results-disk"),
            ("qmp.sock", "socket"),
            ("proxy.sock", "socket"),
        ):
            p = rec.run_dir / name
            if p.exists():
                p.unlink()
                objs.append(self.deletion(kind, f"{rec.vm_id}/{name}", "unlink"))
        shutil.rmtree(rec.run_dir, ignore_errors=True)
        objs.append(self.deletion("run-dir", rec.vm_id, "unlink"))
        rec.state = "destroyed"
        return objs

    def leftovers(self, run_id: str) -> list[str]:
        left = []
        for d in self.known_run_dirs():
            with contextlib.suppress(Exception):
                if VMRecord.load(d).run_id == run_id:
                    left.append(f"run-dir {d.name}")
        if self.systemd and not self.sys.dry_run:
            out = self.sys.run(["ip", "netns", "list"], check=False).stdout.decode()
            for vm_id, rec in self.vms.items():
                if rec.run_id == run_id and f"cc-{vm_id}" in out:
                    left.append(f"netns cc-{vm_id}")
                if rec.run_id == run_id and self.sys.ok(
                    ["systemctl", "is-active", "--quiet", f"crosscheck-vm-{vm_id}"]
                ):
                    left.append(f"unit crosscheck-vm-{vm_id}")
        for vm_id, p in self._procs.items():
            rec = self.vms.get(vm_id)
            if rec and rec.run_id == run_id and p.poll() is None:
                left.append(f"process {p.pid}")
        return left
