#!/usr/bin/env bash
# Build the non-activating Resolve activity HUD and click shield.

set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: build_resolve_activity_overlay.sh" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$(uname)" != "Darwin" ]; then
  echo "build_resolve_activity_overlay: AppKit is available only on macOS" >&2
  exit 4
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "build_resolve_activity_overlay: swiftc is not installed" >&2
  exit 4
fi

OUTPUT="$REPO_ROOT/native/target/release/resolve-activity-overlay"
mkdir -p "$(dirname "$OUTPUT")"
swiftc -O \
  "$REPO_ROOT/native/resolve-activity-overlay/main.swift" \
  -framework AppKit -framework ApplicationServices -framework CoreGraphics \
  -o "$OUTPUT"
codesign --force -s - --identifier com.ren.resolve-activity-overlay "$OUTPUT"
echo "build_resolve_activity_overlay: built and signed $OUTPUT" >&2
