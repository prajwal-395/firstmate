#!/usr/bin/env bash
#
# Guard the two Resolve installers against stamping a DISPOSABLE checkout
# into a DURABLE install.
#
# Both installers write this checkout's absolute path into a surface that
# outlives the checkout - Workspace > Scripts copies, and the Workflow
# Integration plugin directory.  Run one from a lane that is later
# reclaimed and the installed surface silently points at a directory that
# no longer exists (2026-08-31: com.videoeditingpilot.vep installed from
# a .treehouse lane, found dead by audit on 2026-09-14, nothing detected
# it in between).
#
# What "disposable" means here is a PROPERTY of the checkout, not a path
# substring.  A linked git worktree (`git rev-parse --git-dir` differing
# from `git rev-parse --git-common-dir`) is managed by a worktree tool
# and may be reclaimed at any time; that signal generalises to every
# worktree tool, where a `.treehouse` grep would only guard the one
# incident this was born from.  A checkout that is not a git checkout at
# all (a tarball, a plain copy) is a third answer - "unknown" - and it is
# refused too, not passed: stamping a path nobody can vouch for is the
# same trap with less evidence.
#
# Sourced, never executed:
#
#     . "${SCRIPTS_DIR}/lib/guard_durable_checkout.sh"
#     durability="$(vep_checkout_durability "${REPO_ROOT}")"
#
# Prints exactly one of `durable`, `linked-worktree` or `unknown` on
# stdout and always returns 0.  The CALLER decides what that means for
# its mode; the convention both installers follow is:
#
#   install    refuse `linked-worktree` and `unknown`, unless the
#              deliberate per-invocation flag `--allow-disposable` was
#              passed.  The flag is per-invocation ON PURPOSE: an
#              environment variable could be exported once in a shell
#              profile and silently disable the guard forever.
#   --dry-run  report the verdict and continue; it writes nothing.
#   --uninstall  no guard at all; removing a stale install must work
#              from anywhere, including the lane that caused it.

# One of `durable`, `linked-worktree`, `unknown`.  Always returns 0.
vep_checkout_durability() {
    local root="${1:-}"
    local git_dir="" common_dir=""

    if [ -n "${root}" ] && [ -d "${root}" ] \
        && git_dir="$(_vep_git_path "${root}" "git-dir")" \
        && common_dir="$(_vep_git_path "${root}" "git-common-dir")"; then
        if [ "${git_dir}" != "${common_dir}" ]; then
            echo "linked-worktree"
        else
            echo "durable"
        fi
    else
        echo "unknown"
    fi
    return 0
}

# Absolute path for a `git rev-parse` dir flag, resolved against the
# checkout so a relative answer (old git, odd config) still compares.
# Prints the path; returns nonzero when git cannot answer at all.
_vep_git_path() {
    local root="$1" what="$2" out=""
    # NOTE: there is no `--absolute-git-common-dir` (git has
    # `--absolute-git-dir` only), so absoluteness comes from
    # `--path-format=absolute`, which both dir flags honour.
    if out="$(git -C "${root}" rev-parse --path-format=absolute \
            "--${what}" 2>/dev/null)"; then
        printf '%s\n' "${out}"
        return 0
    fi
    if out="$(git -C "${root}" rev-parse "--${what}" 2>/dev/null)"; then
        case "${out}" in
            /*) printf '%s\n' "${out}" ;;
            *)  printf '%s\n' "${root}/${out}" ;;
        esac
        return 0
    fi
    return 1
}

# The refusal both installers print.  Names the property that fired, the
# consequence of proceeding, and the explicit override - and nothing else
# grants it, so a globally exported variable cannot silently disarm this.
# $1 = repo root, $2 = durability word, $3 = installer argv[0] for the hint.
vep_refuse_disposable_checkout() {
    local root="$1" durability="$2" installer="$3" why=""
    case "${durability}" in
        linked-worktree)
            why="it is a linked git worktree, and worktree lanes are reclaimed"
            ;;
        unknown)
            why="it is not a recognisable git checkout, so nothing vouches that it is durable"
            ;;
        *)  why="it is not a durable checkout" ;;
    esac
    cat >&2 <<EOF
✗ refusing to install from ${root}:
  ${why}. The installed copy would carry this path, and break silently
  when the directory goes away.
  Re-run from the durable checkout, or pass --allow-disposable to $installer
  to stamp this checkout deliberately. (--dry-run reports, --uninstall
  always works; neither is blocked.)
EOF
}
