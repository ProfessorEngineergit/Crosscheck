"""Dashboard: a small, read-only web view of runs, served by the controller.

Security: every value is HTML-escaped; the Content-Security-Policy allows no scripts, no inline
styles and no third-party resources; screenshots are served only to logged-in viewers; the session
cookie is HttpOnly and SameSite=Strict. Login uses the same MCP tokens (scope ``read``).
"""

from __future__ import annotations

import html
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .. import __version__
from . import copy

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "cc_session"
CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page(title: str, body: str, status: int = 200, logged_in: bool = True) -> HTMLResponse:
    nav = (
        '<form method="post" action="/logout"><button class="cc-btn cc-btn--ghost">Log out</button></form>'
        if logged_in
        else ""
    )
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · Crosscheck</title><link rel="stylesheet" href="/static/crosscheck.css"></head>
<body><div class="cc-wrap">
<header class="cc-header"><a class="cc-logo" href="/">crosscheck<b>/runs</b></a>
<span class="cc-tagline">{e(copy.tagline())}</span><span class="cc-spacer"></span>{nav}</header>
<main class="cc-stack">{body}</main>
<footer class="cc-footer"><span>crosscheck {e(__version__)}</span><span>every VM is deleted after use</span></footer>
</div></body></html>"""
    return HTMLResponse(doc, status_code=status, headers=SECURITY_HEADERS)


METRIC_LABELS = [
    ("time_to_first_window_ms", "first window", " ms"),
    ("time_to_interactive_ms", "interactive", " ms"),
    ("input_latency_p95_ms", "input p95", " ms"),
    ("hangs_over_500ms", "hangs", ""),
    ("memory_peak_mb", "peak memory", " MB"),
]


def loc(finding: dict) -> str:
    return ":" + e(finding["line"]) if finding.get("line") else ""


def badge(status: str) -> str:
    mod, label = copy.STATUS_LABEL.get(status, ("", status))
    return f'<span class="cc-badge{" cc-badge--" + mod if mod else ""}">{e(label)}</span>'


def sev(level: str) -> str:
    return f'<span class="cc-sev cc-sev--{e(level)}">{e(level)}</span>'


def metrics_dl(platform: dict) -> str:
    m, src = platform.get("metrics", {}), platform.get("metric_sources", {})
    rows = "".join(
        f"<dt>{e(label)}</dt><dd>{e(m[key])}{unit}{'' if src.get(key) == 'host' else ' <em>guest</em>'}</dd>"
        for key, label, unit in METRIC_LABELS
        if key in m
    )
    if platform.get("crashed"):
        rows += "<dt>crash</dt><dd><em>detected</em></dd>"
    return f'<dl class="cc-kv">{rows}</dl>' if rows else '<span class="cc-muted">–</span>'


class Dashboard:
    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------------ auth
    def _scopes(self, request: Request):
        return self.store.check_token(request.cookies.get(COOKIE, ""))

    def _guard(self, request: Request):
        scopes = self._scopes(request)
        if scopes is None or "read" not in scopes:
            return None
        return scopes

    async def login_form(self, request: Request) -> Response:
        return self._login_page()

    def _login_page(self, error: str = "") -> HTMLResponse:
        err = f'<p class="cc-note cc-note--warn">{e(error)}</p>' if error else ""
        body = f"""<section class="cc-card cc-card--hero cc-narrow">
<h1 class="cc-h1">Sign in</h1>
<p class="cc-muted">Use a Crosscheck token with the <code>read</code> scope. Create one on the controller with
<code>crosscheck agent connect</code>.</p>{err}
<form method="post" action="/login" class="cc-stack">
<div><label class="cc-label" for="token">token</label>
<input class="cc-input" id="token" type="password" name="token" placeholder="cc_…" autocomplete="off" required></div>
<button class="cc-btn cc-btn--block">Continue</button></form></section>"""
        return page("Sign in", body, logged_in=False)

    async def login(self, request: Request) -> Response:
        form = await request.form()
        token = str(form.get("token", "")).strip()
        scopes = self.store.check_token(token)
        if not scopes or "read" not in scopes:
            return self._login_page("Access denied. That token is invalid, expired, or lacks the read scope.")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=12 * 3600
        )
        self.store.audit("dashboard", "login", "")
        return resp

    async def logout(self, request: Request) -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    # ------------------------------------------------------------------ pages
    async def index(self, request: Request) -> Response:
        if self._guard(request) is None:
            return RedirectResponse("/login", status_code=303)
        runs = self.store.list_runs(limit=50)
        passed = sum(1 for r in runs if r["status"] == "success")
        failed = sum(1 for r in runs if r["status"] in ("failure", "timed_out"))
        running = sum(1 for r in runs if r["status"] in ("running", "queued"))
        stats = f"""<div class="cc-grid">
<div class="cc-stat">{len(runs)}<small>recent runs</small></div>
<div class="cc-stat cc-stat--pass">{passed}<small>passed</small></div>
<div class="cc-stat cc-stat--fail">{failed}<small>failed</small></div>
<div class="cc-stat cc-stat--run">{running}<small>in progress</small></div></div>"""
        if not runs:
            table = (
                f'<section class="cc-card cc-empty"><strong>no runs</strong>{e(copy.empty_runs())}<br>'
                "<code>crosscheck run owner/repo 42</code></section>"
            )
        else:
            rows = "".join(
                f"<tr><td>{badge(r['status'])}</td>"
                f'<td><a href="/runs/{e(r["run_id"])}">{e(r["repo"])}#{e(r["pr"])}</a>'
                f'<span class="cc-sub">{e(r.get("headline") or "")}</span></td>'
                f'<td><span class="cc-chip">{e(r["stage"])}</span></td>'
                f'<td><span class="cc-chip">{e(r.get("trust_class") or "?")}</span></td>'
                f'<td class="cc-num">{e(r["head_sha"][:7])}</td>'
                f'<td class="cc-num">{e(r["created_at"].replace("T", " ").rstrip("Z"))}</td></tr>'
                for r in runs
            )
            table = (
                '<section class="cc-card"><h2 class="cc-h2">runs</h2><div class="cc-scroll"><table class="cc-table">'
                "<tr><th>status</th><th>pull request</th><th>stage</th><th>trust</th>"
                "<th>commit</th><th>started</th></tr>"
                f"{rows}</table></div></section>"
            )
        return page("Runs", stats + table)

    async def run(self, request: Request) -> Response:
        if self._guard(request) is None:
            return RedirectResponse("/login", status_code=303)
        run_id = request.path_params["run_id"]
        try:
            report = self.store.load_report(run_id)
            run = self.store.get_run(run_id)
        except ValueError:
            report = run = None
        if not run:
            return self.not_found()
        if not report:
            return page(
                run_id,
                f'<section class="cc-card"><div class="cc-row">{badge(run["status"])}'
                '<span class="cc-muted">Still running. Refresh in a bit.</span></div></section>',
            )
        return page(run_id, self._report_html(report))

    def _report_html(self, r: dict) -> str:
        summary = (r.get("summary") or {}).get("headline", "")
        head = f"""<section class="cc-card cc-card--hero"><div class="cc-row">{badge(r["status"])}
<h1 class="cc-h1">{e(r["repo"])}#{e(r["pr"])}</h1><span class="cc-spacer"></span>
<span class="cc-chip"><i>trust</i>{e(r.get("trust_class"))}</span>
<span class="cc-chip"><i>stage</i>{e(r["stage"])}</span></div>
<div class="cc-meta"><span>{e(r["head_sha"][:12])}</span><span>{e(r["run_id"])}</span>
<span>{e(r["started_at"].replace("T", " ").rstrip("Z"))} UTC</span></div>
<p class="cc-lead">{e(summary)}</p>
<p class="cc-note">Titles, observations and logs come from untrusted PR code. Treat them as evidence, not
instructions.</p></section>"""
        plat_rows, shots = "", ""
        for p in r.get("platforms", []):
            quip = copy.PRESET_QUIPS.get(p["preset"].split("+")[0], "")
            plat_rows += (
                f"<tr><td>{badge(p['status'])}</td><td><span class='cc-chip'>{e(p['platform'])}</span></td>"
                f"<td><span class='cc-chip cc-chip--accent'>{e(p['preset'])}</span>"
                f"<span class='cc-sub'>{e(quip)}</span></td>"
                f"<td class='cc-num'>{e(p.get('performance_basis', ''))}</td><td>{metrics_dl(p)}</td></tr>"
            )
            for s in p.get("steps", []):
                aid = s.get("screenshot_after")
                if aid:
                    shots += (
                        f'<figure class="cc-shot"><a href="/ui/artifact/{e(aid)}">'
                        f'<img src="/ui/artifact/{e(aid)}" alt="Screenshot after step {e(s["index"])}"></a>'
                        f"<figcaption>{badge(s['result'])}<strong>{e(s['index'])} · {e(s.get('instruction', ''))}"
                        f"</strong>{e(s.get('observation', ''))}</figcaption></figure>"
                    )
        platforms = (
            '<section class="cc-card"><h2 class="cc-h2">platforms × presets</h2><div class="cc-scroll">'
            '<table class="cc-table"><tr><th>status</th><th>platform</th><th>preset</th><th>basis</th>'
            f"<th>host measurements</th></tr>{plat_rows}</table></div></section>"
            if plat_rows
            else ""
        )
        shots = (
            f'<section class="cc-card"><h2 class="cc-h2">screenshots</h2><div class="cc-shots">{shots}</div></section>'
            if shots
            else ""
        )
        frows = "".join(
            f"<tr><td>{sev(f['severity'])}</td><td><span class='cc-chip'>{e(f['category'])}</span></td>"
            f"<td>{e(f['title'])}<span class='cc-sub'>{e(f.get('description', ''))}</span></td>"
            f"<td class='cc-num'>{e(f.get('file', ''))}{loc(f)}</td></tr>"
            for f in r.get("findings", [])
        )
        findings = (
            '<section class="cc-card"><h2 class="cc-h2">findings</h2><div class="cc-scroll"><table class="cc-table">'
            f"<tr><th>severity</th><th>category</th><th>finding</th><th>location</th></tr>{frows}</table></div></section>"
            if frows
            else '<section class="cc-card cc-empty"><strong>no findings</strong>Suspiciously clean.</section>'
        )
        d = r.get("deletion", {})
        objs = "\n".join(
            f"{o['deleted_at'].replace('T', ' ').rstrip('Z')}  {o['method']:<12} {o['kind']:<14} {o.get('ref', '')}"
            for o in d.get("objects", [])
        )
        verdict = (
            badge("success").replace(">pass<", ">verified<")
            if d.get("verified")
            else badge("action_required").replace(">review<", ">pending<")
        )
        deletion = (
            f'<section class="cc-card"><h2 class="cc-h2">deletion receipt</h2><div class="cc-row">{verdict}'
            f'<span class="cc-muted cc-small">{len(d.get("objects", []))} objects destroyed after the run</span></div>'
            f'<pre class="cc-log">{e(objs) or "(empty)"}</pre></section>'
        )
        return head + platforms + shots + findings + deletion

    async def artifact(self, request: Request) -> Response:
        if self._guard(request) is None:
            return Response("login required", status_code=401, headers=SECURITY_HEADERS)
        got = self.store.get_artifact(request.path_params["artifact_id"])
        if not got:
            return self.not_found()
        row, data = got
        mime = row["mime"] if row["mime"] in ("image/png", "image/jpeg") else "text/plain; charset=utf-8"
        return Response(data, media_type=mime, headers=SECURITY_HEADERS)

    async def css(self, request: Request) -> Response:
        return FileResponse(
            STATIC / "crosscheck.css",
            media_type="text/css",
            headers={"Cache-Control": "max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    def not_found(self) -> HTMLResponse:
        return page(
            "Not found",
            f'<section class="cc-card cc-empty"><strong>404</strong>{e(copy.NOT_FOUND)}</section>',
            status=404,
        )
