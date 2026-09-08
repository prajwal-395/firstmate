#!/usr/bin/env bash
# Send a published build verdict straight to the waiting worker.
#
# bin/fm-ci-poll.sh calls this after publishing the verdict marker, so one
# verdict wakes TWO parties: firstmate through the check line the poll prints,
# and the waiting worker through this send. The worker is already paused for
# exactly this information, so routing the fact through a supervisor turn only
# adds latency. What stays with firstmate is the merge decision and its QA;
# this carries facts, never judgement.
#
# Usage: fm-ci-notify.sh --task <task-id>
#
# It reads state/<task>.ci-watch-fired (the verdict line the poll just
# published), builds a message that lets the worker act without going back to
# the forge - a failure names the failing checks - and sends it with
# bin/fm-send.sh. It then records the outcome as a second marker line,
# "notified: ok" or "notified: send-failed", which bin/fm-ci-check.sh status
# reports and which tells firstmate whether the worker still needs the relay
# the old two-hop path provided (notably when the worker has since exited).
#
# This is best-effort by contract: exit 0 with "notified: ok" when the send
# was accepted (fm-send exit 0, or exit 3 delivered-but-unconfirmed), anything
# else records "notified: send-failed". Either way the caller still prints its
# own firstmate wake, so a dead worker can never cost firstmate its verdict.
# Stdout stays silent so a poll caller can run this without polluting the
# single wake line; diagnostics go to stderr.
#
# Environment:
#   FM_HOME               operational home whose state/ holds the watch
#   FM_CI_NOTIFY_SEND_BIN send backend, defaulting to bin/fm-send.sh beside
#                         this script (a test seam: point it at a stub)
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

[ "$#" -eq 2 ] && [ "$1" = --task ] || die "usage: fm-ci-notify.sh --task <task-id>"
ID=$2
fm_pr_task_id_valid "$ID" || die "invalid task id '$ID'"

fm_ci_artifact_path "$STATE" "$ID" .ci-watch-fired || die "could not resolve the marker path"
FIRED=$FM_CI_ARTIFACT
fm_ci_plain_file "$FIRED" || die "no verdict has been published for $ID"

VERDICT_LINE=$(IFS= read -r line < "$FIRED"; printf '%s' "$line")
[ -n "$VERDICT_LINE" ] || die "the published verdict is empty"

# The verdict line is "ci <verdict> <url>"; the URL is re-parsed, never
# trusted as free text, before it is used for the failing-check lookup.
VERDICT=${VERDICT_LINE#ci }
VERDICT=${VERDICT%% *}
PR_URL=${VERDICT_LINE#ci * }
PR_URL=${PR_URL#* }
case "$VERDICT" in
  passed|failed|conflicting|no-checks|closed) ;;
  *) die "the published verdict is not one this notifier sends: '$VERDICT_LINE'" ;;
esac

# Failing-check detail, so a red verdict does not send the worker back to
# poll the forge for what failed. Any lookup failure degrades to the verdict
# alone rather than blocking the send: a fact without detail still beats a
# worker that feels it must watch for itself.
DETAIL=
if [ "$VERDICT" = failed ] && fm_pr_url_parse "$PR_URL" 2>/dev/null \
  && [ "$FM_PR_PROVIDER" = github ] && command -v gh >/dev/null 2>&1; then
  read -r -d '' NAMES_QUERY <<'JQ' || true
[.statusCheckRollup[] |
  if .__typename == "StatusContext"
  then select(.state != "SUCCESS") | "status \(.context // "?"): \(.state // "?")"
  else select(.status != "COMPLETED" or ((.conclusion // "") != "SUCCESS" and (.conclusion // "") != "NEUTRAL" and (.conclusion // "") != "SKIPPED")) | "check \(.name // "?"): \(.conclusion // .status // "?")"
  end] | join("\n")
JQ
  RAW_NAMES=$(gh pr view "$FM_PR_URL" --json statusCheckRollup -q "$NAMES_QUERY" 2>/dev/null) || RAW_NAMES=
  if [ -n "$RAW_NAMES" ]; then
    # Strip control bytes but keep newlines and UTF-8: forge-derived names are
    # message content, never commands, and must not smuggle terminal escapes.
    CLEAN_NAMES=$(printf '%s' "$RAW_NAMES" | LC_ALL=C tr -d '\000-\010\013\014\016-\037\177' | head -c 2000)
    [ -n "$CLEAN_NAMES" ] || CLEAN_NAMES="(names unavailable)"
    DETAIL=$(printf 'Failing checks:\n%s\nSee them with: gh pr view %s --json statusCheckRollup' \
      "$CLEAN_NAMES" "$FM_PR_URL")
  fi
fi

MESSAGE=
case "$VERDICT" in
  passed)
    MESSAGE="Build verdict: $VERDICT_LINE. Firstmate was woken with the same verdict and keeps the merge decision. Per your brief, report done; do not re-check the build." ;;
  failed)
    MESSAGE="Build verdict: $VERDICT_LINE. Firstmate was woken with the same verdict and keeps the merge decision and its QA; the fix below is yours."
    [ -z "$DETAIL" ] || MESSAGE="$MESSAGE"$'\n'"$DETAIL"
    MESSAGE="$MESSAGE"$'\n'"Fix on your branch, push, then re-arm the watch per your brief. Do not poll the check set." ;;
  conflicting)
    MESSAGE="Build verdict: $VERDICT_LINE. The branch conflicts, so the check set is not a verdict. Firstmate was woken too. Rebase per your brief and re-arm the watch; do not poll." ;;
  no-checks)
    MESSAGE="Build verdict: $VERDICT_LINE. No check ever appeared for the PR, so there is nothing green to report. Firstmate was woken too. Report what is missing per your brief; do not poll." ;;
  closed)
    MESSAGE="Build verdict: $VERDICT_LINE. The PR was merged or closed while the watch was armed, so the wait is over. Firstmate was woken too; follow up with firstmate rather than the build." ;;
esac

SEND_BIN=${FM_CI_NOTIFY_SEND_BIN:-"$SCRIPT_DIR/fm-send.sh"}
SEND_RC=0
if [ -x "$SEND_BIN" ]; then
  FM_SEND_SETTLE=0 FM_HOME="$FM_HOME" "$SEND_BIN" "$ID" "$MESSAGE" >/dev/null 2>&1 || SEND_RC=$?
else
  SEND_RC=127
fi

# fm-send exit 3 is delivered-but-unconfirmed: the text reached the live pane
# and only the synchronous read-back stayed pending, so the worker was still
# woken. Anything else nonzero means it was not.
if [ "$SEND_RC" -eq 0 ] || [ "$SEND_RC" -eq 3 ]; then
  NOTIFIED="notified: ok $ID"
else
  NOTIFIED="notified: send-failed $ID"
fi

# Record the outcome on the marker for `fm-ci-check.sh status` and for a
# firstmate reconciling an exited worker. Best-effort: losing this line must
# never fail a verdict that was already published and woken.
if ! grep -q '^notified: ' "$FIRED" 2>/dev/null; then
  printf '%s\n' "$NOTIFIED" >> "$FIRED" 2>/dev/null || true
fi

[ "$SEND_RC" -eq 0 ] || [ "$SEND_RC" -eq 3 ]
