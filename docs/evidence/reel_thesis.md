# `library.tools.reel_thesis` - the history behind its contract

This is the module docstring of `library/tools/reel_thesis.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The post-build thesis check: does the reel make its point (G1).

Gap G1 (field-test structural scout, 2026-09-19): nothing in the
pipeline reads a sentence. No gate asks whether a reel makes a point,
completes an answer, follows its last line from its setup, or belongs
where it sits. Reels 04, 10, 13, 14 and 27 all failed on this and
every existing gate passed them - every gate is
arithmetic/geometry/timing.

What was wrong with each, from the record
-----------------------------------------
- Reel 04: the reel's entire point ("one is a search engine, the
  other is a decision engine") sits one sentence past the cut; the
  reel plays setup with no takeaway, then a shared website-checkout
  closer about nothing it set up.
- Reel 10: pure selection - after the audit verdict the reel jumps
  ~18 master-minutes to a sales pitch instead of playing the
  answer ("your website is your resume ..."), which exists verbatim
  immediately after.
- Reel 13: a 7.6s span from moment 5's pool plays after the reel's
  own closer, cut mid-thought. (`reel_ledger` measured the mechanism
  afterwards: the snap widened the closer end outward through the
  next segment, so the kept sequence carries it - which is why a
  reading over the kept words sees it.)
- Reel 14: setup good, closer good, middle missing - "that's crazy"
  then the pitch, with the actual substance (the biggest geo
  mistake, what AI rewards instead) never chosen.
- Reel 27: the shared website-checkout closer opens on "So..." with
  no antecedent and argues nothing about reviews; the answer itself
  ends cleanly one clip earlier.

The check
---------
A post-build reading over the reel's KEPT WORD SEQUENCE - text
already on disk via the transcript mapping (`reel_ranges` over the
transcript, the one place the play order is spelled), roughly 2k
tokens per reel. No render, no audio, no re-transcription. Three
questions: what is the point (quoted), does the last line follow
from the setup, does any span belong to another moment's bounds.
The engine checks every quote against the kept words and derives
`coherent` / `incoherent` from the model's own categorical answers;
a recorded `incoherent` over fresh words REFUSES that reel's
promotion, per reel, while passing siblings promote.

Why this shape, and what it is not
----------------------------------
A MACHINE gate, not a person: the captain ruled that an asked-for
edit is the approval, and he will not watch every reel through. So
this refuses by itself on a recorded reading and never routes a
judgement to him.

NO NUMBER DECIDES IT (standing ruling 2026-09-16). There is no
confidence threshold, no score floor and no count of flagged spans
here - not even a generous one. Numeric fields beside an answer
(`score`, `confidence`, `rating`, ...) are DROPPED unread. The
foreign-span rule is existential containment verified
deterministically (a quoted span whose master seconds lie outside
the reel's own declared windows and inside another moment's
declared body), the same class of measurement as
`closer_fit._overlaps` - a misplacement established, not a
threshold crossed. `EPSILON` is float hygiene borrowed from
`reel_ledger`, never a tolerance for content.

THE JUDGE IS ASKED FOR A READING, NEVER FOR A VERDICT
(`reel_quality_bar`): the prompt carries the kept words and nothing
else - no seam marking (a reader told which lines are the ending
reads them as the ending they are meant to be), no verdict
vocabulary. The engine derives the verdict afterwards from the
three answers, and every quote is checked by containment first. An
answer that cannot be checked (unguarded quotes, a foreign span in
no sibling's bounds, an empty reason) reads UNJUDGED - failed
evidence, never evidence of failure - and an unjudged reel
promotes with its report. A gate that strands work it cannot judge
is worse than none (AGENTS.md 10.4).

Reuse is NOT a defect here either: a closer shared with other reels
is inside the reel's own declared closer window, so it can never
verify as foreign, and sharing one is never itself a reason the
reading flags. Which question decided a refusal travels as
`decided_by` (`point` / `ending` / `foreign`) with the model's own
reason carrying the why.

Discrimination (the failure mode this is designed against)
---------------------------------------------------------
A coherence reading was REMOVED on 2026-09-18 for firing on 29 of
31 moments including 20 of the 22 it approved - a signal that says
the same thing about everything is worse than none. So before this
refuses anything, the record it has to beat: the five failing kept
sequences above read INCOHERENT (04 via point+ending, 10 via
point+ending, 13 via ending+foreign, 14 via point+ending, 27 via
ending), and the five fixed sequences from the same report (each
reel ending on its own thesis) read COHERENT.
`tests/test_reel_thesis.py` pins that separation on fixtures built
from the report's verbatim quotes. If a future batch shows this
reading constant across approved and rejected alike, DEMOTE it to a
report exactly the way the coherence warning was removed - do not
calibrate it with a number.

Where it runs
------------
- `survey` renders one prompt per approved reel; the model answers
  each; `record` files the answers into the sidecar
  (`review/reel_thesis.json`). A build places, it does not ask -
  verdicts come from the sidecar, never from a fresh judgement.
- The promotion gate (`gate_promotion`, called by
  `reel_build.promote_staged_reels` - the one path both the inline
  build and the `verify_reels` node promote through) recomputes
  each staged reel's kept words, compares the content hash, and
  refuses the reels with a fresh recorded `incoherent`, per reel.
  Missing sidecar, stale words, unjudged reels and unreadable
  inputs all REPORT and promote: an instrument must never fail the
  build it instruments.

`tests/test_reel_thesis.py`.
```


## The acceptance tests

Module docstring of `tests/test_reel_thesis.py`, moved verbatim on
2026-10-02 when the test file kept only its invariant.

```text
The thesis check separates the five incoherent reels from their fixes.

Gap G1: reels 04, 10, 13, 14 and 27 failed on thesis and every gate
passed them. `library/tools/reel_thesis.py` reads each reel's kept
word sequence with three questions and refuses promotion on a fresh
recorded incoherent. The acceptance test is not that the check runs -
it is that it would have caught those five while passing what is
fine.

Fixtures below are built from the scout report's verbatim quotes
(`data/vep-ft-structural-reels/report.md` in the firstmate home):
each failing reel's kept words, and the same reel's fixed kept words
("after fix the reel says"). The readings are the model's half,
hand-written as the survey would record them; the VERDICTS are
derived by the engine and asserted here. Five read incoherent, five
read coherent - the separation the removed coherence warning never
showed.

These tests pin what is measured, what is deliberately NOT decided
(no score, no threshold, no count anywhere), and that an answer that
cannot be checked reads as unjudged rather than as a verdict.
```
