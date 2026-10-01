#!/usr/bin/env bash
#
# Install the insightface `buffalo_l` face-identity pack ONCE PER MACHINE.
#
#     scripts/install_insightface.sh           # install (or verify) and report
#     scripts/install_insightface.sh --check   # report, install nothing
#
# See `library/tools/shared_environment.py` (the insightface half) for
# the resolution this script obeys - it ASKS that module where the
# checkout lives rather than deciding, so the two cannot drift.
#
# WHAT IT DOES
#
#   1. Fetches `buffalo_l` (~281 MB: detector + ArcFace recognizer +
#      landmark/attribute models this pipeline never reads) into
#      insightface's own cache, unless it is already there with the
#      required files. insightface manages this download itself
#      (`FaceAnalysis(name="buffalo_l").prepare()` triggers it) - this
#      script just calls that and reports the result, rather than
#      re-implementing the fetch.
#   2. Runs the recognizer over a tiny synthetic image, so a checkout
#      that does not load, or a venv half that does not import, FAILs
#      here instead of mid-build. The fixture asserts plumbing, not
#      discrimination - a blank frame finding zero faces is a PASS for
#      this smoke check (it proves the model loads and runs), not a
#      claim about detection accuracy.
#
# Identity discrimination itself was measured on the captain's real
# footage (`data/vep-person-entity-store/eval/results.md` in firstmate's
# home: FAR=0/FRR=0 at cosine >= 0.30 across 83 real faces, 2 cameras,
# profile and eyes-closed frames included); the install reuses that
# operating point rather than re-deriving it.
#
# buffalo_l ships under insightface's own license (non-commercial
# research use - see https://github.com/deepinsight/insightface); check
# it for the use at hand. Removal is one command: `rm -rf <model dir>` -
# the path is printed by `--check`.
#
# It is FAIL-CLOSED and it does not trust its own exit codes alone: the
# last thing it does is re-ask `shared_environment` whether insightface
# is now reachable, and it reports PASS only on that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_INSIGHTFACE_PYTHON:-python3}"

CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

ask() {
    ( cd "$REPO_ROOT" && "$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from library.tools import shared_environment as se
print($1)
" )
}

MODEL_ROOT="$(ask 'se.insightface_model_dir()')"
PACK_NAME="$(ask 'se.INSIGHTFACE_PACK_NAME')"

echo "INSIGHTFACE CHECKOUT"
echo "  root: $MODEL_ROOT"
echo "  pack: $PACK_NAME"

if [ "$CHECK_ONLY" = 1 ]; then
    if [ "$(ask 'se.insightface_available()[0]')" = "True" ]; then
        echo "INSIGHTFACE CHECKOUT: PRESENT (detector + recognizer)"
        exit 0
    fi
    echo "INSIGHTFACE CHECKOUT: ABSENT - run $0 to install"
    exit 1
fi

if [ "$(ask 'se.insightface_available()[0]')" = "True" ]; then
    echo "  checkout : already installed, reusing"
else
    echo "  checkout : fetching $PACK_NAME via insightface's own downloader"
    "$PYTHON" -c "
from insightface.app import FaceAnalysis
app = FaceAnalysis(name='$PACK_NAME', root='$MODEL_ROOT',
                   providers=['CPUExecutionProvider'])
app.prepare(ctx_id=-1, det_size=(640, 640))
"
fi

# ── prove it is a working detector+recognizer, not just bytes ────────
SMOKE_OUT="$(cd "$REPO_ROOT" && "$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
import numpy as np
from insightface.app import FaceAnalysis
app = FaceAnalysis(name='$PACK_NAME', root='$MODEL_ROOT',
                   providers=['CPUExecutionProvider'])
app.prepare(ctx_id=-1, det_size=(640, 640))
blank = np.zeros((480, 640, 3), dtype=np.uint8)
faces = app.get(blank)
print('loaded', len(faces), 'faces-on-blank-frame')
")"
echo "  smoke    : $SMOKE_OUT"

if [ "$(ask 'se.insightface_available()[0]')" != "True" ]; then
    echo "INSIGHTFACE CHECKOUT: FAIL - shared_environment still reports it unreachable" >&2
    echo "$(ask 'se.insightface_missing_message()')" >&2
    exit 1
fi
echo "INSIGHTFACE CHECKOUT: PASS"
