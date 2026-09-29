import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from crosscheck.keyproxy import KeyProxy


def make():
    seen = []

    async def upstream(req: httpx.Request):
        seen.append(req)
        return httpx.Response(
            200,
            json={"usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
            headers={"content-type": "application/json"},
        )

    kp = KeyProxy({"anthropic": "sk-real", "openai": None}, transport=httpx.MockTransport(upstream))
    app = Starlette(routes=[Route("/keyproxy/{token}/{provider}/{path:path}", kp.handle, methods=["GET", "POST"])])
    return kp, TestClient(app), seen


def test_injects_key_and_counts_budget():
    kp, client, seen = make()
    tok = kp.issue("r_1", "anthropic", budget_usd=5.0, ttl_s=60)
    r = client.post(
        f"/keyproxy/{tok}/anthropic/v1/messages",
        json={"model": "claude-opus-5-5"},
        headers={"x-api-key": "guest-fake", "authorization": "Bearer guest"},
    )
    assert r.status_code == 200
    assert seen[0].headers["x-api-key"] == "sk-real" and "authorization" not in seen[0].headers
    assert kp.grants[tok].usd == pytest.approx(4.0)
    client.post(f"/keyproxy/{tok}/anthropic/v1/messages", json={"model": "claude-opus-5-5"})
    assert client.post(f"/keyproxy/{tok}/anthropic/v1/messages", json={}).status_code == 429
    usage = kp.revoke(tok)
    assert usage["requests"] == 2
    assert client.post(f"/keyproxy/{tok}/anthropic/v1/messages", json={}).status_code == 401


def test_rejects_wrong_provider_path_and_unconfigured():
    kp, client, _ = make()
    tok = kp.issue("r_1", "anthropic", budget_usd=1, ttl_s=60)
    assert client.post(f"/keyproxy/{tok}/openai/v1/responses", json={}).status_code == 401
    assert client.get(f"/keyproxy/{tok}/anthropic/v1/organizations/me").status_code == 403
    tok2 = kp.issue("r_1", "openai", budget_usd=1, ttl_s=60)
    assert client.post(f"/keyproxy/{tok2}/openai/v1/responses", json={}).status_code == 503


def test_expired_token():
    kp, client, _ = make()
    tok = kp.issue("r_1", "anthropic", budget_usd=1, ttl_s=-1)
    assert client.post(f"/keyproxy/{tok}/anthropic/v1/messages", json={}).status_code == 401
