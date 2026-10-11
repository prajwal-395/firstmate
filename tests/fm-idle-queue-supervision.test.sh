#!/usr/bin/env bash
# tests/fm-idle-queue-supervision.test.sh - a queued wake in an idle home is
# supervision need, so it always reaches an agent turn.
#
# Seen twice on 2026-10-10: an idle second mate left a queued notification
# unhandled until firstmate prompted it by hand. The vep mate sat idle at its
# prompt with a queue row unhandled while the parent stall alarm fired, and the
# lucie mate, freshly restarted with no work in flight, left its deferred
# startup-network row unhandled for 270s with a beacon stale since 2026-10-08.
# The shared enabler was the supervision-need predicate in
# bin/fm-supervision-lib.sh: an idle home with no task in flight, no event
# source, no registered check, and no Relay poll counted as needing nothing,
# so the guards stayed silent, the Stop-hook auto-arm stayed inert (it only
# fires at a turn end, which an idle agent never reaches), the session-start
# drain ran before the deferred worker appended, and the parent quiet sweep
# read the mate as healthy. A queued row is work the home has not handled yet,
# so queue-pending now implies supervision need, and every consumer inherits
# it: the pull guard, both turn-end guards, the Claude auto-arm gate, and the
# cross-home mate-health verdict.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=bin/fm-supervision-lib.sh
. "$ROOT/bin/fm-supervision-lib.sh"
# shellcheck source=bin/fm-wake-lib.sh
. "$ROOT/bin/fm-wake-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-idle-queue-supervision)

# --- fixture homes -----------------------------------------------------------
# An idle home: state dir only, no task record, no event source, no registered
# check, no Relay poll, no watcher beacon. queue_row appends one durable wake
# row through the real append path, the way the deferred startup worker does.

make_idle_home() {
  local dir=$1
  mkdir -p "$dir/state" "$dir/data" "$dir/config"
  printf '%s\n' "$dir"
}

queue_row() {
  local state=$1 key=$2
  STATE="$state" FM_WAKE_QUEUE="$state/.wake-queue" \
    FM_WAKE_QUEUE_LOCK="$state/.wake-queue.lock" \
    bash -c '. "$0/bin/fm-wake-lib.sh"; fm_wake_append check "$1" "check: $1"' \
    "$ROOT" "$key" || fail "fm_wake_append could not queue $key"
}

# --- predicate: queue-pending implies supervision need ------------------------

test_predicate_idle_empty_needs_nothing() {
  local home state
  home=$(make_idle_home "$TMP_ROOT/pred-empty")
  state="$home/state"
  if fm_supervision_needed "$state" 300; then
    fail "an idle home with an empty queue must need nothing"
  fi
  [ "$FM_SUP_NEEDED" = false ] || fail "FM_SUP_NEEDED must be false, got $FM_SUP_NEEDED"
  [ "$FM_SUP_QUEUE_PENDING" = false ] || fail "an empty queue must not read as pending"
  pass "predicate: an idle home with an empty queue needs nothing"
}

test_predicate_idle_queued_needs_supervision() {
  local home state
  home=$(make_idle_home "$TMP_ROOT/pred-queued")
  state="$home/state"
  queue_row "$state" startup-network
  fm_supervision_needed "$state" 300 || fail "an idle home with a queued row did not register as supervision need"
  [ "$FM_SUP_NEEDED" = true ] || fail "FM_SUP_NEEDED must be true, got $FM_SUP_NEEDED"
  [ "$FM_SUP_IN_FLIGHT" -eq 0 ] || fail "a queued row must not count as an in-flight task, got $FM_SUP_IN_FLIGHT"
  [ "$FM_SUP_QUEUE_PENDING" = true ] || fail "the queued row must read as pending"
  pass "predicate: an idle home with a queued row needs supervision"
}

test_predicate_idle_queued_unhealthy_without_watcher() {
  local home state
  home=$(make_idle_home "$TMP_ROOT/pred-unhealthy")
  state="$home/state"
  queue_row "$state" startup-network
  fm_supervision_unhealthy "$state" 300 || fail "a queued row with no beacon must be unhealthy"
  touch "$state/.last-watcher-beat"
  if fm_supervision_unhealthy "$state" 300; then
    fail "a queued row with a fresh beacon must not be unhealthy"
  fi
  [ "$FM_SUP_WATCHER_FRESH" = true ] || fail "a beacon touched just now must read as fresh"
  pass "predicate: a queued row is unhealthy only while no watcher is live"
}

# --- pull guard: queue-only homes warn, and name the need ---------------------

test_guard_names_queued_wakes_without_tasks() {
  local home state out
  home=$(make_idle_home "$TMP_ROOT/guard-queued")
  state="$home/state"
  queue_row "$state" startup-network
  touch -t 202001010000 "$state/.last-watcher-beat"
  out=$(FM_HOME="$home" FM_SUPERVISION_MODEL=persistent bash "$ROOT/bin/fm-guard.sh" 2>&1)
  assert_contains "$out" "queued wakes are pending" "a queue-only home must warn about its queued rows"
  assert_contains "$out" "WATCHER DOWN" "a queue-only home with a stale beacon must raise the watcher alarm"
  assert_not_contains "$out" "X-mode relay polling needs supervision" "a queue-only alarm must not be misreported as relay polling"
  pass "guard: a queue-only home with a stale beacon names its queued wakes"
}

test_guard_silent_for_idle_empty_home() {
  local home state out
  home=$(make_idle_home "$TMP_ROOT/guard-empty")
  state="$home/state"
  touch -t 202001010000 "$state/.last-watcher-beat"
  out=$(FM_HOME="$home" FM_SUPERVISION_MODEL=persistent bash "$ROOT/bin/fm-guard.sh" 2>&1)
  assert_not_contains "$out" "WATCHER DOWN" "an idle home with an empty queue must stay silent"
  assert_not_contains "$out" "queued wakes" "an idle home with an empty queue must not warn about wakes"
  pass "guard: an idle home with an empty queue stays silent"
}

# --- turn-end guard: a queue-only blind turn blocks ---------------------------
# Secondmate-shaped fixture (plain checkout plus the home marker), mirroring
# tests/fm-turnend-guard.test.sh, with a queued row and no task record.

install_idle_guard_scripts() {
  local dir=$1
  mkdir -p "$dir/bin"
  cp "$ROOT/bin/fm-turnend-guard.sh" "$dir/bin/fm-turnend-guard.sh"
  cp "$ROOT/bin/fm-turnend-guard-grok.sh" "$dir/bin/fm-turnend-guard-grok.sh"
  cp "$ROOT/bin/fm-operational-input.sh" "$dir/bin/fm-operational-input.sh"
  cp "$ROOT/bin/fm-supervision-instructions.sh" "$dir/bin/fm-supervision-instructions.sh"
  cp "$ROOT/bin/fm-harness.sh" "$dir/bin/fm-harness.sh"
  cp "$ROOT/bin/fm-primary-scope-lib.sh" "$dir/bin/fm-primary-scope-lib.sh"
  cp "$ROOT/bin/fm-supervision-lib.sh" "$dir/bin/fm-supervision-lib.sh"
  cp "$ROOT/bin/fm-wake-lib.sh" "$dir/bin/fm-wake-lib.sh"
  cp "$ROOT/bin/fm-hook-host-lib.sh" "$dir/bin/fm-hook-host-lib.sh"
  mkdir -p "$dir/docs"
  cp -R "$ROOT/docs/supervision-protocols" "$dir/docs/supervision-protocols"
  chmod +x "$dir/bin/fm-turnend-guard.sh" "$dir/bin/fm-turnend-guard-grok.sh" "$dir/bin/fm-operational-input.sh" "$dir/bin/fm-supervision-instructions.sh" "$dir/bin/fm-harness.sh"
}

make_idle_secondmate_dir() {
  local dir=$1
  mkdir -p "$dir/state"
  git init -q "$dir"
  git -C "$dir" commit -q --allow-empty -m init
  : > "$dir/AGENTS.md"
  install_idle_guard_scripts "$dir"
  printf 'sm-idle-queue-1\n' > "$dir/.fm-secondmate-home"
  printf '%s\n' "$dir"
}

test_turnend_blocks_queue_only_blind_stop() {
  local dir out status
  dir=$(make_idle_secondmate_dir "$TMP_ROOT/hook-queue-only")
  queue_row "$dir/state" startup-network
  out=$(printf '{"stop_hook_active":false}' | FM_HOME="$dir" bash "$dir/bin/fm-turnend-guard.sh" 2>&1); status=$?
  expect_code 2 "$status" "the guard must block a queue-only blind turn end"
  assert_contains "$out" "TURN WOULD END BLIND" "the block banner must read as an alarm"
  assert_contains "$out" "queued wakes are pending" "the block banner must name the queued rows"
  assert_contains "$out" "fm-wake-drain.sh" "the block banner must point at the drain"
  assert_not_contains "$out" "X-mode relay polling needs supervision" "a queue-only block must not be misreported as relay polling"
  pass "turn-end guard: a queue-only blind stop blocks and names the drain"
}

test_turnend_allows_idle_empty_stop() {
  local dir out status
  dir=$(make_idle_secondmate_dir "$TMP_ROOT/hook-empty")
  out=$(printf '{"stop_hook_active":false}' | FM_HOME="$dir" bash "$dir/bin/fm-turnend-guard.sh" 2>&1); status=$?
  expect_code_out 0 "$status" "$out" "the guard must let an idle home with an empty queue end its turn"
  pass "turn-end guard: an idle home with an empty queue still ends its turn"
}

# --- cross-home mate health: queued rows read as down -------------------------

make_mate_home() {
  local dir=$1 task=$2
  mkdir -p "$dir/state"
  printf '%s\n' "$task" > "$dir/.fm-secondmate-home"
  printf '%s\n' "$dir"
}

test_mate_health_down_for_queued_idle_mate() {
  local mate
  mate=$(make_mate_home "$TMP_ROOT/mate-queued" queuemate)
  queue_row "$mate/state" startup-network
  fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" autoarm
  [ "$FM_MATE_WATCHER_DOWN" = true ] \
    || fail "an idle mate with a queued row must read as down, got down=$FM_MATE_WATCHER_DOWN desc=$FM_MATE_WATCHER_DESC"
  pass "mate health: an idle mate with a queued row reads as down: $FM_MATE_WATCHER_DESC"
}

test_mate_health_silent_for_empty_idle_mate() {
  local mate
  mate=$(make_mate_home "$TMP_ROOT/mate-empty" emptymate)
  fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" autoarm
  [ "$FM_MATE_WATCHER_DOWN" = false ] \
    || fail "an idle mate with an empty queue must stay silent, got down=$FM_MATE_WATCHER_DOWN desc=$FM_MATE_WATCHER_DESC"
  pass "mate health: an idle mate with an empty queue stays silent"
}

# --- drain: late arrivals surface on the next drain ----------------------------
# The session-start re-drain relies on this: when the step 3 drain found an
# empty queue, rows that arrive during startup are presented by one more
# drain in the same digest. Acknowledgement is sequence-bound, so the later
# ack covers every row presented so far.

test_drain_presents_rows_that_arrived_after_an_empty_drain() {
  local home state empty late
  home=$(make_idle_home "$TMP_ROOT/drain-late")
  state="$home/state"
  printf '# tasks\n' > "$home/data/backlog.md"
  empty=$(FM_HOME="$home" bash "$ROOT/bin/fm-wake-drain.sh" 2>&1)
  assert_not_contains "$empty" "WAKE_ACK_REQUIRED" "an empty drain must present nothing to acknowledge"
  queue_row "$state" late-row
  late=$(FM_HOME="$home" bash "$ROOT/bin/fm-wake-drain.sh" 2>&1)
  assert_contains "$late" "late-row" "the next drain must present the late arrival"
  assert_contains "$late" "WAKE_ACK_REQUIRED" "the late arrival must carry its acknowledgement"
  pass "drain: a row that arrived after an empty drain surfaces on the next drain"
}

test_predicate_idle_empty_needs_nothing
test_predicate_idle_queued_needs_supervision
test_predicate_idle_queued_unhealthy_without_watcher
test_guard_names_queued_wakes_without_tasks
test_guard_silent_for_idle_empty_home
test_turnend_blocks_queue_only_blind_stop
test_turnend_allows_idle_empty_stop
test_mate_health_down_for_queued_idle_mate
test_mate_health_silent_for_empty_idle_mate
test_drain_presents_rows_that_arrived_after_an_empty_drain
