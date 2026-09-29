import pytest

from crosscheck.presets import PresetError, resolve


def test_potato_on_fast_host_is_throttled():
    s = resolve("potato", "linux", host_score=2000, host_cores=16, max_memory_mb=16384, allowed_gpu=[])
    assert s.vcpus == 2
    assert s.cpu_quota_percent == 30  # 2 cores * 300/2000
    assert "-avx2" in s.cpu_flags
    assert s.disk["iops_read"] == 120
    assert s.displays[0].width == 1366
    assert s.performance_basis == "measured"


def test_insane_on_weak_host_uses_simulated_traits():
    s = resolve("insane", "linux", host_score=800, host_cores=4, max_memory_mb=8192, allowed_gpu=[])
    assert s.performance_basis == "simulated-traits"
    assert s.vcpus == 32
    assert s.memory_mb >= 65536
    assert s.gpu == "software" and s.trust_downgrades


def test_unreachable_without_strategies():
    s = resolve(
        "highend", "linux", host_score=900, host_cores=4, max_memory_mb=8192, allowed_gpu=["virgl"], strategies=[]
    )
    assert s.performance_basis == "not-reachable"
    assert s.gpu == "virgl"


def test_modifiers():
    s = resolve(
        "mainstream+offline+thermal+dark", "linux", host_score=1000, host_cores=8, max_memory_mb=16384, allowed_gpu=[]
    )
    assert s.network.get("loss_percent") == 100
    assert s.cpu_schedule and s.env["CROSSCHECK_THEME"] == "dark"


def test_errors():
    with pytest.raises(PresetError):
        resolve("nope", "linux", host_score=1, host_cores=1, max_memory_mb=1, allowed_gpu=[])
    with pytest.raises(PresetError):
        resolve("phone-budget", "linux", host_score=1, host_cores=1, max_memory_mb=1, allowed_gpu=[])
    with pytest.raises(PresetError):
        resolve("mainstream+warp", "linux", host_score=1, host_cores=1, max_memory_mb=1, allowed_gpu=[])
