#!/usr/bin/env bash
# fm-tracker-lib.sh - shared contract for firstmate's GitHub Issues project
# layer. Sourced by bin/fm-tracker.sh and bin/fm-tracker-notify.sh.
#
# This file owns the three things that must not drift between the writer, the
# reader, and the wake: the type-label vocabulary, the blocked-by edge format,
# and the guards that refuse a malformed or destructive operation.
#
# Not executable on its own.
#
# Constants and GraphQL documents here are consumed by the sourcing scripts,
# which shellcheck cannot see from this file alone.
# shellcheck disable=SC2034

# The complete type vocabulary. Type is carried by a label and never by the
# title, so rewording a title cannot reclassify a ticket.
FM_TRACKER_TYPES='destination decision unknown task'
FM_TRACKER_LABEL_PREFIX='fm:'

# The frontier had three discriminators - the type label, the assignee, and the
# tracked-issue edge - and "nobody may pick this up yet" is none of them, so a
# held ticket fell through to READY and contradicted what its own body said.
#
# The fourth discriminator is a LABEL, for the one reason that decides the whole
# design: bin/fm-spawn.sh runs `sync` on every dispatch, so a state written only
# into a body or a comment is overwritten by the next dispatch. A label is the
# tracker's own vocabulary, is not part of the body sync converges, and survives.
#
# It lives in its own namespace so it can never be read as a second TYPE label:
# a state and a type are different questions, and counting the hold as a type
# would report every held ticket as ambiguous.
FM_TRACKER_STATE_PREFIX='fm:state:'
FM_TRACKER_HELD_LABEL='fm:state:held'

# Task tickets are grouped by project through a label rather than through a
# sub-issue edge to the destination.  GitHub imposes a hard ceiling of 100
# sub-issues per parent, and a destination that accumulates every dispatched task
# hits it.  The label is unbounded and queryable, so the same association is
# cheaper and never stops working.
FM_TRACKER_PROJECT_LABEL_PREFIX='fm:project:'

# The one heading blocking edges live under, and the one line shape allowed
# beneath it. GitHub builds a queryable trackedIssues edge only from a markdown
# task-list reference, so this shape is what makes a blocker visible to the
# frontier query at all. The checkbox is GitHub's to tick as the blocker closes.
FM_TRACKER_BLOCKED_HEADING='## Blocked by'
FM_TRACKER_EDGE_RE='^- \[[ xX]\] #[1-9][0-9]*$'

# Wording that means "this is blocked" in prose. A body carrying any of these
# outside the canonical section is refused: prose blockers read as authoritative
# to a human and are invisible to the query, which is how a genuinely blocked
# ticket came back READY.
#
# FM_TRACKER_PROSE_WORDS is the vocabulary; FM_TRACKER_PROSE_RE is that
# vocabulary bounded to whole words. The boundary is written as an explicit
# non-word character rather than \b or [[:<:]], because neither of those is
# understood by both GNU and BSD grep and this runs on both.
#
# Without the boundary the guard matched inside unrelated words: "the
# font-independent one" contains the letters of "dependent on" and refused a
# heading about typography. A guard that refuses correct bodies gets worked
# around, which costs the real defect it was built to stop.
FM_TRACKER_PROSE_WORDS='blocked[ -]?by|blocked on|blocks|depends on|dependent on|waiting on|waits on|blocker'
FM_TRACKER_PROSE_RE='(^|[^[:alnum:]_])('"$FM_TRACKER_PROSE_WORDS"')([^[:alnum:]_]|$)'

FM_TRACKER_WATCH_MAGIC='fm-tracker-watch-v1'
FM_TRACKER_CURSOR_MAGIC='fm-tracker-cursor-v1'
# One conditional request per repository plus one for /notifications must finish
# well inside FM_CHECK_TIMEOUT (30s by default), so the watch list is bounded.
FM_TRACKER_MAX_WATCHED_REPOS=10
# GitHub states its own cadence on /notifications via X-Poll-Interval. This is
# only the floor used when a response carries no such header.
FM_TRACKER_DEFAULT_POLL_INTERVAL=60

# ---------------------------------------------------------------------------
# What the fleet wrote itself
# ---------------------------------------------------------------------------
#
# The wake must not fire on a comment the fleet just wrote. The discriminator
# cannot be the author: the fleet authenticates as the captain's own account, so
# ignoring that login would discard the captain's answers - the one thing the
# wake exists to deliver. It is the same trap the assignment rule above
# documents, with the same root.
#
# The discriminator is WHAT WE DID, not who we are: every write path records the
# comment id GitHub returns, and the poll skips exactly those ids. A comment
# firstmate never wrote has no record and always wakes, whoever authored it.
#
# The record is home-wide rather than per-task, because a comment is written by
# whichever command happens to write it and has no task to belong to, while
# every poll in the home must be able to recognise it.
FM_TRACKER_SELF_MAGIC='fm-tracker-self-comments-v1'
FM_TRACKER_SELF_FILE='.tracker-self-comments'
# The floor on how long a recorded id is kept. The binding constraint is not the
# poll cycle: the comment cursor is durable, so the first poll after a watcher
# outage still asks for everything since the last successful poll and sees every
# comment the fleet wrote during it. Seven days covers an outage of that length
# while holding a handful of ids a day, so the file stays a few kilobytes.
# Nothing is dropped early; an id may outlive this, because the record is
# rewritten only when a later write finds something to drop.
FM_TRACKER_SELF_TTL=604800
# A hard ceiling under the age bound, so a runaway writer cannot grow the file
# without limit. The oldest entries go first: they are the ones a poll has most
# likely already passed. Reading the record never applies either bound - an id
# kept too long costs nothing, and dropping one early costs a spurious wake.
FM_TRACKER_SELF_MAX=1000

# Resolve the home-wide record of comments the fleet wrote, or refuse.
fm_tracker_self_comments_path() {  # <state>
  local state=${1-} parent
  FM_TRACKER_ARTIFACT=
  fm_tracker_state_root_valid "$state" || return 1
  parent=$(cd "$state" 2>/dev/null && pwd -P) || return 1
  [ -n "$parent" ] || return 1
  FM_TRACKER_ARTIFACT="$parent/$FM_TRACKER_SELF_FILE"
}

# Record one comment id the fleet just wrote. Appends rather than rewrites: a
# short append is atomic, so two commands writing comments at once cannot lose
# each other's id, and losing an id would cost a spurious wake.
fm_tracker_self_comment_record() {  # <state> <comment-id>
  local state=${1-} id=${2-} file
  case $id in
    ''|*[!0-9]*) return 1 ;;
  esac
  fm_tracker_self_comments_path "$state" || return 1
  file=$FM_TRACKER_ARTIFACT
  if [ -e "$file" ] || [ -L "$file" ]; then
    fm_tracker_plain_file "$file" || return 1
    fm_tracker_self_comment_header_ok "$file" || return 1
  else
    ( umask 077; printf '%s\n' "$FM_TRACKER_SELF_MAGIC" > "$file" ) 2>/dev/null || return 1
  fi
  printf '%s\t%s\n' "$(date +%s)" "$id" >> "$file" 2>/dev/null || return 1
  fm_tracker_self_comment_prune "$file"
  return 0
}

# True when the file declares itself ours.
fm_tracker_self_comment_header_ok() {  # <path>
  local first
  IFS= read -r first < "${1-}" 2>/dev/null || return 1
  [ "$first" = "$FM_TRACKER_SELF_MAGIC" ]
}

# Drop entries past either bound. Best-effort and lock-free: a prune racing an
# append can drop that one id, which costs one spurious wake and never a missed
# captain comment, so it is not worth serialising the write path for. awk exits
# non-zero when nothing needs dropping, so a quiet record is never rewritten.
fm_tracker_self_comment_prune() {  # <path>
  local file=${1-} cutoff tmp
  cutoff=$(( $(date +%s) - FM_TRACKER_SELF_TTL ))
  tmp=$(mktemp "$(dirname "$file")/.fm-tracker-self.XXXXXX" 2>/dev/null) || return 0
  awk -F'\t' -v magic="$FM_TRACKER_SELF_MAGIC" -v cutoff="$cutoff" \
    -v max="$FM_TRACKER_SELF_MAX" '
      NR == 1 { next }
      { total++ }
      $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $1 >= cutoff { line[++n] = $0 }
      END {
        start = (n > max) ? n - max + 1 : 1
        if (start == 1 && n == total) exit 1
        print magic
        for (i = start; i <= n; i++) print line[i]
      }
    ' "$file" > "$tmp" 2>/dev/null || { rm -f -- "$tmp"; return 0; }
  chmod 0600 "$tmp" 2>/dev/null || { rm -f -- "$tmp"; return 0; }
  mv -f -- "$tmp" "$file" 2>/dev/null || rm -f -- "$tmp"
  return 0
}

# ---------------------------------------------------------------------------
# The two meanings of an assignment
# ---------------------------------------------------------------------------
#
# An assignment means one of two opposite things, and which one depends entirely
# on WHO holds it.
#
# For an agent it is a claim: "I am working on this, do not take it". A claimed
# ticket is not available work, so it leaves both the ready and the blocked set.
#
# For the captain it is the opposite of a claim: nobody is working on it, and
# nothing moves until they answer. That is the single most important thing the
# frontier can report as BLOCKED, so a captain-held ticket stays on the frontier
# and names them as what it waits on.
#
# The discriminator is the assignee login, and it is read from local
# configuration rather than derived or hardcoded. `gh api /user` cannot serve
# here: it returns the login the fleet is AUTHENTICATED as, which is the same
# account every crewmate claims with, so deriving the captain from it would
# reclassify every agent claim as a wait on the captain. Absent configuration
# means no login is the captain's, which is exactly the behavior before this
# distinction existed: every assignment is a claim.
FM_TRACKER_CAPTAIN_CONFIG='captain-github'

# Resolve the captain's GitHub login, or print nothing. FM_CAPTAIN_GITHUB wins
# for a one-off run; otherwise the first non-blank line of the config file.
fm_tracker_captain_login() {  # <config-dir>
  local dir=${1-} file line
  if [ -n "${FM_CAPTAIN_GITHUB-}" ]; then
    printf '%s\n' "$FM_CAPTAIN_GITHUB"
    return 0
  fi
  [ -n "$dir" ] || return 0
  file="$dir/$FM_TRACKER_CAPTAIN_CONFIG"
  [ -f "$file" ] && [ ! -L "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [ -n "$line" ] || continue
    printf '%s\n' "$line"
    return 0
  done < "$file"
}

# True when <login> is the captain's. GitHub logins are case-insensitive, so a
# case difference between the config file and the API response must not be what
# decides whether a ticket reads as a wait or as a claim.
fm_tracker_is_captain() {  # <login> <captain-login>
  local who=${1-} captain=${2-}
  [ -n "$who" ] && [ -n "$captain" ] || return 1
  [ "$(printf '%s' "$who" | tr '[:upper:]' '[:lower:]')" \
    = "$(printf '%s' "$captain" | tr '[:upper:]' '[:lower:]')" ]
}

# True when the captain is among an assignee list. The graph joins an issue's
# assignees with a space and a blocker's with a semicolon, so both separators are
# accepted here rather than at each call site. A ticket assigned to the captain
# AND to an agent is a wait, not a claim: the agent cannot finish it either way,
# and reporting the wait is the answer that stays visible.
fm_tracker_assignees_include_captain() {  # <assignees> <captain-login>
  local list=${1-} captain=${2-} who
  [ -n "$captain" ] || return 1
  for who in ${list//;/ }; do
    fm_tracker_is_captain "$who" "$captain" && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# Identity validation
# ---------------------------------------------------------------------------

fm_tracker_repo_parse() {  # <owner/repo>
  local spec=${1-} owner repo
  FM_TRACKER_OWNER=
  FM_TRACKER_REPO=
  case "$spec" in
    */*/*|/*|*/) return 1 ;;
    */*) ;;
    *) return 1 ;;
  esac
  owner=${spec%%/*}
  repo=${spec#*/}
  [ -n "$owner" ] && [ -n "$repo" ] || return 1
  [ "${#owner}" -le 39 ] && [ "${#repo}" -le 100 ] || return 1
  case "$owner" in
    *[!A-Za-z0-9-]*|-*|*-) return 1 ;;
  esac
  case "$repo" in
    *[!A-Za-z0-9._-]*|.|..) return 1 ;;
  esac
  FM_TRACKER_OWNER=$owner
  FM_TRACKER_REPO=$repo
}

fm_tracker_issue_number_valid() {  # <n>
  local n=${1-}
  case "$n" in
    ''|*[!0-9]*|0*) return 1 ;;
  esac
}

fm_tracker_blocker_csv_valid() {  # <csv>
  local csv=${1-} n
  [ -n "$csv" ] || return 1
  while IFS= read -r n; do
    fm_tracker_issue_number_valid "$n" || return 1
  done <<EOF
$(printf '%s' "$csv" | tr ',' '\n')
EOF
}

fm_tracker_type_valid() {  # <type>
  local t=${1-} known
  for known in $FM_TRACKER_TYPES; do
    [ "$t" = "$known" ] && return 0
  done
  return 1
}

fm_tracker_type_label() {  # <type>
  printf '%s%s\n' "$FM_TRACKER_LABEL_PREFIX" "$1"
}

fm_tracker_project_label() {  # <project-name>
  printf '%s%s\n' "$FM_TRACKER_PROJECT_LABEL_PREFIX" "$1"
}

fm_tracker_all_labels() {
  local t out=''
  for t in $FM_TRACKER_TYPES; do
    out="$out $(fm_tracker_type_label "$t")"
  done
  printf '%s\n' "${out# } $FM_TRACKER_HELD_LABEL"
}

# True when a label list marks the ticket as not available to pick up.
fm_tracker_labels_held() {  # <labels>
  local l
  for l in ${1-}; do
    [ "$l" = "$FM_TRACKER_HELD_LABEL" ] && return 0
  done
  return 1
}

# Read the type off a space-separated label list into FM_TRACKER_TYPE and
# FM_TRACKER_TYPE_COUNT. Two type labels is an ambiguity, and the count is what
# reports it, so both are needed and both are set here.
#
# These are globals rather than a printed value on purpose: a command
# substitution runs in a subshell, so a count assigned there would never reach
# the caller, and the caller would read a stale count from the previous ticket.
FM_TRACKER_TYPE=''
FM_TRACKER_TYPE_COUNT=0
fm_tracker_type_of_labels() {  # <labels>
  local labels=${1-} l
  FM_TRACKER_TYPE=''
  FM_TRACKER_TYPE_COUNT=0
  for l in $labels; do
    case "$l" in
      "$FM_TRACKER_STATE_PREFIX"*) ;;
      "$FM_TRACKER_LABEL_PREFIX"*)
        FM_TRACKER_TYPE=${l#"$FM_TRACKER_LABEL_PREFIX"}
        FM_TRACKER_TYPE_COUNT=$((FM_TRACKER_TYPE_COUNT + 1))
        ;;
    esac
  done
}

fm_tracker_labels_answerable() {  # <labels>
  fm_tracker_type_of_labels "${1-}"
  [ "$FM_TRACKER_TYPE_COUNT" -eq 1 ] || return 1
  [ "$FM_TRACKER_TYPE" = decision ] || [ "$FM_TRACKER_TYPE" = unknown ]
}

# ---------------------------------------------------------------------------
# Defect 1: prose blockers are refused, never accepted
# ---------------------------------------------------------------------------

# Report every prose blocking declaration in a body, one record per finding:
#
#     <lineno>\x1f<the offending line>\x1f<issue numbers it names>\x1f<matched text>
#
# The matched text is the fourth field because naming the RULE and quoting the
# whole line leaves the author to re-derive which words tripped it, and on a long
# line that is several attempts of guessing. A finding raised by the SHAPE of a
# line inside the canonical section matched no wording, and its fourth field is
# empty.
#
# A blocking declaration is a BLOCK, not a line: a heading such as
# "**Blocked by:**" followed by task-list references is one declaration, and its
# references belong to it even though they sit on later lines. Judging the
# heading alone would report "names no issue" about a block that plainly names
# one, which is the same confident wrong answer this whole guard exists to stop.
#
# A finding is raised when a line reads as a blocking statement outside the
# canonical section, or when a line inside the canonical section is neither
# blank nor the exact edge shape. Silent when the body is clean.
fm_tracker_body_prose_blockers() {  # <body>
  local body=${1-} lines=() line i n refs matched in_section=0 sep=$'\037'
  while IFS= read -r line; do
    lines+=("$line")
  done <<EOF
$body
EOF
  n=${#lines[@]}
  i=0
  while [ "$i" -lt "$n" ]; do
    line=${lines[$i]}
    if [ "$line" = "$FM_TRACKER_BLOCKED_HEADING" ]; then
      in_section=1
      i=$((i + 1))
      continue
    fi
    case "$line" in
      '#'*) in_section=0 ;;
    esac
    if [ "$in_section" -eq 1 ]; then
      # Inside the canonical section only the exact edge shape and blank lines
      # are allowed; anything else there is prose wearing the right heading.
      if [ -z "$line" ] || printf '%s' "$line" | grep -Eq "$FM_TRACKER_EDGE_RE"; then
        i=$((i + 1))
        continue
      fi
      printf '%s%s%s%s%s%s%s\n' "$((i + 1))" "$sep" "$line" "$sep" \
        "$(printf '%s' "$line" | grep -oE '#[0-9]+' | tr -d '#' | tr '\n' ' ')" "$sep" ''
      i=$((i + 1))
      continue
    fi
    matched=$(fm_tracker_prose_match "$line")
    if [ -n "$matched" ]; then
      # Absorb the contiguous run of task-list references this declaration owns.
      refs=$(printf '%s' "$line" | grep -oE '#[0-9]+' | tr -d '#' | tr '\n' ' ')
      i=$((i + 1))
      while [ "$i" -lt "$n" ] && printf '%s' "${lines[$i]}" | grep -Eq "$FM_TRACKER_EDGE_RE"; do
        refs="$refs$(printf '%s' "${lines[$i]}" | grep -oE '#[0-9]+' | tr -d '#') "
        i=$((i + 1))
      done
      printf '%s%s%s%s%s%s%s\n' "$((i))" "$sep" "$line" "$sep" "$refs" "$sep" "$matched"
      continue
    fi
    i=$((i + 1))
  done
}

# The blocking wording one line states, or nothing. The surrounding boundary
# characters the match consumed are trimmed back off, so what is printed is the
# vocabulary the guard actually objects to and not the neighbouring punctuation.
fm_tracker_prose_match() {  # <line>
  local hit
  hit=$(printf '%s' "${1-}" | grep -oiE "$FM_TRACKER_PROSE_RE" | head -1) || return 0
  [ -n "$hit" ] || return 0
  hit=${hit#"${hit%%[[:alnum:]_]*}"}
  hit=${hit%"${hit##*[[:alnum:]_]}"}
  printf '%s' "$hit"
}

# Refuse a body carrying a prose blocker, naming the text it matched on every
# offending line and the supported alternative. Returns 1 so no caller can
# proceed to a write.
fm_tracker_body_prose_blocker_refuse() {  # <body>
  local found
  found=$(fm_tracker_body_prose_blockers "${1-}")
  [ -n "$found" ] || return 0
  printf 'error: refusing a prose blocking edge\n' >&2
  printf '%s\n' "$found" | while IFS=$'\037' read -r lineno text _refs matched; do
    if [ -n "$matched" ]; then
      printf '  line %s matched "%s": %s\n' "$lineno" "$matched" "$text" >&2
    else
      printf '  line %s under "%s" is not a task-list reference: %s\n' \
        "$lineno" "$FM_TRACKER_BLOCKED_HEADING" "$text" >&2
    fi
  done
  printf 'GitHub builds a queryable blocking edge only from a task-list reference,\n' >&2
  printf 'so a prose blocker is invisible to the frontier query and the ticket\n' >&2
  printf 'reports READY while it is genuinely blocked.\n' >&2
  printf 'Pass blockers as --blocked-by <n[,n...]> and let this script write them.\n' >&2
  return 1
}

# ---------------------------------------------------------------------------
# Destructive-path guards
# ---------------------------------------------------------------------------
#
# A destructive path is never assembled from unguarded variables. An empty state
# root turns "$STATE/$id.check.sh" into "/<id>.check.sh"; an empty id turns it
# into a file nobody named. Both are refused here, before any caller can act on
# the result. The guards assert the facts that make a path ours to remove - the
# root is a real directory, the id is path-safe, the artifact is a plain file
# directly inside that root - rather than trusting the shape of the string.

fm_tracker_state_root_valid() {  # <state>
  local state=${1-}
  [ -n "$state" ] || return 1
  case "$state" in
    /*) ;;
    *) return 1 ;;
  esac
  [ -d "$state" ] && [ ! -L "$state" ]
}

# Resolve one artifact path for a task, or refuse. The suffix comes from a fixed
# allowlist of the artifacts this script publishes, so no caller can aim the
# guard at an arbitrary name.
fm_tracker_artifact_path() {  # <state> <task> <suffix>
  local state=${1-} task=${2-} suffix=${3-} parent
  FM_TRACKER_ARTIFACT=
  fm_tracker_state_root_valid "$state" || return 1
  fm_pr_task_id_valid "$task" || return 1
  case "$suffix" in
    .check.sh|.check-trust|.tracker-watch|.tracker-cursor) ;;
    *) return 1 ;;
  esac
  # The composed path must sit directly inside the physical state directory, so
  # no component can walk out of it.
  parent=$(cd "$state" 2>/dev/null && pwd -P) || return 1
  [ -n "$parent" ] || return 1
  FM_TRACKER_ARTIFACT="$parent/$task$suffix"
}

# True when the file is a plain, single-link, non-symlink file - the shape this
# script publishes and the only shape it will remove.
fm_tracker_plain_file() {  # <path>
  local path=${1-}
  [ -n "$path" ] || return 1
  [ -f "$path" ] && [ ! -L "$path" ] || return 1
  [ "$(fm_pr_file_link_count "$path")" = 1 ]
}

# True when the sidecar exists and declares itself one of ours.
fm_tracker_watch_armed() {  # <state> <task>
  local first
  fm_tracker_artifact_path "${1-}" "${2-}" .tracker-watch || return 1
  fm_tracker_plain_file "$FM_TRACKER_ARTIFACT" || return 1
  IFS= read -r first < "$FM_TRACKER_ARTIFACT" 2>/dev/null || return 1
  [ "$first" = "$FM_TRACKER_WATCH_MAGIC" ]
}

# Remove one artifact, refusing anything that is not the plain file this script
# published at that exact path. An absent artifact is success: removal is
# idempotent, but it is never blind.
fm_tracker_remove_artifact() {  # <state> <task> <suffix>
  fm_tracker_artifact_path "${1-}" "${2-}" "${3-}" || return 1
  [ -e "$FM_TRACKER_ARTIFACT" ] || [ -L "$FM_TRACKER_ARTIFACT" ] || return 0
  fm_tracker_plain_file "$FM_TRACKER_ARTIFACT" || return 1
  rm -f -- "$FM_TRACKER_ARTIFACT"
}

# ---------------------------------------------------------------------------
# The watcher shim
# ---------------------------------------------------------------------------

# The watcher copies a custom check to a randomly named snapshot before running
# it, so the check cannot derive its own task from $0 the way the PR poll does.
# The task id is passed as an argument instead, shell-quoted, and it has already
# passed fm_pr_task_id_valid before reaching here.
fm_tracker_shim_content() {  # <home> <root> <task>
  local home=$1 root=$2 task=$3
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    '# Auto-generated by fm-tracker.sh - GitHub issue-comment wake shim.' \
    '# The watcher runs this each check cycle; output becomes a check: wake.' \
    "export FM_HOME=$(printf '%q' "$home")" \
    "exec $(printf '%q' "$root/bin/fm-tracker-notify.sh") --task $(printf '%q' "$task")"
}

fm_tracker_shim_matches() {  # <file> <home> <root> <task>
  local file=$1
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  cmp -s "$file" <(fm_tracker_shim_content "$2" "$3" "$4")
}

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

fm_tracker_destination_template() {
  cat <<'EOF'
## Destination

_What "done" looks like, in the captain's own words._

## Explicitly out of scope

_What this project is deliberately NOT doing, so scope creep has a named edge._

## Known unknowns

_What is not yet known. Each one that could change what gets built becomes its
own `fm:unknown` ticket._

## Checkable / watchable

**Checkable** - can be asserted by a test or a query, and belongs to the crew:

**Watchable** - only a human judgement settles it, and stays with the captain:
EOF
}

# ---------------------------------------------------------------------------
# A decision has to be answerable cold
# ---------------------------------------------------------------------------
#
# A decision ticket reaches the captain as a phone notification, so its body is
# the whole surface they get. A notification that arrives and then demands
# research is not a notification worth sending, so the write path refuses a
# decision body that cannot be answered from itself.
#
# WHERE THE LINE IS DRAWN. Structure is enforced, judgement is not. Whether a
# context section exists, whether there are at least two options, whether each
# option states its consequence, and whether a recommendation exists are all
# mechanically decidable, and every one of them missing makes the ticket
# unanswerable without opening something else. Whether the recommendation is any
# GOOD is not decidable here and is left to the author.
#
# The invitation to answer with something none of the options say is not asked of
# the author at all: this script writes it, identically, on every decision. An
# options list with no way out quietly pushes the reader toward the nearest
# listed answer, and a section the author can forget is a section that will be
# forgotten on the ticket where it matters most.
FM_TRACKER_DECISION_CONTEXT_HEADING='## Context'
FM_TRACKER_DECISION_OPTIONS_HEADING='## Options'
FM_TRACKER_DECISION_RECOMMENDATION_HEADING='## Recommendation'
FM_TRACKER_DECISION_ESCAPE_HEADING='## Or something else'
# An option carries its own consequence: a noun, then " - ", then what follows
# from choosing it. Both sides must be non-empty.
FM_TRACKER_DECISION_OPTION_RE='^- +[^ ].* - +[^ ]'

# Print the lines under <heading>, stopping at the next ATX heading. Silent when
# the heading is absent.
fm_tracker_body_section() {  # <body> <heading>
  local body=${1-} heading=${2-} line inside=0
  while IFS= read -r line; do
    if [ "$line" = "$heading" ]; then
      inside=1
      continue
    fi
    case $line in
      '#'*)
        if [ "$inside" -eq 1 ]; then
          return 0
        fi
        ;;
    esac
    if [ "$inside" -eq 1 ]; then
      printf '%s\n' "$line"
    fi
  done <<EOF
$body
EOF
}

# Report every way a decision body fails to be answerable cold, one problem per
# line. Silent when the body carries what a reader needs. Deliberately says
# nothing about the escape-hatch section, which this script writes rather than
# reads from the author.
fm_tracker_decision_body_problems() {  # <body>
  local body=${1-} section line options=0
  section=$(fm_tracker_body_section "$body" "$FM_TRACKER_DECISION_CONTEXT_HEADING")
  if [ -z "${section//[[:space:]]/}" ]; then
    printf 'has no "%s" section with anything under it, so answering it starts with research\n' \
      "$FM_TRACKER_DECISION_CONTEXT_HEADING"
  fi

  section=$(fm_tracker_body_section "$body" "$FM_TRACKER_DECISION_OPTIONS_HEADING")
  while IFS= read -r line; do
    case $line in
      '- '*) ;;
      *) continue ;;
    esac
    if printf '%s' "$line" | grep -Eq "$FM_TRACKER_DECISION_OPTION_RE"; then
      options=$((options + 1))
    else
      printf 'option "%s" states no consequence; write it as "- <the option> - <what follows from it>"\n' \
        "$line"
    fi
  done <<EOF
$section
EOF
  if [ "$options" -lt 2 ]; then
    printf 'has fewer than two options under "%s", and one option is not a choice\n' \
      "$FM_TRACKER_DECISION_OPTIONS_HEADING"
  fi

  section=$(fm_tracker_body_section "$body" "$FM_TRACKER_DECISION_RECOMMENDATION_HEADING")
  if [ -z "${section//[[:space:]]/}" ]; then
    printf 'has no "%s" section with anything under it, so the answer has to be built from scratch\n' \
      "$FM_TRACKER_DECISION_RECOMMENDATION_HEADING"
  fi
}

fm_tracker_decision_template() {
  cat <<EOF
$FM_TRACKER_DECISION_CONTEXT_HEADING

_Everything needed to answer this without opening anything else._

$FM_TRACKER_DECISION_OPTIONS_HEADING

- <the first option, in your own nouns> - <what follows from choosing it>
- <the second option> - <what follows from choosing it>

$FM_TRACKER_DECISION_RECOMMENDATION_HEADING

_Which option, and why. A recommendation, not a decision._
EOF
}

# The invitation to answer with something none of the options say. Fixed text,
# written by this script on every decision, so it cannot be forgotten.
fm_tracker_decision_escape_hatch() {
  cat <<EOF


$FM_TRACKER_DECISION_ESCAPE_HEADING

The options above are a starting point, not the whole answer space.
If the right call is none of them, say it in your own words and it will be recorded verbatim.
EOF
}

# Refuse a decision body that cannot be answered cold, naming every problem and
# printing the shape that works. Returns 1 so no caller can proceed to a write.
fm_tracker_decision_body_refuse() {  # <body>
  local body=${1-} found line
  found=$(fm_tracker_decision_body_problems "$body")
  if printf '%s\n' "$body" | grep -Fxq "$FM_TRACKER_DECISION_ESCAPE_HEADING"; then
    found="$found${found:+
}$(printf 'already carries a "%s" section; this script writes that one, so remove yours' \
      "$FM_TRACKER_DECISION_ESCAPE_HEADING")"
  fi
  [ -n "$found" ] || return 0
  printf 'error: refusing a decision that cannot be answered cold\n' >&2
  printf '%s\n' "$found" | while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '  the body %s\n' "$line" >&2
  done
  printf 'This ticket reaches the captain as a notification and nothing else, so its\n' >&2
  printf 'body has to carry the context, the options with their consequences, and a\n' >&2
  printf 'recommendation. Start from this shape:\n\n' >&2
  fm_tracker_decision_template >&2
  return 1
}

fm_tracker_answer_comment() {  # <decision> <settles> <not-settles>
  local decision=$1 settles=${2-} not_settles=${3-}
  printf '%s\n\n' '**Captain'"'"'s decision** (recorded verbatim):'
  printf '%s\n\n' "$decision"
  printf '%s\n' '**What this settles:**'
  if [ -n "$settles" ]; then
    printf '%s\n\n' "$settles"
  else
    printf '%s\n\n' '_Not stated. Read the decision above as settling only its own question._'
  fi
  printf '%s\n' '**What this does NOT settle:**'
  if [ -n "$not_settles" ]; then
    printf '%s\n' "$not_settles"
  else
    printf '%s\n' '_Not stated. Anything this decision does not name is still open._'
  fi
}

# ---------------------------------------------------------------------------
# The issue graph: one query, one record format
# ---------------------------------------------------------------------------

# Native sub-issue attachment for decisions and unknowns only.  Task tickets
# group by a project label instead: GitHub imposes a hard ceiling of 100
# sub-issues per parent, and a destination accumulating every dispatched task
# hits it.  Decisions and unknowns are the curated set a person reads.
# shellcheck disable=SC2016 # $name is a GraphQL variable, not a shell one.
FM_TRACKER_ADD_SUB_ISSUE='mutation($parentId:ID!,$subIssueId:ID!){
  addSubIssue(input:{issueId:$parentId,subIssueId:$subIssueId}){ subIssue{ number } }
}'

# shellcheck disable=SC2016 # $name is a GraphQL variable, not a shell one.
FM_TRACKER_GRAPH_QUERY='query($owner:String!,$repo:String!,$endCursor:String){
  repository(owner:$owner,name:$repo){
    issues(first:100,after:$endCursor,states:[OPEN,CLOSED],orderBy:{field:CREATED_AT,direction:ASC}){
      pageInfo{hasNextPage endCursor}
      nodes{
        number state title body
        labels(first:20){nodes{name}}
        assignees(first:10){nodes{login}}
        parent{number}
        trackedIssues(first:50){
          nodes{
            number state title
            labels(first:20){nodes{name}}
            assignees(first:10){nodes{login}}
          }
        }
      }
    }
  }
}'

# One record per issue, fields separated by US (0x1f). Tab is deliberately NOT
# the separator: shell IFS treats whitespace delimiters as a run, so consecutive
# tabs would collapse and every field after an empty one would shift by one.
# 0x1f is not whitespace, so an empty field stays an empty field.
#
# Title and body are base64 so a multi-line body cannot break a record.
#   number  state  labels  assignees  parent  blockers  title_b64  body_b64
# where each blocker is "number|state|type|assignee|held" and blockers are comma
# separated. A blocker's own hold travels with it so a ticket waiting on held
# work says so, instead of describing it as available.
FM_TRACKER_GRAPH_JQ='.data.repository.issues.nodes[]
| [ (.number|tostring),
    .state,
    ([.labels.nodes[].name]|join(" ")),
    ([.assignees.nodes[].login]|join(" ")),
    (if .parent then (.parent.number|tostring) else "-" end),
    ([.trackedIssues.nodes[]
      | [ (.number|tostring),
          .state,
          (([.labels.nodes[].name]
            |map(select(startswith("fm:") and (startswith("fm:state:")|not)))|.[0])
           // "fm:-" | ltrimstr("fm:")),
          ([.assignees.nodes[].login]|join(";")),
          (if ([.labels.nodes[].name]|index("fm:state:held")) then "held" else "" end)
        ]|join("|")
     ]|join(",")),
    (.title|@base64),
    ((.body // "")|@base64)
  ]|join("\u001f")'

fm_tracker_b64_decode() {  # <b64>
  printf '%s' "$1" | base64 --decode 2>/dev/null
}

# Describe what one blocker is waiting on, in the captain's nouns rather than
# the graph's. A blocker the captain holds is not claimed work either: saying
# "claimed by" there would hide the wait one level down from the ticket that
# reports it.
fm_tracker_blocker_phrase() {  # <number> <state> <type> <assignee> [captain-login] [held]
  local n=$1 state=$2 type=$3 who=$4 captain=${5-} held=${6-} what
  case "$type" in
    decision) what='a decision the captain owns' ;;
    unknown) what='an unresolved unknown' ;;
    task) what='unfinished work' ;;
    destination) what='the destination itself' ;;
    *) what='an untyped ticket' ;;
  esac
  if [ -n "$held" ]; then
    printf '#%s - %s, on hold\n' "$n" "$what"
    : "$state"
    return 0
  fi
  if fm_tracker_assignees_include_captain "$who" "$captain"; then
    printf '#%s - %s, with the captain (%s)\n' "$n" "$what" "${who//;/, }"
  elif [ -n "$who" ]; then
    printf '#%s - %s, claimed by %s\n' "$n" "$what" "${who//;/, }"
  else
    printf '#%s - %s, unclaimed\n' "$n" "$what"
  fi
  : "$state"
}

# Render the frontier from graph records on stdin. An agent's assignment is a
# claim and leaves both sets, because a claimed ticket is not available work. The
# captain's assignment is the opposite and stays in BLOCKED naming them. An empty
# <captain-login> means no login is the captain's, so every assignment is a claim.
#
# A hold is the fourth answer and gets its own section. It ranks above BLOCKED
# because a hold does not clear when a blocker closes - reporting a held ticket
# as merely blocked would promise it becomes available the moment its edges do -
# and below CLAIMED, because someone already working it settles the question.
# Whatever it also waits on is still printed under it.
fm_tracker_render_frontier() {  # <owner/repo> [captain-login]
  local slug=$1 captain=${2-}
  local number state labels assignees parent blockers title_b64 body_b64
  local title type ready='' blocked='' claimed='' held='' b bn bstate btype bwho bheld waits
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    [ -n "$number" ] || continue
    [ "$state" = OPEN ] || continue
    title=$(fm_tracker_b64_decode "$title_b64")
    fm_tracker_type_of_labels "$labels"
    type=$FM_TRACKER_TYPE
    [ -n "$type" ] || type='untyped'
    waits=
    if fm_tracker_assignees_include_captain "$assignees" "$captain"; then
      waits="$(printf '            waits on the captain (%s) - assigned, not claimed' \
        "${assignees// /, }")
"
    elif [ -n "$assignees" ]; then
      # The trailing newline is restored outside the substitution, which strips
      # it: without this every claimed ticket ran onto the end of the previous
      # one, and the section that reports what the fleet is working on is
      # unreadable exactly when the fleet is working on more than one thing.
      claimed="$claimed$(printf '  #%-5s [%-11s] %s\n            claimed by %s' \
        "$number" "$type" "$title" "${assignees// /, }")
"
      continue
    fi
    if [ -n "$blockers" ]; then
      while IFS= read -r b; do
        [ -n "$b" ] || continue
        IFS='|' read -r bn bstate btype bwho bheld <<EOF
$b
EOF
        [ "$bstate" = OPEN ] || continue
        waits="$waits$(printf '            waits on %s' "$(fm_tracker_blocker_phrase "$bn" "$bstate" "$btype" "$bwho" "$captain" "$bheld")")
"
      done <<EOF
$(printf '%s' "$blockers" | tr ',' '\n')
EOF
    fi
    if fm_tracker_labels_held "$labels"; then
      held="$held$(printf '  #%-5s [%-11s] %s\n' "$number" "$type" "$title")
$waits"
    elif [ -n "$waits" ]; then
      blocked="$blocked$(printf '  #%-5s [%-11s] %s\n' "$number" "$type" "$title")
$waits"
    else
      ready="$ready$(printf '  #%-5s [%-11s] %s\n' "$number" "$type" "$title")
"
    fi
    : "$parent" "$body_b64"
  done
  printf 'frontier: %s\n\n' "$slug"
  printf 'READY (open, unclaimed, no open blocker):\n'
  if [ -n "$ready" ]; then printf '%s' "$ready"; else printf '  (none)\n'; fi
  printf '\nBLOCKED (open, waiting on something - a blocker, or the captain):\n'
  if [ -n "$blocked" ]; then printf '%s' "$blocked"; else printf '  (none)\n'; fi
  printf '\nHELD (open, deliberately not available to pick up):\n'
  if [ -n "$held" ]; then printf '%s' "$held"; else printf '  (none)\n'; fi
  printf '\nCLAIMED (excluded from the frontier - an agent assignment is the claim):\n'
  if [ -n "$claimed" ]; then printf '%s' "$claimed"; else printf '  (none)\n'; fi
  if [ -z "$captain" ]; then
    printf '\nnote: no captain login is configured, so every assignment reads as a claim.\n'
    printf '      put the captain GitHub login in config/%s to tell a wait from a claim.\n' \
      "$FM_TRACKER_CAPTAIN_CONFIG"
  fi
}

# Report every structurally malformed ticket. This is the standing guard against
# both observed defects returning by hand-editing.
fm_tracker_render_validate() {  # <owner/repo>
  local slug=$1
  local number state labels assignees parent blockers title_b64 body_b64
  local title body type problems=0 prose lineno text refs ref edges problem
  printf 'validate: %s\n' "$slug"
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    [ -n "$number" ] || continue
    title=$(fm_tracker_b64_decode "$title_b64")
    body=$(fm_tracker_b64_decode "$body_b64")
    fm_tracker_type_of_labels "$labels"
    type=$FM_TRACKER_TYPE
    if [ "$FM_TRACKER_TYPE_COUNT" -eq 0 ]; then
      printf '  #%s missing type label - type would have to be guessed from the title: %s\n' "$number" "$title"
      problems=$((problems + 1))
    elif [ "$FM_TRACKER_TYPE_COUNT" -gt 1 ]; then
      printf '  #%s carries %s type labels (%s) - type is ambiguous\n' "$number" "$FM_TRACKER_TYPE_COUNT" "$labels"
      problems=$((problems + 1))
    fi
    # A prose blocker line is judged against the edges GitHub actually resolved,
    # never against how the line looks. A bold "**Blocked by:**" heading over a
    # real task-list reference still produces a queryable edge, and calling that
    # invisible would be the same confident wrong answer this guard exists to
    # catch. Only a reference with no matching edge is genuinely invisible.
    edges=" $(printf '%s' "$blockers" | tr ',' '\n' | cut -d'|' -f1 | tr '\n' ' ') "
    prose=$(fm_tracker_body_prose_blockers "$body")
    if [ -n "$prose" ]; then
      while IFS=$'\037' read -r lineno text refs _matched; do
        if [ -z "${refs// /}" ]; then
          printf '  #%s line %s states a blocker in prose but names no issue - a human reads it\n' \
            "$number" "$lineno"
          printf '        as authoritative and nothing queries it: %s\n' "$text"
          problems=$((problems + 1))
          continue
        fi
        for ref in $refs; do
          case $edges in
            *" $ref "*)
              printf '  #%s line %s uses a non-canonical blocked-by heading for #%s - the edge\n' \
                "$number" "$lineno" "$ref"
              printf '        resolves, but this was not written by fm-tracker.sh add\n'
              ;;
            *)
              printf '  #%s line %s names #%s as a blocker with no resolved edge - invisible to\n' \
                "$number" "$lineno" "$ref"
              printf '        the frontier query, so this ticket reports READY while blocked\n'
              ;;
          esac
          problems=$((problems + 1))
        done
      done <<EOF
$prose
EOF
    fi
    # A decision that cannot be answered cold is the same class of defect as an
    # invisible blocker: the tool reported something and the report was not
    # usable. The write path refuses one, so anything malformed here arrived by
    # hand-edit, which is exactly what this guard is for.
    if [ "$type" = decision ] && [ "$FM_TRACKER_TYPE_COUNT" -eq 1 ]; then
      while IFS= read -r problem; do
        [ -n "$problem" ] || continue
        printf '  #%s %s\n' "$number" "$problem"
        problems=$((problems + 1))
      done <<EOF
$(fm_tracker_decision_body_problems "$body")
EOF
      if ! printf '%s\n' "$body" | grep -Fxq "$FM_TRACKER_DECISION_ESCAPE_HEADING"; then
        printf '  #%s has no "%s" section - an options list with no way out pushes\n' \
          "$number" "$FM_TRACKER_DECISION_ESCAPE_HEADING"
        printf '        the reader toward the nearest listed answer\n'
        problems=$((problems + 1))
      fi
    fi
    if [ "$type" != destination ] && [ "$type" != task ] && [ "$parent" = '-' ]; then
      printf '  #%s orphaned - no parent, so it hangs off no destination and never rolls up\n' "$number"
      problems=$((problems + 1))
    fi
    if [ "$type" = destination ] && [ "$parent" != '-' ]; then
      printf '  #%s is a destination but is attached under #%s - a destination is a root\n' "$number" "$parent"
      problems=$((problems + 1))
    fi
    : "$state" "$assignees"
  done
  if [ "$problems" -eq 0 ]; then
    printf '  no malformed tickets\n'
    return 0
  fi
  printf '\n%s malformed ticket(s). fm-tracker.sh add writes this structure correctly;\n' "$problems"
  printf 'hand-edits do not.\n'
  return 1
}

# After an answer closes a ticket, name every ticket whose last open blocker it
# was. Reading this from the graph rather than from the closing action keeps the
# answer honest: a ticket with another open blocker is not reported as freed.
fm_tracker_render_unblocked() {  # <closed-number>
  local closed=$1
  local number state labels assignees parent blockers title_b64 body_b64
  local title type b bn bstate btype bwho names='' still tracked
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    [ -n "$number" ] || continue
    [ "$state" = OPEN ] || continue
    [ -n "$blockers" ] || continue
    tracked=0
    still=0
    while IFS= read -r b; do
      [ -n "$b" ] || continue
      IFS='|' read -r bn bstate btype bwho <<EOF
$b
EOF
      [ "$bn" = "$closed" ] && tracked=1
      [ "$bstate" = OPEN ] && still=$((still + 1))
    done <<EOF
$(printf '%s' "$blockers" | tr ',' '\n')
EOF
    [ "$tracked" -eq 1 ] || continue
    title=$(fm_tracker_b64_decode "$title_b64")
    fm_tracker_type_of_labels "$labels"
    type=$FM_TRACKER_TYPE
    [ -n "$type" ] || type='untyped'
    if [ "$still" -eq 0 ]; then
      names="$names$(printf '  #%-5s [%-11s] %s\n' "$number" "$type" "$title")
"
    else
      names="$names$(printf '  #%-5s [%-11s] %s - still has %s open blocker(s)\n' \
        "$number" "$type" "$title" "$still")
"
    fi
    : "$assignees" "$parent" "$body_b64" "$btype" "$bwho"
  done
  if [ -n "$names" ]; then
    printf 'depended on #%s:\n%s' "$closed" "$names"
  else
    printf 'nothing was blocked on #%s\n' "$closed"
  fi
}

# ---------------------------------------------------------------------------
# The task half: which repository a project's tickets live in
# ---------------------------------------------------------------------------
#
# A tracker repository is a per-project choice, not a fleet-wide one: most
# projects have no tracker at all, and the ones that do are not all on the same
# forge account. The mapping is local operating configuration for the same
# reason config/captain-github is - it names the captain's own repositories -
# so it lives in config/tracker-repos and is inherited by secondmate homes.
#
# One line per tracker, "<name>[,<alias>...] <owner/repo>". The name list is the
# project's alias set, and it is the SAME set the backlog's "(repo: <name>)"
# annotation is matched against: data/projects.md already registers a project
# under more than one spelling (video-editing-pilot and video_editing_pilot are
# one project), and a backlog row may use either. Matching one spelling only
# would leave half a project's work off the frontier while reporting the other
# half as the whole - the confident wrong answer this layer exists to prevent.
FM_TRACKER_PROJECT_CONFIG='tracker-repos'

# Resolve a project's tracker repository into FM_TRACKER_PROJECT_REPO, and that
# project's whole alias set into FM_TRACKER_PROJECT_NAMES, so a caller matching
# backlog rows never has to re-read the file to learn the other spellings.
# Returns non-zero when the project has no tracker; an absent file, an absent
# project, and a malformed line are all "no tracker", because having none is the
# ordinary state of a project and must never read as a failure.
#
# Globals rather than a printed value, and the reason is the same one the type
# count carries above: a command substitution runs in a subshell, so a caller
# that captured the repository would then read an empty alias set and match no
# backlog row at all.
FM_TRACKER_PROJECT_REPO=''
FM_TRACKER_PROJECT_NAMES=''
fm_tracker_repo_for_project() {  # <config-dir> <project-name>
  local dir=${1-} want=${2-} file line names spec n
  FM_TRACKER_PROJECT_REPO=
  FM_TRACKER_PROJECT_NAMES=
  [ -n "$dir" ] && [ -n "$want" ] || return 1
  file="$dir/$FM_TRACKER_PROJECT_CONFIG"
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [ -n "$line" ] || continue
    names=${line%%[[:space:]]*}
    spec=${line#*[[:space:]]}
    spec=${spec#"${spec%%[![:space:]]*}"}
    [ -n "$names" ] && [ -n "$spec" ] && [ "$names" != "$spec" ] || continue
    fm_tracker_repo_parse "$spec" || continue
    for n in ${names//,/ }; do
      if [ "$n" = "$want" ]; then
        FM_TRACKER_PROJECT_REPO=$spec
        FM_TRACKER_PROJECT_NAMES=${names//,/ }
        return 0
      fi
    done
  done < "$file"
  return 1
}

# ---------------------------------------------------------------------------
# Withholding a row from a board
# ---------------------------------------------------------------------------
#
# bin/fm-spawn.sh runs `sync` on EVERY dispatch. That is the property that makes
# the task half a mechanism rather than a convention, and it is also why a row
# kept off a board by hand does not stay off it: the next dispatch files it. A
# review that decides a row must not be published therefore has nowhere to put
# that decision, and it survives only as prose in whatever report noticed it.
#
# So the decision is recorded where sync reads it, once, with the reason it was
# taken. Reading the record is unconditional: sync consults it before it composes
# a title, so a withheld row's summary never reaches GitHub at all - which is the
# case that matters, because the summary IS the title and a title is the part a
# withhold is usually protecting.
#
# One record per line, "<project> <task-id> <reason>". Project and task id never
# contain a space, so the reason is simply the rest of the line and the file
# stays hand-editable. A "#" comment and a blank line are ignored.
FM_TRACKER_WITHHOLD_CONFIG='tracker-withhold'

# Resolve the record file, or refuse. Returns non-zero only on an unusable
# config directory; an absent file is the ordinary state and resolves fine.
fm_tracker_withhold_path() {  # <config-dir>
  local dir=${1-}
  FM_TRACKER_WITHHOLD_FILE=
  [ -n "$dir" ] || return 1
  FM_TRACKER_WITHHOLD_FILE="$dir/$FM_TRACKER_WITHHOLD_CONFIG"
}

# Print every record, one "<project> <task-id> <reason>" per line, filtered to
# <project-names> when that alias list is non-empty. Silent when there are none.
fm_tracker_withhold_lines() {  # <config-dir> [project-names]
  local dir=${1-} names=${2-} line proj rest
  fm_tracker_withhold_path "$dir" || return 0
  [ -f "$FM_TRACKER_WITHHOLD_FILE" ] && [ ! -L "$FM_TRACKER_WITHHOLD_FILE" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [ -n "$line" ] || continue
    proj=${line%%[[:space:]]*}
    rest=${line#*[[:space:]]}
    rest=${rest#"${rest%%[![:space:]]*}"}
    [ -n "$proj" ] && [ -n "$rest" ] && [ "$proj" != "$rest" ] || continue
    if [ -n "$names" ]; then
      fm_tracker_project_matches "$proj" "$names" || continue
    fi
    printf '%s %s\n' "$proj" "$rest"
  done < "$FM_TRACKER_WITHHOLD_FILE"
}

# True when this project's <task-id> is withheld, with the recorded reason in
# FM_TRACKER_WITHHOLD_REASON. An unstated reason reads as "no reason recorded"
# rather than as no record: the decision still stands, it is just undocumented.
FM_TRACKER_WITHHOLD_REASON=''
fm_tracker_withheld_reason() {  # <config-dir> <project-names> <task-id>
  local dir=${1-} names=${2-} want=${3-} line id rest
  FM_TRACKER_WITHHOLD_REASON=
  [ -n "$want" ] || return 1
  while IFS= read -r line; do
    rest=${line#*[[:space:]]}
    id=${rest%%[[:space:]]*}
    [ "$id" = "$want" ] || continue
    if [ "$rest" = "$id" ]; then
      FM_TRACKER_WITHHOLD_REASON='no reason recorded'
    else
      FM_TRACKER_WITHHOLD_REASON=${rest#*[[:space:]]}
      FM_TRACKER_WITHHOLD_REASON=${FM_TRACKER_WITHHOLD_REASON#"${FM_TRACKER_WITHHOLD_REASON%%[![:space:]]*}"}
      [ -n "$FM_TRACKER_WITHHOLD_REASON" ] || FM_TRACKER_WITHHOLD_REASON='no reason recorded'
    fi
    return 0
  done <<EOF
$(fm_tracker_withhold_lines "$dir" "$names")
EOF
  return 1
}

# Record one withhold, replacing any earlier record for the same project and
# task. Rewritten through a temporary file so an interrupted write cannot leave
# a half-line that would then be read as a different decision.
fm_tracker_withhold_record() {  # <config-dir> <project> <task-id> <reason>
  local dir=${1-} proj=${2-} task=${3-} reason=${4-} tmp
  [ -n "$proj" ] && [ -n "$task" ] && [ -n "$reason" ] || return 1
  case "$proj$task" in
    *[[:space:]]*) return 1 ;;
  esac
  # A newline would split one decision into two records, and a leading "#" would
  # turn the whole line into a comment the reader then ignores - a withhold that
  # silently stops applying is worse than one that refuses to be written.
  case $reason in
    *'
'*) return 1 ;;
    '#'*) return 1 ;;
  esac
  fm_tracker_withhold_path "$dir" || return 1
  [ -d "$dir" ] || return 1
  tmp=$(mktemp "$dir/.fm-tracker-withhold.XXXXXX") || return 1
  fm_tracker_withhold_drop_into "$dir" "$proj" "$task" "$tmp" || { rm -f "$tmp"; return 1; }
  printf '%s %s %s\n' "$proj" "$task" "$reason" >> "$tmp" || { rm -f "$tmp"; return 1; }
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$FM_TRACKER_WITHHOLD_FILE" || { rm -f "$tmp"; return 1; }
}

# Remove one withhold. Returns non-zero when there was nothing to remove, so a
# caller can say that rather than reporting a clearance that never happened.
fm_tracker_withhold_clear() {  # <config-dir> <project> <task-id>
  local dir=${1-} proj=${2-} task=${3-} tmp before after
  [ -n "$proj" ] && [ -n "$task" ] || return 1
  fm_tracker_withhold_path "$dir" || return 1
  [ -f "$FM_TRACKER_WITHHOLD_FILE" ] && [ ! -L "$FM_TRACKER_WITHHOLD_FILE" ] || return 1
  before=$(fm_tracker_withhold_lines "$dir" | grep -c . || true)
  tmp=$(mktemp "$dir/.fm-tracker-withhold.XXXXXX") || return 1
  fm_tracker_withhold_drop_into "$dir" "$proj" "$task" "$tmp" || { rm -f "$tmp"; return 1; }
  after=$(grep -c . "$tmp" || true)
  [ "$after" -lt "$before" ] || { rm -f "$tmp"; return 1; }
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$FM_TRACKER_WITHHOLD_FILE" || { rm -f "$tmp"; return 1; }
}

# Every record except this project's <task-id>, written to <dest>.
fm_tracker_withhold_drop_into() {  # <config-dir> <project> <task-id> <dest>
  local dir=$1 proj=$2 task=$3 dest=$4 line rest id
  : > "$dest" || return 1
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    rest=${line#*[[:space:]]}
    id=${rest%%[[:space:]]*}
    [ "${line%%[[:space:]]*}" = "$proj" ] && [ "$id" = "$task" ] && continue
    printf '%s\n' "$line" >> "$dest" || return 1
  done <<EOF
$(fm_tracker_withhold_lines "$dir")
EOF
  return 0
}

# ---------------------------------------------------------------------------
# Binding a ticket to a firstmate task
# ---------------------------------------------------------------------------
#
# A task ticket has to be findable from the task id alone, from any session, with
# no local record: the spawn that created it and the cleanup that closes it are
# different processes, days apart, and a local sidecar that goes missing would
# make the next dispatch open a SECOND ticket for the same work. The binding is
# therefore carried in the ticket body, where GitHub keeps it, and read back out
# of the same graph query the frontier already runs.
#
# It is an HTML comment so a reader never sees it, and it is exact-matched rather
# than pattern-matched, so a task id that is a prefix of another one cannot bind
# to its neighbour's ticket.
FM_TRACKER_TASK_MARKER_OPEN='<!-- fm-task: '
FM_TRACKER_TASK_MARKER_CLOSE=' -->'

fm_tracker_task_marker() {  # <task-id>
  printf '%s%s%s\n' "$FM_TRACKER_TASK_MARKER_OPEN" "${1-}" "$FM_TRACKER_TASK_MARKER_CLOSE"
}

# ---------------------------------------------------------------------------
# What a task ticket says
# ---------------------------------------------------------------------------
#
# The writer owns exactly TWO things in a task body - the marker line binding it
# to a firstmate task, and the canonical blocked-by section - and preserves
# everything else verbatim on every later pass.
#
# The narrow ownership is deliberate and was corrected by evidence. Owning the
# whole body is simpler and was the first design; the first live run then filed
# duplicates of task tickets that had already been written by hand, and those
# hand-written bodies turned out to carry the measurements, the captain's own
# words, and the definition of done for that work. A convergent rewrite would
# have deleted all of it. A mirror that destroys what someone wrote into it is
# not a mirror.
#
# NOTHING THE CALLER SUPPLIES IS EVER COMPOSED INTO A BODY, which is a different
# rule and still holds. A task's own description is written for firstmate, and
# firstmate's nouns include "blocked on", "depends on" and "waiting on" - the
# exact wording the prose-blocker guard refuses - so passing a task list note or
# a brief through as a body would make an ordinary dispatch fail to get a ticket
# because of how its summary happened to be worded. A brief also carries the
# captain's private strategy, and an issue body is public to everyone who can
# read the repository. The summary goes in the TITLE, which no guard reads and
# which says only what the work is called.
#
# So a body this script CREATES is its own short note, and a body it FINDS is the
# author's. The prose-blocker guard refuses what this script writes; a prose
# blocker already present in an author's body is reported by `validate`, which is
# the standing guard for exactly that, and never blocks the pass that would give
# the ticket its real edge.
#
# THE WORDING IS FOR WHOEVER READS THE BOARD, not for whoever files it. These
# tickets land in repositories other people work in, and a body that explains
# itself in this tool's own nouns - the fleet, the frontier, the captain - is
# noise on their board at best and reads as a leak at worst. Nothing here names
# an internal role, an internal id, or the tool that wrote it: the task binding
# is already carried invisibly by the marker line above.
# shellcheck disable=SC2016 # The backticks are markdown code spans, not shell.
fm_tracker_task_default_note() {  # <task-id> <kind> <project> [hold-note]
  local task=$1 kind=${2-} project=${3-} hold=${4-}
  : "$task"
  case "$kind" in
    scout) printf 'An investigation' ;;
    ship) printf 'A change' ;;
    *) printf 'A work item' ;;
  esac
  [ -z "$project" ] || printf ' in `%s`' "$project"
  printf '.\n\n'
  printf 'This issue mirrors one item of the working queue behind this repository, so\n'
  printf 'the board shows what is moving as well as where the project is going. The\n'
  printf 'detailed working record is kept outside this repository and is not replaced\n'
  printf 'by this issue.\n'
  if [ -n "$hold" ]; then
    printf '\n## On hold\n\n%s\n' "$hold"
  fi
}

# The one sentence a mechanically held ticket says about its own hold. The
# REASON deliberately does not travel: a hold reason is written for whoever runs
# the queue and routinely names private context, and this body is public to
# everyone who can read the repository.
FM_TRACKER_HOLD_NOTE='Not available to pick up yet. The reason is recorded in the working queue outside this repository.'

# Everything in a body that this script does not own: the marker line and the
# canonical blocked-by section removed, surrounding blank lines trimmed. Printing
# nothing means the body was ours alone and a fresh note replaces it.
fm_tracker_task_body_author_part() {  # <body>
  local body=${1-} line in_section=0 out=''
  while IFS= read -r line; do
    case $line in
      "$FM_TRACKER_TASK_MARKER_OPEN"*"$FM_TRACKER_TASK_MARKER_CLOSE") continue ;;
    esac
    if [ "$line" = "$FM_TRACKER_BLOCKED_HEADING" ]; then
      in_section=1
      continue
    fi
    if [ "$in_section" -eq 1 ]; then
      case $line in
        '#'*) in_section=0 ;;
        *) continue ;;
      esac
    fi
    out="$out$line
"
  done <<EOF
$body
EOF
  printf '%s' "$out" | awk '
    { lines[NR] = $0 }
    END {
      first = 1; last = NR
      while (first <= NR && lines[first] ~ /^[[:space:]]*$/) first++
      while (last >= first && lines[last] ~ /^[[:space:]]*$/) last--
      for (i = first; i <= last; i++) print lines[i]
    }'
}

# The one-line outcome recorded on a ticket as it closes. Kept here rather than
# at the call site so the shipped and unshipped wordings cannot drift apart, and
# so both stay clear of the prose-blocker vocabulary.
fm_tracker_task_outcome_comment() {  # <outcome> [pr-url] [detail]
  local outcome=$1 pr=${2-} detail=${3-}
  case "$outcome" in
    shipped) printf '**Shipped.** This work is finished.\n' ;;
    *) printf '**Closed without shipping.** This was closed before it landed.\n' ;;
  esac
  [ -z "$pr" ] || printf '\nPR: %s\n' "$pr"
  [ -z "$detail" ] || printf '\n%s\n' "$detail"
}

fm_tracker_outcome_valid() {  # <outcome>
  case "${1-}" in
    shipped|not-shipped) return 0 ;;
  esac
  return 1
}

# GitHub's own two closed states. "completed" and "not planned" render
# differently, so the distinction survives in the UI without a reader having to
# open the comment.
fm_tracker_outcome_state_reason() {  # <outcome>
  case "${1-}" in
    shipped) printf 'completed\n' ;;
    *) printf 'not_planned\n' ;;
  esac
}

# ---------------------------------------------------------------------------
# Reading the task half back out of the issue graph
# ---------------------------------------------------------------------------

# Print "<task-id> <number> <state> <parent> <assignees>" for every ticket
# carrying a task marker, from graph records on stdin. This is the whole index a
# writer needs: whether a task already has a ticket, whether that ticket is open,
# whether it hangs off the destination, and whether anyone holds it.
fm_tracker_task_index() {
  local number state labels assignees parent blockers title_b64 body_b64
  local body line id
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    [ -n "$number" ] || continue
    body=$(fm_tracker_b64_decode "$body_b64")
    id=
    while IFS= read -r line; do
      case $line in
        "$FM_TRACKER_TASK_MARKER_OPEN"*"$FM_TRACKER_TASK_MARKER_CLOSE")
          id=${line#"$FM_TRACKER_TASK_MARKER_OPEN"}
          id=${id%"$FM_TRACKER_TASK_MARKER_CLOSE"}
          break
          ;;
      esac
    done <<EOF
$body
EOF
    [ -n "$id" ] || continue
    printf '%s %s %s %s %s\n' "$id" "$number" "$state" "$parent" "${assignees// /,}"
    : "$labels" "$blockers" "$title_b64"
  done
}

# The destination every task ticket hangs off: the one OPEN fm:destination that
# is itself a root. Printed only when there is exactly ONE - zero means this
# repository was never initialised as a tracker, and more than one means the
# right parent is a judgement rather than a lookup. Both print nothing, because
# attaching to a guessed parent is worse than reporting that no attachment could
# be made: validate reports an orphan either way, and a wrong parent additionally
# rolls the work up under a destination it does not serve.
fm_tracker_destination_number() {
  local number state labels assignees parent blockers title_b64 body_b64
  local found='' count=0
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    [ -n "$number" ] || continue
    [ "$state" = OPEN ] || continue
    [ "$parent" = '-' ] || continue
    fm_tracker_type_of_labels "$labels"
    [ "$FM_TRACKER_TYPE_COUNT" -eq 1 ] && [ "$FM_TRACKER_TYPE" = destination ] || continue
    found=$number
    count=$((count + 1))
    : "$assignees" "$blockers" "$title_b64" "$body_b64"
  done
  [ "$count" -eq 1 ] || return 1
  printf '%s\n' "$found"
}

# Print "<number> <state> <parent> <assignees>" for one issue, from graph records
# on stdin - the same shape fm_tracker_task_index emits, so a ticket adopted by
# number enters the writer's index exactly like one found by its marker.
fm_tracker_index_row_for_number() {  # <number>
  local want=$1
  local number state labels assignees parent blockers title_b64 body_b64
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    : "$labels" "$blockers" "$title_b64" "$body_b64"
    [ "$number" = "$want" ] || continue
    printf '%s %s %s %s\n' "$number" "$state" "$parent" "${assignees// /,}"
    return 0
  done
  return 1
}

# Print the labels of one issue, from graph records on stdin. Used to decide
# whether a state label is already present, so a converged ticket is not written
# to again on every dispatch.
fm_tracker_labels_of() {  # <number>
  local want=$1
  local number state labels assignees parent blockers title_b64 body_b64
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    : "$state" "$assignees" "$parent" "$blockers" "$title_b64" "$body_b64"
    [ "$number" = "$want" ] || continue
    printf '%s\n' "$labels"
    return 0
  done
  return 1
}

# Print the body of one issue, from graph records on stdin. Used to decide
# whether a convergent rewrite would change anything, so a sync that changes
# nothing writes nothing.
fm_tracker_body_of() {  # <number>
  local want=$1
  local number state labels assignees parent blockers title_b64 body_b64
  while IFS=$'\037' read -r number state labels assignees parent blockers title_b64 body_b64; do
    : "$state" "$labels" "$assignees" "$parent" "$blockers" "$title_b64"
    [ "$number" = "$want" ] || continue
    fm_tracker_b64_decode "$body_b64"
    return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# The backlog rows a tracker mirrors
# ---------------------------------------------------------------------------
#
# The queue is read straight out of data/backlog.md rather than through
# tasks-axi. Both configured backends write that same file (docs/configuration.md
# "Backlog backend"), so reading it works under either, and it keeps a missing or
# broken tasks-axi from being able to stop a dispatch.
#
# WHAT IS DELIBERATELY NOT MIRRORED. Three kinds of row are skipped, and each
# skip is a decision rather than a limitation:
#
#   kind: captain          A captain-held question is a decision, not a task.
#                          The decision half of this tracker already has its own
#                          type and its own answerable-cold contract; pushing
#                          these through the task path would file them as work
#                          nobody can do and strip the structure that makes them
#                          answerable.
#   hold-kind: future      Deliberately parked, and parked rows are parked FROM
#                          the captain as much as from the fleet. Surfacing one
#                          on a board the captain reads is re-raising it.
#   hold-kind: external    Rows that exist only to give a standing mechanism a
#                          durable identity. They are not work and never finish.
#
# Emits one record per eligible row, US-separated:
#   <task-id>  <kind>  <repo>  <hold-kind>  <blocked-by-csv>  <section>  <summary>
# where section is "flight" or "queued".
fm_tracker_backlog_rows() {  # <backlog-file>
  local file=${1-}
  [ -f "$file" ] && [ ! -L "$file" ] || return 1
  awk -v sep=$'\037' '
    /^## / {
      section = ""
      if ($0 == "## In flight") section = "flight"
      else if ($0 == "## Queued") section = "queued"
      next
    }
    section == "" { next }
    /^- \[[ xX]\] / {
      line = $0
      done_row = (substr(line, 4, 1) != " ")
      if (done_row) next
      rest = substr(line, 7)
      sp = index(rest, " - ")
      if (sp == 0) next
      id = substr(rest, 1, sp - 1)
      rest = substr(rest, sp + 3)

      kind = ""; repo = ""; holdkind = ""
      if (match(line, /\(kind: [^)]*\)/)) kind = substr(line, RSTART + 7, RLENGTH - 8)
      if (match(line, /\(repo: [^)]*\)/)) repo = substr(line, RSTART + 7, RLENGTH - 8)
      if (match(line, /\(hold-kind: [^)]*\)/)) holdkind = substr(line, RSTART + 12, RLENGTH - 13)

      if (kind != "ship" && kind != "scout") next
      if (holdkind == "future" || holdkind == "external") next
      if (repo == "") next

      # The summary is everything before the annotation run; every row carries
      # (repo: ...), so that is where the run starts.
      cut = index(rest, " (repo: ")
      if (cut > 0) rest = substr(rest, 1, cut - 1)

      # A dependency is written into the summary as "blocked-by: <ids>". Lifting
      # it out here is what lets the writer turn it into a real queryable edge
      # instead of shipping the words to GitHub, where they would be exactly the
      # prose blocker this layer refuses.
      #
      # A row may declare more than one, and each is its own annotation. Lifting
      # only the first left the second in the summary - and the summary IS the
      # ticket title - so an internal task id reached a title other people read
      # and the edge it named was silently lost.
      blocked = ""
      while (match(rest, / blocked-by: [^ ]+/)) {
        one = substr(rest, RSTART + 13, RLENGTH - 13)
        blocked = blocked (blocked == "" ? "" : ",") one
        rest = substr(rest, 1, RSTART - 1) substr(rest, RSTART + RLENGTH)
      }
      gsub(/^[ \t]+|[ \t]+$/, "", rest)
      if (rest == "") next
      print id sep kind sep repo sep holdkind sep blocked sep section sep rest
    }
  ' "$file"
}

# True when a backlog row's "(repo: <name>)" names this project. The alias set
# comes from the tracker configuration, which is the same list data/projects.md
# registers the project under.
fm_tracker_project_matches() {  # <row-repo> <alias-list>
  local want=${1-} names=${2-} n
  [ -n "$want" ] || return 1
  for n in $names; do
    [ "$n" = "$want" ] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# A bound on the one call a dispatch makes
# ---------------------------------------------------------------------------
#
# The tracker step runs on the spawn path, and a spawn must survive GitHub being
# unreachable. An error is easy - it returns and the dispatch reports a missing
# ticket. A HANG is the dangerous case: a blackholed connection returns nothing
# and never fails, and an unbounded call there would wedge the dispatch just as
# completely as a refusal would, which is the worse defect this must not trade
# for the lesser one.
#
# Same three-runner ladder as bin/fm-agy-lib.sh's probe bound, and for the same
# reason: a stock macOS has neither timeout nor gtimeout, and perl ships with
# every platform the fleet runs on. With no runner at all the call is DECLINED
# rather than run unbounded - a declined tracker step costs a missing ticket,
# and an unbounded one costs the dispatch.
FM_TRACKER_DISPATCH_TIMEOUT=${FM_TRACKER_DISPATCH_TIMEOUT:-45}

fm_tracker_run_bounded() {  # <seconds> <command> [args...]
  local secs=$1 runner=none
  shift
  case "$secs" in
    ''|*[!0-9]*|0) return 125 ;;
  esac
  if command -v timeout >/dev/null 2>&1; then runner=timeout
  elif command -v gtimeout >/dev/null 2>&1; then runner=gtimeout
  elif command -v perl >/dev/null 2>&1; then runner=perl
  fi
  case "$runner" in
    timeout|gtimeout) "$runner" "$secs" "$@" </dev/null ;;
    perl)
      perl -e 'my $t = shift; my $pid = fork; die "fork failed" unless defined $pid; if (!$pid) { setpgrp(0, 0); exec @ARGV } local $SIG{ALRM} = sub { kill "TERM", -$pid; select undef, undef, undef, 0.2; kill "KILL", -$pid; exit 124 }; alarm $t; waitpid $pid, 0; exit($? >> 8)' \
        "$secs" "$@" </dev/null
      ;;
    *) return 125 ;;
  esac
}
