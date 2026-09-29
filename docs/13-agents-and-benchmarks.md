# 13 Agents: Claude, Codex, local models, and benchmarks

Crosscheck lets agents do what a person at the machine would otherwise do: operate the app, try
edge cases, run benchmarks and interpret them, read the diff for vulnerabilities. Providers are
interchangeable. The agent is never in charge.

## Principle: the controller is not an agent

Orchestration is ordinary, deterministic code. It decides from the policy which VM starts, which
agent gets which task, when to stop, and which status a run gets. Agents are workers with a
narrow toolbox. Even an agent fully taken over through prompt injection cannot:

- start, release or extend a VM,
- change a policy, a budget or a status,
- see any key,
- reach anything outside its one disposable VM.

## Three kinds of agent work

| Kind | Where the agent runs | What it sees | What it may do | Examples |
|------|----------------------|--------------|----------------|----------|
| **Operator** (computer use) | On the controller, **outside** the VM | Screenshots of the VM only | Mouse, keyboard, wait, report a step result | Smoke scenario, exploratory testing, keyboard-only pass |
| **Reviewer** (coding agent) | Inside its own disposable **review VM**, together with the source | Source, diff, stage 0 and runtime findings | Anything inside the VM, including running commands. Outside only through the key proxy | Security review, vulnerability review |
| **Interpreter** | Worker process on the controller | Only structured, schema-checked numbers and findings | No tools, one answer following a schema | Explain benchmark deltas, merge second opinions |

### Operator tools

The operator gets exactly these tools. The controller checks every call (coordinates inside the
screen, text length, step limit) and logs it:

```
screenshot()                    click(x, y, button)         double_click(x, y)
drag(x1, y1, x2, y2)            type(text)                  key(combo)
scroll(x, y, dx, dy)            wait(ms)                    report_step(index, result, observation)
```

There is no tool for a shell, files, network or the report. Text from `type` enters the VM as
keyboard events. There is no clipboard channel.

### Reviewers: coding agents in a review VM

Coding agents such as Claude Code or Codex CLI need a shell to read code, run tests and check
hypotheses. They get their own disposable VM:

1. The controller starts a review VM from the `review-linux` template. It contains Claude Code,
   Codex CLI, common toolchains and the scanners, and no key.
2. Base and head source arrive as tarballs, together with stage 0 and stage 1 findings as JSON.
3. The agent runs headless without prompts, which is acceptable because the VM is thrown away and
   cannot reach anything:
   - Claude Code: `claude -p "<fixed review task>" --output-format json`, with
     `ANTHROPIC_BASE_URL` pointing at the key proxy and the one-time token as its key.
   - Codex CLI: `codex exec "<fixed review task>"`, with the provider base URL pointing at the key
     proxy and the one-time token as its key.
4. The task text comes from the controller and the base-branch config. It demands a file
   `findings.json` following the finding schema in
   [`schemas/report.schema.json`](../schemas/report.schema.json).
5. The controller reads only that file, validates it, truncates free text, marks everything
   `x-untrusted`, and destroys the VM.

The key proxy lets the review VM reach only the model endpoints of the chosen provider. It counts
tokens and stops hard at the run's budget. The only data a hijacked reviewer could send out
through it is the PR's own source code, which the attacker already has.

## Adapters

| Adapter | Operator | Reviewer | Interpreter | Notes |
|---------|:--------:|:--------:|:-----------:|-------|
| `claude` (Anthropic API, computer-use tool) | ✓ | | ✓ | Default for smoke and exploratory testing |
| `claude-code` (headless in the review VM) | | ✓ | | Default for `security-review` |
| `openai` (computer use through the Responses API) | planned | | planned | Alternative or second opinion |
| `codex` (Codex CLI headless in the review VM) | | ✓ | | Alternative or second opinion |
| `local` (open vision model via an OpenAI-compatible endpoint such as Ollama or vLLM) | planned | planned | planned | No running cost, nothing leaves your network, weaker |
| `none` (scripted steps; Playwright for web is planned) | ✓ | | | Deterministic and free |

Assignment per task, in the admin policy, tightenable in `crosscheck.yaml`:

```yaml
agents:
  smoke:         { primary: claude, fallback: none }
  explorative:   { primary: claude }
  security:      { primary: claude-code, second_opinion: codex }
  vulnerability: { primary: codex }
  benchmark:     { primary: none, interpret: claude }
```

## Second opinion

With `second_opinion`, two independent agents check the same thing. Neither sees the other's
result. The controller then matches findings:

| Case | Result |
|------|--------|
| Both report the same thing (same file, overlapping lines, same category) | Confidence goes up, marked `confirmed` |
| Only one reports it | Kept, marked `single`, reduced confidence for the status logic |
| They directly contradict each other | Marked `disputed`, own section in the report |

A second opinion doubles the cost. It is only available for deep reviews and off by default.

## Benchmarks and measurements

Values from inside the VM can be forged. A malicious PR can write "start time 0.1 s" into a file.
Crosscheck separates by source, and every metric in the report carries its source in
`metric_sources`.

| Metric | Source | Forgeable by the PR? |
|--------|--------|----------------------|
| Time to first window | Host: first large framebuffer change after launch | no (only delayable) |
| Time to interactive | Host: operator clicks, host measures the change | no |
| Input latency, hangs | Host: input event to visible change, framebuffer polling | no |
| CPU of the VM | Host: CPU time of the VM's cgroup | no |
| Memory of the VM | Host: memory of the VM's cgroup | no |
| Disk I/O | Host: block statistics of the overlay | no |
| Network | Host: egress proxy log | no |
| Memory of the app process | Guest | **yes**, hint only |
| Project benchmarks (`npm run bench`, `cargo bench`, …) | Guest | **yes**, A/B hint only |
| Crash | Host (window gone, screen frozen) and guest (dump) | partly. The host finding counts |

Only host values can fail a run.

### Automated benchmark runs (planned beyond the smoke timings)

1. The controller runs base and PR in the same preset, interleaved (A, B, A, B, A, B), to cancel
   out heat and load drift on the host.
2. Before each round a micro-benchmark checks calibration. If the host is noisy, the round repeats.
3. The operator (or a scripted sequence without a model) runs the benchmark scenario from the
   config, for example "open the large sample file and scroll to the end".
4. Metrics come from the host. Project benchmarks run additionally inside the guest.
5. The interpreter gets only numbers as JSON, no raw guest text, and writes a short explanation.
   The explanation is a hint. Budgets decide the status.

## Keeping cost under control

- The key proxy counts every token per run, per repository and per month.
- Before every deep run Crosscheck estimates the cost. Above a threshold a `--confirm` is required.
- Screenshots are downscaled before sending and only sent again when the screen changed.
- Smoke and benchmarks default to a cheaper model or run locally. Only deep uses the strongest model.
- When the budget is used up, the run finishes as `neutral` with a note, never as a failure.
