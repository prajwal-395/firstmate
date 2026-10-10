#!/usr/bin/env bash
# tests/fm-timeout-group.test.sh - the bounded-execution contract in
# bin/fm-timeout-lib.sh: a deadline must reap the WHOLE commanded subtree, not
# just timeout's direct child. The production shape is a shell wrapper whose
# descendant ignores TERM (an inherited disposition timeout only resets for its
# direct child, or a pure-bash worker mid-fold that never receives a forwarded
# signal): timeout exits 124 on time while the grandchild keeps running
# unbounded - the home-summary refresh that held its lock and CPU for 8+ minutes
# past its 60-second deadline. These drive the REAL fm_run_timed over crafted
# subprocess trees and assert on process lifetime, never on the lib's source.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=bin/fm-timeout-lib.sh
. "$ROOT/bin/fm-timeout-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-timeout-group-tests)

test_fast_command_returns_its_status() {
  local rc
  fm_run_timed 10 printf 'hello\n'
  rc=$?
  [ "$rc" -eq 0 ] || fail "fast command should exit 0, got $rc"
  fm_run_timed 10 bash -c 'exit 7'
  rc=$?
  [ "$rc" -eq 7 ] || fail "failing command should keep exit 7, got $rc"
}

test_plain_sleeper_times_out() {
  local start now elapsed rc
  start=$(date +%s)
  fm_run_timed 2 sleep 30
  rc=$?
  [ "$rc" -eq 124 ] || fail "overdue sleeper should exit 124, got $rc"
  now=$(date +%s)
  elapsed=$((now - start))
  [ "$elapsed" -lt 15 ] || fail "deadline should fire near 2s, took ${elapsed}s"
}

test_term_ignoring_grandchild_is_reaped() {
  local dir pid waited=0 rc
  dir="$TMP_ROOT/reap"
  mkdir -p "$dir"
  cat > "$dir/worker.sh" <<'EOF'
#!/usr/bin/env bash
trap "" TERM
printf '%s' "$$" > "$1.pid"
i=0
while [ "$i" -lt 30 ]; do sleep 1; i=$((i + 1)); done
EOF
  chmod +x "$dir/worker.sh"
  fm_run_timed 2 bash -c '"$0" "$1"' "$dir/worker.sh" "$dir/worker"
  rc=$?
  [ "$rc" -eq 124 ] || fail "overdue worker tree should exit 124, got $rc"
  pid=$(cat "$dir/worker.pid" 2>/dev/null || true)
  case "$pid" in ''|*[!0-9]*) fail "worker never recorded its pid" ;; esac
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 60 ]; do
    sleep 0.2
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
    fail "TERM-ignoring grandchild $pid outlived the deadline cleanup"
  fi
}

test_monitor_mode_is_restored() {
  case $- in *m*) fail "test started with monitor mode on" ;; esac
  fm_run_timed 2 sleep 30 >/dev/null 2>&1 || true
  case $- in *m*) fail "fm_run_timed leaked monitor mode into the caller" ;; esac
}

# The production defect model: a timeout that signals only its direct child and
# isolates nothing - the observed GNU behavior on the watcher's launch lineage,
# where the refresh worker below the wrapper never receives a signal and a
# pure-bash snapshot mid-fold runs unbounded past the deadline. This fake
# runner encodes exactly that shape, so the test proves the LIBRARY's own group
# cleanup reaps the subtree rather than relying on the real timeout's
# group handling, which varies by launch context.
test_deficient_runner_subtree_is_reaped() {
  local dir rc pid waited=0
  dir="$TMP_ROOT/deficient"
  mkdir -p "$dir/bin"
  cat > "$dir/bin/timeout" <<'EOF'
#!/usr/bin/env bash
# deficient-timeout: signals only the direct child, like the observed lineage.
[ "$1" = -k ] && shift 2
seconds=$1; shift
"$@" &
child=$!
sleep "$seconds"
kill -TERM "$child" 2>/dev/null || true
sleep 1
kill -KILL "$child" 2>/dev/null || true
wait "$child" 2>/dev/null || true
exit 124
EOF
  chmod +x "$dir/bin/timeout"
  cat > "$dir/worker.sh" <<'EOF'
#!/usr/bin/env bash
trap "" TERM
printf '%s' "$$" > "$1.pid"
i=0
while [ "$i" -lt 30 ]; do sleep 1; i=$((i + 1)); done
EOF
  chmod +x "$dir/worker.sh"
  PATH="$dir/bin:$PATH" fm_run_timed 2 bash -c '"$0" "$1"' "$dir/worker.sh" "$dir/worker"
  rc=$?
  [ "$rc" -eq 124 ] || fail "deficient runner should still report 124, got $rc"
  pid=$(cat "$dir/worker.pid" 2>/dev/null || true)
  case "$pid" in ''|*[!0-9]*) fail "worker never recorded its pid" ;; esac
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 60 ]; do
    sleep 0.2
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
    fail "TERM-ignoring grandchild $pid outlived the deadline under a deficient runner"
  fi
}

test_fast_command_returns_its_status
test_plain_sleeper_times_out
test_term_ignoring_grandchild_is_reaped
test_monitor_mode_is_restored
test_deficient_runner_subtree_is_reaped
pass "timeout group enforcement"
