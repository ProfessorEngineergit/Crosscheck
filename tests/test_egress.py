import asyncio
import json

import pytest

from crosscheck.hostd.egress import EgressProxy, host_allowed


def test_host_allowed():
    allow = ["registry.npmjs.org", "*.crates.io", "localhost"]
    assert host_allowed("registry.npmjs.org", allow)
    assert host_allowed("static.crates.io", allow) and host_allowed("crates.io", allow)
    assert not host_allowed("evil.npmjs.org.attacker.net", allow)
    assert not host_allowed("1.2.3.4", ["*"])
    assert not host_allowed("bad host", allow)


async def _request(path, raw):
    r, w = await asyncio.open_unix_connection(path)
    w.write(raw)
    await w.drain()
    data = await asyncio.wait_for(r.read(4096), 10)
    w.close()
    return data


@pytest.mark.asyncio
async def test_proxy_denies_and_logs(tmp_path):
    log = tmp_path / "egress.jsonl"
    proxy = EgressProxy(["localhost"], str(log))
    sock = str(tmp_path / "p.sock")
    server = await asyncio.start_unix_server(proxy.handle, path=sock)

    async def upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
        await writer.drain()
        writer.close()

    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = up.sockets[0].getsockname()[1]
    async with server, up:
        denied = await _request(sock, b"CONNECT evil.example.net:443 HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"403" in denied
        honeypot = await _request(sock, b"GET http://169.254.169.254/latest/meta-data HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"403" in honeypot
        post = await _request(sock, b"POST http://localhost/x HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"403" in post
        wrong_port = await _request(sock, f"GET http://localhost:{port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert b"403" in wrong_port  # only port 80 for plain HTTP
        canary = await _request(sock, b"CONNECT evil.example.net:443 HTTP/1.1\r\nX: AKIACROSSCHECKCANARY1\r\n\r\n")
        assert b"403" in canary
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    reasons = [e["reason"] for e in entries]
    assert "host not on allow-list" in reasons and "honeypot" in reasons
    assert any(e.get("canary") for e in entries)
    assert all(not e["allowed"] for e in entries)


@pytest.mark.asyncio
async def test_offline_profile(tmp_path):
    proxy = EgressProxy(["localhost"], str(tmp_path / "e.jsonl"), {"loss_percent": 100})
    sock = str(tmp_path / "p.sock")
    server = await asyncio.start_unix_server(proxy.handle, path=sock)
    async with server:
        out = await _request(sock, b"CONNECT localhost:443 HTTP/1.1\r\n\r\n")
    assert b"403" in out
    assert json.loads((tmp_path / "e.jsonl").read_text().splitlines()[0])["reason"] == "offline"
