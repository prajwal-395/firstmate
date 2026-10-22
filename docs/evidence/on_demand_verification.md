# On-demand verification: history

History moved out of the module docstring; the module keeps the contract
and the tests keep the invariant.

Lane `p-vi014-on-demand-verify`, 2026-10-22, base `3c04310c6`. The
measurement is `docs/benchmarks/vi014-on-demand-verification-2026-10-22.md`
and the script that produced it is
`docs/benchmarks/vi014_on_demand_verification.py`.

**The rule.** A planned edit that depends on a claim about a span
verifies that claim ON DEMAND, on that exact span, before the edit
executes - and the verification is bounded, reused, persisted with its
provenance and coverage, and stops when more analysis cannot change the
decision.

**What happened.** The punch list's VI-014 asks for on-demand
verification: *"Let the agent request more frames, a longer video
segment, speech context, or an alternate camera when uncertainty
matters. Bound the resource budget of these requests. Reuse already
computed observations and decoded media. Persist new evidence with
accurate provenance and coverage. Stop investigating when further
analysis is unlikely to affect the decision."*

The ORDINAL half of VI-014 was done: `library/tools/temporal_query.py`
parses "the second time she opens the box" and resolves it to exactly
one event, and `library/tools/occurrence_identity.py` numbers the
repeats of one action inside the canonical Project Model. The
VERIFICATION half was missing, and it was missing in a way that reaches
the timeline.

A reel moment's `source_spans` are a REBASE: `reel_proposal.enrich`
derives each span from the master timeline's transcript rows, mapped
onto the source files' own timebase. The mapping says "these source
seconds carry these words" because the master timeline's rows say so -
it is an INFERENCE about the source file. Nothing compared it against the
SOURCE FILE's own transcript, an independent measurement of the same
speech. The proposal file says in its own instruction text that the
spans "were checked against the transcript when it was written, and
reading it back does NOT re-run those checks", so between the write and
the build nothing re-asked. The existing verifiers could not answer the
question either: the lanes read a whole SOURCE (M1's transcript, M2's
frame sample, M8's verdicts) or a whole clip, and there was no way to
aim one at ONE span, no ceiling to fail against, and nothing persisted
- so the same question was measured again on the next run.

**What it does now.** One enumeration, `library/tools/on_demand_verification.py`:
the four request kinds (`RequestKind.MORE_FRAMES`, `LONGER_SEGMENT`,
`SPEECH_CONTEXT`, `ALTERNATE_CAMERA`) and one routing table,
`PRODUCERS`. A producer answers ONE kind about ONE span and never
returns a span - the plan owns where the cut is. The reader of a reel
plan is `library/tools/plan_span_verification.py`; the build calls it at
the PLAN READ-BACK, before any media is placed
(`library/tools/reel_build.py`), and records the report in the project's
review area. The verdicts are filed in the source's own memory under
`on_demand.json` (lane `vi-14`), beside the measurements they were made
from.

**The rules that keep it honest**, each one a test:

- **The budget is checked BEFORE the work.** A request with no budget
  measures nothing, because a cost nobody bounded is a cost nobody read.
- **A recorded verdict is the answer the second time.** The key names the
  source's CONTENT DIGEST, the span, the claim and the kind, so a verdict
  is reused for the same question and never for a different one - and a
  file whose bytes changed has a different digest and no old verdict.
- **A single word can never CONTRADICT.** Two transcriptions of the same
  audio spell a number differently ("twenty" against "20"); one differing
  word is ordinary variance, and calling it a disagreement would make the
  lane cry wolf. Two or more claimed words, all absent from the span
  while this same source measures them elsewhere, is a contradiction.
- **An undecided claim is `unresolved`, never "wrong".** The source's
  own transcript may simply spell the words differently, and a lane that
  read a gap as a refutation would be worse than no lane.
- **The coverage travels with the answer.** A partial answer cannot read
  as an answer about the whole span.
- **The check REPORTS; it does not refuse.** The captain's ruling that a
  report-only check does not block a build stands here too.

**Measured**, on the M0 scenarios and one real reel plan
(`docs/benchmarks/vi014-on-demand-verification-2026-10-22.md`, script
`docs/benchmarks/vi014_on_demand_verification.py`):

- M0 fixture (48.0 s, sampled at a nominal 2 Hz into 11 frames, gaps up
  to 8.333 s): the ordinal the punch list names resolves to occurrence 2
  with `certainty: inferred`; the fixture's own sample holds **0** frames
  inside the adjudicated second-occurrence band, and the nearest sampled
  frame is 0.067 s (2 frames) away. The lane answers `unresolved` /
  `uncovered` with the coverage attached, in 0.017-0.019 s, and the repeat
  costs 0.000 s because the verdict was recorded.
- Real 31-reel plan: **590** source spans, **507** decided (all
  supported), **83** undecided and named, **0** contradicted, at
  **2.3-4.8 s** for the first sweep and **0.4-1.4 s** for the second, with 0
  claims re-measured. Before, **0 of 590** spans were checked at all.

That last number is a NEGATIVE result and is reported as one: on this
corpus the plan's rebase is sound, so the lane's value here is that it
converts an unchecked claim into a checked one at 0.004-0.008 s per span
and makes the undecidable spans visible to a reader.
