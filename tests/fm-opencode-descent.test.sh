#!/usr/bin/env bash
# Behavior tests for bin/fm-opencode-descent-lib.sh - the running-worker half
# of the opencode Go-then-Codex Plus ladder.
#
# The dispatch gate (bin/fm-opencode-ladder-lib.sh) routes the NEXT spawn; it
# never looks at a lane again. A lane that starts on Go and hits the cap
# mid-run sits in a vendor retry backoff for hours doing nothing, which the
# supervisor reads as working. This evaluation re-checks RUNNING lanes against
# the same cap evidence the dispatch gate uses and moves the honest ones.
#
# The load-bearing contracts:
#   1. A healthy lane, a transient retry, and an expired cap stay silent: the
#      descent never touches a worker that is making progress or may resume.
#   2. A quota-scale cap is proven through the REAL record helper
#      (bin/fm-opencode-retry.sh) fed a recorded 21h 54m vendor backoff
#      (78840s), so the state machine is exercised against the refusal
#      without spending paid quota - plus the REAL busy writer
#      (bin/fm-busy-event.sh) latching
#      session-retry, so a stale sidecar beside a resumed turn never moves a
#      worker.
#   3. A proven Go cap relaunches the lane onto Codex Plus through the
#      control plane (overridable for tests), verifies the durable record
#      followed, clears the dead session's sidecar, and says `relaunched`.
#   4. A Plus-recorded cap with nowhere to go, a secondmate, and a failed
#      relaunch surface as `refused`, exactly once per episode - a
#      persistent supervision agent is never auto-touched, and a broken move
#      is never retried blindly.
#   5. FM_OPENCODE_LADDER_OVERRIDE holds a capped worker where the captain put
#      it and says `override`, once per episode.
#   6. A move the record did not follow is reported as `unrecorded`, never as
#      a success.
#   7. An unbound cap on a Go-recorded lane still relaunches, with the
#      ambiguity owned in the handoff note - the launch gate's own bias.
#   8. Off-ladder models are never governed, and the evaluation itself is
#      rate-limited off the watcher's poll.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=bin/fm-opencode-descent-lib.sh
. "$ROOT/bin/fm-opencode-descent-lib.sh"
# Sourced here so the stopped-record fixtures below are written through the
# real stopped library whether or not the descent library under test defers
# to it yet; the fix adds the same guarded source inside the library itself.
# shellcheck source=bin/fm-stopped-lib.sh
. "$ROOT/bin/fm-stopped-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-opencode-descent)
mkdir -p "$TMP_ROOT"
TMP_ROOT=$(cd "$TMP_ROOT" && pwd)
trap 'rm -rf "$TMP_ROOT"' EXIT
FAKEBIN="$TMP_ROOT/fakebin"
mkdir -p "$FAKEBIN"
cat > "$FAKEBIN/quota-axi" <<'SH'
#!/usr/bin/env bash
if [ -n "${QUOTA_AXI_SNAPSHOT:-}" ] && [ -f "$QUOTA_AXI_SNAPSHOT" ]; then
  cat "$QUOTA_AXI_SNAPSHOT"
  exit 0
fi
exit 1
SH
chmod +x "$FAKEBIN/quota-axi"
PATH="$FAKEBIN:$PATH"
export PATH

# Hermetic home: the Codex Plus effort and the second-workspace pick resolve
# through FM_CONFIG_OVERRIDE then FM_HOME/config, so the invoking home's real
# dispatch-policy.json (notably its Codex effort) and opencode-second-org would
# otherwise leak into the default assertions below. Every case runs under this
# empty home; the configured-effort and second-workspace cases set
# FM_CONFIG_OVERRIDE themselves for the case under test.
FAKEHOME="$TMP_ROOT/fakehome"
mkdir -p "$FAKEHOME/config"
FM_HOME="$FAKEHOME"
export FM_HOME
unset FM_CONFIG_OVERRIDE

RETRY="$ROOT/bin/fm-opencode-retry.sh"
BUSY="$ROOT/bin/fm-busy-event.sh"

GO='opencode-go/muse-spark-1.3-contributor'
PLUS='gpt-6-luna'
# The plugin's own vocabulary: OpenCode's event stream reports model.id with
# NO provider prefix, and recordRetry passes it straight through to the
# sidecar. Production sidecars (2026-09-19) carry this bare form, so the
# regressions below speak it - a fixture in the gate's prefixed vocabulary
# would be blind to a producer-side mismatch by construction.
GO_BARE='muse-spark-1.3-contributor'

now_s() { date +%s; }
ms_from_now() {  # <offset-secs> -> epoch ms
  echo $(( ($(now_s) + $1) * 1000 ))
}

fresh_state() {  # <name> -> empty state dir
  local dir="$TMP_ROOT/state-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

# write_meta <state-dir> <id> <harness> <model> <kind>: the durable record the
# way a spawn leaves it - the routed tier, not the requested one - with an
# endpoint the capture stub below can serve.
write_meta() {  # <state-dir> <id> <harness> <model> <kind>
  printf 'harness=%s\nmodel=%s\nkind=%s\nbackend=tmux\nwindow=pane-%s\n' \
    "$3" "$4" "$5" "$2" > "$1/$2.meta"
}

# --- pane-capture stub -------------------------------------------------------
# fm_opencode_descent_cap captures the pane tail for the idle shape; the stub
# serves per-target fixture text so that shape is exercised without a real
# pane. A target with no fixture is uncapturable (unknown, never a move).
FM_PANE_FIXTURES="$TMP_ROOT/panes"
mkdir -p "$FM_PANE_FIXTURES"
fm_backend_capture() {  # <backend> <target> <lines> [label]
  local fixture="$FM_PANE_FIXTURES/${2:-}.txt"
  [ -f "$fixture" ] || return 1
  cat "$fixture"
}

# arm_busy <state-dir> <id> <event>: the REAL busy writer the way the
# spawn-installed plugin drives it - busy on the latched worker session.
arm_busy() {  # <state-dir> <id> <event>
  "$BUSY" arm "$1" "$2" >/dev/null || return 1
  "$BUSY" apply "$1" "$2" busy --current-gen \
    --source opencode-plugin --event "$3" >/dev/null || return 1
}

# arm_idle <state-dir> <id> <event>: the plugin's own turn-end write - state
# idle on session-idle / session-status-idle, which also clears the retry
# sidecar. This is the record an idle-after-cap lane carries.
arm_idle() {  # <state-dir> <id> <event>
  "$BUSY" arm "$1" "$2" >/dev/null || return 1
  "$BUSY" apply "$1" "$2" idle --current-gen \
    --source opencode-plugin --event "$3" >/dev/null || return 1
}

# record_cap <state-dir> <id> <offset-secs> [model]: one retry observation
# through the REAL record helper the plugin names - the recorded vendor
# refusal converted to the structural `next` timestamp the detector owns.
record_cap() {  # <state-dir> <id> <offset-secs> [model]
  local state=$1 id=$2 offset=$3 model=${4:-}
  if [ -n "$model" ]; then
    "$RETRY" record "$state" "$id" 3 "$(ms_from_now "$offset")" "$model" "ses_$id"
  else
    "$RETRY" record "$state" "$id" 3 "$(ms_from_now "$offset")"
  fi
}

# --- the control-plane stub --------------------------------------------------
# The tick moves a worker through bin/fm-control.sh relaunch. Tests override
# that binary so no real agent is ever stopped: the stub records its argv,
# fails on demand, and publishes the Go record the way a real relaunch would.
# A --opencode-workspace secondary move additionally writes the workspace
# marker the way bin/fm-spawn.sh --relaunch records it, while an explicit
# --opencode-workspace main move clears a stale marker the same way.
STUB="$TMP_ROOT/control-stub.sh"
STUB_LOG="$TMP_ROOT/control-argv.log"
cat > "$STUB" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$FM_STUB_LOG"
[ "${FM_STUB_EXIT:-0}" != 0 ] && exit 1
if [ "${FM_STUB_PUBLISH_GO:-0}" = 1 ]; then
  id=$1
  shift
  model=
  harness=
  workspace=
  while [ "$#" -gt 0 ]; do
    case "$1" in --model) model=$2; shift 2 ;; --harness) harness=$2; shift 2 ;; --opencode-workspace) workspace=$2; shift 2 ;; *) shift ;; esac
  done
  meta="$FM_STUB_STATE/$id.meta"
  sed -e "s|^model=.*|model=$model|" -e "s|^harness=.*|harness=$harness|" "$meta" > "$meta.new" \
    && mv "$meta.new" "$meta"
  if [ "$workspace" = secondary ]; then
    grep -v '^opencode_workspace=' "$meta" > "$meta.new" \
      && printf 'opencode_workspace=secondary\n' >> "$meta.new" \
      && mv "$meta.new" "$meta"
  elif [ "$workspace" = main ]; then
    grep -v '^opencode_workspace=' "$meta" > "$meta.new" \
      && mv "$meta.new" "$meta"
  fi
fi
exit 0
SH
chmod +x "$STUB"

stub_env() {  # <state-dir> <exit> <publish>: reset stub behavior for one case
  FM_STUB_STATE=$1 FM_STUB_EXIT=$2 FM_STUB_PUBLISH_GO=$3
  export FM_STUB_STATE FM_STUB_EXIT FM_STUB_PUBLISH_GO
  export FM_STUB_LOG="$STUB_LOG"
  : > "$STUB_LOG"
  export FM_OPENCODE_DESCENT_CONTROL_BIN="$STUB"
}

stub_calls() { cat "$STUB_LOG" 2>/dev/null; }
stub_called() { [ -s "$STUB_LOG" ]; }

# run_tick <state-dir>: one evaluation, outcome lines on stdout.
run_tick() {  # <state-dir>
  FM_OPENCODE_DESCENT_INTERVAL=0 fm_opencode_descent_tick "$1" 2>/dev/null
}

# --- 1. healthy, transient, and expired lanes stay silent ---------------------

test_healthy_lane_is_silent() {
  local state out
  state=$(fresh_state healthy)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-busy || fail "busy writer refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail a healthy lane"
  [ -z "$out" ] || fail "a healthy lane must stay silent, said: $out"
  stub_called && fail "a healthy lane must never reach the control plane"
  pass "a healthy lane stays silent"
}

test_transient_retry_stays() {
  local state out
  state=$(fresh_state transient)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 8 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "a seconds-long backoff must stay silent, said: $out"
  stub_called && fail "a transient retry must never reach the control plane"
  pass "a transient retry backoff stays put"
}

test_expired_cap_stays() {
  local state out
  state=$(fresh_state expired)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 -82800 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "an expired cap must stay silent, said: $out"
  stub_called && fail "an expired cap must never reach the control plane"
  pass "an expired cap is left alone"
}

# --- 2. the latch: a stale sidecar beside a resumed turn never moves ---------

test_stale_sidecar_needs_latch() {
  local state out
  state=$(fresh_state latch)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  # Quota-scale sidecar, but the busy record says the turn resumed: the
  # sidecar belongs to a retired observation, not to this worker now.
  arm_busy "$state" lane1 session-busy || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "a stale sidecar must stay silent, said: $out"
  stub_called && fail "a stale sidecar must never reach the control plane"
  pass "a stale sidecar beside a resumed turn never moves a worker"
}

# --- 3. the recorded refusal relaunches onto Codex Plus -----------------------

test_recorded_refusal_relaunches_to_plus() {
  local state out
  state=$(fresh_state refusal)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  # A 21h 54m vendor backoff (78840s) against the Go tier, converted into the
  # structural `next` timestamp the detector owns.
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail past the cap"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "a proven Go cap must relaunch, said: ${out:-<silent>}" ;;
  esac
  stub_called || fail "a proven Go cap must reach the control plane"
  case "$(stub_calls)" in
    *'relaunch'*"$PLUS"*) : ;;
    *) fail "the move must be a relaunch onto Codex Plus, called: $(stub_calls)" ;;
  esac
  case "$(stub_calls)" in
    *'--effort xhigh'*) : ;;
    *) fail "the Codex Plus fallback must use xhigh effort, called: $(stub_calls)" ;;
  esac
  case "$(stub_calls)" in
    *'--note'*) : ;;
    *) fail "the replacement must inherit a handoff note, called: $(stub_calls)" ;;
  esac
  [ "$(fm_meta_model "$state" lane1)" = "$PLUS" ] \
    || fail "the durable record must name Codex Plus after the move"
  [ ! -f "$state/lane1.opencode-retry" ] \
    || fail "the dead session's sidecar must be cleared after the move"
  pass "the recorded Go refusal relaunches the lane onto Codex Plus"
}

test_pi_ladder_lane_remains_in_descent_scope_when_opted_in() {
  local state out
  state=$(fresh_state pi-ladder)
  stub_env "$state" 0 1
  printf '%s\n' on > "$FAKEHOME/config/pi-opencode-go"
  write_meta "$state" lane1 pi "$GO" scout
  printf 'dispatch_ladder=opencode\n' >> "$state/lane1.meta"
  arm_busy "$state" lane1 session-retry || fail "busy writer refused Pi fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused Pi Go cap"
  out=$(run_tick "$state") || fail "tick must never fail for an opted-in Pi ladder lane"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "an opted-in Pi ladder lane with a proven Go cap must descend, said: ${out:-<silent>}" ;;
  esac
  case "$(stub_calls)" in *'--harness codex'*"$PLUS"*) : ;; *) fail "descent did not request the Plus rung through the control plane: $(stub_calls)" ;; esac
  pass "an opted-in Pi ladder lane remains eligible for the existing cap descent"
}

test_descent_uses_configured_codex_plus_effort() {
  local state out config_dir
  state=$(fresh_state configured-effort)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  config_dir="$TMP_ROOT/config-configured-effort"
  mkdir -p "$config_dir"
  printf '{"default":[{"harness":"opencode","model":"%s","provider":"opencode-go"},{"harness":"codex","model":"%s","effort":"high","provider":"codex"}]}\n' \
    "$GO" "$PLUS" > "$config_dir/dispatch-policy.json"
  out=$(FM_CONFIG_OVERRIDE="$config_dir" run_tick "$state") || fail "tick must never fail past the cap"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "a proven Go cap must relaunch with configured effort, said: ${out:-<silent>}" ;;
  esac
  case "$(stub_calls)" in
    *'--effort high'*) : ;;
    *) fail "the Codex Plus relaunch must use its configured effort, called: $(stub_calls)" ;;
  esac
  pass "cross-harness descent uses the configured Codex Plus effort"
}

fm_meta_model() {  # <state-dir> <id> -> recorded model (test reader only)
  sed -n 's/^model=//p' "$1/$2.meta"
}

test_capped_plus_lane_descends_to_go() {
  local state out cap
  state=$(fresh_state plus-lane)
  stub_env "$state" 0 1
  write_meta "$state" lane1 codex "$PLUS" scout
  printf '%s\n' "You've hit your usage limit. Try again at 2030-01-01T00:00:00Z" \
    > "$FM_PANE_FIXTURES/pane-lane1.txt"
  out=$(run_tick "$state") || fail "tick must not fail on a Codex cap"
  case "$out" in relaunched' '*) : ;; *) fail "a capped Codex lane must descend, said: ${out:-<silent>}" ;; esac
  case "$(stub_calls)" in *'--harness opencode'*"$GO"*) : ;; *) fail "Codex must relaunch onto OpenCode Go, called: $(stub_calls)" ;; esac
  [ "$(fm_meta_model "$state" lane1)" = "$GO" ] || fail "the lane record must follow the Go relaunch"
  cap=$("$RETRY" check-cap "$state" plus) || fail "Codex's stated reset must persist as the shared rung record"
  assert_contains "$cap" 'reset_at=1893456000' "the Codex limit message's stated reset is preserved"
  pass "a Codex usage-limit message records its reset and descends across harnesses"
}

# --- 3a. the human local-time reset form (2026-10-10 incident) ----------------
# Codex v0.162.1 prints 'try again at Oct 14th, 2026 3:34 AM' (ordinal day,
# 12-hour clock, local time zone), which the ISO-only preserver never
# recorded - so the Plus rung kept reading open and lanes relaunched onto it.
# The exact incident string pins the form through the clock parser with no
# expiry involved, so it stays green past the incident window; the
# far-future sentence below proves the same shape descends a lane end to end.

test_codex_human_reset_parses_incident_string() {
  local epoch back
  epoch=$(fm_opencode_descent_codex_human_epoch "Oct 14th, 2026 3:34 AM") \
    || fail "the incident's human reset form must parse"
  back=$(date -d "@$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null) \
    || back=$(date -r "$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null) \
    || fail "the test platform refused to format the parsed epoch"
  [ "$back" = '2026-10-14 03:34' ] \
    || fail "the ordinal 12-hour form must read Oct 14 2026 03:34 local, read: ${back:-<empty>} (epoch $epoch)"
  epoch=$(fm_opencode_descent_codex_human_epoch "October 2nd, 2030 at 1:05 PM") \
    || fail "the full-month PM variant must parse"
  back=$(date -d "@$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null) \
    || back=$(date -r "$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null) \
    || fail "the test platform refused to format the parsed epoch"
  [ "$back" = '2030-10-02 13:05' ] \
    || fail "the full-month PM variant must read Oct 2 2030 13:05 local, read: ${back:-<empty>} (epoch $epoch)"
  fm_opencode_descent_codex_human_epoch "not a reset clock" >/dev/null 2>&1 \
    && fail "garbage must not parse as a reset clock"
  pass "the incident's human reset string parses to its local clock"
}

test_codex_human_reset_form_descends() {
  local state out cap horizon
  state=$(fresh_state plus-human)
  stub_env "$state" 0 1
  write_meta "$state" lane1 codex "$PLUS" scout
  printf '%s\n' "You've hit your usage limit. Try again at Oct 14th, 2030 3:34 AM" \
    > "$FM_PANE_FIXTURES/pane-lane1.txt"
  out=$(run_tick "$state") || fail "tick must not fail on a Codex human-form cap"
  case "$out" in relaunched' '*) : ;; *) fail "a human-form Codex cap must descend, said: ${out:-<silent>}" ;; esac
  case "$(stub_calls)" in *'--harness opencode'*"$GO"*) : ;; *) fail "Codex must relaunch onto OpenCode Go, called: $(stub_calls)" ;; esac
  [ "$(fm_meta_model "$state" lane1)" = "$GO" ] || fail "the lane record must follow the Go relaunch"
  cap=$("$RETRY" check-cap "$state" plus) || fail "Codex's human-form reset must persist as the shared rung record"
  horizon=${cap##*horizon_s=}; horizon=${horizon%% *}
  case "$horizon" in ''|*[!0-9]*) fail "the human-form reset must carry a numeric horizon, said: $cap" ;; esac
  [ "$horizon" -gt 86400 ] \
    || fail "the human-form reset must carry the vendor horizon, not the bounded fallback, said: $cap"
  pass "a Codex human-form reset records its horizon and descends across harnesses"
}

test_codex_unparseable_reset_records_bounded_cap() {
  local state out cap horizon err
  state=$(fresh_state plus-unparseable)
  stub_env "$state" 0 1
  write_meta "$state" lane1 codex "$PLUS" scout
  printf '%s\n' "You've hit your usage limit. Try again soon." \
    > "$FM_PANE_FIXTURES/pane-lane1.txt"
  err=$(fm_opencode_descent_codex_limit_cap "$state" lane1 2>&1) \
    || fail "a limit phrase with no parseable reset must still record the cap"
  assert_contains "$err" 'conservative' "the bounded fallback must say it is conservative"
  cap=$("$RETRY" check-cap "$state" plus) || fail "an unparseable Codex reset must still record the plus cap"
  horizon=${cap##*horizon_s=}; horizon=${horizon%% *}
  case "$horizon" in ''|*[!0-9]*) fail "the fallback cap must carry a numeric horizon, said: $cap" ;; esac
  [ "$horizon" -gt 0 ] && [ "$horizon" -le 3600 ] \
    || fail "the fallback cap must stay bounded, said: $cap"
  out=$(run_tick "$state") || fail "tick must not fail past an unparseable Codex cap"
  case "$out" in relaunched' '*) : ;; *) fail "an unparseable Codex cap must still descend, said: ${out:-<silent>}" ;; esac
  [ "$(fm_meta_model "$state" lane1)" = "$GO" ] || fail "the lane record must follow the Go relaunch"
  pass "an unparseable Codex reset records a bounded cap and descends instead of reading open"
}

# --- 3b. the plugin's bare-model vocabulary binds the same rungs -------------
# PR 129 normalised the dispatch gate for bare sidecar models but left this
# file's own prefixed-only copies. These speak the producer's vocabulary on
# both halves - the sidecar model and the recorded lane model - so either
# form binds the same rung and moves.

test_plugin_vocabulary_go_cap_binds_go() {
  local state cap bound
  state=$(fresh_state barecap)
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  # The captured 2026-09-19 specimen's shape with the Go id: the horizon is
  # recomputed relative to run time through the real record helper because
  # the specimen's own `next` has since expired.
  record_cap "$state" lane1 78840 "$GO_BARE" || fail "record refused the bare-model observation"
  cap=$(fm_opencode_descent_cap "$state" lane1) || fail "a bare-model Go cap must meet the trigger"
  bound=${cap#* }; bound=${bound%% *}
  [ "$bound" = go ] || fail "a bare-model Go cap must bind go, bound: ${bound:-<empty>} (full verdict: $cap)"
  pass "a Go cap in the plugin's bare-model vocabulary binds go"
}

test_plugin_vocabulary_bare_go_lane_descends() {
  local state out
  state=$(fresh_state barelane)
  stub_env "$state" 0 1
  # The lane as a bare-`--model` request records it: the routed tier in the
  # producer's vocabulary, not the gate's prefixed one.
  write_meta "$state" lane1 opencode "$GO_BARE" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO_BARE" || fail "record refused the bare-model observation"
  out=$(run_tick "$state") || fail "tick must never fail past the cap"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "a bare-recorded Go lane past a bare-model cap must relaunch, said: ${out:-<silent>}" ;;
  esac
  stub_called || fail "a bare-recorded Go lane must reach the control plane"
  case "$(stub_calls)" in
    *'relaunch'*"$PLUS"*) : ;;
    *) fail "the move must be a relaunch onto Codex Plus, called: $(stub_calls)" ;;
  esac
  [ "$(fm_meta_model "$state" lane1)" = "$PLUS" ] \
    || fail "the durable record must name Codex Plus after the move"
  [ ! -f "$state/lane1.opencode-retry" ] \
    || fail "the dead session's sidecar must be cleared after the move"
  pass "a bare-recorded Go lane descends onto Codex Plus past a bare-model cap"
}

test_bare_go_lane_refused_when_plus_capped() {
  local state out
  state=$(fresh_state barego)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO_BARE" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  # The bare Go id binds the Go rung exactly like its prefixed form; with
  # Codex Plus capped too, the lane has nowhere to go and must surface.
  record_cap "$state" lane1 78840 "$GO_BARE" || fail "record refused a bare Go-bound cap"
  "$RETRY" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a bare-recorded Go lane past two capped rungs must surface, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "a bare-recorded Go lane has nowhere to move to past two capped rungs"
  pass "a bare-recorded Go lane surfaces instead of moving past two capped rungs"
}

# --- 4. nowhere to move, and nobody to move -----------------------------------

test_go_lane_surfaces_when_plus_capped() {
  local state out
  state=$(fresh_state golane)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  "$RETRY" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a capped Go lane past a capped Plus rung must surface, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "a capped Go lane has nowhere to move to past a capped Plus rung"
  pass "a capped Go lane surfaces instead of moving past a capped Plus rung"
}

test_secondmate_surfaces() {
  local state out
  state=$(fresh_state secondmate)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" secondmate
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a capped secondmate must surface, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "a secondmate must never be auto-relaunched"
  pass "a capped secondmate surfaces for a firstmate decision"
}

test_off_ladder_silent() {
  local state out
  state=$(fresh_state off)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode 'opencode/glm-5.3' scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 'opencode/glm-5.3' || fail "record refused"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "an off-ladder model must stay silent, said: $out"
  stub_called && fail "an off-ladder model must never reach the control plane"
  pass "an off-ladder model is never governed"
}

test_unknown_model_surfaces() {
  local state out
  state=$(fresh_state nomodel)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode '' scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a lane recording no model must surface, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "a lane recording no model must never be moved blind"
  pass "a lane recording no model surfaces instead of moving blind"
}

# --- 5. failure and override: loud once, then quiet ----------------------------

test_failed_relaunch_escalates_once() {
  local state out
  state=$(fresh_state relaunchfail)
  stub_env "$state" 1 0
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a failed move must surface, said: ${out:-<silent>}" ;;
  esac
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "a failed move must not wake twice, said: $out"
  [ "$(stub_calls | grep -c .)" = 1 ] \
    || fail "a failed move must not be retried blindly"
  # A new cap episode re-arms the attempt: the old evidence expired and fresh
  # refusal evidence arrived with a new vendor timestamp, so the next tick
  # tries again.
  "$RETRY" clear "$state" lane1
  record_cap "$state" lane1 70000 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a new cap episode must re-arm the attempt, said: ${out:-<silent>}" ;;
  esac
  [ "$(stub_calls | grep -c .)" = 2 ] \
    || fail "a new cap episode must re-arm the attempt"
  pass "a failed relaunch surfaces once and re-arms on a new episode"
}

test_override_holds() {
  local state out
  state=$(fresh_state override)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(FM_OPENCODE_LADDER_OVERRIDE='captain asked for Go' run_tick "$state") \
    || fail "tick must never fail"
  case "$out" in
    override' '*) : ;;
    *) fail "the override must hold the worker, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "an overridden worker must never be moved"
  out=$(FM_OPENCODE_LADDER_OVERRIDE='captain asked for Go' run_tick "$state") \
    || fail "tick must never fail"
  [ -z "$out" ] || fail "an override must not wake twice, said: $out"
  pass "the override holds a capped worker and says so once"
}

# --- 6. the move must be on the record, and the ambiguity owned ----------------

test_unrecorded_move_reported() {
  local state out
  state=$(fresh_state unrecorded)
  stub_env "$state" 0 0
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    unrecorded' '*) : ;;
    *) fail "a move the record missed must be reported, said: ${out:-<silent>}" ;;
  esac
  [ -f "$state/lane1.opencode-retry" ] \
    || fail "an unrecorded move must keep its evidence for the investigation"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "an unrecorded move must not wake twice, said: $out"
  pass "a move the record missed is reported, never claimed"
}

test_unbound_cap_relaunches_with_ambiguity() {
  local state out
  state=$(fresh_state unbound)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail past the cap"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "an unbound cap on Go must still move, said: ${out:-<silent>}" ;;
  esac
  case "$(stub_calls)" in
    *'unbound'*|*'no model'*) : ;;
    *) fail "the handoff note must own the ambiguity, called: $(stub_calls)" ;;
  esac
  pass "an unbound cap relaunches with the ambiguity owned"
}

# --- 7. the evaluation itself is rate-limited off the watcher poll -------------

test_evaluation_rate_limited() {
  local state out
  state=$(fresh_state ratelimit)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-busy || fail "busy writer refused fixture"
  out=$(FM_OPENCODE_DESCENT_INTERVAL=3600 fm_opencode_descent_tick "$state" 2>/dev/null) \
    || fail "tick must never fail"
  [ -z "$out" ] || fail "a healthy evaluation must stay silent, said: $out"
  # The cap lands inside the rate-limit window: the next poll must not act.
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(FM_OPENCODE_DESCENT_INTERVAL=3600 fm_opencode_descent_tick "$state" 2>/dev/null) \
    || fail "tick must never fail"
  [ -z "$out" ] || fail "a rate-limited evaluation must stay silent, said: $out"
  stub_called && fail "a rate-limited evaluation must not reach the control plane"
  # Past the window the same evidence moves.
  out=$(run_tick "$state") || fail "tick must never fail"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "evidence past the window must move, said: ${out:-<silent>}" ;;
  esac
  pass "the evaluation is rate-limited off the watcher poll"
}

# --- 1b. the idle shape: capped and idle, with no sidecar --------------------
# The 2026-09-14 blind spot: four lanes took the provider error, ended their
# turn, and went idle with no retry sidecar. Only the pane tail still shows
# the cap verbatim. The descent must move those lanes exactly as it moves an
# actively-retrying one.

test_idle_after_cap_relaunches_to_plus() {
  local state out
  state=$(fresh_state idlecap)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_idle "$state" lane1 session-idle || fail "busy writer refused fixture"
  # No record_cap: the sidecar is gone by design. The pane tail carries the
  # cap verbatim, wrapped across rendered lines as the incident showed it.
  printf '%s\n' \
    'working on the task...' \
    'Free usage exceeded, subscri' \
    'be to Go [retrying in 35s attempt #5]' \
    'opencode>' > "$FM_PANE_FIXTURES/pane-lane1.txt"
  out=$(run_tick "$state") || fail "tick must never fail past the cap"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "an idle-after-cap lane must relaunch, said: ${out:-<silent>}" ;;
  esac
  stub_called || fail "an idle-after-cap lane must reach the control plane"
  case "$(stub_calls)" in
    *'relaunch'*"$PLUS"*) : ;;
    *) fail "the move must be a relaunch onto Codex Plus, called: $(stub_calls)" ;;
  esac
  case "$(stub_calls)" in
    *'no retry horizon on record'*) : ;;
    *) fail "the handoff note must own the missing horizon, called: $(stub_calls)" ;;
  esac
  [ "$(fm_meta_model "$state" lane1)" = "$PLUS" ] \
    || fail "the durable record must name Codex Plus after the move"
  # A relaunched lane wakes in a fresh pane: the next evaluation sees clean
  # text and stays silent rather than re-firing on the old capture.
  printf '%s\n' 'fresh session, working' 'opencode>' > "$FM_PANE_FIXTURES/pane-lane1.txt"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "a relaunched lane with a clean pane must stay silent, said: $out"
  pass "an idle-after-cap lane relaunches onto Codex Plus"
}

test_idle_healthy_lane_stays() {
  local state out
  state=$(fresh_state idlehealthy)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_idle "$state" lane1 session-idle || fail "busy writer refused fixture"
  printf '%s\n' 'working on the task...' 'done' 'opencode>' > "$FM_PANE_FIXTURES/pane-lane1.txt"
  out=$(run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "an idle lane with a clean pane must stay silent, said: $out"
  stub_called && fail "an idle lane with a clean pane must never reach the control plane"
  pass "an idle lane with a clean pane stays put"
}

test_healthy_lane_is_silent
test_transient_retry_stays
test_expired_cap_stays
test_stale_sidecar_needs_latch
test_recorded_refusal_relaunches_to_plus
test_pi_ladder_lane_remains_in_descent_scope_when_opted_in
test_descent_uses_configured_codex_plus_effort
test_capped_plus_lane_descends_to_go
test_codex_human_reset_parses_incident_string
test_codex_human_reset_form_descends
test_codex_unparseable_reset_records_bounded_cap
test_plugin_vocabulary_go_cap_binds_go
test_plugin_vocabulary_bare_go_lane_descends
test_bare_go_lane_refused_when_plus_capped
test_idle_after_cap_relaunches_to_plus
test_idle_healthy_lane_stays
test_go_lane_surfaces_when_plus_capped
test_secondmate_surfaces
test_off_ladder_silent
test_unknown_model_surfaces
test_failed_relaunch_escalates_once
test_override_holds

test_filed_descent_off_holds_where_the_tick_runs() {
  local state out
  state=$(fresh_state descent-off-file)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  # The off state the watcher actually reads is a state record, because the
  # watcher is a long-lived process that predates the instruction and never
  # inherits firstmate's environment. Every evaluation below runs with the
  # variable absent, so this fails if the file is honoured only where it is
  # set.
  : > "$state/.opencode-descent-off"
  out=$(unset FM_OPENCODE_DESCENT; run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "a filed descent-off must hold the lane, said: ${out:-<silent>}"
  stub_called && fail "a filed descent-off must never reach the control plane"
  # Removing the file returns the evaluation on the same evidence, so the
  # file was what held it rather than the evidence having gone quiet.
  rm -f "$state/.opencode-descent-off"
  out=$(unset FM_OPENCODE_DESCENT; run_tick "$state") || fail "tick must never fail"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "removing the file must return the evaluation, said: ${out:-<silent>}" ;;
  esac
  pass "a filed .opencode-descent-off disables the evaluation where the tick runs"
}

test_recorded_pin_holds_without_the_env_var() {
  local state out
  state=$(fresh_state override-pin-file)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  # The pin firstmate records when it holds a lane on the captain's word is
  # read back where the tick runs, not from an environment the watcher never
  # inherits. The variable stays absent throughout, so this fails if the hold
  # only sticks where the variable is set. Written directly: the production
  # writer is fm-spawn.sh through fm_opencode_pin_task.
  printf '%s' 'captain: hold Go for this demo' > "$state/.opencode-pin-lane1"
  out=$(unset FM_OPENCODE_LADDER_OVERRIDE; run_tick "$state") || fail "tick must never fail"
  case "$out" in
    override' '*) : ;;
    *) fail "a recorded pin must hold the worker, said: ${out:-<silent>}" ;;
  esac
  case "$out" in
    *'captain: hold Go for this demo'*) : ;;
    *) fail "the recorded reason must be printed, said: $out" ;;
  esac
  stub_called && fail "a pinned worker must never be moved"
  out=$(unset FM_OPENCODE_LADDER_OVERRIDE; run_tick "$state") || fail "tick must never fail"
  [ -z "$out" ] || fail "a pin must not wake twice, said: $out"
  pass "a recorded pin holds a capped worker where the tick runs and says so once"
}

test_filed_descent_off_holds_where_the_tick_runs
test_recorded_pin_holds_without_the_env_var
test_unrecorded_move_reported
test_unbound_cap_relaunches_with_ambiguity
test_evaluation_rate_limited

# --- 8. the finding outlives the task ----------------------------------------
# A descent that moves a lane onto Codex Plus spends real money proving Go
# is exhausted, then clears the dead session's sidecar with the rest of that
# task's state. The FINDING (Go is capped until the vendor horizon) is a
# property of the rung, not of the task that discovered it, so it must stay
# readable after that task's cleanup has run - or the next spawn pays for
# the same refusal again.

test_descended_cap_survives_task_cleanup() {
  local state out cap horizon
  state=$(fresh_state survive)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  # 83823s is a measured horizon, not a round number: the 2026-09-21 evening
  # cap event held three lanes on this rung with sidecars agreeing to within
  # the minute (about 23h16m), which is the direct evidence the cap belongs
  # to the rung and the vendor window rather than to any one task.
  record_cap "$state" lane1 83823 "$GO" || fail "record refused fixture"
  out=$(run_tick "$state") || fail "tick must never fail past the cap"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "a proven Go cap must relaunch, said: ${out:-<silent>}" ;;
  esac
  # That task's cleanup: the tick already cleared the dead session's
  # sidecar; teardown removes the rest of the per-task state (record, busy
  # wiring, episode marker).
  rm -f "$state/lane1.meta" "$state/lane1.opencode-retry" \
    "$state/lane1.busy-gen" "$state/lane1.busy-state" \
    "$state/.opencode-descent-escalated-lane1" \
    "$state/lane1.progress"
  cap=$("$RETRY" check-cap "$state" go 2>/dev/null) \
    || fail "the descended cap must still read exhausted after that task's cleanup"
  case "$cap" in
    *'status=blocked'*) : ;;
    *) fail "the gate must report the Go rung blocked, said: ${cap:-<silent>}" ;;
  esac
  horizon=${cap#*horizon_s=}; horizon=${horizon%% *}
  case "$horizon" in ''|*[!0-9]*) fail "the preserved finding must carry the vendor horizon, said: $cap" ;; esac
  [ "$horizon" -gt 600 ] \
    || fail "the preserved horizon must still read quota-scale, said: $cap"
  pass "a descended cap stays readable after that task's cleanup"
}

# --- second Go workspace (tier member) ---------------------------------------
# A capped main-workspace Go lane descends within the Go tier: same model id,
# workspace marker moved, per-workspace cap records. A capped
# second-workspace lane returns to main while main is uncapped, the mirror
# move; it surfaces only when both plans are capped.

second_config() {  # <name> -> config dir with a second workspace
  local dir="$TMP_ROOT/cfg-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s' 'org_01FIXTURESECONDARY00' > "$dir/opencode-second-org"
  printf '%s\n' "$dir"
}

mark_secondary() {  # <state-dir> <id>: pin the lane's record to the workspace
  printf '%s\n' 'opencode_workspace=secondary' >> "$1/$2.meta"
}

test_go_lane_descends_to_second_workspace() {
  local state out cfg
  state=$(fresh_state gosecond)
  cfg=$(second_config gosecond)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  "$RETRY" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_tick "$state") || fail "tick must never fail"
  case "$out" in
    relaunched' '*'second workspace'*) : ;;
    *) fail "a capped main Go lane must descend to the second workspace, said: ${out:-<silent>}" ;;
  esac
  case "$(stub_calls)" in
    *'--opencode-workspace secondary'*) : ;;
    *) fail "the move must carry the workspace through the control plane, called: $(stub_calls)" ;;
  esac
  [ "$(fm_meta_get "$state/lane1.meta" opencode_workspace)" = secondary ] \
    || fail "the move must be written to the durable record"
  [ ! -e "$state/lane1.opencode-retry" ] \
    || fail "the dead session's sidecar must be cleared after the move"
  pass "a capped main Go lane descends to the second workspace"
}

test_go_lane_refused_when_second_capped() {
  local state out cfg
  state=$(fresh_state gosecondcapped)
  cfg=$(second_config gosecondcapped)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  "$RETRY" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  "$RETRY" record-cap "$state" go-second "$(ms_from_now 78840)" || fail "record refused fourth-rung fixture"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a capped Go lane with a capped fourth rung must surface, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "no move may reach the control plane past two capped Go rungs"
  pass "a capped Go lane surfaces when the second workspace is capped too"
}

test_secondary_go_lane_returns_to_main() {
  local state out cfg
  state=$(fresh_state secondgo)
  cfg=$(second_config secondgo)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  mark_secondary "$state" lane1
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_tick "$state") || fail "tick must never fail"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "a capped second-workspace Go lane must return to main while main is uncapped, said: ${out:-<silent>}" ;;
  esac
  case "$(stub_calls)" in
    *'--opencode-workspace main'*) : ;;
    *) fail "the return must carry the workspace through the control plane, called: $(stub_calls)" ;;
  esac
  [ "$(fm_meta_get "$state/lane1.meta" opencode_workspace 2>/dev/null)" != secondary ] \
    || fail "the return must clear the stale secondary marker from the durable record"
  [ ! -e "$state/lane1.opencode-retry" ] \
    || fail "the dead session's sidecar must be cleared after the move"
  [ -f "$state/.opencode-cap-go-second" ] \
    || fail "a secondary cap must preserve to the fourth rung record"
  [ ! -f "$state/.opencode-cap-go" ] \
    || fail "a secondary cap must not mark the main Go rung capped"
  pass "a capped second-workspace Go lane returns to main while main is uncapped"
}

test_secondary_go_lane_refused_when_main_capped() {
  local state out cfg
  state=$(fresh_state secondgoboth)
  cfg=$(second_config secondgoboth)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  mark_secondary "$state" lane1
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  "$RETRY" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  "$RETRY" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused main-Go fixture"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_tick "$state") || fail "tick must never fail"
  case "$out" in
    refused' '*) : ;;
    *) fail "a capped second-workspace Go lane must surface when main is capped too, said: ${out:-<silent>}" ;;
  esac
  stub_called && fail "no move may reach the control plane past two capped Go plans"
  pass "a capped second-workspace Go lane surfaces when main is capped too"
}

test_go_lane_overflows_past_plus_to_second_workspace() {
  local state out cfg
  state=$(fresh_state gooverflow)
  cfg=$(second_config gooverflow)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  "$RETRY" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  "$RETRY" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go fixture"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_tick "$state") || fail "tick must never fail"
  case "$out" in
    relaunched' '*'second workspace'*) : ;;
    *) fail "a Go lane past two capped rungs must overflow to the second workspace, said: ${out:-<silent>}" ;;
  esac
  case "$(stub_calls)" in
    *'--opencode-workspace secondary'*) : ;;
    *) fail "the overflow must carry the workspace through the control plane, called: $(stub_calls)" ;;
  esac
  [ "$(fm_meta_get "$state/lane1.meta" opencode_workspace)" = secondary ] \
    || fail "the overflow must be written to the durable record"
  pass "a Go lane overflows past two capped rungs to the second workspace"
}

test_descended_cap_survives_task_cleanup
test_go_lane_descends_to_second_workspace
test_go_lane_refused_when_second_capped
test_secondary_go_lane_returns_to_main
test_secondary_go_lane_refused_when_main_capped
test_go_lane_overflows_past_plus_to_second_workspace

# --- 9. stopped-by-design lanes and finished scouts are never relaunched -----
# The 2026-10-15 incident: the video mate stood two native-engine lanes down
# through bin/fm-control.sh exit (stop=declared), and the descent relaunched
# both anyway - spending their stopped records and restarting parked work -
# alongside a finished, idle scout. The tick never consulted the stopped
# record bin/fm-stopped-lib.sh owns, and never asked whether the lane had
# already reported done. Both skips below run through the real stopped
# library and the real busy/retry writers, so they fail while the tick stays
# blind and pass once it defers to those owners.

test_stopped_lane_is_never_relaunched() {
  local state out
  state=$(fresh_state stopped-lane)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" ship
  printf 'spawn_gen=%s\n' 'stopgen1' >> "$state/lane1.meta"
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  fm_stopped_record "$state" lane1 'captain stood down native-engine lanes' \
    || fail "the stopped library refused a bound record"
  out=$(run_tick "$state") || fail "tick must never fail past a stopped lane"
  [ -z "$out" ] || fail "a stopped-by-design lane must stay silent, said: $out"
  stub_called && fail "a stopped-by-design lane must never reach the control plane"
  [ -f "$state/lane1.opencode-retry" ] \
    || fail "a skipped lane must keep its cap evidence for the next spawn's gate"
  pass "a stopped-by-design lane is never relaunched"
}

test_spent_stopped_record_rearms_candidacy() {
  local state out
  state=$(fresh_state spent-stop)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" ship
  printf 'spawn_gen=%s\n' 'stopgen1' >> "$state/lane1.meta"
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  fm_stopped_record "$state" lane1 'captain stood down native-engine lanes' \
    || fail "the stopped library refused a bound record"
  # A relaunch mints a fresh spawn_gen, which spends the predecessor's
  # record by incarnation mismatch: the replacement is supervised normally.
  grep -v '^spawn_gen=' "$state/lane1.meta" > "$state/lane1.meta.new" \
    || fail "the meta record lost its lines"
  printf 'spawn_gen=%s\n' 'stopgen2' >> "$state/lane1.meta.new" \
    || fail "the meta record refused a fresh incarnation"
  mv "$state/lane1.meta.new" "$state/lane1.meta" \
    || fail "the meta record could not be republished"
  out=$(run_tick "$state") || fail "tick must never fail past a spent record"
  case "$out" in
    relaunched' '*) : ;;
    *) fail "a lane whose stopped record no longer binds must relaunch, said: ${out:-<silent>}" ;;
  esac
  stub_called || fail "a lane whose stopped record no longer binds must reach the control plane"
  pass "a spent stopped record re-arms the lane instead of parking it forever"
}

test_done_scout_is_never_relaunched() {
  local state out
  state=$(fresh_state done-scout)
  stub_env "$state" 0 1
  write_meta "$state" lane1 opencode "$GO" scout
  arm_busy "$state" lane1 session-retry || fail "busy writer refused fixture"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  # The finished scout still carries its meta until teardown, so without a
  # completion check it reads as an ordinary capped candidate.
  printf 'working [at=%s]: investigating the brief\n' "$(now_s)" > "$state/lane1.status"
  printf 'done [at=%s]: report complete\n' "$(now_s)" >> "$state/lane1.status"
  out=$(run_tick "$state") || fail "tick must never fail past a finished scout"
  [ -z "$out" ] || fail "a scout that already reported done must stay silent, said: $out"
  stub_called && fail "a scout that already reported done must never reach the control plane"
  pass "a scout that already reported done is never relaunched"
}

test_stopped_lane_is_never_relaunched
test_spent_stopped_record_rearms_candidacy
test_done_scout_is_never_relaunched
