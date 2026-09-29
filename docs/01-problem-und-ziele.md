# 01 Problem und Ziele

## Ausgangslage

Wer ein Cross-Platform-Projekt mit grafischer Oberfläche pflegt, prüft PRs heute typischerweise
lokal: Branch auschecken, bauen, starten, durchklicken, Diff lesen. Das setzt voraus, dass der
eigene Rechner da ist, alle Zielplattformen darauf laufen und man Zeit hat. Drei Dinge brechen
das regelmäßig:

- **Man ist unterwegs.** In der Schule, im Zug, am Handy. Der PR wartet, der Autor wartet.
- **Man hat nicht alle Plattformen.** Ein Linux-Laptop testet kein Windows-Installer-Verhalten
  und kein iOS. Ein Mac ohne Windows-VM testet keine Windows-Font-Fehler.
- **Man will PR-Code nicht auf dem eigenen Rechner ausführen.** Ein Fork-PR kann beliebigen
  Code enthalten. Ein Build-Skript kann Zugangsdaten auslesen.

Gleichzeitig gibt es KI-gestützte Review-Werkzeuge, die sehr gut Diffs lesen, aber nicht
sehen, ob die App nach dem Diff auf Windows noch startet, ob der Dialog auf Android abgeschnitten
wird oder ob die App beim Start plötzlich nach Hause telefoniert.

## Ziel

Ein selbst gehostetes System, das auf einer Proxmox-Maschine (oder einem Cloud-Host) läuft und:

1. bei jedem PR eine günstige statische Prüfung ausführt,
2. den PR-Stand in abgeschirmten Wegwerf-VMs pro Plattform baut und startet,
3. die GUI per Computer-Use-Agent durch ein festgelegtes Smoke-Szenario führt,
4. Crashes, Fehlermeldungen, Secret-Leaks, verdächtige Netzwerk- und Dateizugriffe erkennt,
5. auf Anforderung eine modellgestützte Sicherheits- und Vulnerability-Prüfung fährt,
6. einen strukturierten Bericht erzeugt, der in GitHub als Check erscheint,
7. und denselben Bericht per MCP an lokale und Cloud-Chat-Sessions liefert, inklusive der
   Möglichkeit, die noch laufende VM interaktiv zu übernehmen.

## Nicht-Ziele

- **Kein Ersatz für CI.** Unit-Tests, Linting und Release-Builds bleiben in der bestehenden
  CI. Crosscheck ergänzt sie um GUI-Smoke, Sandbox-Beobachtung und Tiefenprüfung.
- **Keine Auto-Merges.** Crosscheck schreibt Checks und Kommentare, mergt aber nie.
- **Keine vollständige E2E-Testsuite.** Das Smoke-Szenario ist bewusst kurz. Wer ausführliche
  GUI-Tests will, hängt sie als eigenen Schritt ein.
- **Keine Umgehung von Lizenzbedingungen.** macOS und iOS werden nur auf Apple-Hardware
  virtualisiert. Windows nur mit gültiger Lizenz oder Evaluation-Image.

## Erfolgskriterien für ein MVP

- Ein PR auf einem Linux-Desktop-Projekt (z. B. Electron, Qt, GTK, Tauri) löst innerhalb von
  zehn Minuten einen Bericht mit Screenshots und Crash-Status aus.
- Eine Claude-Code-Session (lokal oder Cloud) kann per `crosscheck_get_run` den Bericht
  abrufen und per `crosscheck_get_artifact` einen Screenshot ansehen.
- Ein bewusst bösartiger Test-PR (Env-Dump, Netzwerk-Exfiltration, Prompt-Injection in der
  PR-Beschreibung) führt zu keinem Zugriff auf Controller-Secrets und zu keiner Veränderung
  des Prüfplans.
