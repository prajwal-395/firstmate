#!/usr/bin/env bash
# Build the ren-graphics-fframes binary.
#
# This is the ONE supported way to build the FFrames runtime. The binary
# is precompiled and shipped; Ren never compiles Rust on a customer's
# machine. The build requires:
#   - Rust toolchain (rustup, stable)
#   - Xcode command line tools (for Metal headers on macOS)
#
# The binary lands at native/target/release/ren-graphics-fframes.
# No GPL or nonfree FFmpeg features are enabled.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NATIVE_DIR="$REPO_ROOT/native"
BINARY_NAME="ren-graphics-fframes"

if ! command -v cargo &>/dev/null; then
    if [[ -f "$HOME/.cargo/env" ]]; then
        . "$HOME/.cargo/env"
    else
        echo "ERROR: cargo not found. Install Rust with:" >&2
        echo "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y" >&2
        exit 1
    fi
fi

echo "Building $BINARY_NAME..."
cd "$NATIVE_DIR"
cargo build --release --locked

BINARY_PATH="$NATIVE_DIR/target/release/$BINARY_NAME"
if [[ ! -f "$BINARY_PATH" ]]; then
    echo "ERROR: build succeeded but binary not found at $BINARY_PATH" >&2
    exit 1
fi

SIZE=$(stat -f%z "$BINARY_PATH" 2>/dev/null || stat -c%s "$BINARY_PATH" 2>/dev/null)
echo "Built $BINARY_PATH ($SIZE bytes)"
echo "Copy it to a directory on PATH, or set PIPELINE_GRAPHICS_RENDERER=fframes"
