# Crosscheck

**Jeden Pull Request auf allen Plattformen und jeder Hardware-Klasse prüfen, abgeschirmt auf
eigener Hardware, abrufbar aus jeder Chat-Session.**

Crosscheck ist ein selbst gehostetes Prüfsystem für Pull Requests von Cross-Platform-Apps mit
grafischer Oberfläche. Es baut den PR-Stand in Wegwerf-VMs auf Proxmox (oder jedem Linux mit
KVM), bedient die Oberfläche per Computer-Use-Agent, simuliert schwache bis extrem starke
Hardware, beobachtet Netz- und Dateizugriffe und liefert einen strukturierten Bericht.
Den Bericht sieht man als GitHub-Check, als Push aufs Handy und per MCP direkt in einer
lokalen oder Cloud-Claude-Code-Session, ohne dass die Session ihren Kontext verliert.

Der Kernfall: Du sitzt in der Schule, ein PR kommt rein, dein Laptop ist zu Hause.
Crosscheck prüft ihn auf Linux, Windows, Android, Web und (mit Apple-Hardware) macOS und iOS,
auf einem simulierten Schul-PC genauso wie auf einer 4K-Workstation, bevor du mergst.

## So einfach wie möglich

1. **ISO flashen, Rechner booten, weggehen.** Proxmox installiert sich ohne Eingabe,
   Crosscheck richtet sich selbst ein und baut die Plattform-Images.
2. **QR-Code auf dem Bildschirm scannen, "GitHub-App erstellen" tippen.** Fertig.
3. **Optional:** den angezeigten Einzeiler in Claude Code einfügen, um Ergebnisse im Chat zu haben.

Kein YAML nötig: Crosscheck erkennt den Projekttyp und schlägt nach dem ersten Lauf selbst
eine passende `crosscheck.yaml` per PR vor. Details in
[08 Installation](docs/08-installation-zero-touch.md). Es gibt auch einen Weg für bestehende
Proxmox-Hosts und einen Solo-Modus ohne Proxmox.

## Prüfstufen

| Stufe | Name | Läuft wann? | Was passiert | Modell-Kosten |
|------:|------|-------------|--------------|---------------|
| 0 | Static | Bei jedem PR | Secret-Scan, SAST, Dependency- und Install-Script-Audit, Lizenzen, Injection-Muster. Keine VM. | keine |
| 1 | Smoke | Vertrauenswürdige Autoren automatisch, sonst per Label | Pro Plattform und Hardware-Preset eine Wegwerf-VM, GUI-Smoke per Computer-Use, Crash-Erkennung, Netz- und Dateibeobachtung, Leistungsmessung, visuelle Regression. | gering |
| 2 | Deep | **Nur auf Anforderung** | `/crosscheck security-review`, `/crosscheck vulnerability-review`: modellgestützte Tiefenprüfung plus exploratives Testen. Mit Budget und Kostenschätzung vorab. | spürbar |

## Hardware-Presets

Ein PR, der auf dem eigenen Rechner fliegt, kann auf einem Schul-PC unbenutzbar sein.
Crosscheck drosselt Kerne, Takt, CPU-Generation, RAM, Platte, Grafik und Netz und misst
Startzeit, Eingabelatenz, Hänger und Speicher. Presets werden pro Host kalibriert.

| Preset | Soll entsprechen |
|--------|------------------|
| `kartoffel` | 10 Jahre alter Laptop: 2 schwache Kerne ohne AVX2, 4 GB, HDD, 1366×768, 3G |
| `schul-pc` | Typischer Schul-PC: 2 Kerne, 8 GB, SATA-SSD, Software-Rendering, wackeliges WLAN |
| `mittel` | Aktueller Mittelklasse-Laptop |
| `gut` | Aktueller guter Desktop, 1440p |
| `unfassbar` | Oberklasse-Workstation: alle Kerne, 4K @150 % plus zweiter Monitor. Deckt Races und HiDPI-Fehler auf |
| `handy-billig`, `handy-top` | Android-Einsteiger bis Flaggschiff, iPhone |

Dazu Modifikatoren wie `offline`, `wackelnetz`, `hitze` (thermisches Drosseln während des
Laufs), `ram-druck`, `volle-platte`, `dunkel`, `kontrast`, `rtl`, `pseudo-l10n`,
`falsche-uhr`. Details in [09 Hardware-Profile](docs/09-hardware-profile.md).

## Fremden Code sicher ausführen

Crosscheck kann jeden PR automatisch prüfen, auch von völlig Fremden. Das geht nur, weil
**jeder PR als feindlich gilt**, auch der eigene:

- **Eine Isolation für alle.** Die Admin-Oberfläche legt fest, *wann* ein Lauf startet:
  für alle, nach einem Approve am exakten Commit, nur für bestimmte Nutzer oder manuell.
  Die Abschirmung ist dabei für jeden PR gleich stark und lässt sich nicht abschalten.
- **Wegwerf-VMs ohne Ausweg.** Es gibt keine Schlüssel, kein Heimnetz, kein IPv6 und keine
  Nachbar-VMs, nur einen Paket-Proxy mit Allow-List. Die Hardware ist minimal, QEMU ist
  gehärtet und KSM ist aus. Empfohlen ist ein eigenes Gerät für den Controller, damit
  selbst ein Ausbruch aus der VM nichts Wertvolles findet.
- **Agenten ohne Macht.** Claude, Codex oder lokale Modelle bedienen die App nur über
  Screenshot, Maus und Tastatur von außen. Coding-Agenten prüfen den Code in einer eigenen
  Wegwerf-VM mit Einmal-Token statt API-Schlüssel. Den Status setzt immer der Controller.
- **Messungen von außen.** Startzeit, Latenz, CPU und Netz misst der Host. Was die VM selbst
  meldet, gilt als fälschbar.
- **Nach dem Lauf ist alles weg.** Die VMs werden zerstört und die Platten per
  Crypto-Shredding unlesbar gemacht. Netzregeln und Tokens werden entfernt. Ein
  Aufräum-Dienst arbeitet auch nach einem Stromausfall. Das Löschprotokoll steht im Bericht.

Details: [11 Admin](docs/11-admin-und-vertrauensrichtlinien.md),
[12 Isolation und Löschung](docs/12-isolation-und-loeschung.md),
[13 Agenten und Benchmarks](docs/13-agenten-und-benchmarks.md).

## Grundprinzipien

1. **PR-Code ist feindlich.** Alles aus dem PR ist Daten, nie Anweisung. Es läuft nur in
   Wegwerf-VMs ohne Zugangsdaten und ohne Netz nach außen.
2. **Die Prüfkonfiguration kommt vom Basis-Branch.** Ein PR kann seine eigene Prüfung nicht
   umkonfigurieren.
3. **Nur der Controller hat Geheimnisse.** Runner-VMs bekommen keine. Stattdessen bekommen sie
   gefälschte Kanarienvogel-Zugangsdaten, deren Nutzung sofort auffällt.
4. **Der Bericht wird vom Controller gebaut, nicht vom Modell.** Freitext aus der Sandbox ist
   im Schema als nicht vertrauenswürdig markiert.
5. **Teuer nur auf Anforderung**, mit Budget und Kostenschätzung.
6. **Immer die neuesten Plattformen**, über Kanäle (`latest`, `lts`, `pinned`, `preview`) und
   selbstgeprüfte Template-Updates.
7. **Ohne Kontextverlust abrufbar**: klein zuerst, Details per ID, Push statt Polling.

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [01 Problem und Ziele](docs/01-problem-und-ziele.md) | Ausgangslage, Ziele, Nicht-Ziele, MVP-Kriterien |
| [02 Architektur](docs/02-architektur.md) | Controller, Vision, Runner, Store, Bridge, Datenfluss, Hosting |
| [03 Sicherheitsmodell](docs/03-sicherheitsmodell.md) | Bedrohungen, Vertrauenszonen, Maßnahmen gegen Secret-Diebstahl, Prompt- und Code-Injection, Ausbruch |
| [04 Pipeline und Prüfstufen](docs/04-pipeline-und-stufen.md) | Auslöser, Slash-Kommandos, Stufen 0 bis 2, Status-Logik |
| [05 Plattform-Matrix](docs/05-plattform-matrix.md) | Linux, Windows, Android, Web, macOS, iOS, Image-Pipeline |
| [06 Chat-Bridge (MCP)](docs/06-chat-bridge.md) | MCP-Werkzeuge, Scopes, Cloud-Sessions, Hold und Live-Übernahme |
| [07 Roadmap](docs/07-roadmap.md) | Phasen, bösartige Test-PRs, offene Entscheidungen |
| [08 Installation](docs/08-installation-zero-touch.md) | Zero-Touch-ISO, bestehender Proxmox, Solo-Modus, Versionskanäle |
| [09 Hardware-Profile](docs/09-hardware-profile.md) | Throttling, Presets, Modifikatoren, Kalibrierung, Leistungsbudget, A/B |
| [10 Feature-Katalog](docs/10-feature-katalog.md) | Alle geplanten Funktionen mit Phase und Kosten |
| [11 Admin und Vertrauensrichtlinien](docs/11-admin-und-vertrauensrichtlinien.md) | Richtlinien-Modi, Vertrauensklassen, Grenzen, Admin-Oberfläche, Sicherheitsampel |
| [12 Isolation und Löschung](docs/12-isolation-und-loeschung.md) | Topologie, sieben Schutzschichten, Crypto-Shredding, Löschprotokoll, Ausbruchstests |
| [13 Agenten und Benchmarks](docs/13-agenten-und-benchmarks.md) | Claude, Claude Code, OpenAI, Codex, lokale Modelle, Zweitmeinung, host-seitige Messung |

## Dateien

| Datei | Zweck |
|-------|-------|
| [`examples/crosscheck.yaml`](examples/crosscheck.yaml) | Prüfplan eines Repos mit fast allen Optionen |
| [`examples/admin-policy.yaml`](examples/admin-policy.yaml) | Export einer Admin-Richtlinie: Topologie, Klassen, Grenzen, Aufbewahrung |
| [`examples/mcp.json`](examples/mcp.json) | MCP-Konfiguration für Claude Code |
| [`examples/report.example.json`](examples/report.example.json) | Beispielbericht |
| [`schemas/report.schema.json`](schemas/report.schema.json) | Berichtsschema (JSON Schema 2020-12) |
| [`presets/hardware.yaml`](presets/hardware.yaml) | Hardware-Presets, Netzprofile, Modifikatoren |
| [`versions.yaml`](versions.yaml) | Plattform- und Toolchain-Versionen pro Kanal |
| [`install/answer.toml`](install/answer.toml) | Antwortdatei für die unbeaufsichtigte Proxmox-Installation |

## Status

Konzeptphase. Es gibt noch keinen lauffähigen Code. Die Dokumente beschreiben Zielbild,
Sicherheitsmodell und die Schnittstellen, gegen die die Implementierung gebaut wird.

## Lizenz

GPL-3.0, siehe [LICENSE](LICENSE).
