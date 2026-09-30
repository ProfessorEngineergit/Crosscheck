"""Redaction of secrets in text that leaves a sandbox, and detection of canary credentials."""

from __future__ import annotations

import re

_PATTERNS = [
    ("aws-access-key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("url-credentials", re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")),
]

# Canary credentials planted in every guest image carry this marker. See docs/12.
CANARY_MARKER = "CROSSCHECKCANARY"
_CANARY = re.compile(r"[A-Za-z0-9_\-/+]*" + CANARY_MARKER + r"[A-Za-z0-9_\-/+]*")


def redact(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, kinds_found)."""
    found: list[str] = []
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            found.append(kind)
            if kind == "url-credentials":
                text = pattern.sub(lambda m: m.group(1) + "[REDACTED]@", text)
            else:
                text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text, found


def canary_hits(text: str) -> list[str]:
    return sorted(set(_CANARY.findall(text or "")))
