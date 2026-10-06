#!/usr/bin/env bash
#
# Build the `ren_native` extension module (crate native/ren-core) into a
# Python environment, so `import ren_native` works and the edit backend
# switch (REN_EDIT_BACKEND=rust) has its Rust implementation.
#
#   scripts/build_ren_native.sh <venv-bin-dir>
#
# <venv-bin-dir> is the bin/ of the target environment - the pipeline's
# shared venv (~/.local/share/vep/venv-py312/bin) for a Ren install. The
# build runs under that venv's maturin and installs the extension into
# it with `maturin develop`.
#
# The build bakes a fingerprint of the `native/` sources into the
# extension (REN_NATIVE_SOURCE_FINGERPRINT, computed by the one
# implementation in library/tools/ren_native_source.py and read by
# native/ren-core/build.rs), so `edit_backend.rust()` can refuse a build
# that predates the tree it runs against.
#
# The Rust toolchain is per-machine (~/.cargo/bin), never assumed: when
# cargo or maturin is absent the script exits 4 and the caller keeps the
# Python backend, which is the default and the fallback anyway. Exit 2
# is a usage mistake, exit 1 a build that ran and failed. A tree with
# no native/ren-core sources (an engine predating the Rust core) also
# exits 4: there is nothing to build from and nothing to fingerprint.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: build_ren_native.sh <venv-bin-dir>" >&2
  exit 2
fi

VENV_BIN_DIR="$1"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
    echo "build_ren_native: no cargo on PATH; keeping the Python backend." >&2
    exit 4
fi
if [ ! -x "$VENV_BIN_DIR/maturin" ]; then
    echo "build_ren_native: no maturin in $VENV_BIN_DIR; keeping the Python backend." >&2
    exit 4
fi
if [ ! -d "$REPO_ROOT/native/ren-core/src" ]; then
    echo "build_ren_native: no native/ren-core sources under $REPO_ROOT; keeping the Python backend." >&2
    exit 4
fi

# The fingerprint of the native/ sources, computed by the one Python
# implementation, baked into the extension by build.rs.
FINGERPRINT="$(PYTHONPATH="$REPO_ROOT" "$VENV_BIN_DIR/python3" -c "
import sys
from library.tools.ren_native_source import source_fingerprint
print(source_fingerprint(sys.argv[1]))
" "$REPO_ROOT")"

# maturin develop installs into the environment it is invoked from; the
# venv need not be activated, only its interpreter first on PATH.
export VIRTUAL_ENV="$(cd "$VENV_BIN_DIR/.." && pwd -P)"
export PATH="$VENV_BIN_DIR:$PATH"
export REN_NATIVE_SOURCE_FINGERPRINT="$FINGERPRINT"
cd "$REPO_ROOT/native/ren-core"
exec maturin develop --locked
