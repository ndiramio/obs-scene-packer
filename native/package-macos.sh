#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD="${PACKER_BUILD_DIR:-$ROOT/build}"
OUTPUT="${1:-$ROOT/dist}"
ARCH="${PACKER_ARCH:-arm64}"
BUNDLE="$BUILD/obs-scene-packer.plugin"
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"
if [ ! -d "$BUNDLE" ]; then echo 'Build the plugin first.' >&2; exit 1; fi
codesign --verify --strict "$BUNDLE"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/scene-packer-package.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/payload"
ditto --norsrc --noextattr --qtn "$BUNDLE" "$STAGE/payload/obs-scene-packer.plugin"
pkgbuild --analyze --root "$STAGE/payload" "$STAGE/components.plist"
if /usr/libexec/PlistBuddy -c 'Print :0' "$STAGE/components.plist" >/dev/null 2>&1; then
  if ! /usr/libexec/PlistBuddy -c 'Set :0:BundleIsRelocatable false' "$STAGE/components.plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c 'Add :0:BundleIsRelocatable bool false' "$STAGE/components.plist"
  fi
fi
pkgbuild --component-plist "$STAGE/components.plist" --root "$STAGE/payload" --identifier com.ndiramio.obs-scene-packer --version 0.2.0 --install-location '/Library/Application Support/obs-studio/plugins' "$OUTPUT/obs-scene-packer-0.2.0-macos-$ARCH.pkg"
ditto -c -k --norsrc --noextattr --qtn --keepParent "$BUNDLE" "$OUTPUT/obs-scene-packer-0.2.0-macos-$ARCH.zip"
(
  cd "$OUTPUT"
  shasum -a 256 "obs-scene-packer-0.2.0-macos-$ARCH.pkg" "obs-scene-packer-0.2.0-macos-$ARCH.zip"
) > "$OUTPUT/SHA256SUMS.txt"
