#!/usr/bin/env bash
# Tests for bin/fm-batch-pr-verify.sh: the batch verifier for projects
# without CI, which stacks cleanly-merging PRs with merge-tree/commit-tree,
# checks the stack out as a shared clone, runs the test union once under the
# verify lock, merges through bin/fm-pr-merge.sh when green, and bisects when
# red.
#
# The test_* functions below name the covered moved-head, bisect, and
# fail-closed behavior directly. Every case runs against throwaway local git
# repos in --no-forge mode, never a real project, and merges through a stub
# fm-pr-merge.sh reached via FM_ROOT_OVERRIDE.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
fm_git_identity fmtest fmtest@example.invalid

VERIFY="$ROOT/bin/fm-batch-pr-verify.sh"
TMP_ROOT=$(fm_test_tmproot fm-batch-pr-verify-tests)

# make_repo <dir>: a throwaway project with main plus topic branches.
# Prints nothing; the caller names the branches it commits.
make_repo() {
  local dir=$1
  mkdir -p "$dir"
  git init -q -b main "$dir"
  git -C "$dir" config user.email fmtest@example.invalid
  git -C "$dir" config user.name fmtest
  mkdir -p "$dir/src"
  echo "x = 1" > "$dir/src/a.py"
  git -C "$dir" add -A
  git -C "$dir" commit -qm base
}

# commit_branch <repo> <branch> <file> <content> <message>: commit one file
# on a new branch cut from main.
commit_branch() {
  local repo=$1 branch=$2 file=$3 content=$4 message=$5
  git -C "$repo" checkout -q -b "$branch" main
  mkdir -p "$repo/$(dirname "$file")"
  printf '%s\n' "$content" > "$repo/$file"
  git -C "$repo" add -A
  git -C "$repo" commit -qm "$message"
  git -C "$repo" checkout -q main
}

# Green batch: two stacked members verify green and the project repo keeps
# no batch refs and a clean worktree, because the tool only ever reads it.
test_green_batch_leaves_project_untouched() {
  local repo="$TMP_ROOT/green/proj" batch="$TMP_ROOT/green/batch" out status
  make_repo "$repo"
  commit_branch "$repo" pr1 tests/test_one.py "def test_one(): assert True" pr1
  commit_branch "$repo" pr2 tests/test_two.py "def test_two(): assert True" pr2
  out=$("$VERIFY" build --repo "$repo" --out "$batch" --no-forge pr1:task-a=pr1 pr2:task-b=pr2)
  status=$?
  expect_code_out 0 "$status" "$out" "build of two clean members"
  assert_contains "$out" "members: 2" "build reports both members stacked"
  [ "$(grep -c . "$batch/members")" = 2 ] || fail "members file holds both stacked PRs"
  out=$("$VERIFY" verify --repo "$repo" --batch "$batch" --lock "$TMP_ROOT/green.lock" \
    --select-cmd "true" --test-cmd ":")
  status=$?
  expect_code_out 0 "$status" "$out" "verify of a green stack"
  assert_contains "$(cat "$batch/result")" "verdict: green" "result records the green verdict"
  [ -z "$(git -C "$repo" status --porcelain)" ] || fail "project worktree changed by build or verify"
  [ -z "$(git -C "$repo" for-each-ref 'refs/batch/*')" ] || fail "project repo gained batch refs"
  pass "green batch verifies and never writes to the project repo"
}

# Moved-head refusal: a member advanced after build must not merge, while
# the unmoved member still merges through bin/fm-pr-merge.sh.
test_merge_refuses_moved_head() {
  local repo="$TMP_ROOT/moved/proj" batch="$TMP_ROOT/moved/batch" home="$TMP_ROOT/moved/home"
  local out status
  make_repo "$repo"
  commit_branch "$repo" pr1 tests/test_one.py "def test_one(): assert True" pr1
  commit_branch "$repo" pr2 tests/test_two.py "def test_two(): assert True" pr2
  mkdir -p "$home/bin"
  cat > "$home/bin/fm-pr-merge.sh" <<'STUB'
#!/usr/bin/env bash
echo "$@" >> "${FM_MERGE_LOG:?}/calls"
exit 0
STUB
  chmod +x "$home/bin/fm-pr-merge.sh"
  mkdir -p "$TMP_ROOT/moved/calls"
  out=$("$VERIFY" build --repo "$repo" --out "$batch" --no-forge pr1:task-a=pr1 pr2:task-b=pr2)
  expect_code_out 0 "$?" "$out" "build before the head moves"
  git -C "$repo" checkout -q pr1
  echo "drift = True" >> "$repo/tests/test_one.py"
  git -C "$repo" commit -qam drift
  git -C "$repo" checkout -q main
  out=$(FM_ROOT_OVERRIDE="$home" FM_MERGE_LOG="$TMP_ROOT/moved/calls" \
    "$VERIFY" merge --repo "$repo" --batch "$batch" --no-forge)
  status=$?
  [ "$status" -ne 0 ] || fail "merge with a moved head should exit non-zero"
  assert_contains "$(cat "$batch/merge-refused")" "pr1: head moved after build" "moved member is refused by name"
  assert_contains "$(cat "$TMP_ROOT/moved/calls/calls")" "task-b" "unmoved member still merges"
  assert_not_contains "$(cat "$TMP_ROOT/moved/calls/calls")" "task-a" "moved member never reaches fm-pr-merge.sh"
  pass "merge refuses the moved head and still merges the unmoved member"
}

# Bisect: of three stacked members, the one whose change breaks the test
# command is named as the culprit of the red batch.
test_bisect_isolates_red_member() {
  local repo="$TMP_ROOT/red/proj" batch="$TMP_ROOT/red/batch" out status
  make_repo "$repo"
  commit_branch "$repo" good1 tests/test_good1.py "def test_good1(): assert True" good1
  commit_branch "$repo" bad src/poison.py "POISON = True" bad
  commit_branch "$repo" good2 tests/test_good2.py "def test_good2(): assert True" good2
  cat > "$TMP_ROOT/red-check.sh" <<'CHECK'
#!/bin/sh
if grep -rq POISON src/ 2>/dev/null; then exit 1; fi
exit 0
CHECK
  chmod +x "$TMP_ROOT/red-check.sh"
  out=$("$VERIFY" build --repo "$repo" --out "$batch" --no-forge \
    good1:task-g1=good1 bad:task-bad=bad good2:task-g2=good2)
  expect_code_out 0 "$?" "$out" "build of the three-member stack"
  out=$("$VERIFY" verify --repo "$repo" --batch "$batch" --lock "$TMP_ROOT/red.lock" \
    --select-cmd "true" --test-cmd "$TMP_ROOT/red-check.sh")
  status=$?
  [ "$status" -ne 0 ] || fail "verify with the poisoned member should exit non-zero"
  assert_contains "$(cat "$batch/result")" "verdict: red" "result records the red verdict"
  out=$("$VERIFY" bisect --repo "$repo" --batch "$batch" --lock "$TMP_ROOT/red.lock" \
    --test-cmd "$TMP_ROOT/red-check.sh")
  expect_code_out 0 "$?" "$out" "bisect of the red batch"
  assert_contains "$(cat "$batch/bisect")" "culprit: bad" "bisect names the poisoned member"
  pass "bisect isolates the red member of a red batch"
}

# Fail-closed union: a batch selecting no test files is refused, never
# reported green, so an untested stack cannot flow into merge.
test_verify_refuses_empty_test_union() {
  local repo="$TMP_ROOT/empty/proj" batch="$TMP_ROOT/empty/batch" out status
  make_repo "$repo"
  commit_branch "$repo" docs README.md "more words" docs
  out=$("$VERIFY" build --repo "$repo" --out "$batch" --no-forge docs:task-d=docs \
    --test-glob '(^|/)test_[^/]*\.py$')
  expect_code_out 0 "$?" "$out" "build of the docs-only member"
  out=$("$VERIFY" verify --repo "$repo" --batch "$batch" --lock "$TMP_ROOT/empty.lock" \
    --select-cmd "true" --test-cmd ":")
  status=$?
  [ "$status" -ne 0 ] || fail "verify with no selectable tests should exit non-zero"
  assert_contains "$(cat "$batch/result")" "verdict: refused (no test files selected)" \
    "result records the refusal instead of a green verdict"
  pass "verify refuses a batch with no selectable tests"
}

test_green_batch_leaves_project_untouched
test_merge_refuses_moved_head
test_bisect_isolates_red_member
test_verify_refuses_empty_test_union
