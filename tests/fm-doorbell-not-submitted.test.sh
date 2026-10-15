#!/usr/bin/env bash
# tests/fm-doorbell-not-submitted.test.sh - the doorbell submission verification
# (incident fm-doorbell-not-submitted, 2026-10-15).
#
# Firstmate's steering doorbell was repeatedly typed into an idle Claude Code
# pane on herdr but never submitted, so the worker sat idle on unread
# instructions until a manual Enter. The ring reported success anyway: beyond
# send-failed every submit verdict was discarded, so a swallowed Enter that
# left the constant line pending read as a delivered doorbell and no later
# ring was owed urgency.
#
# These tests pin the fixed contract through the real fm_task_inbox_ring with
# stubbed backend seams (the fm-daemon.test.sh precedent): after any
# non-empty submit verdict the ring verifies the line actually left the
# composer and presses Enter again - never retyping, so text the worker
# already took can never duplicate - and a line that persists on an idle pane
# stays loud (2) so the watcher re-rings it. A busy pane holding the line is
# queued, which is a fine delivery: no extra Enter, success.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=bin/fm-task-inbox-lib.sh
. "$ROOT/bin/fm-task-inbox-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-doorbell-not-submitted)
TMP_ROOT=$(cd "$TMP_ROOT" && pwd -P)

STUB=""

stub_new() {  # <agent-state> <composer-state> <busy-state> <submit-verdict> <holds-seq...>
  STUB=$(mktemp -d "$TMP_ROOT/stub.XXXXXX")
  printf '%s' "$1" > "$STUB/agent_state"
  printf '%s' "$2" > "$STUB/composer_state"
  printf '%s' "$3" > "$STUB/busy_state"
  printf '%s' "$4" > "$STUB/submit_verdict"
  : > "$STUB/holds_seq"
  shift 4
  for h in "$@"; do printf '%s\n' "$h" >> "$STUB/holds_seq"; done
  : > "$STUB/send_text.log"
  : > "$STUB/send_key.log"
  export STUB
}

make_record() {  # <task> -> echoes record path
  local dir="$TMP_ROOT/$1.inbox"
  mkdir -p "$dir/handled"
  printf 'schema=fm-task-inbox.v1\nat=2026-10-15T00:00:00Z\n--\nprobe\n' > "$dir/001.msg"
  printf '%s' "$dir/001.msg"
}

fm_backend_agent_state() { cat "$STUB/agent_state"; }
fm_backend_composer_state() { cat "$STUB/composer_state"; }
fm_backend_busy_state() { cat "$STUB/busy_state"; }
fm_backend_send_text_submit() {  # <backend> <target> <text> <retries> <sleep> <settle> [label]
  printf '%s\n' "$3" >> "$STUB/send_text.log"
  cat "$STUB/submit_verdict"
  return 0
}
fm_backend_send_key() {  # <backend> <target> <key> [label]
  printf '%s\n' "$2" >> "$STUB/send_key.log"
  return 0
}
fm_task_inbox_composer_holds() {  # scripted: consume one holds_seq entry, 1 = holds
  local ans tmp
  ans=$(head -n 1 "$STUB/holds_seq" 2>/dev/null || true)
  tmp="$STUB/holds_seq.tmp"
  tail -n +2 "$STUB/holds_seq" > "$tmp" 2>/dev/null || true
  mv "$tmp" "$STUB/holds_seq" 2>/dev/null || true
  [ "$ans" = 1 ]
}

ring() {  # <record> -> echoes rc
  local rc=0
  fm_task_inbox_ring herdr "session:w1:p1" "$1" >/dev/null 2>&1 || rc=$?
  printf '%s' "$rc"
}

count() {  # <file> -> line count or 0
  [ -f "$1" ] || { printf '0'; return 0; }
  wc -l < "$1" | tr -d '[:space:]'
}

test_unsubmitted_line_stays_loud_without_retyping() {
  local rec rc
  stub_new alive empty idle pending 1 1 1
  rec=$(make_record loud)
  rc=$(ring "$rec")
  [ "$rc" = 2 ] || fail "a doorbell line persisting on an idle pane must stay loud (2), got $rc"
  [ "$(count "$STUB/send_text.log")" -eq 1 ] || fail "the ring must type the line exactly once, never retype: $(cat "$STUB/send_text.log")"
  [ "$(count "$STUB/send_key.log")" -eq 2 ] || fail "the ring must press Enter again twice, sent: $(cat "$STUB/send_key.log")"
  pass "doorbell-not-submitted: an unsubmitted line stays loud without retyping"
}

test_resubmitted_line_reports_success() {
  local rec rc
  stub_new alive empty idle pending 1 0
  rec=$(make_record cleared)
  rc=$(ring "$rec")
  [ "$rc" = 0 ] || fail "a resubmitted line that clears must report success, got $rc"
  [ "$(count "$STUB/send_text.log")" -eq 1 ] || fail "the ring must type the line exactly once: $(cat "$STUB/send_text.log")"
  [ "$(count "$STUB/send_key.log")" -eq 1 ] || fail "the ring must stop pressing Enter once the line clears, sent: $(cat "$STUB/send_key.log")"
  pass "doorbell-not-submitted: a resubmitted line that clears reports success"
}

test_queued_line_on_busy_pane_is_fine() {
  local rec rc
  stub_new alive empty busy pending 1
  rec=$(make_record queued)
  rc=$(ring "$rec")
  [ "$rc" = 0 ] || fail "a queued line on a busy pane is a fine delivery, got $rc"
  [ "$(count "$STUB/send_key.log")" -eq 0 ] || fail "no extra Enter may land on a busy pane: $(cat "$STUB/send_key.log")"
  pass "doorbell-not-submitted: a queued line on a busy pane needs no extra Enter"
}

test_clean_submit_sends_no_extra_enter() {
  local rec rc
  stub_new alive empty idle empty
  rec=$(make_record clean)
  rc=$(ring "$rec")
  [ "$rc" = 0 ] || fail "a confirmed submit must report success, got $rc"
  [ "$(count "$STUB/send_key.log")" -eq 0 ] || fail "a confirmed submit needs no extra Enter: $(cat "$STUB/send_key.log")"
  pass "doorbell-not-submitted: a confirmed submit sends no extra Enter"
}

test_stale_leftover_gets_submitted() {
  local rec rc
  stub_new alive pending idle empty 1 0
  rec=$(make_record stale)
  rc=$(ring "$rec")
  [ "$rc" = 0 ] || fail "a stale leftover that clears must report success, got $rc"
  [ "$(count "$STUB/send_text.log")" -eq 0 ] || fail "a stale leftover must never be retyped: $(cat "$STUB/send_text.log")"
  pass "doorbell-not-submitted: a stale leftover is submitted without retyping"
}

test_stale_leftover_that_persists_stays_loud() {
  local rec rc
  stub_new alive pending idle empty 1 1 1 1
  rec=$(make_record stuck)
  rc=$(ring "$rec")
  [ "$rc" = 2 ] || fail "a stale leftover that persists must stay loud (2), got $rc"
  [ "$(count "$STUB/send_text.log")" -eq 0 ] || fail "a stale leftover must never be retyped: $(cat "$STUB/send_text.log")"
  pass "doorbell-not-submitted: a stale leftover that persists stays loud"
}

test_send_failed_stays_loud() {
  local rec rc
  stub_new alive empty idle send-failed
  rec=$(make_record failed)
  rc=$(ring "$rec")
  [ "$rc" = 2 ] || fail "a failed send must stay loud (2), got $rc"
  pass "doorbell-not-submitted: a failed send stays loud"
}

test_dead_endpoint_types_nothing() {
  local rec rc
  stub_new dead empty idle empty
  rec=$(make_record dead)
  rc=$(ring "$rec")
  [ "$rc" = 3 ] || fail "a dead endpoint must report 3, got $rc"
  [ "$(count "$STUB/send_text.log")" -eq 0 ] || fail "a dead endpoint must never be typed into: $(cat "$STUB/send_text.log")"
  [ "$(count "$STUB/send_key.log")" -eq 0 ] || fail "a dead endpoint must never be keyed: $(cat "$STUB/send_key.log")"
  pass "doorbell-not-submitted: a dead endpoint is never typed into"
}

test_unsubmitted_line_stays_loud_without_retyping
test_resubmitted_line_reports_success
test_queued_line_on_busy_pane_is_fine
test_clean_submit_sends_no_extra_enter
test_stale_leftover_gets_submitted
test_stale_leftover_that_persists_stays_loud
test_send_failed_stays_loud
test_dead_endpoint_types_nothing
