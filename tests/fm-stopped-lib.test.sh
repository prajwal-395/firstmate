#!/usr/bin/env bash
# tests/fm-stopped-lib.test.sh - the declared-stop record (bin/fm-stopped-lib.sh):
# the one durable statement that a task's endpoint is agent-free BY DESIGN.
#
# The contract under test, and why each half exists:
#   1. The record round-trips what the control plane knows at the moment it acts
#      - the reason and the incarnation - because nothing downstream could
#        recover either from an empty endpoint.
#   2. The INCARNATION BINDING is the safety property, not a detail. A record
#      applies to the one agent it was written for; a task now running a
#      different agent must read as undeclared, or a stale record would silence
#      a later, different worker on the same task id.
#   3. A record it cannot bind is refused rather than written unbound, and a
#      task with no record at all is undeclared - which is what keeps a worker
#      that died on its own surfacing immediately.
#
# Pure functions over exact bytes on disk: no agent, no backend, no endpoint.
# The pairing with a verified-gone agent belongs to bin/fm-crew-state.sh and is
# proven in tests/fm-crew-state.test.sh; the absorb it licenses belongs to the
# watcher and is proven in tests/fm-watch-triage.test.sh.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=/dev/null
. "$ROOT/bin/fm-stopped-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-stopped-lib)

new_state() {  # <name> -> echoes a fresh state dir
  local d="$TMP_ROOT/$1"
  mkdir -p "$d"
  printf '%s\n' "$d"
}

write_meta() {  # <state> <id> [extra key=value...]
  local state=$1 id=$2
  shift 2
  {
    echo "window=fm:fm-$id"
    echo "worktree=$state/wt-$id"
    echo "harness=claude"
    local kv
    for kv in "$@"; do echo "$kv"; done
  } > "$state/$id.meta"
}

test_record_round_trips_reason_and_incarnation() {
  local state; state=$(new_state round-trip)
  write_meta "$state" t1 "spawn_gen=s1.1.1"
  fm_stopped_record "$state" t1 "zero model quota until about 19:00; relaunch when it resets" \
    claude "fm:fm-t1" tmux || fail "recording a declared stop failed"
  [ -f "$state/t1.stopped" ] || fail "no record was written"
  [ "$(fm_stopped_field "$state" t1 incarnation)" = "s1.1.1" ] || fail "the incarnation was not recorded"
  [ "$(fm_stopped_field "$state" t1 reason)" = "zero model quota until about 19:00; relaunch when it resets" ] \
    || fail "the reason was not recorded verbatim"
  [ "$(fm_stopped_field "$state" t1 harness)" = claude ] || fail "the harness evidence was not recorded"
  [ "$(fm_stopped_field "$state" t1 backend)" = tmux ] || fail "the backend evidence was not recorded"
  fm_stopped_declared "$state" t1 || fail "a freshly written record must bind to its own incarnation"
  pass "the record round-trips its reason, incarnation, and evidence, and binds on write"
}

# The one that matters. A relaunch mints a new spawn_gen, so the predecessor's
# record must stop binding the moment the meta names a different agent.
test_record_stops_binding_when_the_incarnation_changes() {
  local state; state=$(new_state rebound)
  write_meta "$state" t1 "spawn_gen=s2.1.1"
  fm_stopped_record "$state" t1 "stopped for the quota window" || fail "recording failed"
  fm_stopped_declared "$state" t1 || fail "the record must bind before the incarnation changes"
  write_meta "$state" t1 "spawn_gen=s2.2.2"   # the replacement worker
  ! fm_stopped_declared "$state" t1 \
    || fail "a record may never bind to an incarnation it was not written for"
  pass "a declared stop stops binding the moment the task runs a different agent"
}

test_absent_record_is_never_declared() {
  local state; state=$(new_state absent)
  write_meta "$state" t1 "spawn_gen=s3.1.1"
  ! fm_stopped_declared "$state" t1 || fail "a task with no record must read as undeclared"
  [ -z "$(fm_stopped_field "$state" t1 reason)" ] || fail "an absent record must yield no fields"
  pass "a task with no record is undeclared, so a worker that died on its own still surfaces"
}

# A record whose meta has vanished has nothing to bind to. Refusing here is what
# keeps an unbounded record from outliving every agent the task ever ran.
test_unbindable_record_is_refused_and_never_declared() {
  local state; state=$(new_state unbindable)
  ! fm_stopped_record "$state" t1 "stopped" || fail "a record with no meta to bind to must be refused"
  [ ! -f "$state/t1.stopped" ] || fail "a refused record must not be written"
  write_meta "$state" t1 "spawn_gen=s4.1.1"
  fm_stopped_record "$state" t1 "stopped" || fail "recording with a meta present should succeed"
  rm -f "$state/t1.meta"
  ! fm_stopped_declared "$state" t1 || fail "a record whose meta is gone must not bind to nothing"
  pass "a record that cannot be bound is refused, and one whose task record vanished stops binding"
}

# A meta published before spawn_gen existed still has an identity, so an old task
# is bound rather than left unbounded - and changing that identity spends it.
test_legacy_meta_without_spawn_gen_is_still_bound() {
  local state legacy; state=$(new_state legacy)
  write_meta "$state" t1
  fm_stopped_record "$state" t1 "stopped" || fail "a legacy meta must still be recordable"
  legacy=$(fm_stopped_field "$state" t1 incarnation)
  case "$legacy" in legacy-*) ;; *) fail "a legacy meta should bind through its endpoint identity, got: $legacy" ;; esac
  fm_stopped_declared "$state" t1 || fail "a legacy record must bind to its own identity"
  printf 'window=fm:fm-t1-elsewhere\nworktree=%s/wt-other\nharness=claude\n' "$state" > "$state/t1.meta"
  ! fm_stopped_declared "$state" t1 || fail "a legacy record must stop binding when the identity changes"
  pass "a meta with no spawn_gen is bound through its endpoint identity, not left unbounded"
}

# The record is read back into one-line supervision output, so a multi-line or
# tabbed reason must not be able to forge extra fields.
test_reason_is_collapsed_to_one_line() {
  local state; state=$(new_state oneline)
  write_meta "$state" t1 "spawn_gen=s5.1.1"
  fm_stopped_record "$state" t1 "$(printf 'first line\nincarnation=forged\tand tabbed')" \
    || fail "recording a multi-line reason failed"
  [ "$(fm_stopped_field "$state" t1 incarnation)" = "s5.1.1" ] \
    || fail "a reason must not be able to forge another field"
  case "$(fm_stopped_field "$state" t1 reason)" in
    *"first line"*"forged"*) ;;
    *) fail "the reason text was lost rather than collapsed" ;;
  esac
  [ "$(grep -c . "$state/t1.stopped")" -eq 9 ] || fail "the record grew or lost lines: $(cat "$state/t1.stopped")"
  pass "a multi-line or tabbed reason is collapsed to one line and cannot forge a field"
}

test_record_round_trips_reason_and_incarnation
test_record_stops_binding_when_the_incarnation_changes
test_absent_record_is_never_declared
test_unbindable_record_is_refused_and_never_declared
test_legacy_meta_without_spawn_gen_is_still_bound
test_reason_is_collapsed_to_one_line

echo "all fm-stopped-lib tests passed"
