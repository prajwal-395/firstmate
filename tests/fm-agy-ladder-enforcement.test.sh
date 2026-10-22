#!/usr/bin/env bash
# tests/fm-agy-ladder-enforcement.test.sh - the regression for the agy model
# ladder gate.
#
# The captain's ladder used to be prose only, so obeying it depended on an agent
# remembering it. This suite pins the tooling that now refuses instead. It
# drives the decision library directly for the rule matrix, and the real
# bin/fm-spawn.sh for the part that actually matters: that the gate sits on the
# ordinary dispatch path and fires there, not just in a library nobody calls.
#
# The load-bearing contracts:
#   1. Descending past a rung that still has headroom is refused, and the reason
#      names the rung and the percentage it acted on.
#   2. Descending with NO evidence about a rung above is refused too. Absence is
#      not permission; descending is the move the policy constrains.
#   3. Opus 4.6 is refused at or below its 25% floor - the captain's reserved
#      quarter - wherever it sits on the ladder, while other models exhaust to 0.
#   4. The honest paths still launch: rung 1 with healthy quota, rung 1 with no
#      evidence at all (a fresh home must not be wedged), and a lower rung once
#      every rung above is recorded exhausted.
#   5. The override launches a refused request and SAYS SO. A silent override
#      would be worse than no gate.
#   6. Both spellings agy accepts resolve to the same rung, and a model the
#      ladder does not rank is allowed but reported.
#   7. Headroom is reserved for launches already in flight, so a burst just
#      above the floor cannot all clear it on one pre-burst reading.
#   8. The intake poll serves BOTH decisions the gate makes. Stale evidence did
#      not only fail the floor open - it also blocked every descent, because a
#      descent needs the rungs above PROVEN spent. One fresh poll answers every
#      rung, which is what keeps the ladder usable instead of pushing dispatch
#      off it onto a costlier model.
#   9. When the poll cannot answer, the gate still decides and says which
#      fallback it took: rung 1 launches, a descent does not.
#   10. The ladder order is settable on its own through agy_ladder while the
#      legacy default form keeps resolving exactly as before and is named as
#      legacy, so the coupled read is reported rather than silent.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# The shared lock helpers, loaded before the ladder exactly as bin/fm-spawn.sh
# loads them on the real dispatch path. The gate takes the account's reservation
# lock across its decide-and-reserve step, so a suite without these would
# exercise a gate that never locks - which is not the gate any dispatch runs.
# shellcheck source=bin/fm-wake-lib.sh
. "$ROOT/bin/fm-wake-lib.sh"
# shellcheck source=bin/fm-agy-ladder-lib.sh
. "$ROOT/bin/fm-agy-ladder-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-agy-ladder)

# The rule-matrix cases below decide on readings this file writes by hand, so
# the live intake poll is off for them: a real poll would replace those readings
# with the host account's own and every expectation would turn on whatever agy
# says today. Every case that exercises the poll - including the bin/fm-spawn.sh
# ones - turns it back on explicitly, against a stub agy.
export FM_AGY_QUOTA_POLL=off
# Pinned rather than inherited so a host that tunes them cannot move a boundary
# this suite asserts against.
export FM_AGY_QUOTA_MAX_AGE=300
export FM_AGY_INFLIGHT_TTL=300
# The in-flight ledger is scoped to the agy ACCOUNT and stored machine-wide, so
# this suite is pointed at a scratch root. Without it these cases would read and
# write the operator's own reservations, and every fresh_state would inherit
# whatever the fleet had in flight.
export FM_AGY_SHARED_ROOT="$TMP_ROOT/agy-shared"
export FM_AGY_LADDER_INFLIGHT_MARGIN=1

RUNG1='Claude Opus 4.6 (Thinking)'
RUNG2='Gemini 3.8 Flash (High)'

# Readings are written through the real observer, which stamps them with the
# wall clock, so this suite never encodes the on-disk format and takes its own
# offsets from the same clock.
NOW=$(date +%s)

# record <state-dir> <display-model> <percent> <reset-window>
# Writes one reading exactly as bin/fm-watch.sh would, by handing the real
# observer the pane footer agy draws.
record() {
  local state=$1 model=$2 percent=$3 window=$4
  mkdir -p "$state"
  fm_agy_quota_observe "$model | ctx: 3.0% | quota: $percent% ($window)" "$state"
}

# fresh_state: a state dir with no agy readings at all, and no reservations
# standing against the account either.
#
# The in-flight ledger deliberately does NOT live under state/ - it is scoped to
# the agy account so every home on it reserves against one figure - so clearing
# the state dir alone would leave each case inheriting the launches the previous
# case authorized. A case that wants a clean slate wants a clean ACCOUNT.
fresh_state() {
  local dir="$TMP_ROOT/state-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  rm -f "$(fm_agy_inflight_path "$dir")" 2>/dev/null || true
  printf '%s\n' "$dir"
}

# gate_out <state-dir> <model>: run the gate, echo its output, return its code.
gate_out() {
  local state=$1 model=$2 out rc=0
  out=$(fm_agy_ladder_gate "$model" "$state") || rc=$?
  printf '%s' "$out"
  return "$rc"
}

# --- 1. Rung resolution ------------------------------------------------------

test_both_spellings_resolve_to_one_rung() {
  local rung
  rung=$(fm_agy_ladder_rung "$RUNG1") || fail "rung 1 display name must resolve"
  [ "$rung" = 1 ] || fail "expected rung 1 for '$RUNG1', got '$rung'"
  rung=$(fm_agy_ladder_rung claude-opus-4-6-thinking) || fail "rung 1 kebab id must resolve"
  [ "$rung" = 1 ] || fail "expected rung 1 for the kebab id, got '$rung'"

  rung=$(fm_agy_ladder_rung "$RUNG2") || fail "rung 2 display name must resolve"
  [ "$rung" = 2 ] || fail "expected rung 2 for '$RUNG2', got '$rung'"
  rung=$(fm_agy_ladder_rung gemini-3.8-flash-high) || fail "rung 2 kebab id must resolve"
  [ "$rung" = 2 ] || fail "expected rung 2 for the kebab id, got '$rung'"

  ! fm_agy_ladder_rung 'Gemini 3.1 Pro (High)' >/dev/null \
    || fail "a retired model agy no longer lists must not resolve to a rung"
  ! fm_agy_ladder_rung gemini-3.1-pro-high >/dev/null \
    || fail "a retired kebab id must not resolve to a rung"
  ! fm_agy_ladder_rung 'Gemini 3.7 Flash (High)' >/dev/null \
    || fail "a retired model agy no longer lists must not resolve to a rung"
  ! fm_agy_ladder_rung gemini-3.7-flash-high >/dev/null \
    || fail "a retired kebab id must not resolve to a rung"
  ! fm_agy_ladder_rung 'Gemini 3.1 Pro (Low)' >/dev/null \
    || fail "a reasoning class the ladder does not rank must not resolve to a rung"
  ! fm_agy_ladder_rung 'Claude Sonnet 4.6 (Thinking)' >/dev/null \
    || fail "a model outside the ladder must not resolve to a rung"
  pass "fm_agy_ladder_rung: both spellings agy accepts map onto the ladder, and nothing else does"
}

test_floors_are_the_captains_floors() {
  # The floor follows the MODEL, not the rung position. Opus carries the 25%
  # reserve wherever it sits; every other model exhausts to 0.
  [ "$(fm_agy_ladder_floor 1)" = 25 ] || fail "rung 1 (Opus) carries the captain's reserved 25%"
  [ "$(fm_agy_ladder_floor 2)" = 0 ] || fail "rung 2 (Flash) exhausts to 0"
  pass "fm_agy_ladder_floor: Opus carries 25% at its rung, the rest run to 0"
}

# --- 2. Refusals -------------------------------------------------------------

test_refuses_descending_past_an_available_rung() {
  local state out rc=0
  state=$(fresh_state descend-available)
  record "$state" "$RUNG1" 80.0 '4h 0m'

  out=$(gate_out "$state" "$RUNG2") || rc=$?
  [ "$rc" -eq 1 ] || fail "rung 2 must be refused while rung 1 still has headroom (rc=$rc)"
  assert_contains "$out" "error: agy ladder refuses" "the refusal must be an error line"
  assert_contains "$out" "rung 1 (Claude Opus 4.6 (Thinking))" "the reason must name the rung above"
  assert_contains "$out" "80.0%" "the reason must carry the evidence it acted on"
  assert_contains "$out" "25% floor" "the reason must name the floor it compared against"
  [ "$(printf '%s' "$out" | wc -l)" -eq 0 ] || fail "the refusal must be one line"
  pass "refuses a descent while a rung above still has headroom, naming the rung and the evidence"
}

test_refuses_descending_on_no_evidence() {
  local state out rc=0
  state=$(fresh_state descend-unknown)

  out=$(gate_out "$state" "$RUNG2") || rc=$?
  [ "$rc" -eq 1 ] || fail "rung 2 must be refused when nothing is known about rung 1 (rc=$rc)"
  assert_contains "$out" "rung 1 (Claude Opus 4.6 (Thinking)) has no current quota reading" \
    "the reason must say the rung above is unproven"

  # A reading that has outlived its own reset window is not evidence either:
  # the window it described has rolled over, so rung 1 may be full again.
  record "$state" "$RUNG1" 4.0 '1h 0m'
  rc=0
  out=$(fm_agy_ladder_check "$RUNG2" "$state" "$((NOW + 7200))") || rc=$?
  [ "$rc" -eq 1 ] || fail "an expired rung-1 reading must not authorize a descent (rc=$rc)"
  assert_contains "$out" "has no current quota reading" \
    "an expired reading must read as unknown, not as exhausted"
  pass "refuses a descent on absent or expired evidence about the rung above"
}

test_refuses_opus_at_the_captains_floor() {
  local state out rc=0
  state=$(fresh_state floor)
  # Opus sits at rung 1, so its 25% floor is enforced with no rung above needed.
  record "$state" "$RUNG1" 24.9 '4h 0m'

  out=$(gate_out "$state" "$RUNG1") || rc=$?
  [ "$rc" -eq 1 ] || fail "Opus must be refused below its 25% floor (rc=$rc)"
  assert_contains "$out" "rung 1 (Claude Opus 4.6 (Thinking)) is at 24.9%" \
    "the reason must name the rung and the reading"
  assert_contains "$out" "reserved for the captain" "the reason must say whose quarter it is"

  # The floor is inclusive: exactly 25% is already spent, as far as automatic
  # dispatch is concerned.
  record "$state" "$RUNG1" 25.0 '4h 0m'
  rc=0
  gate_out "$state" "$RUNG1" >/dev/null || rc=$?
  [ "$rc" -eq 1 ] || fail "Opus at exactly 25% must be refused; the floor is inclusive"

  # And just above it, Opus runs.
  record "$state" "$RUNG1" 25.1 '4h 0m'
  rc=0
  gate_out "$state" "$RUNG1" >/dev/null || rc=$?
  [ "$rc" -eq 0 ] || fail "Opus at 25.1% is above the floor and must launch (rc=$rc)"
  pass "refuses Opus at or below the captain's reserved 25% at its rung, and runs it just above"
}

test_refuses_an_exhausted_lower_rung() {
  local state out rc=0
  state=$(fresh_state lower-spent)
  # Exhaust rung 1 (Opus, floor=25) below its reserve and put Flash at 0%.
  record "$state" "$RUNG1" 10.0 '4h 0m'
  record "$state" "$RUNG2" 0.0 '4h 0m'

  # Opus at 10% is below its own 25% floor.
  out=$(gate_out "$state" "$RUNG1") || rc=$?
  [ "$rc" -eq 1 ] || fail "Opus at 10% must be refused even with no rung above it (rc=$rc)"
  assert_contains "$out" "reserved for the captain" \
    "the reason must name the captain's reserve"

  # Rung 2 (Flash) at 0% with its own 0% floor - that is a plain
  # exhaustion, distinct from the captain's reserved quarter.
  rc=0
  out=$(gate_out "$state" "$RUNG2") || rc=$?
  [ "$rc" -eq 1 ] || fail "rung 2 at 0% must be refused on its own 0% floor (rc=$rc)"
  assert_contains "$out" "rung 2 (Gemini 3.8 Flash (High)) is exhausted at 0.0%" \
    "the reason must name the spent rung and its reading"
  pass "refuses a rung that is itself exhausted, whether on its own floor or the captain's"
}

# --- 3. The honest paths still launch ---------------------------------------

test_rung_one_launches_with_healthy_quota() {
  local state rc=0 out
  state=$(fresh_state healthy)
  record "$state" "$RUNG1" 94.7 '4h 24m'

  out=$(gate_out "$state" "$RUNG1") || rc=$?
  [ "$rc" -eq 0 ] || fail "rung 1 with 94.7% remaining must launch (rc=$rc)"
  [ -z "$out" ] || fail "an ordinary clean allow must stay quiet, got '$out'"
  pass "rung 1 with healthy quota launches, and says nothing"
}

test_rung_one_launches_on_a_fresh_home() {
  local state rc=0 out
  state=$(fresh_state fresh-home)

  out=$(gate_out "$state" "$RUNG1") || rc=$?
  [ "$rc" -eq 0 ] || fail "rung 1 must launch on a home that has never drawn an agy pane (rc=$rc)"
  [ -z "$out" ] || fail "a fresh-home allow must stay quiet, got '$out'"

  # The same absence that permits rung 1 still blocks rung 2. That asymmetry is
  # the design, so assert the two verdicts diverge on identical evidence.
  rc=0
  gate_out "$state" "$RUNG2" >/dev/null || rc=$?
  [ "$rc" -eq 1 ] || fail "the same empty evidence must refuse rung 2 while permitting rung 1"
  pass "no evidence never wedges rung 1, and still blocks a descent from it"
}

test_lower_rung_launches_once_every_rung_above_is_spent() {
  local state rc=0 out
  state=$(fresh_state descend-ok)
  # Rung 1 (Opus, floor=25) below its reserve is exhausted.
  record "$state" "$RUNG1" 12.0 '4h 0m'

  # Rung 2 (Flash) with healthy quota launches, because rung 1 above is spent.
  record "$state" "$RUNG2" 60.0 '4h 0m'
  out=$(gate_out "$state" "$RUNG2") || rc=$?
  [ "$rc" -eq 0 ] || fail "rung 2 must launch once rung 1 is below its floor (rc=$rc)"
  [ -z "$out" ] || fail "an authorized descent must stay quiet, got '$out'"
  pass "a lower rung launches only once every rung above it is recorded exhausted"
}

test_off_ladder_models_are_allowed_but_reported() {
  local state rc=0 out
  state=$(fresh_state off-ladder)
  record "$state" "$RUNG1" 99.0 '4h 0m'

  out=$(gate_out "$state" 'Claude Sonnet 4.6 (Thinking)') || rc=$?
  [ "$rc" -eq 0 ] || fail "a model the ladder does not rank must not be refused by it (rc=$rc)"
  assert_contains "$out" "notice: agy ladder not applied" \
    "an off-ladder launch must be reported, not silent"
  assert_contains "$out" "is not on the ladder" "the notice must say why the ladder did not apply"

  # A retired model agy no longer lists is off the ladder too: allowed, but
  # reported, so a future rename degrades instead of blocking all work.
  rc=0
  out=$(gate_out "$state" 'Gemini 3.1 Pro (High)') || rc=$?
  [ "$rc" -eq 0 ] || fail "a retired model must not be refused by the ladder (rc=$rc)"
  assert_contains "$out" "is not on the ladder" "a retired model must be reported as off-ladder"

  # No model at all: agy picks its own, so there is nothing to enforce - and
  # that is exactly the case worth saying out loud.
  rc=0
  out=$(gate_out "$state" '') || rc=$?
  [ "$rc" -eq 0 ] || fail "a launch with no model must not be refused by the ladder (rc=$rc)"
  assert_contains "$out" "no model was requested" "an unmodelled launch must be reported"
  pass "an off-ladder or unmodelled launch is allowed and reported, never silently ungoverned"
}

# --- 4. The override ---------------------------------------------------------

test_override_launches_and_is_visible() {
  local state rc=0 out
  state=$(fresh_state override)
  # Put Opus (rung 1) below its floor.
  record "$state" "$RUNG1" 8.0 '4h 0m'

  # Without the override, the captain's reserved quarter is unreachable.
  out=$(gate_out "$state" "$RUNG1") || rc=$?
  [ "$rc" -eq 1 ] || fail "Opus below its floor must be refused without the override"
  assert_contains "$out" "--captain-route" "the refusal must name the way past it"

  # Set inside the command substitution's own subshell so the override cannot
  # leak into any later case and quietly pass a test that should refuse.
  rc=0
  out=$(FM_CAPTAIN_OVERRIDE_REASON='captain asked for the reserve'; export FM_CAPTAIN_OVERRIDE_REASON; fm_agy_ladder_gate "$RUNG1" "$state") || rc=$?
  [ "$rc" -eq 0 ] || fail "the override must let the refused launch through (rc=$rc)"
  assert_contains "$out" "overridden" "the override must announce itself"
  assert_contains "$out" "captain asked for the reserve" "the override's stated reason must be printed"
  assert_contains "$out" "is at 8.0%" "the override must still print what it overrode"

  # It reaches every refusal, not only the floor.
  local empty
  empty=$(fresh_state override-descend)
  rc=0
  out=$(FM_CAPTAIN_OVERRIDE_REASON=1; export FM_CAPTAIN_OVERRIDE_REASON; fm_agy_ladder_gate "$RUNG2" "$empty") || rc=$?
  [ "$rc" -eq 0 ] || fail "the override must also cover an unproven descent (rc=$rc)"
  assert_contains "$out" "overridden" "the override must announce itself on a descent too"
  pass "the override reaches the captain's reserve and every other refusal, and never silently"
}

test_override_must_be_deliberate() {
  local state rc=0
  state=$(fresh_state override-empty)
  record "$state" "$RUNG1" 8.0 '4h 0m'

  # An exported-but-empty value is not an override. Reaching the reserved
  # quarter has to be an act, not a leftover in the environment.
  rc=0
  ( FM_CAPTAIN_OVERRIDE_REASON=; export FM_CAPTAIN_OVERRIDE_REASON; fm_agy_ladder_gate "$RUNG1" "$state" >/dev/null ) || rc=$?
  [ "$rc" -eq 1 ] || fail "an empty FM_CAPTAIN_OVERRIDE_REASON must not override anything"
  pass "an empty override variable is not an override"
}

# --- 5. Headroom for launches already in flight ------------------------------
#
# A reading describes the account at the moment it was taken. Concurrent
# launches are invisible to it, so at 26% an unbounded burst could all clear the
# 25% floor on the same pre-burst number and land the account well under the
# captain's reserve before anything was re-read.

test_a_burst_just_above_the_floor_is_refused() {
  local state rc=0 out
  state=$(fresh_state burst)
  record "$state" "$RUNG1" 26.0 '4h 0m'

  # The first launch fits: 26% really is above 25%.
  out=$(gate_out "$state" "$RUNG1") || rc=$?
  [ "$rc" -eq 0 ] || fail "the first launch at 26% must be allowed (rc=$rc)"

  # Nothing hand-writes the ledger here. The gate reserves each launch's own
  # margin as it authorizes it, so the burst is refused by the same calls that
  # made it - which is the whole point: a caller cannot forget to reserve.
  rc=0
  out=$(gate_out "$state" "$RUNG1") || rc=$?

  [ "$rc" -eq 1 ] || fail "a burst at 26% must be refused before it crosses the floor (rc=$rc)"
  assert_contains "$out" "is at 26.0%" "the refusal must still report the evidence it read"
  assert_contains "$out" "in flight" "the refusal must say headroom was reserved for launches in flight"
  assert_contains "$out" "leaving 25.0%" "the refusal must show the figure it actually compared"
  assert_contains "$out" "reserved for the captain" "the refusal is still the captain's floor"

  # The reading itself never moved, so this is the reservation refusing and not
  # some other change of evidence.
  case "$(fm_agy_quota_read "$RUNG1" "$state")" in
    '26.0 '*) ;;
    *) fail "the burst must be refused on the SAME reading that allowed the first launch" ;;
  esac
  pass "a burst of launches just above the floor is refused before it crosses the captain's reserve"
}

test_in_flight_reservations_expire() {
  local state rc=0 now
  state=$(fresh_state inflight-expire)
  # Rung 2 (Flash, floor=0) at 1.0% - above the floor but barely.
  # Rung 1 (Opus) sits below its reserve so rung 2 is reachable and the case
  # turns on headroom alone.
  record "$state" "$RUNG1" 10.0 '4h 0m'
  record "$state" "$RUNG2" 1.0 '4h 0m'
  now=$(date +%s)

  # Two inflight records at margin=1 reserve 2%, so effective = 1.0 - 2 = -1.0,
  # which is below the 0% floor. This must be refused.
  fm_agy_inflight_record "$RUNG2" "$state" "$now"
  fm_agy_inflight_record "$RUNG2" "$state" "$now"
  rc=0
  fm_agy_ladder_check "$RUNG2" "$state" "$now" >/dev/null || rc=$?
  [ "$rc" -eq 1 ] || fail "two launches in flight at 1% must be refused (rc=$rc)"

  # Once a reading could have seen them, they stop being reserved - otherwise
  # every past launch would permanently shrink the rung. Both readings are
  # re-taken at the check instant so this turns on expiry alone.
  fm_agy_quota_observe "$RUNG1 | ctx: 3.0% | quota: 10.0% (4h 0m)" "$state" "$((now + 301))"
  fm_agy_quota_observe "$RUNG2 | ctx: 3.0% | quota: 1.0% (4h 0m)" "$state" "$((now + 301))"
  rc=0
  fm_agy_ladder_check "$RUNG2" "$state" "$((now + 301))" >/dev/null || rc=$?
  [ "$rc" -eq 0 ] || fail "expired reservations must stop reserving headroom (rc=$rc)"
  pass "headroom is reserved only for launches a current reading cannot have seen"
}

# --- 6. The intake poll serves the floor AND the descent ---------------------
#
# This is the half the original diagnosis understated. Absent evidence does not
# only fail rung 1's floor open; it also refuses every descent, because a
# descent must PROVE the rungs above are spent. Both halves are fixed by the
# same fresh reading, and one poll answers every rung at once.

# stub_agy_quota <dir> <opus-fraction> <flash-fraction>: an `agy` on PATH that
# answers /quota with the real command's JSON shape and runs no turn.
stub_agy_quota() {
  local dir=$1 f1=$2 f2=$3 fakebin
  mkdir -p "$dir"
  fakebin=$(fm_fakebin "$dir")
  cat > "$fakebin/agy" <<SH
#!/usr/bin/env bash
case "\$*" in
  *models*)
    printf 'claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n'
    printf 'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n'
    ;;
  */quota*)
    cat <<'JSON'
{"status":"SUCCESS","command":{"name":"usage","data":{"groups":[{"name":"All Models","buckets":[
{"id":"claude-opus-4-6-thinking","name":"Claude Opus 4.6 (Thinking)","remaining_fraction":$f1,"reset_time":""},
{"id":"gemini-3.8-flash-high","name":"Gemini 3.8 Flash (High)","remaining_fraction":$f2,"reset_time":""}
]}]}}}
JSON
    ;;
esac
exit 0
SH
  chmod +x "$fakebin/agy"
  printf '%s\n' "$fakebin"
}

test_the_poll_unblocks_a_descent_stale_evidence_refused() {
  local state fakebin rc=0 out
  command -v jq >/dev/null 2>&1 || { echo "skip - the intake poll needs jq, which is absent"; return 0; }

  state=$(fresh_state poll-descent)

  # Rung 1 (Opus) was observed spent, but long enough ago that the reading
  # is no longer evidence. This is the state a home sits in whenever no agy pane
  # is running.
  record "$state" "$RUNG1" 10.0 '5h 0m'
  rc=0
  out=$(fm_agy_ladder_check "$RUNG2" "$state" "$(( $(date +%s) + 400 ))") || rc=$?
  [ "$rc" -eq 1 ] || fail "a descent on a reading past the ceiling must be refused (rc=$rc)"
  assert_contains "$out" "has no current quota reading" \
    "stale evidence must read as unproven, which is what blocks the descent"

  # The same request, with the poll allowed to answer: rung 1 really is spent,
  # so the descent is now PROVEN rather than merely likely, and it proceeds.
  # Stub: Opus (f1) at 10%, Flash (f2) at 60%.
  fakebin=$(stub_agy_quota "$TMP_ROOT/poll-descent-bin" 0.10 0.60)
  rc=0
  out=$(PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on fm_agy_ladder_gate "$RUNG2" "$state") || rc=$?
  [ "$rc" -eq 0 ] || fail "a live poll proving rung 1 spent must permit the descent (rc=$rc)"
  [ -z "$out" ] || fail "an authorized descent must stay quiet, got '$out'"
  pass "a live poll turns an unprovable descent into an authorized one, instead of stranding the ladder"
}

test_the_poll_holds_the_floor_a_stale_reading_would_have_missed() {
  local state fakebin rc=0 out
  command -v jq >/dev/null 2>&1 || { echo "skip - the intake poll needs jq, which is absent"; return 0; }

  state=$(fresh_state poll-floor)
  # An hours-old reading says rung 1 (Opus) is healthy. Under the window-only
  # rule this authorised every launch for the rest of its window; the account
  # has since fallen below the captain's reserve.
  record "$state" "$RUNG1" 95.0 '5h 0m'
  # Stub: Opus (f1) at 20.3%, Flash (f2) at 90%.
  fakebin=$(stub_agy_quota "$TMP_ROOT/poll-floor-bin" 0.203 0.90)

  rc=0
  out=$(PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on fm_agy_ladder_gate "$RUNG1" "$state") || rc=$?
  [ "$rc" -eq 1 ] || fail "the poll's current reading must hold the floor the stale one missed (rc=$rc)"
  assert_contains "$out" "is at 20.3%" "the refusal must act on the polled figure, not the stale one"
  assert_not_contains "$out" "95.0" "the stale reading must not survive the poll"

  # And the descent it implies is now available, which is the whole point of
  # polling every rung at once rather than only the requested one.
  rc=0
  out=$(PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on fm_agy_ladder_gate "$RUNG2" "$state") || rc=$?
  [ "$rc" -eq 0 ] || fail "the same poll must authorize the descent it just justified (rc=$rc)"
  pass "one poll both holds Opus's floor and authorizes the descent that follows from it"
}

# --- 7. When the poll itself cannot answer ----------------------------------
#
# The captain ruled the fleet must not stall, so an unreachable quota service
# never refuses a launch at the top of the ladder. It must still refuse a
# descent, and it must SAY which of the two it did, so the reader reaches for
# the override rather than abandoning the ladder for a model outside it.

stub_agy_silent() {  # <dir>: an agy that answers nothing at all
  local dir=$1 fakebin
  mkdir -p "$dir"
  fakebin=$(fm_fakebin "$dir")
  printf '#!/usr/bin/env bash\nexit 0\n' > "$fakebin/agy"
  chmod +x "$fakebin/agy"
  printf '%s\n' "$fakebin"
}

test_an_unavailable_poll_never_stalls_rung_one_but_still_blocks_a_descent() {
  local state fakebin rc=0 out
  state=$(fresh_state poll-unavailable)
  fakebin=$(stub_agy_silent "$TMP_ROOT/poll-unavailable-bin")

  # Rung 1 launches. This is the no-stall ruling: no evidence is not a reason to
  # stop agy work at the top of the ladder.
  rc=0
  out=$(PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on fm_agy_ladder_gate "$RUNG1" "$state") || rc=$?
  [ "$rc" -eq 0 ] || fail "an unreachable quota read must not stall rung 1 (rc=$rc)"
  assert_contains "$out" "unchecked against the floor" \
    "an allow the floor could not check must say so rather than passing silently"

  # The descent is still refused, and the reason distinguishes "could not reach
  # agy just now" from "never observed" so the next step is obvious.
  rc=0
  out=$(PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on fm_agy_ladder_gate "$RUNG2" "$state") || rc=$?
  [ "$rc" -eq 1 ] || fail "an unreachable quota read must not authorize a descent (rc=$rc)"
  assert_contains "$out" "a live quota read was attempted just now and did not answer" \
    "the refusal must name the failed live read, not report a bare absence"
  assert_contains "$out" "--captain-route" \
    "the refusal must name the way through, so the ladder is not abandoned for a costlier model"

  # The two verdicts diverge on identical evidence, which is the asymmetry
  # itself; assert the divergence so neither half can go quietly vacuous.
  pass "an unreachable quota read launches rung 1, refuses a descent, and names which it did"
}

# --- 8. The gate is on the real dispatch path -------------------------------
#
# Everything above proves the decision. This proves it is WIRED: bin/fm-spawn.sh
# is the one path every agy crewmate and scout launch takes, so the gate has to
# fire there. The refusal lands before the brief, the worktree, or any backend
# endpoint exists, which is why these cases need none of them.

spawn_agy() {  # <state-dir> <model> [<env-assignment>...]
  local state=$1 model=$2
  shift 2
  local dir="$TMP_ROOT/spawn" fakebin
  mkdir -p "$dir/data" "$dir/config" "$dir/projects" "$state"
  # The spawn resolves a per-project Treehouse lock before it reaches the
  # brief check, and lock resolution needs a git repo to hash - so the probe
  # projects dir is one. The fork needed none of this: its spawn reached the
  # brief check with no project machinery in between.
  [ -d "$dir/projects/.git" ] || git init -q "$dir/projects"
  fakebin=$(fm_fakebin "$dir")
  # agy resolves from PATH before the gate runs, and the catalogue probe after
  # it does. A stub covers both so the case turns on the ladder alone and never
  # makes a network call.
  cat > "$fakebin/agy" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = models ]; then
  printf 'claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n'
  printf 'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n'
fi
exit 0
SH
  chmod +x "$fakebin/agy"
  # The poll is left ON for these cases, against that same stub, which answers
  # nothing for /quota. That is deliberate: it proves the live read sits on the
  # real dispatch path and that failing to answer degrades to the recorded
  # reading instead of stopping the spawn.
  env "$@" PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on \
    FM_STATE_OVERRIDE="$state" FM_DATA_OVERRIDE="$dir/data" \
    FM_CONFIG_OVERRIDE="$dir/config" FM_PROJECTS_OVERRIDE="$dir/projects" \
    "$ROOT/bin/fm-spawn.sh" agy-ladder-probe "$dir/projects" --scout \
    --harness agy --model "$model" 2>&1
}

spawn_agy_override() {  # <state-dir> <model> <captain-words>: the same probe
  # launch under a validated per-task captain route instead of explicit axes.
  local state=$1 model=$2 words=$3
  shift 3
  local dir="$TMP_ROOT/spawn-override" fakebin
  mkdir -p "$dir/data" "$dir/config" "$dir/projects" "$state"
  [ -d "$dir/projects/.git" ] || git init -q "$dir/projects"
  fakebin=$(fm_fakebin "$dir")
  cat > "$fakebin/agy" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = models ]; then
  printf 'claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n'
  printf 'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n'
fi
exit 0
SH
  chmod +x "$fakebin/agy"
  env "$@" PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on \
    FM_STATE_OVERRIDE="$state" FM_DATA_OVERRIDE="$dir/data" \
    FM_CONFIG_OVERRIDE="$dir/config" FM_PROJECTS_OVERRIDE="$dir/projects" \
    "$ROOT/bin/fm-spawn.sh" agy-ladder-probe "$dir/projects" --scout \
    --captain-route "agy/$model" --captain-said "$words" 2>&1
}

test_spawn_refuses_a_ladder_violation() {
  local state out rc=0
  state=$(fresh_state spawn-refuse)
  record "$state" "$RUNG1" 91.0 '4h 0m'

  out=$(spawn_agy "$state" "$RUNG2") || rc=$?
  [ "$rc" -ne 0 ] || fail "fm-spawn.sh must refuse an agy launch that violates the ladder"
  assert_contains "$out" "error: agy ladder refuses this launch" \
    "fm-spawn.sh did not report the ladder refusal"
  assert_contains "$out" "rung 1 (Claude Opus 4.6 (Thinking))" \
    "the spawn refusal must name the rung and the evidence"
  pass "fm-spawn.sh: refuses an agy launch that violates the ladder, on the ordinary dispatch path"
}

test_spawn_passes_an_honest_launch_through() {
  local state out rc=0
  state=$(fresh_state spawn-allow)
  record "$state" "$RUNG1" 91.0 '4h 0m'

  out=$(spawn_agy "$state" "$RUNG1") || rc=$?
  assert_not_contains "$out" "agy ladder" "an honest rung-1 launch must not be gated"
  # The spawn still stops - there is no brief for this probe id - but it stops
  # PAST the gate, which is what proves the gate let it through.
  assert_contains "$out" "no brief at" \
    "the honest launch must reach the brief check, i.e. past the ladder gate"
  [ "$rc" -ne 0 ] || fail "this probe spawn cannot succeed; it has no brief"
  pass "fm-spawn.sh: an honest rung-1 launch passes the gate and proceeds"
}

test_spawn_reserves_headroom_for_the_launch_it_authorized() {
  local state out rc=0
  state=$(fresh_state spawn-inflight)
  record "$state" "$RUNG1" 91.0 '4h 0m'

  [ "$(fm_agy_inflight_count "$RUNG1" "$state")" = 0 ] \
    || fail "a fresh state must owe no headroom"

  out=$(spawn_agy "$state" "$RUNG1") || rc=$?
  assert_contains "$out" "no brief at" "the launch must have been authorized"
  [ "$(fm_agy_inflight_count "$RUNG1" "$state")" = 1 ] \
    || fail "an authorized rung-1 launch must reserve headroom against the NEXT launch"

  # A refused launch spends nothing, so it must reserve nothing either.
  rc=0
  spawn_agy "$state" "$RUNG2" >/dev/null 2>&1 || rc=$?
  [ "$rc" -ne 0 ] || fail "the rung-2 probe must be refused while rung 1 is healthy"
  [ "$(fm_agy_inflight_count "$RUNG2" "$state")" = 0 ] \
    || fail "a refused launch must not reserve headroom"
  pass "fm-spawn.sh: an authorized launch reserves its headroom and a refused one does not"
}

test_spawn_records_the_override() {
  local state out rc=0
  state=$(fresh_state spawn-override)
  record "$state" "$RUNG1" 91.0 '4h 0m'

  out=$(spawn_agy_override "$state" "$RUNG2" 'captain: drain flash tonight') || rc=$?
  assert_contains "$out" "overridden by the captain for this task: captain: drain flash tonight" \
    "fm-spawn.sh must print the override and its stated reason"
  assert_contains "$out" "no brief at" \
    "the overridden launch must proceed past the gate"
  [ "$rc" -ne 0 ] || fail "this probe spawn cannot succeed; it has no brief"
  pass "fm-spawn.sh: the override launches a refused request and prints that it did"
}


# --- 9. Two homes, one account ----------------------------------------------
#
# On 2026-09-02 the primary home and the Lucie secondmate both dispatched agy
# workers against the same Claude Opus pool while it fell from 100% to 0%,
# straight through the captain's reserved quarter. Each home reserved headroom
# in its own state/ directory, so neither gate could see what the other had
# already committed, and each could authorise a launch the other had spent the
# quota for. These cases drive the REAL bin/fm-spawn.sh from two separate homes.

# spawn_agy_home: spawn_agy against a home of its own - its own state, data,
# config, and projects - so the two callers below are genuinely two FM_HOMEs and
# not one home called twice.
spawn_agy_home() {  # <home-name> <state-dir> <model> [<env-assignment>...]
  local home=$1 state=$2 model=$3
  shift 3
  local dir="$TMP_ROOT/home-$home" fakebin
  mkdir -p "$dir/data" "$dir/config" "$dir/projects" "$state"
  # Same probe-projects-repo requirement as spawn_agy above: the spawn
  # resolves its per-project Treehouse lock before the brief check.
  [ -d "$dir/projects/.git" ] || git init -q "$dir/projects"
  fakebin=$(fm_fakebin "$dir")
  cat > "$fakebin/agy" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = models ]; then
  printf 'claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n'
  printf 'gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n'
fi
exit 0
SH
  chmod +x "$fakebin/agy"
  env "$@" PATH="$fakebin:$PATH" FM_AGY_QUOTA_POLL=on \
    FM_STATE_OVERRIDE="$state" FM_DATA_OVERRIDE="$dir/data" \
    FM_CONFIG_OVERRIDE="$dir/config" FM_PROJECTS_OVERRIDE="$dir/projects" \
    "$ROOT/bin/fm-spawn.sh" agy-ladder-probe "$dir/projects" --scout \
    --harness agy --model "$model" 2>&1
}

# two_home_second_launch: set up two homes that each independently believe Opus
# (rung 1) is at 26%, run an authorized rung-1 launch in the first, then return
# what the SECOND home's gate says about the same launch. The extra environment
# is passed through to both, so a case can run this against the shared ledger
# and against the home-local one and compare.
two_home_second_launch() {  # <tag> [<env-assignment>...]
  local tag=$1
  shift
  local a="$TMP_ROOT/state-$tag-a" b="$TMP_ROOT/state-$tag-b" out

  rm -rf "$a" "$b"
  mkdir -p "$a" "$b"
  rm -f "$(fm_agy_inflight_path "$a")" 2>/dev/null || true

  # Each home records its own readings, because state/ is per-home and stays so.
  # Both see the identical account: rung 1 at 26% - one point of room above the
  # captain's 25%, which fits exactly one launch.
  record "$a" "$RUNG1" 26.0 '4h 0m'
  record "$b" "$RUNG1" 26.0 '4h 0m'

  out=$(spawn_agy_home "$tag-a" "$a" "$RUNG1" "$@")
  case "$out" in
    *'no brief at'*) ;;
    *) fail "the first home's launch at 26% must be authorized, got: $out" ;;
  esac

  spawn_agy_home "$tag-b" "$b" "$RUNG1" "$@"
}

test_two_homes_cannot_authorize_past_each_other() {
  local out
  out=$(two_home_second_launch shared)

  assert_contains "$out" "error: agy ladder refuses this launch" \
    "the second home must be refused because the first home already spent the room"
  assert_contains "$out" "in flight" \
    "the refusal must name the launch in flight that took the headroom"
  assert_contains "$out" "reserved for the captain" \
    "the second home must be stopped by the captain's floor, not by some other rule"
  assert_contains "$out" "is at 26.0%" \
    "the refusal must act on the reading, which never moved: only the reservation did"
  pass "two homes on one account: the second launch is refused because of the first home's worker"
}

test_the_refusal_is_the_shared_ledger_and_not_some_other_rule() {
  local out
  # The SAME scenario with the account-wide ledger switched off, which is
  # exactly the per-home accounting this fleet ran on before. The second home
  # sees only its own empty ledger, reads 26% as untouched, and launches - the
  # defect, reproduced on demand. Asserting the divergence is what stops the
  # case above from passing for some unrelated reason.
  out=$(two_home_second_launch local FM_AGY_SHARED_ROOT=off)

  assert_contains "$out" "no brief at" \
    "with per-home accounting the second home must launch, which is the defect being fixed"
  assert_not_contains "$out" "agy ladder refuses" \
    "per-home accounting cannot see the other home's launch, so nothing refuses it"
  pass "with per-home accounting the same second launch is allowed, which is the defect this closes"
}


# --- 10. A ladder the gate cannot enforce must be loud -----------------------
#
# The rung order came from config/dispatch-policy.json for the first time on
# 2026-09-02. _fm_agy_ladder_init falls back to the built-in order rather than
# refusing, which is right at dispatch time - a home whose config it cannot read
# still has to launch work - but it is also silent, and a silently ignored
# config is how the captain reorders the ladder, mistypes one model, and runs
# the opposite order without a word. The loud half is asked once per session by
# bin/fm-bootstrap.sh.

ladder_config() {  # <json>
  local f="$TMP_ROOT/ladder-config.json"
  printf '%s' "$1" > "$f"
  printf '%s\n' "$f"
}

test_a_config_the_ladder_cannot_enforce_is_reported() {
  local f out rc=0

  # The control: the captain's real order reports nothing.
  f=$(ladder_config '{"default":[{"harness":"agy","model":"Claude Opus 4.6 (Thinking)"},{"harness":"agy","model":"Gemini 3.8 Flash (High)"}]}')
  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 0 ] || fail "a usable ladder order must report nothing (rc=$rc)"
  [ -z "$out" ] || fail "a usable ladder order must print nothing, got '$out'"

  # A mistyped model. The bad rung is SKIPPED with a notice naming it while the
  # remaining rungs still enforce, so a future rename degrades instead of
  # blocking all work.
  rc=0
  f=$(ladder_config '{"default":[{"harness":"agy","model":"Gemini 3.1 Pro (Hgih)"},{"harness":"agy","model":"Claude Opus 4.6 (Thinking)"}]}')
  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 1 ] || fail "a model the ladder cannot rank must be reported (rc=$rc)"
  assert_contains "$out" "Gemini 3.1 Pro (Hgih)" "the report must name the model it could not rank"
  assert_contains "$out" "skipped" \
    "the report must say the bad rung is skipped rather than the whole order ignored"
  assert_contains "$out" "remaining order is enforced" \
    "the report must say the remaining rungs still enforce"

  # And the runtime really does skip just that rung, which is what makes the
  # report necessary rather than decorative. Asserting both halves keeps the
  # case from passing while the loader quietly changed behaviour.
  out=$(FM_AGY_LADDER_CONFIG="$f" ROOT="$ROOT" bash -c '. "$ROOT/bin/fm-agy-ladder-lib.sh" 2>&1; fm_agy_ladder_display 1')
  [ "$out" = "$RUNG1" ] \
    || fail "the loader must keep the remaining rung, got '$out'"
  out=$(FM_AGY_LADDER_CONFIG="$f" ROOT="$ROOT" bash -c '. "$ROOT/bin/fm-agy-ladder-lib.sh" 2>&1; fm_agy_ladder_display 2; printf "rc=%s" "$?"')
  assert_contains "$out" "rc=1" \
    "the loader must enforce a single remaining rung, not fall back to the built-in two"
  pass "a model the ladder cannot rank is reported as skipped, while dispatch keeps the remaining rungs"
}

test_a_retired_model_is_skipped_with_a_notice() {
  local f out rc=0
  # The old three-rung order names models agy no longer lists. Those rungs are
  # skipped with a notice naming them while Opus still enforces.
  f=$(ladder_config '{"agy_ladder":["Gemini 3.1 Pro (High)","Claude Opus 4.6 (Thinking)","Gemini 3.7 Flash (High)"]}')
  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 1 ] || fail "retired models must be reported (rc=$rc)"
  assert_contains "$out" "Gemini 3.1 Pro (High)" "the report must name the first retired rung"
  assert_contains "$out" "Gemini 3.7 Flash (High)" "the report must name the second retired rung"
  assert_contains "$out" "skipped" "the report must say the retired rungs are skipped"

  out=$(FM_AGY_LADDER_CONFIG="$f" ROOT="$ROOT" bash -c '. "$ROOT/bin/fm-agy-ladder-lib.sh" 2>&1; fm_agy_ladder_display 1')
  [ "$out" = 'Claude Opus 4.6 (Thinking)' ] \
    || fail "the loader must keep the remaining Opus rung, got '$out'"
  pass "a retired model is skipped with a notice naming it, while the remaining rung still enforces"
}

test_a_repeated_rung_is_reported() {
  local f out rc=0

  # Both spellings agy accepts name one model, so a config using one of each is
  # a duplicate the gate would otherwise accept as two distinct rungs.
  f=$(ladder_config '{"default":[{"harness":"agy","model":"Claude Opus 4.6 (Thinking)"},{"harness":"agy","model":"claude-opus-4-6-thinking"},{"harness":"agy","model":"Gemini 3.8 Flash (High)"}]}')
  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 1 ] || fail "a model listed twice must be reported (rc=$rc)"
  assert_contains "$out" "Claude Opus 4.6 (Thinking)" "the report must name the repeated model"
  assert_contains "$out" "more than once" "the report must say what is wrong with it"

  # A config that ranks no agy model at all is not an error: the built-in order
  # is the right answer for a fleet that has not stated one.
  rc=0
  f=$(ladder_config '{"default":[{"harness":"claude","model":"claude-opus-5"}]}')
  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 0 ] || fail "a config that ranks no agy model must not be reported as broken (rc=$rc)"
  [ -z "$out" ] || fail "a config with no agy rung must print nothing, got '$out'"
  pass "a model repeated across its two spellings is reported, and a config with no agy rung is not"
}


# --- 11. The split form sets the order on its own; the old form keeps working -
#
# The default dispatch field used to carry two meanings at once: the ladder
# order (through the legacy derivation) and the default-harness ruling, so the
# order could not be set without also changing default dispatch, and nothing
# said so. The order now lives in agy_ladder. These cases pin both halves of
# that split: an old single-field file still resolves exactly as before, and a
# split file follows agy_ladder whatever default says.

# ladder_order_and_floor <config-file>: "display1|display2|floor1" as
# one fresh shell reads it, so each case asserts the enforced order plus the
# floor the rung-1 model carries.
ladder_order_and_floor() {  # <config-file>
  FM_AGY_LADDER_CONFIG="$1" ROOT="$ROOT" bash -c \
    '. "$ROOT/bin/fm-agy-ladder-lib.sh"; printf "%s|%s|%s\n" "$(fm_agy_ladder_display 1)" "$(fm_agy_ladder_display 2)" "$(fm_agy_ladder_floor 1)"'
}

test_the_legacy_default_field_still_sets_the_ladder() {
  local f out rc=0 order
  # The old single-field form: agy rungs in default, no agy_ladder, Opus first
  # so the case also proves the reserve travels with the model, not the rung.
  f=$(ladder_config '{"default":[{"harness":"agy","model":"Claude Opus 4.6 (Thinking)","effort":"high"},{"harness":"agy","model":"Gemini 3.8 Flash (High)","effort":"high"}]}')
  order=$(ladder_order_and_floor "$f")
  [ "$order" = 'Claude Opus 4.6 (Thinking)|Gemini 3.8 Flash (High)|25' ] \
    || fail "the legacy default order must still resolve with the reserve on Opus at rung 1, got '$order'"

  # A usable legacy order is not broken, so the problem check stays silent.
  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 0 ] || fail "a usable legacy order must not be reported as broken (rc=$rc)"
  [ -z "$out" ] || fail "a usable legacy order must print no problem, got '$out'"

  # But the coupled read must be named: setting the order there also changes
  # default dispatch, which is exactly what the split removes.
  out=$(fm_agy_ladder_legacy_notice "$f") || rc=$?
  [ "$rc" -eq 0 ] || fail "the legacy notice is advisory and must always succeed (rc=$rc)"
  assert_contains "$out" "legacy default dispatch profiles" \
    "the notice must say where the order is being read from"
  assert_contains "$out" "agy_ladder" \
    "the notice must say where the order belongs"
  pass "the old single-field form still resolves, and its coupled read is named as legacy"
}

test_the_split_form_sets_order_independently_of_default() {
  local f out rc=0 order
  # The split form, shaped like the captain's ruling: default dispatches to
  # the opencode ladder while agy_ladder sets an order the built-in default
  # does not share, so only the split field can account for the result.
  f=$(ladder_config '{"default":[{"harness":"opencode","model":"opencode-go/muse-spark-1.3-contributor","provider":"opencode-go"}],"agy_ladder":["Claude Opus 4.6 (Thinking)","Gemini 3.8 Flash (High)"]}')
  order=$(ladder_order_and_floor "$f")
  [ "$order" = 'Claude Opus 4.6 (Thinking)|Gemini 3.8 Flash (High)|25' ] \
    || fail "the split order must follow agy_ladder with the reserve on Opus at rung 1, got '$order'"

  out=$(fm_agy_ladder_config_problem "$f") || rc=$?
  [ "$rc" -eq 0 ] || fail "a usable split order must not be reported as broken (rc=$rc)"
  [ -z "$out" ] || fail "a usable split order must print no problem, got '$out'"

  out=$(fm_agy_ladder_legacy_notice "$f") || rc=$?
  [ "$rc" -eq 0 ] || fail "the legacy notice is advisory and must always succeed (rc=$rc)"
  [ -z "$out" ] || fail "a split file must print no legacy notice, got '$out'"
  pass "the split form sets the rung order on its own while default keeps its own ruling"
}

test_agy_ladder_wins_when_both_fields_state_an_order() {
  local f out rc=0 order
  # Both fields state an order and they disagree: default's agy rows run
  # Flash-first while agy_ladder runs Opus-first. The split field decides.
  f=$(ladder_config '{"default":[{"harness":"agy","model":"Gemini 3.8 Flash (High)"}],"agy_ladder":["Claude Opus 4.6 (Thinking)","Gemini 3.8 Flash (High)"]}')
  order=$(ladder_order_and_floor "$f")
  [ "$order" = 'Claude Opus 4.6 (Thinking)|Gemini 3.8 Flash (High)|25' ] \
    || fail "agy_ladder must decide when both fields state an order, got '$order'"

  out=$(fm_agy_ladder_legacy_notice "$f") || rc=$?
  [ -z "$out" ] || fail "a file with agy_ladder must print no legacy notice, got '$out'"
  pass "agy_ladder wins when both fields state an order"
}

test_the_legacy_notice_stays_silent_outside_the_legacy_form() {
  local f out rc=0
  out=$(fm_agy_ladder_legacy_notice "$TMP_ROOT/does-not-exist.json") || rc=$?
  [ "$rc" -eq 0 ] && [ -z "$out" ] || fail "a missing file must stay silent, got rc=$rc out='$out'"

  f=$(ladder_config '{oops')
  out=$(fm_agy_ladder_legacy_notice "$f") || rc=$?
  [ "$rc" -eq 0 ] && [ -z "$out" ] || fail "a malformed file must stay silent for the notice, got rc=$rc out='$out'"

  f=$(ladder_config '{"default":[{"harness":"claude","model":"claude-opus-5"}]}')
  out=$(fm_agy_ladder_legacy_notice "$f") || rc=$?
  [ "$rc" -eq 0 ] && [ -z "$out" ] || fail "a file with no agy rung must stay silent, got rc=$rc out='$out'"

  f=$(ladder_config '{"agy_ladder":["Claude Opus 4.6 (Thinking)","Gemini 3.8 Flash (High)"]}')
  out=$(fm_agy_ladder_legacy_notice "$f") || rc=$?
  [ "$rc" -eq 0 ] && [ -z "$out" ] || fail "an agy_ladder-only file must stay silent, got rc=$rc out='$out'"
  pass "the legacy notice stays silent outside the legacy form"
}


test_simultaneous_launches_across_homes_are_serialized() {
  local n=10 i allowed refused root="$TMP_ROOT/race-account" dir="$TMP_ROOT/race"

  # Two homes deciding an instant apart is the ordinary case; two homes
  # deciding at the SAME instant is the one a ledger alone does not answer.
  # Ten homes are released together against one point of room, so the ledger
  # has to be read and written as one step or more than one of them clears a
  # floor only one of them fits above.
  rm -rf "$dir" "$root"
  mkdir -p "$dir"

  for i in $(seq 1 "$n"); do
    mkdir -p "$dir/h$i"
    # Each home records its own readings, as each really does.
    FM_AGY_SHARED_ROOT="$root" record "$dir/h$i" "$RUNG1" 26.0 '4h 0m'
  done

  # A file every worker spins on, so they are released together rather than
  # staggered by however long each took to start.
  : > "$dir/go"
  for i in $(seq 1 "$n"); do
    (
      while [ -f "$dir/go" ]; do :; done
      if FM_AGY_SHARED_ROOT="$root" fm_agy_ladder_gate "$RUNG1" "$dir/h$i" >/dev/null 2>&1; then
        echo allowed > "$dir/out.$i"
      else
        echo refused > "$dir/out.$i"
      fi
    ) &
  done
  sleep 0.3
  rm -f "$dir/go"
  wait

  allowed=$(grep -lx allowed "$dir"/out.* 2>/dev/null | wc -l | tr -d ' ')
  refused=$(grep -lx refused "$dir"/out.* 2>/dev/null | wc -l | tr -d ' ')

  [ "$((allowed + refused))" -eq "$n" ] \
    || fail "every simultaneous launch must reach a verdict (allowed=$allowed refused=$refused of $n)"
  [ "$allowed" -eq 1 ] \
    || fail "exactly one launch fits above the floor; $allowed were authorized"
  pass "simultaneous launches from $n homes on one account authorize exactly the one that fits"
}

test_both_spellings_resolve_to_one_rung
test_floors_are_the_captains_floors
test_refuses_descending_past_an_available_rung
test_refuses_descending_on_no_evidence
test_refuses_opus_at_the_captains_floor
test_refuses_an_exhausted_lower_rung
test_rung_one_launches_with_healthy_quota
test_rung_one_launches_on_a_fresh_home
test_lower_rung_launches_once_every_rung_above_is_spent
test_off_ladder_models_are_allowed_but_reported
test_override_launches_and_is_visible
test_override_must_be_deliberate
test_a_burst_just_above_the_floor_is_refused
test_in_flight_reservations_expire
test_the_poll_unblocks_a_descent_stale_evidence_refused
test_the_poll_holds_the_floor_a_stale_reading_would_have_missed
test_an_unavailable_poll_never_stalls_rung_one_but_still_blocks_a_descent
test_spawn_refuses_a_ladder_violation
test_spawn_passes_an_honest_launch_through
test_spawn_records_the_override
test_spawn_reserves_headroom_for_the_launch_it_authorized
test_two_homes_cannot_authorize_past_each_other
test_the_refusal_is_the_shared_ledger_and_not_some_other_rule
test_a_config_the_ladder_cannot_enforce_is_reported
test_a_retired_model_is_skipped_with_a_notice
test_a_repeated_rung_is_reported
test_the_legacy_default_field_still_sets_the_ladder
test_the_split_form_sets_order_independently_of_default
test_agy_ladder_wins_when_both_fields_state_an_order
test_the_legacy_notice_stays_silent_outside_the_legacy_form
test_simultaneous_launches_across_homes_are_serialized
