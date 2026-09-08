#!/usr/bin/env bash
# Behavior tests for the merge-before-PR dispatch gate.
#
# A squash merge of a stale branch reverts cleanly with no conflict, so the
# worker must merge the tracked upstream and re-run the chosen selection ON
# the merged tree before the PR is opened. bin/fm-brief-lib.sh therefore owns
# a refusal gate - exercised here through bin/fm-brief.sh --check, the same
# public interface bin/fm-spawn.sh gates dispatch on - that refuses a ship
# brief lacking either half of that step.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-brief-merge)
HOME_DIR="$TMP_ROOT/home"
mkdir -p "$HOME_DIR/data"

# Scaffold a ship brief in the given mode and fill every placeholder with a
# neutral body. Prints the brief path.
make_filled_ship_brief() {  # <id> <mode>
  local id=$1 mode=$2 brief
  FM_HOME="$HOME_DIR" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode "$mode" >/dev/null 2>&1 \
    || fail "scaffold failed for $id ($mode)"
  brief="$HOME_DIR/data/$id/brief.md"
  python3 - "$brief" <<'FILL'
import sys
path = sys.argv[1]
text = open(path).read()
text = text.replace("{TASK}", "Do the thing.")
text = text.replace("{DONE_CHECK}", "Run tests/merge-check.sh covering the change.")
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

test_fresh_ship_briefs_carry_the_step() {
  local mode id brief out status
  for mode in no-mistakes direct-PR local-only; do
    id="merge-fresh-$mode"
    brief=$(make_filled_ship_brief "$id" "$mode")
    assert_grep "merge the tracked upstream" "$brief" \
      "$mode: scaffold lost the merge half of the required step"
    assert_grep "ON the merged tree" "$brief" \
      "$mode: scaffold lost the verify half of the required step"
    out=$(check_brief "$id"); status=$?
    expect_code_out 0 "$status" "$out" "$mode: a freshly scaffolded ship brief must report ready"
    assert_contains "$out" "ready:" "$mode: the ready report did not say the brief is ready"
  done
  pass "merge gate: freshly scaffolded ship briefs in every mode carry the step and report ready"
}

test_refuses_ship_brief_without_the_step() {
  local brief out status
  brief=$(make_filled_ship_brief merge-stripped-a1 direct-PR)
  python3 - "$brief" <<'STRIP'
import sys, re
path = sys.argv[1]
lines = open(path).read().splitlines(keepends=True)
lines = [l for l in lines if "merge the tracked upstream" not in l.lower() and "on the merged tree" not in l.lower()]
open(path, "w").write("".join(lines))
STRIP
  assert_no_grep "merge the tracked upstream" "$brief" \
    "the fixture kept the merge half the refusal needs gone"
  out=$(check_brief merge-stripped-a1); status=$?
  [ "$status" -ne 0 ] || fail "a ship brief without the merge-before-PR step was reported as ready"
  assert_contains "$out" "merge-before-PR" "the refusal did not name the merge-before-PR step"
  pass "merge gate: a ship brief without the step is refused"
}

test_refuses_ship_brief_missing_only_the_verify_half() {
  local brief out status
  brief=$(make_filled_ship_brief merge-half-a2 direct-PR)
  python3 - "$brief" <<'STRIP'
import sys
path = sys.argv[1]
lines = open(path).read().splitlines(keepends=True)
lines = [l for l in lines if "on the merged tree" not in l.lower()]
open(path, "w").write("".join(lines))
STRIP
  assert_grep "merge the tracked upstream" "$brief" \
    "the fixture lost the merge half the half-missing case needs kept"
  out=$(check_brief merge-half-a2); status=$?
  [ "$status" -ne 0 ] || fail "a ship brief with the merge but no verify-on-merged-tree was reported as ready"
  assert_contains "$out" "ON the merged tree" "the refusal did not name the missing verify half"
  pass "merge gate: the merge half alone is not enough without the verify half"
}

test_scout_brief_passes_vacuously() {
  local brief out status
  FM_HOME="$HOME_DIR" "$ROOT/bin/fm-brief.sh" merge-scout-a3 some-proj --scout >/dev/null 2>&1 \
    || fail "scout scaffold failed"
  brief="$HOME_DIR/data/merge-scout-a3/brief.md"
  python3 - "$brief" <<'FILL'
import sys
path = sys.argv[1]
text = open(path).read()
text = text.replace("{TASK}", "Investigate the thing.")
text = text.replace("{SCOPE_DONE}", "The report answers the question.")
text = text.replace("{SCOPE_OUT_OF_SCOPE}", "Anything else.")
text = text.replace("{SCOPE_KNOWN_UNKNOWNS}", "Nothing unknown.")
text = text.replace("{SCOPE_BLOCKED_ON}", "nothing")
open(path, "w").write(text)
FILL
  out=$(check_brief merge-scout-a3); status=$?
  expect_code_out 0 "$status" "$out" "a scout brief carries no PR and must not be refused for the merge step"
  pass "merge gate: a scout brief with no PR passes vacuously"
}

test_fresh_ship_briefs_carry_the_step
test_refuses_ship_brief_without_the_step
test_refuses_ship_brief_missing_only_the_verify_half
test_scout_brief_passes_vacuously
