#!/bin/bash
# Run the release-shaped install qualification in a disposable local account.
# Invoke without sudo; this script builds the artifact as the caller and asks
# for administrator authorization once for account creation and cleanup.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
TEST_USER="renqualify"
TEST_HOME="/Users/$TEST_USER"

if [ "${1:-}" = "--as-admin" ]; then
    [ "${EUID:-$(id -u)}" -eq 0 ] || { echo "Internal admin invocation was not elevated." >&2; exit 2; }
    WORK="$2"
    CALLER="$3"
    REPO="$4"
    PYTHON="$5"
    VEP_HOME="/Users/$CALLER/.local/share/vep"
    if dscl . -read "/Users/$TEST_USER" >/dev/null 2>&1; then
        echo "Refusing: the reserved test account $TEST_USER already exists." >&2
        exit 2
    fi
    if [ -e "$TEST_HOME" ]; then
        echo "Refusing: $TEST_HOME already exists without the reserved test account." >&2
        exit 2
    fi
    "$PYTHON" "$REPO/scripts/install_preservation.py" capture \
        "$VEP_HOME" "$WORK/vep-before.json" --paths-only
    CREATED=1
    cleanup_account() {
        if [ "$CREATED" -eq 1 ]; then
            dscl . -delete "/Users/$TEST_USER" >/dev/null 2>&1 || true
            rm -rf -- "$TEST_HOME"
        fi
    }
    trap cleanup_account EXIT
    PASSWORD="$(openssl rand -hex 32)"
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON" \
        "$REPO/scripts/qualification_account.py" \
        "$TEST_USER" "$PASSWORD" "$TEST_HOME"
    unset PASSWORD
    TEST_UID="$(dscl . -read "/Users/$TEST_USER" UniqueID | awk '{print $2}')"
    case "$TEST_UID" in
        ''|*[!0-9]*) echo "Could not read the qualification account UID." >&2; exit 1 ;;
    esac
    if dseditgroup -o checkmember -m "$TEST_USER" admin >/dev/null 2>&1; then
        echo "Refusing: qualification account unexpectedly has admin membership." >&2
        exit 1
    fi

    set +e
    sudo -u "$TEST_USER" env HOME="$TEST_HOME" PATH="$PATH" \
        PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON" "$REPO/scripts/ren_fresh_machine_qualify.py" \
        --src "$REPO" --artifact "$WORK/ren-engine-qualification.zip" \
        --sandbox-root "$TEST_HOME/qualification" 2>&1 | tee "$WORK/qualification-report.txt"
    QUALIFY_STATUS=${PIPESTATUS[0]}
    set -e

    cleanup_account
    if dscl . -read "/Users/$TEST_USER" >/dev/null 2>&1; then
        echo "FAIL: test account $TEST_USER remains registered." >&2
        exit 1
    fi
    CREATED=0
    [ ! -e "$TEST_HOME" ] || { echo "FAIL: test home remains at $TEST_HOME"; exit 1; }
    [ ! -e "/Users/$TEST_USER" ] || { echo "FAIL: account home remains at /Users/$TEST_USER"; exit 1; }
    "$PYTHON" "$REPO/scripts/install_preservation.py" capture \
        "/Users/$CALLER" "$WORK/home-after.json" \
        --owner-uid "$TEST_UID" --path-prefix "$VEP_HOME"
    set +e
    "$PYTHON" "$REPO/scripts/install_preservation.py" compare \
        "$WORK/vep-before.json" "$WORK/home-after.json" \
        --renqualify-uid "$TEST_UID"
    AFTER_STATUS=$?
    set -e
    echo "Cleanup: removed account $TEST_USER and home $TEST_HOME."
    echo "Cleanup: temporary artifact, checksums, and report directory will be removed by the invoking script."
    if [ "$AFTER_STATUS" -ne 0 ]; then
        echo "FAIL: caller-home preservation check failed."
        exit 1
    fi
    echo "Verified caller-home ownership and VEP path changes before/after the run."
    exit "$QUALIFY_STATUS"
fi

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "Qualification requires Apple Silicon macOS." >&2
    exit 2
fi

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    echo "Run this script as your normal account, without sudo." >&2
    exit 2
fi

cd "$REPO"
WORK="$(mktemp -d "/private/tmp/ren-clean-mac.XXXXXX")"
cleanup_unprivileged() { rm -rf "$WORK"; }
trap cleanup_unprivileged EXIT

PYTHONDONTWRITEBYTECODE=1 python3 -m ren.package_engine \
    --src "$REPO" --dest "$WORK/engine" \
    --channel stable --sha qualify-local
mkdir "$WORK/ren-engine"
cp -R "$WORK/engine/." "$WORK/ren-engine/"
python3 - "$WORK" <<'PY'
import hashlib
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
artifact = root / "ren-engine-qualification.zip"
with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((root / "ren-engine").rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(root))
digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
(root / "SHA256SUMS.txt").write_text(
    f"{digest}  {artifact.name}\n", encoding="utf-8")
PY
chmod -R a+rX "$WORK"

sudo "$REPO/scripts/ren_clean_mac_install_test.sh" --as-admin "$WORK" \
    "$(id -un)" "$REPO" "$(command -v python3)"
