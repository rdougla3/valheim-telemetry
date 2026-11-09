#!/bin/bash
set -euo pipefail
umask 0077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VALHEIM_ENV_FILE="${VALHEIM_ENV_FILE:-$SCRIPT_DIR/../.env}"
if [[ -f "$VALHEIM_ENV_FILE" ]]; then
    set -a
    source "$VALHEIM_ENV_FILE"
    set +a
fi
: "${SERVER_NAME:?Set SERVER_NAME in .env}"
: "${WORLD_NAME:?Set WORLD_NAME in .env}"
: "${SERVER_PASSWORD:?Set SERVER_PASSWORD in .env}"
[[ "$SERVER_PASSWORD" != 'CHANGE_ME_BEFORE_STARTING' && ${#SERVER_PASSWORD} -ge 5 ]] || { echo 'Configure a password of at least five characters in .env' >&2; exit 1; }
[[ "$WORLD_NAME" != */* && "$WORLD_NAME" != *\\* && "$WORLD_NAME" != '.' && "$WORLD_NAME" != '..' ]] || { echo 'WORLD_NAME must be a single directory name' >&2; exit 1; }
SERVER_PORT="${SERVER_PORT:-2456}"
[[ "$SERVER_PORT" =~ ^[0-9]+$ ]] && ((SERVER_PORT >= 1024 && SERVER_PORT <= 65534)) || { echo 'SERVER_PORT must be between 1024 and 65534' >&2; exit 1; }
[[ "${SERVER_PUBLIC:-1}" =~ ^[01]$ ]] || { echo 'SERVER_PUBLIC must be 0 or 1' >&2; exit 1; }
SERVER_DIR="${VALHEIM_SERVER_DIR:-$SCRIPT_DIR/server}"
SAVE_DIR="${VALHEIM_SAVE_DIR:-$SCRIPT_DIR/data}"
export VALHEIM_LIVE_PATH="${VALHEIM_LIVE_PATH:-$SCRIPT_DIR/../valheim-dashboard/data/live.json}"
mkdir -p "$SAVE_DIR" "$(dirname -- "$VALHEIM_LIVE_PATH")" "${HOME:?}"
cd "$SERVER_DIR"
[[ -x ./valheim_server.x86_64 ]] || { echo 'Install the game server with update-server.sh first' >&2; exit 1; }
[[ -f ./BepInEx/core/BepInEx.Preloader.dll ]] || { echo 'Install BepInEx with install-bepinex.sh first' >&2; exit 1; }
[[ -f ./BepInEx/plugins/ValheimObserver/ValheimObserver.dll ]] || { echo 'Build and install Valheim Observer first (scripts/build-observer.sh)' >&2; exit 1; }
export SteamAppId=892970
export LD_LIBRARY_PATH="$PWD/doorstop_libs:$PWD/linux64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export DOORSTOP_ENABLED=1
export DOORSTOP_TARGET_ASSEMBLY=./BepInEx/core/BepInEx.Preloader.dll
export LD_PRELOAD="libdoorstop_x64.so${LD_PRELOAD:+:$LD_PRELOAD}"
args=(-nographics -batchmode -name "$SERVER_NAME" -port "$SERVER_PORT" -world "$WORLD_NAME" -password "$SERVER_PASSWORD" -public "${SERVER_PUBLIC:-1}" -savedir "$SAVE_DIR")
case "${CROSSPLAY:-true}" in
    true) args+=(-crossplay);;
    false) ;;
    *) echo 'CROSSPLAY must be true or false' >&2; exit 1;;
esac
printf '%s\n' 'VALHEIM_SERVER_START'
exec ./valheim_server.x86_64 "${args[@]}"
