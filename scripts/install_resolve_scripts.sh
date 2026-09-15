#!/usr/bin/env bash
#
# Put this repository's DaVinci Resolve scripts where Workspace > Scripts
# can see them.
#
#     scripts/install_resolve_scripts.sh [--allow-disposable] [--dry-run|--uninstall]
#
# The stamp below OUTLIVES this checkout, so installing from a disposable
# lane (a linked git worktree, or anything git cannot vouch for) is
# REFUSED - the installed copy would break silently when the lane is
# reclaimed.  `--allow-disposable` stamps it anyway, deliberately, for
# the developer testing from a lane; `--dry-run` only reports the
# verdict; `--uninstall` always works.
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
# `--show-toplevel`, never `--git-common-dir`.  In a git WORKTREE the
# common dir is the PRIMARY checkout's `.git`, so `${GIT_DIR}/..` stamped
# the primary checkout's path into a copy taken from the worktree - the
# installed script then imported a `library/` that was not the one being
# installed.  `--show-toplevel` is the root of THIS checkout, which is
# what the stamp is for.
if ! REPO_ROOT="$(cd "${THIS_DIR}" && git rev-parse --show-toplevel 2>/dev/null)"; then
  REPO_ROOT="$(cd "${THIS_DIR}/.." && pwd -P)"
fi
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd -P)"
SOURCE_DIR="$(cd "${THIS_DIR}/.." && pwd -P)/resolve_scripts"

# Under Utility a script is listed on EVERY page, which is what the
# capture button needs: the captain reviews on Edit and on Color.
DEST_DIR="${HOME}/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

MODE="install"
ALLOW_DISPOSABLE=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run)   MODE="dry-run" ;;
    --uninstall) MODE="uninstall" ;;
    --allow-disposable) ALLOW_DISPOSABLE=1 ;;
    *) echo "usage: $0 [--allow-disposable] [--dry-run|--uninstall]" >&2; exit 64 ;;
  esac
done

if [ ! -d "${SOURCE_DIR}" ]; then
  echo "✗ no ${SOURCE_DIR} in this checkout" >&2
  exit 1
fi

# The stamp below outlives this checkout, so a disposable lane must not
# be stamped silently: `linked-worktree` (a worktree tool may reclaim
# it) and `unknown` (nothing vouches for it) both refuse a real install.
# Uninstall always works - removing a stale install must succeed from
# anywhere, including the lane that caused it.
. "${THIS_DIR}/lib/guard_durable_checkout.sh"
DURABILITY="$(vep_checkout_durability "${REPO_ROOT}")"
if [ "${DURABILITY}" != "durable" ] && [ "${ALLOW_DISPOSABLE}" -eq 1 ] \
    && [ "${MODE}" = "install" ]; then
  echo "proceeding from a ${DURABILITY} checkout by explicit --allow-disposable." >&2
fi
if [ "${MODE}" = "install" ] && [ "${DURABILITY}" != "durable" ] \
    && [ "${ALLOW_DISPOSABLE}" -ne 1 ]; then
  vep_refuse_disposable_checkout "${REPO_ROOT}" "${DURABILITY}" "$0"
  exit 1
fi

echo "repository: ${REPO_ROOT}"
echo "scripts to: ${DEST_DIR}"
echo
if [ "${MODE}" = "dry-run" ] && [ "${DURABILITY}" != "durable" ] \
    && [ "${ALLOW_DISPOSABLE}" -ne 1 ]; then
  echo "note: install from here would be REFUSED (${DURABILITY}); pass"
  echo "--allow-disposable to stamp it deliberately."
fi
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
