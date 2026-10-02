# Rough cut review

History moved out of test module docstrings; the tests keep the invariant.

## `tests/test_rough_cut_gate.py` (moved from its module docstring, 2026-10-02)

A rejected rough cut refuses re-entry, and the override is recorded.

The defect
----------
Four planning steps - 4.01, 4.02, 4.03, 4.04 - each declared the prose
precondition `"'rough_cut_review.passed' is true in state"`, and nothing
read it: `grep -rn rough_cut_review --include=*.py library/` found no
read of `.passed` anywhere. Measured, a run against a recorded review
with `passed: false` planned four real subtitles and said nothing.

A full run already stops at a failed review, because
`step_3_03_review_rough_cut/step.py:386-387` exits 1. So the hole was
only ever on RE-ENTRY - a `--resume`, a `--from`, a scoped `--only`,
anything that reads the recorded review instead of re-running 3.03.

The ruling
----------
REFUSE WITH A DELIBERATE OVERRIDE (firstmate, 2026-09-05,
`data/decisions/rough-cut-gate.md`). Refusing outright would remove the
iterate-on-a-rejected-cut loop the captain plausibly wants; proceeding
silently is the defect itself. Refuse-with-override keeps the loop and
removes the silence.

Three constraints, and this file tests all three:

1. the override is EXPLICIT - nothing reaches it by default;
2. it is RECORDED in the run's own outputs, with the verdict it
   overrode, so a later reader can see the cut was rejected when this
   was planned;
3. it is NOT REACHABLE BY DEFAULT - a requirement must opt in, and
   naming one that has not is refused.

## `tests/test_rough_cut_actual_script.py` (moved from its module docstring, 2026-10-02)

Check 5's reconstruction is script work, and now a script does it.

Step 3.03's handoff asks the model to look up the temporal index per
A-roll range and concatenate the words in timeline order - range
lookup plus string joining over word timings the pipeline already
holds. `build_actual_script` (in step.py, beside the mechanical
checks whose inputs it shares) builds it off the spine's own word
timings instead, so the narrative review judges the measured script
rather than a reconstruction.

Deliberately NOT in `library/tools/timeline_transcript.py`: that
module rebuilds timeline audio from source media and transcribes it
with Whisper for live Resolve timelines that carry no measured words
at all. Concatenating already-measured words is a different mechanism
for a different input; forcing it in there would be the second
mechanism the WP2a brief says to stop at.

Proven in both directions: correct concatenation passes, and every
way the lookup can come up empty is named rather than skipped.
