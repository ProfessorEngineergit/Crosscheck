# 01 Problem and goals

## Who this is for

You maintain a cross-platform app with a graphical interface. A pull request comes in while
you are sitting in a lecture, working your day job, commuting, or simply busy with something
else. You want to stay present for your contributors and respond quickly, but you do not want
to hand over control: nothing gets merged until you have seen what it does.

Today, reviewing such a PR properly means being at your own machine: check out the branch,
build it, start it, click through it, read the diff. That breaks down in three ways:

- **You are not at your machine.** The PR waits, the contributor waits.
- **You do not own every platform.** A Linux laptop does not show Windows installer
  behaviour, a Mac without a Windows VM does not show Windows font rendering, and almost
  nobody has a spread of weak, average and top-end hardware on their desk.
- **You should not run PR code on your own machine anyway.** A PR from a fork can contain
  anything. A build script can read your credentials.

AI review tools read diffs well. They cannot tell you whether the app still starts on
Windows after the change, whether a dialog is cut off on a 1366×768 screen, or whether the
app suddenly phones home on startup.

## Goal

A self-hosted system, running on a dedicated box (Proxmox or any Linux with KVM) or a cloud
host, that:

1. runs a cheap static check on every PR,
2. builds and starts the PR in isolated, disposable VMs per platform,
3. drives the GUI through a defined smoke scenario with a computer-use agent,
4. simulates hardware classes from very weak to very strong, including hardware you do not own,
5. detects crashes, error dialogs, secret leaks, suspicious network and file access,
6. runs a deeper model-driven security and vulnerability review only when you ask for it,
7. produces a structured report that shows up as a GitHub check,
8. and delivers the same report to local and cloud chat sessions (Claude Code, Codex) over
   MCP, so you can ask "what happened on Windows?" from your phone without losing context.

## Non-goals

- **Not a CI replacement.** Unit tests, linting and release builds stay in your CI.
- **No auto-merge.** Crosscheck writes checks and comments. It never merges.
- **Not a full E2E test suite.** The smoke scenario is short on purpose.
- **No licence workarounds.** macOS and iOS run only on Apple hardware. Windows only with a
  valid licence or an evaluation image.

## MVP success criteria

- A PR on a Linux desktop project (Electron, Qt, GTK, Tauri, Flutter) produces a report with
  screenshots, host-measured timings and crash status within ten minutes.
- `curl … | sh`-style installation on a fresh Linux box with KVM, followed by one guided
  `crosscheck init`, is enough to get there.
- `crosscheck agent setup claude` or `crosscheck agent setup codex` connects a local or cloud
  agent session, and the agent can fetch the report and a screenshot through MCP.
- A deliberately malicious test PR (environment dump, network exfiltration, prompt injection in
  the PR description and on screen) gains no access to controller secrets and does not change
  the check plan.
