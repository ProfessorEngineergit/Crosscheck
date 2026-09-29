"""JSON-RPC over stdio for ``crosscheck hostd --stdio`` (SSH forced command) and a janitor loop."""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
import traceback

from .service import HostdService

MAX_LINE = 8 * 1024 * 1024


def serve_stdio(service: HostdService, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while True:
        line = stdin.readline(MAX_LINE + 1)
        if not line:
            break
        if len(line) > MAX_LINE:
            stdout.write(b'{"id":null,"error":"request too large"}\n')
            stdout.flush()
            break
        try:
            req = json.loads(line)
            rid = req.get("id")
            result = service.call(str(req.get("method", "")), req.get("params") or {})
            resp = {"id": rid, "result": result}
        except Exception as exc:
            resp = {"id": locals().get("rid"), "error": f"{type(exc).__name__}: {exc}"}
            if "--debug" in sys.argv:
                traceback.print_exc(file=sys.stderr)
        stdout.write(json.dumps(resp).encode() + b"\n")
        stdout.flush()


def serve_unix(service: HostdService, path: str, group: str | None = None) -> None:
    """Serve the same line protocol on a Unix socket (single-machine installs).

    The socket is root:<group> 0660, so only the controller's user can talk to hostd.
    """
    import grp
    import os
    import socketserver

    lock = threading.Lock()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            wrapped = _LockedService(service, lock)
            serve_stdio(wrapped, self.rfile, self.wfile)

    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    server = socketserver.ThreadingUnixStreamServer(path, Handler)
    os.chmod(path, 0o660)
    if group:
        with contextlib.suppress(KeyError, PermissionError):
            os.chown(path, 0, grp.getgrnam(group).gr_gid)
    server.daemon_threads = True
    server.serve_forever()


class _LockedService:
    def __init__(self, service: HostdService, lock: threading.Lock):
        self.service = service
        self.lock = lock

    def call(self, method: str, params: dict):
        with self.lock:
            return self.service.call(method, params)


def janitor_loop(service: HostdService, interval: float = 60.0, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            service.rpc_janitor()
        except Exception:  # noqa: S110 - janitor must never die
            pass
        stop.wait(interval)
        time.sleep(0)
