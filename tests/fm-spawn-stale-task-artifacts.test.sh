#!/usr/bin/env bash
# Regression tests for fm-spawn's pooled-slot stale-artifact annotation.
#
# A pooled worktree refresh resets tracked files while gitignored ones stay in
# place, so a re-dispatched task can draw a slot still holding its predecessor's
# working notes under its own data/<task-id>/ path and follow them over its
# brief. The spawn must name those pre-existing ignored files in the launch
# brief as untrusted leftovers from a previous run, never as evidence, and must
# never delete them: ignored paths are where preserved evidence lives.
set -u

# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"

TMP_ROOT=$(fm_test_tmproot fm-spawn-stale-task-artifacts)

make_stale_case() {
  local name=$1 id=$2 case_dir home project origin pool fakebin
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  project="$case_dir/project"
  origin="$case_dir/origin.git"
  pool="$case_dir/pool"
  fakebin=$(make_spawn_fakebin "$case_dir/fake")

  mkdir -p "$home/data/$id" "$home/projects" "$home/state" "$home/config"
  printf 'codex\n' > "$home/config/crew-harness"
  fm_test_spawn_brief "$home" "$id"
  touch "$home/state/.last-watcher-beat"

  git init --quiet -b main "$project"
  printf 'data/\n' > "$project/.gitignore"
  printf 'base\n' > "$project/README.md"
  git -C "$project" add .gitignore README.md
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm initial
  git clone --quiet --bare "$project" "$origin"
  git -C "$project" remote add origin "file://$origin"
  git -C "$project" fetch --quiet origin
  git -C "$project" worktree add --quiet --detach "$pool" HEAD

  printf '%s\n' "$home|$project|$pool|$fakebin"
}

read_stale_case() {
  IFS='|' read -r HOME_DIR PROJECT_DIR POOL_DIR FAKEBIN_DIR <<EOF
$1
EOF
}

run_stale_spawn() {
  local id=$1
  shift
  fm_test_run_spawn "$HOME_DIR" "$POOL_DIR" "$FAKEBIN_DIR" \
    "$id" "$PROJECT_DIR" "$@"
}

test_stale_task_artifacts_are_named_in_the_brief() {
  local rec id out status brief
  id='pool-stale-artifacts-r1'
  rec=$(make_stale_case stale-named "$id")
  read_stale_case "$rec"
  mkdir -p "$POOL_DIR/data/$id"
  printf 'stale predecessor plan: hold for a deletion list that already landed\n' > "$POOL_DIR/data/$id/derivation.md"
  printf 'stale predecessor measurement: a 292-id set on a tree that no longer exists\n' > "$POOL_DIR/data/$id/notes.md"
  [ -z "$(git -C "$POOL_DIR" status --porcelain)" ] \
    || fail "fixture: the stale data files should be gitignored, but the pool reads dirty"

  out=$(run_stale_spawn "$id" --mode direct-PR --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should succeed when the slot holds stale ignored files"$'\n'"$out"
  assert_contains "$out" "spawned $id" "spawn did not report success"
  brief="$HOME_DIR/data/$id/launch-brief.md"
  assert_grep "data/$id/derivation.md" "$brief" \
    "the launch brief does not name the stale derivation file"
  assert_grep "data/$id/notes.md" "$brief" \
    "the launch brief does not name the second stale file"
  assert_grep "ntrusted leftovers" "$brief" \
    "the launch brief does not mark the stale files as untrusted leftovers"
  assert_grep "this brief wins" "$brief" \
    "the launch brief does not tell the worker the brief wins over the stale files"
  assert_grep "o not delete" "$brief" \
    "the launch brief does not tell the worker to leave the stale files alone"
  assert_grep 'stale predecessor plan' "$POOL_DIR/data/$id/derivation.md" \
    "spawn deleted or rewrote the stale derivation file it must only report"
  assert_grep 'stale predecessor measurement' "$POOL_DIR/data/$id/notes.md" \
    "spawn deleted or rewrote the stale notes file it must only report"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed stale annotation:\n'
    grep -A8 'UNTRUSTED LEFTOVERS' "$brief"
  fi
  pass "a spawn names its slot's stale ignored files in the brief and leaves them in place"
}

test_clean_slot_leaves_the_brief_unchanged() {
  local rec id out status brief
  id='pool-stale-clean-r2'
  rec=$(make_stale_case stale-clean "$id")
  read_stale_case "$rec"

  out=$(run_stale_spawn "$id" --mode direct-PR --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should succeed for a task id with no slot files"$'\n'"$out"
  brief="$HOME_DIR/data/$id/launch-brief.md"
  assert_no_grep "UNTRUSTED LEFTOVERS" "$brief" \
    "a clean slot drew a stale-artifact warning it should not have"
  pass "a task id with no slot files leaves the brief unchanged"
}

test_other_task_artifacts_are_not_claimed() {
  local rec id out status brief other='pool-stale-neighbor-r9'
  id='pool-stale-other-r3'
  rec=$(make_stale_case stale-other "$id")
  read_stale_case "$rec"
  mkdir -p "$POOL_DIR/data/$other"
  printf 'a different task left this here\n' > "$POOL_DIR/data/$other/derivation.md"

  out=$(run_stale_spawn "$id" --mode direct-PR --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should succeed when only another task id has slot files"$'\n'"$out"
  brief="$HOME_DIR/data/$id/launch-brief.md"
  assert_no_grep "UNTRUSTED LEFTOVERS" "$brief" \
    "the brief claimed another task id's files as this task's leftovers"
  assert_no_grep "data/$other/derivation.md" "$brief" \
    "the brief named another task id's file"
  assert_grep 'a different task left this here' "$POOL_DIR/data/$other/derivation.md" \
    "spawn touched another task id's file"
  pass "only the task's own data path is reported, never another task's"
}

test_stale_task_artifacts_are_named_for_scouts() {
  local rec id out status brief
  id='pool-stale-scout-r4'
  rec=$(make_stale_case stale-scout "$id")
  read_stale_case "$rec"
  mkdir -p "$POOL_DIR/data/$id"
  printf 'stale scout working note\n' > "$POOL_DIR/data/$id/derivation.md"

  out=$(run_stale_spawn "$id" --scout)
  status=$?
  expect_code 0 "$status" "a scout spawn should succeed when the slot holds stale ignored files"$'\n'"$out"
  brief="$HOME_DIR/data/$id/launch-brief.md"
  assert_grep "data/$id/derivation.md" "$brief" \
    "the scout launch brief does not name the stale file"
  assert_grep 'stale scout working note' "$POOL_DIR/data/$id/derivation.md" \
    "the scout spawn deleted the stale file it must only report"
  pass "a scout spawn names its slot's stale ignored files in the brief"
}

test_stale_task_artifacts_are_named_in_the_brief
test_clean_slot_leaves_the_brief_unchanged
test_other_task_artifacts_are_not_claimed
test_stale_task_artifacts_are_named_for_scouts
