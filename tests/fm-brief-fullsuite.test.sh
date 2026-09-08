#!/usr/bin/env bash
# Behavior tests for the full-suite done-check guard.
#
# bin/fm-brief.sh scaffolds a three-tier test ladder into every brief, but a
# Done-check reading "run the full test suite" overrides it: the Done-check is
# the last thing a worker reads, so it wins. bin/fm-brief-lib.sh therefore owns
# a refusal gate - exercised here through bin/fm-brief.sh --check, the same
# public interface bin/fm-spawn.sh gates dispatch on - that refuses a Done-check
# demanding the whole suite unless the brief states a fan-out reason.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-brief-fullsuite)
HOME_DIR="$TMP_ROOT/home"
mkdir -p "$HOME_DIR/data"

# Scaffold a ship brief and fill every placeholder except the Done-check, which
# is filled with the given body. Prints the brief path.
make_brief_with_donecheck() {  # <id> <donecheck-body>
  local id=$1 body=$2 brief
  FM_HOME="$HOME_DIR" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode direct-PR >/dev/null 2>&1 \
    || fail "scaffold failed for $id"
  brief="$HOME_DIR/data/$id/brief.md"
  DONECHECK_BODY="$body" python3 - "$brief" <<'FILL'
import os, sys
path = sys.argv[1]
text = open(path).read()
text = text.replace("{TASK}", "Do the thing.")
text = text.replace("{DONE_CHECK}", os.environ["DONECHECK_BODY"])
text = text.replace("{SCOPE_DONE}", "The thing is done.")
text = text.replace("{SCOPE_OUT_OF_SCOPE}", "Anything else.")
text = text.replace("{SCOPE_KNOWN_UNKNOWNS}", "Nothing unknown.")
text = text.replace("{SCOPE_BLOCKED_ON}", "nothing")
open(path, "w").write(text)
FILL
  printf '%s\n' "$brief"
}

check_brief() {  # <id> -> prints output, returns check status
  FM_HOME="$HOME_DIR" "$ROOT/bin/fm-brief.sh" "$1" --check 2>&1
}

test_refuses_bare_full_suite_demand() {
  local brief out status
  brief=$(make_brief_with_donecheck fullsuite-bare-a1 "Run the full test suite.")
  assert_grep "Run the full test suite." "$brief" "the fixture lost its full-suite demand before the check ran"
  out=$(check_brief fullsuite-bare-a1); status=$?
  [ "$status" -ne 0 ] || fail "a Done-check demanding the full suite with no reason was reported as ready"
  assert_contains "$out" "full suite" "the refusal did not name the full-suite demand"
  assert_contains "$out" "fan-out" "the refusal did not say a stated fan-out reason is the way through"
  assert_contains "$out" "covering the changed files" "the refusal did not name the cheaper tier"
  pass "fullsuite guard: a bare full-suite demand is refused and taught the cheaper tier"
}

test_refuses_bare_pytest_and_pytest_tests_dir() {
  local out status
  make_brief_with_donecheck fullsuite-pytest-a2 "Run pytest to verify." >/dev/null
  out=$(check_brief fullsuite-pytest-a2); status=$?
  [ "$status" -ne 0 ] || fail "a bare pytest Done-check was reported as ready"
  assert_contains "$out" "fan-out" "the bare-pytest refusal did not say a stated reason is the way through"
  make_brief_with_donecheck fullsuite-pytest-a3 "Run pytest tests/ to verify." >/dev/null
  out=$(check_brief fullsuite-pytest-a3); status=$?
  [ "$status" -ne 0 ] || fail "a pytest tests/ Done-check was reported as ready"
  make_brief_with_donecheck fullsuite-make-a4 "Run make test to verify." >/dev/null
  out=$(check_brief fullsuite-make-a4); status=$?
  [ "$status" -ne 0 ] || fail "a make test Done-check was reported as ready"
  pass "fullsuite guard: bare pytest, pytest tests/, and make test are refused without a reason"
}

test_passes_with_stated_fanout_reason() {
  local out status
  make_brief_with_donecheck fullsuite-reason-a5 "Run the full test suite because compile_manifest fans out across every lane." >/dev/null
  out=$(check_brief fullsuite-reason-a5); status=$?
  expect_code_out 0 "$status" "$out" "a full-suite demand with a stated fan-out reason must report ready"
  assert_contains "$out" "ready:" "the ready report did not say the brief is ready"
  make_brief_with_donecheck fullsuite-reason-a6 "Run pytest tests/ - compile_manifest touches every lane, so the narrow selection is wider than the suite." >/dev/null
  out=$(check_brief fullsuite-reason-a6); status=$?
  expect_code_out 0 "$status" "$out" "a command-form demand with a stated reason must report ready"
  pass "fullsuite guard: a stated fan-out reason lets a full-suite Done-check through"
}

test_passes_narrow_selection() {
  local out status
  make_brief_with_donecheck fullsuite-narrow-a7 "Run bin/fm-test-run.sh tests/fm-brief-fullsuite.test.sh and paste the output." >/dev/null
  out=$(check_brief fullsuite-narrow-a7); status=$?
  expect_code_out 0 "$status" "$out" "a narrow Done-check must report ready"
  make_brief_with_donecheck fullsuite-narrow-a8 "Run pytest tests/fm-brief-fullsuite.test.sh covering the changed gate." >/dev/null
  out=$(check_brief fullsuite-narrow-a8); status=$?
  expect_code_out 0 "$status" "$out" "a pytest-with-path Done-check must report ready"
  pass "fullsuite guard: a narrow Done-check still passes"
}

test_prohibition_is_not_a_demand() {
  local out status
  make_brief_with_donecheck fullsuite-negation-a9 "Do NOT run the full suite - run only tests/fm-brief-fullsuite.test.sh covering the changed gate." >/dev/null
  out=$(check_brief fullsuite-negation-a9); status=$?
  expect_code_out 0 "$status" "$out" "a Done-check prohibiting the full suite must not be refused as demanding it"
  pass "fullsuite guard: prohibiting the full suite is not demanding it"
}

test_scaffold_ladder_prose_is_not_a_demand() {
  local out status brief
  brief=$(make_brief_with_donecheck fullsuite-scope-a10 "Run tests/fm-brief-fullsuite.test.sh covering the changed gate.")
  assert_grep "Reaching for the whole suite because you have not looked for the narrower path" "$brief" \
    "the fixture lost the scaffold ladder prose the scoping test needs"
  out=$(check_brief fullsuite-scope-a10); status=$?
  expect_code_out 0 "$status" "$out" "the scaffold's own ladder prose must not read as a Done-check demand"
  pass "fullsuite guard: detection is scoped to the Done-check section, not the scaffold ladder"
}

test_refuses_bare_full_suite_demand
test_refuses_bare_pytest_and_pytest_tests_dir
test_passes_with_stated_fanout_reason
test_passes_narrow_selection
test_prohibition_is_not_a_demand
test_scaffold_ladder_prose_is_not_a_demand
