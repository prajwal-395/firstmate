#!/usr/bin/env bash
# tests/fm-agent-process-liveness.test.sh - portable regression for the
# agent-process name classifier (bin/fm-agent-process-lib.sh), the single
# owner of the process-name vocabulary every liveness signal shares.
#
# It calls fm_agent_process_classify_name directly with harness, shell, and
# lookalike names, so it runs everywhere with no backend, no harness, and no
# credentials. The live per-harness counterpart is
# tests/fm-harness-liveness-drift-live-e2e.test.sh, which proves the same
# classifier against real installed harnesses. Deeper slices (Cursor's
# node/install-tree rule, omp's anchoring, agy's anchoring) live in their own
# harness suites.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=/dev/null
. "$ROOT/bin/fm-agent-process-lib.sh"

is_agent() { [ "$(fm_agent_process_classify_name "$1" "${2:-}")" = agent ]; }
is_shell() { [ "$(fm_agent_process_classify_name "$1" "${2:-}")" = shell ]; }
not_agent() { [ "$(fm_agent_process_classify_name "$1" "${2:-}")" != agent ]; }

test_verified_harness_names_classify_agent() {
  local name
  for name in claude codex opencode pi pi-signed pi-launcher grok kimi rovo omp agy; do
    is_agent "$name" || fail "'$name' must classify agent"
  done
  is_agent "muse-bin-0.1.0-R708.1" || fail "muse's version-suffixed binary must classify agent"
  is_agent "/opt/omp/bin/omp" || fail "an omp install path must classify agent"
  is_agent "node" "/opt/cursor/bin/cursor-agent" || fail "node with a cursor-agent argv0 must classify agent"
  pass "agent-process liveness: verified harness names classify agent"
}

test_shells_classify_shell() {
  local name
  for name in zsh bash sh dash fish; do
    is_shell "$name" || fail "'$name' must classify shell"
  done
  pass "agent-process liveness: interactive shells classify shell"
}

test_lookalikes_never_classify_agent() {
  local name
  for name in musescore amuse muse-binary muse-bind ompd comp magyk node /usr/bin/node; do
    not_agent "$name" || fail "'$name' must never classify agent"
  done
  not_agent "agent" "/usr/local/bin/agent" || fail "a generic agent argv0 must never classify agent"
  pass "agent-process liveness: lookalike names never classify agent"
}

test_verified_harness_names_classify_agent
test_shells_classify_shell
test_lookalikes_never_classify_agent
