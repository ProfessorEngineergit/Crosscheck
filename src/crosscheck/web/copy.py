"""Microcopy. Dry, nerdy one-liners live here, so security-relevant text elsewhere stays plain."""

from __future__ import annotations

import random

TAGLINES = [
    "works on my machine, and on yours, and on a potato",
    "every PR is hostile until proven boring",
    "have you tried turning the VM off and deleting it?",
    "0 secrets were harmed in the making of this report",
    "sudo make me a sandbox",
    "fork bombs welcome. forks too.",
]

PRESET_QUIPS = {
    "potato": "runs Doom, barely",
    "office": "37 toolbars, one fan",
    "mainstream": "the laptop your users actually own",
    "highend": "RGB adds 10% performance",
    "insane": "compiles the kernel while you blink",
    "phone-budget": "2 GB of RAM and a dream",
    "phone-flagship": "costs more than your first car",
}

STATUS_LABEL = {
    "success": ("pass", "pass"),
    "failure": ("fail", "fail"),
    "action_required": ("look", "review"),
    "timed_out": ("fail", "timeout"),
    "neutral": ("", "neutral"),
    "cancelled": ("", "aborted"),
    "running": ("run", "running"),
    "queued": ("run", "queued"),
    "met": ("pass", "met"),
    "not_met": ("fail", "not met"),
    "unclear": ("look", "unclear"),
    "flaky": ("look", "flaky"),
    "skipped": ("", "skipped"),
}

EMPTY_RUNS = [
    "Your PRs are suspiciously quiet.",
    "Nothing to check. The VMs are idling at 0.0% CPU.",
    "No runs yet. Open a pull request and watch it squirm.",
]

NOT_FOUND = "Like every VM here, this page was destroyed after use."


def tagline() -> str:
    return random.choice(TAGLINES)  # noqa: S311 - jokes, not crypto


def empty_runs() -> str:
    return random.choice(EMPTY_RUNS)  # noqa: S311
