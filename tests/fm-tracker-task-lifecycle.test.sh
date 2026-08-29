#!/usr/bin/env bash
# Behavior tests for the TASK half of firstmate's GitHub Issues project layer.
#
# The defect this half exists to close is not a crash: the destination, decision
# and unknown halves were adopted and the task half was not, so the board showed
# where a project was going and said nothing about what was moving. A frontier
# that answers "what is the fleet doing" with silence reads as an answer, which
# is worse than no frontier at all.
#
# So the load-bearing claims here are the two that make it a mechanism rather
# than a convention, and the one that keeps it from becoming a liability:
#
#   - a real bin/fm-spawn.sh dispatch files the ticket, on the one path a
#     dispatch cannot route around;
#   - a real bin/fm-teardown.sh cleanup closes it with its PR link (that half
#     lives in tests/fm-teardown.test.sh, next to the rest of cleanup behavior);
#   - and NONE of it can stop work. GitHub being unreachable, absent, or
#     unconfigured must cost a ticket and never a dispatch, because a dispatch
#     that dies because GitHub is down is a worse defect than the missing ticket.
#
# Every case drives the real command lines against a fake gh, so no case touches
# the network.
set -u

# shellcheck source=tests/lib.sh
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TRACKER="$ROOT/bin/fm-tracker.sh"
SPAWN="$ROOT/bin/fm-spawn.sh"
TMP_ROOT=$(fm_test_tmproot fm-tracker-task)

# --- fake gh ----------------------------------------------------------------
#
# Answers the REST and GraphQL calls this layer makes, and records each one as a
# directory of files: call.<n>.args for the flags, and call.<n>.<key> for every
# "key=value" argument. Splitting the values out by key is what makes an issue
# body assertable at all - a body is multi-line, so a flat log would smear it
# across the record of every other call and no case could then claim what was
# actually written.
setup_fake_gh() {  # <fakebin>
  cat > "$1/gh" <<'SH'
#!/usr/bin/env bash
set -u
[ "${1:-}" = api ] || exit 0
shift
n=$(cat "$FAKE_GH_DIR/seq" 2>/dev/null || printf '0\n')
n=$((n + 1))
printf '%s\n' "$n" > "$FAKE_GH_DIR/seq"
: > "$FAKE_GH_DIR/call.$n.args"
for a in "$@"; do
  printf '%s\n' "$a" >> "$FAKE_GH_DIR/call.$n.args"
  case $a in
    ?*=*) printf '%s' "${a#*=}" > "$FAKE_GH_DIR/call.$n.${a%%=*}" ;;
  esac
done

# The unreachable-GitHub case: every call fails the way a network error does.
[ "${FAKE_GH_FAIL:-0}" = 1 ] && exit 1

method=GET path= graphql=0 jq= mutation=0 prev=
for a in "$@"; do
  case $a in
    graphql) graphql=1 ;;
    *addSubIssue*) mutation=1 ;;
    /*) [ -n "$path" ] || path=$a ;;
  esac
  case $prev in
    -X) method=$a ;;
    --jq) jq=$a ;;
  esac
  prev=$a
done

if [ "$graphql" -eq 1 ]; then
  [ "$mutation" -eq 1 ] && { printf '{}\n'; exit 0; }
  [ -f "$FAKE_GH_DIR/graph" ] && cat "$FAKE_GH_DIR/graph"
  exit 0
fi

case $path in
  /user) printf '%s\n' "${FAKE_GH_LOGIN:-tester}"; exit 0 ;;
esac
case $path in
  */labels|*/assignees) exit 0 ;;
  */comments)
    c=$(cat "$FAKE_GH_DIR/cseq" 2>/dev/null || printf '9000\n')
    c=$((c + 1)); printf '%s\n' "$c" > "$FAKE_GH_DIR/cseq"
    printf '%s\n' "$c"; exit 0 ;;
esac
# Issue creation allocates the next number; every other issue call answers about
# the number already in its own path.
if [ "$method" = POST ] && [ "${path##*/}" = issues ]; then
  i=$(cat "$FAKE_GH_DIR/iseq" 2>/dev/null || printf '100\n')
  i=$((i + 1)); printf '%s\n' "$i" > "$FAKE_GH_DIR/iseq"
  printf '%s\n' "$i" > "$FAKE_GH_DIR/call.$n.issue"
  printf '%s\n' "$i"; exit 0
fi
num=${path##*/}
case $num in
  ''|*[!0-9]*) printf '0\n'; exit 0 ;;
esac
[ -f "$FAKE_GH_DIR/missing.$num" ] && exit 1
case $jq in
  .node_id) printf 'NODE_%s\n' "$num" ;;
  *) printf '%s\n' "$num" ;;
esac
exit 0
SH
  chmod +x "$1/gh"
}

# One graph record in the exact shape bin/fm-tracker-lib.sh's query emits:
# fields separated by US (0x1f), title and body base64.
graph_record() {  # <number> <state> <labels> <assignees> <parent> <blockers> <title> <body>
  local us=$'\037'
  printf '%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n' \
    "$1" "$us" "$2" "$us" "$3" "$us" "$4" "$us" "$5" "$us" "$6" "$us" \
    "$(printf '%s' "$7" | base64 | tr -d '\n')" "$us" \
    "$(printf '%s' "$8" | base64 | tr -d '\n')"
}

# A repository that has been initialised as a tracker: one open destination, and
# nothing else. Without a destination a task ticket would hang off nothing and
# report as orphaned, so this is the baseline every task case starts from.
seed_destination() {  # <gh-dir>
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body' > "$1/graph"
  printf '156\n' > "$1/iseq"
}

make_home() {  # <name>
  local home="$TMP_ROOT/$1" fakebin
  mkdir -p "$home/state" "$home/data" "$home/config" "$home/gh"
  fakebin=$(fm_fakebin "$home")
  setup_fake_gh "$fakebin"
  seed_destination "$home/gh"
  printf '%s\n' "$home"
}

run_tracker() {  # <home> <args...>
  local home=$1
  shift
  PATH="$home/fakebin:$PATH" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$home" \
    FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
    FM_CONFIG_OVERRIDE="$home/config" FAKE_GH_DIR="$home/gh" \
    FAKE_GH_FAIL="${FAKE_GH_FAIL:-0}" "$TRACKER" "$@"
}

# Every flag of every recorded api call.
gh_calls() {  # <home>
  cat "$1"/gh/call.*.args 2>/dev/null || true
}

# Every issue body this run wrote, as a grep target that keeps its line
# structure. Prints nothing when nothing was written, which is the claim a
# degraded-path case makes.
gh_bodies() {  # <home>
  local f
  for f in "$1"/gh/call.*.body; do
    [ -f "$f" ] || continue
    cat "$f"
    printf '\n'
  done
}

gh_titles() {  # <home>
  local f
  for f in "$1"/gh/call.*.title; do
    [ -f "$f" ] || continue
    cat "$f"
    printf '\n'
  done
}

# ===========================================================================
# Which repository a project's tickets live in
# ===========================================================================

HOME_A=$(make_home a)

printf '%s\n' '# a comment' 'video-editing-pilot,video_editing_pilot owner/video' \
  'other proj/other' > "$HOME_A/config/tracker-repos"
cat > "$HOME_A/data/backlog.md" <<'BACKLOG'
# Backlog

## Queued
- [ ] under-dash - A row filed under the dashed spelling (repo: video-editing-pilot) (kind: ship) (since 2026-08-26)
- [ ] under-underscore - A row filed under the underscored spelling (repo: video_editing_pilot) (kind: ship) (since 2026-08-26)
BACKLOG

# data/projects.md registers one project under more than one spelling, and a
# backlog row may use either. Matching one spelling only would leave half a
# project's work off the frontier while reporting the other half as the whole,
# so BOTH rows have to be mirrored whichever spelling the dispatch used.
for spelling in video_editing_pilot video-editing-pilot; do
  out=$(run_tracker "$HOME_A" sync --project "$spelling" --dry-run 2>&1)
  rc=$?
  expect_code_out 0 "$rc" "$out" "sync on $spelling must succeed"
  assert_contains "$out" "would file under-dash" "$spelling must reach the dashed row"
  assert_contains "$out" "would file under-underscore" "$spelling must reach the underscored row"
done
pass "a project's tracker repository and backlog rows resolve through its whole alias set"

# ===========================================================================
# A dispatch cannot be stopped by the tracker
# ===========================================================================
#
# Every one of these is a state the fleet is genuinely in - most projects have no
# tracker, gh is not always installed, and GitHub goes down - and in every one of
# them the work has to keep moving. `sync` therefore reports and exits 0.

rm -f "$HOME_A"/gh/call.* "$HOME_A"/gh/seq
out=$(run_tracker "$HOME_A" sync --project dotfiles 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "an unconfigured project must not fail the dispatch"
assert_contains "$out" "no tracker repository is configured for dotfiles" \
  "an unconfigured project must say which ticket was not filed"
[ -z "$(gh_calls "$HOME_A")" ] || fail "an unconfigured project must make no GitHub call"
pass "a project with no configured tracker reports and lets the dispatch proceed"

rm -f "$HOME_A"/gh/call.* "$HOME_A"/gh/seq
out=$(FAKE_GH_FAIL=1 run_tracker "$HOME_A" sync --project video_editing_pilot 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "an unreachable GitHub must not fail the dispatch"
assert_contains "$out" "could not read the issue graph" \
  "an unreachable GitHub must say which ticket was not filed"
[ -z "$(gh_bodies "$HOME_A")" ] || fail "an unreachable GitHub must write no issue body"
pass "an unreachable GitHub reports and lets the dispatch proceed"

# gh absent is different from gh failing: nothing is even attempted, and the
# silent-failure trap is that this reads exactly like a repository where nothing
# needed filing.
HOME_NOGH="$TMP_ROOT/nogh"
mkdir -p "$HOME_NOGH/state" "$HOME_NOGH/data" "$HOME_NOGH/config" "$HOME_NOGH/gh" "$HOME_NOGH/fakebin"
cp "$HOME_A/config/tracker-repos" "$HOME_NOGH/config/tracker-repos"
out=$(PATH="$HOME_NOGH/fakebin:/usr/bin:/bin" FM_ROOT_OVERRIDE="$ROOT" FM_HOME="$HOME_NOGH" \
  FM_STATE_OVERRIDE="$HOME_NOGH/state" FM_DATA_OVERRIDE="$HOME_NOGH/data" \
  FM_CONFIG_OVERRIDE="$HOME_NOGH/config" FAKE_GH_DIR="$HOME_NOGH/gh" \
  FM_TRACKER_GH=definitely-not-a-real-gh "$TRACKER" sync --project video_editing_pilot 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "an absent gh must not fail the dispatch"
assert_contains "$out" "is not on PATH" "an absent gh must say so rather than reading as nothing to file"
pass "an absent gh reports and lets the dispatch proceed"

# ===========================================================================
# Which backlog rows become tickets, and which deliberately do not
# ===========================================================================

HOME_B=$(make_home b)
printf '%s\n' 'proj proj/repo' > "$HOME_B/config/tracker-repos"
cat > "$HOME_B/data/backlog.md" <<'BACKLOG'
# Backlog

## In flight
- [ ] live-work - A worker is on this right now (repo: proj) (kind: ship) (since 2026-08-26)

## Queued
- [ ] queued-work - Nobody has started this (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] a-scout - Find out whether the thing is true (repo: proj) (kind: scout) (since 2026-08-26)
- [ ] downstream - Depends on the first one blocked-by: queued-work (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] captain-question - Which grain does the house look use? (repo: proj) (kind: captain) (since 2026-08-26) (hold: taste) (hold-kind: captain)
- [ ] parked-style - Caption size came from a web convention (repo: proj) (kind: ship) (since 2026-08-26) (hold: parked) (hold-kind: future)
- [ ] standing-watch - Not work, just an identity for a standing mechanism (repo: proj) (kind: ship) (since 2026-08-26) (hold: none) (hold-kind: external)
- [ ] someone-elses - Work on another project entirely (repo: other) (kind: ship) (since 2026-08-26)

## Done
- [x] finished-work - This already shipped (repo: proj) (kind: ship) (merged 2026-08-25)
BACKLOG

out=$(run_tracker "$HOME_B" sync --project proj --dry-run 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a dry-run sync must succeed"
for want in live-work queued-work a-scout downstream; do
  assert_contains "$out" "would file $want" "sync must mirror the $want row"
done
# Each of these skips is a decision, not a gap. A captain-held question is a
# decision and has its own type with its own answerable-cold contract; a parked
# row is parked FROM the captain as much as from the fleet, so putting it on a
# board they read is re-raising it; an external row is not work and never
# finishes; and a Done row is finished.
for skip in captain-question parked-style standing-watch someone-elses finished-work; do
  assert_not_contains "$out" "would file $skip" "sync must not mirror the $skip row"
done
pass "sync mirrors ship and scout work and deliberately skips decisions, parked and external rows"

# An in-flight row has a live worker behind it, so its ticket is claimed and
# leaves the frontier. A queued row is available work and has to stay on it, or
# the frontier reports an empty queue while work is waiting.
assert_contains "$out" "would file live-work [ship] claim=1" "in-flight work must be claimed"
assert_contains "$out" "would file queued-work [ship] claim=0" "queued work must stay unclaimed"
assert_contains "$out" "blocked-by=queued-work" "a dry run must name the dependency it would draw an edge to"
pass "the frontier's claimed/queued split comes from the row's own section"

# A dispatched task that has no row in firstmate's own task list gets no ticket,
# and that silence is the one worth breaking: a missing row costs far more than a
# missing ticket, and everything around it filing normally would hide it.
out=$(run_tracker "$HOME_B" sync --project proj --task never-recorded 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "an unrecorded task must not fail the dispatch"
assert_contains "$out" "no open ship or scout row names never-recorded" \
  "an unrecorded task must be named rather than passed over"
pass "a dispatched task with no task-list row is reported rather than silently unfiled"

# ===========================================================================
# What a filed ticket actually says
# ===========================================================================

HOME_C=$(make_home c)
printf '%s\n' 'proj proj/repo' > "$HOME_C/config/tracker-repos"
cp "$HOME_B/data/backlog.md" "$HOME_C/data/backlog.md"

out=$(run_tracker "$HOME_C" sync --project proj --task live-work 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a real sync must succeed"
assert_contains "$out" "4 reconciled" "sync must file every eligible row"

calls=$(gh_calls "$HOME_C")
assert_contains "$calls" "labels[]=fm:task" "a task ticket's type must be sent as a label"
titles=$(gh_titles "$HOME_C")
assert_contains "$titles" "A worker is on this right now" \
  "the task summary must be the ticket TITLE"

# Defect 2 in the task half: type is a label, so nothing may be encoded in the
# title for a reader - or a later parser - to pick back out.
case $titles in
  *fm:task*|*'[ship]'*|*'[task]'*) fail "the title must not carry the ticket's type" ;;
esac
pass "a filed ticket carries its type as a label and its summary as the title"

bodies=$(gh_bodies "$HOME_C")
assert_contains "$bodies" "<!-- fm-task: live-work -->" \
  "a task ticket must carry the binding back to its firstmate task"
# The summary goes in the title precisely so no caller prose reaches the body,
# where firstmate's own nouns would collide with the prose-blocker guard and a
# private brief would become public.
case $bodies in
  *"A worker is on this right now"*) fail "caller prose must not reach the ticket body" ;;
esac
pass "the ticket body carries the task binding and no caller prose"

# ===========================================================================
# Blocking edges are the form the frontier can query
# ===========================================================================
#
# This is defect 1 arriving through the task half. GitHub builds a queryable
# trackedIssues edge ONLY from a markdown task-list reference, so a prose
# "blocked by" is invisible to the frontier query and the blocked ticket reports
# READY while it is genuinely blocked. The backlog states dependencies in exactly
# that prose form, so the conversion is the whole job.

assert_contains "$bodies" "## Blocked by" "a dependency must use the canonical heading"
printf '%s\n' "$bodies" | grep -Eq '^- \[[ xX]\] #[1-9][0-9]*$' \
  || fail "a dependency must be written as a task-list reference"
# The prose the backlog states it in must not survive into the body.
case $bodies in
  *"blocked-by: queued-work"*|*"Blocked by: #"*|*"depends on"*) fail "a prose blocker must never reach a ticket body" ;;
esac
pass "a backlog dependency becomes a queryable task-list edge, never prose"

# The edge has to point at the blocker's OWN ticket, which means the blocker must
# be filed first. A reference to a number that does not exist yet is a task-list
# entry GitHub never resolves, so the blocked ticket queries as READY - the same
# defect arriving through ordering instead of wording.
blocked_body=$(grep -l 'fm-task: downstream' "$HOME_C"/gh/call.*.body | head -1)
[ -n "$blocked_body" ] || fail "the blocked row must have been filed"
edge=$(grep -oE '#[1-9][0-9]*' "$blocked_body" | head -1)
[ -n "$edge" ] || fail "the blocked ticket must name an edge"
blocker_body=$(grep -l 'fm-task: queued-work' "$HOME_C"/gh/call.*.body | head -1)
[ -n "$blocker_body" ] || fail "the blocker row must have been filed"
# Issue numbers are allocated in creation order by the fake, so the blocker's
# ticket existing before the edge that names it is what this asserts.
blocker_call=${blocker_body%.body}
blocked_call=${blocked_body%.body}
[ "${blocker_call##*call.}" -lt "${blocked_call##*call.}" ] \
  || fail "the blocker's ticket must be created before the ticket that references it"
pass "a dependency's ticket is created before the edge that references it"

# ===========================================================================
# Filing the same queue twice writes nothing the second time
# ===========================================================================
#
# sync runs on every dispatch, so a pass that re-wrote every ticket would churn
# the repository - and, worse, would make every re-file look like new activity to
# anyone watching the project.

reindex_home_from_calls() {  # <home>
  local f body num
  : > "$1/gh/graph"
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body' >> "$1/gh/graph"
  for f in "$1"/gh/call.*.issue; do
    [ -f "$f" ] || continue
    num=$(cat "$f")
    body=$(cat "${f%.issue}.body" 2>/dev/null) || continue
    graph_record "$num" OPEN 'fm:task' '' 156 '' 'a task' "$body" >> "$1/gh/graph"
  done
}
reindex_home_from_calls "$HOME_C"
rm -f "$HOME_C"/gh/call.* "$HOME_C"/gh/seq

out=$(run_tracker "$HOME_C" sync --project proj 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a repeat sync must succeed"
[ -z "$(gh_titles "$HOME_C")" ] || fail "a repeat sync must create no second ticket"
[ -z "$(gh_bodies "$HOME_C")" ] || fail "a repeat sync must rewrite no body"
pass "a sync that changes nothing writes nothing"

# ===========================================================================
# The frontier stays readable once more than one thing is claimed
# ===========================================================================
#
# Until the task half was filed, nothing was ever claimed, so the CLAIMED section
# had never rendered more than one row - and it ran every row onto the end of the
# previous one. The section that reports what the fleet is working on was
# unreadable exactly when the fleet was working on more than one thing.

HOME_CL=$(make_home claimed)
{
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body'
  graph_record 171 OPEN 'fm:task' 'tester' 156 '' 'the first claimed thing' 'body'
  graph_record 172 OPEN 'fm:task' 'tester' 156 '' 'the second claimed thing' 'body'
  graph_record 173 OPEN 'fm:task' 'tester' 156 '' 'the third claimed thing' 'body'
} > "$HOME_CL/gh/graph"
out=$(run_tracker "$HOME_CL" frontier proj/repo 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "frontier must render"
for n in 171 172 173; do
  printf '%s\n' "$out" | grep -Eq "^  #$n " \
    || fail "claimed ticket #$n must start its own line"
done
printf '%s\n' "$out" | grep -q 'claimed by tester  #' \
  && fail "claimed tickets must not run onto each other"
pass "the CLAIMED section renders one ticket per line when several are claimed"

# ===========================================================================
# Completion closes the ticket, and says what happened
# ===========================================================================

HOME_D=$(make_home d)
printf '%s\n' 'proj proj/repo' > "$HOME_D/config/tracker-repos"
{
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body'
  graph_record 157 OPEN 'fm:task' 'tester' 156 '' 'shipped work' \
    "$(printf '<!-- fm-task: shipped-work -->\n\nbody\n')"
  graph_record 158 OPEN 'fm:task' 'tester' 156 '' 'abandoned work' \
    "$(printf '<!-- fm-task: abandoned-work -->\n\nbody\n')"
} > "$HOME_D/gh/graph"

out=$(run_tracker "$HOME_D" complete --project proj --task shipped-work \
  --outcome shipped --pr https://github.com/proj/repo/pull/167 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "completing a shipped task must succeed"
assert_contains "$out" "#157 closed (shipped)" "completion must report the ticket it closed"
calls=$(gh_calls "$HOME_D")
assert_contains "$calls" "state=closed" "completion must close the ticket"
assert_contains "$calls" "state_reason=completed" "shipped work must close as completed"
comment=$(cat "$HOME_D"/gh/call.*.body 2>/dev/null)
assert_contains "$comment" "https://github.com/proj/repo/pull/167" \
  "the closing comment must carry the PR link"
pass "completion closes the task ticket and records its PR link"

# Every comment the fleet writes is recorded by id, and the poll skips exactly
# those ids. The fleet authenticates as the captain's own account, so a comment
# written outside that record wakes firstmate on firstmate's own writing.
assert_present "$HOME_D/state/.tracker-self-comments" \
  "a closing comment must be recorded as the fleet's own"
pass "the closing comment is recorded so the wake does not fire on it"

# A task cleaned up before it landed leaves a CLOSED record with its reason
# rather than an open ticket nobody is working. An open ticket with no worker
# behind it reports READY on the frontier forever, which reads as queued work.
rm -f "$HOME_D"/gh/call.* "$HOME_D"/gh/seq
out=$(run_tracker "$HOME_D" complete --project proj --task abandoned-work \
  --outcome not-shipped --detail 'No PR was recorded for this work.' 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "completing an unshipped task must succeed"
calls=$(gh_calls "$HOME_D")
assert_contains "$calls" "state_reason=not_planned" \
  "work cleaned up before it landed must close as not planned"
pass "a task cleaned up before it landed closes as not planned, not left open"

# Cleanup is a path that can be retried, so closing an already-closed ticket must
# not post a second outcome onto a finished record.
{
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body'
  graph_record 157 CLOSED 'fm:task' '' 156 '' 'shipped work' \
    "$(printf '<!-- fm-task: shipped-work -->\n\nbody\n')"
} > "$HOME_D/gh/graph"
rm -f "$HOME_D"/gh/call.* "$HOME_D"/gh/seq
out=$(run_tracker "$HOME_D" complete --project proj --task shipped-work --outcome shipped 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "re-completing must succeed"
assert_contains "$out" "already closed" "re-completing must report rather than rewrite"
assert_not_contains "$(gh_calls "$HOME_D")" "state=closed" \
  "re-completing must not write to a finished ticket"
pass "completing an already-closed ticket is a no-op it reports"

# Work that comes back has to come back to the SAME ticket. A second ticket for
# one task splits its history and puts a duplicate on the frontier.
rm -f "$HOME_D"/gh/call.* "$HOME_D"/gh/seq
out=$(run_tracker "$HOME_D" task-open proj/repo --task shipped-work --title 'shipped work' 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "re-dispatching closed work must succeed"
assert_contains "$out" "#157" "a re-dispatch must reuse the same ticket"
assert_contains "$(gh_calls "$HOME_D")" "state=open" "a re-dispatch must reopen the ticket"
[ -z "$(gh_titles "$HOME_D")" ] || fail "a re-dispatch must not create a second ticket"
pass "re-dispatching closed work reopens its ticket rather than filing a duplicate"

# ===========================================================================
# An orphan is never created to avoid an error message
# ===========================================================================
#
# `validate` reports a ticket with no parent as orphaned, so filing one to dodge
# a failure would trade a loud refusal for a quiet malformed record - exactly the
# trade this whole layer refuses to make.

HOME_E=$(make_home e)
printf '%s\n' 'proj proj/repo' > "$HOME_E/config/tracker-repos"
cp "$HOME_B/data/backlog.md" "$HOME_E/data/backlog.md"
: > "$HOME_E/gh/graph"
out=$(run_tracker "$HOME_E" sync --project proj 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a repository with no destination must not fail the dispatch"
assert_contains "$out" "no single open destination" "the missing destination must be named"
[ -z "$(gh_titles "$HOME_E")" ] || fail "no ticket may be filed without a destination to hang it off"
pass "a repository with no destination reports rather than filing orphans"

# ===========================================================================
# A ticket someone wrote by hand keeps what they wrote
# ===========================================================================
#
# This is not hypothetical: the first live run of this layer filed duplicates of
# task tickets that had already been written by hand, and those bodies carried
# the measurements, the captain's own words and the definition of done for that
# work. A convergent rewrite would have deleted all of it.

HOME_F=$(make_home f)
printf '%s\n' 'proj proj/repo' > "$HOME_F/config/tracker-repos"
HAND_BODY=$(cat <<'HAND'
**In flight.** Local task id `hand-filed`.

Measured 2026-08-26 on project 001: 40 empty directories under `pipeline_output/`.

## Definition of done

A measured render, not an inspected timeline.
HAND
)
{
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body'
  graph_record 157 OPEN 'fm:task' '' 156 '' 'a hand-written ticket' "$HAND_BODY"
} > "$HOME_F/gh/graph"
cat > "$HOME_F/data/backlog.md" <<'BACKLOG'
# Backlog

## In flight
- [ ] hand-filed - a hand-written ticket (repo: proj) (kind: ship) (since 2026-08-26)
BACKLOG

out=$(run_tracker "$HOME_F" task-open proj/repo --task hand-filed --adopt 157 \
  --title 'a hand-written ticket' --kind ship 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "adopting an existing ticket must succeed"
assert_contains "$out" "#157" "adoption must bind the named ticket"
[ -z "$(gh_titles "$HOME_F")" ] || fail "adoption must not create a second ticket"
adopted=$(cat "$HOME_F"/gh/call.*.body 2>/dev/null)
assert_contains "$adopted" "<!-- fm-task: hand-filed -->" \
  "adoption must add the binding the ticket was missing"
assert_contains "$adopted" "40 empty directories" \
  "adoption must preserve what the author wrote"
assert_contains "$adopted" "## Definition of done" \
  "adoption must preserve the author's own sections"
pass "adopting a hand-filed ticket binds it and preserves its body"

# Adoption is by number and never by title: matching a ticket to a task by title
# would be a guess, and a guess here attaches a task's whole future to a ticket
# about something else.
rm -f "$HOME_F"/gh/call.* "$HOME_F"/gh/seq
out=$(run_tracker "$HOME_F" task-open proj/repo --task hand-filed --adopt 999 \
  --title 'a hand-written ticket' 2>&1)
rc=$?
expect_code_out 1 "$rc" "$out" "adopting a ticket that does not exist must be refused"
[ -z "$(gh_titles "$HOME_F")" ] || fail "a refused adoption must create nothing"
pass "adopting a ticket that does not exist is refused rather than resolved into a new one"

# Once bound, an ordinary sync must converge the SAME ticket - and must still not
# touch the author's half of it, however many times it runs.
graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body' > "$HOME_F/gh/graph"
graph_record 157 OPEN 'fm:task' '' 156 '' 'a hand-written ticket' \
  "$(printf '<!-- fm-task: hand-filed -->\n\n%s\n' "$HAND_BODY")" >> "$HOME_F/gh/graph"
rm -f "$HOME_F"/gh/call.* "$HOME_F"/gh/seq
out=$(run_tracker "$HOME_F" sync --project proj 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a sync over an adopted ticket must succeed"
[ -z "$(gh_titles "$HOME_F")" ] || fail "a sync must not duplicate an adopted ticket"
[ -z "$(gh_bodies "$HOME_F")" ] || fail "a sync must not rewrite an adopted ticket's body"
pass "a sync over an adopted ticket neither duplicates it nor rewrites what the author wrote"

# ===========================================================================
# The dispatch itself files the ticket
# ===========================================================================
#
# This is the claim the whole change rests on. The task half was not missing
# because the tool could not file a ticket - `fm:task` existed the whole time -
# it was missing because filing one was something firstmate had to REMEMBER. So
# the test that matters is not that fm-tracker.sh can file a ticket, it is that
# an ordinary bin/fm-spawn.sh dispatch files one without being asked to.

make_spawn_case() {  # <name> <task-id>
  local name=$1 id=$2 case_dir home proj wt fakebin
  case_dir="$TMP_ROOT/$name"
  home="$case_dir/home"
  proj="$case_dir/proj"
  wt="$case_dir/wt"
  mkdir -p "$home/data/$id" "$home/state" "$home/config" "$home/projects" "$home/gh"
  fakebin=$(fm_fakebin "$case_dir/fake")
  setup_fake_gh "$fakebin"
  seed_destination "$home/gh"
  cat > "$fakebin/tmux" <<'SH'
#!/usr/bin/env bash
set -u
case "$*" in
  *"#{pane_current_path}"*) printf '%s\n' "${FM_FAKE_PANE_PATH:-}"; exit 0 ;;
esac
case "${1:-}" in
  display-message) printf 'firstmate\n'; exit 0 ;;
esac
exit 0
SH
  cat > "$fakebin/timeout" <<'SH'
#!/usr/bin/env bash
shift
exec "$@"
SH
  chmod +x "$fakebin/tmux" "$fakebin/timeout"
  fm_fake_treehouse "$fakebin"
  fm_git_worktree "$proj" "$wt" "wt-$name"
  printf 'claude\n' > "$home/config/crew-harness"
  printf 'brief for %s\n' "$id" > "$home/data/$id/brief.md"
  touch "$home/state/.last-watcher-beat"
  # The project's directory basename is what the dispatch resolves the tracker
  # repository from, so the mapping is written for exactly that name.
  printf '%s\n' "$(basename "$proj") proj/repo" > "$home/config/tracker-repos"
  cat > "$home/data/backlog.md" <<BACKLOG
# Backlog

## In flight
- [ ] $id - Deliver the planned audio mix to the timeline (repo: $(basename "$proj")) (kind: ship) (since 2026-08-26)
BACKLOG
  printf '%s|%s|%s|%s\n' "$home" "$proj" "$wt" "$fakebin"
}

run_spawn_case() {  # <home> <wt> <fakebin> <args...>
  local home=$1 wt=$2 fakebin=$3
  shift 3
  FM_ROOT_OVERRIDE='' FM_HOME="$home" \
    FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
    FM_PROJECTS_OVERRIDE="$home/projects" FM_CONFIG_OVERRIDE="$home/config" \
    FM_SPAWN_NO_GUARD=1 FM_FAKE_PANE_PATH="$wt" TMUX="fake,1,0" \
    CLAUDE_CONFIG_DIR='' FAKE_GH_DIR="$home/gh" \
    FAKE_GH_FAIL="${FAKE_GH_FAIL:-0}" PATH="$fakebin:$PATH" \
    "$SPAWN" "$@" 2>&1
}

IFS='|' read -r SP_HOME SP_PROJ SP_WT SP_BIN <<EOF
$(make_spawn_case dispatch vep-audio-mix)
EOF

out=$(run_spawn_case "$SP_HOME" "$SP_WT" "$SP_BIN" vep-audio-mix "$SP_PROJ" \
  --mode direct-PR --yolo off)
rc=$?
expect_code_out 0 "$rc" "$out" "an ordinary dispatch must succeed"
assert_contains "$out" "spawned vep-audio-mix" "the dispatch must report the spawn"
assert_contains "$out" "TRACKER: task ticket for vep-audio-mix" \
  "the dispatch must report the ticket it filed"
titles=$(gh_titles "$SP_HOME")
assert_contains "$titles" "Deliver the planned audio mix to the timeline" \
  "an ordinary dispatch must file the task's ticket without being asked"
bodies=$(gh_bodies "$SP_HOME")
assert_contains "$bodies" "<!-- fm-task: vep-audio-mix -->" \
  "the filed ticket must be bound to the dispatched task"
assert_contains "$(gh_calls "$SP_HOME")" "labels[]=fm:task" \
  "the filed ticket must be typed as task"
pass "an ordinary dispatch files its GitHub task ticket on the one path it cannot route around"

# The degraded case, proven through the real dispatch rather than through the
# tracker alone: a dispatch that dies because GitHub is down would be a worse
# defect than the missing ticket this exists to prevent.
IFS='|' read -r SP2_HOME SP2_PROJ SP2_WT SP2_BIN <<EOF
$(make_spawn_case dispatch-degraded vep-tests-real)
EOF

out=$(FAKE_GH_FAIL=1 run_spawn_case "$SP2_HOME" "$SP2_WT" "$SP2_BIN" vep-tests-real "$SP2_PROJ" \
  --mode direct-PR --yolo off)
rc=$?
expect_code_out 0 "$rc" "$out" "a dispatch must survive GitHub being unreachable"
assert_contains "$out" "spawned vep-tests-real" "the worker must still be launched"
assert_contains "$out" "could not read the issue graph" \
  "the dispatch must say which ticket it could not file"
assert_present "$SP2_HOME/state/vep-tests-real.meta" \
  "the task's durable record must still be published"
[ -z "$(gh_titles "$SP2_HOME")" ] || fail "an unreachable GitHub must file nothing"
pass "a dispatch survives an unreachable GitHub and reports the ticket it could not file"

# A secondmate is a persistent direct report rather than a work item and never
# appears in a task list, so it must file nothing at all.
IFS='|' read -r SP3_HOME _SP3_PROJ SP3_WT SP3_BIN <<EOF
$(make_spawn_case dispatch-secondmate mate-one)
EOF
SP3_MATE="$TMP_ROOT/mate-one-home"
mkdir -p "$SP3_MATE/bin" "$SP3_MATE/data"
printf '# Firstmate\n' > "$SP3_MATE/AGENTS.md"
printf 'mate-one\n' > "$SP3_MATE/.fm-secondmate-home"
printf 'charter\n' > "$SP3_MATE/data/charter.md"
out=$(run_spawn_case "$SP3_HOME" "$SP3_WT" "$SP3_BIN" mate-one "$SP3_MATE" --secondmate)
rc=$?
expect_code_out 0 "$rc" "$out" "a secondmate spawn must succeed"
[ -z "$(gh_titles "$SP3_HOME")" ] || fail "a secondmate spawn must file no task ticket"
pass "a secondmate spawn files no task ticket, because it is not a work item"

# ===========================================================================
# Every declared dependency becomes an edge, not just the first
# ===========================================================================
#
# A row may declare more than one blocker. Lifting only the first leaves the
# second in the summary, and the summary becomes the ticket TITLE - so an
# internal task id reaches a title other people read, and the edge it named is
# lost, which is defect 1 arriving through the parser.

HOME_G=$(make_home g)
printf '%s\n' 'proj proj/repo' > "$HOME_G/config/tracker-repos"
cat > "$HOME_G/data/backlog.md" <<'BACKLOG'
# Backlog

## Queued
- [ ] parser-half - Land the parser (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] renderer-half - Land the renderer (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] two-blockers - Wire the two halves together blocked-by: parser-half blocked-by: renderer-half (repo: proj) (kind: ship) (since 2026-08-26)
BACKLOG

out=$(run_tracker "$HOME_G" sync --project proj --dry-run 2>&1)
rc=$?
expect_code_out 0 "$rc" "$out" "a dry-run sync must succeed"
assert_contains "$out" "blocked-by=parser-half,renderer-half" \
  "every declared dependency must be lifted, not only the first"
pass "a row declaring two dependencies reports both"

out=$(run_tracker "$HOME_G" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "a real sync must succeed"
titles=$(gh_titles "$HOME_G")
case $titles in
  *blocked-by:*) fail "an internal task id must never survive into a ticket title" ;;
esac
assert_contains "$titles" "Wire the two halves together" \
  "the summary must reach the title with the annotations removed"
body=$(grep -l 'fm-task: two-blockers' "$HOME_G"/gh/call.*.body | head -1)
[ -n "$body" ] || fail "the dependent row must have been filed"
edges=$(grep -cE '^- \[[ xX]\] #[1-9][0-9]*$' "$body")
[ "$edges" -eq 2 ] || fail "both dependencies must become task-list edges, got $edges"
pass "both declared dependencies become queryable edges and neither reaches the title"

# ===========================================================================
# A ticket nobody can pick up does not report itself as available
# ===========================================================================
#
# The frontier had three discriminators - the type label, the assignee and the
# tracked-issue edge - and a hold is none of them, so a held ticket fell through
# to READY and contradicted its own body. The fourth discriminator is a label,
# because a label is the tracker's own vocabulary and survives the next sync.

HOME_H=$(make_home h)
printf '%s\n' 'proj proj/repo' > "$HOME_H/config/tracker-repos"
{
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body'
  graph_record 157 OPEN 'fm:task' '' '156' '' 'available work' ''
  graph_record 158 OPEN 'fm:task fm:state:held' '' '156' '' 'held work' ''
} > "$HOME_H/gh/graph"

out=$(run_tracker "$HOME_H" frontier proj/repo 2>&1)
expect_code_out 0 "$?" "$out" "frontier must succeed"
ready=$(printf '%s\n' "$out" | sed -n '/^READY/,/^$/p')
held=$(printf '%s\n' "$out" | sed -n '/^HELD/,/^$/p')
assert_contains "$ready" "#157" "an available ticket must stay ready"
assert_not_contains "$ready" "#158" "a held ticket must not report itself as ready"
assert_contains "$held" "#158" "a held ticket must be reported as held"
assert_contains "$out" "[task" "the hold label must not be read as the ticket's type"
assert_not_contains "$out" "missing type" "a held ticket still carries its type"
pass "a held ticket leaves READY and is reported as held"

# A hold label must not read as a second type label, or validate would report
# every held ticket as ambiguous and the guard would become noise.
out=$(run_tracker "$HOME_H" validate proj/repo 2>&1)
expect_code_out 0 "$?" "$out" "validate must pass on a held but well-formed ticket"
assert_contains "$out" "no malformed tickets" "a held ticket is not malformed"
pass "a hold label is not counted as a type"

# The hold is raised by the queue itself: a row the task list holds must not
# reach the board advertising as available work.
HOME_I=$(make_home i)
printf '%s\n' 'proj proj/repo' > "$HOME_I/config/tracker-repos"
cat > "$HOME_I/data/backlog.md" <<'BACKLOG'
# Backlog

## Queued
- [ ] open-work - Ordinary available work (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] held-work - Waiting on a ruling (repo: proj) (kind: ship) (since 2026-08-26) (hold: taste) (hold-kind: captain)
BACKLOG

out=$(run_tracker "$HOME_I" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "a sync over a held row must succeed"
held_num=$(grep -l 'fm-task: held-work' "$HOME_I"/gh/call.*.body | head -1)
[ -n "$held_num" ] || fail "the held row must still be filed"
held_num=${held_num%.body}
assert_present "$held_num.labels[]" "the filed ticket must carry labels"
assert_contains "$(cat "$held_num.labels[]")" "fm:state:held" \
  "a held row's ticket must carry the hold label"
open_call=$(grep -l 'fm-task: open-work' "$HOME_I"/gh/call.*.body | head -1)
open_call=${open_call%.body}
assert_not_contains "$(cat "$open_call.labels[]" 2>/dev/null || true)" "fm:state:held" \
  "an available row's ticket must not be held"
pass "sync raises the hold label from the task list's own hold"

# ===========================================================================
# A withhold is a durable decision, not a one-time act
# ===========================================================================
#
# Every dispatch runs sync, so a row kept off a board by hand is published by the
# next dispatch. The decision has to be recorded where sync reads it, with the
# reason it was taken.

HOME_J=$(make_home j)
printf '%s\n' 'proj proj/repo' > "$HOME_J/config/tracker-repos"
cat > "$HOME_J/data/backlog.md" <<'BACKLOG'
# Backlog

## Queued
- [ ] publishable - Ordinary engineering work (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] private-work - Package the findings for the qualification plan (repo: proj) (kind: ship) (since 2026-08-26)
BACKLOG

out=$(run_tracker "$HOME_J" withhold --project proj --task private-work \
  --reason 'names go-to-market targeting rather than engineering work' 2>&1)
expect_code_out 0 "$?" "$out" "recording a withhold must succeed"
assert_present "$HOME_J/config/tracker-withhold" "the decision must be recorded durably"
assert_contains "$(cat "$HOME_J/config/tracker-withhold")" "go-to-market targeting" \
  "the reason must be recorded with the decision"

out=$(run_tracker "$HOME_J" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "a sync over a withheld row must succeed"
assert_contains "$out" "withheld from proj/repo: private-work" \
  "sync must say what it did not publish"
titles=$(gh_titles "$HOME_J")
assert_contains "$titles" "Ordinary engineering work" "publishable work must still be filed"
case $titles in
  *"qualification plan"*) fail "a withheld row must not reach the board" ;;
esac
pass "a withheld row is not published and the sync says so"

# The whole point: the NEXT dispatch runs sync again and must reach the same
# decision without anyone re-taking it.
rm -f "$HOME_J"/gh/call.*
out=$(run_tracker "$HOME_J" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "a second sync must succeed"
case $(gh_titles "$HOME_J") in
  *"qualification plan"*) fail "a later sync must not publish a withheld row" ;;
esac
pass "a withhold survives the next dispatch's sync"

# A withhold decided after the row was already filed cannot unpublish it. Saying
# nothing would let the skip read as if it had.
HOME_L=$(make_home l)
printf '%s\n' 'proj proj/repo' > "$HOME_L/config/tracker-repos"
cat > "$HOME_L/data/backlog.md" <<'BACKLOG'
# Backlog

## Queued
- [ ] late-withhold - Package the findings (repo: proj) (kind: ship) (since 2026-08-26)
BACKLOG
out=$(run_tracker "$HOME_L" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "the first sync must file the row"
filed=$(grep -l 'fm-task: late-withhold' "$HOME_L"/gh/call.*.body | head -1)
[ -n "$filed" ] || fail "the row must have been filed before it was withheld"
num=$(cat "${filed%.body}.issue")
{
  graph_record 156 OPEN 'fm:destination' '' '-' '' 'The destination' 'body'
  graph_record "$num" OPEN 'fm:task' '' '156' '' 'Package the findings' \
    "$(printf '<!-- fm-task: late-withhold -->\n\nA change.\n')"
} > "$HOME_L/gh/graph"
out=$(run_tracker "$HOME_L" withhold --project proj --task late-withhold \
  --reason 'names commercial planning' 2>&1)
expect_code_out 0 "$?" "$out" "a late withhold must be recordable"
out=$(run_tracker "$HOME_L" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "a sync after a late withhold must succeed"
assert_contains "$out" "already has ticket #$num" \
  "a withhold cannot unpublish, and must say which ticket is already out"
pass "a withhold taken after filing names the ticket already on the board"

out=$(run_tracker "$HOME_J" withheld --project proj 2>&1)
expect_code_out 0 "$?" "$out" "listing withholds must succeed"
assert_contains "$out" "private-work" "the record must be readable back"

out=$(run_tracker "$HOME_J" unwithhold --project proj --task private-work 2>&1)
expect_code_out 0 "$?" "$out" "clearing a withhold must succeed"
rm -f "$HOME_J"/gh/call.*
out=$(run_tracker "$HOME_J" sync --project proj 2>&1)
assert_contains "$(gh_titles "$HOME_J")" "qualification plan" \
  "a cleared withhold must let the row be filed"
pass "a withhold can be cleared, and only then is the row published"

# ===========================================================================
# What a client-visible ticket says
# ===========================================================================
#
# These tickets are read by people outside the fleet. A body that explains itself
# in the fleet's own nouns is noise on their board at best.

HOME_K=$(make_home k)
printf '%s\n' 'proj proj/repo' > "$HOME_K/config/tracker-repos"
cat > "$HOME_K/data/backlog.md" <<'BACKLOG'
# Backlog

## Queued
- [ ] plain-work - Correct the sensitivity report (repo: proj) (kind: ship) (since 2026-08-26)
- [ ] plain-held - Wait for the ruling (repo: proj) (kind: ship) (since 2026-08-26) (hold: taste) (hold-kind: captain)
BACKLOG

out=$(run_tracker "$HOME_K" sync --project proj 2>&1)
expect_code_out 0 "$?" "$out" "a sync must succeed"
bodies=$(gh_bodies "$HOME_K")
[ -n "$bodies" ] || fail "the sync must have written bodies"
for word in fleet frontier captain horizon crewmate Firstmate firstmate; do
  case $bodies in
    *"$word"*) fail "a client-visible body must not carry the word '$word'" ;;
  esac
done
pass "a filed ticket body carries no fleet-internal vocabulary"

echo "all fm-tracker-task-lifecycle tests passed"
