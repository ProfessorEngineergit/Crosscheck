import asyncio
import json
import socket
import threading
import time

import httpx2
import pytest
import uvicorn

from crosscheck.demo import DEMO_REPO
from crosscheck.http_app import build_app
from crosscheck.policy import Trigger


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def served(make_rt):
    rt, _gh = make_rt("ok")
    rt.controller.consider(DEMO_REPO, 1, Trigger(kind="push"))
    rt.controller.execute(rt.controller.queue.get_nowait())
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(build_app(rt.controller, rt.store, rt.keyproxy), host="127.0.0.1", port=port, log_level="error")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield rt, port
    server.should_exit = True
    t.join(5)


async def call(port, token, tool, args):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as hc:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=hc) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                return await s.call_tool(tool, args)


def test_auth_required(served):
    _rt, port = served
    r = httpx2.post(f"http://127.0.0.1:{port}/mcp", json={})
    assert r.status_code == 401
    r = httpx2.post(f"http://127.0.0.1:{port}/mcp", json={}, headers={"Authorization": "Bearer cc_wrong"})
    assert r.status_code == 401
    assert httpx2.get(f"http://127.0.0.1:{port}/healthz").text == "ok"


def test_tools_and_scopes(served):
    rt, port = served
    token = rt.store.create_token("t", ["read", "artifacts"])
    res = asyncio.run(call(port, token, "crosscheck_list_runs", {"repo": DEMO_REPO}))
    run_id = json.loads(res.content[0].text)["runs"][0].split()[0]
    res = asyncio.run(call(port, token, "crosscheck_get_run", {"run_id": run_id}))
    body = json.loads(res.content[0].text)
    assert body["trust"] == "sandbox-observation" and "Status: success" in body["data"]["summary"]
    steps = json.loads(
        asyncio.run(call(port, token, "crosscheck_get_steps", {"run_id": run_id, "platform": "linux"})).content[0].text
    )
    aid = steps["data"][0]["steps"][0]["screenshot_after"]
    img = asyncio.run(call(port, token, "crosscheck_get_artifact", {"artifact_id": aid}))
    assert img.content[0].type == "image"
    denied = asyncio.run(call(port, token, "crosscheck_request_run", {"repo": DEMO_REPO, "pr": 1}))
    assert denied.is_error and "request" in denied.content[0].text
    deep = asyncio.run(
        call(
            port,
            rt.store.create_token("r", ["read", "request"]),
            "crosscheck_request_run",
            {"repo": DEMO_REPO, "pr": 1, "stage": "deep"},
        )
    )
    assert deep.is_error and "request:deep" in deep.content[0].text


def test_revoked_and_expired_tokens(served):
    rt, port = served
    token = rt.store.create_token("gone", ["read"])
    rt.store.revoke_token("gone")
    assert (
        httpx2.post(f"http://127.0.0.1:{port}/mcp", json={}, headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )
