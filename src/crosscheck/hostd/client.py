"""Controller-side clients for hostd: in-process (single machine) or SSH stdio (two devices)."""

from __future__ import annotations

import base64
import contextlib
import json
import subprocess
import threading
from abc import ABC, abstractmethod

from ..util import new_id

CHUNK = 4 * 1024 * 1024
MAX_REPLY = 96 * 1024 * 1024


class HostdError(RuntimeError):
    pass


class HostdClient(ABC):
    @abstractmethod
    def call(self, method: str, **params): ...

    def put_blob(self, data: bytes) -> str:
        blob_id = new_id("b", 12)
        for offset in range(0, max(1, len(data)), CHUNK):
            self.call(
                "put_blob",
                blob_id=blob_id,
                offset=offset,
                data_b64=base64.b64encode(data[offset : offset + CHUNK]).decode(),
            )
        return blob_id

    def close(self) -> None:  # noqa: B027 - optional hook
        pass


class LocalHostd(HostdClient):
    def __init__(self, service):
        self.service = service
        self._lock = threading.Lock()

    def call(self, method: str, **params):
        with self._lock:
            try:
                return json.loads(json.dumps(self.service.call(method, params)))
            except Exception as exc:
                raise HostdError(f"{type(exc).__name__}: {exc}") from exc


class _LineHostd(HostdClient):
    """Shared request/response handling over a line-oriented byte stream."""

    def __init__(self):
        self._lock = threading.Lock()
        self._seq = 0

    def _io(self):  # returns (writer, reader) file objects
        raise NotImplementedError

    def _reset(self) -> None:
        raise NotImplementedError

    def call(self, method: str, **params):
        with self._lock:
            w, r = self._io()
            self._seq += 1
            req = json.dumps({"id": self._seq, "method": method, "params": params}).encode() + b"\n"
            try:
                w.write(req)
                w.flush()
                line = r.readline(MAX_REPLY + 1)
            except (BrokenPipeError, OSError) as exc:
                self._reset()
                raise HostdError(f"connection to hostd lost: {exc}") from exc
            if not line or len(line) > MAX_REPLY:
                self._reset()
                raise HostdError("no or oversized reply from hostd")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HostdError("invalid reply from hostd") from exc
            if not isinstance(resp, dict) or resp.get("id") != self._seq:
                self._reset()
                raise HostdError("reply out of sequence")
            if resp.get("error"):
                raise HostdError(str(resp["error"])[:1000])
            return resp.get("result")


class UnixHostd(_LineHostd):
    """hostd on the same machine, as a separate root service on a Unix socket."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self._sock = None
        self._files = None

    def _io(self):
        if self._files is None:
            import socket

            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.path)
            self._sock = s
            self._files = (s.makefile("wb"), s.makefile("rb"))
        return self._files

    def _reset(self) -> None:
        if self._sock:
            with contextlib.suppress(OSError):
                self._sock.close()
        self._sock = None
        self._files = None

    def close(self) -> None:
        self._reset()


class SshHostd(HostdClient):
    """Talks to ``crosscheck hostd --stdio`` through SSH. On the runner host the key is restricted with
    ``command="/usr/local/bin/crosscheck hostd --stdio",restrict`` and ``PermitRootLogin forced-commands-only``,
    so it can do nothing else."""

    def __init__(
        self, host: str, user: str, port: int = 22, key_file: str | None = None, known_hosts_file: str | None = None
    ):
        argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-p", str(port)]
        if key_file:
            argv += ["-i", key_file, "-o", "IdentitiesOnly=yes"]
        if known_hosts_file:
            argv += ["-o", f"UserKnownHostsFile={known_hosts_file}", "-o", "StrictHostKeyChecking=yes"]
        argv += [f"{user}@{host}", "crosscheck", "hostd", "--stdio"]
        self.argv = argv
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._seq = 0

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        return self._proc

    def call(self, method: str, **params):
        with self._lock:
            proc = self._ensure()
            self._seq += 1
            req = json.dumps({"id": self._seq, "method": method, "params": params}).encode() + b"\n"
            try:
                proc.stdin.write(req)
                proc.stdin.flush()
                line = proc.stdout.readline(MAX_REPLY + 1)
            except (BrokenPipeError, OSError) as exc:
                self._proc = None
                raise HostdError(f"connection to runner host lost: {exc}") from exc
            if not line or len(line) > MAX_REPLY:
                self._proc = None
                raise HostdError("no or oversized reply from runner host")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HostdError("invalid reply from runner host") from exc
            if not isinstance(resp, dict) or resp.get("id") != self._seq:
                raise HostdError("reply out of sequence")
            if resp.get("error"):
                raise HostdError(str(resp["error"])[:1000])
            return resp.get("result")

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.close()
            self._proc.terminate()
