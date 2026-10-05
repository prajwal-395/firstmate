#!/usr/bin/env bash
# Install Ren from this checkout, or fetch a pinned GitHub source archive
# when run as the one-line installer. No git clone or developer toolchain.

set -euo pipefail

REPOSITORY="prajwal-395/video_editing_pilot"
DEFAULT_REF="main"
HOME_DIR="${PIPELINE_VEP_HOME:-}"
SOURCE_DIR="${REN_INSTALL_SOURCE:-}"
SKIP_SYSTEM_DEPS=0
SETUP_ARGS=()

usage() {
    cat <<'EOF'
Usage: install_ren.sh [--source <checkout>] [--home <Ren home>]
                      [--with <panns|mfa|ecapa|deepfilter>]...
                      [--skip-system-deps]

With no --source, downloads a pinned GitHub source archive for main.
Homebrew installs uv, Node.js, and ffmpeg; Ren installs its Python 3.12
runtime and hash-locked Python dependencies in PIPELINE_VEP_HOME.
--skip-system-deps is for managed machines that already provide every
required tool.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            [ "$#" -ge 2 ] || { echo "--source needs a path" >&2; exit 2; }
            SOURCE_DIR="$2"
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
else
    REF="${REN_INSTALL_REF:-$DEFAULT_REF}"
    case "$REF" in
        *[!A-Za-z0-9._/-]*|*..*|/*)
            echo "REN_INSTALL_REF contains unsupported characters: $REF" >&2
            exit 2
            ;;
    esac
    echo "Resolving $REPOSITORY ref $REF..."
    curl -fsSL --retry 2 \
        "https://api.github.com/repos/$REPOSITORY/commits/$REF" \
        -o "$WORK/commit.json"
    SOURCE_SHA="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["sha"])' "$WORK/commit.json")"
    case "$SOURCE_SHA" in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
        *) echo "GitHub did not return a commit SHA for $REF" >&2; exit 1 ;;
    esac
    echo "Downloading source archive $SOURCE_SHA..."
    curl -fsSL --retry 2 \
        "https://github.com/$REPOSITORY/archive/$SOURCE_SHA.tar.gz" \
        -o "$WORK/source.tar.gz"
    mkdir -p "$WORK/source"
    tar -xzf "$WORK/source.tar.gz" --strip-components=1 -C "$WORK/source"
    SOURCE_DIR="$WORK/source"
fi

export PYTHONPATH="$SOURCE_DIR${PYTHONPATH:+:$PYTHONPATH}"
if [ ! -f "$HOME_DIR/current/manage_project.py" ]; then
    echo "Building the immutable Ren engine tree..."
    "$PYTHON" -m ren.package_engine --src "$SOURCE_DIR" \
        --home "$HOME_DIR" --channel main --sha "$SOURCE_SHA"
else
    echo "Ren already has an engine at $HOME_DIR/current; continuing setup without replacing it."
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
