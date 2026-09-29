"""hostd RPC surface. Called by the controller in-process (single machine) or over SSH stdio.

hostd trusts the controller's requests (they come over an authenticated SSH connection with a
forced command), but validates shapes and limits anyway. It never returns anything but plain data.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import timedelta
from pathlib import Path

from .. import __version__
from ..util import iso, now
from . import qmp
from .backends import BackendError, make_backend
from .backends.base import VMRecord

_ID = re.compile(r"^[a-z0-9_]{3,40}$")
MAX_BLOB = 512 * 1024 * 1024
MAX_RESULTS = 64 * 1024 * 1024
BUTTONS = {"left", "right", "middle"}


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise BackendError(f"invalid {what}")
    return value


class HostdService:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.backend = make_backend(cfg)
        self.state_dir = Path(cfg["state_dir"])
        self.blob_dir = self.state_dir / "blobs"
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        Path(cfg["run_dir"]).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ dispatch
    def call(self, method: str, params: dict) -> object:
        if method.startswith("_") or not hasattr(self, f"rpc_{method}"):
            raise BackendError(f"unknown method {method!r}")
        return getattr(self, f"rpc_{method}")(**(params or {}))

    # ------------------------------------------------------------------ info
    def rpc_info(self) -> dict:
        cal = self.cfg.get("calibration", {})
        return {
            "version": __version__,
            **self.backend.info(),
            "templates": sorted(self.cfg.get("templates", {}) or self.cfg["proxmox"].get("template_ids", {})),
            "host_score": cal.get("host_score"),
            "host_cores": cal.get("host_cores") or os.cpu_count(),
            "max_parallel": self.cfg["max_parallel"],
            "max_vm_memory_mb": self.cfg["max_vm_memory_mb"],
            "active_vms": len(self.backend.vms),
        }

    # ------------------------------------------------------------------ blobs
    def rpc_put_blob(self, blob_id: str, offset: int, data_b64: str) -> dict:
        _check_id(blob_id, "blob id")
        data = base64.b64decode(data_b64)
        path = self.blob_dir / blob_id
        if offset == 0:
            path.write_bytes(b"")
        if offset != path.stat().st_size or offset + len(data) > MAX_BLOB:
            raise BackendError("blob offset mismatch or blob too large")
        with open(path, "ab") as fh:
            fh.write(data)
        return {"size": offset + len(data)}

    def _take_blob(self, blob_id: str | None) -> bytes | None:
        if not blob_id:
            return None
        path = self.blob_dir / _check_id(blob_id, "blob id")
        data = path.read_bytes()
        path.unlink()
        return data

    # ------------------------------------------------------------------ VMs
    def _free_slot(self) -> int:
        used = {rec.slot for rec in self.backend.vms.values()}
        for slot in range(max(1, self.cfg["max_parallel"]) * 4):
            if slot not in used:
                return slot
        raise BackendError("no free VM slot")

    def rpc_create_vm(
        self,
        run_id: str,
        platform: str,
        spec: dict,
        job: dict,
        ttl_s: int,
        blobs: dict | None = None,
        egress_allow: list[str] | None = None,
    ) -> dict:
        _check_id(run_id, "run id")
        if len(self.backend.vms) >= self.cfg["max_parallel"]:
            raise BackendError("runner host is busy")
        with self._lock:
            slot = self._free_slot()
            vm_id = f"v{int(time.time()) % 100000:05d}{slot:02d}"
            rec = VMRecord(
                vm_id=vm_id,
                run_id=run_id,
                platform=platform,
                spec=spec,
                run_dir=Path(self.cfg["run_dir"]) / vm_id,
                expires_at=iso(now() + timedelta(seconds=int(ttl_s) + 120)),
                slot=slot,
            )
            rec.extra["ttl_s"] = int(ttl_s)
            if len(str(rec.run_dir / "proxy.sock")) > 100:
                raise BackendError(
                    f"run_dir {self.cfg['run_dir']} is too long for Unix sockets (max ~90 chars); "
                    "use a short path such as /run/crosscheck"
                )
            self.backend.vms[vm_id] = rec
        files: dict[str, bytes] = {"job.json": json.dumps(job, indent=2).encode()}
        for name, blob_id in (blobs or {}).items():
            data = self._take_blob(blob_id)
            if data is not None:
                files[name] = data
        guest = Path(__file__).resolve().parent.parent / "guest" / "crosscheck_guest.py"
        if guest.exists():
            files["crosscheck-guest.py"] = guest.read_bytes()
        try:
            allow = [a for a in (egress_allow or []) if isinstance(a, str) and len(a) < 254][:100]
            self.backend.create(rec, files, allow)
        except Exception:
            self._destroy(rec)
            raise
        return {"vm_id": vm_id, "expires_at": rec.expires_at}

    def _rec(self, vm_id: str) -> VMRecord:
        rec = self.backend.vms.get(_check_id(vm_id, "vm id"))
        if not rec:
            raise BackendError(f"unknown VM {vm_id}")
        return rec

    def rpc_start_vm(self, vm_id: str) -> dict:
        rec = self._rec(vm_id)
        try:
            self.backend.start(rec)
        except Exception:
            self._destroy(rec)
            raise
        rec.started_at = time.monotonic()
        return {"state": rec.state}

    def rpc_screenshot(self, vm_id: str) -> dict:
        rec = self._rec(vm_id)
        png, w, h = self.backend.screenshot(rec)
        rec.screen = (w, h)
        return {"png_b64": base64.b64encode(png).decode(), "width": w, "height": h, "t": time.time()}

    def rpc_input(self, vm_id: str, actions: list[dict]) -> dict:
        rec = self._rec(vm_id)
        if not isinstance(actions, list) or len(actions) > 50:
            raise BackendError("too many actions")
        w, h = (
            rec.screen
            if rec.screen != (0, 0)
            else (rec.spec["displays"][0]["width"], rec.spec["displays"][0]["height"])
        )
        for a in actions:
            self._action(rec, a, w, h)
        return {"ok": True, "t": time.time()}

    def _action(self, rec: VMRecord, a: dict, w: int, h: int) -> None:
        send = lambda ev: self.backend.send_events(rec, ev)  # noqa: E731
        kind = a.get("action")

        def pos():
            x, y = int(a["x"]), int(a["y"])
            if not (0 <= x < w and 0 <= y < h):
                raise BackendError("coordinate outside the screen")
            return qmp.abs_events(x, y, w, h)

        mods = [qmp.keyname_to_qcode(m) for m in (a.get("modifiers") or []) if m][:4]
        if kind == "move":
            send(pos())
        elif kind in ("click", "double_click", "triple_click"):
            button = a.get("button", "left")
            if button not in BUTTONS:
                raise BackendError("invalid button")
            if "x" in a:
                send(pos())
            send([qmp.key_event(m, True) for m in mods])
            for _ in range({"click": 1, "double_click": 2, "triple_click": 3}[kind]):
                send([qmp.btn(button, True)])
                send([qmp.btn(button, False)])
            send([qmp.key_event(m, False) for m in reversed(mods)])
        elif kind in ("mouse_down", "mouse_up"):
            send([qmp.btn("left", kind == "mouse_down")])
        elif kind == "drag":
            send(qmp.abs_events(int(a["x1"]), int(a["y1"]), w, h))
            send([qmp.btn("left", True)])
            send(qmp.abs_events(int(a["x2"]), int(a["y2"]), w, h))
            send([qmp.btn("left", False)])
        elif kind == "scroll":
            if "x" in a:
                send(pos())
            direction = a.get("direction", "down")
            button = {"up": "wheel-up", "down": "wheel-down", "left": "wheel-left", "right": "wheel-right"}.get(
                direction
            )
            if not button:
                raise BackendError("invalid scroll direction")
            for _ in range(max(1, min(int(a.get("amount", 3)), 30))):
                send([qmp.btn(button, True), qmp.btn(button, False)])
        elif kind == "type":
            text = str(a.get("text", ""))[:2000]
            for batch in qmp.text_events(text):
                send(batch)
                time.sleep(0.005)
        elif kind == "key":
            for _ in range(max(1, min(int(a.get("repeat", 1)), 100))):
                send(qmp.combo_events(str(a["keys"])))
        elif kind == "hold_key":
            keys = [qmp.keyname_to_qcode(k) for k in str(a["keys"]).split("+") if k]
            send([qmp.key_event(k, True) for k in keys])
            time.sleep(min(float(a.get("duration", 1)), 10))
            send([qmp.key_event(k, False) for k in reversed(keys)])
        else:
            raise BackendError(f"unknown action {kind!r}")

    def rpc_vm_state(self, vm_id: str) -> dict:
        return self.backend.state(self._rec(vm_id))

    def rpc_set_cpu(self, vm_id: str, quota_percent: int) -> dict:
        self.backend.set_cpu(self._rec(vm_id), int(quota_percent))
        return {"ok": True}

    def rpc_stop_vm(self, vm_id: str) -> dict:
        self.backend.stop(self._rec(vm_id))
        return {"ok": True}

    def rpc_read_results(self, vm_id: str) -> dict:
        data = self.backend.read_results(self._rec(vm_id), MAX_RESULTS)
        return {"archive_b64": base64.b64encode(data).decode() if data else None}

    def rpc_egress_log(self, vm_id: str) -> dict:
        return {"entries": self.backend.egress_log(self._rec(vm_id))}

    def _destroy(self, rec: VMRecord) -> list[dict]:
        try:
            objs = self.backend.destroy(rec)
        finally:
            self.backend.vms.pop(rec.vm_id, None)
        return objs

    def rpc_destroy_vm(self, vm_id: str) -> dict:
        rec = self.backend.vms.get(_check_id(vm_id, "vm id"))
        if not rec:
            return {"objects": []}
        return {"objects": self._destroy(rec)}

    def rpc_verify_gone(self, run_id: str) -> dict:
        return {"leftovers": self.backend.leftovers(_check_id(run_id, "run id"))}

    def rpc_destroy_run(self, run_id: str, keep: list[str] | None = None) -> dict:
        _check_id(run_id, "run id")
        keep = set(keep or [])
        objs = []
        for rec in [r for r in self.backend.vms.values() if r.run_id == run_id and r.vm_id not in keep]:
            objs += self._destroy(rec)
        return {"objects": objs}

    def rpc_kill_all(self) -> dict:
        objs = []
        for rec in list(self.backend.vms.values()):
            objs += self._destroy(rec)
        for d in self.backend.known_run_dirs():
            try:
                objs += self.backend.destroy(VMRecord.load(d))
            except Exception:  # noqa: S110 - best effort in the kill switch
                pass
        return {"objects": objs}

    def rpc_janitor(self) -> dict:
        return {"destroyed": self.backend.janitor()}

    def rpc_list_vms(self) -> dict:
        return {
            "vms": [
                {
                    "vm_id": r.vm_id,
                    "run_id": r.run_id,
                    "platform": r.platform,
                    "state": r.state,
                    "expires_at": r.expires_at,
                }
                for r in self.backend.vms.values()
            ]
        }
