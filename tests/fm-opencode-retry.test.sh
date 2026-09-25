#!/usr/bin/env bash
# Behavior tests for bin/fm-opencode-retry.sh - the retry-backoff evidence
# sidecar that makes a usage-capped opencode lane visible to supervision.
#
# A capped lane never fails: OpenCode holds the session in `retry` with an
# hours-long backoff while the pane still reads busy. The detector is
# structural - the vendor's own `next` timestamp minus now is the backoff
# horizon, so no banner text is ever read. These cases pin the contract
# through the helper's own executable surface:
#   (a) a 22-hour horizon classifies blocked, naming attempt and model
#   (b) a seconds-long horizon classifies waiting (transient retry stays working)
#   (c) an expired sidecar (scheduled retry time plus grace has passed)
#       classifies nothing - it no longer describes the present
#   (d) malformed sidecars and refused records degrade to nothing-to-classify,
#       never to a false blocked
#   (e) the horizon threshold is caller-tunable via environment
#   (f) clear removes the evidence best-effort
#   (g) the rung-scoped record (record-cap/check-cap) preserves a proven cap
#       past the discovering task's cleanup: newer evidence wins, expiry and
#       threshold match the sidecar, malformed records classify nothing
#   (h) verdict-cap keeps expired and absent tellable apart: both route to
#       free through check-cap, but the honest answer names expired-evidence
#       (a cap was proved and its window passed) versus no-evidence (nobody
#       ever proved anything about this rung)
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HELPER="$ROOT/bin/fm-opencode-retry.sh"
TMP_ROOT=$(fm_test_tmproot fm-opencode-retry)

[ -x "$HELPER" ] || fail "helper not executable: $HELPER"

now_s() { date +%s; }
ms_from_now() {  # <offset-secs> -> epoch ms
  echo $(( ($(now_s) + $1) * 1000 ))
}

test_cap_horizon_is_blocked() {
  local d="$TMP_ROOT/cap"; mkdir -p "$d"
  "$HELPER" record "$d" lane1 3 "$(ms_from_now 79200)" "opencode/muse-spark-1.3-contributor-free" "ses_abc" \
    || fail "record refused a well-formed cap observation"
  [ -f "$d/lane1.opencode-retry" ] || fail "record wrote no sidecar"
  local out; out=$("$HELPER" check "$d" lane1) || fail "check refused a live cap sidecar"
  assert_contains "$out" "status=blocked" "22h horizon -> blocked"
  assert_contains "$out" "attempt=3" "attempt is reported"
  assert_contains "$out" "model=opencode/muse-spark-1.3-contributor-free" "model id is reported"
  pass "a quota-scale backoff horizon classifies blocked"
}

test_transient_horizon_is_waiting() {
  local d="$TMP_ROOT/transient"; mkdir -p "$d"
  "$HELPER" record "$d" lane1 1 "$(ms_from_now 8)" || fail "record refused a transient observation"
  local out; out=$("$HELPER" check "$d" lane1) || fail "check refused a live transient sidecar"
  assert_contains "$out" "status=waiting" "8s horizon -> waiting, never blocked"
  assert_not_contains "$out" "status=blocked" "transient retry must not read blocked"
  pass "a seconds-long backoff horizon classifies waiting"
}

test_expired_sidecar_classifies_nothing() {
  local d="$TMP_ROOT/expired"; mkdir -p "$d"
  # Attempt 9 with a scheduled retry an hour ago: the vendor's own time has
  # passed, so this file no longer describes the present even though the
  # attempt count is high.
  "$HELPER" record "$d" lane1 9 "$(ms_from_now -3600)" || fail "record refused an old observation"
  "$HELPER" check "$d" lane1 >/dev/null 2>&1 \
    && fail "expired sidecar must not classify"
  pass "an expired sidecar classifies nothing"
}

test_malformed_degrades_to_nothing() {
  local d="$TMP_ROOT/malformed"; mkdir -p "$d"
  printf 'junk\n' > "$d/bad1.opencode-retry"
  printf 'v1 attempt=3 next=9999999999999 ts=1\nextra line\n' > "$d/bad2.opencode-retry"
  printf 'v1 attempt=three next=9999999999999 ts=1\n' > "$d/bad3.opencode-retry"
  printf 'v1 attempt=3 ts=1\n' > "$d/bad4.opencode-retry"
  local id
  for id in bad1 bad2 bad3 bad4; do
    "$HELPER" check "$d" "$id" >/dev/null 2>&1 \
      && fail "malformed sidecar $id must not classify"
  done
  "$HELPER" record "$d" lane1 "3x" "$(ms_from_now 79200)" >/dev/null 2>&1 \
    && fail "non-numeric attempt must be refused"
  "$HELPER" record "$d" lane1 3 "soon" >/dev/null 2>&1 \
    && fail "non-numeric next must be refused"
  "$HELPER" record "$d" lane1 3 "$(ms_from_now 79200)" "model with spaces" >/dev/null 2>&1 \
    && fail "model outside the token charset must be refused"
  "$HELPER" check "$d" lane1 >/dev/null 2>&1 \
    && fail "refused records must leave nothing to classify"
  "$HELPER" check "$d" missing >/dev/null 2>&1 \
    && fail "an absent sidecar must not classify"
  pass "malformed evidence degrades to nothing-to-classify, never false blocked"
}

test_threshold_is_tunable() {
  local d="$TMP_ROOT/threshold"; mkdir -p "$d"
  "$HELPER" record "$d" lane1 2 "$(ms_from_now 8)" || fail "record refused fixture"
  FM_OPENCODE_RETRY_BLOCK_SECS=1 "$HELPER" check "$d" lane1 2>/dev/null | grep -q "status=blocked" \
    || fail "a 1s threshold should flip an 8s horizon to blocked"
  FM_OPENCODE_RETRY_BLOCK_SECS=999999 "$HELPER" check "$d" lane1 2>/dev/null | grep -q "status=waiting" \
    || fail "a huge threshold should keep a 22h-scale horizon waiting"
  pass "the blocked horizon threshold is caller-tunable"
}

test_clear_removes_evidence() {
  local d="$TMP_ROOT/clear"; mkdir -p "$d"
  "$HELPER" record "$d" lane1 3 "$(ms_from_now 79200)" || fail "record refused fixture"
  "$HELPER" clear "$d" lane1 || fail "clear must always succeed"
  "$HELPER" check "$d" lane1 >/dev/null 2>&1 \
    && fail "cleared evidence must not classify"
  "$HELPER" clear "$d" lane1 || fail "clearing an absent sidecar must still succeed"
  pass "clear removes the evidence best-effort"
}

test_idle_shape_text_is_blocked() {
  local d="$TMP_ROOT/idle-shape"; mkdir -p "$d"
  # No sidecar at all: the lane took the provider error and ended its turn,
  # so the plugin cleared (or never wrote) the retry evidence. The pane tail
  # still carries the cap verbatim - wrapped across rendered lines, exactly
  # as the 2026-09-14 incident showed it. This is the entire bug: a detector
  # that only reads the sidecar answers OPEN for this lane.
  printf '%s\n' \
    'working on the task...' \
    'Free usage exceeded, subscri' \
    'be to Go [retrying in 35s attempt #5]' \
    'opencode>' > "$d/pane.txt"
  "$HELPER" check "$d" lane1 >/dev/null 2>&1 \
    && fail "the sidecar-only check must stay blind to the idle shape"
  local out
  out=$("$HELPER" check "$d" lane1 --text-file "$d/pane.txt") \
    || fail "check with the pane tail must report the idle-after-cap lane"
  assert_contains "$out" "status=blocked" "idle-after-cap text reads blocked"
  assert_contains "$out" "source=text" "the evidence source is named"
  assert_not_contains "$out" "horizon_s=" "an idle lane's bracketed duration is not claimed as a horizon"
  out=$("$HELPER" scan-text --file "$d/pane.txt") \
    || fail "scan-text must match the wrap-split phrase"
  assert_contains "$out" "match=free-usage-exceeded" "the matched phrase is named"
  out=$("$HELPER" verdict "$d" lane1 --text-file "$d/pane.txt") \
    || fail "verdict must answer the idle-after-cap lane"
  assert_contains "$out" "verdict=capped" "the three-state answer is capped"
  pass "a lane idle after the cap is detected off its pane tail"
}

test_healthy_text_is_open_not_capped() {
  local d="$TMP_ROOT/healthy-text"; mkdir -p "$d"
  # Near-miss text a naive matcher would flag: a transient retry countdown
  # plus the GENERIC rate-limit phrase, which this detector deliberately does
  # not match - text alone carries no horizon to tell a throttle from a cap,
  # and matching it would descend working lanes onto the paid rung.
  printf '%s\n' \
    'working on the task...' \
    'Rate limit exceeded, retrying in 8s attempt #1' \
    'done' \
    'opencode>' > "$d/pane.txt"
  "$HELPER" check "$d" lane1 --text-file "$d/pane.txt" >/dev/null 2>&1 \
    && fail "healthy pane text must never read as capped"
  "$HELPER" scan-text --file "$d/pane.txt" >/dev/null 2>&1 \
    && fail "the generic rate-limit phrase must not match"
  local out
  out=$("$HELPER" verdict "$d" lane1 --text-file "$d/pane.txt") \
    || fail "verdict must answer a healthy capture"
  assert_contains "$out" "verdict=open" "a fresh clean capture renders open, never unknown"
  assert_not_contains "$out" "capped" "a healthy lane is never capped"
  pass "a healthy lane is not read as capped"
}

test_verdict_three_states() {
  local d="$TMP_ROOT/three-states"; mkdir -p "$d"
  printf 'all good, working\nopencode>\n' > "$d/clean.txt"
  printf 'Free usage exceeded, subscribe to Go [retrying in 35s attempt #5]\n' > "$d/cap.txt"
  local out
  out=$("$HELPER" verdict "$d" lane1) || fail "verdict must always answer"
  assert_contains "$out" "verdict=unknown" "no sidecar and no capture is unknown, never open"
  assert_contains "$out" "reason=no-evidence" "the absence names itself"
  out=$("$HELPER" verdict "$d" lane1 --text-file "$d/clean.txt") \
    || fail "verdict must answer a clean capture"
  assert_contains "$out" "verdict=open" "a clean capture renders open"
  out=$("$HELPER" verdict "$d" lane1 --text-file "$d/cap.txt") \
    || fail "verdict must answer a cap capture"
  assert_contains "$out" "verdict=capped" "a cap capture renders capped"
  assert_contains "$out" "episode=text-" "text evidence carries an episode identity"
  "$HELPER" record "$d" lane1 3 "$(ms_from_now 79200)" || fail "record refused fixture"
  out=$("$HELPER" verdict "$d" lane1) || fail "verdict must answer a blocking sidecar"
  assert_contains "$out" "verdict=capped evidence=sidecar" "a blocking sidecar renders capped with its source named"
  pass "capped, open, and unknown are separately expressible"
}

test_rung_cap_is_preserved_and_classified() {
  local d="$TMP_ROOT/rung-cap"; mkdir -p "$d"
  "$HELPER" record-cap "$d" free "$(ms_from_now 79200)" \
    || fail "record-cap refused a well-formed rung observation"
  [ -f "$d/.opencode-cap-free" ] || fail "record-cap wrote no rung record"
  local out; out=$("$HELPER" check-cap "$d" free) \
    || fail "check-cap refused a live rung record"
  assert_contains "$out" "status=blocked" "22h horizon -> blocked"
  case "$out" in
    *'horizon_s='*) : ;;
    *) fail "the preserved finding must carry its horizon, said: $out" ;;
  esac
  pass "a preserved rung cap classifies blocked with its horizon"
}

test_rung_cap_newer_evidence_wins() {
  local d="$TMP_ROOT/rung-newer"; mkdir -p "$d"
  "$HELPER" record-cap "$d" free "$(ms_from_now 79200)" || fail "record-cap refused fixture"
  local first; first=$(cat "$d/.opencode-cap-free")
  # An older horizon must not shorten the rung's recorded cap.
  "$HELPER" record-cap "$d" free "$(ms_from_now 3600)" || fail "record-cap must swallow an older observation as a no-op"
  [ "$(cat "$d/.opencode-cap-free")" = "$first" ] \
    || fail "an older observation must not clobber a newer rung record"
  # A newer horizon replaces it.
  "$HELPER" record-cap "$d" free "$(ms_from_now 80000)" || fail "record-cap refused a newer observation"
  [ "$(cat "$d/.opencode-cap-free")" != "$first" ] \
    || fail "a newer observation must replace the rung record"
  pass "newer rung evidence wins, older never shortens the cap"
}

test_rung_cap_expires_and_degrades() {
  local d="$TMP_ROOT/rung-expired"; mkdir -p "$d"
  "$HELPER" record-cap "$d" free "$(ms_from_now -3600)" || fail "record-cap refused an old observation"
  "$HELPER" check-cap "$d" free >/dev/null 2>&1 \
    && fail "an expired rung record must not classify"
  # An expired incumbent loses to any valid observation.
  "$HELPER" record-cap "$d" free "$(ms_from_now 79200)" || fail "record-cap refused fixture"
  "$HELPER" check-cap "$d" free 2>/dev/null | grep -q "status=blocked" \
    || fail "a valid observation must replace an expired rung record"
  # Malformed records and refused writes degrade to nothing-to-classify.
  # The malformed fixture uses a ladder rung key (go) with junk bytes, so the
  # failure proves the content is unreadable rather than the name unaccepted.
  local dm="$TMP_ROOT/rung-malformed"; mkdir -p "$dm"
  printf 'junk\n' > "$dm/.opencode-cap-go"
  "$HELPER" check-cap "$dm" go >/dev/null 2>&1 \
    && fail "a malformed rung record must not classify"
  "$HELPER" record-cap "$d" 'bad rung' "$(ms_from_now 79200)" >/dev/null 2>&1 \
    && fail "a rung outside the accepted set must be refused"
  "$HELPER" record-cap "$d" free soon >/dev/null 2>&1 \
    && fail "a non-numeric horizon must be refused"
  # Absent: a ladder rung key with no record on disk classifies nothing.
  local da="$TMP_ROOT/rung-absent"; mkdir -p "$da"
  "$HELPER" check-cap "$da" go >/dev/null 2>&1 \
    && fail "an absent rung record must not classify"
  "$HELPER" check-cap /nonexistent-dir free >/dev/null 2>&1 \
    && fail "a missing state dir must not classify"
  pass "an expired or malformed rung record degrades to nothing-to-classify"
}

test_rung_cap_threshold_is_tunable() {
  local d="$TMP_ROOT/rung-threshold"; mkdir -p "$d"
  "$HELPER" record-cap "$d" free "$(ms_from_now 8)" || fail "record-cap refused fixture"
  FM_OPENCODE_RETRY_BLOCK_SECS=1 "$HELPER" check-cap "$d" free 2>/dev/null | grep -q "status=blocked" \
    || fail "a 1s threshold should flip an 8s rung horizon to blocked"
  FM_OPENCODE_RETRY_BLOCK_SECS=999999 "$HELPER" check-cap "$d" free 2>/dev/null | grep -q "status=waiting" \
    || fail "a huge threshold should keep a rung-scale horizon waiting"
  pass "the rung record honors the same blocked threshold"
}

test_rung_cap_verdict_keeps_expired_and_absent_apart() {
  local d="$TMP_ROOT/rung-verdict"; mkdir -p "$d"
  local out
  # Absent: nobody ever proved anything about this rung.
  out=$("$HELPER" verdict-cap "$d" free) \
    || fail "verdict-cap must always answer, even with no record"
  assert_contains "$out" "verdict=unknown" "absent rung record is unknown, never capped"
  assert_contains "$out" "reason=no-evidence" "absent means nothing was ever proved"
  # Live quota-scale cap.
  "$HELPER" record-cap "$d" free "$(ms_from_now 83823)" \
    || fail "record-cap refused a well-formed rung observation"
  out=$("$HELPER" verdict-cap "$d" free) \
    || fail "verdict-cap must always answer, even past the cap"
  assert_contains "$out" "verdict=capped" "a live rung cap reads capped"
  assert_contains "$out" "evidence=rung-record" "the finding names where it lives"
  # Live but short horizon: worth watching, not quota-scale.
  "$HELPER" record-cap "$d" go "$(ms_from_now 8)" \
    || fail "record-cap refused a transient rung observation"
  out=$("$HELPER" verdict-cap "$d" go) \
    || fail "verdict-cap must always answer a transient record"
  assert_contains "$out" "verdict=waiting" "a transient rung horizon reads waiting, never capped"
  # Expired: a cap WAS proved and its window has passed - free is worth
  # trying, and the record says why. Same routing as absent, different fact.
  # An isolated dir keeps the expired free record clear of the live one above.
  local de="$TMP_ROOT/rung-verdict-expired"; mkdir -p "$de"
  "$HELPER" record-cap "$de" free "$(ms_from_now -3600)" \
    || fail "record-cap refused an old observation"
  "$HELPER" check-cap "$de" free >/dev/null 2>&1 \
    && fail "an expired rung record must not classify through check-cap"
  out=$("$HELPER" verdict-cap "$de" free) \
    || fail "verdict-cap must always answer an expired record"
  assert_contains "$out" "verdict=unknown" "an expired rung record is unknown, never capped"
  assert_contains "$out" "reason=expired-evidence" "expired names the proved cap whose window passed"
  # Malformed: present but unreadable, under a ladder rung key so the reason
  # proves content, not name, is at fault.
  local dmb="$TMP_ROOT/rung-verdict-malformed"; mkdir -p "$dmb"
  printf 'junk\n' > "$dmb/.opencode-cap-go"
  out=$("$HELPER" verdict-cap "$dmb" go) \
    || fail "verdict-cap must always answer a malformed record"
  assert_contains "$out" "reason=malformed-record" "a malformed rung record names itself"
  pass "verdict-cap tells expired-evidence from no-evidence"
}

test_rung_cap_rejects_unknown_rung() {
  # The 2026-09-22 defect: `record-cap <dir> opencode` succeeded and wrote
  # `.opencode-cap-opencode`, which nothing ever reads - strictly worse than
  # recording nothing, because the writer's own re-checks reassured while the
  # dispatch gate saw no evidence. An unreadable rung must fail loudly.
  local d="$TMP_ROOT/rung-reject"; mkdir -p "$d"
  local err
  err=$(mktemp "$TMP_ROOT/reject-err.XXXXXX")
  "$HELPER" record-cap "$d" opencode "$(ms_from_now 79200)" 2>"$err" \
    && fail "record-cap with an unreadable rung must exit nonzero"
  [ ! -f "$d/.opencode-cap-opencode" ] \
    || fail "a refused rung must write no record file"
  assert_contains "$(cat "$err")" "opencode" "the refusal names what was passed"
  assert_contains "$(cat "$err")" "free" "the refusal names the accepted free rung"
  assert_contains "$(cat "$err")" "go" "the refusal names the accepted go rung"
  assert_contains "$(cat "$err")" "plus" "the refusal names the accepted plus rung"
  rm -f "$err"
  pass "record-cap refuses an unreadable rung without writing and names the valid rungs"
}

test_rung_cap_commands_reject_the_same_set() {
  # A name cannot be written by one command and read by another: all three
  # rung commands reject the identical set.
  local d="$TMP_ROOT/rung-same-set"; mkdir -p "$d"
  local rung
  for rung in opencode bad missing old 'bad rung'; do
    "$HELPER" record-cap "$d" "$rung" "$(ms_from_now 79200)" >/dev/null 2>&1 \
      && fail "record-cap must refuse rung '$rung'"
    "$HELPER" check-cap "$d" "$rung" >/dev/null 2>&1 \
      && fail "check-cap must refuse rung '$rung'"
    "$HELPER" verdict-cap "$d" "$rung" >/dev/null 2>&1 \
      && fail "verdict-cap must refuse rung '$rung'"
    case "$rung" in
      *' '*) : ;;
      *)
        [ ! -f "$d/.opencode-cap-$rung" ] \
          || fail "a refused rung '$rung' must leave no record file" ;;
    esac
  done
  pass "record-cap, check-cap and verdict-cap reject the same rung set"
}

test_rung_cap_accepted_rungs_work_end_to_end() {
  # Every ladder rung classifies consistently: a quota-scale horizon
  # reads blocked (capped) and a seconds-long horizon reads waiting.
  local rung d_long d_short out
  for rung in free go plus; do
    d_long="$TMP_ROOT/rung-e2e-$rung-blocked"; mkdir -p "$d_long"
    "$HELPER" record-cap "$d_long" "$rung" "$(ms_from_now 79200)" \
      || fail "record-cap refused a quota-scale observation for rung '$rung'"
    out=$("$HELPER" check-cap "$d_long" "$rung") \
      || fail "check-cap refused a live record for rung '$rung'"
    assert_contains "$out" "status=blocked" "22h horizon on '$rung' reads blocked"
    out=$("$HELPER" verdict-cap "$d_long" "$rung") \
      || fail "verdict-cap must answer a live record for rung '$rung'"
    assert_contains "$out" "verdict=capped" "a quota-scale record on '$rung' reads capped"
    d_short="$TMP_ROOT/rung-e2e-$rung-waiting"; mkdir -p "$d_short"
    "$HELPER" record-cap "$d_short" "$rung" "$(ms_from_now 8)" \
      || fail "record-cap refused a transient observation for rung '$rung'"
    out=$("$HELPER" check-cap "$d_short" "$rung") \
      || fail "check-cap refused a transient record for rung '$rung'"
    assert_contains "$out" "status=waiting" "8s horizon on '$rung' reads waiting, never blocked"
    out=$("$HELPER" verdict-cap "$d_short" "$rung") \
      || fail "verdict-cap must answer a transient record for rung '$rung'"
    assert_contains "$out" "verdict=waiting" "a transient record on '$rung' reads waiting"
  done
  pass "the accepted rungs classify blocked above the threshold and waiting below it"
}

test_cap_horizon_is_blocked
test_transient_horizon_is_waiting
test_expired_sidecar_classifies_nothing
test_malformed_degrades_to_nothing
test_threshold_is_tunable
test_clear_removes_evidence
test_idle_shape_text_is_blocked
test_healthy_text_is_open_not_capped
test_verdict_three_states
test_rung_cap_is_preserved_and_classified
test_rung_cap_newer_evidence_wins
test_rung_cap_expires_and_degrades
test_rung_cap_threshold_is_tunable
test_rung_cap_verdict_keeps_expired_and_absent_apart
test_rung_cap_rejects_unknown_rung
test_rung_cap_commands_reject_the_same_set
test_rung_cap_accepted_rungs_work_end_to_end
