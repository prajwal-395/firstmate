#!/usr/bin/env bash
# Regression tests for fm-spawn's pooled-worktree base refresh.
#
# A treehouse pool can return a clean detached worktree whose tracked upstream
# was advanced after the worktree was allocated, or whose HEAD was checked out
# from the wrong remote entirely (e.g. origin instead of fork in a fork fleet).
# These tests drive the real spawn path with a fake terminal, then prove it
# starts the worker from the tracked upstream's tip, warns but proceeds when
# the remote is unreachable or unreadable, corrects a pool whose HEAD came
# from the wrong remote, and refuses a base that is genuinely not descended
# from the tracked upstream.
# pool as-is, or stops when a configured origin is unusable.
# A home that opts in with config/spawn-base-primary-checkout starts a
# local-only ship or a scout from the primary checkout's default branch when it
# strictly contains origin's tip; the opt-in cases below also prove that a
# publishing mode, a named base branch, a diverged or trailing primary, and an
# unclean slot keep the rules above, and that the review starts where the slot
set -u

# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"

TMP_ROOT=$(fm_test_tmproot fm-spawn-pool-base-freshen)

make_case() {
  local name=$1 id=$2 default=${3:-main} case_dir home project origin pool publisher fakebin initial
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  project="$case_dir/project"
  origin="$case_dir/origin.git"
  pool="$case_dir/pool"
  publisher="$case_dir/publisher"
  fakebin=$(make_spawn_fakebin "$case_dir/fake")

  mkdir -p "$home/data/$id" "$home/projects" "$home/state" "$home/config"
  printf 'codex\n' > "$home/config/crew-harness"
  fm_test_spawn_brief "$home" "$id"
  touch "$home/state/.last-watcher-beat"

  git init --quiet -b "$default" "$project"
  printf 'base\n' > "$project/README.md"
  git -C "$project" add README.md
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm initial
  git clone --quiet --bare "$project" "$origin"
  git -C "$project" remote add origin "file://$origin"
  initial=$(git -C "$project" rev-parse HEAD)
  git -C "$project" worktree add --quiet --detach "$pool" "$initial"

  git clone --quiet "file://$origin" "$publisher"
  printf 'must survive a newly spawned branch\n' > "$publisher/advanced-main.txt"
  git -C "$publisher" add advanced-main.txt
  git -C "$publisher" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm advance-main
  git -C "$publisher" push --quiet origin "$default"

  printf '%s\n' "$case_dir|$home|$project|$pool|$fakebin|$initial|$default"
}

read_case_record() {
  IFS='|' read -r CASE_DIR HOME_DIR PROJECT_DIR POOL_DIR FAKEBIN_DIR INITIAL_SHA DEFAULT_BRANCH <<EOF
$1
EOF
}

run_spawn() {
  local id=$1
  shift
  fm_test_run_spawn "$HOME_DIR" "$POOL_DIR" "$FAKEBIN_DIR" \
    "$id" "$PROJECT_DIR" "$@"
}

test_remote_seeded_home_spawns_from_treehouse_pool() {
  local rec id out status lock
  id='pool-remote-seeded-r13'
  rec=$(make_case remote-seeded "$id")
  read_case_record "$rec"
  cat > "$HOME_DIR/.fm-secondmate-parent" <<'REC'
schema=fm-secondmate-parent.v1
route=remote
parent_host=parent-machine
REC

  out=$(run_spawn "$id" --scout)
  status=$?
  expect_code 0 "$status" \
    "a remote-seeded secondmate home should allocate and launch from its Treehouse pool"$'\n'"$out"
  assert_contains "$out" "spawned $id" \
    "the remote-seeded spawn did not report success"
  assert_grep "worktree=$POOL_DIR" "$HOME_DIR/state/$id.meta" \
    "the remote-seeded spawn did not publish its allocated pool worktree"
  lock=$(FM_HOME="$HOME_DIR" bash -c '. "$1"; fm_treehouse_project_lock_path "$2"' _ \
    "$ROOT/bin/fm-wake-lib.sh" "$PROJECT_DIR") \
    || fail "the launched remote-seeded home could not resolve its Treehouse project lock"
  case "$lock" in
    "$HOME_DIR/state/"*) ;;
    *) fail "the remote-seeded spawn anchored its lock outside its local root: $lock" ;;
  esac
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# remote-seeded Treehouse spawn command\n'
    printf '$ FM_HOME=%s bin/fm-spawn.sh %s %s --scout\n%s\nexit=%s\n' \
      "$HOME_DIR" "$id" "$PROJECT_DIR" "$out" "$status"
    printf 'published worktree=%s\nresolved project lock=%s\n' "$POOL_DIR" "$lock"
  fi
  pass "a remote-seeded secondmate home allocates and launches from its Treehouse pool"
}

test_linked_spawning_home_rejects_primary_before_refresh() {
  local rec id out status returned primary spawning before_reflog
  for returned in primary primary-alias spawning scout; do
    id="pool-linked-${returned}-r12"
    rec=$(make_case "linked-$returned" "$id")
    read_case_record "$rec"
    primary=$PROJECT_DIR
    spawning="$CASE_DIR/secondmate"
    git -C "$primary" worktree add --quiet --detach "$spawning" HEAD
    PROJECT_DIR=$spawning
    case "$returned" in
      primary) POOL_DIR=$primary ;;
      primary-alias)
        ln -s "$primary" "$CASE_DIR/primary-alias"
        POOL_DIR="$CASE_DIR/primary-alias"
        ;;
      spawning) POOL_DIR=$spawning ;;
    esac
    before_reflog=$(git -C "$primary" reflog)
    # The assertion concerns identity, not how long an unchanged cwd is polled.
    fm_test_fake_sleep_noop "$FAKEBIN_DIR"

    out=$(run_spawn "$id" --scout)
    status=$?
    if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
      printf '# evidence begin: linked-home spawn, returned=%s\n' "$returned"
      printf '$ bin/fm-spawn.sh %s %s --scout\n%s\nexit=%s\n' "$id" "$PROJECT_DIR" "$out" "$status"
      printf 'primary HEAD before=%s after=%s\n' "$INITIAL_SHA" "$(git -C "$primary" rev-parse HEAD)"
      printf 'primary reflog before:\n%s\nprimary reflog after:\n%s\n' "$before_reflog" "$(git -C "$primary" reflog)"
      if [ -e "$primary/.git/FETCH_HEAD" ]; then
        printf 'FETCH_HEAD:\n'; cat "$primary/.git/FETCH_HEAD"
      else
        printf 'FETCH_HEAD absent\n'
      fi
      if [ -e "$HOME_DIR/state/$id.meta" ]; then
        printf 'saved task metadata:\n'; cat "$HOME_DIR/state/$id.meta"
        printf 'worker HEAD=%s origin/main=%s\n' "$(git -C "$POOL_DIR" rev-parse HEAD)" "$(git -C "$POOL_DIR" rev-parse origin/main)"
      else
        printf 'task metadata absent\n'
      fi
      printf '# evidence end\n'
    fi
    if [ "$returned" = scout ]; then
      expect_code 0 "$status" "a genuine scout copy from a linked home should launch"$'\n'"$out"
      assert_grep "worktree=$POOL_DIR" "$HOME_DIR/state/$id.meta" \
        "spawn did not record the genuine scout copy"
      [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$(git -C "$POOL_DIR" rev-parse origin/main)" ] \
        || fail "spawn did not refresh the genuine scout copy"
    else
      [ "$status" -ne 0 ] || fail "linked spawning home accepted $returned as a disposable copy"
      # None of these is an isolated copy, so the worktree poll never adopts one
      # and the wait runs out instead: the spawning directory fails the poll's
      # own project comparison, and the repository primary (named directly or
      # through a symlink) fails the isolation screen the poll shares with the
      # guard. The refusal names the last path the pane reported.
      assert_contains "$out" "did not enter an isolated worktree" \
        "spawn did not explain its isolation refusal"
      assert_contains "$out" "last seen" "refusal did not name the path the pane reported"
      [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "refused spawn published task metadata"
      [ ! -e "$primary/.git/FETCH_HEAD" ] || fail "refused spawn fetched before proving isolation"
    fi
    [ "$(git -C "$primary" rev-parse HEAD)" = "$INITIAL_SHA" ] \
      || fail "spawn reset the repository primary from a linked home"
    [ "$(git -C "$primary" reflog)" = "$before_reflog" ] \
      || fail "spawn touched the primary reflog from a linked home"
    pass "linked spawning home: $returned preserves the primary before any refresh"
  done
}

test_stale_pool_base_refreshes_before_branching() {
  local rec id out status current branch_head
  id='pool-current-base-r1'
  rec=$(make_case current-base "$id")
  read_case_record "$rec"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should refresh a stale pooled worktree"
  assert_contains "$out" "spawned $id" "spawn did not report success"
  current=$(git -C "$POOL_DIR" rev-parse origin/main)
  branch_head=$(git -C "$POOL_DIR" rev-parse HEAD)
  [ "$branch_head" = "$current" ] || fail "spawn left the pooled worktree on stale history"
  [ "$branch_head" != "$INITIAL_SHA" ] || fail "fixture did not prove origin/main advanced past the pool base"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed spawn: %s\n' "$(printf '%s\n' "$out" | tail -n 1)"
    printf '# observed base: HEAD=%s origin/main=%s advanced-main=%s\n' \
      "$branch_head" "$current" "$(cat "$POOL_DIR/advanced-main.txt")"
  fi

  id='pool-current-base-repeat-r1'
  fm_test_spawn_brief "$HOME_DIR" "$id"
  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "repeating the base refresh should be idempotent"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$current" ] \
    || fail "an idempotent repeat moved the pool away from current origin/main"

  git -C "$POOL_DIR" checkout --quiet -b "fm/$id"
  git -C "$POOL_DIR" diff --exit-code origin/main...HEAD >/dev/null \
    || fail "a branch created after spawn differs from current origin/main"
  assert_grep 'must survive a newly spawned branch' "$POOL_DIR/advanced-main.txt" \
    "the branch created after spawn omitted advanced-main content"
  pass "a stale pooled worktree refreshes to current origin/main before a crew branch is created"
}

test_non_main_default_branch_refreshes_before_branching() {
  local rec id out status current branch_head
  id='pool-current-trunk-r2'
  rec=$(make_case current-trunk "$id" trunk)
  read_case_record "$rec"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should refresh a stale pooled worktree on a non-main default branch"
  current=$(git -C "$POOL_DIR" rev-parse "origin/$DEFAULT_BRANCH")
  branch_head=$(git -C "$POOL_DIR" rev-parse HEAD)
  [ "$branch_head" = "$current" ] || fail "spawn did not refresh to current origin/$DEFAULT_BRANCH"
  [ "$branch_head" != "$INITIAL_SHA" ] || fail "fixture did not prove origin/$DEFAULT_BRANCH advanced past the pool base"
  pass "a stale pooled worktree resolves and refreshes a non-main default branch"
}

make_originless_case() {  # <name> <id>
  local name=$1 id=$2 case_dir home project pool fakebin initial
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  project="$case_dir/project"
  pool="$case_dir/pool"
  fakebin=$(make_spawn_fakebin "$case_dir/fake")

  mkdir -p "$home/data/$id" "$home/projects" "$home/state" "$home/config"
  printf 'codex\n' > "$home/config/crew-harness"
  fm_test_spawn_brief "$home" "$id"
  touch "$home/state/.last-watcher-beat"

  git init --quiet -b main "$project"
  printf 'base\n' > "$project/README.md"
  git -C "$project" add README.md
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm initial
  initial=$(git -C "$project" rev-parse HEAD)
  git -C "$project" worktree add --quiet --detach "$pool" "$initial"

  printf '%s\n' "$case_dir|$home|$project|$pool|$fakebin|$initial|main"
}

test_originless_pool_launches_without_a_freshness_fetch() {
  local rec id out status before
  id='pool-originless-r6'
  rec=$(make_originless_case originless "$id")
  read_case_record "$rec"
  ! git -C "$POOL_DIR" remote get-url origin >/dev/null 2>&1 \
    || fail "fixture unexpectedly configured an origin remote"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should launch a local-only pooled worktree with no origin"$'\n'"$out"
  assert_contains "$out" "spawned $id" "spawn did not report success for the origin-less pool"
  assert_not_contains "$out" "could not fetch origin" \
    "spawn attempted a freshness fetch against a nonexistent origin"
  [ ! -e "$POOL_DIR/.git/FETCH_HEAD" ] || fail "spawn fetched against a pooled worktree with no origin"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD on an origin-less pooled worktree that had nothing to refresh against"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed origin-less launch: %s\n' "$(printf '%s\n' "$out" | tail -n 1)"
  fi
  pass "an origin-less pooled worktree launches as-is, skipping the freshness gate"
}

test_originless_dirty_pool_refuses_without_discarding_work() {
  local rec id out status before
  id='pool-originless-dirty-r1'
  rec=$(make_originless_case originless-dirty "$id")
  read_case_record "$rec"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)
  printf 'keep this local work\n' > "$POOL_DIR/uncommitted.txt"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn succeeded despite a dirty origin-less pooled worktree"
  assert_contains "$out" "is not clean" \
    "spawn did not clearly refuse a dirty origin-less pooled worktree"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD while refusing a dirty origin-less pooled worktree"
  assert_grep 'keep this local work' "$POOL_DIR/uncommitted.txt" \
    "spawn discarded local work from an origin-less pool"
  pass "a dirty origin-less pooled worktree is refused without discarding its local work"
}

test_origin_config_without_url_warns_and_proceeds() {
  local rec id out status before
  id='pool-origin-without-url-r1'
  rec=$(make_originless_case origin-without-url "$id")
  read_case_record "$rec"
  git -C "$POOL_DIR" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  # An unusable origin is unknown, not wrong: the spawn warns about the failed
  # fetch and proceeds on local refs rather than refusing a clean copy.
  expect_code 0 "$status" "spawn should warn and proceed when the origin URL is unusable"$'\n'"$out"
  assert_contains "$out" "could not fetch 'origin'" \
    "spawn did not warn about the unusable origin"
  assert_contains "$out" "spawned $id" "spawn did not report success"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD after finding an unusable origin configuration"
  pass "an origin configuration without a URL warns and proceeds on local refs"
}

test_empty_origin_config_section_warns_and_proceeds() {
  local rec id out status before config
  id='pool-empty-origin-section-r1'
  rec=$(make_originless_case empty-origin-section "$id")
  read_case_record "$rec"
  config=$(git -C "$POOL_DIR" rev-parse --path-format=absolute --git-path config)
  printf '\n[remote "origin"]\n' >> "$config"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  # Same contract as an origin without a URL: the failed fetch warns and the
  # clean copy proceeds on local refs.
  expect_code 0 "$status" "spawn should warn and proceed when the origin section is empty"$'\n'"$out"
  assert_contains "$out" "could not fetch 'origin'" \
    "spawn did not warn about the empty origin configuration section"
  assert_contains "$out" "spawned $id" "spawn did not report success"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD after finding an empty origin configuration section"
  pass "an empty origin configuration section warns and proceeds on local refs"
}

test_empty_only_included_origin_config_section_launches_pool() {
  local rec id out status before config included
  id='pool-empty-only-included-origin-section-r1'
  rec=$(make_originless_case empty-only-included-origin-section "$id")
  read_case_record "$rec"
  config=$(git -C "$POOL_DIR" rev-parse --path-format=absolute --git-path config)
  included=$(dirname "$config")/empty-origin.inc
  printf '[remote "origin"]\n' > "$included"
  git -C "$POOL_DIR" config include.path "$(basename "$included")"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should proceed when an included empty origin section is not enumerable"$'\n'"$out"
  assert_contains "$out" "spawned $id" "spawn did not report success for the undetectable included section"
  assert_not_contains "$out" "could not fetch origin" \
    "spawn treated an undetectable included empty section as a configured origin"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD despite treating the included empty section as origin-less"
  pass "an empty-only included origin section documents the accepted detection boundary"
}

test_inactive_conditional_origin_include_launches_pool() {
  local rec id out status before config included
  id='pool-inactive-origin-include-r1'
  rec=$(make_originless_case inactive-origin-include "$id")
  read_case_record "$rec"
  config=$(git -C "$POOL_DIR" rev-parse --path-format=absolute --git-path config)
  included=$(dirname "$config")/inactive-origin.inc
  printf '[fm-test]\n\tmarker = true\n[remote "origin"]\n' > "$included"
  git -C "$POOL_DIR" config 'includeIf.gitdir:/never/matches/this/worktree/.path' "$included"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should ignore an inactive conditional origin include"$'\n'"$out"
  assert_contains "$out" "spawned $id" "spawn did not report success with an inactive origin include"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD despite having no effective origin"
  pass "an inactive conditional origin include leaves the pooled worktree origin-less"
}

test_unreachable_origin_warns_but_proceeds_with_local_refs() {
  local rec id out status before after
  id='pool-unreachable-origin-r2'
  rec=$(make_case unreachable-origin "$id")
  read_case_record "$rec"
  git -C "$POOL_DIR" remote set-url origin "file://$CASE_DIR/missing-origin.git"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  # An unreachable remote is unknown, not wrong: the spawn warns but proceeds
  # when the locally available tracked upstream ref still passes the ancestry check.
  expect_code 0 "$status" "spawn should warn but proceed when origin is unreachable and local refs are OK"$'\n'"$out"
  assert_contains "$out" "could not fetch 'origin'" \
    "spawn did not warn about the unreachable origin"
  assert_contains "$out" "spawned $id" "spawn did not report success"
  after=$(git -C "$POOL_DIR" rev-parse HEAD)
  # HEAD stays at what the local origin/main ref resolves to - the initial commit -
  # since the fetch could not update it to the remote's actual tip.
  [ "$after" = "$before" ] || fail "spawn moved the pooled worktree despite a failed fetch"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed unreachable-origin warning: %s\n' "$(printf '%s\n' "$out" | tail -n 1)"
  fi
  pass "an unreachable origin warns but proceeds when local refs pass the tracked-base assertion"
}

test_direct_pr_and_scout_refresh_before_launch() {
  local rec id out status contract current
  for contract in direct-pr scout; do
    id="pool-${contract}-r3"
    rec=$(make_case "$contract" "$id")
    read_case_record "$rec"
    if [ "$contract" = scout ]; then
      out=$(run_spawn "$id" --scout)
    else
      out=$(run_spawn "$id" --mode direct-PR --yolo off)
    fi
    status=$?
    expect_code 0 "$status" "$contract spawn should refresh a stale pooled worktree"
    current=$(git -C "$POOL_DIR" rev-parse origin/main)
    [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$current" ] \
      || fail "$contract spawn did not start at current origin/main"
    assert_grep 'must survive a newly spawned branch' "$POOL_DIR/advanced-main.txt" \
      "$contract spawn omitted advanced-main content"
    if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
      printf '# observed %s spawn: %s\n' "$contract" "$(printf '%s\n' "$out" | tail -n 1)"
    fi
  done
  pass "direct-PR ships and scouts both refresh stale pooled worktrees before launch"
}

test_dirty_pool_refuses_without_discarding_work() {
  local rec id out status before
  id='pool-dirty-refusal-r4'
  rec=$(make_case dirty-refusal "$id")
  read_case_record "$rec"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)
  printf 'keep this local work\n' > "$POOL_DIR/uncommitted.txt"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn succeeded despite a dirty pooled worktree"
  assert_contains "$out" "is not clean" "spawn did not clearly refuse a dirty pooled worktree"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD while refusing a dirty pooled worktree"
  assert_grep 'keep this local work' "$POOL_DIR/uncommitted.txt" \
    "spawn discarded uncommitted work while refusing the pool"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed dirty refusal: %s; preserved=%s\n' \
      "$(printf '%s\n' "$out" | tail -n 1)" "$(cat "$POOL_DIR/uncommitted.txt")"
  fi
  pass "a dirty pooled worktree is refused without discarding its local work"
}

test_untracked_default_falls_back_to_origin() {
  local rec id out status current branch_head
  id='pool-untracked-fallback-r5'
  rec=$(make_case untracked-fallback "$id")
  read_case_record "$rec"
  git -C "$PROJECT_DIR" branch --unset-upstream "$DEFAULT_BRANCH" 2>/dev/null || true

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should fall back to origin when tracking is absent"$'\n'"$out"
  assert_contains "$out" "spawned $id" "spawn did not report success"
  current=$(git -C "$POOL_DIR" rev-parse origin/main)
  branch_head=$(git -C "$POOL_DIR" rev-parse HEAD)
  [ "$branch_head" = "$current" ] || fail "spawn did not reset to origin/main when tracking was absent"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed untracked-fallback: HEAD=%s origin/main=%s (correctly fell back to origin)\n' "$branch_head" "$current"
  fi
  pass "an untracked default branch falls back to origin for the pooled worktree"
}

# A slot left on a stale submodule pin is the field failure this diagnosis exists
# for: a refresh moved the superproject and left the submodule behind, so the
# refusal fires a spawn later, on a slot whose own `git status` looks clean to the
# operator. Nothing here is converged - the gate only has to say why. The fixture
# only builds the repositories; the residue itself is produced by a real spawn, so
# these tests cover the reset that actually strands the submodule.
make_submodule_case() {  # <name> <id>
  local name=$1 id=$2 case_dir home project origin pool publisher fakebin sub subpin1 subpin2 advanced
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  project="$case_dir/project"
  origin="$case_dir/origin.git"
  pool="$case_dir/pool"
  publisher="$case_dir/publisher"
  sub="$case_dir/sub-origin"
  fakebin=$(make_spawn_fakebin "$case_dir/fake")

  mkdir -p "$home/data/$id" "$home/projects" "$home/state" "$home/config"
  printf 'codex\n' > "$home/config/crew-harness"
  fm_test_spawn_brief "$home" "$id"
  touch "$home/state/.last-watcher-beat"

  git init --quiet -b main "$sub"
  printf 'pin one\n' > "$sub/lib.txt"
  git -C "$sub" add lib.txt
  git -C "$sub" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm sub-one
  subpin1=$(git -C "$sub" rev-parse HEAD)
  printf 'pin two\n' > "$sub/lib.txt"
  git -C "$sub" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qam sub-two
  subpin2=$(git -C "$sub" rev-parse HEAD)
  git -C "$sub" checkout --quiet "$subpin1"

  git init --quiet -b main "$project"
  printf 'base\n' > "$project/README.md"
  git -C "$project" add README.md
  git -C "$project" -c protocol.file.allow=always -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' \
    submodule --quiet add "file://$sub" ui
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm initial
  git clone --quiet --bare "$project" "$origin"
  git -C "$project" remote add origin "file://$origin"
  git -C "$project" worktree add --quiet --detach "$pool" HEAD
  git -C "$pool" -c protocol.file.allow=always submodule --quiet update --init

  # Advance origin and move the submodule pin, exactly as the field incident did.
  git clone --quiet "file://$origin" "$publisher"
  git -C "$publisher" -c protocol.file.allow=always submodule --quiet update --init
  git -C "$publisher/ui" checkout --quiet "$subpin2"
  git -C "$publisher" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qam advance-pin
  git -C "$publisher" push --quiet origin main
  advanced=$(git -C "$publisher" rev-parse HEAD)

  printf '%s\n' "$case_dir|$home|$project|$pool|$fakebin|$subpin1|$subpin2|$advanced"
}

read_submodule_case() {
  IFS='|' read -r CASE_DIR HOME_DIR PROJECT_DIR POOL_DIR FAKEBIN_DIR SUBPIN1 SUBPIN2 ADVANCED_SHA <<EOF
$1
EOF
}

# The first of two consecutive spawns: it succeeds, resets the superproject onto
# the base that moved the pin, and leaves the submodule checkout on the pin the
# old base recorded. That reset is what strands the slot, so every case below
# starts from residue this code path actually produced rather than a hand-built one.
strand_submodule_pin_via_spawn() {  # <seed-id>
  local id=$1 out status
  fm_test_spawn_brief "$HOME_DIR" "$id"
  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "the spawn that moves the submodule pin should succeed"
  assert_contains "$out" "spawned $id" "the spawn that moves the submodule pin did not report success"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$ADVANCED_SHA" ] \
    || fail "the first spawn did not move the pooled base across the moved submodule pin"
  [ "$(git -C "$POOL_DIR/ui" rev-parse HEAD)" = "$SUBPIN1" ] \
    || fail "the first spawn did not strand the submodule on the pin the old base recorded"
}

test_stale_submodule_pin_explains_itself() {
  local rec id out status before before_sub
  id='pool-stale-pin-r7'
  rec=$(make_submodule_case stale-pin "$id")
  read_submodule_case "$rec"
  strand_submodule_pin_via_spawn 'pool-stale-pin-seed-r7'
  git -C "$POOL_DIR" remote remove origin
  before=$(git -C "$POOL_DIR" rev-parse HEAD)
  before_sub=$(git -C "$POOL_DIR/ui" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "the second spawn launched from a slot carrying a stale submodule pin"
  assert_contains "$out" "stale submodule checkout" \
    "refusal did not name the cause as a stale submodule checkout"
  assert_contains "$out" "submodule 'ui'" "refusal did not name the submodule"
  assert_contains "$out" "$SUBPIN1" "refusal did not report the pin the slot actually has"
  assert_contains "$out" "$SUBPIN2" "refusal did not report the pin the base records"
  # No remedy is printed on purpose: the containment check reads local refs only,
  # so a stale remote-tracking ref can make an unpushed commit look contained, and
  # a checkout command on that judgement could cost the operator a commit.
  assert_not_contains "$out" "submodule update --checkout" \
    "refusal printed a remedy command the containment check cannot stand behind"
  assert_not_contains "$out" "refusing to discard uncommitted work" \
    "a stale pin was misreported as uncommitted work"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD while refusing a stale submodule pin"
  [ "$(git -C "$POOL_DIR/ui" rev-parse HEAD)" = "$before_sub" ] \
    || fail "spawn converged the submodule; this gate must never touch the slot"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed stale-pin refusal: %s\n' "$(printf '%s\n' "$out" | grep 'submodule' | head -n 1)"
  fi
  pass "an origin-less pool with a stale submodule pin refuses while naming both pins and no remedy"
}

test_unpushed_submodule_commit_is_still_uncommitted_work() {
  local rec id out status unpushed before before_sub
  id='pool-sub-unpushed-r10'
  rec=$(make_submodule_case sub-unpushed "$id")
  read_submodule_case "$rec"
  strand_submodule_pin_via_spawn 'pool-sub-unpushed-seed-r10'
  # A commit made inside the submodule and never pushed leaves the submodule work
  # tree clean and the pins different - the same two facts a stale pin shows. Any
  # checkout of the recorded pin would move HEAD off this commit and leave it
  # unreferenced, so this case must keep the conservative refusal.
  printf 'unlanded submodule work\n' > "$POOL_DIR/ui/unlanded.txt"
  git -C "$POOL_DIR/ui" add unlanded.txt
  git -C "$POOL_DIR/ui" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' \
    commit -qm unlanded-submodule-work
  unpushed=$(git -C "$POOL_DIR/ui" rev-parse HEAD)
  [ -z "$(git -C "$POOL_DIR/ui" status --porcelain)" ] \
    || fail "fixture did not leave the submodule work tree clean"
  [ "$unpushed" != "$(git -C "$POOL_DIR" rev-parse "HEAD:ui")" ] \
    || fail "fixture did not leave the recorded pin different from what is checked out"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)
  before_sub=$unpushed

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn launched from a slot holding an unpushed submodule commit"
  assert_contains "$out" "refusing to discard uncommitted work" \
    "an unpushed submodule commit was not refused as uncommitted work"
  assert_not_contains "$out" "stale submodule checkout" \
    "an unpushed submodule commit was misreported as a stale pin"
  assert_not_contains "$out" "is checked out at" \
    "an unpushed submodule commit still drew the stale-pin diagnosis"
  [ "$(git -C "$POOL_DIR/ui" rev-parse HEAD)" = "$before_sub" ] \
    || fail "spawn moved the submodule off its unpushed commit"
  git -C "$POOL_DIR/ui" cat-file -e "$unpushed^{commit}" \
    || fail "the unpushed submodule commit did not survive the refusal"
  assert_grep 'unlanded submodule work' "$POOL_DIR/ui/unlanded.txt" \
    "spawn discarded the unpushed submodule work while refusing the pool"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved HEAD while refusing a slot holding an unpushed submodule commit"
  pass "an unpushed submodule commit keeps the uncommitted-work refusal and survives it"
}

test_work_inside_submodule_is_still_uncommitted_work() {
  local rec id out status
  id='pool-sub-work-r8'
  rec=$(make_submodule_case sub-work "$id")
  read_submodule_case "$rec"
  strand_submodule_pin_via_spawn 'pool-sub-work-seed-r8'
  # Put the submodule back on the pin the base records, so the ONLY deviation is
  # real work inside it. This must never be softened into a stale-pin diagnosis.
  git -C "$POOL_DIR/ui" checkout --quiet "$SUBPIN2"
  printf 'work that must survive\n' > "$POOL_DIR/ui/keep-me.txt"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn launched from a slot holding work inside a submodule"
  assert_contains "$out" "refusing to discard uncommitted work" \
    "work inside a submodule was not refused as uncommitted work"
  assert_not_contains "$out" "stale submodule checkout" \
    "real work inside a submodule was misreported as a stale pin"
  assert_grep 'work that must survive' "$POOL_DIR/ui/keep-me.txt" \
    "spawn discarded work inside the submodule while refusing the pool"
  pass "work inside a submodule is still refused as uncommitted work, not called stale"
}

test_stale_pin_carrying_real_work_is_not_called_stale() {
  local rec id out status
  id='pool-sub-both-r9'
  rec=$(make_submodule_case sub-both "$id")
  read_submodule_case "$rec"
  strand_submodule_pin_via_spawn 'pool-sub-both-seed-r9'
  # Stale pin AND real work inside it: calling this merely stale would be wrong, so
  # the refusal must stay the conservative one.
  printf 'work that must survive\n' > "$POOL_DIR/ui/keep-me.txt"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn launched from a slot with a stale pin and work inside it"
  assert_contains "$out" "refusing to discard uncommitted work" \
    "a stale pin carrying real work was not refused as uncommitted work"
  assert_not_contains "$out" "stale submodule checkout" \
    "a submodule holding real work was reported as merely stale"
  assert_grep 'work that must survive' "$POOL_DIR/ui/keep-me.txt" \
    "spawn discarded work inside the submodule while refusing the pool"
  pass "a stale pin carrying real work is refused conservatively, never called stale"
}

test_stale_pin_beside_other_dirt_reports_one_verdict() {
  local rec id out status
  id='pool-sub-mixed-r11'
  rec=$(make_submodule_case sub-mixed "$id")
  read_submodule_case "$rec"
  strand_submodule_pin_via_spawn 'pool-sub-mixed-seed-r11'
  # Git sorts status paths, so the stale 'ui' entry is scanned before this file.
  # The conservative verdict must not arrive contradicted by a stale-pin line.
  printf 'notes the operator still wants\n' > "$POOL_DIR/zz-notes.txt"

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn launched from a slot with a stale pin beside an untracked file"
  assert_contains "$out" "refusing to discard uncommitted work" \
    "a stale pin beside an untracked file was not refused as uncommitted work"
  assert_not_contains "$out" "stale submodule checkout" \
    "a slot carrying more than a stale pin was reported as merely stale"
  assert_not_contains "$out" "is checked out at" \
    "the stale-pin diagnosis was printed alongside the conservative refusal"
  assert_grep 'notes the operator still wants' "$POOL_DIR/zz-notes.txt" \
    "spawn discarded the untracked file while refusing the pool"
  pass "a stale pin beside other dirt yields the conservative refusal alone, with no stale-pin line"
}

# Re-lay a case's pooled worktree as a managed Treehouse slot: <pool>/<slot>/<repo>
# with the pool's state file beside the slot, which is the shape fm-spawn claims
# for its task. Rewrites POOL_DIR to the relocated checkout.
lay_out_as_pool_slot() {
  local slot_root="$CASE_DIR/slots"
  mkdir -p "$slot_root/1"
  git -C "$PROJECT_DIR" worktree move "$POOL_DIR" "$slot_root/1/project"
  printf '{"worktrees":[{"name":"1","path":"%s"}]}\n' "$slot_root/1/project" \
    > "$slot_root/treehouse-state.json"
  POOL_DIR="$slot_root/1/project"
  SLOT_CLAIM="$slot_root/1/.fm-slot-owner"
}

# The spawn side of the slot-owner claim that bin/fm-teardown.sh later reads:
# a launched task's claim names it, a slot that cannot be claimed refuses before
# anything is published, and an abort while the allocation lock is still held
# leaves no claim naming a task with no record.
test_pool_slot_claim_follows_the_spawn_outcome() {
  local rec id out status before

  id='pool-slot-claim-r1'
  rec=$(make_case slot-claim "$id")
  read_case_record "$rec"
  lay_out_as_pool_slot
  out=$(run_spawn "$id" --scout)
  status=$?
  expect_code 0 "$status" "spawn from a Treehouse slot should launch"$'\n'"$out"
  assert_grep "worktree=$POOL_DIR" "$HOME_DIR/state/$id.meta" \
    "spawn did not publish the relocated slot as its worktree"
  [ -f "$SLOT_CLAIM" ] || fail "spawn left its Treehouse slot unclaimed: $out"
  grep -Fxq -- "task=$id" "$SLOT_CLAIM" \
    || fail "the slot claim does not name the spawned task: $(cat "$SLOT_CLAIM")"
  grep -Fxq -- "home=$HOME_DIR" "$SLOT_CLAIM" \
    || fail "the slot claim does not name the spawning home: $(cat "$SLOT_CLAIM")"

  id='pool-slot-unclaimable-r1'
  rec=$(make_case slot-unclaimable "$id")
  read_case_record "$rec"
  lay_out_as_pool_slot
  mkdir -p "$SLOT_CLAIM"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)
  out=$(run_spawn "$id" --scout)
  status=$?
  [ "$status" -ne 0 ] || fail "spawn launched a worker on a slot it could not claim"
  assert_contains "$out" "could not claim Treehouse pool slot" \
    "spawn did not name the unclaimable slot as the reason"
  assert_contains "$out" "inspect window" \
    "a non-polytoken unclaimable-slot refusal must keep its window pointer"
  assert_not_contains "$out" "is closed on exit" \
    "a non-polytoken refusal must not claim its window was closed"
  [ -d "$SLOT_CLAIM" ] || fail "spawn replaced the directory blocking its slot claim"
  [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "spawn published a record for an unclaimable slot"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "spawn moved the slot's HEAD after failing to claim it"

  id='pool-slot-claim-aborted-r1'
  rec=$(make_originless_case slot-claim-aborted "$id")
  read_case_record "$rec"
  lay_out_as_pool_slot
  git -C "$POOL_DIR" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  # An unusable origin is unknown, not wrong: the spawn warns about the failed
  # fetch and proceeds on local refs, so the claim follows the successful
  # outcome and names the launched task.
  expect_code 0 "$status" "spawn should warn and proceed when the slot's origin is unusable"$'\n'"$out"
  assert_contains "$out" "could not fetch 'origin'" \
    "the spawn did not warn on its unusable origin"
  assert_grep "worktree=$POOL_DIR" "$HOME_DIR/state/$id.meta" \
    "the warned spawn did not publish its record"
  [ -f "$SLOT_CLAIM" ] || fail "the warned spawn left its Treehouse slot unclaimed: $out"
  grep -Fxq -- "task=$id" "$SLOT_CLAIM" \
    || fail "the slot claim does not name the spawned task: $(cat "$SLOT_CLAIM")"
  pass "a Treehouse slot claim names the launched task and refuses when unclaimable"
}

# --- fork-fleet divergence regression (the defect that produced fork PR 27) ---
# Reproduce the exact scenario: a pool worktree whose HEAD is from origin
# (upstream kunchenguid/firstmate) while the default branch tracks fork/main
# (prajwal-395/firstmate). The two diverge because the sync was squash-merged,
# so origin's commits are not ancestors of the fork. A distance check cannot
# distinguish this from merely stale; only the positive ancestry assertion does.

make_fork_case() {
  local name=$1 id=$2 case_dir home project upstream_bare fork_bare pool publisher_upstream publisher_fork fakebin initial
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  project="$case_dir/project"
  upstream_bare="$case_dir/upstream.git"
  fork_bare="$case_dir/fork.git"
  pool="$case_dir/pool"
  publisher_upstream="$case_dir/publisher-upstream"
  publisher_fork="$case_dir/publisher-fork"
  fakebin=$(make_spawn_fakebin "$case_dir/fake")

  mkdir -p "$home/data/$id" "$home/projects" "$home/state" "$home/config"
  printf 'codex\n' > "$home/config/crew-harness"
  fm_test_spawn_brief "$home" "$id"
  touch "$home/state/.last-watcher-beat"

  # Create the project repo with a shared initial commit.
  git init --quiet -b main "$project"
  printf 'shared base\n' > "$project/README.md"
  git -C "$project" add README.md
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'shared base'
  initial=$(git -C "$project" rev-parse HEAD)

  # Create two bare remotes: upstream (origin) and fork.
  git clone --quiet --bare "$project" "$upstream_bare"
  git clone --quiet --bare "$project" "$fork_bare"

  # Add both remotes to the project repo.
  git -C "$project" remote add origin "file://$upstream_bare"
  git -C "$project" remote add fork "file://$fork_bare"
  git -C "$project" fetch --quiet origin
  git -C "$project" fetch --quiet fork

  # The default branch tracks fork/main, not origin/main - the fleet's source of truth.
  git -C "$project" branch --set-upstream-to=fork/main main >/dev/null

  # Diverge the two remotes: push a different commit to each.
  # This simulates a squash-merge sync where upstream's commits are NOT ancestors of fork.
  git clone --quiet "file://$upstream_bare" "$publisher_upstream"
  printf 'upstream-only change\n' > "$publisher_upstream/upstream-only.txt"
  git -C "$publisher_upstream" add upstream-only.txt
  git -C "$publisher_upstream" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'upstream advance'
  git -C "$publisher_upstream" push --quiet origin main

  git clone --quiet "file://$fork_bare" "$publisher_fork"
  printf 'fork-only change (squash-merged content)\n' > "$publisher_fork/fork-only.txt"
  git -C "$publisher_fork" add fork-only.txt
  git -C "$publisher_fork" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'fork advance (squash merge)'
  git -C "$publisher_fork" push --quiet origin main

  # Re-fetch both remotes so fork/main and origin/main refs are current.
  git -C "$project" fetch --quiet origin
  git -C "$project" fetch --quiet fork
  # Allocate a pool worktree checked out from the WRONG remote (origin/main).
  # This is the exact defect: the treehouse pool fetched origin and checked out its tip.
  git -C "$project" worktree add --quiet --detach "$pool" origin/main

  printf '%s\n' "$case_dir|$home|$project|$pool|$fakebin|$initial|main"
}

# The correct test: verify that the fixed code resets a wrong-base pool to the correct fork/main.
test_wrong_remote_base_corrected_by_tracking() {
  local rec id out status before fork_tip origin_tip after
  id='pool-wrong-remote-fix-r6'
  rec=$(make_fork_case wrong-remote-fix "$id")
  read_case_record "$rec"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)
  origin_tip=$(git -C "$PROJECT_DIR" rev-parse origin/main)
  fork_tip=$(git -C "$PROJECT_DIR" rev-parse fork/main)

  # Preconditions: HEAD is on origin/main (the wrong remote) and the two tips diverge.
  [ "$before" = "$origin_tip" ] || fail "fixture: pool HEAD is not at origin/main"
  [ "$origin_tip" != "$fork_tip" ] || fail "fixture: origin/main and fork/main did not diverge"
  if git -C "$POOL_DIR" merge-base --is-ancestor "$fork_tip" "$origin_tip"; then
    fail "fixture: fork/main is an ancestor of origin/main; divergence was not constructed"
  fi

  out=$(run_spawn "$id" --mode direct-PR --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should correct a pool checked out from the wrong remote by using the tracked upstream"
  after=$(git -C "$POOL_DIR" rev-parse HEAD)
  [ "$after" = "$fork_tip" ] || fail "spawn did not reset the pool to fork/main; HEAD=$after, expected=$fork_tip"
  [ "$after" != "$origin_tip" ] || fail "spawn left the pool at origin/main instead of resetting to fork/main"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed wrong-remote fix: before=%s (origin/main) after=%s (fork/main)\n' "$before" "$after"
  fi
  pass "a pool checked out from the wrong remote is corrected by tracking-based refresh"
}

# --- tracked-base assertion tests -------------------------------------------
# These test the independent assert_spawn_tracked_base post-condition.

# Fork-based project: origin is NOT the push target, default branch tracks fork/main.
# Must NOT refuse. Uses the same fork fixture as the divergence regression above.
test_fork_project_tracked_base_assertion_passes() {
  local rec id out status fork_tip after
  id='fork-assert-ok-r7'
  rec=$(make_fork_case fork-assert-ok "$id")
  read_case_record "$rec"
  fork_tip=$(git -C "$PROJECT_DIR" rev-parse fork/main)

  out=$(run_spawn "$id" --mode direct-PR --yolo off)
  status=$?
  expect_code 0 "$status" "fork project spawn should succeed - tracked-base assertion must not refuse"
  assert_contains "$out" "spawned $id" "fork project spawn did not report success"
  after=$(git -C "$POOL_DIR" rev-parse HEAD)
  [ "$after" = "$fork_tip" ] || fail "fork project spawn did not land on fork/main"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed fork-assert-ok: HEAD=%s fork/main=%s (correctly tracked fork, not origin)\n' "$after" "$fork_tip"
  fi
  pass "a fork-based project passes the tracked-base assertion using fork/main, not origin"
}

# Symlinked clone: the project directory is a symlink into the captain's real
# working directory. Must NOT refuse. This is the real layout for most of the
# captain's fleet.
test_symlinked_clone_tracked_base_assertion_passes() {
  local rec id out status current case_dir symlink
  id='symlink-assert-ok-r8'
  rec=$(make_case symlink-assert-base "$id")
  read_case_record "$rec"

  # Create a symlink to the project directory, simulating the captain's layout.
  case_dir=$(dirname "$HOME_DIR")
  symlink="$case_dir/project-symlink"
  ln -s "$PROJECT_DIR" "$symlink"

  # Run spawn through the symlink path.
  out=$(fm_test_run_spawn "$HOME_DIR" "$POOL_DIR" "$FAKEBIN_DIR" \
    "$id" "$symlink" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "symlinked clone spawn should succeed"$'\n'"$out"
  assert_contains "$out" "spawned $id" "symlinked clone spawn did not report success"
  current=$(git -C "$POOL_DIR" rev-parse origin/main)
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$current" ] \
    || fail "symlinked clone spawn did not start at current origin/main"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed symlink-assert: symlink=%s -> project=%s HEAD=%s\n' "$symlink" "$PROJECT_DIR" "$(git -C "$POOL_DIR" rev-parse HEAD)"
  fi
  pass "a symlinked clone passes the tracked-base assertion"
}

# Completely unreadable upstream: both the remote is unreachable AND the tracked
# upstream ref does not exist locally. The assertion must warn and proceed
# (unreadable = unknown, not wrong).
test_completely_unreadable_upstream_warns_and_proceeds() {
  local rec id out status before after
  id='pool-unreadable-all-r9'
  rec=$(make_case unreadable-all "$id")
  read_case_record "$rec"
  # Point origin at a nonexistent path AND remove the local tracking ref.
  git -C "$POOL_DIR" remote set-url origin "file://$CASE_DIR/missing-origin.git"
  git -C "$PROJECT_DIR" branch --unset-upstream "$DEFAULT_BRANCH" 2>/dev/null || true
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "spawn should warn and proceed when upstream is completely unreadable"
  assert_contains "$out" "spawned $id" "spawn did not report success despite unreadable upstream"
  after=$(git -C "$POOL_DIR" rev-parse HEAD)
  [ "$after" = "$before" ] || fail "spawn moved the pooled worktree despite a completely unreadable upstream"
  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed completely-unreadable: status=%s out=%s\n' "$status" "$(printf '%s\n' "$out" | tail -n 2)"
  fi
  pass "a completely unreadable upstream warns and proceeds without refusing"
}

# Wrong-base refusal, against the SHIPPED assertion function rather than a
# reimplementation of its check: source the real default_branch helper and
# evaluate the real assert_spawn_tracked_base body, then run it over a pool
# worktree whose HEAD is genuinely not descended from the tracked upstream.
# The full spawn path's freshen resets the worktree before the assertion runs,
# so only the function itself can be exercised on a wrong base - and it must
# REFUSE while leaving the copy untouched.
test_wrong_base_assertion_refuses_and_preserves() {
  local case_dir project origin fork_bare pool publisher_fork
  local before fork_tip wrong_sha after out rc
  case_dir="$TMP_ROOT/wrong-base-refuse"
  project="$case_dir/project"
  origin="$case_dir/origin.git"
  fork_bare="$case_dir/fork.git"
  pool="$case_dir/pool"

  # Create a project with origin and fork remotes.
  git init --quiet -b main "$project"
  printf 'base\n' > "$project/README.md"
  git -C "$project" add README.md
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'initial'

  git clone --quiet --bare "$project" "$origin"
  git clone --quiet --bare "$project" "$fork_bare"
  git -C "$project" remote add origin "file://$origin"
  git -C "$project" remote add fork "file://$fork_bare"
  git -C "$project" fetch --quiet origin
  git -C "$project" fetch --quiet fork
  git -C "$project" branch --set-upstream-to=fork/main main >/dev/null

  # Advance fork with a legitimate commit.
  publisher_fork="$case_dir/publisher-fork"
  git clone --quiet "file://$fork_bare" "$publisher_fork"
  printf 'fork advance\n' > "$publisher_fork/fork-file.txt"
  git -C "$publisher_fork" add fork-file.txt
  git -C "$publisher_fork" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'fork advance'
  git -C "$publisher_fork" push --quiet origin main
  git -C "$project" fetch --quiet fork

  fork_tip=$(git -C "$project" rev-parse fork/main)

  # Create a pool worktree on a WRONG orphan commit (not descended from fork/main).
  git -C "$project" worktree add --quiet --detach "$pool"
  git -C "$pool" checkout --quiet --orphan wrong-base
  printf 'wrong base content\n' > "$pool/wrong.txt"
  git -C "$pool" add wrong.txt
  git -C "$pool" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'wrong orphan base'
  wrong_sha=$(git -C "$pool" rev-parse HEAD)

  # Clean the orphan checkout to detached HEAD on the wrong commit.
  git -C "$pool" checkout --quiet --detach "$wrong_sha"
  git -C "$pool" clean -fd >/dev/null 2>&1
  before=$(git -C "$pool" rev-parse HEAD)
  [ "$before" = "$wrong_sha" ] || fail "fixture: HEAD is not the wrong commit"

  # The shipped assertion, evaluated over this fixture. Precondition: HEAD is
  # NOT descended from fork/main, which the tracking config names.
  if git -C "$pool" merge-base --is-ancestor "$fork_tip" "$before" 2>/dev/null; then
    fail "fixture: HEAD should NOT be descended from fork/main, but is"
  fi
  out=$(bash -c '
    . "$0/bin/fm-ff-lib.sh"
    eval "$(sed -n "/^assert_spawn_tracked_base() {/,/^}$/p" "$0/bin/fm-spawn.sh")"
    assert_spawn_tracked_base "$1"
  ' "$ROOT" "$pool" 2>&1); rc=$?
  [ "$rc" -ne 0 ] || fail "the shipped assertion passed despite a genuinely wrong base"
  assert_contains "$out" "not based on tracked upstream" \
    "the shipped assertion did not name the wrong base: $out"
  after=$(git -C "$pool" rev-parse HEAD)
  [ "$after" = "$before" ] || fail "the check modified the worktree (should be read-only)"

  # And the positive control: the same copy, reset onto the tracked tip,
  # passes the shipped assertion.
  git -C "$pool" checkout --quiet --detach "$fork_tip"
  git -C "$pool" clean -fd >/dev/null 2>&1
  out=$(bash -c '
    . "$0/bin/fm-ff-lib.sh"
    eval "$(sed -n "/^assert_spawn_tracked_base() {/,/^}$/p" "$0/bin/fm-spawn.sh")"
    assert_spawn_tracked_base "$1"
  ' "$ROOT" "$pool" 2>&1); rc=$?
  expect_code 0 "$rc" "the shipped assertion should pass a base descended from the tracked upstream"$'\n'"$out"

  if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
    printf '# observed wrong-base refusal: HEAD=%s fork/main=%s (correctly diverged)\n' "$before" "$fork_tip"
  fi
  pass "a genuinely wrong base is refused by the shipped tracked-base assertion and the copy is left untouched"
}

# A stateful terminal and pool stand-in for the refused-launch sequence. Each
# window is a file recording its stable id and the path its shell sits in; a
# `treehouse get` typed into a window moves it into the lowest slot no live
# window sits in, and a slot stays leased exactly while that window lives -
# the process lease real Treehouse keeps for an interactive acquire.
install_pool_world_tmux() {  # <fakebin> <world-dir> <slot-root>
  local fakebin=$1 world=$2 slots=$3
  mkdir -p "$world"
  cat > "$fakebin/tmux" <<SH
#!/usr/bin/env bash
set -u
world='$world'
slots='$slots'
SH
  cat >> "$fakebin/tmux" <<'SH'
window_name() {  # <target>
  local t=${1#=}
  t=${t#*:}
  t=${t#=}
  case "$t" in
    @*) grep -lxF -- "$t" "$world"/*.id 2>/dev/null | head -n 1 | sed 's|.*/||; s|\.id$||' ;;
    *) printf '%s' "$t" ;;
  esac
}
target= name= cwd= fmt= text=
cmd=${1:-}
[ "$#" -eq 0 ] || shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    -t) target=$2; shift 2 ;;
    -n) name=$2; shift 2 ;;
    -c) cwd=$2; shift 2 ;;
    -F) fmt=$2; shift 2 ;;
    -*) shift ;;
    *) text="$text${text:+ }$1"; shift ;;
  esac
done
case "$cmd" in
  display-message)
    case "$text" in
      *pane_current_path*) cat "$world/$(window_name "$target").path" 2>/dev/null ;;
      *) printf 'firstmate\n' ;;
    esac
    ;;
  list-windows)
    for f in "$world"/*.id; do [ -e "$f" ] || continue; f=${f##*/}; printf '%s\n' "${f%.id}"; done
    ;;
  new-window)
    n=$(( $(cat "$world/.next" 2>/dev/null || echo 0) + 1 ))
    printf '%s\n' "$n" > "$world/.next"
    printf '@%s\n' "$n" > "$world/$name.id"
    printf '%s\n' "$cwd" > "$world/$name.path"
    printf '@%s\n' "$n"
    ;;
  kill-window)
    w=$(window_name "$target")
    rm -f "$world/$w.id" "$world/$w.path"
    ;;
  send-keys)
    if [ "$text" = "treehouse get Enter" ]; then
      w=$(window_name "$target")
      for slot in "$slots"/*/project; do
        grep -qxF -- "$slot" "$world"/*.path 2>/dev/null && continue
        printf '%s\n' "$slot" > "$world/$w.path"
        break
      done
    fi
    ;;
esac
exit 0
SH
  chmod +x "$fakebin/tmux"
}

# A launch refused after it claimed its slot must leave the pool and the task
# records exactly as it found them, so a retry of the same task and a later
# second task each land on a slot of their own. The refusal here is a polytoken
# launch into a project that tracks its own .polytoken/hooks.json.
test_refused_launch_leaves_the_pool_for_a_retry_and_a_second_task() {
  local rec id other out status slot_root world claim1 claim2
  id='pool-refused-retry-r1'
  other='pool-refused-second-r1'
  rm -rf "/tmp/fm-$id" "/tmp/fm-$other"
  rec=$(make_case refused-retry "$id")
  read_case_record "$rec"
  fm_test_spawn_brief "$HOME_DIR" "$other"
  mkdir -p "$CASE_DIR/publisher/.polytoken"
  printf '[]\n' > "$CASE_DIR/publisher/.polytoken/hooks.json"
  git -C "$CASE_DIR/publisher" add .polytoken/hooks.json
  git -C "$CASE_DIR/publisher" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm project-hooks
  git -C "$CASE_DIR/publisher" push --quiet origin "$DEFAULT_BRANCH"
  slot_root="$CASE_DIR/slots"
  mkdir -p "$slot_root/1" "$slot_root/2"
  git -C "$PROJECT_DIR" worktree move "$POOL_DIR" "$slot_root/1/project"
  git -C "$PROJECT_DIR" worktree add --quiet --detach "$slot_root/2/project" "$INITIAL_SHA"
  printf '{"worktrees":[{"name":"1","path":"%s"},{"name":"2","path":"%s"}]}\n' \
    "$slot_root/1/project" "$slot_root/2/project" > "$slot_root/treehouse-state.json"
  claim1="$slot_root/1/.fm-slot-owner"
  claim2="$slot_root/2/.fm-slot-owner"
  world="$CASE_DIR/tmux-world"
  install_pool_world_tmux "$FAKEBIN_DIR" "$world" "$slot_root"
  cat > "$FAKEBIN_DIR/polytoken" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"print models"*) printf 'zai/glm-5.3-flash\nzai/glm-5.3-flash(low)\n' ;;
esac
exit 0
SH
  chmod +x "$FAKEBIN_DIR/polytoken"

  out=$(run_spawn "$id" --harness polytoken --model zai/glm-5.3-flash --effort low --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "a polytoken launch into a project with its own hook layer should refuse"
  assert_contains "$out" "not firstmate's to clobber" "the refusal message changed"$'\n'"$out"
  [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "the refused launch published a task record"
  [ ! -e "$world/fm-$id.id" ] \
    || fail "the refused launch left its window open, still leasing $(cat "$world/fm-$id.path")"
  [ ! -e "$claim1" ] && [ ! -e "$claim2" ] || fail "the refused launch left a slot claim behind"
  [ ! -e "/tmp/fm-$id" ] || fail "the refused launch left its task temp root behind"

  out=$(run_spawn "$id" --harness codex --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "the retry of the refused task should launch"$'\n'"$out"
  assert_grep "worktree=$slot_root/1/project" "$HOME_DIR/state/$id.meta" \
    "the retry did not land on the slot the refused launch returned"

  out=$(run_spawn "$other" --harness codex --mode no-mistakes --yolo off)
  status=$?
  expect_code 0 "$status" "a second task spawned after the retry should launch"$'\n'"$out"
  assert_grep "worktree=$slot_root/2/project" "$HOME_DIR/state/$other.meta" \
    "the second task was handed the retried task's slot"
  grep -Fxq -- "task=$id" "$claim1" || fail "slot 1 is not claimed by the retried task: $(cat "$claim1" 2>/dev/null)"
  grep -Fxq -- "task=$other" "$claim2" || fail "slot 2 is not claimed by the second task: $(cat "$claim2" 2>/dev/null)"
  rm -rf "/tmp/fm-$id" "/tmp/fm-$other" "/tmp/fm-$id+"* "/tmp/fm-$other+"*
  pass "a launch refused after its slot claim leaves the pool for a retry and a second task"
}

# A refused polytoken launch removes its per-id paths only under the per-id
# spawn lock. Here a retry of the same id takes that lock while the abort is
# still closing the window, so the abort must leave the task temp root the
# retry may already be reusing, while still closing the window and returning
# the slot.
test_refused_launch_spares_per_id_paths_under_a_contended_spawn_lock() {
  local rec id out status slot_root world holder_pid
  id='pool-refused-contended-r1'
  rm -rf "/tmp/fm-$id"
  rec=$(make_case refused-contended "$id")
  read_case_record "$rec"
  mkdir -p "$CASE_DIR/publisher/.polytoken"
  printf '[]\n' > "$CASE_DIR/publisher/.polytoken/hooks.json"
  git -C "$CASE_DIR/publisher" add .polytoken/hooks.json
  git -C "$CASE_DIR/publisher" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm project-hooks
  git -C "$CASE_DIR/publisher" push --quiet origin "$DEFAULT_BRANCH"
  slot_root="$CASE_DIR/slots"
  mkdir -p "$slot_root/1"
  git -C "$PROJECT_DIR" worktree move "$POOL_DIR" "$slot_root/1/project"
  printf '{"worktrees":[{"name":"1","path":"%s"}]}\n' "$slot_root/1/project" \
    > "$slot_root/treehouse-state.json"
  world="$CASE_DIR/tmux-world"
  install_pool_world_tmux "$FAKEBIN_DIR" "$world" "$slot_root"
  mv "$FAKEBIN_DIR/tmux" "$FAKEBIN_DIR/tmux.world"
  cat > "$FAKEBIN_DIR/tmux" <<SH
#!/usr/bin/env bash
if [ "\${1:-}" = kill-window ] && [ ! -e '$CASE_DIR/holder.pid' ]; then
  FM_HOME='$HOME_DIR' nohup bash -c '. "\$1"; fm_lock_try_acquire "\$2" || exit 1; : > "\$3"; exec /bin/sleep 60' _ \\
    '$ROOT/bin/fm-wake-lib.sh' '$HOME_DIR/state/.spawn-$id.lock' '$CASE_DIR/holder.ready' \\
    >/dev/null 2>&1 &
  printf '%s\n' "\$!" > '$CASE_DIR/holder.pid'
  n=0
  while [ ! -e '$CASE_DIR/holder.ready' ] && [ "\$n" -lt 100 ]; do /bin/sleep 0.05; n=\$((n + 1)); done
fi
exec '$FAKEBIN_DIR/tmux.world' "\$@"
SH
  chmod +x "$FAKEBIN_DIR/tmux"
  cat > "$FAKEBIN_DIR/polytoken" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"print models"*) printf 'zai/glm-5.3-flash\nzai/glm-5.3-flash(low)\n' ;;
esac
exit 0
SH
  chmod +x "$FAKEBIN_DIR/polytoken"

  out=$(run_spawn "$id" --harness polytoken --model zai/glm-5.3-flash --effort low --mode no-mistakes --yolo off)
  status=$?
  holder_pid=$(cat "$CASE_DIR/holder.pid" 2>/dev/null || true)
  [ -z "$holder_pid" ] || kill "$holder_pid" 2>/dev/null || true
  [ "$status" -ne 0 ] || fail "a polytoken launch into a project with its own hook layer should refuse"
  [ -e "$CASE_DIR/holder.ready" ] || fail "the stand-in retry never took the per-id spawn lock"$'\n'"$out"
  assert_contains "$out" "another spawn of $id holds its spawn lock" \
    "the abort did not report skipping its per-id cleanup"$'\n'"$out"
  [ -d "/tmp/fm-$id" ] || fail "the abort removed the task temp root while another spawn held the per-id lock"
  [ ! -e "$world/fm-$id.id" ] || fail "the contended abort left its window open"
  [ ! -e "$slot_root/1/.fm-slot-owner" ] || fail "the contended abort left its slot claim behind"
  rm -rf "/tmp/fm-$id"
  pass "a refused polytoken launch leaves its per-id paths alone while another spawn holds the per-id lock"
}

# A launch on any harness other than polytoken keeps main's abort behavior: its
# pre-publication claim is dropped under the allocation lock, but the window
# it opened, and with it the slot's lease, stays for the operator to inspect.
test_non_polytoken_refused_launch_keeps_its_window() {
  local rec id out status slot_root world
  id='pool-refused-codex-r1'
  rec=$(make_originless_case refused-codex "$id")
  read_case_record "$rec"
  slot_root="$CASE_DIR/slots"
  mkdir -p "$slot_root/1"
  git -C "$PROJECT_DIR" worktree move "$POOL_DIR" "$slot_root/1/project"
  printf '{"worktrees":[{"name":"1","path":"%s"}]}\n' "$slot_root/1/project" \
    > "$slot_root/treehouse-state.json"
  git -C "$slot_root/1/project" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
  world="$CASE_DIR/tmux-world"
  install_pool_world_tmux "$FAKEBIN_DIR" "$world" "$slot_root"

  out=$(run_spawn "$id" --harness codex --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "a codex launch on a slot with an unusable origin should refuse"
  assert_contains "$out" "could not fetch origin" "the codex refusal message changed"$'\n'"$out"
  [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "the refused codex launch published a task record"
  [ -e "$world/fm-$id.id" ] || fail "a non-polytoken refusal closed its window, unlike main"
  [ ! -e "$slot_root/1/.fm-slot-owner" ] || fail "the refused codex launch left its slot claim"
  pass "a non-polytoken launch refused after its slot claim keeps main's abort behavior"
}

# install_isolation_flip_git <fakebin> <worktree>: a git that answers every
# command truthfully except `rev-parse --show-toplevel` on <worktree>, which
# reports a foreign root from its third read on - after the discovery poll's
# two agreeing reads adopted the slot, so only the post-poll isolation check
# sees the worktree stop being isolated.
install_isolation_flip_git() {  # <fakebin> <worktree>
  local fakebin=$1 wt=$2 real_git
  real_git=$(command -v git)
  cat > "$fakebin/git" <<SH
#!/usr/bin/env bash
if [ "\$1" = -C ] && [ "\$2" = '$wt' ] && [ "\$3" = rev-parse ] && [ "\$4" = --show-toplevel ]; then
  n=\$(cat '$fakebin/.toplevel-reads' 2>/dev/null || echo 0)
  n=\$((n + 1))
  printf '%s\n' "\$n" > '$fakebin/.toplevel-reads'
  if [ "\$n" -gt 2 ]; then
    printf '%s\n' '$fakebin'
    exit 0
  fi
fi
exec '$real_git' "\$@"
SH
  chmod +x "$fakebin/git"
}

# The post-poll treehouse-get isolation refusal points the operator at the
# window only when that window survives the abort: every harness but polytoken
# keeps main's `Inspect target <window>`, and a fresh polytoken spawn, whose
# window is closed on exit, names the worktree and says so instead.
test_treehouse_isolation_refusal_points_at_what_survives() {
  local rec id out status
  id='pool-isolation-codex-r1'
  rec=$(make_case isolation-codex "$id")
  read_case_record "$rec"
  fm_test_fake_sleep_noop "$FAKEBIN_DIR"
  install_isolation_flip_git "$FAKEBIN_DIR" "$POOL_DIR"
  out=$(run_spawn "$id" --harness codex --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "a codex spawn whose slot stopped being isolated should refuse"
  assert_contains "$out" "treehouse get did not yield an isolated worktree" \
    "the codex spawn did not refuse on the post-poll isolation check"$'\n'"$out"
  assert_contains "$out" "refusing to launch to avoid tangling the primary checkout. Inspect target firstmate:fm-$id" \
    "a non-polytoken isolation refusal must keep main's window pointer"$'\n'"$out"
  assert_not_contains "$out" "is closed on exit" \
    "a non-polytoken isolation refusal must not claim its window was closed"

  id='pool-isolation-polytoken-r1'
  rec=$(make_case isolation-polytoken "$id")
  read_case_record "$rec"
  fm_test_fake_sleep_noop "$FAKEBIN_DIR"
  install_isolation_flip_git "$FAKEBIN_DIR" "$POOL_DIR"
  cat > "$FAKEBIN_DIR/polytoken" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"print models"*) printf 'zai/glm-5.3-flash\nzai/glm-5.3-flash(low)\n' ;;
esac
exit 0
SH
  chmod +x "$FAKEBIN_DIR/polytoken"
  out=$(run_spawn "$id" --harness polytoken --model zai/glm-5.3-flash --effort low --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "a polytoken spawn whose slot stopped being isolated should refuse"
  assert_contains "$out" "treehouse get did not yield an isolated worktree" \
    "the polytoken spawn did not refuse on the post-poll isolation check"$'\n'"$out"
  assert_contains "$out" "pool (window firstmate:fm-$id is closed on exit" \
    "a fresh polytoken isolation refusal must name the worktree and the closed window"$'\n'"$out"
  assert_not_contains "$out" "Inspect target firstmate:fm-$id" \
    "a fresh polytoken isolation refusal must not point at its closed window"
  rm -rf "/tmp/fm-$id" "/tmp/fm-$id+"*
  pass "the treehouse-get isolation refusal points only at what survives the abort"
}

publish_feature_branch() { # <branch>
  git -C "$CASE_DIR/publisher" checkout --quiet -b "$1"
  printf 'only on %s\n' "$1" > "$CASE_DIR/publisher/feature-only.txt"
  git -C "$CASE_DIR/publisher" add feature-only.txt
  git -C "$CASE_DIR/publisher" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm feature
  git -C "$CASE_DIR/publisher" push --quiet origin "$1"
}

brief_with_base() { # <id> <base> [<intent>]
  fm_test_spawn_brief "$HOME_DIR" "$1" ${3:+"$3"}
  printf '\n# Setup\nYou are in a disposable git worktree of project, at a detached HEAD on a clean copy of its base branch.\nBase branch: %s\n' "$2" >> "$HOME_DIR/data/$1/brief.md"
}

test_named_base_branch_starts_from_that_branch() {
  local rec id out status kind
  for kind in ship scout; do
    id="pool-named-base-$kind-r1"
    rec=$(make_case "named-base-$kind" "$id")
    read_case_record "$rec"
    publish_feature_branch feature/hub
    brief_with_base "$id" feature/hub
    if [ "$kind" = ship ]; then
      out=$(run_spawn "$id" --mode direct-PR --yolo off --base-branch feature/hub)
    else
      out=$(run_spawn "$id" --scout --base-branch feature/hub)
    fi
    status=$?
    expect_code 0 "$status" "a $kind spawn with a matching base branch should launch"$'\n'"$out"
    [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$(git -C "$POOL_DIR" rev-parse origin/feature/hub)" ] \
      || fail "the $kind copy did not start from origin/feature/hub"
    assert_grep 'only on feature/hub' "$POOL_DIR/feature-only.txt" \
      "the $kind copy is missing the base branch content"
    assert_grep 'base_branch=feature/hub' "$HOME_DIR/state/$id.meta" \
      "the $kind spawn did not record its base branch"
  done

  id='pool-named-base-missing-r1'
  rec=$(make_case named-base-missing "$id")
  read_case_record "$rec"
  brief_with_base "$id" feature/missing
  out=$(run_spawn "$id" --scout --base-branch feature/missing)
  status=$?
  [ "$status" -ne 0 ] || fail "a base branch origin lacks should refuse the spawn"
  assert_contains "$out" "origin/feature/missing" "the refusal did not name the missing base"
  [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "a refused base-branch spawn published task metadata"

  id='pool-named-base-local-only-r1'
  rec=$(make_case named-base-local-only "$id")
  read_case_record "$rec"
  brief_with_base "$id" feature/hub
  out=$(run_spawn "$id" --mode local-only --yolo off --base-branch feature/hub)
  status=$?
  [ "$status" -ne 0 ] || fail "a base branch on a local-only ship should refuse the spawn"
  assert_contains "$out" "mode=local-only" "the local-only refusal did not explain itself"
  pass "--base-branch picks the copy's starting point and is recorded; a missing or local-only base refuses"
}

# Regression for a based spawn whose selected branch is behind the default
# upstream. The launch must validate against the selected branch, while an
# unrelated copy must still fail that same assertion.
test_spawn_base_branch_behind_default_upstream_launches() {
  local rec id out status base_sha default_sha unrelated_sha
  id='pool-base-behind-default-r1'
  rec=$(make_case base-behind-default "$id")
  read_case_record "$rec"
  git -C "$PROJECT_DIR" push --quiet origin "$INITIAL_SHA:refs/heads/fm/lb2-02-safe-fetch"
  git -C "$PROJECT_DIR" fetch --quiet origin
  base_sha=$(git -C "$PROJECT_DIR" rev-parse origin/fm/lb2-02-safe-fetch)
  default_sha=$(git -C "$PROJECT_DIR" rev-parse origin/main)
  [ "$base_sha" != "$default_sha" ] || fail "fixture: selected base is not behind origin/main"
  if git -C "$PROJECT_DIR" merge-base --is-ancestor "$default_sha" "$base_sha"; then
    fail "fixture: selected base unexpectedly descends from origin/main"
  fi
  brief_with_base "$id" fm/lb2-02-safe-fetch
  out=$(run_spawn "$id" --mode direct-PR --yolo off --base-branch fm/lb2-02-safe-fetch)
  status=$?
  expect_code 0 "$status" "a ship based on a branch behind the default upstream should launch"$'\n'"$out"
  assert_contains "$out" "spawned $id" "the based ship did not report success"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$base_sha" ] \
    || fail "the pooled copy did not start from the selected behind-default base"

  git -C "$POOL_DIR" checkout --quiet --orphan unrelated-base
  printf 'unrelated\n' > "$POOL_DIR/unrelated.txt"
  git -C "$POOL_DIR" add unrelated.txt
  git -C "$POOL_DIR" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm unrelated
  unrelated_sha=$(git -C "$POOL_DIR" rev-parse HEAD)
  out=$(bash -c '
    . "$0/bin/fm-ff-lib.sh"
    eval "$(sed -n "/^assert_spawn_tracked_base() {/,/^}$/p" "$0/bin/fm-spawn.sh")"
    assert_spawn_tracked_base "$1" fm/lb2-02-safe-fetch
  ' "$ROOT" "$POOL_DIR" 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "an unrelated copy passed the explicit-base assertion"
  assert_contains "$out" "not based on tracked upstream 'origin/fm/lb2-02-safe-fetch'" \
    "the explicit-base refusal did not identify the selected base"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$unrelated_sha" ] \
    || fail "the assertion changed the unrelated copy"
  pass "ship spawn on a branch behind default launches, while an unrelated base refuses"
}

test_base_branch_must_agree_with_the_brief() {
  local rec id out status case_name
  for case_name in flag-only line-only task-decoy; do
    id="pool-base-agree-$case_name-r1"
    rec=$(make_case "base-agree-$case_name" "$id")
    read_case_record "$rec"
    publish_feature_branch feature/hub
    case "$case_name" in
    flag-only)
      fm_test_spawn_brief "$HOME_DIR" "$id"
      out=$(run_spawn "$id" --scout --base-branch feature/hub)
      ;;
    line-only)
      brief_with_base "$id" feature/hub
      out=$(run_spawn "$id" --scout)
      ;;
    task-decoy)
      brief_with_base "$id" feature/hub $'You are in a disposable git worktree of project, at a detached HEAD on a clean copy of its base branch.\nBase branch: decoy'
      out=$(run_spawn "$id" --scout --base-branch feature/hub)
      ;;
    esac
    status=$?
    [ "$status" -ne 0 ] || fail "a $case_name base-branch disagreement should refuse the spawn"
    assert_contains "$out" "Base branch" "the $case_name refusal did not name the Base branch line"
    [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "a refused $case_name spawn published task metadata"
  done
  pass "a spawn refuses when --base-branch and the brief's Base branch lines disagree"
}

# A Base branch line quoted in the captain's intent is prose: it neither blocks
# a spawn nor redirects it.
test_prose_base_branch_line_is_ignored() {
  local rec id out status
  id='pool-base-prose-none-r1'
  rec=$(make_case base-prose-none "$id")
  read_case_record "$rec"
  fm_test_spawn_brief "$HOME_DIR" "$id" $'Fix the release.\nBase branch: release/1.2'
  out=$(run_spawn "$id" --scout)
  status=$?
  expect_code 0 "$status" "a no-base brief quoting a Base branch line should launch"$'\n'"$out"
  assert_no_grep 'base_branch=' "$HOME_DIR/state/$id.meta" "prose recorded a base branch"

  id='pool-base-prose-flag-r1'
  rec=$(make_case base-prose-flag "$id")
  read_case_record "$rec"
  publish_feature_branch feature/hub
  brief_with_base "$id" feature/hub $'Fix the release.\nBase branch: release/1.2'
  out=$(run_spawn "$id" --scout --base-branch feature/hub)
  status=$?
  expect_code 0 "$status" "a flagged spawn whose intent names another branch should launch"$'\n'"$out"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$(git -C "$POOL_DIR" rev-parse origin/feature/hub)" ] \
    || fail "the prose branch redirected the copy away from origin/feature/hub"
  assert_grep 'base_branch=feature/hub' "$HOME_DIR/state/$id.meta" "the flag's base was not recorded"
  pass "a Base branch line in the captain's intent neither blocks nor redirects a spawn"
}

# A named base is validated against the project's registered forge for scouts
# too, so a scout that promotion could never ship is refused at spawn.
test_scout_base_branch_refused_on_gerrit_forge() {
  local rec id out status
  id='pool-base-gerrit-scout-r1'
  rec=$(make_case base-gerrit-scout "$id")
  read_case_record "$rec"
  publish_feature_branch feature/hub
  printf '%s\n' '- project [no-mistakes forge=gerrit] - fixture (added 2026-01-01)' > "$HOME_DIR/data/projects.md"
  brief_with_base "$id" feature/hub
  out=$(run_spawn "$id" --scout --base-branch feature/hub)
  status=$?
  [ "$status" -ne 0 ] || fail "a based scout on a forge=gerrit project should refuse the spawn"
  assert_contains "$out" "forge=gerrit" "the gerrit refusal did not explain itself"
  [ ! -e "$HOME_DIR/state/$id.meta" ] || fail "a refused gerrit scout spawn published task metadata"
  pass "a based scout on a forge=gerrit project is refused at spawn"
}

# A local-only project lands work with bin/fm-merge-local.sh: the primary
# checkout's default branch advances and nothing is pushed, so origin stays at
# the tip the slot was allocated from while that branch moves on.
make_local_landing_case() { # <name> <id> <landings>
  local name=$1 id=$2 landings=$3 case_dir home project origin pool fakebin frozen n
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
  printf 'base\n' > "$project/README.md"
  git -C "$project" add README.md
  git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm initial
  git clone --quiet --bare "$project" "$origin"
  git -C "$project" remote add origin "file://$origin"
  frozen=$(git -C "$project" rev-parse HEAD)
  git -C "$project" worktree add --quiet --detach "$pool" "$frozen"

  n=1
  while [ "$n" -le "$landings" ]; do
    printf 'landed locally %s\n' "$n" > "$project/landed-$n.txt"
    git -C "$project" add "landed-$n.txt"
    git -C "$project" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm "land $n"
    n=$((n + 1))
  done

  printf '%s\n' "$case_dir|$home|$project|$pool|$fakebin|$frozen|main"
}

opt_in_primary_checkout_base() {
  : > "$HOME_DIR/config/spawn-base-primary-checkout"
}

# Push a commit to origin that the primary checkout never pulls.
push_forge_only_commit() {
  local publisher="$CASE_DIR/forge-publisher"
  git clone --quiet "file://$CASE_DIR/origin.git" "$publisher"
  printf 'pushed straight to the forge\n' > "$publisher/forge-only.txt"
  git -C "$publisher" add forge-only.txt
  git -C "$publisher" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm forge-only
  git -C "$publisher" push --quiet origin main
}

spawn_delivery() { # <id> <no-mistakes|direct-PR|local-only|scout>
  if [ "$2" = scout ]; then
    run_spawn "$1" --scout
  else
    run_spawn "$1" --mode "$2" --yolo off
  fi
}

# Without the opt-in the refresh is the origin reset pinned above, even for the
# two deliveries the opt-in moves: a local-only ship and a scout still start
# from origin's tip however far the primary checkout's branch has moved.
test_default_keeps_origin_tip_while_the_primary_leads() {
  local rec id out status contract
  for contract in local-only scout; do
    id="pool-default-$contract-r1"
    rec=$(make_local_landing_case "default-$contract" "$id" 3)
    read_case_record "$rec"

    out=$(spawn_delivery "$id" "$contract")
    status=$?
    expect_code 0 "$status" "a $contract spawn without the opt-in should launch"$'\n'"$out"
    [ "$(git -C "$PROJECT_DIR" rev-parse main)" != "$INITIAL_SHA" ] \
      || fail "fixture did not advance the primary checkout past origin's tip"
    [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$INITIAL_SHA" ] \
      || fail "without the opt-in a $contract spawn moved off origin's tip"
    [ ! -e "$POOL_DIR/landed-3.txt" ] \
      || fail "without the opt-in a $contract spawn started from the primary checkout's branch"
    assert_not_contains "$out" "note: pooled worktree" \
      "without the opt-in the $contract refresh printed a base note"
  done
  pass "without the opt-in a local-only ship or a scout still starts from origin's tip while the primary leads"
}

# With the opt-in the base follows the delivery: a local-only ship or a scout
# starts from the primary checkout's branch when it strictly contains origin's
# tip, and every other case keeps origin's tip - either publishing mode, a
# primary that has diverged (reported in a note), and a primary that trails
# origin. The slot's base and the commit the captain's review starts from must
# be the same commit, so each case also commits on the slot and runs the
# opted-in review.
test_opt_in_base_follows_the_delivery_and_the_review_follows_the_base() {
  local shape contract publishes id rec out status head expected review base_line review_start landings
  for shape in leading diverged trailing; do
    for contract in no-mistakes direct-PR local-only scout; do
      id="pool-opt-in-$shape-$contract-r1"
      publishes=0
      case $contract in no-mistakes | direct-PR) publishes=1 ;; esac
      landings=2
      [ "$shape" != trailing ] || landings=0
      rec=$(make_local_landing_case "opt-in-$shape-$contract" "$id" "$landings")
      read_case_record "$rec"
      opt_in_primary_checkout_base
      [ "$shape" = leading ] || push_forge_only_commit

      out=$(spawn_delivery "$id" "$contract")
      status=$?
      expect_code 0 "$status" "$shape/$contract: an opted-in spawn should launch"$'\n'"$out"
      head=$(git -C "$POOL_DIR" rev-parse HEAD)
      if [ "$shape" = leading ] && [ "$publishes" = 0 ]; then
        expected=$(git -C "$PROJECT_DIR" rev-parse main)
        [ "$expected" != "$INITIAL_SHA" ] || fail "$shape/$contract: fixture did not advance the primary checkout"
      else
        expected=$(git -C "$POOL_DIR" rev-parse origin/main)
      fi
      [ "$head" = "$expected" ] || fail "$shape/$contract: the slot started at $head, not $expected"
      if [ "$shape" = diverged ] && [ "$publishes" = 0 ]; then
        assert_contains "$out" "main in the primary checkout has diverged from origin/main (ahead by 2, behind by 1); starting from origin's tip" \
          "$shape/$contract: the spawn did not report the divergence it set aside"
      else
        assert_not_contains "$out" "note: pooled worktree" "$shape/$contract: the spawn printed a base note"
      fi

      git -C "$POOL_DIR" checkout --quiet -b "fm/$id"
      printf 'the work under review\n' > "$POOL_DIR/task-change.txt"
      git -C "$POOL_DIR" add task-change.txt
      git -C "$POOL_DIR" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' commit -qm 'task work'
      review=$(FM_ROOT_OVERRIDE="$ROOT" FM_STATE_OVERRIDE="$HOME_DIR/state" FM_CONFIG_OVERRIDE="$HOME_DIR/config" \
        "$ROOT/bin/fm-review-diff.sh" "$id" --stat 2>/dev/null)
      base_line=$(printf '%s\n' "$review" | sed -n 's/^diff base: //p')
      [ -n "$base_line" ] || fail "$shape/$contract: the review printed no base"
      review_start=$(git -C "$POOL_DIR" merge-base "$base_line" "fm/$id" 2>/dev/null) \
        || fail "$shape/$contract: the review base '$base_line' shares no history with the branch"
      [ "$review_start" = "$head" ] \
        || fail "$shape/$contract: the slot started at $head but the review starts at $review_start (anchored on $base_line)"
      assert_contains "$review" 'task-change.txt' "$shape/$contract: the review lost the task's own work"
      assert_not_contains "$review" 'landed-' "$shape/$contract: the review carries a local landing the task never authored"
      assert_not_contains "$review" 'forge-only.txt' "$shape/$contract: the review carries an origin commit the task never authored"
      if [ "${FM_TEST_EVIDENCE:-0}" = 1 ]; then
        printf '# opted-in %s/%s: slot=%s review base=%s\n' "$shape" "$contract" "$head" "$base_line"
      fi
    done
  done
  pass "with the opt-in only a local-only ship or a scout leaves origin's tip, for a primary that strictly contains it, and the review starts where the slot did"
}

# A named base branch is delivered against origin, so the opt-in leaves it on
# origin's copy even when the primary checkout's branch of that name is ahead.
test_opt_in_keeps_a_named_base_branch_on_origin() {
  local rec id out status parent local_tip
  id='pool-opt-in-named-base-r1'
  rec=$(make_case opt-in-named-base "$id")
  read_case_record "$rec"
  opt_in_primary_checkout_base
  publish_feature_branch feature/hub
  git -C "$PROJECT_DIR" fetch --quiet origin
  parent=$(git -C "$PROJECT_DIR" rev-parse origin/feature/hub)
  local_tip=$(git -C "$PROJECT_DIR" -c user.name='Firstmate Tests' -c user.email='tests@example.invalid' \
    commit-tree "$parent^{tree}" -p "$parent" -m 'local only on feature/hub')
  git -C "$PROJECT_DIR" update-ref refs/heads/feature/hub "$local_tip"
  brief_with_base "$id" feature/hub

  out=$(run_spawn "$id" --scout --base-branch feature/hub)
  status=$?
  expect_code 0 "$status" "an opted-in scout on a named base should launch"$'\n'"$out"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$(git -C "$POOL_DIR" rev-parse origin/feature/hub)" ] \
    || fail "the opt-in moved a named base branch off origin/feature/hub"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" != "$local_tip" ] \
    || fail "the opt-in started a named base branch from the primary checkout's copy"
  pass "with the opt-in a named base branch still starts from origin's copy"
}

# The clean-slot refusal still comes first: an unclean slot is refused as
# uncommitted work, untouched, before any base is chosen or reported.
test_opt_in_still_refuses_an_unclean_slot_first() {
  local rec id out status before
  id='pool-opt-in-dirty-r1'
  rec=$(make_local_landing_case opt-in-dirty "$id" 2)
  read_case_record "$rec"
  opt_in_primary_checkout_base
  push_forge_only_commit
  printf 'work the captain has not committed\n' > "$POOL_DIR/uncommitted.txt"
  before=$(git -C "$POOL_DIR" rev-parse HEAD)

  out=$(run_spawn "$id" --mode local-only --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "an opted-in spawn launched from an unclean slot"
  assert_contains "$out" "refusing to discard uncommitted work" \
    "the unclean slot was not refused as uncommitted work"
  assert_not_contains "$out" "has diverged" \
    "the unclean slot was reported as a divergence instead of as uncommitted work"
  assert_grep 'work the captain has not committed' "$POOL_DIR/uncommitted.txt" \
    "the opted-in spawn discarded uncommitted work"
  [ "$(git -C "$POOL_DIR" rev-parse HEAD)" = "$before" ] \
    || fail "the opted-in spawn moved an unclean slot's base"
  pass "with the opt-in an unclean slot is still refused, untouched, before any base is chosen"
}

test_remote_seeded_home_spawns_from_treehouse_pool
test_pool_slot_claim_follows_the_spawn_outcome
test_wrong_remote_base_corrected_by_tracking
test_fork_project_tracked_base_assertion_passes
test_symlinked_clone_tracked_base_assertion_passes
test_completely_unreadable_upstream_warns_and_proceeds
test_wrong_base_assertion_refuses_and_preserves
test_refused_launch_leaves_the_pool_for_a_retry_and_a_second_task
test_refused_launch_spares_per_id_paths_under_a_contended_spawn_lock
test_non_polytoken_refused_launch_keeps_its_window
test_treehouse_isolation_refusal_points_at_what_survives
test_linked_spawning_home_rejects_primary_before_refresh
test_stale_pool_base_refreshes_before_branching
test_named_base_branch_starts_from_that_branch
test_spawn_base_branch_behind_default_upstream_launches
test_base_branch_must_agree_with_the_brief
test_prose_base_branch_line_is_ignored
test_scout_base_branch_refused_on_gerrit_forge
test_non_main_default_branch_refreshes_before_branching
test_direct_pr_and_scout_refresh_before_launch
test_dirty_pool_refuses_without_discarding_work
test_untracked_default_falls_back_to_origin
test_unreachable_origin_warns_but_proceeds_with_local_refs
test_originless_pool_launches_without_a_freshness_fetch
test_originless_dirty_pool_refuses_without_discarding_work
test_origin_config_without_url_warns_and_proceeds
test_empty_origin_config_section_warns_and_proceeds
test_empty_only_included_origin_config_section_launches_pool
test_inactive_conditional_origin_include_launches_pool
test_stale_submodule_pin_explains_itself
test_unpushed_submodule_commit_is_still_uncommitted_work
test_work_inside_submodule_is_still_uncommitted_work
test_stale_pin_carrying_real_work_is_not_called_stale
test_stale_pin_beside_other_dirt_reports_one_verdict
test_default_keeps_origin_tip_while_the_primary_leads
test_opt_in_base_follows_the_delivery_and_the_review_follows_the_base
test_opt_in_keeps_a_named_base_branch_on_origin
test_opt_in_still_refuses_an_unclean_slot_first

echo "# all fm-spawn-pool-base-freshen tests passed"
