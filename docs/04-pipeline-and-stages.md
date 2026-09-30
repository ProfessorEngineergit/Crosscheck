# 04 Pipeline and stages

## Triggers

| Event | Effect |
|-------|--------|
| New head SHA on an open PR (poll or `pull_request` webhook) | Stage 0 always. Stage 1 according to policy. A running run for the same PR is cancelled |
| Approving review on exactly this SHA | Stage 1 if the policy mode is `approved` |
| Label `crosscheck:run` | Stage 1 for this PR, also for forks and first-time contributors |
| Label `crosscheck:matrix` | Stage 1 with the full preset matrix |
| Label `crosscheck:deep` | Stage 2 after stage 1 |
| Comment `/crosscheck run [--platforms …]` | Stage 1, maintainers only |
| Comment `/crosscheck security-review` | Stage 2 focused on the diff, maintainers only |
| Comment `/crosscheck vulnerability-review` | Stage 2 focused on dependencies and runtime behaviour |
| Comment `/crosscheck hold [--minutes N]` | Keep the VM after the smoke run for live inspection (default 30, max 120) |
| Comment `/crosscheck stop` | Cancel, destroy VMs |
| MCP `crosscheck_request_run` | Same as the slash command, with token scope |
| Schedule (optional) | Nightly run on the base branch to catch template drift |

## Stage 0: static

Cheap and always on. Two parts:

**On the controller, metadata only.** No source file is opened here.
1. **Config change:** does the PR touch `crosscheck.yaml`? (from the PR files list)
2. **Injection patterns:** title, body and commit messages are checked for text that looks like
   instructions to a model.

**In an analysis VM without network.** Scanners parse attacker-controlled files, so they never
run on the controller.
3. **Secret scan:** gitleaks on the diff, and on the whole tree for first-time contributors.
4. **SAST:** Semgrep with the configured rule sets. Only findings touching the diff count for
   the status.
5. **Dependency audit:** lockfile diff, OSV scan against a mirrored database. New packages are
   listed with age and maintainer count (typosquatting hint).
6. **Install scripts:** which new dependencies run code on install (`postinstall`,
   `setup.py`, `build.rs`).

Result: check `crosscheck / static`.

## Stage 1: smoke

One disposable VM per platform and preset.

1. **Provision:** copy-on-write overlay of the template, job ISO, results disk, preset applied.
2. **Build:** the guest runner runs the build command from the base-branch config. Packages come
   through the egress proxy.
3. **Launch:** the guest runner starts the app. The host watches the framebuffer and records the
   time of the first large change (first window).
4. **Smoke scenario:** the operator agent works through the steps from the config. Each step is a
   plain sentence with an expectation, for example "Open the File menu and choose New.
   Expect: an empty document appears." Per step it reports `met`, `not_met` or `unclear`.
5. **Crash detection:** the window disappears, the screen freezes, known crash dialogs appear
   (Windows Error Reporting, "Application not responding", Android "has stopped").
6. **Collect:** screenshots, host timings, results archive (build log, app log, crash dumps),
   egress log. Redaction and size limits apply.
7. **Destroy:** VM and overlay are destroyed. A deletion receipt goes into the report.

Result: one check per platform. One PR comment with a table, edited on later runs, never re-posted.

## Stage 2: deep

Only on request. Uses model budget.

**security-review**: diff plus context, stage 0 findings and stage 1 observations (network
destinations, files written, child processes). The review agent runs inside a disposable review
VM. Findings come back as JSON following the finding schema.

**vulnerability-review**: lockfile diff, SBOM (syft), OSV hits and runtime observations. Are new
CVEs reachable? Are there signs of compromised packages?

**explorative** (optional): the operator agent tries edge-case inputs (very long strings, paths
with `..`, unusual Unicode) for a limited number of steps.

With `second_opinion` two different agents review independently. See
[13 Agents](13-agents-and-benchmarks.md).

## Status logic

| Condition | Check status |
|-----------|--------------|
| Secret found | failure |
| Canary credential touched | failure |
| Build failed on a required platform | failure |
| Crash during smoke (host-detected) | failure |
| `injection-attempt` or `config-change` | action_required |
| Required smoke step `not_met` | failure (optional steps: neutral) |
| Egress to a host outside the allow-list | failure |
| Host-measured performance budget exceeded | failure or neutral, as configured |
| Stage 2 finding high/critical with confidence ≥ 0.7 | failure |
| Budget exhausted | neutral with a note |
| Otherwise | success |

A failure blocks merging only if you mark the check as required. Crosscheck does not enforce it.

## Reproducibility

Every run records template build ID, config hash, base SHA, head SHA, agent adapters and prompt
version. `crosscheck_request_run` with `replay_of` repeats a run with the same parameters.
