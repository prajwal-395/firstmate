#!/usr/bin/env bash
#
# Put this repository's Workflow Integration where
# Workspace > Workflow Integrations can see it.
#
#     scripts/install_workflow_integration.sh              # install
#     scripts/install_workflow_integration.sh --dry-run    # say what it would do
#     scripts/install_workflow_integration.sh --uninstall   # take it out again
#
# The repository is the source of truth.  The plugin directory is COPIED
# into the Workflow Integration Plugins root with this checkout's path
# stamped into main.js, so the installed copy reaches `library/` over the
# bridge and cannot drift from it.  Re-run this when the checkout MOVES,
# or when a plugin file CHANGES - unlike the Script surface, an Electron
# app is loaded from the copy, so an edit here needs a re-install.
#
# Two things this does NOT do, deliberately:
#
#   - It does not ship Blackmagic's `WorkflowIntegration.node`.  That
#     1.7 MB binary is COPIED out of the Resolve installation on this
#     machine, which is also what Blackmagic's own README asks for
#     ("Update WorkflowIntegration.node in your plugin to get the latest
#     improvements"), and it keeps a third-party binary out of this
#     repository (AGENTS.md section 11).
#   - It does not restart Resolve.  Resolve scans this root ON STARTUP
#     only, so a newly installed plugin appears in the menu after the
#     next launch.  This script says so rather than doing it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PLUGIN_ID="com.videoeditingpilot.vep"
SOURCE_DIR="${REPO_ROOT}/resolve_workflow_integration/${PLUGIN_ID}"

# Machine-level, not per-user: this is where Resolve Studio scans.  It is
# world-writable on macOS, so no admin rights are needed, but it may not
# exist yet.
PLUGIN_ROOT="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins"
DEST_DIR="${PLUGIN_ROOT}/${PLUGIN_ID}"

# Blackmagic's native module, taken from the Resolve installation.
SDK_NODE="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/Examples/SamplePlugin/WorkflowIntegration.node"

MODE="install"
case "${1:-}" in
  --dry-run)   MODE="dry-run" ;;
  --uninstall) MODE="uninstall" ;;
  "")          ;;
  *) echo "usage: $0 [--dry-run|--uninstall]" >&2; exit 64 ;;
esac

echo "repository: ${REPO_ROOT}"
echo "plugin to:  ${DEST_DIR}"
echo

if [ "${MODE}" = "uninstall" ]; then
  if [ -d "${DEST_DIR}" ]; then
    rm -rf "${DEST_DIR}"
    echo "  removed  ${DEST_DIR}"
  else
    echo "  absent   ${DEST_DIR}"
  fi
  echo
  echo "Gone from Workspace > Workflow Integrations after Resolve next starts."
  exit 0
fi

if [ ! -d "${SOURCE_DIR}" ]; then
  echo "✗ no ${SOURCE_DIR} in this checkout" >&2
  exit 1
fi

if [ ! -f "${SDK_NODE}" ]; then
  echo "✗ Blackmagic's WorkflowIntegration.node is not on this machine at:" >&2
  echo "    ${SDK_NODE}" >&2
  echo "  It ships with DaVinci Resolve Studio.  Workflow Integrations are" >&2
  echo "  a Studio-only feature; the free edition cannot load this plugin." >&2
  exit 1
fi

if [ "${MODE}" = "dry-run" ]; then
  echo "  would create   ${PLUGIN_ROOT}"
  echo "  would copy     ${SOURCE_DIR}  ->  ${DEST_DIR}"
  echo "  would copy     WorkflowIntegration.node from the Resolve installation"
  echo "  would stamp    REPO_ROOT = '${REPO_ROOT}'  into main.js"
  exit 0
fi

mkdir -p "${PLUGIN_ROOT}"
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"

# Copy the plugin, source files only.
( cd "${SOURCE_DIR}" && find . -type f ! -name '.DS_Store' -print0 ) |
  ( cd "${SOURCE_DIR}" && xargs -0 -I{} sh -c 'mkdir -p "$0/$(dirname "$1")" && cp "$1" "$0/$1"' "${DEST_DIR}" {} )

cp "${SDK_NODE}" "${DEST_DIR}/WorkflowIntegration.node"

# Stamp the checkout's location in, in Python rather than sed: the path
# is interpolated as a literal, so a space or a quote in it cannot
# rewrite the file into something else.  Same reasoning, and same shape,
# as install_resolve_scripts.sh.
REPO_ROOT="${REPO_ROOT}" python3 - "${DEST_DIR}/main.js" <<'PY'
import json, os, sys
dest = sys.argv[1]
text = open(dest, encoding="utf-8").read()
needle = 'const REPO_ROOT = "";'
if needle not in text:
    raise SystemExit("✗ %s has no %s line to stamp" % (dest, needle))
stamped = text.replace(
    needle, "const REPO_ROOT = %s;" % json.dumps(os.environ["REPO_ROOT"]), 1)
open(dest, "w", encoding="utf-8").write(stamped)
PY

echo "  installed  ${PLUGIN_ID}"
find "${DEST_DIR}" -type f | sed "s|${DEST_DIR}/|    |"
echo
echo "Resolve scans this directory ON STARTUP only, so 'VEP Pipeline' appears"
echo "under Workspace > Workflow Integrations the next time Resolve is launched."
echo
echo "To remove it:  scripts/install_workflow_integration.sh --uninstall"
