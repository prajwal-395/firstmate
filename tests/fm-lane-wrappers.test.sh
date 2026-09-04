#!/usr/bin/env bash
set -e
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TMP_ROOT=$(fm_test_tmproot fm-lane-wrappers)
fm_git_identity fmtest fmtest@example.invalid

test_lane_wrapper_protects_checked_out_branches() {
  local repo="$TMP_ROOT/main_repo"
  local clone="$TMP_ROOT/clone"
  local worker="$TMP_ROOT/worker"
  
  git init -q -b main "$repo"
  echo "base" > "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -q -m "base"
  
  git clone -q "$repo" "$clone"
  git -C "$clone" worktree add -q "$worker" -b feature
  
  # Inject the wrapper
  export PATH="$ROOT/bin/lane-wrappers:$PATH"
  
  # Worker tries to forcibly checkout main
  if cd "$worker" && git checkout -B main origin/main 2>"$TMP_ROOT/err"; then
    fail "checkout -B main should have been blocked"
  fi
  
  assert_contains "$(cat "$TMP_ROOT/err")" "Firstmate protection: cannot forcefully check out 'main'" "Wrapper rejected checkout -B"
  
  # Normal checkout -b should not be blocked by the wrapper, but git will reject it
  # Wait, the wrapper only blocks -B or -C
  if cd "$worker" && git checkout -b newbranch 2>"$TMP_ROOT/err2"; then
    pass "Normal branch creation succeeds"
  else
    fail "Wrapper erroneously blocked normal branch creation"
  fi
}

test_lane_wrapper_protects_checked_out_branches
echo "ok - git wrapper test"
