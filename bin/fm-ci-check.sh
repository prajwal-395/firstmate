#!/usr/bin/env bash
# Arm, re-arm, or disarm a task's PR build-verdict watch.
#
# This is the direct-PR handoff point. A crewmate that has just opened a PR
# arms this watch, declares a wait, and ends its turn; the watcher then wakes
# firstmate once, when the check set reaches a verdict, and firstmate relays
# that verdict back to the same worker. Re-checking the forge from inside the
# agent costs a full model turn per check and produces nothing, which is the
# cost this replaces.
#
# It does NOT watch for a merge. bin/fm-pr-check.sh owns that, arming a
# different static poll at the same state/<id>.check.sh path once the worker
# reports the PR ready - which is also what retires this watch.
#
# Usage:
#   fm-ci-check.sh arm --task <task-id> --pr <pr-url>
#   fm-ci-check.sh disarm --task <task-id>
#   fm-ci-check.sh status --task <task-id>
#
# Only a GitHub pull request is supported: the verdict is read through gh's
# statusCheckRollup, and no equivalent has been verified for another forge.
# A merge request on GitLab is refused here rather than watched incorrectly.
#
# Environment:
#   FM_HOME               operational home whose state/ holds the watch
#   FM_CI_NO_CHECKS_GRACE seconds an empty check set stays silent before it
#                         wakes as no-checks (default 900; read by the poll)
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-pr-lib.sh
. "$SCRIPT_DIR/fm-pr-lib.sh"
# shellcheck source=bin/fm-ci-lib.sh
. "$SCRIPT_DIR/fm-ci-lib.sh"

die() { echo "error: $*" >&2; exit 1; }
usage_die() { echo "error: $*" >&2; echo "usage: fm-ci-check.sh arm|disarm|status --task <task-id> [--pr <url>]" >&2; exit 2; }

print_help() {
  sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

parse_args() {  # sets TASK and PR_URL
  TASK=''
  PR_URL=''
  while [ "$#" -gt 0 ]; do
    case $1 in
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; TASK=$2; shift 2 ;;
      --task=*) TASK=${1#--task=}; shift ;;
      --pr) [ "$#" -gt 1 ] || usage_die "--pr requires a URL"; PR_URL=$2; shift 2 ;;
      --pr=*) PR_URL=${1#--pr=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$TASK" ] || usage_die "a --task <task-id> is required"
  fm_pr_task_id_valid "$TASK" || usage_die "invalid task id '$TASK'"
}

cmd_arm() {
  local check data fired device tmp now
  parse_args "$@"
  [ -n "$PR_URL" ] || usage_die "arm requires --pr <pr-url>"
  fm_pr_url_parse "$PR_URL" || die "'$PR_URL' is not a pull request or merge request URL"
  [ "$FM_PR_PROVIDER" = github ] \
    || die "watching a check set is supported for GitHub pull requests only, not $FM_PR_PROVIDER"

  fm_ci_state_root_valid "$STATE" \
    || die "state directory is unavailable: '$STATE' is not an existing directory"

  # The poll is silent on every error by design, so a missing gh would be
  # indistinguishable from a build that never finishes. Arming is the one point
  # where that can be reported, so it is refused here instead of watching
  # nothing.
  command -v gh >/dev/null 2>&1 \
    || die "watching a pull request's check set requires gh on PATH"

  fm_ci_artifact_path "$STATE" "$TASK" .check.sh || die "could not resolve the check path"
  check=$FM_CI_ARTIFACT
  fm_ci_artifact_path "$STATE" "$TASK" .ci-watch || die "could not resolve the watch path"
  data=$FM_CI_ARTIFACT
  fm_ci_artifact_path "$STATE" "$TASK" .ci-watch-fired || die "could not resolve the marker path"
  fired=$FM_CI_ARTIFACT
  device=$(fm_pr_file_device "$STATE") || die "state directory is unavailable"

  # A check already armed at this path belongs to whoever armed it. Overwriting
  # a merge poll, or any other check, would silently disarm it, so this refuses
  # unless the file is absent or is already this task's own shim.
  if [ -e "$check" ] || [ -L "$check" ]; then
    fm_ci_shim_matches "$check" "$FM_HOME" "$FM_ROOT" "$TASK" \
      || die "a different check is already armed at state/$TASK.check.sh; disarm its owner first"
  fi
  fm_pr_regular_destination_on_device_or_absent "$data" "$device" \
    || die "the watch sidecar path is unavailable"
  fm_pr_regular_destination_on_device_or_absent "$fired" "$device" \
    || die "the watch marker path is unavailable"

  now=$(date +%s) || die "could not read the current time"

  umask 077
  # The marker goes first: a re-arm after a fix push must not inherit the
  # previous arming's already-reported verdict, and clearing it before the new
  # sidecar exists can only cost a silent cycle, never a stale wake.
  fm_ci_remove_artifact "$STATE" "$TASK" .ci-watch-fired \
    || die "refusing to replace state/$TASK.ci-watch-fired: it is not a plain file this watch published"

  tmp=$(mktemp "$STATE/.fm-ci-watch.XXXXXX") || die "could not stage the watch sidecar"
  printf '%s\n%s\n%s\n' "$FM_CI_WATCH_MAGIC" "$FM_PR_URL" "$now" > "$tmp" \
    || die "could not write the watch sidecar"
  chmod 0600 "$tmp" || die "could not secure the watch sidecar"
  mv -f -- "$tmp" "$data" || die "could not publish the watch sidecar"

  tmp=$(mktemp "$STATE/.fm-ci-check.XXXXXX") || die "could not stage the check"
  fm_ci_shim_content "$FM_HOME" "$FM_ROOT" "$TASK" > "$tmp" || die "could not write the check"
  chmod 0700 "$tmp" || die "could not secure the check"
  mv -f -- "$tmp" "$check" || die "could not publish the check"

  "$SCRIPT_DIR/fm-check-register.sh" "$TASK" >/dev/null || die "could not register the check"
  printf 'watching: %s for %s\n' "$FM_PR_URL" "$TASK"
  printf 'armed: state/%s.check.sh (registered)\n' "$TASK"
}

# Disarming removes files, so it asserts the facts that make them ours before it
# removes anything: a real state root, a path-safe task id, an armed watch
# sidecar, and a check that is this task's own shim.
cmd_disarm() {
  local check suffix
  parse_args "$@"
  [ -z "$PR_URL" ] || usage_die "disarm takes no --pr"
  fm_ci_state_root_valid "$STATE" \
    || die "refusing to remove anything: '$STATE' is not an existing state directory"
  fm_ci_watch_armed "$STATE" "$TASK" \
    || die "refusing to remove anything: no build watch is armed for $TASK"

  fm_ci_artifact_path "$STATE" "$TASK" .check.sh || die "could not resolve the check path"
  check=$FM_CI_ARTIFACT
  if [ -e "$check" ] || [ -L "$check" ]; then
    fm_ci_shim_matches "$check" "$FM_HOME" "$FM_ROOT" "$TASK" \
      || die "refusing to remove state/$TASK.check.sh: it is not this watch's own shim"
  fi

  for suffix in .check.sh .check-trust .ci-watch .ci-watch-fired; do
    fm_ci_remove_artifact "$STATE" "$TASK" "$suffix" \
      || die "refusing to remove state/$TASK$suffix: it is not a plain file this watch published"
  done
  printf 'disarmed %s\n' "$TASK"
}

cmd_status() {
  local fired line
  parse_args "$@"
  [ -z "$PR_URL" ] || usage_die "status takes no --pr"
  if ! fm_ci_watch_read "$STATE" "$TASK"; then
    printf 'not armed: %s\n' "$TASK"
    exit 1
  fi
  printf 'watching: %s for %s\n' "$FM_CI_WATCH_URL" "$TASK"
  printf 'armed_at: %s\n' "$FM_CI_WATCH_ARMED_AT"
  fm_ci_artifact_path "$STATE" "$TASK" .ci-watch-fired || exit 0
  fired=$FM_CI_ARTIFACT
  if fm_ci_plain_file "$fired" && IFS= read -r line < "$fired"; then
    printf 'reported: %s\n' "$line"
  else
    printf 'reported: (nothing yet)\n'
  fi
}

[ "$#" -gt 0 ] || usage_die "a command is required"
CMD=$1
shift
case $CMD in
  -h|--help|help) print_help ;;
  arm) cmd_arm "$@" ;;
  disarm) cmd_disarm "$@" ;;
  status) cmd_status "$@" ;;
  *) usage_die "unknown command '$CMD'" ;;
esac
