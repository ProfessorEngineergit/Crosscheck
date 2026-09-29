"""Build golden images from official Ubuntu cloud images with cloud-init.

    crosscheck images build linux      # desktop runner image (smoke runs)
    crosscheck images build analysis   # linux + gitleaks, semgrep, osv-scanner (stage 0)
    crosscheck images build review     # linux + Claude Code and Codex CLI (deep reviews)

Building is the only time an image has network access. It never sees PR code. The finished
image has a static address without gateway, no DNS, no IPv6, no SSH server and no guest agent.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pycdlib
import yaml

from ..redact import CANARY_MARKER
from ..util import iso, new_id

CLOUD_IMAGES = "https://cloud-images.ubuntu.com/releases"
CANDIDATES = {"latest": ["27.04", "26.10", "26.04", "25.10", "24.04"], "lts": ["26.04", "24.04"]}
KINDS = ("linux", "analysis", "review")
PROFILE_PACKAGES = {
    "node": ["nodejs", "npm"],
    "python": ["python3-venv", "python3-pip", "pipx"],
    "rust": ["cargo", "rustc"],
    "cpp": ["cmake", "ninja-build", "qt6-base-dev", "libgtk-3-dev"],
    "dotnet": [],
    "java": ["openjdk-21-jdk-headless"],
}


class ImageError(RuntimeError):
    pass


def resolve_ubuntu(channel: str = "latest", client: httpx.Client | None = None) -> tuple[str, str, str]:
    """Return (version, image_url, sha256) of the newest release available for the channel."""
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    for version in CANDIDATES.get(channel, CANDIDATES["latest"]):
        base = f"{CLOUD_IMAGES}/{version}/release"
        try:
            r = client.get(f"{base}/SHA256SUMS")
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        name = f"ubuntu-{version}-server-cloudimg-amd64.img"
        for line in r.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == name:
                return version, f"{base}/{name}", parts[0]
    raise ImageError("no Ubuntu cloud image found for channel " + channel)


def download(url: str, dest: Path, sha256: str) -> Path:
    if dest.exists() and _sha256(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    h = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, read=600)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                h.update(chunk)
                fh.write(chunk)
    if h.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise ImageError(f"checksum mismatch for {url}")
    tmp.replace(dest)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------- cloud-init
START_SCRIPT = """#!/bin/sh
# Crosscheck guest start: mount the job CD and run the guest runner shipped on it.
mkdir -p /media/ccjob 2>/dev/null
for i in $(seq 1 30); do
  mount /media/ccjob 2>/dev/null && break
  sleep 1
done
[ -f /media/ccjob/crosscheck-guest.py ] || exit 0
exec python3 /media/ccjob/crosscheck-guest.py /media/ccjob > "$HOME/.crosscheck-guest.log" 2>&1
"""

AUTOSTART = """[Desktop Entry]
Type=Application
Name=Crosscheck guest runner
Exec=/usr/local/bin/crosscheck-guest-start
X-GNOME-Autostart-enabled=true
NoDisplay=true
"""

FINAL_NETPLAN = {
    "network": {
        "version": 2,
        "ethernets": {
            "eth": {
                "match": {"macaddress": "52:54:00:12:34:56"},
                "set-name": "eth0",
                "dhcp4": False,
                "dhcp6": False,
                "link-local": [],
                "addresses": ["10.0.2.15/24"],
            }
        },
    }
}


def canary_files() -> dict[str, str]:
    m = CANARY_MARKER
    return {
        "/home/runner/.aws/credentials": f"[default]\naws_access_key_id = AKIA{m}0001\n"
        f"aws_secret_access_key = {m}/Kx9y8zQ1w2e3r4t5y6u7i8o9p0\n",
        "/home/runner/.ssh/id_ed25519": f"-----BEGIN OPENSSH PRIVATE KEY-----\n{m}b3BlbnNzaC1rZXktdjEAAAAA\n"
        "-----END OPENSSH PRIVATE KEY-----\n",
        "/home/runner/.git-credentials": f"https://runner:ghp_{m}000000000000000000000000@github.com\n",
        "/home/runner/.npmrc": f"//registry.npmjs.org/:_authToken=npm_{m}00000000000000000000000\n",
        "/home/runner/.config/gh/hosts.yml": (
            f"github.com:\n    oauth_token: gho_{m}0000000000000000\n    user: runner\n"
        ),
    }


def user_data(kind: str, profiles: list[str] | None = None) -> str:
    if kind not in KINDS:
        raise ImageError(f"unknown image kind {kind!r}")
    profiles = profiles or list(PROFILE_PACKAGES)
    packages = [
        "xfce4",
        "xfce4-terminal",
        "lightdm",
        "lightdm-gtk-greeter",
        "xserver-xorg",
        "x11-xserver-utils",
        "dbus-x11",
        "python3",
        "python3-tk",
        "git",
        "curl",
        "ca-certificates",
        "build-essential",
        "fonts-dejavu",
        "fonts-noto-core",
        "fonts-noto-cjk",
        "fonts-noto-color-emoji",
        "xdg-utils",
        "libnss3",
        "libgbm1",
        "libgtk-3-0t64",
        "mesa-utils",
        "unzip",
        "xz-utils",
        "file",
    ]
    for p in profiles:
        packages += PROFILE_PACKAGES.get(p, [])
    files = [
        {"path": "/usr/local/bin/crosscheck-guest-start", "permissions": "0755", "content": START_SCRIPT},
        {"path": "/etc/xdg/autostart/crosscheck-guest.desktop", "content": AUTOSTART},
        {
            "path": "/etc/lightdm/lightdm.conf.d/50-crosscheck.conf",
            "content": "[Seat:*]\nautologin-user=runner\nautologin-user-timeout=0\nautologin-session=xfce\n",
        },
        {
            "path": "/etc/udev/rules.d/60-crosscheck-results.rules",
            "content": 'KERNEL=="vd*", ENV{ID_SERIAL}=="ccresults", OWNER="runner", MODE="0600"\n',
        },
        {
            "path": "/etc/sysctl.d/99-crosscheck.conf",
            "content": "net.ipv6.conf.all.disable_ipv6 = 1\nnet.ipv6.conf.default.disable_ipv6 = 1\n"
            "kernel.core_pattern = /var/crash/core.%e.%p\n",
        },
        {"path": "/etc/crosscheck/netplan-final.yaml", "permissions": "0600", "content": yaml.safe_dump(FINAL_NETPLAN)},
        {
            "path": "/etc/environment.d/90-crosscheck.conf",
            "content": "http_proxy=http://10.0.2.2:3128\nhttps_proxy=http://10.0.2.2:3128\n"
            "HTTP_PROXY=http://10.0.2.2:3128\nHTTPS_PROXY=http://10.0.2.2:3128\n",
        },
        {
            "path": "/etc/crosscheck/image.json",
            "content": json.dumps({"kind": kind, "built_at": iso(), "profiles": profiles}) + "\n",
        },
    ]
    for path, content in canary_files().items():
        files.append({"path": path, "content": content, "permissions": "0600", "owner": "root:root", "defer": True})
    runcmd = [
        "echo 'LABEL=CCJOB /media/ccjob iso9660 ro,user,noauto 0 0' >> /etc/fstab",
        "mkdir -p /media/ccjob /var/crash && chmod 1777 /var/crash",
        "apt-get install -y libasound2t64 || apt-get install -y libasound2 || true",
        "apt-get purge -y unattended-upgrades light-locker xfce4-screensaver xfce4-power-manager apport "
        "openssh-server snapd qemu-guest-agent || true",
        "systemctl set-default graphical.target",
        "systemctl mask systemd-resolved.service apt-daily.timer apt-daily-upgrade.timer motd-news.timer || true",
        "chown -R runner:runner /home/runner && chmod 700 /home/runner/.ssh /home/runner/.aws || true",
        "find /home/runner/.aws /home/runner/.ssh /home/runner/.git-credentials /home/runner/.npmrc "
        "/home/runner/.config/gh -type f -exec touch -a -d '2020-01-01' {} + || true",
    ]
    if kind == "analysis":
        runcmd += [
            "pipx install --global semgrep || pip3 install --break-system-packages semgrep",
            "mkdir -p /opt/semgrep-rules && "
            "curl -fsSL https://semgrep.dev/c/p/default -o /opt/semgrep-rules/default.yml",
            "sh -c 'set -e; cd /tmp; V=$(curl -fsSL https://api.github.com/repos/gitleaks/gitleaks/releases/latest "
            '| python3 -c "import json,sys;print(json.load(sys.stdin)[\\"tag_name\\"].lstrip(\\"v\\"))"); '
            "curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v$V/gitleaks_${V}_linux_x64.tar.gz; "
            "curl -fsSLO https://github.com/gitleaks/gitleaks/releases/download/v$V/gitleaks_${V}_checksums.txt; "
            "grep linux_x64.tar.gz gitleaks_${V}_checksums.txt | sha256sum -c -; "
            "tar -xzf gitleaks_${V}_linux_x64.tar.gz gitleaks; install -m 0755 gitleaks /usr/local/bin/'",
            "sh -c 'set -e; cd /tmp; curl -fsSL -o osv-scanner https://github.com/google/osv-scanner/releases/latest/"
            "download/osv-scanner_linux_amd64; install -m 0755 osv-scanner /usr/local/bin/'",
        ]
    if kind == "review":
        runcmd += [
            "curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - && apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-code @openai/codex",
        ]
    runcmd += [
        "rm -f /etc/netplan/*.yaml && "
        "install -m 0600 /etc/crosscheck/netplan-final.yaml /etc/netplan/90-crosscheck.yaml",
        "mkdir -p /etc/cloud/cloud.cfg.d && echo 'network: {config: disabled}' > "
        "/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg",
        "apt-get clean && rm -rf /var/lib/apt/lists/*",
        "cloud-init clean --logs --machine-id || truncate -s 0 /etc/machine-id",
        "touch /etc/cloud/cloud-init.disabled",
        "fstrim -av || true",
    ]
    doc = {
        "hostname": "crosscheck-guest",
        "users": [
            {
                "name": "runner",
                "gecos": "Crosscheck runner",
                "shell": "/bin/bash",
                "lock_passwd": True,
                "groups": ["video", "audio", "input", "plugdev"],
            }
        ],
        "ssh_pwauth": False,
        "package_update": True,
        "package_upgrade": True,
        "packages": packages,
        "write_files": files,
        "runcmd": runcmd,
        "power_state": {"mode": "poweroff", "timeout": 600, "condition": True},
    }
    return "#cloud-config\n" + yaml.safe_dump(doc, sort_keys=False, width=200)


def seed_iso(path: Path, user: str, instance_id: str) -> None:
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="cidata")
    for name, data in (
        ("user-data", user.encode()),
        ("meta-data", f"instance-id: {instance_id}\nlocal-hostname: crosscheck-guest\n".encode()),
    ):
        iso_name = "/" + name.upper().replace("-", "") + ".;1"
        iso.add_fp(io.BytesIO(data), len(data), iso_name, rr_name=name, joliet_path="/" + name)
    iso.write(str(path))
    iso.close()


# ---------------------------------------------------------------------- build
def build(
    kind: str,
    hostd_cfg: dict,
    channel: str = "latest",
    profiles: list[str] | None = None,
    timeout_s: int = 5400,
    log=print,
) -> Path:
    state = Path(hostd_cfg["state_dir"])
    cache = state / "images" / "cache"
    work = state / "images" / "work" / new_id("i", 6)
    templates = state / "templates"
    for d in (cache, work, templates):
        d.mkdir(parents=True, exist_ok=True)
    qemu, qimg = hostd_cfg["qemu_binary"], hostd_cfg["qemu_img_binary"]
    if not Path("/dev/kvm").exists():
        raise ImageError("building images needs KVM (/dev/kvm)")
    version, url, sha = resolve_ubuntu(channel)
    log(f"using Ubuntu {version} cloud image")
    base = download(url, cache / f"ubuntu-{version}.img", sha)
    overlay = work / "build.qcow2"
    subprocess.run(
        [qimg, "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(base), str(overlay), "40G"], check=True
    )
    seed = work / "seed.iso"
    seed_iso(seed, user_data(kind, profiles), new_id("iid", 8))
    log(f"installing {kind} image (this takes 10 to 40 minutes)")
    argv = [
        qemu,
        "-accel",
        "kvm",
        "-machine",
        "q35",
        "-cpu",
        "host",
        "-smp",
        "4",
        "-m",
        "4096",
        "-display",
        "none",
        "-serial",
        f"file:{work / 'console.log'}",
        "-drive",
        f"file={overlay},if=virtio,format=qcow2,discard=unmap",
        "-drive",
        f"file={seed},media=cdrom,readonly=on",
        "-nic",
        "user,model=virtio-net-pci",
    ]
    proc = subprocess.Popen(argv)
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise ImageError(f"image build timed out, see {work / 'console.log'}") from None
    if proc.returncode != 0:
        raise ImageError(f"image build VM exited with {proc.returncode}, see {work / 'console.log'}")
    build_id = f"{kind}-ubuntu{version.replace('.', '')}-{time.strftime('%Y%m%d%H%M')}"
    target = templates / f"{build_id}.qcow2"
    subprocess.run([qimg, "convert", "-O", "qcow2", str(overlay), str(target)], check=True)
    target.chmod(0o644)
    shutil.rmtree(work, ignore_errors=True)
    log(f"template ready: {target}")
    return target


def register_template(hostd_cfg_path: Path, kind: str, template: Path) -> None:
    raw = yaml.safe_load(hostd_cfg_path.read_text()) if hostd_cfg_path.exists() else {}
    raw = raw or {}
    raw.setdefault("templates", {})[kind] = str(template)
    hostd_cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))


def import_proxmox(template: Path, kind: str, vmid: int, storage: str) -> None:
    """Create a Proxmox template VM from a built qcow2."""
    run = lambda *a: subprocess.run(list(a), check=True)  # noqa: E731
    run(
        "qm",
        "create",
        str(vmid),
        "--name",
        f"crosscheck-tpl-{kind}",
        "--machine",
        "q35",
        "--memory",
        "4096",
        "--cores",
        "4",
        "--net0",
        "virtio=52:54:00:12:34:56,bridge=vmbr0",
        "--ostype",
        "l26",
        "--scsihw",
        "virtio-scsi-single",
    )
    run("qm", "importdisk", str(vmid), str(template), storage)
    run("qm", "set", str(vmid), "--scsi0", f"{storage}:vm-{vmid}-disk-0,discard=on", "--boot", "order=scsi0")
    run("qm", "template", str(vmid))
