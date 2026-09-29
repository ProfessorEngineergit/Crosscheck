"""``crosscheck demo``: a complete run without GitHub, KVM or API keys.

Uses an in-memory GitHub, the fake VM backend and the scripted operator, then serves the MCP bridge
on localhost so you can connect Claude Code or Codex and look at real reports.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path

import yaml

from .config import ControllerConfig
from .policy import Trigger

DEMO_REPO = "demo/fake-app"
HEAD = "a" * 40
BASE = "b" * 40

DEMO_CONFIG = {
    "version": 1,
    "project": {"profile": "electron", "build": "npm ci && npm run build", "launch": {"linux": "npm start"}},
    "platforms": {"linux": {"required": True}},
    "matrix": {"default": ["mainstream"], "label_run": ["potato", "mainstream", "insane"]},
    "smoke": {
        "steps": [
            {
                "do": "Open the File menu.",
                "expect": "A menu with New, Open, Settings and Quit appears.",
                "required": True,
                "actions": [{"action": "click", "x": 40, "y": 40}],
                "expect_change": True,
            },
            {
                "do": "Choose Settings.",
                "expect": "A settings dialog appears.",
                "required": False,
                "actions": [{"action": "click", "x": 60, "y": 112}],
                "expect_change": True,
            },
            {
                "do": "Close the dialog with Escape.",
                "expect": "The dialog closes.",
                "required": False,
                "actions": [{"action": "key", "keys": "Escape"}],
                "expect_change": True,
            },
        ]
    },
}


class FakeGitHub:
    is_app = False

    def __init__(
        self,
        author: str = "someone-new",
        permission: str = "none",
        title: str = "Add settings dialog",
        body: str = "Adds a settings dialog.",
        config: dict | None = None,
        files: list[str] | None = None,
    ):
        self.author = author
        self.permission_level = permission
        self.title = title
        self.body = body
        self.config = config if config is not None else DEMO_CONFIG
        self.files = files or ["src/settings.ts", "package.json"]
        self.checks: list[dict] = []
        self.comments: dict[int, str] = {}
        self.approvals: list[str] = []
        self.labels: list[str] = []

    def _pr(self, number: int = 1) -> dict:
        return {
            "number": number,
            "title": self.title,
            "body": self.body,
            "user": {"login": self.author, "type": "User"},
            "labels": [{"name": n} for n in self.labels],
            "head": {"sha": HEAD, "repo": {"full_name": "someone-new/fake-app"}},
            "base": {"sha": BASE, "repo": {"full_name": DEMO_REPO}},
        }

    def pr(self, repo, number):
        return self._pr(number)

    def open_prs(self, repo, limit=50):
        return [self._pr(1)]

    def pr_files(self, repo, number):
        return self.files

    def pr_commit_messages(self, repo, number):
        return ["Add settings dialog"]

    def permission(self, repo, login):
        return self.permission_level if login == self.author else "admin"

    def merged_pr_count(self, repo, login):
        return 0

    def approvals_on(self, repo, number, sha):
        return list(self.approvals)

    def comments_since(self, repo, number, since):
        return []

    def label_actor(self, repo, number, label):
        return "maintainer"

    def file_at(self, repo, path, ref):
        if path == "crosscheck.yaml" and self.config is not None:
            return yaml.safe_dump(self.config)
        return None

    def tarball(self, repo, sha):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b'{"name": "fake-app", "scripts": {"build": "echo ok", "start": "echo start"}}'
            info = tarfile.TarInfo("demo-fake-app-aaaaaaa/package.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def set_check(self, repo, sha, name, status, conclusion, title, summary, check_id=None):
        self.checks.append({"status": status, "conclusion": conclusion, "title": title})
        return None

    def upsert_comment(self, repo, number, body, comment_id):
        cid = comment_id or 1
        self.comments[cid] = body
        return cid

    def react(self, repo, comment_id, content="eyes"):
        pass


def demo_config(root: Path, scenario: str = "ok") -> ControllerConfig:
    return ControllerConfig.from_dict(
        {
            "version": 1,
            "data_dir": str(root / "data"),
            "github": {"auth": "token", "repos": [DEMO_REPO]},
            "runner": {"transport": "local"},
            "mcp": {"host": "127.0.0.1", "port": 8750},
            "agents": {"operator": {"adapter": "none"}},
            "policy": {"defaults": {"smoke": {"mode": "all"}}},
            "hostd": {
                "backend": "fake",
                "state_dir": str(root / "hostd"),
                "run_dir": str(root / "run"),
                "templates": {"linux": "fake"},
                "calibration": {"host_score": 1200, "host_cores": 8},
                "fake": {"scenario": scenario, "build_s": 0.3},
            },
        }
    )


def run_demo(port: int = 8750, serve: bool = True, scenario: str = "ok", root: Path | None = None) -> dict:
    from . import runtime
    from .agent_setup import instructions

    root = root or Path(tempfile.mkdtemp(prefix="crosscheck-demo-"))
    from .web.banner import banner

    print(banner(f"demo mode: pretend VM, pretend PR, very real report (scenario: {scenario})"))
    cfg = demo_config(root, scenario)
    cfg.mcp.port = port
    gh = FakeGitHub()
    rt = runtime.build(cfg, gh=gh)
    rt.controller.consider(DEMO_REPO, 1, Trigger(kind="push"))
    plan = rt.controller.queue.get_nowait()
    report = rt.controller.execute(plan)
    from .report import summarize

    print(summarize(report))
    print(f"\nPR comment that would be posted:\n\n{gh.comments.get(1, '')}\n")
    if serve:
        token = rt.store.create_token("demo", ["read", "artifacts", "request", "interact"], expires_days=1)
        print(instructions(f"http://127.0.0.1:{port}", token))
        print(f"Dashboard: http://127.0.0.1:{port}/  (log in with the token above)")
        print(f"MCP:       http://127.0.0.1:{port}/mcp  (Ctrl+C to stop)")
        import threading

        import uvicorn

        from .http_app import build_app

        threading.Thread(target=rt.controller.worker_loop, daemon=True).start()
        uvicorn.run(build_app(rt.controller, rt.store, rt.keyproxy), host="127.0.0.1", port=port, log_level="warning")
    return report
