#!/usr/bin/env bash
# Install Ren from a released v* tag, or build from a checkout with
# --source. The release route fetches the engine artifact that
# .github/workflows/release.yml builds and verifies it against the
# release's SHA256SUMS.txt. No Apple Developer ID or notarization.

set -euo pipefail

REPOSITORY="prajwal-395/video_editing_pilot"
HOME_DIR="${PIPELINE_VEP_HOME:-}"
SOURCE_DIR="${REN_INSTALL_SOURCE:-}"
URL="${REN_INSTALL_URL:-}"
TAG="${REN_INSTALL_TAG:-}"
SKIP_SYSTEM_DEPS=0
SETUP_ARGS=()

usage() {
    cat <<'EOF'
Usage: install_ren.sh [--source <checkout>] [--url <artifact-url>]
                       [--tag <vX.Y.Z>] [--home <Ren home>]
                       [--with <panns|mfa|ecapa|deepfilter>]...
                       [--skip-system-deps]

Fetches the engine artifact from the newest released v* tag (or --tag),
verifies it against the release's SHA256SUMS.txt, and installs it. --url
fetches the artifact from a specific URL instead (https or file), reading
its checksum from <dir>/SHA256SUMS.txt. --source builds from a checkout
instead (developers). Homebrew installs uv, Node.js, and ffmpeg; Ren
installs its Python 3.12 runtime and hash-locked Python dependencies in
PIPELINE_VEP_HOME. --skip-system-deps is for managed machines that
already provide every required tool.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            [ "$#" -ge 2 ] || { echo "--source needs a path" >&2; exit 2; }
            SOURCE_DIR="$2"
            shift 2
            ;;
        --url)
            [ "$#" -ge 2 ] || { echo "--url needs a URL" >&2; exit 2; }
            URL="$2"
            shift 2
            ;;
        --tag)
            [ "$#" -ge 2 ] || { echo "--tag needs a release tag" >&2; exit 2; }
            TAG="$2"
            shift 2
            ;;
        --home)
            [ "$#" -ge 2 ] || { echo "--home needs a path" >&2; exit 2; }
            HOME_DIR="$2"
            shift 2
            ;;
        --with)
            [ "$#" -ge 2 ] || { echo "--with needs a pack name" >&2; exit 2; }
            SETUP_ARGS+=(--with "$2")
            shift 2
            ;;
        --skip-system-deps)
            SKIP_SYSTEM_DEPS=1
            SETUP_ARGS+=(--skip-system-deps)
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$HOME_DIR" ]; then
    if [ -n "${XDG_DATA_HOME:-}" ]; then
        HOME_DIR="$XDG_DATA_HOME/vep"
    else
        HOME_DIR="$HOME/.local/share/vep"
    fi
fi
case "$HOME_DIR" in
    /*) ;;
    *) HOME_DIR="$PWD/$HOME_DIR" ;;
esac
HOME_DIR="${HOME_DIR%/}"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "Ren's packaged installer currently supports Apple Silicon macOS only." >&2
    exit 2
fi

find_brew() {
    if command -v brew >/dev/null 2>&1; then
        command -v brew
    elif [ -x /opt/homebrew/bin/brew ]; then
        echo /opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then
        echo /usr/local/bin/brew
    fi
}

BREW="$(find_brew)"
if [ -z "$BREW" ] && [ "$SKIP_SYSTEM_DEPS" -eq 0 ]; then
    echo "Installing Homebrew (macOS may ask to install command-line tools or for an administrator password)."
    INSTALLER="$(mktemp "${TMPDIR:-/tmp}/ren-homebrew.XXXXXX")"
    trap 'rm -f "$INSTALLER"' EXIT
    if ! curl -fsSL --retry 2 \
        https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh \
        -o "$INSTALLER"; then
        echo "Could not download Homebrew's installer. Check the network and retry." >&2
        exit 1
    fi
    if ! /bin/bash "$INSTALLER"; then
        echo "Homebrew could not be installed. Install Homebrew, then run this installer again." >&2
        exit 1
    fi
    rm -f "$INSTALLER"
    trap - EXIT
    BREW="$(find_brew)"
fi

if [ -z "$BREW" ]; then
    echo "Homebrew is required to locate/install Ren's system prerequisites." >&2
    echo "Install Homebrew, or run with --skip-system-deps on a managed machine." >&2
    exit 1
fi

BREW_PREFIX="$("$BREW" --prefix)"
export PATH="$BREW_PREFIX/bin:$BREW_PREFIX/sbin:$PATH"

if [ "$SKIP_SYSTEM_DEPS" -eq 0 ]; then
    "$BREW" install uv
fi
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
    echo "uv is missing. Install uv with Homebrew, then retry." >&2
    exit 1
fi

mkdir -p "$HOME_DIR/cache/uv" "$HOME_DIR/python"
export PIPELINE_VEP_HOME="$HOME_DIR"
export UV_CACHE_DIR="$HOME_DIR/cache/uv"
export UV_PYTHON_INSTALL_DIR="$HOME_DIR/python"
"$UV" python install 3.12 --install-dir "$HOME_DIR/python"
PYTHON="$("$UV" python find --managed-python --no-project 3.12)"
if [ ! -x "$PYTHON" ]; then
    echo "uv installed Python 3.12 but did not return a runnable interpreter: $PYTHON" >&2
    exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ren-install.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

if [ -n "$SOURCE_DIR" ]; then
    SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd -P)"
    if [ ! -f "$SOURCE_DIR/ren/package_engine.py" ]; then
        echo "--source must name a Ren checkout: $SOURCE_DIR" >&2
        exit 2
    fi
    SOURCE_SHA="$(git -C "$SOURCE_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
    SOURCE_CHANNEL="main"
else
    if [ -n "$URL" ]; then
        ZIP_URL="$URL"
        SUMS_URL="$(dirname "$URL")/SHA256SUMS.txt"
        ASSET_NAME="$(basename "$URL")"
    else
        if [ -n "$TAG" ]; then
            case "$TAG" in
                v[!A-Za-z0-9._/-]*|*..*|/*)
                    echo "--tag must be a release tag like v0.1.0: $TAG" >&2
                    exit 2
                    ;;
            esac
            API_ENDPOINT="tags/$TAG"
        else
            API_ENDPOINT="latest"
        fi
        echo "Resolving Ren release ($API_ENDPOINT)..."
        if ! curl -fsSL --retry 2 -H "Accept: application/vnd.github+json" \
            "https://api.github.com/repos/$REPOSITORY/releases/$API_ENDPOINT" \
            -o "$WORK/release.json"; then
            echo "Could not reach GitHub to resolve the Ren release. Check the network and retry." >&2
            exit 1
        fi
        read -r TAG ASSET_NAME ZIP_URL SUMS_URL <<< "$("$PYTHON" - "$WORK/release.json" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except ValueError:
    sys.exit("GitHub did not return a readable Ren release.")
assets = {asset["name"]: asset["browser_download_url"] for asset in data.get("assets", [])}
name = f"ren-engine-{data['tag_name']}.zip"
for needed in (name, "SHA256SUMS.txt"):
    if needed not in assets:
        sys.exit(f"the Ren release {data['tag_name']} has no {needed} asset")
print(data["tag_name"], name, assets[name], assets["SHA256SUMS.txt"])
PY
)"
        if [ -z "$ASSET_NAME" ]; then
            echo "GitHub did not return a Ren release with an engine artifact." >&2
            exit 1
        fi
    fi
    echo "Downloading $ASSET_NAME..."
    if ! curl -fsSL --retry 2 "$SUMS_URL" -o "$WORK/SHA256SUMS.txt"; then
        echo "Could not download the release checksum manifest. Check the network and retry." >&2
        exit 1
    fi
    if ! curl -fsSL --retry 2 "$ZIP_URL" -o "$WORK/$ASSET_NAME"; then
        echo "Could not download $ASSET_NAME. Check the network and retry." >&2
        exit 1
    fi
    echo "Verifying $ASSET_NAME against SHA256SUMS.txt..."
    if ! (cd "$WORK" && grep -F "$ASSET_NAME" SHA256SUMS.txt | shasum -a 256 --check -); then
        echo "$ASSET_NAME does not match the checksum published in SHA256SUMS.txt." >&2
        echo "The download is corrupt or the release is incomplete; refusing to install it." >&2
        exit 1
    fi
    echo "Extracting the engine tree..."
    if ! unzip -q "$WORK/$ASSET_NAME" -d "$WORK"; then
        echo "Could not extract $ASSET_NAME." >&2
        exit 1
    fi
    SOURCE_DIR="$WORK/ren-engine"
    SOURCE_SHA=""
    SOURCE_CHANNEL="stable"
fi

export PYTHONPATH="$SOURCE_DIR${PYTHONPATH:+:$PYTHONPATH}"
if [ ! -f "$HOME_DIR/current/manage_project.py" ]; then
    echo "Installing the immutable Ren engine tree..."
    if [ -n "$SOURCE_SHA" ]; then
        "$PYTHON" -m ren.package_engine --src "$SOURCE_DIR" \
            --home "$HOME_DIR" --channel "$SOURCE_CHANNEL" --sha "$SOURCE_SHA"
    else
        "$PYTHON" -m ren.package_engine --src "$SOURCE_DIR" \
            --home "$HOME_DIR" --channel "$SOURCE_CHANNEL"
    fi
else
    echo "Ren already has an engine at $HOME_DIR/current; continuing setup without replacing it."
    echo "Run `ren update` to move it to the newest release."
fi

export PIPELINE_PYTHON="$PYTHON"
export REN_ENGINE_ROOT="$HOME_DIR/current"
# macOS bash 3.2 treats "${arr[@]}" on an empty array as unbound under
# set -u, so expand it only when the installer collected arguments.
if [ "${#SETUP_ARGS[@]}" -gt 0 ]; then
    "$HOME_DIR/current/bin/ren" setup "${SETUP_ARGS[@]}"
else
    "$HOME_DIR/current/bin/ren" setup
fi
