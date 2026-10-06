#!/usr/bin/env bash
# Verify open crew PRs as one batch on projects without CI.
# A no-CI project verifies every finished PR serially under a single shared
# lock, so PRs queue while workers sit idle; this tool stacks every
# cleanly-merging open PR onto the default branch, checks the stack out once,
# runs the union of the project's dependent-test selection once under the
# project's verify lock, and merges the whole batch when it is green.
#
# The project repo is only ever read (it is the fetch source); every write
# lands inside the batch directory given by --out/--batch, so a primary
# checkout or a live worker worktree is never touched.
# Stacking uses git merge-tree --write-tree plus git commit-tree inside a
# scratch bare repo in the batch directory: no worktree, no write to the
# project. The stack is then checked out as a git clone --shared of that
# scratch repo. The dependent-test selection command, the test command, and
# the verify-lock path are project inputs on every invocation; nothing about
# any one project (paths, runners, selection scripts) is hardcoded here.
# Merging goes only through bin/fm-pr-merge.sh, so merge metadata and its
# guards still apply, and the existing no-red-merge rule is unchanged: a red
# batch is never merged, it is bisected to the culprit PR instead.
#
# Subcommands: build, verify, bisect, merge, run. `run` is build then verify,
# then merge when green and bisect when red; the other four are the same
# stages for stepwise or supervised use. `run --no-merge` stops after verify.
# Exit 0 means green (merged, or --no-merge green, or a stage done as asked);
# exit 1 means red, refused, or partially merged; exit 2 means bad usage.
# verify refuses an empty batch and an empty selected-test union rather than
# reporting an untested stack as green. merge refuses any PR whose head moved
# after the batch was built: unmoved members still merge, moved ones are
# listed in merge-refused, and the exit is 1.
#
# The new-test check is evidence, not verdict: each PR's newly added test
# files are also run against the base to confirm they fail there, and a new
# test that passes on base is recorded as a warning because it proves
# nothing, but it does not turn a green batch red.
# Bisect replays the recorded test-file list against binary-search prefixes
# of the stack; a prefix missing every recorded file (the test itself lands
# later in the stack) counts as green for that step, which can misattribute
# when the breakage and its test arrive in different PRs - the bisect log
# names the tested prefixes so that case is visible.
#
# Locking tiers, first available wins: flock, lockf, then a mkdir spin lock
# on <lock>.batch-lock, which excludes only other runs of this tool. The
# tier used is printed into the batch log, so a project whose own runner
# uses a different primitive can see the mismatch.
# Forge mode (default) lists and validates each PR through gh and fetches
# heads from the project's remote; --no-forge trusts explicit NAME:TASK=REV
# members with no OPEN/base gate and exists for local repos and tests.
# Usage: fm-batch-pr-verify.sh --help
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MERGE_BIN="$FM_ROOT/bin/fm-pr-merge.sh"

DEFAULT_BASE=main
DEFAULT_REMOTE=origin
DEFAULT_PR_REF='pull/{n}/head'
DEFAULT_TEST_GLOB='(^|/)test_[^/]*\.py$'

fail() {
  echo "error: $1" >&2
  exit 1
}

usage_error() {
  echo "error: $1" >&2
  echo "Run 'fm-batch-pr-verify.sh --help' for usage." >&2
  exit 2
}

print_help() {
  cat <<'HELP'
Usage: fm-batch-pr-verify.sh <build|verify|bisect|merge|run> [options] [MEMBER...]
       fm-batch-pr-verify.sh --help

Verify open crew PRs as one batch on projects without CI. See the script
header for the full contract.

  build --repo DIR --out DIR [--base main] [--remote origin]
        [--slug OWNER/REPO] [--no-forge] [--pr-ref 'pull/{n}/head']
        [--pr-url-template T] MEMBER...
    Stack every cleanly-merging member onto the base. MEMBER is N:TASK
    (PR number plus the owning task id for later merging); with --no-forge
    it is NAME:TASK=REV (an explicit rev in the repo, no fetch, no gh).
    Writes members, skipped, base, head into the batch directory.
  verify --repo DIR --batch DIR --lock PATH --select-cmd CMD --test-cmd CMD
        [--test-glob REGEX]
    Check the stack out as a shared clone, run the union of the
    dependent-test selection once under the lock, and run each PR's new
    test files against the base as evidence. Writes testfiles, result,
    oldmain.
  bisect --repo DIR --batch DIR --lock PATH --test-cmd CMD
    Bisect a red batch to the first-red member. Writes bisect.
  merge --repo DIR --batch DIR [--slug OWNER/REPO]
        [--no-forge] [--pr-url-template T]
    Refuse members whose head moved since build; merge the rest through
    bin/fm-pr-merge.sh. Writes merge-log, merge-refused.
  run --repo DIR --out DIR --lock PATH --select-cmd CMD --test-cmd CMD
        [--no-merge] [other build/verify options] MEMBER...
    build, then verify, then merge when green and bisect when red.

--select-cmd receives the changed files (base..batch) as trailing arguments
and prints selected test files, one per line. --test-cmd receives the
selected test files as trailing arguments and runs in the batch checkout.
--test-glob (default: (^|/)test_[^/]*\.py$) also selects changed and newly
added test files; override it for non-pytest projects. --pr-url-template
substitutes {n} with the PR number; forge mode prefers the URL gh reports.
HELP
}

# batch_with_lock <lock> -- <cmd...>: hold the project's verify lock while
# running the command. First available tier wins: flock, lockf, mkdir spin.
batch_with_lock() {
  local lock=$1
  shift
  [ "${1:-}" = "--" ] || fail "lock invocation must separate the lock from the command with --"
  shift
  if command -v flock >/dev/null 2>&1; then
    flock "$lock" "$@"
  elif command -v lockf >/dev/null 2>&1; then
    lockf "$lock" "$@"
  else
    local spin="$lock.batch-lock" waited=0
    while ! mkdir "$spin" 2>/dev/null; do
      sleep 1
      waited=$((waited + 1))
      [ $((waited % 60)) -eq 0 ] && echo "waiting on $spin ..." >&2
    done
    trap 'rmdir "$spin" 2>/dev/null || true' EXIT INT TERM
    "$@"
    local rc=$?
    rmdir "$spin" 2>/dev/null || true
    trap - EXIT INT TERM
    return "$rc"
  fi
}

batch_lock_tier() {
  if command -v flock >/dev/null 2>&1; then
    printf 'flock'
  elif command -v lockf >/dev/null 2>&1; then
    printf 'lockf'
  else
    printf 'mkdir'
  fi
}

# batch_fetch_src <repo> <remote>: the read-only fetch source. The project's
# remote URL when the checkout names one, else the checkout path itself.
batch_fetch_src() {
  local repo=$1 remote=$2 url
  url=$(git -C "$repo" config "remote.$remote.url" 2>/dev/null || true)
  if [ -n "$url" ]; then
    printf '%s' "$url"
  else
    printf '%s' "$repo"
  fi
}

# batch_gh_field <n> <slug...> <jq>: one gh read for a PR field line.
batch_gh_field() {
  local n=$1
  shift
  local jq=$1
  shift
  if [ $# -gt 0 ]; then
    gh pr view "$n" -R "$1" --json state,baseRefName,headRefOid,url --jq "$jq"
  else
    gh pr view "$n" --json state,baseRefName,headRefOid,url --jq "$jq"
  fi
}

# batch_build <repo> <out> <base> <remote> <slug> <no-forge> <pr-ref> <url-tpl> -- <members...>
batch_build() {
  local repo=$1 out=$2 base=$3 remote=$4 slug=$5 noforge=$6 prref=$7 urltpl=$8
  shift 8
  [ "${1:-}" = "--" ] && shift
  [ -d "$repo" ] || fail "repo is not a directory: $repo"
  command -v gh >/dev/null 2>&1 || [ "$noforge" = "yes" ] || fail "forge mode needs gh on PATH"
  mkdir -p "$out" || fail "cannot create batch directory: $out"
  local scratch="$out/fetch.git" src base_oid acc
  rm -rf "$scratch"
  git init -q --bare "$scratch" || fail "cannot init scratch repo in $out"
  if [ "$noforge" = "yes" ]; then
    src="$repo"
    git -C "$scratch" fetch -q "$src" "$base:refs/batch/base" || fail "cannot fetch base '$base' from $src"
  else
    src=$(batch_fetch_src "$repo" "$remote")
    git -C "$scratch" fetch -q "$src" "$base:refs/batch/base" || fail "cannot fetch base '$base' from the $remote remote"
  fi
  base_oid=$(git -C "$scratch" rev-parse refs/batch/base) || fail "cannot resolve fetched base"
  git -C "$scratch" symbolic-ref HEAD refs/batch/base >/dev/null 2>&1 || true
  printf '%s\n' "$base_oid" > "$out/base"
  : > "$out/members"
  : > "$out/skipped"
  acc=$base_oid
  local spec name task rev head url refspec state baseref
  for spec in "$@"; do
    if [ "$noforge" = "yes" ]; then
      case "$spec" in
        *:*=*) ;;
        *) echo "LOCAL $spec malformed-member" >> "$out/skipped"; continue ;;
      esac
      name=${spec%%:*}
      task=${spec#*:}; task=${task%%=*}; rev=${spec#*=}
      case "$name" in ''|*[!A-Za-z0-9_.-]*) echo "$name malformed-member" >> "$out/skipped"; continue ;; esac
      [ -n "$task" ] && [ -n "$rev" ] || { echo "$name malformed-member" >> "$out/skipped"; continue; }
      git -C "$scratch" fetch -q "$src" "$rev:refs/batch/pr$name" 2>/dev/null \
        || { echo "$name unknown-rev" >> "$out/skipped"; continue; }
      head=$(git -C "$scratch" rev-parse "refs/batch/pr$name") || { echo "$name unknown-rev" >> "$out/skipped"; continue; }
      url=${urltpl//\{n\}/$name}
    else
      case "$spec" in
        *:*)
          name=${spec%%:*}; task=${spec#*:}
          ;;
        *) echo "$spec malformed-member" >> "$out/skipped"; continue ;;
      esac
      case "$name" in ''|*[!0-9]*) echo "$name malformed-member" >> "$out/skipped"; continue ;; esac
      [ -n "$task" ] || { echo "$name malformed-member" >> "$out/skipped"; continue; }
      if [ -n "$slug" ]; then
        state=$(batch_gh_field "$name" "$slug" '.state') || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
      else
        state=$(git -C "$repo" gh pr view "$name" --json state,baseRefName,headRefOid,url --jq '.state' 2>/dev/null) \
          || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
      fi
      [ "$state" = "OPEN" ] || { echo "$name not-open" >> "$out/skipped"; continue; }
      if [ -n "$slug" ]; then
        baseref=$(batch_gh_field "$name" "$slug" '.baseRefName') || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
        head=$(batch_gh_field "$name" "$slug" '.headRefOid') || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
        url=$(batch_gh_field "$name" "$slug" '.url') || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
      else
        baseref=$(git -C "$repo" gh pr view "$name" --json baseRefName --jq '.baseRefName' 2>/dev/null) \
          || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
        head=$(git -C "$repo" gh pr view "$name" --json headRefOid --jq '.headRefOid' 2>/dev/null) \
          || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
        url=$(git -C "$repo" gh pr view "$name" --json url --jq '.url' 2>/dev/null) \
          || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
      fi
      [ "$baseref" = "${base##*/}" ] || { echo "$name not-on-base-$baseref" >> "$out/skipped"; continue; }
      [ -n "$head" ] || { echo "$name gh-read-failed" >> "$out/skipped"; continue; }
      [ -n "$url" ] || url=${urltpl//\{n\}/$name}
      refspec=${prref//\{n\}/$name}
      git -C "$scratch" fetch -q "$src" "$refspec:refs/batch/pr$name" 2>/dev/null \
        || { echo "$name fetch-failed" >> "$out/skipped"; continue; }
      head=$(git -C "$scratch" rev-parse "refs/batch/pr$name") || { echo "$name fetch-failed" >> "$out/skipped"; continue; }
    fi
    local tree new_acc
    if tree=$(git -C "$scratch" merge-tree --write-tree "$acc" "$head" 2>/dev/null); then
      tree=${tree%%$'\n'*}
      new_acc=$(git -C "$scratch" commit-tree "$tree" -p "$acc" -p "$head" -m "batch $name") \
        || { echo "$name stack-failed" >> "$out/skipped"; continue; }
      acc=$new_acc
      if [ "$noforge" = "yes" ]; then
        printf '%s %s %s %s %s\n' "$name" "$head" "$task" "$url" "$rev" >> "$out/members"
      else
        printf '%s %s %s %s\n' "$name" "$head" "$task" "$url" >> "$out/members"
      fi
    else
      echo "$name conflict" >> "$out/skipped"
    fi
  done
  printf '%s\n' "$acc" > "$out/head"
  echo "members: $(grep -c . "$out/members" 2>/dev/null || true) skipped: $(grep -c . "$out/skipped" 2>/dev/null || true) head: $acc"
}

# batch_checkout <scratch> <dir> <oid>: a fresh shared clone at one commit.
batch_checkout() {
  local scratch=$1 dir=$2 oid=$3
  rm -rf "$dir"
  git clone -q --shared --no-checkout "$scratch" "$dir" || fail "cannot clone scratch repo to $dir"
  git -C "$dir" checkout -q --detach "$oid" || fail "cannot check out $oid in $dir"
}

# batch_run_tests <dir> <lock> <cmd> -- [files...]: run the test command in
# the checkout dir under the verify lock, files as trailing arguments.
batch_run_tests() {
  local dir=$1 lock=$2 cmd=$3
  shift 3
  [ "${1:-}" = "--" ] && shift
  (
    cd "$dir" || exit 1
    BATCH_FILES=$(printf '%s\n' "$@") batch_with_lock "$lock" -- sh -c "$cmd"' "$@"' batch-test "$@"
  )
}

# batch_verify <repo> <batch> <lock> <select-cmd> <test-cmd> <test-glob>
batch_verify() {
  local repo=$1 batch=$2 lock=$3 selectcmd=$4 testcmd=$5 testglob=$6
  shift 6 || true
  [ -f "$batch/members" ] && [ -f "$batch/base" ] && [ -f "$batch/head" ] \
    || fail "batch directory is not a built batch: $batch"
  [ -n "$lock" ] || fail "verify needs --lock"
  [ -n "$selectcmd" ] || fail "verify needs --select-cmd"
  [ -n "$testcmd" ] || fail "verify needs --test-cmd"
  [ "$(grep -c . "$batch/members")" -gt 0 ] 2>/dev/null || fail "batch has no members: nothing to verify"
  local base head scratch tree basedir
  base=$(cat "$batch/base")
  head=$(cat "$batch/head")
  scratch="$batch/fetch.git"
  [ -d "$scratch" ] || fail "batch scratch repo is missing: $scratch"
  tree="$batch/tree"
  basedir="$batch/base-checkout"
  batch_checkout "$scratch" "$tree" "$head"
  batch_checkout "$scratch" "$basedir" "$base"
  local changed new selected paths
  changed=$(git -C "$scratch" diff --name-only "$base" "$head" || true)
  new=$(git -C "$scratch" diff --name-only --diff-filter=A "$base" "$head" | grep -E "$testglob" || true)
  if [ -n "$changed" ]; then
    # shellcheck disable=SC2086
    selected=$(cd "$tree" && sh -c "$selectcmd" batch-select $changed || true)
  else
    selected=
  fi
  paths=$(printf '%s\n%s\n%s' "$selected" "$new" "$(printf '%s\n' "$changed" | grep -E "$testglob" || true)" \
    | grep -v '^$' | sort -u | while IFS= read -r p; do [ -f "$tree/$p" ] && printf '%s\n' "$p"; done || true)
  {
    echo "members: $(grep -c . "$batch/members")"
    echo "lock: $(batch_lock_tier) $lock"
  } > "$batch/result"
  if [ -z "$paths" ]; then
    echo "verdict: refused (no test files selected)" >> "$batch/result"
    echo DONE >> "$batch/result"
    echo "verdict: refused (no test files selected)"
    return 1
  fi
  printf '%s\n' "$paths" > "$batch/testfiles"
  echo "test files: $(grep -c . "$batch/testfiles")" >> "$batch/result"
  local files out rc
  files=$(tr '\n' ' ' < "$batch/testfiles")
  # shellcheck disable=SC2086
  if out=$(batch_run_tests "$tree" "$lock" "$testcmd" -- $paths 2>&1); then
    rc=0
  else
    rc=$?
  fi
  printf '%s\n' "$out" >> "$batch/result"
  if [ "$rc" -eq 0 ]; then
    echo "verdict: green" >> "$batch/result"
  else
    echo "verdict: red" >> "$batch/result"
  fi
  : > "$batch/oldmain"
  local n prhead prnew t
  while read -r n prhead _rest; do
    [ -n "$n" ] || continue
    prnew=$(git -C "$scratch" diff --name-only --diff-filter=A "$base" "$prhead" | grep -E "$testglob" || true)
    if [ -z "$prnew" ]; then
      echo "$n: no new test files" >> "$batch/oldmain"
      continue
    fi
    while IFS= read -r t; do
      [ -n "$t" ] || continue
      mkdir -p "$basedir/$(dirname "$t")"
      cp "$tree/$t" "$basedir/$t"
    done <<< "$prnew"
    # shellcheck disable=SC2086
    if oldout=$(batch_run_tests "$basedir" "$lock" "$testcmd" -- $prnew 2>&1); then
      echo "$n: new tests PASS on base (prove nothing)" >> "$batch/oldmain"
    else
      echo "$n: new tests fail on base as expected" >> "$batch/oldmain"
    fi
    printf '%s\n' "$oldout" | grep -E 'passed|failed|error' | head -5 >> "$batch/oldmain" || true
  done < "$batch/members"
  echo "new-test base check:" >> "$batch/result"
  cat "$batch/oldmain" >> "$batch/result"
  grep -E 'PASS on base' "$batch/oldmain" | sed 's/^/warning: /' >> "$batch/result" || true
  echo DONE >> "$batch/result"
  if [ "$rc" -eq 0 ]; then
    echo "verdict: green ($files)"
    return 0
  fi
  echo "verdict: red"
  return 1
}

# batch_test_prefix <batch> <lock> <testcmd> <tree> <oid>: check
# one prefix out and run the recorded test files; prints green or red.
batch_test_prefix() {
  local batch=$1 lock=$2 testcmd=$3 tree=$4 oid=$5
  git -C "$tree" checkout -q --detach "$oid" || fail "cannot check out prefix $oid"
  local files
  files=$(while IFS= read -r p; do [ -f "$tree/$p" ] && printf '%s\n' "$p"; done < "$batch/testfiles")
  if [ -z "$files" ]; then
    printf 'green (no recorded files present)'
    return 0
  fi
  # shellcheck disable=SC2086
  if batch_run_tests "$tree" "$lock" "$testcmd" -- $files >/dev/null 2>&1; then
    printf 'green'
  else
    printf 'red'
  fi
}

# batch_bisect <repo> <batch> <lock> <test-cmd>
batch_bisect() {
  local repo=$1 batch=$2 lock=$3 testcmd=$4
  [ -f "$batch/members" ] && [ -f "$batch/base" ] && [ -f "$batch/testfiles" ] \
    || fail "batch is not a verified batch: $batch (run verify first)"
  [ -n "$lock" ] || fail "bisect needs --lock"
  local scratch="$batch/fetch.git" tree="$batch/tree" base
  base=$(cat "$batch/base")
  [ -d "$scratch" ] || fail "batch scratch repo is missing: $scratch"
  [ -d "$tree" ] || fail "batch checkout is missing: $tree (run verify first)"
  local names=() heads=()
  while read -r n prhead _rest; do
    [ -n "$n" ] || continue
    names+=("$n")
    heads+=("$prhead")
  done < "$batch/members"
  [ "${#names[@]}" -gt 0 ] || fail "batch has no members: nothing to bisect"
  local prefix_oids=() acc tree_out i
  acc=$base
  for i in "${!names[@]}"; do
    if tree_out=$(git -C "$scratch" merge-tree --write-tree "$acc" "${heads[$i]}" 2>/dev/null); then
      tree_out=${tree_out%%$'\n'*}
      acc=$(git -C "$scratch" commit-tree "$tree_out" -p "$acc" -p "${heads[$i]}" -m "bisect ${names[$i]}")
      prefix_oids+=("$acc")
    else
      fail "prefix through ${names[$i]} no longer stacks cleanly"
    fi
  done
  {
    echo "members: ${#names[@]}"
    echo "lock: $(batch_lock_tier) $lock"
  } > "$batch/bisect"
  local lo=0 hi=$((${#names[@]} - 1)) mid verdict
  verdict=$(batch_test_prefix "$batch" "$lock" "$testcmd" "$tree" "${prefix_oids[$hi]}")
  echo "prefix ${names[$hi]}: $verdict" >> "$batch/bisect"
  if [ "$verdict" != "red" ]; then
    echo "culprit: none (full stack is green)" >> "$batch/bisect"
    echo DONE >> "$batch/bisect"
    echo "culprit: none (full stack is green)"
    return 1
  fi
  while [ "$lo" -lt "$hi" ]; do
    mid=$(((lo + hi) / 2))
    verdict=$(batch_test_prefix "$batch" "$lock" "$testcmd" "$tree" "${prefix_oids[$mid]}")
    echo "prefix ${names[$mid]}: $verdict" >> "$batch/bisect"
    if [ "$verdict" = "red" ]; then
      hi=$mid
    else
      lo=$((mid + 1))
    fi
  done
  echo "culprit: ${names[$lo]}" >> "$batch/bisect"
  echo DONE >> "$batch/bisect"
  echo "culprit: ${names[$lo]}"
  return 0
}

# batch_live_head <repo> <slug> <noforge> <name> <extra>: the PR's
# current head commit from the forge, or from the local repo in --no-forge.
batch_live_head() {
  local repo=$1 slug=$2 noforge=$3 name=$4 extra=$5
  if [ "$noforge" = "yes" ]; then
    git -C "$repo" rev-parse "$extra" 2>/dev/null || return 1
  elif [ -n "$slug" ]; then
    batch_gh_field "$name" "$slug" '.headRefOid' || return 1
  else
    git -C "$repo" gh pr view "$name" --json headRefOid --jq '.headRefOid' 2>/dev/null || return 1
  fi
}

# batch_merge <repo> <batch> <slug> <noforge> <url-tpl>
batch_merge() {
  local repo=$1 batch=$2 slug=$3 noforge=$4 urltpl=$5
  [ -f "$batch/members" ] || fail "batch is not a built batch: $batch"
  [ "$(grep -c . "$batch/members")" -gt 0 ] 2>/dev/null || fail "batch has no members: nothing to merge"
  [ -x "$MERGE_BIN" ] || fail "merge path is missing: $MERGE_BIN"
  : > "$batch/merge-log"
  : > "$batch/merge-refused"
  local rc=0 n recorded task url extra live
  while read -r n recorded task url extra; do
    [ -n "$n" ] || continue
    [ -n "$url" ] || url=${urltpl//\{n\}/$n}
    live=$(batch_live_head "$repo" "$slug" "$noforge" "$n" "${extra:-}") \
      || { echo "$n: head unreadable, refused" | tee -a "$batch/merge-refused"; rc=1; continue; }
    if [ "$live" != "$recorded" ]; then
      echo "$n: head moved after build ($recorded -> $live), refused" | tee -a "$batch/merge-refused"
      rc=1
      continue
    fi
    if "$MERGE_BIN" "$task" "$url" >> "$batch/merge-log" 2>&1; then
      echo "$n: merged" | tee -a "$batch/merge-log"
    else
      echo "$n: merge failed, see merge-log" | tee -a "$batch/merge-refused"
      rc=1
    fi
  done < "$batch/members"
  echo DONE >> "$batch/merge-log"
  return "$rc"
}

# Option parsing shared by every subcommand. Sets globals from flags and
# leaves "$REMAINING" holding the positional members.
parse_common() {
  REPO=''
  OUT=''
  BATCH=''
  BASE=$DEFAULT_BASE
  REMOTE=$DEFAULT_REMOTE
  SLUG=''
  NOFORGE=no
  PRREF=$DEFAULT_PR_REF
  URLTPL='local:{n}'
  LOCK=''
  SELECTCMD=''
  TESTCMD=''
  TESTGLOB=$DEFAULT_TEST_GLOB
  NOMERGE=no
  REMAINING=''
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) REPO=$2; shift 2 ;;
      --repo=*) REPO=${1#--repo=}; shift ;;
      --out) OUT=$2; shift 2 ;;
      --out=*) OUT=${1#--out=}; shift ;;
      --batch) BATCH=$2; shift 2 ;;
      --batch=*) BATCH=${1#--batch=}; shift ;;
      --base) BASE=$2; shift 2 ;;
      --base=*) BASE=${1#--base=}; shift ;;
      --remote) REMOTE=$2; shift 2 ;;
      --remote=*) REMOTE=${1#--remote=}; shift ;;
      --slug) SLUG=$2; shift 2 ;;
      --slug=*) SLUG=${1#--slug=}; shift ;;
      --no-forge) NOFORGE=yes; shift ;;
      --pr-ref) PRREF=$2; shift 2 ;;
      --pr-ref=*) PRREF=${1#--pr-ref=}; shift ;;
      --pr-url-template) URLTPL=$2; shift 2 ;;
      --pr-url-template=*) URLTPL=${1#--pr-url-template=}; shift ;;
      --lock) LOCK=$2; shift 2 ;;
      --lock=*) LOCK=${1#--lock=}; shift ;;
      --select-cmd) SELECTCMD=$2; shift 2 ;;
      --select-cmd=*) SELECTCMD=${1#--select-cmd=}; shift ;;
      --test-cmd) TESTCMD=$2; shift 2 ;;
      --test-cmd=*) TESTCMD=${1#--test-cmd=}; shift ;;
      --test-glob) TESTGLOB=$2; shift 2 ;;
      --test-glob=*) TESTGLOB=${1#--test-glob=}; shift ;;
      --no-merge) NOMERGE=yes; shift ;;
      -h|--help|help) print_help; exit 0 ;;
      --) shift; break ;;
      -*) usage_error "unknown flag: $1" ;;
      *) break ;;
    esac
  done
  REMAINING="$*"
}

main() {
  [ $# -ge 1 ] || { print_help >&2; exit 2; }
  local cmd=$1
  shift
  case "$cmd" in
    -h|--help|help) print_help; exit 0 ;;
  esac
  parse_common "$@"
  # shellcheck disable=SC2086
  set -- $REMAINING
  case "$cmd" in
    build)
      [ -n "$REPO" ] || usage_error "build needs --repo"
      [ -n "$OUT" ] || usage_error "build needs --out"
      [ $# -ge 1 ] || usage_error "build needs at least one MEMBER"
      batch_build "$REPO" "$OUT" "$BASE" "$REMOTE" "$SLUG" "$NOFORGE" "$PRREF" "$URLTPL" -- "$@"
      ;;
    verify)
      [ -n "$REPO" ] || usage_error "verify needs --repo"
      [ -n "$BATCH" ] || usage_error "verify needs --batch"
      batch_verify "$REPO" "$BATCH" "$LOCK" "$SELECTCMD" "$TESTCMD" "$TESTGLOB"
      ;;
    bisect)
      [ -n "$REPO" ] || usage_error "bisect needs --repo"
      [ -n "$BATCH" ] || usage_error "bisect needs --batch"
      batch_bisect "$REPO" "$BATCH" "$LOCK" "$TESTCMD"
      ;;
    merge)
      [ -n "$REPO" ] || usage_error "merge needs --repo"
      [ -n "$BATCH" ] || usage_error "merge needs --batch"
      batch_merge "$REPO" "$BATCH" "$SLUG" "$NOFORGE" "$URLTPL"
      ;;
    run)
      local rc
      [ -n "$REPO" ] || usage_error "run needs --repo"
      [ -n "$OUT" ] || usage_error "run needs --out"
      [ $# -ge 1 ] || usage_error "run needs at least one MEMBER"
      batch_build "$REPO" "$OUT" "$BASE" "$REMOTE" "$SLUG" "$NOFORGE" "$PRREF" "$URLTPL" -- "$@"
      if batch_verify "$REPO" "$OUT" "$LOCK" "$SELECTCMD" "$TESTCMD" "$TESTGLOB"; then
        if [ "$NOMERGE" = "yes" ]; then
          echo "green, merge suppressed by --no-merge"
          return 0
        fi
        batch_merge "$REPO" "$OUT" "$SLUG" "$NOFORGE" "$URLTPL"
      else
        rc=$?
        [ "$rc" -eq 1 ] || return "$rc"
        batch_bisect "$REPO" "$OUT" "$LOCK" "$TESTCMD" || true
        return 1
      fi
      ;;
    *) usage_error "unknown subcommand: $cmd" ;;
  esac
}

main "$@"
