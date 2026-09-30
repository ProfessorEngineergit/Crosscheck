"""Resolve a hardware preset plus modifiers into a concrete VM specification.

Throttling makes a strong host look weak. A host can never look faster than it is; when a
preset exceeds the host, the strategies from docs/14 apply and the result is labelled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .resources import load_yaml

REFERENCE_SCORE = 1000  # one mid-range desktop core from 2024
TRAIT_SIM_VCPUS = 32  # vCPUs shown to the guest when simulating a many-core machine


class PresetError(ValueError):
    pass


@dataclass
class Display:
    width: int
    height: int
    scale: int = 100


@dataclass
class VMSpec:
    platform: str
    preset: str
    modifiers: list[str]
    vcpus: int
    cpu_model: str
    cpu_flags: list[str]
    cpu_quota_percent: int  # systemd CPUQuota semantics, 100 = one full core
    memory_mb: int
    disk: dict[str, int]  # mbps_read, mbps_write, iops_read, iops_write (0 = unthrottled)
    gpu: str
    displays: list[Display]
    network: dict[str, Any]
    network_name: str
    tmp_on_ramdisk: bool = False
    cpu_schedule: list[dict] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    trust_downgrades: list[str] = field(default_factory=list)
    performance_basis: str = "measured"
    target_score: int | None = None
    expected_score: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> VMSpec:
        raw = dict(raw)
        raw["displays"] = [Display(**d) for d in raw.get("displays", [])]
        return cls(**raw)


def catalog(extra: dict | None = None) -> dict:
    data = load_yaml("hardware.yaml")
    presets = dict(data["presets"])
    presets.update((extra or {}).get("presets", {}))
    return {
        "presets": presets,
        "networks": {**data["networks"], **(extra or {}).get("networks", {})},
        "modifiers": {**data["modifiers"], **(extra or {}).get("modifiers", {})},
    }


def parse_name(name: str) -> tuple[str, list[str]]:
    """``mainstream+offline+dark`` -> ("mainstream", ["offline", "dark"])."""
    parts = [p.strip() for p in name.split("+") if p.strip()]
    if not parts:
        raise PresetError("empty preset name")
    return parts[0], parts[1:]


def resolve(
    name: str,
    platform: str,
    *,
    host_score: int | None,
    host_cores: int | None,
    max_memory_mb: int,
    allowed_gpu: list[str],
    strategies: list[str] | None = None,
    extra: dict | None = None,
) -> VMSpec:
    cat = catalog(extra)
    base_name, modifiers = parse_name(name)
    if base_name not in cat["presets"]:
        raise PresetError(f"unknown preset {base_name!r}")
    preset = cat["presets"][base_name]
    if preset.get("platforms") and platform not in preset["platforms"]:
        raise PresetError(f"preset {base_name!r} is not available for {platform}")
    for mod in modifiers:
        if mod not in cat["modifiers"]:
            raise PresetError(f"unknown modifier {mod!r}")

    host_cores = max(1, host_cores or 1)
    host_score = host_score or REFERENCE_SCORE
    usable_cores = max(1, host_cores - 1) if host_cores > 2 else host_cores
    strategies = strategies if strategies is not None else preset.get("when_unreachable", [])

    cpu = preset.get("cpu") or preset.get("emulator") or {}
    want_cores = cpu.get("cores", 2)
    want_score = cpu.get("score", REFERENCE_SCORE)
    basis = "measured"
    target = None if want_score == "max" else int(want_score)

    if want_cores == "max":
        vcpus = usable_cores
        if "simulated-traits" in strategies and usable_cores < TRAIT_SIM_VCPUS:
            vcpus = TRAIT_SIM_VCPUS
            basis = "simulated-traits"
    else:
        vcpus = int(want_cores)
        if vcpus > usable_cores:
            basis = "simulated-traits" if "simulated-traits" in strategies else "not-reachable"

    # per-core speed factor relative to the host
    if target is None:
        per_core = 1.0
    else:
        per_core = min(1.0, target / host_score)
        if target > host_score:
            basis = "simulated-traits" if "simulated-traits" in strategies else "not-reachable"
    physical_cores_backing = min(vcpus, usable_cores)
    quota = max(5, int(round(physical_cores_backing * per_core * 100)))
    expected = int(round(min(host_score, target or host_score)))

    mem = cpu.get("memory_mb") or preset.get("memory_mb", 4096)
    if mem == "max":
        mem = max_memory_mb
        if "simulated-traits" in strategies:
            mem = max(mem, 65536)
            basis = "simulated-traits"
    mem = int(mem)
    if mem > max_memory_mb and basis != "simulated-traits":
        if "simulated-traits" in strategies:
            basis = "simulated-traits"  # lazily backed, capped by the VM's cgroup
        else:
            mem = max_memory_mb
            basis = "not-reachable" if basis == "measured" else basis

    disk_raw = preset.get("disk", "unthrottled")
    disk = {"mbps_read": 0, "mbps_write": 0, "iops_read": 0, "iops_write": 0}
    if isinstance(disk_raw, dict):
        disk.update({k: int(v) for k, v in disk_raw.items() if k in disk})

    # GPU: first option that the trust class allows; software is always allowed
    gpu_opts = preset.get("gpu", "software")
    gpu_opts = [gpu_opts] if isinstance(gpu_opts, str) else list(gpu_opts)
    gpu = "software"
    downgrades = []
    for opt in gpu_opts:
        if opt in ("software", "swiftshader") or opt in allowed_gpu:
            gpu = opt
            break
    if gpu_opts and gpu_opts[0] not in ("software", "swiftshader") and gpu != gpu_opts[0]:
        downgrades.append(f"gpu {gpu_opts[0]} -> {gpu} (not allowed for this trust class)")

    disp_raw = preset.get("display", {"width": 1920, "height": 1080, "scale": 100})
    disp_raw = disp_raw if isinstance(disp_raw, list) else [disp_raw]
    displays = [
        Display(width=int(d["width"]), height=int(d["height"]), scale=int(d.get("scale", 100))) for d in disp_raw
    ]

    network_name = preset.get("network", "lan")
    spec = VMSpec(
        platform=platform,
        preset=base_name,
        modifiers=modifiers,
        vcpus=vcpus,
        cpu_model=cpu.get("model", "host"),
        cpu_flags=list(cpu.get("flags", [])),
        cpu_quota_percent=quota,
        memory_mb=max(1024, mem),
        disk=disk,
        gpu=gpu,
        displays=displays,
        network=dict(cat["networks"].get(network_name, {})),
        network_name=network_name,
        tmp_on_ramdisk=bool(preset.get("tmp_on_ramdisk")),
        trust_downgrades=downgrades,
        performance_basis=basis,
        target_score=target,
        expected_score=expected,
    )
    for mod in modifiers:
        _apply_modifier(spec, mod, cat)
    return spec


def _apply_modifier(spec: VMSpec, mod: str, cat: dict) -> None:
    m = cat["modifiers"][mod]
    if "network" in m:
        spec.network = dict(cat["networks"][m["network"]])
        spec.network_name = m["network"]
    if "network_flap" in m:
        spec.network["flap"] = m["network_flap"]
    if "cpu_schedule" in m:
        spec.cpu_schedule = list(m["cpu_schedule"])
    for key in (
        "theme",
        "locale",
        "power_profile",
        "timezone",
        "clock_offset_days",
        "font_scale",
        "high_contrast",
        "free_disk_mb",
        "text_expansion_percent",
        "clock_set",
        "balloon_fill_percent_of_free",
    ):
        if key in m:
            spec.env[f"CROSSCHECK_{key.upper()}"] = str(m[key])
