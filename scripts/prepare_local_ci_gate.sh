#!/usr/bin/env bash
# Prepare the native binaries whose tests are part of a Tier-2 full gate.
#
# The merge queue uses disposable checkouts, so ignored native/target
# artifacts are not carried into a run. Share Cargo's build cache outside
# those checkouts, then build the binaries from the exact tree under test.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export REPO_ROOT
NATIVE_CACHE="${REN_CI_NATIVE_CACHE:-${HOME}/.cache/ren/native-ci}"
PYTHON_BIN="${1:-${FULL_SUITE_GATE_PYTHON:-python3}}"
PYTHON_BIN="$(command -v "${PYTHON_BIN}")"
mkdir -p "${NATIVE_CACHE}"
NATIVE_CACHE="$(cd "${NATIVE_CACHE}" && pwd -P)"
case "${NATIVE_CACHE}/" in
  "${REPO_ROOT}/"*)
    echo "refusing native cache inside the disposable checkout: ${NATIVE_CACHE}" >&2
    exit 1
    ;;
esac

if ! command -v cargo >/dev/null 2>&1; then
  if [ -f "${HOME}/.cargo/env" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.cargo/env"
  fi
fi
if ! command -v cargo >/dev/null 2>&1 || ! command -v rustc >/dev/null 2>&1; then
  echo "cargo and rustc are required to fingerprint native artifacts" >&2
  exit 1
fi

NATIVE_KEY="$(python3 "${REPO_ROOT}/library/tools/native_ci_cache.py" \
  "${REPO_ROOT}" cargo "${PYTHON_BIN}")"
REN_NATIVE_KEY="$(python3 "${REPO_ROOT}/library/tools/native_ci_cache.py" \
  "${REPO_ROOT}" ren-native "${PYTHON_BIN}")"
SHARED_TARGET="${NATIVE_CACHE}/cargo/${NATIVE_KEY}"
REN_NATIVE_CACHE="${NATIVE_CACHE}/ren-native/${REN_NATIVE_KEY}"
NATIVE_TARGET="${REPO_ROOT}/native/target"
LEGACY_TARGET="${REN_CI_STATE:-${HOME}/.ren-ci}/native-target"
if [ -d "$(dirname "${LEGACY_TARGET}")" ]; then
  LEGACY_TARGET="$(cd "$(dirname "${LEGACY_TARGET}")" && pwd -P)/$(basename "${LEGACY_TARGET}")"
fi

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
    case "${CURRENT_TARGET}/" in
      "${NATIVE_CACHE}/cargo/"*) ;;
      *)
        if [ "${CURRENT_TARGET}" != "${LEGACY_TARGET}" ]; then
          echo "refusing native target link outside the Ren CI cache: ${CURRENT_TARGET}" >&2
          exit 1
        fi
        echo "migrating legacy native target cache ${CURRENT_TARGET}"
        ;;
    esac
    rm "${NATIVE_TARGET}"
    ln -s "${SHARED_TARGET}" "${NATIVE_TARGET}"
    echo "rebound native target cache ${NATIVE_TARGET} -> ${SHARED_TARGET}"
  fi
elif [ -e "${NATIVE_TARGET}" ]; then
  if [ ! -d "${NATIVE_TARGET}" ]; then
    echo "refusing non-directory native target path: ${NATIVE_TARGET}" >&2
    exit 1
  fi
  echo "refusing unkeyed native target directory: ${NATIVE_TARGET}" >&2
  exit 1
else
  ln -s "${SHARED_TARGET}" "${NATIVE_TARGET}"
  echo "bound native target cache ${NATIVE_TARGET} -> ${SHARED_TARGET}"
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

mkdir -p "${REN_NATIVE_CACHE}"
export PIPELINE_REN_NATIVE_CACHE="${REN_NATIVE_CACHE}"
if "${PYTHON_BIN}" -c "
from pathlib import Path
from library.tools import ren_native_cache
root = Path('${REPO_ROOT}')
raise SystemExit(0 if ren_native_cache.select(root) is not None else 1)
"; then
  echo "ren_native cache hit for the tested tree and interpreter"
else
  echo "building ren_native for the tested tree and interpreter"
  "${REPO_ROOT}/scripts/build_ren_native.sh" \
    "$(dirname "${PYTHON_BIN}")" "${REN_NATIVE_CACHE}"
  python3 "${REPO_ROOT}/library/tools/native_refresh.py" record ren_native \
    --root "${REPO_ROOT}"
fi
"${PYTHON_BIN}" -c "
from pathlib import Path
from library.tools import ren_native_cache
from library.tools.ren_native_source import source_fingerprint
root = Path('${REPO_ROOT}')
fingerprint = source_fingerprint(root)
module = ren_native_cache.select(root)
if fingerprint is None or module is None:
    raise SystemExit('ren_native extension is unavailable or stale for the tested tree')
print('ren_native fingerprint:', fingerprint)
"

echo "CI native capabilities: PASS"
