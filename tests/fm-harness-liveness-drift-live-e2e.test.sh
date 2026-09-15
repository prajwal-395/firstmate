#!/usr/bin/env bash
# tests/fm-harness-liveness-drift-live-e2e.test.sh - default-on drift guard proving
# every INSTALLED harness is still classified `alive` by the agent-process
# liveness probe (bin/fm-agent-process-lib.sh, read through the herdr adapter)
# AND still identified by the harness-detection ancestry walk
# (bin/fm-harness.sh).
#
# Why this file exists: both verdicts depend on how a harness names its own
# process, which is a surface the harness vendor controls and changes without
# notice. Claude Code began reporting its version string as its process name and
# became unattributable, which silently degraded supervision. A regression that
# only a real harness release can cause needs a check that runs real harnesses;
# a stubbed agent cannot see it, and neither can a table of names transcribed
# from a previous release.
#
# Detection carries the same exposure for a second reason: a structural ancestor
# now outranks an environment marker (bin/fm-harness.sh owns that boundary), so
# a harness whose process name stops matching no longer merely loses a fast
# path - the walk keeps climbing and can reach a DIFFERENT harness that really
# is further up the tree. This guard is what catches that at the release that
# causes it.
#
# Each harness is launched bare in its own Herdr lab pane, with no prompt, so
# this consumes no model tokens. The launch uses whatever credentials the
# harness already has; an unauthenticated harness still starts its process,
# which is all the liveness probe reads.
#
# Portable serial CI installs the public Pi package but no credentials, so this
# guard checks that available token-free surface there and runs against every installed
# harness on more capable hosts. The portable counterpart in
# tests/fm-agent-process-liveness.test.sh pins the classifier logic in CI. Run this
# guard after any harness upgrade and before trusting refreshed evidence.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

fm_live_gate default-on FM_HARNESS_LIVENESS_DRIFT herdr jq

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_HELPER="$ROOT/bin/fm-herdr-lab.sh"

fail() { printf 'not ok - %s\n' "$1" >&2; cleanup_all; exit 1; }
pass() { printf 'ok - %s\n' "$1"; }
note() { printf '# %s\n' "$1"; }

# The suite itself may run inside a herdr pane; drop that launcher binding so
# every adapter call below resolves inside the lab session, not the
# developer's own.
unset HERDR_ENV HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID HERDR_SOCKET_PATH HERDR_SESSION
LAB=$(mktemp -d "${TMPDIR:-/tmp}/fm-liveness-drift.XXXXXX")
SESSION=$("$LAB_HELPER" name liveness-drift) || fail "could not mint a lab session name"

cleanup_all() {
  "$LAB_HELPER" teardown "$SESSION" >/dev/null 2>&1 || true
  [ -n "${LAB:-}" ] && rm -rf "$LAB"
}
trap cleanup_all EXIT
"$LAB_HELPER" provision "$SESSION" || fail "could not provision the isolated Herdr lab session"
export HERDR_SESSION="$SESSION"

mkdir -p "$LAB/wt"
WSID=$(herdr workspace create --cwd "$LAB/wt" --label "drift-$$" --no-focus --session "$SESSION" \
  | jq -r '.result.workspace.workspace_id // empty') || fail "could not create the drift workspace"
[ -n "$WSID" ] || fail "could not parse the drift workspace id"

# shellcheck source=/dev/null
. "$ROOT/bin/fm-backend.sh"
# shellcheck source=/dev/null
. "$ROOT/bin/fm-cursor-lib.sh"

# Kimi is not required to be on PATH; mirror bin/fm-spawn.sh's own resolution
# order so this guard covers the same binary firstmate would actually launch.
resolve_harness_binary() {  # <harness>
  local harness=$1 candidate
  # cursor is resolved FIRST, before the generic PATH lookup, and only through
  # the verified owner fm-spawn uses. The Cursor agent never installs as
  # `cursor`: it installs as `cursor-agent` plus the legacy alias `agent`. A
  # machine that also has the Cursor editor does have an executable `cursor` on
  # PATH, and launching that one exits immediately, leaving a bare shell in the
  # pane that this guard then reports as liveness drift the classifier can do
  # nothing about. Asking the owner first also keeps an unrelated executable
  # named `agent` rejected here exactly as it would be at launch.
  if [ "$harness" = cursor ]; then
    fm_cursor_resolve_binary 2>/dev/null && return 0
    return 1
  fi
  candidate=$(command -v "$harness" 2>/dev/null || true)
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  if [ "$harness" = kimi ] && [ -n "${HOME:-}" ] && [ -x "$HOME/.kimi-code/bin/kimi" ]; then
    printf '%s\n' "$HOME/.kimi-code/bin/kimi"
    return 0
  fi
  return 1
}

CHECKED=0
SKIPPED=

# The verified adapters, in the order the harness-adapters skill router records
# them. An adapter that gains a verified launch path belongs here too.
# muse matters most of all here: its launcher execs a VERSION-SUFFIXED binary,
# so the live process name changes on every auto-update and its install path
# carries no `muse` component to fall back on. That is precisely the drift this
# guard exists to catch, and only a real muse release can produce it.
# cursor matters for the same reason muse does, from the other direction: it
# runs as a bundled node script, so its pane title is a bare `node` that no name
# pattern can own, and identity has to come from its install path or argv[0].
for harness in claude codex opencode pi pi-signed grok kimi cursor muse; do
  if ! bin_path=$(resolve_harness_binary "$harness"); then
    SKIPPED="$SKIPPED $harness"
    note "skip: $harness is not installed on this machine, so its classification is unverified here"
    continue
  fi

  version=$("$bin_path" --version 2>/dev/null | head -1 | tr -d '\r') || version=
  [ -n "$version" ] || version="unknown"

  target=""
  # cursor blocks on a workspace-trust prompt in a directory it has never seen,
  # which would hang this probe rather than classify anything; --trust is the
  # same flag fm-spawn passes for the same reason.
  launch_args=""
  [ "$harness" = cursor ] && launch_args="--trust"
  # A fresh tab per harness; its root pane runs the harness bare through
  # `pane run`, a no-prompt launch.
  create_out=$(herdr tab create --workspace "$WSID" --cwd "$LAB/wt" --label "$harness" --no-focus --session "$SESSION" 2>/dev/null) \
    || fail "$harness ($version): could not create a tab for the liveness probe"
  tab_id=$(printf '%s' "$create_out" | jq -r '.result.tab.tab_id // empty' 2>/dev/null)
  pane_id=$(printf '%s' "$create_out" | jq -r '.result.root_pane.pane_id // empty' 2>/dev/null)
  { [ -n "$tab_id" ] && [ -n "$pane_id" ]; } \
    || fail "$harness ($version): could not parse a tab/pane id from the tab create response"
  target="$SESSION:$pane_id"
  # Wait for the pane's shell before submitting the launch: typing into a
  # pane with no shell yet would lose the command.
  shell_pid=
  for _ in $(seq 1 150); do
    shell_pid=$(herdr pane process-info --pane "$pane_id" --session "$SESSION" 2>/dev/null \
      | jq -r '.result.process_info.shell_pid // empty' 2>/dev/null)
    [ -n "$shell_pid" ] && break
    sleep 0.2
  done
  [ -n "$shell_pid" ] || fail "$harness ($version): the probe pane never grew a shell"
  # shellcheck disable=SC2086  # deliberate: an empty value must add no argument
  herdr pane run "$pane_id" "$bin_path $launch_args" --session "$SESSION" >/dev/null 2>&1 \
    || fail "$harness ($version): could not launch the harness in the probe pane"

  state=
  for _ in $(seq 1 300); do
    state=$(fm_backend_agent_state herdr "$target")
    [ "$state" = alive ] && break
    sleep 0.2
  done

  info=$(herdr pane process-info --pane "$pane_id" --session "$SESSION" 2>/dev/null || true)
  comms=$(printf '%s' "$info" | jq -r '.result.process_info.foreground_processes[]? | "\(.name // "") \(((.argv // []) | join(" ")))"' 2>/dev/null | tr '\n' ';')

  [ "$state" = alive ] || fail \
    "LIVENESS DRIFT: $harness $version is running but classifies '$state', not 'alive'. Supervision and lifecycle control treat this endpoint as unattributable. Observed foreground processes [$comms]. Teach bin/fm-agent-process-lib.sh's fm_agent_process_classify_name the identity this release actually reports."

  note "$harness $version: foreground=[$comms]"

  pass "harness liveness: $harness $version classifies alive"

  # Detection: ask the ancestry walk what it makes of this real harness process.
  # Both Pi identities share one launcher name, so ancestry can only ever prove
  # the family; only the launch-boundary marker selects the signed identity.
  expect_harness=$harness
  [ "$harness" = pi-signed ] && expect_harness=pi
  # Probe from BELOW the pane shell, not the shell alone. The shipped
  # guarantee is a strength claim: detect_own hands an args-strength verdict back
  # to a retained foreign marker, so a harness is only protected where the walk
  # reaches it at comm strength. A harness that ships as a thin interpreter shim
  # spawning its native binary as a CHILD is args strength from the shell
  # process and comm strength from below that child - which is where firstmate's own
  # detection actually runs, as a tool subprocess. Probing only the shell would
  # therefore pass on evidence the guarantee does not rest on, and would keep
  # passing if a release stopped spawning the native child at all.
  #
  # The vantage set is the UPWARD path from the deepest foreground descendant, not
  # every descendant in the subtree, because harness_ancestry only ever climbs: a
  # sibling branch is a vantage firstmate's own detection can never occupy.
  # Herdr's process-info already scopes the selection to the pane's foreground
  # group, which keeps a process left running in the background out of the
  # selection as well.
  #
  # The reject-other-harness cross-check below judges COMM-strength vantages only.
  # An args-strength verdict is path-ambiguous by construction: harness_ancestry's
  # bare-interpreter branch matches a harness name anywhere in the script path, so a
  # harness-spawned MCP server running as `node <home>/.claude/mcp/<server>.js`
  # answers `args claude` purely from the .claude path component, and such a server
  # is normally a child of the agent binary rather than a sibling of it, so it can
  # be the deepest descendant and sit ON this path. That ambiguity is the sole source
  # of the false failure; a comm-strength verdict carries the real process name and
  # cannot be produced that way. The comm-strength REQUIREMENT is unchanged - some
  # vantage on the path must still name the expected harness at comm strength,
  # because detect_own hands an args-strength verdict straight back to a retained
  # foreign marker.
  # The native binary can take a moment to appear, so poll for it.
  verdicts=
  for _ in $(seq 1 150); do
    info=$(herdr pane process-info --pane "$pane_id" --session "$SESSION" 2>/dev/null || true)
    shell_pid=$(printf '%s' "$info" | jq -r '.result.process_info.shell_pid // empty' 2>/dev/null)
    fg_pids=$(printf '%s' "$info" | jq -r '.result.process_info.foreground_processes[]? | select((.pid | type) == "number") | .pid' 2>/dev/null | tr '\n' ' ')
    if [ -n "$shell_pid" ]; then
      # shellcheck disable=SC2086  # deliberate: the foreground pids are separate arguments
      verdicts=$("$ROOT/bin/fm-harness.sh" ancestry-descent "$shell_pid" $fg_pids 2>/dev/null || true)
    fi
    case "$verdicts" in *"comm $expect_harness"*) break ;; esac
    sleep 0.2
  done

  drift_context="Observed foreground processes [$comms]; observed ancestry verdicts [$(printf '%s' "$verdicts" | tr '\n' ';')]."

  [ -n "$verdicts" ] || fail \
    "DETECTION DRIFT: $harness $version is running but the ancestry walk reports nothing from the pane process or any vantage below it, so firstmate cannot identify this session at all. $drift_context Teach bin/fm-harness.sh's harness_ancestry the name this release actually reports."

  SAW_COMM=0
  while read -r strength named; do
    [ -n "$strength" ] || continue
    [ "$strength" = comm ] || continue
    [ "$named" = "$expect_harness" ] || fail \
      "DETECTION DRIFT: $harness $version is running but a comm-strength vantage point on the upward path through its own session resolves to '$named', not '$expect_harness'. bin/fm-harness.sh lets a structural ancestor outrank an environment marker, so an unmatched process name can resolve to a DIFFERENT harness further up the tree instead of merely losing a fast path. $drift_context Teach bin/fm-harness.sh's harness_ancestry the name this release actually reports."
    SAW_COMM=1
  done <<EOF
$verdicts
EOF

  [ "$SAW_COMM" = 1 ] || fail \
    "DETECTION DRIFT: $harness $version is identified only at interpreter-args strength, from no vantage point on the upward path through its session at comm strength. detect_own hands an args-strength verdict back to a retained foreign marker, so a stale CLAUDECODE would silently rename this session even though this guard sees the right identity. $drift_context Restore a process name bin/fm-harness.sh's harness_ancestry can match structurally, or teach it the name this release reports."

  note "$harness $version: ancestry verdicts=[$(printf '%s' "$verdicts" | tr '\n' ';')]"
  pass "harness detection: $harness $version is identified by the ancestry walk at comm strength"
  CHECKED=$((CHECKED + 1))
done

[ "$CHECKED" -gt 0 ] || fail \
  "no verified harness is installed here, so this run proved nothing; install at least one harness before trusting a pass"

if [ -n "$SKIPPED" ]; then
  note "unverified on this machine (not installed):$SKIPPED"
fi
note "checked $CHECKED installed harness(es)"

cleanup_all
trap - EXIT
