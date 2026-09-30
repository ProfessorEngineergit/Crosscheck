#!/usr/bin/env python3
"""Crosscheck guest runner. Runs inside the disposable VM, standard library only.

Started at login by the image (see crosscheck/images/linux.py). It is a convenience, not a
security boundary: PR code runs as the same user and may tamper with it. The host treats
everything this script produces as untrusted and measures what matters from the outside.

    crosscheck_guest.py <job-dir>

Job types: smoke (build + launch app), static (scanners), review (coding agent).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time

PROXY = "http://10.0.2.2:3128"
MARKER = "#123456"
RESULTS_SERIAL = "ccresults"
MAGIC = b"CCRESULT2\n"
HEADER_OFFSETS = (0, 512)
DATA_OFFSET = 4096
LOG_LIMIT = 2 * 1024 * 1024
WORK = os.path.expanduser("~/work")
TMP = os.path.expanduser("~/.crosscheck-tmp")

state = {"build": {}, "launch": {}, "events": [], "started": time.time()}
logs = {"build.log": b"", "app.log": b""}
_seq = [0]
_lock = threading.Lock()


def log_event(kind: str, **fields) -> None:
    state["events"].append({"t": round(time.time() - state["started"], 2), "event": kind, **fields})


# ------------------------------------------------------------------ results disk
def results_device() -> str | None:
    by_id = "/dev/disk/by-id"
    if os.path.isdir(by_id):
        for name in os.listdir(by_id):
            if name.endswith(RESULTS_SERIAL):
                return os.path.join(by_id, name)
    return None


def build_archive(extra_files: dict | None = None) -> bytes:
    buf = io.BytesIO()
    files = {"status.json": json.dumps(state).encode(), **logs}
    files["audit.jsonl"] = audit_snapshot()
    files.update(extra_files or {})
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(time.time())
            tf.addfile(info, io.BytesIO(data))
        for path in collect_crash_files():
            try:
                with open(path, "rb") as fh:
                    data = fh.read(4 * 1024 * 1024)
                info = tarfile.TarInfo("crash/" + os.path.basename(path)[:100])
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            except OSError:
                continue
    return buf.getvalue()


def write_results(extra_files: dict | None = None) -> None:
    dev = results_device()
    if not dev:
        return
    with _lock:
        archive = build_archive(extra_files)
        _seq[0] += 1
        seq = _seq[0]
        try:
            with open(dev, "r+b", buffering=0) as fh:
                size = fh.seek(0, 2)
                ssize = (size - DATA_OFFSET) // 2
                if len(archive) > ssize:
                    return
                slot = seq % 2
                fh.seek(DATA_OFFSET + slot * ssize)
                fh.write(archive)
                os.fsync(fh.fileno())
                hdr = (
                    MAGIC
                    + b"%016x\n%016x\n" % (seq, len(archive))
                    + hashlib.sha256(archive).hexdigest().encode()
                    + b"\n"
                )
                fh.seek(HEADER_OFFSETS[slot])
                fh.write(hdr)
                os.fsync(fh.fileno())
        except OSError as exc:
            sys.stderr.write(f"cannot write results: {exc}\n")


def snapshot_loop(stop: threading.Event) -> None:
    while not stop.wait(5):
        write_results()


# ------------------------------------------------------------------ observation (guest-reported, hints only)
_audit: list[dict] = []
CANARY_FILES = [
    os.path.expanduser(p)
    for p in ("~/.aws/credentials", "~/.ssh/id_ed25519", "~/.config/gh/hosts.yml", "~/.git-credentials", "~/.npmrc")
]


def canary_atimes() -> dict:
    out = {}
    for p in CANARY_FILES:
        try:
            out[p] = os.stat(p).st_atime
        except OSError:
            pass
    return out


def audit_snapshot() -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in _audit[-5000:])


def observe_loop(stop: threading.Event, baseline: dict) -> None:
    seen_procs = set()
    while not stop.wait(2):
        for p, atime in canary_atimes().items():
            if atime > baseline.get(p, 0) + 1:
                _audit.append({"t": time.time(), "event": "canary-read", "path": p})
                baseline[p] = atime
        try:
            for pid in os.listdir("/proc"):
                if not pid.isdigit() or pid in seen_procs:
                    continue
                seen_procs.add(pid)
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as fh:
                        cmd = fh.read(300).replace(b"\0", b" ").decode(errors="replace")
                    _audit.append({"t": time.time(), "event": "process", "pid": int(pid), "cmd": cmd})
                except OSError:
                    continue
        except OSError:
            pass


def collect_crash_files() -> list:
    out = []
    for d in ("/var/crash", os.path.expanduser("~/.local/share/crashes"), WORK):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.startswith("core") or name.endswith((".crash", ".dmp", ".core")):
                out.append(os.path.join(d, name))
    return out[:5]


# ------------------------------------------------------------------ desktop helpers
class Marker:
    """Full-screen solid colour while building, so the host can time the app start from outside."""

    def __init__(self):
        self.proc = None

    def show(self, text: str) -> None:
        code = (
            "import tkinter as t\nr=t.Tk()\nr.attributes('-fullscreen',True)\n"
            f"r.configure(bg='{MARKER}')\n"
            f"t.Label(r,text={text!r},fg='white',bg='{MARKER}',font=('Sans',24)).pack(expand=True)\n"
            "r.mainloop()\n"
        )
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError:
            self.proc = None

    def hide(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def apply_display(job: dict) -> None:
    disp = (job.get("displays") or [{}])[0]
    w, h, scale = disp.get("width"), disp.get("height"), disp.get("scale", 100)
    if w and h and shutil.which("xrandr"):
        if subprocess.run(["xrandr", "-s", f"{w}x{h}"], capture_output=True).returncode != 0:
            out = subprocess.run(["xrandr"], capture_output=True, text=True).stdout
            output = next((ln.split()[0] for ln in out.splitlines() if " connected" in ln), None)
            if output:
                mode = f"{w}x{h}"
                cvt = subprocess.run(["cvt", str(w), str(h)], capture_output=True, text=True).stdout
                line = next((ln for ln in cvt.splitlines() if ln.startswith("Modeline")), None)
                if line:
                    parts = line.split()[2:]
                    subprocess.run(["xrandr", "--newmode", mode] + parts, capture_output=True)
                    subprocess.run(["xrandr", "--addmode", output, mode], capture_output=True)
                    subprocess.run(["xrandr", "--output", output, "--mode", mode], capture_output=True)
    if scale and scale != 100 and shutil.which("xrdb"):
        dpi = int(96 * scale / 100)
        subprocess.run(["xrdb", "-merge"], input=f"Xft.dpi: {dpi}\n", text=True, capture_output=True)
    log_event("display", width=w, height=h, scale=scale)


def app_env(job: dict) -> dict:
    env = dict(os.environ)
    for k in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "npm_config_proxy",
        "npm_config_https_proxy",
        "PIP_PROXY",
        "CARGO_HTTP_PROXY",
    ):
        env[k] = PROXY
    env["no_proxy"] = env["NO_PROXY"] = ""
    env["CI"] = "true"
    env["CROSSCHECK"] = "1"
    for k, v in (job.get("env") or {}).items():
        if k.startswith("CROSSCHECK_"):
            env[k] = str(v)
    scale = ((job.get("displays") or [{}])[0]).get("scale", 100)
    if scale and scale != 100:
        env["GDK_SCALE"] = str(round(scale / 100))
        env["QT_SCALE_FACTOR"] = str(scale / 100)
        env["ELECTRON_FORCE_DEVICE_SCALE_FACTOR"] = str(scale / 100)
    if env.get("CROSSCHECK_THEME") == "dark":
        env["GTK_THEME"] = "Adwaita:dark"
    if env.get("CROSSCHECK_LOCALE"):
        env["LANG"] = env["LC_ALL"] = env["CROSSCHECK_LOCALE"]
    if env.get("CROSSCHECK_TIMEZONE"):
        env["TZ"] = env["CROSSCHECK_TIMEZONE"]
    return env


def run_logged(cmd: str, name: str, env: dict, timeout: int, cwd: str) -> int:
    start = time.time()
    try:
        proc = subprocess.run(
            ["bash", "-lc", cmd], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout
        )
        out, code = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out, code = (exc.output or b"") + b"\n[crosscheck] timed out\n", 124
    logs[name] = (logs[name] + out)[-LOG_LIMIT:]
    log_event(name.replace(".log", ""), exit_code=code, duration_s=round(time.time() - start, 1))
    return code


def unpack_source(job_dir: str, name: str, dest: str) -> bool:
    path = os.path.join(job_dir, name)
    if not os.path.exists(path):
        return False
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(path) as tf:
        members = []
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1] or m.issym() or m.islnk() or m.isdev():
                continue
            m.name = parts[1]
            if m.name.startswith("/") or ".." in m.name.split("/"):
                continue
            members.append(m)
        tf.extractall(dest, members=members)  # noqa: S202 - filtered above, and the VM is disposable anyway
    return True


# ------------------------------------------------------------------ zero-config detection
def autodetect(work: str) -> tuple:
    """Guess build and launch commands when the repository has no crosscheck.yaml."""

    def has(name):
        return os.path.exists(os.path.join(work, name))

    if has("package.json"):
        try:
            with open(os.path.join(work, "package.json")) as fh:
                pkg = json.load(fh)
        except (OSError, ValueError):
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        scripts = pkg.get("scripts", {})
        install = "npm ci" if has("package-lock.json") else "npm install"
        build = install + (" && npm run build" if "build" in scripts else "")
        if "electron" in deps:
            return build, "npx electron ."
        if "@tauri-apps/cli" in deps:
            return (
                install + " && npx tauri build --debug",
                "find src-tauri/target/debug -maxdepth 1 -type f -perm -u+x | head -1 | sh",
            )
        return build, "npm start" if "start" in scripts else None
    if has("pubspec.yaml"):
        return "flutter pub get && flutter build linux --debug", "$(ls -1 build/linux/x64/debug/bundle/* | head -1)"
    if has("Cargo.toml"):
        return "cargo build", "cargo run"
    if has("CMakeLists.txt"):
        return (
            "cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build -j",
            "$(find build -maxdepth 2 -type f -perm -u+x ! -name '*.so*' | head -1)",
        )
    if has("pyproject.toml") or has("requirements.txt"):
        req = "pip install -r requirements.txt" if has("requirements.txt") else "pip install ."
        return f"python3 -m venv .venv && . .venv/bin/activate && {req}", None
    if has("Makefile"):
        return "make", None
    return None, None


# ------------------------------------------------------------------ job types
def job_smoke(job: dict, job_dir: str, stop: threading.Event) -> None:
    marker = Marker()
    marker.show("Crosscheck: building")
    apply_display(job)
    env = app_env(job)
    unpack_source(job_dir, "source.tar.gz", WORK)
    build_cmd = job.get("build")
    launch = job.get("launch")
    if not build_cmd or not launch:
        guess_build, guess_launch = autodetect(WORK)
        build_cmd = build_cmd or guess_build
        launch = launch or guess_launch
        log_event("autodetect", build=build_cmd, launch=launch)
    ok = True
    if build_cmd:
        code = run_logged(build_cmd, "build.log", env, int(job.get("build_timeout_s", 900)), WORK)
        state["build"] = {"ok": code == 0, "exit_code": code}
        ok = code == 0
    write_results()
    marker.hide()
    if not ok:
        return
    if not launch:
        state["launch"] = {"ok": False, "error": "no launch command for this platform"}
        return
    log_path = os.path.join(WORK, ".crosscheck-app.log")
    with open(log_path, "wb") as fh:
        proc = subprocess.Popen(
            ["bash", "-lc", launch], cwd=WORK, env=env, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True
        )
    state["launch"] = {"ok": True, "pid": proc.pid, "t": round(time.time() - state["started"], 2)}
    log_event("launch", pid=proc.pid)
    while not stop.is_set():
        code = proc.poll()
        try:
            with open(log_path, "rb") as fh:
                logs["app.log"] = fh.read()[-LOG_LIMIT:]
        except OSError:
            pass
        if code is not None:
            state["app_exit_code"] = code
            log_event("app-exit", exit_code=code)
            break
        stop.wait(1)


def job_static(job: dict, job_dir: str, stop: threading.Event) -> None:
    os.makedirs(TMP, exist_ok=True)
    unpack_source(job_dir, "source.tar.gz", WORK)
    results = {"tools": {}}
    env = dict(os.environ)
    tools = {
        "gitleaks": [
            "gitleaks",
            "detect",
            "--no-git",
            "--source",
            WORK,
            "--report-format",
            "json",
            "--report-path",
            os.path.join(TMP, "gitleaks.json"),
            "--exit-code",
            "0",
        ],
        "semgrep": [
            "semgrep",
            "scan",
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--config",
            os.environ.get("SEMGREP_RULES", "/opt/semgrep-rules"),
            "-o",
            os.path.join(TMP, "semgrep.json"),
            WORK,
        ],
        "osv-scanner": [
            "osv-scanner",
            "scan",
            "--offline",
            "--format",
            "json",
            "--output",
            os.path.join(TMP, "osv.json"),
            "-r",
            WORK,
        ],
    }
    for name, argv in tools.items():
        if not shutil.which(argv[0]):
            results["tools"][name] = {"available": False}
            continue
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=600, env=env)
            results["tools"][name] = {"available": True, "exit_code": proc.returncode}
        except subprocess.TimeoutExpired:
            results["tools"][name] = {"available": True, "timed_out": True}
        out_file = (
            argv[argv.index("--report-path") + 1]
            if "--report-path" in argv
            else argv[argv.index("-o") + 1]
            if "-o" in argv
            else argv[argv.index("--output") + 1]
        )
        try:
            with open(out_file) as fh:
                results["tools"][name]["output"] = json.load(fh)
        except (OSError, ValueError):
            pass
    write_results({"static.json": json.dumps(results).encode()[: 4 * 1024 * 1024]})


def job_review(job: dict, job_dir: str, stop: threading.Event) -> None:
    unpack_source(job_dir, "source.tar.gz", WORK)
    unpack_source(job_dir, "base.tar.gz", WORK + "-base")
    env = app_env(job)
    task = job.get("review_task", "")
    with open(os.path.join(WORK, ".crosscheck-context.json"), "w") as fh:
        json.dump(job.get("context", {}), fh)
    kp = job.get("key_proxy") or {}
    agent = job.get("agent", "claude-code")
    if agent == "claude-code":
        env["ANTHROPIC_BASE_URL"] = kp.get("anthropic_base_url", "")
        env["ANTHROPIC_API_KEY"] = kp.get("token", "")
        cmd = ["claude", "-p", task, "--output-format", "json", "--dangerously-skip-permissions"]
    else:
        env["OPENAI_BASE_URL"] = kp.get("openai_base_url", "")
        env["OPENAI_API_KEY"] = kp.get("token", "")
        cmd = ["codex", "exec", "--skip-git-repo-check", "--full-auto", task]
    try:
        proc = subprocess.run(cmd, cwd=WORK, env=env, capture_output=True, timeout=int(job.get("timeout_s", 1500)))
        logs["app.log"] = (proc.stdout + proc.stderr)[-LOG_LIMIT:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        logs["app.log"] = str(exc).encode()
    findings = b"[]"
    try:
        with open(os.path.join(WORK, "findings.json"), "rb") as fh:
            findings = fh.read(1024 * 1024)
    except OSError:
        pass
    write_results({"findings.json": findings})


def job_escape(job: dict, job_dir: str, stop: threading.Event) -> None:
    """Isolation self-test used by `crosscheck doctor --escape-test`. Runs no PR code."""
    import socket

    results = {}
    for name, host, port in job.get("targets", []):
        try:
            with socket.create_connection((host, int(port)), timeout=3):
                results[name] = "reachable"
        except OSError as exc:
            results[name] = f"blocked ({type(exc).__name__})"
    try:
        socket.getaddrinfo("example.com", 443)
        results["DNS resolution"] = "reachable"
    except OSError:
        results["DNS resolution"] = "blocked"
    try:
        with socket.create_connection(("2606:4700:4700::1111", 443), timeout=3):
            results["IPv6 internet"] = "reachable"
    except OSError:
        results["IPv6 internet"] = "blocked"
    try:
        with socket.create_connection(("10.0.2.2", 3128), timeout=3) as s:
            s.sendall(b"CONNECT evil.example.net:443 HTTP/1.1\r\nHost: evil.example.net:443\r\n\r\n")
            reply = s.recv(64).decode(errors="replace")
            results["proxy to non-allowed host"] = "reachable" if " 200 " in reply else "blocked"
    except OSError:
        results["proxy to non-allowed host"] = "blocked"
    write_results({"escape.json": json.dumps(results).encode()})


def main() -> int:
    job_dir = sys.argv[1] if len(sys.argv) > 1 else "/media/ccjob"
    with open(os.path.join(job_dir, "job.json")) as fh:
        job = json.load(fh)
    state["job"] = {"kind": job.get("kind"), "run_id": job.get("run_id"), "platform": job.get("platform")}
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    threading.Thread(target=snapshot_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=observe_loop, args=(stop, canary_atimes()), daemon=True).start()
    kind = job.get("kind", "smoke")
    try:
        {"smoke": job_smoke, "static": job_static, "review": job_review, "escape-test": job_escape}[kind](
            job, job_dir, stop
        )
    except Exception as exc:
        log_event("runner-error", error=str(exc)[:500])
    write_results()
    while not stop.wait(5):
        write_results()
    write_results()
    return 0


if __name__ == "__main__":
    sys.exit(main())
