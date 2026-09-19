#!/usr/bin/env bash
# tests/fm-secondmate-child-fact-wakes.test.sh - a BARE script-published
# secondmate child fact is RECORDED and PRESENTED but raises no supervision
# wake.
#
# The incident: the parent home took a supervision turn for every routine child
# fact a mate's scripts published (child PR ready, child outcome, merge), while
# only a handful of wakes in the same session needed a decision. Delivery and
# waking were never separated: status_is_captain_relevant treats any leading
# done: verb as terminal, so every delivered fact also woke the parent.
#
# The narrowing: only the two fully publisher-templated namespaces suppress -
# child-pr- (PR ready) and merged- (merge landed) - because they offer no
# free-text slot and no report pointer. A child-outcome- line always embeds the
# child's own terminal note, and a real one carried a measurement the captain
# had personally asked to see, so outcome lines keep waking on every verb.
#
# The contract this pins, through the public classifier and the real watcher
# and drain paths (never implementation bytes):
#   - a done: line carrying a bare-fact key (child-pr-, merged-) on a
#     kind=secondmate task is not wake-actionable;
#   - the same line on any other task kind still wakes (scope is key AND kind,
#     never verb alone);
#   - a note-carrying child outcome (done included), failed:,
#     needs-decision:, blocked:, mate-written judgement, and the main home's
#     own crewmate done: always wake;
#   - the suppressed line is still delivered (it stays on the parent channel)
#     and still presented exactly once (UNREAD STATUS), never replayed.
# The publishers, pause/absorb vocabulary, liveness reader, and backstops are
# untouched by this change and unasserted here beyond what the paths prove.
set -u

# shellcheck source=tests/wake-helpers.sh
. "$(dirname "${BASH_SOURCE[0]}")/wake-helpers.sh"
# shellcheck source=/dev/null
. "$ROOT/bin/fm-classify-lib.sh"

WATCH="$ROOT/bin/fm-watch.sh"
DRAIN="$ROOT/bin/fm-wake-drain.sh"

TMP_ROOT=$(fm_test_tmproot fm-secondmate-child-fact-tests)

CHILD_PR='done [key=child-pr-c1]: child c1 PR ready: https://example.com/pull/1 mode=direct-PR yolo=off'
CHILD_MERGED='done [key=merged-c1]: merged c1 https://example.com/pull/1'
CHILD_OUTCOME='done [key=child-outcome-c1-done-abcdef12]: child c1 done: PR https://example.com/pull/1 checks green pr=https://example.com/pull/1 mode=direct-PR'
# The live specimen: same outcome key shape, but the embedded child note
# carries a measurement the captain personally asked to see. This line must
# keep waking - the outcome key is not a proxy for "no payload".
CHILD_OUTCOME_WITH_NOTE='done [key=child-outcome-c2-done-d182a075]: child c2 done: PR https://example.com/pull/1233 - closer fit measured: 8 of 28 misfit; nothing re-cut mode=direct-PR yolo=on'

write_mate() {  # <state> <id> [<kind>]
  local state=$1 id=$2 kind=${3:-secondmate}
  printf 'kind=%s\n' "$kind" > "$state/$id.meta"
}

# --- public classifier: suppression is bare-fact key shape AND task kind ----

test_bare_fact_spans_are_not_actionable_on_a_secondmate_task() {
  local dir state
  dir=$(make_case classifier-suppressed); state="$dir/state"
  write_mate "$state" mate
  printf '%s\n' "$CHILD_PR" > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    && fail "a child-pr fact on a secondmate task classified actionable"
  printf '%s\n' "$CHILD_MERGED" > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    && fail "a merged fact on a secondmate task classified actionable"
  status_span_only_suppressed_child_facts "$state/mate.status" 0 \
    || fail "a solely-facts span was not recognized as solely facts"
  # The note-carrying outcome is NOT bare: it keeps waking, done verb or not.
  printf '%s\n' "$CHILD_OUTCOME" > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "a child-outcome done on a secondmate task stopped waking"
  printf '%s\n' "$CHILD_OUTCOME_WITH_NOTE" > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "the measurement-carrying outcome specimen stopped waking"
  status_span_only_suppressed_child_facts "$state/mate.status" 0 \
    && fail "an outcome span read as solely bare facts"
  pass "bare child-pr and merged facts suppress; child-outcome keeps waking"
}

test_bare_fact_shapes_still_wake_off_a_secondmate_task() {
  local dir state
  dir=$(make_case classifier-kind-scope); state="$dir/state"
  write_mate "$state" crew ship
  printf '%s\n' "$CHILD_PR" > "$state/crew.status"
  status_span_has_actionable "$state/crew.status" 0 \
    || fail "a child-pr-shaped done: on a ship task stopped waking"
  printf '%s\n' "$CHILD_MERGED" > "$state/nometa.status"
  status_span_has_actionable "$state/nometa.status" 0 \
    || fail "a merged-shaped done: with no task meta stopped waking"
  write_mate "$state" mate
  printf 'done: PR https://example.com/pull/9 checks green\n' > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "a keyless done: on a secondmate task stopped waking"
  pass "suppression needs the secondmate kind too, never the key shape alone"
}

test_must_wake_verbs_still_wake_on_a_secondmate_task() {
  local dir state event
  dir=$(make_case classifier-must-wake); state="$dir/state"
  write_mate "$state" mate
  printf 'failed [key=child-outcome-c9-failed-abcdef12]: child c9 failed: tests red\n' > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "a failed child outcome on a secondmate task stopped waking"
  printf 'failed: child c9 exploded with no key at all\n' > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "a keyless failed: on a secondmate task stopped waking"
  printf 'needs-decision [key=cap1]: pick the API shape\n' > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "a needs-decision: on a secondmate task stopped waking"
  printf 'blocked: cannot reach the package registry\n' > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "a blocked: on a secondmate task stopped waking"
  printf 'done: mate judgement in its own words, no script key\n' > "$state/mate.status"
  status_span_has_actionable "$state/mate.status" 0 \
    || fail "mate-written judgement on a secondmate task stopped waking"
  event=$(status_span_first_actionable "$state/mate.status" 0) \
    || fail "mate judgement produced no event"
  [ "$event" = 'done: mate judgement in its own words, no script key' ] \
    || fail "mate judgement surfaced as '$event' instead of itself"
  printf 'working: tidying\n%s\n' "$CHILD_PR" > "$state/mate.status"
  status_span_only_suppressed_child_facts "$state/mate.status" 0 \
    && fail "a span mixing a fact with another line read as solely facts"
  pass "failed, needs-decision, blocked, outcomes, and mate judgement still wake"
}

test_main_home_crewmate_done_still_wakes() {
  local dir state
  dir=$(make_case classifier-main-done); state="$dir/state"
  printf 'kind=ship\n' > "$state/job.meta"
  printf 'done: PR https://example.com/pull/7 checks green\n' > "$state/job.status"
  status_span_has_actionable "$state/job.status" 0 \
    || fail "the main home's own crewmate done: stopped waking"
  pass "the main home's own crewmate done: still wakes"
}

# --- real drain: delivered AND presented exactly once ------------------------

test_suppressed_fact_is_delivered_and_presented_once() {
  local dir state out status
  dir=$(make_case drain-presents-fact); state="$dir/state"
  out="$dir/drain.out"
  status="$state/mate.status"
  write_mate "$state" mate
  printf 'working: mate running our routed work\n' > "$status"
  FM_STATE_OVERRIDE="$state" "$DRAIN" >/dev/null 2>/dev/null \
    || fail "bootstrap drain failed while priming the presentation cursor"

  # The publisher's delivered artifact, byte for byte as fm-pr-check.sh writes
  # it to the parent channel.
  printf '%s\n' "$CHILD_PR" >> "$status"

  FM_STATE_OVERRIDE="$state" "$DRAIN" > "$out" 2> "$dir/drain.err" \
    || fail "drain failed on the suppressed-fact shape"
  grep -F 'UNREAD STATUS' "$out" >/dev/null \
    || fail "the suppressed fact produced no UNREAD STATUS section: $(cat "$out")"
  grep -F "mate $CHILD_PR" "$out" >/dev/null \
    || fail "the suppressed fact was not presented: $(cat "$out")"
  if grep -F 'OPEN DECISIONS' "$out" >/dev/null; then
    fail "the suppressed fact opened a decision: $(cat "$out")"
  fi
  grep -F "$CHILD_PR" "$status" >/dev/null \
    || fail "the delivered fact did not survive presentation on the channel"

  FM_STATE_OVERRIDE="$state" "$DRAIN" > "$out" 2> "$dir/drain2.err" \
    || fail "second drain after presentation failed"
  if grep -F "$CHILD_PR" "$out" >/dev/null; then
    fail "the presented fact was replayed as new: $(cat "$out")"
  fi
  if grep -F 'UNREAD STATUS' "$out" >/dev/null; then
    fail "the second drain reprinted UNREAD STATUS with no new lines: $(cat "$out")"
  fi
  pass "a suppressed fact is still delivered and presented exactly once"
}

# --- real watcher: no wake for facts, a wake for failure ---------------------

watch_bg_quiet() {  # <state> <fakebin> <out>
  local state=$1 fakebin=$2 out=$3
  PATH="$fakebin:$PATH" FM_STATE_OVERRIDE="$state" \
    FM_CREW_STATE_BIN="$fakebin/fm-crew-state.sh" \
    FM_POLL=1 FM_SIGNAL_GRACE=1 FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 \
    "$WATCH" > "$out" 2> "$out.err" &
}

beat_mtime() {
  stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null
}

wait_poll_cycle() {  # <state> <pid> [limit-ticks]
  local state=$1 pid=$2 limit=${3:-300} beat first now i=0
  beat="$state/.last-watcher-beat"
  rm -f "$beat"
  first=""
  while [ "$i" -lt "$limit" ]; do
    kill -0 "$pid" 2>/dev/null || return 1
    first=$(beat_mtime "$beat")
    [ -n "$first" ] && break
    sleep 0.1
    i=$((i + 1))
  done
  while [ "$i" -lt "$limit" ]; do
    kill -0 "$pid" 2>/dev/null || return 1
    now=$(beat_mtime "$beat")
    if [ -n "$now" ] && [ "$now" != "$first" ]; then
      return 0
    fi
    sleep 0.1
    i=$((i + 1))
  done
  return 1
}

reap() { kill "$1" 2>/dev/null || true; wait "$1" 2>/dev/null || true; }

test_watcher_absorbs_facts_then_wakes_on_failure() {
  local dir state fakebin out pid
  dir=$(make_case watcher-absorb-then-wake); state="$dir/state"
  fakebin="$dir/fakebin"
  out="$dir/watch.out"
  write_mate "$state" mate
  printf 'working: mate running our routed work\n' > "$state/mate.status"
  # Production classifies the earlier line in its own poll: prime the signal
  # markers so the facts below arrive in a facts-only span, as they do live.
  prime_status_seen "$state" "$state/mate.status"
  printf '%s\n' "$CHILD_PR" >> "$state/mate.status"
  printf '%s\n' "$CHILD_MERGED" >> "$state/mate.status"

  watch_bg_quiet "$state" "$fakebin" "$out"
  pid=$!
  wait_poll_cycle "$state" "$pid" \
    || { reap "$pid"; fail "watcher died on a facts-only batch instead of absorbing it: $(cat "$out" "$out.err" 2>/dev/null)"; }
  kill -0 "$pid" 2>/dev/null \
    || { reap "$pid"; fail "watcher exited for suppressed facts: $(cat "$out" 2>/dev/null)"; }
  if [ -s "$state/.wake-queue" ]; then
    reap "$pid"
    fail "suppressed facts queued a wake: $(cat "$state/.wake-queue")"
  fi

  printf 'failed: child c1 exploded after its PR landed\n' >> "$state/mate.status"
  # wake() exits 0 after queueing an actionable wake, so the queue entry -
  # not the exit code - is the proof the failure woke the parent.
  wait_for_exit "$pid" 100
  grep -F "signal:" "$out" >/dev/null \
    || fail "child failure printed no signal wake: $(cat "$out" 2>/dev/null)"
  grep -F "mate.status" "$state/.wake-queue" >/dev/null \
    || fail "child failure queued no wake: $(cat "$state/.wake-queue" 2>/dev/null)"
  grep -F "$CHILD_PR" "$state/mate.status" >/dev/null \
    || fail "the facts went missing from the channel around the failure wake"
  pass "the watcher absorbs facts-only batches and still wakes on a child failure"
}

test_watcher_wakes_on_decision() {
  local dir state fakebin out pid
  dir=$(make_case watcher-decision); state="$dir/state"
  fakebin="$dir/fakebin"
  out="$dir/watch.out"
  write_mate "$state" mate
  printf 'kind=ship\n' > "$state/job.meta"
  printf 'working: setup\n' > "$state/job.status"
  printf 'needs-decision [key=cap1]: pick the API shape\n' >> "$state/mate.status"

  watch_bg_quiet "$state" "$fakebin" "$out"
  pid=$!
  wait_for_exit "$pid" 100
  grep -F "mate.status" "$state/.wake-queue" >/dev/null \
    || fail "a mate needs-decision queued no wake: $(cat "$out" 2>/dev/null)"
  pass "a mate needs-decision still wakes the parent watcher"
}

test_watcher_wakes_on_blocker() {
  local dir state fakebin out pid
  dir=$(make_case watcher-blocker); state="$dir/state"
  fakebin="$dir/fakebin"
  out="$dir/watch.out"
  write_mate "$state" mate
  printf 'blocked: cannot reach the package registry\n' > "$state/mate.status"

  watch_bg_quiet "$state" "$fakebin" "$out"
  pid=$!
  wait_for_exit "$pid" 100
  grep -F "mate.status" "$state/.wake-queue" >/dev/null \
    || fail "a mate blocker queued no wake: $(cat "$out" 2>/dev/null)"
  pass "a mate blocker still wakes the parent watcher"
}

test_watcher_wakes_on_outcome_with_note() {
  local dir state fakebin out pid
  dir=$(make_case watcher-outcome-note); state="$dir/state"
  fakebin="$dir/fakebin"
  out="$dir/watch.out"
  write_mate "$state" mate
  printf '%s\n' "$CHILD_OUTCOME_WITH_NOTE" > "$state/mate.status"

  watch_bg_quiet "$state" "$fakebin" "$out"
  pid=$!
  wait_for_exit "$pid" 100
  grep -F "mate.status" "$state/.wake-queue" >/dev/null \
    || fail "the measurement-carrying outcome queued no wake: $(cat "$out" 2>/dev/null)"
  pass "a note-carrying child outcome still wakes the parent watcher"
}

test_watcher_wakes_on_main_crewmate_done() {
  local dir state fakebin out pid
  dir=$(make_case watcher-main-done); state="$dir/state"
  fakebin="$dir/fakebin"
  out="$dir/watch.out"
  printf 'kind=ship\n' > "$state/job.meta"
  printf 'done: PR https://example.com/pull/7 checks green\n' > "$state/job.status"

  watch_bg_quiet "$state" "$fakebin" "$out"
  pid=$!
  wait_for_exit "$pid" 100
  grep -F "job.status" "$state/.wake-queue" >/dev/null \
    || fail "a main crewmate done queued no wake: $(cat "$out" 2>/dev/null)"
  pass "a main crewmate done still wakes the watcher"
}

test_bare_fact_spans_are_not_actionable_on_a_secondmate_task
test_bare_fact_shapes_still_wake_off_a_secondmate_task
test_must_wake_verbs_still_wake_on_a_secondmate_task
test_main_home_crewmate_done_still_wakes
test_suppressed_fact_is_delivered_and_presented_once
test_watcher_absorbs_facts_then_wakes_on_failure
test_watcher_wakes_on_decision
test_watcher_wakes_on_outcome_with_note
test_watcher_wakes_on_blocker
test_watcher_wakes_on_main_crewmate_done
