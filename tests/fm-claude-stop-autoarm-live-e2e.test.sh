#!/usr/bin/env bash
# Opt-in credentialed Claude live regression for the Stop-owned auto-arm
# (bin/fm-claude-stop-autoarm.sh + bin/fm-turnend-guard.sh --claude).
# Proves, against the real installed Claude Code and the real tracked hook
# registration: the run-tier SessionStart hook lands a complete session-start
# digest in model context before the first turn and reclaims the dead owner's
# session lock; at least two tokenless auto-arm and rewake cycles then complete
# with zero model-issued arm commands and exactly one wake drain each; the
# epoch ledger records the rewake outcome and releases its owner lock; and the
# cooperative guard consumes no forced continuation while the hook's launch is
# healthy.
# Claude is a RUN-tier session-open harness (docs/sessionstart-nudge.md), so the
# hook - not the model - runs bin/fm-session-start.sh, and AGENTS.md section 3
# makes a model-issued run explicitly optional against that delivered digest.
# This guard therefore asserts that the digest reached context before the first
# turn, never which actor produced it, and its fixture budget is measured in
# hook-owned arm cycles so an optional extra model-issued session start cannot
# change the verdict.
# The project and FM_HOME are isolated; Claude keeps using its existing managed
# authentication. No live fleet home, worktree, or session is touched.
# shellcheck disable=SC2016 # the model, not this test shell, reads the prompt text
set -u

if [ "${FM_CLAUDE_LIVE_E2E:-0}" != 1 ]; then
  echo "skip: set FM_CLAUDE_LIVE_E2E=1 to run the Claude Stop auto-arm regression"
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

command -v claude >/dev/null 2>&1 || fail "claude not found"
# The tool-call ledger hook and the transcript assertions both parse JSON.
command -v jq >/dev/null 2>&1 || fail "jq not found"

LAB="$ROOT/.claude-autoarm-live-e2e.$$"
PROJECT="$LAB/project"
HOME_DIR="$LAB/fmhome"
LIVE_OWNER_HOME="$LAB/live-owner-home"
TRANSCRIPT="$LAB/claude.jsonl"
CLAUDE_VERSION=$(claude --version)

cleanup() {
  rm -rf "$LAB"
}
trap cleanup EXIT

mkdir -p "$LAB"
# git clone of this worktree carries only committed state, so copy the
# working-tree surfaces under test (same pattern as the continuity live E2E).
git clone -q "$ROOT" "$PROJECT"
cp -R "$ROOT/bin/." "$PROJECT/bin/"
cp "$ROOT/.claude/settings.json" "$PROJECT/.claude/settings.json"
# The lab keeps the real tracked .claude/settings.json SessionStart run-tier
# registration, Stop guard, and asyncRewake auto-arm registration.
# The only local hook records model-issued Bash calls without acquiring the
# session lock or otherwise changing lifecycle behavior.
cat > "$PROJECT/.claude/settings.local.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/bin/tool-logger.sh" }
        ]
      }
    ]
  }
}
JSON

cat > "$PROJECT/bin/tool-logger.sh" <<'SH'
#!/usr/bin/env bash
P=$(cat 2>/dev/null || true)
printf '%s\n' "$P" | jq -r '.tool_input.command // "unknown"' >> "$FM_HOME/state/tool-calls.log" 2>/dev/null
exit 0
SH
chmod +x "$PROJECT/bin/tool-logger.sh"

mkdir -p "$HOME_DIR/state" "$HOME_DIR/config" "$HOME_DIR/data"
printf 'project=fixture\nwindow=fixture\nbackend=tmux\n' > "$HOME_DIR/state/task.meta"
# A numeric pid above the supported OS pid range is a demonstrably dead prior
# harness owner under fm_harness_pid_alive, matching the reproduced incident.
printf '9999999\n' > "$HOME_DIR/state/.lock"

# Rapid-death arm fixture: started plus an immediate actionable reason, the
# exact spent-Stop edge shape. Runs 1-2 close actionable; run 3 is a safety
# valve that ends the in-flight need so a misbehaving session can never loop
# forever.
cat > "$PROJECT/bin/fm-watch-arm.sh" <<'SH'
#!/usr/bin/env bash
N=$(cat "$FM_HOME/state/arm-count" 2>/dev/null || echo 0); N=$((N+1)); echo "$N" > "$FM_HOME/state/arm-count"
echo "arm-run=$N pid=$$" >> "$FM_HOME/state/arm-ran"
if [ "$N" -ge 3 ]; then
  rm -f "$FM_HOME/state/task.meta"
  printf 'watcher: attached pid=%s (beacon 2s)\n' "$$"
  exit 0
fi
printf 'watcher: started pid=%s (beacon fresh)\n' "$$"
printf 'stale: fixture-rapid-%s\n' "$N"
exit 0
SH
# Drain fixture: session start invokes it once through the run-tier hook, and
# the model invokes it once per rewake. It ends the in-flight need after the
# SECOND hook-owned arm cycle rather than after a fixed number of drains,
# because the number of drains depends on how many times session start ran and
# an optional model-issued session start must not shorten the budget.
cat > "$PROJECT/bin/fm-wake-drain.sh" <<'SH'
#!/usr/bin/env bash
N=$(cat "$FM_HOME/state/drain-count" 2>/dev/null || echo 0); N=$((N+1)); echo "$N" > "$FM_HOME/state/drain-count"
echo "drain-run=$N" >> "$FM_HOME/state/drain-ran"
ARMS=$(cat "$FM_HOME/state/arm-count" 2>/dev/null || echo 0)
if [ "$ARMS" -ge 2 ]; then
  rm -f "$FM_HOME/state/task.meta"
fi
printf 'stale: fixture-rapid drained\n'
SH
chmod +x "$PROJECT/bin/fm-watch-arm.sh" "$PROJECT/bin/fm-wake-drain.sh"

# The prompt deliberately leaves a model-issued bin/fm-session-start.sh
# OPTIONAL, exactly as AGENTS.md section 3 does on a run-tier harness, and
# bounds the rest of the tool surface so the auto-arm's own cost is measurable.
PROMPT='Reply with exactly CYCLE0 and stop. Whenever a Stop hook feedback message wakes you, run exactly `bin/fm-wake-drain.sh` once with Bash, then reply with exactly ACK and stop. Never run bin/fm-watch-arm.sh or any other arm command. Apart from bin/fm-wake-drain.sh and an optional bin/fm-session-start.sh, use no tools at all.'

(
  cd "$PROJECT" || exit 1
  FM_HOME="$HOME_DIR" CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false \
    claude -p "$PROMPT" --dangerously-skip-permissions --effort low --output-format stream-json --verbose
) > "$TRANSCRIPT" 2>&1 || fail "Claude credentialed auto-arm session failed: $(tail -20 "$TRANSCRIPT")"

# An account quota refusal ends the session mid-cycle and would otherwise
# surface as a misleading shortfall in the cycle counts below, so name that
# environmental cause before asserting anything about the auto-arm.
RATE_REJECTED=$(jq -Rr 'fromjson? | select(.type=="rate_limit_event") | .rate_limit_info.status' \
  < "$TRANSCRIPT" 2>/dev/null | grep -c '^rejected$' || true)
[ "${RATE_REJECTED:-0}" -eq 0 ] \
  || fail "Claude account quota rejected this live session, so no auto-arm behavior was measured; rerun with available quota: $(tail -3 "$TRANSCRIPT")"

ARM_RUNS=$(wc -l < "$HOME_DIR/state/arm-ran" 2>/dev/null | tr -d ' ')
[ "$ARM_RUNS" = 2 ] || fail "expected exactly 2 hook-owned arm cycles, got $ARM_RUNS: $(cat "$HOME_DIR/state/arm-ran" 2>/dev/null)"
REWAKES=$(grep -c 'Stop hook feedback' "$TRANSCRIPT" 2>/dev/null || true)
[ "$REWAKES" -ge 2 ] || fail "expected at least 2 exit-2 rewake deliveries, got $REWAKES"
grep -q 'stale: fixture-rapid-1' "$TRANSCRIPT" || fail "first rapid rewake reason missing from the transcript"
grep -q 'stale: fixture-rapid-2' "$TRANSCRIPT" || fail "second rapid rewake reason missing from the transcript"

# Claude is run tier: the SessionStart hook executes bin/fm-session-start.sh and
# its digest lands in context BEFORE the first model turn, which is exactly what
# makes a model-issued run optional. Assert that delivery and its ordering, not
# which actor produced it.
DIGEST_LINE=$(grep -n -m1 -F "SESSION START - $HOME_DIR" "$TRANSCRIPT" | cut -d: -f1)
[ -n "$DIGEST_LINE" ] || fail "the session-start digest never reached this session's context"
FIRST_TURN_LINE=$(grep -n -m1 '"type":"assistant"' "$TRANSCRIPT" | cut -d: -f1)
[ -n "$FIRST_TURN_LINE" ] || fail "the credentialed session produced no model turn at all"
[ "$DIGEST_LINE" -lt "$FIRST_TURN_LINE" ] \
  || fail "the session-start digest reached context only after the first model turn (digest at line $DIGEST_LINE, first turn at line $FIRST_TURN_LINE)"
grep -q 'Stop-owned auto-arm' "$TRANSCRIPT" \
  || fail "the delivered digest did not carry the Stop-owned supervision instruction"
[ "$(cat "$HOME_DIR/state/.lock" 2>/dev/null)" != 9999999 ] \
  || fail "session start did not reclaim the stale dead-owner lock"

# The ledger of model-issued Bash calls is what proves the continuity was
# tokenless: one wake drain per rewake, no arm command, and no shell ampersand.
TOOL_LOG="$HOME_DIR/state/tool-calls.log"
[ -f "$TOOL_LOG" ] || fail "no model tool call was recorded, so the session never handled a rewake"
MODEL_DRAINS=$(grep -c 'fm-wake-drain\.sh' "$TOOL_LOG" || true)
[ "$MODEL_DRAINS" = 2 ] \
  || fail "expected exactly 2 model-issued wake drains, one per Stop-owned rewake, got $MODEL_DRAINS: $(cat "$TOOL_LOG")"
! grep -q 'fm-watch-arm.sh' "$TOOL_LOG" \
  || fail "model issued an arm command despite Stop-owned continuity: $(cat "$TOOL_LOG")"
! grep -q '&' "$TOOL_LOG" \
  || fail "model used a shell ampersand: $(cat "$TOOL_LOG")"
# Every drain is accounted for: the run-tier hook's own session start drained
# once, each optional model-issued session start drained once more, and the
# model drained once per rewake. Nothing else may have drained.
MODEL_SESSION_STARTS=$(grep -c 'fm-session-start\.sh' "$TOOL_LOG" || true)
DRAIN_RUNS=$(wc -l < "$HOME_DIR/state/drain-ran" 2>/dev/null | tr -d ' ')
EXPECTED_DRAINS=$((1 + MODEL_SESSION_STARTS + MODEL_DRAINS))
[ "$DRAIN_RUNS" = "$EXPECTED_DRAINS" ] \
  || fail "expected $EXPECTED_DRAINS drains (1 hook session start + $MODEL_SESSION_STARTS model session starts + $MODEL_DRAINS wake drains), got $DRAIN_RUNS"

! grep -q 'TURN WOULD END BLIND' "$TRANSCRIPT" \
  || fail "cooperative guard consumed a forced continuation while the auto-arm launch was healthy"
[ "$(sed -n 's/^.*outcome=\([a-z][a-z]*\) .*$/\1/p' "$HOME_DIR/state/.claude-autoarm-epoch" 2>/dev/null)" = rewake ] \
  || fail "auto-arm epoch ledger must record the rewake outcome"
[ ! -e "$HOME_DIR/state/.claude-autoarm.lock" ] || fail "auto-arm owner lock was left behind"

# Live-owner negative control: a separate supported-harness process owns a
# second isolated home while another Stop hook fires from the same primary
# project. The competing hook must not replace the session lock, arm, write an
# epoch, or rewake.
FAKE_CLAUDE="$LAB/claude"
ln -s /bin/bash "$FAKE_CLAUDE"
mkdir -p "$LIVE_OWNER_HOME/state" "$LIVE_OWNER_HOME/config"
printf 'project=fixture\n' > "$LIVE_OWNER_HOME/state/task.meta"
"$FAKE_CLAUDE" -c 'sleep 3; :' &
LIVE_OWNER_PID=$!
printf '%s\n' "$LIVE_OWNER_PID" > "$LIVE_OWNER_HOME/state/.lock"
LIVE_OWNER_RC=0
printf '%s\n' '{"session_id":"live-owner-control"}' \
  | FM_HOME="$LIVE_OWNER_HOME" FM_ROOT_OVERRIDE="$PROJECT" "$FAKE_CLAUDE" -c '"$FM_ROOT_OVERRIDE/bin/fm-claude-stop-autoarm.sh"' \
      >"$LAB/live-owner.out" 2>"$LAB/live-owner.err" || LIVE_OWNER_RC=$?
[ "$LIVE_OWNER_RC" -eq 0 ] || fail "competing Stop hook returned $LIVE_OWNER_RC while another live session owned the home"
[ "$(cat "$LIVE_OWNER_HOME/state/.lock")" = "$LIVE_OWNER_PID" ] || fail "competing Stop hook replaced the live session owner"
[ ! -e "$LIVE_OWNER_HOME/state/arm-ran" ] || fail "competing Stop hook armed while another live session owned the home"
[ ! -e "$LIVE_OWNER_HOME/state/.claude-autoarm-epoch" ] || fail "competing Stop hook wrote an epoch while another live session owned the home"
[ ! -s "$LAB/live-owner.out" ] && [ ! -s "$LAB/live-owner.err" ] || fail "competing Stop hook produced a rewake while another live session owned the home"
wait "$LIVE_OWNER_PID"

printf 'ok - Claude %s live E2E delivered the session-start digest before the first turn, reclaimed a stale session lock, completed two tokenless Stop-owned rewake cycles, and preserved the competing-live-owner boundary\n' "$CLAUDE_VERSION"
