#!/usr/bin/env bash
#
# Build the resolve-control driver binary in place, so the Python client
# (library/tools/computer_use/client.py) has its release build beside the
# checkout to spawn. Binaries are never committed; this script is the one
# builder.
#
#   scripts/build_resolve_control.sh
#
# Contract: exit 0 and native/target/release/resolve-control built with
# cargo, exit 4 and nothing built without it, exit 1 when a build ran
# and failed. Exit 2 is a usage mistake.
#
# The Rust toolchain is per-machine (~/.cargo/bin), never assumed: when
# cargo is absent the script exits 4 and the caller keeps whatever
# binary (or refusal naming this script) it already had.

set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: build_resolve_control.sh" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
  echo "build_resolve_control: no cargo on PATH; leaving any existing binary." >&2
  exit 4
fi

cargo build --release -p resolve-control \
  --manifest-path "$REPO_ROOT/native/Cargo.toml" --locked

# Record what was built from, so Ren's native-refresh can tell a stale
# binary from a fresh one without rebuilding. Never fails the build.
python3 "$REPO_ROOT/library/tools/native_refresh.py" record resolve-control \
    --root "$REPO_ROOT" \
    || echo "warning: could not record the resolve-control fingerprint" >&2
