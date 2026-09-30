# Examples

| File | Purpose |
|------|---------|
| [`crosscheck.yaml`](crosscheck.yaml) | Check plan in a repository (read from the base commit) |
| [`controller.yaml`](controller.yaml) | Controller configuration and admin policy, as written by `crosscheck init` |
| [`mcp.json`](mcp.json) | Project-level MCP configuration for Claude Code, reading `CROSSCHECK_URL` and `CROSSCHECK_TOKEN` from the environment |
| [`report.example.json`](report.example.json) | A real report from `crosscheck demo --scenario egress`, valid against [`schemas/report.schema.json`](../schemas/report.schema.json) |

Crosscheck needs **no GitHub Actions workflow** in the checked repository. It works as a GitHub App
or with a token, by polling or webhooks. A workflow using `pull_request_target` is exactly where
fork PRs could reach secrets, so Crosscheck avoids that design.
