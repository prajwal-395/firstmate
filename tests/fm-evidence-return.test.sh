#!/usr/bin/env bash
# Behavior tests for completed-task evidence return using a synthetic backlog,
# forge responses, and pc executable. No operator data or network is touched.
set -u

# shellcheck source=tests/lib.sh disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SCRIPT="$ROOT/bin/fm-evidence-return.sh"
TMP_ROOT=$(fm_test_tmproot fm-evidence-return)
FIXTURE="$TMP_ROOT/home"
mkdir -p "$FIXTURE/data/proj-a" "$TMP_ROOT/bin"

cat > "$FIXTURE/data/backlog.md" <<'EOF'
# Backlog

## Done
- [x] done-01 - First completed PR (repo: proj-a) (links: https://github.com/example/proj-a/pull/1)
- [x] done-02 - Second completed PR (repo: proj-a) (links: https://github.com/example/proj-a/pull/2)
- [x] done-03 - Third completed PR (repo: proj-a) (links: https://github.com/example/proj-a/pull/3)
EOF
cat > "$FIXTURE/data/proj-a/plan.toml" <<'EOF'
ir_version = 0
[plan]
id = "synthetic"
title = "Synthetic plan"
[objective]
root_task = "done-01"
weights = { duration_minutes = 1.0 }
[[objective.acceptance]]
kind = "manual"
description = "Synthetic fixture"
[[task]]
id = "done-01"
title = "First completed PR"
state = "done"
[[task]]
id = "done-02"
title = "Second completed PR"
state = "done"
[[task]]
id = "done-03"
title = "Third completed PR"
state = "done"
EOF
: > "$FIXTURE/data/proj-a/evidence.jsonl"

cat > "$TMP_ROOT/bin/gh-axi" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *pulls/1*) printf '2026-10-12T10:00:00Z\t2026-10-12T10:10:00Z\thttps://github.com/example/proj-a/pull/1\n' ;;
  *pulls/2*) printf '2026-10-12T10:00:00Z\t2026-10-12T10:20:00Z\thttps://github.com/example/proj-a/pull/2\n' ;;
  *pulls/3*) printf '2026-10-12T10:00:00Z\t2026-10-12T10:30:00Z\thttps://github.com/example/proj-a/pull/3\n' ;;
  *) echo "unexpected gh-axi request: $*" >&2; exit 1 ;;
esac
EOF
cat > "$TMP_ROOT/bin/pc" <<'EOF'
#!/usr/bin/env python3
import json, pathlib, sys
if len(sys.argv) != 4 or sys.argv[1] != "record":
    raise SystemExit("expected pc record <json> <plan-dir>")
row = json.loads(pathlib.Path(sys.argv[2]).read_text())
with (pathlib.Path(sys.argv[3]) / "evidence.jsonl").open("a") as output:
    output.write(json.dumps(row, separators=(",", ":")) + "\n")
print("recorded " + row["subject"] + " " + row["metric"])
EOF
chmod +x "$TMP_ROOT/bin/gh-axi" "$TMP_ROOT/bin/pc"
TEST_PC="${FM_TEST_PC:-$TMP_ROOT/bin/pc}"
[ -x "$TEST_PC" ] || fail "FM_TEST_PC is not executable: $TEST_PC"

test_records_completed_pr_timings_with_typed_bases() {
  local output
  output=$(PATH="$TMP_ROOT/bin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$FIXTURE" PC="$TEST_PC" bash "$SCRIPT") || fail "evidence recorder failed: $output"
  assert_contains "$output" "appended 3 record(s)" "three completed PRs were not recorded"
  assert_equals "3" "$(wc -l < "$FIXTURE/data/proj-a/evidence.jsonl" | tr -d ' ')" "expected three evidence lines"
  python3 - "$FIXTURE/data/proj-a/evidence.jsonl" <<'PY'
import json, sys
rows=[json.loads(line) for line in open(sys.argv[1])]
assert [row["subject"] for row in rows] == ["task:done-01", "task:done-02", "task:done-03"]
assert [row["value"] for row in rows] == [10.0, 20.0, 30.0]
assert all(row["basis"] == "absolute" and row["unit"] == "minutes" for row in rows)
assert all("open-to-merge" in row["source"] for row in rows)
PY
  pass "fm-evidence-return.sh appends forge PR duration records with typed bases"
}

test_refuses_when_pc_is_missing() {
  local output
  output=$(PATH="$TMP_ROOT/bin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$FIXTURE" env -u PC bash "$SCRIPT" 2>&1) && fail "missing PC unexpectedly succeeded"
  assert_contains "$output" "PC must name the planning-compiler executable" "missing-PC refusal was unclear"
  pass "fm-evidence-return.sh refuses clearly when PC is unset"
}

test_is_idempotent_for_already_recorded_prs() {
  local output
  output=$(PATH="$TMP_ROOT/bin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$FIXTURE" PC="$TEST_PC" bash "$SCRIPT") || fail "repeat run failed: $output"
  assert_contains "$output" "appended 0 record(s)" "repeat run appended duplicates"
  assert_equals "3" "$(wc -l < "$FIXTURE/data/proj-a/evidence.jsonl" | tr -d ' ')" "repeat run changed evidence count"
  pass "fm-evidence-return.sh skips PR observations already recorded"
}

if command -v tasks-axi >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  test_records_completed_pr_timings_with_typed_bases
  test_refuses_when_pc_is_missing
  test_is_idempotent_for_already_recorded_prs
else
  echo "skip: tasks-axi or python3 not found; evidence-return cases not run"
fi
