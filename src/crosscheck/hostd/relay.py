"""Relay inside a run's network namespace: TCP listener -> Unix socket of the egress proxy.

This is the only process listening in the namespace. It does not parse traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib


async def _pipe(src, dst) -> None:
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            dst.close()


async def serve(listen_host: str, listen_port: int, target: str, max_conns: int = 64) -> None:
    sem = asyncio.Semaphore(max_conns)

    async def handle(reader, writer):
        if sem.locked():
            writer.close()
            return
        async with sem:
            try:
                ur, uw = await asyncio.open_unix_connection(target)
            except OSError:
                writer.close()
                return
            await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))

    server = await asyncio.start_server(handle, listen_host, listen_port)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="crosscheck-relay")
    ap.add_argument("--listen", default="10.0.2.2:3128")
    ap.add_argument("--to", required=True, help="Unix socket of the egress proxy")
    args = ap.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    asyncio.run(serve(host, int(port), args.to))


if __name__ == "__main__":
    main()
