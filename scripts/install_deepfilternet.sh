#!/usr/bin/env bash
#
# Install the DeepFilterNet binary ONCE PER MACHINE.
#
#     scripts/install_deepfilternet.sh           # install (or verify) and report
#     scripts/install_deepfilternet.sh --check   # report, install nothing
#
# See `library/tools/shared_environment.py` (the DeepFilterNet half) for
# the resolution this script obeys - it ASKS that module where the
# binary lives and which release it is rather than deciding, so the two
# cannot drift.
#
# WHAT IT DOES
#
#   1. Fetches the PINNED prebuilt `deep-filter` binary for Apple
#      Silicon from the upstream release page into `<vep_home>/bin`,
#      unless it is already there. The bytes are checked against the
#      sha256 pinned in `shared_environment.DEEPFILTER_SHA256` before
#      install AND on reuse - a mismatch refuses, and a fresh mismatch
#      is deleted, never chmodded or run. No Python, no venv, no numpy: the
#      binary is a static Rust build carrying its own model runtime,
#      which is the whole reason it exists - `deepfilternet` pins
#      numpy<2 and `deepfilterlib` 0.5.6 ships no cp312 macOS-arm64
#      wheel, so it cannot live in the shared ML venv
#      (see `library/tools/dialogue_cleanup.py`).
#   2. Runs `deep-filter --version` and cleans a 1 s synthetic 48 kHz
#      fixture through it, so a download that is not a working
#      noise suppressor FAILs here instead of mid-compile. The model
#      weights download on first use (~100 MB); that download is part
#      of this install, not of a run.
#
# Licence: MIT OR Apache-2.0 (LICENSE-MIT and LICENSE-APACHE upstream,
# Rikorose/DeepFilterNet). Removal is one command: `rm <binary path>` -
# the path is printed by `--check`.
#
# It is FAIL-CLOSED and it does not trust its own exit codes alone: the
# last thing it does is re-ask `shared_environment` whether
# DeepFilterNet is now reachable, and it reports PASS only on that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_DEEPFILTERNET_PYTHON:-python3}"

CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# Every path below comes from shared_environment. This script decides none
# of them.
ask() {
    ( cd "$REPO_ROOT" && "$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from library.tools import shared_environment as se
print($1)
" )
}

DEEPFILTER_BINARY="$(ask 'se.deepfilter_binary()')"
DEEPFILTER_URL="$(ask 'se.DEEPFILTER_DOWNLOAD_URL')"
DEEPFILTER_SHA256="$(ask 'se.DEEPFILTER_SHA256')"
DEEPFILTER_VERSION="$(ask 'se.DEEPFILTER_VERSION')"

sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

echo "DEEPFILTERNET BINARY"
echo "  binary : $DEEPFILTER_BINARY"
echo "  pinned : $DEEPFILTER_VERSION (sha256 verified)"

if [ "$CHECK_ONLY" = 1 ]; then
    if [ -x "$DEEPFILTER_BINARY" ]; then
        echo "DEEPFILTERNET BINARY: PRESENT"
        exit 0
    fi
    echo "DEEPFILTERNET BINARY: ABSENT - run $0 to install"
    exit 1
fi

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) ;;
    *) echo "DEEPFILTERNET BINARY: FAIL - no prebuilt binary for $(uname -s)-$(uname -m)" >&2
       echo "Upstream ships prebuilt binaries per platform; see the release page named in" >&2
       echo "shared_environment.DEEPFILTER_DOWNLOAD_URL, or run the Python path" >&2
       echo "(deepfilternet==0.5.6 on Python 3.11) instead." >&2
       exit 1 ;;
esac

if [ -x "$DEEPFILTER_BINARY" ]; then
    echo "  binary : already installed, verifying sha256"
    if [ "$(sha256_of "$DEEPFILTER_BINARY")" != "$DEEPFILTER_SHA256" ]; then
        echo "DEEPFILTERNET BINARY: FAIL - existing binary fails the pinned sha256" >&2
        echo "  It was left in place and nothing was run; remove it yourself and re-run:" >&2
        echo "    rm '$DEEPFILTER_BINARY' && $0" >&2
        exit 1
    fi
    echo "  binary : sha256 verified, reusing"
else
    echo "  binary : fetching $DEEPFILTER_URL"
    mkdir -p "$(dirname "$DEEPFILTER_BINARY")"
    DF_TMP="$(mktemp "$(dirname "$DEEPFILTER_BINARY")/deep-filter-XXXXXX")"
    trap 'rm -f "$DF_TMP"' EXIT
    curl -fsSL -o "$DF_TMP" "$DEEPFILTER_URL"
    if [ "$(sha256_of "$DF_TMP")" != "$DEEPFILTER_SHA256" ]; then
        echo "DEEPFILTERNET BINARY: FAIL - sha256 mismatch (expected $DEEPFILTER_SHA256)" >&2
        echo "  The download was deleted, nothing was installed or run." >&2
        rm -f "$DF_TMP"
        exit 1
    fi
    echo "  sha256 : verified"
    mv "$DF_TMP" "$DEEPFILTER_BINARY"
    chmod +x "$DEEPFILTER_BINARY"
    trap - EXIT
fi

# ── prove it is a working suppressor, not just bytes ──────────────────
echo "  version: $("$DEEPFILTER_BINARY" --version)"
FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT
"$PYTHON" -c "
import math, struct, wave
rate = 48000
samples = []
for i in range(rate):
    t = i / rate
    word = 0.5 * math.sin(2 * math.pi * 440.0 * t) if 0.2 <= t < 0.8 else 0.0
    room = 0.01 * math.sin(2 * math.pi * 120.0 * t)
    samples.append(word + room)
packed = struct.pack(f'<{len(samples)}h',
                     *(int(max(-1.0, min(1.0, v)) * 32767.0) for v in samples))
with wave.open('$FIXTURE_DIR/probe.wav', 'wb') as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(rate)
    handle.writeframes(packed)
"
"$DEEPFILTER_BINARY" "$FIXTURE_DIR/probe.wav" -o "$FIXTURE_DIR/out"
if ! ls "$FIXTURE_DIR"/out/*.wav >/dev/null 2>&1; then
    echo "DEEPFILTERNET BINARY: FAIL - the binary ran and left no stem" >&2
    exit 1
fi
echo "  smoke  : cleaned the 1 s fixture, stem on disk"

# ── verdict, from the resolution rather than from exit codes ──────────
if [ "$(ask 'se.deepfilter_available()[0]')" != "True" ]; then
    echo "DEEPFILTERNET BINARY: FAIL - shared_environment still reports it unreachable" >&2
    echo "$(ask 'se.deepfilter_missing_message()')" >&2
    exit 1
fi
echo "DEEPFILTERNET BINARY: PASS"
