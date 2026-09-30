# 09 Hardware profiles and throttling

A PR can run perfectly on your fast machine and be unusable on a two-core office PC with a
spinning disk and a 1366×768 screen. Crosscheck therefore simulates **hardware classes**, not
just operating systems. Each platform can run in several presets, and the host measures how the
app behaves in each.

This page covers making a machine *weaker*. Making it look *stronger* than it is has its own
page: [14 Simulating hardware you don't have](14-simulating-hardware-you-dont-have.md).

## Knobs

Everything is done with means QEMU, Proxmox and the Linux host already provide. Nothing needs
code inside the VM.

| Dimension | Mechanism | What it reveals |
|-----------|-----------|-----------------|
| **Cores** | `-smp` / Proxmox `cores` | Assumptions about parallelism, deadlocks on one core, blocked UI threads |
| **CPU speed** | cgroup `cpu.max` on the QEMU process / Proxmox `cpulimit` | Slow start, janky animations, timeouts in the code |
| **CPU generation** | CPU model (`x86-64-v2`, `x86-64-v3`, `host`) and flag masks (`-avx2`, `-avx512f`) | **"Illegal instruction" crashes** on older CPUs because a dependency was built with AVX2 |
| **Thermal throttling** | hostd changes the CPU limit during the run (e.g. full speed for 30 s, then 40 %) | Apps that become unusable after the first impression |
| **Memory** | `-m`, no balloon, small swap on a throttled disk | Memory hunger, OOM kills, swap stutter |
| **Memory pressure** | hostd inflates a balloon during the run | Behaviour when other programs use RAM |
| **Disk** | QEMU drive throttling (`bps`, `iops` read/write) | eMMC and HDD machines, synchronous file I/O on the UI thread |
| **Network** | Shaping in the egress proxy: latency, jitter, loss, bandwidth | Hanging UI without network, missing timeouts, retry storms |
| **Offline** | Egress proxy refuses everything from app start | Offline behaviour, error messages |
| **Graphics** | Software rendering in the guest (llvmpipe, WARP) by default | Blank windows without a GPU, Chromium/Electron GPU fallbacks |
| **Display** | Resolution and scale set in the guest: 1366×768 @100 %, 1920×1080 @100/125 %, 2560×1440 @125 %, 3840×2160 @150/200 % | Cut-off dialogs, blurry icons, broken HiDPI handling |
| **Multiple monitors** | Two virtual displays with different scales | Windows opening on the wrong screen, DPI changes |
| **Clock** | Guest clock offset, time zone, DST switch | Date bugs, certificate errors with a wrong clock |
| **Power** | Windows power plan, Android battery saver, Linux `power-saver` profile | Background work that dies when saving power |
| **Phones** | Android emulator: cores, memory, device profile. iOS Simulator: device type | Layout on small screens, memory on low-end Android |

Limit: the iOS Simulator uses the Mac's CPU. Throttling works only on the macOS VM as a whole.
iOS performance numbers are marked "not representative for real devices".

## Presets

Presets bundle these knobs under a name. They live in
[`presets/hardware.yaml`](../presets/hardware.yaml) and can be overridden per repository.

| Preset | Meant to resemble | Cores | CPU | RAM | Disk | Graphics | Display | Network |
|--------|-------------------|-------|-----|-----|------|----------|---------|---------|
| `potato` | Ten-year-old laptop, netbook | 2 | 300 points, `x86-64-v2` without AVX2 | 4 GB | HDD: 80 MB/s, 120 IOPS | software | 1366×768 @100 % | 3G |
| `office` | Typical office or library PC | 2 | 550 points, `x86-64-v2` | 8 GB | SATA SSD | software | 1920×1080 @100 % | busy Wi-Fi |
| `mainstream` | Current mid-range laptop | 4 | 1000 points, `x86-64-v3` | 16 GB | NVMe, throttled | software | 1920×1080 @125 % | DSL |
| `highend` | Current good desktop | 8 | 1400 points, `host` | 32 GB | unthrottled | software or GPU if allowed | 2560×1440 @125 % | fibre |
| `insane` | Top-end workstation | as many as possible | max, `host` | as much as possible | RAM disk for temp | GPU if allowed | 3840×2160 @150 % plus a second monitor | LAN |
| `phone-budget` | Entry-level Android phone | 4 | 350 points | 2 GB | throttled | software | 720×1600, 320 dpi | 3G |
| `phone-flagship` | Current flagship phone | 8 | max | 12 GB | unthrottled | host GPU if allowed | 1440×3120, 560 dpi | 5G |

"Points" are the score of the bundled calibration benchmark, 1000 being one mid-range desktop
core from 2024.

Why `insane` at all? Fast machines expose their own bugs: **race conditions** that only occur
when a background task finishes before the UI is ready, and **HiDPI bugs** on 4K with mixed
monitors. If your host cannot actually deliver `insane`, see
[14](14-simulating-hardware-you-dont-have.md).

GPU acceleration increases the attack surface of the host. For trust classes without GPU
permission (default: everyone except `maintainer`) Crosscheck replaces it with software
rendering and notes that in the report. See [12](12-isolation-and-deletion.md).

### Modifiers

Modifiers can be added to any preset, for example `mainstream+offline+dark`.

| Modifier | Effect |
|----------|--------|
| `offline` | No network from app start |
| `flaky-net` | Network drops for 5 s every 20 s |
| `thermal` | CPU throttled to 40 % after 30 s |
| `mem-pressure` | 50 % of free RAM taken by a balloon after start |
| `disk-full` | Only 200 MB free on the system disk |
| `battery-saver` | Power saving mode on |
| `dark` | Dark system theme |
| `high-contrast` | High contrast, 150 % font size |
| `rtl` | Arabic locale, right-to-left layout |
| `pseudo-l10n` | Pseudo-localisation: all strings 40 % longer with accents, reveals cut-off text |
| `wrong-clock` | Clock two years in the past |
| `dst` | Clock two minutes before a daylight-saving switch |

## Calibration: the same preset on different hosts

A 60 % CPU limit on a 2025 server is something else than on a 2019 mini PC. Presets are
therefore defined as **target performance**, not percentages.

1. On first start, `crosscheck doctor --calibrate` measures the host with a fixed benchmark
   (compression, JSON parsing, a small rendering workload).
2. hostd computes, per preset, which CPU limit and core count reach the target.
3. Each VM runs a five-second micro-benchmark right after boot. If the result deviates by more
   than 15 % from the target (for example because the host is busy), hostd adjusts or marks the
   measurement as unreliable.
4. If the host is too weak for a preset, the preset runs with what is there and the report says
   so plainly: "Preset `insane` not reachable, measured 62 % of target". Then the strategies in
   [14](14-simulating-hardware-you-dont-have.md) apply.

## What is measured per preset

| Metric | Source |
|--------|--------|
| Time to first window | Host: first large framebuffer change after launch |
| Time to interactive | Host: the operator clicks a defined element, the host measures the reaction |
| Input latency (p50, p95) | Host: input event until visible change, framebuffer polling |
| Hangs | Host: periods > 500 ms without reaction after input |
| CPU of the VM | Host: CPU time of the QEMU process |
| Memory of the VM | Host: RSS of the QEMU process |
| Disk I/O | Host: block statistics of the overlay |
| Network | Host: egress proxy log |
| Memory of the app process | Guest (hint only, forgeable) |

## Performance budget

```yaml
performance:
  budgets:
    potato:     { time_to_interactive_ms: 8000, input_latency_p95_ms: 400 }
    mainstream: { time_to_interactive_ms: 2500, input_latency_p95_ms: 120 }
  regression:
    max_slowdown_percent: 20   # compared with the base branch, same host, same preset
    fail_on_regression: false  # hint only
```

## Base versus PR

Single measurements fluctuate. On request the base branch runs in the same preset, interleaved
with the PR (A, B, A, B, A, B). Crosscheck then reports differences instead of absolute numbers:
"Start on `office` 1.8 s slower than `main` (± 0.3 s over 3 repetitions)". Base measurements are
cached per base SHA.

## Keeping the matrix small

- **Every push:** primary platform, preset `mainstream`.
- **Label `crosscheck:run`:** all platforms, presets `office` and `mainstream`.
- **Label `crosscheck:matrix`:** all platforms × all presets from the config.
- **Nightly on the base branch:** full matrix including modifiers.
- **Smart selection:** a diff that only touches Windows-specific code gets more Windows presets.
  A docs-only diff gets stage 0 only.
