#!/usr/bin/env bash
# Behavior tests for the advisory planning-compiler overlay on Bearings payloads.
set -u

# shellcheck source=tests/lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SCRIPT="$ROOT/bin/fm-plan-frontier.sh"
BOARD="$ROOT/bin/fm-bearings-board.sh"
TMP_ROOT=$(fm_test_tmproot fm-plan-frontier)

unset TASKS_AXI_FILE TASKS_AXI_BACKEND FM_HOME FM_ROOT_OVERRIDE \
  FM_DATA_OVERRIDE FM_STATE_OVERRIDE FM_CONFIG_OVERRIDE FM_PROJECTS_OVERRIDE PC_BIN

seed_fixture() {  # <dir>
  mkdir -p "$1/data" "$1/out" "$1/bin"
  cat > "$1/data/backlog.md" <<'EOF'
# Backlog

## Queued
- [ ] pa-01 - Ready candidate (repo: proj-a) (kind: ship)
- [ ] pa-02 - Estimate missing (repo: proj-a) (kind: ship)

## In flight
- [ ] pa-08 - Already underway (repo: proj-a) (kind: ship)
EOF
  cat > "$1/bin/tasks-axi" <<'EOF'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$TASKS_AXI_LOG"
case "${1-}" in
  list)
    case "${3-}" in
      queued)
        printf '  pa-01, Ready candidate\n  pa-02, Estimate missing\n'
        ;;
      in_flight)
        printf '  pa-08, Already underway\n'
        ;;
      held) : ;;
      *) exit 2 ;;
    esac
    ;;
  show)
    case "${2-}" in
      pa-01)
        printf 'task: pa-01\n  id: pa-01\n  title: Ready candidate\n  state: queued\n  repo: proj-a\n'
        ;;
      pa-02)
        printf 'task: pa-02\n  id: pa-02\n  title: Estimate missing\n  state: queued\n  repo: proj-a\n'
        ;;
      pa-08)
        printf 'task: pa-08\n  id: pa-08\n  title: Already underway\n  state: in_flight\n  repo: proj-a\n'
        ;;
      *) exit 2 ;;
    esac
    ;;
  *) exit 2 ;;
esac
EOF
  chmod +x "$1/bin/tasks-axi"
  cat > "$1/bin/pc" <<'EOF'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$PC_CALL_LOG"
case "${1-}" in
  check) printf 'ok: fixture plan\n' ;;
  compile) printf 'makespan 1.000 minutes\n' ;;
  frontier)
    [ "${3-}" = --json ] || exit 2
    cat "$PC_FRONTIER_JSON"
    ;;
  *) exit 2 ;;
esac
EOF
  chmod +x "$1/bin/pc"
  cat > "$1/frontier.json" <<'EOF'
{"items":[{"subject":"task:pa-01","kind":"open-task","score":0.75,"reason":"critical path"},{"subject":"method:proposal","kind":"proposal","score":1.0,"reason":"out of scope"}],"unestimated":[{"subject":"task:pa-02","kind":"open-task","reason":"no accepted method"}],"blocked":[{"subject":"method:alternative","kind":"blocked-alternative","reason":"out of scope"}]}
EOF
  cat > "$1/payload.json" <<'EOF'
{"schema":"fm-bearings-board.v1","home":"test-home","generated":"2026-10-12T12:00:00Z","prs_live":false,"captains_call":[],"underway":[],"landed":[],"charted":[{"id":"pa-01","repo":"proj-a","title":"Ready candidate","reason":"Waiting on its date gate","dispatchable":true},{"id":"pa-02","repo":"proj-a","title":"Estimate missing","reason":"Blocked by another task","dispatchable":false},{"id":"pa-08","repo":"proj-a","title":"Already underway","reason":"In flight","dispatchable":false}]}
EOF
}

test_missing_pc_bin_refuses_clearly_before_export() {
  local dir="$TMP_ROOT/no-pc" err="$TMP_ROOT/no-pc.err"
  seed_fixture "$dir"
  if PATH="$dir/bin:$PATH" TASKS_AXI_LOG="$dir/tasks.log" \
    bash "$SCRIPT" --payload "$dir/payload.json" --data-dir "$dir/data" --out "$dir/out" \
    >"$dir/output.json" 2>"$err"; then
    fail "the frontier helper accepted a missing PC_BIN"
  fi
  assert_contains "$(cat "$err")" "PC_BIN is not set" "missing compiler path was not explained"
  assert_absent "$dir/tasks.log" "export ran before the missing compiler path was refused"
  pass "fm-plan-frontier.sh clearly refuses when PC_BIN is absent"
}

test_frontier_annotations_preserve_dispatch_and_board_schema() {
  local dir="$TMP_ROOT/overlay" before after
  seed_fixture "$dir"
  before=$(shasum -a 256 "$dir/data/backlog.md" | awk '{print $1}')
  PATH="$dir/bin:$PATH" \
    TASKS_AXI_LOG="$dir/tasks.log" \
    PC_CALL_LOG="$dir/pc.log" \
    PC_FRONTIER_JSON="$dir/frontier.json" \
    PC_BIN="$dir/bin/pc" \
    FM_HOME="$dir" FM_DATA_OVERRIDE="$dir/data" \
    bash "$SCRIPT" --payload "$dir/payload.json" --data-dir "$dir/data" --out "$dir/out" \
    >"$dir/result.json" 2>"$dir/run.log" \
    || fail "frontier overlay failed: $(cat "$dir/run.log")"
  after=$(shasum -a 256 "$dir/data/backlog.md" | awk '{print $1}')
  assert_equals "$before" "$after" "frontier export modified backlog.md"
  assert_present "$dir/out/proj-a/plan.toml" "exported plan was not written"
  assert_present "$dir/out/proj-a/evidence.jsonl" "exported evidence file was not written"
  "$BOARD" validate "$dir/result.json" >/dev/null \
    || fail "frontier output did not validate under fm-bearings-board.sh"
  jq -e '
    .charted[0].dispatchable == true
    and (.charted[0].reason | contains("Plan frontier advisory: rank 1, score 0.750"))
    and (.charted[0].reason | contains("tasks-axi ready remains the dispatch source of truth"))
    and .charted[1].dispatchable == false
    and (.charted[1].reason | contains("Plan frontier advisory: unestimated"))
    and .charted[2].reason == "In flight"
    and ([keys[]] | sort) == ["captains_call","charted","generated","home","landed","prs_live","schema","underway"]
  ' "$dir/result.json" >/dev/null || fail "frontier overlay changed dispatch decisions or unexpected payload fields"
  assert_contains "$(cat "$dir/pc.log")" "check " "pc check was not run"
  assert_contains "$(cat "$dir/pc.log")" "compile " "pc compile was not run"
  assert_contains "$(cat "$dir/pc.log")" "frontier " "pc frontier was not run"
  assert_contains "$(cat "$dir/pc.log")" "--json" "pc frontier did not request JSON"
  assert_no_grep 'ready' "$dir/tasks.log" "the overlay consulted tasks-axi ready"
  pass "frontier annotations enrich only existing queued rows and preserve the board payload contract"
}

test_missing_pc_bin_refuses_clearly_before_export
test_frontier_annotations_preserve_dispatch_and_board_schema

pass "fm-plan-frontier behavior tests passed"
