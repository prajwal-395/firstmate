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

test_cap_horizon_is_blocked
test_transient_horizon_is_waiting
test_expired_sidecar_classifies_nothing
test_malformed_degrades_to_nothing
test_threshold_is_tunable
test_clear_removes_evidence
