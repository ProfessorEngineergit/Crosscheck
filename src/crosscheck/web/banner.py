"""Terminal banner shared by init, demo and doctor."""

from __future__ import annotations

import os
import sys

BANNER = r"""
   ___                   _           _
  / __|_ _ ___ ______ __| |_  ___ __| |__
 | (__| '_/ _ (_-<_-</ _| ' \/ -_) _| / /
  \___|_| \___/__/__/\__|_||_\___\__|_\_\
"""

CYAN, MAGENTA, LIME, AMBER, RED, DIM, BOLD, RESET = (
    "\033[96m",
    "\033[95m",
    "\033[92m",
    "\033[93m",
    "\033[91m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


def color_ok(stream=sys.stdout) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, color: str, stream=sys.stdout) -> str:
    return f"{color}{text}{RESET}" if color_ok(stream) else text


def banner(tagline: str) -> str:
    return c(BANNER, CYAN) + "  " + c(tagline, MAGENTA) + "\n"
