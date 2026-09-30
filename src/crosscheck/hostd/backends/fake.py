"""In-memory backend for tests and demos. Simulates a desktop app without any VM.

Scenario is taken from ``hostd.fake.scenario``: ok, crash, build-fail, egress, canary, slow.
"""

from __future__ import annotations

import io
import json
import tarfile
import time

from PIL import Image, ImageDraw

from .base import Backend, VMRecord

MARKER_COLOR = (18, 52, 86)  # the guest runner's full-screen "building" marker


class FakeApp:
    MENU = {"File": ["New", "Open", "Settings", "Quit"], "Edit": ["Undo", "Redo"], "Help": ["About"]}

    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.open_menu: str | None = None
        self.dialog: str | None = None
        self.text = ""
        self.pointer = (0, 0)
        self.clicks = 0

    def menu_boxes(self) -> dict[str, tuple[int, int, int, int]]:
        boxes, x = {}, 10
        for name in self.MENU:
            boxes[name] = (x, 30, x + 60, 50)
            x += 70
        return boxes

    def item_boxes(self) -> dict[str, tuple[int, int, int, int]]:
        if not self.open_menu:
            return {}
        x0 = self.menu_boxes()[self.open_menu][0]
        return {item: (x0, 52 + i * 24, x0 + 140, 74 + i * 24) for i, item in enumerate(self.MENU[self.open_menu])}

    def click(self, x: int, y: int) -> None:
        self.clicks += 1
        for item, (a, b, c, d) in self.item_boxes().items():
            if a <= x <= c and b <= y <= d:
                self.dialog = item
                self.open_menu = None
                return
        for name, (a, b, c, d) in self.menu_boxes().items():
            if a <= x <= c and b <= y <= d:
                self.open_menu = None if self.open_menu == name else name
                return
        self.open_menu = None

    def key(self, qcode: str) -> None:
        if qcode == "esc":
            self.open_menu = None
            self.dialog = None
        elif len(qcode) == 1:
            self.text += qcode
        elif qcode == "spc":
            self.text += " "

    def render(self) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h), (235, 235, 240))
        dr = ImageDraw.Draw(img)
        dr.rectangle((0, 0, self.w, 26), fill=(40, 40, 60))
        dr.text((10, 7), "FakeApp - Crosscheck demo", fill=(255, 255, 255))
        for name, box in self.menu_boxes().items():
            dr.rectangle(box, fill=(210, 210, 220) if self.open_menu == name else (225, 225, 230))
            dr.text((box[0] + 8, box[1] + 5), name, fill=(0, 0, 0))
        dr.rectangle((10, 60, self.w - 10, self.h - 10), outline=(150, 150, 160), fill=(255, 255, 255))
        dr.text((20, 80), self.text or "Editor", fill=(0, 0, 0))
        for item, box in self.item_boxes().items():
            dr.rectangle(box, fill=(200, 215, 240), outline=(60, 60, 60))
            dr.text((box[0] + 6, box[1] + 5), item, fill=(0, 0, 0))
        if self.dialog:
            dr.rectangle(
                (self.w // 4, self.h // 4, 3 * self.w // 4, 3 * self.h // 4), fill=(245, 245, 255), outline=(0, 0, 0)
            )
            dr.text((self.w // 4 + 20, self.h // 4 + 20), f"{self.dialog} dialog", fill=(0, 0, 0))
        return img


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        fake = cfg.get("fake", {})
        self.scenario = fake.get("scenario", "ok")
        self.build_s = float(fake.get("build_s", 0.3))
        self.apps: dict[str, FakeApp] = {}
        self.events: dict[str, list] = {}

    def create(self, rec: VMRecord, job_files: dict[str, bytes], egress_allow: list[str]) -> None:
        rec.run_dir.mkdir(parents=True, exist_ok=True)
        rec.extra["job"] = json.loads(job_files.get("job.json", b"{}"))
        rec.extra["egress_allow"] = egress_allow
        rec.save()

    def start(self, rec: VMRecord) -> None:
        disp = rec.spec.get("displays", [{"width": 1280, "height": 800}])[0]
        self.apps[rec.vm_id] = FakeApp(min(disp["width"], 1280), min(disp["height"], 800))
        self.events[rec.vm_id] = []
        rec.started_at = time.monotonic()
        rec.state = "running"
        rec.save()
        with open(rec.run_dir / "egress.jsonl", "w") as fh:
            fh.write(
                json.dumps(
                    {"ts": time.time(), "allowed": True, "method": "CONNECT", "host": "registry.npmjs.org", "port": 443}
                )
                + "\n"
            )
            if self.scenario in ("egress", "canary"):
                fh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "allowed": False,
                            "method": "CONNECT",
                            "host": "evil.example.net",
                            "port": 443,
                            "reason": "host not on allow-list",
                            "canary": True if self.scenario == "canary" else None,
                        }
                    )
                    + "\n"
                )
                fh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "allowed": False,
                            "method": "GET",
                            "host": "169.254.169.254",
                            "port": 80,
                            "reason": "honeypot",
                        }
                    )
                    + "\n"
                )

    def _phase(self, rec: VMRecord) -> str:
        elapsed = time.monotonic() - (rec.started_at or time.monotonic())
        if elapsed < self.build_s:
            return "build"
        if self.scenario == "build-fail":
            return "desktop"
        if self.scenario == "crash" and elapsed > self.build_s + 3.0:
            return "desktop"
        if elapsed < self.build_s + (2.0 if self.scenario == "slow" else 0.8):
            return "desktop"
        return "app"

    def screenshot(self, rec: VMRecord) -> tuple[bytes, int, int]:
        app = self.apps[rec.vm_id]
        phase = self._phase(rec)
        if phase == "build":
            img = Image.new("RGB", (app.w, app.h), MARKER_COLOR)
            ImageDraw.Draw(img).text((20, 20), "Crosscheck: building", fill=(255, 255, 255))
        elif phase == "desktop":
            img = Image.new("RGB", (app.w, app.h), (60, 90, 120))
        else:
            img = app.render()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), app.w, app.h

    def send_events(self, rec: VMRecord, events: list[dict]) -> None:
        app = self.apps[rec.vm_id]
        self.events[rec.vm_id].append(events)
        x = y = None
        for ev in events:
            if ev["type"] == "abs":
                v = ev["data"]["value"] * ((app.w - 1) if ev["data"]["axis"] == "x" else (app.h - 1)) / 0x7FFF
                if ev["data"]["axis"] == "x":
                    x = round(v)
                else:
                    y = round(v)
        if x is not None and y is not None:
            app.pointer = (x, y)
        for ev in events:
            if ev["type"] == "btn" and ev["data"]["down"] and ev["data"]["button"] == "left":
                if self._phase(rec) == "app":
                    app.click(*app.pointer)
            if ev["type"] == "key" and ev["data"]["down"] and self._phase(rec) == "app":
                app.key(ev["data"]["key"]["data"])

    def state(self, rec: VMRecord) -> dict:
        return {"running": rec.state == "running", "cpu_seconds": 1.5, "memory_peak_mb": 900}

    def set_cpu(self, rec: VMRecord, quota_percent: int) -> None:
        rec.extra.setdefault("cpu_changes", []).append(quota_percent)

    def stop(self, rec: VMRecord) -> None:
        rec.state = "stopped"
        if rec.run_dir.exists():
            rec.save()

    def read_results(self, rec: VMRecord, max_bytes: int) -> bytes | None:
        status = {
            "build": {
                "ok": self.scenario != "build-fail",
                "exit_code": 0 if self.scenario != "build-fail" else 2,
                "duration_s": 12.5,
            },
            "launch": {"ok": self.scenario != "build-fail", "pid": 4242},
            "app_exit_code": 139 if self.scenario == "crash" else None,
            "bench": {"startup_ms": 50},
        }
        files = {
            "status.json": json.dumps(status).encode(),
            "build.log": b"npm ci\nadded 312 packages\nbuild ok\n"
            if self.scenario != "build-fail"
            else b"npm ERR! missing script: build\n",
            "app.log": b"starting\n" + (b"Segmentation fault\n" if self.scenario == "crash" else b"ready\n"),
            "audit.jsonl": b"",
        }
        if self.scenario == "canary":
            files["audit.jsonl"] = (
                json.dumps(
                    {"event": "open", "path": "/home/runner/.aws/credentials", "note": "AKIACROSSCHECKCANARY01"}
                ).encode()
                + b"\n"
            )
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def destroy(self, rec: VMRecord) -> list[dict]:
        import shutil

        self.apps.pop(rec.vm_id, None)
        shutil.rmtree(rec.run_dir, ignore_errors=True)
        rec.state = "destroyed"
        return [
            self.deletion("vm", rec.vm_id, "destroy"),
            self.deletion("disk-overlay", rec.vm_id, "crypto-shred"),
            self.deletion("run-dir", rec.vm_id, "unlink"),
        ]

    def leftovers(self, run_id: str) -> list[str]:
        return [f"vm {vm_id}" for vm_id, rec in self.vms.items() if rec.run_id == run_id and vm_id in self.apps]
