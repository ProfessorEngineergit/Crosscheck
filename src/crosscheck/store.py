"""Run, artifact and token storage: a directory tree plus a SQLite index."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .util import iso, new_id, now, parse_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, repo TEXT NOT NULL, pr INTEGER NOT NULL, head_sha TEXT NOT NULL,
  stage TEXT NOT NULL, status TEXT NOT NULL, trust_class TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, headline TEXT
);
CREATE INDEX IF NOT EXISTS runs_pr ON runs(repo, pr);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, file TEXT NOT NULL,
  mime TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
  repo TEXT NOT NULL, pr INTEGER NOT NULL, head_sha TEXT NOT NULL, stage TEXT NOT NULL,
  run_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY (repo, pr, head_sha, stage)
);
CREATE TABLE IF NOT EXISTS watches (
  run_id TEXT NOT NULL, url TEXT NOT NULL, secret TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token_id TEXT PRIMARY KEY, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, scopes TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger (
  run_id TEXT, kind TEXT NOT NULL, usd REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
  repo TEXT NOT NULL, pr INTEGER NOT NULL, comment_id INTEGER NOT NULL, PRIMARY KEY (repo, pr)
);
CREATE TABLE IF NOT EXISTS audit (
  at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT
);
"""

ALL_SCOPES = ("read", "artifacts", "request", "request:deep", "interact", "admin")
DEFAULT_SCOPES = ("read", "artifacts", "request")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.root / "index.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    # ------------------------------------------------------------------ runs
    def run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("r_") or "/" in run_id or ".." in run_id:
            raise ValueError("invalid run id")
        return self.root / "runs" / run_id

    def create_run(self, repo: str, pr: int, head_sha: str, stage: str, trust_class: str) -> str:
        run_id = new_id("r")
        self.run_dir(run_id).mkdir(parents=True)
        ts = iso()
        with self._tx() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, repo, pr, head_sha, stage, "queued", trust_class, ts, ts, None),
            )
        return run_id

    def update_status(self, run_id: str, status: str, headline: str | None = None) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE runs SET status=?, updated_at=?, headline=COALESCE(?, headline) WHERE run_id=?",
                (status, iso(), headline, run_id),
            )

    def save_report(self, run_id: str, report: dict) -> None:
        path = self.run_dir(run_id) / "report.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        tmp.replace(path)
        self.update_status(run_id, report["status"], (report.get("summary") or {}).get("headline"))

    def load_report(self, run_id: str) -> dict | None:
        path = self.run_dir(run_id) / "report.json"
        return json.loads(path.read_text()) if path.exists() else None

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(
        self, repo: str | None = None, pr: int | None = None, status: str | None = None, limit: int = 20
    ) -> list[dict]:
        q, args = "SELECT * FROM runs WHERE 1=1", []
        if repo:
            q += " AND repo=?"
            args.append(repo)
        if pr:
            q += " AND pr=?"
            args.append(pr)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))
        with self._lock:
            return [dict(r) for r in self._db.execute(q, args).fetchall()]

    def active_runs(self, repo: str, pr: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs WHERE repo=? AND pr=? AND status IN ('queued','running')", (repo, pr)
            )
            return [dict(r) for r in rows.fetchall()]

    # ------------------------------------------------------------------ dedupe
    def mark_seen(self, repo: str, pr: int, head_sha: str, stage: str, run_id: str | None) -> bool:
        """Returns False if this (repo, pr, sha, stage) was already handled."""
        with self._tx() as db:
            try:
                db.execute("INSERT INTO seen VALUES (?,?,?,?,?,?)", (repo, pr, head_sha, stage, run_id, iso()))
                return True
            except sqlite3.IntegrityError:
                return False

    def was_seen(self, repo: str, pr: int, head_sha: str, stage: str) -> bool:
        with self._lock:
            return (
                self._db.execute(
                    "SELECT 1 FROM seen WHERE repo=? AND pr=? AND head_sha=? AND stage=?", (repo, pr, head_sha, stage)
                ).fetchone()
                is not None
            )

    # ------------------------------------------------------------------ artifacts
    def add_artifact(self, run_id: str, kind: str, data: bytes, mime: str, ext: str) -> str:
        prefix = {"screenshot": "s", "video": "v", "log": "l", "diff": "d", "build": "b"}[kind]
        artifact_id = new_id(prefix, 12)
        adir = self.run_dir(run_id) / "artifacts"
        adir.mkdir(exist_ok=True)
        path = adir / f"{artifact_id}.{ext}"
        path.write_bytes(data)
        with self._tx() as db:
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?)",
                (artifact_id, run_id, kind, str(path.relative_to(self.root)), mime, iso()),
            )
        return artifact_id

    def get_artifact(self, artifact_id: str) -> tuple[dict, bytes] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if not row:
            return None
        path = (self.root / row["file"]).resolve()
        if not str(path).startswith(str(self.root.resolve())) or not path.exists():
            return None
        return dict(row), path.read_bytes()

    def apply_retention(self, retention: dict) -> int:
        """Delete artifacts past their retention period. Returns number deleted."""
        limits = {
            "screenshot": retention.get("screenshots_days", 30),
            "diff": retention.get("screenshots_days", 30),
            "video": retention.get("screenshots_days", 30),
            "log": retention.get("logs_days", 14),
            "build": retention.get("build_artifacts_days", 0),
        }
        deleted = 0
        with self._lock:
            rows = self._db.execute("SELECT * FROM artifacts").fetchall()
        for row in rows:
            age_days = (now() - parse_iso(row["created_at"])).total_seconds() / 86400
            if age_days >= limits.get(row["kind"], 30):
                (self.root / row["file"]).unlink(missing_ok=True)
                with self._tx() as db:
                    db.execute("DELETE FROM artifacts WHERE artifact_id=?", (row["artifact_id"],))
                deleted += 1
        keep_reports = retention.get("reports_days", 90)
        for run in self.list_runs(limit=200):
            if (now() - parse_iso(run["created_at"])).total_seconds() / 86400 >= keep_reports:
                shutil.rmtree(self.run_dir(run["run_id"]), ignore_errors=True)
                with self._tx() as db:
                    db.execute("DELETE FROM runs WHERE run_id=?", (run["run_id"],))
                deleted += 1
        return deleted

    # ------------------------------------------------------------------ watches
    def add_watch(self, run_id: str, url: str, secret: str | None) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO watches VALUES (?,?,?,?)", (run_id, url, secret, iso()))

    def pop_watches(self, run_id: str) -> list[dict]:
        with self._tx() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM watches WHERE run_id=?", (run_id,)).fetchall()]
            db.execute("DELETE FROM watches WHERE run_id=?", (run_id,))
        return rows

    # ------------------------------------------------------------------ comments
    def comment_id(self, repo: str, pr: int) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT comment_id FROM comments WHERE repo=? AND pr=?", (repo, pr)).fetchone()
        return row["comment_id"] if row else None

    def set_comment_id(self, repo: str, pr: int, comment_id: int) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO comments VALUES (?,?,?)", (repo, pr, comment_id))

    # ------------------------------------------------------------------ budget
    def spend(self, run_id: str | None, kind: str, usd: float) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO ledger VALUES (?,?,?,?)", (run_id, kind, float(usd), iso()))

    def spent_this_month(self, kind: str | None = None) -> float:
        month = iso()[:7]
        q = "SELECT COALESCE(SUM(usd),0) AS s FROM ledger WHERE substr(created_at,1,7)=?"
        args: list = [month]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._lock:
            return float(self._db.execute(q, args).fetchone()["s"])

    # ------------------------------------------------------------------ audit
    def audit(self, actor: str, action: str, detail: str = "") -> None:
        with self._tx() as db:
            db.execute("INSERT INTO audit VALUES (?,?,?,?)", (iso(), actor, action, detail[:1000]))

    def audit_log(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM audit ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ tokens
    def create_token(
        self, name: str, scopes: list[str] | tuple[str, ...] = DEFAULT_SCOPES, expires_days: int | None = 180
    ) -> str:
        bad = set(scopes) - set(ALL_SCOPES)
        if bad:
            raise ValueError(f"unknown scopes: {sorted(bad)}")
        token = "cc_" + secrets.token_urlsafe(32)
        expires = None
        if expires_days:
            from datetime import timedelta

            expires = iso(now() + timedelta(days=expires_days))
        with self._tx() as db:
            db.execute(
                "INSERT INTO tokens VALUES (?,?,?,?,?,?,0)",
                (new_id("t", 8), name, _hash(token), " ".join(scopes), iso(), expires),
            )
        return token

    def check_token(self, token: str) -> list[str] | None:
        if not token or not token.startswith("cc_"):
            return None
        with self._lock:
            row = self._db.execute("SELECT * FROM tokens WHERE hash=? AND revoked=0", (_hash(token),)).fetchone()
        if not row:
            return None
        if row["expires_at"] and parse_iso(row["expires_at"]) < now():
            return None
        return row["scopes"].split()

    def list_tokens(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT token_id, name, scopes, created_at, expires_at, revoked FROM tokens")
            return [dict(r) for r in rows.fetchall()]

    def revoke_token(self, token_id_or_name: str | None = None, all_tokens: bool = False) -> int:
        with self._tx() as db:
            if all_tokens:
                cur = db.execute("UPDATE tokens SET revoked=1 WHERE revoked=0")
            else:
                cur = db.execute(
                    "UPDATE tokens SET revoked=1 WHERE token_id=? OR name=?", (token_id_or_name, token_id_or_name)
                )
            return cur.rowcount
