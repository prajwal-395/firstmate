#!/usr/bin/env bash
# tests/fm-secondmate-watcher-quiet.test.sh - quiet secondmate watchers must be
# detectable, and the auto-arm records must make a future outage diagnosable.
#
# A secondmate home whose watcher goes quiet while its records claim otherwise
# is the outage this covers: the Stop-hook arm is the sole continuity owner in
# every Claude-primary home, and the last such outage ran three days before a
# human armed a watcher by hand. Two halves are pinned here:
#
#   1. Detection: bin/fm-watch.sh's secondmate_watcher_quiet_tick evaluates the
#      model-aware verdict (bin/fm-wake-lib.sh's fm_mate_watcher_health) against
#      each endpoint-recorded local mate home and queues one durable parent
#      check per quiet episode. The verdict, not raw beacon age, decides, so
#      between-turns rewake gaps, extension hand-offs, and idle mates stay
#      silent. The tick itself is exercised through a real watcher process; the
#      verdict is pinned directly case by case.
#   2. Records: the three bounded best-effort auto-arm records in
#      bin/fm-wake-lib.sh (Claude build, last verified success, per-firing
#      disposition history) close the "when did the arm last succeed" and
#      "did the hook ever fire" gaps the epoch ledger cannot answer.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=bin/fm-backend.sh
. "$ROOT/bin/fm-backend.sh"
# shellcheck source=bin/fm-wake-lib.sh
. "$ROOT/bin/fm-wake-lib.sh"
# shellcheck source=bin/fm-supervision-lib.sh
. "$ROOT/bin/fm-supervision-lib.sh"
# shellcheck source=bin/fm-stopped-lib.sh
. "$ROOT/bin/fm-stopped-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-secondmate-watcher-quiet)
WATCH="$ROOT/bin/fm-watch.sh"

# --- harness to supervision-model mapping ------------------------------------

[ "$(fm_supervision_model_for_harness claude)" = autoarm ] \
  || fail "claude must map to the autoarm supervision model"
[ "$(fm_supervision_model_for_harness cursor)" = autoarm ] \
  || fail "cursor must map to the autoarm supervision model"
[ "$(fm_supervision_model_for_harness pi)" = extension ] \
  || fail "pi must map to the extension supervision model"
[ "$(fm_supervision_model_for_harness pi-signed)" = extension ] \
  || fail "pi-signed must map to the extension supervision model"
[ "$(fm_supervision_model_for_harness omp)" = extension ] \
  || fail "omp must map to the extension supervision model"
[ "$(fm_supervision_model_for_harness "")" = persistent ] \
  || fail "an empty harness must map to the strictest (persistent) model"
[ "$(fm_supervision_model_for_harness opencode)" = persistent ] \
  || fail "an unknown harness must map to the strictest (persistent) model"
pass "harness names map to supervision models, unknown maps strictest"

# --- fixture mates ------------------------------------------------------------

# make_mate <dir> <task> [need]
# A mate home directory with the marker the parent tick requires. With need,
# its state carries an in-flight record so the home needs supervision.
make_mate() {
  local dir=$1 task=$2 need=${3:-}
  mkdir -p "$dir/state"
  printf '%s\n' "$task" > "$dir/.fm-secondmate-home"
  if [ -n "$need" ]; then
    printf 'kind=ship\n' > "$dir/state/work.meta"
  fi
  printf '%s\n' "$dir"
}

# --- cross-home verdict -------------------------------------------------------

mate=$(make_mate "$TMP_ROOT/quiet/state-home" quietmate need)
fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" autoarm
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "a mate that needs supervision with no beacon must read as down, got down=$FM_MATE_WATCHER_DOWN"
[ -n "$FM_MATE_WATCHER_DESC" ] \
  || fail "a down verdict must carry a human-readable description"
pass "a mate that needs supervision with no beacon reads as down: $FM_MATE_WATCHER_DESC"

mate=$(make_mate "$TMP_ROOT/idle/state-home" idlemate)
fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" autoarm
[ "$FM_MATE_WATCHER_DOWN" = false ] \
  || fail "an idle mate must stay silent, got down=$FM_MATE_WATCHER_DOWN desc=$FM_MATE_WATCHER_DESC"
pass "an idle mate with nothing riding on its watcher stays silent"

# --- parked homes stay silent: empty and all-stopped --------------------------
# FIRING TWO (vep): zero task records in state/, zero in-flight backlog rows,
# zero queued wakes - yet the check fired. An empty mate home needs no
# supervision, so the verdict must stay down=false and the observe path must
# stay silent and markerless. The backlog file below mirrors the production
# shape (rows present, none in flight); the predicate reads task records, and
# the file documents that neither signal claimed work.
#
# FIRING ONE (lucie): six task records, all deliberately stopped after their
# work was pushed - yet the check fired. A declared stop is agent-free by
# design, so a home whose every record is stopped must read exactly like an
# idle home. A single live record, or a stop record left behind by a replaced
# agent, must still fire: the exclusion silences parked homes, never workers.

# make_stopped_task <mate-state> <id> [gen]: a task record parked on purpose,
# with a declared-stop record bound to its current incarnation.
make_stopped_task() {
  local mstate=$1 id=$2 gen=${3:-gen-$2-1}
  printf 'kind=ship\nspawn_gen=%s\n' "$gen" > "$mstate/$id.meta"
  fm_stopped_record "$mstate" "$id" "parked for test" \
    || fail "could not record a declared stop for $id"
}

# observe_mate <parent-state> <mate-home> <task>: endpoint-record the mate and
# run one observation, printing the reason (if any) and returning its rc.
observe_mate() {
  local pstate=$1 mhome=$2 task=$3
  cat > "$pstate/$task.meta" <<EOF
kind=secondmate
harness=claude
home=$mhome
EOF
  fm_mate_quiet_observe "$pstate" "$pstate/$task.meta" 300
}

parkhome="$TMP_ROOT/parked"
parkstate="$parkhome/parent/state"
mkdir -p "$parkstate"

# The vep shape: nothing of any kind.
vep=$(make_mate "$parkhome/vepmate" vepmate)
mkdir -p "$vep/data"
printf '# backlog\n\n- [x] done: earlier work (Done)\n' > "$vep/data/backlog.md"
[ ! -e "$vep/state/.wake-queue" ] \
  || fail "the empty-home fixture must hold zero queued wakes"
fm_mate_watcher_health "$vep/state" "$vep/bin/fm-watch.sh" 300 "$vep" "$vep" autoarm
[ "$FM_MATE_WATCHER_DOWN" = false ] \
  || fail "a home with zero task records and zero in-flight rows must stay silent, got desc=$FM_MATE_WATCHER_DESC"
out=$(observe_mate "$parkstate" "$vep" vepmate); rc=$?
[ "$rc" -eq 0 ] || fail "observing an empty mate must succeed, got rc=$rc"
[ -z "$out" ] || fail "an empty mate must stay silent, got: $out"
[ ! -e "$parkstate/.secondmate-watcher-quiet-vepmate" ] \
  || fail "an empty mate must leave no episode marker"
pass "a home with zero task records and zero in-flight rows never reports quiet"

# The live shape: one in-flight task, no live watcher - must still fire.
live=$(make_mate "$parkhome/livemate" livemate need)
mkdir -p "$live/data"
printf '# backlog\n\n- [ ] live work (In flight)\n' > "$live/data/backlog.md"
fm_mate_watcher_health "$live/state" "$live/bin/fm-watch.sh" 300 "$live" "$live" autoarm
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "a home with a live in-flight task and no live watcher must read as down, got down=$FM_MATE_WATCHER_DOWN"
out=$(observe_mate "$parkstate" "$live" livemate); rc=$?
[ "$rc" -eq 0 ] || fail "observing a live mate must succeed, got rc=$rc"
assert_contains "$out" "check: secondmate watcher quiet: mate=livemate" "observing a live mate must print its check reason"
assert_present "$parkstate/.secondmate-watcher-quiet-livemate" "a live mate must leave a quiet-episode marker"
pass "a home with a live in-flight task and no live watcher still reports quiet"

# The lucie shape: every record stopped, nothing running.
lucie=$(make_mate "$parkhome/lucie" luciemate)
make_stopped_task "$lucie/state" t1
make_stopped_task "$lucie/state" t2
make_stopped_task "$lucie/state" t3
fm_mate_watcher_health "$lucie/state" "$lucie/bin/fm-watch.sh" 300 "$lucie" "$lucie" autoarm
[ "$FM_MATE_WATCHER_DOWN" = false ] \
  || fail "a home whose every task record is stopped must stay silent, got desc=$FM_MATE_WATCHER_DESC"
assert_contains "$FM_MATE_WATCHER_DESC" "stopped by design" "an all-stopped verdict must say why it stays silent"
out=$(observe_mate "$parkstate" "$lucie" luciemate); rc=$?
[ "$rc" -eq 0 ] || fail "observing an all-stopped mate must succeed, got rc=$rc"
[ -z "$out" ] || fail "an all-stopped mate must stay silent, got: $out"
[ ! -e "$parkstate/.secondmate-watcher-quiet-luciemate" ] \
  || fail "an all-stopped mate must leave no episode marker"
pass "a home whose every task record is stopped never reports quiet"

# One live record among stopped ones still fires: the exclusion never
# silences a worker that may still be running.
mixed=$(make_mate "$parkhome/mixed" mixedmate)
make_stopped_task "$mixed/state" s1
printf 'kind=ship\nspawn_gen=%s\n' "gen-live-1" > "$mixed/state/w1.meta"
fm_mate_watcher_health "$mixed/state" "$mixed/bin/fm-watch.sh" 300 "$mixed" "$mixed" autoarm
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "one live record among stopped ones must still read as down, got down=$FM_MATE_WATCHER_DOWN"
pass "one live record among stopped ones still reports quiet"

# A stop record left behind by a replaced agent binds to nobody: the
# replacement is supervised normally and still fires.
replaced=$(make_mate "$parkhome/replaced" replacedmate)
printf 'kind=ship\nspawn_gen=%s\n' "gen-old-1" > "$replaced/state/r1.meta"
fm_stopped_record "$replaced/state" "r1" "parked for test" \
  || fail "could not record a declared stop for r1"
printf 'kind=ship\nspawn_gen=%s\n' "gen-new-2" > "$replaced/state/r1.meta"
fm_mate_watcher_health "$replaced/state" "$replaced/bin/fm-watch.sh" 300 "$replaced" "$replaced" autoarm
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "a stale stop record from a replaced agent must not silence its replacement, got down=$FM_MATE_WATCHER_DOWN"
pass "a stale stop record never silences the replacement worker"

mate=$(make_mate "$TMP_ROOT/fresh/state-home" freshmate need)
touch "$mate/state/.last-watcher-beat"
fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" autoarm
[ "$FM_MATE_WATCHER_DOWN" = false ] \
  || fail "a mate with a fresh beacon must read as healthy, got desc=$FM_MATE_WATCHER_DESC"
pass "a mate with a fresh beacon reads as healthy under the autoarm model"

mate=$(make_mate "$TMP_ROOT/strict/state-home" strictmate need)
touch "$mate/state/.last-watcher-beat"
fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" persistent
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "a fresh leftover beacon with no live watcher must read as down under the persistent model"
pass "the persistent model still requires a live watcher, not just a fresh beacon"

# An away home whose daemon is healthily cycling (live identity-matched owner
# plus a fresh beacon) stays silent, exactly as the turn-end guard treats it.
afkmate=$(make_mate "$TMP_ROOT/afk/state-home" afkmate need)
: > "$afkmate/state/.afk"
sleep 60 &
daemon=$!
mkdir -p "$afkmate/state/.supervise-daemon.lock"
printf '%s\n' "$daemon" > "$afkmate/state/.supervise-daemon.lock/pid"
fm_pid_identity "$daemon" > "$afkmate/state/.supervise-daemon.lock/pid-identity"
touch "$afkmate/state/.last-watcher-beat"
fm_mate_watcher_health "$afkmate/state" "$afkmate/bin/fm-watch.sh" 300 "$afkmate" "$afkmate" autoarm
[ "$FM_MATE_WATCHER_DOWN" = false ] \
  || fail "a healthily cycling away-mode daemon must stay silent, got desc=$FM_MATE_WATCHER_DESC"
kill "$daemon" 2>/dev/null || true
wait "$daemon" 2>/dev/null || true
pass "a healthily cycling away-mode daemon stays silent"

# An away home whose beacon passed grace reads as down: the daemon stopped
# cycling, and the turn-end guard blocks on exactly this state.
deadmate=$(make_mate "$TMP_ROOT/afk-dead/state-home" deadafkmate need)
: > "$deadmate/state/.afk"
fm_mate_watcher_health "$deadmate/state" "$deadmate/bin/fm-watch.sh" 300 "$deadmate" "$deadmate" autoarm
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "an away home with a stale beacon and no live daemon must read as down"
pass "an away home whose daemon stopped cycling reads as down"

# The caller's own model override must survive a cross-home check.
FM_SUPERVISION_MODEL=extension
mate=$(make_mate "$TMP_ROOT/restore/state-home" restoremate need)
fm_mate_watcher_health "$mate/state" "$mate/bin/fm-watch.sh" 300 "$mate" "$mate" autoarm
[ "${FM_SUPERVISION_MODEL:-__unset__}" = extension ] \
  || fail "a mate health check must restore the caller's supervision model, got ${FM_SUPERVISION_MODEL:-__unset__}"
unset FM_SUPERVISION_MODEL
pass "a mate health check leaves the caller's own supervision model untouched"

# --- shared observe + sweep (poll tick and session-start backstop) -------------

# The residual travels with the verdict: a down verdict never implies a cause,
# because hook silence and no Stop event read identically from outside.
resmate=$(make_mate "$TMP_ROOT/residual/state-home" residualmate need)
fm_mate_watcher_health "$resmate/state" "$resmate/bin/fm-watch.sh" 300 "$resmate" "$resmate" autoarm
[ "$FM_MATE_WATCHER_DOWN" = true ] \
  || fail "the residual fixture must read as down, got down=$FM_MATE_WATCHER_DOWN"
assert_contains "$FM_MATE_WATCHER_DESC" "cause unknown from outside" "a down verdict must carry the outside-cause residual"
assert_contains "$FM_MATE_WATCHER_DESC" "hook silence and no Stop event read identically" "the residual must name the indistinguishable pair"
pass "a down verdict carries the outside-cause residual instead of implying one"

ORIG_QUEUE=$FM_WAKE_QUEUE
ohome="$TMP_ROOT/observe"
opstate="$ohome/parent/state"
omate=$(make_mate "$ohome/mate" obsmate need)
mkdir -p "$opstate"
cat > "$opstate/obsmate.meta" <<EOF
kind=secondmate
harness=claude
home=$omate
EOF

out=$(fm_mate_quiet_observe "$opstate" "$opstate/obsmate.meta" 300); rc=$?
[ "$rc" -eq 0 ] || fail "observing a quiet mate must succeed, got rc=$rc"
assert_contains "$out" "check: secondmate watcher quiet: mate=obsmate" "observing a quiet mate must print its check reason"
assert_contains "$out" "cause unknown from outside" "the queued reason must carry the residual"
grep -qF "secondmate-watcher-quiet-obsmate" "$opstate/.wake-queue" \
  || fail "the observation must land in the observed state's durable queue"
assert_present "$opstate/.secondmate-watcher-quiet-obsmate" "a quiet episode must leave a dedupe marker"
assert_contains "$(cat "$opstate/.secondmate-watcher-quiet-obsmate")" "cause unknown from outside" "the marker must carry the residual too"
[ "$FM_WAKE_QUEUE" = "$ORIG_QUEUE" ] \
  || fail "observing a fixture state must restore the caller's queue binding, got $FM_WAKE_QUEUE"
pass "one observation queues one durable check with its marker"

# The second call is silent: the episode is already alerted, and no row doubles.
out=$(fm_mate_quiet_observe "$opstate" "$opstate/obsmate.meta" 300); rc=$?
[ "$rc" -eq 0 ] || fail "re-observing an alerted episode must succeed, got rc=$rc"
[ -z "$out" ] || fail "an already-alerted episode must stay silent, got: $out"
[ "$(grep -cF 'secondmate-watcher-quiet-obsmate' "$opstate/.wake-queue")" = 1 ] \
  || fail "an alerted episode must not queue a duplicate row"
pass "an alerted episode stays silent with no duplicate row"

# A pre-queued key still gets its marker (the tick's wake-on-reason parity),
# but the row is never duplicated.
qhome="$TMP_ROOT/prequeued"
qpstate="$qhome/parent/state"
qmate=$(make_mate "$qhome/mate" qmate need)
mkdir -p "$qpstate"
cat > "$qpstate/qmate.meta" <<EOF
kind=secondmate
harness=claude
home=$qmate
EOF
printf '%s\t%s\t%s\t%s\t%s\n' "1700000000" "1" "check" "secondmate-watcher-quiet-qmate" "stale row" >> "$qpstate/.wake-queue"
printf '%s\n' "1" > "$qpstate/.wake-queue.seq"
out=$(fm_mate_quiet_observe "$qpstate" "$qpstate/qmate.meta" 300); rc=$?
[ "$rc" -eq 0 ] || fail "observing a queued-but-unmarked episode must succeed, got rc=$rc"
[ -n "$out" ] || fail "an unmarked episode must still report its reason"
[ "$(grep -cF 'secondmate-watcher-quiet-qmate' "$qpstate/.wake-queue")" = 1 ] \
  || fail "a pre-queued key must not gain a duplicate row"
assert_present "$qpstate/.secondmate-watcher-quiet-qmate" "the unmarked episode must gain its marker"
pass "a pre-queued key gains its marker without a duplicate row"

# Skips are silent rc=2: a non-mate kind, a remote mate, and a bad task name.
printf 'kind=ship\n' > "$opstate/plain.meta"
out=$(fm_mate_quiet_observe "$opstate" "$opstate/plain.meta" 300); rc=$?
[ "$rc" -eq 2 ] || fail "a non-mate meta must skip, got rc=$rc"
[ -z "$out" ] || fail "a skipped meta must stay silent"
cat > "$opstate/rmate.meta" <<EOF
kind=secondmate
harness=claude
remote_host=elsewhere
home=$omate
EOF
out=$(fm_mate_quiet_observe "$opstate" "$opstate/rmate.meta" 300); rc=$?
[ "$rc" -eq 2 ] || fail "a remote mate must skip, got rc=$rc"
[ -z "$out" ] || fail "a skipped remote mate must stay silent"
pass "non-mate, remote, and foreign metas skip silently"

# An idle mate observes healthy: silent, markerless.
imat=$(make_mate "$ohome/idlem" idlemate)
cat > "$opstate/idlemate.meta" <<EOF
kind=secondmate
harness=claude
home=$imat
EOF
out=$(fm_mate_quiet_observe "$opstate" "$opstate/idlemate.meta" 300); rc=$?
[ "$rc" -eq 0 ] || fail "observing an idle mate must succeed, got rc=$rc"
[ -z "$out" ] || fail "an idle mate must stay silent, got: $out"
[ ! -e "$opstate/.secondmate-watcher-quiet-idlemate" ] \
  || fail "an idle mate must leave no episode marker"
pass "an idle mate observes healthy with no marker"

# Recovery clears the episode on the next observation.
touch "$omate/state/.last-watcher-beat"
out=$(fm_mate_quiet_observe "$opstate" "$opstate/obsmate.meta" 300); rc=$?
[ "$rc" -eq 0 ] || fail "observing a recovered mate must succeed, got rc=$rc"
[ -z "$out" ] || fail "a recovered mate must stay silent, got: $out"
[ ! -e "$opstate/.secondmate-watcher-quiet-obsmate" ] \
  || fail "recovery must clear the quiet-episode marker"
pass "recovery clears the episode on the next observation"

# A marker that is not a regular file fails loudly, like the stall tick.
ln -s /tmp "$opstate/.secondmate-watcher-quiet-obsmate"
out=$(fm_mate_quiet_observe "$opstate" "$opstate/obsmate.meta" 300); rc=$?
[ "$rc" -eq 1 ] || fail "a symlinked marker must fail loudly, got rc=$rc"
rm -f "$opstate/.secondmate-watcher-quiet-obsmate"
pass "a non-regular marker fails loudly instead of observing"

# The sweep observes every meta and prints only newly queued reasons.
swhome="$TMP_ROOT/sweep"
swstate="$swhome/parent/state"
sw1=$(make_mate "$swhome/m1" sweep1 need)
sw2=$(make_mate "$swhome/m2" sweep2)
mkdir -p "$swstate"
cat > "$swstate/sweep1.meta" <<EOF
kind=secondmate
harness=claude
home=$sw1
EOF
cat > "$swstate/sweep2.meta" <<EOF
kind=secondmate
harness=claude
home=$sw2
EOF
printf 'kind=ship\n' > "$swstate/plain.meta"
out=$(fm_mate_quiet_sweep "$swstate" 300); rc=$?
[ "$rc" -eq 0 ] || fail "sweeping a mixed fleet must succeed, got rc=$rc"
[ "$(printf '%s\n' "$out" | grep -c .)" = 1 ] \
  || fail "a sweep must print exactly the one newly queued reason, got: $out"
assert_contains "$out" "mate=sweep1" "the sweep must report the quiet mate"
assert_present "$swstate/.secondmate-watcher-quiet-sweep1" "the sweep must mark the quiet episode"
[ ! -e "$swstate/.secondmate-watcher-quiet-sweep2" ] \
  || fail "the sweep must leave the idle mate unmarked"
out=$(fm_mate_quiet_sweep "$swstate" 300); rc=$?
[ "$rc" -eq 0 ] || fail "re-sweeping must succeed, got rc=$rc"
[ -z "$out" ] || fail "a re-sweep with nothing new must stay silent, got: $out"
pass "a sweep reports only newly queued episodes across mixed metas"

FAKEBIN=$(fm_fakebin "$TMP_ROOT/fakebin")
cat > "$FAKEBIN/claude" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "${FM_FAKE_CLAUDE_VERSION:-2.1.7 (Claude Code)}"
SH
chmod +x "$FAKEBIN/claude"

vhome="$TMP_ROOT/version/state"
mkdir -p "$vhome"
PATH="$FAKEBIN:$PATH" fm_autoarm_record_version "$vhome"
assert_present "$vhome/.claude-autoarm-version" "the first firing must record the running build"
assert_contains "$(cat "$vhome/.claude-autoarm-version")" "2.1.7" "the version file must carry the build string"
assert_contains "$(cat "$vhome/.claude-autoarm-version")" "recorded_at=" "the version file must carry its observation time"
: > "$vhome/marker"
sleep 1
PATH="$FAKEBIN:$PATH" fm_autoarm_record_version "$vhome"
[ "$vhome/.claude-autoarm-version" -nt "$vhome/marker" ] \
  && fail "an unchanged build must not dirty the version file"
export FM_FAKE_CLAUDE_VERSION="2.1.8 (Claude Code)"
PATH="$FAKEBIN:$PATH" fm_autoarm_record_version "$vhome"
assert_contains "$(cat "$vhome/.claude-autoarm-version")" "2.1.8" "an upgraded build must refresh the version file"
pass "the build record is written once per build and stays inert otherwise"

nohome="$TMP_ROOT/noversion/state"
mkdir -p "$nohome" "$TMP_ROOT/emptybin"
# A PATH with no claude anywhere: the system directories alone resolve the
# ornamentals (date, sed, mv) but no harness binary.
PATH="$TMP_ROOT/emptybin:/usr/bin:/bin" fm_autoarm_record_version "$nohome"
[ ! -e "$nohome/.claude-autoarm-version" ] \
  || fail "a home without a claude binary must record nothing"
pass "the build record is best-effort where no Claude binary exists"

# --- last-success record ------------------------------------------------------

shome="$TMP_ROOT/success/state"
mkdir -p "$shome"
fm_autoarm_record_success "$shome" 41 rewake
line=$(cat "$shome/.claude-autoarm-last-success")
assert_contains "$line" "gen=41" "the success record must carry the generation"
assert_contains "$line" "outcome=rewake" "the success record must carry the outcome"
assert_contains "$line" "at=" "the success record must carry its timestamp"
fm_autoarm_record_success "$shome" 42 healthy
assert_contains "$(cat "$shome/.claude-autoarm-last-success")" "gen=42" "a later success must supersede the earlier one"
pass "the last verified success separates from the epoch's last-claim touch"

# --- disposition history ------------------------------------------------------

hhome="$TMP_ROOT/history/state"
mkdir -p "$hhome"
fm_autoarm_history_append "$hhome" 7 claim firing
fm_autoarm_history_append "$hhome" 7 rewake ""
fm_autoarm_history_append "$hhome" - deferred open-claim
log=$(cat "$hhome/.claude-autoarm-history.log")
assert_contains "$log" "gen=7" "history lines must carry the generation"
assert_contains "$log" "claim" "history must record the claim disposition"
assert_contains "$log" "rewake" "history must record the terminal disposition"
assert_contains "$log" "deferred" "history must record pre-claim defers"
fm_autoarm_history_append "$hhome" 8 clean "$(printf 'line1\nline2\tTabbed')"
[ "$(wc -l < "$hhome/.claude-autoarm-history.log" | tr -d '[:space:]')" = 4 ] \
  || fail "a detail carrying newline/tab must not split the history line"
[ "$(tail -n 1 "$hhome/.claude-autoarm-history.log" | awk -F '\t' '{print NF}')" = 4 ] \
  || fail "a detail carrying tabs must be sanitized to the four-field shape"
pass "every firing that passes the need gate leaves one history line"

# Rotation stays bounded: realistic lines past the cap, then one append.
bhome="$TMP_ROOT/rotation/state"
mkdir -p "$bhome"
i=0
while [ "$i" -lt 700 ]; do
  printf 'at=1700000000\tgen=%s\toutcome=rewake\tdetail=\n' "$i" >> "$bhome/.claude-autoarm-history.log"
  i=$((i + 1))
done
fm_autoarm_history_append "$bhome" 999 clean ""
size=$(wc -c < "$bhome/.claude-autoarm-history.log" | tr -d '[:space:]')
[ "$size" -lt 65536 ] \
  || fail "history rotation must bound the log, got $size bytes"
assert_contains "$(tail -n 1 "$bhome/.claude-autoarm-history.log")" "gen=999" "rotation must keep the newest line"
head -n 1 "$bhome/.claude-autoarm-history.log" | grep -q '^at=' \
  || fail "rotation must keep well-formed lines"
pass "history rotation bounds the log while keeping the newest evidence"

# --- end-to-end through a real watcher ----------------------------------------

# A quiet mate must surface one durable parent check within one poll cycle.
ehome="$TMP_ROOT/e2e"
pstate="$ehome/parent/state"
mate=$(make_mate "$ehome/mate" quietmate need)
mkdir -p "$pstate"
cat > "$pstate/quietmate.meta" <<EOF
kind=secondmate
harness=claude
home=$mate
EOF
eout="$ehome/watch.out"
PATH="$FAKEBIN:$PATH" FM_STATE_OVERRIDE="$pstate" FM_POLL=1 FM_SIGNAL_GRACE=1 \
  FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$WATCH" > "$eout" 2>&1 &
watcher=$!
i=0
while [ "$i" -lt 300 ]; do
  grep -qF "secondmate watcher quiet" "$eout" 2>/dev/null && break
  kill -0 "$watcher" 2>/dev/null || break
  sleep 0.1
  i=$((i + 1))
done
wait "$watcher" 2>/dev/null || true
grep -qF "check: secondmate watcher quiet: mate=quietmate" "$eout" \
  || fail "a quiet mate must wake the parent within one poll cycle: $(cat "$eout")"
grep -qF "secondmate-watcher-quiet-quietmate" "$pstate/.wake-queue" \
  || fail "the quiet wake must land in the durable queue"
assert_present "$pstate/.secondmate-watcher-quiet-quietmate" "a quiet episode must leave a dedupe marker"
pass "a quiet mate surfaces one durable parent check within one poll cycle"

# Recovery clears the episode: the same parent with a fresh mate beacon stays silent.
touch "$mate/state/.last-watcher-beat"
rm -f "$pstate/.wake-queue" "$pstate/.wake-queue.seq"
eout2="$ehome/watch2.out"
PATH="$FAKEBIN:$PATH" FM_STATE_OVERRIDE="$pstate" FM_POLL=1 FM_SIGNAL_GRACE=1 \
  FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$WATCH" > "$eout2" 2>&1 &
watcher2=$!
sleep 5
kill "$watcher2" 2>/dev/null || true
wait "$watcher2" 2>/dev/null || true
[ ! -e "$pstate/.secondmate-watcher-quiet-quietmate" ] \
  || fail "recovery must clear the quiet-episode marker"
if [ -e "$pstate/.wake-queue" ]; then
  grep -qF "secondmate watcher quiet" "$pstate/.wake-queue" \
    && fail "a recovered mate must not re-wake the parent"
fi
pass "a recovered mate clears its episode and stays silent"

# An idle mate never wakes the parent at all.
ihome="$TMP_ROOT/e2e-idle"
ipstate="$ihome/parent/state"
imat=$(make_mate "$ihome/mate" idlemate)
mkdir -p "$ipstate"
cat > "$ipstate/idlemate.meta" <<EOF
kind=secondmate
harness=claude
home=$imat
EOF
eout3="$ihome/watch3.out"
PATH="$FAKEBIN:$PATH" FM_STATE_OVERRIDE="$ipstate" FM_POLL=1 FM_SIGNAL_GRACE=1 \
  FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$WATCH" > "$eout3" 2>&1 &
watcher3=$!
sleep 5
kill "$watcher3" 2>/dev/null || true
wait "$watcher3" 2>/dev/null || true
if [ -e "$ipstate/.wake-queue" ]; then
  grep -qF "secondmate watcher quiet" "$ipstate/.wake-queue" \
    && fail "an idle mate must never wake the parent"
fi
[ ! -e "$ipstate/.secondmate-watcher-quiet-idlemate" ] \
  || fail "an idle mate must leave no quiet-episode marker"
pass "an idle mate never wakes the parent"

echo "ALL TESTS PASSED"
