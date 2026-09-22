#!/usr/bin/env bash
# Task-shaped merge record for firstmate's own direct changes to this repo's
# shared tracked material. AGENTS.md section 1 permits that direct work when no
# crewmate is live and requires it to ship through the repo's PR path with the
# same merge authority as any other project, but bin/fm-pr-merge.sh takes a
# task id and records merge metadata against that task's durable record, and
# firstmate's own direct work has no task.
#
# The honest answer is that this needs a task-shaped record after all, so
# creating one is the documented path here rather than a second merge route:
# this script only writes and retires the record, and the merge itself still
# runs through the unchanged bin/fm-pr-merge.sh, which keeps every guarantee -
# the live green check at the verified head, the head-bound forge call, and
# the proved (never reported) outcome. A red PR, a changed head, or a merge
# the forge did not accept refuses exactly as it does for a task-owned PR,
# because the refusal lives in that script, not in the record.
#
# Why a state-only record with no backlog row: bin/fm-captain-hold.sh's
# `open --distinguish-absent` predicate returns absent (exit 3) when no backlog
# row names the id, and bin/fm-pr-merge.sh treats absent exactly like released,
# so a record firstmate never held needs no hold to release. No backlog item is
# filed because this bookkeeping must not pollute the captain's queue.
#
# create <record-id> [--mode <mode>] [--yolo]
#   Writes state/<record-id>.meta carrying kind=firstmate-direct, mode=<mode>
#   (default direct-PR; one of direct-PR, no-mistakes, local-only), and
#   yolo=off unless --yolo is passed (which records yolo=on, with the same
#   away-posture meaning task metadata carries). The file is published
#   atomically, mode 0600, single-link, on the state filesystem. Refuses when
#   any per-id state already exists, so a live task's record can never be
#   clobbered or shadowed. Prefer record ids starting with fm-direct-, a
#   prefix task dispatch never generates.
#
# retire <record-id> <pr-url>
#   Removes the record after its merge is proved. Refuses unless both the
#   identity-bound merge authority (persisted only after the forge accepted the
#   merge) and the merge-notified marker (committed only after the outcome was
#   published) match the canonical PR identity, so retiring before a proved
#   merge - which would drop the merge poll a catch-up still needs - is
#   impossible. Removes the meta file, the merge-authority and merge-poll
#   artifacts, and the merge-notified marker itself, the same set
#   bin/fm-teardown.sh removes, so a retired record leaves no residue behind.
#
# Usage: fm-direct-merge-record.sh create <record-id> [--mode <mode>] [--yolo]
#        fm-direct-merge-record.sh retire <record-id> <pr-url>
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-pr-lib.sh
. "$SCRIPT_DIR/fm-pr-lib.sh"
# shellcheck source=bin/fm-wake-lib.sh
. "$SCRIPT_DIR/fm-wake-lib.sh"
# shellcheck source=bin/fm-merge-authority-lib.sh
. "$SCRIPT_DIR/fm-merge-authority-lib.sh"

usage() {
  cat >&2 <<'EOF'
usage: fm-direct-merge-record.sh create <record-id> [--mode <mode>] [--yolo]
       fm-direct-merge-record.sh retire <record-id> <pr-url>
EOF
}

# Per-id state this home may hold for the record. The merge set is what the
# merge path arms and what retire removes, the same set bin/fm-teardown.sh
# removes. The foreign set is supervisor state a direct record never carries;
# create refuses to shadow it and retire refuses to touch it. The
# merge-notified marker is armed by the merge outcome report, so create refuses
# to adopt a leftover one and retire removes it last, after it has served as
# the published-outcome proof.
record_merge_artifacts() {  # <state> <id>
  printf '%s\n' \
    "$1/$2.meta" \
    "$1/$2.check.sh" \
    "$1/$2.check-trust" \
    "$1/$2.pr-poll" \
    "$1/$2.pr-poll-registration" \
    "$1/$2.pr-poll-retirement" \
    "$1/$2.merge-authority"
}

record_foreign_artifacts() {  # <state> <id>
  printf '%s\n' \
    "$1/$2.status" \
    "$1/$2.turn-ended" \
    "$1/$2.stopped" \
    "$1/$2.progress"
}

cmd_create() {
  local id='' mode='direct-PR' yolo='off'
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --mode)
        [ -n "${2:-}" ] || { echo "error (caller): --mode requires a value" >&2; return 2; }
        mode=$2
        shift 2
        ;;
      --mode=*)
        mode=${1#--mode=}
        shift
        ;;
      --yolo)
        yolo='on'
        shift
        ;;
      --help|-h)
        usage
        return 0
        ;;
      --*)
        echo "error (caller): unknown flag '$1'" >&2
        return 2
        ;;
      *)
        if [ -n "$id" ]; then
          echo "error (caller): unexpected argument '$1'" >&2
          return 2
        fi
        id=$1
        shift
        ;;
    esac
  done
  [ -n "$id" ] || { echo "error (caller): missing record id" >&2; return 2; }
  fm_task_id_creation_valid "$id" \
    || { echo "error (caller): invalid record id '$id'" >&2; return 2; }
  case "$mode" in
    direct-PR|no-mistakes|local-only) ;;
    *)
      echo "error (caller): invalid mode '$mode'; expected direct-PR, no-mistakes, or local-only" >&2
      return 2
      ;;
  esac
  [ -d "$STATE" ] && [ ! -L "$STATE" ] \
    || { echo "error: state directory is unavailable" >&2; return 1; }

  local lock
  local rc=0
  # The same per-task control lock bin/fm-pr-merge.sh holds across its gate,
  # so create, merge, and retire on one id serialize instead of interleaving.
  lock="$STATE/.control-$id.lock"
  fm_lock_acquire_wait "$lock" \
    || { echo "error: could not lock the record for '$id'" >&2; return 1; }
  create_locked "$id" "$mode" "$yolo" || rc=$?
  fm_lock_release "$lock" || rc=1
  return "$rc"
}

create_locked() {  # <id> <mode> <yolo>
  local id=$1 mode=$2 yolo=$3
  local state_device artifact tmp
  state_device=$(fm_pr_file_device "$STATE") \
    || { echo "error: state directory is unavailable" >&2; return 1; }

  while IFS= read -r artifact; do
    if [ -e "$artifact" ] || [ -L "$artifact" ]; then
      echo "error: refusing to shadow existing state for '$id': $artifact exists" >&2
      return 1
    fi
  done <<EOF
$(record_merge_artifacts "$STATE" "$id")
$(record_foreign_artifacts "$STATE" "$id")
EOF
  if [ -e "$STATE/$id.pr-poll-merge-notified" ] || [ -L "$STATE/$id.pr-poll-merge-notified" ]; then
    echo "error: refusing to shadow existing state for '$id': $STATE/$id.pr-poll-merge-notified exists" >&2
    return 1
  fi

  umask 077
  tmp=$(mktemp "$STATE/.fm-direct-record.XXXXXX") \
    || { echo "error: could not stage the record for '$id'" >&2; return 1; }
  if ! printf '%s\n' "kind=firstmate-direct" "mode=$mode" "yolo=$yolo" > "$tmp" \
    || ! chmod 0600 "$tmp" \
    || ! fm_pr_private_file_valid "$tmp" 600 "$state_device" \
    || ! fm_pr_regular_destination_on_device_or_absent "$STATE/$id.meta" "$state_device" \
    || ! mv -f -- "$tmp" "$STATE/$id.meta" \
    || ! fm_pr_private_file_valid "$STATE/$id.meta" 600 "$state_device"; then
    rm -f -- "$tmp"
    echo "error: could not publish the record for '$id'" >&2
    return 1
  fi
  printf 'created: state/%s.meta\n' "$id"
}

cmd_retire() {
  local id=${1-} raw_url=${2-}
  [ "$#" -eq 2 ] || { echo "error (caller): retire takes a record id and a PR URL" >&2; return 2; }
  fm_pr_task_id_valid "$id" \
    || { echo "error (caller): invalid record id '$id'" >&2; return 2; }
  fm_pr_url_parse "$raw_url" \
    || { echo "error (caller): malformed PR URL '$raw_url'" >&2; return 2; }
  local provider host path number url
  provider=$FM_PR_PROVIDER
  host=$FM_PR_HOST
  path=$FM_PR_PATH
  number=$FM_PR_NUMBER
  url=$FM_PR_URL
  [ -d "$STATE" ] && [ ! -L "$STATE" ] \
    || { echo "error: state directory is unavailable" >&2; return 1; }

  # Serialize against any in-flight merge for this id through its per-task
  # control lock, the same lock bin/fm-pr-merge.sh holds across its gate.
  local control_lock
  local rc=0
  control_lock="$STATE/.control-$id.lock"
  fm_lock_acquire_wait "$control_lock" \
    || { echo "error: could not lock the record for '$id'" >&2; return 1; }
  retire_locked "$id" "$provider" "$host" "$path" "$number" "$url" || rc=$?
  fm_lock_release "$control_lock" || rc=1
  return "$rc"
}

retire_locked() {  # <id> <provider> <host> <path> <number> <url>
  local id=$1 provider=$2 host=$3 path=$4 number=$5 url=$6
  local kind artifact state_device
  kind=$(grep '^kind=' "$STATE/$id.meta" 2>/dev/null | tail -1 | cut -d= -f2- || true)
  [ "$kind" = firstmate-direct ] \
    || { echo "error: refusing to retire '$id': not a firstmate-direct record" >&2; return 1; }
  if ! fm_merge_authority_read "$STATE" "$id" "$provider" "$host" "$path" "$number"; then
    echo "error: refusing to retire '$id': no merge authority proves $url was merged here" >&2
    return 1
  fi
  case "$FM_MERGE_AUTHORITY" in
    yolo|away-grant|attended) ;;
    *)
      echo "error: refusing to retire '$id': no merge authority proves $url was merged here" >&2
      return 1
      ;;
  esac
  if ! fm_pr_poll_merge_already_notified "$STATE" "$id" "$provider" "$host" "$path" "$number"; then
    echo "error: refusing to retire '$id': the merge outcome for $url is not published yet" >&2
    return 1
  fi

  local artifact state_device
  state_device=$(fm_pr_file_device "$STATE") \
    || { echo "error: state directory is unavailable" >&2; return 1; }
  while IFS= read -r artifact; do
    if [ -e "$artifact" ] || [ -L "$artifact" ]; then
      echo "error: refusing to retire '$id': unexpected supervisor state $artifact exists" >&2
      return 1
    fi
  done <<EOF
$(record_foreign_artifacts "$STATE" "$id")
EOF
  while IFS= read -r artifact; do
    if [ -L "$artifact" ]; then
      echo "error: refusing to retire '$id': $artifact is a symlink" >&2
      return 1
    fi
    if [ -e "$artifact" ]; then
      if ! [ -f "$artifact" ] \
        || ! [ "$(fm_pr_file_link_count "$artifact")" = 1 ] \
        || ! [ "$(fm_pr_file_device "$artifact")" = "$state_device" ] \
        || { [ "$artifact" = "$STATE/$id.merge-authority" ] \
          && [ "$(fm_pr_file_mode "$artifact")" != 600 ]; }; then
        echo "error: refusing to retire '$id': $artifact is not a plain state file" >&2
        return 1
      fi
      rm -f -- "$artifact" \
        || { echo "error: could not remove $artifact" >&2; return 1; }
    fi
  done <<EOF
$(record_merge_artifacts "$STATE" "$id")
EOF
  fm_pr_poll_merge_notified_remove "$STATE" "$id" \
    || { echo "error: could not retire the merge outcome marker for '$id'" >&2; return 1; }
  printf 'retired: %s %s\n' "$id" "$url"
}

if [ "$#" -lt 1 ]; then
  usage
  exit 2
fi
sub=$1
shift
case "$sub" in
  create) cmd_create "$@" ;;
  retire) cmd_retire "$@" ;;
  --help|-h|help) usage; exit 0 ;;
  *) echo "error (caller): unknown subcommand '$sub'" >&2; usage; exit 2 ;;
esac
