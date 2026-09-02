#!/usr/bin/env bash
# Watcher program for one PR's build-verdict watch.
#
# It prints exactly one line when firstmate should wake and stays silent
# otherwise, including on every error, so a failed lookup can never be read as
# a passing build. bin/fm-ci-check.sh arms it; bin/fm-ci-lib.sh owns the
# sidecar and shim shapes.
#
# Usage: fm-ci-poll.sh --task <task-id>
#
# The wake line is one of:
#   ci passed <url>        every check reported a passing conclusion
#   ci failed <url>        at least one check reported a failing conclusion
#   ci conflicting <url>   the PR is CONFLICTING, so its check set is not a verdict
#   ci no-checks <url>     no check ever appeared within the grace window
#   ci closed <url>        the PR was merged or closed while the watch was armed
#
# A check set with anything still queued or running is NOT a verdict and stays
# silent. An empty check set is not a pass either: it stays silent until
# FM_CI_NO_CHECKS_GRACE seconds after arming and then wakes as no-checks, so a
# conflicting or workflow-less branch surfaces as a question instead of being
# mistaken for green or waited on forever.
#
# The wake fires once per arming. bin/fm-ci-check.sh clears the marker when the
# watch is re-armed after a fix push, which is what makes the next verdict wake
# again without the watcher re-reporting a verdict nobody has acted on yet.
set -u
LC_ALL=C
export LC_ALL

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-pr-lib.sh
. "$SCRIPT_DIR/fm-pr-lib.sh" 2>/dev/null || exit 0
# shellcheck source=bin/fm-ci-lib.sh
. "$SCRIPT_DIR/fm-ci-lib.sh" 2>/dev/null || exit 0

GRACE=${FM_CI_NO_CHECKS_GRACE:-900}
case "$GRACE" in
  ''|*[!0-9]*) GRACE=900 ;;
esac

[ "$#" -eq 2 ] && [ "$1" = --task ] || exit 0
ID=$2
fm_pr_task_id_valid "$ID" || exit 0

fm_ci_watch_read "$STATE" "$ID" || exit 0
URL=$FM_CI_WATCH_URL
ARMED_AT=$FM_CI_WATCH_ARMED_AT
[ "$FM_PR_PROVIDER" = github ] || exit 0

# Already reported for this arming: stay silent rather than spending a
# supervision turn on a verdict firstmate has already been handed.
fm_ci_artifact_path "$STATE" "$ID" .ci-watch-fired || exit 0
FIRED=$FM_CI_ARTIFACT
[ ! -e "$FIRED" ] && [ ! -L "$FIRED" ] || exit 0

command -v gh >/dev/null 2>&1 || exit 0

# gh normalizes nothing here, so the two rollup shapes are normalized to one
# bucket each. Every unrecognized conclusion buckets as a failure: the only
# direction an unknown value may take is the one that cannot be read as green.
read -r -d '' QUERY <<'JQ' || true
def cbucket:
  if .__typename == "StatusContext"
  then (.state // "PENDING")
    | if . == "PENDING" or . == "EXPECTED" then "pending"
      elif . == "SUCCESS" then "pass"
      else "fail" end
  else
    if (.status // "COMPLETED") != "COMPLETED" then "pending"
    else (.conclusion // "")
      | if . == "SUCCESS" or . == "NEUTRAL" or . == "SKIPPED" then "pass" else "fail" end
    end
  end;
((.statusCheckRollup // []) | map(cbucket)) as $b
| [ (.state // ""), (.mergeable // ""), ($b | length),
    ($b | map(select(. == "pending")) | length),
    ($b | map(select(. == "fail")) | length) ] | @tsv
JQ

RAW=$(gh pr view "$URL" --json state,mergeable,statusCheckRollup -q "$QUERY" 2>/dev/null) || exit 0
IFS=$'\t' read -r PR_STATE MERGEABLE TOTAL PENDING FAILED <<< "$RAW" || exit 0
case "${TOTAL:-}${PENDING:-}${FAILED:-}" in
  ''|*[!0-9]*) exit 0 ;;
esac

VERDICT=
if [ "$PR_STATE" = MERGED ] || [ "$PR_STATE" = CLOSED ]; then
  VERDICT=closed
elif [ "$MERGEABLE" = CONFLICTING ]; then
  VERDICT=conflicting
elif [ "$TOTAL" -eq 0 ]; then
  NOW=$(date +%s 2>/dev/null) || exit 0
  [ "$NOW" -ge "$((ARMED_AT + GRACE))" ] || exit 0
  VERDICT=no-checks
elif [ "$PENDING" -gt 0 ]; then
  exit 0
elif [ "$FAILED" -gt 0 ]; then
  VERDICT=failed
else
  VERDICT=passed
fi

LINE="ci $VERDICT $URL"

# Publish the marker BEFORE printing. A crash between the two costs one missed
# wake that re-arming recovers; the other order costs a wake every cycle until
# firstmate happens to act, which is the loop this whole watch exists to end.
umask 077
TMP=$(mktemp "$STATE/.fm-ci-fired.XXXXXX") || exit 0
printf '%s\n' "$LINE" > "$TMP" || { rm -f -- "$TMP"; exit 0; }
chmod 0600 "$TMP" || { rm -f -- "$TMP"; exit 0; }
mv -f -- "$TMP" "$FIRED" || { rm -f -- "$TMP"; exit 0; }

printf '%s\n' "$LINE"
