#!/usr/bin/env bash
# Build TDLib's self-hosted telegram-bot-api server from source (gzb).
# bus #53128/#53139/#53143: ingest.py can't download Telegram files >20MB
# against the cloud Bot API (hard cap) -- a local server in --local mode
# lifts that to ~2GB. No apt/brew package exists; this builds from source.
#
# STAGED, not yet run for real: everything below EXCEPT the one apt line is
# non-sudo (clones + builds entirely under $HOME, installs to a prefix
# inside the clone -- never /usr/local, so no sudo needed for the build or
# install steps). The ONE sudo step (apt-get install cmake gperf) is
# deliberately split out as its own check so this script can be re-run
# after that one step lands, without re-deriving what's missing by hand.
#
# Usage: scripts/build_telegram_bot_api.sh
# Output: $HOME/build/telegram-bot-api/bin/telegram-bot-api (per upstream's
# own documented layout: https://github.com/tdlib/telegram-bot-api#build-instructions)
set -euo pipefail

BUILD_ROOT="$HOME/build/telegram-bot-api"
CLONE_DIR="$BUILD_ROOT/src"
BIN="$BUILD_ROOT/bin/telegram-bot-api"

missing=()
for tool in cmake gperf g++ make git; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ "${#missing[@]}" -gt 0 ]; then
    echo "build_telegram_bot_api: missing build tool(s): ${missing[*]}" >&2
    echo "  -> needs the gzb sudo password (vault leak-flagged / rotation-unconfirmed, bus #53052 ask #1610 -- do NOT use the flagged cred; wait for rotation):" >&2
    echo "     sudo apt-get update && sudo apt-get install -y ${missing[*]}" >&2
    exit 1
fi

if [ -x "$BIN" ]; then
    echo "build_telegram_bot_api: already built at $BIN -- skipping (delete $BUILD_ROOT to force a rebuild)"
    exit 0
fi

mkdir -p "$BUILD_ROOT"
if [ ! -d "$CLONE_DIR" ]; then
    git clone --recursive https://github.com/tdlib/telegram-bot-api.git "$CLONE_DIR"
fi

cd "$CLONE_DIR"
mkdir -p build
cd build
# Install prefix is the SAME build root, never /usr/local -- the whole
# point is zero further sudo after the one apt step above.
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH="$BUILD_ROOT" ..
cmake --build . --target install -j "$(nproc)"

if [ ! -x "$BIN" ]; then
    echo "build_telegram_bot_api: build finished but $BIN is not there -- check the cmake/build output above" >&2
    exit 1
fi

echo "build_telegram_bot_api: built $BIN"
"$BIN" --version || true
