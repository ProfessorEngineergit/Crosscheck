# 14 Simulating hardware you don't have

Throttling makes a strong machine behave like a weak one. The reverse is not possible:
**no amount of configuration makes a CPU compute faster than it can.** If your runner host is a
four-core mini PC, a VM on it will never run like a 32-core workstation.

That does not mean you cannot test for high-end hardware. What you actually want to know about
a fast machine falls into four questions, and each has its own answer.

| Question | Strategy | Cost | Honesty label in the report |
|----------|----------|------|-----------------------------|
| Does the app behave correctly on a machine that *looks* high-end? | **Trait simulation** | free | `simulated-traits` |
| Would it be fast enough there? | **Throttle sweep and extrapolation** | free | `extrapolated` |
| Do bugs appear that only fast machines trigger? | **Race amplification** | free | `amplified` |
| What are the real numbers? | **Cloud burst** on rented hardware | pay per run | `measured` |

Status in 0.1: trait simulation is built; extrapolation, race amplification and cloud burst are
planned. Crosscheck picks the strategy automatically when a preset is not reachable on the local host
(see calibration in [09](09-hardware-profiles.md)), and the report always says which one was used.

## 1. Trait simulation: look like a high-end machine

Many high-end bugs are not about speed at all. They are about what the app *sees*. Those traits
can be simulated on any host:

| Trait | How | Bugs it finds |
|-------|-----|---------------|
| **Many cores** | Give the VM more vCPUs than the host has (e.g. 32 vCPUs on 4 physical cores). The guest sees 32 cores | Thread pools sized by core count, per-core memory allocation, work-splitting bugs, `navigator.hardwareConcurrency` assumptions |
| **Lots of RAM** | Assign a large memory size that is not backed until touched (no pre-allocation). The guest sees 64 GB. The QEMU process runs in a cgroup with a hard memory cap the host can afford, so if the app really uses the memory the run ends with a clear "exceeded host memory" result instead of hurting the host. Works well for Linux guests. Windows zeroes free memory in the background and therefore gets a smaller oversize | Cache sizes computed from total RAM, 32-bit overflow in memory math |
| **Huge displays** | Virtual displays do not need real hardware: 3840×2160, 5120×2880, two or three monitors, mixed scale 100/150/200 % | Layout, HiDPI assets, window placement on multi-monitor setups |
| **Instant storage** | Put the overlay and temp directories on a RAM disk on the host | Races where a file operation completes before the UI expects it |
| **Instant network** | The egress proxy serves allowed resources from cache with zero latency, or a local mock answers immediately | Races where a response arrives before the UI is ready to handle it |
| **Newest CPU features** | Pass through the host CPU model (`host`). If the host lacks a feature (e.g. AVX-512), QEMU cannot provide it at usable speed. The report states which features were missing | Code paths that are only taken with newer instruction sets |

The key insight behind the last three rows: bugs that "only happen on fast machines" are usually
about **relative timing** between parts of the program. A fast CPU is only one way to change
that ratio. Making I/O instant changes it just as well, and costs nothing.

## 2. Throttle sweep and extrapolation: estimate the numbers

To estimate how fast the app would be on a stronger machine, Crosscheck measures it at several
speeds the host *can* deliver and fits a model to the results.

1. Run the smoke scenario at, for example, 25 %, 50 %, 75 % and 100 % of the host's CPU.
2. For each run, measure time to interactive, input latency and hangs on the host.
3. Fit how each metric scales with CPU speed and core count. The fit also classifies the app:
   **CPU-bound** (scales with speed), **parallel** (scales with cores), **I/O-bound** (does not
   scale with CPU), or **fixed** (dominated by timeouts or sleeps).
4. Extrapolate to the target preset's performance score, with a confidence interval.

Example report line:

```
insane (extrapolated): time to interactive ≈ 0.9 s (0.7–1.2 s, 95 % interval)
  basis: 4 host measurements from 25 % to 100 %, app classified as CPU-bound (R² 0.97)
```

Extrapolation is marked as such, is never used to fail a run, and is withheld when the fit is
poor (for example when the app is I/O-bound, since a faster CPU would not help anyway, which is
itself a useful finding).

## 3. Race amplification: provoke the bugs fast machines trigger

Some bugs appear only when things happen in an unusual order. Instead of waiting for a fast
machine to produce that order by chance, Crosscheck makes unusual orders likely:

- **Chaos scheduling (Linux guests):** the app runs under `rr record --chaos`, which randomises
  thread scheduling. The recording can be replayed deterministically when something breaks.
  rr needs hardware performance counters inside the VM (KVM with a `host` CPU model) and slows
  the app down, so it runs as a separate pass, not during timing measurements.
- **Timing jitter:** hostd varies the VM's CPU limit in short random bursts, so thread
  interleavings change from run to run.
- **Instant I/O plus slow CPU:** the inverse ratio of a fast machine. Disk and network answer
  immediately while the CPU is throttled. This is the cheapest way to reproduce "the data arrived
  before the window was ready".
- **Repetition:** the scenario runs several times with different seeds. A step that fails only
  sometimes is reported as `flaky` with the failing seed, which is a strong race hint.

## 4. Cloud burst: rent the real thing for a few minutes

When you need real numbers, Crosscheck can start a strong machine in the cloud for a single run
and delete it afterwards.

**How it works**

1. The controller holds the cloud API credentials. The runner never sees them.
2. For a run that needs a preset the local host cannot reach, the controller starts a fresh
   instance from a provider image with nested virtualisation or bare metal, using a
   cloud-init that installs `crosscheck hostd` and fetches the templates from your image cache.
3. The controller connects to it over SSH exactly like to a local runner host. The instance has
   no route to your network and a security group that only allows the controller's SSH
   connection in and the egress proxy's allow-list out.
4. After the run, the controller **terminates** the instance. Terminated instances and their
   disks are destroyed by the provider. The deletion receipt records the instance ID and the
   termination time.

**Where this is possible**

| Option | Notes |
|--------|-------|
| Providers with nested virtualisation on regular VMs | The cloud VM becomes the runner host, the PR runs in a VM inside it. Two isolation layers |
| Bare-metal instances billed per second or hour | Full KVM performance, typically the most expensive |
| GPU instances | For real GPU presets. The cloud instance itself is the disposable sandbox, without nesting, because GPU passthrough into a nested VM is rarely available |
| A friend's or team's spare strong machine | Same zero-touch runner image, paired with your controller. It must be a dedicated box, never someone's daily machine |

**Cost control**

- A monthly cloud budget in the admin policy, separate from the model budget.
- Cloud burst is **off by default** and, when enabled, available only for trust classes you
  choose (default: `maintainer` and `trusted`).
- The estimate is shown before the run. Above a threshold a maintainer has to confirm with
  `/crosscheck run --presets insane --confirm`.
- Instances have a hard maximum lifetime set at creation, so a crashed controller cannot leave
  an expensive machine running.

## What the report shows

Every platform result carries `performance_basis`:

| Value | Meaning |
|-------|---------|
| `measured` | The preset was reached on real hardware, locally or in the cloud |
| `simulated-traits` | The app saw high-end traits. Speed is the host's |
| `extrapolated` | Numbers are a model estimate from a throttle sweep |
| `amplified` | Race amplification was used |
| `not-reachable` | None of the above was enabled. The preset ran with what was available |

Only `measured` values can fail a run on performance budgets. Everything else is a hint.
