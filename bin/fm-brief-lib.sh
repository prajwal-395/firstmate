#!/usr/bin/env bash
# fm-brief-lib.sh - single owner of a task brief's readiness contract.
#
# bin/fm-brief.sh scaffolds four scope fields into every ship and scout brief,
# and both bin/fm-brief.sh --check and bin/fm-spawn.sh gate dispatch through
# this one implementation: first on unfilled scaffold placeholders, then on the
# four scope fields, then on the whole-suite Done-check guard, then on the
# merge-before-PR step, then on the Herdr lifecycle scan. No side effects on
# source. set -u / set -e safe.
#
# The four fields, in scaffold order:
#   What done means for this task  - this task's finish line, not the project's.
#   Out of scope                   - what a helpful worker must not "improve".
#   Known unknowns                 - what we do not know that could change the answer.
#   Blocked on                     - a decision, credential, or prior task, named,
#                                    or the literal word `nothing`.
#
# An unfilled scaffold placeholder means the brief was never completed. A worker
# handed one is told to do something the text never says - "run the check
# above" where the check is the literal word {DONE_CHECK}. Firstmate fills these
# by habit, so a placeholder ADDED to the scaffold later is exactly the one that
# gets missed: the habit covers the placeholder that already existed. Refuse
# rather than rely on remembering, and name every unfilled one so a second is
# not discovered on the next attempt.
#
# A scope field whose body is only the scaffold's own placeholder prose is not
# an answer: the emptiness test below strips unfilled placeholders before
# deciding a field is filled, so such a field is refused as empty and named.
# That judgment is about the scaffold's own tokens, never about what a field
# says otherwise: no minimum length, no keyword check, no heuristic for whether
# an answer is good enough. That judgment belongs to firstmate at intake. A
# required field that cannot be answered honestly gets filled with noise, and
# noise in a required field is worse than no field because it looks like
# compliance. `nothing` is a complete answer for Blocked on, and a one-line fix
# must be able to satisfy every field as readily as a month of programme work.
#
# A field is satisfied when its heading is present and at least one line carrying
# an answer follows it before the next heading. A line holding only unfilled
# scaffold placeholders is not an answer. Any heading level matches, so a brief
# edited by hand does not fail on a `#` count.
#
# Briefs scaffolded before this contract existed carry none of the four
# headings. Those warn once and proceed rather than becoming undispatchable; a
# brief carrying SOME of the headings is a current-generation brief with a field
# emptied, and that is refused.
#
# The same fail-closed shape guards the Done-check of a ship brief: a Done-check
# that demands the whole suite overrides the test-selection ladder the scaffold
# teaches above it, because the Done-check is the last instruction the worker
# reads. The gate refuses the demand unless the same section states a fan-out
# reason, the way the scope contract refuses emptiness rather than judging
# quality. bin/fm-brief.sh --check and bin/fm-spawn.sh both gate through
# fm_brief_fullsuite_check below, so the two never hold separate opinions.
#
# Detection rule, argued rather than exhaustive. A demand is a line in the
# Done-check section only - never the scaffold's own ladder prose - matching a
# whole-suite phrase (full/whole/entire/complete suite, full/entire/whole test
# suite, all tests) or a whole-suite command form (a bare pytest with no path
# and no selection flag, pytest over tests/ as a directory, make test/check).
# A line carrying a prohibition cue (do not, does not, never, avoid, must not,
# without, cannot) is not a demand: "Do NOT run the full suite" is the guardrail
# working, not the defect. Deliberately not caught: other runners' full-suite
# spellings (npm, cargo, go, tox, bazel) and filtered pytest runs (-k, -m, a
# path argument) - judging those would refuse legitimate briefs, and the
# recorded incidents are all pytest-suite demands. A stated reason is any
# fan-out cue in the same section (fan-out, because, reason, justif-, wide,
# touch-, affect-, span-, across, cover-): presence, never quality, because a
# required reason that is judged gets filled with noise.
#
# The same fail-closed shape guards the merge-before-PR step of a ship brief:
# a squash merge of a stale branch reverts cleanly with no conflict, so the
# worker must merge the tracked upstream and re-run the chosen selection ON
# the merged tree before the PR is opened. The gate refuses a ship brief that
# lacks both halves of that step, the way the scope contract refuses emptiness
# rather than judging quality. bin/fm-brief.sh --check and bin/fm-spawn.sh
# both gate through fm_brief_merge_check below, so the two never hold separate
# opinions.
#
# Detection rule, presence only. A ship brief passes when it carries the
# merge half (merge the tracked upstream) and the verify half (on the merged
# tree), each matched case-insensitively anywhere in the brief. The scaffold
# states both in its own Definition of done, so a freshly scaffolded brief
# passes and a brief with the step deleted is refused. The Done-check section
# is the ship marker, the same one the full-suite gate above uses: scout
# briefs, secondmate charters, and minimal fixtures without one carry no PR
# and pass vacuously. There is deliberately no pre-contract grandfathering: an
# older ship brief with a Done-check but without the step is refused so the
# missing merge is reported rather than silently skipped.
#
# The same fail-closed shape guards omitted Herdr intent. A brief scaffolded
# without --herdr-lab carries a short declaration instead of the lab contract,
# but prose is only read when the worker reads it. So dispatch additionally
# refuses a brief whose filled # Task text itself drives Herdr lifecycle
# behavior while the brief carries no lab contract, with the regeneration
# instruction. bin/fm-brief.sh --check and bin/fm-spawn.sh both gate through
# fm_brief_herdr_check below, so the two never hold separate opinions.
#
# Detection rule, argued rather than exhaustive. A demand is a # Task line
# naming herdr alongside a lifecycle verb (start, stop, delete, restart,
# provision, handoff, profile, reload, teardown). A line carrying a
# prohibition cue (do not, never, without, cannot, stop and regenerate) is not
# a demand. Deliberately not caught: read-only mentions such as
# `herdr session list`, which drive no lifecycle, and Herdr named in a scope
# field as out of scope or unknown, which is context rather than lifecycle the
# worker will drive - only the # Task section is scanned.

# Canonical field headings, in scaffold order, separated by "|".
FM_BRIEF_SCOPE_FIELDS='What done means for this task|Out of scope|Known unknowns|Blocked on'

# Canonical unfilled-scaffold placeholder pattern. Every scaffold token the
# fleet must fill matches it. Both bin/fm-brief.sh --check and bin/fm-spawn.sh
# refuse through fm_brief_placeholder_check below, so the two never hold
# separate opinions about what a filled brief is.
FM_BRIEF_PLACEHOLDER_RE='\{[A-Z_]+\}'

# Print the sorted unique unfilled scaffold placeholders in a brief, one per
# line. Prints nothing when the brief is fully filled.
fm_brief_unfilled_placeholders() {  # <brief-path>
  grep -oE "$FM_BRIEF_PLACEHOLDER_RE" "$1" 2>/dev/null | sort -u || true
}

# Gate one brief on unfilled scaffold placeholders. Returns 0 when no
# placeholder remains, 1 naming every unfilled one otherwise. <what> names the
# action being gated so the refusal reads in the caller's own terms.
fm_brief_placeholder_check() {  # <brief-path> <what>
  local brief=$1 what=$2 unfilled
  unfilled=$(fm_brief_unfilled_placeholders "$brief" | tr '\n' ' ')
  if [ -n "$unfilled" ]; then
    echo "error: $brief still contains unfilled scaffold placeholder(s): ${unfilled% }" >&2
    echo "       fill every placeholder before dispatch - a worker cannot act on one, and the" >&2
    echo "       instruction referring to it becomes a sentence about nothing" >&2
    return 1
  fi
  return 0
}

# Print exactly one status line for a brief:
#   pre-contract          none of the four headings is present
#   ok                    every field is present and answered
#   empty<TAB>field...    one line naming every missing, blank, or
#                         placeholder-only field
fm_brief_scope_state() {  # <brief-path>
  # The placeholder pattern below is FM_BRIEF_PLACEHOLDER_RE above, passed in
  # rather than restated, so the emptiness test cannot drift from the gate.
  awk -v fields="$FM_BRIEF_SCOPE_FIELDS" -v placeholder="$FM_BRIEF_PLACEHOLDER_RE" '
    BEGIN {
      count = split(fields, want, "|")
      current = ""
      seen_any = 0
    }
    /^#/ {
      heading = $0
      sub(/^#+[[:space:]]*/, "", heading)
      sub(/[[:space:]]+$/, "", heading)
      current = ""
      for (i = 1; i <= count; i++) {
        if (heading == want[i]) { current = i; seen[i] = 1; seen_any = 1 }
      }
      next
    }
    {
      if (current != "") {
        body = $0
        gsub(placeholder, "", body)
        gsub(/[[:space:]]/, "", body)
        if (body != "") filled[current] = 1
      }
    }
    END {
      if (seen_any == 0) { print "pre-contract"; exit }
      out = ""
      for (i = 1; i <= count; i++) {
        if (!(i in seen) || !(i in filled)) out = out "\t" want[i]
      }
      if (out == "") { print "ok" } else { print "empty" out }
    }
  ' "$1" 2>/dev/null
}

# Print the Done-check section of a brief: from its heading to the next
# heading. Prints nothing when the brief carries no Done-check heading (scout
# briefs, secondmate charters, and pre-contract briefs), so callers gate only
# ship briefs through the full-suite check below. Any heading level matches.
fm_brief_donecheck_section() {  # <brief-path>
  awk '
    /^#/ {
      heading = $0
      sub(/^#+[[:space:]]*/, "", heading)
      sub(/[[:space:]]+$/, "", heading)
      if (tolower(heading) == "done-check") { in_section = 1; next }
      if (in_section) exit
      next
    }
    in_section { print }
  ' "$1" 2>/dev/null
}

# Gate one brief on a whole-suite demand in its Done-check without a stated
# fan-out reason. Returns 0 when dispatch may proceed (no Done-check section,
# no demand, or a demand with a reason), 1 naming the demand otherwise.
# <what> names the action being gated so the refusal reads in the caller's own
# terms. Only presence is judged, never the quality of the reason.
fm_brief_fullsuite_check() {  # <brief-path> <what>
  local brief=$1 what=$2 section lowered demand_line
  section=$(fm_brief_donecheck_section "$brief")
  [ -n "$section" ] || return 0
  lowered=$(printf '%s\n' "$section" | tr '[:upper:]' '[:lower:]')
  demand_line=$(printf '%s\n' "$lowered" | fm_brief_fullsuite_demand_line) || demand_line=
  [ -n "$demand_line" ] || return 0
  if printf '%s\n' "$lowered" | grep -qE 'fan-? ?out|because|reason|justif|wide|touch(es|ing)?|affects?|affecting|spans?|spanning|across|covers?|covering'; then
    return 0
  fi
  echo "error: $brief demands the whole suite in its Done-check ('${demand_line}'), so $what is refused:" >&2
  echo "       Name the narrower selection that proves the change instead - usually the test files" >&2
  echo "       covering the changed files, per the brief's own test-selection ladder." >&2
  echo "       A brief that genuinely needs the whole suite states its fan-out reason in the" >&2
  echo "       Done-check (e.g. 'full suite because compile_manifest touches every lane')," >&2
  echo "       and then it passes; there is no flag that skips this check." >&2
  return 1
}

# Print the first Done-check line (already lowercased) that demands the whole
# suite, or nothing. Prohibition lines are skipped first so a ban on the full
# suite never reads as a demand for it. A pytest line is a demand unless it
# names a path (a slash or .py token) or a selection flag (-k, -m and their
# long forms): "run pytest to verify" is bare, "run pytest tests/foo.py" and
# "run pytest -k brief" are selections. pytest over the tests/ directory is
# always a demand and is tested before the path exclusion.
fm_brief_fullsuite_demand_line() {
  awk '
    /do not|does not|never|avoid|must not|without|cannot|refus/ { next }
    /(full|whole|entire|complete)[ -]+(test[ -]+)?suite|all[ -]+tests/ { print; exit }
    /pytest[ \t]+tests\/?([ \t]|$|[;&])/ { print; exit }
    /pytest/ && !/\// && !/\.py/ && !/-k([^a-z]|$)/ && !/-m([^a-z]|$)/ && !/--deselect/ && !/--lf/ { print; exit }
    /(^|[ \t])make[ \t]+(test|check)([ \t]|$|[;&])/ { print; exit }
  '
}

# Gate one brief on the required merge-before-PR step. Returns 0 when dispatch
# may proceed (not a ship brief, or a ship brief carrying both halves of the
# step), 1 naming the missing half otherwise. <what> names the action being
# gated so the refusal reads in the caller's own terms. Only presence is
# judged, never how the step is worded beyond its two halves.
fm_brief_merge_check() {  # <brief-path> <what>
  local brief=$1 what=$2 section lowered has_merge has_verify
  section=$(fm_brief_donecheck_section "$brief")
  [ -n "$section" ] || return 0
  lowered=$(tr '[:upper:]' '[:lower:]' < "$brief" 2>/dev/null)
  has_merge=0
  has_verify=0
  printf '%s\n' "$lowered" | grep -q 'merge the tracked upstream' || has_merge=1
  printf '%s\n' "$lowered" | grep -q 'on the merged tree' || has_verify=1
  [ "$has_merge" -eq 0 ] && [ "$has_verify" -eq 0 ] && return 0
  echo "error: $brief lacks the required merge-before-PR step, so $what is refused:" >&2
  [ "$has_merge" -eq 0 ] || echo "       missing: merge the tracked upstream before opening the PR" >&2
  [ "$has_verify" -eq 0 ] || echo "       missing: run the chosen test selection once ON the merged tree" >&2
  echo "       A squash merge of a stale branch reverts cleanly with no conflict, so the" >&2
  echo "       worker must merge the tracked upstream and verify ON the merged tree before" >&2
  echo "       the PR is opened; restore the scaffold's own step instead of working around it." >&2
  return 1
}

# Print the # Task section of a brief: from its heading to the next
# heading. Prints nothing when the brief carries no Task heading, so callers
# gate only briefs that record task text. Any heading level matches.
fm_brief_task_section() {  # <brief-path>
  awk '
    /^#/ {
      heading = $0
      sub(/^#+[[:space:]]*/, "", heading)
      sub(/[[:space:]]+$/, "", heading)
      if (tolower(heading) == "task") { in_section = 1; next }
      if (in_section) exit
      next
    }
    in_section { print }
  ' "$1" 2>/dev/null
}

# Print the first # Task line (already lowercased) that drives Herdr
# lifecycle, or nothing. Prohibition lines are skipped first so a refusal to
# touch Herdr never reads as a demand for it.
fm_brief_herdr_demand_line() {
  awk '
    /do not|does not|never|avoid|must not|without|cannot|refus|stop and regenerate/ { next }
    /herdr/ && /start|stop|delet|restart|provision|handoff|profil|reload|teardown|live/ { print; exit }
  '
}

# Gate one brief on Herdr lifecycle driven by its own task text without the
# lab contract. Returns 0 when dispatch may proceed (lab contract present, no
# Task section, or no lifecycle demand), 1 naming the demand otherwise.
# <what> names the action being gated so the refusal reads in the caller's
# own terms. Only presence is judged, never whether the lifecycle is wise.
fm_brief_herdr_check() {  # <brief-path> <what>
  local brief=$1 what=$2 task lowered demand_line
  grep -q '^# Herdr isolation - HARD SAFETY CONTRACT' "$brief" 2>/dev/null && return 0
  task=$(fm_brief_task_section "$brief")
  [ -n "$task" ] || return 0
  lowered=$(printf '%s\n' "$task" | tr '[:upper:]' '[:lower:]')
  demand_line=$(printf '%s\n' "$lowered" | fm_brief_herdr_demand_line) || demand_line=
  [ -n "$demand_line" ] || return 0
  echo "error: $brief drives Herdr lifecycle in its task text ('${demand_line}'), so $what is refused:" >&2
  echo "       Regenerate the brief with --herdr-lab before dispatch; never hand-add Herdr lifecycle" >&2
  echo "       commands to this unguarded brief." >&2
  return 1
}

# Gate one brief. Returns 0 when dispatch may proceed (warning once for a brief
# that predates the contract), 1 when a required field is empty. <what> names the
# action being gated so the refusal reads in the caller's own terms.
fm_brief_scope_check() {  # <brief-path> <what>
  local brief=$1 what=$2 state missing field
  state=$(fm_brief_scope_state "$brief")
  case "$state" in
    ok)
      return 0
      ;;
    pre-contract)
      echo "warning: $brief carries no scope contract (scaffolded before briefs recorded one); $what proceeds without it - confirm this task's finish line, exclusions, unknowns, and blockers yourself" >&2
      return 0
      ;;
  esac
  echo "error: $brief leaves a required scope field empty, so $what is refused:" >&2
  missing=${state#empty}
  printf '%s\n' "$missing" | tr '\t' '\n' | while IFS= read -r field; do
    [ -n "$field" ] || continue
    echo "       empty: $field" >&2
  done
  echo "       Fill every field before dispatch - a blank one is a boundary the worker has to guess at." >&2
  echo "       Only emptiness is refused here - a body holding just the scaffold's own" >&2
  echo "       placeholders is empty, not an answer; anything else you write is never judged," >&2
  echo "       and the literal word 'nothing' is a complete answer for 'Blocked on'." >&2
  return 1
}
