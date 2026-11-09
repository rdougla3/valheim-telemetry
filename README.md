# Valheim server and world dashboard

A Docker-based Valheim dedicated server with a self-hosted dashboard for player
activity, world progression, building, survival, movement, farming and observed
combat. The server-side Valheim Observer plugin supplies live telemetry; players
use ordinary Valheim clients.

This repository contains source code and configuration templates. Game binaries,
BepInEx, world saves, player history, passwords and downloaded tools are installed
or created locally. Server identity, branding and deployment settings live in the
Git-ignored `.env` file. No existing player identities are seeded into the database.

## Requirements

- An x86-64 Linux host running systemd-journald, Docker Engine and Docker Compose v2.
- Python 3.11 or later on the host for initial configuration and source tests.
- Network access to Steam, Thunderstore and the Microsoft container registry for installation.
- Enough disk space for the dedicated server, downloaded tools, world saves and backups.

The parser supports committed chunked world formats 40–42. The observer's
version-sensitive gameplay adapters target **Valheim 1.0.20**; they disable
themselves on other versions while leaving identity/clock heartbeats available.
Rebuild and check compatibility after a game update. Optional third-party
simulation/gameplay mods are not included.

## 1. Create local configuration

From the repository root:

```bash
python3 scripts/configure.py
```

This creates empty, ignored runtime directories and a private `.env` with a
random password, your numeric UID/GID, and the host journal group. It preserves an
existing `.env`. Edit that file before starting the server:

| Setting | Purpose |
| --- | --- |
| `SERVER_NAME` | Name advertised in the server browser; default dashboard title |
| `WORLD_NAME` | Save name, matching the directory under `worlds_local/` |
| `SERVER_PASSWORD` | Server password; at least five characters |
| `SERVER_PORT` | Base game UDP port, normally 2456 |
| `SERVER_PUBLIC` | Server-browser visibility: 0 or 1 |
| `CROSSPLAY` | `true` enables the PlayFab relay; `false` uses Steam networking |
| `DASHBOARD_BRAND`, `DASHBOARD_TITLE` | Display branding; an empty title follows `SERVER_NAME` |
| `DASHBOARD_SUBTITLE`, `DASHBOARD_DESCRIPTION` | Page subtitle and description |
| `DASHBOARD_TIMEZONE`, `DASHBOARD_LOCALE` | IANA timezone and browser locale for displayed dates/numbers |
| `DASHBOARD_BIND`, `DASHBOARD_PORT` | Host dashboard address and TCP port; defaults to localhost:8080 |
| `DASHBOARD_URL_PREFIXES` | Optional comma-separated URL prefixes, such as `/valheim` |
| `VALHEIM_CONTAINER_NAME` | Docker name used by journal ingestion; unique per host |
| `VALHEIM_JOURNAL_USER_UNIT` | Optional old user service name when importing retained journal history |
| `RUN_UID`, `RUN_GID`, `JOURNAL_GID` | Host file ownership and read-only journal access |
| `TZ` | Container timezone |
| `BEPINEX_VERSION`, `BEPINEX_SHA256` | BepInEx package version and expected download checksum |
| `DOTNET_IMAGE` | .NET SDK image used to build the observer |

Use quoted literal values in `.env`, as shown in `.env.example`; do not use shell
expansions or executable shell statements. Never commit `.env`, post expanded
`docker compose config` output, or copy live data into a source release. The
example password is deliberately rejected by the launch script.

## 2. Install the game and BepInEx

```bash
docker compose build valheim
docker compose run --rm --no-deps valheim /bin/bash /opt/valheim/update-server.sh
docker compose run --rm --no-deps valheim /bin/bash /opt/valheim/install-bepinex.sh
```

SteamCMD downloads the dedicated server anonymously into `valheim/server/`.
The BepInEx installer downloads the configured upstream package, verifies its
SHA-256, and installs it into that directory. No game server is started by these
installation commands. BepInEx's own generated configuration stays inside the
ignored `valheim/server/BepInEx/config/` directory.

The template pins BepInExPack Valheim 5.4.2351. If changing versions, obtain and
verify the matching upstream checksum and confirm its Doorstop launch variables
are compatible with `valheim/start-server.sh`.

## 3. Build the observer

```bash
./scripts/build-observer.sh
```

This runs the .NET SDK in a temporary container, references your separately
installed game/BepInEx assemblies, and installs the resulting observer DLL under
`valheim/server/BepInEx/plugins/ValheimObserver/`. Downloaded assemblies and build
products are ignored. No runtime player data is required to compile the plugin.

For a local SDK instead, run the following and copy the resulting DLL into the
same plugin directory:

```bash
dotnet build valheim-dashboard/plugin/ValheimObserver.csproj -c Release
```

The project defaults to the sibling `valheim/server/` directory. Override that
with `-p:ValheimServerDir=/absolute/path/to/server` when using another installation.

## 4. Start the services

```bash
docker compose up -d
docker compose ps
docker compose logs -f valheim
```

The first world generation can take several minutes. The dashboard starts once
the observer heartbeat is healthy. Open **http://localhost:8080/**, or the address
and port configured in `.env`. Initial world metrics appear after a committed
world save exists. Its API is `GET /api/dashboard`.

The game uses host networking. Allow the configured game UDP port and its next
port through the host/router when using direct connections. Crossplay uses the
PlayFab relay; get the current join code from `docker compose logs valheim` after
registration. Join codes can change at every restart. In crossplay mode, use the
relay join code/server browser rather than a loopback or local-IP connection.

The dashboard binds to localhost by default. For LAN access, set
`DASHBOARD_BIND=0.0.0.0`, then run `docker compose up -d`. It displays player names
and activity without authentication; use a suitable authenticated reverse proxy
when exposing it to others. A reverse proxy can forward `/valheim/` to port 8080
without stripping that prefix; configure other prefixes in `.env`.

Both containers use `unless-stopped` restart policies. Enable Docker at boot:

```bash
sudo systemctl enable --now docker
```

Auto-login is unnecessary. Manually stopped containers stay stopped after a
reboot; run `docker compose up -d` to restore them. Valheim receives SIGINT and
has 120 seconds to save. Set the Docker daemon's `shutdown-timeout` to at least
150 seconds (merge it into `/etc/docker/daemon.json`) and its systemd stop timeout
to at least 180 seconds so host shutdown also allows that save window. See
[Docker daemon configuration](https://docs.docker.com/reference/cli/dockerd/).

## Import an existing world

Stop both containers before copying saves:

```bash
docker compose stop dashboard valheim
```

Copy the complete committed world directory to
`valheim/data/worlds_local/<WORLD_NAME>/`, then set `WORLD_NAME` in `.env` to that
name. For older, unchunked save formats, let the compatible game server upgrade
the world before expecting dashboard metrics. Keep a separate backup of the
original save. Restore ownership to `RUN_UID:RUN_GID` if you copied as another
user; the containers need write access to the world and dashboard data.

```bash
docker compose up -d
```

## Routine management

```bash
# Gracefully stop both services; wait for the save to finish.
docker compose stop dashboard valheim

# Restart both after an observer/game change.
docker compose restart valheim dashboard

# Rebuild/recreate after dashboard source or deployment-config changes.
docker compose build valheim
docker compose up -d
```

For a game update, stop both services, back up their data, run the SteamCMD
installation command from step 2, rebuild the observer, then run `docker compose
up -d`. Updates are explicit; the server never downloads a new game version at
startup. A container's automatic restart does not restart its Compose dependents,
so name both services when performing maintenance.

## Backups

For a consistent backup, stop both services and archive the local state:

```bash
docker compose stop dashboard valheim
umask 077
tar -czf "valheim/backups/backup-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" \
  .env valheim/data valheim/server/BepInEx valheim-dashboard/data
docker compose up -d
```

Backups contain credentials and player information; keep them private. Preserve
the complete dashboard data directory, including SQLite WAL files if present.
Never delete runtime directories while updating source. Existing source releases
should not include backups, logs, telemetry, screenshots or save files.

## Development and source checks

```bash
cd valheim-dashboard
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
cd ..
python3 scripts/check-publication.py
```

Tests generate synthetic save fixtures in temporary directories and use fictional
account IDs outside the real Steam user-ID range. Production databases start with
no identities. No previous deployment's Git history, runtime files, credentials,
logs, personal domains, host paths or player records are included.

The dashboard uses Python's standard library, SQLite and self-hosted fonts. It
reads game saves and host journals without mounting the Docker socket.
`VALHEIM_LIVE_PATH` configures the observer heartbeat output; Compose sets it to
the shared dashboard data directory. Advanced native deployments can override
`VALHEIM_SERVER_DIR`, `VALHEIM_SAVE_DIR`, `VALHEIM_DASHBOARD_DATA`,
`VALHEIM_WORLD_PATH`, `VALHEIM_JOURNAL_CONTAINER`, and `VALHEIM_ENV_FILE` in the
ignored `.env` or process environment.

See [metric definitions](docs/METRICS.md) and
[third-party notices](valheim-dashboard/THIRD_PARTY.md) for scope and attribution.
