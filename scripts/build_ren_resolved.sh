#!/usr/bin/env bash
# Build the Rust Resolve broker in the shared native target directory.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: build_ren_resolved.sh" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
  echo "build_ren_resolved: no cargo on PATH; leaving any existing binary." >&2
  exit 4
fi

cargo build --release -p ren-resolved \
  --manifest-path "$REPO_ROOT/native/Cargo.toml" --locked

python3 "$REPO_ROOT/library/tools/native_refresh.py" record ren-resolved \
  --root "$REPO_ROOT" \
  || echo "warning: could not record the ren-resolved fingerprint" >&2
