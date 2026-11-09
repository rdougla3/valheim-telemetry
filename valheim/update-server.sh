#!/bin/bash
# Run only while the game server is stopped. Game binaries are never bundled.
set -euo pipefail
umask 0077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="${VALHEIM_SERVER_DIR:-$SCRIPT_DIR/server}"
STEAMCMD_DIR="${VALHEIM_STEAMCMD_DIR:-$SCRIPT_DIR/steamcmd}"
mkdir -p "$SERVER_DIR" "$STEAMCMD_DIR" "${HOME:?}"
if [[ ! -f "$STEAMCMD_DIR/steamcmd.sh" ]]; then
    archive="$(mktemp)"
    trap 'rm -f "$archive"' EXIT
    curl --fail --location --retry 3 https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz -o "$archive"
    tar -xzf "$archive" -C "$STEAMCMD_DIR"
fi
"$STEAMCMD_DIR/steamcmd.sh" +force_install_dir "$SERVER_DIR" +login anonymous +app_update 896660 validate +quit
[[ -x "$SERVER_DIR/valheim_server.x86_64" ]] || { echo 'SteamCMD did not install the server successfully' >&2; exit 1; }
