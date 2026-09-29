# 08 Installation

The goal is one command and a handful of questions. There are three setups; all end with the same
two services and the same agent commands.

| Setup | For | Security light with "every PR" |
|-------|-----|-------------------------------|
| **Single machine** | One dedicated box: an old PC, a mini PC, a Proxmox host | yellow: a VM escape would reach controller secrets on the same machine |
| **Two devices** | Controller on a small always-on device (Raspberry Pi 5, mini PC, small cloud VM), runner on a separate box | green: the runner host holds no secrets |
| **Demo** | Trying it out, no KVM needed | not for real PRs |

## Single machine

```bash
curl -fsSL https://raw.githubusercontent.com/ProfessorEngineergit/Crosscheck/main/install.sh | sudo bash
```

The installer is short on purpose; read it first. It:

1. installs `python3`, `git`, `curl`, `iproute2`, and QEMU (not on Proxmox, which has its own),
2. installs Crosscheck into `/opt/crosscheck` and links `/usr/local/bin/crosscheck`,
3. disables KSM (memory deduplication between VMs is a side channel),
4. starts `crosscheck init`.

Pin a version with `CROSSCHECK_REF=v0.1.0`. Non-interactive:

```bash
curl -fsSL .../install.sh | sudo bash -s -- --yes --repo me/app --github-token-file /root/gh.token \
  --anthropic-key-file /root/anthropic.key
```

### What `crosscheck init` asks

| Question | Default | Notes |
|----------|---------|-------|
| Role | single machine | or controller only / runner only |
| Repositories | – | `owner/name`, comma separated |
| GitHub | GitHub App | One click: the wizard serves a page (with QR code) that posts a prepared App manifest to GitHub. You see all permissions on GitHub before anything is created. Permissions: contents read, pull requests write, checks write, statuses write, metadata read. Or paste a fine-grained token |
| Anthropic API key | skip | Without it, smoke steps run scripted (no model) |
| Public HTTPS URL | skip | Needed for cloud agents, webhooks and deep reviews. Without it Crosscheck polls GitHub and only local agents can connect |
| Automatic runs | maintainers and trusted users automatically, others after approval | `all`, `approved`, `manual` also available. Isolation is identical in every mode |

It writes `/etc/crosscheck/controller.yaml`, `/etc/crosscheck/hostd.yaml` and secrets under
`/etc/crosscheck/secrets/` (mode 0600), creates the `crosscheck` user, installs and starts:

- `crosscheck-hostd.service`: root, no secrets, creates and destroys VMs, listens on
  `/run/crosscheck/hostd.sock` (group `crosscheck` only),
- `crosscheck-controller.service`: user `crosscheck`, holds the secrets, polls GitHub, runs agents,
  serves MCP on `127.0.0.1:8750`,

and prints the first MCP token with ready-to-paste agent commands.

### After init

```bash
sudo crosscheck images build linux        # 10 to 40 minutes; newest Ubuntu cloud image, verified checksum
sudo crosscheck images build analysis     # optional: gitleaks, semgrep, osv-scanner for stage 0
sudo crosscheck images build review       # optional: Claude Code and Codex CLI for deep reviews
sudo crosscheck doctor --calibrate        # checks and host speed for hardware presets
sudo crosscheck doctor --escape-test      # a test VM tries to reach the host, LAN, DNS, IPv6, metadata
```

### Reaching the controller from outside

Local agents on the same machine or LAN need nothing else. For cloud agents, webhooks and deep
reviews, expose **only** `/mcp`, `/webhook`, `/keyproxy` and `/healthz` over HTTPS, for example:

- **Tailscale Funnel:** `tailscale funnel --bg 8750`
- **Cloudflare Tunnel:** `cloudflared tunnel --url http://127.0.0.1:8750`

Put the resulting URL into `mcp.public_url` and `mcp.allowed_hosts` in `controller.yaml` and restart
the controller. Everything on these paths is authenticated (bearer tokens, webhook HMAC, one-time
key-proxy tokens).

## Two devices

On the runner host (dedicated, ideally in its own VLAN that the router forwards only to the internet):

```bash
curl -fsSL .../install.sh | sudo bash -s -- --role runner
sudo crosscheck images build linux
```

On the controller device:

```bash
curl -fsSL .../install.sh | sudo bash -s -- --role controller --runner-host runner.lan
```

Then give the controller access to the runner with a key that can do exactly one thing, start hostd:

```bash
# on the controller
sudo -u crosscheck ssh-keygen -t ed25519 -N '' -f /etc/crosscheck/secrets/runner_ed25519
sudo sh -c 'ssh-keyscan runner.lan > /etc/crosscheck/runner_known_hosts'

# on the runner: root may log in only with keys that carry a forced command
echo 'PermitRootLogin forced-commands-only' | sudo tee /etc/ssh/sshd_config.d/crosscheck.conf
sudo systemctl reload ssh
# append the controller's public key with the forced command:
echo 'command="/usr/local/bin/crosscheck hostd --stdio",restrict ssh-ed25519 AAAA... controller' \
  | sudo tee -a /root/.ssh/authorized_keys
```

The runner never connects to the controller. The controller treats everything the runner says as
potentially hostile.

## Proxmox VE

The installer detects Proxmox and uses the `proxmox` backend: templates become Proxmox template VMs
(`crosscheck images build linux` imports them), runs are linked clones, and every run gets its own
bridge without host IP. Set the storage in `hostd.yaml` (`proxmox.storage`, default `local-lvm`).
Proxmox runs QEMU as root without AppArmor; prefer the two-device setup for untrusted PRs.

## Demo

```bash
uvx --from git+https://github.com/ProfessorEngineergit/Crosscheck crosscheck demo
```

## Versions and channels

Images are always built from the newest upstream release of the channel (`--channel latest`, default,
or `lts`), with checksums verified. See [`versions.yaml`](../versions.yaml). Automatic nightly
rebuilds and a self-test before activation are on the roadmap.

## Uninstall

```bash
sudo crosscheck kill --revoke-tokens
sudo systemctl disable --now crosscheck-controller crosscheck-hostd
sudo rm -rf /opt/crosscheck /usr/local/bin/crosscheck /etc/crosscheck /var/lib/crosscheck /var/lib/crosscheck-hostd \
  /etc/systemd/system/crosscheck-*.service
```

## Planned: zero-touch runner ISO

A Proxmox VE installation image with the automated installer's answer file
([`install/answer.toml`](../install/answer.toml)) and a first-boot step that installs Crosscheck as a
runner and shows a pairing code. Boot it on a dedicated machine and walk away. Not built yet.
