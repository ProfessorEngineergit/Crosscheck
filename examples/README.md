# Beispiele

| Datei | Zweck |
|-------|-------|
| [`crosscheck.yaml`](crosscheck.yaml) | Prüfplan für ein Repo, fast alle Optionen am Beispiel einer Electron-App |
| [`mcp.json`](mcp.json) | MCP-Konfiguration für Claude Code. Als `.mcp.json` ins Projekt legen, Token als Umgebungsvariable `CROSSCHECK_TOKEN` setzen (in Claude Code Cloud als Environment-Secret) |
| [`report.example.json`](report.example.json) | Beispielbericht nach [`schemas/report.schema.json`](../schemas/report.schema.json) |

Crosscheck braucht **keinen GitHub-Actions-Workflow**. Es arbeitet als GitHub-App mit
Webhooks oder im Poll-Modus. Das ist Absicht: Ein Workflow mit `pull_request_target` wäre
genau die Stelle, an der Fork-PRs an Secrets kommen können.
