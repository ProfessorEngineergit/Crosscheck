# 10 Feature-Katalog

Alle geplanten Funktionen auf einen Blick, gruppiert. Die Spalte **Phase** verweist auf die
[Roadmap](07-roadmap.md): 0 = MVP, 1 = Plattformen, 2 = Tiefe und Interaktion, 3 = Betrieb.
**Kosten** heißt hier Modell-Budget: `–` braucht kein Modell, `$` wenig, `$$` spürbar.

## Einfachheit

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Zero-Touch-ISO | USB-Stick, booten, QR-Code, ein Klick. Siehe [08](08-installation-zero-touch.md) | 0 | – |
| GitHub-App per Manifest | App wird mit einem Klick angelegt, minimale Rechte vorausgefüllt | 0 | – |
| Zero-Config | Projekttyp wird erkannt, generisches Smoke-Szenario, danach Vorschlags-PR für `crosscheck.yaml` | 0 | $ |
| Poll-Modus | Funktioniert ohne Tunnel und offenen Port | 0 | – |
| `crosscheck doctor` | Selbsttest inklusive aktivem Ausbruchsversuch aus einer Test-VM | 0 | – |
| Solo-Modus | Ein Binary auf beliebigem Linux mit KVM, ohne Proxmox | 1 | – |
| Szenario in Alltagssprache | Smoke-Schritte als normale Sätze statt Selektoren | 0 | $ |
| Szenario aufnehmen | Im Hold einmal selbst durchklicken, Crosscheck schreibt daraus das Szenario | 2 | $ |

## Plattformen und Images

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Linux, Web, Windows, Android, macOS, iOS | Siehe [05](05-plattform-matrix.md) | 0–1 | – |
| Kanäle `latest`, `lts`, `pinned`, `preview` | Immer die neueste Plattform, ohne dass Updates Läufe kaputt machen | 1 | – |
| Selbstgeprüfte Template-Updates | Neue Templates werden erst nach grünem Referenzlauf aktiv | 1 | – |
| Mehrere Distributionen | Ubuntu, Fedora, Debian, Arch, jeweils X11 und Wayland | 1 | – |
| Mehrere Desktops | GNOME, KDE Plasma, Xfce (Tray-Icons und Dialoge verhalten sich verschieden) | 2 | – |
| Paketformat-Tests | `.deb`, `.rpm`, AppImage, Flatpak, Snap, `.msi`, `.msix`, `.exe`-Installer, `.dmg`, `.apk`, `.aab` installieren, starten, deinstallieren | 1 | – |
| Deinstallations-Sauberkeit | Nach Deinstallation: Welche Dateien, Registry-Schlüssel, Autostart-Einträge bleiben? | 2 | – |
| Upgrade-Test | Letztes Release installieren, Daten anlegen, auf PR-Stand upgraden, prüfen, ob Daten und Einstellungen überleben | 2 | $ |

## Hardware und Umgebung

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Hardware-Presets | `kartoffel` bis `unfassbar`, Handy-Presets. Siehe [09](09-hardware-profile.md) | 1 | – |
| Kalibrierung | Presets als Zielleistung, pro Host umgerechnet | 1 | – |
| CPU-Generationen | Fehlende AVX2/AVX-512 simulieren, fängt "Illegal instruction" ab | 1 | – |
| Thermisches Drosseln | CPU-Limit ändert sich während des Laufs | 2 | – |
| Netzprofile | 3G bis 10 Gbit, offline, Wackelnetz | 1 | – |
| Modifikatoren | Dunkel, Kontrast, RTL, Pseudo-Lokalisierung, falsche Uhr, Sommerzeit, volle Platte, RAM-Druck, Akku | 2 | – |
| Mehrere Monitore | Gemischte Skalierung | 2 | – |
| Intelligente Matrix | Diff bestimmt, welche Plattformen und Presets laufen | 2 | – |

## Prüfungen

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Secret-Scan | gitleaks über Diff und Build-Artefakte (Secrets im fertigen Binary!) | 0 | – |
| SAST | Semgrep, optional CodeQL | 0 | – |
| Dependency-Audit | OSV offline, neue Pakete mit Alter und Maintainer-Zahl, Typosquatting-Abstand | 0 | – |
| Install-Script-Wächter | Welche Abhängigkeiten führen beim Installieren Code aus (npm `postinstall`, `setup.py`, `build.rs`)? Was tun sie (Netz, Dateien)? | 1 | – |
| SBOM und SBOM-Diff | syft pro Build, Diff gegen Basis | 1 | – |
| Lizenz-Diff | Neue Lizenzen im Abhängigkeitsbaum, Konflikt mit GPL-3.0 und Co. | 1 | – |
| Binärgrößen-Diff | Wie viel größer wird Installer oder App? | 1 | – |
| Reproduzierbarer Build | Zweimal bauen, Hashes vergleichen | 3 | – |
| GUI-Smoke | Computer-Use durch das Szenario | 0 | $ |
| Crash-Erkennung und Symbolisierung | Crash-Dumps (minidump, core, tombstone, `.ips`) einsammeln, mit Debug-Symbolen des Builds symbolisieren | 1 | – |
| Nach-Hause-Telefonieren | Jede Netzverbindung beim Start mit Ziel, Zeitpunkt, Prozess. Neue Ziele gegenüber Basis werden hervorgehoben | 0 | – |
| Dateisystem-Diff | Was schreibt die App außerhalb ihres Datenordners? | 1 | – |
| Berechtigungs-Diff | Android-Manifest, macOS-Entitlements, iOS-`Info.plist`-Usage-Strings, Windows-Manifest: neue Rechte gegenüber Basis | 1 | – |
| Visuelle Regression | Screenshots Basis gegen PR pro Schritt und Preset, Pixel- und Struktur-Diff, Diff-Bild im Bericht | 1 | – |
| Barrierefreiheit | Accessibility-Baum (AT-SPI, UI Automation, Android Accessibility, macOS AX) auf fehlende Namen, Kontrast, Tastaturbedienbarkeit, Fokus-Reihenfolge | 2 | – |
| Tastatur-only-Durchlauf | Szenario nur mit Tab, Enter, Pfeiltasten | 2 | $ |
| Lokalisierung | Fehlende Übersetzungen, abgeschnittene Texte bei Pseudo-Lokalisierung, gespiegeltes Layout bei RTL | 2 | $ |
| Speicherleck-Hinweis | RSS-Wachstum bei wiederholtem Szenario (10 Runden) | 2 | – |
| Leistungsbudget und A/B | Siehe [09](09-hardware-profile.md) | 1 | – |
| Security-Review | Modellgestützte Diff-Prüfung plus Laufzeitbeobachtungen | 2 | $$ |
| Vulnerability-Review | CVE-Erreichbarkeit, verdächtige Pakete | 2 | $$ |
| Exploratives Testen | Modell versucht gezielt Grenzfälle in Eingabefeldern | 2 | $$ |
| Fuzz-Kurzlauf | Wenn das Repo Fuzz-Ziele hat (cargo-fuzz, libFuzzer, Atheris, Jazzer): 5 Minuten pro Ziel | 3 | – |
| Flake-Erkennung | Schlägt ein Schritt fehl, wird er einmal wiederholt. Ergebnis `flaky` statt `failure`, mit Statistik über Läufe | 1 | – |

## Geschwindigkeit

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Linked Clones | VM-Start in Sekunden statt Minuten | 0 | – |
| Warmer Pool | Pro Plattform eine VM fertig gebootet im Snapshot mit RAM-Zustand. Neuer Lauf: Snapshot klonen und fortsetzen, bedienbar in ca. 5 s | 1 | – |
| Build-Cache | Pro Repo ein schreibgeschützter Cache (npm, cargo, Gradle, ccache/sccache), der nur von Läufen auf dem Basis-Branch befüllt wird. PRs lesen, schreiben nie | 1 | – |
| Paket-Proxy-Cache | Einmal geladene Pakete kommen lokal | 0 | – |
| Frühabbruch | Neuer Push bricht den alten Lauf ab | 0 | – |
| Prioritäten | Maintainer-Anfragen vor automatischen Läufen, Nachtlauf zuletzt | 1 | – |
| Mehrere Hosts | Proxmox-Cluster oder Proxmox + Mac mini + Cloud-Host, Scheduler verteilt nach Plattform und freier Leistung | 3 | – |

## Ergebnisse und Chat

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Check-Runs pro Plattform | Mit Annotations im Diff | 0 | – |
| Ein PR-Kommentar, immer aktualisiert | Tabelle, Screenshot-Galerie (verkleinert), Link zum Bericht. Nie Kommentar-Spam | 0 | – |
| MCP-Bridge | Siehe [06](06-chat-bridge.md) | 0 | – |
| Webhook bei Abschluss | Weckt Claude-Code-Cloud-Sessions | 2 | – |
| Push aufs Handy | ntfy, Pushover, Telegram, Matrix, E-Mail. Nur bei Status-Änderung | 1 | – |
| Ruhezeiten | Z. B. werktags 8 bis 13 Uhr nur kritische Benachrichtigungen | 1 | – |
| Video | Pro Plattform ein kurzes WebM, Schritte als Kapitel | 1 | – |
| Hold und Live-Übernahme | noVNC im Browser, oder Chat-Session klickt per `crosscheck_interact` | 2 | – |
| "Frag die VM" | In der Chat-Session: "Öffne in der Windows-VM den Export-Dialog und zeig mir den Screenshot" | 2 | $ |
| Vorher-Nachher-Galerie | Basis und PR nebeneinander, pro Preset | 1 | – |
| Kostenschätzung vor Deep-Review | Antwort auf `/crosscheck security-review` enthält zuerst die geschätzten Kosten; ab Schwellwert ist ein `--confirm` nötig | 2 | – |
| Bericht als Markdown und SARIF | SARIF für GitHub Code Scanning, Markdown für Menschen | 1 | – |
| Übersicht über alle Repos | Web-UI mit Trends: Startzeit, Speicher, Findings pro Woche | 3 | – |

## Betrieb und Energie

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Wake-on-LAN | Proxmox-Host schläft, ein stromsparender Wächter (Raspberry Pi, Router-Skript oder die Controller-LXC auf einem anderen Gerät) weckt ihn bei neuem PR | 2 | – |
| Energiesparplan | Nach 30 min ohne Lauf: Warm-Pool einfrieren, Host in Suspend | 2 | – |
| Budget pro Repo und Monat | Für Modellkosten, mit Anzeige im Web-UI und in `crosscheck_list_runs` | 2 | – |
| Kontingente pro Autor | Schützt vor Push-Fluten | 1 | – |
| Automatische Updates | Host-Sicherheitsupdates, Neustart nur in Ruhezeit ohne aktive Läufe | 1 | – |
| Backups | Controller-Konfiguration und Berichtsindex nächtlich, optional auf Proxmox Backup Server | 3 | – |
| Metriken | Prometheus-Endpunkt: Warteschlange, Dauer, Kosten, Flake-Rate | 3 | – |
| Audit-Log | Wer hat welchen Lauf, Hold, Deep-Review ausgelöst | 1 | – |

## Sicherheit (zusätzlich zu [03](03-sicherheitsmodell.md))

| Feature | Beschreibung | Phase | Kosten |
|---------|--------------|-------|--------|
| Kanarienvogel-Secrets | Jede VM enthält gefälschte, eindeutige Zugangsdaten (AWS-Key-Format, GitHub-Token-Format, `.ssh/id_ed25519`, Browser-Cookie-Datenbank). Liest die App sie oder tauchen sie im Egress auf, gibt es ein kritisches Finding | 1 | – |
| Honeypot-Endpunkte | Der Proxy beantwortet Anfragen an Cloud-Metadaten-IPs (169.254.169.254) mit einer Falle und meldet den Versuch | 1 | – |
| Signierte Releases | ISO, Binaries und Installer über Sigstore signiert, Build-Provenienz (SLSA) | 1 | – |
| Kurzlebige Tokens | GitHub-Installation-Tokens nur für den Moment der Nutzung, Bridge-Tokens mit Ablaufdatum | 0 | – |
| Zwei-Personen-Freigabe | Optional: Deep-Review für Fork-PRs braucht zwei Maintainer-Kommentare | 3 | – |
