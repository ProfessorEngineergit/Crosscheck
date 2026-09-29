# 11 Admin-Oberfläche und Vertrauensrichtlinien

## Grundsatz

**Die Isolation ist für jeden PR gleich stark, egal von wem er kommt.** Crosscheck behandelt
auch den PR des Repo-Besitzers als feindlichen Code. Ein gekaperter Account, ein
kompromittiertes Paket oder ein Dependabot-PR mit bösartigem Update ist genauso
gefährlich wie ein Fremder.

Die Vertrauensrichtlinie in der Admin-Oberfläche steuert deshalb nur drei Dinge:

1. **Ob und wann** ein Lauf automatisch startet.
2. **Wie viel** er verbrauchen darf: Plattformen, Presets, Laufzeit, Parallelität, Modellbudget.
3. **Welche Zusatzfunktionen** erlaubt sind, die die Angriffsfläche vergrößern, etwa GPU,
   verschachtelte Virtualisierung und Egress-Beobachtung. Details stehen in
   [12 Isolation](12-isolation-und-loeschung.md).

Die Grundisolation lässt sich in der Oberfläche nicht abschalten. Es gibt keinen Schalter
"vertrauenswürdig, ohne VM ausführen".

## Vertrauensklassen

Crosscheck ordnet jeden PR beim Start des Laufs einer Klasse zu. Die Zuordnung wird zu
diesem Zeitpunkt live über die GitHub-API geprüft: Berechtigung des Autors, Reviews am
exakten Head-SHA. Die Angaben im Webhook-Payload allein genügen nicht.

| Klasse | Wer | Typische Richtlinie |
|--------|-----|---------------------|
| `maintainer` | Schreibrechte am Repo (Owner, Collaborator, Team mit Write) | Alles automatisch außer Deep |
| `vertraut` | Nutzer oder Teams auf der Allow-List der Admin-Oberfläche | Smoke automatisch |
| `bekannt` | Hat schon mindestens einen gemergten PR im Repo | Static automatisch, Smoke nach Richtlinie |
| `fremd` | Alle anderen, inklusive Erstbeitrag und Fork | Static automatisch, Smoke nach Richtlinie |
| `bot` | Dependabot, Renovate, andere Apps | Wie `fremd`, aber mit eigenem Kontingent. Abhängigkeitsupdates sind ein klassischer Lieferketten-Angriffsweg |
| `gesperrt` | Nutzer auf der Deny-List | Nichts außer Static |

## Richtlinien-Modi

Pro Repo und pro Stufe wählbar. Die Oberfläche zeigt die Modi als Auswahlliste mit
Erklärung.

| Modus | Bedeutung |
|-------|-----------|
| `alle` | Jeder PR startet automatisch, auch Forks und Erstbeiträge |
| `approved` | Start erst nach einem Approve-Review eines Maintainers **auf genau diesem Head-SHA**. Jeder neue Push braucht ein neues Approve |
| `klassen` | Automatisch nur für die gewählten Klassen, z. B. `maintainer` und `vertraut`. Alle anderen per Label oder Slash-Kommando |
| `manuell` | Nur per Label `crosscheck:run`, Slash-Kommando oder MCP |
| `aus` | Stufe ist für dieses Repo deaktiviert |

Standardwerte nach der Installation:

| Stufe | Standard | Warum |
|-------|----------|-------|
| Static | `alle` | Läuft in einer Analyse-VM, günstig, kein Code wird ausgeführt |
| Smoke | `klassen: [maintainer, vertraut]`, Rest `approved` | Sicher wäre auch `alle`, aber so bleiben Rechenzeit und Warteschlange unter Kontrolle |
| Deep | `manuell` | Kostet Modellbudget |

Wer `alle` für Smoke setzt, sieht in der Oberfläche eine Sicherheitsampel (siehe unten).

### Schutz vor Zeitlücken beim Approve

Ein Angreifer könnte darauf warten, dass ein Maintainer Commit A freigibt, und dann schnell
Commit B pushen. Dagegen hilft:

- Crosscheck arbeitet immer mit einem **festen SHA**, nie mit einem Branch-Namen. Der
  Quellcode wird per GitHub-Tarball-API für genau diesen SHA geholt.
- Das Approve muss sich per `commit_id` auf diesen SHA beziehen.
- Direkt vor dem VM-Start prüft Crosscheck erneut: Stimmt der SHA noch mit dem Approve
  überein? Hat der Approver noch Schreibrechte? Wurde das Approve zurückgezogen?

## Grenzen je Klasse

Alle Werte stellt man in der Admin-Oberfläche ein. Die Standardwerte sind:

| Grenze | maintainer | vertraut | bekannt | fremd / bot |
|--------|-----------:|---------:|--------:|------------:|
| Plattformen pro Lauf | alle | alle | 2 | 1 |
| Presets pro Lauf | alle | 3 | 2 | 1 |
| Max. Laufzeit gesamt | 60 min | 45 min | 30 min | 20 min |
| Parallele Läufe | 2 | 1 | 1 | 1 (globale Warteschlange) |
| Läufe pro Tag und Autor | ∞ | 30 | 10 | 5 |
| Hold erlaubt | ja | ja | nein | nein |
| Deep erlaubt | per Kommando | per Kommando | per Maintainer-Kommando | per Maintainer-Kommando, Zweitbestätigung optional |
| GPU (virgl, Passthrough) | wählbar | nein | nein | nein |
| Android (verschachtelte Virtualisierung) | ja | ja | nur auf eigenem Runner-Host | nur auf eigenem Runner-Host |
| Egress-Modus `observe` | wählbar | nein | nein | nein |
| Build-Cache lesen | ja | ja | ja | ja |
| Build-Cache schreiben | nur Basis-Branch-Läufe | nein | nein | nein |

## Repo-Datei und Admin-Oberfläche

- Die **Admin-Oberfläche ist die obere Grenze.** Sie steht nur auf dem Controller und ist
  nicht über Git änderbar.
- Die `crosscheck.yaml` im Repo darf nur **weiter einschränken**. Sie kann z. B. Smoke für
  Bots abschalten, aber nie `fremd` auf `alle` setzen, wenn der Admin `approved` gewählt hat.
  Widersprüche meldet Crosscheck als Hinweis, der strengere Wert gewinnt.
- Beispiel: [`examples/admin-policy.yaml`](../examples/admin-policy.yaml). Die Oberfläche
  kann die Datei im- und exportieren.

## Die Admin-Oberfläche

Die Oberfläche ist über das lokale Netz oder Tailscale erreichbar, **nie über den
öffentlichen Tunnel**. Über den Tunnel ist nur der Webhook-Endpunkt erreichbar.
Die Anmeldung läuft per Passkey (WebAuthn). Änderungen an Richtlinien verlangen eine
erneute Bestätigung per Passkey.

| Seite | Inhalt |
|-------|--------|
| **Übersicht** | Laufende Läufe, Warteschlange, Sicherheitsampel, Budget, letzte Findings |
| **Richtlinien** | Modus pro Repo und Stufe, Grenzen pro Klasse, Vorschau "Was würde bei PR #N passieren?" |
| **Nutzer und Teams** | Allow-List, Deny-List, Übersicht, wer in welche Klasse fällt |
| **Topologie** | Controller-Gerät, Runner-Hosts, Netze, Härtungsstatus pro Host (aus `crosscheck doctor`) |
| **Agenten** | Welcher Agent für welche Aufgabe, API-Schlüssel (nur schreibbar, nie wieder anzeigbar), Budgets. Siehe [13 Agenten](13-agenten-und-benchmarks.md) |
| **Aufbewahrung und Löschung** | Wie lange Berichte, Screenshots, Videos, Logs bleiben, Löschprotokolle. Siehe [12](12-isolation-und-loeschung.md) |
| **Audit-Log** | Jede Änderung, jeder manuell ausgelöste Lauf, jede Hold-Sitzung, jede Anmeldung |
| **Notaus** | Ein Knopf: Webhooks pausieren, alle Läufe abbrechen, alle Runner-VMs zerstören, alle Bridge-Tokens sperren |

### Sicherheitsampel

Die Ampel bewertet die Kombination aus Richtlinie und Topologie und erklärt in einem Satz,
warum sie welche Farbe hat.

| Farbe | Beispiel |
|-------|----------|
| Grün | Smoke `alle`, Controller auf eigenem Gerät, Runner-Host im eigenen VLAN, Host wird regelmäßig neu aufgesetzt, KSM aus |
| Gelb | Smoke `alle` auf einem Einzelrechner, auf dem auch der Controller läuft: Ein Ausbruch aus der VM würde die Controller-Schlüssel erreichen |
| Rot | Runner-Netz kann das Heimnetz erreichen, oder `crosscheck doctor` hat einen Ausbruchstest nicht bestanden. Smoke für `fremd` wird dann automatisch pausiert |

### Sicherheit der Oberfläche selbst

Die Oberfläche zeigt Inhalte aus feindlichen PRs an: Titel, Dateinamen, Beobachtungen und
Screenshots. Deshalb gilt:

- Jede Ausgabe wird kontextgerecht escaped. Eine strikte Content-Security-Policy verbietet
  Inline-Skripte.
- Screenshots und Videos kommen von einer **eigenen Origin** (`artifacts.<host>`) ohne
  Cookies. SVG wird nie ausgeliefert, nur PNG, JPEG und WebM, die vom Host neu kodiert sind.
- Links aus PR-Inhalten sind nicht klickbar.
- Aktionen, die etwas ändern, brauchen einen CSRF-Schutz und die Passkey-Bestätigung.
