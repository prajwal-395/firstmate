#!/usr/bin/env bash
# agy Stop-hook adapter for the firstmate PRIMARY turn-end guard.
#
# agy's Stop hook receives a JSON payload on stdin that includes session_id
# and stop_hook_active. The payload shape matches Grok's native path closely:
# stop_hook_active=true means the current stop already follows a guard block,
# so this guard must allow it (one-shot loop prevention). When stop_hook_active
# is absent or false, the shared guard predicate in bin/fm-turnend-guard.sh
# decides whether to block.
#
# agy hooks MUST exit 0 - a non-zero exit is treated as hook failure, not a
# semantic signal. When the guard wants to block (exit 2 from the shared
# guard), this adapter uses the legacy one-resume path: inject a prompt into
# the session via agy --continue to force the agent to re-arm supervision.
set -u

PAYLOAD=$(cat 2>/dev/null || true)
[ -n "$PAYLOAD" ] || exit 0

command -v jq >/dev/null 2>&1 || exit 0
# Validate that the payload is well-formed JSON with expected fields.
printf '%s' "$PAYLOAD" | jq -n --stream -e '
  reduce inputs as $item (
    {};
    if (
      ($item | length) == 2
      and ($item[0] | length) > 0
      and (
        $item[0][0] == "session_id"
        or $item[0][0] == "stop_hook_active"
      )
    ) then
      .[$item[0][0]] = ((.[$item[0][0]] // 0) + 1)
    else
      .
    end
  )
  | all(.[]; . == 1)
' >/dev/null 2>&1 || exit 0

# Check if stop_hook_active is present and true (loop prevention).
STOP_ACTIVE=$(printf '%s' "$PAYLOAD" | jq -er '
  if type != "object" then error("payload")
  elif has("stop_hook_active") then
    if (.stop_hook_active | type) == "boolean" then .stop_hook_active
    else error("stop_hook_active type")
    end
  else false
  end
' 2>/dev/null) || exit 0

# If stop_hook_active is true, this stop follows a prior guard block.
# Allow it unconditionally (one-shot loop prevention, same as Grok).
[ "$STOP_ACTIVE" = "true" ] && exit 0

ROOT=${ANTIGRAVITY_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-}}
[ -n "$ROOT" ] || exit 0
ROOT=${ROOT%/}
[ -x "$ROOT/bin/fm-turnend-guard.sh" ] || exit 0

# Check if the guard wants to block. agy cannot exit 2 (hooks must exit 0),
# so when the guard blocks we inject a resume prompt to force continuation.
[ -n "${AGY_TURNEND_GUARD_ACTIVE:-}" ] && exit 0
SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -er '
  .session_id | select(type == "string" and length > 0)
' 2>/dev/null) || exit 0
command -v agy >/dev/null 2>&1 || exit 0

ERR=$(mktemp "${TMPDIR:-/tmp}/fm-turnend-agy.XXXXXX") || exit 0
trap 'rm -f "$ERR"' EXIT

printf '%s' "$PAYLOAD" | "$ROOT/bin/fm-turnend-guard.sh" 2>"$ERR"
RC=$?
[ "$RC" -eq 2 ] || exit 0

REASON=$(cat "$ERR" 2>/dev/null || true)
[ -n "$REASON" ] || REASON='tasks in flight, no live watcher - repair missing watcher supervision according to the session-start operating block before ending the turn'
# shellcheck source=bin/fm-operational-input.sh
. "$ROOT/bin/fm-operational-input.sh"
fm_operational_input_encode turn-end-guard \
  "TURN WOULD END BLIND - supervision is off. Repair missing watcher supervision according to the session-start operating block before ending the turn.

$REASON" \
  PROMPT || exit 0

AGY_TURNEND_GUARD_ACTIVE=1 \
  agy --continue "$SESSION_ID" \
    -p "$PROMPT" >/dev/null 2>&1 || true
