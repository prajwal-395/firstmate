# The spoken-lines view - the history behind its tests

Moved from the module docstring of `tests/unit/audio/test_spoken_lines_view.py` on
2026-10-02, when that file kept only its invariant. The test and
`library/tools/context_views.py` are the contract; this is the story.

```text
The selector can see a SUB-TURN boundary, and still cannot see the noise.

#583 took the raw `timeline_transcript` document out of step 3.04's
prompt: 817,316 characters, 8,509 per-word timing records, 940 absolute
source paths and 940 Resolve item ids, to say 47,182 characters of
English.  None of that should ever come back.

It also took away the only thing carrying the boundaries INSIDE a
speaker's turn, and nobody noticed because the pre-bridge is the
document's only reader in CODE - the model was reading it too.  Measured
on the field-test episode of 2026-09-06:

  * the run that still had the document timed a reel's closer to
    `Akshita 337.59-341.27`, a boundary that exists only in `segments`;
    her turn is 328.61-341.27;
  * the run after it was projected away said, unprompted, that *"every
    boundary I can name is a TURN boundary, because `turns` is the only
    speech table I was given"*, and opened its reel on "well this has
    been fun recently" - which the brief names as throat-clearing -
    because the line that should have opened it sits inside a turn;
  * 929 bound segments collapse into 136 turns, so 793 of the places a
    reel may start or stop disappeared in the collapse;
  * and the step's own post-bridge snaps every chosen boundary OUT to a
    bound-segment edge (`reel_proposal.snap_to_speech`), so the two
    halves of one step disagreed about what a boundary IS.

So this file holds both directions at once, because a gate that can only
fail one way is not a gate (AGENTS.md 10.4):

  * the sub-turn boundaries and their words REACH the prompt, and
  * the word timings, source paths and item ids still do NOT.

Every assertion here fails at `origin/main` (20dde19) or would fail on
the revert that would be the lazy way to buy the first half.
```
