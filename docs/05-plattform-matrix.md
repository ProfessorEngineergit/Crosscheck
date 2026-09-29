# 05 Plattform-Matrix und VM-Images

"ROMs" im Sinne des Projekts sind die **Golden Images**: fertig eingerichtete Betriebssystem-
Abbilder, die als Proxmox-Templates liegen und pro Lauf als Linked Clone gestartet werden.
Sie werden reproduzierbar mit Packer gebaut und mit Ansible konfiguriert. Nichts wird von
Hand in einem Template geändert.

## Übersicht

| Plattform | Host | Virtualisierung | Image-Quelle | Lizenz | Automationsstack | Aufwand |
|-----------|------|-----------------|--------------|--------|------------------|---------|
| Linux Desktop (Ubuntu 24.04 LTS, Fedora) | Proxmox | KVM | Cloud-Image + Desktop-Pakete | frei | Xorg-Session (nicht Wayland), VNC über QEMU, Guest-Agent | niedrig |
| Windows 10/11 | Proxmox | KVM + VirtIO | Evaluation-ISO (90 Tage, verlängerbar) oder eigene Lizenz | Eval oder gekauft | Autounattend.xml, Sysmon, OpenSSH für Guest-Files, VNC über QEMU | mittel |
| Android | Proxmox (Nested-KVM) | KVM in KVM, Android-Emulator | AOSP-System-Images über SDK-Manager (gespiegelt) | frei | `adb`, Emulator-Konsole, Screenshot über `adb` oder Framebuffer | mittel |
| Web (Chromium, Firefox, WebKit) | Proxmox | KVM (nie Container, auch nicht für Web) | Linux-Image + Playwright-Browser | frei | Playwright direkt, kein Computer-Use nötig | niedrig |
| macOS | Apple-Hardware (Mac mini) | Tart oder UTM (Apple Virtualization) | IPSW von Apple | Apple-EULA: nur auf Apple-Hardware, max. 2 VMs pro Host | Screen Recording-Erlaubnis, `cliclick`/Accessibility, VNC | hoch |
| iOS | Apple-Hardware, in der macOS-VM | Xcode iOS Simulator | Xcode | wie macOS | `xcrun simctl` (Install, Launch, Screenshot, Touch-Events über `simctl io`) | hoch |

### Zu iOS ganz konkret

Es gibt keinen freien, legalen Weg, iOS-Apps ohne Apple-Hardware auszuführen. Open-Source-
Projekte, die "iOS-Simulation" versprechen, laufen entweder gar nicht (Reverse-Engineering-
Versuche), sind Web-Emulationen ohne echtes Runtime oder verstoßen gegen die EULA. Die
tragfähige Lösung ist ein gebrauchter Mac mini (Apple Silicon) als zweiter Runner-Host neben
Proxmox. Darauf läuft eine macOS-VM per Tart, darin Xcode mit Simulator. Der Simulator ist
kein Emulator, er führt für arm64/x86_64-Mac gebauten Code aus; für die meisten
Cross-Platform-Frameworks (Flutter, React Native, Tauri Mobile, .NET MAUI, Qt) reicht das
für Layout-, Navigations- und Crash-Prüfung. Gerätespezifisches (Push, Kamera, echte
Signierung) bleibt außen vor.

Alternative ohne eigenen Mac: Cloud-Mac-Anbieter (MacStadium, AWS EC2 Mac, Scaleway). Dann
verlässt der PR-Code das eigene Netz; für öffentliche Repos ist das vertretbar, für private
eine bewusste Entscheidung.

## Image-Pipeline

```
packer/
  linux-ubuntu-desktop.pkr.hcl
  windows-11.pkr.hcl
  android-emulator-host.pkr.hcl
ansible/
  roles/
    common/            # Guest-Agent, Auto-Login, feste Auflösung, Build-Runner
    egress-lockdown/   # nftables / Windows-Firewall: deny all außer Proxy
    audit/             # auditd / Sysmon
    toolchains/        # node, python, rust, dotnet, jdk je nach Repo-Profil
```

Ablauf: `packer build` erzeugt eine VM auf Proxmox, Ansible richtet sie ein, Packer wandelt
sie in ein Template um und taggt es mit `crosscheck:<platform>:<build-id>`. Der Controller
wählt beim Klonen immer das jüngste Template mit dem passenden Tag, das den Nachtlauf
(siehe Auslöser in [04](04-pipeline-und-stufen.md)) bestanden hat.

Template-Rebuild: wöchentlich für Sicherheitsupdates, sofort nach Änderung an den
Ansible-Rollen. Alte Templates bleiben zwei Wochen für Wiederholungsläufe.

## Was in jedem Image steckt

- **Auto-Login** in eine grafische Session. Auflösung und Skalierung setzt der Build-Runner
  beim Start aus dem Preset im Auftrag (Standard 1920×1080 @100 %). Bildschirmschoner und Energiesparen aus, Benachrichtigungen aus, Update-Dienste aus.
- **Guest-Agent** (QEMU) für `guest-file-read`, `guest-exec` (nur vom Controller, nur für
  den Build-Runner-Start) und sauberes Shutdown.
- **Build-Runner:** ein kleines Skript (`crosscheck-runner`), das beim Login startet, das
  Einmal-ISO mountet, `job.json` liest, Tarball entpackt, Build-Kommando ausführt, App
  startet, Statusdatei schreibt. Es hat keine Netzwerkfunktion und kennt keinen Controller.
- **Egress-Lockdown:** Firewall lässt nur den Paket-Proxy (fixe IP im Runner-VLAN) durch.
  Alles andere wird geloggt und verworfen. DNS zeigt auf den Proxy, der nur Allow-List-Namen
  auflöst.
- **Audit:** Prozess-Start, Dateischreibzugriffe außerhalb des Arbeitsverzeichnisses,
  Netzwerkverbindungen, jeweils mit PID-Baum unter dem App-Prozess.
- **Toolchains:** Pro Repo-Profil (`profile: electron`, `qt`, `flutter`, `dotnet`, …) ein
  vorinstalliertes Set. Ein Repo kann zusätzliche Pakete anfordern, die dann über den Proxy
  beim Build kommen; das kostet Zeit und wird im Bericht ausgewiesen.
- **Kein Zustand:** Kein Benutzerprofil mit Daten, kein Browser mit Cookies, keine
  gespeicherten Passwörter. Das Image ist leer außer Werkzeugen.

## Proxmox-Einstellungen

- Runner-Pool: eigener Ressourcen-Pool `crosscheck`, Storage mit Thin-Provisioning (LVM-thin
  oder ZFS) für schnelle Linked Clones.
- Netz: Bridge `vmbr-crosscheck` ohne physisches Interface. Darauf: Paket-Proxy (eigene VM), sonst
  nur Runner. Der Controller hängt nicht an dieser Bridge; er erreicht die Runner
  ausschließlich über den Proxmox-Host (VNC-Socket, Guest-Agent-Socket).
- CPU: Typ laut Preset. Für den Android-Runner Nested-Virt aktiv (`kvm-intel.nested=1` bzw. AMD). Verschachtelte Virtualisierung vergrößert die Angriffsfläche des Host-Kernels. Für fremde PRs läuft Android deshalb nur auf einem Runner-Host ohne Controller (siehe [12](12-isolation-und-loeschung.md)).
- Display: `std` oder `virtio-gpu`, VNC über den Proxmox-VNC-Proxy oder direkt QEMU
  (`-vnc unix:/run/crosscheck/<vmid>.sock`).
- Grundgrößen der Templates, pro Lauf durch das Preset überschrieben (siehe [09](09-hardware-profile.md)): Linux 4 vCPU / 8 GB, Windows 4 vCPU / 8 GB, Android-Host 6 vCPU / 12 GB,
  Web 2 vCPU / 4 GB. Ein Heim-Proxmox mit 16 Kernen und 64 GB fährt zwei Plattformen parallel.
- Zeitlimit hart über den Controller, zusätzlich ein Watchdog in der VM, der nach
  `max_runtime + 5 min` selbst herunterfährt.

## Kosten- und Zeitrahmen (grob)

| Setup | Einmalig | Laufend |
|-------|----------|---------|
| Proxmox-Host vorhanden, nur Linux + Web | 1 Tag Einrichtung | Strom |
| + Windows | + 1 Tag (Autounattend, Sysmon, VirtIO-Treiber) | Lizenz oder Eval-Reset alle 90 Tage |
| + Android | + 0,5 Tag | mehr RAM |
| + macOS/iOS auf Mac mini | + 2 Tage plus Hardware (gebraucht ab ca. 400 €) | Strom, Xcode-Updates |
| Stufe 2 aktiv | 0 | Modell-Budget, typ. 0,20 bis 2 € pro Deep-Lauf je nach Diff-Größe |
