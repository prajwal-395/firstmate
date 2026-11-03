#!/usr/bin/env bash
# Sibling-script exec lint owned by bin/fm-lint-script-exec.sh.
#
# On macOS 26.3 every by-path exec of a `#!` script from a process tree rooted
# in a `#!` script leaked one kernel kalloc.1024 element until reboot, and
# firstmate's script-to-script calls made thousands of them per test lane.
# These cases pin the two ways the lint can regress: missing a by-path exec
# (the leak returns unnoticed), and flagging or --fix-rewriting a script path
# that is only data (a rewrite would then break the command it was passed to).
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SRC="$ROOT/bin/fm-lint-script-exec.sh"

# A throwaway repo whose bin/ holds the lint plus one sibling script, so the
# lint's notion of "a firstmate script" is exactly fm-b.sh.
lint_repo() {  # <name>
  local repo
  repo="$(fm_test_tmproot "$1")/repo"
  mkdir -p "$repo/bin/backends" "$repo/tests"
  cp "$SRC" "$repo/bin/fm-lint-script-exec.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$repo/bin/fm-b.sh"
  printf '%s\n' "$repo"
}

run_lint() {  # <repo> [args...]
  local repo=$1
  shift
  bash "$repo/bin/fm-lint-script-exec.sh" "$@"
}

test_rejects_by_path_execs() {
  local repo out rc=0
  repo=$(lint_repo fm-lint-script-exec-rejects)
  cat > "$repo/bin/fm-a.sh" <<'SH'
#!/usr/bin/env bash
DIR=$(dirname "$0")
B="$DIR/fm-b.sh"
"$DIR/fm-b.sh" plain
out=$("$DIR/fm-b.sh" substituted)
FM_X=1 timeout 5 "$DIR/fm-b.sh" wrapped || true
case "$1" in
  go) "$B" via-variable ;;
esac
FM_HOME="$(printf '%s' "$x")" \
  FM_Y=2 "$DIR/fm-b.sh" continued
exec "$DIR/fm-b.sh" replaced
FM_Z=$(printf '%s' "$x" | tr a b) \
  "$DIR/fm-b.sh" after-a-substituted-value
SH
  out=$(run_lint "$repo" "$repo/bin/fm-a.sh") || rc=$?
  [ "$rc" -eq 1 ] || fail "by-path execs exited $rc, expected 1"
  for line in 4 5 6 8 11 12 14; do
    assert_contains "$out" "fm-a.sh:$line: runs firstmate script" \
      "the by-path exec on line $line was not reported"
  done
  [ "$(printf '%s\n' "$out" | grep -c 'runs firstmate script')" -eq 7 ] \
    || fail "expected exactly the seven by-path execs, got: $out"
  pass "fm-lint-script-exec.sh reports each by-path exec of a sibling script"
}

test_accepts_interpreter_calls_and_data_paths() {
  local repo out rc=0
  repo=$(lint_repo fm-lint-script-exec-accepts)
  cat > "$repo/bin/fm-a.sh" <<'SH'
#!/usr/bin/env bash
DIR=$(dirname "$0")
bash "$DIR/fm-b.sh" explicit
exec bash "$DIR/fm-b.sh" replaced
. "$DIR/fm-b.sh"
cp "$DIR/fm-b.sh" "$DIR/copy.sh"
[ -x "$DIR/fm-b.sh" ] && echo executable
listener=("$DIR/fm-b.sh" poll)
echo "run $DIR/fm-b.sh yourself"
bash -c '. "$1"' _ \
  "$DIR/fm-b.sh"
got=$(bash -c '. "$1"; f "$2"' _ \
  "$DIR/fm-b.sh" arg)
cat > "$DIR/stub" <<EOF
"$DIR/fm-b.sh" inside-a-heredoc
EOF
"$DIR/fm-b.sh" allowed  # fm-script-exec-ok: fixture for the same-line form
# fm-script-exec-ok: fixture for the next-line form
"$DIR/fm-b.sh" allowed
# fm-script-exec-ok-begin: fixture for the block form
"$DIR/fm-b.sh" allowed
# fm-script-exec-ok-end
SH
  out=$(run_lint "$repo" "$repo/bin/fm-a.sh") || rc=$?
  [ "$rc" -eq 0 ] || fail "interpreter calls, data paths, or allowlisted lines were flagged: $out"
  pass "fm-lint-script-exec.sh accepts interpreter calls, data paths, heredocs, and allowlisted lines"
}

test_fix_rewrites_only_the_execs() {
  local repo out rc=0
  repo=$(lint_repo fm-lint-script-exec-fix)
  # A #!/bin/bash target keeps its absolute interpreter: process matching
  # (the remote job reaper) reads it from the command line.
  printf '#!/bin/bash\nexit 0\n' > "$repo/bin/fm-c.sh"
  cat > "$repo/bin/fm-a.sh" <<'SH'
#!/usr/bin/env bash
DIR=$(dirname "$0")
"$DIR/fm-b.sh" one && exec "$DIR/fm-b.sh" two
cp "$DIR/fm-b.sh" "$DIR/copy.sh"
"$DIR/fm-c.sh" three
SH
  run_lint "$repo" --fix "$repo/bin/fm-a.sh" >/dev/null || true
  out=$(run_lint "$repo" "$repo/bin/fm-a.sh") || rc=$?
  [ "$rc" -eq 0 ] || fail "--fix left findings behind: $out"
  # shellcheck disable=SC2016 # The expected file text holds literal $DIR.
  assert_contains "$(sed -n 3p "$repo/bin/fm-a.sh")" \
    'bash "$DIR/fm-b.sh" one && exec bash "$DIR/fm-b.sh" two' \
    "--fix did not put bash before both execs and keep exec"
  # shellcheck disable=SC2016 # The expected file text holds literal $DIR.
  assert_contains "$(sed -n 4p "$repo/bin/fm-a.sh")" 'cp "$DIR/fm-b.sh" "$DIR/copy.sh"' \
    "--fix rewrote a script path passed as data"
  # shellcheck disable=SC2016 # The expected file text holds literal $DIR.
  assert_contains "$(sed -n 5p "$repo/bin/fm-a.sh")" '/bin/bash "$DIR/fm-c.sh" three' \
    "--fix did not use the absolute interpreter a #!/bin/bash target names"
  pass "fm-lint-script-exec.sh --fix inserts the target interpreter before execs only"
}

test_rejects_by_path_execs
test_accepts_interpreter_calls_and_data_paths
test_fix_rewrites_only_the_execs
