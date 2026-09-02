#!/usr/bin/env bash
# Behavior tests for the PR build-verdict watch.
#
# The defect this layer exists to prevent is a crewmate spending a model turn
# per CI check, so the cases that carry the weight are the SILENT ones: the
# poll must produce nothing while a build is still running, nothing when the
# lookup fails, and nothing a second time for a verdict already reported. A
# suite that only proved "it prints passed when green" would have passed while
# every one of those regressed into a wake storm - or, worse, while a failed
# lookup read as green.
#
# Every case drives bin/fm-ci-check.sh and bin/fm-ci-poll.sh through their real
# command lines against a fake gh, so no case touches the network.
set -u

# shellcheck source=tests/lib.sh
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# fm_pr_file_mode is the repo's single owner of reading a file's mode; stat's
# own flags differ between BSD and GNU, and a hand-rolled read passed on macOS
# while silently comparing the wrong string on Linux.
# shellcheck source=bin/fm-pr-lib.sh
# shellcheck disable=SC1091
. "$ROOT/bin/fm-pr-lib.sh"

CI="$ROOT/bin/fm-ci-check.sh"
TMP_ROOT=$(fm_test_tmproot fm-ci-check)
PR=https://github.com/acme/widget/pull/7

FAKEBIN=$(fm_fakebin "$TMP_ROOT")
PATH="$FAKEBIN:$PATH"
export PATH

# --- fake gh ----------------------------------------------------------------
#
# Answers `gh pr view ... -q <query>` by running the real query against a
# fixture body, so the tests exercise the poll's own jq rather than a
# hand-written stand-in for it. GH_FIXTURE names the body; GH_FAIL makes the
# call fail the way a network or auth error does.
cat > "$FAKEBIN/gh" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$GH_LOG"
[ -z "${GH_FAIL:-}" ] || exit 1
query=
prev=
for arg in "$@"; do
  [ "$prev" = -q ] && query=$arg
  prev=$arg
done
[ -n "$query" ] || exit 1
jq -r "$query" < "$GH_FIXTURE"
SH
chmod +x "$FAKEBIN/gh"
command -v jq >/dev/null 2>&1 || { echo "skip: jq not found"; exit 0; }

GH_LOG="$TMP_ROOT/gh.log"
export GH_LOG
: > "$GH_LOG"

# rollup <entry-json>...: a gh pr view body with an OPEN, MERGEABLE PR.
rollup() {
  local body='{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":['
  local first=1 e
  for e in "$@"; do
    [ "$first" = 1 ] || body="$body,"
    first=0
    body="$body$e"
  done
  printf '%s]}\n' "$body"
}

run_check() {  # <home> <task>
  GH_FIXTURE="$GH_FIXTURE" "$1/state/$2.check.sh"
}

new_home() {  # <name> -> echoes home path
  local home="$TMP_ROOT/$1"
  mkdir -p "$home/state"
  printf '%s\n' "$home"
}

arm() {  # <home> <task> [pr]
  FM_HOME="$1" "$CI" arm --task "$2" --pr "${3:-$PR}"
}

PASSING='{"__typename":"CheckRun","status":"COMPLETED","conclusion":"SUCCESS"}'
SKIPPED='{"__typename":"CheckRun","status":"COMPLETED","conclusion":"SKIPPED"}'
RUNNING='{"__typename":"CheckRun","status":"IN_PROGRESS","conclusion":null}'
QUEUED='{"__typename":"CheckRun","status":"QUEUED","conclusion":null}'
FAILING='{"__typename":"CheckRun","status":"COMPLETED","conclusion":"FAILURE"}'
CANCELLED='{"__typename":"CheckRun","status":"COMPLETED","conclusion":"CANCELLED"}'
NOVEL='{"__typename":"CheckRun","status":"COMPLETED","conclusion":"SOME_NEW_THING"}'
CTX_PENDING='{"__typename":"StatusContext","state":"PENDING"}'
CTX_OK='{"__typename":"StatusContext","state":"SUCCESS"}'
CTX_ERR='{"__typename":"StatusContext","state":"ERROR"}'

# --- a green check set wakes exactly once -----------------------------------

home=$(new_home green)
out=$(arm "$home" build-green) || fail "arming a GitHub PR must succeed: $out"
assert_contains "$out" "armed: state/build-green.check.sh (registered)" \
  "arm must report the registered check"
[ "$(fm_pr_file_mode "$home/state/build-green.check.sh")" = 700 ] \
  || fail "the published check must be mode 0700"

GH_FIXTURE="$TMP_ROOT/green.json"
rollup "$PASSING" "$SKIPPED" > "$GH_FIXTURE"
out=$(run_check "$home" build-green)
[ "$out" = "ci passed $PR" ] || fail "a fully green check set must wake as passed; got: '$out'"

out=$(run_check "$home" build-green)
[ -z "$out" ] || fail "a verdict already reported must not wake again; got: '$out'"
pass "fm-ci-check: a green check set wakes once and then stays silent"

# --- a re-arm after a fix push re-enables the wake ---------------------------

out=$(arm "$home" build-green) || fail "re-arming must succeed: $out"
out=$(run_check "$home" build-green)
[ "$out" = "ci passed $PR" ] \
  || fail "re-arming after a fix push must let the next verdict wake again; got: '$out'"
pass "fm-ci-check: re-arming clears the reported marker so the next verdict wakes"

# --- anything still running is not a verdict ---------------------------------

home=$(new_home pending)
arm "$home" build-pending >/dev/null || fail "arm failed"
for entry in "$RUNNING" "$QUEUED" "$CTX_PENDING"; do
  GH_FIXTURE="$TMP_ROOT/pending.json"
  rollup "$PASSING" "$entry" > "$GH_FIXTURE"
  out=$(run_check "$home" build-pending)
  [ -z "$out" ] || fail "an unfinished check must stay silent (entry $entry); got: '$out'"
done
assert_absent "$home/state/build-pending.ci-watch-fired" \
  "a silent poll must not claim it reported anything"
pass "fm-ci-check: a check set with work still running stays silent"

# --- a failing conclusion wakes as failed, and unknown ones do not read green -

for entry in "$FAILING" "$CANCELLED" "$NOVEL" "$CTX_ERR"; do
  home=$(new_home "fail-$(printf '%s' "$entry" | cksum | cut -d' ' -f1)")
  arm "$home" build-red >/dev/null || fail "arm failed"
  GH_FIXTURE="$TMP_ROOT/red.json"
  rollup "$PASSING" "$entry" > "$GH_FIXTURE"
  out=$(run_check "$home" build-red)
  [ "$out" = "ci failed $PR" ] \
    || fail "a non-passing conclusion must wake as failed (entry $entry); got: '$out'"
done
pass "fm-ci-check: every non-passing conclusion, including an unrecognized one, wakes as failed"

# --- a passing StatusContext still counts as a pass --------------------------

home=$(new_home ctx)
arm "$home" build-ctx >/dev/null || fail "arm failed"
GH_FIXTURE="$TMP_ROOT/ctx.json"
rollup "$CTX_OK" "$PASSING" > "$GH_FIXTURE"
out=$(run_check "$home" build-ctx)
[ "$out" = "ci passed $PR" ] || fail "a legacy status context must be readable as a pass; got: '$out'"
pass "fm-ci-check: a legacy status context is normalized alongside a check run"

# --- an empty check set is never a pass --------------------------------------

home=$(new_home empty)
arm "$home" build-empty >/dev/null || fail "arm failed"
GH_FIXTURE="$TMP_ROOT/empty.json"
printf '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[]}\n' > "$GH_FIXTURE"
out=$(run_check "$home" build-empty)
[ -z "$out" ] || fail "an empty check set inside the grace window must stay silent; got: '$out'"
out=$(FM_CI_NO_CHECKS_GRACE=0 run_check "$home" build-empty)
[ "$out" = "ci no-checks $PR" ] \
  || fail "an empty check set past the grace window must wake as no-checks, never as a pass; got: '$out'"
pass "fm-ci-check: an empty check set stays silent, then surfaces as no-checks rather than a pass"

# --- a conflicting PR is not a pass, even with a green rollup ----------------

home=$(new_home conflict)
arm "$home" build-conflict >/dev/null || fail "arm failed"
GH_FIXTURE="$TMP_ROOT/conflict.json"
printf '{"state":"OPEN","mergeable":"CONFLICTING","statusCheckRollup":[%s]}\n' "$PASSING" > "$GH_FIXTURE"
out=$(run_check "$home" build-conflict)
[ "$out" = "ci conflicting $PR" ] \
  || fail "a CONFLICTING PR must wake as conflicting even when its rollup is green; got: '$out'"
pass "fm-ci-check: a conflicting PR never reports a pass"

# --- a closed or merged PR stops the wait ------------------------------------

home=$(new_home closed)
arm "$home" build-closed >/dev/null || fail "arm failed"
GH_FIXTURE="$TMP_ROOT/closed.json"
printf '{"state":"CLOSED","mergeable":"UNKNOWN","statusCheckRollup":[]}\n' > "$GH_FIXTURE"
out=$(run_check "$home" build-closed)
[ "$out" = "ci closed $PR" ] \
  || fail "a closed PR must wake rather than leave the worker waiting forever; got: '$out'"
pass "fm-ci-check: a merged or closed PR wakes instead of stranding the wait"

# --- a failed lookup is never a verdict --------------------------------------

home=$(new_home ghfail)
arm "$home" build-ghfail >/dev/null || fail "arm failed"
GH_FIXTURE="$TMP_ROOT/green.json"
out=$(GH_FAIL=1 run_check "$home" build-ghfail)
[ -z "$out" ] || fail "a failed forge lookup must stay silent, not report a verdict; got: '$out'"
assert_absent "$home/state/build-ghfail.ci-watch-fired" \
  "a failed lookup must not mark a verdict as reported"
out=$(run_check "$home" build-ghfail)
[ "$out" = "ci passed $PR" ] \
  || fail "the poll must recover once the forge answers again; got: '$out'"
pass "fm-ci-check: a failed forge lookup stays silent and does not consume the wake"

# --- a doctored sidecar cannot redirect the poll -----------------------------

home=$(new_home tamper)
arm "$home" build-tamper >/dev/null || fail "arm failed"
printf 'fm-ci-watch-v1\nhttps://evil.example/acme/widget/pull/7\n1\n' \
  > "$home/state/build-tamper.ci-watch"
GH_FIXTURE="$TMP_ROOT/green.json"
out=$(run_check "$home" build-tamper)
[ -z "$out" ] || fail "an unparseable watch URL must stay silent; got: '$out'"
assert_no_grep "evil.example" "$GH_LOG" "the poll must never call the forge for a rejected URL"

printf 'not-our-magic\n%s\n1\n' "$PR" > "$home/state/build-tamper.ci-watch"
out=$(run_check "$home" build-tamper)
[ -z "$out" ] || fail "a sidecar without our magic line must stay silent; got: '$out'"
pass "fm-ci-check: a doctored watch sidecar is refused instead of followed"

# --- arming refuses rather than watching nothing -----------------------------

home=$(new_home refusals)
out=$(FM_HOME="$home" "$CI" arm --task build-gl \
  --pr https://gitlab.example.com/acme/widget/-/merge_requests/3 2>&1) && rc=0 || rc=$?
expect_code_out 1 "$rc" "$out" "a GitLab merge request must be refused, not watched incorrectly"
assert_contains "$out" "GitHub pull requests only" "the refusal must name what is unsupported"
assert_absent "$home/state/build-gl.check.sh" "a refused arm must publish nothing"

out=$(FM_HOME="$home" "$CI" arm --task build-bad --pr "not a url" 2>&1) && rc=0 || rc=$?
expect_code_out 1 "$rc" "$out" "a non-URL must be refused"
assert_absent "$home/state/build-bad.check.sh" "a refused arm must publish nothing"

out=$(FM_HOME="$home" "$CI" arm --task "../escape" --pr "$PR" 2>&1) && rc=0 || rc=$?
expect_code_out 2 "$rc" "$out" "a path-unsafe task id must be refused"
pass "fm-ci-check: arming refuses an unsupported forge, a bad URL, and an unsafe task id"

# --- arming never silently disarms someone else's check ----------------------

home=$(new_home foreign)
printf '#!/usr/bin/env bash\nexit 0\n' > "$home/state/build-foreign.check.sh"
chmod 0700 "$home/state/build-foreign.check.sh"
out=$(arm "$home" build-foreign 2>&1) && rc=0 || rc=$?
expect_code_out 1 "$rc" "$out" "arming over a foreign check must be refused"
assert_contains "$out" "a different check is already armed" "the refusal must say why"
assert_grep "exit 0" "$home/state/build-foreign.check.sh" "the foreign check must be left intact"
pass "fm-ci-check: arming refuses to overwrite a check this watch did not publish"

# --- disarm removes only its own artifacts -----------------------------------

home=$(new_home disarm)
arm "$home" build-disarm >/dev/null || fail "arm failed"
GH_FIXTURE="$TMP_ROOT/green.json"
run_check "$home" build-disarm >/dev/null
out=$(FM_HOME="$home" "$CI" disarm --task build-disarm) || fail "disarm failed: $out"
for suffix in .check.sh .check-trust .ci-watch .ci-watch-fired; do
  assert_absent "$home/state/build-disarm$suffix" "disarm must remove state/build-disarm$suffix"
done
out=$(FM_HOME="$home" "$CI" disarm --task build-disarm 2>&1) && rc=0 || rc=$?
expect_code_out 1 "$rc" "$out" "disarming an unarmed task must refuse, not remove blindly"
pass "fm-ci-check: disarm removes exactly its own artifacts and refuses when unarmed"

# --- status reports what the watch has done ----------------------------------

home=$(new_home status)
out=$(FM_HOME="$home" "$CI" status --task build-status 2>&1) && rc=0 || rc=$?
expect_code_out 1 "$rc" "$out" "status on an unarmed task must report not armed"
arm "$home" build-status >/dev/null || fail "arm failed"
out=$(FM_HOME="$home" "$CI" status --task build-status) || fail "status failed: $out"
assert_contains "$out" "reported: (nothing yet)" "a fresh watch must report nothing yet"
GH_FIXTURE="$TMP_ROOT/green.json"
run_check "$home" build-status >/dev/null
out=$(FM_HOME="$home" "$CI" status --task build-status) || fail "status failed: $out"
assert_contains "$out" "reported: ci passed $PR" "status must report the verdict already sent"
pass "fm-ci-check: status distinguishes unarmed, armed-and-silent, and already-reported"
