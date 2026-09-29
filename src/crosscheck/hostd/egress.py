"""Per-run egress proxy.

The only way out of a runner VM. Runs as its own unprivileged process on the runner host.
The guest has no DNS resolver; it sends proxy requests, and the proxy resolves names only for
hosts on the allow-list. Every attempt is logged as one JSON line.

Supported:
- ``CONNECT host:443`` to allow-listed hosts (TLS is passed through, not inspected)
- plain ``GET``/``HEAD`` with an absolute URI to allow-listed hosts
Everything else is refused with 403 and logged.

Network shaping (latency, bandwidth, flaky periods, offline) is applied here, per run.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fnmatch
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlsplit

from ..redact import CANARY_MARKER

HONEYPOT_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata", "100.100.100.200", "fd00:ec2::254"}
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
MAX_HEADER = 64 * 1024


def host_allowed(host: str, allow: list[str]) -> bool:
    host = host.lower().rstrip(".")
    if not _HOST_RE.match(host) or host.replace(".", "").isdigit():
        return False  # raw IPs are never allowed
    for pattern in allow:
        p = pattern.lower().strip()
        if p.startswith("*."):
            if host.endswith(p[1:]) or host == p[2:]:
                return True
        elif fnmatch.fnmatchcase(host, p):
            return True
    return False


class Shaper:
    def __init__(self, profile: dict, started: float):
        self.p = profile or {}
        self.started = started
        self.offline = self.p.get("loss_percent", 0) >= 100

    def blocked_now(self) -> bool:
        if self.offline:
            return True
        flap = self.p.get("flap")
        if flap:
            period = flap["up_s"] + flap["down_s"]
            return (time.monotonic() - self.started) % period >= flap["up_s"]
        return False

    def drop_connection(self) -> bool:
        loss = self.p.get("loss_percent", 0)
        return 0 < loss < 100 and random.random() * 100 < loss  # noqa: S311 - simulation, not crypto

    async def delay(self) -> None:
        d = self.p.get("delay_ms", 0) + random.uniform(0, self.p.get("jitter_ms", 0))  # noqa: S311
        if d > 0:
            await asyncio.sleep(d / 1000)

    async def throttle(self, nbytes: int) -> None:
        rate = self.p.get("rate_kbit")
        if rate:
            await asyncio.sleep(nbytes * 8 / (rate * 1000))


class EgressProxy:
    def __init__(
        self, allow: list[str], log_path: str, profile: dict | None = None, max_bytes_per_conn: int = 2 * 1024**3
    ):
        self.allow = allow
        self.log_path = log_path
        self.shaper = Shaper(profile or {}, time.monotonic())
        self.max_bytes = max_bytes_per_conn
        self._log = open(log_path, "a", buffering=1)

    def log(self, **entry) -> None:
        entry["ts"] = round(time.time(), 3)
        self._log.write(json.dumps(entry) + "\n")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            writer.close()
            return
        try:
            await self._dispatch(head, reader, writer)
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _refuse(self, writer, code: int, reason: str, **log) -> None:
        self.log(allowed=False, reason=reason, **log)
        writer.write(
            f"HTTP/1.1 {code} Crosscheck egress denied\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        with contextlib.suppress(Exception):
            await writer.drain()

    async def _dispatch(self, head: bytes, reader, writer) -> None:
        text = head.decode("latin-1")
        request_line = text.split("\r\n", 1)[0]
        parts = request_line.split(" ")
        if len(parts) != 3:
            await self._refuse(writer, 400, "malformed request")
            return
        method, target, _ = parts
        canary = CANARY_MARKER in text
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            port_i = int(port) if port.isdigit() else 0
            await self._tunnel(host, port_i, reader, writer, canary)
        elif method in ("GET", "HEAD"):
            url = urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                await self._refuse(
                    writer, 403, "only absolute http:// URLs", method=method, host=(url.hostname or "")[:253]
                )
                return
            await self._forward_http(method, url, text, writer, canary)
        else:
            await self._refuse(writer, 403, f"method {method[:10]} not allowed", method=method[:10])

    def _check(self, host: str, port: int, allowed_ports: tuple[int, ...]) -> str | None:
        if host.lower() in HONEYPOT_HOSTS:
            return "honeypot"
        if self.shaper.blocked_now():
            return "offline"
        if port not in allowed_ports:
            return f"port {port} not allowed"
        if not host_allowed(host, self.allow):
            return "host not on allow-list"
        if self.shaper.drop_connection():
            return "simulated packet loss"
        return None

    async def _tunnel(self, host, port, reader, writer, canary) -> None:
        problem = self._check(host, port, (443,))
        if problem:
            await self._refuse(
                writer, 403, problem, method="CONNECT", host=host[:253], port=port, canary=canary or None
            )
            return
        await self.shaper.delay()
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=20)
        except (OSError, TimeoutError):
            await self._refuse(writer, 502, "upstream unreachable", method="CONNECT", host=host, port=port)
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        sent, received = await self._pump(reader, writer, up_r, up_w)
        self.log(
            allowed=True,
            method="CONNECT",
            host=host,
            port=port,
            bytes_up=sent,
            bytes_down=received,
            canary=canary or None,
        )

    async def _forward_http(self, method, url, head_text, writer, canary) -> None:
        port = url.port or 80
        problem = self._check(url.hostname, port, (80,))
        if problem:
            await self._refuse(
                writer, 403, problem, method=method, host=url.hostname[:253], port=port, canary=canary or None
            )
            return
        await self.shaper.delay()
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(url.hostname, port), timeout=20)
        except (OSError, TimeoutError):
            await self._refuse(writer, 502, "upstream unreachable", method=method, host=url.hostname)
            return
        path = url.path or "/"
        if url.query:
            path += "?" + url.query
        headers = [h for h in head_text.split("\r\n")[1:] if h and not h.lower().startswith(("proxy-", "connection"))]
        up_w.write(
            f"{method} {path} HTTP/1.1\r\n".encode()
            + "\r\n".join(headers).encode("latin-1")
            + b"\r\nConnection: close\r\n\r\n"
        )
        await up_w.drain()
        received = 0
        while True:
            chunk = await up_r.read(65536)
            if not chunk:
                break
            received += len(chunk)
            await self.shaper.throttle(len(chunk))
            writer.write(chunk)
            await writer.drain()
            if received > self.max_bytes:
                break
        up_w.close()
        self.log(allowed=True, method=method, host=url.hostname, port=port, bytes_down=received, canary=canary or None)

    async def _pump(self, cr, cw, ur, uw) -> tuple[int, int]:
        counts = [0, 0]

        async def copy(src, dst, idx):
            try:
                while True:
                    chunk = await src.read(65536)
                    if not chunk:
                        break
                    counts[idx] += len(chunk)
                    if counts[idx] > self.max_bytes:
                        break
                    await self.shaper.throttle(len(chunk))
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    dst.close()

        await asyncio.gather(copy(cr, uw, 0), copy(ur, cw, 1))
        return counts[0], counts[1]


async def serve(args) -> None:
    allow = [a for a in args.allow.split(",") if a]
    profile = json.loads(args.profile) if args.profile else {}
    proxy = EgressProxy(allow, args.log, profile)
    if args.unix:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(args.unix)
        server = await asyncio.start_unix_server(proxy.handle, path=args.unix, limit=MAX_HEADER)
        os.chmod(args.unix, 0o660)
    else:
        host, _, port = args.listen.rpartition(":")
        server = await asyncio.start_server(proxy.handle, host, int(port), limit=MAX_HEADER)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="crosscheck-egress")
    ap.add_argument("--unix", help="listen on this Unix socket")
    ap.add_argument("--listen", help="host:port to listen on")
    ap.add_argument("--allow", default="", help="comma-separated host patterns")
    ap.add_argument("--profile", default="", help="network profile JSON")
    ap.add_argument("--log", required=True)
    args = ap.parse_args(argv)
    if not args.unix and not args.listen:
        ap.error("--unix or --listen is required")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
