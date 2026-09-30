"""Proxmox VE backend, driving ``qm`` locally on the runner host.

Differences from the plain QEMU backend:
- Proxmox starts QEMU as root without an AppArmor profile. hostd still adds the seccomp sandbox and
  removes unneeded devices, but the two-device topology matters more here (docs/12).
- Each run gets its own bridge without a physical interface and without any host IP (IPv6 disabled
  on it). A veth pair connects the bridge to a per-run network namespace where the relay listens on
  the gateway address. The host itself is therefore not reachable from the guest at all.
- Overlays are Proxmox linked clones on the configured storage. Crypto-shredding requires that
  storage to sit on a dm-crypt volume whose key is regenerated at boot (set up by the installer).
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
from pathlib import Path

from ..jobmedia import RESULTS_DISK_SERIAL, build_job_iso, read_results_disk
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
from .qemu import SANDBOX, cpu_arg

ISO_DIR = Path("/var/lib/vz/template/iso")


class ProxmoxBackend(Backend):
    name = "proxmox"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.px = cfg["proxmox"]
        self._qmp: dict[str, QMP] = {}

    def info(self) -> dict:
        return {"backend": self.name, "storage": self.px.get("storage"), "executor": "systemd"}

    # ------------------------------------------------------------------ helpers
    def _vmid(self, rec: VMRecord) -> int:
        lo, hi = self.px.get("vmid_range", [9100, 9999])
        vmid = lo + rec.slot
        if vmid > hi:
            raise BackendError("no free VM ID in the configured range")
        return vmid

    def _names(self, rec: VMRecord) -> dict[str, str]:
        vmid = self._vmid(rec)
        return {
            "bridge": f"ccbr{vmid}",
            "veth_host": f"ccv{vmid}a",
            "veth_ns": f"ccv{vmid}b",
            "netns": f"cc-{rec.vm_id}",
            "iso": f"cc-{rec.vm_id}.iso",
            "relay": f"crosscheck-relay-{rec.vm_id}",
            "egress": f"crosscheck-egress-{rec.vm_id}",
        }

    def _template_id(self, platform: str) -> int:
        tid = self.px.get("template_ids", {}).get(platform)
        if not tid:
            raise BackendError(f"no Proxmox template ID configured for {platform!r}")
        return int(tid)

    def _args(self, rec: VMRecord) -> str:
        spec = rec.spec
        parts = [
            f"-qmp unix:{rec.run_dir / 'qmp.sock'},server=on,wait=off",
            "-device virtio-tablet-pci",
            "-device virtio-keyboard-pci",
            f"-sandbox {SANDBOX}",
        ]
        if spec.get("cpu_flags"):
            parts.append(f"-cpu {cpu_arg(spec['cpu_model'], spec['cpu_flags'], True)}")
        return " ".join(parts)

    # ------------------------------------------------------------------ lifecycle
    def create(self, rec: VMRecord, job_files: dict[str, bytes], egress_allow: list[str]) -> None:
        ids = slot_ids(self.cfg["uid_base"], rec.slot)
        ensure_dir(rec.run_dir, 0o770, 0, ids["gid"])
        rec.extra.update({"egress_allow": egress_allow, "ids": ids, "vmid": self._vmid(rec)})
        n = self._names(rec)
        vmid = str(self._vmid(rec))
        spec = rec.spec
        iso_path = ISO_DIR / n["iso"]
        build_job_iso(rec.run_dir / "job.iso", job_files)
        if not self.sys.dry_run:
            ISO_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rec.run_dir / "job.iso", iso_path)
        run = self.sys.run
        run(
            [
                "qm",
                "clone",
                str(self._template_id(rec.platform)),
                vmid,
                "--name",
                f"cc-{rec.vm_id}",
                "--full",
                "0",
                "--description",
                f"crosscheck run {rec.run_id} expires {rec.expires_at}",
            ]
        )
        disp = spec["displays"][0] if spec.get("displays") else {"width": 1920, "height": 1080}
        cpu_model = (
            spec["cpu_model"] if spec["cpu_model"] in ("host", "x86-64-v2-AES", "x86-64-v3", "x86-64-v4") else "host"
        )
        cpulimit = max(0.05, spec["cpu_quota_percent"] / 100)
        run(
            [
                "qm",
                "set",
                vmid,
                "--cores",
                str(spec["vcpus"]),
                "--cpu",
                cpu_model,
                "--cpulimit",
                f"{cpulimit:.2f}",
                "--memory",
                str(spec["memory_mb"]),
                "--balloon",
                "0",
                "--tablet",
                "0",
                "--vga",
                "std,memory=64",
                "--agent",
                "0",
                "--onboot",
                "0",
                "--protection",
                "0",
                "--net0",
                f"virtio=52:54:00:12:34:56,bridge={n['bridge']},firewall=0",
                "--ide2",
                f"local:iso/{n['iso']},media=cdrom",
                "--scsi1",
                f"{self.px['storage']}:1,serial={RESULTS_DISK_SERIAL},discard=on",
                "--args",
                self._args(rec),
                "--tags",
                f"crosscheck;run-{rec.run_id}",
            ]
        )
        run(
            ["qm", "set", vmid, "--delete", "serial0,serial1,audio0,usb0,usb1,usb2,usb3,hostpci0,efidisk0"], check=False
        )
        disk = spec.get("disk", {})
        if any(disk.values()):
            conf = self._config(vmid)
            key = next((k for k in ("scsi0", "virtio0", "sata0") if k in conf), None)
            if key:
                vol = conf[key].split(",")[0]
                opts = [f"{vol}", "discard=on"]
                for src, dst in (
                    ("mbps_read", "mbps_rd"),
                    ("mbps_write", "mbps_wr"),
                    ("iops_read", "iops_rd"),
                    ("iops_write", "iops_wr"),
                ):
                    if disk.get(src):
                        opts.append(f"{dst}={disk[src]}")
                run(["qm", "set", vmid, f"--{key}", ",".join(opts)])
        rec.extra["display"] = disp
        rec.state = "created"
        rec.save()

    def _config(self, vmid: str) -> dict:
        if self.sys.dry_run:
            return {"scsi0": "local-lvm:vm-0-disk-0,size=32G"}
        out = self.sys.run(["qm", "config", vmid]).stdout.decode()
        conf = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                conf[k.strip()] = v.strip()
        return conf

    def _setup_network(self, rec: VMRecord) -> None:
        n = self._names(rec)
        run = self.sys.run
        run(["ip", "link", "add", n["bridge"], "type", "bridge"])
        run(["sysctl", "-q", "-w", f"net.ipv6.conf.{n['bridge']}.disable_ipv6=1"])
        run(["ip", "link", "add", n["veth_host"], "type", "veth", "peer", "name", n["veth_ns"]])
        run(["sysctl", "-q", "-w", f"net.ipv6.conf.{n['veth_host']}.disable_ipv6=1"])
        run(["ip", "link", "set", n["veth_host"], "master", n["bridge"]])
        run(["ip", "netns", "add", n["netns"]])
        run(["ip", "link", "set", n["veth_ns"], "netns", n["netns"]])
        inns = ["ip", "netns", "exec", n["netns"]]
        run(inns + ["sysctl", "-q", "-w", "net.ipv6.conf.all.disable_ipv6=1"])
        run(inns + ["ip", "link", "set", "lo", "up"])
        run(inns + ["ip", "addr", "add", f"{GATEWAY_IP}/24", "dev", n["veth_ns"]])
        run(inns + ["ip", "link", "set", n["veth_ns"], "up"])
        run(["ip", "link", "set", n["veth_host"], "up"])
        run(["ip", "link", "set", n["bridge"], "up"])

    def start(self, rec: VMRecord) -> None:
        n = self._names(rec)
        ids = rec.extra["ids"]
        ttl = max(60, int(rec.extra.get("ttl_s", 3600)))
        self._setup_network(rec)
        allow = sorted(set(rec.extra.get("egress_allow", [])) | set(self.cfg["egress"].get("always_allow", [])))
        self.sys.run(
            systemd_service(
                n["egress"],
                [
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
                ],
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
                n["relay"],
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
                netns=n["netns"],
                runtime_max_s=ttl + 60,
                memory_max_mb=128,
            )
        )
        vmid = str(rec.extra["vmid"])
        self.sys.run(["qm", "start", vmid, "--timeout", "60"])
        tap = f"tap{vmid}i0"
        self.sys.run(["sysctl", "-q", "-w", f"net.ipv6.conf.{tap}.disable_ipv6=1"], check=False)
        if not self.sys.dry_run:
            q = QMP(rec.run_dir / "qmp.sock")
            q.connect(wait=30)
            self._qmp[rec.vm_id] = q
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
        target.unlink(missing_ok=True)
        self._q(rec).screendump(str(target))
        return ppm_to_png(target.read_bytes())

    def send_events(self, rec: VMRecord, events: list[dict]) -> None:
        self._q(rec).send_events(events)

    def state(self, rec: VMRecord) -> dict:
        vmid = str(rec.extra.get("vmid"))
        out = self.sys.run(["qm", "status", vmid], check=False).stdout.decode()
        running = "running" in out
        res = {"running": running}
        cg = Path(f"/sys/fs/cgroup/qemu.slice/{vmid}.scope")
        with contextlib.suppress(OSError, ValueError):
            for line in (cg / "cpu.stat").read_text().splitlines():
                if line.startswith("usage_usec"):
                    res["cpu_seconds"] = int(line.split()[1]) / 1e6
            res["memory_peak_mb"] = int((cg / "memory.peak").read_text()) // (1024 * 1024)
        return res

    def set_cpu(self, rec: VMRecord, quota_percent: int) -> None:
        self.sys.run(["qm", "set", str(rec.extra["vmid"]), "--cpulimit", f"{max(0.05, quota_percent / 100):.2f}"])

    def stop(self, rec: VMRecord) -> None:
        q = self._qmp.pop(rec.vm_id, None)
        if q:
            with contextlib.suppress(QMPError, OSError):
                q.close()
        if "vmid" in rec.extra:
            self.sys.run(["qm", "stop", str(rec.extra["vmid"]), "--skiplock", "1"], check=False)
        n = self._names(rec)
        for unit in (n["relay"], n["egress"]):
            self.sys.run(["systemctl", "stop", unit], check=False)
        rec.state = "stopped"
        if rec.run_dir.exists():
            rec.save()

    def read_results(self, rec: VMRecord, max_bytes: int) -> bytes | None:
        vmid = str(rec.extra["vmid"])
        conf = self._config(vmid)
        vol = conf.get("scsi1", "").split(",")[0]
        if not vol or self.sys.dry_run:
            return None
        path = self.sys.run(["pvesm", "path", vol]).stdout.decode().strip()
        if not re.match(r"^/[A-Za-z0-9_./-]+$", path):
            raise BackendError("unexpected volume path")
        if not Path(path).exists():
            self.sys.run(["lvchange", "-ay", "-K", path], check=False)
        return read_results_disk(Path(path), max_bytes)

    def destroy(self, rec: VMRecord) -> list[dict]:
        objs = []
        if rec.state != "stopped":
            with contextlib.suppress(Exception):
                self.stop(rec)
        n = self._names(rec)
        if "vmid" in rec.extra:
            vmid = str(rec.extra["vmid"])
            self.sys.run(
                ["qm", "destroy", vmid, "--purge", "1", "--destroy-unreferenced-disks", "1", "--skiplock", "1"],
                check=False,
            )
            objs += [
                self.deletion("vm", vmid, "destroy"),
                self.deletion("disk-overlay", vmid, "discard"),
                self.deletion("results-disk", vmid, "discard"),
            ]
        for unit in (n["relay"], n["egress"]):
            self.sys.run(["systemctl", "reset-failed", unit], check=False)
        objs.append(self.deletion("proxy", n["egress"], "destroy"))
        if self.sys.run(["ip", "netns", "delete", n["netns"]], check=False).returncode == 0:
            objs.append(self.deletion("netns", n["netns"], "destroy"))
        self.sys.run(["ip", "link", "delete", n["veth_host"]], check=False)
        if self.sys.run(["ip", "link", "delete", n["bridge"]], check=False).returncode == 0:
            objs.append(self.deletion("bridge", n["bridge"], "destroy"))
        iso = ISO_DIR / n["iso"]
        if iso.exists():
            iso.unlink()
        objs.append(self.deletion("job-iso", n["iso"], "unlink"))
        shutil.rmtree(rec.run_dir, ignore_errors=True)
        objs.append(self.deletion("run-dir", rec.vm_id, "unlink"))
        rec.state = "destroyed"
        return objs

    def leftovers(self, run_id: str) -> list[str]:
        left = []
        if self.sys.dry_run:
            return left
        out = self.sys.run(["qm", "list"], check=False).stdout.decode()
        for rec in list(self.vms.values()):
            if rec.run_id == run_id and f"cc-{rec.vm_id}" in out:
                left.append(f"vm cc-{rec.vm_id}")
        for d in self.known_run_dirs():
            with contextlib.suppress(Exception):
                if VMRecord.load(d).run_id == run_id:
                    left.append(f"run-dir {d.name}")
        return left
