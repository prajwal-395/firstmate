#!/usr/bin/env bash
# Behavior tests for firstmate's GitHub Issues project layer.
#
# The two defects this layer exists to prevent are both "the tool answered
# confidently and the answer was wrong", so the refusals carry most of the
# weight here: a suite that only walked the happy path would have passed while
# both defects were live.
#
# Every case drives bin/fm-tracker.sh and bin/fm-tracker-notify.sh through their
# real command lines against a fake gh, so no case touches the network.
set -u

# shellcheck source=tests/lib.sh
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TRACKER="$ROOT/bin/fm-tracker.sh"
NOTIFY="$ROOT/bin/fm-tracker-notify.sh"
TMP_ROOT=$(fm_test_tmproot fm-tracker)

command -v python3 >/dev/null 2>&1 || { echo "skip: python3 not found"; exit 0; }

# --- fake gh ----------------------------------------------------------------
#
# Records every invocation to $FAKE_GH_LOG and answers from $FAKE_GH_DIR: a
# response file named for the request, or a canned default. Recording the calls
# is what lets a refusal case assert that NOTHING was created, which is the
# claim that matters - "it printed an error" is not the same as "it did not
# write".

FAKE_GH_DIR="$TMP_ROOT/gh"
FAKE_GH_LOG="$TMP_ROOT/gh/calls.log"
mkdir -p "$FAKE_GH_DIR"

setup_fake_gh() {
  local fakebin=$1
  cat > "$fakebin/gh" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$FAKE_GH_LOG"
mode=$1
case $mode in
  api) shift ;;
  *) exit 0 ;;
esac

want_graphql=0
path=
for arg in "$@"; do
  case $arg in
    graphql) want_graphql=1 ;;
    /*) [ -n "$path" ] || path=$arg ;;
  esac
done

if [ "$want_graphql" -eq 1 ]; then
  [ -f "$FAKE_GH_DIR/graphql.out" ] || exit 0
  cat "$FAKE_GH_DIR/graphql.out"
  exit 0
fi

# An -i request is served verbatim from a canned HTTP response so the poll sees
# real status lines, ETag and X-Poll-Interval headers.
case " $* " in
  *" -i "*)
    n=$(cat "$FAKE_GH_DIR/http.seq" 2>/dev/null || echo 1)
    file="$FAKE_GH_DIR/http.$n"
    if [ ! -f "$file" ]; then
      file="$FAKE_GH_DIR/http.default"
    fi
    printf '%s\n' "$((n + 1))" > "$FAKE_GH_DIR/http.seq"
    [ -f "$file" ] || exit 1
    cat "$file"
    grep -q '^HTTP/[^ ]* 304' "$file" && exit 1
    exit 0
    ;;
esac

case $path in
  /user) printf '%s\n' "${FAKE_GH_LOGIN:-tester}"; exit 0 ;;
esac

name=$(printf '%s' "$path" | tr '/?&=' '____')
if [ -f "$FAKE_GH_DIR/rest$name" ]; then
  cat "$FAKE_GH_DIR/rest$name"
  exit 0
fi
if [ -f "$FAKE_GH_DIR/rest.default" ]; then
  cat "$FAKE_GH_DIR/rest.default"
  exit 0
fi
exit 0
SH
  chmod +x "$fakebin/gh"
}

# A home with a state directory and a fake gh on PATH.
make_home() {  # <name>
  local home="$TMP_ROOT/$1" fakebin
  mkdir -p "$home/state"
  fakebin=$(fm_fakebin "$home")
  setup_fake_gh "$fakebin"
  printf '%s\n' "$home"
}

run_tracker() {  # <home> <args...>
  local home=$1
  shift
  PATH="$home/fakebin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$home" \
    FM_STATE_OVERRIDE="$home/state" FAKE_GH_DIR="$FAKE_GH_DIR" \
    FAKE_GH_LOG="$FAKE_GH_LOG" "$TRACKER" "$@"
}

# Deliberately passes an EMPTY state root, which is the case the guard exists
# for: "$STATE/$task.check.sh" with an empty $STATE resolves to a path at the
# filesystem root that nobody named.
run_tracker_empty_state() {  # <home> <args...>
  local home=$1
  shift
  PATH="$home/fakebin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$home" \
    FM_STATE_OVERRIDE="" FAKE_GH_DIR="$FAKE_GH_DIR" \
    FAKE_GH_LOG="$FAKE_GH_LOG" "$TRACKER" "$@"
}

run_notify() {  # <home> <args...>
  local home=$1
  shift
  PATH="$home/fakebin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$home" \
    FM_STATE_OVERRIDE="$home/state" FAKE_GH_DIR="$FAKE_GH_DIR" \
    FAKE_GH_LOG="$FAKE_GH_LOG" "$NOTIFY" "$@"
}

reset_gh() {
  rm -f "$FAKE_GH_DIR"/rest* "$FAKE_GH_DIR"/http.* "$FAKE_GH_DIR"/graphql.out
  : > "$FAKE_GH_LOG"
}

# Build one graph record the way bin/fm-tracker-lib.sh's query emits it: fields
# separated by US (0x1f), title and body base64.
graph_record() {  # <number> <state> <labels> <assignees> <parent> <blockers> <title> <body>
  local us=$'\037'
  printf '%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n' \
    "$1" "$us" "$2" "$us" "$3" "$us" "$4" "$us" "$5" "$us" "$6" "$us" \
    "$(printf '%s' "$7" | base64 | tr -d '\n')" "$us" \
    "$(printf '%s' "$8" | base64 | tr -d '\n')"
}

# A decision body carrying what an answer needs: the context, two options each
# stating its own consequence, and a recommendation. `add --type decision`
# refuses anything less, so every decision case below starts from this.
GOOD_DECISION=$(cat <<'BODY'
## Context

The queue backs up past 10k messages twice a week and the retry path replays duplicates.

## Options

- Keep the current queue - no migration, and the duplicate replays stay.
- Move to the managed queue - duplicates go away, and it adds a monthly bill and a migration week.

## Recommendation

Move to the managed queue: the duplicate replays are already costing more than the bill.
BODY
)

# ===========================================================================
# Defect 1: a prose blocking edge is refused, and nothing is created
# ===========================================================================

HOME_A=$(make_home a)

reset_gh
out=$(run_tracker "$HOME_A" add o/r --type task --title 'a task' \
  --body 'Blocked by: #12 - the ruling has to land first.' 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "prose blocker must be refused"
assert_contains "$out" "refusing a prose blocking edge" "refusal must name the reason"
assert_contains "$out" "Blocked by: #12" "refusal must quote the offending line"
assert_contains "$out" "--blocked-by" "refusal must name the supported alternative"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused body must not create anything"
pass "add refuses a prose blocking edge before any write"

# The same refusal must fire on every spelling that reads as a blocker, not just
# the one the original defect happened to use.
for phrasing in \
  'This depends on #12.' \
  'waiting on #12 to land' \
  'blocker: #12 must close first'; do
  reset_gh
  out=$(run_tracker "$HOME_A" add o/r --type task --title 'a task' --body "$phrasing" 2>&1)
  rc=$?
  expect_code_out 1 "$rc" "$out" "prose blocker '$phrasing' must be refused"
  assert_no_grep "POST" "$FAKE_GH_LOG" "refused '$phrasing' must not create anything"
done
pass "add refuses every prose blocker phrasing, not only the observed one"

# Prose wearing the canonical heading is still prose: the heading alone does not
# make an edge, and this is the shape a well-meaning hand-edit produces.
reset_gh
out=$(run_tracker "$HOME_A" add o/r --type task --title 'a task' \
  --body "$(printf '## Blocked by\nthe grain ruling, once it lands\n')" 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "prose under the canonical heading must be refused"
assert_no_grep "POST" "$FAKE_GH_LOG" "refused canonical-heading prose must not create anything"
pass "add refuses prose under the canonical heading"

# A body carrying an already-correct task list is NOT prose and must be accepted,
# or the refusal would be unusable.
reset_gh
printf '7\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_A" add o/r --type task --title 'a task' \
  --body "$(printf '## Blocked by\n- [ ] #12\n')" 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a canonical task list must be accepted"
pass "add accepts an already-canonical blocked-by section"

# ===========================================================================
# Defect 2: type is carried by a label and never by the title
# ===========================================================================

reset_gh
printf '7\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_A" add o/r --type decision \
  --title 'TASK: does the house grain belong here?' --body "$GOOD_DECISION" 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "add must create a typed ticket"
assert_grep 'labels[]=fm:decision' "$FAKE_GH_LOG" "type must be sent as a label"
assert_grep 'TASK: does the house grain belong here?' "$FAKE_GH_LOG" "the title must pass through verbatim"
assert_contains "$out" "[fm:decision]" "the reported type must come from the label"
pass "add carries type as a label, independent of the title text"

# The title is never consulted for type: a title that says DECISION on a ticket
# typed task must still be a task.
reset_gh
printf '7\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_A" add o/r --type task --title 'DECISION: ship it' 2>&1)
expect_code_out 0 "$?" "$out" "a misleading title must not change the type"
assert_grep 'labels[]=fm:task' "$FAKE_GH_LOG" "a DECISION title typed task must send fm:task"
assert_no_grep 'labels[]=fm:decision' "$FAKE_GH_LOG" "the title must never produce a decision label"
pass "a title reading DECISION does not reclassify a task"

reset_gh
out=$(run_tracker "$HOME_A" add o/r --type Decision --title 'x' 2>&1)
expect_code_out 2 "$?" "$out" "an unknown type must be a usage error"
assert_contains "$out" "invalid --type" "the type vocabulary must be closed"
pass "add refuses a type outside the closed vocabulary"

# ===========================================================================
# Blocking edges are written in the one form GitHub resolves
# ===========================================================================

reset_gh
printf '7\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_A" add o/r --type task --title 'blocked work' --blocked-by 12,13 2>&1)
expect_code_out 0 "$?" "$out" "add must accept --blocked-by"
assert_grep '## Blocked by' "$FAKE_GH_LOG" "the canonical heading must be written"
assert_grep '- [ ] #12' "$FAKE_GH_LOG" "each blocker must be a task-list reference"
assert_grep '- [ ] #13' "$FAKE_GH_LOG" "each blocker must be a task-list reference"
pass "add writes blocking edges as the task list GitHub resolves"

reset_gh
out=$(run_tracker "$HOME_A" add o/r --type task --title 'x' --blocked-by 'twelve' 2>&1)
expect_code_out 2 "$?" "$out" "a non-numeric blocker must be a usage error"
pass "add refuses a non-numeric blocker"

# ===========================================================================
# frontier: ready, blocked, and what each blocked ticket waits on
# ===========================================================================

reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 2 OPEN 'fm:decision' '' '1' '' 'a captain decision' ''
  graph_record 3 OPEN 'fm:task' '' '1' '2|OPEN|decision|' 'blocked work' ''
  graph_record 4 OPEN 'fm:task' '' '1' '2|CLOSED|decision|' 'freed work' ''
  graph_record 5 OPEN 'fm:task' 'someone' '1' '' 'claimed work' ''
  graph_record 6 CLOSED 'fm:task' '' '1' '' 'finished work' ''
} > "$FAKE_GH_DIR/graphql.out"

out=$(run_tracker "$HOME_A" frontier o/r 2>&1)
expect_code_out 0 "$?" "$out" "frontier must succeed"
ready=$(printf '%s\n' "$out" | sed -n '/^READY/,/^$/p')
blocked=$(printf '%s\n' "$out" | sed -n '/^BLOCKED/,/^$/p')
claimed=$(printf '%s\n' "$out" | sed -n '/^CLAIMED/,$p')
assert_contains "$ready" "#4" "a ticket whose only blocker is closed must be ready"
assert_not_contains "$ready" "#3" "a ticket with an open blocker must not be ready"
assert_contains "$blocked" "#3" "a ticket with an open blocker must be blocked"
assert_contains "$blocked" "waits on #2" "a blocked ticket must name what it waits on"
assert_contains "$blocked" "a decision the captain owns" "the wait must be described by type"
assert_not_contains "$ready" "#5" "an assigned ticket must be excluded from ready"
assert_not_contains "$blocked" "#5" "an assigned ticket must be excluded from blocked"
assert_contains "$claimed" "#5" "an assigned ticket is reported as claimed"
assert_not_contains "$out" "#6" "a closed ticket is not on the frontier"
pass "frontier splits ready and blocked, names the wait, and excludes claims"

# A blocked ticket becomes ready with no rewrite once its blocker closes: the
# same records with the blocker's state flipped are all it takes.
reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 3 OPEN 'fm:task' '' '1' '2|CLOSED|decision|' 'blocked work' ''
} > "$FAKE_GH_DIR/graphql.out"
out=$(run_tracker "$HOME_A" frontier o/r 2>&1)
assert_contains "$(printf '%s\n' "$out" | sed -n '/^READY/,/^$/p')" "#3" \
  "a dependent must become ready when its blocker closes"
pass "a dependent moves from blocked to ready when its blocker closes"

# ===========================================================================
# validate: the standing guard against both defects returning by hand-edit
# ===========================================================================

reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 2 OPEN '' '' '1' '' 'DECISION: untyped' ''
  graph_record 3 OPEN 'fm:task' '' '1' '' 'invisible blocker' 'Blocked by: #2 - must land first.'
  graph_record 4 OPEN 'fm:task fm:decision' '' '1' '' 'two types' ''
  graph_record 5 OPEN 'fm:task' '' '-' '' 'orphan' ''
  graph_record 6 OPEN 'fm:task' '' '1' '2|OPEN|decision|' 'off-convention heading' \
    "$(printf '**Blocked by:**\n- [ ] #2\n')"
} > "$FAKE_GH_DIR/graphql.out"

out=$(run_tracker "$HOME_A" validate o/r 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "validate must fail when tickets are malformed"
assert_contains "$out" "#2 missing type label" "an unlabelled ticket must be reported"
assert_contains "$out" "#3 line 1 names #2 as a blocker with no resolved edge" \
  "a prose blocker with no edge must be reported as invisible"
assert_contains "$out" "reports READY while blocked" "the consequence must be stated"
assert_contains "$out" "#4 carries 2 type labels" "an ambiguous type must be reported"
assert_contains "$out" "#5 orphaned" "a ticket with no parent must be reported"
pass "validate reports missing type, invisible prose blockers, ambiguity and orphans"

# The distinction that keeps validate honest: an off-convention heading over a
# reference GitHub DID resolve is not invisible, and must not be reported as if
# it were. Claiming otherwise would be the same confident wrong answer this
# whole layer exists to prevent.
assert_contains "$out" "#6 line 2 uses a non-canonical blocked-by heading for #2" \
  "an off-convention heading over a real edge must be reported as such"
assert_contains "$out" "the edge" "the report must say the edge still resolves"
assert_not_contains "$(printf '%s\n' "$out" | grep '#6' || true)" "no resolved edge" \
  "a resolved edge must never be reported as invisible"
pass "validate distinguishes an invisible prose blocker from an off-convention heading"

reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 2 OPEN 'fm:task' '' '1' '' 'clean work' 'Done when: it ships.'
} > "$FAKE_GH_DIR/graphql.out"
out=$(run_tracker "$HOME_A" validate o/r 2>&1)
expect_code_out 0 "$?" "$out" "validate must pass on well-formed tickets"
assert_contains "$out" "no malformed tickets" "a clean repo must say so"
pass "validate passes on tickets this script wrote"

# ===========================================================================
# claim, release and answer
# ===========================================================================

reset_gh
printf 'someone-else\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_A" claim o/r 3 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "claiming a ticket someone else holds must be refused"
assert_contains "$out" "already claimed by" "the refusal must name the holder"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused claim must not reassign"
pass "claim refuses a ticket another session already holds"

reset_gh
printf 'fm:task\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_A" answer o/r 3 --decision 'do it' 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "answering a task must be refused"
assert_contains "$out" "not a decision or unknown" "the refusal must name the reason"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused answer must not comment or close"
pass "answer refuses a ticket that is not a decision or unknown"

# ===========================================================================
# Destructive-path guards
#
# Disarming removes files, so the guards assert the facts that make a file ours
# before anything is removed. An empty variable is the case that matters: it
# silently resolves into a path nobody named, and it must refuse instead.
# ===========================================================================

HOME_B=$(make_home b)
: > "$HOME_B/state/other.check.sh"
chmod 0700 "$HOME_B/state/other.check.sh"

out=$(run_tracker_empty_state "$HOME_B" unwatch --task anything 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "an empty state root must refuse"
assert_contains "$out" "refusing to remove anything" "the refusal must lead with what it did not do"
# Pin the state-root guard specifically, not just the outcome. A later guard
# also refuses this input, so asserting only "it refused" would still pass with
# this guard deleted - the test would be reporting on a different guard than the
# one it names.
assert_contains "$out" "is not an existing state directory" \
  "the state-root guard itself must be what refuses an empty root"
assert_present "$HOME_B/state/other.check.sh" "an empty state root must remove nothing"
pass "unwatch refuses when the state root is empty, and removes nothing"

out=$(run_tracker "$HOME_B" unwatch --task '' 2>&1)
expect_code_out 2 "$?" "$out" "an empty task id must be a usage error"
assert_present "$HOME_B/state/other.check.sh" "an empty task id must remove nothing"
pass "unwatch refuses an empty task id, and removes nothing"

out=$(run_tracker "$HOME_B" unwatch --task ../escape 2>&1)
expect_code_out 2 "$?" "$out" "a traversing task id must be a usage error"
assert_present "$HOME_B/state/other.check.sh" "a traversing task id must remove nothing"
pass "unwatch refuses a task id that would escape the state root"

out=$(run_tracker "$HOME_B" unwatch --task never-armed 2>&1)
expect_code_out 1 "$?" "$out" "an unarmed task must refuse"
assert_contains "$out" "no tracker watch is armed" "the refusal must say why"
pass "unwatch refuses a task that has no watch armed"

# A watch sidecar next to somebody else's check: disarming must not touch the
# check it did not publish.
printf '%s\n%s\n' 'fm-tracker-watch-v1' 'o/r' > "$HOME_B/state/other.tracker-watch"
out=$(run_tracker "$HOME_B" unwatch --task other 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "a foreign check must refuse"
assert_contains "$out" "not this watch's own shim" "the refusal must name the mismatch"
assert_present "$HOME_B/state/other.check.sh" "a foreign check must survive"
pass "unwatch refuses to remove a check it did not publish"

out=$(run_tracker "$HOME_B" watch --task other o/r 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "arming over a foreign check must refuse"
assert_contains "$out" "already armed" "the refusal must name the conflict"
pass "watch refuses to overwrite a check another owner armed"

# The happy path, so the guards above are not passing vacuously.
out=$(run_tracker "$HOME_B" watch --task mine o/r 2>&1)
expect_code_out 0 "$?" "$out" "arming a fresh watch must succeed"
assert_present "$HOME_B/state/mine.check.sh" "arming must publish the check"
assert_present "$HOME_B/state/mine.tracker-watch" "arming must publish the sidecar"
assert_present "$HOME_B/state/mine.check-trust" "arming must register the check"
# bin/fm-pr-lib.sh owns the cross-platform stat forms. Rolling them here with a
# `stat -f ... || stat -c ...` fallback is wrong on Linux, where `stat -f`
# succeeds as "filesystem status" and the fallback never runs.
mode=$(bash -c '. "$1/bin/fm-pr-lib.sh"; fm_pr_file_mode "$2"' _ "$ROOT" "$HOME_B/state/mine.check.sh")
[ "$mode" = 700 ] || fail "the check must be mode 0700, got $mode"
links=$(bash -c '. "$1/bin/fm-pr-lib.sh"; fm_pr_file_link_count "$2"' _ "$ROOT" "$HOME_B/state/mine.check.sh")
[ "$links" = 1 ] || fail "the check must be a single-link file, got $links links"
pass "watch publishes a registered, single-link, mode-0700 check"

out=$(run_tracker "$HOME_B" unwatch --task mine 2>&1)
expect_code_out 0 "$?" "$out" "disarming its own watch must succeed"
assert_absent "$HOME_B/state/mine.check.sh" "disarming must remove its own check"
assert_absent "$HOME_B/state/mine.tracker-watch" "disarming must remove its own sidecar"
assert_absent "$HOME_B/state/mine.check-trust" "disarming must remove its own registration"
assert_present "$HOME_B/state/other.check.sh" "disarming must leave another owner's check alone"
pass "unwatch removes exactly the artifacts it published"

# ===========================================================================
# The wake poll
# ===========================================================================

HOME_C=$(make_home c)

http_response() {  # <index> <status> <extra-headers> <body>
  {
    printf 'HTTP/2.0 %s\r\n' "$2"
    printf 'Etag: "e%s"\r\n' "$1"
    printf '%s' "$3"
    printf '\r\n'
    printf '%s\n' "$4"
  } > "$FAKE_GH_DIR/http.$1"
}

arm_watch() {  # <home> <task>
  run_tracker "$1" watch --task "$2" o/r >/dev/null 2>&1 \
    || fail "could not arm the watch fixture"
  rm -f "$FAKE_GH_DIR"/http.* 
  printf '1\n' > "$FAKE_GH_DIR/http.seq"
}

# Seeding: the first run after arming establishes position and must be silent,
# or arming a watch would immediately wake firstmate on the whole backlog.
reset_gh
arm_watch "$HOME_C" wake
http_response 1 '200 OK' 'X-Poll-Interval: 60
' '[{"subject":{"type":"Issue","latest_comment_url":"https://api.github.com/repos/o/r/issues/9"},"repository":{"full_name":"o/r"},"updated_at":"2026-08-25T10:00:00Z"}]'
http_response 2 '200 OK' '' '[{"issue_url":"https://api.github.com/repos/o/r/issues/9","updated_at":"2026-08-25T10:00:00Z"}]'
out=$(run_notify "$HOME_C" --task wake 2>&1)
expect_code_out 0 "$?" "$out" "the seeding run must succeed"
[ -z "$out" ] || fail "the seeding run must print nothing, got: $out"
assert_present "$HOME_C/state/wake.tracker-cursor" "the seeding run must record its position"
pass "the first poll after arming seeds silently"

# 304 is the common case and must be silent and free.
rm -f "$FAKE_GH_DIR"/http.*
printf '1\n' > "$FAKE_GH_DIR/http.seq"
http_response 1 '304 Not Modified' 'X-Poll-Interval: 60
' ''
http_response 2 '304 Not Modified' '' ''
out=$(run_notify "$HOME_C" --task wake 2>&1)
expect_code_out 0 "$?" "$out" "a 304 poll must succeed"
[ -z "$out" ] || fail "a 304 poll must print nothing, got: $out"
pass "a 304 poll prints nothing"

# A genuinely new comment produces exactly one line naming the repo and issue.
rm -f "$FAKE_GH_DIR"/http.*
printf '1\n' > "$FAKE_GH_DIR/http.seq"
sed -i.bak 's/^notifications_next=.*/notifications_next=0/' "$HOME_C/state/wake.tracker-cursor"
rm -f "$HOME_C/state/wake.tracker-cursor.bak"
http_response 1 '200 OK' 'X-Poll-Interval: 60
' '[{"subject":{"type":"Issue","latest_comment_url":"https://api.github.com/repos/o/r/issues/9"},"repository":{"full_name":"o/r"},"updated_at":"2026-08-25T12:00:00Z"}]'
http_response 2 '304 Not Modified' '' ''
out=$(run_notify "$HOME_C" --task wake 2>&1)
expect_code_out 0 "$?" "$out" "a new-comment poll must succeed"
[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" = 1 ] || fail "the wake must be one line, got: $out"
assert_contains "$out" "o/r#9" "the wake must name the repository and issue"
pass "a new comment wakes firstmate with one line naming the repo and issue"

# The same comment must not fire twice: a repeated wake on settled state is the
# supervision equivalent of a stuck alarm.
rm -f "$FAKE_GH_DIR"/http.*
printf '1\n' > "$FAKE_GH_DIR/http.seq"
sed -i.bak 's/^notifications_next=.*/notifications_next=0/' "$HOME_C/state/wake.tracker-cursor"
rm -f "$HOME_C/state/wake.tracker-cursor.bak"
http_response 1 '200 OK' 'X-Poll-Interval: 60
' '[{"subject":{"type":"Issue","latest_comment_url":"https://api.github.com/repos/o/r/issues/9"},"repository":{"full_name":"o/r"},"updated_at":"2026-08-25T12:00:00Z"}]'
http_response 2 '304 Not Modified' '' ''
out=$(run_notify "$HOME_C" --task wake 2>&1)
[ -z "$out" ] || fail "an already-reported comment must not wake again, got: $out"
pass "an already-reported comment does not wake firstmate twice"

# X-Poll-Interval is the server's own cadence and is honoured as a floor: with
# the interval unexpired, the poll must not re-request the inbox at all.
reset_gh
arm_watch "$HOME_C" cadence
http_response 1 '200 OK' 'X-Poll-Interval: 3600
' '[]'
http_response 2 '304 Not Modified' '' ''
run_notify "$HOME_C" --task cadence >/dev/null 2>&1
assert_grep 'notifications_interval=3600' "$HOME_C/state/cadence.tracker-cursor" \
  "the server's stated cadence must be recorded"
: > "$FAKE_GH_LOG"
printf '1\n' > "$FAKE_GH_DIR/http.seq"
run_notify "$HOME_C" --task cadence >/dev/null 2>&1
assert_no_grep '/notifications' "$FAKE_GH_LOG" \
  "the inbox must not be re-requested before the stated interval elapses"
pass "the poll honours the server's X-Poll-Interval as a floor"

# A poll for a task with no armed watch must do nothing at all, so a stale check
# left behind by anything else cannot start making requests.
reset_gh
out=$(run_notify "$HOME_C" --task unarmed 2>&1)
expect_code_out 0 "$?" "$out" "an unarmed poll must exit cleanly"
[ -z "$out" ] || fail "an unarmed poll must print nothing, got: $out"
assert_no_grep 'api' "$FAKE_GH_LOG" "an unarmed poll must make no request"
pass "a poll with no armed watch makes no request and prints nothing"

# ===========================================================================
# Stock Bash 3.2
#
# The poll runs as a watcher check on whatever bash the machine has, and stock
# macOS Bash is 3.2. CI's stock-bash job only PARSES the shell inventory, and a
# bash-4-only construct parses fine there while failing at runtime - which for
# this script meant writing no cursor and still exiting 0, indistinguishable
# from an inbox where nothing happened. A wake that silently never fires is the
# worst failure this script has, so it is exercised here rather than parsed.
stock_bash=''
for candidate in /bin/bash /usr/bin/bash; do
  [ -x "$candidate" ] || continue
  # shellcheck disable=SC2016 # $BASH_VERSION must expand in the CHILD shell.
  case "$("$candidate" -c 'printf %s "$BASH_VERSION"' 2>/dev/null)" in
    3.*) stock_bash=$candidate; break ;;
  esac
done

if [ -z "$stock_bash" ]; then
  printf 'note: no Bash 3.x on this machine; stock-Bash case not exercised\n'
else
  HOME_D=$(make_home d)
  reset_gh
  run_tracker "$HOME_D" watch --task stock o/r >/dev/null 2>&1 \
    || fail "could not arm the stock-bash fixture"
  rm -f "$FAKE_GH_DIR"/http.*
  printf '1\n' > "$FAKE_GH_DIR/http.seq"
  http_response 1 '200 OK' 'X-Poll-Interval: 60
' '[]'
  http_response 2 '200 OK' '' '[]'
  out=$(PATH="$HOME_D/fakebin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$HOME_D" \
    FM_STATE_OVERRIDE="$HOME_D/state" FAKE_GH_DIR="$FAKE_GH_DIR" FAKE_GH_LOG="$FAKE_GH_LOG" \
    "$stock_bash" "$NOTIFY" --task stock 2>&1)
  rc=$?
  expect_code_out 0 "$rc" "$out" "the poll must run under $stock_bash"
  assert_not_contains "$out" "invalid option" "the poll must use no Bash 4 builtin option"
  assert_not_contains "$out" "unbound variable" "the poll must not trip set -u under Bash 3.2"
  # Exiting 0 is not enough: it must actually have recorded position, or it
  # would be failing silently in exactly the way that looks like success.
  assert_present "$HOME_D/state/stock.tracker-cursor" \
    "the poll must record its cursor under Bash 3.2, not just exit 0"
  assert_grep 'seeded=1' "$HOME_D/state/stock.tracker-cursor" \
    "the cursor written under Bash 3.2 must be complete"
  pass "the wake poll runs correctly under stock Bash 3.x, not merely parses"
fi

# ===========================================================================
# An assignment carries two opposite meanings, and the assignee decides which
#
# For an agent it is a claim and the ticket leaves the frontier. For the captain
# it is a wait and the ticket must stay in BLOCKED naming them, because a
# decision nobody is working on is the single most important thing the frontier
# has to report. These cases pin BOTH directions: the captain case is the new
# behavior, and the agent case is the behavior that must not break to get it.
# ===========================================================================

HOME_C=$(make_home c)
mkdir -p "$HOME_C/config"
printf 'the-captain\n' > "$HOME_C/config/captain-github"

assignment_graph() {
  {
    graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
    graph_record 2 OPEN 'fm:decision' 'the-captain' '1' '' 'which storage engine' ''
    graph_record 3 OPEN 'fm:task' 'a-crewmate' '1' '' 'claimed work' ''
    graph_record 4 OPEN 'fm:task' '' '1' '' 'unassigned work' ''
  } > "$FAKE_GH_DIR/graphql.out"
}

reset_gh
assignment_graph
out=$(run_tracker "$HOME_C" frontier o/r 2>&1)
expect_code_out 0 "$?" "$out" "frontier must succeed"
ready=$(printf '%s\n' "$out" | sed -n '/^READY/,/^$/p')
blocked=$(printf '%s\n' "$out" | sed -n '/^BLOCKED/,/^$/p')
claimed=$(printf '%s\n' "$out" | sed -n '/^CLAIMED/,$p')

assert_contains "$blocked" "#2" "a decision assigned to the captain must be blocked"
assert_contains "$blocked" "waits on the captain (the-captain)" \
  "the blocked entry must name the captain as what it waits on"
assert_not_contains "$claimed" "#2" "the captain's assignment must not read as a claim"
assert_not_contains "$ready" "#2" "a decision waiting on the captain is not ready work"
pass "a decision assigned to the captain is BLOCKED naming them, not CLAIMED"

assert_contains "$claimed" "#3" "an assignment to an agent must still be a claim"
assert_contains "$claimed" "claimed by a-crewmate" "the claim must still name its holder"
assert_not_contains "$ready" "#3" "a claimed ticket must still leave the ready set"
assert_not_contains "$blocked" "#3" "a claimed ticket must still leave the blocked set"
pass "an assignment to anyone else still reads as claimed and leaves both sets"

assert_contains "$ready" "#4" "an unassigned ticket with no blocker must be ready"
assert_not_contains "$blocked" "#4" "an unassigned ticket must not be blocked"
assert_not_contains "$claimed" "#4" "an unassigned ticket must not be claimed"
pass "an unassigned ticket is unchanged: ready, and in neither other set"

# The safe default. With no captain login configured the classifier is inert and
# every assignment is a claim, which is the behavior before this distinction
# existed. A wrong default here would silently reclassify every ticket a human
# touches, so it is pinned rather than assumed.
HOME_NOCAP=$(make_home nocaptain)
reset_gh
assignment_graph
out=$(run_tracker "$HOME_NOCAP" frontier o/r 2>&1)
expect_code_out 0 "$?" "$out" "frontier must succeed with no captain configured"
assert_contains "$(printf '%s\n' "$out" | sed -n '/^CLAIMED/,$p')" "#2" \
  "with no captain configured every assignment must still read as a claim"
assert_contains "$out" "no captain login is configured" \
  "an inert classifier must say so rather than look like a working one"
assert_contains "$out" "config/captain-github" "the note must name the file that fixes it"
pass "with no captain configured, the classifier is inert and says so"

# GitHub logins are case-insensitive, so a case difference between the config
# file and the API response must not be what decides wait versus claim.
HOME_CASE=$(make_home captaincase)
mkdir -p "$HOME_CASE/config"
printf 'The-Captain\n' > "$HOME_CASE/config/captain-github"
reset_gh
assignment_graph
out=$(run_tracker "$HOME_CASE" frontier o/r 2>&1)
assert_contains "$(printf '%s\n' "$out" | sed -n '/^BLOCKED/,/^$/p')" "#2" \
  "a case difference in the login must not reclassify the captain's wait"
pass "the captain is matched case-insensitively, as GitHub treats logins"

# A ticket held by the captain AND an agent is still a wait: the agent cannot
# finish it either way, and the wait is the answer that has to stay visible.
reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 2 OPEN 'fm:decision' 'a-crewmate the-captain' '1' '' 'shared hold' ''
} > "$FAKE_GH_DIR/graphql.out"
out=$(run_tracker "$HOME_C" frontier o/r 2>&1)
assert_contains "$(printf '%s\n' "$out" | sed -n '/^BLOCKED/,/^$/p')" "#2" \
  "a ticket the captain also holds must be reported as a wait"
pass "an assignment holding the captain and an agent is a wait, not a claim"

# The same distinction one level down. A blocker the captain holds is not
# claimed work, and calling it "claimed by" would hide the wait from the ticket
# that reports it.
reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 3 OPEN 'fm:task' '' '1' '2|OPEN|decision|the-captain' 'downstream work' ''
} > "$FAKE_GH_DIR/graphql.out"
out=$(run_tracker "$HOME_C" frontier o/r 2>&1)
blocked=$(printf '%s\n' "$out" | sed -n '/^BLOCKED/,/^$/p')
assert_contains "$blocked" "with the captain (the-captain)" \
  "a blocker the captain holds must read as a wait on them"
assert_not_contains "$blocked" "claimed by the-captain" \
  "a blocker the captain holds must never read as claimed work"
pass "a blocker held by the captain reads as a wait on them, not as a claim"

# claim still claims - what claim does for agents is unchanged - but when the
# fleet is authenticated as the captain's own account it says what the frontier
# will then report, rather than writing that ambiguity silently.
reset_gh
printf '\n' > "$FAKE_GH_DIR/rest.default"
export FAKE_GH_LOGIN=the-captain
out=$(run_tracker "$HOME_C" claim o/r 3 2>&1)
rc=$?
unset FAKE_GH_LOGIN
expect_code_out 0 "$rc" "$out" "claiming as the captain's own login must still claim"
assert_contains "$out" "claimed #3" "the claim itself must be unchanged"
assert_contains "$out" "the configured captain login" "the collision must be named"
assert_grep "assignees" "$FAKE_GH_LOG" "the assignment must still be written"
pass "claim as the captain's own login still claims, and names the ambiguity"

# ===========================================================================
# A decision has to be answerable cold
#
# A decision ticket reaches the captain as a notification and nothing else, so
# the write path refuses a body that would send them off to do research first.
# The line drawn is structural: presence of context, of two options each stating
# its consequence, and of a recommendation. Whether the recommendation is any
# good is the author's problem and is deliberately not judged here.
# ===========================================================================

# The stored shape a well-formed decision ends up with, written out here rather
# than derived from the script, so this fixture cannot drift into agreeing with
# a broken writer.
ESCAPE_HATCH=$(cat <<'BODY'


## Or something else

The options above are a starting point, not the whole answer space.
If the right call is none of them, say it in your own words and it will be recorded verbatim.
BODY
)

reset_gh
printf '7\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_C" add o/r --type decision --title 'which queue' \
  --body "$GOOD_DECISION" 2>&1)
expect_code_out 0 "$?" "$out" "a decision that can be answered cold must be accepted"
assert_grep "## Or something else" "$FAKE_GH_LOG" \
  "the write path must add the invitation to answer with something unlisted"
assert_grep "say it in your own words" "$FAKE_GH_LOG" \
  "the invitation must be explicit, not implied by a heading"
assert_grep "## Recommendation" "$FAKE_GH_LOG" "the author's own sections must survive"
pass "a well-formed decision is accepted and the escape hatch is written for it"

# Each refusal is asserted by the reason it names, not merely by exit 1: three
# guards refuse the same body, so "it refused" alone would keep passing with any
# two of them deleted.
reset_gh
out=$(run_tracker "$HOME_C" add o/r --type decision --title 'which queue' \
  --body 'Postgres or SQLite?' 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "a bare decision body must be refused"
assert_contains "$out" "cannot be answered cold" "the refusal must name the reason"
assert_contains "$out" '"## Context" section' "the missing context must be named"
assert_contains "$out" '"## Options"' "the missing options must be named"
assert_contains "$out" '"## Recommendation" section' "the missing recommendation must be named"
assert_contains "$out" "_Which option, and why" "the refusal must print the shape that works"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused decision must not create anything"
pass "add refuses a decision body carrying none of what an answer needs"

reset_gh
out=$(run_tracker "$HOME_C" add o/r --type decision --title 'which queue' --body "$(cat <<'BODY'
## Context

The queue backs up twice a week.

## Options

- Move to the managed queue - duplicates go away, and it adds a monthly bill.

## Recommendation

Move to the managed queue.
BODY
)" 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "a single-option decision must be refused"
assert_contains "$out" "one option is not a choice" "the refusal must say why one option fails"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused decision must not create anything"
pass "add refuses a decision offering a single option"

reset_gh
out=$(run_tracker "$HOME_C" add o/r --type decision --title 'which queue' --body "$(cat <<'BODY'
## Context

The queue backs up twice a week.

## Options

- Keep the current queue
- Move to the managed queue

## Recommendation

Move to the managed queue.
BODY
)" 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "options with no consequences must be refused"
assert_contains "$out" "states no consequence" "the refusal must name the empty option"
assert_contains "$out" "Keep the current queue" "the refusal must quote the offending option"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused decision must not create anything"
pass "add refuses options that state no consequence"

reset_gh
out=$(run_tracker "$HOME_C" add o/r --type decision --title 'which queue' --body "$(cat <<'BODY'
## Context

The queue backs up twice a week.

## Options

- Keep the current queue - the duplicate replays stay.
- Move to the managed queue - duplicates go away, and it adds a monthly bill.
BODY
)" 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "a decision with no recommendation must be refused"
assert_contains "$out" '"## Recommendation" section' "the refusal must name what is missing"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused decision must not create anything"
pass "add refuses a decision with options but no recommendation"

# The escape hatch has one owner. An author-written copy would let a second,
# possibly weaker, invitation ship beside the fixed one.
reset_gh
out=$(run_tracker "$HOME_C" add o/r --type decision --title 'which queue' \
  --body "$GOOD_DECISION

## Or something else

whatever you like" 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "an author-written escape hatch must be refused"
assert_contains "$out" "this script writes that one" "the refusal must name the owner"
assert_no_grep "POST" "$FAKE_GH_LOG" "a refused decision must not create anything"
pass "add refuses an author-written escape hatch, which this script owns"

# The shape is required of decisions only. A task with a plain body is untouched.
reset_gh
printf '7\n' > "$FAKE_GH_DIR/rest.default"
out=$(run_tracker "$HOME_C" add o/r --type task --title 'do the thing' \
  --body 'Done when it ships.' 2>&1)
expect_code_out 0 "$?" "$out" "a task body must not be held to the decision shape"
assert_no_grep "## Or something else" "$FAKE_GH_LOG" \
  "a task must not be given a decision's escape hatch"
pass "the decision shape is required of decisions only"

# validate is the standing guard: the write path refuses a malformed decision,
# so one in the graph arrived by hand-edit, which is what validate exists for.
reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 2 OPEN 'fm:decision' '' '1' '' 'hand-edited decision' 'Postgres or SQLite?'
} > "$FAKE_GH_DIR/graphql.out"
out=$(run_tracker "$HOME_C" validate o/r 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "a hand-edited decision must fail validation"
assert_contains "$out" '#2 has no "## Context" section' "validate must name the missing context"
assert_contains "$out" '#2 has no "## Or something else" section' \
  "validate must catch a stripped escape hatch"
pass "validate reports a decision that can no longer be answered cold"

reset_gh
{
  graph_record 1 OPEN 'fm:destination' '' '-' '' 'the destination' ''
  graph_record 2 OPEN 'fm:decision' '' '1' '' 'a good decision' \
    "$GOOD_DECISION$ESCAPE_HATCH"
} > "$FAKE_GH_DIR/graphql.out"
out=$(run_tracker "$HOME_C" validate o/r 2>&1)
expect_code_out 0 "$?" "$out" "a decision this script wrote must pass validation"
assert_contains "$out" "no malformed tickets" "a clean decision must be reported clean"
pass "validate passes the decision shape this script writes"

# ===========================================================================
# The fleet's own comments
#
# The wake fired on every comment firstmate wrote, because the fleet
# authenticates as the captain's own account and the comment feed cannot tell
# them apart by author. These cases pin the discriminator that can: what we
# wrote, recorded by id as we write it. The pair that matters is the two
# directions - the same response body, the same author, the same repository,
# differing only in whether the id is in the record.
# ===========================================================================

HOME_D=$(make_home d)
SELF_RECORD="$HOME_D/state/.tracker-self-comments"

# One comment in the feed, with GitHub's own shape: unedited comments carry
# created_at == updated_at, which is what lets an edit still wake firstmate.
comment_feed() {  # <id> <created> [updated]
  printf '[{"id":%s,"issue_url":"https://api.github.com/repos/o/r/issues/9",' "$1"
  printf '"created_at":"%s","updated_at":"%s"}]' "$2" "${3:-$2}"
}

# Every poll below drives the comment feed, so the inbox is pinned quiet: it
# structurally cannot carry the account its own actions and has nothing to say
# about this behaviour.
pin_inbox_quiet() {  # <home> <task>
  sed -i.bak 's/^notifications_next=.*/notifications_next=9999999999/' \
    "$1/state/$2.tracker-cursor"
  rm -f "$1/state/$2.tracker-cursor.bak"
}

reset_gh
printf 'fm:decision\n' > "$FAKE_GH_DIR/rest.default"
printf '5555\n' > "$FAKE_GH_DIR/rest_repos_o_r_issues_9_comments"
out=$(run_tracker "$HOME_D" answer o/r 9 --decision 'ship it' 2>&1)
expect_code_out 0 "$?" "$out" "answering a decision must succeed"
assert_grep '5555' "$SELF_RECORD" "answer must record the comment id it wrote"
pass "answer records the comment id it wrote"

reset_gh
printf '6666\n' > "$FAKE_GH_DIR/rest_repos_o_r_issues_9_comments"
out=$(run_tracker "$HOME_D" comment o/r 9 --body 'closing this out' 2>&1)
expect_code_out 0 "$?" "$out" "commenting must succeed"
assert_grep 'POST /repos/o/r/issues/9/comments' "$FAKE_GH_LOG" \
  "comment must post to the issue's comment endpoint"
assert_grep '6666' "$SELF_RECORD" "comment must record the id it wrote"
pass "comment writes an issue comment and records its id"

out=$(run_tracker "$HOME_D" comment o/r 9 2>&1)
rc=$?
expect_code_out 2 "$rc" "$out" "a comment with no body must be refused"
assert_contains "$out" "requires --body" "the refusal must name what is missing"
pass "comment refuses a call with no body"

# Seed the poll's position, then pin the inbox so the comment feed is the only
# source under test.
reset_gh
arm_watch "$HOME_D" own
http_response 1 '200 OK' 'X-Poll-Interval: 60
' '[]'
http_response 2 '200 OK' '' '[]'
run_notify "$HOME_D" --task own >/dev/null 2>&1
pin_inbox_quiet "$HOME_D" own

poll_comment() {  # <id> <created> [updated]
  rm -f "$FAKE_GH_DIR"/http.*
  printf '1\n' > "$FAKE_GH_DIR/http.seq"
  http_response 1 '200 OK' '' "$(comment_feed "$@")"
  run_notify "$HOME_D" --task own 2>&1
}

# Direction one: a comment the fleet wrote must not wake firstmate.
out=$(poll_comment 5555 '2026-08-25T12:00:00Z')
[ -z "$out" ] || fail "a comment the fleet wrote must not wake firstmate, got: $out"
pass "a comment the fleet wrote does not wake firstmate"

# Direction two, and the one that must not break: the SAME account, the same
# feed, the same shape - only the record differs. An unrecorded comment is the
# captain's and must wake firstmate.
out=$(poll_comment 7777 '2026-08-25T12:05:00Z')
assert_contains "$out" "o/r#9" "an unrecorded comment must wake firstmate"
pass "a comment the fleet did not write wakes firstmate from the same account"

# An edit is somebody saying something new, so a recorded id whose comment has
# been edited since it was written still wakes firstmate.
out=$(poll_comment 6666 '2026-08-25T12:06:00Z' '2026-08-25T12:07:00Z')
assert_contains "$out" "o/r#9" "an edited comment must wake firstmate"
pass "an edited comment wakes firstmate even though the fleet wrote it"

# The record is a suppression list, so an unreadable one must suppress nothing
# rather than swallow the captain.
mv "$SELF_RECORD" "$SELF_RECORD.aside"
printf 'not our file\n' > "$SELF_RECORD"
out=$(poll_comment 5555 '2026-08-25T12:08:00Z')
assert_contains "$out" "o/r#9" "an unrecognised record must suppress nothing"
mv -f "$SELF_RECORD.aside" "$SELF_RECORD"
pass "a record the poll cannot recognise suppresses nothing"

# Retention. The record is bounded by age and by count, and a later write is
# what applies both, so nothing has to be cleaned up on the read path.
reset_gh
printf '7000\n' > "$FAKE_GH_DIR/rest_repos_o_r_issues_9_comments"
printf '%s\t4242\n' "$(( $(date +%s) - 604801 ))" >> "$SELF_RECORD"
run_tracker "$HOME_D" comment o/r 9 --body 'later' >/dev/null 2>&1 \
  || fail "the retention fixture comment must succeed"
assert_no_grep '4242' "$SELF_RECORD" "an id past the age bound must be dropped"
assert_grep '7000' "$SELF_RECORD" "the id just written must be kept"
pass "a recorded id past the age bound is dropped by the next write"

now=$(date +%s)
{
  printf 'fm-tracker-self-comments-v1\n'
  i=0
  while [ "$i" -lt 1200 ]; do
    printf '%s\t9%06d\n' "$now" "$i"
    i=$((i + 1))
  done
} > "$SELF_RECORD"
reset_gh
printf '8000\n' > "$FAKE_GH_DIR/rest_repos_o_r_issues_9_comments"
run_tracker "$HOME_D" comment o/r 9 --body 'ceiling' >/dev/null 2>&1 \
  || fail "the ceiling fixture comment must succeed"
kept=$(sed 1d "$SELF_RECORD" | wc -l | tr -d ' ')
[ "$kept" -le 1000 ] || fail "the record must stay under its ceiling, kept $kept"
assert_grep '8000' "$SELF_RECORD" "the newest id must survive the ceiling"
assert_no_grep '9000000' "$SELF_RECORD" "the oldest entries must be the ones dropped"
pass "the record stays under its ceiling and drops its oldest entries first"

printf '\nall fm-tracker cases passed\n'
