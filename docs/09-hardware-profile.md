# 09 Hardware-Profile und Throttling

Ein PR kann auf dem eigenen schnellen Rechner einwandfrei laufen und auf dem Schul-PC mit
zwei Kernen, HDD und 1366×768 unbenutzbar sein. Crosscheck simuliert deshalb nicht nur
Betriebssysteme, sondern auch **Hardware-Klassen**. Jede Plattform kann in mehreren Presets
laufen, und Crosscheck misst, wie sich die App darin verhält.

## Stellschrauben

Alles wird über Mittel gesteuert, die Proxmox/QEMU, der Linux-Host oder der Emulator
ohnehin anbieten. Nichts davon braucht Code in der VM.

| Dimension | Mechanismus | Was es aufdeckt |
|-----------|-------------|-----------------|
| **Kerne** | Proxmox `cores`, `sockets` | Annahmen über Parallelität, Deadlocks bei 1 Kern, UI-Thread-Blockaden |
| **CPU-Takt** | Proxmox `cpulimit` (Anteil CPU-Zeit, z. B. 0.6 = 60 % eines Kerns) plus `cpuunits` | Langsamer Start, Animationen ruckeln, Timeouts im Code |
| **CPU-Generation** | Proxmox `cpu` Typ (`x86-64-v2-AES`, `x86-64-v3`, `host`) und Flag-Masken (`-avx2`, `-avx512f`) | **Absturz mit "Illegal instruction"** auf älteren CPUs, weil eine Abhängigkeit mit AVX2 gebaut wurde |
| **Thermisches Drosseln** | Controller ändert `cpulimit` während des Laufs per API (z. B. 30 s voll, dann 40 %) | Apps, die nach dem ersten Eindruck unbrauchbar werden |
| **Arbeitsspeicher** | Proxmox `memory`, `balloon` aus, kleine Swap-Datei auf gedrosselter Platte | Speicherhunger, OOM-Kills, Swap-Stottern |
| **Speicherdruck** | Controller bläht per Balloon-Treiber während des Laufs Speicher auf | Verhalten, wenn andere Programme RAM belegen |
| **Platte** | Proxmox `mbps_rd`, `mbps_wr`, `iops_rd`, `iops_wr` pro Disk | eMMC- und HDD-Rechner, synchrone Datei-I/O im UI-Thread |
| **Netz** | `tc qdisc netem` auf dem Tap-Interface des Hosts: Latenz, Jitter, Verlust, Bandbreite, Neuordnung | Hängende UI ohne Netz, fehlende Timeouts, Retry-Stürme |
| **Offline** | Tap-Interface down oder `netem loss 100%` | Offline-Fähigkeit, Fehlermeldungen |
| **Grafik** | kein GPU-Treiber (Software-Rendering: llvmpipe, WARP), `virtio-gpu` mit virgl, GPU-Passthrough per VFIO | Blanke Fenster ohne GPU, Shader-Fehler, Electron/Chromium-GPU-Blacklist |
| **Bildschirm** | Auflösung und Skalierung im Gast gesetzt: 1366×768 @100 %, 1920×1080 @100/125 %, 2560×1440 @125 %, 3840×2160 @150/200 % | Abgeschnittene Dialoge, verschwommene Icons, falsche HiDPI-Behandlung |
| **Mehrere Monitore** | Zwei virtuelle Displays, unterschiedliche Skalierung | Fenster öffnen auf dem falschen Schirm, DPI-Wechsel |
| **Zeit** | Uhr des Gastes verschoben, Zeitzone, Sommerzeit-Wechsel simuliert | Datumsfehler, Zertifikatsfehler bei falscher Uhr |
| **Energie** | Windows-Energiesparplan, Android `dumpsys battery unplug` + Energiesparmodus, Linux `powerprofilesctl power-saver` | Hintergrundarbeit, die bei Energiesparen stirbt |
| **Mobilgeräte** | Android-Emulator: `-cores`, `-memory`, `-netdelay`, `-netspeed`, Geräteprofil (Bildschirm, DPI). iOS-Simulator: Gerätetyp (iPhone SE bis Pro Max, iPad) | Layout auf kleinen Displays, Speicher auf Low-End-Android |

Grenze: Der iOS-Simulator läuft mit der CPU des Macs. CPU-Throttling ist dort nur über die
macOS-VM selbst möglich (Kerne, Speicher), nicht pro Simulator. Crosscheck weist
iOS-Leistungswerte deshalb als "nicht aussagekräftig für echte Geräte" aus.

## Presets

Presets sind benannte Bündel dieser Stellschrauben. Sie stehen in
[`presets/hardware.yaml`](../presets/hardware.yaml) und können pro Repo überschrieben
oder ergänzt werden.

| Preset | Soll entsprechen | Kerne | CPU | RAM | Platte | Grafik | Bildschirm | Netz |
|--------|------------------|-------|-----|-----|--------|--------|------------|------|
| `kartoffel` | 10 Jahre alter Laptop, Netbook, Chromebook mit Linux | 2 | 35 % pro Kern, `x86-64-v2` ohne AVX2 | 4 GB | HDD: 80 MB/s, 120 IOPS | Software | 1366×768 @100 % | 3G: 300 ms, 1,5 Mbit, 2 % Verlust |
| `schul-pc` | Typischer Schul- oder Büro-PC | 2 | 60 %, `x86-64-v2` | 8 GB | SATA-SSD: 400 MB/s | Software | 1920×1080 @100 % | Schul-WLAN: 40 ms ± 30 ms, 15 Mbit, 1 % Verlust |
| `mittel` | Aktueller Mittelklasse-Laptop | 4 | 100 %, `x86-64-v3` | 16 GB | NVMe gedrosselt: 1500 MB/s | virgl | 1920×1080 @125 % | DSL: 20 ms, 50 Mbit |
| `gut` | Aktueller guter Desktop | 8 | 100 %, `host` | 32 GB | ungedrosselt | virgl oder Passthrough | 2560×1440 @125 % | Glasfaser: 5 ms, 500 Mbit |
| `unfassbar` | Workstation der Oberklasse | alle freien Kerne | 100 %, `host` | so viel wie frei, bis 128 GB | ungedrosselt, RAM-Disk für Temp | Passthrough, falls vorhanden | 3840×2160 @150 % + zweiter Monitor @100 % | 10 Gbit, 0 ms |
| `handy-billig` | Android-Einsteigergerät | 4 (Emulator) | 50 % | 2 GB | gedrosselt | Software | 720×1600, 320 dpi | 3G |
| `handy-top` | Android-Flaggschiff / aktuelles iPhone | 8 | 100 % | 12 GB | ungedrosselt | Host-GPU | 1440×3120, 560 dpi | 5G: 20 ms, 300 Mbit |

Grafikbeschleunigung (`virgl`, Passthrough) vergrößert die Angriffsfläche des Hosts. Für
Vertrauensklassen ohne GPU-Freigabe (Standard: alle außer `maintainer`) ersetzt Crosscheck
sie automatisch durch Software-Rendering und vermerkt das im Bericht. Siehe
[12 Isolation](12-isolation-und-loeschung.md).

Warum auch `unfassbar`? Schnelle Maschinen decken eigene Fehler auf: **Race Conditions**,
die nur auftreten, wenn der Hintergrund-Thread schneller fertig ist als die UI, und
**HiDPI-Fehler** auf 4K mit Skalierung und gemischten Monitoren.

### Modifikatoren

Modifikatoren lassen sich auf jedes Preset setzen, z. B. `mittel+offline+dunkel`.

| Modifikator | Wirkung |
|-------------|---------|
| `offline` | Kein Netz ab App-Start |
| `wackelnetz` | Netz fällt alle 20 s für 5 s aus |
| `hitze` | Nach 30 s wird die CPU auf 40 % gedrosselt |
| `ram-druck` | Nach dem Start werden 50 % des freien RAMs per Balloon belegt |
| `volle-platte` | Nur 200 MB frei auf der Systemplatte |
| `akku` | Energiesparmodus aktiv |
| `dunkel` | Dunkles Systemdesign |
| `kontrast` | Hoher Kontrast, Schriftgröße 150 % |
| `rtl` | Sprache Arabisch/Hebräisch, Rechts-nach-links-Layout |
| `pseudo-l10n` | Pseudo-Lokalisierung: alle Texte 40 % länger mit Akzenten, deckt abgeschnittene Texte auf |
| `falsche-uhr` | Uhr zwei Jahre zurück |
| `sommerzeit` | Uhr 2 Minuten vor Zeitumstellung |

## Kalibrierung: gleiche Presets auf unterschiedlichen Hosts

`cpulimit 0.35` auf einem Server von 2025 ist etwas anderes als auf einem Mini-PC von 2019.
Deshalb werden Presets nicht in Prozent, sondern in **Zielleistung** angegeben: ein
Single-Thread- und ein Multi-Thread-Richtwert.

1. Beim ersten Start misst `crosscheck-firstboot` die Host-Leistung mit einem festen,
   mitgelieferten Benchmark (Kompression, JSON-Parsing, Canvas-Rendering im Headless-Browser).
2. Der Controller rechnet pro Preset aus, welches `cpulimit` und welche Kernzahl die
   Zielleistung ergeben.
3. Jede VM startet mit einem 5-Sekunden-Kurzbenchmark. Weicht das Ergebnis mehr als 15 %
   vom Ziel ab (z. B. weil der Host gerade ausgelastet ist), justiert der Controller nach
   oder markiert die Messung als unsicher.
4. Ist der Host zu schwach für ein Preset (z. B. `unfassbar` auf einem Mini-PC), läuft das
   Preset mit dem, was da ist, und der Bericht sagt das offen: "Preset `unfassbar` nicht
   erreichbar, gemessen: 62 % der Zielleistung".

## Was pro Preset gemessen wird

| Messwert | Wie |
|----------|-----|
| **Zeit bis erstes Fenster** | Build-Runner meldet Fenster, Controller nimmt die Zeit |
| **Zeit bis bedienbar** | `crosscheck-vision` klickt ein definiertes Element und misst, bis sich der Bildschirm ändert |
| **Eingabelatenz** | Mittel und 95. Perzentil über alle Smoke-Aktionen (Klick bis sichtbare Änderung, per Framebuffer-Diff mit 30 fps) |
| **Hänger** | Zeiträume > 500 ms, in denen das Fenster nach einer Eingabe nicht reagiert |
| **Speicher** | RSS des Prozessbaums: Start, Spitze, nach Smoke, Wachstum pro Minute (Leck-Hinweis) |
| **CPU im Leerlauf** | Mittlere Auslastung in 30 s ohne Eingabe nach dem Smoke |
| **Plattenzugriffe** | Gelesene/geschriebene MB beim Start |
| **Netzverkehr** | Anzahl Verbindungen, Ziele, übertragene Bytes |
| **Layout** | Elemente außerhalb des Bildschirms, abgeschnittener Text (Vision-Befund plus Accessibility-Baum) |

## Leistungsbudget

Im Prüfplan kann man Grenzen setzen. Ein Überschreiten wird ein Finding der Kategorie
`performance`:

```yaml
performance:
  budgets:
    kartoffel:
      time_to_interactive_ms: 8000
      input_latency_p95_ms: 400
      memory_peak_mb: 1200
    mittel:
      time_to_interactive_ms: 2500
      input_latency_p95_ms: 120
  regression:
    # Vergleich mit dem Basis-Branch auf demselben Host und Preset
    max_slowdown_percent: 20
    fail_on_regression: false   # nur Hinweis, nicht rot
```

## Vergleich Basis gegen PR

Einzelwerte schwanken. Deshalb läuft auf Wunsch der **Basis-Branch im selben Preset direkt
davor oder parallel** (A/B). Crosscheck meldet dann Unterschiede statt absoluter Werte:
"Start auf `schul-pc` 1,8 s langsamer als auf `main` (± 0,3 s über 3 Wiederholungen)".
Die Basis-Messung wird pro Basis-SHA zwischengespeichert, damit sie nicht für jeden PR
neu läuft.

## Matrix-Größe bändigen

Plattformen × Presets × Modifikatoren wird schnell groß. Standardregeln:

- **Jeder Push:** eine Plattform (die primäre), Preset `mittel`.
- **Label `crosscheck:run`:** alle Plattformen, Presets `schul-pc` und `mittel`.
- **Label `crosscheck:matrix`:** alle Plattformen × alle Presets aus dem Prüfplan.
- **Nachtlauf auf `main`:** volle Matrix inklusive Modifikatoren, damit Regressionen am
  nächsten Morgen sichtbar sind.
- **Intelligente Auswahl:** Berührt der Diff nur Windows-spezifischen Code, läuft Windows
  mit mehr Presets und Linux nur mit einem. Berührt er nur Dokumentation, läuft nur Stufe 0.
