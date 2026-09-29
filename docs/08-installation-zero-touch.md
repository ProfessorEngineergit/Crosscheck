# 08 Installation: Zero-Touch

Ziel: **USB-Stick rein, Rechner an, weggehen.** Nach dem ersten Start zeigt der Bildschirm
einen QR-Code. Den scannt man mit dem Handy, klickt einmal "GitHub-App erstellen", und
Crosscheck prüft ab dann jeden PR. Keine Konsole, kein YAML, keine Proxmox-Oberfläche.

Es gibt drei Wege, alle mit demselben Ergebnis.

| Weg | Für wen | Klicks | Dauer bis zum ersten Bericht |
|-----|---------|--------|------------------------------|
| **A. Crosscheck-ISO** | Leerer Rechner, alter PC, Mini-PC | 0 an der Maschine, 1 am Handy | ca. 45 min (Template-Bau inklusive) |
| **B. Auf bestehendem Proxmox** | Proxmox läuft schon | 1 Befehl, 1 am Handy | ca. 30 min |
| **C. Solo-Modus** | Irgendein Linux mit KVM, kein Proxmox gewünscht | 1 Befehl, 1 am Handy | ca. 30 min |

## Weg A: Crosscheck-ISO (Zero-Touch)

Die ISO ist ein **unverändertes Proxmox-VE-Installationsabbild in der aktuellen Version**
plus eine eingebettete Antwortdatei für den offiziellen automatischen Installer
(`proxmox-auto-install-assistant prepare-iso --fetch-from iso --answer-file answer.toml`)
plus ein First-Boot-Skript. Crosscheck baut kein eigenes Betriebssystem und patcht Proxmox
nicht. Dadurch bleiben Updates über die normalen Proxmox-Paketquellen möglich.

Ablauf ohne Eingriff:

1. **Download und Flashen.** `crosscheck-<version>.iso` von den GitHub-Releases, signiert
   (Sigstore/cosign). Flashen mit Balena Etcher, Rufus oder `dd`.
2. **Boot.** Der automatische Installer startet nach 10 Sekunden ohne Tastendruck.
   Er wählt die größte leere Platte, legt ZFS (bei ≥ 2 Platten als Mirror) oder
   LVM-thin an, nimmt DHCP, setzt Hostname `crosscheck`, erzeugt ein Zufallspasswort.
   Die Antwortdatei ist unter [`install/answer.toml`](../install/answer.toml) einsehbar.
3. **Sicherheitsbremse.** Zero-Touch heißt: Die Zielplatte wird ohne Rückfrage gelöscht.
   Die ISO ist deshalb für einen **dedizierten Rechner** gedacht. Das Boot-Menü zeigt
   10 Sekunden lang in großer Schrift "Crosscheck installiert und LÖSCHT die erste Platte",
   mit einer zweiten Menüzeile "Abbrechen / normal booten". Wer einen Rechner mit mehreren
   Platten hat, erzeugt die ISO mit fester Plattenwahl:
   `crosscheck build-iso --disk-serial <seriennummer>`.
4. **First Boot.** Das Skript `crosscheck-firstboot`:
   - aktiviert die No-Subscription-Paketquelle und spielt alle Updates ein,
   - prüft Hardware (VT-x/AMD-V, Nested-Virt, IOMMU, RAM, Kerne, GPU) und wählt den
     passenden Betriebsmodus (siehe [09 Hardware-Profile](09-hardware-profile.md)),
   - legt Netz `vmbr-crosscheck` an, schaltet KSM ab, erzeugt die Controller-VM (nur im
     Einzelrechner-Modus) und die Paket-Proxy-VM,
   - startet den Template-Bau im Hintergrund, Linux zuerst, dann Web, dann Rest,
   - misst die Hostleistung für die Hardware-Kalibrierung,
   - zeigt auf der Konsole (und per HDMI) einen **QR-Code** und eine kurze URL.
5. **Handy.** Der QR-Code öffnet den Einrichtungsassistenten. Er ist mit einem Einmal-Token
   geschützt, das nur auf dem Bildschirm steht und nach Nutzung verfällt.
6. **Ein Klick: GitHub-App.** Der Assistent nutzt den **GitHub-App-Manifest-Flow**: Ein Klick
   öffnet GitHub mit vorausgefülltem App-Manifest (Name, minimale Rechte, Webhook-URL,
   Events). GitHub legt die App an und schickt Schlüssel und Webhook-Secret zurück.
   Man wählt die Repos aus, fertig.
7. **Erreichbarkeit.** Der Assistent richtet automatisch einen Tunnel ein, damit GitHub den
   Rechner hinter dem Heim-Router erreicht. Zur Wahl: Tailscale Funnel, Cloudflare Tunnel
   oder ein eigener Relay-Server. Ohne jeden Tunnel läuft der **Poll-Modus**: Der
   Controller fragt GitHub jede Minute nach neuen PRs, nur ausgehend, kein offener Port.
8. **Chat verbinden.** Der Assistent erzeugt ein Bridge-Token und zeigt die fertige
   MCP-Konfiguration zum Kopieren, als QR-Code und als Einzeiler für Claude Code:
   ```bash
   claude mcp add --transport http crosscheck https://crosscheck.<tailnet>.ts.net/mcp \
     --header "Authorization: Bearer <token>"
   ```

### Zwei Geräte statt einem

Empfohlen, wenn jeder PR automatisch geprüft werden soll (siehe
[12 Isolation](12-isolation-und-loeschung.md)):

- **Runner-Host:** die ISO mit Bootmenü-Eintrag "Crosscheck Runner" (oder
  `crosscheck build-iso --role runner`). Er zeigt nach dem Start nur einen Kopplungscode.
- **Controller:** `crosscheck-solo up --role controller` auf einem Raspberry Pi 5, Mini-PC
  oder einer kleinen Cloud-VM. Im Einrichtungsassistenten gibt man den Kopplungscode ein.
  Der Controller erzeugt dann selbst den eingeschränkten Proxmox-API-Zugang auf dem Runner-Host.

## Weg B: Auf bestehendem Proxmox

```bash
# Auf dem Proxmox-Host als root. Das Skript prüft seine eigene Signatur, bevor es etwas tut.
curl -fsSLO https://github.com/ProfessorEngineergit/Crosscheck/releases/latest/download/install.sh
curl -fsSLO https://github.com/ProfessorEngineergit/Crosscheck/releases/latest/download/install.sh.sigstore.json
cosign verify-blob install.sh --bundle install.sh.sigstore.json \
  --certificate-identity-regexp 'github.com/ProfessorEngineergit/Crosscheck' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
bash install.sh
```

Das Skript ändert nichts an bestehenden VMs. Es legt einen eigenen Ressourcen-Pool, einen
eigenen API-Benutzer mit minimalen Rechten, eine eigene Bridge und eigene VM-IDs
(Bereich 9000 bis 9999, konfigurierbar) an. `bash install.sh --dry-run` zeigt vorher jede
Änderung. `crosscheck uninstall` entfernt alles wieder rückstandsfrei.

## Weg C: Solo-Modus ohne Proxmox

Für einen Laptop, einen Cloud-Server oder einen Rechner, auf dem schon ein normales Linux
läuft. Voraussetzung ist nur `/dev/kvm`.

```bash
curl -fsSLO https://github.com/ProfessorEngineergit/Crosscheck/releases/latest/download/crosscheck-solo
chmod +x crosscheck-solo && ./crosscheck-solo up
```

Ein einzelnes statisches Binary startet Controller, Bridge und Proxy als Prozesse und
steuert die Runner direkt über QEMU/KVM statt über die Proxmox-API. Die Isolation ist
dieselbe: vollwertige VMs, eigenes Netz-Namespace mit Bridge, kein Egress. Es fehlen nur
Proxmox-Komfortfunktionen wie Web-Konsole und Cluster.

## Immer die neuesten Versionen

"Neueste" ist bei Prüf-Images ein zweischneidiges Schwert: Man will aktuelle Plattformen
testen, aber ein Lauf soll nicht scheitern, weil gestern ein Update kam. Crosscheck löst das
mit **Kanälen**:

| Kanal | Bedeutung | Standard |
|-------|-----------|----------|
| `latest` | Jede Nacht neu gebaut aus den neuesten Upstream-Versionen, nach bestandenem Selbsttest aktiv | Ja |
| `lts` | Nur Long-Term-Support-Versionen, monatlicher Rebuild für Sicherheitsupdates | |
| `pinned` | Exakte Versionen aus `versions.lock`, für reproduzierbare Vergleiche | |
| `preview` | Betas (nächste Android-, macOS-, Windows-Insider-Version), optional als zusätzliche Plattform | |

Die Versionen stehen in [`versions.yaml`](../versions.yaml). Ein nächtlicher Job prüft
Upstream (Proxmox-Repos, Ubuntu-Releases, Windows-ISO-Feed, Android-SDK-Repository,
Apple-IPSW-Katalog, Xcode-Releases, Toolchain-Releases) und öffnet bei neuen Versionen
automatisch einen PR auf das Crosscheck-Repo selbst, wie Renovate. Der PR wird **von
Crosscheck selbst geprüft**: Neue Templates werden gebaut, gegen ein Referenzprojekt
getestet und erst nach grünem Lauf in `latest` übernommen. Das alte Template bleibt als
Rückfall zwei Wochen erhalten.

Das Host-System (Proxmox) aktualisiert sich über `unattended-upgrades` für Sicherheits-
updates selbst. Kernel-Updates, die einen Neustart brauchen, wartet der Controller ab, bis
keine Läufe aktiv sind, und startet dann in der konfigurierten Ruhezeit neu.

## Nach der Installation

- **Selbsttest.** `crosscheck doctor` (auch im Web-UI) prüft Virtualisierung, Netz-Isolation
  (versucht aktiv aus einer Test-VM auszubrechen, z. B. Controller anpingen, DNS nach außen,
  Metadaten-IP), Tunnel, GitHub-App-Rechte, Speicherplatz, Template-Status.
- **Beispiel-PR.** Der Assistent bietet an, im Repo `crosscheck-canary` die bösartigen
  Test-PRs zu öffnen und zeigt live, wie Crosscheck sie abfängt.
- **Zero-Config für Repos.** Hat ein Repo keine `crosscheck.yaml`, erkennt Crosscheck den
  Projekttyp selbst (Electron, Tauri, Flutter, Qt, GTK, .NET MAUI, Avalonia, Compose
  Multiplatform, React Native, Kotlin/Swift nativ, Web) und nutzt ein generisches
  Smoke-Szenario: starten, Hauptfenster erkennen, alle Top-Level-Menüs öffnen, Einstellungen
  öffnen, schließen. Nach dem ersten Lauf schlägt Crosscheck per PR eine passende
  `crosscheck.yaml` vor, die man nur noch mergen muss.
