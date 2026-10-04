#!/usr/bin/env bash
#
# Install the Montreal Forced Aligner environment ONCE PER MACHINE.
#
#     scripts/install_mfa.sh           # install (or verify) and report
#     scripts/install_mfa.sh --check   # report, install nothing
#
# See `library/tools/shared_environment.py` (the MFA half) for the
# resolution this script obeys - it ASKS that module where the
# environment lives rather than deciding, so the two cannot drift.
#
# WHAT IT DOES
#
#   1. Fetches the PINNED micromamba binary for this platform into
#      `<vep_home>/micromamba/bin`, unless it is already there. The
#      tarball URL is versioned (`shared_environment.MICROMAMBA_VERSION`,
#      never `/latest`) and its sha256 is verified before unpacking -
#      a mismatch is deleted, never executed. No
#      `conda init`, no shell profile touched.
#   2. `micromamba create -n mfa -c conda-forge
#      montreal-forced-aligner=<exact>` (the exact spec lives in
#      `shared_environment.MFA_PACKAGE_SPEC`), unless the env already exists.
#   3. Downloads the PINNED acoustic model, dictionary and G2P
#      models into MFA's model dir. The pins are
#      `shared_environment.MFA_ACOUSTIC_MODEL_VERSION`,
#      `MFA_DICTIONARY_VERSION` and `MFA_G2P_VERSION`: unpinned
#      models float and the models are what place the boundaries.
#
# Measured 2026-09-16: under 10 minutes wall, most of it unattended
# download (~1.8 GB env, ~105 MB models). Removal is one command:
# `rm -rf <micromamba root> <models dir>` - both paths are printed by
# `--check`.
#
# It is FAIL-CLOSED and it does not trust its own exit codes alone: the
# last thing it does is re-ask `shared_environment` whether MFA is now
# reachable, and it reports PASS only on that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_MFA_PYTHON:-python3}"

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

MICROMAMBA_ROOT="$(ask 'se.micromamba_root()')"
MFA_BINARY="$(ask 'se.mfa_binary()')"
MFA_MODELS="$(ask 'se.mfa_models_dir()')"
ACOUSTIC="$(ask 'se.MFA_ACOUSTIC_MODEL')"
ACOUSTIC_VERSION="$(ask 'se.MFA_ACOUSTIC_MODEL_VERSION')"
DICTIONARY="$(ask 'se.MFA_DICTIONARY')"
DICTIONARY_VERSION="$(ask 'se.MFA_DICTIONARY_VERSION')"
G2P_MODEL="$(ask 'se.MFA_G2P_MODEL')"
G2P_VERSION="$(ask 'se.MFA_G2P_VERSION')"
PACKAGE_SPEC="$(ask 'se.MFA_PACKAGE_SPEC')"

echo "MFA ENV"
echo "  micromamba root : $MICROMAMBA_ROOT"
echo "  mfa binary      : $MFA_BINARY"
echo "  models dir      : $MFA_MODELS"
echo "  acoustic model  : $ACOUSTIC $ACOUSTIC_VERSION (pinned)"
echo "  dictionary      : $DICTIONARY $DICTIONARY_VERSION (pinned)"
echo "  g2p             : $G2P_MODEL $G2P_VERSION (pinned)"
echo "  conda package   : $PACKAGE_SPEC (pinned)"

if [ "$CHECK_ONLY" = 1 ]; then
    if [ -x "$MFA_BINARY" ] && [ -d "$MFA_MODELS" ]; then
        echo "MFA ENV: PRESENT"
        exit 0
    fi
    echo "MFA ENV: ABSENT - run $0 to install"
    exit 1
fi

# ── 1. the micromamba binary ─────────────────────────────────────────
# Pinned version + sha256: the old `/latest` fetch moved under every
# fresh machine. Both the URL and the hash come from shared_environment,
# and a tarball that does not match is deleted, never unpacked.
MM_BIN="$MICROMAMBA_ROOT/bin/micromamba"

sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

if [ -x "$MM_BIN" ]; then
    echo "  micromamba      : already installed, reusing"
else
    MM_URL="$(ask 'se.micromamba_download_url()')"
    MM_SHA256="$(ask 'se.micromamba_expected_sha256()')"
    echo "  micromamba      : fetching $MM_URL"
    mkdir -p "$MICROMAMBA_ROOT/bin"
    MM_TMP="$(mktemp "$MICROMAMBA_ROOT/micromamba-XXXXXX.tar.bz2")"
    trap 'rm -f "$MM_TMP"' EXIT
    curl -fsSL "$MM_URL" -o "$MM_TMP"
    if [ "$(sha256_of "$MM_TMP")" != "$MM_SHA256" ]; then
        echo "MFA ENV: FAIL - micromamba sha256 mismatch (expected $MM_SHA256)" >&2
        echo "  The tarball was deleted, nothing was unpacked." >&2
        rm -f "$MM_TMP"
        exit 1
    fi
    echo "  micromamba      : sha256 verified"
    tar -xvj -C "$MICROMAMBA_ROOT/bin" --strip-components=1 bin/micromamba < "$MM_TMP"
    rm -f "$MM_TMP"
    trap - EXIT
fi

# ── 2. the mfa env ───────────────────────────────────────────────────
if [ -x "$MFA_BINARY" ]; then
    echo "  mfa env         : already installed, reusing"
else
    echo "  mfa env         : creating (conda-forge, $PACKAGE_SPEC)"
    "$MM_BIN" create -y -n mfa -c conda-forge "$PACKAGE_SPEC"
fi

# ── 3. the pinned models ─────────────────────────────────────────────
echo "  models          : downloading $ACOUSTIC $ACOUSTIC_VERSION + $DICTIONARY $DICTIONARY_VERSION + g2p $G2P_VERSION"
"$MFA_BINARY" model download acoustic "$ACOUSTIC" --version "$ACOUSTIC_VERSION"
"$MFA_BINARY" model download dictionary "$DICTIONARY" --version "$DICTIONARY_VERSION"
"$MFA_BINARY" model download g2p "$G2P_MODEL" --version "$G2P_VERSION"

# ── verdict, from the resolution rather than from exit codes ─────────
if [ "$(ask 'se.mfa_available()[0]')" != "True" ]; then
    echo "MFA ENV: FAIL - shared_environment still reports it unreachable" >&2
    echo "$(ask 'se.mfa_missing_message()')" >&2
    exit 1
fi
echo "MFA ENV: PASS"
