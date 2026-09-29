# 12 Isolation without escalation paths, and complete deletion

Crosscheck runs other people's code automatically. This page describes how that is contained.
It is the binding reference. Where other pages are less strict, this one wins.

## Guiding idea

Absolute security does not exist. Hypervisors have bugs too. Crosscheck is built so that three
things hold at the same time:

1. **Escaping is hard.** Several independent layers would have to fail at once.
2. **Escaping is not worth it.** Where PR code runs there are no keys, no data from other runs,
   and no path into your home or office network.
3. **Nothing survives.** Everything the PR touched is destroyed after the run. The runner host
   itself is rebuilt from scratch on a schedule.

## What is considered lost inside the guest

Crosscheck assumes that PR code has **root or SYSTEM rights inside the VM immediately.**
Privilege escalation *inside* the VM is not prevented, because it gains nothing. Consequences:

- Everything coming out of the VM is forged until proven otherwise: logs, status files,
  benchmarks, crash dumps, build artifacts, even "I am done".
- The guest runner is a convenience, not a security boundary.
- No value from inside the VM can make a run pass. The measurements that count are taken by the
  host from the outside (see [13](13-agents-and-benchmarks.md)).

## Topology

| Topology | Description | Safety light with smoke `all` |
|----------|-------------|-------------------------------|
| **Two devices (recommended)** | Controller on its own device, dedicated runner host in its own VLAN. An escape up to the runner host finds no keys | Green |
| **Single machine** | Controller and runner on the same host, controller running as a separate user. An escape from QEMU to root reaches the controller's secrets | Yellow |
| **Disposable runner host** | Two devices, and the runner host is rebuilt from the signed image on a schedule or after every untrusted run | Green+ |

The controller reaches the runner host only via SSH with a key that is restricted to a forced
command (`command="crosscheck hostd --stdio",restrict`). The runner host cannot open any
connection to the controller.

## The layers

### Layer 1: full VM, minimal hardware

- KVM VMs only, never containers for PR code. That includes web runs and the stage 0 scanners.
- Minimal virtual hardware: `virtio` disk and network, `std` VGA, `virtio-tablet` and
  `virtio-keyboard`. **No** USB controller, no sound, no serial console, no guest agent, no
  SPICE, no clipboard, no shared folders. The job CD is read-only.
- Every extra device class increases the code the guest talks to in the host's QEMU. These
  extras are therefore bound to trust classes:

| Extra | Risk | Allowed for |
|-------|------|-------------|
| Software rendering in the guest | none added | everyone |
| `virtio-gpu` with virgl/Venus | Guest graphics commands are processed on the host, a layer with a history of bugs | `maintainer` only, preferably as a separate sandboxed `vhost-user-gpu` process |
| GPU passthrough (VFIO) | Real hardware with DMA, firmware state can survive a reset | `maintainer` only, never on a host that runs untrusted PRs |
| Nested virtualisation (Android emulator) | Larger KVM attack surface in the host kernel | everyone, but for `known`, `stranger` and `bot` only on a runner host without the controller |
| USB passthrough | Direct hardware access | never |

### Layer 2: hardened QEMU process

- **`qemu` backend (plain Linux):** hostd starts every VM as a transient systemd service with
  its own numeric user ID (never root), `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, write access only to its own run directory, and hard
  `CPUQuota`, `MemoryMax` and `RuntimeMaxSec`. QEMU itself runs with
  `-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny`.
  The CPU quota can be changed while the VM runs, which is how thermal throttling is simulated.
- **`proxmox` backend:** Proxmox starts QEMU as root without an AppArmor profile. That is the
  main reason for the two-device topology. hostd still adds the seccomp sandbox through the VM's
  `args` and removes every unneeded device. `crosscheck doctor` checks and reports the result.
- Planned: headless tasks without a screen (stage 0, builds, CLI benchmarks) in Firecracker
  microVMs, whose device model is drastically smaller than QEMU's.

### Layer 3: host kernel and CPU

- Current microcode and kernel, all speculative-execution mitigations on (`mitigations=off` is
  refused by `crosscheck doctor`).
- **KSM off** on the runner host. Memory deduplication between VMs is a known side channel.
  Proxmox enables KSM by default, the installer disables it.
- Optionally **SMT off**, or only one untrusted run per host at a time.
- No unrelated services on the runner host.

### Layer 4: network

Your LAN is the real target: router, NAS, printers, phones, other people's laptops.

- VMs have **no route and no DNS**. Each run gets its own network namespace containing exactly
  one tap interface and nothing else: no uplink, no default route. The guest has a static
  address and no gateway. The only thing listening in that namespace is a tiny relay, running
  as its own unprivileged service, which forwards the proxy port to the per-run egress proxy
  outside through a Unix socket.
- Packages and allowed downloads go through the proxy as HTTP(S) proxy requests. The proxy
  resolves names itself, only for names on the allow-list, so DNS tunnelling is impossible.
- The proxy allows `CONNECT` only to allow-listed hosts on port 443, and plain `GET` only to
  allow-listed hosts. Everything else is refused and logged.
- On Proxmox, every run gets its own temporary bridge without a physical interface, so VMs never
  share a network segment. An nftables table drops everything arriving from these bridges except
  the proxy port, which also shields the Proxmox API and SSH on the host from the guest.
- The runner host itself sits in its own VLAN that the router forwards only to the internet,
  never to the LAN.
- No IPv6 in the runner network. VMs cannot see each other.
- `crosscheck doctor --escape-test` boots a test VM that tries to reach the controller, private
  ranges, the router, IPv6, public DNS and cloud metadata addresses. Every attempt must fail.

### Layer 5: one-way street to the controller

- All connections start at the controller.
- The controller treats answers from hostd as potentially hostile, because the runner host may
  lie after an escape: strict JSON schema, size limits, timeouts.
- Source never gets `git clone`d on a trusted machine. The controller fetches the tarball for the
  pinned SHA and hands it over **unopened**. Git hooks, malicious submodule URLs and Git parser
  bugs can only hit the disposable VM.
- Files coming back from a VM are never parsed by the controller process itself. The results
  archive is parsed by a separate, resource-limited worker process that extracts only
  whitelisted names with size limits and returns JSON. Crash dumps and SBOMs are processed in an
  evaluation VM.

### Layer 6: no keys within reach

- VMs get **no real credentials**.
- Agents that must work inside a VM (review agents like Claude Code or Codex CLI, see
  [13](13-agents-and-benchmarks.md)) get a **one-time token for the key proxy**. It is valid for
  this run and this model API only, has a hard budget, and is revoked when the VM is destroyed.
  The real API key is inserted by the key proxy on the controller.
- The operator agent runs on the controller, outside every VM. In 0.1 it runs inside the controller
  process with the controller's key; moving it into a separate worker behind the key proxy is on
  the roadmap.
- **Canary credentials** in every VM turn any credential theft attempt into a critical finding.

### Layer 7: resources

- Hard limits for CPU, memory, disk and run time per VM. A host-side watchdog destroys the VM
  on overrun, whatever the guest does.
- Disk space per overlay is capped. A full disk only affects that run.
- Parallel untrusted runs per the policy, default 1.

## Complete deletion

After every run everything the PR touched disappears. That also holds after a cancellation, a
controller crash or a power loss.

| Object | How | When |
|--------|-----|------|
| VM process | Hard stop (`quit` over QMP or `qm stop`), then destroy | Right after collection, at hold expiry or at timeout |
| Disk overlay | **Crypto-shredding**: each run writes to an encrypted overlay whose random key exists only in hostd's memory. Key gone means data unreadable. The overlay file is then deleted and its blocks discarded | On destroy |
| Overlay key | Dropped from memory | On destroy |
| VM memory | The host kernel zeroes pages before handing them to another process. With KSM off there are no shared pages | When QEMU exits |
| Job CD, results disk | Deleted | On destroy |
| Sockets | QMP socket and run directory deleted | On destroy |
| Egress proxy | Per-run listener closed, log moved into the report | On destroy |
| Key-proxy token | Revoked | On destroy, at the latest at maximum run time |
| Network shaping, firewall rules (Proxmox) | Removed | On destroy |
| Build artifacts | Removed from the store | After the retention period, default immediately |
| Screenshots, logs | Removed from the store | After the retention period |
| Build cache | Untrusted runs never write to it | – |

On the `qemu` backend the overlay is a qcow2 file with LUKS encryption. Its random key is written to
the run directory under `/run` (tmpfs, RAM), readable only by the VM's user, and deleted as soon
as QEMU has opened the disk; from then on it exists only in QEMU's memory. On Proxmox the overlays
are linked clones on the configured storage, destroyed with `qm destroy --purge`; a per-boot
dm-crypt storage for them is on the roadmap. LVM-thin and ZFS return zeros for blocks that were
never written in a new volume, so a later run cannot read an earlier run's data.

### Cleanup after failures

- Every resource carries the run ID and an expiry time.
- The **janitor** in hostd runs every minute and at boot. It destroys everything past its expiry,
  even if the controller is unreachable.
- After a power loss no runner VM starts automatically. At boot everything with a Crosscheck tag
  is destroyed.

### Deletion receipt

After cleanup the controller asks hostd to verify: no process, no overlay, no socket, no run
directory with this run ID. The result goes into the report as `deletion`, with a timestamp per
object. Leftovers become a `cleanup` finding and turn the safety light yellow.

### The host itself

- **Regular rebuild** of the runner host (never, weekly, nightly, after every untrusted run),
  made practical by the zero-touch installer. Templates are restored from the signed image cache.
- Planned: **measured boot** with a TPM, so the controller only accepts a runner host whose
  measured boot chain matches the expected image.
