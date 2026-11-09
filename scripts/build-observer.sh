#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo 'Run python3 scripts/configure.py first' >&2; exit 1; }
set -a
source .env
set +a
[[ -f valheim/server/BepInEx/core/BepInEx.dll ]] || { echo 'Install the game and BepInEx first' >&2; exit 1; }
docker run --rm --user "${RUN_UID:-$(id -u)}:${RUN_GID:-$(id -g)}" \
    -e DOTNET_CLI_HOME=/tmp/dotnet -e HOME=/tmp/dotnet \
    -v "$ROOT:/work" -w /work/valheim-dashboard/plugin \
    "${DOTNET_IMAGE:-mcr.microsoft.com/dotnet/sdk:8.0}" \
    dotnet build ValheimObserver.csproj -c Release -p:ValheimServerDir=/work/valheim/server
install -d -m 700 valheim/server/BepInEx/plugins/ValheimObserver
install -m 600 valheim-dashboard/plugin/bin/Release/netstandard2.1/ValheimObserver.dll \
    valheim/server/BepInEx/plugins/ValheimObserver/ValheimObserver.dll
echo 'Observer installed. Start or restart both services when ready.'
