#!/usr/bin/env bash
# Behavior tests for bin/fm-opencode-server.sh: one shared server per profile,
# reused while healthy, restarted once dead, and kept alive while any task
# still holds a lease.
set -u

# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"

TEST_ROOT=$(fm_test_tmproot fm-opencode-server)
TEST_ROOT=$(cd "$TEST_ROOT" && pwd -P)
STATE="$TEST_ROOT/state"
FAKE="$TEST_ROOT/fake"
SERVER="$ROOT/bin/fm-opencode-server.sh"
mkdir -p "$STATE" "$TEST_ROOT/wt"
chmod 700 "$STATE"
FAKEBIN=$(fm_fakebin "$TEST_ROOT")
fm_test_fake_opencode_server "$FAKEBIN" "$FAKE"
export PATH="$FAKEBIN:$PATH"

server_pid() { sed -n 's/^pid=//p' "$STATE/opencode-server-$1"; }

stop_test_servers() {
  local record pid
  for record in "$STATE"/opencode-server-main "$STATE"/opencode-server-secondary "$STATE"/opencode-server-default; do
    [ -f "$record" ] || continue
    pid=$(sed -n 's/^pid=//p' "$record")
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    kill "$pid" 2>/dev/null || true
  done
}
trap 'stop_test_servers; fm_test_cleanup' EXIT

wait_dead() {
  local i
  for i in $(seq 1 40); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.25; done
  return 1
}

test_healthy_server_is_reused_and_dead_one_restarted() {
  local first second pid1 pid2
  first=$("$SERVER" ensure "$STATE" default task-a) || fail "the default server did not start"
  pid1=$(server_pid default)
  second=$("$SERVER" ensure "$STATE" default task-b) || fail "a healthy server was not reused"
  [ "$first" = "$second" ] || fail "a reused server changed its address: $first vs $second"
  [ "$(server_pid default)" = "$pid1" ] || fail "reusing a healthy server started another"
  kill "$pid1"
  wait_dead "$pid1" || fail "the fake server did not stop"
  "$SERVER" ensure "$STATE" default task-c >/dev/null || fail "a dead server was not restarted"
  pid2=$(server_pid default)
  [ "$pid2" != "$pid1" ] && kill -0 "$pid2" 2>/dev/null || fail "the restarted server is not a live new process"
  pass "a healthy server is reused and a dead one is restarted on the next launch"
}

test_release_keeps_server_until_last_lease_leaves() {
  local pid
  pid=$(server_pid default)
  "$SERVER" release "$STATE" default task-a ses_one "$TEST_ROOT/wt" || fail "releasing task-a failed"
  grep -q "POST /session/ses_one/abort $TEST_ROOT/wt" "$FAKE/api.log" || fail "release did not abort the task session"
  grep -q "DELETE /session/ses_one $TEST_ROOT/wt" "$FAKE/api.log" || fail "release did not delete the task session"
  "$SERVER" release "$STATE" default task-b || fail "releasing task-b failed"
  kill -0 "$pid" 2>/dev/null || fail "the server stopped while task-c still held a lease"
  "$SERVER" release "$STATE" default task-c || fail "releasing the last lease failed"
  wait_dead "$pid" || fail "the server outlived its last lease"
  [ ! -e "$STATE/opencode-server-default" ] || fail "a stopped server left its record behind"
  pass "release ends the task session and stops the server only after the last lease"
}

test_profiles_get_separate_servers_on_their_own_databases() {
  local main_url default_url port
  touch "$TEST_ROOT/main.db"
  main_url=$("$SERVER" ensure "$STATE" default task-d | cut -f1) || fail "the default server did not start"
  # A pinned profile proves its database through the workspace library before
  # it starts; a missing workspace id must refuse rather than start unpinned.
  if "$SERVER" ensure "$STATE" main task-e 2>/dev/null; then
    fail "a pinned profile started without its workspace id"
  fi
  [ ! -e "$STATE/opencode-server-main" ] || fail "a refused pinned profile still recorded a server"
  default_url=$main_url
  port=${default_url##*:}
  [ -z "$(cat "$FAKE/servers/$port.db")" ] || fail "the default server must run on the machine's own database"
  "$SERVER" release "$STATE" default task-d || fail "releasing task-d failed"
  pass "the default profile runs unpinned and a pinned profile refuses without its workspace"
}

test_start_failure_reports_unavailable() {
  touch "$FAKE/serve-fails"
  if FM_OPENCODE_SERVER_START_POLLS=4 "$SERVER" ensure "$STATE" default task-f 2>/dev/null; then
    fail "a server that cannot start was reported available"
  fi
  rm -f "$FAKE/serve-fails"
  [ ! -e "$STATE/opencode-server-default" ] || fail "a failed start left a server record"
  pass "a server that cannot start is reported unavailable so the caller launches standalone"
}

test_prompt_carries_model_and_variant() {
  local url session
  url=$("$SERVER" ensure "$STATE" default task-g | cut -f1) || fail "the default server did not start"
  [ -n "$url" ] || fail "ensure printed no address"
  session=$("$SERVER" session "$STATE" default "$TEST_ROOT/wt" "Firstmate task task-g") || fail "session creation failed"
  "$SERVER" prompt "$STATE" default "$session" "$TEST_ROOT/wt" anthropic/claude-sonnet-4-5 high 'Read the brief.' \
    || fail "prompt submission failed"
  [ "$(jq -r '.model.providerID + "|" + .model.modelID + "|" + .variant + "|" + .parts[0].text' "$FAKE/prompt.json")" \
    = 'anthropic|claude-sonnet-4-5|high|Read the brief.' ] || fail "prompt lost its model, variant, or text: $(cat "$FAKE/prompt.json")"
  "$SERVER" prompt "$STATE" default "$session" "$TEST_ROOT/wt" '' '' 'Next.' || fail "default-model prompt failed"
  [ "$(jq -c 'has("model"), has("variant")' "$FAKE/prompt.json" | tr '\n' ' ')" = 'false false ' ] \
    || fail "a default-model prompt must leave the model to the server: $(cat "$FAKE/prompt.json")"
  "$SERVER" release "$STATE" default task-g "$session" "$TEST_ROOT/wt" || fail "releasing task-g failed"
  pass "the first prompt carries the task's model and effort variant, and omits them when unset"
}

test_healthy_server_is_reused_and_dead_one_restarted
test_release_keeps_server_until_last_lease_leaves
test_profiles_get_separate_servers_on_their_own_databases
test_start_failure_reports_unavailable
test_prompt_carries_model_and_variant
