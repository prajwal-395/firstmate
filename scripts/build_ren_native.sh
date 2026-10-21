#!/usr/bin/env bash
#
# Build the `ren_native` extension module (crate native/ren-core) into
# the machine's per-tree cache, so `import ren_native` works and the
# edit backend has its default Rust implementation.
#
#   scripts/build_ren_native.sh <venv-bin-dir> <cache-dir>
#
# <venv-bin-dir> is the bin/ of the environment whose maturin runs the
# build - the pipeline's shared venv (~/.local/share/vep/venv-py312/bin)
# for a Ren install. <cache-dir> is the per-tree build cache
# (`library/tools/ren_native_cache.py`); the entry is named by the
# source fingerprint computed below, so checkouts on different trees
# never fight over one artifact.
#
# The build NEVER installs into the venv (`maturin develop` did, and
# every checkout's refresh then rebuilt the one shared copy,
# invalidating the others - measured 2026-10-08: 551 stale-rebuild
# lines, zero completions). `maturin build` writes a wheel, and only
# the built `ren_native*.so` is unpacked into the entry; the wheel and
# the cargo target dir stay per-checkout scratch. The caller records
# the fingerprint beside the entry (`native_refresh.record`); a `.so`
# with no record still reads as unbuilt.
#
# The build bakes a fingerprint of the `native/` sources into the
# extension (REN_NATIVE_SOURCE_FINGERPRINT, computed by the one
# implementation in library/tools/ren_native_source.py and read by
# native/ren-core/build.rs), so the importer loads only the entry
# matching its own tree.
#
# The Rust toolchain is per-machine (~/.cargo/bin), never assumed: when
# cargo or maturin is absent the script exits 4 and the caller keeps the
# Python fallback. Exit 2
# is a usage mistake, exit 1 a build that ran and failed. A tree with
# no native/ren-core sources (an engine predating the Rust core) also
# exits 4: there is nothing to build from and nothing to fingerprint.

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: build_ren_native.sh <venv-bin-dir> <cache-dir>" >&2
    exit 2
fi

VENV_BIN_DIR="$1"
CACHE_DIR="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# The build tool, and where it comes from.
#
# The runtime venv carries the RUNTIME's dependencies, and maturin is a
# BUILD tool, not one of them: the locked set has no maturin, so an
# installed engine's venv never had one. The build therefore refused on
# every install ("no maturin in <venv>"), while `ren doctor` told the
# reader to "run ren setup to build it from the engine's Rust sources"
# - a fix that could not work (measured 2026-10-21 on a clean install
# into a throwaway prefix: the Rust backend stayed unbuilt and the
# Python fallback ran).
#
# `uv` is on the machine - the installer requires it - so maturin is
# fetched EPHEMERALLY through it, never installed into the runtime
# venv, at a PINNED version. The pin lives here, beside the build.
MATURIN_VERSION="1.15.0"

export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
    echo "build_ren_native: no cargo on PATH; keeping the Python backend." >&2
    exit 4
fi
if [ -x "$VENV_BIN_DIR/maturin" ]; then
    MATURIN=("$VENV_BIN_DIR/maturin")
elif command -v uv >/dev/null 2>&1; then
    MATURIN=(uvx --from "maturin==$MATURIN_VERSION" maturin)
else
    echo "build_ren_native: no maturin in $VENV_BIN_DIR and no uv to fetch one with; keeping the Python backend." >&2
    exit 4
fi
if [ ! -d "$REPO_ROOT/native/ren-core/src" ]; then
    echo "build_ren_native: no native/ren-core sources under $REPO_ROOT; keeping the Python backend." >&2
    exit 4
fi

# A PACKAGED engine tree is immutable: its version directory must not
# gain a cargo target directory. The build's scratch goes to the
# machine's cache instead, where a later build reuses it. A checkout
# keeps cargo's default (native/target), so the development loop keeps
# its warm build cache.
if [ -f "$REPO_ROOT/ren/_build.py" ]; then
    export CARGO_TARGET_DIR="$CACHE_DIR/cargo-target"
fi

# The fingerprint of the native/ sources, computed by the one Python
# implementation, baked into the extension by build.rs.
FINGERPRINT="$(PYTHONPATH="$REPO_ROOT" "$VENV_BIN_DIR/python3" -c "
import sys
from library.tools.ren_native_source import source_fingerprint
print(source_fingerprint(sys.argv[1]))
" "$REPO_ROOT")"

ENTRY_DIR="$CACHE_DIR/$FINGERPRINT"
mkdir -p "$ENTRY_DIR"

# maturin build writes a wheel; only the extension itself is unpacked
# into the entry. The interpreter runs the build so the wheel targets
# the environment that will load it.
WHEEL_DIR="$ENTRY_DIR/wheels"
rm -rf "$WHEEL_DIR"
mkdir -p "$WHEEL_DIR"
cd "$REPO_ROOT/native/ren-core"
export REN_NATIVE_SOURCE_FINGERPRINT="$FINGERPRINT"
"${MATURIN[@]}" build --release --locked -i "$VENV_BIN_DIR/python3" --out "$WHEEL_DIR"

# Exactly one wheel, exactly one extension inside it.
set -- "$WHEEL_DIR"/*.whl
if [ "$#" -ne 1 ]; then
    echo "build_ren_native: expected one wheel in $WHEEL_DIR, found $#." >&2
    exit 1
fi
"$VENV_BIN_DIR/python3" -c "
import sys, zipfile
wheel, entry = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(wheel) as archive:
    members = [m for m in archive.namelist() if m.endswith('.so')]
    if len(members) != 1:
        raise SystemExit(f'expected one .so in {wheel}, found {len(members)}')
    with archive.open(members[0]) as source, open(entry + '/' + members[0].rsplit('/', 1)[-1], 'wb') as target:
        target.write(source.read())
" "$1" "$ENTRY_DIR"
rm -rf "$WHEEL_DIR"
echo "build_ren_native: built $FINGERPRINT into $ENTRY_DIR." >&2
