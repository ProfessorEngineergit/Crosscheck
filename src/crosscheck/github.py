"""Minimal GitHub REST client: GitHub App (preferred) or fine-grained token.

Only the calls Crosscheck needs. The controller never clones; source comes as a tarball for a pinned SHA.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from dataclasses import dataclass

import httpx
import jwt

from . import __version__

MAX_TARBALL = 300 * 1024 * 1024
UA = f"crosscheck/{__version__}"


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


@dataclass
class Comment:
    id: int
    author: str
    body: str
    created_at: str
    author_association: str


class GitHub:
    def __init__(
        self,
        api_url: str = "https://api.github.com",
        token: str | None = None,
        app_id: int | None = None,
        private_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not token and not (app_id and private_key):
            raise ValueError("GitHub token or app_id + private_key required")
        self.api_url = api_url.rstrip("/")
        self.static_token = token
        self.app_id = app_id
        self.private_key = private_key
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": UA, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        self._inst_tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def is_app(self) -> bool:
        return self.static_token is None

    # ------------------------------------------------------------------ auth
    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}, self.private_key, algorithm="RS256"
        )

    def _token_for(self, repo: str) -> str:
        if self.static_token:
            return self.static_token
        owner = repo.split("/")[0]
        with self._lock:
            cached = self._inst_tokens.get(owner)
            if cached and cached[1] > time.time() + 300:
                return cached[0]
            headers = {"Authorization": f"Bearer {self._app_jwt()}"}
            r = self._http.get(f"{self.api_url}/repos/{repo}/installation", headers=headers)
            self._raise(r)
            inst = r.json()["id"]
            r = self._http.post(f"{self.api_url}/app/installations/{inst}/access_tokens", headers=headers)
            self._raise(r)
            data = r.json()
            exp = time.time() + 3000
            self._inst_tokens[owner] = (data["token"], exp)
            return data["token"]

    @staticmethod
    def _raise(r: httpx.Response) -> None:
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            raise GitHubError(r.status_code, str(msg)[:300])

    def _req(self, method: str, repo: str, path: str, **kw) -> httpx.Response:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token_for(repo)}"
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        r = self._http.request(method, url, headers=headers, **kw)
        self._raise(r)
        return r

    def get(self, repo: str, path: str, **params):
        return self._req("GET", repo, path, params=params or None).json()

    # ------------------------------------------------------------------ reads
    def open_prs(self, repo: str, limit: int = 50) -> list[dict]:
        return self.get(
            repo, f"/repos/{repo}/pulls", state="open", per_page=min(limit, 100), sort="updated", direction="desc"
        )

    def pr(self, repo: str, number: int) -> dict:
        return self.get(repo, f"/repos/{repo}/pulls/{number}")

    def pr_files(self, repo: str, number: int) -> list[str]:
        files = self.get(repo, f"/repos/{repo}/pulls/{number}/files", per_page=100)
        return [f["filename"] for f in files]

    def pr_commit_messages(self, repo: str, number: int) -> list[str]:
        commits = self.get(repo, f"/repos/{repo}/pulls/{number}/commits", per_page=100)
        return [c["commit"]["message"] for c in commits]

    def permission(self, repo: str, login: str) -> str:
        try:
            return self.get(repo, f"/repos/{repo}/collaborators/{login}/permission").get("permission", "none")
        except GitHubError as exc:
            if exc.status in (403, 404):
                return "none"
            raise

    def user_teams(self, repo: str, login: str) -> list[str]:
        return []  # team lookups need org read access; configured teams are resolved lazily in future

    def merged_pr_count(self, repo: str, login: str) -> int:
        try:
            data = self.get(repo, "/search/issues", q=f"repo:{repo} is:pr is:merged author:{login}", per_page=1)
            return int(data.get("total_count", 0))
        except GitHubError:
            return 0

    def approvals_on(self, repo: str, number: int, sha: str) -> list[str]:
        reviews = self.get(repo, f"/repos/{repo}/pulls/{number}/reviews", per_page=100)
        latest: dict[str, dict] = {}
        for rv in reviews:
            login = (rv.get("user") or {}).get("login")
            if login:
                latest[login] = rv
        out = []
        for login, rv in latest.items():
            if (
                rv.get("state") == "APPROVED"
                and rv.get("commit_id") == sha
                and self.permission(repo, login) in ("admin", "maintain", "write")
            ):
                out.append(login)
        return out

    def comments_since(self, repo: str, number: int, since: str | None) -> list[Comment]:
        params = {"per_page": 100}
        if since:
            params["since"] = since
        data = self.get(repo, f"/repos/{repo}/issues/{number}/comments", **params)
        return [
            Comment(
                c["id"],
                (c.get("user") or {}).get("login", ""),
                c.get("body") or "",
                c["created_at"],
                c.get("author_association", "NONE"),
            )
            for c in data
        ]

    def label_actor(self, repo: str, number: int, label: str) -> str | None:
        events = self.get(repo, f"/repos/{repo}/issues/{number}/events", per_page=100)
        actor = None
        for ev in events:
            if ev.get("event") == "labeled" and (ev.get("label") or {}).get("name") == label:
                actor = (ev.get("actor") or {}).get("login")
        return actor

    def file_at(self, repo: str, path: str, ref: str, max_bytes: int = 256 * 1024) -> str | None:
        try:
            data = self.get(repo, f"/repos/{repo}/contents/{path}", ref=ref)
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("type") != "file" or data.get("size", 0) > max_bytes:
            return None
        return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")

    def tarball(self, repo: str, sha: str) -> bytes:
        r = self._http.get(
            f"{self.api_url}/repos/{repo}/tarball/{sha}", headers={"Authorization": f"Bearer {self._token_for(repo)}"}
        )
        if r.status_code in (301, 302, 307, 308):
            loc = r.headers["location"]
            with self._http.stream("GET", loc, follow_redirects=True) as s:
                self._raise(s)
                buf = bytearray()
                for chunk in s.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_TARBALL:
                        raise GitHubError(413, "source tarball too large")
                return bytes(buf)
        self._raise(r)
        return r.content

    # ------------------------------------------------------------------ writes
    def set_check(
        self,
        repo: str,
        sha: str,
        name: str,
        status: str,
        conclusion: str | None,
        title: str,
        summary: str,
        check_id: int | None = None,
    ) -> int | None:
        """Check Run with a GitHub App, commit status otherwise."""
        if self.is_app:
            body = {
                "name": name,
                "head_sha": sha,
                "status": status,
                "output": {"title": title[:250], "summary": summary[:60000]},
            }
            if conclusion:
                body["conclusion"] = conclusion
            if check_id:
                r = self._req("PATCH", repo, f"/repos/{repo}/check-runs/{check_id}", json=body)
            else:
                r = self._req("POST", repo, f"/repos/{repo}/check-runs", json=body)
            return r.json()["id"]
        state = {
            "success": "success",
            "neutral": "success",
            "failure": "failure",
            "action_required": "failure",
            "cancelled": "error",
            "timed_out": "failure",
        }.get(conclusion or "", "pending")
        self._req(
            "POST",
            repo,
            f"/repos/{repo}/statuses/{sha}",
            json={"state": state, "context": name, "description": title[:140]},
        )
        return None

    def upsert_comment(self, repo: str, number: int, body: str, comment_id: int | None) -> int:
        if comment_id:
            try:
                r = self._req("PATCH", repo, f"/repos/{repo}/issues/comments/{comment_id}", json={"body": body})
                return r.json()["id"]
            except GitHubError as exc:
                if exc.status != 404:
                    raise
        r = self._req("POST", repo, f"/repos/{repo}/issues/{number}/comments", json={"body": body})
        return r.json()["id"]

    def react(self, repo: str, comment_id: int, content: str = "eyes") -> None:
        try:
            self._req("POST", repo, f"/repos/{repo}/issues/comments/{comment_id}/reactions", json={"content": content})
        except GitHubError:
            pass

    # ------------------------------------------------------------------ app manifest
    @staticmethod
    def convert_manifest(code: str, api_url: str = "https://api.github.com") -> dict:
        r = httpx.post(
            f"{api_url.rstrip('/')}/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json", "User-Agent": UA},
            timeout=30,
        )
        GitHub._raise(r)
        return r.json()


def verify_webhook(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
