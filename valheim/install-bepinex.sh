#!/bin/bash
set -euo pipefail
umask 0077
: "${BEPINEX_VERSION:?Set BEPINEX_VERSION in .env}"
: "${BEPINEX_SHA256:?Set BEPINEX_SHA256 in .env}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="${VALHEIM_SERVER_DIR:-$SCRIPT_DIR/server}"
[[ -f "$SERVER_DIR/valheim_server.x86_64" ]] || { echo 'Install the game server first' >&2; exit 1; }
[[ "$BEPINEX_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid BepInEx version' >&2; exit 1; }
[[ "$BEPINEX_SHA256" =~ ^[[:xdigit:]]{64}$ ]] || { echo 'Invalid BepInEx SHA-256' >&2; exit 1; }
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl --fail --location --retry 3 "https://thunderstore.io/package/download/denikson/BepInExPack_Valheim/$BEPINEX_VERSION/" -o "$tmp/bepinex.zip"
printf '%s  %s\n' "$BEPINEX_SHA256" "$tmp/bepinex.zip" | sha256sum --check --status
unzip -q "$tmp/bepinex.zip" -d "$tmp/unpacked"
cp -a "$tmp/unpacked/BepInExPack_Valheim/." "$SERVER_DIR/"
echo 'BepInEx installed; build and install the observer next.'
