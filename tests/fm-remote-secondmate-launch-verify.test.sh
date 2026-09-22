#!/usr/bin/env bash
# tests/fm-remote-secondmate-launch-verify.test.sh - a remote secondmate launch
# must prove an agent process actually started before it reports success.
#
# Three consecutive secondmate launches onto a host with no agent binary
# reported SUCCESS while placing an empty pane: cmd_launch in
# bin/fm-remote-secondmate-control.sh printed its route as soon as fm-spawn
# returned and endpoint metadata existed, without ever checking that an agent
# had registered in the new pane.
#
# This drives the REAL host-local launch leg
# (bin/fm-remote-secondmate-control.sh launch -> the real bin/fm-spawn.sh
# --secondmate on the herdr backend) against a fake herdr CLI, in two shapes:
# a host whose harness executable is absent must fail naming the missing
# binary, and a host where the spawn succeeds but no agent ever registers
# must fail instead of reporting a route - never source-text matching. Both
# shapes fail through the same post-launch proof: the launch reads its fresh
# endpoint's own agent state and refuses while it shows no agent.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=tests/remote-herdr-fixture.sh
. "$(dirname "${BASH_SOURCE[0]}")/remote-herdr-fixture.sh"

command -v jq >/dev/null 2>&1 || { echo "skip: jq not found"; exit 0; }
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
TMP_ROOT=$(fm_test_tmproot fm-remote-launch-verify)
mkdir -p "$TMP_ROOT"
TMP_ROOT=$(cd "$TMP_ROOT" && pwd -P)
REMOTE_HOME="$TMP_ROOT/remote-home"
FAKEHOST="$TMP_ROOT/fakehost"
HERDR_STATE="$TMP_ROOT/remote-herdr.state"
HERDR_LOG="$TMP_ROOT/remote-herdr.log"
SWALLOW_SENDS="$TMP_ROOT/herdr-swallow-sends"
cleanup() {
  rm -rf -- "$TMP_ROOT"
}
trap cleanup EXIT

# A seeded remote secondmate home, as the host-local leg expects it: the
# identity marker names the id, and the charter is the secondmate brief.
mkdir -p "$REMOTE_HOME/bin" "$REMOTE_HOME/config" "$REMOTE_HOME/data" "$REMOTE_HOME/state"
printf 'rsm\n' > "$REMOTE_HOME/.fm-secondmate-home"
printf 'fixture secondmate home\n' > "$REMOTE_HOME/AGENTS.md"
printf 'Verify-launch regression charter.\n' > "$REMOTE_HOME/data/charter.md"

# The proven remote-herdr surface, wrapped so one flag file turns the pane
# into the incident shape: sends are accepted (the spawn proceeds exactly as
# it did onto the binary-less host) but nothing is ever typed, so no agent
# can register and `agent get` keeps answering agent_not_found.
mkdir -p "$FAKEHOST/bin"
install_remote_herdr_fixture "$FAKEHOST" "$HERDR_STATE" "$HERDR_LOG" \
  "$TMP_ROOT/herdr-send-fail" "$TMP_ROOT/herdr.sock"
mv "$FAKEHOST/bin/herdr" "$FAKEHOST/bin/herdr.real"
cat > "$FAKEHOST/bin/herdr" <<SH
#!/usr/bin/env bash
set -u
printf '%s\n' "\$*" >> '$HERDR_LOG'
if [ -f '$SWALLOW_SENDS' ]; then
  case "\${1:-} \${2:-}" in
    "pane send-text"|"pane send-keys") exit 0 ;;
  esac
fi
exec '$FAKEHOST/bin/herdr.real' "\$@"
SH
chmod +x "$FAKEHOST/bin/herdr"

REMOTE_META="$REMOTE_HOME/state/parent-route/rsm.meta"

run_launch() { # <out-file> <err-file>; rc in LAUNCH_RC, stdout in file
  local out=$1 err=$2
  LAUNCH_RC=0
  FM_HOME="$REMOTE_HOME" \
  PATH="$FAKEHOST/bin:$PATH" \
  FM_REMOTE_LAUNCH_WAIT=3 \
    "$ROOT/bin/fm-remote-secondmate-control.sh" launch rsm codex - - herdr \
    > "$out" 2> "$err" || LAUNCH_RC=$?
}

# Case 1: the host has no usable agent binary. A non-executable shadow placed
# first on PATH proves absence deterministically on every machine, whatever
# the runner has installed: the pane shell could not exec it either. Its
# consequence is what the launch must answer to - a shell that cannot start
# the agent leaves an agent-less pane - so the send swallow models exactly
# that: the typed launch runs nowhere and no agent ever registers.
: > "$FAKEHOST/bin/codex"
: > "$SWALLOW_SENDS"
rm -rf -- "$REMOTE_HOME/state/parent-route"
run_launch "$TMP_ROOT/case1.out" "$TMP_ROOT/case1.err"
[ "$LAUNCH_RC" -ne 0 ] \
  || fail "remote launch reported success with no usable agent binary (rc=0): $(cat "$TMP_ROOT/case1.out")"
assert_grep 'codex' "$TMP_ROOT/case1.err" \
  "the missing-binary failure did not name the binary"
assert_no_grep 'schema=fm-remote-secondmate-control.v1' "$TMP_ROOT/case1.out" \
  "the agent-less launch still printed its success route"
assert_present "$REMOTE_META" \
  "the failed launch must retain its endpoint record as a truthful dead endpoint for recovery"
pass "remote launch fails when the harness executable is absent, naming the missing binary"

# Case 2: the binary resolves but no agent ever registers in the new pane -
# the spawn succeeds exactly as it did in the incident, so only a
# post-launch proof can keep the success line honest.
printf '#!/bin/sh\nexit 0\n' > "$FAKEHOST/bin/codex"
chmod +x "$FAKEHOST/bin/codex"
: > "$SWALLOW_SENDS"
rm -rf -- "$REMOTE_HOME/state/parent-route"
run_launch "$TMP_ROOT/case2.out" "$TMP_ROOT/case2.err"
[ "$LAUNCH_RC" -ne 0 ] \
  || fail "remote launch reported success although no agent registered (rc=0): $(cat "$TMP_ROOT/case2.out")"
assert_grep 'codex' "$TMP_ROOT/case2.err" \
  "the agent-less failure did not name the harness binary"
assert_no_grep 'schema=fm-remote-secondmate-control.v1' "$TMP_ROOT/case2.out" \
  "the agent-less launch still printed its success route"
pass "remote launch fails when no agent registers after spawn instead of reporting success"

echo "ALL TESTS PASSED"
