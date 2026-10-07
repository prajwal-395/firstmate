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

# Pin a stable code identity. Cargo linker-signs ad-hoc with a
# per-build identifier (resolve_control-<hash>), so every rebuild
# looks like a new app to macOS privacy grants: Accessibility and
# Screen Recording approvals do not carry across rebuilds, and
# posted input starts dropping silently while AX keeps working on
# whatever grant survived (measured 2026-10-07). Re-signing ad-hoc
# under a fixed identifier keeps the prompts recognizable and
# auditable; only Developer ID signing (the captain's Apple developer
# membership) would carry grants across rebuilds for good. A failed
# re-sign fails the build on macOS: an unstable identity is the bug
# (other systems skip signing - no TCC there).
if [ "$(uname)" = "Darwin" ]; then
  codesign --force -s - --identifier com.ren.resolve-control \
    "$REPO_ROOT/native/target/release/resolve-control"
  echo "build_resolve_control: signed com.ren.resolve-control (ad-hoc, stable identifier)" >&2
fi

# Record what was built from, so Ren's native-refresh can tell a stale
# binary from a fresh one without rebuilding. Never fails the build.
python3 "$REPO_ROOT/library/tools/native_refresh.py" record resolve-control \
    --root "$REPO_ROOT" \
    || echo "warning: could not record the resolve-control fingerprint" >&2
