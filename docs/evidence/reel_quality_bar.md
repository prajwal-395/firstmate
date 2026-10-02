# `library.tools.reel_quality_bar` - the history behind its contract

This is the module docstring of `library/tools/reel_quality_bar.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The captain's four qualities, each held the way its KIND allows.

The captain's words, 2026-09-06, verbatim:

    "given all the raw footage, the goal is to create as many good reels
    as possible -- ones that have some kind of call to action, that
    provide value, are coherent in the conversation and what is being
    said, and are around the time frame we want the reels to"

    "i want to know that the quality of videos you produce before any
    visual effects and stuff get implemented are good enough and those
    qualities are generalized into the pipeline"

Four qualities.  Two of them are EXACT and two of them are JUDGEMENT, and
the whole design of this module is that those two kinds are held
differently rather than averaged into one number.

What was there before, and what was missing
-------------------------------------------
`reel_conformance_verifier` had nineteen checks and every one was
mechanical: format, item count, picture holes, audio holes, caption
timing, caption overlap, styling, plan-matches-timeline.  Not one asked
whether the reel was any good.  `reel_exchange` computed
`within_length_guidance` and only REPORTED it.  So a reel could pass
every check the pipeline had and still be worthless, and on 2026-09-05
nineteen of them did.

Measured on the field test's own approved plan (25 moments, 2026-09-06)
before a line of this was written:

- eight ran outside the captain's own 45-90s guidance - four of them
  under 45 and one at 108.1s - and nothing failed;
- seven named no closer at all;
- one 4.4-second closer closed seven reels and another closed five, and
  nothing counted them.

Every one of those is exact.  None of them was being held.

The two EXACT qualities
-----------------------
**DURATION, which is MEASURED exactly and never judged.**  The 45-90s
window came OUT of this module on 2026-09-18 (captain, option c:
"Delete the gate; the script reading judges length as part of
judging the reel").  The brief already said "no fixed target",
"preferably" and "No hard cap", and the standing ruling behind the
removal (2026-09-16) is "there is no hard coded number that hits
this" - so there is no band here any more, and nothing in its place:
not a wider window, not a warning threshold, not a configurable
default.  `DURATION_GATE_REMOVED` keeps that ruling; the 2026-09-09
demotion to a warning it supersedes stays below as history, with the
harvest-batch numbers that forced it.

What the bar still records on every reel is how long it RUNS -
`duration_reading`, reconciled with what the build appends after the
body.  The reel the old warning called 5.5s short built at 45.92s,
because the judged figure never included the 3.8s standard ending
(the freeze tail plus the tail card).  A figure that omits the ending
is wrong wherever it appears, so the ending is resolved with the
owners' own functions (`reel_ending`, `reel_build.plan_cards`) and the
reconciled figure is the only one this module reports.  Nothing here
restates the ending's length: a hardcoded 3.8 would be the same
standing-ruling violation as a hardcoded 45.

Length reaches the model as one input among others: step 3.05 sends
`runs_for_seconds` beside the lines, reconciled the same way, and no
finding, score or tolerance is computed from it here.  Tonight's
script QA judged 22 reels on their words and length never decided a
verdict; the reading does the job the gate was pretending to do.

The ERROR that remains is `reel_exchange.ABSURD_SECONDS`, imported and
not restated: past five minutes a "reel" is most of the episode.  That
is MECHANICAL rather than editorial (AGENTS.md 10.5) and it is the only
length bound the brief leaves standing.  It fired on 0 of the 31, and
that is said here rather than left to read as coverage.  The brief
declares no floor at all, so this module holds none.

**THE LENGTH GUIDANCE.**  The selector still weighs the captain's
45-90s brief while choosing - a story that needs 95 seconds to finish
is a real answer and `exchange_windows` deliberately stopped
truncating at 90.  The BAR reports no band.  Those are two different
moments and the difference is the point: guidance the model weighs
while choosing is the selector's; once the choice is made the reel is
judged on its words.  Nothing here shortens, drops or rewrites a
reel.

**A CALL TO ACTION.**  Three things, all exact:

1. *One exists.*  Not "the moment declared a `cta` field" - a reel whose
   body already ends on a spoken invitation needs no declared closer and
   was never a defect.  What is checked is whether the seconds the reel
   PLAYS LAST are a call to action, and the evidence for what counts as
   one is the batch's own: `declared_closers` collects every span any
   moment in the batch named as its closer, and a reel whose tail plays
   one of them ends on one.  The alternative was a word list, which is
   the defect `sfx_library` exists to have removed (AGENTS.md 10.5).
   A batch that declares no closer anywhere establishes nothing, and
   every reel in it reads ABSENT - which is the correct answer, not a
   failure of the instrument.
2. *It is inside what the reel plays.*  `reel_build.reel_ranges` lays the
   closer down last, so this is structural - but a plan is a FILE THE
   CAPTAIN EDITS, `read_proposal` does not re-run validation, and the
   proposal file says so in its own instruction line.  So the checks
   `validate_proposal` ran when the proposal was WRITTEN are run again
   here against what will actually be played.
3. *Whose it is.*  How many reels close on the same seconds is counted
   and named.  It is REPORTED and does not fail, and that is not
   softness: `reel_proposal`'s own docstring rules that the same CTA may
   close any number of reels, and six spoken closers covering sixteen
   reels is the mechanism working.  A gate that FAILS correct output is
   no more coverage than one that cannot fail (AGENTS.md 10.4).  What was
   missing was never a rule - it was the COUNT.  Nothing said "this
   4.4-second sentence is the ending of seven of your nineteen reels",
   and a reader who cannot see that cannot rule on it.

The two JUDGEMENT qualities, and why they are not asked for
-----------------------------------------------------------
**COHERENCE** - "a stranger who has never heard the episode can follow
it" - and **VALUE** - "does this hand a viewer something they can use" -
cannot be computed.  They go to a model.

**COHERENCE RECORDS AND DOES NOT GATE (2026-09-06).**  Asked as "does
this reel lean on anything unheard", it read not_followable on 31 of 31,
and a second reader that never saw the first reproduced that exactly.
Any 40-second clip pulled out of a conversation leans on something, so a
careful reader always finds one; a column constant across a batch
carries no information about that batch.  Four deterministic halves were
measured against those same 31 reels and every one failed - one was
constant, one measured the transcript instead of the reel, one measured
a different property, and one moved with a window width nobody could
source.  `COHERENCE_DOES_NOT_GATE` carries each with the number that
killed it.

What survives is the recording without the signal: `coherence_of` and
`dependency_positions` are still derived on every judged reel, and the
QB-NOT-FOLLOWABLE warning that used to carry them was REMOVED on
2026-09-18.  It fired on 29 of 31 script-QA moments, including 20 of
the 22 the reading approved - a column constant across approved and
rejected alike carries no information about either, and a signal that
says the same thing about everything is worse than none because it
looks like diligence.  Calibration was already tried: four
deterministic halves were measured against the 31 and every one
failed (`COHERENCE_DOES_NOT_GATE` carries each with the number that
killed it), and the model-dependent half moves with which reader read,
so deriving a verdict from it would be enforcing a model's opinion
with an arithmetic step in front of it.  REMOVE was the honest one of
the two options, and the positions - which are exact - stay as
evidence a reader can weigh.

The hard part is that a recorded judgement must be worth something.  A
model asked "is this good?" that answers "yes" has told you nothing, and
on 2026-09-05 a model was handed the criteria it would be graded on and
duly graded itself well.  Two mechanisms stop that here, and both are
structural rather than a matter of prompt discipline:

**1. The judge is never asked for a verdict.  It is asked for a
READING.**  What does this reel claim, quoting it; what could a listener
repeat or act on afterwards, quoting it; how does it open and how does it
end, quoted; what does it refer to that a listener could not know from
the reel itself, quoting where; where does it stop adding anything.  Not
one of those questions has a good answer and a bad answer.  The ENGINE
derives the verdicts from the reading afterwards
(`coherence_of`, `value_of`), and the mapping is not in the prompt.  A
judge that does not know which way an answer counts cannot flatter
itself.

**2. Every reading is CHECKED against the reel's own words.**  Each
observation carries a QUOTE, and a quote either appears in what the reel
plays or it does not - there is no fraction, no similarity and no
threshold.  A reading whose quotes are not in the reel is REFUSED and
recorded as refused; it is never stored as a verdict.  That is the whole
of "a judgement that cannot be checked against the transcript is not
evidence, and you should say so rather than storing it", implemented:
`check_reading` returns the reasons and `judge` keeps them.

`FORBIDDEN_IN_THE_ASK` is the third leg and it is mechanical: the words
that would hand the judge the criteria - coherent, value, quality, good,
pass, fail, atomic, hook, call to action, 45, 90 - may not appear in what
the judge is sent, and `assert_ask_is_uncontaminated` raises if one does.
`tests/unit/reels/test_reel_quality_bar.py` runs it over the real handoff, so the
guarantee does not depend on anyone remembering it.

The judge is also given the reel's WORDS AND NOTHING ELSE.  Not its
slug, not the `reason` the selector wrote for it, not its `hook` or
`close` or `value` fields, not its measurements, not its findings.  The
selector's handoff states the criteria in full and asks the selector to
argue for its own choices; a judge that read that argument would be
grading the argument.

The ranking
-----------
"As many good reels as possible" needs an ordering, and an ordering is
the one form of judgement this repository already knows how to take:
**it is an ORDERING and there is NO SCORE** (`passage_engagement`, and
the captain's ruling of 2026-09-02 behind it).  The judge places the
batch in one ordering with a one-sentence basis each; a reel it declines
to place reads UNJUDGED and is never coerced to last or to zero.
`ranked` is the reader, and `compare ranks only near the top` applies
here for the same reason it applies there.

Nothing here drops a reel.  The bar REPORTS - per reel, with its
findings, its derived verdicts and its place in the ordering - and the
caller decides.  A funnel that discards without saying why is the defect
`reel_exchange` was rebuilt to remove, and it would be the same defect
here.

Reachability
------------
    python3 -m library.tools.reel_quality_bar --project <project_folder>
    python3 -m library.tools.reel_quality_bar --project <p> --json

`tests/unit/reels/test_reel_quality_bar.py`.
```

## `tests/unit/reels/test_quality_bar_thesis_ending.py` module docstring (moved 2026-10-02)

```text
A closer-less reel a project declares ends on its own thesis passes.

Reel 27 (2026-09-19): Q->A on Google reviews, shared website-checkout
closer deleted as topically alien, no own-thread replacement in the
episode. `declared`/`in_body` cannot express that - a closer inside
its own body is refused as a double play - so QB-CTA-ABSENT failed a
reel ending exactly where its authorised re-cut puts it (AGENTS.md
10.4). A HAND-WRITTEN `external/reel_ending.json` entry the reel
honours (anchor measured in timed words or present in the approved
transcript preview) now reads `thesis`, never ABSENT.

Fail-closed both ways: undeclared absent reels still fail, and a
declaration whose anchor is neither timed nor in the approved preview
fails too - a declaration nobody honours is not a pass.

`library/tools/reel_quality_bar.py` (`thesis_reading`,
`cta_reading`, `exact_findings`, `judge`); declarations owned by
`library/tools/reel_ending.py`; wired into the gate in
`library/tools/reel_conformance_verifier.py`.
```
