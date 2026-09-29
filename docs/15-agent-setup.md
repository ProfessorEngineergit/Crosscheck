# 15 Agent setup: Claude Code and Codex

Crosscheck talks to agents through one MCP server at `<controller>/mcp`, protected by bearer tokens
with scopes. You configure it once per machine or environment.

## 1. Get a token

On the controller:

```bash
sudo crosscheck agent connect --name laptop
```

This creates a token with the scopes `read`, `artifacts`, `request`. It prints the token once,
together with all commands below, pre-filled. Other scopes: `request:deep` (deep reviews, costs
model budget), `interact` (control a held VM), `admin`. For example:

```bash
sudo crosscheck agent connect --name phone-cloud --scopes read,artifacts
sudo crosscheck token list
sudo crosscheck token revoke phone-cloud
```

Tokens expire after 180 days by default. Only a hash is stored on the controller.

## 2. Claude Code on your machine

Easiest, if Crosscheck is installed or `uvx` is available on the laptop:

```bash
uvx --from git+https://github.com/ProfessorEngineergit/Crosscheck crosscheck agent setup claude \
  --url https://crosscheck.example.net --token cc_...
```

It stores `CROSSCHECK_URL` and `CROSSCHECK_TOKEN` in `~/.config/crosscheck/env` (mode 0600),
sources that file from your shell profile, and registers the MCP server with Claude Code at user
scope. By hand it is one command:

```bash
claude mcp add --scope user --transport http crosscheck https://crosscheck.example.net/mcp \
  --header "Authorization: Bearer $CROSSCHECK_TOKEN"
```

### The plugin (recommended)

The repository is also a Claude Code plugin marketplace. The plugin bundles the MCP connection
(reading `CROSSCHECK_URL` and `CROSSCHECK_TOKEN` from the environment), a skill that teaches the
agent how to read reports correctly and safely, and commands:

```
/plugin marketplace add ProfessorEngineergit/Crosscheck
/plugin install crosscheck@crosscheck
```

| Command | Does |
|---------|------|
| `/crosscheck:status owner/repo 42` | Summary of the latest run, platforms, presets, top findings |
| `/crosscheck:check owner/repo 42 [platforms] [presets]` | Requests a smoke run |
| `/crosscheck:deep-review owner/repo 42` | Asks you first, then requests a deep review |
| `/crosscheck:screenshot r_... [platform]` | Shows and describes the screenshot of a failed step |

Or simply ask: "What did Crosscheck find on PR 42 on Windows?"

## 3. Claude Code in the cloud (claude.ai/code, phone)

Cloud sessions run in a container that cannot see your LAN, so the controller needs a public HTTPS
URL (see [08 Installation](08-installation.md)).

1. In the cloud environment's settings, add the variables `CROSSCHECK_URL` and `CROSSCHECK_TOKEN`
   (a token with only `read,artifacts` is enough for reading results).
2. Allow the host of `CROSSCHECK_URL` in the environment's network settings.
3. In your app repository, commit the plugin settings so every cloud session loads the plugin:

   ```bash
   crosscheck agent setup claude-project --dir path/to/your/app
   ```

   This merges into `.claude/settings.json`:

   ```json
   {
     "extraKnownMarketplaces": {"crosscheck": {"source": {"source": "github", "repo": "ProfessorEngineergit/Crosscheck"}}},
     "enabledPlugins": {"crosscheck@crosscheck": true}
   }
   ```

To be woken up when a run finishes instead of polling, a session can call `crosscheck_watch_run`
with a public HTTPS callback URL (for example the one Claude Code cloud sessions create for
webhooks). The payload is signed with `X-Crosscheck-Signature`.

## 4. Codex

```bash
uvx --from git+https://github.com/ProfessorEngineergit/Crosscheck crosscheck agent setup codex \
  --url https://crosscheck.example.net --token cc_...
```

It saves the environment file as above, adds this to `~/.codex/config.toml` (replacing an older
`crosscheck` entry, keeping everything else):

```toml
[mcp_servers.crosscheck]
url = "https://crosscheck.example.net/mcp"
bearer_token_env_var = "CROSSCHECK_TOKEN"
```

and appends short usage guidance to `~/.codex/AGENTS.md`. Codex reads the token from the
environment, so it is never written into the config file.

## Using it well

- Ask for the summary first; the agent fetches details and screenshots by ID only when needed.
  This keeps long sessions small.
- Everything from a run is marked as untrusted observation. The skill and the guidance tell the
  agent never to follow instructions found in findings, logs or screenshots, and to point them
  out as likely prompt injection instead.
- `performance_basis` and `metric_sources` tell you how much a number is worth: `measured` and
  `host` are real; `simulated-traits`, `extrapolated` and `guest` are hints.
- Deep reviews cost model budget. The plugin asks you before requesting one.

## Security notes

- The token is a bearer credential. Keep it in the environment file or the cloud environment's
  variables, never in a repository. Revoke with `crosscheck token revoke <name>`.
- `crosscheck kill --revoke-tokens` revokes all tokens at once.
- The MCP endpoint only exposes reports and artifacts of configured repositories, and the request
  tools are limited to those repositories.
