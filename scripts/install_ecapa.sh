#!/usr/bin/env bash
#
# Install the ECAPA speaker-encoder checkout ONCE PER MACHINE.
#
#     scripts/install_ecapa.sh           # install (or verify) and report
#     scripts/install_ecapa.sh --check   # report, install nothing
#
# See `library/tools/shared_environment.py` (the ECAPA half) for the
# resolution this script obeys - it ASKS that module which repo, which
# revision and where the checkout lives, rather than deciding, so the
# two cannot drift.
#
# WHAT IT DOES
#
#   1. Fetches the PINNED `speechbrain/spkrec-ecapa-voxceleb` checkout
#      (~90 MB) from HuggingFace at `ECAPA_REVISION` into
#      `<vep_home>/models/ecapa/spkrec-ecapa-voxceleb/`, unless it is
#      already there with the required files. Public repo, no token.
#      The weights are data, not code: the pip half (`speechbrain`,
#      MIT) lives in the shared ML venv via requirements.txt.
#   2. Runs the pipeline's own embedder over a 2 s synthetic fixture,
#      so a checkout that does not load, or a venv half that does not
#      import, FAILs here instead of mid-transcript. The fixture asserts
#      plumbing, not discrimination - a pure tone is one voice by
#      construction and crosses no speaker boundary by design.
#      Discrimination itself was measured on the captain's real audio
#      (see the eval at `data/vep-single-track-diarization/eval/` in
#      firstmate's home), and the install reuses its operating points
#      rather than re-deriving them.
#
# The recipe is speechbrain's (Apache-2.0); the encoder is trained on
# VoxCeleb, whose dataset terms govern the weights - check them for the
# use at hand. Removal is one command: `rm -rf <model dir>` - the path
# is printed by `--check`.
#
# It is FAIL-CLOSED and it does not trust its own exit codes alone: the
# last thing it does is re-ask `shared_environment` whether ECAPA is
# now reachable, and it reports PASS only on that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_ECAPA_PYTHON:-python3}"

CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --*) echo "unknown option: $arg" >&2; exit 2 ;;
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

ECAPA_DIR="$(ask 'se.ecapa_model_dir()')"
ECAPA_REPO="$(ask 'se.ECAPA_REPO')"
ECAPA_REVISION="$(ask 'se.ECAPA_REVISION')"

echo "ECAPA CHECKOUT"
echo "  directory: $ECAPA_DIR"

present() {
    [ -f "$ECAPA_DIR/hyperparams.yaml" ] && [ -f "$ECAPA_DIR/embedding_model.ckpt" ]
}

if [ "$CHECK_ONLY" = 1 ]; then
    if present; then
        echo "ECAPA CHECKOUT: PRESENT (hyperparams + encoder weights)"
        exit 0
    fi
    echo "ECAPA CHECKOUT: ABSENT - run $0 to install"
    exit 1
fi

if present; then
    echo "  checkout : already installed, reusing"
else
    echo "  checkout : fetching $ECAPA_REPO@$ECAPA_REVISION"
    mkdir -p "$ECAPA_DIR"
    "$PYTHON" -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$ECAPA_REPO', revision='$ECAPA_REVISION',
                  local_dir='$ECAPA_DIR')
"
    if ! present; then
        echo "ECAPA CHECKOUT: FAIL - required files missing after fetch" >&2
        exit 1
    fi
    echo "  checkout : fetched"
fi

# ── prove it is a working encoder, not just bytes ────────────────────
FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT
"$PYTHON" -c "
import math, struct, wave
rate = 16000
samples = []
for i in range(2 * rate):
    t = i / rate
    tone = 0.5 * math.sin(2 * math.pi * 220.0 * t) if 0.5 <= t < 1.5 else 0.0
    samples.append(tone)
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
from library.tools import single_track_diarization as std
result = std.diarize_track('$FIXTURE_DIR/probe.wav', num_speakers=1)
emb = result.clusters[0].centroid
import math
norm = math.sqrt(sum(v * v for v in emb))
print(len(emb), round(norm, 4), result.method.split(' ')[0])
")"
echo "  smoke    : $PROBE_OUT"

# ── verdict, from the resolution rather than from exit codes ─────────
if [ "$(ask 'se.ecapa_available()[0]')" != "True" ]; then
    echo "ECAPA CHECKOUT: FAIL - shared_environment still reports it unreachable" >&2
    echo "$(ask 'se.ecapa_missing_message()')" >&2
    exit 1
fi
echo "ECAPA CHECKOUT: PASS"
