# 10 Feature catalog

Status: **built** (in 0.1, tested), **partial** (built, but not all of what the row says), **planned**.

## Simplicity

| Feature | Status |
|---------|--------|
| One-command installer (`install.sh`) for Debian, Ubuntu, Fedora, Proxmox | built |
| Guided `crosscheck init` with non-interactive flags | built |
| GitHub App in one click via manifest flow, page with QR code | built |
| Polling mode: no inbound port needed | built |
| `crosscheck demo`: full run without GitHub, KVM or keys | built |
| Zero-config: build and launch detection for Electron, Tauri, Flutter, Rust, CMake, Node, Python | built (Linux guest) |
| `crosscheck doctor`, `--calibrate`, `--escape-test` | built |
| Zero-touch runner ISO | planned |
| Dashboard: runs, screenshots, findings, deletion receipts | built (read-only) |
| Admin actions in the dashboard | planned (CLI covers policy, tokens, status, kill switch) |
| Scenario recording from a hold session | planned |

## Platforms and images

| Feature | Status |
|---------|--------|
| Linux desktop image from the newest Ubuntu cloud image, checksum-verified | built |
| Analysis image (gitleaks, semgrep, osv-scanner) and review image (Claude Code, Codex CLI) | built |
| Channels `latest` and `lts` | built |
| Nightly rebuilds with self-test before activation | planned |
| Windows, Web (Playwright), Android, macOS, iOS | planned |
| Package format tests, upgrade and uninstall tests | planned |

## Hardware

| Feature | Status |
|---------|--------|
| Presets `potato` … `insane`, phone presets | built (phone presets need the Android runner) |
| Cores, CPU quota, CPU level and flag masks (AVX2 crash detection) | built |
| Disk bandwidth and IOPS throttling | built |
| Network profiles (latency, jitter, bandwidth, loss, offline, flaky) in the egress proxy | built |
| Thermal throttling during a run | built |
| Display size and scale | built (one display) |
| Calibration benchmark and per-host conversion | built |
| Trait simulation for presets the host can't reach | built |
| Throttle-sweep extrapolation, race amplification, cloud burst | planned |
| Multiple displays, memory pressure balloon | planned |

## Checks

| Feature | Status |
|---------|--------|
| Config-change and prompt-injection detection on PR metadata | built |
| Secret scan, SAST, dependency audit in an analysis VM | built (needs analysis image) |
| GUI smoke via Claude computer toolset | built |
| Scripted smoke steps without a model | built |
| Host-measured first window, settled screen, input latency, hangs, VM CPU and memory | built |
| Crash detection (host heuristic plus guest exit code) | built |
| Egress logging, blocked destinations, honeypot, canary credentials | built |
| Performance budgets per preset | built |
| Deep security and vulnerability review with Claude Code / Codex, second opinion | built (needs review image and public URL) |
| Visual regression against base, accessibility tree, localisation checks, fuzzing | planned |

## Results and chat

| Feature | Status |
|---------|--------|
| Check run (App) or commit status (token), one edited PR comment | built |
| MCP bridge with scoped tokens, untrusted envelopes, screenshots as images | built |
| Hold and live interaction through MCP | built |
| Webhook callbacks on completion, signed | built |
| Claude Code plugin (MCP, skill, commands), Codex setup | built |
| Push notifications (ntfy, e-mail), quiet hours | planned |
| Video of runs | planned |

## Isolation and deletion

| Feature | Status |
|---------|--------|
| KVM VMs only, minimal virtual hardware, no USB, no guest agent | built |
| Per-run network namespace (QEMU) or bridge without host IP (Proxmox), no IPv6, no DNS | built |
| Hardened transient systemd services, separate UIDs, seccomp QEMU | built (QEMU backend) |
| Egress proxy blocks private and local destinations even for allow-listed names | built |
| LUKS-encrypted overlays, key deleted once QEMU opened the disk | built (QEMU backend) |
| Per-boot dm-crypt storage for Proxmox overlays | planned |
| Janitor, verified deletion receipts, kill switch | built |
| Key proxy with one-time budgeted tokens for review VMs | built |
| Operator agent as a separate worker behind the key proxy | planned (runs in the controller today) |
| Firecracker for headless jobs, measured boot, disposable runner host | planned |
