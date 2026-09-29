# 13 Agenten: Claude, Codex, lokale Modelle und Benchmarks

Crosscheck lässt Agenten die Arbeit machen, die sonst ein Mensch am Rechner erledigt:
die App bedienen, Grenzfälle ausprobieren, Benchmarks starten und deuten, den Diff auf
Sicherheitslücken lesen. Dafür sind mehrere Anbieter austauschbar. Der Agent ist dabei
nie der Chef.

## Grundsatz: Der Controller ist kein Agent

Die Ablaufsteuerung ist gewöhnlicher, deterministischer Code. Er entscheidet anhand der
Richtlinie, welche VM gestartet wird, welcher Agent welche Aufgabe bekommt, wann
abgebrochen wird und welchen Status der Lauf bekommt. Agenten sind Arbeiter mit einem
engen Werkzeugkasten. Selbst ein vollständig per Prompt-Injection übernommener Agent kann:

- keine VM starten, freigeben oder am Leben halten,
- keine Richtlinie, kein Budget und keinen Status ändern,
- keinen Schlüssel sehen,
- nichts außerhalb seiner einen Wegwerf-VM erreichen.

## Drei Arten von Agenten-Einsatz

| Art | Wo läuft der Agent? | Was sieht er? | Was darf er? | Beispiele |
|-----|---------------------|---------------|--------------|-----------|
| **Bediener** (Computer Use) | Agenten-Sandbox auf dem Controller-Gerät, **außerhalb** der VM | Nur Screenshots der VM | Maus, Tastatur, Warten, Ergebnis melden | Smoke-Szenario, exploratives Testen, Barrierefreiheit per Tastatur |
| **Prüfer** (Coding-Agent) | In einer eigenen **Review-VM**, zusammen mit dem Quellcode | Quellcode, Diff, Stufe-0- und Laufzeitbefunde | Alles innerhalb der Wegwerf-VM, auch Befehle ausführen. Nach draußen nur über den Schlüssel-Proxy | Security-Review, Vulnerability-Review, Test-Ideen |
| **Auswerter** | Agenten-Sandbox auf dem Controller-Gerät | Nur strukturierte, schema-geprüfte Daten | Keine Werkzeuge, nur eine Antwort nach Schema | Benchmarks deuten, Befunde zusammenfassen, Zweitmeinungen abgleichen |

### Bediener: Werkzeuge

Der Bediener bekommt genau diese Werkzeuge. Jeder Aufruf wird vom Controller geprüft
(Koordinaten im Bildschirm, Textlänge, Schrittlimit) und protokolliert:

```
screenshot()                      -> Bild der VM
click(x, y, button)               double_click(x, y)          drag(x1, y1, x2, y2)
type(text)                        key(combo)                  scroll(x, y, dx, dy)
wait(ms)                          report_step(index, result, observation)
finish(summary)
```

Es gibt kein Werkzeug für Shell, Dateien, Netz oder den Bericht. Der Text aus `type` geht
als Tastatur-Events in die VM. Es gibt keinen Zwischenablage-Kanal.

### Prüfer: Coding-Agenten in der Review-VM

Coding-Agenten wie Claude Code oder Codex CLI brauchen eine Shell, um Code zu lesen,
Tests zu starten und Hypothesen zu prüfen. Sie bekommen deshalb eine eigene Wegwerf-VM:

1. Der Controller startet eine Review-VM vom Template `review-linux`. Darin sind Claude
   Code, Codex CLI, gängige Toolchains und die Scanner vorinstalliert, aber kein Schlüssel.
2. Der Quellcode von Basis und Head kommt als Tarball hinein, dazu die Befunde aus Stufe 0
   und 1 als JSON.
3. Der Agent läuft headless ohne Rückfragen. Das ist hier vertretbar, weil die VM
   weggeworfen wird und nichts erreichen kann:
   - Claude Code: `claude -p "<fester Review-Auftrag>" --output-format json`, mit
     `ANTHROPIC_BASE_URL` auf den Schlüssel-Proxy und dem Einmal-Token als Schlüssel.
   - Codex CLI: `codex exec "<fester Review-Auftrag>"`, mit der Basis-URL des Anbieters
     auf den Schlüssel-Proxy und dem Einmal-Token als Schlüssel.
4. Der Auftrag stammt aus dem Controller und dem Prüfplan des Basis-Branches. Er verlangt
   am Ende eine Datei `findings.json` nach dem Finding-Schema aus
   [`schemas/report.schema.json`](../schemas/report.schema.json).
5. Der Controller liest nur diese Datei. Er prüft sie gegen das Schema, kürzt Freitext,
   markiert alles als `untrusted` und zerstört die VM.

Der Schlüssel-Proxy lässt aus der Review-VM nur die Modell-Endpunkte des gewählten
Anbieters zu. Er zählt Tokens mit und bricht beim Budget des Laufs hart ab. Die einzigen
Daten, die ein gekaperter Agent darüber hinausschicken könnte, sind der Quellcode des PRs
selbst, und den hat der Angreifer ohnehin.

## Unterstützte Anbieter

Die Anbindung läuft über eine schmale Adapter-Schnittstelle. Neue Anbieter brauchen nur
einen Adapter.

| Adapter | Bediener | Prüfer | Auswerter | Anmerkung |
|---------|:--------:|:------:|:---------:|-----------|
| **Claude** (Anthropic API, Computer-Use-Werkzeug) | ✓ | | ✓ | Standard für Smoke und exploratives Testen |
| **Claude Code** (headless in der Review-VM) | | ✓ | | Standard für `security-review` |
| **OpenAI** (Computer-Use über die Responses API) | ✓ | | ✓ | Alternative oder Zweitmeinung |
| **Codex CLI** (headless in der Review-VM) | | ✓ | | Alternative oder Zweitmeinung beim Review |
| **Lokal** (offenes Vision-Modell über Ollama oder vLLM auf eigener GPU) | ✓ | ✓ | ✓ | Keine laufenden Kosten, keine Daten nach außen, schwächer. Gut für einfache Smoke-Szenarien |
| **Ohne Modell** (Playwright, feste Eingabeskripte) | ✓ | | | Für Web-Apps und für feste Klickfolgen, deterministisch und kostenlos |

Die Zuordnung stellt man in der Admin-Oberfläche pro Aufgabe ein, und die
`crosscheck.yaml` kann sie verschärfen:

```yaml
agents:
  smoke:        { primary: claude, fallback: local }
  explorative:  { primary: claude }
  security:     { primary: claude-code, second_opinion: codex }
  vulnerability:{ primary: codex }
  benchmark:    { primary: none, interpret: claude }   # messen ohne Modell, deuten mit Modell
```

## Zweitmeinung: der Name ist Programm

Mit `second_opinion` prüfen zwei unabhängige Agenten dasselbe, etwa Claude Code und Codex.
Keiner sieht das Ergebnis des anderen. Der Controller gleicht die Findings danach ab:

| Fall | Ergebnis |
|------|----------|
| Beide finden dasselbe (gleiche Datei, überlappende Zeilen, gleiche Kategorie) | Konfidenz steigt, Kennzeichnung `bestätigt` |
| Nur einer findet etwas | Bleibt drin, Kennzeichnung `einzeln`, für die Status-Logik mit reduzierter Konfidenz |
| Beide widersprechen sich direkt | Kennzeichnung `strittig`, eigener Abschnitt im Bericht |

Die Zweitmeinung verdoppelt die Kosten. Sie ist deshalb nur für Deep verfügbar und
standardmäßig aus.

## Benchmarks und Messungen

Messwerte aus der VM sind fälschbar. Ein bösartiger PR kann "Startzeit 0,1 s" in eine
Datei schreiben. Crosscheck trennt deshalb strikt nach Messquelle, und jeder Messwert im
Bericht trägt seine Quelle (`metric_sources`).

| Messwert | Quelle | Fälschbar durch den PR? |
|----------|--------|-------------------------|
| Zeit bis erstes Fenster | Host: erste Bildänderung im Framebuffer nach Start-Befehl | nein (höchstens verzögerbar) |
| Zeit bis bedienbar | Host: Bediener klickt, Host misst Bildänderung | nein |
| Eingabelatenz, Hänger | Host: Eingabe-Event bis Bildänderung, 30 fps Framebuffer-Diff | nein |
| CPU-Last der VM | Host: CPU-Zeit des QEMU-Prozesses (cgroup) | nein |
| RAM der VM gesamt | Host: RSS des QEMU-Prozesses | nein |
| Plattenzugriffe | Host: I/O-Statistik des Overlays | nein |
| Netzverbindungen | Host: Proxy-Log und Firewall-Log | nein |
| RAM des App-Prozesses | Gast | **ja**, nur als Hinweis |
| Projekteigene Benchmarks (`npm run bench`, `cargo bench`, …) | Gast | **ja**, nur für A/B auf gleichem Host und nur als Hinweis |
| Crash | Host (Fenster weg, Bild erstarrt) und Gast (Dump) | teilweise. Host-Befund zählt |

Nur Werte mit Quelle `host` können einen Lauf auf "fehlgeschlagen" setzen. Gast-Werte
erscheinen im Bericht mit dem Vermerk "vom Gast gemeldet".

### Automatisierte Benchmark-Abläufe

Benchmarks laufen ohne menschlichen Eingriff, gesteuert vom Controller:

1. Der Controller startet Basis und PR im selben Preset, abwechselnd (A, B, A, B, A, B), um
   Drift durch Wärme oder Last auf dem Host auszugleichen.
2. Vor jeder Runde misst ein Kurzbenchmark die Kalibrierung (siehe
   [09](09-hardware-profile.md)). Läuft der Host zu dem Zeitpunkt unruhig, wird die Runde
   wiederholt.
3. Der Bediener fährt das Benchmark-Szenario aus dem Prüfplan, etwa "Öffne die große
   Beispieldatei, scrolle bis ans Ende". Alternativ läuft eine feste Klickfolge ohne Modell.
4. Die Messwerte kommen vom Host. Projekteigene Benchmarks laufen zusätzlich im Gast.
5. Der Auswerter bekommt nur die Zahlen als JSON, keine Rohtexte aus dem Gast. Er schreibt
   eine kurze Deutung, etwa "Scroll-Latenz auf `schul-pc` um 40 % schlechter, auf
   `unfassbar` unverändert, deutet auf CPU-gebundene Arbeit im UI-Thread". Diese Deutung ist
   ein Hinweis. Den Status bestimmen die Budgets.

## Kosten im Griff

- Der Schlüssel-Proxy zählt jeden Token pro Lauf, pro Repo und pro Monat.
- Vor jedem Deep-Lauf schätzt Crosscheck die Kosten aus Diff-Größe, Plattformen und
  Anbieter. Ab einem Schwellwert ist ein `--confirm` nötig.
- Screenshots werden vor dem Senden verkleinert und nur bei Bildänderung neu gesendet.
- Smoke und Benchmarks laufen standardmäßig mit einem günstigeren Modell oder ganz lokal.
  Nur Deep nutzt das stärkste Modell.
- Ist das Budget erschöpft, schließt der Lauf als `neutral` mit Hinweis ab, nie als Fehler.
