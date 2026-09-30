import pytest

from crosscheck.config import hostd_config
from crosscheck.hostd import qmp
from crosscheck.hostd.backends.base import VMRecord, systemd_service
from crosscheck.hostd.backends.proxmox import ProxmoxBackend
from crosscheck.hostd.backends.qemu import QemuBackend, cpu_arg
from crosscheck.presets import resolve


def test_text_and_combo_events():
    batches = qmp.text_events("aB!")
    assert batches[1][0] == qmp.key_event("shift", True)
    assert [e["data"]["key"]["data"] for e in qmp.combo_events("ctrl+shift+s")[:3]] == ["ctrl", "shift", "s"]
    assert qmp.keyname_to_qcode("Return") == "ret" and qmp.keyname_to_qcode("Page_Down") == "pgdn"
    with pytest.raises(ValueError):
        qmp.text_events("ü")


def test_abs_mapping_edges():
    ev = qmp.abs_events(1919, 1079, 1920, 1080)
    assert ev[0]["data"]["value"] == qmp.ABS_MAX and ev[1]["data"]["value"] == qmp.ABS_MAX


def test_cpu_arg_levels():
    arg = cpu_arg("x86-64-v2-AES", ["-avx2"], kvm=True)
    assert arg.startswith("qemu64,") and "aes=on" in arg and arg.endswith("avx2=off")
    assert cpu_arg("host", [], kvm=False) == "max"


def _rec(tmp_path, backend="qemu"):
    spec = resolve("potato", "linux", host_score=1000, host_cores=8, max_memory_mb=8192, allowed_gpu=[]).to_dict()
    return VMRecord(
        vm_id="v1",
        run_id="r_x1",
        platform="linux",
        spec=spec,
        run_dir=tmp_path / "v1",
        expires_at="2099-01-01T00:00:00Z",
        extra={"ids": {"gid": 1, "vm": 2, "relay": 3, "egress": 4}, "ttl_s": 600, "vmid": 9100},
    )


def test_qemu_argv_is_minimal_and_sandboxed(tmp_path):
    cfg = hostd_config({"run_dir": str(tmp_path), "state_dir": str(tmp_path), "dry_run": True})
    b = QemuBackend(cfg)
    argv = " ".join(b.qemu_argv(_rec(tmp_path)))
    assert "-nodefaults" in argv and "-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny" in argv
    for forbidden in ("usb", "virtio-serial", "qemu-guest-agent", "-soundhw", "spice", "9p", "virtfs"):
        assert forbidden not in argv
    assert "virtio-tablet-pci" in argv and "readonly=on" in argv
    assert "throttling.iops-read=120" in argv and "encrypt.key-secret=sec0" in argv
    assert "-netdev tap,id=n0,ifname=tap0" in argv


def test_qemu_systemd_start_commands(tmp_path):
    cfg = hostd_config({"run_dir": str(tmp_path), "state_dir": str(tmp_path), "dry_run": True})
    b = QemuBackend(cfg)
    rec = _rec(tmp_path)
    rec.run_dir.mkdir()
    b.start(rec)
    cmds = [" ".join(c) for c in b.sys.log]
    assert any("ip netns add cc-v1" in c for c in cmds)
    assert any("disable_ipv6=1" in c for c in cmds)
    vm = next(c for c in cmds if "crosscheck-vm-v1" in c)
    for prop in (
        "User=2",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "NetworkNamespacePath=/run/netns/cc-v1",
        "DevicePolicy=closed",
        "RuntimeMaxSec=600",
        "CPUQuota=60%",
    ):
        assert prop in vm
    egress = next(c for c in cmds if "crosscheck-egress-v1" in c)
    assert "IPAddressDeny=localhost link-local multicast 10.0.0.0/8" in egress
    assert "User=4" in egress


def test_systemd_service_without_devices_uses_private_devices():
    cmd = " ".join(systemd_service("u", ["true"]))
    assert "PrivateDevices=yes" in cmd


def test_proxmox_commands(tmp_path):
    cfg = hostd_config(
        {
            "backend": "proxmox",
            "run_dir": str(tmp_path),
            "state_dir": str(tmp_path),
            "dry_run": True,
            "proxmox": {"template_ids": {"linux": 9000}},
        }
    )
    b = ProxmoxBackend(cfg)
    rec = _rec(tmp_path, "proxmox")
    rec.extra = {}
    b.create(rec, {"job.json": b"{}"}, ["registry.npmjs.org"])
    b.start(rec)
    cmds = [" ".join(c) for c in b.sys.log]
    assert any(c.startswith("qm clone 9000 9100") and "--full 0" in c for c in cmds)
    qset = next(c for c in cmds if c.startswith("qm set 9100 --cores"))
    assert "--tablet 0" in qset and "-sandbox on" in qset and "bridge=ccbr9100" in qset
    assert any("net.ipv6.conf.ccbr9100.disable_ipv6=1" in c for c in cmds)
    assert not any("ip addr add" in c and "ccbr9100" in c for c in cmds)  # host never gets an IP on the bridge
    objs = b.destroy(rec)
    assert {o["kind"] for o in objs} >= {"vm", "bridge", "netns", "job-iso", "run-dir"}
