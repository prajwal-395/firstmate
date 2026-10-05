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
# The Rust toolchain is per-machine (~/.cargo/bin), never assumed: when
# cargo or maturin is absent the script exits 4 and the caller keeps the
# Python backend, which is the default and the fallback anyway. Exit 2
# is a usage mistake, exit 1 a build that ran and failed.

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

# maturin develop installs into the environment it is invoked from; the
# venv need not be activated, only its interpreter first on PATH.
export VIRTUAL_ENV="$(cd "$VENV_BIN_DIR/.." && pwd -P)"
export PATH="$VENV_BIN_DIR:$PATH"
cd "$REPO_ROOT/native/ren-core"
exec maturin develop --locked
