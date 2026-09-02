#!/usr/bin/env bash
# fm-tracker.sh - the single owner of firstmate's GitHub Issues project layer.
#
# GitHub Issues carries firstmate's project state: the destination, the
# decisions, the known unknowns, and the tasks, with real blocking edges between
# them. tasks-axi remains the execution layer and is untouched by this script.
#
# The TASK half is filed mechanically rather than by hand. `sync` runs on the one
# path every dispatch takes (bin/fm-spawn.sh) and `complete` on the one path every
# cleanup takes (bin/fm-teardown.sh), so a ticket is never something firstmate has
# to remember. That is the point: a half-adopted convention is the failure mode
# this whole script exists to make impossible, and a backfill that then has to be
# maintained by hand IS a half-adopted convention. Both entrypoints resolve the
# project's tracker repository from config/tracker-repos and NEVER fail - an
# unconfigured project, an absent gh, an unreachable GitHub and a hung connection
# all report the ticket they could not file and let the work proceed, because a
# dispatch that dies because GitHub is down is a worse defect than a missing
# ticket.
#
# This script exists because a half-adopted convention produces confident wrong
# answers rather than obvious failures. The defects observed in a hand-rolled
# run of this structure, and in this layer's own first live use, are made
# structurally impossible here:
#
#   1. Blocking edges written as prose. GitHub builds a queryable trackedIssues
#      edge only from a markdown task-list reference, so a prose
#      "Blocked by: #144" is invisible to the frontier query and the blocked
#      ticket reports READY. Every write path emits the task-list form under one
#      fixed heading, and a body carrying a prose blocker is REFUSED.
#   2. Ticket type parsed out of the title string, so a reworded title silently
#      reclassified a ticket. Type is a label (fm:destination, fm:decision,
#      fm:unknown, fm:task) and is never read from, or written into, a title.
#   3. Every assignment read as a claim. An agent's assignment is a claim and
#      leaves the frontier, but the captain's assignment means the opposite -
#      nothing moves until they answer - so it stays on the frontier as BLOCKED,
#      naming them. The discriminator is the assignee login, configured in
#      config/captain-github (or FM_CAPTAIN_GITHUB); with none configured every
#      assignment reads as a claim. See docs/configuration.md.
#
#   3b. A ticket nobody may pick up advertising itself as READY. The frontier
#      had only three discriminators - the type label, the assignee and the
#      tracked-issue edge - and a hold is none of them, so a held ticket fell
#      through to READY and contradicted its own body. The fourth discriminator
#      is the fm:state:held label, in its own namespace so it is never read as a
#      second type. It is a LABEL because `sync` runs on every dispatch and
#      rewrites the body: anything not in the tracker's own vocabulary is
#      overwritten by the next dispatch. `sync` raises the hold from the queue's
#      own hold and NEVER lowers it - lowering one is `unhold`, which is somebody
#      deciding it - so a hold taken in review survives the next dispatch.
#   4. A decision that could not be answered without going and finding the
#      background. A decision reaches the captain as a notification and nothing
#      else, so `add --type decision` REFUSES a body without context, two
#      options that each state their consequence, and a recommendation, and
#      always writes the invitation to answer with something none of them say.
#   5. The wake firing on the fleet's own writing. Every comment the fleet
#      writes is recorded by id as it is written, and the poll skips exactly
#      those ids. The author cannot serve as the discriminator - the fleet
#      authenticates as the captain's own account - so THIS SCRIPT IS THE ONLY
#      WAY THE FLEET MAY COMMENT: a bare `gh api ... /comments` call leaves no
#      record and wakes firstmate on what firstmate just wrote. Use
#      `fm-tracker.sh comment`.
#
#   5b. A row that must not be published being published anyway. `sync` runs on
#      every dispatch, so a row kept off a board by hand is filed by the next
#      one and the decision survives only as prose in whatever review took it.
#      A withhold is recorded in config/tracker-withhold with its reason, is
#      consulted before a title is ever composed, and therefore holds across
#      every later dispatch. See `withhold` below and docs/configuration.md.
#
#   6. The structure filed for the horizon and not for the work. The destination,
#      decision and unknown halves were adopted and the task half was not, so the
#      board showed where a project was going and nothing about what was moving:
#      one destination and five open questions over an evening in which two
#      workers were live and five PRs merged. A frontier that answers "what is
#      the fleet doing" with silence is worse than no frontier, because the
#      silence reads as an answer. The task half is therefore filed by the
#      dispatch and the cleanup themselves, never by a convention.
#
# Because this script is the only writer of that structure, `validate` is a real
# guard rather than a linter over free text.
#
# GitHub Projects is deliberately out of scope: this builds on Issues only.
#
# Requires gh, authenticated. gh-axi cannot serve this script: it exposes
# neither the GraphQL endpoint the issue graph needs nor response headers.
#
# Usage:
#   fm-tracker.sh init <owner/repo> --destination <title> [--body-file <path>]
#   fm-tracker.sh add <owner/repo> --type <destination|decision|unknown|task>
#                     --title <title> [--body <text>|--body-file <path>]
#                     [--parent <n>] [--blocked-by <n[,n...]>]
#     --type decision requires a body carrying '## Context', '## Options' with at
#     least two '- <option> - <consequence>' lines, and '## Recommendation'.
#     '## Or something else' is appended by this script and must not be authored.
#   fm-tracker.sh frontier <owner/repo>
#     READY, BLOCKED, HELD and CLAIMED. HELD ranks above BLOCKED because a hold
#     does not clear when a blocker closes, and below CLAIMED because somebody
#     already working a ticket settles the question.
#   fm-tracker.sh claim <owner/repo> <n>
#   fm-tracker.sh release <owner/repo> <n>
#   fm-tracker.sh hold <owner/repo> <n>
#   fm-tracker.sh unhold <owner/repo> <n>
#     Take one ticket out of the ready set, or put it back. The carrier is the
#     fm:state:held label, so the state survives every later sync.
#   fm-tracker.sh withhold --project <name> --task <task-id> --reason <text>
#   fm-tracker.sh unwithhold --project <name> --task <task-id>
#   fm-tracker.sh withheld [--project <name>]
#     Record, clear and list the rows `sync` must never publish. --reason is
#     required: a withhold nobody can explain later is a withhold nobody can
#     lift. The record is consulted before a title is composed, so a withheld
#     row's summary never reaches GitHub at all. A withhold cannot unpublish a
#     ticket already filed; `sync` names that ticket rather than implying it did.
#   fm-tracker.sh answer <owner/repo> <n> --decision <text>
#                     [--settles <text>] [--does-not-settle <text>]
#   fm-tracker.sh comment <owner/repo> <n> --body <text>|--body-file <path>
#     The fleet's only issue-comment write path, so the wake can tell a comment
#     firstmate wrote from one the captain wrote on the same account.
#   fm-tracker.sh validate <owner/repo>
#   fm-tracker.sh task-open <owner/repo> --task <task-id> --title <title>
#                     [--kind <ship|scout>] [--project <name>] [--hold <text>]
#                     [--blocked-by <n[,n...]>] [--adopt <n>] [--no-claim]
#     Create, reopen, re-attach, re-body and claim ONE fm:task ticket until it
#     says what that task is, then print its number. Idempotent: it writes only
#     where the ticket and the intent disagree. Nothing the caller supplies is
#     composed into the body - the task summary is the TITLE - so a summary
#     worded in firstmate's own nouns can never be refused as a prose blocker and
#     no private brief text reaches a public issue. In a body it FINDS, this owns
#     only the marker line and the blocked-by section and preserves the rest
#     verbatim, so a ticket someone wrote by hand keeps what they wrote.
#     --adopt <n> binds an existing ticket to this task instead of filing a new
#     one, for a ticket written before this layer reached the dispatch. It is by
#     number and never by title, because matching a ticket to a task by title
#     would be a guess.
#   fm-tracker.sh task-close <owner/repo> --task <task-id>
#                     --outcome <shipped|not-shipped> [--pr <url>] [--detail <text>]
#     Record the outcome and close. `shipped` closes as completed; `not-shipped`
#     closes as not planned, so a task cleaned up before it landed leaves a
#     closed record with its reason rather than a ticket that reports READY
#     forever with nobody working it. A re-dispatch reopens the same ticket.
#   fm-tracker.sh sync --project <name> [--task <task-id>] [--dry-run] [--limit <n>]
#     Reconcile that project's whole open queue into task tickets, in dependency
#     order, and claim the in-flight rows. Skips every row recorded in
#     config/tracker-withhold, and marks a held row's ticket fm:state:held. Reads data/backlog.md directly, so it
#     works under either backlog backend and a broken tasks-axi cannot stop a
#     dispatch. Skips kind:captain rows (a decision is not a task and has its own
#     type), hold-kind:future rows (parked rows are parked from the captain too)
#     and hold-kind:external rows (not work). Always exits 0.
#   fm-tracker.sh complete --project <name> --task <task-id>
#                     --outcome <shipped|not-shipped> [--pr <url>] [--detail <text>]
#     task-close with the repository resolved from config. Always exits 0.
#   fm-tracker.sh watch --task <task-id> <owner/repo> [more owner/repo...]
#   fm-tracker.sh unwatch --task <task-id>
#   fm-tracker.sh --help
#
# Exit status: 0 on success, 1 on a refused or failed operation, 2 on invalid
# usage. `validate` exits 1 when it reports any malformed ticket.
set -u
LC_ALL=C
export LC_ALL

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE-$FM_HOME/state}"
CONFIG="${FM_CONFIG_OVERRIDE:-$FM_HOME/config}"
DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"

# shellcheck source=bin/fm-pr-lib.sh
. "$SCRIPT_DIR/fm-pr-lib.sh"
# shellcheck source=bin/fm-tracker-lib.sh
. "$SCRIPT_DIR/fm-tracker-lib.sh"

GH=${FM_TRACKER_GH:-gh}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage_die() { printf 'error: %s\n' "$*" >&2; printf 'run: fm-tracker.sh --help\n' >&2; exit 2; }

print_help() {
  sed -n '3,/^set -u$/p' "$SCRIPT_DIR/fm-tracker.sh" | sed 's/^#\{1\} \{0,1\}//; $d'
}

require_gh() {
  command -v "$GH" >/dev/null 2>&1 || die "$GH is not on PATH"
}

parse_repo() {  # <owner/repo>
  local spec=${1-}
  fm_tracker_repo_parse "$spec" || usage_die "invalid repository '$spec' (expected owner/repo)"
  OWNER=$FM_TRACKER_OWNER
  REPO=$FM_TRACKER_REPO
}

gh_api() { "$GH" api "$@"; }

captain_login() { fm_tracker_captain_login "$CONFIG"; }

# ---------------------------------------------------------------------------
# Body assembly and the blocked-by contract
# ---------------------------------------------------------------------------

# The caller's prose first, then the one fixed blocked-by section. The task-list
# form is what GitHub turns into a real trackedIssues edge, and GitHub ticks the
# checkbox as each blocker closes, so the graph and the rendered issue cannot
# disagree.
compose_body() {  # <body> <blocker-csv>
  local body=$1 blockers=$2 n
  printf '%s' "$body"
  [ -z "$body" ] || printf '\n'
  [ -n "$blockers" ] || return 0
  printf '\n%s\n' "$FM_TRACKER_BLOCKED_HEADING"
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    printf -- '- [ ] #%s\n' "$n"
  done <<EOF
$(printf '%s' "$blockers" | tr ',' '\n')
EOF
}

# Every blocker must be a real issue in this repository. A typo'd number would
# leave a task-list entry GitHub never resolves into an edge - defect 1 arriving
# through a different door: a ticket that looks blocked and queries as ready.
verify_blockers_exist() {  # <csv>
  local csv=$1 n
  [ -n "$csv" ] || return 0
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    gh_api "/repos/$OWNER/$REPO/issues/$n" --jq .number >/dev/null 2>&1 \
      || die "blocker #$n does not exist in $OWNER/$REPO"
  done <<EOF
$(printf '%s' "$csv" | tr ',' '\n')
EOF
}

ensure_labels() {  # [project-name]
  local t name desc color proj=${1-}
  for t in $FM_TRACKER_TYPES; do
    name=$(fm_tracker_type_label "$t")
    case "$t" in
      destination) desc='the destination this project steers toward'; color=0e8a16 ;;
      decision) desc='a decision for the project owner'; color=b60205 ;;
      unknown) desc='a known unknown to be resolved'; color=fbca04 ;;
      *) desc='executable work'; color=1d76db ;;
    esac
    gh_api -X POST "/repos/$OWNER/$REPO/labels" \
      -f "name=$name" -f "description=$desc" -f "color=$color" >/dev/null 2>&1 || true
  done
  gh_api -X POST "/repos/$OWNER/$REPO/labels" \
    -f "name=$FM_TRACKER_HELD_LABEL" \
    -f 'description=not available to pick up yet' -f 'color=6a737d' >/dev/null 2>&1 || true
  if [ -n "$proj" ]; then
    name=$(fm_tracker_project_label "$proj")
    gh_api -X POST "/repos/$OWNER/$REPO/labels" \
      -f "name=$name" -f "description=work item in $proj" -f 'color=5319e7' >/dev/null 2>&1 || true
  fi
}

create_issue() {  # <title> <body> <type> [extra-labels...]
  local title=$1 body=$2 type=$3 label num
  shift 3
  label=$(fm_tracker_type_label "$type")
  local -a args=(-X POST "/repos/$OWNER/$REPO/issues"
    -f "title=$title" -f "body=$body" -f "labels[]=$label")
  local extra
  for extra in "$@"; do
    [ -n "$extra" ] || continue
    args+=(-f "labels[]=$extra")
  done
  args+=(--jq .number)
  num=$(gh_api "${args[@]}") \
    || die "could not create the issue in $OWNER/$REPO"
  fm_tracker_issue_number_valid "$num" || die "GitHub returned no usable issue number"
  printf '%s\n' "$num"
}

# Raise or lower the hold on an existing ticket. Separate calls rather than a
# label rewrite, so a label somebody else put on the ticket is left alone.
add_held_label() {  # <number>
  gh_api -X POST "/repos/$OWNER/$REPO/issues/$1/labels" \
    -f "labels[]=$FM_TRACKER_HELD_LABEL" >/dev/null 2>&1
}

remove_held_label() {  # <number>
  gh_api -X DELETE "/repos/$OWNER/$REPO/issues/$1/labels/$FM_TRACKER_HELD_LABEL" \
    >/dev/null 2>&1
}

node_id_for() {  # <number>
  gh_api "/repos/$OWNER/$REPO/issues/$1" --jq .node_id 2>/dev/null
}

# Native sub-issue attachment, so hierarchy and completion rollup come from
# GitHub rather than from a convention this script would have to re-derive.
attach_parent() {  # <child> <parent>
  local child=$1 parent=$2 child_id parent_id
  child_id=$(node_id_for "$child") || true
  parent_id=$(node_id_for "$parent") || true
  [ -n "$child_id" ] && [ -n "$parent_id" ] \
    || die "could not resolve #$child or #$parent for attachment"
  gh_api graphql -f "query=$FM_TRACKER_ADD_SUB_ISSUE" \
    -F "parentId=$parent_id" -F "subIssueId=$child_id" >/dev/null \
    || die "could not attach #$child under #$parent"
}

# ONE GraphQL query answers the whole frontier: ready, blocked, and what each
# blocked ticket waits on. trackedIssues is GitHub's own resolution of the
# task-list edges, so no body is ever re-parsed to find a blocker.
graph_json() {
  gh_api graphql --paginate -f "query=$FM_TRACKER_GRAPH_QUERY" \
    -F "owner=$OWNER" -F "repo=$REPO" --jq "$FM_TRACKER_GRAPH_JQ"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_init() {
  local dest='' body_file='' body num
  parse_repo "${1-}"; shift
  while [ "$#" -gt 0 ]; do
    case $1 in
      --destination) [ "$#" -gt 1 ] || usage_die "--destination requires a title"; dest=$2; shift 2 ;;
      --destination=*) dest=${1#--destination=}; shift ;;
      --body-file) [ "$#" -gt 1 ] || usage_die "--body-file requires a path"; body_file=$2; shift 2 ;;
      --body-file=*) body_file=${1#--body-file=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$dest" ] || usage_die "init requires --destination <title>"
  if [ -n "$body_file" ]; then
    [ -f "$body_file" ] || die "no such body file: $body_file"
    body=$(cat "$body_file")
  else
    body=$(fm_tracker_destination_template)
  fi
  fm_tracker_body_prose_blocker_refuse "$body" || exit 1
  require_gh
  ensure_labels
  num=$(create_issue "$dest" "$body" destination) || exit 1
  printf 'created destination #%s in %s/%s\n' "$num" "$OWNER" "$REPO"
  printf 'labels ensured: %s\n' "$(fm_tracker_all_labels)"
}

cmd_add() {
  local type='' title='' body='' body_file='' parent='' blockers='' num
  parse_repo "${1-}"; shift
  while [ "$#" -gt 0 ]; do
    case $1 in
      --type) [ "$#" -gt 1 ] || usage_die "--type requires a value"; type=$2; shift 2 ;;
      --type=*) type=${1#--type=}; shift ;;
      --title) [ "$#" -gt 1 ] || usage_die "--title requires a value"; title=$2; shift 2 ;;
      --title=*) title=${1#--title=}; shift ;;
      --body) [ "$#" -gt 1 ] || usage_die "--body requires a value"; body=$2; shift 2 ;;
      --body=*) body=${1#--body=}; shift ;;
      --body-file) [ "$#" -gt 1 ] || usage_die "--body-file requires a path"; body_file=$2; shift 2 ;;
      --body-file=*) body_file=${1#--body-file=}; shift ;;
      --parent) [ "$#" -gt 1 ] || usage_die "--parent requires an issue number"; parent=$2; shift 2 ;;
      --parent=*) parent=${1#--parent=}; shift ;;
      --blocked-by) [ "$#" -gt 1 ] || usage_die "--blocked-by requires issue numbers"; blockers=$2; shift 2 ;;
      --blocked-by=*) blockers=${1#--blocked-by=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$type" ] || usage_die "add requires --type <${FM_TRACKER_TYPES// /|}>"
  fm_tracker_type_valid "$type" \
    || usage_die "invalid --type '$type' (expected one of: ${FM_TRACKER_TYPES// /, })"
  [ -n "$title" ] || usage_die "add requires --title <title>"
  if [ -n "$body_file" ]; then
    [ -z "$body" ] || usage_die "pass --body or --body-file, not both"
    [ -f "$body_file" ] || die "no such body file: $body_file"
    body=$(cat "$body_file")
  fi
  if [ -n "$parent" ]; then
    fm_tracker_issue_number_valid "$parent" \
      || usage_die "invalid --parent '$parent' (expected a positive issue number)"
  fi
  if [ -n "$blockers" ]; then
    fm_tracker_blocker_csv_valid "$blockers" \
      || usage_die "invalid --blocked-by '$blockers' (expected comma-separated issue numbers)"
  fi

  # Refuse before any network call, so a rejected body never leaves a partly
  # created ticket behind.
  fm_tracker_body_prose_blocker_refuse "$body" || exit 1
  if [ "$type" = decision ]; then
    fm_tracker_decision_body_refuse "$body" || exit 1
    body="$body$(fm_tracker_decision_escape_hatch)"
  fi

  require_gh
  verify_blockers_exist "$blockers"
  if [ -n "$parent" ]; then
    gh_api "/repos/$OWNER/$REPO/issues/$parent" --jq .number >/dev/null 2>&1 \
      || die "parent #$parent does not exist in $OWNER/$REPO"
  fi

  ensure_labels
  num=$(create_issue "$title" "$(compose_body "$body" "$blockers")" "$type") || exit 1
  [ -z "$parent" ] || attach_parent "$num" "$parent"
  printf 'created #%s [%s] %s\n' "$num" "$(fm_tracker_type_label "$type")" "$title"
  [ -z "$parent" ] || printf '  attached under #%s\n' "$parent"
  if [ -n "$blockers" ]; then
    printf '  blocked by: %s\n' "$(printf '%s' "$blockers" | sed 's/[0-9][0-9]*/#&/g; s/,/ /g')"
  fi
}

cmd_frontier() {
  local json
  parse_repo "${1-}"; shift
  [ "$#" -eq 0 ] || usage_die "unexpected argument '$1'"
  require_gh
  json=$(graph_json) || die "could not read the issue graph for $OWNER/$REPO"
  printf '%s\n' "$json" | fm_tracker_render_frontier "$OWNER/$REPO" "$(captain_login)"
}

cmd_validate() {
  local json
  parse_repo "${1-}"; shift
  [ "$#" -eq 0 ] || usage_die "unexpected argument '$1'"
  require_gh
  json=$(graph_json) || die "could not read the issue graph for $OWNER/$REPO"
  printf '%s\n' "$json" | fm_tracker_render_validate "$OWNER/$REPO"
}

cmd_claim() {
  local num me holder captain
  parse_repo "${1-}"; shift
  num=${1-}
  fm_tracker_issue_number_valid "$num" || usage_die "claim requires an issue number"
  shift
  [ "$#" -eq 0 ] || usage_die "unexpected argument '$1'"
  require_gh
  me=$(gh_api /user --jq .login) || die "could not resolve the authenticated GitHub login"
  holder=$(gh_api "/repos/$OWNER/$REPO/issues/$num" --jq '[.assignees[].login]|join(",")' 2>/dev/null) \
    || die "#$num does not exist in $OWNER/$REPO"
  # Assignment IS the claim, so an existing assignee is a lock another session
  # holds. Overwriting it would let two agents work the same ticket, which is
  # the exact failure the claim exists to prevent.
  if [ -n "$holder" ] && [ "$holder" != "$me" ]; then
    die "#$num is already claimed by $holder"
  fi
  # A claim by the captain's own login is indistinguishable from a wait ON the
  # captain, because both are the same assignment. This still claims - what
  # `claim` does for agents is unchanged - but it says so, because the frontier
  # will report the ticket as waiting on the captain rather than as claimed.
  captain=$(captain_login)
  if fm_tracker_is_captain "$me" "$captain"; then
    printf 'warning: %s is the configured captain login, so the frontier will report\n' "$me" >&2
    printf '         #%s as waiting on the captain rather than as claimed work\n' "$num" >&2
  fi
  gh_api -X POST "/repos/$OWNER/$REPO/issues/$num/assignees" -f "assignees[]=$me" >/dev/null \
    || die "could not claim #$num"
  printf 'claimed #%s as %s\n' "$num" "$me"
}

cmd_release() {
  local num me
  parse_repo "${1-}"; shift
  num=${1-}
  fm_tracker_issue_number_valid "$num" || usage_die "release requires an issue number"
  shift
  [ "$#" -eq 0 ] || usage_die "unexpected argument '$1'"
  require_gh
  me=$(gh_api /user --jq .login) || die "could not resolve the authenticated GitHub login"
  gh_api -X DELETE "/repos/$OWNER/$REPO/issues/$num/assignees" -f "assignees[]=$me" >/dev/null \
    || die "could not release #$num"
  printf 'released #%s\n' "$num"
}

cmd_answer() {
  local num='' decision='' settles='' not_settles='' labels comment comment_id json
  parse_repo "${1-}"; shift
  num=${1-}
  fm_tracker_issue_number_valid "$num" || usage_die "answer requires an issue number"
  shift
  while [ "$#" -gt 0 ]; do
    case $1 in
      --decision) [ "$#" -gt 1 ] || usage_die "--decision requires text"; decision=$2; shift 2 ;;
      --decision=*) decision=${1#--decision=}; shift ;;
      --settles) [ "$#" -gt 1 ] || usage_die "--settles requires text"; settles=$2; shift 2 ;;
      --settles=*) settles=${1#--settles=}; shift ;;
      --does-not-settle) [ "$#" -gt 1 ] || usage_die "--does-not-settle requires text"; not_settles=$2; shift 2 ;;
      --does-not-settle=*) not_settles=${1#--does-not-settle=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$decision" ] || usage_die "answer requires --decision <text>"
  require_gh
  labels=$(gh_api "/repos/$OWNER/$REPO/issues/$num" --jq '[.labels[].name]|join(" ")' 2>/dev/null) \
    || die "#$num does not exist in $OWNER/$REPO"
  # An answer is meaningful only on a ticket typed as a question. Refusing here
  # keeps a decision record off a task, where nothing would read it.
  fm_tracker_labels_answerable "$labels" \
    || die "#$num is not a decision or unknown (labels: ${labels:-none}); only those can be answered"

  comment=$(fm_tracker_answer_comment "$decision" "$settles" "$not_settles")
  comment_id=$(gh_api -X POST "/repos/$OWNER/$REPO/issues/$num/comments" -f "body=$comment" --jq .id) \
    || die "could not record the decision on #$num"
  record_self_comment "$comment_id"
  gh_api -X PATCH "/repos/$OWNER/$REPO/issues/$num" \
    -f state=closed -f state_reason=completed --jq .number >/dev/null \
    || die "could not close #$num"
  printf 'answered #%s: decision recorded and closed\n' "$num"

  if ! json=$(graph_json); then
    printf 'warning: could not re-read the graph to report what became unblocked\n' >&2
    return 0
  fi
  printf '%s\n' "$json" | fm_tracker_render_unblocked "$num"
}

# ---------------------------------------------------------------------------
# Comments the fleet writes
# ---------------------------------------------------------------------------

# Record a comment this script just wrote, so the wake can tell it from one the
# captain wrote from the same account. A failure here is reported and not fatal:
# the comment already exists, and the cost is one spurious wake rather than a
# lost decision.
record_self_comment() {  # <comment-id>
  local id=${1-}
  fm_tracker_self_comment_record "$STATE" "$id" && return 0
  printf 'warning: could not record comment id %s in state/%s; this comment may wake firstmate\n' \
    "${id:-<none>}" "$FM_TRACKER_SELF_FILE" >&2
}

# The fleet's issue-comment write path. It exists so that firstmate has one, and
# only one, way to comment that the wake can recognise afterwards: a comment
# posted with a bare `gh api` call leaves no record and wakes the fleet on its
# own writing.
cmd_comment() {
  local num='' body='' body_file='' have_body=0 comment_id
  parse_repo "${1-}"; shift
  num=${1-}
  fm_tracker_issue_number_valid "$num" || usage_die "comment requires an issue number"
  shift
  while [ "$#" -gt 0 ]; do
    case $1 in
      --body) [ "$#" -gt 1 ] || usage_die "--body requires text"; body=$2; have_body=1; shift 2 ;;
      --body=*) body=${1#--body=}; have_body=1; shift ;;
      --body-file) [ "$#" -gt 1 ] || usage_die "--body-file requires a path"; body_file=$2; shift 2 ;;
      --body-file=*) body_file=${1#--body-file=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  if [ -n "$body_file" ]; then
    [ "$have_body" -eq 0 ] || usage_die "pass --body or --body-file, not both"
    [ -f "$body_file" ] || die "no such body file: $body_file"
    body=$(cat "$body_file") || die "could not read $body_file"
    have_body=1
  fi
  [ "$have_body" -eq 1 ] || usage_die "comment requires --body <text> or --body-file <path>"
  [ -n "$body" ] || usage_die "comment refuses an empty body"
  require_gh
  comment_id=$(gh_api -X POST "/repos/$OWNER/$REPO/issues/$num/comments" -f "body=$body" --jq .id) \
    || die "could not comment on #$num"
  record_self_comment "$comment_id"
  printf 'commented on #%s\n' "$num"
}

# ---------------------------------------------------------------------------
# The task half of the tracker
# ---------------------------------------------------------------------------
#
# Every command below is built on ONE cached read of the issue graph, so a sync
# that reconciles a whole backlog costs one query plus a write per ticket that
# genuinely needs one.

GRAPH_CACHE=
GRAPH_LOADED=0
LABELS_ENSURED=0
TASK_INDEX=
DESTINATION=
GH_LOGIN=

graph_load() {
  [ "$GRAPH_LOADED" -eq 1 ] && return 0
  GRAPH_CACHE=$(graph_json) || return 1
  GRAPH_LOADED=1
  TASK_INDEX=$(printf '%s\n' "$GRAPH_CACHE" | fm_tracker_task_index)
  DESTINATION=$(printf '%s\n' "$GRAPH_CACHE" | fm_tracker_destination_number) || DESTINATION=
  return 0
}

ensure_labels_once() {  # [project-name]
  [ "$LABELS_ENSURED" -eq 1 ] && return 0
  ensure_labels "${1-}"
  LABELS_ENSURED=1
}

# Fields of the ticket bound to <task-id>, or nothing. The id is matched whole,
# so a task id that is a prefix of another cannot resolve to its neighbour's
# ticket.
task_index_row() {  # <task-id>
  local want=$1 id number state parent assignees
  while read -r id number state parent assignees; do
    [ "$id" = "$want" ] || continue
    printf '%s %s %s %s\n' "$number" "$state" "$parent" "$assignees"
    return 0
  done <<EOF
$TASK_INDEX
EOF
  return 1
}

# True when a US-separated row list carries <task-id> in its first field. Field
# equality rather than a regex, because a task id may contain characters a
# pattern would read as syntax.
row_list_has_task() {  # <rows> <task-id>
  printf '%s\n' "${1-}" | awk -F'\037' -v want="${2-}" '$1 == want { found = 1 } END { exit found ? 0 : 1 }'
}

authenticated_login() {
  [ -n "$GH_LOGIN" ] && { printf '%s\n' "$GH_LOGIN"; return 0; }
  GH_LOGIN=$(gh_api /user --jq .login) || return 1
  printf '%s\n' "$GH_LOGIN"
}

# Assignment IS the claim (defect 3). Claiming as the configured captain login
# would make the frontier report the ticket as a wait ON the captain rather than
# as claimed work, so this says so rather than quietly inverting the meaning.
claim_issue() {  # <number>
  local num=$1 me captain
  me=$(authenticated_login) || return 1
  captain=$(captain_login)
  if fm_tracker_is_captain "$me" "$captain"; then
    printf 'warning: %s is the configured captain login, so the frontier will report\n' "$me" >&2
    printf '         #%s as waiting on the captain rather than as claimed work\n' "$num" >&2
  fi
  gh_api -X POST "/repos/$OWNER/$REPO/issues/$num/assignees" -f "assignees[]=$me" >/dev/null 2>&1
}

# Create, reopen, re-body and claim one task ticket until it says what
# this task actually is, then print its number. Idempotent by construction: it
# writes only where the ticket and the intent disagree, so a sync that changes
# nothing makes no write at all.
#
# The composed body is authoritative because no part of it came from a caller
# (fm_tracker_task_prose owns why), so convergence never has to merge with
# anything a human typed. Comments are untouched and are where discussion lives.
#
# Task tickets are NOT parented under the destination. GitHub imposes a hard
# ceiling of 100 sub-issues per parent, and a destination that accumulates every
# dispatched task hits it. Tasks are grouped by a project label instead, which
# is unbounded, queryable, and keeps the destination's children curated to the
# small set a person reads: decisions, unknowns, and epics.
#
# Callers invoke this through a command substitution, which is also what bounds
# the `die` inside its GitHub helpers to this one ticket rather than the whole
# run: a sync must survive one ticket it cannot file.
# The body to publish: the marker, then whatever the author already had (or this
# script's own short note when the ticket is new), then the computed edges.
# fm_tracker_task_body_author_part owns why the author's half is preserved rather
# than regenerated.
compose_task_body() {  # <task-id> <kind> <project> <hold> <blocker-csv> <existing-body>
  local task=$1 kind=$2 project=$3 hold=$4 blockers=$5 existing=$6 author
  author=$(fm_tracker_task_body_author_part "$existing")
  [ -n "$author" ] || author=$(fm_tracker_task_default_note "$task" "$kind" "$project" "$hold")
  compose_body "$(fm_tracker_task_marker "$task"; printf '\n%s\n' "$author")" "$blockers"
}

# The hold is raised but never lowered here, and that asymmetry is the point.
# `sync` can see the hold the task list declares; it cannot see a hold decided
# anywhere else - in review, or about what a particular board may show - and a
# dispatch that cleared those would undo a decision nobody re-took. Lowering a
# hold is `unhold`, which is somebody deciding it.
ensure_task_ticket() {  # <task-id> <title> <kind> <project> <hold> <blocker-numbers-csv> <claim:0|1> <held:0|1>
  local task=$1 title=$2 kind=$3 project=$4 hold=$5 blockers=$6 claim=$7 held=${8:-0}
  local row num state parent assignees body current labels proj_label
  proj_label=
  [ -z "$project" ] || proj_label=$(fm_tracker_project_label "$project")
  if row=$(task_index_row "$task"); then
    read -r num state parent assignees <<EOF
$row
EOF
    if [ "$state" != OPEN ]; then
      gh_api -X PATCH "/repos/$OWNER/$REPO/issues/$num" -f state=open --jq .number >/dev/null \
        || { printf 'error: could not reopen #%s for %s\n' "$num" "$task" >&2; return 1; }
    fi
    current=$(printf '%s\n' "$GRAPH_CACHE" | fm_tracker_body_of "$num") || current=
    # A prose blocker already in the author's body is `validate`'s to report, not
    # this pass's to refuse: refusing here would leave the ticket without the very
    # edge that fixes it.
    body=$(compose_task_body "$task" "$kind" "$project" "$hold" "$blockers" "$current")
    if [ "$current" != "$body" ]; then
      gh_api -X PATCH "/repos/$OWNER/$REPO/issues/$num" -f "body=$body" --jq .number >/dev/null \
        || printf 'warning: could not update the body of #%s for %s\n' "$num" "$task" >&2
    fi
    # Add the project label to existing tickets that predate this grouping scheme.
    if [ -n "$proj_label" ]; then
      labels=$(printf '%s\n' "$GRAPH_CACHE" | fm_tracker_labels_of "$num") || labels=
      case " $labels " in
        *" $proj_label "*) ;;
        *) gh_api -X POST "/repos/$OWNER/$REPO/issues/$num/labels" \
             -f "labels[]=$proj_label" >/dev/null 2>&1 || true ;;
      esac
    fi
    if [ "$claim" = 1 ] && [ -z "$assignees" ]; then
      claim_issue "$num" || true
    fi
    if [ "$held" = 1 ]; then
      labels=$(printf '%s\n' "$GRAPH_CACHE" | fm_tracker_labels_of "$num") || labels=
      fm_tracker_labels_held "$labels" || add_held_label "$num" || true
    fi
    printf '%s\n' "$num"
    return 0
  fi
  body=$(compose_task_body "$task" "$kind" "$project" "$hold" "$blockers" '')
  fm_tracker_body_prose_blocker_refuse "$body" || return 1
  ensure_labels_once "$project"
  local -a extra_labels=()
  [ -z "$proj_label" ] || extra_labels+=("$proj_label")
  [ "$held" != 1 ] || extra_labels+=("$FM_TRACKER_HELD_LABEL")
  num=$(create_issue "$title" "$body" task "${extra_labels[@]}") || return 1
  if [ "$claim" = 1 ]; then
    claim_issue "$num" || true
  fi
  printf '%s\n' "$num"
}

# Record a ticket this run just resolved, so a later row in the same run reads it
# as an existing edge target instead of filing a second ticket for it.
task_index_record() {  # <task-id> <number> <claim:0|1>
  local holder=''
  [ "$3" = 1 ] && holder=$GH_LOGIN
  TASK_INDEX="${TASK_INDEX:+$TASK_INDEX
}$1 $2 OPEN - $holder"
}

cmd_task_open() {
  local task='' title='' kind='' project='' hold='' blockers='' claim=1 adopt='' held=0 num row
  parse_repo "${1-}"; shift
  while [ "$#" -gt 0 ]; do
    case $1 in
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      --title) [ "$#" -gt 1 ] || usage_die "--title requires a value"; title=$2; shift 2 ;;
      --title=*) title=${1#--title=}; shift ;;
      --kind) [ "$#" -gt 1 ] || usage_die "--kind requires a value"; kind=$2; shift 2 ;;
      --kind=*) kind=${1#--kind=}; shift ;;
      --project) [ "$#" -gt 1 ] || usage_die "--project requires a name"; project=$2; shift 2 ;;
      --project=*) project=${1#--project=}; shift ;;
      --hold) [ "$#" -gt 1 ] || usage_die "--hold requires text"; hold=$2; shift 2 ;;
      --hold=*) hold=${1#--hold=}; shift ;;
      --blocked-by) [ "$#" -gt 1 ] || usage_die "--blocked-by requires issue numbers"; blockers=$2; shift 2 ;;
      --blocked-by=*) blockers=${1#--blocked-by=}; shift ;;
      --adopt) [ "$#" -gt 1 ] || usage_die "--adopt requires an issue number"; adopt=$2; shift 2 ;;
      --adopt=*) adopt=${1#--adopt=}; shift ;;
      --no-claim) claim=0; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$task" ] || usage_die "task-open requires --task <task-id>"
  fm_pr_task_id_valid "$task" || usage_die "invalid task id '$task'"
  [ -n "$title" ] || usage_die "task-open requires --title <title>"
  case "$kind" in
    ''|ship|scout) ;;
    *) usage_die "invalid --kind '$kind' (expected ship or scout)" ;;
  esac
  if [ -n "$blockers" ]; then
    fm_tracker_blocker_csv_valid "$blockers" \
      || usage_die "invalid --blocked-by '$blockers' (expected comma-separated issue numbers)"
  fi
  if [ -n "$adopt" ]; then
    fm_tracker_issue_number_valid "$adopt" \
      || usage_die "invalid --adopt '$adopt' (expected a positive issue number)"
  fi
  require_gh
  graph_load || die "could not read the issue graph for $OWNER/$REPO"
  verify_blockers_exist "$blockers"
  # Adoption exists because a ticket filed before this layer reached the dispatch
  # carries no marker, so nothing binds it to a task and the next sync files a
  # SECOND ticket for the same work. It is deliberately explicit and by number:
  # matching an existing ticket to a task by title would be a guess, and a guess
  # here attaches a task's whole future to a ticket about something else.
  if [ -n "$adopt" ]; then
    if task_index_row "$task" >/dev/null; then
      die "$task is already bound to a ticket in $OWNER/$REPO; release that one before adopting another"
    fi
    row=$(printf '%s\n' "$GRAPH_CACHE" | fm_tracker_index_row_for_number "$adopt") \
      || die "#$adopt does not exist in $OWNER/$REPO"
    TASK_INDEX="${TASK_INDEX:+$TASK_INDEX
}$task $row"
  fi
  held=0
  [ -n "$hold" ] && held=1
  num=$(ensure_task_ticket "$task" "$title" "$kind" "$project" "$hold" "$blockers" "$claim" "$held") || exit 1
  printf 'task %s -> #%s in %s/%s\n' "$task" "$num" "$OWNER" "$REPO"
}

cmd_task_close() {
  local task='' outcome='' pr='' detail='' row num state comment comment_id reason
  parse_repo "${1-}"; shift
  while [ "$#" -gt 0 ]; do
    case $1 in
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      --outcome) [ "$#" -gt 1 ] || usage_die "--outcome requires a value"; outcome=$2; shift 2 ;;
      --outcome=*) outcome=${1#--outcome=}; shift ;;
      --pr) [ "$#" -gt 1 ] || usage_die "--pr requires a URL"; pr=$2; shift 2 ;;
      --pr=*) pr=${1#--pr=}; shift ;;
      --detail) [ "$#" -gt 1 ] || usage_die "--detail requires text"; detail=$2; shift 2 ;;
      --detail=*) detail=${1#--detail=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$task" ] || usage_die "task-close requires --task <task-id>"
  fm_pr_task_id_valid "$task" || usage_die "invalid task id '$task'"
  fm_tracker_outcome_valid "$outcome" \
    || usage_die "task-close requires --outcome <shipped|not-shipped>"
  require_gh
  graph_load || die "could not read the issue graph for $OWNER/$REPO"
  row=$(task_index_row "$task") || die "no task ticket in $OWNER/$REPO is bound to $task"
  read -r num state _parent _assignees <<EOF
$row
EOF
  # Closing a closed ticket again would post a second outcome onto a finished
  # record. Reporting and stopping keeps this safe to re-run, which is what a
  # cleanup path that can be retried needs.
  if [ "$state" != OPEN ]; then
    printf 'task %s -> #%s already closed in %s/%s\n' "$task" "$num" "$OWNER" "$REPO"
    return 0
  fi
  comment=$(fm_tracker_task_outcome_comment "$outcome" "$pr" "$detail")
  comment_id=$(gh_api -X POST "/repos/$OWNER/$REPO/issues/$num/comments" -f "body=$comment" --jq .id) \
    || die "could not record the outcome on #$num"
  record_self_comment "$comment_id"
  reason=$(fm_tracker_outcome_state_reason "$outcome")
  gh_api -X PATCH "/repos/$OWNER/$REPO/issues/$num" \
    -f state=closed -f "state_reason=$reason" --jq .number >/dev/null \
    || die "could not close #$num"
  printf 'task %s -> #%s closed (%s)\n' "$task" "$num" "$outcome"
}

# ---------------------------------------------------------------------------
# Holding and withholding
# ---------------------------------------------------------------------------

cmd_hold() {  # <owner/repo> <n> [--clear]
  local num clear=0
  parse_repo "${1-}"; shift
  num=${1-}
  [ -n "$num" ] || usage_die "hold requires an issue number"
  shift
  fm_tracker_issue_number_valid "$num" || usage_die "invalid issue number '$num'"
  while [ "$#" -gt 0 ]; do
    case $1 in
      --clear) clear=1; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  require_gh
  gh_api "/repos/$OWNER/$REPO/issues/$num" --jq .number >/dev/null 2>&1 \
    || die "#$num does not exist in $OWNER/$REPO"
  if [ "$clear" -eq 1 ]; then
    remove_held_label "$num" || true
    printf '#%s is available again in %s/%s\n' "$num" "$OWNER" "$REPO"
    return 0
  fi
  ensure_labels
  add_held_label "$num" || die "could not label #$num"
  printf '#%s is held in %s/%s and leaves the ready set\n' "$num" "$OWNER" "$REPO"
}

cmd_unhold() { cmd_hold "$@" --clear; }

# The withhold record is a decision about what may be PUBLISHED, so it is kept
# in this home's own configuration rather than on the board: a board is exactly
# the place a decision not to publish must not be written down.
cmd_withhold() {
  local project='' task='' reason=''
  while [ "$#" -gt 0 ]; do
    case $1 in
      --project) [ "$#" -gt 1 ] || usage_die "--project requires a name"; project=$2; shift 2 ;;
      --project=*) project=${1#--project=}; shift ;;
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      --reason) [ "$#" -gt 1 ] || usage_die "--reason requires text"; reason=$2; shift 2 ;;
      --reason=*) reason=${1#--reason=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$project" ] || usage_die "withhold requires --project <name>"
  [ -n "$task" ] || usage_die "withhold requires --task <task-id>"
  fm_pr_task_id_valid "$task" || usage_die "invalid task id '$task'"
  [ -n "$reason" ] || usage_die "withhold requires --reason <text>; a withhold nobody can explain later is a withhold nobody can lift"
  fm_tracker_withhold_record "$CONFIG" "$project" "$task" "$reason" \
    || die "could not record the withhold in $CONFIG/$FM_TRACKER_WITHHOLD_CONFIG"
  printf '%s is withheld from %s: %s\n' "$task" "$project" "$reason"
  printf 'no sync will file it until this is cleared with: fm-tracker.sh unwithhold --project %s --task %s\n' \
    "$project" "$task"
}

cmd_unwithhold() {
  local project='' task=''
  while [ "$#" -gt 0 ]; do
    case $1 in
      --project) [ "$#" -gt 1 ] || usage_die "--project requires a name"; project=$2; shift 2 ;;
      --project=*) project=${1#--project=}; shift ;;
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$project" ] || usage_die "unwithhold requires --project <name>"
  [ -n "$task" ] || usage_die "unwithhold requires --task <task-id>"
  fm_tracker_withhold_clear "$CONFIG" "$project" "$task" \
    || die "no withhold is recorded for $task under $project"
  printf '%s is no longer withheld from %s; the next sync will file it\n' "$task" "$project"
}

cmd_withheld() {
  local project='' names='' lines
  while [ "$#" -gt 0 ]; do
    case $1 in
      --project) [ "$#" -gt 1 ] || usage_die "--project requires a name"; project=$2; shift 2 ;;
      --project=*) project=${1#--project=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  if [ -n "$project" ]; then
    if fm_tracker_repo_for_project "$CONFIG" "$project"; then
      names=$FM_TRACKER_PROJECT_NAMES
    else
      names=$project
    fi
  fi
  lines=$(fm_tracker_withhold_lines "$CONFIG" "$names")
  if [ -z "$lines" ]; then
    printf 'nothing is withheld%s\n' "${project:+ from $project}"
    return 0
  fi
  printf '%s\n' "$lines"
}

# ---------------------------------------------------------------------------
# The path a dispatch takes
# ---------------------------------------------------------------------------
#
# `sync` is what makes the task half MECHANICAL rather than a discipline. It
# reconciles a project's whole open queue in one pass, so the frontier reports
# what is claimed, what is queued and what is blocked without anyone having to
# remember to file a ticket. bin/fm-spawn.sh calls it on every crewmate and scout
# launch, which is the one path a dispatch cannot route around.
#
# It NEVER fails. A project with no configured tracker, an absent gh, an
# unauthenticated gh, an unreachable GitHub and a hung connection all report what
# was not filed and exit 0, because a dispatch that dies because GitHub is down
# is a worse defect than the missing ticket it was trying to prevent.
sync_report() { printf 'TRACKER: %s\n' "$*" >&2; }

cmd_sync() {
  local project='' task='' dry=0 limit=25 repo names backlog
  local id kind rowrepo holdkind blocked section summary
  local rows pending progress created=0 skipped=0 capped=0 deferred
  local blocker_nums bid brow bnum bstate claim hold held num wrow
  while [ "$#" -gt 0 ]; do
    case $1 in
      --project) [ "$#" -gt 1 ] || usage_die "--project requires a name"; project=$2; shift 2 ;;
      --project=*) project=${1#--project=}; shift ;;
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      --dry-run) dry=1; shift ;;
      --limit) [ "$#" -gt 1 ] || usage_die "--limit requires a count"; limit=$2; shift 2 ;;
      --limit=*) limit=${1#--limit=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$project" ] || usage_die "sync requires --project <name>"
  case "$limit" in
    ''|*[!0-9]*|0) usage_die "invalid --limit '$limit'" ;;
  esac
  if [ -n "$task" ] && ! fm_pr_task_id_valid "$task"; then
    usage_die "invalid task id '$task'"
  fi

  if ! fm_tracker_repo_for_project "$CONFIG" "$project"; then
    sync_report "no tracker repository is configured for $project, so no task ticket was filed (config/$FM_TRACKER_PROJECT_CONFIG)"
    return 0
  fi
  repo=$FM_TRACKER_PROJECT_REPO
  names=$FM_TRACKER_PROJECT_NAMES
  parse_repo "$repo"

  if ! command -v "$GH" >/dev/null 2>&1; then
    sync_report "$GH is not on PATH, so no task ticket was filed in $repo"
    return 0
  fi
  backlog="$DATA/backlog.md"
  if ! rows=$(fm_tracker_backlog_rows "$backlog"); then
    sync_report "no readable task list at $backlog, so no task ticket was filed in $repo"
    return 0
  fi
  if ! graph_load; then
    sync_report "could not read the issue graph for $repo, so no task ticket was filed"
    return 0
  fi
  if [ -z "$DESTINATION" ]; then
    sync_report "note: $repo has no single open destination ticket; task tickets will be filed without a parent"
  fi

  # The withhold check runs HERE, before a title is composed and before the row
  # can reach any write path, because the summary is the title and a title is
  # usually the part a withhold is protecting. A row dropped here is reported by
  # name and reason, so the standing decision stays visible on the dispatch that
  # honours it rather than only in whatever review took it.
  pending=$(printf '%s\n' "$rows" | while IFS=$'\037' read -r id kind rowrepo holdkind blocked section summary; do
    [ -n "$id" ] || continue
    fm_tracker_project_matches "$rowrepo" "$names" || continue
    if fm_tracker_withheld_reason "$CONFIG" "$names" "$id"; then
      sync_report "withheld from $repo: $id ($FM_TRACKER_WITHHOLD_REASON)"
      # A withhold decided AFTER the row was filed cannot unpublish what is
      # already on the board, and quietly skipping the row would let that read as
      # if it had. Name the ticket instead: whether it is closed or edited is a
      # judgement, and one this must not make on a repository other people read.
      if wrow=$(task_index_row "$id"); then
        sync_report "  but $id already has ticket #${wrow%% *} there; close or edit it by hand if it must come down"
      fi
      continue
    fi
    printf '%s\037%s\037%s\037%s\037%s\037%s\037%s\n' \
      "$id" "$kind" "$rowrepo" "$holdkind" "$blocked" "$section" "$summary"
  done)
  if [ -z "$pending" ]; then
    sync_report "no open ship or scout work is recorded for $project, so nothing was filed in $repo"
    return 0
  fi

  # Place the rows so a blocker's ticket exists before the ticket referencing it.
  # A reference to a number that does not exist yet is a task-list entry GitHub
  # never resolves into an edge, and the blocked ticket then queries as READY -
  # defect 1 arriving through ordering rather than through wording. Each pass
  # files every row whose dependencies are already settled; a pass that files
  # nothing means what is left depends on itself, and is reported rather than
  # filed without its edges.
  progress=1
  while [ -n "$pending" ] && [ "$progress" -eq 1 ]; do
    progress=0
    rows=$pending
    pending=
    while IFS=$'\037' read -r id kind rowrepo holdkind blocked section summary; do
      [ -n "$id" ] || continue
      # A blocker that is closed, or that this deliberately does not mirror (a
      # finished task, or a captain-held row), contributes no edge: an edge to
      # something already settled says nothing the frontier can use.
      blocker_nums=
      deferred=0
      for bid in ${blocked//,/ }; do
        [ -n "$bid" ] || continue
        if brow=$(task_index_row "$bid"); then
          read -r bnum bstate _bparent _bassignees <<EOF
$brow
EOF
          [ "$bstate" = OPEN ] && blocker_nums="${blocker_nums:+$blocker_nums,}$bnum"
        elif row_list_has_task "$rows" "$bid"; then
          deferred=1
        fi
      done
      if [ "$deferred" -eq 1 ]; then
        pending="${pending:+$pending
}$id"$'\037'"$kind"$'\037'"$rowrepo"$'\037'"$holdkind"$'\037'"$blocked"$'\037'"$section"$'\037'"$summary"
        continue
      fi
      progress=1
      # An in-flight row has a live worker, so its ticket is claimed and leaves
      # the frontier; a queued row is available work and stays on it as READY or
      # BLOCKED. That split is the whole point of mirroring the queue at all.
      claim=0
      [ "$section" = flight ] && claim=1
      [ "$id" = "$task" ] && claim=1
      hold=
      held=0
      if [ -n "$holdkind" ]; then
        hold=$FM_TRACKER_HOLD_NOTE
        held=1
      fi
      if [ "$dry" -eq 1 ]; then
        # A dry run names the DECLARED dependency rather than an issue number:
        # the blocker's ticket is one of the things it did not create, so any
        # number printed here would be invented. The placeholder below still
        # enters the index, so the ordering pass is exercised for real.
        printf 'would file %s [%s] claim=%s blocked-by=%s: %s\n' \
          "$id" "$kind" "$claim" "${blocked:-none}" "$summary"
        task_index_record "$id" 0 "$claim"
        continue
      fi
      if [ "$created" -ge "$limit" ]; then
        capped=1
        continue
      fi
      if num=$(ensure_task_ticket "$id" "$summary" "$kind" "$project" "$hold" "$blocker_nums" "$claim" "$held"); then
        created=$((created + 1))
        task_index_record "$id" "$num" "$claim"
        if [ "$id" = "$task" ]; then
          sync_report "task ticket for $id: https://github.com/$OWNER/$REPO/issues/$num"
        fi
      else
        skipped=$((skipped + 1))
      fi
    done <<EOF
$rows
EOF
  done

  if [ -n "$pending" ]; then
    sync_report "left $(printf '%s\n' "$pending" | grep -c .) row(s) unfiled in $repo because their dependencies form a cycle"
  fi
  if [ "$capped" -eq 1 ]; then
    sync_report "stopped at the $limit-ticket limit for one pass in $repo; rerun to continue"
  fi
  if [ "$skipped" -gt 0 ]; then
    sync_report "$skipped task ticket(s) could not be filed in $repo"
  fi
  # The dispatched task getting no ticket while everything around it does is the
  # one silence worth breaking: it means the work has no row in firstmate's own
  # task list, which section 10 requires on every dispatch, and a missing row
  # costs far more than a missing ticket.
  if [ -n "$task" ] && [ "$dry" -eq 0 ] && ! task_index_row "$task" >/dev/null; then
    sync_report "no open ship or scout row names $task under $project, so it has no ticket in $repo; record it in the task list"
  fi
  if [ "$dry" -eq 0 ]; then
    printf 'sync %s -> %s: %s reconciled, %s could not be filed\n' "$project" "$repo" "$created" "$skipped"
  fi
  return 0
}

# The cleanup half of the same contract, and non-fatal for the same reason:
# bin/fm-teardown.sh calls it, and a cleanup that could not finish because GitHub
# is unreachable would strand a worktree over a ticket. The subshell is what
# bounds task-close's refusals to this call.
cmd_complete() {
  local project='' task='' outcome='' pr='' detail='' repo
  while [ "$#" -gt 0 ]; do
    case $1 in
      --project) [ "$#" -gt 1 ] || usage_die "--project requires a name"; project=$2; shift 2 ;;
      --project=*) project=${1#--project=}; shift ;;
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      --outcome) [ "$#" -gt 1 ] || usage_die "--outcome requires a value"; outcome=$2; shift 2 ;;
      --outcome=*) outcome=${1#--outcome=}; shift ;;
      --pr) [ "$#" -gt 1 ] || usage_die "--pr requires a URL"; pr=$2; shift 2 ;;
      --pr=*) pr=${1#--pr=}; shift ;;
      --detail) [ "$#" -gt 1 ] || usage_die "--detail requires text"; detail=$2; shift 2 ;;
      --detail=*) detail=${1#--detail=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$project" ] || usage_die "complete requires --project <name>"
  [ -n "$task" ] || usage_die "complete requires --task <task-id>"
  fm_pr_task_id_valid "$task" || usage_die "invalid task id '$task'"
  fm_tracker_outcome_valid "$outcome" \
    || usage_die "complete requires --outcome <shipped|not-shipped>"
  if ! fm_tracker_repo_for_project "$CONFIG" "$project"; then
    sync_report "no tracker repository is configured for $project, so no ticket was closed for $task"
    return 0
  fi
  repo=$FM_TRACKER_PROJECT_REPO
  if ! command -v "$GH" >/dev/null 2>&1; then
    sync_report "$GH is not on PATH, so no ticket was closed for $task in $repo"
    return 0
  fi
  if ! ( cmd_task_close "$repo" --task "$task" --outcome "$outcome" \
    ${pr:+--pr "$pr"} ${detail:+--detail "$detail"} ); then
    sync_report "could not close the task ticket for $task in $repo"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# The notification-driven wake
# ---------------------------------------------------------------------------

cmd_watch() {
  local task='' spec device check data tmp
  local -a repos=()
  while [ "$#" -gt 0 ]; do
    case $1 in
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      -*) usage_die "unexpected option '$1'" ;;
      *) repos+=("$1"); shift ;;
    esac
  done
  [ -n "$task" ] || usage_die "watch requires --task <task-id>"
  fm_pr_task_id_valid "$task" || usage_die "invalid task id '$task'"
  [ "${#repos[@]}" -gt 0 ] || usage_die "watch requires at least one owner/repo"
  [ "${#repos[@]}" -le "$FM_TRACKER_MAX_WATCHED_REPOS" ] \
    || die "watch accepts at most $FM_TRACKER_MAX_WATCHED_REPOS repositories, so one poll stays inside FM_CHECK_TIMEOUT"
  for spec in "${repos[@]}"; do
    fm_tracker_repo_parse "$spec" || usage_die "invalid repository '$spec' (expected owner/repo)"
  done

  fm_tracker_state_root_valid "$STATE" \
    || die "state directory is unavailable: '${STATE}' is not an existing directory"

  # The poll is silent on every error by design, so a missing dependency would
  # be indistinguishable from an inbox where nothing happened. Arming is the one
  # point where that can be reported, so it is refused here instead.
  command -v "${FM_TRACKER_PYTHON:-python3}" >/dev/null 2>&1 \
    || die "watching GitHub issue comments requires python3 on PATH"
  [ -f "$SCRIPT_DIR/fm-tracker-parse.py" ] && [ ! -L "$SCRIPT_DIR/fm-tracker-parse.py" ] \
    || die "bin/fm-tracker-parse.py is missing; the wake cannot filter a response without it"
  fm_tracker_artifact_path "$STATE" "$task" .check.sh || die "could not resolve the check path"
  check=$FM_TRACKER_ARTIFACT
  fm_tracker_artifact_path "$STATE" "$task" .tracker-watch || die "could not resolve the sidecar path"
  data=$FM_TRACKER_ARTIFACT
  device=$(fm_pr_file_device "$STATE") || die "state directory is unavailable"

  # A check already armed at this path belongs to whoever armed it. Overwriting
  # a PR merge poll, or any other check, would silently disarm it, so this
  # refuses unless the file is absent or is already this task's own shim.
  if [ -e "$check" ] || [ -L "$check" ]; then
    fm_tracker_shim_matches "$check" "$FM_HOME" "$FM_ROOT" "$task" \
      || die "a different check is already armed at state/$task.check.sh; disarm its owner first"
  fi
  fm_pr_regular_destination_on_device_or_absent "$data" "$device" \
    || die "the watch sidecar path is unavailable"

  umask 077
  tmp=$(mktemp "$STATE/.fm-tracker-watch.XXXXXX") || die "could not stage the watch sidecar"
  { printf '%s\n' "$FM_TRACKER_WATCH_MAGIC"
    for spec in "${repos[@]}"; do printf '%s\n' "$spec"; done
  } > "$tmp" || die "could not write the watch sidecar"
  chmod 0600 "$tmp" || die "could not secure the watch sidecar"
  mv -f -- "$tmp" "$data" || die "could not publish the watch sidecar"

  tmp=$(mktemp "$STATE/.fm-tracker-check.XXXXXX") || die "could not stage the check"
  fm_tracker_shim_content "$FM_HOME" "$FM_ROOT" "$task" > "$tmp" || die "could not write the check"
  chmod 0700 "$tmp" || die "could not secure the check"
  mv -f -- "$tmp" "$check" || die "could not publish the check"

  "$SCRIPT_DIR/fm-check-register.sh" "$task" >/dev/null || die "could not register the check"
  printf 'watching:%s for %s\n' "$(printf ' %s' "${repos[@]}")" "$task"
  printf 'armed: state/%s.check.sh (registered)\n' "$task"
}

# Disarming removes files, so it asserts the facts that make them ours before it
# removes anything: a real state root, a path-safe task id, an armed tracker
# sidecar, and a check that is this task's own shim. An unarmed task, a foreign
# check, or an empty variable is refused rather than resolved into some path
# nobody named.
cmd_unwatch() {
  local task='' suffix check
  while [ "$#" -gt 0 ]; do
    case $1 in
      --task) [ "$#" -gt 1 ] || usage_die "--task requires a task id"; task=$2; shift 2 ;;
      --task=*) task=${1#--task=}; shift ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
  [ -n "$task" ] || usage_die "unwatch requires --task <task-id>"
  fm_pr_task_id_valid "$task" || usage_die "invalid task id '$task'"
  fm_tracker_state_root_valid "$STATE" \
    || die "refusing to remove anything: '${STATE}' is not an existing state directory"
  fm_tracker_watch_armed "$STATE" "$task" \
    || die "refusing to remove anything: no tracker watch is armed for $task"

  fm_tracker_artifact_path "$STATE" "$task" .check.sh || die "could not resolve the check path"
  check=$FM_TRACKER_ARTIFACT
  if [ -e "$check" ] || [ -L "$check" ]; then
    fm_tracker_shim_matches "$check" "$FM_HOME" "$FM_ROOT" "$task" \
      || die "refusing to remove state/$task.check.sh: it is not this watch's own shim"
  fi

  for suffix in .check.sh .check-trust .tracker-watch .tracker-cursor; do
    fm_tracker_remove_artifact "$STATE" "$task" "$suffix" \
      || die "refusing to remove state/$task$suffix: it is not a plain file this watch published"
  done
  printf 'unwatched %s\n' "$task"
}

[ "$#" -gt 0 ] || usage_die "a command is required"
CMD=$1
shift
case $CMD in
  -h|--help|help) print_help ;;
  init) cmd_init "$@" ;;
  add) cmd_add "$@" ;;
  frontier) cmd_frontier "$@" ;;
  claim) cmd_claim "$@" ;;
  release) cmd_release "$@" ;;
  answer) cmd_answer "$@" ;;
  comment) cmd_comment "$@" ;;
  validate) cmd_validate "$@" ;;
  hold) cmd_hold "$@" ;;
  unhold) cmd_unhold "$@" ;;
  withhold) cmd_withhold "$@" ;;
  unwithhold) cmd_unwithhold "$@" ;;
  withheld) cmd_withheld "$@" ;;
  task-open) cmd_task_open "$@" ;;
  task-close) cmd_task_close "$@" ;;
  sync) cmd_sync "$@" ;;
  complete) cmd_complete "$@" ;;
  watch) cmd_watch "$@" ;;
  unwatch) cmd_unwatch "$@" ;;
  *) usage_die "unknown command '$CMD'" ;;
esac
