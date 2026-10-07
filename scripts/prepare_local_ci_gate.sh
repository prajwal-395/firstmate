#!/usr/bin/env bash
# Prepare the native binaries whose tests are part of a Tier-2 full gate.
#
# The merge queue uses disposable checkouts, so ignored native/target
# artifacts are not carried into a run. Share Cargo's build cache outside
# those checkouts, then build the binaries from the exact tree under test.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export REPO_ROOT
STATE_DIR="${REN_CI_STATE:-${HOME}/.ren-ci}"
SHARED_TARGET="${REN_CI_NATIVE_TARGET:-${STATE_DIR}/native-target}"
NATIVE_TARGET="${REPO_ROOT}/native/target"

mkdir -p "${SHARED_TARGET}"
SHARED_TARGET="$(cd "${SHARED_TARGET}" && pwd -P)"
case "${SHARED_TARGET}/" in
  "${REPO_ROOT}/"*)
    echo "refusing Cargo cache inside the disposable checkout: ${SHARED_TARGET}" >&2
    exit 1
    ;;
esac

if [ -L "${NATIVE_TARGET}" ]; then
  CURRENT_TARGET="$(cd "${NATIVE_TARGET}" && pwd -P)" || {
    echo "refusing broken native target link: ${NATIVE_TARGET}" >&2
    exit 1
  }
  if [ "${CURRENT_TARGET}" != "${SHARED_TARGET}" ]; then
    echo "refusing native target link outside the Ren CI cache: ${CURRENT_TARGET}" >&2
    exit 1
  fi
elif [ -e "${NATIVE_TARGET}" ]; then
  if [ ! -d "${NATIVE_TARGET}" ]; then
    echo "refusing non-directory native target path: ${NATIVE_TARGET}" >&2
    exit 1
  fi
  echo "using existing native target directory ${NATIVE_TARGET}"
else
  ln -s "${SHARED_TARGET}" "${NATIVE_TARGET}"
  echo "bound native target cache ${NATIVE_TARGET} -> ${SHARED_TARGET}"
fi

if ! command -v cargo >/dev/null 2>&1; then
  if [ -f "${HOME}/.cargo/env" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.cargo/env"
  fi
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo is not available; install the Rust toolchain before Tier-2" >&2
  exit 1
fi

# Tier-2 owns the heavy-work slot. Keep Rust compilation bounded too.
export CARGO_BUILD_JOBS=2

echo "building ml-worker-client from the tested checkout"
cargo build --manifest-path "${REPO_ROOT}/native/Cargo.toml" \
  --locked --bin ml-worker-client
if [ ! -x "${NATIVE_TARGET}/debug/ml-worker-client" ]; then
  echo "ml-worker-client build left no executable in native/target/debug" >&2
  exit 1
fi

echo "building ren-graphics-fframes from the tested checkout"
"${REPO_ROOT}/scripts/build_fframes_runtime.sh"
if [ ! -x "${NATIVE_TARGET}/release/ren-graphics-fframes" ]; then
  echo "FFrames build left no executable in native/target/release" >&2
  exit 1
fi
"${NATIVE_TARGET}/release/ren-graphics-fframes" --version >/dev/null

echo "CI native capabilities: PASS"
