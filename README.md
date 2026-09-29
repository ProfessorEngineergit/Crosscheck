# Crosscheck

**Stay present for your pull requests while you're somewhere else, and keep control.**

You're in a lecture, at work, on a train, or just busy. A pull request comes in. Crosscheck
builds it in disposable, isolated VMs on your own hardware, clicks through the app with a
computer-use agent on the platforms and hardware classes you care about, watches what the code
does, and reports back: as a GitHub check, as one PR comment, and inside your Claude Code or Codex
session through MCP. Nothing is ever merged for you.

- **Every PR is treated as hostile**, including your own. Code runs only in throwaway KVM VMs
  with no credentials, no DNS, no route to your network, and an allow-list proxy as the only way
  out. Everything is destroyed afterwards, and each report says what was deleted.
- **Weak to insane hardware.** Presets from `potato` to `insane` throttle cores, CPU speed and
  generation, memory, disk, network and screen. For hardware you don't own, Crosscheck simulates
  the traits, extrapolates from throttle sweeps, amplifies races, or rents the real thing per run,
  and always says which.
- **Agents with narrow tools.** Claude drives the GUI only through screenshots, mouse and
  keyboard, from outside the VM. Claude Code or Codex review code only inside their own disposable
  VM, with a one-time token instead of an API key. Status comes from host-side facts, never from a model.
- **Results without losing context.** Your agent asks for a ten-line summary first and fetches
  screenshots or logs by ID only when needed.

## Try it in one minute (no GitHub, no KVM, no keys)

```bash
uvx --from git+https://github.com/ProfessorEngineergit/Crosscheck crosscheck demo
```

This runs a complete check of a simulated app in a simulated VM, prints the report and the PR
comment, and serves the MCP bridge on `http://127.0.0.1:8750`. It also prints the commands to
connect Claude Code or Codex so you can ask your agent about the run.
Try `--scenario crash`, `build-fail`, `egress` or `canary` to see how problems are reported.

## Install

On a dedicated Linux machine with KVM (Debian, Ubuntu, Fedora) or a Proxmox VE host:

```bash
curl -fsSL https://raw.githubusercontent.com/ProfessorEngineergit/Crosscheck/main/install.sh | sudo bash
```

The script installs QEMU and Crosscheck, then starts `crosscheck init`, which asks a few questions:

1. **Topology:** everything on this machine, or a controller plus a separate runner host (safest).
2. **Repositories** to check.
3. **GitHub:** one click creates a GitHub App with minimal permissions (a page with a QR code opens
   on your phone or laptop), or paste a fine-grained token.
4. **Anthropic API key** for the operator agent (optional; without it, checks run scripted).
5. **When PRs start automatically:** everyone, after your approval of the exact commit,
   maintainers only, or only when asked. Isolation is the same in every mode.

It then installs two services (`crosscheck-hostd` as root without secrets, `crosscheck-controller`
as an unprivileged user with the secrets) and prints ready-to-paste commands for your agents.
Finish with:

```bash
sudo crosscheck images build linux      # golden VM image from the newest Ubuntu cloud image
sudo crosscheck doctor --calibrate      # host checks and hardware calibration
sudo crosscheck doctor --escape-test    # boots a VM that tries to break out; everything must be blocked
```

Details, two-device setup and Proxmox: [docs/08-installation.md](docs/08-installation.md).

## Connect your agent

On the controller, create a token and get the commands:

```bash
sudo crosscheck agent connect --name laptop
```

**Claude Code** (local): `claude mcp add --scope user --transport http crosscheck <url>/mcp --header "Authorization: Bearer $CROSSCHECK_TOKEN"`,
or install the plugin for a skill and `/crosscheck:*` commands:

```
/plugin marketplace add ProfessorEngineergit/Crosscheck
/plugin install crosscheck@crosscheck
```

**Claude Code in the cloud:** set `CROSSCHECK_URL` and `CROSSCHECK_TOKEN` in the environment,
allow the host in its network settings, and commit the plugin settings to your app repo
(`crosscheck agent setup claude-project`).

**Codex:** `crosscheck agent setup codex --url <url> --token <token>` writes
`[mcp_servers.crosscheck]` to `~/.codex/config.toml` and adds usage guidance to `AGENTS.md`.

Full guide: [docs/15-agent-setup.md](docs/15-agent-setup.md).

## On the PR

| You do | Crosscheck does |
|--------|-----------------|
| Nothing | Static checks on every push; VM runs according to your policy |
| Approve the PR | Runs it, if your policy waits for approval (approval counts only for that exact commit) |
| Label `crosscheck:run` / `crosscheck:matrix` | Runs on more presets / the full matrix |
| Comment `/crosscheck run --presets potato,insane` | Runs those presets |
| Comment `/crosscheck security-review` | Deep review by Claude Code (and Codex as second opinion, if configured) |
| Comment `/crosscheck hold --minutes 20` | Keeps the VM so your agent can inspect it live |
| Comment `/crosscheck stop` | Cancels and destroys everything |

Only users with write access can trigger runs by comment or label.

## Status

Working and tested: policy engine, GitHub integration (App or token, polling or webhooks),
plain-QEMU and Proxmox backends with per-run network namespaces and encrypted overlays, egress
proxy, hardware presets and throttling, Claude computer-use operator, scripted operator, report
builder and schema, MCP bridge with scoped tokens, key proxy, deletion receipts, installer, init
wizard, doctor, image builder, Claude Code plugin and Codex setup.

Not yet: Windows, Android, macOS and iOS images, the zero-touch ISO, cloud burst, the web admin
view, OpenAI and local-model operators. See [docs/07-roadmap.md](docs/07-roadmap.md).
The QEMU and Proxmox backends are unit-tested and the QEMU control path was exercised against a
real QEMU; a full run with a desktop image needs a KVM host and has not been part of CI.

## Documentation

| | |
|---|---|
| [01 Problem and goals](docs/01-problem-and-goals.md) | Who it's for, goals, non-goals |
| [02 Architecture](docs/02-architecture.md) | Controller, hostd, VMs, data flow |
| [03 Threat model](docs/03-threat-model.md) | Attackers, goals, mitigations |
| [04 Pipeline and stages](docs/04-pipeline-and-stages.md) | Triggers, static, smoke, deep, status logic |
| [05 Platform matrix](docs/05-platform-matrix.md) | Linux, Web, Windows, Android, macOS, iOS, images |
| [06 Chat bridge](docs/06-chat-bridge.md) | MCP tools, scopes, envelope |
| [07 Roadmap](docs/07-roadmap.md) | What is built, what is next, canary PRs |
| [08 Installation](docs/08-installation.md) | One-liner, wizard, two devices, Proxmox, versions |
| [09 Hardware profiles](docs/09-hardware-profiles.md) | Throttling, presets, modifiers, calibration |
| [10 Feature catalog](docs/10-feature-catalog.md) | Everything, with status |
| [11 Admin and trust policies](docs/11-admin-and-trust-policies.md) | Modes, classes, limits |
| [12 Isolation and deletion](docs/12-isolation-and-deletion.md) | The layers, deletion, escape tests |
| [13 Agents and benchmarks](docs/13-agents-and-benchmarks.md) | Operator, reviewers, host-side measurement |
| [14 Simulating hardware you don't have](docs/14-simulating-hardware-you-dont-have.md) | Traits, extrapolation, races, cloud |
| [15 Agent setup](docs/15-agent-setup.md) | Claude Code, cloud sessions, Codex |

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest -q && .venv/bin/ruff check src tests
```

## Licence

GPL-3.0, see [LICENSE](LICENSE).
