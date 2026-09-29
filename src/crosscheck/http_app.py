"""The controller's HTTP surface: /mcp (bearer tokens), /webhook (HMAC), /keyproxy (one-time tokens),
/healthz. Only these paths are meant to be exposed through a tunnel."""

from __future__ import annotations

import json
import logging

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import policy as pol
from .github import verify_webhook
from .mcp_server import BearerAuth, build_mcp, transport_security
from .util import REPO_RE, read_secret

log = logging.getLogger("crosscheck")


def build_app(controller, store, keyproxy=None):
    cfg = controller.cfg
    server = build_mcp(controller, store)
    secret = read_secret(cfg.github.webhook_secret_file)

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request):
        return PlainTextResponse("ok")

    @server.custom_route("/webhook", methods=["POST"])
    async def webhook(request: Request):
        if not secret:
            return JSONResponse({"error": "webhooks not configured"}, status_code=404)
        body = await request.body()
        if len(body) > 5 * 1024 * 1024 or not verify_webhook(secret, body, request.headers.get("x-hub-signature-256")):
            return JSONResponse({"error": "bad signature"}, status_code=401)
        event = request.headers.get("x-github-event", "")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse({"error": "bad payload"}, status_code=400)
        repo = (payload.get("repository") or {}).get("full_name", "")
        if not REPO_RE.match(repo) or repo not in cfg.github.repos:
            return JSONResponse({"ignored": "repository not configured"})
        try:
            await anyio.to_thread.run_sync(handle_event, controller, event, payload, repo)
        except Exception as exc:
            log.warning("webhook handling failed: %s", exc)
        return JSONResponse({"ok": True})

    if keyproxy is not None:
        from starlette.routing import Route

        server._custom_starlette_routes.append(
            Route("/keyproxy/{token}/{provider}/{path:path}", keyproxy.handle, methods=["GET", "POST"])
        )

    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(cfg.mcp.allowed_hosts),
        host=cfg.mcp.host,
    )
    return BearerAuth(app, store)


def handle_event(controller, event: str, payload: dict, repo: str) -> None:
    action = payload.get("action")
    if event == "pull_request" and action in ("opened", "synchronize", "reopened", "ready_for_review"):
        pr = payload["pull_request"]
        controller.consider(repo, pr["number"], pol.Trigger(kind="push"), pr_json=pr)
    elif event == "pull_request" and action == "labeled":
        label = (payload.get("label") or {}).get("name", "")
        if label in ("crosscheck:run", "crosscheck:matrix", "crosscheck:deep"):
            actor = (payload.get("sender") or {}).get("login", "")
            controller.consider(
                repo,
                payload["pull_request"]["number"],
                pol.Trigger(
                    kind="label",
                    stage="deep" if label == "crosscheck:deep" else "smoke",
                    requested_by=actor,
                    requester_class=controller.requester_class(repo, actor),
                ),
                pr_json=payload["pull_request"],
            )
    elif event == "pull_request_review" and action == "submitted":
        if (payload.get("review") or {}).get("state", "").lower() == "approved":
            pr = payload["pull_request"]
            controller.consider(repo, pr["number"], pol.Trigger(kind="approval"))
    elif event == "issue_comment" and action == "created" and (payload.get("issue") or {}).get("pull_request"):
        c = payload["comment"]
        controller.handle_comment(
            repo, payload["issue"]["number"], c["id"], (c.get("user") or {}).get("login", ""), c.get("body") or ""
        )
