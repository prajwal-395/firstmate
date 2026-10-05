#!/usr/bin/env bash
#
# Build Ren's native front door into an engine tree's bin/ren.
#
#   scripts/build_ren_cli.sh <engine-tree-bin-dir>
#
# Writes <engine-tree-bin-dir>/ren, replacing the bash front door the
# tree copied from the checkout. The binary execs the unchanged Python
# implementation (`python -m ren.cli`), so a tree built this way runs
# exactly as the bash door did.
#
# The Rust toolchain is per-machine (~/.cargo/bin), never assumed: when
# cargo is absent the script exits 4 and the caller keeps the bash
# front door, so packaging never fails for lack of Rust. Exit 2 is a
# usage mistake, exit 1 a build that ran and failed.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: build_ren_cli.sh <engine-tree-bin-dir>" >&2
  exit 2
fi

TARGET_BIN_DIR="$1"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
  echo "build_ren_cli: no cargo on PATH; keeping the bash front door." >&2
  exit 4
fi

cargo build --release --manifest-path "$REPO_ROOT/native/Cargo.toml" --locked
install -m 755 "$REPO_ROOT/native/target/release/ren" "$TARGET_BIN_DIR/ren"
