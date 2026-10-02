#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEPS="${PACKER_DEPS_DIR:-$ROOT/.deps}"
BUILD="${PACKER_BUILD_DIR:-$ROOT/build}"
OBS_APP="${OBS_APP:-/Applications/OBS.app}"
ARCH="${PACKER_ARCH:-arm64}"
CMAKE="${CMAKE:-cmake}"
mkdir -p "$DEPS"
fetch() {
  local url="$1" archive="$2" expected="$3" directory="$4" strip="$5"
  if [ ! -f "$archive" ]; then curl --fail --location --retry 2 "$url" --output "$archive"; fi
  local actual
  actual="$(shasum -a 256 "$archive" | cut -d ' ' -f 1)"
  if [ "$actual" != "$expected" ]; then echo "SDK checksum mismatch: $archive" >&2; exit 1; fi
  if [ ! -d "$directory" ]; then mkdir -p "$directory"; tar -xf "$archive" -C "$directory" --strip-components="$strip"; fi
}
fetch 'https://github.com/obsproject/obs-deps/releases/download/2025-07-11/macos-deps-qt6-2025-07-11-universal.tar.xz' "$DEPS/qt.tar.xz" 'd3f5f04b6ea486e032530bdf0187cbda9a54e0a49621a4c8ba984c5023998867' "$DEPS/qt" 0
fetch 'https://github.com/obsproject/obs-studio/archive/refs/tags/31.1.1.tar.gz' "$DEPS/obs.tar.gz" '39751f067bacc13d44b116c5138491b5f1391f91516d3d590d874edd21292291' "$DEPS/obs" 1
"$CMAKE" -S "$ROOT" -B "$BUILD" -G 'Unix Makefiles' -DCMAKE_PREFIX_PATH="$DEPS/qt" -DOBS_SOURCE="$DEPS/obs" -DOBS_APP="$OBS_APP" -DCMAKE_OSX_ARCHITECTURES="$ARCH" -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 -DCMAKE_BUILD_TYPE=Release
"$CMAKE" --build "$BUILD" --parallel 4
printf 'Built plugin: %s/obs-scene-packer.plugin\n' "$BUILD"
