"""Connect coding agents to a Crosscheck controller.

Two sides:
- On the controller: ``crosscheck agent connect`` creates a scoped token and prints ready-to-paste
  commands for Claude Code (local), Claude Code (cloud) and Codex.
- On your laptop: ``crosscheck agent setup claude|codex --url ... --token ...`` edits the agent's
  configuration for you. Works without installing Crosscheck:
  ``uvx --from git+https://github.com/ProfessorEngineergit/Crosscheck crosscheck agent setup ...``
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .resources import asset_path

REPO = "ProfessorEngineergit/Crosscheck"
MARKETPLACE = "crosscheck"
PLUGIN = "crosscheck"
ENV_URL = "CROSSCHECK_URL"
ENV_TOKEN = "CROSSCHECK_TOKEN"  # noqa: S105 - name of an environment variable


def mcp_url(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/mcp") else base + "/mcp"


def instructions(url: str, token: str) -> str:
    u = mcp_url(url)
    return f"""Crosscheck is ready for your agents. The token below is shown only once.

  export {ENV_URL}={url.rstrip("/")}
  export {ENV_TOKEN}={token}

Claude Code on your machine (MCP server, all projects):
  claude mcp add --scope user --transport http crosscheck {u} --header "Authorization: Bearer ${{{ENV_TOKEN}}}"

Claude Code with the Crosscheck plugin (adds a skill and /crosscheck commands):
  /plugin marketplace add {REPO}
  /plugin install {PLUGIN}@{MARKETPLACE}
  (the plugin reads {ENV_URL} and {ENV_TOKEN} from the environment)

Claude Code in the cloud: add {ENV_URL} and {ENV_TOKEN} to the cloud environment's variables, allow the
host of {ENV_URL} in its network settings, and commit this to .claude/settings.json of your app repo:
{json.dumps(claude_project_settings(), indent=2)}

Codex (~/.codex/config.toml):
{codex_toml(u)}
Or let Crosscheck edit the files for you on your laptop:
  uvx --from git+https://github.com/{REPO} crosscheck agent setup claude --url {url.rstrip("/")} --token <token>
  uvx --from git+https://github.com/{REPO} crosscheck agent setup codex  --url {url.rstrip("/")} --token <token>
"""


def claude_project_settings() -> dict:
    return {
        "extraKnownMarketplaces": {MARKETPLACE: {"source": {"source": "github", "repo": REPO}}},
        "enabledPlugins": {f"{PLUGIN}@{MARKETPLACE}": True},
    }


def codex_toml(url: str) -> str:
    return f'[mcp_servers.crosscheck]\nurl = "{url}"\nbearer_token_env_var = "{ENV_TOKEN}"\n'


# ---------------------------------------------------------------------- laptop side
def _shell_profile() -> Path:
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("fish"):
        return home / ".config" / "fish" / "config.fish"
    return home / ".bashrc"


def save_env(url: str, token: str, profile: Path | None = None) -> Path:
    """Store URL and token in ~/.config/crosscheck/env (0600) and source it from the shell profile."""
    cfg_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "crosscheck"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    env_file = cfg_dir / "env"
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"export {ENV_URL}={url.rstrip('/')}\nexport {ENV_TOKEN}={token}\n")
    profile = profile or _shell_profile()
    line = f'[ -f "{env_file}" ] && . "{env_file}"  # crosscheck\n'
    if profile.name == "config.fish":
        line = f"test -f {env_file}; and source {env_file}  # crosscheck\n"
    existing = profile.read_text() if profile.exists() else ""
    if "# crosscheck" not in existing:
        profile.parent.mkdir(parents=True, exist_ok=True)
        with open(profile, "a") as fh:
            fh.write("\n" + line)
    return env_file


def setup_claude(url: str, token: str, scope: str = "user", dry_run: bool = False) -> list[str]:
    """Register the MCP server with Claude Code and install the plugin marketplace entry."""
    done = []
    env_file = save_env(url, token) if not dry_run else None
    if env_file:
        done.append(f"saved {ENV_URL}/{ENV_TOKEN} to {env_file} (sourced from your shell profile)")
    claude = shutil.which("claude")
    cmd = [
        "claude",
        "mcp",
        "add",
        "--scope",
        scope,
        "--transport",
        "http",
        "crosscheck",
        mcp_url(url),
        "--header",
        f"Authorization: Bearer ${{{ENV_TOKEN}}}",
    ]
    if claude and not dry_run:
        subprocess.run(["claude", "mcp", "remove", "--scope", scope, "crosscheck"], capture_output=True)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            done.append(f"registered MCP server 'crosscheck' in Claude Code ({scope} scope)")
        else:
            done.append("could not run 'claude mcp add', run it yourself: " + " ".join(_quote(cmd)))
    else:
        done.append("Claude Code CLI not found; run: " + " ".join(_quote(cmd)))
    done.append(
        f"optional plugin (skill + /crosscheck commands): /plugin marketplace add {REPO} "
        f"then /plugin install {PLUGIN}@{MARKETPLACE}"
    )
    return done


def setup_claude_project(project_dir: Path) -> Path:
    """Write .claude/settings.json in an app repository so cloud sessions get the plugin automatically."""
    path = project_dir / ".claude" / "settings.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    wanted = claude_project_settings()
    data.setdefault("extraKnownMarketplaces", {}).update(wanted["extraKnownMarketplaces"])
    data.setdefault("enabledPlugins", {}).update(wanted["enabledPlugins"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def setup_codex(url: str, token: str, config_path: Path | None = None, dry_run: bool = False) -> list[str]:
    done = []
    if not dry_run:
        env_file = save_env(url, token)
        done.append(f"saved {ENV_URL}/{ENV_TOKEN} to {env_file} (sourced from your shell profile)")
    path = config_path or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    text = path.read_text() if path.exists() else ""
    block = codex_toml(mcp_url(url))
    pattern = re.compile(r"^\[mcp_servers\.crosscheck\]\n(?:(?!^\[).*\n?)*", re.M)
    new = pattern.sub(block, text) if pattern.search(text) else (text.rstrip("\n") + "\n\n" + block if text else block)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
    done.append(f"{'would write' if dry_run else 'wrote'} [mcp_servers.crosscheck] to {path}")
    agents_md = path.parent / "AGENTS.md"
    guidance = (asset_path("assets", "agents-guidance.md")).read_text()
    marker = "<!-- crosscheck -->"
    existing = agents_md.read_text() if agents_md.exists() else ""
    if marker not in existing and not dry_run:
        with open(agents_md, "a") as fh:
            fh.write(("\n" if existing else "") + marker + "\n" + guidance)
        done.append(f"added Crosscheck guidance to {agents_md}")
    return done


def _quote(argv: list[str]) -> list[str]:
    import shlex

    return [shlex.quote(a) for a in argv]
