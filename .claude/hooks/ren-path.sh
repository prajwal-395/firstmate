#!/bin/sh
# SessionStart: put this worktree's `bin` on the session PATH.
#
# Appends `PATH=<worktree>/bin:$PATH` to `$CLAUDE_ENV_FILE`, so `ren`
# resolves with no `bin/` prefix in Claude Code sessions. The worktree is
# resolved at runtime (`git rev-parse --show-toplevel`, else the physical
# cwd), so one checked-in hook serves every disposable worktree.
# Idempotent: a PATH already carrying the bin dir is left alone, and so
# is a file that already names it (a second SessionStart in one session
# must not duplicate the entry whether or not the file was sourced yet).
# No-op (exit 0) outside Claude Code, where `$CLAUDE_ENV_FILE` is unset.
set -u
if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
  exit 0
fi
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P 2>/dev/null || true)"
[ -n "${ROOT:-}" ] || exit 0
BIN="$ROOT/bin"
case ":${PATH:-}:" in
  *":$BIN:"*) exit 0 ;;
esac
if grep -q -F "$BIN" "$CLAUDE_ENV_FILE" 2>/dev/null; then
  exit 0
fi
printf 'PATH=%s/bin:%s\n' "$ROOT" "${PATH:-}" >> "$CLAUDE_ENV_FILE"
