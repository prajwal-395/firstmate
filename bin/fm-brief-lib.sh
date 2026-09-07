#!/usr/bin/env bash
# fm-brief-lib.sh - single owner of a task brief's readiness contract.
#
# bin/fm-brief.sh scaffolds four scope fields into every ship and scout brief,
# and both bin/fm-brief.sh --check and bin/fm-spawn.sh gate dispatch through
# this one implementation: first on unfilled scaffold placeholders, then on the
# four scope fields. No side effects on source. set -u / set -e safe.
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
