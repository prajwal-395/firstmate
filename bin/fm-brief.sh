#!/usr/bin/env bash
# Scaffold a crewmate brief or persistent secondmate charter at
# data/<task-id>/brief.md under the active firstmate home.
# For ordinary tasks, the standard Setup/Rules/Definition-of-done contract is
# filled in. Ship and scout `# Task` sections have two subsections Firstmate
# fills before dispatch: `{TASK}` under `## Captain's intent` (the captain's
# own ask plus the context needed to read it, including the substance of any
# report, decision, or PR the ask refers to) and `{FIRSTMATE_SPEC}`
# under `## Firstmate spec` (build instructions, which are never the captain's
# intent). bin/fm-dod-lib.sh owns the no-mistakes `--intent` contract those
# subsections feed; bin/fm-spawn.sh refuses leftover placeholders. Secondmate
# charters still use a single `{TASK}` charter fill. Firstmate may adjust other
# sections when the task genuinely deviates (e.g. working an existing external
# PR instead of shipping a new one).
# Usage: fm-brief.sh <task-id> <repo-name> --mode <no-mistakes|direct-PR|local-only> [--from-task[=<record-id>]] [--herdr-lab]
#        fm-brief.sh <task-id> <repo-name> --scout [--from-task[=<record-id>]] [--herdr-lab]
#        fm-brief.sh <task-id> --secondmate {<project>...|--no-projects}
#        fm-brief.sh <task-id> --check
#   --check reports whether an already-filled brief is ready to dispatch: it
#   refuses while a Task placeholder below is still unfilled, while either
#   Task subsection below is still empty, and while the filled subsections
#   widen the ask against the scope-discipline contract. bin/fm-spawn.sh and
#   bin/fm-promote.sh run the identical gates before launching, so a brief
#   this reports ready is a brief the spawn accepts. bin/fm-dod-lib.sh owns
#   every gate and its wording.
#   --scout writes the scout contract instead: the deliverable is a report at
#   data/<task-id>/report.md (no branch, no push, no PR) and the worktree is scratch.
#   It offers the Lavish review loop only when `fm-bootstrap.sh lavish-compatible`
#   confirms the supported lavish-axi floor; otherwise it asks for a text report.
#   --secondmate writes a persistent secondmate charter. The project list
#   is cloned into the secondmate home, while the natural-language scope
#   tells the main firstmate when to route work there; routine churn stays in its own home;
#   captain-relevant escalations and marked from-firstmate replies append to this
#   home's status file.
#   --no-projects writes a project-less charter for a domain whose subject is the
#   firstmate repo itself (its home is a firstmate worktree, its crews take pooled
#   worktrees of the same repo). It is mutually exclusive with a project list, and
#   omitting both still fails loudly so an accidental omission is never silent.
#   Set FM_SECONDMATE_CHARTER='<charter>' to fill the charter text.
#   Set FM_SECONDMATE_SCOPE='<scope>' to write a routing scope distinct from the charter text.
#   --from-task renders the two `# Task` subsections from the task record
#   instead of leaving `{TASK}` / `{FIRSTMATE_SPEC}` placeholders for hand
#   editing. A bare `--from-task` renders from the record with the same id;
#   `--from-task=<other-id>` renders from a named record. There is no
#   space-separated form: it would be ambiguous with the positional task id.
#   The record is read through bin/fm-tasks-axi.sh show, so any configured
#   backlog backend works; only the record's existing title and free-form
#   body are used, never a tasks-axi field that package does not own.
#   Filing convention (compose once at filing, render mechanically): file the
#   body with captain material under a `## Captain's intent` heading (or a
#   `Captain's intent:` / `Captain intent:` label line) and build material
#   under `## Firstmate spec` (or a `Firstmate spec:` label); everything
#   outside the intent block renders into the spec, so nothing filed is
#   lost. Bodies without those markers fall back to a paragraph heuristic:
#   paragraphs carrying captain provenance (`captain`, `verbatim`, or `their
#   words`) render into the intent alongside the title, and the full body
#   renders into the spec. A body with no such paragraph renders the title
#   alone as the intent with a warning, for firstmate to enrich by hand.
#   Render is the default path, not the only path: omitting --from-task
#   scaffolds today's placeholders for free-form hand editing, and a
#   rendered brief stays hand-editable exactly as a hand-filled one.
#   bin/fm-spawn.sh and bin/fm-promote.sh still refuse leftover placeholders
#   and empty subsections after either path. The scaffold still needs its
#   explicit --mode (ship) and repo: render never infers the delivery
#   contract from the record. --from-task is refused on --secondmate
#   charters, whose charter path is untouched. An unknown record, an empty
#   body, or a body with no spec material refuses loudly and writes no
#   brief; a missing tasks-axi names the hand-fill fallback.
#   --herdr-lab is mandatory when the task will issue Herdr lifecycle commands.
#   It adds the hard isolation contract backed by bin/fm-herdr-lab.sh.
#   The flag must be explicit because {TASK} and {FIRSTMATE_SPEC} are filled
#   after scaffolding and the caller-supplied repo string cannot reliably
#   identify this repo. Briefs made without it carry a loud declaration so an
#   omitted contract cannot be silent.
# For ship tasks, --mode is REQUIRED and shapes the definition of done. Firstmate
# resolves it per task at intake (AGENTS.md section 7); data/projects.md holds the
# captain's standing posture as context, and this script never reads it:
#   no-mistakes  implement -> /no-mistakes pipeline -> PR -> configured merge authority
#   direct-PR    implement -> push + open PR via gh-axi (no pipeline) -> configured merge authority
#   local-only   implement on branch, stop and report "ready in branch" (no push/PR);
#                the configured merge authority approves, firstmate merges to local main
# no-mistakes-prod-only is a registry policy, not a task mode; resolve it to one of
# the three concrete modes at intake before calling this script.
# The generated ship brief records the chosen mode as a fixed machine-readable
# "Delivery contract: mode=<mode>" line. bin/fm-spawn.sh reads that line and refuses
# to launch a ship task whose explicit --mode disagrees, so an adjusted brief and the
# recorded task metadata cannot drift apart.
# Ship briefs begin with a worktree-isolation assertion before the branch step.
# The ship Setup then names the verified worktree as the only editable workspace
# and firstmate's home as not the workspace, so a home path never reads as a
# second workplace; the project-memory helper is named once for the same reason.
# --mode is refused on scout and secondmate scaffolds: a scout's deliverable is a
# report rather than a merge, and a charter is not a delivery contract.
# There is no --yolo flag here. The worker never owns merge decisions, so yolo is
# a spawn-time and firstmate-side input only (AGENTS.md section 7).
# Every scaffold's status protocol distinguishes the configured
# declared-external-wait verb (FM_CLASSIFY_PAUSED_VERB, default "paused") from
# "blocked:": pause for a known external wait expected to clear on its own,
# blocked when firstmate must act.
# Every scaffold also carries the steering-inbox receive-and-ack section:
# process state/<id>.inbox/*.msg in order and acknowledge each by moving it to
# handled/ (record, doorbell, and ladder owned by bin/fm-task-inbox-lib.sh).
# Ship tasks include a project-memory section so durable project-intrinsic
# learnings can be committed to AGENTS.md through the project's delivery path;
# it carries the AGENTS.md authoring bar (widely useful knowledge only, pointers
# over copied detail) and defers self-governance recognition and insertion to
# fm-ensure-agents-md.sh's contract.
# Scaffolds carry no role scope: fm-spawn.sh supplies fm_brief_worker_role from
# fm-dod-lib.sh to every ship/scout launch brief, so this file never becomes a
# second owner of a contract that must stay current across relaunches.
# Refuses to overwrite an existing brief.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

# shellcheck source=bin/fm-marker-lib.sh
. "$SCRIPT_DIR/fm-marker-lib.sh"
# shellcheck source=bin/fm-classify-lib.sh
. "$SCRIPT_DIR/fm-classify-lib.sh"
# shellcheck source=bin/fm-dod-lib.sh
. "$SCRIPT_DIR/fm-dod-lib.sh"
PAUSED_VERB=${FM_CLASSIFY_PAUSED_VERB:-$FM_CLASSIFY_PAUSED_VERB_DEFAULT}

resolve_directory_input() {
  local name=$1 path=$2 resolved
  case "$path" in
    /*) printf '%s\n' "$path"; return 0 ;;
  esac
  resolved=$(CDPATH='' cd -- "$path" 2>/dev/null && pwd -P) || {
    echo "error: $name directory cannot be resolved: $path" >&2
    return 1
  }
  printf '%s\n' "$resolved"
}

FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME=$(resolve_directory_input FM_HOME "${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}") || exit 1
if [ -n "${FM_DATA_OVERRIDE:-}" ]; then
  DATA=$(resolve_directory_input FM_DATA_OVERRIDE "$FM_DATA_OVERRIDE") || exit 1
else
  DATA="$FM_HOME/data"
fi
if [ -n "${FM_STATE_OVERRIDE:-}" ]; then
  STATE=$(resolve_directory_input FM_STATE_OVERRIDE "$FM_STATE_OVERRIDE") || exit 1
else
  STATE="$FM_HOME/state"
fi
KIND=ship
HERDR_LAB=0
NO_PROJECTS=0
CHECK=0
FROM_TASK=0
FROM_TASK_ID=
MODE=
MODE_SET=0
POS=()
want_value=
for a in "$@"; do
  if [ -n "$want_value" ]; then
    case "$a" in
      --*) echo "error: --$want_value requires a value" >&2; exit 1 ;;
    esac
    case "$want_value" in
      mode) MODE=$a; MODE_SET=1 ;;
      *) echo "error: internal parser state for --$want_value" >&2; exit 1 ;;
    esac
    want_value=
    continue
  fi
  case "$a" in
    --scout) KIND=scout ;;
    --secondmate) KIND=secondmate ;;
    --check) CHECK=1 ;;
    --herdr-lab) HERDR_LAB=1 ;;
    --no-projects) NO_PROJECTS=1 ;;
    --mode) want_value=mode ;;
    --mode=*) MODE=${a#--mode=}; MODE_SET=1 ;;
    --from-task) FROM_TASK=1 ;;
    --from-task=*) FROM_TASK=1; FROM_TASK_ID=${a#--from-task=} ;;
    # yolo never reaches the worker: it is firstmate's merge authority, not a
    # brief input. Refuse it loudly so it is never silently dropped here and then
    # believed to have been recorded.
    --yolo|--yolo=*) echo "error: --yolo is not a brief input; pass it to bin/fm-spawn.sh, which records the task's merge posture" >&2; exit 1 ;;
    *) POS+=("$a") ;;
  esac
done
[ -z "$want_value" ] || { echo "error: --$want_value requires a value" >&2; exit 1; }

# --check inspects a brief that already exists, so it takes neither a delivery
# mode nor a scaffold kind. bin/fm-dod-lib.sh owns every verdict and its
# wording; this branch only resolves the path and reports the ready case. The
# three gates run in the same order as bin/fm-spawn.sh: unfilled placeholders
# first, then empty subsections, then the scope-discipline contract.
if [ "$CHECK" -eq 1 ]; then
  [ "$MODE_SET" -eq 0 ] || { echo "error: --check inspects an existing brief and takes no --mode" >&2; exit 1; }
  [ "$KIND" = ship ] || { echo "error: --check inspects an existing brief and takes no --scout or --secondmate" >&2; exit 1; }
  [ "$HERDR_LAB" -eq 0 ] || { echo "error: --check inspects an existing brief and takes no --herdr-lab" >&2; exit 1; }
  [ "$NO_PROJECTS" -eq 0 ] || { echo "error: --check inspects an existing brief and takes no --no-projects" >&2; exit 1; }
  [ "$FROM_TASK" -eq 0 ] || { echo "error: --check inspects an existing brief and takes no --from-task" >&2; exit 1; }
  [ "${#POS[@]}" -eq 1 ] || { echo "error: --check takes exactly one task id" >&2; exit 1; }
  CHECK_BRIEF="$DATA/${POS[0]}/brief.md"
  [ -f "$CHECK_BRIEF" ] || { echo "error: no brief at $CHECK_BRIEF" >&2; exit 1; }
  if fm_brief_task_placeholders_present "$CHECK_BRIEF"; then
    echo "error: $CHECK_BRIEF still contains {TASK} or {FIRSTMATE_SPEC}; fill ## Captain's intent and ## Firstmate spec before dispatch" >&2
    exit 1
  fi
  if ! fm_brief_task_content_valid "$CHECK_BRIEF"; then
    echo "error: $CHECK_BRIEF must contain nonempty ## Captain's intent and ## Firstmate spec subsections (or a nonempty legacy # Task body) before dispatch" >&2
    exit 1
  fi
  fm_brief_scope_check "$CHECK_BRIEF" "dispatch" || exit 1
  echo "ready: $CHECK_BRIEF (intent and spec are filled and scope-disciplined)"
  exit 0
fi

# Ship delivery mode is an explicit per-task decision (AGENTS.md section 7). A
# missing or invalid value stops the scaffold rather than silently defaulting.
if [ "$KIND" = ship ]; then
  [ "$MODE_SET" -eq 1 ] || {
    echo "error: ship briefs require --mode <no-mistakes|direct-PR|local-only>; resolve it at intake from the captain's instruction and the project's registered posture in data/projects.md" >&2
    exit 1
  }
  case "$MODE" in
    no-mistakes|direct-PR|local-only) ;;
    no-mistakes-prod-only)
      echo "error: no-mistakes-prod-only is a registry policy, not a task mode; classify this task's surface and resolve it to no-mistakes or direct-PR at intake" >&2
      exit 1 ;;
    *) echo "error: --mode must be one of no-mistakes, direct-PR, local-only (got '$MODE')" >&2; exit 1 ;;
  esac
elif [ "$MODE_SET" -eq 1 ]; then
  echo "error: --mode applies only to ship briefs; a scout delivers a report and a secondmate charter is not a delivery contract" >&2
  exit 1
fi
ID=${POS[0]}

if [ "$KIND" = secondmate ] && [ "$HERDR_LAB" -eq 1 ]; then
  echo "error: --herdr-lab applies only to crewmate ship or scout briefs" >&2
  exit 1
fi

if [ "$NO_PROJECTS" -eq 1 ] && [ "$KIND" != secondmate ]; then
  echo "error: --no-projects applies only to --secondmate charters" >&2
  exit 1
fi

if [ "$FROM_TASK" -eq 1 ] && [ "$KIND" = secondmate ]; then
  echo "error: --from-task applies only to ship or scout briefs; a secondmate charter is filled from FM_SECONDMATE_CHARTER, not a task record" >&2
  exit 1
fi

if [ "$FROM_TASK" -eq 1 ] && [ -n "$FROM_TASK_ID" ]; then
  case "$FROM_TASK_ID" in
    *[!A-Za-z0-9-]*|"") echo "error: --from-task=<record-id> requires a non-empty task id; use a bare --from-task for the brief's own id" >&2; exit 1 ;;
  esac
fi

# Render helpers for --from-task. The record is read through
# bin/fm-tasks-axi.sh show (any configured backend); the title/body split
# below parses the record's existing free-form body conventions, never a
# tasks-axi field that package does not own. All multi-line programs are
# single-quoted awk arguments, never heredocs in command substitutions
# (tests/fm-brief.test.sh owns that Bash 3.2 parse-safety guard).
RENDER_TMP=
render_cleanup() {
  if [ -n "$RENDER_TMP" ] && [ -d "$RENDER_TMP" ]; then
    rm -rf "$RENDER_TMP"
  fi
}
# Parse `tasks-axi show --full` output: write the title to $TITLE_OUT and the
# body to $BODY_IN. Exits 2/3 when the title/body field is absent; the
# caller owns the diagnostic. Quoted scalars use \" \\ \n \t \r escapes.
fm_brief_show_parse() {
  awk '
    function unescape(s,   out, i, n, c) {
      out = ""
      i = 1
      n = length(s)
      while (i <= n) {
        c = substr(s, i, 1)
        if (c == "\\" && i < n) {
          i++
          c = substr(s, i, 1)
          if (c == "n") out = out "\n"
          else if (c == "t") out = out "\t"
          else if (c == "r") out = out "\r"
          else if (c == "\\" || c == "\"") out = out c
          else out = out "\\" c
        } else {
          out = out c
        }
        i++
      }
      return out
    }
    function scalar(value) {
      if (value == "-" || value == "\"-\"") return ""
      if (substr(value, 1, 1) == "\"" && substr(value, length(value), 1) == "\"" && length(value) >= 2) {
        return unescape(substr(value, 2, length(value) - 2))
      }
      return value
    }
    /^  title: / { title = scalar(substr($0, 10)); have_title = 1; next }
    /^  body: / { body = scalar(substr($0, 9)); have_body = 1; next }
    END {
      if (!have_title) exit 2
      if (!have_body) exit 3
      printf "%s", title > title_out
      printf "%s", body > body_out
    }
  ' "title_out=$TITLE_OUT" "body_out=$BODY_IN" "$SHOW_OUT"
}
# Split the record body in $BODY_IN into an intent part ($INTENT_OUT) and a
# spec part ($SPEC_OUT). Explicit markers win: a `## Captain's intent`
# heading opens an intent block that a `## Firstmate spec` heading (or a
# `Firstmate spec:` label) closes, and a `Captain's intent:` /
# `Captain intent:` / `Captain-approved ...:` / `Captain ruled ...:` /
# `..., verbatim:` label (even mid-line, with any text before it staying in
# the spec) contributes its paragraph. Paragraphs carrying captain
# provenance (`captain`, `verbatim`, or `their words`) join the intent in
# either mode. The spec keeps everything outside explicit intent regions,
# so nothing filed is ever dropped by the render.
fm_brief_body_split() {
  awk '
    function seg(text, sec) {
      sub(/[ \t]+$/, "", text)
      if (text == "" && sec != "gap") return
      nseg++
      ST[nseg] = text
      SS[nseg] = sec
    }
    function cur() { return (in_heading || in_label_para ? "intent" : "spec") }
    {
      folded = tolower($0)
      if (folded ~ /^##+[ \t]+firstmate[ \t]+spec[ \t]*$/) {
        seen_marker = 1
        in_heading = 0
        in_label_para = 0
        next
      }
      if (folded ~ /^##+[ \t]+captain('\''s)?[ \t]+intent[ \t]*$/) {
        seen_marker = 1
        in_heading = 1
        in_label_para = 0
        next
      }
      if ($0 ~ /^[ \t]*$/) {
        in_label_para = 0
        seg("", "gap")
        next
      }
      if (match(folded, /firstmate[ \t]+spec[ \t]*:/)) {
        seen_marker = 1
        prefix = substr($0, 1, RSTART - 1)
        rest = substr($0, RSTART + RLENGTH)
        sub(/^[ \t]+/, "", rest)
        if (prefix ~ /[^ \t]/) seg(prefix, cur())
        in_heading = 0
        in_label_para = 0
        if (rest ~ /[^ \t]/) seg(rest, "spec")
        next
      }
      if (match(folded, /captain('\''s|[- ]approved)?[^:]*:/)) {
        prefix = substr($0, 1, RSTART - 1)
        marker_at = 0
        if (prefix ~ /^[ \t]*$/) marker_at = 1
        else if (prefix ~ /^##+[ \t]*$/) marker_at = 1
        else if (prefix ~ /(^|[ \t])-[ \t]+$/) marker_at = 1
        else if (prefix ~ /[.!?;][ \t]+$/) marker_at = 1
        else if (prefix ~ /\([ \t]*$/) marker_at = 1
        if (marker_at) {
          seen_marker = 1
          rest = substr($0, RSTART)
          sub(/^[ \t]+/, "", rest)
          if (prefix ~ /[^ \t]/) seg(prefix, cur())
          in_heading = 0
          in_label_para = 1
          if (rest ~ /[^ \t]/) seg(rest, "intent")
          next
        }
      }
      seg($0, cur())
    }
    END {
      a = 1
      while (a <= nseg) {
        while (a <= nseg && ST[a] == "") a++
        if (a > nseg) break
        b = a
        while (b + 1 <= nseg && ST[b + 1] != "") b++
        t = ""
        for (k = a; k <= b; k++) t = (t == "" ? ST[k] : t "\n" ST[k])
        t = tolower(t)
        prov = (t ~ /captain/ || t ~ /verbatim/ || t ~ /their words/)
        has_intent = 0
        for (k = a; k <= b; k++) if (SS[k] == "intent") has_intent = 1
        for (k = a; k <= b; k++) {
          if (SS[k] == "spec") {
            SSEL[k] = 1
            if (prov && !has_intent) ISEL[k] = 1
          } else {
            ISEL[k] = 1
          }
        }
        a = b + 1
      }
      out = ""
      pend = 0
      for (m = 1; m <= nseg; m++) {
        t = ST[m]
        if (t == "") { if (out != "") pend = 1; continue }
        if (!ISEL[m]) { if (out != "") pend = 1; continue }
        if (pend) { out = out "\n"; pend = 0 }
        out = (out == "" ? t : out "\n" t)
      }
      printf "%s", out > intent_out
      out = ""
      pend = 0
      for (m = 1; m <= nseg; m++) {
        t = ST[m]
        if (t == "") { if (out != "") pend = 1; continue }
        if (!SSEL[m]) { if (out != "") pend = 1; continue }
        if (pend) { out = out "\n"; pend = 0 }
        out = (out == "" ? t : out "\n" t)
      }
      printf "%s", out > spec_out
    }
  ' "intent_out=$INTENT_OUT" "spec_out=$SPEC_OUT" "$BODY_IN"
}

# Splice the rendered sections into a scaffolded brief by replacing the exact
# placeholder lines. Content passes through awk print, never a shell
# expansion, so backticks, dollar signs, and quotes in the record survive.
fm_brief_apply_render() {
  awk -v intent_f="$INTENT_BODY_FILE" -v spec_f="$SPEC_BODY_FILE" '
    $0 == "{TASK}" { while ((getline line < intent_f) > 0) print line; next }
    $0 == "{FIRSTMATE_SPEC}" { while ((getline line < spec_f) > 0) print line; next }
    { print }
  ' "$BRIEF" > "$BRIEF.rendered" || return 1
  mv "$BRIEF.rendered" "$BRIEF" || return 1
  if grep -qxF -e '{TASK}' -e '{FIRSTMATE_SPEC}' "$BRIEF"; then
    echo "error: internal failure: rendered brief still carries a placeholder fill site" >&2
    return 1
  fi
}

BRIEF="$DATA/$ID/brief.md"
[ -e "$BRIEF" ] && { echo "error: $BRIEF already exists" >&2; exit 1; }

# --from-task renders before anything is written, so every refusal below
# leaves no half-written brief behind.
RENDERED=0
RECORD_ID=
INTENT_BODY_FILE=
SPEC_BODY_FILE=
if [ "$FROM_TASK" -eq 1 ]; then
  trap render_cleanup EXIT
  RECORD_ID=${FROM_TASK_ID:-$ID}
  [ -n "$RECORD_ID" ] || { echo "error: --from-task needs a task id: pass a brief task id or --from-task=<record-id>" >&2; exit 1; }
  command -v tasks-axi >/dev/null 2>&1 || {
    echo "error: cannot render from task record $RECORD_ID: tasks-axi is not on PATH; scaffold without --from-task and fill ## Captain's intent and ## Firstmate spec by hand" >&2
    exit 1
  }
  RENDER_TMP=$(mktemp -d "${TMPDIR:-/tmp}/fm-brief-from-task.XXXXXX") || { echo "error: cannot create a render workspace" >&2; exit 1; }
  SHOW_OUT="$RENDER_TMP/show.txt"
  TITLE_OUT="$RENDER_TMP/title.txt"
  BODY_IN="$RENDER_TMP/body.txt"
  INTENT_OUT="$RENDER_TMP/intent.txt"
  SPEC_OUT="$RENDER_TMP/spec.txt"
  # tasks-axi reports failures on stdout, so capture the stream first and only
  # then publish it for parsing.
  SHOW_CAPTURED=$("$SCRIPT_DIR/fm-tasks-axi.sh" show "$RECORD_ID" --full 2>&1) || {
    [ -n "$SHOW_CAPTURED" ] && printf '%s\n' "$SHOW_CAPTURED" >&2
    echo "error: cannot render from task record $RECORD_ID: the record could not be read; compose ## Captain's intent and ## Firstmate spec by hand" >&2
    exit 1
  }
  printf '%s\n' "$SHOW_CAPTURED" >"$SHOW_OUT"
  TITLE_OUT="$TITLE_OUT" BODY_IN="$BODY_IN" SHOW_OUT="$SHOW_OUT" fm_brief_show_parse || {
    status=$?
    case "$status" in
      2) echo "error: cannot render from task record $RECORD_ID: the record has no title field" >&2 ;;
      3) echo "error: cannot render from task record $RECORD_ID: the record has no body field" >&2 ;;
      *) echo "error: cannot render from task record $RECORD_ID: the record could not be parsed" >&2 ;;
    esac
    exit 1
  }
  [ -s "$BODY_IN" ] || {
    echo "error: cannot render from task record $RECORD_ID: its body is empty; compose the intent and spec at filing, or fill ## Captain's intent and ## Firstmate spec by hand" >&2
    exit 1
  }
  INTENT_OUT="$INTENT_OUT" SPEC_OUT="$SPEC_OUT" BODY_IN="$BODY_IN" fm_brief_body_split || {
    echo "error: cannot render from task record $RECORD_ID: its body could not be split into intent and spec" >&2
    exit 1
  }
  [ -s "$SPEC_OUT" ] || {
    echo "error: cannot render from task record $RECORD_ID: its body carries no spec material outside ## Captain's intent; file the build instructions, or fill ## Firstmate spec by hand" >&2
    exit 1
  }
  RECORD_SAFE=$(printf '%s' "$RECORD_ID" | tr -d '"')
  PROVENANCE="<!-- Rendered mechanically from task record \"$RECORD_SAFE\" by bin/fm-brief.sh --from-task; hand edits after render are authoritative. -->"
  INTENT_BODY_FILE="$RENDER_TMP/intent-body.txt"
  SPEC_BODY_FILE="$RENDER_TMP/spec-body.txt"
  if [ -s "$TITLE_OUT" ]; then
    RENDER_TITLE=$(cat "$TITLE_OUT")
  else
    RENDER_TITLE="$RECORD_ID"
  fi
  {
    printf '%s\n' "$RENDER_TITLE"
    if [ -s "$INTENT_OUT" ]; then
      printf '\n'
      cat "$INTENT_OUT"
      printf '\n'
    else
      printf '\n'
      echo "warning: task record $RECORD_ID carries no captain-provenance paragraph; ## Captain's intent renders the title alone - enrich it by hand when the ask needs more" >&2
    fi
    printf '%s\n' "$PROVENANCE"
  } > "$INTENT_BODY_FILE"
  {
    cat "$SPEC_OUT"
    printf '\n%s\n' "$PROVENANCE"
  } > "$SPEC_BODY_FILE"
  RENDERED=1
fi
mkdir -p "$DATA/$ID"

ASK_USER_BLOCK=
if [ "$KIND" = ship ] && [ "$MODE" = no-mistakes ]; then
  ASK_USER_BLOCK=$(fm_ask_user_escalation_block "$DATA" "$ID")
fi

shell_quote() {
  printf "'"
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

STATUS_FILE=$(shell_quote "$STATE/$ID.status")
INBOX_DIR=$(shell_quote "$STATE/$ID.inbox")

# The receive-and-ack half of the steering-inbox contract, included in every
# scaffold kind. The record format, doorbell line, and re-ring ladder are
# owned by bin/fm-task-inbox-lib.sh; the doorbell itself is self-describing,
# so this section is reinforcement for the natural-checkpoint habit, not the
# only carrier of the instruction.
IFS= read -r -d '' INBOX_SECTION <<EOF || true
# Firstmate instruction inbox
Firstmate steers you through durable message files in $INBOX_DIR.
When a terminal message says an instruction is waiting there - and at any natural checkpoint when you are unsure - list $INBOX_DIR/*.msg, read and act on each message in numeric order, then acknowledge each handled message by moving it: \`mv $INBOX_DIR/NNN.msg $INBOX_DIR/handled/\`.
The move IS the acknowledgement: without it firstmate rings again and eventually treats you as stuck. An empty or absent inbox needs no action.
EOF
INBOX_SECTION=${INBOX_SECTION%$'\n'}

if [ "$KIND" = secondmate ]; then
SECONDMATE_PROJECTS=""
idx=1
while [ "$idx" -lt "${#POS[@]}" ]; do
  SECONDMATE_PROJECTS="${SECONDMATE_PROJECTS}${SECONDMATE_PROJECTS:+ }${POS[$idx]}"
  idx=$((idx + 1))
done
if [ "$NO_PROJECTS" -eq 1 ]; then
  [ -z "$SECONDMATE_PROJECTS" ] || { echo "error: --no-projects cannot be combined with a project list" >&2; exit 1; }
else
  [ -n "$SECONDMATE_PROJECTS" ] || { echo "error: --secondmate requires at least one project, or --no-projects for a project-less home" >&2; exit 1; }
fi
SECONDMATE_CHARTER=${FM_SECONDMATE_CHARTER:-"{TASK}"}
SECONDMATE_SCOPE=${FM_SECONDMATE_SCOPE:-${FM_SECONDMATE_CHARTER:-"{TASK}"}}
if [ "$NO_PROJECTS" -eq 1 ]; then
  PROJECT_CLONES_BODY="None. This is a project-less domain: its subject is the firstmate repo this home lives in, so it needs no separate clones under \`projects/\`; its crews take pooled worktrees of that firstmate repo."
  PROJECT_CLONES_NOTE="This domain has no separate project clones: its subject is the firstmate repo this home lives in, and its crews take pooled worktrees of that repo."
else
  PROJECT_CLONES_BODY=$(printf '%s\n' "$SECONDMATE_PROJECTS" | tr ' ' '\n' | sed 's/^/- /')
  PROJECT_CLONES_NOTE="The projects above are local clones for work you supervise; they are not an exclusive ownership claim."
fi
cat > "$BRIEF" <<EOF
You are a persistent second mate managed by the main firstmate. Work on your own; do not wait for a human.

# Charter
$SECONDMATE_CHARTER

# Routing scope
$SECONDMATE_SCOPE

# Project clones
$PROJECT_CLONES_BODY

# Operating model
You are in an isolated firstmate home. The local \`AGENTS.md\` is your job description, and your local \`data/\`, \`state/\`, \`config/\`, and \`projects/\` dirs are yours to operate.
$PROJECT_CLONES_NOTE
Delegate project work to your own crewmates with the normal firstmate lifecycle: brief, spawn, status, watcher, steer, teardown, and recovery.
Do not invent a second delegation system.
You do not generate your own work.
Act only on tasks the main firstmate routes to you.
Never start a survey, audit, or "find improvements" sweep on your own initiative; that is not your job and it is unwanted.

# The captain and the parent channel
Nobody reads this chat: the captain and the main firstmate see only what is appended to $STATUS_FILE, and a captain-facing sentence that is not appended there has not been sent.
That file is your parent channel, and in this home it IS the captain: every sentence you would say to the captain, and every outcome the local AGENTS.md tells a firstmate to bring to the captain, is one appended line there, never chat.
Your own machinery publishes the durable facts about your crew's work for you (\`bin/fm-parent-channel-lib.sh\`): a child's terminal done or failed line with its note and PR on every supervision poll, a PR-ready line when you register a PR, a task you hold for the captain and its answer, a merge, and a child's final line at cleanup all reach the parent channel from the scripts that record them, whether or not you append anything.
What only you can append is judgement: the answer to a marked request below, a recommendation or caveat on a delivered outcome, a blocker or failure of your own, and anything else you would otherwise say to the captain.

# Requests from the main firstmate
You are a firstmate in your own home, so an incoming message reaches you in your own chat.
You must distinguish who it is from, because the answer goes to a different place.
A request relayed to you by the main firstmate is tagged with a leading \`$FM_FROMFIRST_LABEL\` marker followed by an invisible system separator; this marker is untypable, so a human never produces it.
When a message carries that marker, do the work, then respond via the STATUS/ESCALATION path below, never only in this chat: the main firstmate does not read your chat, so a chat-only reply is lost.
Marked requests also carry a privacy-safe \`corr=<id>\` token after the marker; include that exact token in your parent status reply (or in the status pointer to a detailed doc) so the parent can correlate the answer.
Optional helper: \`bin/fm-secondmate-report.sh <verb> <corr_id> <note>\` appends that correlated line to the parent channel itself - do not pass a status path, and do not write a hand path under this home.
A plain \`echo\` that includes the same \`corr=<id>\` on this parent channel is equally valid; do not depend on the helper being present.
For a terse result, a status line is the whole answer.
For a detailed answer (an investigation, a plan, an audit), write it to a doc under your home's \`data/\` and append a status line that points to that doc - the scout-report pattern - so the main firstmate is woken and can read it.
Before treating an investigation or visual review as complete, load \`captain-hold-lifecycle\` from this home's \`.agents/skills/\` and pass its shared completion gate.
A message with NO marker is the captain typing directly into your pane: treat it as authoritative captain intervention and stay conversational exactly as you would for any captain message; do not force it onto the status path.
A request arriving through the instruction inbox below follows the same marker and reply rules.

$INBOX_SECTION

# Escalation to main firstmate
Handle routine work yourself.
Report only true captain-relevant outcomes or a declared external wait by appending one line:
   \`echo "{state}: {one short line}" >> $STATUS_FILE\`
States: working, needs-decision, blocked, $PAUSED_VERB, done, failed.
Use \`$PAUSED_VERB: {why}\` (distinct from \`blocked:\`) only when your domain is deliberately idling on a known external wait you expect to clear on its own, naming when it clears with \`until <YYYY-MM-DDTHH:MMZ>\` (UTC) when you know; use \`blocked:\` when you are stuck and need firstmate to act.
Use this only for material phase changes, a captain decision, a real blocker, a failure, work ready for review, or work you landed.
Work you landed includes a merge you performed yourself under standing merge authority and one the captain merged on the forge: under that authority nothing is ever \"ready for review\", so a landed merge that goes unreported reaches the captain as silence.
This is also how you return the answer to a marked from-firstmate request above.
A marked request requires one correlated answer after the work; it does not require a separate receipt or start acknowledgement.
Never append \`working:\` merely to acknowledge receipt or announce that a marked request has started.
When a routed-work phase has a supervisor-actionable material change worth reporting under the rule above, give that reported phase a stable key.
If its first reportable event is \`working [key=<work-slug>]: {material phase}\`, use the same key in the same position on its later \`$PAUSED_VERB\`, \`done\`, \`failed\`, \`needs-decision\`, or \`blocked\` event so the earlier working phase is superseded.
When a keyed phase ends without another reportable state, append \`resolved [key=<work-slug>]: {why it is no longer active}\`.
\`resolved\` separately closes an escalated decision or blocker, and only a \`resolved\` line carrying that decision's exact key closes it: a later \`done\` or \`working\` event never does, even when the answer is what started that work.
The main firstmate's answer normally writes that closing line at answer time; when a blocker or wait clears WITHOUT an answer from the main firstmate, append \`resolved [key=<slug>]: {how it cleared}\` yourself (the token sits between the verb and the colon, as when you opened it) as your domain resumes.
Routine internal supervision, heartbeats, retries, and crewmate churn stay inside your own home and must not touch that status file.

# Definition of done
You are persistent by default. Do not exit just because your queue is empty.
On startup and restart, run normal firstmate bootstrap and recovery through \`bin/fm-session-start.sh\` for your own home, but only to RECONCILE work that is already yours: in-flight crewmates, tracked backlog items, and durable watches recorded in this home.
When you have no assigned or in-flight work after that reconciliation, go idle and wait silently for the main firstmate to route you a task.
An empty queue is a healthy resting state, not a cue to invent work: never spawn a survey, audit, or any self-directed "find work" task on your own initiative.
If this charter cannot be carried out, append \`blocked: {why}\` or \`failed: {why}\` to the main status file and stop.
EOF
if [ "$SECONDMATE_CHARTER" = "{TASK}" ]; then
  echo "scaffolded: $BRIEF (secondmate charter; replace {TASK})"
else
  echo "scaffolded: $BRIEF (secondmate charter)"
fi
exit 0
fi

REPO=${POS[1]}

if [ "$HERDR_LAB" -eq 1 ]; then
HERDR_LAB_HELPER=$(shell_quote "$FM_ROOT/bin/fm-herdr-lab.sh")
# shellcheck disable=SC2016  # single quotes are deliberate: these lines are literal brief text whose backtick-wrapped $(...) and "$HERDR_LAB_SESSION" snippets must reach the reading agent verbatim, not expand at scaffold time; only the '"$VAR"' break-outs interpolate.
HERDR_SECTION=$(printf '%s\n' \
'# Herdr isolation - HARD SAFETY CONTRACT' \
'This brief was explicitly scaffolded with `--herdr-lab` because the task will drive Herdr lifecycle behavior.' \
'On Herdr 0.7.3 the API socket is not relocatable by `HERDR_CONFIG_PATH`, `XDG_CONFIG_HOME`, or `HOME`.' \
'A named non-`default` session plus a trailing `--session <name>` on every call is the only viable local isolation.' \
'' \
'1. Set `HERDR_LAB_HELPER='"$HERDR_LAB_HELPER"'` and generate the session name with `HERDR_LAB_SESSION=$("$HERDR_LAB_HELPER" name '"$ID"')`.' \
'   Install `trap '\''"$HERDR_LAB_HELPER" teardown "$HERDR_LAB_SESSION"'\'' EXIT` before provisioning, then provision only with `"$HERDR_LAB_HELPER" provision "$HERDR_LAB_SESSION"`.' \
'2. Run every task-specific non-lifecycle Herdr command through `"$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" <arguments...>`.' \
'   The helper appends the required trailing `--session "$HERDR_LAB_SESSION"`; `HERDR_SESSION` alone is never accepted as isolation.' \
'3. Teardown only through `"$HERDR_LAB_HELPER" teardown "$HERDR_LAB_SESSION"`.' \
'   It re-checks refuse-default immediately before stop and again immediately before delete, and fails closed on ambiguity.' \
'4. If an experiment requires a deliberate mid-run session stop, use only `"$HERDR_LAB_HELPER" stop "$HERDR_LAB_SESSION"`; it performs the same immediate refuse-default check.' \
'5. Forbidden commands: direct `herdr server stop`, every other server-global operation such as `herdr server live-handoff` or reload/update operations, direct `herdr session stop`, direct `herdr session delete`, and any Herdr call scoped only by ambient or inline `HERDR_SESSION`.' \
'6. The helper records the live default session before provisioning and verifies the identical fleet state after teardown.' \
'   A missing, stopped, or changed default session is a hard tripwire failure, never a cleanup warning to ignore.' \
'' \
'Never bypass the helper, even for a read-only lifecycle probe or cleanup after failure.' \
'The captain fleet uses the running `default` session.')
else
IFS= read -r -d '' HERDR_SECTION <<'EOF' || true
# Herdr lifecycle declaration - NOT ENABLED
**HARD SAFETY GATE:** this scaffold cannot inspect the task text filled in above.
If the task will start, stop, delete, restart, profile, or otherwise drive Herdr lifecycle behavior, stop and regenerate the brief with `--herdr-lab` before dispatch.
Do not add Herdr lifecycle commands to this unguarded brief by hand.
EOF
HERDR_SECTION=${HERDR_SECTION%$'\n'}
fi

IFS= read -r -d '' TASK_SECTION <<'EOF' || true
# Task
## Captain's intent
{TASK}

## Firstmate spec
{FIRSTMATE_SPEC}
EOF
TASK_SECTION=${TASK_SECTION%$'\n'}

if [ "$KIND" = scout ]; then
if "$SCRIPT_DIR/fm-bootstrap.sh" lavish-compatible >/dev/null 2>&1; then
  LAVISH_LINE='If your deliverable is a visual artifact the captain will review and iterate on, you may host the Lavish review loop yourself (poll, revise, re-serve, staying alive) instead of handing it back to firstmate.'
else
  LAVISH_LINE='Lavish is unavailable (lavish-axi is missing or below its supported version floor), so deliver your findings as a text report without Lavish, even for a visual deliverable.'
fi
cat > "$BRIEF" <<EOF
You are a crewmate: an autonomous worker agent managed by firstmate. Work on your own; do not wait for a human.

$TASK_SECTION

$HERDR_SECTION

# Setup
You are in a disposable git worktree of $REPO, at a detached HEAD on a clean default branch.
The spawn path already refreshed this worktree to the tracked upstream and asserted the base, so HEAD should be current.
This is a SCOUT task: the deliverable is a written report, not a PR.
The worktree is your laboratory - install, run, edit, and make scratch commits freely; all of it is discarded at teardown.
The report is the only thing that survives, so anything worth keeping must be in it.

# Rules
1. Never push to any remote and never open a PR.
2. Stay inside this worktree; the only files you may write outside it are the report and the status file below.
3. Use gh-axi for GitHub operations and chrome-devtools-axi for browser operations.
4. Report status by appending one line:
   \`echo "{state}: {one short line}" >> $STATUS_FILE\`
   States: working, needs-decision, blocked, $PAUSED_VERB, done, failed.
   Each append wakes firstmate, so report sparingly: only phase changes a supervisor
   would act on and the needs-decision/blocked/paused/done/failed states. No step-by-step
   FYI progress lines; firstmate reads your pane for that.
   Whenever you mention a PR anywhere - a status line, your terminal, a summary - write its full
   https:// URL exactly as the forge printed it, never a bare number such as "PR 108"; firstmate
   copies that URL from your line rather than assembling one.
   Use \`$PAUSED_VERB: {why}\` - distinct from \`blocked:\` - ONLY when you are deliberately idling on a
   known external wait you expect to clear on its own (an upstream release, a rate-limit reset):
   firstmate then leaves your idle pane alone and rechecks it on a long cadence instead of
   treating it as a possible wedge. When you know when the wait clears, say so in the line with
   \`until <YYYY-MM-DDTHH:MMZ>\` (UTC) and firstmate rechecks at that time instead.
   Use \`blocked:\` when you are stuck and need help.
5. If you hit the same obstacle twice, append \`blocked: {why}\` and stop; firstmate will help.
6. If a decision belongs to a human (product choices, destructive actions),
   append \`needs-decision [key=<slug>]: {summary of options}\` and stop. Firstmate will reply with the decision.
   A decision or blocker you opened stays open until a \`resolved\` line carrying its exact key lands; a later \`done:\` or \`working:\` line never closes it, even when the answer is what started that work.
   Firstmate's reply normally writes that closing line at answer time; when a blocker or wait clears WITHOUT a firstmate reply, append \`resolved [key=<slug>]: {how it cleared}\` yourself (the token sits between the verb and the colon, as when you opened it) as you resume.
7. Never stop, restart, or update the shared \`no-mistakes\` daemon - it is one instance serving
   every lane/home, so restarting it kills other lanes' in-flight pipeline runs; only firstmate
   manages the daemon.
   Before you append \`blocked:\` about the pipeline, run \`no-mistakes daemon status\` and
   \`no-mistakes axi status\`. If the daemon socket refuses connections or is missing, append
   \`blocked: {the daemon error}\` and stop even when the local run record still says running or
   fixing, because that record can be stale after the daemon exits. A run record failed with a
   daemon error is also a real block.
   Only after ruling out socket refusal, if the run is still running or fixing, reattach and keep
   going. A drive-call error, timeout, slow read, or generic unreachability is NOT a daemon error:
   the daemon accepts \`respond\` immediately and runs the round in the background, so a killed or
   timed-out call was only waiting for a read while the run kept working.

$INBOX_SECTION

# Tests
Size the run before you start it: a changed-file selection can be WIDER than a single lane or family when your change has wide fan-out, so compare both sizes before picking rather than assuming the change-scoped one is smaller.

# Definition of done
Write your findings to \`$DATA/$ID/report.md\`.
The report must stand alone: what you did, what you found, the evidence (commands run, output, file:line references), and what you recommend.
$LAVISH_LINE
Before reporting done, read and follow \`$FM_ROOT/.agents/skills/captain-hold-lifecycle/SKILL.md\` and pass its shared completion gate for the report and any visual review.
When the report is complete, append \`done: {one-line conclusion}\` to the status file and stop.
If your findings reveal work that should ship (e.g. you reproduced a bug and the fix is clear), say so in the report; firstmate may promote this task in place, and you would then receive mode-specific ship instructions as a follow-up message.
EOF
if [ "$RENDERED" -eq 1 ]; then
  fm_brief_apply_render || exit 1
  echo "scaffolded: $BRIEF (scout; Task sections rendered from record $RECORD_ID)"
  exit 0
fi
echo "scaffolded: $BRIEF (scout; replace {TASK} and {FIRSTMATE_SPEC})"
exit 0
fi

# Ship task: shape Setup / Rule 1 by this task's explicit delivery mode, validated
# above, and render the Definition of done from its single owner, bin/fm-dod-lib.sh,
# which bin/fm-promote.sh renders too so a promoted scout receives the same contract.
# The block opens with the fixed "Delivery contract: mode=<mode>" line that
# bin/fm-spawn.sh checks against its own explicit --mode before launching.
case "$MODE" in
  direct-PR)
    SETUP2=""
    RULE1='1. Never push to the default branch (push only your `fm/'"$ID"'` branch). Never merge a PR.'
    ;;
  local-only)
    SETUP2=""
    RULE1="1. Never push to any remote and never open a PR. Work only on your \`fm/$ID\` branch; firstmate handles the merge into local \`main\`."
    ;;
  *)  # no-mistakes
    SETUP2="
2. Run \`no-mistakes doctor\`; if it reports the repo is not initialized here, run \`no-mistakes init\`."
    RULE1='1. Never push to the default branch. Never merge a PR.'
    ;;
esac
DOD=$(fm_dod_block "$MODE" "$ID") || exit 1

cat > "$BRIEF" <<EOF
You are a crewmate: an autonomous worker agent managed by firstmate. Work on your own; do not wait for a human.

$TASK_SECTION

$HERDR_SECTION

# Setup
You are in a disposable git worktree of $REPO, at a detached HEAD on a clean default branch.
The spawn path already refreshed this worktree to the tracked upstream and asserted the base, so HEAD should be current.

**Verify isolation before anything else.** Run \`pwd -P\` and \`git rev-parse --show-toplevel\`; both must resolve to the disposable task worktree you were launched in, such as a treehouse pool path, not the primary checkout firstmate operates from.
The path check is authoritative: \`git rev-parse --git-dir\` and \`git rev-parse --git-common-dir\` can help inspect the repo, but they do not prove you are outside the primary checkout.
If the top-level path is the primary checkout or not the worktree you were launched in, STOP - do not branch or commit here - append \`blocked: launched in primary checkout, not an isolated worktree\` to the status file and stop.

1. First action: create your branch: \`git checkout -b fm/$ID\`$SETUP2

# Workspace vs firstmate's home
Your workspace is the disposable worktree you just verified above (the \`git rev-parse --show-toplevel\` path): branch, edit, commit, and run code only there.
Firstmate's home is \`$FM_HOME\`: the status file, inbox, and helper scripts named below all live under it, and it is NOT your workspace even when it holds a checkout of the same repo.
Never cd into it, edit, commit, branch, or run project code there; the only writes allowed under it are the ones this brief gives you explicit commands for.

# Rules
$RULE1
2. Stay inside that worktree; the firstmate-home paths below are outside it, not additional places to work.
3. Use gh-axi for GitHub operations and chrome-devtools-axi for browser operations.
4. Report status by appending one line (that file lives in firstmate's home, outside your worktree):
   \`echo "{state}: {one short line}" >> $STATUS_FILE\`
   States: working, needs-decision, blocked, $PAUSED_VERB, done, failed.
   Each append wakes firstmate, so report sparingly: only phase changes a supervisor
   would act on (setup done, bug reproduced, fix implemented, validation passed) and the
   needs-decision/blocked/paused/done/failed states. No step-by-step FYI progress lines;
   firstmate reads your pane for that.
   Whenever you mention a PR anywhere - a status line, your terminal, a summary - write its full
   https:// URL exactly as the forge printed it, never a bare number such as "PR 108"; firstmate
   copies that URL from your line rather than assembling one.
   A mid-task \`working:\` line (including setup complete) is nonterminal: do not end the
   turn after it; continue the same stage until a defined \`done:\` gate under Definition of done.
   Use \`$PAUSED_VERB: {why}\` - distinct from \`blocked:\` - ONLY when you are deliberately idling on a
   known external wait you expect to clear on its own (an upstream release, a rate-limit reset,
   a scheduled window): firstmate then leaves your idle pane alone and rechecks it on a long
   cadence instead of treating it as a possible wedge. Use \`blocked:\` when you are stuck and need help.
5. If you hit the same obstacle twice, append \`blocked: {why}\` and stop; firstmate will help.
6. If a decision belongs above the implementation worker (product choices, destructive actions),
   append \`needs-decision [key=<slug>]: {summary of options}\` and stop. Firstmate will reply with the decision.
$ASK_USER_BLOCK
   A decision or blocker you opened stays open until a \`resolved\` line carrying its exact key lands; a later \`done:\` or \`working:\` line never closes it, even when the answer is what started that work.
   Firstmate's reply normally writes that closing line at answer time; when a blocker or wait clears WITHOUT a firstmate reply, append \`resolved [key=<slug>]: {how it cleared}\` yourself (the token sits between the verb and the colon, as when you opened it) as you resume.
7. Never stop, restart, or update the shared \`no-mistakes\` daemon - it is one instance serving
   every lane/home, so restarting it kills other lanes' in-flight pipeline runs; only firstmate
   manages the daemon.
   Before you append \`blocked:\` about the pipeline, run \`no-mistakes daemon status\` and
   \`no-mistakes axi status\`. If the daemon socket refuses connections or is missing, append
   \`blocked: {the daemon error}\` and stop even when the local run record still says running or
   fixing, because that record can be stale after the daemon exits. A run record failed with a
   daemon error is also a real block.
   Only after ruling out socket refusal, if the run is still running or fixing, reattach and keep
   going. A drive-call error, timeout, slow read, or generic unreachability is NOT a daemon error:
   the daemon accepts \`respond\` immediately and runs the round in the background, so a killed or
   timed-out call was only waiting for a read while the run kept working.

$INBOX_SECTION

# Project memory
If \`AGENTS.md\` or \`CLAUDE.md\` already exists, or if this task produced durable project-intrinsic knowledge, run the firstmate helper \`$FM_ROOT/bin/fm-ensure-agents-md.sh .\` in the worktree.
It operates on your workspace; do not open, edit, or run anything else under \`$FM_ROOT\` itself.
Record only project knowledge useful to almost every future session.
For anything the codebase already shows, prefer a pointer to the authoritative file, command, or doc over copying the detail.
If you touch a project \`AGENTS.md\`, follow that helper's self-governance contract in the same pass.
Keep it proportionate: skip \`AGENTS.md\` edits for trivial tasks that produced no durable project knowledge.

# Tests
Size the run before you start it: a changed-file selection can be WIDER than a single lane or family when your change has wide fan-out, so compare both sizes before picking rather than assuming the change-scoped one is smaller.

$DOD
EOF
if [ "$RENDERED" -eq 1 ]; then
  fm_brief_apply_render || exit 1
  echo "scaffolded: $BRIEF (ship, mode=$MODE; Task sections rendered from record $RECORD_ID)"
  exit 0
fi
echo "scaffolded: $BRIEF (ship, mode=$MODE; replace {TASK} and {FIRSTMATE_SPEC})"
