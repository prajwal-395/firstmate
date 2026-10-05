#!/usr/bin/env bash
#
# Install the Remotion/Node dependencies ONCE PER MACHINE, and bind this
# checkout to them.
#
#     scripts/install_node_deps.sh                  # this checkout
#     scripts/install_node_deps.sh <remotion-dir>   # another one
#     scripts/install_node_deps.sh --check          # report, install nothing
#
# See `docs/SHARED_ENVIRONMENT.md` for why, and
# `library/tools/shared_environment.py` for the resolution this script
# obeys - it ASKS that module where the store is rather than deciding,
# so the two cannot drift.
#
# WHAT IT DOES
#
#   1. Keys the checkout's `package-lock.json` by its sha256.
#   2. `npm ci`s that lockfile into `<store>/<key>/`, unless it is
#      already there.  Two checkouts whose locks agree share the tree;
#      two whose locks differ get separate ones, so a stale tree is not
#      reachable - changing the lock changes the key.
#   3. Symlinks `<remotion-dir>/node_modules` at it.  `node_modules/` is
#      gitignored, so binding never dirties a checkout.
#
# It is FAIL-CLOSED and it does not trust its own exit codes alone: the
# last thing it does is re-ask `shared_environment` whether the
# dependencies are now reachable, and it reports PASS only on that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_NODE_DEPS_PYTHON:-python3}"

CHECK_ONLY=0
TARGET=""
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *)  TARGET="$arg" ;;
    esac
done

if ! (cd "$REPO_ROOT" && REN_ENGINE_ROOT="$REPO_ROOT" \
    PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -m ren.edition check renderer.remotion --action fetch); then
    exit 1
fi

# Every path below comes from shared_environment. This script decides none
# of them.
ask() {
    ( cd "$REPO_ROOT" && "$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from library.tools import shared_environment as ne
d = ${1:-None}
print(getattr(ne, '$2')(d))
" )
}

if [ -n "$TARGET" ]; then
    TARGET="$(cd "$TARGET" && pwd)"
    ARG="'$TARGET'"
else
    ARG="None"
fi

REMOTION_DIR="$(ask "$ARG" remotion_dir)"
if [ -n "$TARGET" ]; then REMOTION_DIR="$TARGET"; fi

if [ ! -f "$REMOTION_DIR/package-lock.json" ]; then
    echo "NODE DEPS: FAIL - no package-lock.json in $REMOTION_DIR" >&2
    echo "  It is tracked in git; a checkout without it is incomplete." >&2
    exit 1
fi

ENTRY="$(ask "'$REMOTION_DIR'" store_entry)"
STORE_NM="$ENTRY/node_modules"
LOCAL_NM="$REMOTION_DIR/node_modules"

echo "NODE DEPS"
echo "  renderer source : $REMOTION_DIR   (tracked code, stays in the checkout)"
echo "  store entry     : $ENTRY"
echo "                    keyed by sha256(package-lock.json)"

if [ "$CHECK_ONLY" = 1 ]; then
    if [ -d "$LOCAL_NM" ]; then
        echo "  bound           : yes -> $(cd "$LOCAL_NM" && pwd -P)"
        echo "NODE DEPS: PRESENT"
        exit 0
    fi
    echo "  bound           : NO"
    echo "NODE DEPS: ABSENT - run $0 to install and bind"
    exit 1
fi

# ── 1. fill the store entry, if this lockfile has none ───────────────
if [ -d "$STORE_NM" ]; then
    echo "  store           : already installed, reusing"
else
    echo "  store           : installing (npm ci)"
    mkdir -p "$ENTRY"
    cp "$REMOTION_DIR/package.json" "$REMOTION_DIR/package-lock.json" "$ENTRY/"
    # `npm ci` in the store, never in the checkout: the checkout holds a
    # symlink, and npm would rewrite what it points at.
    ( cd "$ENTRY" && npm_config_yes=true npm ci --no-audit --no-fund )
    if [ ! -d "$STORE_NM" ]; then
        echo "NODE DEPS: FAIL - npm ci left no node_modules in $ENTRY" >&2
        exit 1
    fi
fi

# ── 2. bind this checkout ────────────────────────────────────────────
# A REAL directory here is somebody's existing install. It is not ours to
# delete, so say so and stop rather than replacing 585 MB of someone's
# work with a symlink they did not ask for.
if [ -d "$LOCAL_NM" ] && [ ! -L "$LOCAL_NM" ]; then
    echo "  bind            : SKIPPED - $LOCAL_NM is a real directory"
    echo
    echo "This checkout already carries its own copy of the dependencies."
    echo "It works, and nothing here will delete it. To move it into the"
    echo "shared store, remove it yourself and re-run:"
    echo "    rm -rf '$LOCAL_NM' && $0 '$REMOTION_DIR'"
else
    ln -sfn "$STORE_NM" "$LOCAL_NM"
    echo "  bind            : $LOCAL_NM -> $STORE_NM"
fi

# ── 3. verdict, from the resolution rather than from exit codes ──────
if [ "$(ask "'$REMOTION_DIR'" dependencies_present)" != "True" ]; then
    echo "NODE DEPS: FAIL - shared_environment still reports them unreachable" >&2
    exit 1
fi
echo "NODE DEPS: PASS"
