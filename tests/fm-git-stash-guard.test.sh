#!/usr/bin/env bash
# Behavior tests for the per-task git stash guard.
#
# The defect this pins: every worktree of one repository shares a single
# refs/stash, so a `git stash` made in one worker lane can be popped in another
# lane and land that lane's changes in the first lane's worktree. Three lanes
# of one repository raced the shared stash that way on 2026-10-21. The guard
# refuses the stash command in a spawned worker's own worktree, and every
# ordinary git command in that same worktree keeps working.
#
# The spawn half of this suite drives the real bin/fm-spawn.sh against a fake
# pane and a real isolated git worktree, then EXECUTES the launch command the
# pane received under a synthetic pane environment, with the harness binary
# replaced by a probe that runs the two git commands in the task's worktree.
# What the probe reports is what a real worker would have seen.
set -u

# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"

GUARD_INSTALL="$ROOT/bin/fm-git-stash-guard.sh"
TMP_ROOT=$(fm_test_tmproot fm-git-stash-guard)

# Portable bounded run. macOS has no coreutils timeout, and a guard that hands
# off to itself would otherwise wedge the suite instead of failing it.
run_bounded() { # <seconds> <outfile> <cmd...>
  local secs=$1 out=$2
  shift 2
  "$@" >"$out" 2>&1 &
  local pid=$!
  ( sleep "$secs"; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  local killer=$!
  local rc=0
  wait "$pid" || rc=$?
  kill "$killer" 2>/dev/null
  wait "$killer" 2>/dev/null
  return $rc
}

# The guard directory is installed read-only, so a later install over an old
# one has to restore the owner write bit first.
remove_guard_dir() {
  chmod -R u+w "$1" 2>/dev/null || true
  rm -rf "$1"
}

install_guard() { # <dir>
  remove_guard_dir "$1"
  mkdir -p "$(dirname "$1")"
  bash "$GUARD_INSTALL" install "$1" >/dev/null 2>&1 \
    || fail "the guard installer refused to install into $1"
  [ -x "$1/git" ] || fail "the installer did not leave an executable git wrapper in $1"
}

# --- the guard itself -------------------------------------------------------

# The refusal is the contract: the stash command is refused, the refusal names
# the command and the alternative, and every ordinary git command still runs.
test_guard_refuses_stash_and_keeps_git_working() {
  local repo="$TMP_ROOT/unit/repo" guard="$TMP_ROOT/unit/guard" out rc
  mkdir -p "$TMP_ROOT/unit"
  git init -q -b main "$repo" || fail "could not initialize the guard fixture repo"
  echo base >"$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" -c user.email=t@e.invalid -c user.name=t commit -q -m base
  echo change >>"$repo/file.txt"
  install_guard "$guard"

  run_bounded 30 "$TMP_ROOT/unit/stash.out" env PATH="$guard:$PATH" git -C "$repo" stash
  rc=$?
  [ "$rc" -ne 0 ] || fail "git stash was not refused inside a guarded lane"
  assert_contains "$(cat "$TMP_ROOT/unit/stash.out")" "'git stash' is disabled" \
    "the refusal names the refused command"
  assert_contains "$(cat "$TMP_ROOT/unit/stash.out")" "your own branch instead" \
    "the refusal names the alternative the worker should use"

  # The refused stash left the repository's shared stash empty, which is the
  # cross-lane hazard this guard exists to stop.
  run_bounded 30 "$TMP_ROOT/unit/stashlist.out" git -C "$repo" stash list
  [ -s "$TMP_ROOT/unit/stashlist.out" ] \
    && fail "the refused stash still reached the repository's shared stash"
  pass "a refused git stash leaves the repository's shared stash empty"

  # Ordinary git keeps working in the same lane.
  run_bounded 30 "$TMP_ROOT/unit/status.out" env PATH="$guard:$PATH" git -C "$repo" status --porcelain
  rc=$?
  assert_equals 0 "$rc" "git status must keep working in a guarded lane"
  assert_contains "$(cat "$TMP_ROOT/unit/status.out")" "file.txt" \
    "git status must still report the lane's own uncommitted change"

  run_bounded 30 "$TMP_ROOT/unit/commit.out" env PATH="$guard:$PATH" \
    git -C "$repo" -c user.email=t@e.invalid -c user.name=t commit -q -am wip
  rc=$?
  assert_equals 0 "$rc" "git commit must keep working in a guarded lane"
  pass "the guard refuses git stash and leaves every ordinary git command working"
}

# Relaunch installs the guard over the read-only copy left by the prior
# spawn. The installer must replace it successfully and leave the refusal
# active afterward.
test_guard_install_is_idempotent() {
  local guard="$TMP_ROOT/reinstall/guard" out rc
  mkdir -p "$TMP_ROOT/reinstall"
  install_guard "$guard"
  bash "$GUARD_INSTALL" install "$guard" \
    || fail "a second install must replace the read-only guard"
  run_bounded 30 "$TMP_ROOT/reinstall/stash.out" env PATH="$guard:$PATH" git stash
  rc=$?
  [ "$rc" -ne 0 ] || fail "the reinstalled guard did not refuse git stash"
  assert_contains "$(cat "$TMP_ROOT/reinstall/stash.out")" "'git stash' is disabled" \
    "the reinstalled guard must retain the stash refusal"
  pass "a second install replaces the read-only guard and keeps protection active"
}

# The refusal keys on the git subcommand, not on the word: a commit message
# that mentions a stash is an ordinary commit, and the stash command behind a
# global option is still the stash command.
test_guard_reads_the_subcommand_not_the_word() {
  local repo="$TMP_ROOT/subcommand/repo" guard="$TMP_ROOT/subcommand/guard" out rc
  mkdir -p "$TMP_ROOT/subcommand"
  git init -q -b main "$repo" || fail "could not initialize the subcommand fixture repo"
  echo base >"$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" -c user.email=t@e.invalid -c user.name=t commit -q -m base
  echo change >>"$repo/file.txt"
  install_guard "$guard"

  run_bounded 30 "$TMP_ROOT/subcommand/msg.out" env PATH="$guard:$PATH" \
    git -C "$repo" -c user.email=t@e.invalid -c user.name=t commit -q -am "stash the old work"
  rc=$?
  assert_equals 0 "$rc" "a commit message mentioning a stash must not be refused"

  run_bounded 30 "$TMP_ROOT/subcommand/option.out" env PATH="$guard:$PATH" \
    git --no-pager -C "$repo" stash push -m wip
  rc=$?
  [ "$rc" -ne 0 ] || fail "git stash behind global options was not refused"
  pass "the guard refuses the stash subcommand and ignores the word in a message"
}

# The hand-off must never reach this guard again. A PATH entry can name the
# guard's directory through a symlink, and the same directory can appear twice
# under two names, so the hand-off compares physical paths before it picks the
# next git. A guard that handed off to itself would recurse until the process
# was killed, so the assertion here is that the command finishes at all.
test_guard_never_hands_off_to_itself() {
  local repo="$TMP_ROOT/recursion/repo" guard="$TMP_ROOT/recursion/guard"
  local link="$TMP_ROOT/recursion/link-guard" out rc
  mkdir -p "$TMP_ROOT/recursion"
  git init -q -b main "$repo" || fail "could not initialize the recursion fixture repo"
  echo base >"$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" -c user.email=t@e.invalid -c user.name=t commit -q -m base
  install_guard "$guard"
  ln -sfn "$guard" "$link"

  run_bounded 30 "$TMP_ROOT/recursion/out" env PATH="$link:$PATH" git -C "$repo" status --porcelain
  rc=$?
  assert_equals 0 "$rc" "the guard must not hand off to itself through a symlinked PATH entry"

  run_bounded 30 "$TMP_ROOT/recursion/out2" env PATH="$guard:$link:$PATH" git -C "$repo" status --porcelain
  rc=$?
  assert_equals 0 "$rc" "the guard must not hand off to itself when its directory is on PATH twice"
  pass "the guard hands off to the real git and never to itself"
}

test_guard_refuses_stash_and_keeps_git_working
test_guard_install_is_idempotent
test_guard_reads_the_subcommand_not_the_word
test_guard_never_hands_off_to_itself

# --- the spawned worker -----------------------------------------------------

# make_case <name> <harness> <id>...
# Echoes "<case-dir>|<home>|<project>|<worktree>|<fakebin>|<launch-log>|<pane-log>".
make_case() {
  local name=$1 harness=$2 case_dir home proj wt fakebin launchlog panelog id
  shift 2
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  proj="$case_dir/project"
  wt="$case_dir/wt"
  launchlog="$case_dir/launch.log"
  panelog="$case_dir/pane.log"
  fakebin=$(fm_test_make_spawn_fakebin "$case_dir/fake" "$harness")
  fm_test_spawn_home "$home" "$harness"
  fm_git_worktree "$proj" "$wt" "wt-$name"
  for id in "$@"; do
    fm_test_spawn_brief "$home" "$id"
  done
  printf '%s\n' "$case_dir|$home|$proj|$wt|$fakebin|$launchlog|$panelog"
}

read_case() {
  IFS='|' read -r CASE_DIR HOME_DIR PROJ_DIR WT_DIR FAKEBIN_DIR LAUNCH_LOG PANE_LOG <<EOF
$1
EOF
}

# The probe replaces the harness binary and runs the two git commands in the
# task's worktree, which is what a worker does when it reaches for a stash.
install_worktree_probe() { # <fakebin> <harness> <outdir>
  cat > "$1/$2" <<SH
#!/usr/bin/env bash
cd "\$FM_TEST_WT" || exit 1
printf 'worker change\\n' > worker-change.txt
git stash >"$3/stash.out" 2>&1
printf 'STASH_RC=%s\n' "\$?" >> "$3/probe.out"
git status --porcelain >"$3/status.out" 2>&1
printf 'STATUS_RC=%s\n' "\$?" >> "$3/probe.out"
SH
  chmod +x "$1/$2"
}

# The defect: a spawned worker's `git stash` in its own worktree is refused,
# while the ordinary git commands in that same worktree keep working and the
# lane's uncommitted change is still there.
test_spawned_worker_cannot_stash_in_its_worktree() {
  local id=spawn-guard-a1 rec out status launch preamble probe_dir seen
  rec=$(make_case spawn-guard codex "$id")
  read_case "$rec"
  out=$(FM_FAKE_LAUNCH_LOG="$LAUNCH_LOG" FM_FAKE_PANE_LOG="$PANE_LOG" \
    fm_test_run_spawn "$HOME_DIR" "$WT_DIR" "$FAKEBIN_DIR" "$id" "$PROJ_DIR" \
    --mode direct-PR --yolo off)
  status=$?
  expect_code 0 "$status" "the guarded spawn should succeed: $out"

  [ -x "$HOME_DIR/state/$id.git-guard/git" ] \
    || fail "the spawn did not install this task's git guard"
  assert_contains "$(cat "$PANE_LOG")" "$id.git-guard" \
    "the pane must receive this task's git guard on its PATH"

  probe_dir="$CASE_DIR/probe"
  mkdir -p "$probe_dir"
  install_worktree_probe "$FAKEBIN_DIR" codex "$probe_dir"
  launch=$(cat "$LAUNCH_LOG")
  [ -n "$launch" ] || fail "the guarded spawn sent no launch command"
  preamble=$(grep '^export ' "$PANE_LOG")
  seen=$(env -i HOME="$TMP_ROOT/pane-home" PATH="$FAKEBIN_DIR:$PATH" TERM=xterm \
    TMUX=synthetic-pane FM_TEST_WT="$WT_DIR" FM_TEST_OUT="$probe_dir" \
    /bin/sh -c "$preamble
$launch") || fail "the emitted launch failed to run: $seen"

  [ -f "$probe_dir/probe.out" ] || fail "the worker probe never reported a result"
  assert_contains "$(cat "$probe_dir/probe.out")" "STASH_RC=128" \
    "a spawned worker's git stash in its worktree must be refused"
  assert_contains "$(cat "$probe_dir/stash.out")" "'git stash' is disabled" \
    "the refusal must reach the worker with its reason"
  assert_contains "$(cat "$probe_dir/probe.out")" "STATUS_RC=0" \
    "ordinary git commands must keep working in the spawned worker's worktree"
  assert_contains "$(cat "$probe_dir/status.out")" "worker-change.txt" \
    "the worker's own uncommitted change must still be in its worktree"
  # The shared stash of the repository behind that worktree is still empty, so
  # no other lane can pop the worker's changes out from under it.
  run_bounded 30 "$probe_dir/stashlist.out" git -C "$PROJ_DIR" stash list
  [ -s "$probe_dir/stashlist.out" ] \
    && fail "the refused stash still reached the repository's shared stash"
  pass "a spawned worker's git stash is refused while ordinary git keeps working"
}

test_spawned_worker_cannot_stash_in_its_worktree
