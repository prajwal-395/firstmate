#!/usr/bin/env bash
# Refuse `git stash` inside a fleet worker lane, and install that refusal as a
# per-task git wrapper for a fleet launch.
#
# Usage:
#   fm-git-stash-guard.sh install <dir>
#       Install <dir>/git: a git wrapper that refuses the stash command for the
#       worker lane that owns <dir> and hands every other git command to the
#       real git. The wrapper reaches the worker only through the PATH of its
#       own pane (bin/fm-spawn.sh prepends <dir> there and in the launch
#       command), so the captain's own shells and firstmate itself keep an
#       ordinary git.
#
# WHY THIS EXISTS. Every worktree of one repository shares a single
# refs/stash. A stash made in one lane is pushed onto that shared stack, and a
# `git stash pop` in another lane pops the same entry and lands the first
# lane's changes in the second lane's worktree. On 2026-10-21 three worker
# lanes of one repository raced the shared stash that way. Nothing was lost,
# because each lane's own branch kept the commits, but the hazard is a silent
# cross-lane edit, and the alternative a worker is told to use instead -
# committing to its own branch - is always available.
#
# The refusal names that alternative, and it covers the whole lane: a stash
# made anywhere in the lane's worktree is the same shared stash.
#
# The refusal is mechanical rather than advisory on purpose. A brief line
# telling a worker not to stash is guidance the worker may ignore, while a
# refused command cannot be ignored by accident.
set -u

usage() {
  cat >&2 <<'EOF'
usage:
  fm-git-stash-guard.sh install <dir>
EOF
  exit 2
}

# The wrapper body. It is written verbatim, so every path it needs is resolved
# at run time inside the lane rather than baked in at install time.
write_guard() {
  local dest=$1
  cat >"$dest" <<'GUARD'
#!/usr/bin/env bash
# Firstmate per-task git guard: refuses `git stash` in this worker lane.
#
# Every worktree of one repository shares a single refs/stash, so a stash made
# here can be popped by another lane and land that lane's changes in this
# worktree. Commit the work to this lane's own branch instead.
#
# Every other git command is handed to the next git on PATH that is not this
# guard, so a lane wrapper already on PATH keeps its own protection and the
# real git still runs.
set -u

# The directory this guard was found under, as the PATH entry named it, and the
# same directory resolved physically. The two differ when the PATH entry is a
# symlink, and a guard that hands off to itself would recurse forever.
SELF_ENTRY=$(dirname -- "${BASH_SOURCE[0]}")
SELF_DIR=$(cd -- "$SELF_ENTRY" 2>/dev/null && pwd -P) || SELF_DIR=$SELF_ENTRY
SELF_BASE=${SELF_ENTRY##*/}

# The subcommand is the first argument that is neither a global git option nor
# the value of one.
subcommand=
expect_value=0
for arg in "$@"; do
  if [ "$expect_value" = 1 ]; then
    expect_value=0
    continue
  fi
  case "$arg" in
  --)
    continue
    ;;
  -C | -c | --git-dir | --work-tree | --namespace | --super-prefix | --config-env)
    expect_value=1
    continue
    ;;
  --git-dir=* | --work-tree=* | --namespace=* | --super-prefix=* | --config-env=* | --exec-path=*)
    continue
    ;;
  -?*)
    continue
    ;;
  *)
    subcommand=$arg
    break
    ;;
  esac
done

if [ "$subcommand" = "stash" ]; then
  {
    echo "fatal: Firstmate protection: 'git stash' is disabled in this worker lane."
    echo "Every worktree of this repository shares one refs/stash, so a stash made here can be popped by another lane and land that lane's changes in this worktree."
    echo "Commit your work to your own branch instead, for example: git add -A && git commit -m \"wip: <what changed>\"."
  } >&2
  exit 128
fi

# Hand off to the next git on PATH that is not this guard. This guard's own
# directory also leaves PATH for the hand-off, so the git that runs is never
# this guard again, whatever the next match on PATH is.
rest_path=
remaining=${PATH:-}
while [ -n "$remaining" ]; do
  case "$remaining" in
  *:*)
    entry=${remaining%%:*}
    remaining=${remaining#*:}
    ;;
  *)
    entry=$remaining
    remaining=
    ;;
  esac
  if [ "$entry" = "$SELF_ENTRY" ]; then
    continue
  fi
  if [ "${entry##*/}" = "$SELF_BASE" ]; then
    entry_real=$(cd -- "$entry" 2>/dev/null && pwd -P) || entry_real=
    [ "$entry_real" = "$SELF_DIR" ] && continue
  fi
  if [ -z "$rest_path" ]; then
    rest_path=$entry
  else
    rest_path=$rest_path:$entry
  fi
done

if [ -z "$rest_path" ]; then
  rest_path=${PATH:-}
fi

next_git=
while IFS= read -r candidate; do
  [ -n "$candidate" ] || continue
  [ "$candidate" = "$SELF_ENTRY/git" ] && continue
  candidate_real=$(cd -- "$(dirname -- "$candidate")" 2>/dev/null && pwd -P)/$(basename -- "$candidate")
  [ "$candidate_real" = "$SELF_DIR/git" ] && continue
  next_git=$candidate
  break
done < <(type -p -a git 2>/dev/null)

if [ -z "$next_git" ]; then
  echo "fatal: Firstmate git guard: cannot find the real git binary on PATH (only this guard is visible). Check your PATH." >&2
  exit 127
fi

exec env PATH="$rest_path" "$next_git" "$@"
GUARD
  chmod 500 "$dest"
}

install_guard() {
  local dir=$1
  [ -n "$dir" ] || usage
  chmod u+w "$dir" 2>/dev/null || true
  mkdir -p "$dir" || return 1
  chmod 700 "$dir" 2>/dev/null || true
  dir=$(CDPATH='' cd -- "$dir" && pwd -P) || return 1
  write_guard "$dir/git" || return 1
  chmod 500 "$dir" 2>/dev/null || true
}

CMD=${1:-}
case "$CMD" in
install)
  [ "$#" -eq 2 ] || usage
  install_guard "$2"
  ;;
-h | --help)
  usage
  ;;
'')
  usage
  ;;
*)
  usage
  ;;
esac
