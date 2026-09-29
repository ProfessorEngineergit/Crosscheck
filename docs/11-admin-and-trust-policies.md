# 11 Admin and trust policies

## Principle

**Isolation is equally strong for every PR, no matter who opened it.** Crosscheck treats the
repository owner's PR as hostile code too. A hijacked account, a compromised package or a
Dependabot PR with a poisoned update is as dangerous as a stranger.

The trust policy therefore controls only three things:

1. **Whether and when** a run starts automatically.
2. **How much** it may use: platforms, presets, run time, parallelism, model and cloud budget.
3. **Which extras** that increase attack surface are allowed: GPU acceleration, nested
   virtualisation, observe-only egress, cloud burst. See [12](12-isolation-and-deletion.md).

The baseline isolation cannot be switched off. There is no "trusted, run without a VM" setting.

## Trust classes

The class is determined when the run starts, live through the GitHub API: the author's
permission on the repository, and reviews on the exact head SHA. The webhook payload alone is
not enough.

| Class | Who | Typical policy |
|-------|-----|----------------|
| `maintainer` | Write access (owner, collaborator, team with write) | Everything automatic except deep |
| `trusted` | Users or teams on the admin allow-list | Smoke automatic |
| `known` | At least one merged PR in the repository | Static automatic, smoke per policy |
| `stranger` | Everyone else, including first-time contributors and forks | Static automatic, smoke per policy |
| `bot` | Dependabot, Renovate, other apps | Like `stranger`, with its own quota. Dependency updates are a classic supply-chain path |
| `blocked` | Users on the deny list | Static only |

## Policy modes

Set per repository and per stage.

| Mode | Meaning |
|------|---------|
| `all` | Every PR runs automatically, including forks and first-time contributors |
| `approved` | Runs only after a maintainer approved **exactly this head SHA**. Every new push needs a new approval |
| `classes` | Automatic only for the listed classes. Everyone else via label, slash command or MCP |
| `manual` | Only via label, slash command or MCP |
| `off` | Stage disabled for this repository |

Defaults after installation:

| Stage | Default | Why |
|-------|---------|-----|
| static | `all` | Runs in an analysis VM, cheap, executes no PR code |
| smoke | `classes: [maintainer, trusted]`, others `approved` | `all` would be safe too, this keeps queue and compute under control |
| deep | `manual` | Costs model budget |

Choosing `all` for smoke on a single-machine setup shows a yellow safety light (see below).

### No time-of-check gaps on approval

An attacker could wait for a maintainer to approve commit A and quickly push commit B.

- Crosscheck always works on a **pinned SHA**, never a branch name. Source is fetched through the
  tarball API for that SHA.
- The approval must reference that SHA (`commit_id`).
- Right before the VM starts, Crosscheck checks again: is the SHA still the approved one, does the
  approver still have write access, was the approval dismissed?

## Limits per class

Defaults, all editable:

| Limit | maintainer | trusted | known | stranger / bot |
|-------|-----------:|--------:|------:|---------------:|
| Platforms per run | all | all | 2 | 1 |
| Presets per run | all | 3 | 2 | 1 |
| Max run time | 60 min | 45 min | 30 min | 20 min |
| Parallel runs | 2 | 1 | 1 | 1 (global queue) |
| Runs per day and author | ∞ | 30 | 10 | 5 (bots: 20) |
| Hold allowed | yes | yes | no | no |
| Deep review | on command | on command | on maintainer command | on maintainer command |
| GPU acceleration | optional | no | no | no |
| Android (nested virtualisation) | yes | yes | dedicated runner host only | dedicated runner host only |
| Egress `observe` mode | optional | no | no | no |
| Cloud burst | optional | optional | no | no |
| Build cache | read, write only on base-branch runs | read | read | read |

## Repository file and admin policy

- The **admin policy is the upper bound.** It lives on the controller and cannot be changed
  through Git.
- `crosscheck.yaml` in a repository can only **tighten** it. It can switch smoke off for bots,
  but it can never set `stranger` to `all` when the admin chose `approved`. Conflicts are
  reported and the stricter value wins.
- Example: [`examples/controller.yaml`](../examples/controller.yaml).
- Command line: `crosscheck policy show`, `crosscheck policy explain <repo> <pr>` ("what would
  happen for this PR?"), `crosscheck policy set <repo> smoke all`.

## Admin view

The admin view is served by the controller on the local network or Tailscale only, **never
through the public tunnel**. Only the webhook endpoint and the MCP endpoint are public, and both
require authentication.

| Page | Content |
|------|---------|
| **Overview** | Running runs, queue, safety light, budget, latest findings |
| **Policies** | Mode per repository and stage, limits per class, "explain PR #N" preview |
| **Users and teams** | Allow list, deny list, who falls into which class |
| **Topology** | Controller, runner hosts, networks, hardening status per host from `crosscheck doctor` |
| **Agents** | Which agent does which task, API keys (write-only, never displayed again), budgets |
| **Retention and deletion** | How long reports, screenshots and logs stay, deletion receipts |
| **Audit log** | Every change, every manual run, every hold, every login |
| **Kill switch** | One button: pause polling, cancel all runs, destroy all runner VMs, revoke all MCP tokens |

The same actions are available on the command line (`crosscheck policy`, `crosscheck token`,
`crosscheck kill`), which is what the MVP ships first.

### Safety light

| Colour | Example |
|--------|---------|
| Green | Smoke `all`, controller on its own device, runner host in its own VLAN, re-imaged regularly, KSM off |
| Yellow | Smoke `all` on a single machine that also holds the controller secrets: a VM escape would reach them |
| Red | The runner network can reach the LAN, or `crosscheck doctor` failed an escape test. Smoke for `stranger` and `bot` pauses automatically |

### Security of the admin view itself

The admin view displays content from hostile PRs: titles, file names, observations, screenshots.

- Every output is escaped for its context. A strict Content-Security-Policy forbids inline scripts.
- Screenshots come from a separate origin without cookies. SVG is never served, only PNG, JPEG
  and WebM re-encoded by the host.
- Links from PR content are not clickable.
- Changing actions need CSRF protection and passkey confirmation.
