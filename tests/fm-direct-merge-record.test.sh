#!/usr/bin/env bash
# Tests for bin/fm-direct-merge-record.sh: the task-shaped merge record for
# firstmate's own direct changes to this repo's shared tracked material.
#
# The contract under test is that firstmate's direct work merges through the
# UNCHANGED bin/fm-pr-merge.sh, so every refusal that script owns - a red PR,
# a merge the forge did not accept, and the head-bound forge call that keeps a
# changed head from landing unverified - fires exactly as it does for a
# task-owned PR. The test_* functions below name the covered record, refusal,
# head-binding, and retirement behavior directly.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
fm_git_identity fmtest fmtest@example.invalid

RECORD="$ROOT/bin/fm-direct-merge-record.sh"
PR_MERGE="$ROOT/bin/fm-pr-merge.sh"
TMP_ROOT=$(fm_test_tmproot fm-direct-merge-record-tests)

command -v jq >/dev/null 2>&1 || fail "these tests read the mocked GitHub JSON with the real jq, which was not found"

file_mode() {
  if [ "$(uname)" = Darwin ]; then
    stat -f %Lp "$1"
  else
    stat -c %a "$1"
  fi
}

# Build a fresh sandbox for one test case: a state dir and a home dir with a
# backlog, plus a directory for the forge-command mocks. Echoes the case dir.
make_case() {
  local name=$1 case_dir
  case_dir="$TMP_ROOT/$name"
  mkdir -p "$case_dir/state" "$case_dir/home/data" "$case_dir/home/config" "$case_dir/fakebin"
  cp "$ROOT/.tasks.toml" "$case_dir/home/.tasks.toml"
  printf '%s\n' '## In flight' '' '## Queued' '' '## Done' \
    > "$case_dir/home/data/backlog.md"
  printf '%s\n' \
    'state=MERGED' \
    'merged=true' \
    'queued=false' \
    'base=main' > "$case_dir/github-outcome"
  : > "$case_dir/gh.log"
  printf '%s\n' "$case_dir"
}

# Live GitHub JSON for the pre-merge verify: open, mergeable, and every check
# green at the given head. Merge itself is `gh pr merge --match-head-commit`.
write_github_live_json() {
  local case_dir=$1 head=$2
  printf '%s\n' "$head" > "$case_dir/github-head"
  cat > "$case_dir/github-view.json" <<JSON
{"state":"OPEN","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","headRefOid":"$head","baseRefName":"main","statusCheckRollup":[{"__typename":"CheckRun","name":"ci","status":"COMPLETED","conclusion":"SUCCESS"}]}
JSON
}

write_github_red_json() {
  local case_dir=$1 head=$2 name=$3
  printf '%s\n' "$head" > "$case_dir/github-head"
  cat > "$case_dir/github-view.json" <<JSON
{"state":"OPEN","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","headRefOid":"$head","baseRefName":"main","statusCheckRollup":[{"__typename":"CheckRun","name":"$name","status":"COMPLETED","conclusion":"FAILURE"}]}
JSON
}

write_github_outcome() {
  local case_dir=$1 state=$2 merged=$3 queued=$4 base=$5
  printf '%s\n' \
    "state=$state" \
    "merged=$merged" \
    "queued=$queued" \
    "base=$base" > "$case_dir/github-outcome"
}

add_gh_mocks() {
  local case_dir=$1 head=$2
  write_github_live_json "$case_dir" "$head"
  cat > "$case_dir/fakebin/gh-axi" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FM_TEST_GH_AXI_LOG"
case "${1:-} ${2:-}" in
  "pr view")
    [ "$#" -eq 5 ] && [ "${4:-}" = --repo ] || exit 2
    printf 'pull_request:\n  number: %s\n  state: %s\n' "$3" "${FM_TEST_GH_MERGE_STATE:-merged}"
    ;;
esac
exit 0
SH
  cat > "$case_dir/fakebin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FM_TEST_GH_LOG"
case "${1:-} ${2:-}" in
  "pr view")
    case " $* " in
      *statusCheckRollup*)
        cat "$FM_TEST_GH_VIEW_JSON"
        exit 0
        ;;
      *headRefOid*)
        cat "$FM_TEST_GH_HEAD"
        exit 0
        ;;
    esac
    ;;
  "pr merge")
    if [ -n "${FM_TEST_GH_MERGE_OUTPUT:-}" ]; then
      printf '%s\n' "$FM_TEST_GH_MERGE_OUTPUT"
    else
      printf 'merged:\n  number: %s\n  status: ok\n' "${3:-}"
    fi
    merge_rc=0
    if [ -f "${FM_TEST_GH_MERGE_RC_FILE:-}" ]; then
      merge_rc=$(cat "$FM_TEST_GH_MERGE_RC_FILE")
    fi
    exit "$merge_rc"
    ;;
  "api graphql")
    cat "$FM_TEST_GH_OUTCOME"
    exit 0
    ;;
esac
exit 0
SH
  chmod +x "$case_dir/fakebin/gh-axi" "$case_dir/fakebin/gh"
}

run_record() {
  local case_dir=$1 rc; shift
  FM_ROOT_OVERRIDE="$ROOT" \
  FM_HOME="$case_dir/home" \
  FM_STATE_OVERRIDE="$case_dir/state" \
  PATH="$case_dir/fakebin:$PATH" \
    "$RECORD" "$@"
  rc=$?
  return "$rc"
}

run_pr_merge() {
  local case_dir=$1 rc; shift
  FM_ROOT_OVERRIDE="$ROOT" \
  FM_HOME="$case_dir/home" \
  FM_STATE_OVERRIDE="$case_dir/state" \
  FM_TEST_GH_AXI_LOG="$case_dir/gh-axi.log" \
  FM_TEST_GH_LOG="$case_dir/gh.log" \
  FM_TEST_GH_OUTCOME="$case_dir/github-outcome" \
  FM_TEST_GH_VIEW_JSON="$case_dir/github-view.json" \
  FM_TEST_GH_HEAD="$case_dir/github-head" \
  FM_TEST_GH_MERGE_RC_FILE="$case_dir/github-merge-rc" \
  FM_TEST_GH_MERGE_OUTPUT="$(cat "$case_dir/github-merge-output" 2>/dev/null || true)" \
  HOME="$case_dir/user-home" \
  PATH="$case_dir/fakebin:$PATH" \
    "$PR_MERGE" "$@"
  rc=$?
  return "$rc"
}

test_create_writes_task_shaped_record() {
  local case_dir rc
  case_dir=$(make_case create-record)

  set +e
  run_record "$case_dir" create fm-direct-create \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e

  expect_code 0 "$rc" "create-record: create should succeed"
  assert_grep 'created: state/fm-direct-create.meta' "$case_dir/stdout" \
    "create-record: create did not report the published record"
  assert_grep 'kind=firstmate-direct' "$case_dir/state/fm-direct-create.meta" \
    "create-record: the record does not name its kind"
  assert_grep 'mode=direct-PR' "$case_dir/state/fm-direct-create.meta" \
    "create-record: the record does not default to direct-PR"
  assert_grep 'yolo=off' "$case_dir/state/fm-direct-create.meta" \
    "create-record: the record does not default to yolo=off"
  [ "$(file_mode "$case_dir/state/fm-direct-create.meta")" = 600 ] \
    || fail "create-record: the record was not published mode 0600"
  pass "create publishes a task-shaped firstmate-direct record"
}

test_create_modes_yolo_and_caller_errors() {
  local case_dir rc
  case_dir=$(make_case create-flags)

  run_record "$case_dir" create fm-direct-flags --mode no-mistakes --yolo \
    >/dev/null 2>&1 \
    || fail "create-flags: --mode and --yolo should be accepted"
  assert_grep 'mode=no-mistakes' "$case_dir/state/fm-direct-flags.meta" \
    "create-flags: the requested mode was not recorded"
  assert_grep 'yolo=on' "$case_dir/state/fm-direct-flags.meta" \
    "create-flags: --yolo was not recorded"

  set +e
  run_record "$case_dir" create fm-direct-bad-mode --mode hyperdrive \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 2 "$rc" "create-flags: an invalid mode must be a caller error"
  [ ! -e "$case_dir/state/fm-direct-bad-mode.meta" ] \
    || fail "create-flags: an invalid mode still published a record"

  set +e
  run_record "$case_dir" create 'not an id!' \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 2 "$rc" "create-flags: an invalid record id must be a caller error"

  set +e
  run_record "$case_dir" create \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 2 "$rc" "create-flags: a missing record id must be a caller error"
  pass "create records mode and yolo and refuses caller errors without publishing"
}

test_create_refuses_to_shadow_existing_state() {
  local case_dir rc
  case_dir=$(make_case create-shadow)

  # A live task's record must never be clobbered or shadowed.
  fm_write_meta "$case_dir/state/task-live.meta" \
    "window=firstmate:fm-task-live" \
    "kind=ship" \
    "mode=direct-PR" \
    "yolo=off"
  set +e
  run_record "$case_dir" create task-live \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "create-shadow: a live task meta must refuse"
  assert_grep 'refusing to shadow existing state' "$case_dir/stderr" \
    "create-shadow: the refusal did not name the shadowed state"
  assert_grep 'window=firstmate:fm-task-live' "$case_dir/state/task-live.meta" \
    "create-shadow: the live task meta was modified"

  # Supervisor state a direct record never carries must refuse just as loudly.
  : > "$case_dir/state/fm-direct-busy.status"
  set +e
  run_record "$case_dir" create fm-direct-busy \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "create-shadow: supervisor state must refuse"

  # A leftover merge-outcome marker must refuse rather than be silently adopted.
  printf '%s\n' fm-pr-poll-merge-notified-v1 github github.com \
    example/repo 7 > "$case_dir/state/fm-direct-stale.pr-poll-merge-notified"
  set +e
  run_record "$case_dir" create fm-direct-stale \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "create-shadow: a leftover outcome marker must refuse"
  pass "create refuses to shadow a live task, supervisor state, or a leftover marker"
}

test_create_needs_no_backlog_row() {
  local case_dir
  # No .tasks.toml and no data/backlog.md anywhere: the record is state-only
  # by design, so this bookkeeping must not pollute the captain's queue.
  case_dir="$TMP_ROOT/create-no-backlog"
  mkdir -p "$case_dir/state" "$case_dir/home" "$case_dir/fakebin"
  run_record "$case_dir" create fm-direct-norow >/dev/null 2>&1 \
    || fail "create-no-backlog: create must not need a backlog row"
  assert_grep 'kind=firstmate-direct' "$case_dir/state/fm-direct-norow.meta" \
    "create-no-backlog: the record was not published"
  pass "create needs no backlog row"
}

test_red_pr_refuses_on_a_direct_record() {
  local case_dir rc head
  head=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  case_dir=$(make_case direct-red)
  mkdir -p "$case_dir/wt"
  add_gh_mocks "$case_dir" "$head"
  write_github_red_json "$case_dir" "$head" lint
  run_record "$case_dir" create fm-direct-red >/dev/null 2>&1 \
    || fail "direct-red: create must succeed before the merge"

  set +e
  run_pr_merge "$case_dir" fm-direct-red https://github.com/example/repo/pull/80 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e

  expect_code 1 "$rc" "direct-red: a red check must refuse on a direct record"
  assert_grep "check 'lint' is not green" "$case_dir/stderr" \
    "direct-red: the red check was not named"
  assert_no_grep 'pr merge' "$case_dir/gh.log" \
    "direct-red: gh pr merge ran on a red PR"
  # The refusal lives in the unchanged merge script: pr= is still recorded
  # before the forge call, exactly as for a task-owned PR.
  assert_grep 'pr=https://github.com/example/repo/pull/80' \
    "$case_dir/state/fm-direct-red.meta" \
    "direct-red: pr= was not recorded before the refusal"
  [ ! -e "$case_dir/state/fm-direct-red.merge-authority" ] \
    || fail "direct-red: a refused merge persisted merge authority"
  pass "the unchanged merge refuses a red PR on a firstmate-direct record"
}

test_failed_forge_merge_refuses_and_retire_refuses_the_unproved_merge() {
  local case_dir rc head
  head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  case_dir=$(make_case direct-merge-fails)
  mkdir -p "$case_dir/wt"
  add_gh_mocks "$case_dir" "$head"
  printf '1\n' > "$case_dir/github-merge-rc"
  printf 'error: pr merge failed\n' > "$case_dir/github-merge-output"
  write_github_outcome "$case_dir" OPEN false false main
  run_record "$case_dir" create fm-direct-fails >/dev/null 2>&1 \
    || fail "direct-merge-fails: create must succeed before the merge"

  set +e
  run_pr_merge "$case_dir" fm-direct-fails https://github.com/example/repo/pull/81 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  [ "$rc" -ne 0 ] \
    || fail "direct-merge-fails: a merge the forge did not accept must refuse"
  [ ! -e "$case_dir/state/fm-direct-fails.merge-authority" ] \
    || fail "direct-merge-fails: an unaccepted merge persisted merge authority"

  # Retiring now would drop the still-armed merge poll, so it must refuse: no
  # merge authority proves the PR was merged here.
  set +e
  run_record "$case_dir" retire fm-direct-fails https://github.com/example/repo/pull/81 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "direct-merge-fails: retire must refuse an unproved merge"
  assert_grep 'no merge authority proves' "$case_dir/stderr" \
    "direct-merge-fails: the retire refusal did not name the missing proof"
  [ -f "$case_dir/state/fm-direct-fails.meta" ] \
    || fail "direct-merge-fails: a refused retire removed the record"
  [ -f "$case_dir/state/fm-direct-fails.pr-poll" ] \
    || fail "direct-merge-fails: a refused retire dropped the armed merge poll"
  pass "an unaccepted merge refuses and retire refuses the unproved merge"
}

test_verified_merge_binds_the_head_and_retire_clears_everything() {
  local case_dir rc head
  head=cccccccccccccccccccccccccccccccccccccccc
  case_dir=$(make_case direct-green)
  mkdir -p "$case_dir/wt"
  add_gh_mocks "$case_dir" "$head"
  run_record "$case_dir" create fm-direct-green >/dev/null 2>&1 \
    || fail "direct-green: create must succeed before the merge"

  set +e
  run_pr_merge "$case_dir" fm-direct-green https://github.com/example/repo/pull/82 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 0 "$rc" "direct-green: a green PR must merge on a direct record"
  # The changed-head protection is the head-bound forge call: the merge must
  # carry the exact head the green check verified, so a push that lands
  # between the read and the merge fails instead of landing unverified code.
  grep -qxF "pr merge 82 --repo example/repo --match-head-commit $head --squash" \
    "$case_dir/gh.log" \
    || fail "direct-green: gh pr merge did not bind the verified head"$'\n'"got: $(cat "$case_dir/gh.log")"
  assert_grep 'pr=https://github.com/example/repo/pull/82' \
    "$case_dir/state/fm-direct-green.meta" \
    "direct-green: pr= was not recorded"
  [ -f "$case_dir/state/fm-direct-green.merge-authority" ] \
    || fail "direct-green: the accepted merge persisted no authority"
  [ -f "$case_dir/state/fm-direct-green.pr-poll-merge-notified" ] \
    || fail "direct-green: the landed merge published no outcome marker"

  # A retire for a different PR identity must refuse: the proof is bound to
  # the canonical PR, not to the record id alone.
  set +e
  run_record "$case_dir" retire fm-direct-green https://github.com/example/repo/pull/999 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "direct-green: retire must refuse a different PR identity"
  [ -f "$case_dir/state/fm-direct-green.meta" ] \
    || fail "direct-green: a refused retire removed the record"

  set +e
  run_record "$case_dir" retire fm-direct-green https://github.com/example/repo/pull/82 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 0 "$rc" "direct-green: retire must succeed after a proved merge"
  assert_grep 'retired: fm-direct-green https://github.com/example/repo/pull/82' \
    "$case_dir/stdout" \
    "direct-green: retire did not report the retired record"
  for artifact in meta merge-authority check.sh check-trust pr-poll \
    pr-poll-registration pr-poll-retirement pr-poll-merge-notified; do
    [ ! -e "$case_dir/state/fm-direct-green.$artifact" ] \
      && [ ! -L "$case_dir/state/fm-direct-green.$artifact" ] \
      || fail "direct-green: retire left residue: fm-direct-green.$artifact"
  done

  set +e
  run_record "$case_dir" retire fm-direct-green https://github.com/example/repo/pull/82 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "direct-green: a second retire must refuse"
  pass "a proved merge binds the verified head and retire clears every artifact"
}

test_retire_never_touches_a_task_owned_record() {
  local case_dir rc
  case_dir=$(make_case retire-not-direct)
  fm_write_meta "$case_dir/state/task-x1.meta" \
    "window=firstmate:fm-task-x1" \
    "kind=ship" \
    "mode=direct-PR" \
    "yolo=off"

  set +e
  run_record "$case_dir" retire task-x1 https://github.com/example/repo/pull/83 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "retire-not-direct: a task-owned record must refuse"
  assert_grep 'not a firstmate-direct record' "$case_dir/stderr" \
    "retire-not-direct: the refusal did not name the kind guard"
  assert_grep 'kind=ship' "$case_dir/state/task-x1.meta" \
    "retire-not-direct: the task record was modified"
  pass "retire refuses a record firstmate-direct did not create"
}

test_retire_refuses_supervisor_state() {
  local case_dir rc
  case_dir=$(make_case retire-foreign)
  run_record "$case_dir" create fm-direct-foreign >/dev/null 2>&1 \
    || fail "retire-foreign: create must succeed"
  # A stray supervisor artifact means this id is no longer a pure direct
  # record, so retire must stop rather than sweep it away.
  : > "$case_dir/state/fm-direct-foreign.status"

  set +e
  run_record "$case_dir" retire fm-direct-foreign https://github.com/example/repo/pull/84 \
    > "$case_dir/stdout" 2> "$case_dir/stderr"
  rc=$?
  set -e
  expect_code 1 "$rc" "retire-foreign: supervisor state must refuse"
  [ -f "$case_dir/state/fm-direct-foreign.meta" ] \
    || fail "retire-foreign: a refused retire removed the record"
  pass "retire refuses when supervisor state shares the record id"
}

test_create_writes_task_shaped_record
test_create_modes_yolo_and_caller_errors
test_create_refuses_to_shadow_existing_state
test_create_needs_no_backlog_row
test_red_pr_refuses_on_a_direct_record
test_failed_forge_merge_refuses_and_retire_refuses_the_unproved_merge
test_verified_merge_binds_the_head_and_retire_clears_everything
test_retire_never_touches_a_task_owned_record
test_retire_refuses_supervisor_state
