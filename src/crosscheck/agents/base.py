"""Shared pieces for operator agents: the VM handle with host-side timing, and step results."""

from __future__ import annotations

import base64
import io
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from PIL import Image, ImageChops

from ..util import clean_text

MAX_LONG_EDGE = 2576
MAX_VISUAL_TOKENS = 4784


@dataclass
class Frame:
    png: bytes
    width: int
    height: int
    t: float

    def image(self) -> Image.Image:
        return Image.open(io.BytesIO(self.png)).convert("RGB")


@dataclass
class StepResult:
    index: int
    instruction: str
    result: str = "unclear"  # met | not_met | unclear | flaky | skipped
    observation: str = ""
    screenshot_before: str | None = None  # artifact IDs, filled by the orchestrator
    screenshot_after: str | None = None
    duration_ms: int | None = None
    frames: list[Frame] = field(default_factory=list)


def changed_fraction(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        return 1.0
    """Fraction of 8x8 blocks that contain at least one clearly changed pixel."""
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0)
    small = diff.resize((max(1, a.width // 8), max(1, a.height // 8)), Image.Resampling.BOX)
    hist = small.histogram()
    return sum(hist[1:]) / max(1, small.width * small.height)


def dominant_fraction(img: Image.Image, rgb: tuple[int, int, int], tol: int = 12) -> float:
    small = img.resize((64, 40))
    px = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
    hits = sum(1 for p in px if all(abs(p[i] - rgb[i]) <= tol for i in range(3)))
    return hits / len(px)


class VMHandle:
    """What agents get: screenshots and input for exactly one VM, plus host-side latency measurement."""

    def __init__(self, hostd, vm_id: str, max_actions: int = 400):
        self.hostd = hostd
        self.vm_id = vm_id
        self.max_actions = max_actions
        self.actions = 0
        self.latencies_ms: list[int] = []
        self.hangs = 0
        self.last: Frame | None = None
        self.pointer = (0, 0)

    def screenshot(self) -> Frame:
        r = self.hostd.call("screenshot", vm_id=self.vm_id)
        self.last = Frame(base64.b64decode(r["png_b64"]), r["width"], r["height"], r["t"])
        return self.last

    def act(self, actions: list[dict], measure: bool = False) -> None:
        self.actions += len(actions)
        if self.actions > self.max_actions:
            raise RuntimeError("action limit reached")
        before = self.last or self.screenshot()
        for a in actions:
            if "x" in a and "y" in a:
                self.pointer = (int(a["x"]), int(a["y"]))
        self.hostd.call("input", vm_id=self.vm_id, actions=actions)
        if measure:
            self._measure(before)

    def _measure(self, before: Frame, timeout: float = 2.0) -> None:
        start = time.monotonic()
        base = before.image()
        while time.monotonic() - start < timeout:
            f = self.screenshot()
            if changed_fraction(base, f.image()) > 0.002:
                ms = int((time.monotonic() - start) * 1000)
                self.latencies_ms.append(ms)
                if ms > 500:
                    self.hangs += 1
                return
        self.hangs += 1

    def latency_stats(self) -> dict:
        if not self.latencies_ms:
            return {}
        xs = sorted(self.latencies_ms)
        p = lambda q: xs[min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1)]  # noqa: E731
        return {"input_latency_p50_ms": p(0.5), "input_latency_p95_ms": p(0.95), "hangs_over_500ms": self.hangs}


def fit_for_model(frame: Frame) -> tuple[bytes, float]:
    """Downscale a screenshot to the model's image limits. Returns (png, scale) where
    model coordinates * scale = screen coordinates."""
    w, h = frame.width, frame.height
    scale = 1.0
    while True:
        sw, sh = int(w / scale), int(h / scale)
        if (
            max(sw, sh) <= MAX_LONG_EDGE
            and math.ceil(sw / 28) * math.ceil(sh / 28) <= MAX_VISUAL_TOKENS
            and max(sw, sh) <= 1920
        ):
            break
        scale *= 1.25
    if scale == 1.0:
        return frame.png, 1.0
    img = frame.image().resize((int(w / scale), int(h / scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), scale


class Operator(ABC):
    name = "abstract"

    @abstractmethod
    def run(self, vm: VMHandle, steps: list[dict], *, max_steps: int, deadline: float) -> list[StepResult]:
        """Work through the smoke steps. Must stop at ``deadline`` (monotonic time)."""

    @staticmethod
    def sanitize(results: list[StepResult]) -> list[StepResult]:
        for r in results:
            if r.result not in ("met", "not_met", "unclear", "flaky", "skipped"):
                r.result = "unclear"
            r.observation = clean_text(r.observation, 2000)
        return results
