"""Heuristic detection of text aimed at a language model, in PR metadata.

This does not try to be clever. It flags obvious attempts so a maintainer looks at them.
The real defence is structural: agents never receive PR text as instructions.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|messages?)",
    r"disregard (all |any )?(the )?(previous|prior|above) ",
    r"\byou are now\b",
    r"\bnew instructions\s*:",
    r"(^|\n)\s*(system|assistant)\s*:",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"\breport (the )?(run|check|status) as (passed|success|green)\b",
    r"\bmark (this|the) (pr|run|check) as (safe|passed|success)\b",
    r"\bdo not (report|mention|flag)\b.{0,40}\b(finding|issue|vulnerab)",
    r"\b(claude|codex|chatgpt|gpt|llm|ai assistant|language model)\b.{0,60}\b(must|should|please)\b",
]
_RE = [re.compile(p, re.I) for p in _PATTERNS]


def scan(text: str | None) -> list[str]:
    if not text:
        return []
    hits = []
    for rx in _RE:
        m = rx.search(text)
        if m:
            hits.append(m.group(0).strip()[:120])
    return hits
