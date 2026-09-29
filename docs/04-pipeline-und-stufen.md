# 04 Pipeline und Prüfstufen

## Auslöser

| Ereignis | Wirkung |
|----------|---------|
| `pull_request` opened / synchronize / reopened | Stufe 0 immer. Stufe 1 nach Policy. Laufender Lauf desselben PRs wird abgebrochen. |
| `pull_request` labeled `crosscheck:run` | Stufe 1 für diesen PR, auch bei Fork/Erstbeitrag |
| `pull_request` labeled `crosscheck:deep` | Stufe 2 (Security- und Vulnerability-Review) nach Stufe 1 |
| `issue_comment` `/crosscheck run [--platforms …]` | Stufe 1, nur Maintainer |
| `issue_comment` `/crosscheck security-review` | Stufe 2, Fokus Diff-Sicherheit, nur Maintainer |
| `issue_comment` `/crosscheck vulnerability-review` | Stufe 2, Fokus Abhängigkeiten und bekannte CVEs plus dynamische Beobachtung |
| `issue_comment` `/crosscheck hold [--minutes N]` | Nach dem Smoke bleibt die VM N Minuten stehen (Standard 30, Max 120) für interaktiven Zugriff |
| `issue_comment` `/crosscheck stop` | Bricht den laufenden Lauf ab, zerstört VMs |
| MCP `crosscheck_request_run` | Wie Slash-Kommando, mit Token-Scope |
| Zeitplan (optional) | Nächtlicher Lauf gegen den Basis-Branch, um Template-Drift zu erkennen |

## Stufe 0: Static

Läuft im Controller in einem unprivilegierten Container ohne Secrets. Dauer: 1 bis 5 Minuten.

1. **Config-Diff:** Wurde `crosscheck.yaml` im PR geändert? Finding `config-change`.
2. **Secret-Scan:** gitleaks über den Diff und, bei Erstbeitragenden, über den ganzen Baum.
   Zusätzlich TruffleHog mit Verifikation aus, weil Verifikation Netz braucht.
3. **SAST:** Semgrep mit den Regelsätzen des Projekts (`p/default`, `p/security-audit`,
   sprachspezifisch) und, wenn konfiguriert, CodeQL. Nur Findings, die den Diff berühren,
   zählen für den Status; der Rest geht als Kontext in den Bericht.
4. **Dependency-Audit:** Lockfile-Diff, `npm audit` / `pip-audit` / `cargo audit` /
   `osv-scanner` offline gegen einen gespiegelten Datenbestand. Neue Abhängigkeiten werden
   im Bericht aufgelistet, mit Alter des Pakets und Anzahl der Maintainer (Typosquatting-Hinweis).
5. **Injection-Muster:** PR-Text, Commit-Messages und Diff werden auf Muster geprüft, die
   Anweisungen an ein Modell darstellen. Finding `injection-attempt`.
6. **Build-Vorprüfung:** Prüft, ob die im Prüfplan genannten Build-Kommandos existieren
   (z. B. `package.json`-Scripts). Fehlt etwas, bricht Stufe 1 gar nicht erst an.

Ergebnis: Check-Run `crosscheck / static` mit Zusammenfassung und Annotations im Diff.

## Stufe 1: Smoke

Pro Plattform eine VM, parallel soweit der Host es erlaubt. Dauer: 5 bis 20 Minuten pro Plattform.

1. **Provisionierung:** Linked Clone vom Template, Einmal-ISO mit Auftrag (`job.json`,
   Quell-Tarball, Prüfplan) anhängen, starten, auf Guest-Agent warten.
2. **Build:** Der Build-Runner in der VM führt das Build-Kommando aus dem Prüfplan aus.
   Abhängigkeiten kommen über den Paket-Proxy. Ausgabe nach `build.log`. Bei Fehler:
   Finding `build-failed`, Lauf endet für diese Plattform.
3. **Start:** Launch-Kommando aus dem Prüfplan. Der Build-Runner wartet bis zu 60 s auf ein
   Fenster mit dem konfigurierten Titel-Muster und schreibt `status.json`
   (`{"state":"running","pid":…,"window":"…"}`).
4. **Beobachtung an:** Prozess-, Datei- und Netzwerk-Mitschnitt für die App-PID starten
   (Linux: auditd + nftables-Log; Windows: Sysmon + Firewall-Log; Android: `strace`-Ersatz
   über `adb` und Emulator-Netzwerklog).
5. **Smoke-Szenario:** `crosscheck-vision` arbeitet das Szenario aus dem Prüfplan ab. Ein
   Szenario ist eine Liste natürlicher Sätze mit Erwartung, z. B.:
   - "Öffne das Menü Datei und wähle Neu. Erwartung: ein leeres Dokument erscheint."
   - "Tippe 'Hallo' ins Hauptfeld. Erwartung: der Text ist sichtbar."
   - "Öffne Einstellungen. Erwartung: ein Dialog mit mindestens einem Tab."
   Das Modell bekommt pro Schritt den Satz, den aktuellen Screenshot und die Werkzeug-Liste.
   Es meldet nach jedem Schritt `met`, `not_met` oder `unclear` mit kurzer Begründung.
6. **Crash-Erkennung:** Prozess weg, Fenster weg, bekannte Crash-Dialoge (Windows WER,
   GNOME "Programm reagiert nicht", Android "hat angehalten"), Stacktraces in Logs.
7. **Einsammeln:** Screenshots, Video (aus den Screenshots), `build.log`, App-Logs, Audit-
   Mitschnitte, `status.json`. Redaktionsfilter. Größenlimits.
8. **Aufräumen:** VM zerstören oder in `hold` versetzen.

Ergebnis: Check-Run `crosscheck / smoke (<platform>)` pro Plattform. Ein PR-Kommentar mit
einer Tabelle (Plattform, Status, Dauer, drei wichtigste Findings, Link zum Bericht) wird
einmal erzeugt und bei Folgeläufen bearbeitet, nicht neu gepostet.

## Stufe 2: Deep

Nur auf Anforderung. Dauer: 5 bis 30 Minuten zusätzlich. Verbraucht Modell-Budget.

Zwei Modi, kombinierbar:

**security-review**
- Eingabe: Diff, geänderte Dateien im Kontext (n Zeilen um jede Änderung), Stufe-0-Findings,
  Beobachtungen aus Stufe 1 (Netzwerkziele, Dateischreibpfade, gestartete Kindprozesse).
- Fragen an das Modell (fester Prompt): Neue Angriffsfläche? Eingabevalidierung?
  Deserialisierung? Pfad-Traversal? Command-Injection? Auth/Authz-Änderungen? Krypto?
  Verändertes Verhalten bei Netzwerk und Dateisystem, das der Diff nicht erklärt?
- Ausgabe: Findings nach Schema mit `severity`, `confidence`, `file`, `line`, `evidence`.

**vulnerability-review**
- Eingabe: Lockfile-Diff, SBOM des Builds (syft), OSV-Treffer, Beobachtungen aus Stufe 1.
- Fragen: Welche neuen CVEs kommen herein? Sind sie im Kontext erreichbar (wird die
  verwundbare Funktion aufgerufen)? Gibt es Anzeichen für kompromittierte Pakete
  (Install-Scripts, ungewöhnliche Netzwerkziele beim Build)?
- Optional: exploratives GUI-Testing, bei dem `crosscheck-vision` frei versuchen darf,
  Eingabefelder mit Grenzfällen zu füttern (sehr lange Strings, Pfade mit `..`, Unicode),
  begrenzt auf N Schritte.

Der Controller merged beide Ausgaben in den Bericht. Findings mit `severity: high` oder
`critical` und `confidence >= 0.7` setzen den Check auf `failure`, alles andere auf
`neutral` mit Annotations.

## Status-Logik

| Bedingung | Check-Status |
|-----------|--------------|
| Secret gefunden (verifiziert oder hohe Entropie in bekanntem Format) | failure |
| Build fehlgeschlagen auf einer Pflicht-Plattform | failure |
| Crash im Smoke | failure |
| `injection-attempt` oder `config-change` | action_required |
| Smoke-Schritt `not_met` | failure, wenn der Schritt im Prüfplan `required: true` ist, sonst neutral |
| Unerwarteter Egress | failure, wenn Ziel nicht in Allow-List; neutral, wenn Allow-List leer und Repo `egress: observe` gesetzt hat |
| Stufe-2-Finding high/critical mit Konfidenz ≥ 0,7 | failure |
| Budget erschöpft | neutral mit Hinweis |
| Alles andere | success |

Ein `failure` blockiert das Mergen nur, wenn das Repo den Check als Required Check
eingetragen hat. Crosscheck erzwingt das nicht.

## Wiederholbarkeit

Jeder Lauf speichert: Template-Version (Packer-Build-ID), Prüfplan-Hash, Basis-SHA,
Head-SHA, Modell-ID und Prompt-Version. Ein Lauf lässt sich mit denselben Parametern
wiederholen (`crosscheck_request_run` mit `replay_of`).
