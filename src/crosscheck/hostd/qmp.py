"""Minimal synchronous QMP client and input helpers.

hostd adds its own QMP socket to every VM. Screenshots use ``screendump``; input uses
``input-send-event`` against virtio-tablet (absolute pointer) and virtio-keyboard.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

ABS_MAX = 0x7FFF


class QMPError(RuntimeError):
    pass


class QMP:
    def __init__(self, path: str | Path, timeout: float = 10.0):
        self.path = str(path)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    def connect(self, wait: float = 30.0, alive=None) -> None:
        deadline = time.monotonic() + wait
        last: Exception | None = None
        while time.monotonic() < deadline:
            if alive is not None and not alive():
                raise QMPError("QEMU exited during startup")
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(self.timeout)
                s.connect(self.path)
                self._sock = s
                greeting = self._read()
                if "QMP" not in greeting:
                    raise QMPError("unexpected QMP greeting")
                self.execute("qmp_capabilities")
                return
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                last = exc
                time.sleep(0.2)
        raise QMPError(f"cannot connect to QMP socket {self.path}: {last}")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _read(self) -> dict:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise QMPError("QMP connection closed")
            self._buf += chunk
            if len(self._buf) > 4 * 1024 * 1024:
                raise QMPError("QMP message too large")
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def execute(self, command: str, arguments: dict | None = None) -> dict:
        if self._sock is None:
            raise QMPError("not connected")
        msg = {"execute": command}
        if arguments:
            msg["arguments"] = arguments
        self._sock.sendall(json.dumps(msg).encode() + b"\n")
        while True:
            reply = self._read()
            if "event" in reply:
                continue
            if "error" in reply:
                raise QMPError(f"{command}: {reply['error'].get('desc')}")
            return reply.get("return", {})

    # ------------------------------------------------------------------ helpers
    def screendump(self, filename: str, head: int | None = None) -> None:
        args: dict = {"filename": filename}
        if head is not None:
            args["head"] = head
        self.execute("screendump", args)

    def send_events(self, events: list[dict]) -> None:
        if events:
            self.execute("input-send-event", {"events": events})

    def quit(self) -> None:
        try:
            self.execute("quit")
        except QMPError:
            pass


# ---------------------------------------------------------------------- input mapping


def abs_events(x: int, y: int, width: int, height: int) -> list[dict]:
    ax = max(0, min(ABS_MAX, round(x * ABS_MAX / max(1, width - 1))))
    ay = max(0, min(ABS_MAX, round(y * ABS_MAX / max(1, height - 1))))
    return [{"type": "abs", "data": {"axis": "x", "value": ax}}, {"type": "abs", "data": {"axis": "y", "value": ay}}]


def btn(button: str, down: bool) -> dict:
    return {"type": "btn", "data": {"down": down, "button": button}}


def key_event(qcode: str, down: bool) -> dict:
    return {"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": qcode}}}


# US layout: character -> (qcode, shift)
_CHARS: dict[str, tuple[str, bool]] = {}
for c in "abcdefghijklmnopqrstuvwxyz":
    _CHARS[c] = (c, False)
    _CHARS[c.upper()] = (c, True)
for d in "0123456789":
    _CHARS[d] = (d, False)
for ch, q in {
    " ": "spc",
    "\n": "ret",
    "\t": "tab",
    "-": "minus",
    "=": "equal",
    "[": "bracket_left",
    "]": "bracket_right",
    "\\": "backslash",
    ";": "semicolon",
    "'": "apostrophe",
    "`": "grave_accent",
    ",": "comma",
    ".": "dot",
    "/": "slash",
}.items():
    _CHARS[ch] = (q, False)
for ch, q in {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "minus",
    "+": "equal",
    "{": "bracket_left",
    "}": "bracket_right",
    "|": "backslash",
    ":": "semicolon",
    '"': "apostrophe",
    "~": "grave_accent",
    "<": "comma",
    ">": "dot",
    "?": "slash",
}.items():
    _CHARS[ch] = (q, True)

_KEYNAMES = {
    "return": "ret",
    "enter": "ret",
    "escape": "esc",
    "esc": "esc",
    "tab": "tab",
    "space": "spc",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "page_up": "pgup",
    "pageup": "pgup",
    "page_down": "pgdn",
    "pagedown": "pgdn",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "super": "meta_l",
    "cmd": "meta_l",
    "meta": "meta_l",
    "win": "meta_l",
    "menu": "menu",
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
}


def keyname_to_qcode(name: str) -> str:
    n = name.strip().lower().replace("-", "_")
    if n in _KEYNAMES:
        return _KEYNAMES[n]
    if len(name) == 1 and name in _CHARS:
        return _CHARS[name][0]
    if len(n) == 1 and n in _CHARS:
        return _CHARS[n][0]
    raise ValueError(f"unknown key {name!r}")


def combo_events(combo: str) -> list[dict]:
    """``ctrl+shift+s`` -> press all in order, release in reverse."""
    keys = [keyname_to_qcode(k) for k in combo.split("+") if k]
    return [key_event(k, True) for k in keys] + [key_event(k, False) for k in reversed(keys)]


def text_events(text: str) -> list[list[dict]]:
    """One event batch per character. Unsupported characters raise ValueError."""
    batches = []
    for ch in text:
        if ch not in _CHARS:
            raise ValueError(f"cannot type character {ch!r} on the US layout")
        q, shift = _CHARS[ch]
        ev = []
        if shift:
            ev.append(key_event("shift", True))
        ev += [key_event(q, True), key_event(q, False)]
        if shift:
            ev.append(key_event("shift", False))
        batches.append(ev)
    return batches
