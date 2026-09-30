"""Key proxy: review VMs get a one-time token, the controller inserts the real API key.

A token is valid for one run, one provider, a limited time and a hard budget. It is revoked when
the review VM is destroyed. The only upstream endpoints reachable are the model APIs listed here.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

UPSTREAM = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com"}
ALLOWED_PATHS = {
    "anthropic": ("v1/messages", "v1/messages/count_tokens"),
    "openai": ("v1/responses", "v1/chat/completions", "v1/models"),
}
PRICES = {  # USD per 1M tokens (input, output); unknown models use the most expensive row
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
    "default": (10.0, 50.0),
}
HOP_HEADERS = {
    "host",
    "content-length",
    "authorization",
    "x-api-key",
    "connection",
    "transfer-encoding",
    "accept-encoding",
    "cookie",
    "proxy-authorization",
}


@dataclass
class Grant:
    run_id: str
    provider: str
    budget_usd: float
    expires: float
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class KeyProxy:
    def __init__(self, keys: dict[str, str | None], transport: httpx.AsyncBaseTransport | None = None):
        self.keys = {k: v for k, v in keys.items() if v}
        self.grants: dict[str, Grant] = {}
        self._transport = transport
        self._lock = threading.Lock()

    def issue(self, run_id: str, provider: str, budget_usd: float, ttl_s: int) -> str:
        if provider not in UPSTREAM:
            raise ValueError("unknown provider")
        token = "kp_" + secrets.token_urlsafe(24)
        with self._lock:
            self.grants[token] = Grant(run_id, provider, float(budget_usd), time.time() + ttl_s)
        return token

    def revoke(self, token: str) -> dict | None:
        with self._lock:
            g = self.grants.pop(token, None)
        if not g:
            return None
        return {
            "input_tokens": g.input_tokens,
            "output_tokens": g.output_tokens,
            "usd": round(g.usd, 4),
            "requests": g.requests,
        }

    def revoke_all(self) -> int:
        with self._lock:
            n = len(self.grants)
            self.grants.clear()
        return n

    def _account(self, g: Grant, model: str, usage: dict) -> None:
        pin, pout = PRICES.get(model, PRICES["default"])
        it = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        ot = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        with g.lock:
            g.input_tokens += it
            g.output_tokens += ot
            g.usd += it / 1e6 * pin + ot / 1e6 * pout

    async def handle(self, request: Request) -> Response:
        token = request.path_params["token"]
        provider = request.path_params["provider"]
        path = request.path_params["path"].lstrip("/")
        g = self.grants.get(token)
        if not g or g.expires < time.time() or g.provider != provider:
            return JSONResponse({"error": "invalid or expired token"}, status_code=401)
        if not any(path == p or path.startswith(p + "/") for p in ALLOWED_PATHS[provider]):
            return JSONResponse({"error": "endpoint not allowed"}, status_code=403)
        if g.usd >= g.budget_usd:
            return JSONResponse({"error": "run budget exhausted"}, status_code=429)
        key = self.keys.get(provider)
        if not key:
            return JSONResponse({"error": "provider not configured"}, status_code=503)
        body = await request.body()
        if len(body) > 20 * 1024 * 1024:
            return JSONResponse({"error": "request too large"}, status_code=413)
        model = "default"
        try:
            model = json.loads(body or b"{}").get("model", "default")
        except (json.JSONDecodeError, AttributeError):
            pass
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        if provider == "anthropic":
            headers["x-api-key"] = key
        else:
            headers["authorization"] = f"Bearer {key}"
        g.requests += 1
        client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20), transport=self._transport)
        upstream = client.build_request(
            request.method,
            f"{UPSTREAM[provider]}/{path}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
        resp = await client.send(upstream, stream=True)
        ctype = resp.headers.get("content-type", "")

        async def relay():
            buf = b""
            try:
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > 8 * 1024 * 1024:
                        buf = buf[-1024 * 1024 :]
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()
                self._account_from_body(g, model, buf, ctype)

        out_headers = {
            k: v for k, v in resp.headers.items() if k.lower() in ("content-type", "request-id", "retry-after")
        }
        return StreamingResponse(relay(), status_code=resp.status_code, headers=out_headers)

    def _account_from_body(self, g: Grant, model: str, buf: bytes, ctype: str) -> None:
        text = buf.decode("utf-8", "replace")
        if "event-stream" in ctype:
            usage: dict = {}
            for line in text.splitlines():
                if line.startswith("data:") and '"usage"' in line:
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    u = (
                        data.get("usage")
                        or (data.get("message") or {}).get("usage")
                        or (data.get("response") or {}).get("usage")
                        or {}
                    )
                    for k, v in u.items():
                        if isinstance(v, int):
                            usage[k] = max(usage.get(k, 0), v)
            self._account(g, model, usage)
        else:
            try:
                self._account(g, model, json.loads(text).get("usage") or {})
            except (json.JSONDecodeError, AttributeError):
                pass
