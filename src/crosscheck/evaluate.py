"""Parse a results archive from a VM in a separate, resource-limited process.

Everything in the archive is attacker-controlled. The parent process never opens it; it runs
``python -m crosscheck.evaluate`` with the archive on stdin and reads JSON from stdout.
Only whitelisted member names are read, into memory, with size limits. Nothing is written
to disk. Symlinks, devices and directories are ignored.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tarfile

MAX_ARCHIVE = 64 * 1024 * 1024
LIMITS = {
    "status.json": 256 * 1024,
    "build.log": 2 * 1024 * 1024,
    "app.log": 2 * 1024 * 1024,
    "audit.jsonl": 4 * 1024 * 1024,
    "findings.json": 1024 * 1024,
    "sbom.json": 8 * 1024 * 1024,
    "static.json": 4 * 1024 * 1024,
    "bench.json": 256 * 1024,
    "escape.json": 64 * 1024,
}
CRASH_PREFIX = "crash/"
MAX_CRASH_FILES = 5
MAX_CRASH_BYTES = 4 * 1024 * 1024


def _parse(archive: bytes) -> dict:
    out: dict = {"files": {}, "crash": [], "ignored": 0, "errors": []}
    try:
        tf = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    except (tarfile.TarError, EOFError) as exc:
        return {"files": {}, "crash": [], "ignored": 0, "errors": [f"not a tar archive: {exc}"]}
    members = 0
    for m in tf:
        members += 1
        if members > 500:
            out["errors"].append("too many members")
            break
        name = m.name.lstrip("./")
        if not m.isreg():
            out["ignored"] += 1
            continue
        if name in LIMITS:
            if m.size > LIMITS[name]:
                out["errors"].append(f"{name} too large ({m.size} bytes)")
                continue
            fh = tf.extractfile(m)
            data = fh.read(LIMITS[name] + 1) if fh else b""
            out["files"][name] = data.decode("utf-8", "replace")
        elif name.startswith(CRASH_PREFIX) and len(out["crash"]) < MAX_CRASH_FILES and "/" not in name[6:]:
            if m.size > MAX_CRASH_BYTES:
                out["errors"].append(f"{name} too large")
                continue
            fh = tf.extractfile(m)
            data = fh.read(MAX_CRASH_BYTES + 1) if fh else b""
            out["crash"].append({"name": name[6:][:100], "size": len(data), "b64": base64.b64encode(data).decode()})
        else:
            out["ignored"] += 1
    for key in ("status.json", "findings.json", "static.json", "bench.json", "escape.json"):
        if key in out["files"]:
            try:
                out.setdefault("json", {})[key] = json.loads(out["files"][key])
            except json.JSONDecodeError as exc:
                out["errors"].append(f"{key}: invalid JSON: {exc}")
    return out


def _limit_resources() -> None:  # pragma: no cover - runs in the child
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    except (ImportError, ValueError, OSError):
        pass


def evaluate(archive: bytes, timeout: float = 30.0) -> dict:
    """Parse ``archive`` in a child process. Never raises on bad input."""
    if not archive:
        return {"files": {}, "crash": [], "ignored": 0, "errors": ["no results written"]}
    if len(archive) > MAX_ARCHIVE:
        return {"files": {}, "crash": [], "ignored": 0, "errors": ["archive too large"]}
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-m", "crosscheck.evaluate"],
            input=archive,
            capture_output=True,
            timeout=timeout,
            preexec_fn=_limit_resources,
            env={"LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired:
        return {"files": {}, "crash": [], "ignored": 0, "errors": ["parser timed out"]}
    if proc.returncode != 0:
        return {"files": {}, "crash": [], "ignored": 0, "errors": [f"parser failed ({proc.returncode})"]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"files": {}, "crash": [], "ignored": 0, "errors": ["parser returned invalid output"]}


def main() -> None:  # pragma: no cover - child entry point
    data = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
    json.dump(_parse(data), sys.stdout)


if __name__ == "__main__":  # pragma: no cover
    main()
