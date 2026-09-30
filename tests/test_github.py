import json

import httpx

from crosscheck.github import GitHub, verify_webhook


def make(handler):
    return GitHub("https://api.github.test", token="t0ken", transport=httpx.MockTransport(handler))


def test_approvals_filter_by_sha_and_permission():
    def handler(req):
        p = req.url.path
        if p.endswith("/reviews"):
            return httpx.Response(
                200,
                json=[
                    {"user": {"login": "maint"}, "state": "APPROVED", "commit_id": "a" * 40},
                    {"user": {"login": "old"}, "state": "APPROVED", "commit_id": "c" * 40},
                    {"user": {"login": "rando"}, "state": "APPROVED", "commit_id": "a" * 40},
                    {"user": {"login": "flip"}, "state": "APPROVED", "commit_id": "a" * 40},
                    {"user": {"login": "flip"}, "state": "CHANGES_REQUESTED", "commit_id": "a" * 40},
                ],
            )
        if "/permission" in p:
            login = p.split("/")[-2]
            return httpx.Response(
                200, json={"permission": {"maint": "write", "old": "admin", "flip": "admin"}.get(login, "read")}
            )
        return httpx.Response(404, json={"message": "nope"})

    assert make(handler).approvals_on("o/r", 1, "a" * 40) == ["maint"]


def test_tarball_follows_redirect_and_limits():
    def handler(req):
        if req.url.path.endswith("/tarball/" + "a" * 40):
            assert req.headers["authorization"] == "Bearer t0ken"
            return httpx.Response(302, headers={"location": "https://codeload.test/x.tar.gz"})
        if req.url.host == "codeload.test":
            assert "authorization" not in req.headers
            return httpx.Response(200, content=b"TARBYTES")
        return httpx.Response(404)

    assert make(handler).tarball("o/r", "a" * 40) == b"TARBYTES"


def test_token_mode_sets_commit_status():
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={})

    make(handler).set_check("o/r", "a" * 40, "crosscheck", "completed", "neutral", "title", "summary")
    assert seen["path"] == "/repos/o/r/statuses/" + "a" * 40 and seen["body"]["state"] == "success"


def test_file_at_missing_and_too_big():
    def handler(req):
        if "missing" in req.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"type": "file", "size": 10_000_000, "content": ""})

    gh = make(handler)
    assert gh.file_at("o/r", "missing.yaml", "b" * 40) is None
    assert gh.file_at("o/r", "big.yaml", "b" * 40) is None


def test_webhook_signature():
    import hashlib
    import hmac

    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_webhook("s3cret", body, sig)
    assert not verify_webhook("s3cret", body + b" ", sig)
    assert not verify_webhook("s3cret", body, None)
