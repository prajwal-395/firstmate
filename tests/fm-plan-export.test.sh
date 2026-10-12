#!/usr/bin/env bash
# Behavior tests for bin/fm-plan-export.sh, the read-only backlog-to-IR exporter.
#
# Every case runs the exporter against a synthetic fixture home (never the
# operator's live backlog) and exercises the shipped executable: per-project
# plan layout, blocked-by order edges, loud drops for edges the IR cannot
# carry, byte-exact carry-through of fields with no IR counterpart, and proof
# the fixture backlog is untouched. Structural validity (unique ids, resolved
# edge endpoints, acyclic orders, parseable TOML) is asserted through the
# emitted plan.toml; a `pc` binary on PATH additionally proves `pc check` is
# clean, and is skipped otherwise.
set -u

# shellcheck source=tests/lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

EXPORTER="$ROOT/bin/fm-plan-export.sh"
TMP_ROOT=$(fm_test_tmproot fm-plan-export)

unset TASKS_AXI_FILE TASKS_AXI_BACKEND FM_HOME FM_ROOT_OVERRIDE \
  FM_DATA_OVERRIDE FM_STATE_OVERRIDE FM_CONFIG_OVERRIDE FM_PROJECTS_OVERRIDE

HAVE_TASKS_AXI=0
command -v tasks-axi >/dev/null 2>&1 && HAVE_TASKS_AXI=1
HAVE_PYTHON=0
command -v python3 >/dev/null 2>&1 && HAVE_PYTHON=1

# Twelve live rows across two projects: chains, a self-edge, a two-cycle, a
# cross-project blocker, a hold, an in-flight row, and bodies carrying commas,
# quotes, newlines, and a backslash. Done rows must stay out of every plan.
seed_fixture() {  # <dir>
  mkdir -p "$1/data" "$1/out"
  cat > "$1/data/backlog.md" <<'EOF'
# Backlog

## In flight
- [ ] pa-08 - Shipping the widget (repo: proj-a) (kind: ship) (since 2026-10-01)
  Already started work.

## Queued
- [ ] pa-01 - Base task, with comma and "quotes" (repo: proj-a) (kind: ship) (since 2026-10-01)
  First line.
  Second line with "quotes", comma, and backslash \ here.
- [ ] pa-02 - Second task blocked-by: pa-01 (repo: proj-a) (kind: ship) (since 2026-10-02)
- [ ] pa-03 - Third task blocked-by: pa-02 (repo: proj-a) (kind: captain) (since 2026-10-03)
- [ ] pa-04 - Self-blocked task blocked-by: pa-04 (repo: proj-a) (kind: ship)
- [ ] pa-05 - Cycle left blocked-by: pa-06 (repo: proj-a) (kind: ship)
- [ ] pa-06 - Cycle right blocked-by: pa-05 (repo: proj-a) (kind: ship)
- [ ] pa-07 - Held task (repo: proj-a) (kind: captain) (hold: waiting on the captain) (hold-kind: future)
- [ ] pb-01 - Bee base task (repo: proj-b) (kind: ship)
- [ ] pb-02 - Bee second blocked-by: pb-01 (repo: proj-b) (kind: ship)
- [ ] pb-03 - Bee cross-repo blocked-by: pa-01 (repo: proj-b) (kind: ship)
- [ ] pb-04 - Bee plain (repo: proj-b) (kind: task)

## Done
- [x] old-01 - Finished long ago (repo: proj-a) (kind: ship)
EOF
}

export_all() {  # <dir>; prints exporter stdout
  (cd "$ROOT" && FM_DATA_OVERRIDE="$1/data" bash "$EXPORTER" --out "$1/out")
}

test_exports_one_plan_per_project_and_leaves_the_backlog_alone() {
  local dir="$TMP_ROOT/all"
  seed_fixture "$dir"
  local before
  before=$(shasum -a 256 "$dir/data/backlog.md" | awk '{print $1}')
  local output
  output=$(export_all "$dir") || fail "exporter exited nonzero: $output"
  local after
  after=$(shasum -a 256 "$dir/data/backlog.md" | awk '{print $1}')
  assert_equals "$before" "$after" "the exporter modified the fixture backlog.md"
  assert_present "$dir/out/proj-a/plan.toml" "proj-a plan.toml was not written"
  assert_present "$dir/out/proj-a/evidence.jsonl" "proj-a evidence.jsonl was not written"
  assert_present "$dir/out/proj-b/plan.toml" "proj-b plan.toml was not written"
  assert_present "$dir/out/proj-b/evidence.jsonl" "proj-b evidence.jsonl was not written"
  assert_contains "$output" "exported: 12 live rows into 2 plan(s)" "the summary count is wrong"
  assert_equals "9" "$(grep -c '^\[\[task\]\]' "$dir/out/proj-a/plan.toml")" "proj-a should hold 8 rows plus its root task"
  assert_equals "5" "$(grep -c '^\[\[task\]\]' "$dir/out/proj-b/plan.toml")" "proj-b should hold 4 rows plus its root task"
  assert_no_grep "old-01" "$dir/out/proj-a/plan.toml" "a done row leaked into the plan"
  pass "fm-plan-export.sh exports one plan per project and leaves backlog.md untouched"
}

test_blocked_by_becomes_order_edges_and_impossible_edges_drop_loudly() {
  local dir="$TMP_ROOT/edges"
  seed_fixture "$dir"
  local output
  output=$(export_all "$dir") || fail "exporter exited nonzero: $output"
  assert_grep '{ from = "pa-01", to = "pa-02" }' "$dir/out/proj-a/plan.toml" "the pa-01 -> pa-02 chain edge is missing"
  assert_contains "$output" "dropped-edge: pa-04 -> pa-04 (self-edge)" "the self-edge drop was not reported"
  assert_contains "$output" "dropped-edge: pa-06 -> pa-05 (would close an order cycle)" "the cycle-breaking drop was not reported"
  assert_contains "$output" "dropped-edge: pb-03 -> pa-01 (target is outside this plan)" "the cross-project drop was not reported"
  pass "fm-plan-export.sh maps blocked-by to order edges and drops impossible edges loudly"
}

test_unmappable_fields_ride_through_byte_exact() {
  local dir="$TMP_ROOT/carry"
  seed_fixture "$dir"
  export_all "$dir" >/dev/null || fail "exporter exited nonzero"
  if FM_FIXTURE_DIR="$dir" python3 - <<'EOF'
import json
import os

plan = os.path.join(os.environ["FM_FIXTURE_DIR"], "out", "proj-a", "plan.toml")
carried = {}
for line in open(plan, encoding="utf-8"):
    if line.startswith("# tasks-axi-json: "):
        record = json.loads(line[len("# tasks-axi-json: "):])
        carried[record["id"]] = record
assert len(carried) == 8, f"expected 8 carried rows, saw {len(carried)}"
body = carried["pa-01"]["body"]
assert body == 'First line.\nSecond line with "quotes", comma, and backslash \\ here.', f"body mangled: {body!r}"
assert carried["pa-07"]["hold_reason"] == "waiting on the captain", "hold reason mangled"
assert carried["pa-07"]["hold_kind"] == "future", "hold kind mangled"
assert carried["pa-08"]["state"] == "in_flight", "in-flight state mangled"
assert carried["pa-03"]["kind"] == "captain", "kind mangled"
EOF
  then
    pass "fm-plan-export.sh carries fields with no IR counterpart through untouched"
  else
    fail "tasks-axi-json carry-through is not byte-exact"
  fi
}

test_emitted_plans_are_structurally_valid() {
  local dir="$TMP_ROOT/valid"
  seed_fixture "$dir"
  export_all "$dir" >/dev/null || fail "exporter exited nonzero"
  if FM_FIXTURE_DIR="$dir" python3 - <<'EOF'
import os
import tomllib

for project in ("proj-a", "proj-b"):
    path = os.path.join(os.environ["FM_FIXTURE_DIR"], "out", project, "plan.toml")
    with open(path, "rb") as handle:
        plan = tomllib.load(handle)
    assert plan["ir_version"] == 0, f"{project}: wrong ir_version"
    root = plan["objective"]["root_task"]
    ids = [task["id"] for task in plan["task"]]
    assert len(ids) == len(set(ids)), f"{project}: duplicate task ids"
    assert root in ids, f"{project}: root task missing"
    assert len(plan["method"]) == 1, f"{project}: expected exactly the root decompose method"
    method = plan["method"][0]
    assert method["task"] == root and method["kind"] == "decompose", f"{project}: root method shape wrong"
    assert method["status"] == "accepted", f"{project}: root method must be accepted"
    assert set(method["subtasks"]) == set(ids) - {root}, f"{project}: subtasks must be exactly the rows"
    for edge in method["order_edges"]:
        assert edge["from"] in method["subtasks"] and edge["to"] in method["subtasks"], f"{project}: dangling edge {edge}"
    # Acyclicity over the emitted edges: repeatedly strip sources; any
    # leftover edge means a cycle.
    remaining = [(edge["from"], edge["to"]) for edge in method["order_edges"]]
    stripped = True
    while stripped and remaining:
        stripped = False
        targets = {dest for _, dest in remaining}
        for edge in list(remaining):
            if edge[0] not in targets:
                remaining.remove(edge)
                stripped = True
    assert not remaining, f"{project}: order cycle in emitted edges"
EOF
  then
    structural_ok=1
  else
    structural_ok=0
  fi
  [ "$structural_ok" = 1 ] || fail "an emitted plan.toml is structurally invalid"
  if command -v pc >/dev/null 2>&1; then
    pc check "$dir/out/proj-a" >/dev/null || fail "pc check rejected the proj-a export"
    pc check "$dir/out/proj-b" >/dev/null || fail "pc check rejected the proj-b export"
  else
    echo "skip: pc not on PATH; structural assertions stand in for pc check"
  fi
  pass "fm-plan-export.sh emits structurally valid plans"
}

test_project_filter_and_empty_refusal() {
  local dir="$TMP_ROOT/filter"
  seed_fixture "$dir"
  (cd "$ROOT" && FM_DATA_OVERRIDE="$dir/data" bash "$EXPORTER" --out "$dir/one" --project proj-b) >/dev/null \
    || fail "--project proj-b exited nonzero"
  assert_present "$dir/one/proj-b/plan.toml" "the filtered plan was not written"
  assert_absent "$dir/one/proj-a" "the filter exported an unasked project"
  if (cd "$ROOT" && FM_DATA_OVERRIDE="$dir/data" bash "$EXPORTER" --out "$dir/none" --project nope 2>/dev/null); then
    fail "an empty project selection should refuse instead of writing nothing"
  fi
  pass "fm-plan-export.sh filters by project and refuses an empty selection"
}

if [ "$HAVE_TASKS_AXI" = 1 ] && [ "$HAVE_PYTHON" = 1 ]; then
  test_exports_one_plan_per_project_and_leaves_the_backlog_alone
  test_blocked_by_becomes_order_edges_and_impossible_edges_drop_loudly
  test_unmappable_fields_ride_through_byte_exact
  test_emitted_plans_are_structurally_valid
  test_project_filter_and_empty_refusal
else
  echo "skip: tasks-axi or python3 not found; exporter cases not run"
fi
