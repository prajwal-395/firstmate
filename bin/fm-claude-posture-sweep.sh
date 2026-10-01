#!/usr/bin/env bash
# Scan local task records for a Claude terminal-manager bare-resume and repair
# it through fm-control's verified lifecycle transaction.
# Usage: FM_HOME=<home> fm-claude-posture-sweep.sh
# Prints only completed repairs and actionable failures; clean and busy workers
# are silent. The startup bootstrap and watcher own its cadence.
set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -z "${FM_HOME:-}" ] || [ ! -d "$FM_HOME" ]; then
  echo "error: FM_HOME must name a readable Firstmate home" >&2
  exit 1
fi
STATE=${FM_STATE_OVERRIDE:-$FM_HOME/state}
[ -d "$STATE" ] || exit 0

# shellcheck source=bin/fm-control-lib.sh
. "$SCRIPT_DIR/fm-control-lib.sh"
# shellcheck source=bin/fm-backend.sh
. "$SCRIPT_DIR/fm-backend.sh"

failed=0
for meta in "$STATE"/*.meta; do
  [ -f "$meta" ] || continue
  [ -z "$(fm_meta_get "$meta" remote_host)" ] || continue
  harness=$(fm_meta_get "$meta" harness)
  family=$(fm_control_harness_family "$harness") || continue
  [ "$family" = claude ] || continue
  id=$(basename "$meta" .meta)
  # A malformed or incomplete record cannot identify a live worker. Leave
  # those records to the existing metadata-reconciliation path; asking the
  # lifecycle plane to repair one would turn an unrelated stale record into
  # a watcher failure wake.
  fm_backend_validate_task_endpoint "$meta" "$id" >/dev/null 2>&1 || continue
  output=$(FM_HOME="$FM_HOME" FM_STATE_OVERRIDE="$STATE" \
    "$SCRIPT_DIR/fm-control.sh" "$id" repair-posture 2>&1)
  case "$output" in
    posture-repaired\ *) printf '%s\n' "$output" ;;
    posture-clean\ *|posture-skipped\ *|posture-deferred\ *) ;;
    *)
      printf 'POSTURE_REPAIR_FAILED: %s: %s\n' "$id" "${output:-fm-control returned no result}"
      failed=1
      ;;
  esac
done
exit "$failed"
