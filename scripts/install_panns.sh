#!/usr/bin/env bash
#
# Install the PANNs sound-event checkpoint ONCE PER MACHINE.
#
#     scripts/install_panns.sh           # install (or verify) and report
#     scripts/install_panns.sh --check   # report, install nothing
#
# See `library/tools/shared_environment.py` (the sound-events half) for
# the resolution this script obeys - it ASKS that module where the
# checkpoint lives, which release it is and what its md5 is, rather
# than deciding, so the two cannot drift.
#
# WHAT IT DOES
#
#   1. Fetches the PINNED `Cnn14_DecisionLevelMax_mAP=0.385.pth`
#      weights (327 MB) from the Zenodo record into
#      `<vep_home>/models/panns/`, unless they are already there with
#      the recorded md5. The weights are data, not code: the pip half
#      (`panns-inference` + `torchlibrosa`, both MIT) lives in the
#      shared ML venv via requirements.txt.
#   2. Verifies the md5 and runs the pipeline's own measurement over a
#      2 s synthetic fixture, so a checkpoint that does not load, or a
#      venv half that does not import, FAILs here instead of mid-index.
#      The fixture asserts plumbing, not detection - a pure tone is a
#      weak stimulus for an AudioSet model and crosses no operating
#      threshold by design. Detection itself was measured on the
#      captain's real audio (see the measurement in
#      `library/tools/analysis/sound_event_pipeline.py`), and the
#      install reuses its operating points rather than re-deriving
#      them.
#
# Licence: CC-BY-4.0 (the Zenodo record 3987831 licence - commercial
# use with attribution; see `library/tools/analysis/
# sound_event_pipeline.py`). Removal is one command: `rm <checkpoint
# path>` - the path is printed by `--check`.
#
# It is FAIL-CLOSED and it does not trust its own exit codes alone: the
# last thing it does is re-ask `shared_environment` whether PANNs is
# now reachable, and it reports PASS only on that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_PANNS_PYTHON:-python3}"

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

PANNS_CHECKPOINT="$(ask 'se.panns_checkpoint()')"
PANNS_URL="$(ask 'se.PANNS_DOWNLOAD_URL')"
PANNS_MD5="$(ask 'se.PANNS_CHECKPOINT_MD5')"

echo "PANNS CHECKPOINT"
echo "  checkpoint : $PANNS_CHECKPOINT"

md5_of() {
    if command -v md5 >/dev/null 2>&1; then
        md5 -q "$1"
    else
        md5sum "$1" | awk '{print $1}'
    fi
}

if [ "$CHECK_ONLY" = 1 ]; then
    if [ -f "$PANNS_CHECKPOINT" ] && [ "$(md5_of "$PANNS_CHECKPOINT")" = "$PANNS_MD5" ]; then
        echo "PANNS CHECKPOINT: PRESENT (md5 verified)"
        exit 0
    fi
    echo "PANNS CHECKPOINT: ABSENT - run $0 to install"
    exit 1
fi

if [ -f "$PANNS_CHECKPOINT" ] && [ "$(md5_of "$PANNS_CHECKPOINT")" = "$PANNS_MD5" ]; then
    echo "  checkpoint : already installed, md5 verified, reusing"
else
    echo "  checkpoint : fetching $PANNS_URL"
    mkdir -p "$(dirname "$PANNS_CHECKPOINT")"
    curl -fsSL -o "$PANNS_CHECKPOINT" "$PANNS_URL"
    echo "  md5        : $(md5_of "$PANNS_CHECKPOINT")"
    if [ "$(md5_of "$PANNS_CHECKPOINT")" != "$PANNS_MD5" ]; then
        echo "PANNS CHECKPOINT: FAIL - md5 mismatch (expected $PANNS_MD5)" >&2
        exit 1
    fi
    echo "  md5        : verified"
fi

# ── prove it is a working detector, not just bytes ────────────────────
FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT
"$PYTHON" -c "
import math, struct, wave
rate = 32000
samples = []
for i in range(2 * rate):
    t = i / rate
    burst = 0.6 * math.sin(2 * math.pi * 2000.0 * t) if 0.5 <= t < 1.5 else 0.0
    samples.append(burst)
packed = struct.pack(f'<{len(samples)}h',
                     *(int(max(-1.0, min(1.0, v)) * 32767.0) for v in samples))
with wave.open('$FIXTURE_DIR/probe.wav', 'wb') as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(rate)
    handle.writeframes(packed)
"
PROBE_OUT="$(cd "$REPO_ROOT" && "$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from library.tools.analysis import sound_event_pipeline as sep
result = sep.measure_sound_events('$FIXTURE_DIR/probe.wav')
print(len(result['events']), result['method'])
print(sorted({e['label'] for e in result['events']}))
")"
echo "  smoke  : $PROBE_OUT"

# ── verdict, from the resolution rather than from exit codes ──────────
if [ "$(ask 'se.panns_available()[0]')" != "True" ]; then
    echo "PANNS CHECKPOINT: FAIL - shared_environment still reports it unreachable" >&2
    echo "$(ask 'se.panns_missing_message()')" >&2
    exit 1
fi
echo "PANNS CHECKPOINT: PASS"
