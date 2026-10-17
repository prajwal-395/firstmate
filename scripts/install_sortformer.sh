#!/usr/bin/env bash
# Install the pinned FluidAudio CLI and offline Sortformer model once per machine.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${INSTALL_SORTFORMER_PYTHON:-python3}"
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        -h|--help) echo "Usage: scripts/install_sortformer.sh [--check]"; exit 0 ;;
        --*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

( cd "$REPO_ROOT" && REN_EDITION="${REN_EDITION:-personal}" REN_WORKLOAD_PURPOSE="${REN_WORKLOAD_PURPOSE:-commercial_client}" PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m ren.edition check model.sortformer --action fetch )
ask() {
    ( cd "$REPO_ROOT" && "$PYTHON" -c "import sys;sys.path.insert(0,'$REPO_ROOT');from library.tools import shared_environment as se;print($1)" )
}
CLI="$(ask 'se.sortformer_cli()')"
MODEL="$(ask 'se.sortformer_model_path()')"
MODEL_ROOT="${MODEL%/v3/palettized/SortformerOffline_v2.1.mlmodelc}"
REVISION="$(ask 'se.SORTFORMER_REVISION')"
REPO="$(ask 'se.SORTFORMER_REPO')"
SOURCE_REVISION="$(ask 'se.SORTFORMER_SOURCE_REVISION')"
SOURCE="$(ask 'se.vep_home() / se.SORTFORMER_SOURCE_RELATIVE')"
echo "Sortformer install"
echo "  cli: $CLI"
echo "  model: $MODEL"
if [ "$CHECK_ONLY" = 1 ]; then
    ( cd "$REPO_ROOT" && "$PYTHON" -c "import sys;sys.path.insert(0,'$REPO_ROOT');from library.tools import shared_environment as se;ok,detail=se.sortformer_available();print(detail);raise SystemExit(0 if ok else 1)" )
    exit $?
fi
mkdir -p "$(dirname "$CLI")" "$(dirname "$MODEL_ROOT")" "$(dirname "$SOURCE")"
if [ ! -d "$SOURCE/.git" ]; then
    git clone --filter=blob:none https://github.com/FluidInference/FluidAudio.git "$SOURCE"
fi
git -C "$SOURCE" fetch --depth 1 origin "$SOURCE_REVISION"
git -C "$SOURCE" checkout --detach "$SOURCE_REVISION"
if [ ! -x "$CLI" ]; then
    swift build --package-path "$SOURCE" -c release --product fluidaudiocli
    install -m 755 "$SOURCE/.build/release/fluidaudiocli" "$CLI"
fi
"$PYTHON" -c "from huggingface_hub import snapshot_download;snapshot_download(repo_id='$REPO',revision='$REVISION',local_dir='$MODEL_ROOT',allow_patterns=['v3/palettized/SortformerOffline_v2.1.mlmodelc/**'])"
( cd "$REPO_ROOT" && REN_EDITION="${REN_EDITION:-personal}" REN_WORKLOAD_PURPOSE="${REN_WORKLOAD_PURPOSE:-commercial_client}" PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -c "from library.tools import shared_environment as se;print(se.require_sortformer())" )
echo "SORTFORMER INSTALL: PASS"
