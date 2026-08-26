#!/usr/bin/env bash
# fm-brief-lib.sh - single owner of a task brief's required scope contract.
#
# bin/fm-brief.sh scaffolds four scope fields into every ship and scout brief,
# and both bin/fm-brief.sh --check and bin/fm-spawn.sh gate dispatch on them
# through this one implementation. No side effects on source. set -u / set -e safe.
#
# The four fields, in scaffold order:
#   What done means for this task  - this task's finish line, not the project's.
#   Out of scope                   - what a helpful worker must not "improve".
#   Known unknowns                 - what we do not know that could change the answer.
#   Blocked on                     - a decision, credential, or prior task, named,
#                                    or the literal word `nothing`.
#
# EMPTY IS THE ONLY REFUSAL. This library never judges what a field says: no
# minimum length, no keyword check, no heuristic for whether an answer is good
# enough. That judgment belongs to firstmate at intake. A required field that
# cannot be answered honestly gets filled with noise, and noise in a required
# field is worse than no field because it looks like compliance. `nothing` is a
# complete answer for Blocked on, and a one-line fix must be able to satisfy
# every field as readily as a month of programme work.
#
# A field is satisfied when its heading is present and at least one non-blank
# line follows it before the next heading. Any heading level matches, so a brief
# edited by hand does not fail on a `#` count.
#
# Briefs scaffolded before this contract existed carry none of the four
# headings. Those warn once and proceed rather than becoming undispatchable; a
# brief carrying SOME of the headings is a current-generation brief with a field
# emptied, and that is refused.

# Canonical field headings, in scaffold order, separated by "|".
FM_BRIEF_SCOPE_FIELDS='What done means for this task|Out of scope|Known unknowns|Blocked on'

# Print exactly one status line for a brief:
#   pre-contract          none of the four headings is present
#   ok                    every field is present and non-blank
#   empty<TAB>field...    one line naming every missing or blank field
fm_brief_scope_state() {  # <brief-path>
  awk -v fields="$FM_BRIEF_SCOPE_FIELDS" '
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
  echo "       Only emptiness is refused here; what you write is never judged, and the literal word" >&2
  echo "       'nothing' is a complete answer for 'Blocked on'." >&2
  return 1
}
