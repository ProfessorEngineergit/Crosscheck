from __future__ import annotations

from .base import Backend, BackendError


def make_backend(cfg: dict) -> Backend:
    kind = cfg["backend"]
    if kind == "qemu":
        from .qemu import QemuBackend

        return QemuBackend(cfg)
    if kind == "proxmox":
        from .proxmox import ProxmoxBackend

        return ProxmoxBackend(cfg)
    if kind == "fake":
        from .fake import FakeBackend

        return FakeBackend(cfg)
    raise BackendError(f"unknown backend {kind!r}")


__all__ = ["Backend", "BackendError", "make_backend"]
