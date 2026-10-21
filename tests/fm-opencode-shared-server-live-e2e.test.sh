#!/usr/bin/env bash
# Live guard for the shared OpenCode server experiment (bin/fm-opencode-server.sh).
# Opt in with FM_OPENCODE_SHARED_SERVER_LIVE=1; it submits prompts to
# FM_OPENCODE_SHARED_SERVER_MODEL (default: the anonymous free model), so it
# needs no login and runs against a throwaway HOME and FM_HOME.
# The real fm-spawn launch command runs in a private tmux server; only worktree
# allocation and initial endpoint delivery use fixtures. Busy and turn-end
# signals come from the worktree plugin running inside the shared server, and
# steering, interrupt, exit, relaunch, and the teardown lease release all use
# the real Firstmate control plane against the attached client.
set -u
# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"
fm_live_gate opt-in FM_OPENCODE_SHARED_SERVER_LIVE opencode tmux jq curl
REAL_TMUX=$(command -v tmux)
VERSION="opencode $(opencode --version)"
MODEL=${FM_OPENCODE_SHARED_SERVER_MODEL:-opencode-go/minimax-m3.1-flash-free}
LAB=$(mktemp -d "${TMPDIR:-/tmp}/ocs.XXXXXX")
LAB=$(cd "$LAB" && pwd -P)
SOCKET="$LAB/tmux.sock"
H="$LAB/home"
WT="$LAB/wt"
PROJ="$LAB/project"
ID=ocs-live
TARGET="firstmate:fm-$ID"
server_pid() { sed -n 's/^pid=//p' "$H/state/opencode-server-default" 2>/dev/null; }
cleanup() {
  local pid
  pid=$(server_pid)
  case "$pid" in
    ''|*[!0-9]*) ;;
    *)
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
      ;;
  esac
  "$REAL_TMUX" -S "$SOCKET" kill-server >/dev/null 2>&1 || true
  # The spawn leaves its commit-msg hook directory read-only.
  chmod -R u+w "$LAB" 2>/dev/null || true
  rm -rf "$LAB"
}
trap cleanup EXIT
fail() {
  printf 'not ok - %s: %s\n' "$VERSION" "$1" >&2
  "$REAL_TMUX" -S "$SOCKET" capture-pane -p -t "$TARGET" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -n 30 >&2
  [ ! -s "$LAB/send.log" ] || { printf 'last send:\n' >&2; cat "$LAB/send.log" >&2; }
  exit 1
}
# shellcheck source=bin/fm-busy-lib.sh
. "$ROOT/bin/fm-busy-lib.sh"
# shellcheck source=bin/fm-backend.sh
. "$ROOT/bin/fm-backend.sh"

fm_test_spawn_home "$H" opencode
printf 'on\n' > "$H/config/opencode-shared-server"
fm_git_worktree "$PROJ" "$WT" ocs-live
git -C "$WT" config user.name 'OpenCode Shared Server Live Guard'
git -C "$WT" config user.email ocs-live-guard@example.invalid
fm_test_spawn_brief "$H" "$ID" "Runtime verification only: compute 12345 plus 67890 using your shell tool and write only the result into answer.txt, then stop and end your turn. Do no other work, do not delegate, and do not look for further instructions until a new message arrives."
fakebin=$(make_spawn_fakebin "$LAB/fake")
FM_FAKE_LAUNCH_LOG="$LAB/launch.sh" fm_test_run_spawn "$H" "$WT" "$fakebin" "$ID" "$PROJ" \
  --scout --harness opencode --model "$MODEL" > "$LAB/spawn.log" 2>&1 \
  || fail "fm-spawn failed: $(cat "$LAB/spawn.log")"
grep -q 'opencode attach' "$LAB/launch.sh" || fail "the launch did not attach: $(cat "$LAB/spawn.log")"
SESSION1=$(fm_meta_get "$H/state/$ID.meta" opencode_server_session)
[ -n "$SESSION1" ] || fail 'no server session was recorded'
# Route every later backend read and write to this guard's own socket.
mkdir -p "$LAB/bin"
printf '#!/bin/sh\nexec "%s" -S "%s" "$@"\n' "$REAL_TMUX" "$SOCKET" > "$LAB/bin/tmux"
chmod +x "$LAB/bin/tmux"
export PATH="$LAB/bin:$PATH" FM_HOME="$H" HOME="$H/user-home"
unset FM_ROOT_OVERRIDE FM_STATE_OVERRIDE FM_DATA_OVERRIDE FM_CONFIG_OVERRIDE FM_PROJECTS_OVERRIDE
# Past 120 columns OpenCode 1.19.0 draws its session sidebar on the composer
# rows, which the composer check reads as pending text for any OpenCode pane.
"$REAL_TMUX" -S "$SOCKET" new-session -d -s firstmate -n "fm-$ID" -x 120 -y 40 -c "$WT" \
  "/bin/sh '$LAB/launch.sh'; exec /bin/bash --noprofile --norc" || fail 'could not start pane'
capture() { "$REAL_TMUX" -S "$SOCKET" capture-pane -p -t "$TARGET"; }
classify() { fm_busy_classify tmux "$TARGET" opencode "$ID" "$H/state"; }
wait_file() {
  local path=$1 _
  for _ in $(seq 1 480); do [ -s "$path" ] && return 0; sleep 0.5; done
  fail "timed out waiting for ${path##*/}"
}
wait_classify() {
  local want=$1 _
  # A relaunched worker may redo earlier inbox work, so allow a long turn.
  for _ in $(seq 1 600); do [ "$(classify)" = "$want" ] && return 0; sleep 0.5; done
  fail "busy state never read '$want' (last: $(classify))"
}
wait_file "$WT/answer.txt"
[ "$(tr -d '[:space:]' < "$WT/answer.txt")" = 80235 ] || fail 'the brief did not execute through the server'
wait_classify 'idle opencode-plugin'
[ -f "$H/state/$ID.turn-ended" ] || fail 'the server-side plugin did not notify turn end'
[ "$(fm_backend_agent_state tmux "$TARGET")" = alive ] || fail 'the attached client is not classified alive'
pass "$VERSION: an attached worker runs its brief and reports busy, idle, and turn end from the server"

"$ROOT/bin/fm-send.sh" "$ID" 'Runtime steering verification: compute 31 times 37 and write only the result to steer.txt. Acknowledge this instruction by moving its .msg file into handled/ as instructed by the doorbell. Do no other work.' \
  > "$LAB/send.log" 2>&1 || fail "steer failed: $(cat "$LAB/send.log")"
wait_file "$WT/steer.txt"
wait_file "$H/state/$ID.inbox/handled/001.msg"
[ "$(tr -d '[:space:]' < "$WT/steer.txt")" = 1147 ] || fail 'wrong steering result'
wait_classify 'idle opencode-plugin'
pass "$VERSION: a real fm-send doorbell reaches the attached client and is acknowledged"

"$ROOT/bin/fm-send.sh" "$ID" 'Runtime interrupt verification: run sleep 90 in your shell tool, then wait for it to finish. Do not respond before it finishes.' \
  > "$LAB/send.log" 2>&1 || fail 'could not steer the interrupt probe'
wait_classify 'busy opencode-plugin'
sleep 3
"$ROOT/bin/fm-control.sh" "$ID" interrupt > "$LAB/interrupt.log" 2>&1 || fail "interrupt failed: $(cat "$LAB/interrupt.log")"
wait_classify 'idle opencode-plugin'
[ "$(fm_backend_agent_state tmux "$TARGET")" = alive ] || fail 'interrupt took the attached client down'
pass "$VERSION: interrupt cancels the server-side turn through the attached client"

SERVER_PID=$(server_pid)
"$ROOT/bin/fm-control.sh" "$ID" exit > "$LAB/exit.log" 2>&1 || fail "exit failed: $(cat "$LAB/exit.log")"
[ "$(fm_backend_agent_state tmux "$TARGET")" = dead ] || fail 'exit did not return the pane to its shell'
kill -0 "$SERVER_PID" 2>/dev/null || fail 'exiting the client stopped the shared server'
pass "$VERSION: exit closes the attached client and leaves the shared server running"

"$ROOT/bin/fm-control.sh" "$ID" relaunch --note 'Relaunch verification: write the product of 17 and 29 into relaunched.txt, then stop.' \
  > "$LAB/relaunch.log" 2>&1 || fail "relaunch failed: $(cat "$LAB/relaunch.log")"
SESSION2=$(fm_meta_get "$H/state/$ID.meta" opencode_server_session)
[ -n "$SESSION2" ] && [ "$SESSION2" != "$SESSION1" ] || fail "relaunch did not give the task a fresh session ($SESSION1 -> $SESSION2)"
[ "$(server_pid)" = "$SERVER_PID" ] || fail 'relaunch restarted a healthy shared server'
wait_file "$WT/relaunched.txt"
[ "$(tr -d '[:space:]' < "$WT/relaunched.txt")" = 493 ] || fail 'the relaunched worker did not run its note'
wait_classify 'idle opencode-plugin'
PASSWORD=$(cat "$H/state/opencode-server-default.password")
PORT=$(sed -n 's/^port=//p' "$H/state/opencode-server-default")
DIR_ENC=$(jq -rn --arg v "$WT" '$v|@uri')
code=$(printf 'user = "opencode:%s"\n' "$PASSWORD" | curl --config - -s -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:$PORT/session/$SESSION1?directory=$DIR_ENC")
[ "$code" = 404 ] || fail "the replaced session still exists on the server (HTTP $code)"
pass "$VERSION: relaunch reuses the server, retires the old session, and reports busy state for the new one"

"$ROOT/bin/fm-control.sh" "$ID" exit > "$LAB/exit.log" 2>&1 || fail "exit before cleanup failed: $(cat "$LAB/exit.log")"
printf 'Live guard report.\n' > "$H/data/$ID/report.md"
"$ROOT/bin/fm-captain-hold.sh" complete "$ID" --none > "$LAB/inventory.log" 2>&1 \
  || fail "the scout's empty captain-call inventory could not be recorded: $(cat "$LAB/inventory.log")"
# Cleanup reaps processes whose working directory is inside the worktree; the
# shared server runs from the state directory, so it is never one of them.
server_cwd=$(lsof -a -d cwd -p "$SERVER_PID" -Fn 2>/dev/null | sed -n 's/^n//p')
[ "$server_cwd" = "$(cd "$H/state" && pwd -P)" ] || fail "the shared server runs from '$server_cwd', not the state directory"
# Worktree allocation is a fixture here, so its return is too.
PATH="$LAB/bin:$fakebin:$PATH" "$ROOT/bin/fm-teardown.sh" "$ID" > "$LAB/teardown.log" 2>&1 \
  || fail "teardown failed: $(cat "$LAB/teardown.log")"
for _ in $(seq 1 40); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 0.25; done
! kill -0 "$SERVER_PID" 2>/dev/null || fail 'the server outlived its last worker'
[ ! -e "$H/state/opencode-server-default.leases/$ID" ] || fail 'teardown left the task lease behind'
pass "$VERSION: teardown releases the task and stops the shared server once no worker holds it"
