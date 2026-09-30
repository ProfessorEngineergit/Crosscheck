import json

import yaml

from crosscheck import agent_setup as ag
from crosscheck.images.build import FINAL_NETPLAN, user_data
from crosscheck.init_wizard import Answers, app_manifest, normalize_public_url, systemd_units, write_all
from crosscheck.redact import CANARY_MARKER


def test_codex_config_insert_and_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('model = "gpt-5"\n\n[mcp_servers.crosscheck]\nurl = "http://old/mcp"\n\n[other]\nx = 1\n')
    ag.setup_codex("https://cc.example.net", "cc_tok", config_path=cfg)
    text = cfg.read_text()
    assert 'url = "https://cc.example.net/mcp"' in text and "http://old" not in text
    assert "[other]" in text and 'model = "gpt-5"' in text
    assert 'bearer_token_env_var = "CROSSCHECK_TOKEN"' in text
    env = (tmp_path / ".config" / "crosscheck" / "env").read_text()
    assert "CROSSCHECK_TOKEN=cc_tok" in env
    assert oct((tmp_path / ".config" / "crosscheck" / "env").stat().st_mode)[-3:] == "600"
    assert "crosscheck" in (tmp_path / ".codex" / "AGENTS.md").read_text().lower()


def test_claude_project_settings_merge(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"x@y": True}}))
    path = ag.setup_claude_project(tmp_path)
    data = json.loads(path.read_text())
    assert data["enabledPlugins"] == {"x@y": True, "crosscheck@crosscheck": True}
    assert data["extraKnownMarketplaces"]["crosscheck"]["source"]["repo"] == ag.REPO


def test_instructions_mention_all_agents():
    text = ag.instructions("https://cc.example.net", "cc_tok")
    assert "claude mcp add" in text and "[mcp_servers.crosscheck]" in text and "/plugin install" in text


def test_write_all_and_units(tmp_path, monkeypatch):
    a = Answers(
        repos=["o/r"], github_token="ghp_x", anthropic_key="sk-ant-x", public_url="https://cc.example.net", systemd=True
    )
    written = write_all(a, tmp_path / "etc", tmp_path / "data")
    ctrl = yaml.safe_load(written["controller"].read_text())
    assert ctrl["runner"]["transport"] == "unix"
    assert ctrl["agents"]["operator"]["adapter"] == "claude"
    assert ctrl["mcp"]["allowed_hosts"] == ["cc.example.net"]
    assert "ghp_x" not in written["controller"].read_text()
    assert oct((tmp_path / "etc" / "secrets" / "github-token").stat().st_mode)[-3:] == "600"
    from crosscheck.config import ControllerConfig

    ControllerConfig.from_dict(ctrl)
    units = systemd_units("single", "/usr/local/bin/crosscheck", tmp_path / "etc")
    assert "User=crosscheck" in units["crosscheck-controller.service"]
    assert "User=" not in units["crosscheck-hostd.service"]


def test_app_manifest_minimal_permissions():
    m = app_manifest("x", "http://h/cb", None)
    assert "hook_attributes" not in m
    assert m["default_permissions"]["contents"] == "read"
    assert "administration" not in m["default_permissions"]
    assert "default_events" not in m  # events without a hook are rejected by GitHub


def test_app_manifest_with_webhook():
    m = app_manifest("x", "http://h/cb", " crosscheck.example.ts.net/ ")
    assert m["hook_attributes"]["url"] == "https://crosscheck.example.ts.net/webhook"
    assert "issue_comment" in m["default_events"]
    assert m["default_permissions"]["issues"] == "read"  # required by issue_comment


def test_normalize_public_url():
    assert normalize_public_url("") is None
    assert normalize_public_url("nonsense") is None
    assert normalize_public_url("two words.example.com") is None
    assert normalize_public_url("https://a.example.com/x/") == "https://a.example.com/x"
    assert normalize_public_url("a.example.com:8443") == "https://a.example.com:8443"


def test_image_user_data():
    text = user_data("linux", ["node"])
    assert text.startswith("#cloud-config")
    doc = yaml.safe_load(text)
    assert "nodejs" in doc["packages"] and "xfce4" in doc["packages"]
    assert CANARY_MARKER in text
    assert any("openssh-server" in c for c in doc["runcmd"])  # purged
    assert "gateway4" not in json.dumps(FINAL_NETPLAN) and "routes" not in json.dumps(FINAL_NETPLAN)
    assert doc["power_state"]["mode"] == "poweroff"
    assert "claude-code" in user_data("review")
    assert "gitleaks" in user_data("analysis")
