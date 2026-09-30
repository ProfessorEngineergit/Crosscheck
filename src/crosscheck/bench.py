"""Calibration micro-benchmark. Score 1000 is meant to approximate one mid-range desktop core from 2024.

The absolute scale is approximate. What matters is that the same benchmark runs on the host and in
every VM, so presets can be expressed as a ratio of the host's speed.
"""

from __future__ import annotations

import json
import os
import time
import zlib

REFERENCE_SECONDS = 0.55  # time of one round on the reference core


def _round() -> None:
    data = json.dumps(
        [{"id": i, "name": f"item-{i}", "tags": ["a", "b", str(i % 7)], "v": i * 1.5} for i in range(6000)]
    ).encode()
    for _ in range(3):
        c = zlib.compress(data, 6)
        json.loads(zlib.decompress(c))
    s = 0
    for i in range(250_000):
        s = (s * 31 + i) % 1_000_003


def single_thread_score(rounds: int = 3) -> int:
    best = float("inf")
    for _ in range(rounds):
        t = time.perf_counter()
        _round()
        best = min(best, time.perf_counter() - t)
    return max(1, int(round(1000 * REFERENCE_SECONDS / best)))


def host_calibration() -> dict:
    return {"host_score": single_thread_score(), "host_cores": os.cpu_count() or 1}
