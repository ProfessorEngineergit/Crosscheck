import pytest

from crosscheck import runtime
from crosscheck.demo import DEMO_REPO, FakeGitHub, demo_config


@pytest.fixture
def make_rt(tmp_path):
    def _make(scenario="ok", gh=None, policy=None, **fake):
        cfg = demo_config(tmp_path / scenario, scenario)
        if policy:
            from crosscheck.config import deep_merge

            cfg.policy = deep_merge(cfg.policy, policy)
        cfg.hostd.setdefault("fake", {}).update(fake)
        gh = gh or FakeGitHub()
        rt = runtime.build(cfg, gh=gh)
        return rt, gh

    return _make


@pytest.fixture
def repo():
    return DEMO_REPO
