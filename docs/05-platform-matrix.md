# 05 Platform matrix and VM images

Golden images are fully prepared operating system images. Every run starts a copy-on-write
overlay of one and throws it away afterwards. Images are built reproducibly with
`crosscheck images build <platform>`. Nothing is changed by hand inside an image.

## Overview

| Platform | Host | Virtualisation | Image source | Licence | Automation | Effort |
|----------|------|----------------|--------------|---------|------------|--------|
| Linux desktop (Ubuntu, Fedora, Debian) | Any KVM host | KVM | Official cloud images + desktop packages | Free | Xfce or GNOME on X11 by default, Wayland optional. QMP screenshots and input | Low |
| Web (Chromium, Firefox, WebKit) | Any KVM host | KVM (never a container) | Linux image + Playwright browsers | Free | Playwright, no model needed | Low |
| Windows 11 | Any KVM host | KVM + VirtIO drivers | Evaluation ISO or your own licence | Evaluation or purchased | `autounattend.xml`, Sysmon, virtio-input | Medium |
| Android | KVM host with nested virtualisation | Android emulator inside a Linux VM | AOSP system images via the SDK (mirrored) | Free | `adb`, emulator console | Medium |
| macOS | Apple hardware (e.g. Mac mini) | Tart (Apple Virtualization) | IPSW from Apple | Apple EULA: Apple hardware only, max. 2 VMs per host | Screen recording permission, VNC | High |
| iOS | Apple hardware, inside the macOS VM | Xcode iOS Simulator | Xcode | As macOS | `xcrun simctl` (install, launch, screenshot, input) | High |

### iOS, honestly

There is no free and legal way to run iOS apps without Apple hardware. Projects claiming
"iOS emulation" either do not run real iOS code or violate the licence. The workable path is a
Mac mini (Apple silicon, second-hand is fine) as an additional runner host. It runs a macOS VM
with Tart, and inside it Xcode with the Simulator. For Flutter, React Native, .NET MAUI, Qt and
similar frameworks that is enough for layout, navigation and crash checks. Push notifications,
camera and real signing stay out of scope.

Without your own Mac you can use a cloud Mac provider. The PR code then leaves your network.
For public repositories that is acceptable. For private ones it is a conscious decision.

## Image pipeline

`crosscheck images build linux` does the following, on the runner host:

1. Downloads the official cloud image of the configured release and verifies it against the
   published checksums.
2. Creates a build overlay and a cloud-init seed. Building is the **only** time an image has
   network access, and it never sees PR code.
3. cloud-init installs the desktop, auto-login, the guest runner, canary credentials, the
   toolchains of the selected profiles, and hardening (no IPv6, no SSH server, no guest agent).
4. The VM powers itself off. The overlay is flattened into a template and tagged with a build ID.
5. A self-test boots the template once with a reference app. Only then does it become active.

Channels decide which versions are used, see [`versions.yaml`](../versions.yaml):

| Channel | Meaning | Default |
|---------|---------|---------|
| `latest` | Newest stable upstream versions, rebuilt nightly, activated after the self-test | Yes |
| `lts` | Long-term-support versions only, rebuilt monthly for security updates | |
| `pinned` | Exact versions from `versions.lock`, for reproducible comparisons | |
| `preview` | Betas (next Android, macOS, Windows Insider), optional extra platform | |

The previous template stays for two weeks as a fallback and for replays.

## What every image contains

- **Auto-login** into a graphical session. Resolution and scaling are set by the guest runner
  from the preset. Screen saver, power saving, notifications and update services are off.
- **Guest runner** (`crosscheck-guest`): reads the job from the read-only CD, unpacks the
  source, runs the build, starts the app, and at the end writes one archive to the results disk.
  It has no network function of its own and knows nothing about the controller.
- **Egress settings:** `http_proxy` and `https_proxy` point to the per-run egress proxy.
  There is no DNS resolver and no other route.
- **Audit:** process starts, file writes outside the work directory, and connection attempts,
  written to the results archive (guest-reported, therefore a hint only).
- **Canary credentials** with a unique marker.
- **Toolchains** per profile (`electron`, `tauri`, `flutter`, `qt`, `gtk`, `dotnet`, …).
- **No state:** no user data, no browser profile, no saved passwords.

## Virtual hardware

- Machine `q35`, CPU model from the preset, `virtio` disk and network, `std` VGA.
- Input through `virtio-tablet-pci` and `virtio-keyboard-pci`. **No USB controller.**
- No sound, no serial console, no guest agent, no shared folders, no clipboard.
- The job CD is read-only. The results disk is raw and small.

## Sizes

Base sizes of the templates. The preset overrides them per run
(see [09](09-hardware-profiles.md)): Linux 4 vCPU / 8 GB, Windows 4 vCPU / 8 GB,
Android host 6 vCPU / 12 GB, Web 2 vCPU / 4 GB. A host with 16 cores and 64 GB runs two
platforms in parallel comfortably.
