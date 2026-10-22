#!/usr/bin/env bash
# Behavior tests for bin/fm-opencode-ladder-lib.sh - the Codex Plus-then-Go ladder.
#
# The dispatch rule: spend Codex Plus
# (gpt-6-luna at the configured Codex profile effort, or xhigh by default)
# first, then the paid Go tier
# (opencode-go/muse-spark-1.3-contributor, on Pi). New spawns climb back when
# the preceding tier becomes available again.
#
# The retired free rung (opencode-go/minimax-m3.1-flash-free, deprecated by
# OpenCode on 2026-10-22) is governed no more and names no rung.
#
# Codex Plus and Go use fresh exhausted readings (runway exhausted_now, then a
# known zero reading); Go also accepts reactive vendor-cap evidence.
#
# The load-bearing contracts:
#   1. A fresh home dispatches Codex Plus by default, including a spawn that
#      names no model at all.
#   2. A Plus request with no proven cap stays on Plus, silently.
#   3. A Plus request with a proven quota-scale cap on the Plus tier falls
#      through to Go, and says so. The test converts the vendor's horizon
#      into the structural `next` timestamp through the real record helper,
#      so the state machine is exercised against a recorded refusal without
#      spending quota.
#   4. A seconds-long backoff (transient retry) never triggers the fall-through.
#   5. An expired cap climbs back to Codex Plus on its own: the vendor's
#      horizon is the timer, via the record helper's own expiry.
#   6. A cap bound to the Go tier never moves a Plus request: the tiers are
#      separate quotas, so Go being capped says nothing about Plus.
#   7. An explicit Go request always stands, even past a proven cap.
#   8. A model outside the governed pair launches unchanged, but reported.
#   9. FM_OPENCODE_LADDER_OVERRIDE holds a Plus request on Codex Plus past a
#      proven cap, and SAYS SO. A silent override would be worse than no
#      ladder.
#  10. A quota-scale cap with NO model binding is unknown, never exhaustion:
#      the gate dispatches Codex Plus, silently, instead of spending the
#      captain's money on an unproven refusal.
#  11. The gate sits on the ordinary dispatch path: a real bin/fm-spawn.sh
#      launch with a proven Plus cap carries the Go model id, and a real
#      launch with no model at all carries the Codex Plus id - pinned to the
#      tmux transport with the herdr binary refused, so no real backend is
#      ever reached.
#  12. A fresh positive quota reading retires the corresponding persisted
#      cap, while a stale reading leaves that cap in force.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=bin/fm-opencode-ladder-lib.sh
. "$ROOT/bin/fm-opencode-ladder-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-opencode-ladder)
QUOTA_BIN="$TMP_ROOT/quota-bin"
mkdir -p "$QUOTA_BIN"
# Default quota fixtures are stale so they represent no current availability
# evidence; tests that need fresh evidence use QUOTA_AXI_SNAPSHOT explicitly.
cat > "$QUOTA_BIN/quota-axi" <<'SH'
#!/usr/bin/env bash
provider=
while [ "$#" -gt 0 ]; do
  case "$1" in --provider) provider=$2; shift 2 ;; *) shift ;; esac
done
if [ -z "$provider" ] && [ -n "${QUOTA_AXI_SNAPSHOT:-}" ] && [ -f "$QUOTA_AXI_SNAPSHOT" ]; then
  cat "$QUOTA_AXI_SNAPSHOT"
  exit 0
fi
case "$provider" in
  opencode-go) remaining=${FM_TEST_GO_REMAINING:-100} ;;
  codex) remaining=${FM_TEST_CODEX_REMAINING:-100} ;;
  *) exit 1 ;;
esac
printf '{"providers":[{"provider":"%s","state":{"stale":true,"status":"stale"},"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":%s}]}}]}\n' "$provider" "$remaining"
SH
chmod +x "$QUOTA_BIN/quota-axi"
HELPER="$ROOT/bin/fm-opencode-retry.sh"
SPAWN="$ROOT/bin/fm-spawn.sh"

# Hermetic home: the gate resolves the second workspace through
# FM_CONFIG_OVERRIDE then FM_HOME/config, so the invoking home's real
# opencode-second-org would otherwise offer a fourth rung to cases that
# assert three-rung exhaustion. Every case runs under this empty home; cases
# that need a second workspace set FM_CONFIG_OVERRIDE themselves, and the
# spawn cases below build their own homes per launch.
FAKEHOME="$TMP_ROOT/fakehome"
mkdir -p "$FAKEHOME/config"
FM_HOME="$FAKEHOME"
export FM_HOME
unset FM_CONFIG_OVERRIDE

GO='opencode-go/muse-spark-1.3-contributor'
PLUS='gpt-6-luna'
# The plugin's own vocabulary: OpenCode's event stream reports model.id with
# NO provider prefix, and recordRetry passes it straight through to the
# sidecar. Production sidecars (2026-09-19) carry this bare form, so the
# regression below speaks it - a fixture in the gate's prefixed vocabulary
# would be blind to a producer-side mismatch by construction.
GO_BARE='muse-spark-1.3-contributor'

now_s() { date +%s; }
ms_from_now() {  # <offset-secs> -> epoch ms
  echo $(( ($(now_s) + $1) * 1000 ))
}

fresh_state() {  # <name> -> state dir with no retry evidence
  local dir="$TMP_ROOT/state-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

# record_cap <state-dir> <id> <offset-secs> [model]: one retry observation
# through the real record helper - the same executable the spawn-installed
# plugin names, so the fixture is evidence-shaped, never hand-written.
record_cap() {  # <state-dir> <id> <offset-secs> [model]
  local state=$1 id=$2 offset=$3 model=${4:-}
  if [ -n "$model" ]; then
    "$HELPER" record "$state" "$id" 3 "$(ms_from_now "$offset")" "$model" "ses_$id"
  else
    "$HELPER" record "$state" "$id" 3 "$(ms_from_now "$offset")"
  fi
}

# run_gate <state-dir> <model>: the gate the way a caller runs it - effective
# model on stdout, notice (if any) on stderr. Prints "<model><TAB><note>".
# The notice travels on stderr rather than a global because callers capture
# the model with $(...), which runs in a subshell where a global assignment
# would be lost. A caller that needs environment (e.g. the override) prefixes
# the run_gate call itself, which propagates into the capture subshell.
run_gate() {  # <state-dir> <model>
  local state=$1 model=$2 note got
  note=$(mktemp "$TMP_ROOT/note.XXXXXX")
  got=$(PATH="$QUOTA_BIN:$PATH" fm_opencode_ladder_model "$model" "$state" 2>"$note") || return 1
  printf '%s|%s\n' "$got" "$(cat "$note")"
}

GOT=''
NOTE=''
split_gate() {  # <run_gate-output>: sets GOT and NOTE
  GOT=${1%%|*}
  NOTE=${1#*|}
}

# --- pane-capture stub -------------------------------------------------------
# fm_opencode_ladder_pane_file captures through fm_backend_capture; the stub
# serves per-target fixture text so the idle shape is exercised without a
# real pane. Fixtures live under $FM_PANE_FIXTURES/<target>.txt; a target
# with no fixture is uncapturable (unknown, never open).
FM_PANE_FIXTURES="$TMP_ROOT/panes"
mkdir -p "$FM_PANE_FIXTURES"
fm_backend_capture() {  # <backend> <target> <lines> [label]
  local fixture="$FM_PANE_FIXTURES/${2:-}.txt"
  [ -f "$fixture" ] || return 1
  cat "$fixture"
}

# write_lane_meta <state-dir> <id> <model>: the durable record the way a
# spawn leaves it, with an endpoint the capture stub can serve.
write_lane_meta() {  # <state-dir> <id> <model>
  printf 'harness=opencode\nmodel=%s\nkind=scout\nbackend=tmux\nwindow=pane-%s\n' \
    "$3" "$2" > "$1/$2.meta"
}

# --- 1. Codex Plus is the default --------------------------------------------------

test_regression_fresh_uncapped_default_uses_plus_model() {
  local state out
  state=$(fresh_state fresh)
  out=$(run_gate "$state" '') || fail "gate must never refuse a fresh home"
  split_gate "$out"
  [ "$GOT" = "$PLUS" ] || fail "fresh home with no model must dispatch Codex Plus, got '$GOT'"
  [ -z "$NOTE" ] || fail "clean Plus dispatch must stay silent, said: $NOTE"
  pass "a fresh uncapped default dispatch uses the Codex Plus model"
}

test_explicit_go_stays_go_when_healthy() {
  local state out
  state=$(fresh_state healthy)
  out=$(run_gate "$state" "$GO") || fail "gate must never refuse a healthy rung"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "healthy Go request must stay Go, got '$GOT'"
  [ -z "$NOTE" ] || fail "clean Go dispatch must stay silent, said: $NOTE"
  pass "a Go request with no proven cap stays on Go"
}

# --- 2/3. the recorded refusal falls through ----------------------------------

test_recorded_plus_cap_falls_through_to_go() {
  local state out
  state=$(fresh_state refusal)
  # A 21h 54m vendor horizon (78840s) preserved against the Plus rung,
  # converted into the structural `next` timestamp the detector owns, so the
  # state machine is proven against a recorded refusal without spending quota.
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused the refusal-shaped observation"
  out=$(run_gate "$state" '') || fail "gate must never refuse, even past the cap"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "proven Plus cap must fall through to Go, got '$GOT'"
  case "$NOTE" in
    *'Codex Plus is capped'*) : ;;
    *) fail "fall-through must name the exhausted Plus tier, said: ${NOTE:-<silent>}" ;;
  esac
  pass "the recorded Plus refusal falls through to Go with the reason stated"
}

test_go_cap_leaves_plus_dispatch_alone() {
  local state out
  state=$(fresh_state go-exhausted)
  write_lane_meta "$state" lane1 "$GO"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_gate "$state" '') || fail "gate must never refuse past a Go cap"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "a Go cap must leave dispatch on Codex Plus, got '$GOT'"
  [ -z "$NOTE" ] || fail "an uncapped Plus dispatch must stay silent, said: $NOTE"
  pass "a Go cap leaves dispatch on Codex Plus"
}

test_plus_cap_routes_to_go() {
  local state out
  state=$(fresh_state go-reactive)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Codex cap fixture"
  out=$(run_gate "$state" '') || fail "gate must route past a recorded Codex cap"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a Codex cap must route to Go, got '$GOT'"
  pass "a recorded Codex cap routes the next spawn to Go"
}

test_all_rungs_exhausted_refuses() {
  local state note got
  state=$(fresh_state all-out)
  write_lane_meta "$state" lane1 "$GO"
  record_cap "$state" lane1 78840 "$GO" || fail "record refused Go fixture"
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  note=$(mktemp "$TMP_ROOT/all-out.XXXXXX")
  if got=$(FM_TEST_GO_REMAINING=0 FM_TEST_CODEX_REMAINING=0 PATH="$QUOTA_BIN:$PATH" fm_opencode_ladder_model "" "$state" 2>"$note"); then
    fail "all exhausted tiers must refuse, got '$got'"
  fi
  case "$(cat "$note")" in *'Codex Plus capped until'*'Go capped until'*) : ;; *) fail "refusal must name all reset times: $(cat "$note")" ;; esac
  got=$(FM_OPENCODE_LADDER_OVERRIDE=hold FM_TEST_GO_REMAINING=0 FM_TEST_CODEX_REMAINING=0 PATH="$QUOTA_BIN:$PATH" fm_opencode_ladder_model "" "$state" 2>"$note") \
    || fail "the explicit ladder override should continue holding Codex Plus after all caps: $(cat "$note")"
  [ "$got" = gpt-6-luna ] || fail "the explicit override must hold Codex Plus, got '$got'"
  assert_contains "$(cat "$note")" 'OVERRIDDEN by FM_OPENCODE_LADDER_OVERRIDE=hold' "all-cap override explains its bypass"
  pass "both exhausted rungs refuse, and the explicit override still holds Codex Plus"
}

test_plugin_vocabulary_go_cap_with_plus_capped_routes_to_go() {
  local state out cfg
  state=$(fresh_state barego)
  cfg=$(second_config baresplit)
  write_lane_meta "$state" lane1 "$GO"
  # The plugin's own vocabulary, byte-shape as the 2026-09-19 production
  # sidecars: `model=muse-spark-1.3-contributor` with no provider prefix,
  # and the 19h17m horizon (69412s) from that specimen, recomputed relative to
  # run time through the real record helper. The verdict is asserted through
  # the public gate, never against implementation bytes.
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  record_cap "$state" lane1 69412 "$GO_BARE" || fail "record refused the bare-model observation"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "gate must never refuse, even past the caps"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a bare-model Go cap with Plus capped must route to Go, got '$GOT'"
  case "$NOTE" in
    *'on the second workspace'*) : ;;
    *) fail "fall-through must name the second workspace, said: ${NOTE:-<silent>}" ;;
  esac
  pass "a Go cap in the plugin's bare-model vocabulary routes to Go past a capped Plus rung"
}

test_bare_go_cap_leaves_explicit_plus_alone() {
  local state out
  state=$(fresh_state barego)
  # Tier separation the other way: a Go-tier cap says nothing about an
  # explicit Codex Plus request, which stands on its own rung.
  record_cap "$state" lane1 69412 "$GO_BARE" || fail "record refused a bare Go-bound cap"
  out=$(run_gate "$state" "$PLUS") || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = "$PLUS" ] || fail "a bare-model Go-tier cap must not move an explicit Plus request, got '$GOT'"
  pass "a Go cap in the plugin's bare-model vocabulary leaves explicit Plus alone"
}

test_transient_retry_stays_plus() {
  local state out
  state=$(fresh_state transient)
  record_cap "$state" lane1 8 "$GO" || fail "record refused a transient observation"
  out=$(run_gate "$state" '') || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "a seconds-long backoff must never trigger the fall-through, got '$GOT'"
  pass "a transient retry backoff stays on Codex Plus"
}

# --- 5. climb-back on the vendor's own horizon ---------------------------------

test_expired_cap_climbs_back_to_plus() {
  local state out
  state=$(fresh_state expired)
  # A 22-hour cap recorded 23 hours ago: the vendor's own scheduled retry time
  # plus grace has passed, so the file no longer describes the present and the
  # next spawn climbs back without any timer of this ladder's own.
  record_cap "$state" lane1 -82800 "$GO" || fail "record refused an old observation"
  out=$(run_gate "$state" '') || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "an expired cap must climb back to Codex Plus, got '$GOT'"
  pass "an expired cap climbs back to Codex Plus on the vendor's own horizon"
}

# --- 6/7. the tiers are separate -----------------------------------------------
# A Go cap says nothing about a Plus request, and an explicit Go request
# stands even past its own proven cap.

test_explicit_go_stands_despite_go_cap() {
  local state out
  state=$(fresh_state gocap)
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused a Go-bound cap"
  out=$(run_gate "$state" "$GO") || fail "gate must never refuse an explicit Go request"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "an explicit Go request must stand past its own cap, got '$GOT'"
  [ -z "$NOTE" ] || fail "an explicit Go request must stand silently, said: $NOTE"
  pass "an explicit Go request stands past its own proven cap"
}

test_explicit_go_stands_despite_plus_cap() {
  local state out
  state=$(fresh_state explicitgo)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused fixture"
  out=$(run_gate "$state" "$GO") || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "an explicit Go request must stand, got '$GOT'"
  pass "an explicit Go request stands even while Plus is capped"
}

# --- 8/9/10. off-ladder, override, unbound ---------------------------------------

test_off_ladder_model_allowed_with_notice() {
  local state out
  state=$(fresh_state off)
  record_cap "$state" lane1 78840 "$GO" || fail "record refused fixture"
  out=$(run_gate "$state" 'opencode/glm-5.3') || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = 'opencode/glm-5.3' ] || fail "an off-ladder model must launch unchanged, got '$GOT'"
  [ -n "$NOTE" ] || fail "an off-ladder model must be reported, not silently passed"
  pass "a model outside the governed pair launches unchanged but reported"
}

test_override_holds_plus_past_cap() {
  local state out
  state=$(fresh_state override)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused fixture"
  out=$(FM_OPENCODE_LADDER_OVERRIDE='captain asked for Plus' run_gate "$state" '') \
    || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "the override must hold Codex Plus past the cap, got '$GOT'"
  case "$NOTE" in
    *'OVERRID'*|*' verrid'*) : ;;
    *) fail "an overridden fall-through must say so, said: ${NOTE:-<silent>}" ;;
  esac
  pass "FM_OPENCODE_LADDER_OVERRIDE holds Codex Plus past a proven cap and says so"
}

test_unbound_cap_dispatches_plus() {
  local state out
  state=$(fresh_state unbound)
  # Quota-scale, but the plugin recorded no model binding: no tier is proven
  # exhausted, so this is unknown rather than a cap - and unknown dispatches
  # Codex Plus, silently, instead of spending the captain's money on an
  # unproven refusal.
  record_cap "$state" lane1 78840 || fail "record refused an unbound observation"
  out=$(run_gate "$state" '') || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "an unbound quota-scale cap must dispatch Codex Plus, got '$GOT'"
  [ -z "$NOTE" ] || fail "an unbound cap must stay silent, said: $NOTE"
  pass "an unbound quota-scale cap dispatches Codex Plus"
}

# --- 11. the dispatch path ------------------------------------------------------
#
# A real bin/fm-spawn.sh launch with a fake tmux pane, so the assertions pin
# the command firstmate would run without starting any real harness. The fleet
# runs on herdr now, so a tmux stub alone no longer covers the backend a real
# spawn resolves: ambient FM_BACKEND=herdr wins over TMUX in fm_backend_name,
# and HERDR_ENV=1 wins when TMUX is unset, and either route reached the real
# herdr binary and minted workspaces in the live session. The fakebin below
# therefore refuses `herdr` outright - a herdr-routed spawn fails loudly here
# instead of leaking a workspace - and the spawn env pins FM_BACKEND=tmux, the
# same pin the harness suites carry. Refusal is structural: it holds when the
# test aborts mid-run, where a teardown cleanup would not.

spawn_fakebin() {  # <dir> -> fakebin with tmux/treehouse/timeout stubs, herdr refused
  local fakebin
  fakebin=$(fm_fakebin "$1")
  cat > "$fakebin/tmux" <<'SH'
#!/usr/bin/env bash
set -u
case "$*" in
  *"#{pane_current_path}"*) printf '%s\n' "${FM_FAKE_PANE_PATH:-}"; exit 0 ;;
esac
case "${1:-}" in
  display-message) printf 'firstmate\n'; exit 0 ;;
  list-windows) exit 0 ;;
  has-session|new-session|new-window|kill-window) exit 0 ;;
  send-keys)
    if [ -n "${FM_FAKE_LAUNCH_LOG:-}" ]; then
      prev=
      for a in "$@"; do
        if [ "$prev" = "-l" ]; then
          printf '%s\n' "$a" >> "$FM_FAKE_LAUNCH_LOG"
        fi
        prev=$a
      done
    fi
    exit 0
    ;;
esac
exit 0
SH
  chmod +x "$fakebin/tmux"
  # Test-local pool-CLI stub, carried from the fork's tests/lib.sh with the
  # real-spawn case: this base's shared lib has no treehouse stub, and the
  # spawn here only needs `get` to print a leased path (upstream's spawn never
  # calls it; exit 0 suffices).
  fm_fake_treehouse() {
    local _treehouse_fakebin=$1
    cat > "$_treehouse_fakebin/treehouse" <<'SH'
#!/usr/bin/env bash
set -u
if [ "${1:-}" = get ]; then
  printf '%s\n' "${FM_FAKE_LEASE_PATH:-${FM_FAKE_PANE_PATH:-}}"
fi
exit 0
SH
    chmod +x "$_treehouse_fakebin/treehouse"
  }
  fm_fake_treehouse "$fakebin"
  cat > "$fakebin/timeout" <<'SH'
#!/usr/bin/env bash
shift
exec "$@"
SH
  chmod +x "$fakebin/timeout"
  # The fleet's live backend is herdr, so the real `herdr` binary must never be
  # reached no matter which backend a spawn resolves. This stub refuses loudly
  # and records the attempt: a herdr-routed spawn fails the test instead of
  # minting a workspace in the live session, including on an aborted run where
  # no teardown ever executes.
  cat > "$fakebin/herdr" <<'SH'
#!/usr/bin/env bash
set -u
if [ -n "${FM_FAKE_HERDR_CALL_LOG:-}" ]; then
  printf '%s\n' "$*" >> "$FM_FAKE_HERDR_CALL_LOG"
fi
echo "fm-opencode-ladder fixture: real herdr must never be reached" >&2
exit 1
SH
  chmod +x "$fakebin/herdr"
  # Spawn fixtures default to stale quota evidence; cases that exercise
  # predictive cap updates supply fresh snapshots explicitly.
  cat > "$fakebin/quota-axi" <<'SH'
#!/usr/bin/env bash
provider=
if [ "${1:-}" = --json ]; then
  codex=${FM_TEST_CODEX_REMAINING:-100}
  opencode=${FM_TEST_OPENCODE_REMAINING:-100}
  go=${FM_TEST_GO_REMAINING:-100}
  cat <<JSON
{"schemaVersion":5,"providers":[
 {"provider":"codex","state":{"status":"stale","stale":true},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":$codex,"runway":{"status":"through_reset"},"selection":{"spendPriority":0.1}}]}},
 {"provider":"opencode","state":{"status":"stale","stale":true},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":$opencode,"runway":{"status":"through_reset"},"selection":{"spendPriority":0.1}}]}},
 {"provider":"opencode-go","state":{"status":"stale","stale":true},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":$go,"runway":{"status":"through_reset"},"selection":{"spendPriority":0.1}}]}}
]}
JSON
  exit 0
fi
while [ "$#" -gt 0 ]; do
  case "$1" in --provider) provider=$2; shift 2 ;; *) shift ;; esac
done
case "$provider" in
  opencode-go) remaining=${FM_TEST_GO_REMAINING:-100} ;;
  codex) remaining=${FM_TEST_CODEX_REMAINING:-100} ;;
  *) remaining=100 ;;
esac
printf '{"providers":[{"provider":"%s","state":{"stale":false},"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":%s}]}}]}\n' "$provider" "$remaining"
SH
  chmod +x "$fakebin/quota-axi"
  cat > "$fakebin/codex" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = debug ] && [ "${2:-}" = models ]; then
  printf '%s\n' '{"models":[{"slug":"gpt-6-luna","supported_reasoning_levels":[{"effort":"max"}]}]}'
fi
exit 0
SH
  chmod +x "$fakebin/codex"
  cat > "$fakebin/agy" <<'SH'
#!/usr/bin/env bash
if [ -n "${FM_FAKE_AGY_CALL_LOG:-}" ]; then
  printf '%s\n' "$*" >> "$FM_FAKE_AGY_CALL_LOG"
fi
if [ "${1:-}" = models ]; then
  printf 'gemini-3.1-pro-high\tGemini 3.1 Pro (High)\n'
fi
exit 0
SH
  chmod +x "$fakebin/agy"
  printf '%s\n' "$fakebin"
}

# spawn_opencode <dir> <id> [model] [harness] [dispatch-ladder] [configured-plus-effort]
# Run a REAL bin/fm-spawn.sh launch and print "<launch-log> <herdr-call-log> <home>".
# Model empty means no --model flag at all.
spawn_opencode() {  # <dir> <id> [model] [harness] [dispatch-ladder] [configured-plus-effort] [fallback-mode] [override-reason]
  local dir=$1 id=$2 model=${3:-} harness=${4:-opencode} ladder=${5:-} plus_effort=${6:-} fallback_mode=${7:-} override_reason=${8:-} home proj wt fakebin
  local -a ladder_args override_args
  ladder_args=()
  [ -z "$ladder" ] || ladder_args=(--dispatch-ladder "$ladder")
  override_args=()
  [ -z "$override_reason" ] || override_args=(--dispatch-override-reason "$override_reason")
  home="$dir/home"
  proj="$dir/proj"
  wt="$dir/wt"
  mkdir -p "$home/data/$id" "$home/state" "$home/config" "$home/projects"
  if [ -n "$plus_effort" ] || [ -n "$fallback_mode" ]; then
    [ -n "$plus_effort" ] || plus_effort=xhigh
    if [ "$fallback_mode" = declared ]; then
      printf '{"rules":[],"default":[{"harness":"codex","model":"gpt-6-luna","effort":"%s","provider":"codex"},{"harness":"opencode","model":"%s","provider":"opencode-go"}],"agy_ladder":["Gemini 3.1 Pro (High)"],"exhausted_ladder_fallback":{"include_agy_ladder":true,"use":{"harness":"opencode","model":"opencode-go/longcat-2.5-preview-free","provider":"opencode-go"}}}\n' \
        "$plus_effort" "$GO" > "$home/config/crew-dispatch.json"
    elif [ "$fallback_mode" = none ]; then
      printf '{"rules":[],"default":[{"harness":"codex","model":"gpt-6-luna","effort":"%s","provider":"codex"},{"harness":"opencode","model":"%s","provider":"opencode-go"}]}\n' \
        "$plus_effort" "$GO" > "$home/config/crew-dispatch.json"
    else
      printf '{"default":[{"harness":"codex","model":"gpt-6-luna","effort":"%s","provider":"codex"},{"harness":"opencode","model":"%s","provider":"opencode-go"}]}\n' \
        "$plus_effort" "$GO" > "$home/config/crew-dispatch.json"
    fi
  fi
  fakebin=$(spawn_fakebin "$dir/fake")
  fm_git_worktree "$proj" "$wt" "wt-$id"
  # This base kept upstream's brief scaffold (batch 2 was dropped), which
  # refuses a brief whose ## Captain's intent / ## Firstmate spec subsections
  # do not sit under a top-level # Task heading - so the fixture carries that
  # shape, where the fork's looser scaffold accepted one line.
  printf '# Task\n\n## Captain'"'"'s intent\n\nroute this test lane\n\n## Firstmate spec\n\nopencode ladder dispatch\n' > "$home/data/$id/brief.md"
  touch "$home/state/.last-watcher-beat"
  if [ -n "$model" ]; then
    env PATH="$fakebin:$PATH" FM_ROOT_OVERRIDE='' FM_HOME="$home" \
      FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
      FM_PROJECTS_OVERRIDE="$home/projects" FM_CONFIG_OVERRIDE="$home/config" \
      FM_SPAWN_NO_GUARD=1 FM_FAKE_PANE_PATH="$wt" FM_FAKE_LAUNCH_LOG="$dir/launch.log" TMUX="fake,1,0" \
      FM_BACKEND=tmux FM_FAKE_HERDR_CALL_LOG="$dir/herdr-calls.log" FM_FAKE_AGY_CALL_LOG="$dir/agy-calls.log" \
      "$SPAWN" "$id" "$proj" \
      --harness "$harness" --model "$model" "${ladder_args[@]}" "${override_args[@]}" --mode no-mistakes --yolo off >"$dir/spawn.log" 2>&1
  else
    env PATH="$fakebin:$PATH" FM_ROOT_OVERRIDE='' FM_HOME="$home" \
      FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
      FM_PROJECTS_OVERRIDE="$home/projects" FM_CONFIG_OVERRIDE="$home/config" \
      FM_SPAWN_NO_GUARD=1 FM_FAKE_PANE_PATH="$wt" FM_FAKE_LAUNCH_LOG="$dir/launch.log" TMUX="fake,1,0" \
      FM_BACKEND=tmux FM_FAKE_HERDR_CALL_LOG="$dir/herdr-calls.log" FM_FAKE_AGY_CALL_LOG="$dir/agy-calls.log" \
      "$SPAWN" "$id" "$proj" \
      --harness "$harness" "${ladder_args[@]}" "${override_args[@]}" --mode no-mistakes --yolo off >"$dir/spawn.log" 2>&1
  fi
  printf '%s %s %s\n' "$dir/launch.log" "$dir/herdr-calls.log" "$home"
}

# read_launch: resolve the spawn helper's tmux source-script command to its
# actual launch text, keeping assertions on the command the worker receives.
read_launch() {  # <launch-log>
  local logged source
  logged=$(cat "$1")
  source=$(printf '%s\n' "$logged" | sed -n "s/^\\. '\(.*\)'$/\1/p")
  if [ -n "$source" ] && [ -f "$source" ]; then
    cat "$source"
  else
    printf '%s\n' "$logged"
  fi
}

test_spawn_falls_through_on_proven_cap() {
  local dir="$TMP_ROOT/spawn-go" log herdr_calls home launch log_home
  mkdir -p "$dir/home/state"
  "$HELPER" record-cap "$dir/home/state" go "$(ms_from_now 78840)" \
    || fail "record refused fixture"
  log_home=$(spawn_opencode "$dir" task-go)
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "the spawn must never reach the herdr binary"
  [ -f "$log" ] || fail "spawn wrote no launch command"
  launch=$(read_launch "$log")
  assert_contains "$launch" "codex --model" "a proven Go cap routes the real launch to Codex Plus"
  assert_contains "$launch" "gpt-6-luna" "a proven Go cap keeps the Codex Plus model"
  assert_contains "$launch" "model_reasoning_effort=\"xhigh\"" "Codex Plus defaults to xhigh effort"
  assert_not_contains "$launch" 'model_reasoning_effort="max"' "Codex Plus must not use max without that config declaration"
  assert_not_contains "$launch" "$GO" "a proven Go cap leaves no Go model flag on the launch"
  assert_contains "$(cat "$home/state/task-go.meta")" "model=gpt-6-luna" "the durable record names the Plus model"
  pass "a proven Go cap routes a real spawn to Codex Plus"
}

test_spawn_uses_configured_codex_plus_effort() {
  local dir log launch log_home
  dir="$TMP_ROOT/spawn-plus-configured-effort"
  mkdir -p "$dir/home/state"
  "$HELPER" record-cap "$dir/home/state" go "$(ms_from_now 78840)" || fail "record refused Go cap"
  log_home=$(spawn_opencode "$dir" task-plus-configured "" codex '' high)
  log=${log_home%% *}
  [ -f "$log" ] || fail "configured-effort spawn wrote no launch command"
  launch=$(read_launch "$log")
  assert_contains "$launch" "codex --model" "a proven Go cap routes to Codex Plus"
  assert_contains "$launch" "gpt-6-luna" "a proven Go cap keeps the Codex Plus model"
  assert_contains "$launch" 'model_reasoning_effort="high"' "Codex Plus uses the configured Codex profile effort"
  assert_not_contains "$launch" 'model_reasoning_effort="max"' "configured high effort must not be replaced with max"
  pass "a configured Codex Plus effort reaches the real spawn launch"
}

test_codex_dispatch_branch_uses_configured_max_effort() {
  local dir log launch log_home
  dir="$TMP_ROOT/spawn-codex-plus-configured-max"
  mkdir -p "$dir/home/state"
  "$HELPER" record-cap "$dir/home/state" go "$(ms_from_now 78840)" || fail "record refused Go cap"
  log_home=$(spawn_opencode "$dir" task-codex-plus "" codex '' max)
  log=${log_home%% *}
  [ -f "$log" ] || fail "configured-max spawn wrote no launch command"
  launch=$(read_launch "$log")
  assert_contains "$launch" "codex --model" "the Codex dispatch branch routes the capped Go rung to Plus"
  assert_contains "$launch" "gpt-6-luna" "the Codex dispatch branch keeps the Plus model"
  assert_contains "$launch" 'model_reasoning_effort="max"' "Codex Plus accepts max when the default profile declares it"
  pass "the Codex dispatch branch preserves a configured max effort"
}

test_spawn_routes_to_go_when_plus_is_capped() {
  local dir="$TMP_ROOT/spawn-plus" log herdr_calls home launch log_home
  mkdir -p "$dir/home/state"
  "$HELPER" record-cap "$dir/home/state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap fixture"
  log_home=$(spawn_opencode "$dir" task-plus)
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "the spawn must never reach the herdr binary"
  launch=$(read_launch "$log")
  assert_contains "$launch" "opencode --model" "capped Codex Plus must launch on Go"
  assert_contains "$launch" "$GO" "capped Codex Plus must keep the Go model"
  assert_not_contains "$launch" "codex --model" "the Go rung stays on OpenCode"
  assert_contains "$(cat "$home/state/task-plus.meta")" "harness=opencode" "the task record must identify the Go harness"
  assert_contains "$(cat "$home/state/task-plus.meta")" "model=$GO" "the task record must identify the Go model"
  pass "a capped Codex Plus rung launches and records Go"
}

test_spawn_defaults_to_plus() {
  local dir="$TMP_ROOT/spawn-plus-default" log herdr_calls home launch log_home
  mkdir -p "$dir"
  log_home=$(spawn_opencode "$dir" task-plus-default)
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "the spawn must never reach the herdr binary"
  [ -f "$log" ] || fail "spawn wrote no launch command"
  launch=$(read_launch "$log")
  assert_contains "$launch" "gpt-6-luna" "a modelless opencode spawn carries the Codex Plus id"
  assert_contains "$(cat "$home/state/task-plus-default.meta")" "model=gpt-6-luna" "the durable record names the Codex Plus model"
  pass "a modelless opencode spawn dispatches Codex Plus end to end"
}

test_spawn_uses_declared_fallback_when_default_ladder_is_exhausted() {
  local dir="$TMP_ROOT/spawn-declared-fallback" log_home log herdr_calls home launch state
  mkdir -p "$dir/home/state"
  state="$dir/home/state"
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go fixture"
  log_home=$(spawn_opencode "$dir" task-fallback '' codex '' xhigh declared)
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "fallback launch must never reach Herdr"
  assert_absent "$dir/agy-calls.log" "the agy ladder must remain a last resort while LongCat is eligible"
  [ -f "$log" ] || fail "declared fallback wrote no launch command: $(cat "$dir/spawn.log")"
  launch=$(read_launch "$log")
  assert_contains "$launch" 'opencode-go/longcat-2.5-preview-free' "the declared use profile must take priority over an eligible agy last resort"
  assert_not_contains "$launch" "minimax-m3.1-flash-free" "the retired free rung must not be launched"
  assert_contains "$(cat "$home/state/task-fallback.meta")" 'dispatch_fallback=opencode/opencode-go/longcat-2.5-preview-free' \
    "task metadata must record the selected fallback"
  pass "an exhausted default OpenCode ladder launches and records the declared fallback"
}

test_spawn_refuses_exhausted_default_ladder_without_fallback() {
  local dir="$TMP_ROOT/spawn-no-fallback" log_home log herdr_calls home state
  mkdir -p "$dir/home/state"
  state="$dir/home/state"
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus fixture"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go fixture"
  log_home=$(spawn_opencode "$dir" task-no-fallback '' codex '' xhigh none)
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "refusal must never reach Herdr"
  assert_absent "$log" "an exhausted ladder without a declared fallback must not launch"
  assert_contains "$(cat "$dir/spawn.log")" 'no exhausted_ladder_fallback is configured' \
    "refusal must explain that no fallback was declared"
  assert_absent "$home/state/task-no-fallback.meta" "refusal must not publish task metadata"
  pass "an exhausted default OpenCode ladder refuses when no fallback is declared"
}

test_explicit_dispatch_override_keeps_its_profile() {
  local dir log_home log herdr_calls home launch
  dir="$TMP_ROOT/spawn-explicit-dispatch-override"
  log_home=$(spawn_opencode "$dir" task-explicit-override 'opencode-go/longcat-2.5-preview-free' opencode '' xhigh none \
    'captain instruction: use LongCat as the exhausted-ladder alternative')
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "the override launch must never reach Herdr"
  [ -f "$log" ] || fail "explicit override wrote no launch command: $(cat "$dir/spawn.log")"
  launch=$(read_launch "$log")
  assert_contains "$launch" "opencode --model" \
    "the authorized explicit model must not be replaced by the configured default ladder"
  assert_contains "$launch" 'opencode-go/longcat-2.5-preview-free' \
    "the authorized explicit model must keep its configured model"
  assert_contains "$(cat "$home/state/task-explicit-override.meta")" \
    'dispatch_override_reason=captain instruction: use LongCat as the exhausted-ladder alternative' \
    "metadata must preserve the override instruction"
  pass "an authorized explicit dispatch override keeps its profile instead of re-entering the default ladder"
}

test_default_resolver_codex_profile_still_enters_ladder_gate() {
  local dir log herdr_calls home launch log_home
  dir="$TMP_ROOT/spawn-resolver-codex"
  log_home=$(spawn_opencode "$dir" resolver-codex gpt-6-luna codex opencode)
  log=${log_home%% *}; log_home=${log_home#* }
  herdr_calls=${log_home%% *}; home=${log_home#* }
  assert_absent "$herdr_calls" "the spawn must never reach the herdr binary"
  launch=$(read_launch "$log")
  assert_contains "$launch" "codex --model" "the resolver's Codex profile must pass through the shared gate"
  assert_contains "$launch" "gpt-6-luna" "the shared gate must keep the default array on Codex Plus"
  assert_contains "$(cat "$home/state/resolver-codex.meta")" "harness=codex" "the routed task record must follow the Plus rung"
  pass "a default-array Codex profile is gated through the OpenCode ladder"
}

test_idle_healthy_lane_stays_plus() {
  local state out
  state=$(fresh_state idlehealthy)
  write_lane_meta "$state" lane1 "$GO"
  # Idle turn, clean pane, no sidecar: no evidence of a cap anywhere. A gate
  # that read this as capped would spend the captain's money for nothing.
  printf '%s\n' \
    'working on the task...' \
    'done' \
    'opencode>' > "$FM_PANE_FIXTURES/pane-lane1.txt"
  out=$(run_gate "$state" '') || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "an idle lane with a clean pane must stay Codex Plus, got '$GOT'"
  [ -z "$NOTE" ] || fail "a clean idle dispatch must stay silent, said: $NOTE"
  pass "an idle lane with a clean pane stays on Codex Plus"
}

# --- 12. the preserved rung cap routes dispatch past task cleanup --------------
# A descent proves Go is exhausted, moves its lane onto Codex Plus, and clears the
# dead session's sidecar with the task's own state. The rung-scoped record it
# preserved must still fall a later Go request through - asserted here
# through the public gate with no per-task evidence left at all.

test_preserved_plus_cap_falls_through_to_go() {
  local state out
  state=$(fresh_state rungcap)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" \
    || fail "record-cap refused a well-formed rung observation"
  out=$(run_gate "$state" '') || fail "gate must never refuse, even past the cap"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a preserved rung cap must fall through to Go, got '$GOT'"
  case "$NOTE" in
    *'Codex Plus is capped'*) : ;;
    *) fail "fall-through must name the exhausted Plus tier, said: ${NOTE:-<silent>}" ;;
  esac
  pass "a preserved rung cap falls through to Go with no per-task evidence left"
}

test_expired_rung_cap_climbs_back_to_plus() {
  local state out
  state=$(fresh_state rungexpired)
  "$HELPER" record-cap "$state" plus "$(ms_from_now -3600)" \
    || fail "record-cap refused an old observation"
  out=$(run_gate "$state" '') || fail "gate must never refuse"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "an expired rung cap must climb back to Codex Plus, got '$GOT'"
  pass "an expired rung cap climbs back to Codex Plus on the vendor's own horizon"
}

test_plus_cap_skips_to_go_and_returns_after_reset() {
  local state out fixture
  state=$(fresh_state predictive-codex)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Codex cap"
  out=$(run_gate "$state" '') || fail "an uncapped Go rung should accept the spawn"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a Codex cap must route to Go, got '$GOT'"
  state=$(fresh_state predictive-codex-reset)
  "$HELPER" record-cap "$state" plus "$(ms_from_now -1)" || fail "record refused expired Codex cap"
  out=$(run_gate "$state" '') || fail "an expired Codex cap must be ignored"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "an expired Codex cap must leave dispatch on Codex Plus, got '$GOT'"

  fixture="$TMP_ROOT/codex-zero.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"codex","state":{"stale":false},"windows":[{"id":"weekly","resetsAt":"2030-01-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"limitingWindowIds":["weekly"]}]}},{"provider":"opencode-go","state":{"status":"fresh"},"windows":[{"id":"daily","resetsAt":"2030-01-02T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"limitingWindowIds":["daily"]}]}}]}
JSON
  state=$(fresh_state predictive-snapshot)
  out=$(QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '' 2>/dev/null) && fail "a fully capped ladder must refuse, got '$out'"
  [ -f "$state/.opencode-cap-plus" ] || fail "fresh Codex zero quota with resetAt must create the shared plus cap record"
  [ -f "$state/.opencode-cap-go" ] || fail "fresh Go zero quota with its window reset must create the shared go cap record"
  pass "rung records prove both caps and the exhausted ladder refuses"
}

test_fresh_quota_snapshot_retires_caps_but_stale_snapshot_does_not() {
  local state fresh stale out
  state=$(fresh_state early-reset)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go cap"

  fresh="$TMP_ROOT/early-reset-fresh.json"
  cat > "$fresh" <<'JSON'
{"providers":[{"provider":"codex","state":{"status":"fresh"},"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":100}]}},{"provider":"opencode-go","state":{"status":"fresh"},"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":100}]}}]}
JSON
  out=$(QUOTA_AXI_SNAPSHOT="$fresh" run_gate "$state" '') || fail "a fresh quota snapshot must leave dispatch eligible"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "fresh 100% quota must retire its future caps and dispatch on Codex Plus, got '$GOT'"
  [ ! -e "$state/.opencode-cap-plus" ] || fail "fresh Codex availability must retire the Plus cap"
  [ ! -e "$state/.opencode-cap-go" ] || fail "fresh main Go availability must retire the Go cap"

  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused replacement Plus cap"
  stale="$TMP_ROOT/early-reset-stale.json"
  cat > "$stale" <<'JSON'
{"providers":[{"provider":"codex","state":{"status":"stale","stale":true},"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":100}]}},{"provider":"opencode-go","state":{"status":"fresh"},"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":100}]}}]}
JSON
  out=$(QUOTA_AXI_SNAPSHOT="$stale" run_gate "$state" '') || fail "a stale reading must leave dispatch eligible on Go"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a stale Codex reading must leave the recorded Plus cap in force and route Go, got '$GOT'"
  [ -f "$state/.opencode-cap-plus" ] || fail "stale Codex availability must leave the Plus cap intact"
  pass "fresh quota availability retires rung caps while stale evidence preserves them"
}

# --- second Go workspace (fourth rung) -----------------------------------------
# The fourth rung exists only with config/opencode-second-org: a new spawn
# reaches it only when the first Go rung holds a proven, unexpired cap, each
# workspace keeps its own cap record, and exhaustion fires only once all four
# are capped. Model ids are identical on both Go rungs
# (opencode-go/muse-spark-1.3-contributor); the lane's task-meta marker is
# what binds reactive evidence to a workspace.

second_config() {  # <name> -> config dir with a second workspace
  local dir="$TMP_ROOT/cfg-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s' 'org_01FIXTURESECONDARY00' > "$dir/opencode-second-org"
  printf '%s\n' "$dir"
}

empty_config() {  # <name> -> config dir with no second workspace
  local dir="$TMP_ROOT/cfg-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

cap_two_rungs() {  # <state-dir>: proven caps on Codex Plus and Go
  local state=$1
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go cap"
}

test_go_capped_routes_to_second_workspace() {
  local state out cfg
  state=$(fresh_state second-route)
  cfg=$(second_config route)
  cap_two_rungs "$state"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "a live fourth rung must accept the spawn, said: $out"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a capped Go rung must route to Go on the second workspace, got '$GOT'"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "the fourth-rung route must say so: ${NOTE:-<silent>}" ;; esac
  pass "a capped Go rung routes the next spawn to the second workspace"
}

test_workspace_quota_rows_split_caps() {
  local state out cfg fixture
  state=$(fresh_state workspace-quota-split)
  cfg=$(second_config workspacequotasplit)
  cap_plus_only "$state"
  fixture="$TMP_ROOT/workspace-quota-split.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"opencode-go","accountKey":"main","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2030-01-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"resetsAt":"2030-01-01T00:00:00Z"}]}},{"provider":"opencode-go","accountKey":"second","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2030-01-02T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":50,"resetsAt":"2030-01-02T00:00:00Z"}]}}]}
JSON
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" PATH="$QUOTA_BIN:$PATH" run_gate "$state" '') \
    || fail "the second workspace must accept dispatch past a main-workspace quota cap"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a live second Go workspace must receive the dispatch, got '$GOT'"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "the route must identify the second workspace: ${NOTE:-<silent>}" ;; esac
  [ -f "$state/.opencode-cap-go" ] || fail "zero main-workspace quota must keep the main Go cap"
  [ ! -e "$state/.opencode-cap-go-second" ] || fail "positive second-workspace quota must not cap its Go rung"

  pass "workspace-keyed quota caps main at zero and leaves secondary available"
}

test_fractional_monthly_reset_timestamp_is_parsed() {
  local cfg fixture reset
  cfg=$(second_config fractionalreset)
  fixture="$TMP_ROOT/fractional-monthly-reset.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"opencode-go","accountKey":"main","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2026-11-08T02:02:34.000Z"}]},{"provider":"opencode-go","accountKey":"second","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2026-11-06T15:22:44.000Z"}]}]}
JSON
  reset=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" PATH="$QUOTA_BIN:$PATH" \
    fm_opencode_ladder_go_monthly main "$cfg") || fail "fractional main monthly reset must parse"
  [ "$reset" -eq "$(jq -nr '"2026-11-08T02:02:34Z" | strptime("%Y-%m-%dT%H:%M:%SZ") | mktime')" ] \
    || fail "fractional seconds must parse to the correct epoch, got '$reset'"
  pass "fractional-second monthly reset timestamps parse"
}

test_secondary_quota_axi_monthly_row_is_read() {
  local cfg fixture reset
  cfg=$(priority_config secondrow '2026-11-08T02:02:34Z' '2026-12-01T00:00:00Z')
  fixture="$TMP_ROOT/secondary-monthly-reset.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"opencode-go","accountKey":"main","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2026-11-08T02:02:34.000Z"}]},{"provider":"opencode-go","accountKey":"second","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2026-11-06T15:22:44.000Z"}]}]}
JSON
  reset=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" PATH="$QUOTA_BIN:$PATH" \
    fm_opencode_ladder_go_monthly secondary "$cfg") || fail "the secondary quota-axi monthly row must be read"
  [ "$reset" -eq "$(jq -nr '"2026-11-06T15:22:44Z" | strptime("%Y-%m-%dT%H:%M:%SZ") | mktime')" ] \
    || fail "the live secondary row must beat its later configured anchor, got '$reset'"
  pass "the secondary workspace monthly reset comes from its fresh quota-axi row"
}

test_go_workspace_reset_priority_overrides_spend_priority() {
  local state cfg fixture pick out note
  state=$(fresh_state screenshotreset)
  cfg=$(second_config screenshotreset)
  cap_plus_only "$state"
  fixture="$TMP_ROOT/go-reset-spend-priority.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"opencode-go","accountKey":"main","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2026-11-08T02:02:34.000Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"selection":{"spendPriority":-0.28}}]}},{"provider":"opencode-go","accountKey":"second","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2026-11-06T15:22:44.000Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":40,"selection":{"spendPriority":-0.90}}]}}]}
JSON
  pick=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" PATH="$QUOTA_BIN:$PATH" \
    fm_opencode_ladder_pick_go_workspace "$state" 2>/dev/null) \
    || fail "the workspace pick must accept fresh quota rows with fractional resets"
  [ "$pick" = secondary ] \
    || fail "the second plan resetting Nov 6 must beat main resetting Nov 8 despite spendPriority favoring main, got '$pick'"
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" PATH="$QUOTA_BIN:$PATH" \
    fm_opencode_ladder_model "" "$state" 2>"$TMP_ROOT/screenshot-reset-notice") \
    || fail "dispatch must route to the remaining Go plan"
  [ "$out" = "$GO" ] || fail "capped Plus must dispatch on Go, got '$out'"
  note=$(cat "$TMP_ROOT/screenshot-reset-notice")
  case "$note" in *'on the second workspace'*) : ;; *)
    fail "dispatch notice must name the second workspace: $note" ;; esac
  pass "the Nov 6 Go reset selects secondary despite the opposite spendPriority rank"
}

test_unlabelled_go_quota_row_keeps_main_meaning() {
  local state out cfg fixture
  state=$(fresh_state unlabelled-go-quota)
  cfg=$(second_config unlabelledgoquota)
  cap_plus_only "$state"
  fixture="$TMP_ROOT/unlabelled-go-quota.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"opencode-go","state":{"status":"fresh"},"windows":[{"id":"monthly","resetsAt":"2030-01-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"resetsAt":"2030-01-01T00:00:00Z"}]}}]}
JSON
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "a legacy main-workspace quota cap must leave the second workspace available"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "the uncapped second workspace must receive dispatch, got '$GOT'"
  [ -f "$state/.opencode-cap-go" ] || fail "an unlabelled row must record the legacy main Go cap"
  [ ! -e "$state/.opencode-cap-go-second" ] || fail "an unlabelled row must not affect the second Go rung"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "a main cap must route through the second workspace: ${NOTE:-<silent>}" ;; esac
  pass "an unlabelled OpenCode Go quota row retains its legacy main-workspace meaning"
}

test_fourth_rung_exhaustion_refuses() {
  local state note got rc cfg
  state=$(fresh_state second-out)
  cfg=$(second_config out)
  cap_two_rungs "$state"
  "$HELPER" record-cap "$state" go-second "$(ms_from_now 78840)" || fail "record refused fourth-rung cap"
  note=$(mktemp "$TMP_ROOT/second-out.XXXXXX")
  got=$(FM_CONFIG_OVERRIDE="$cfg" PATH="$QUOTA_BIN:$PATH" fm_opencode_ladder_model "" "$state" 2>"$note")
  rc=$?
  [ "$rc" = 3 ] || fail "four-rung exhaustion must return 3, returned $rc (got '$got')"
  case "$(cat "$note")" in *'Go (second workspace) capped until'*) : ;; *) fail "refusal must name the fourth reset time: $(cat "$note")" ;; esac
  pass "all four capped rungs refuse with every reset time named"
}

test_second_rung_cap_never_marks_main_go() {
  local state out cfg
  state=$(fresh_state second-alone)
  cfg=$(second_config alone)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  "$HELPER" record-cap "$state" go-second "$(ms_from_now 78840)" || fail "record refused fourth-rung cap"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "an uncapped main Go rung must accept the spawn"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "an uncapped main Go rung must route to Go, got '$GOT'"
  case "$NOTE" in *'second workspace'*) fail "a main-Go route must not mention the second workspace: $NOTE" ;; esac
  pass "a fourth-rung cap never marks the main Go rung capped"
}

test_secondary_lane_evidence_feeds_fourth_rung() {
  local state out cfg
  state=$(fresh_state second-reactive)
  cfg=$(second_config reactive)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  write_lane_meta "$state" seclane "$GO"
  printf '%s\n' 'opencode_workspace=secondary' >> "$state/seclane.meta"
  record_cap "$state" seclane 78840 "$GO_BARE" || fail "record refused the secondary lane observation"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "gate must run the reactive split"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "Plus-capped dispatch must route Go, got '$GOT'"
  [ -f "$state/.opencode-cap-go-second" ] || fail "secondary-lane evidence must preserve a fourth-rung cap"
  [ ! -f "$state/.opencode-cap-go" ] || fail "secondary-lane evidence must not mark the main Go rung capped"
  case "$NOTE" in
    *'Codex Plus is capped'*) : ;;
    *) fail "a Plus-capped route must name the Plus cap: ${NOTE:-<silent>}" ;;
  esac
  pass "reactive evidence on a secondary lane feeds the fourth rung only"
}

test_main_lane_evidence_feeds_main_go() {
  local state out cfg
  state=$(fresh_state main-reactive)
  cfg=$(second_config mainreactive)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  write_lane_meta "$state" mainlane "$GO"
  record_cap "$state" mainlane 78840 "$GO_BARE" || fail "record refused the main lane observation"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "gate must run the reactive split"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "the gate must route Go, got '$GOT'"
  [ -f "$state/.opencode-cap-go" ] || fail "main-lane evidence must preserve a main Go cap"
  [ ! -f "$state/.opencode-cap-go-second" ] || fail "main-lane evidence must not mark the fourth rung capped"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "a capped main Go rung must route to the second workspace: ${NOTE:-<silent>}" ;; esac
  pass "reactive evidence on a main lane feeds the main Go rung only"
}

test_orphan_retry_attributes_to_no_go_rung() {
  local state out cfg
  state=$(fresh_state orphan-go)
  cfg=$(second_config orphango)
  cap_plus_only "$state"
  # The 2026-10-10 shape: the lane was cleaned up, so it has no meta and no
  # workspace marker, but its retry record survives with a Go-tier model and
  # a quota-scale horizon. That orphan evidence must attribute to no Go rung.
  record_cap "$state" ghostlane 78840 "$GO_BARE" || fail "record refused the orphan observation"
  [ -f "$state/ghostlane.opencode-retry" ] || fail "record wrote no sidecar"
  fm_opencode_ladder_go_reactive_capped "$state" 2>/dev/null \
    && fail "an orphan retry record must not read as a main Go cap"
  fm_opencode_ladder_go_second_reactive_capped "$state" 2>/dev/null \
    && fail "an orphan retry record must not read as a fourth-rung cap"
  fm_opencode_ladder_go_main_capped "$state" 2>/dev/null \
    && fail "an orphan retry record must not cap the Go tier"
  [ ! -f "$state/.opencode-cap-go" ] || fail "an orphan retry record must not preserve a main Go cap"
  [ ! -f "$state/.opencode-cap-go-second" ] || fail "an orphan retry record must not preserve a fourth-rung cap"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "an uncapped Go rung must accept the spawn"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "with Go uncapped the gate must route Go, got '$GOT'"
  case "$NOTE" in *'second workspace'*) fail "an orphan must not divert the route to the second workspace: $NOTE" ;; esac
  [ ! -f "$state/.opencode-cap-go" ] || fail "the gate must not preserve a main Go cap from orphan evidence"
  pass "an orphan retry record attributes to no Go rung"
}

test_markerless_live_lane_records_main_go_cap() {
  local state
  state=$(fresh_state markerless-go)
  # The boundary the orphan fix must not cross: a live lane with no marker is
  # the main workspace by convention, so its Go evidence still feeds this rung.
  write_lane_meta "$state" plainlane "$GO"
  record_cap "$state" plainlane 78840 "$GO_BARE" || fail "record refused the markerless lane observation"
  fm_opencode_ladder_go_reactive_capped "$state" 2>/dev/null \
    || fail "a live markerless lane must still read as the main workspace"
  [ -f "$state/.opencode-cap-go" ] || fail "a live markerless lane must preserve a main Go cap"
  [ ! -f "$state/.opencode-cap-go-second" ] || fail "a live markerless lane must not mark the fourth rung capped"
  pass "a live markerless lane records its Go cap against the main rung"
}

test_explicit_go_stands_despite_live_second_rung() {
  local state out cfg
  state=$(fresh_state explicit-go-second)
  cfg=$(second_config explicitgo)
  cap_two_rungs "$state"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" "$GO") || fail "an explicit Go request must stand, said: $out"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "an explicit Go request must stand past proven caps, got '$GOT'"
  [ -z "$NOTE" ] || fail "an explicit Go request must stand silently, said: $NOTE"
  pass "an explicit Go request stands past a proven cap even with a live fourth rung"
}

test_plus_request_falls_to_second_workspace() {
  local state out cfg
  state=$(fresh_state plus-second)
  cfg=$(second_config plussecond)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go cap"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" gpt-6-luna) || fail "a live fourth rung must accept the Plus request"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "capped Plus and Go must fall to the second workspace, got '$GOT'"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "the fourth-rung route must say so: ${NOTE:-<silent>}" ;; esac
  pass "a capped Plus request falls through Go to the second workspace"
}

test_unconfigured_exhaustion_ignores_second_record() {
  local state note got rc cfg
  state=$(fresh_state second-compat)
  cfg=$(empty_config compat)
  cap_two_rungs "$state"
  "$HELPER" record-cap "$state" go-second "$(ms_from_now 78840)" || fail "record refused fourth-rung cap"
  note=$(mktemp "$TMP_ROOT/second-compat.XXXXXX")
  got=$(FM_CONFIG_OVERRIDE="$cfg" PATH="$QUOTA_BIN:$PATH" fm_opencode_ladder_model "" "$state" 2>"$note")
  rc=$?
  [ "$rc" = 3 ] || fail "three-rung exhaustion must return 3, returned $rc (got '$got')"
  case "$(cat "$note")" in *'second workspace'*) fail "unconfigured refusal must not mention the fourth rung: $(cat "$note")" ;; esac
  case "$(cat "$note")" in *'Codex Plus capped until'*'Go capped until'*) : ;; *) fail "refusal must name both reset times: $(cat "$note")" ;; esac
  pass "without a second workspace the ladder refuses exactly as before"
}

# --- Go tier by billing timing ------------------------------------------------
# The two Go workspaces are one tier: the plan whose overall (monthly) quota
# resets soonest serves first, and a plan under a shorter cap (5-hour, then
# weekly) yields to the other until that limit resets, then traffic returns
# on its own. Per-plan monthly resets come from config/opencode-go-resets;
# the second plan's shorter windows are reactive-only, so its caps carry a
# horizon but no window identity - the switch-back horizon IS that limit's
# reset whatever window it was.

priority_config() {  # <name> <main-iso> <second-iso> -> config dir with billing resets
  local dir="$TMP_ROOT/cfg-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s' 'org_01FIXTURESECONDARY00' > "$dir/opencode-second-org"
  printf '%s %s\n%s %s\n' 'main' "$2" 'secondary' "$3" > "$dir/opencode-go-resets"
  printf '%s\n' "$dir"
}

cap_plus_only() {  # <state-dir>: a proven cap on Codex Plus, below the Go tier
  local state=$1
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
}

test_monthly_projection_rolls_forward() {
  local anchor now got want
  anchor='2026-10-25T14:00:00Z'
  want=$(jq -nr '"2026-10-25T14:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  now=$(jq -nr '"2026-10-10T00:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  got=$(fm_opencode_go_next_monthly "$anchor" "$now") || fail "a future anchor must project to itself"
  [ "$got" = "$want" ] || fail "a future anchor is its own next reset, got '$got' want '$want'"
  want=$(jq -nr '"2026-11-25T14:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  now=$(jq -nr '"2026-10-26T00:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  got=$(fm_opencode_go_next_monthly "$anchor" "$now") || fail "a past anchor must roll forward"
  [ "$got" = "$want" ] || fail "a past anchor must roll to the same day next month, got '$got' want '$want'"
  want=$(jq -nr '"2026-02-28T10:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  now=$(jq -nr '"2026-02-01T00:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  got=$(fm_opencode_go_next_monthly '2026-01-31T10:00:00Z' "$now") || fail "a month-end anchor must project"
  [ "$got" = "$want" ] || fail "a 31st anchor must clamp to Feb 28 in a common year, got '$got' want '$want'"
  want=$(jq -nr '"2024-02-29T10:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  now=$(jq -nr '"2024-02-01T00:00:00Z"|fromdateiso8601') || fail "jq refused fixture"
  got=$(fm_opencode_go_next_monthly '2024-01-31T10:00:00Z' "$now") || fail "a month-end anchor must project"
  [ "$got" = "$want" ] || fail "a 31st anchor must clamp to Feb 29 in a leap year, got '$got' want '$want'"
  fm_opencode_go_next_monthly 'not-a-date' "$now" 2>/dev/null \
    && fail "an unparsable anchor must read as unknown"
  pass "monthly anchors project forward by calendar month with month-end clamping"
}

test_priority_plan_serves_first() {
  local state out cfg
  state=$(fresh_state priorityfirst)
  cfg=$(priority_config priorityfirst '2026-11-08T02:02:34Z' '2026-10-25T14:00:00Z')
  cap_plus_only "$state"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused main Go cap"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "a live priority plan must accept the spawn, said: $out"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "the monthly-soonest plan must serve first, got '$GOT'"
  case "$NOTE" in *'on the second workspace'*) : ;; *) fail "the priority route must name the second workspace: ${NOTE:-<silent>}" ;; esac
  pass "the plan whose overall quota resets soonest serves first"
}

test_priority_falls_back_on_shorter_cap() {
  local state out cfg
  state=$(fresh_state prioritycap)
  cfg=$(priority_config prioritycap '2026-11-08T02:02:34Z' '2026-10-25T14:00:00Z')
  cap_plus_only "$state"
  "$HELPER" record-cap "$state" go-second "$(ms_from_now 18000)" || fail "record refused priority-plan cap"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "the other plan must accept the spawn"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a capped priority plan must yield to the other plan, got '$GOT'"
  case "$NOTE" in *'second workspace'*) fail "the fallback route must not name the second workspace: $NOTE" ;; esac
  pass "a priority plan under a shorter cap yields to the other plan"
}

test_priority_returns_after_cap_reset() {
  local state out cfg
  state=$(fresh_state priorityback)
  cfg=$(priority_config priorityback '2026-11-08T02:02:34Z' '2026-10-25T14:00:00Z')
  cap_plus_only "$state"
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused main Go cap"
  "$HELPER" record-cap "$state" go-second "$(ms_from_now -100)" || fail "record refused expired fixture"
  out=$(FM_CONFIG_OVERRIDE="$cfg" run_gate "$state" '') || fail "the reset priority plan must accept the spawn"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "traffic must return to the priority plan after its limit resets, got '$GOT'"
  case "$NOTE" in *'on the second workspace'*) : ;; *) fail "the return route must name the second workspace: ${NOTE:-<silent>}" ;; esac
  pass "traffic returns to the priority plan once its shorter limit resets"
}

rolling_zero_fixture() {  # <path>: quota-axi snapshot with the 5-hour window at zero
  cat > "$1" <<'JSON'
{"providers":[{"provider":"opencode-go","state":{"status":"fresh"},"windows":[{"id":"rolling","resetsAt":"2030-01-01T05:00:00Z"},{"id":"monthly","resetsAt":"2026-11-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"limitingWindowIds":["rolling"]}]}}]}
JSON
}

weekly_zero_fixture() {  # <path>: quota-axi snapshot with the weekly window at zero
  cat > "$1" <<'JSON'
{"providers":[{"provider":"opencode-go","state":{"status":"fresh"},"windows":[{"id":"weekly","resetsAt":"2030-01-08T00:00:00Z"},{"id":"monthly","resetsAt":"2026-11-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"limitingWindowIds":["weekly"]}]}}]}
JSON
}

healthy_fixture() {  # <path>: quota-axi snapshot with full quota and a main-sooner monthly window
  cat > "$1" <<'JSON'
{"providers":[{"provider":"opencode-go","state":{"status":"fresh"},"windows":[{"id":"rolling","resetsAt":"2030-01-01T00:05:00Z"},{"id":"weekly","resetsAt":"2030-01-08T00:00:00Z"},{"id":"monthly","resetsAt":"2026-11-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":100,"limitingWindowIds":["rolling"]}]}}]}
JSON
}

test_5h_cap_falls_back_and_returns() {
  local state out cfg fixture healthy
  state=$(fresh_state fiveh)
  cfg=$(priority_config fiveh '2026-10-08T02:02:34Z' '2026-12-01T00:00:00Z')
  fixture="$TMP_ROOT/fiveh-zero.json"
  rolling_zero_fixture "$fixture"
  cap_plus_only "$state"
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "the other plan must accept the spawn past a 5-hour cap"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a 5-hour-capped priority plan must yield, got '$GOT'"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "the 5-hour fallback must name the second workspace: ${NOTE:-<silent>}" ;; esac
  state=$(fresh_state fivehback)
  cap_plus_only "$state"
  "$HELPER" record-cap "$state" go "$(ms_from_now -100)" || fail "record refused expired fixture"
  healthy="$TMP_ROOT/fiveh-healthy.json"
  healthy_fixture "$healthy"
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$healthy" run_gate "$state" '') \
    || fail "the reset priority plan must serve again"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "traffic must return after the 5-hour window resets, got '$GOT'"
  case "$NOTE" in *'second workspace'*) fail "the return route must stay on the priority plan: $NOTE" ;; esac
  pass "a 5-hour cap yields to the other plan and returns after it resets"
}

test_weekly_cap_falls_back_and_returns() {
  local state out cfg fixture healthy
  state=$(fresh_state weekly)
  cfg=$(priority_config weekly '2026-10-08T02:02:34Z' '2026-12-01T00:00:00Z')
  fixture="$TMP_ROOT/weekly-zero.json"
  weekly_zero_fixture "$fixture"
  cap_plus_only "$state"
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "the other plan must accept the spawn past a weekly cap"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "a weekly-capped priority plan must yield, got '$GOT'"
  case "$NOTE" in *'second workspace'*) : ;; *) fail "the weekly fallback must name the second workspace: ${NOTE:-<silent>}" ;; esac
  state=$(fresh_state weeklyback)
  cap_plus_only "$state"
  "$HELPER" record-cap "$state" go "$(ms_from_now -100)" || fail "record refused expired fixture"
  healthy="$TMP_ROOT/weekly-healthy.json"
  healthy_fixture "$healthy"
  out=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$healthy" run_gate "$state" '') \
    || fail "the reset priority plan must serve again"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "traffic must return after the weekly window resets, got '$GOT'"
  case "$NOTE" in *'second workspace'*) fail "the return route must stay on the priority plan: $NOTE" ;; esac
  pass "a weekly cap yields to the other plan and returns after it resets"
}

test_longcat_unreachable_while_second_plan_live() {
  local state note got rc cfg
  state=$(fresh_state longcatlive)
  cfg=$(second_config longcatlive)
  cap_two_rungs "$state"
  note=$(mktemp "$TMP_ROOT/longcatlive.XXXXXX")
  got=$(FM_CONFIG_OVERRIDE="$cfg" PATH="$QUOTA_BIN:$PATH" fm_opencode_ladder_model "" "$state" 2>"$note")
  rc=$?
  [ "$rc" = 0 ] || fail "a live second plan must accept the spawn, returned $rc (got '$got')"
  [ "$got" = "$GO" ] || fail "a live second plan must route Go, got '$got'"
  case "$(cat "$note")" in *'second workspace'*) : ;; *) fail "the route must name the second workspace: $(cat "$note")" ;; esac
  pass "the declared fallback stays unreachable while either Go plan can serve"
}

test_unstarted_window_never_prioritizes_idle_plan() {
  local state cfg fixture pick
  state=$(fresh_state idlewindow)
  cfg=$(second_config idlewindow)
  fixture="$TMP_ROOT/idle-healthy.json"
  cat > "$fixture" <<'JSON'
{"providers":[{"provider":"opencode-go","state":{"status":"fresh"},"windows":[{"id":"rolling","resetsAt":"2030-01-01T00:05:00Z"},{"id":"weekly","resetsAt":"2030-01-08T00:00:00Z"},{"id":"monthly","resetsAt":"2030-02-01T00:00:00Z"}],"quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":100,"limitingWindowIds":["rolling"]}]}}]}
JSON
  pick=$(FM_CONFIG_OVERRIDE="$cfg" QUOTA_AXI_SNAPSHOT="$fixture" PATH="$QUOTA_BIN:$PATH" \
    fm_opencode_ladder_pick_go_workspace "$state" 2>/dev/null) || fail "the pick must answer on healthy quota"
  [ "$pick" = main ] || fail "an idle plan's near window reset must not win over monthly priority, got '$pick'"
  pass "an unstarted window on an idle plan never counts as resetting soonest"
}

test_bills_go_names_only_the_go_tier() {
  fm_opencode_model_bills_go "$GO" \
    || fail "the Go model id must bill the Go workspace"
  fm_opencode_model_bills_go "$GO_BARE" \
    || fail "the bare Go model id must bill the Go workspace"
  fm_opencode_model_bills_go "$FM_OPENCODE_LADDER_PLUS_MODEL" \
    && fail "the Codex Plus model must bill no Go workspace"
  fm_opencode_model_bills_go "anthropic/claude-opus-4-5" \
    && fail "an ungoverned passthrough model must bill no Go workspace"
  fm_opencode_model_bills_go "" \
    && fail "an empty model must bill no Go workspace"
  fm_opencode_model_bills_go "default" \
    && fail "the default model must bill no Go workspace"
  fm_opencode_model_requires_console_login "$GO" \
    && fail "the retired free-model login gate must stay silent for the Go model"
  pass "only the Go tier bills a Go workspace, in either id form"
}

# --- exhausted rungs the quota snapshot proves --------------------------------
# 2026-10-13: three spawns printed 'Go is capped; dispatching on Codex
# Plus' while quota-axi showed Codex at 0% with runway exhausted_now on the
# five-hour window, and the workers landed on 'You've hit your usage limit'.
# 2026-10-14: two workers launched onto a capped rung while its cap was still
# active.
# Each case below reproduces one ignored exhaustion with a recorded quota-axi
# JSON fixture - never real spend - and pins the skip to the next rung.

test_codex_exhausted_now_without_reset_skips_plus() {
  local state out fixture
  state=$(fresh_state codex-no-reset)
  fixture="$TMP_ROOT/codex-exhausted-no-reset.json"
  cat > "$fixture" <<'JSON'
{"schemaVersion":5,"providers":[
{"provider":"codex","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"runway":{"status":"exhausted_now"}}]}},
{"provider":"opencode-go","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"}}]}}
]}
JSON
  out=$(QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "an exhausted Plus rung must still dispatch, said: $out"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "Codex exhausted_now with no parseable reset must skip Plus for Go, got '$GOT'"
  case "$NOTE" in *'conservative'*'instead of leaving the rung open'*) : ;; *)
    fail "the bound horizon must be owned as conservative: ${NOTE:-<silent>}" ;; esac
  "$HELPER" check-cap "$state" plus >/dev/null \
    || fail "exhausted_now without a reset must still record the Plus cap"
  pass "Codex exhausted_now without a reset skips Plus for Go"
}

test_codex_home_exhaustion_not_masked_by_work_account() {
  local state out fixture
  state=$(fresh_state codex-home-masked)
  "$HELPER" record-cap "$state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  fixture="$TMP_ROOT/codex-home-masked.json"
  cat > "$fixture" <<'JSON'
{"schemaVersion":6,"providers":[
{"provider":"codex","accountKey":"codex-home","state":{"status":"fresh","stale":false},"windows":[{"id":"five_hour","resetsAt":"2030-01-01T00:00:00Z"}],"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"runway":{"status":"exhausted_now"},"limitingWindowIds":["five_hour"]}]}},
{"provider":"codex","accountKey":"openai-codex-work","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":50,"runway":{"status":"through_reset"}}]}},
{"provider":"opencode-go","accountKey":"main","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"}}]}}
]}
JSON
  out=$(QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "an exhausted home row must still dispatch, said: $out"
  split_gate "$out"
  [ "$GOT" = "$GO" ] || fail "an exhausted codex-home row must skip Plus for Go despite a healthy work account, got '$GOT'"
  "$HELPER" check-cap "$state" plus >/dev/null \
    || fail "a healthy work account must never retire the serving row's Plus cap"
  pass "an exhausted codex-home row skips Plus despite a healthy work account"
}

test_quota_go_row_exhaustion_skips_go() {
  local state out fixture
  state=$(fresh_state quota-go-row)
  fixture="$TMP_ROOT/quota-go-row.json"
  cat > "$fixture" <<'JSON'
{"schemaVersion":5,"providers":[
{"provider":"codex","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"}}]}},
{"provider":"opencode-go","state":{"status":"fresh","stale":false},"windows":[{"id":"monthly","resetsAt":"2030-01-01T00:00:00Z"}],"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"runway":{"status":"exhausted_now"},"limitingWindowIds":["monthly"]}]}}
]}
JSON
  out=$(QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "an exhausted Go row must still dispatch, said: $out"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "a quota-exhausted Go row must skip Go for Codex Plus, got '$GOT'"
  "$HELPER" check-cap "$state" go >/dev/null \
    || fail "an exhausted opencode-go row must record the Go rung cap"
  pass "a quota-exhausted Go row skips Go for Codex Plus"
}

test_quota_go_row_positive_retires_go_cap() {
  local state out fixture
  state=$(fresh_state quota-go-retire)
  "$HELPER" record-cap "$state" go "$(ms_from_now 78840)" || fail "record refused Go cap"
  fixture="$TMP_ROOT/quota-go-retire.json"
  cat > "$fixture" <<'JSON'
{"schemaVersion":5,"providers":[
{"provider":"opencode","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"}}]}},
{"provider":"codex","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"}}]}},
{"provider":"opencode-go","state":{"status":"fresh","stale":false},"quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"}}]}}
]}
JSON
  out=$(QUOTA_AXI_SNAPSHOT="$fixture" run_gate "$state" '') \
    || fail "a retired Go cap must dispatch, said: $out"
  split_gate "$out"
  [ "$GOT" = gpt-6-luna ] || fail "a positive opencode-go row must retire the Go cap and dispatch Codex Plus, got '$GOT'"
  [ ! -e "$state/.opencode-cap-go" ] || fail "a positive opencode-go row must retire the Go rung cap"
  pass "a positive opencode-go row retires the Go cap and dispatches Codex Plus"
}

# --- relaunch without an explicit model takes the shared gate ------------------
# 2026-10-13: a relaunch with --harness opencode and no model came up on Codex
# while Codex Plus was exhausted. A relaunch naming no model is a modelless
# dispatch, so it enters the rung gate from Go exactly like a fresh launch
# instead of staying on the recorded harness past its cap.

relaunch_dead_fakebin() {  # <dir> -> fakebin with a dead tmux endpoint and shell ps
  local fakebin
  fakebin=$(spawn_fakebin "$1")
  cat > "$fakebin/tmux" <<'SH'
#!/usr/bin/env bash
set -u
if [ "${1:-}" = list-windows ]; then
  case "$*" in
    *"${FM_FAKE_DEAD_SESSION:?}"*) printf '%s\n' "${FM_FAKE_DEAD_WINDOW:?}"; exit 0 ;;
  esac
  exit 0
fi
if [ "${1:-}" = display-message ]; then
  case "$*" in
    *'pane_tty'*) printf '/dev/ttys999\n'; exit 0 ;;
    *'pane_current_command'*) printf 'bash\n'; exit 0 ;;
    *'pane_pid'*) printf '111\n'; exit 0 ;;
    *"#{pane_current_path}"*) printf '%s\n' "${FM_FAKE_PANE_PATH:-}"; exit 0 ;;
  esac
  printf 'firstmate\n'; exit 0
fi
case "${1:-}" in
  display-message) printf 'firstmate\n'; exit 0 ;;
  list-windows) exit 0 ;;
  has-session|new-session|new-window|kill-window) exit 0 ;;
  send-keys)
    if [ -n "${FM_FAKE_LAUNCH_LOG:-}" ]; then
      prev=
      for a in "$@"; do
        if [ "$prev" = "-l" ]; then
          printf '%s\n' "$a" >> "$FM_FAKE_LAUNCH_LOG"
        fi
        prev=$a
      done
    fi
    exit 0
    ;;
esac
exit 0
SH
  chmod +x "$fakebin/tmux"
  cat > "$fakebin/ps" <<'SH'
#!/usr/bin/env bash
set -u
case "$*" in
  *ttys999*"-o pid="*) printf '111 111 111 bash\n'; exit 0 ;;
esac
case "$*" in
  *"-p 111"*"-o args="*) printf -- '-bash\n'; exit 0 ;;
esac
exec /bin/ps "$@"
SH
  chmod +x "$fakebin/ps"
  printf '%s\n' "$fakebin"
}

test_relaunch_without_model_follows_ladder_gate() {
  local dir="$TMP_ROOT/relaunch-gate" home proj wt fakebin launch log_home
  local herdr_calls
  mkdir -p "$dir/home/data/task-relaunch" "$dir/home/state" "$dir/home/config" "$dir/home/projects"
  home="$dir/home"
  proj="$dir/proj"
  wt="$dir/wt"
  fakebin=$(relaunch_dead_fakebin "$dir/fake")
  fm_git_worktree "$proj" "$wt" "wt-relaunch"
  printf '# Task\n\n## Captain'"'"'s intent\n\nroute this test lane\n\n## Firstmate spec\n\nopencode ladder dispatch\n' > "$home/data/task-relaunch/brief.md"
  touch "$home/state/.last-watcher-beat"
  "$HELPER" record-cap "$home/state" plus "$(ms_from_now 78840)" || fail "record refused Plus cap"
  cat > "$home/state/task-relaunch.meta" <<META
harness=codex
kind=ship
project=$proj
worktree=$wt
branch=fm/task-relaunch
mode=no-mistakes
yolo=off
window=fakesess:fm-task-relaunch
META
  if ! env PATH="$fakebin:$PATH" FM_ROOT_OVERRIDE='' FM_HOME="$home" \
    FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
    FM_PROJECTS_OVERRIDE="$home/projects" FM_CONFIG_OVERRIDE="$home/config" \
    FM_SPAWN_NO_GUARD=1 FM_FAKE_PANE_PATH="$wt" FM_FAKE_LAUNCH_LOG="$dir/launch.log" TMUX="fake,1,0" \
    FM_BACKEND=tmux FM_FAKE_HERDR_CALL_LOG="$dir/herdr-calls.log" FM_FAKE_AGY_CALL_LOG="$dir/agy-calls.log" \
    FM_FAKE_DEAD_SESSION=fakesess FM_FAKE_DEAD_WINDOW=fm-task-relaunch \
    "$SPAWN" task-relaunch --relaunch >"$dir/spawn.log" 2>&1; then
    fail "modelless relaunch must not refuse past capped rungs: $(cat "$dir/spawn.log")"
  fi
  log_home="$dir/launch.log"
  [ -f "$log_home" ] || fail "modelless relaunch wrote no launch command: $(cat "$dir/spawn.log")"
  launch=$(read_launch "$log_home")
  assert_contains "$launch" "opencode --model" "a modelless relaunch past capped Plus must launch on Go"
  assert_contains "$launch" "$GO" "a modelless relaunch past capped Plus must keep the Go model"
  assert_not_contains "$launch" "codex --model" "a modelless relaunch must not stay on capped Codex Plus"
  assert_contains "$(cat "$home/state/task-relaunch.meta")" "harness=opencode" "the relaunched record must follow the Go rung"
  assert_contains "$(cat "$home/state/task-relaunch.meta")" "model=$GO" "the relaunched record must name the Go model"
  pass "a relaunch without an explicit model follows the ladder gate"
}

test_regression_fresh_uncapped_default_uses_plus_model
test_explicit_go_stays_go_when_healthy
test_recorded_plus_cap_falls_through_to_go
test_go_cap_leaves_plus_dispatch_alone
test_plus_cap_routes_to_go
test_all_rungs_exhausted_refuses
test_plugin_vocabulary_go_cap_with_plus_capped_routes_to_go
test_bare_go_cap_leaves_explicit_plus_alone
test_transient_retry_stays_plus
test_expired_cap_climbs_back_to_plus
test_explicit_go_stands_despite_go_cap
test_explicit_go_stands_despite_plus_cap
test_off_ladder_model_allowed_with_notice
test_override_holds_plus_past_cap
test_unbound_cap_dispatches_plus
test_idle_healthy_lane_stays_plus
test_spawn_falls_through_on_proven_cap
test_spawn_uses_configured_codex_plus_effort
test_codex_dispatch_branch_uses_configured_max_effort
test_spawn_uses_declared_fallback_when_default_ladder_is_exhausted
test_spawn_refuses_exhausted_default_ladder_without_fallback
test_explicit_dispatch_override_keeps_its_profile
test_spawn_routes_to_go_when_plus_is_capped
test_spawn_defaults_to_plus
test_default_resolver_codex_profile_still_enters_ladder_gate
test_preserved_plus_cap_falls_through_to_go
test_expired_rung_cap_climbs_back_to_plus
test_plus_cap_skips_to_go_and_returns_after_reset
test_fresh_quota_snapshot_retires_caps_but_stale_snapshot_does_not
test_go_capped_routes_to_second_workspace
test_workspace_quota_rows_split_caps
test_fractional_monthly_reset_timestamp_is_parsed
test_secondary_quota_axi_monthly_row_is_read
test_go_workspace_reset_priority_overrides_spend_priority
test_unlabelled_go_quota_row_keeps_main_meaning
test_fourth_rung_exhaustion_refuses
test_second_rung_cap_never_marks_main_go
test_secondary_lane_evidence_feeds_fourth_rung
test_main_lane_evidence_feeds_main_go
test_orphan_retry_attributes_to_no_go_rung
test_markerless_live_lane_records_main_go_cap
test_explicit_go_stands_despite_live_second_rung
test_plus_request_falls_to_second_workspace
test_unconfigured_exhaustion_ignores_second_record
test_monthly_projection_rolls_forward
test_priority_plan_serves_first
test_priority_falls_back_on_shorter_cap
test_priority_returns_after_cap_reset
test_5h_cap_falls_back_and_returns
test_weekly_cap_falls_back_and_returns
test_longcat_unreachable_while_second_plan_live
test_unstarted_window_never_prioritizes_idle_plan
test_bills_go_names_only_the_go_tier
test_codex_exhausted_now_without_reset_skips_plus
test_codex_home_exhaustion_not_masked_by_work_account
test_quota_go_row_exhaustion_skips_go
test_quota_go_row_positive_retires_go_cap
test_relaunch_without_model_follows_ladder_gate
