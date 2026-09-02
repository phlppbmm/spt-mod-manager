# Handoff: `sptmod` – git-basierter Mod-Manager für Single Player Tarkov

Version 3 des Handoffs. Ersetzt frühere Fassungen vollständig. Änderungen gegenüber v2: Forge ist Kernbestandteil (Katalog, Download, Installation aus dem Manager), Erststart-Flow mit Forge-Login, drei Listenansichten als Frontend-Kern, Drop-Zone gestrichen, keine Phasen-Zeitplanung mehr – alles wird in einem Zug gebaut, die Reihenfolge in §10 ist nur Abhängigkeitsreihenfolge.

## 1. Kontext

Single Player Tarkov (SPT, aktuell 4.1.x) hat keinen Mod-Manager, der Profile versioniert, Installationen sauber rückgängig macht oder unter Linux läuft. Die bestehenden Manager (SP-EFT Manager, TioEmirs SPT Mod Manager) sind Electron-Apps für Windows, tracken Mods über eigene Registry-JSONs und wissen nichts vom Spielerprofil. Mods werden sonst von Hand entpackt; Entfernen heißt Dateien suchen und löschen, und Mods mit eigenen Items/Tradern/Quests hinterlassen Rückstände im Profil-JSON, die den Server crashen lassen.

Kernidee: Das Spielverzeichnis wird ein Git-Repo mit einer Whitelist-`.gitignore`, die nur Mod-Pfade und Profile trackt. Jeder Mod ist ein Commit mit strukturierten Trailern. Entfernen ist ein Revert, Deaktivieren ist ein Revert, Reaktivieren ein Cherry-Pick. Die History ist die Mod-Liste. Profile werden mitversioniert, sodass Mod- und Profilstand immer zusammen zurückrollen. Git ist die einzige Quelle der Wahrheit, es gibt keine eigene Datenbank.

Positionierung: „Der einzige SPT-Mod-Manager, der jede Installation rückgängig machen kann, dein Profil mitsichert, unter Linux und Windows läuft und dessen Modstand eine Textdatei ist, die du in Discord posten kannst."

UX-Leitlinie: Der Nutzer wird genau zweimal gefragt – nach dem Installationspfad und nach dem Forge-Login. Danach sieht er nur noch Modlisten (alle, installiert, nicht installiert) und klickt. Keine Archive, keine Drop-Zones, keine Dateidialoge im Normalbetrieb. Das CLI bleibt als gleichwertige Oberfläche für Linux-Nutzer und Fika-Server-Betreiber erhalten.

Entwicklung: Philipp, Linux (EndeavourOS), SPT läuft über Wine/Proton (UMU-Launcher). Zielplattformen des fertigen Produkts: Linux und Windows. Keine Linux-only-Annahmen im Kern, Pfade über `pathlib`.

## 2. Zwei Stufen, eine Architektur

**Stufe 1 (Prototyp):** Python. Ein Core-Paket mit reiner Geschäftslogik, darüber zwei dünne Adapter: ein CLI (typer) und ein FastAPI-Server. Ein Svelte-5-Frontend spricht mit dem FastAPI-Server. Ziel: vollständige, saubere Testsuite gegen den Core, die als Spezifikation für den Port dient.

**Stufe 2 (Port):** Core und CLI werden nach Rust portiert (`git2`, `clap`, `sevenz-rust`, `unrar`, `serde_json`, `reqwest`, `keyring`). Das Svelte-Frontend bleibt und wird von Tauri gehostet; Tauri-Commands ersetzen die HTTP-Endpunkte 1:1. Kein Node-Server mehr.

Damit Stufe 2 ein Austausch und kein Neuschreiben ist, gelten drei Regeln:

1. **Der Core kennt weder CLI noch HTTP.** Service-Funktionen nehmen einfache Typen und Dataclasses, geben Dataclasses zurück, werfen typisierte Exceptions. Kein `print`, kein Request-Objekt, kein Logging-Output außer über einen injizierten Logger.
2. **Ein API-Vertrag, zwei Transporte.** Jeder FastAPI-Endpunkt ist eine 1:1-Abbildung einer Service-Funktion: gleicher Name, gleiche Parameter, gleiches Rückgabemodell. Die Pydantic-Modelle sind der Vertrag; aus ihnen wird ein OpenAPI-Schema generiert, aus dem die TypeScript-Typen des Frontends erzeugt werden (`openapi-typescript`). Im Rust-Port implementieren Tauri-Commands denselben Vertrag mit denselben Namen und Feldern.
3. **Das Frontend hat eine Transport-Schicht.** Ein einziges Modul `src/lib/api/transport.ts` mit einer Funktion `call<T>(name, payload): Promise<T>`. Stufe 1: `fetch` gegen FastAPI. Stufe 2: `invoke` aus `@tauri-apps/api`. Kein anderer Code im Frontend ruft `fetch` oder `invoke` auf. Long-running Operationen (Install, Entpacken) melden Fortschritt über eine ebenfalls abstrahierte `subscribe(name, handler)`-Funktion – Stufe 1 Server-Sent Events, Stufe 2 Tauri-Events.

## 3. SPT-Fakten, die der Core kennen muss

Ein SPT-4.x-Mod-Archiv (`.7z`, `.zip`, seltener `.rar`) enthält einen oder beide dieser Bäume, die mit dem Spielverzeichnis gemergt werden:

- `SPT/user/mods/<ModOrdner>/` – Server-Mod. Enthält immer `package.json` mit mindestens `name`, `version`, `sptVersion` (Semver-Range), oft `author`, `dependencies`, manchmal eine GUID. Optional `db/` mit eigenen Items/Tradern/Quests als JSON.
- `BepInEx/plugins/<ModName>.dll` oder `BepInEx/plugins/<ModOrdner>/` – Client-Mod. Metadaten (Name, Version, GUID) stecken in den .NET-Assembly-Attributen der DLL; für den Prototyp reicht Dateiname als Name und `--name/--version` als Override, Assembly-Parsing wird nicht gebaut (Forge-Match läuft über den Katalog).
- Client-Mods schreiben zur Laufzeit Configs nach `BepInEx/config/<autor.modname>.cfg`.

Die Archivstruktur weicht in der Praxis ständig ab, und das ist der Support-Fall Nummer eins aller bestehenden Manager: ein Wrapper-Ordner um alles (`SAIN-3.2.1/SPT/...`), `user/mods/` ohne `SPT/`-Präfix (ältere Mods), `plugins/` ohne `BepInEx/`, Groß-/Kleinschreibung. Das Tool muss den Ankerpunkt im Archiv suchen, nicht voraussetzen.

Load Order ist kein Thema mehr: SPT 4.x-Mods deklarieren ihre Abhängigkeiten selbst, die Community hat manuelles Präfix-Sortieren aufgegeben.

Profile: `SPT/user/profiles/<profileId>.json`. Der Server legt eigene Backups unter `SPT/user/profiles/backups/` an (nicht tracken). Für Rückstände relevant: `characters.pmc.Inventory.items[].tpl` (Item-Template-IDs, 24-stellige Hex-Strings), `characters.pmc.TradersInfo` (Keys = Trader-IDs), `characters.pmc.Quests[].qid`, `characters.pmc.Hideout`.

Aktuelle SPT-Version steht in `SPT/SPT_Data/configs/core.json` (Feld `sptVersion`) – Pfad beim ersten echten Verzeichnis verifizieren.

Mods dürfen nur bei geschlossenem Server und Client verändert werden. Prozess-Check auf `SPT.Server` und `EscapeFromTarkov` im Prozessnamen (unter Linux laufen sie als Wine-Prozesse, Name endet auf `.exe`); bei Treffer abbrechen.

Entpacken: `py7zr` für .7z, `zipfile` für .zip, `rarfile` für .rar (Backend `unrar` oder `bsdtar`, Fehlermeldung mit Installationshinweis, wenn keins gefunden). Archive vor dem Entpacken auf Path-Traversal prüfen (`..`, absolute Pfade). Archive kommen im Normalfall aus dem Forge-Download (§4a), das CLI akzeptiert zusätzlich lokale Dateien.

### 3a. Forge-API

Der Forge (`forge.sp-tarkov.com`) ist die einzige Mod-Quelle und hat eine dokumentierte REST-API unter `/api/v0` (Doku: `forge.sp-tarkov.com/docs/index.html`). Die Website wird **nicht** gecrawlt – Cloudflare, instabiles HTML, und die Betreiber haben die API genau dafür gebaut. Fällt die API aus, zeigt der Manager den letzten Cache und sagt es.

Authentifizierung: Bearer-Token. Ohne Token ist nur `/api/v0/ping` erreichbar. Token gibt es auf zwei Wegen: `POST /api/v0/auth/login` mit E-Mail und Passwort (liefert Token) oder manuell über die Website (Profilbild → API Tokens). Der Manager nutzt den Login-Endpunkt für den Erststart-Flow und bietet daneben ein Feld zum Einfügen eines selbst erzeugten Tokens. Das Passwort wird nie gespeichert; das Token landet im System-Schlüsselbund (`keyring`: Secret Service unter Linux, Credential Manager unter Windows), Fallback mit deutlicher Warnung: Datei mit 0600 im Konfigverzeichnis. Token beim Erzeugen benennen („sptmod auf <hostname>"), „Abmelden" löscht es lokal.

Relevante Endpunkte (Feldnamen beim ersten echten Aufruf gegen die Doku verifizieren):

- `GET /api/v0/mods` – Katalog, paginiert, mit Suche/Filter. Keine Download-Links.
- `GET /api/v0/mod/{modId}` – Details.
- `GET /api/v0/mod/{modId}/versions` – Versionen mit `link` (Direktdownload), `content_length`, SPT-Kompatibilität.
- `GET /api/v0/mods/updates` – installierte Mods gegen empfohlene Versionen, mit `link`.
- `GET /api/v0/mods/dependencies` – kompatible Versionen von Abhängigkeiten, mit `link`.

Rate-Limits stehen nicht in der Doku; TioEmirs Manager arbeitet mit 40 Requests/10 s und 200/60 s. Client mit Token-Bucket auf diese Werte drosseln, 429 mit Backoff behandeln. Alle Katalogdaten lokal cachen (SQLite im Konfigverzeichnis, kein Git – das ist Fremd-Zustand, keine Wahrheit über die Installation), Voll-Sync beim ersten Start, danach Delta über `updated_at`/Paginierung, manueller „Aktualisieren"-Knopf.

Download-Links sind laut Forge-Richtlinien verpflichtend Direktlinks (GitHub-Releases empfohlen, Landing Pages verboten). Download mit Streaming und Fortschritt, Dateigröße gegen `content_length` prüfen, Content-Disposition oder URL-Endung für den Dateinamen, dann derselbe Install-Pfad wie bei lokalen Archiven. Der Nutzer sieht das Archiv nie.

## 4. Funktionsumfang

Alles unten ist Core-Funktionalität und über CLI *und* API erreichbar. Die Tabelle nennt den CLI-Namen; der API-Name ist derselbe in snake_case.

### 4a. Forge-Kommandos

| Kommando | Verhalten |
|---|---|
| `login [--token TOKEN]` | Ohne `--token`: E-Mail/Passwort abfragen, `POST /auth/login`, Token in Schlüsselbund. Mit `--token`: direkt speichern. Prüft mit einem Testaufruf. |
| `logout` | Token aus Schlüsselbund löschen. |
| `sync [--full]` | Katalog vom Forge in den lokalen Cache holen (Delta, `--full` erzwingt Vollabgleich). Meldet Anzahl neuer/aktualisierter Einträge. |
| `search QUERY [--compatible]` | Suche im Cache; `--compatible` filtert auf erkannte SPT-Version. Zeigt Installationsstatus aus der Git-History. |
| `browse [--installed\|--available\|--all] [--compatible]` | Die drei Listen des Frontends als Tabelle. „installed" = aktiver Mod mit `Mod-Forge-Id` oder Namensmatch; „available" = im Cache, nicht installiert. |
| `install MOD_ID_OR_NAME [--version V] [--force]` | **Hauptpfad.** Löst über den Cache Version (Standard: neueste zur SPT-Version kompatible), fragt `/mods/dependencies`, zeigt Plan (Mod + fehlende Dependencies mit Größen), lädt alle Archive mit Fortschritt, installiert in Abhängigkeitsreihenfolge, jeder Mod ein eigener Commit mit gesetzter `Mod-Forge-Id`. Konflikt-/Profil-Regeln aus §6 gelten unverändert. |
| `install --file ARCHIVE...` | Lokale Archive, sonst identisch (CLI-only-Komfort, im Frontend nicht vorhanden). Versucht Forge-Match über Name/Version und setzt `Mod-Forge-Id`, wenn eindeutig. |
| `update [NAME...] [--check]` | `/mods/updates` für installierte Mods; `--check` zeigt nur. Update = neues Archiv laden, alten Mod-Stand im selben Commit ersetzen (`mod: SAIN 3.2.1 → 3.3.0`, Trailer `Mod-Previous-Version`). Configs unter `BepInEx/config/` bleiben unangetastet. |

### 4b. Lokale Kommandos

| Kommando | Verhalten |
|---|---|
| `init [PATH]` | Prüft SPT-Verzeichnis (`SPT/` und `BepInEx/` vorhanden). Legt Whitelist-`.gitignore` an, `git init`, Commit `chore: clean SPT <version>` mit Trailer `Spt-Version`. Bricht ab, wenn bereits Repo. Existieren bereits Mods, werden sie automatisch per `adopt` aufgenommen (mit Rückfrage im CLI, im Frontend als Liste zur Bestätigung). |
| *(interner Install-Kern)* | Von `install` (Forge) und `install --file` gemeinsam genutzt: Working tree clean, Spiel nicht laufend. Entpacken nach Temp, **Ankerpunkt-Suche** (§3), Typ bestimmen (server/client/hybrid). Dependency-Check und Konfliktprüfung (§6). Kopieren, Commit mit Trailern (§5). Fortschritt pro Schritt melden. |
| `list [--all]` | Aktive Mods aus `git log` per Trailer: Name, Version, Typ, SPT-Range, Zustand, Datum, Kurzhash. `--all` zeigt auch deaktivierte und entfernte. |
| `files NAME` | Dateien des Mod-Commits. |
| `remove NAME [--force]` | Profil-Check (§7), dann `git revert --no-edit`. Bei Revert-Konflikt: abbrechen, Zustand säubern, meldet, welcher spätere Mod die Datei überschreibt. |
| `disable NAME` / `enable NAME` | `disable` = Revert mit Trailer `Mod-State: disabled` und `Mod: NAME`. `enable` = Cherry-Pick des ursprünglichen Install-Commits mit Trailer `Mod-State: enabled`. Kein `.disabled`-Ordner, alles bleibt in der History. Zustand eines Mods ergibt sich aus dem jüngsten Commit mit seinem `Mod:`-Trailer. |
| `status` | `git status --short` interpretiert: geänderte Profile („normal nach dem Spielen", Hinweis auf `snapshot`), geänderte Configs, **untracked Dateien unter Mod-Pfaden als „manuell installiert" gruppiert** mit Vorschlag `adopt`. |
| `adopt PATH [--name] [--version]` | Nimmt einen bereits vorhandenen, untracked Mod-Ordner oder eine DLL als Install-Commit auf. Metadaten aus `package.json`, sonst Override nötig. Macht bestehende Installationen migrierbar. |
| `snapshot [-m MSG]` | Committet nur `SPT/user/profiles/` und `BepInEx/config/` als `snapshot: <MSG oder Zeitstempel>`. Kein `Mod:`-Trailer. |
| `check` | Profil-Check über alle aktiven Mods und alle Profile, ohne Änderung. |
| `export [--format text\|json] [--client-only]` | Modliste als Text (Name, Version, Forge-Link) oder JSON. `--client-only` für Fika-Spieler. |
| `import LISTE [--apply]` | Liest exportierte Liste, vergleicht mit installiertem Stand, zeigt Differenz: fehlt / andere Version / zusätzlich. Mit `--apply` werden fehlende Mods über den Forge-Install-Pfad installiert (Fika-Spieler übernehmen so den Server-Stand mit einem Kommando). |
| `history [NAME]` | Chronologische Commit-Liste, optional auf einen Mod gefiltert. |

Server-Adapter zusätzlich: `GET /events` (SSE) für Fortschritt, `GET /health`, `GET /openapi.json`. Server bindet nur an `127.0.0.1`, Port konfigurierbar, wird per `sptmod serve` gestartet und öffnet optional den Browser. Das Token verlässt den Core nie: Das Frontend schickt beim Login E-Mail/Passwort an den Server, der Server ruft den Forge und speichert; das Frontend erhält nur `{logged_in: true, username}`.

### 4c. Frontend

Erststart-Flow, genau zwei Schritte, danach nie wieder: (1) SPT-Installationspfad wählen (nativer Ordnerdialog; Validierung und `init` laufen sofort, vorhandene Mods werden zur Übernahme gelistet), (2) Forge-Login (E-Mail/Passwort oder Token einfügen; überspringbar, dann bleiben Katalogansichten leer mit Login-Hinweis). Danach Katalog-Sync im Hintergrund mit Fortschritt.

Hauptansicht: eine Modliste mit drei Tabs – **Alle**, **Installiert**, **Nicht installiert** – plus Suchfeld und Filter „nur kompatibel mit meiner SPT-Version" (standardmäßig an). Jede Zeile: Name, Autor, installierte/neueste Version, Typ, Zustand (aktiv/deaktiviert/Update verfügbar/inkompatibel), eine primäre Aktion (Installieren / Aktualisieren / Deaktivieren) und ein Menü (Aktivieren, Entfernen, Dateien, Forge-Seite öffnen). Installieren zeigt vorher den Plan (Mod + Dependencies mit Größen) zur Bestätigung, danach Fortschritt inline in der Zeile. Konflikte und Profil-Rückstände erscheinen als Dialog mit den Meldungen aus §6/§7 und einem „Trotzdem"-Knopf, der `--force` entspricht.

Nebenansichten: **History** (Commit-Liste, pro Eintrag Dateien und Rückgängig), **Profile** (Profil-Check-Ergebnis, Snapshot-Knopf), **Einstellungen** (Pfad, Forge-Konto, Abmelden, Export/Import der Modliste, Cache leeren).

Nicht gebaut: Drop-Zone, Dateidialoge für Archive, Load-Order-Editor, Instanzverwaltung als Feature (ein Repo pro Installation, Pfadwechsel in den Einstellungen reicht), Modpack-Branches als Feature (Git-Branches funktionieren, das Tool darf sie nur nicht kaputt machen), automatische Profil-Bereinigung (nur Warnung), Assembly-Metadaten aus DLLs (Name aus Dateiname, Forge-Match über Katalog).

## 5. `.gitignore` und Commit-Format

Whitelist:

```
*
!.gitignore
!SPT/
SPT/*
!SPT/user/
SPT/user/*
!SPT/user/mods/
!SPT/user/profiles/
SPT/user/profiles/backups/
!BepInEx/
BepInEx/*
!BepInEx/plugins/
!BepInEx/config/
```

Git-Fallstrick: Ein ignorierter Ordner lässt sich nicht per `!` für Unterinhalte öffnen, daher die `ordner/*`-Zeilen. Testen, dass `SPT/user/logs/`, `SPT/user/cache/` und alle Spieldaten draußen bleiben.

Commit:

```
mod: SAIN 3.2.1

Mod: SAIN
Mod-Version: 3.2.1
Mod-Type: hybrid
Mod-Author: Solarint
Mod-SPT: ~4.1.0
Mod-Source: https://github.com/Solarint/SAIN/releases/download/3.2.1/SAIN-3.2.1.7z
Mod-Forge-Id: 1234
Mod-Forge-Version-Id: 56789
Mod-Guid:
Mod-State: installed
```

`Mod-Source` ist die Download-URL (bei `--file` der lokale Dateiname). `Mod-Forge-Id`/`Mod-Forge-Version-Id` leer, wenn kein Match. Bei Updates zusätzlich `Mod-Previous-Version`. `Mod-State` ∈ `installed | updated | disabled | enabled | removed`. Trailer per `git interpret-trailers` schreiben und per `git log --format=%(trailers:key=Mod,valueonly)` lesen, nicht per Regex über den Body. `Mod:` ist der Identifier; Matching case-insensitive, bei Mehrdeutigkeit Fehler mit Auswahl.

## 6. Konflikt- und Dependency-Prüfung bei `install`

Vor dem Kopieren für jede Archivdatei: existiert sie im Spielverzeichnis? Wenn getrackt: Mod ermitteln, der sie zuletzt angefasst hat (`git log -1 --format=%(trailers:key=Mod,valueonly) -- PATH`) → „wird bereits von Mod Y bereitgestellt". Wenn untracked: „manuell vorhanden, erst `adopt`". Beides ohne `--force` Abbruch.

Zusätzlich DLL-Duplikate: gleicher Dateiname einer `.dll` irgendwo unter `BepInEx/plugins/` in einem anderen Mod-Ordner → Warnung (typisch: mitgelieferte Shared-Libraries wie `Newtonsoft.Json.dll`).

Dependencies: `package.json` → `dependencies` (Dict Name→Semver-Range). Für jede: aktiver Mod mit diesem Namen vorhanden und Version in Range? Fehlend → Warnung mit Liste, ohne `--force` Abbruch. `sptVersion`-Range gegen erkannte SPT-Version prüfen, Mismatch ist Warnung, kein Abbruch (Ranges sind in der Praxis oft zu eng).

## 7. Profil-Check bei `remove` / `check`

Aus dem Install-Commit des Mods (nicht vom Dateisystem – `git show HASH:PATH`) alle `SPT/user/mods/*/db/**/*.json` lesen und IDs sammeln: Item-Template-IDs (Keys in `templates/items.json` o. ä., 24-Hex), Trader-IDs (Ordnernamen unter `db/traders/` bzw. `_id` in `base.json`), Quest-IDs (`_id` in Quest-JSONs). Dann jedes Profil durchsuchen: `Inventory.items[].tpl`, `TradersInfo`-Keys, `Quests[].qid`, plus konservativer String-Fallback über das gesamte Profil. Fund → Warnung mit Zählung pro Profil und Kategorie, ohne `--force` Abbruch. Mit `--force`: Revert durchführen, Profile unangetastet. `db/`-Strukturen variieren zwischen Mods – heuristisch und tolerant parsen, bei Parsing-Fehlern warnen statt abbrechen.

## 8. Projektstruktur

```
sptmod/
  pyproject.toml            # uv, [project.scripts] sptmod = "sptmod.cli:main"
  src/sptmod/
    core/
      errors.py             # SptmodError → NotAGameDirError, DirtyTreeError, GameRunningError,
                            #   ConflictError, DependencyError, ProfileResidueError, ArchiveError, ...
      models.py             # Dataclasses: ModInfo, ModFile, Conflict, ProfileResidue, InstallPlan, ...
      game_dir.py           # Verzeichnis erkennen, SPT-Version, Prozess-Check, Pfadkonstanten
      archive.py            # entpacken (.7z/.zip/.rar), Traversal-Check, Ankerpunkt-Suche, Dateiliste
      modmeta.py            # package.json lesen, Fallbacks, Trailer bauen/parsen
      repo.py               # DIE Git-Schicht: Repo mit init/commit/revert/cherry_pick/log/show_files/
                            #   last_touching/status/is_clean/untracked_under. Einzige Stelle mit subprocess.
      profile.py            # Profil laden, ID-Sammlung aus db/, Residue-Suche
      conflicts.py          # Datei-, DLL- und Dependency-Konflikte
      forge/
        client.py           # HTTP-Client: Auth-Header, Token-Bucket, 429-Backoff, Paginierung. Einzige Stelle mit httpx.
        auth.py             # login/logout, Token via keyring (Fallback: Datei 0600)
        catalog.py          # SQLite-Cache: sync (voll/delta), search, get_versions, resolve_version
        download.py         # Streaming-Download mit Progress und Größenprüfung nach Temp
        resolve.py          # Install-Plan: gewünschte Version + Dependencies über /mods/dependencies
      services.py           # install/remove/list/browse/update/... als reine Funktionen über den Modulen oben.
                            #   Progress via injiziertem Callback. Forge-Install = resolve → download → Install-Kern.
    cli.py                  # typer; übersetzt Exceptions in Exit-Codes, rich-Tabellen
    server/
      app.py                # FastAPI; ein Endpunkt pro Service-Funktion, gleiche Namen
      schemas.py            # Pydantic-Modelle = API-Vertrag, aus core.models abgeleitet
      events.py             # SSE für Progress
  frontend/                 # SvelteKit, Svelte 5 (Runes), adapter-static, TypeScript
    src/lib/api/transport.ts   # call()/subscribe() – einzige Stelle mit fetch/invoke
    src/lib/api/types.ts       # generiert aus openapi.json, nicht von Hand
    src/lib/api/client.ts      # typisierte Wrapper: installMods(), listMods(), ...
    src/routes/                # Seiten: Setup (Pfad, Login), Mods (drei Tabs), History, Profile, Einstellungen
  tests/
```

Regeln: `subprocess` nur in `repo.py`, HTTP nur in `forge/client.py`. Keine Ausgabe außerhalb von `cli.py` und `server/`. Typannotationen überall, `mypy --strict`. Kein `GitPython` – rohe Git-Aufrufe sind für den Port nachvollziehbarer. Forge-Cache ist Fremd-Zustand und liegt im Konfigverzeichnis (`platformdirs`), nie im Spielverzeichnis oder in Git. Frontend: adapter-static, damit der gebaute Output später unverändert von Tauri gehostet werden kann; kein SSR, keine SvelteKit-Server-Routes.

Tooling: Python 3.12, `uv`, `ruff`, `mypy --strict`, `pytest`, `respx` für HTTP-Mocks. Frontend: `pnpm`, `vite`, `svelte-check`, `vitest` für die Transport- und Client-Schicht, `openapi-typescript` im Build-Schritt. Abhängigkeiten minimal: `typer`, `rich`, `py7zr`, `rarfile`, `psutil`, `httpx`, `keyring`, `platformdirs`, `fastapi`, `uvicorn`, `sse-starlette`.

## 9. Tests

Die Testsuite ist die Spezifikation für den Rust-Port und deshalb wichtiger als das Frontend. Kein echtes Spiel nötig.

Fixtures: `fake_game_dir(tmp_path)` baut ein SPT-Verzeichnis mit `SPT/user/mods`, `SPT/user/profiles/abc.json` (Minimalprofil mit Inventory/TradersInfo/Quests), `SPT/SPT_Data/configs/core.json`, `BepInEx/plugins`, `BepInEx/config`, plus Dummy-Dateien an ignorierten Stellen (`SPT/user/logs/x.log`, `EscapeFromTarkov_Data/foo`). `make_archive(tmp_path, {pfad: inhalt}, format)` erzeugt `.7z`/`.zip`/`.rar` (rar nur, wenn Backend vorhanden, sonst skip).

Pflichtfälle:

- init: Whitelist korrekt; ignorierte Dummys nicht im Commit, Profil schon; zweites `init` abgelehnt; vorhandene Mods → Hinweis auf adopt.
- archive: Ankerpunkt-Suche für alle Varianten (korrekt, Wrapper-Ordner, ohne `SPT/`-Präfix, ohne `BepInEx/`-Präfix, Groß-/Kleinschreibung, mehrere Wrapper); Path-Traversal abgelehnt; ungültige Struktur mit hilfreicher Meldung abgelehnt.
- install: server-only, client-only, hybrid; Trailer vollständig und korrekt; dirty tree abgelehnt; laufendes Spiel (gemockt) abgelehnt; Progress-Callback wird in erwarteter Reihenfolge gerufen.
- conflicts: Datei von anderem Mod → Abbruch ohne `--force`, Meldung nennt den Mod; untracked Datei → Hinweis auf adopt; DLL-Duplikat → Warnung; fehlende Dependency → Abbruch; Dependency mit falscher Version → Abbruch; `sptVersion`-Mismatch → nur Warnung.
- remove: sauberer Revert; Revert-Konflikt durch späteren Mod → verständlicher Abbruch, `git status` danach clean.
- disable/enable: Dateien verschwinden und kommen wieder; `list` zeigt Zustand korrekt; `list --all` zeigt entfernte; enable eines nie installierten Mods → Fehler; doppelt disable → No-op mit Hinweis.
- adopt: Ordner mit `package.json`; DLL ohne Metadaten mit Override; danach in `list` und `files` sichtbar.
- profile: Fake-Mod mit `db/templates/items.json` und Trader; Profil mit einer dieser IDs im Inventory → `remove` bricht ab, Meldung zählt pro Kategorie; `--force` revertet; `check` findet dasselbe ohne Änderung; kaputtes db-JSON → Warnung, kein Abbruch.
- export/import: Roundtrip; import gegen abweichenden Stand liefert korrekte Differenz; `--apply` installiert fehlende über den (gemockten) Forge-Pfad.
- snapshot: nur Profile/Configs committet, Mod-Dateien unberührt.
- forge/client (alles gegen `respx`-Mocks, nie gegen den echten Forge): Auth-Header gesetzt; Token-Bucket drosselt; 429 → Backoff und Retry; Paginierung vollständig durchlaufen; Netzfehler → typisierte `ForgeUnavailableError`, Cache bleibt nutzbar.
- forge/auth: login speichert Token im gemockten keyring und nie das Passwort; logout löscht; kein keyring verfügbar → Datei-Fallback mit 0600 und Warnung.
- forge/catalog: Voll-Sync füllt SQLite; Delta-Sync ändert nur Geänderte; search findet nach Name/Autor; Kompatibilitätsfilter gegen SPT-Version; `browse` joint Cache mit Git-History korrekt (installiert/verfügbar/Update).
- forge/download: Streaming mit Progress-Callbacks; `content_length`-Mismatch → Fehler und Temp gelöscht; Dateiname aus Content-Disposition, Fallback URL.
- forge/resolve: Plan enthält Mod und alle fehlenden Dependencies in Installationsreihenfolge; bereits installierte Dependencies werden ausgelassen; unauflösbare Dependency → Fehler mit Name.
- install (Forge-Pfad): End-to-End mit gemocktem Forge und Fake-Archiv im Download-Mock: Commit hat `Mod-Forge-Id`, `Mod-Forge-Version-Id`, `Mod-Source` = URL; Dependencies als eigene Commits davor.
- update: neue Version ersetzt alte Dateien, entfernte Dateien der alten Version verschwinden, `BepInEx/config/` bleibt, Trailer `Mod-Previous-Version` gesetzt.
- API: jeder Endpunkt gegen den Service getestet (TestClient); Fehler → HTTP-Status + strukturierter Fehlerkörper `{error, code, details}`; OpenAPI-Schema wird erzeugt und enthält alle Endpunkte; Login-Endpunkt gibt nie das Token zurück.
- Frontend: `transport.ts` mit gemocktem fetch; `client.ts` typkorrekt gegen generierte Typen (`svelte-check` im CI); Tab-Filterlogik (alle/installiert/nicht installiert × kompatibel) als reine Funktion getestet.

## 10. Reihenfolge

Keine Zeitplanung, nur Abhängigkeitsreihenfolge – jeder Schritt ist testbar, bevor der nächste beginnt.

1. Projektgerüst, `core/repo.py` mit Tests – das ist die Schicht, die im Port ersetzt wird, ihre Schnittstelle muss die sauberste sein.
2. `archive.py` mit Ankerpunkt-Suche und allen Strukturvarianten als Tests.
3. Install-Kern, `init` (inkl. Auto-Adopt), `list`, `files`, `remove`, `history`, `install --file` – CLI.
4. Konfliktprüfung, Dependencies aus `package.json`, Profil-Check.
5. `disable`/`enable`, `adopt`, `status`, `snapshot`, `check`, `export`/`import`.
6. `forge/`: client → auth → catalog → download → resolve, alles gegen Mocks. Dann `login`, `sync`, `search`, `browse`, `install` (Forge), `update`, `import --apply`.
7. FastAPI-Adapter über die fertigen Services, SSE, OpenAPI-Schema, Typ-Generierung.
8. Svelte-Frontend: Transport-Schicht, Setup-Flow, Mods-Ansicht mit drei Tabs und Inline-Progress, History, Profile, Einstellungen.
9. README: Warnhinweis (Prototyp, Spielverzeichnis vorher sichern), Erststart, Migration bestehender Installationen, Hinweis, dass ein Forge-Account nötig ist und wofür das Token gespeichert wird.

Bei Unklarheiten zur SPT-Struktur: offizielles Wiki unter https://github.com/sp-tarkov/wiki (Installing_Mods.md, Profiles.md). Forge-API: https://forge.sp-tarkov.com/docs/index.html – Feldnamen dort verifizieren, nicht aus diesem Dokument übernehmen. Referenz für Verhalten bestehender Manager: https://github.com/Nevek20/SPT_Mod_Manager. Nicht raten – nachschauen oder nachfragen.
