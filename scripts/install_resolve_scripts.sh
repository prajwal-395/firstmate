#!/usr/bin/env bash
#
# Put this repository's DaVinci Resolve scripts where Workspace > Scripts
# can see them.
#
#     scripts/install_resolve_scripts.sh              # install
#     scripts/install_resolve_scripts.sh --dry-run    # say what it would do
#     scripts/install_resolve_scripts.sh --uninstall  # take them out again
#
# The repository is the source of truth.  Each script is COPIED into the
# Scripts folder with this checkout's path stamped into it, so the copy
# imports the real implementation out of `library/` and cannot drift from
# it - editing the repository takes effect on the next click, with no
# re-install.  Re-run this only when the checkout MOVES, or when a new
# script is added.
#
# Nothing else in the pipeline writes into the application support
# folder.  This is the one command that does, it is run by hand, and it
# says exactly what it touched.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if GIT_DIR="$(cd "${THIS_DIR}" && git rev-parse --git-common-dir 2>/dev/null)"; then
  REPO_ROOT="$(cd "${THIS_DIR}" && cd "${GIT_DIR}/.." && pwd -P)"
else
  REPO_ROOT="$(cd "${THIS_DIR}/.." && pwd -P)"
fi
SOURCE_DIR="$(cd "${THIS_DIR}/.." && pwd -P)/resolve_scripts"

# Under Utility a script is listed on EVERY page, which is what the
# capture button needs: the captain reviews on Edit and on Color.
DEST_DIR="${HOME}/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

MODE="install"
case "${1:-}" in
  --dry-run)   MODE="dry-run" ;;
  --uninstall) MODE="uninstall" ;;
  "")          ;;
  *) echo "usage: $0 [--dry-run|--uninstall]" >&2; exit 64 ;;
esac

if [ ! -d "${SOURCE_DIR}" ]; then
  echo "✗ no ${SOURCE_DIR} in this checkout" >&2
  exit 1
fi

echo "repository: ${REPO_ROOT}"
echo "scripts to: ${DEST_DIR}"
echo

shopt -s nullglob
found=0
for src in "${SOURCE_DIR}"/*.py; do
  found=1
  base="$(basename "${src}")"
  dest="${DEST_DIR}/${base}"
  case "${MODE}" in
    uninstall)
      if [ -f "${dest}" ]; then
        rm "${dest}"
        echo "  removed  ${dest}"
      else
        echo "  absent   ${dest}"
      fi
      ;;
    dry-run)
      echo "  would install  ${base}  ->  ${dest}"
      ;;
    install)
      mkdir -p "${DEST_DIR}"
      # Stamp the checkout's location in, in Python rather than sed: the
      # path is interpolated as a literal, so a space or a quote in it
      # cannot rewrite the file into something else.
      REPO_ROOT="${REPO_ROOT}" python3 - "${src}" "${dest}" <<'PY'
import os, sys
src, dest = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
needle = 'REPO_ROOT = ""'
if needle not in text:
    raise SystemExit(f"✗ {src} has no {needle} line to stamp")
stamped = text.replace(
    needle, "REPO_ROOT = " + repr(os.environ["REPO_ROOT"]), 1)
open(dest, "w", encoding="utf-8").write(stamped)
PY
      echo "  installed  ${base}"
      ;;
  esac
done
shopt -u nullglob

if [ "${found}" -eq 0 ]; then
  echo "✗ no .py scripts in ${SOURCE_DIR}" >&2
  exit 1
fi

echo
case "${MODE}" in
  install)
    echo "Restart DaVinci Resolve, or reopen Workspace > Scripts, and the"
    echo "entries appear there on every page."
    ;;
  uninstall)
    echo "Gone from Workspace > Scripts after Resolve next reads the folder."
    ;;
esac
