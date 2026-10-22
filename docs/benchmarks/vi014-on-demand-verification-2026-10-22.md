# VI-014: on-demand verification of a span a plan depends on

Lane: `p-vi014-on-demand-verify`. Date: 2026-10-22. Base: `3c04310c6`.

## What the punch list asked

> VI-014 — Add on-demand verification.
>
> - Let the agent request more frames, a longer video segment, speech
>   context, or an alternate camera when uncertainty matters.
> - Bound the resource budget of these requests.
> - Reuse already computed observations and decoded media.
> - Persist new evidence with accurate provenance and coverage.
> - Stop investigating when further analysis is unlikely to affect the
>   decision.

The firstmate spec added the integration point and the measurement:

> When a planned edit depends on a claim that is inferred or unmeasured,
> run the cheapest targeted check on that exact span before execution and
> record the result. Measure on the M0 scenarios and one real reel plan:
> claims verified on demand, verification cost in seconds, and wrong-span
> edits avoided, before vs after, with denominators.

## What already existed, and what was missing

The ORDINAL half of VI-014 was done:

- `library/tools/temporal_query.py` parses "the second time she opens
  the box" (`parse_ordinal_reference`) and resolves it to exactly one
  event (`resolve_ordinal_reference`), refusing rather than falling back
  to the nearest plausible moment.
- `library/tools/occurrence_identity.py` numbers the repeats of one
  action inside the canonical Project Model, so an ordinal survives a
  re-measure of the same source.

The VERIFICATION half was missing, and it was missing in a way that
reaches the timeline:

- **A reel moment's `source_spans` are a rebase, and a rebase was never
  checked.** `reel_proposal.enrich` derives each span from the master
  timeline's transcript rows, mapped onto the source files' own
  timebase. That mapping is an INFERENCE: it says "these source seconds
  carry these words" because the master timeline's rows say so. Nothing
  compared it against the SOURCE FILE's own transcript — an independent
  measurement of the same speech.
- **A plan's spans were checked against the master timeline when the
  plan was written, and reading it back does not re-run those checks.**
  The proposal file says so in its own instruction text. Between the
  write and the build, nothing re-asks.
- **The verifiers existed but could not be aimed at ONE span.** The
  existing lanes answer a whole source (M1's transcript, M2's frame
  sample, M8's verdicts) or a whole clip. There was no way for an agent
  to say "this one span, this one question, inside this budget".
- **Nothing bounded the cost of asking.** There was no ceiling to fail
  against and no cost to report.
- **Nothing was persisted.** A verification's answer lived in the run
  that produced it, so the same question was measured again next time.

## What landed

1. **`library/tools/on_demand_verification.py` (new).** The lane. Four
   request kinds — the punch list's own vocabulary — and one routing
   table, `PRODUCERS`. Each producer answers ONE request kind about ONE
   span and never returns a span: the plan owns where the cut is.
   - `MORE_FRAMES` reads the source's own frame sample and reports
     WHICH frames of the span it holds. It costs no decode.
   - `LONGER_SEGMENT` widens the window the plan named, because a
     boundary claim can be wrong by being just outside it.
   - `SPEECH_CONTEXT` answers from the source's own transcript: the
     cheapest check there is, and the one that decides a claim about
     words.
   - `ALTERNATE_CAMERA` names the other camera of the same instant from
     the measured multicam clock, and says that no frame of it has been
     inspected.
   Every outcome carries a `Verdict` (`supported` / `contradicted` /
   `unresolved`), a `StopReason`, the COVERAGE it inspected, its cost in
   seconds, and its provenance.
   - **The budget is checked BEFORE the work, not after it.**
   - **A recorded verdict is the answer the second time**, keyed by the
     source's content digest, the span, the claim and the kind.
   - **A single word can never contradict.** Two transcriptions of the
     same audio spell a number differently; one differing word is
     ordinary variance, and calling it a disagreement would make the
     lane cry wolf. Two or more claimed words, all absent from the span
     while this same source measures them elsewhere, is a contradiction.

2. **`library/tools/plan_span_verification.py` (new).** The reader of a
   reel plan. It derives each span's claim from the master transcript,
   asks the lane about it, and writes a report with the denominators:
   spans total, decided, unverified, supported, contradicted, and the
   cost.

3. **`library/tools/reel_build.py`.** The build now verifies the plan's
   source-span claims at the PLAN READ-BACK, before any media is
   placed, and records the report in the project's review area. It is a
   REPORT, not a refusal: the captain's ruling that a report-only check
   does not block a build stands for this check too, and a span the
   check cannot decide is reported as undecided rather than dropped.

4. **A source-memory slot.** Verdicts are filed beside the measurements
   they were made from, under `on_demand.json` (lane `vi-14`), with the
   evidence envelope the other lanes use.

## The measurement

Run: `python3 docs/benchmarks/vi014_on_demand_verification.py`.

### The M0 scenarios

The M0 fixture is the device take: 48.0 s at 30 fps, sampled at a
nominal 2 Hz into **11 frames**, with gaps of up to **8.333 s**. The
ordinal the punch list names is "the second time she opens the box".

| | Value |
|---|---|
| Occurrences of `hand_near_mouth` in the canonical model | 6 |
| The second occurrence's ordinal index | 2 |
| Its certainty | `inferred` (every occurrence carries it) |
| Its span | 68.167–70.101 s |
| Frames the fixture's own sample holds | 11 (2 Hz nominal) |
| Largest gap between sampled frames | 8.333 s |
| Frames inside the adjudicated second-occurrence band 33.30–33.70 s | **0** |
| Nearest sampled frame to that band | t=33.233 s, frame index 997 |
| That frame's error against the band | 0.067 s (2 frames at 30 fps) |

Two on-demand requests were made on that exact span:

| Request | Verdict | Stop reason | Cost | Repeat |
|---|---|---|---|---|
| `more_frames` | `unresolved` | `uncovered` | 0.017–0.019 s | 0.000 s, reused |
| `longer_segment` | `unresolved` | `no_further_producer` | 0.001–0.002 s | 0.000 s, reused |

The band is **agent-adjudicated and provisional**; the baseline report
records that a human must re-adjudicate it before the fixture is a
scored benchmark. It is used here as the CLAIM the lane is asked to
check, not as a ground truth.

**What this measures.** The M0 ordinal answer is an INFERENCE over a
candidate set: the canonical model carries `certainty: inferred` on every
occurrence, and the fixture's own 2 Hz sample holds **no frame at all**
inside the adjudicated band, so the coarse lane cannot see the onset it
is being asked about. The lane says exactly that — `uncovered`, with the
coverage attached — instead of returning a plausible answer. The
alternative camera request names the other camera of the same instant
and reports that no frame of it was inspected.

### One real reel plan

The 31-reel plan of the scratch podcast project, and the source spans it
names.

| | Before | After |
|---|---:|---:|
| Spans whose claim is verified before execution | 0 of 590 | **507 of 590** |
| Spans whose claim is contradicted | — | **0** |
| Spans left undecided | — | **83 of 590** |
| Verification cost, first sweep (7 source transcripts read, 590 verdicts computed, 7 slots written) | — | **2.3–4.8 s** (0.004–0.008 s per span) |
| Verification cost, second sweep (every verdict recorded) | — | **0.4–1.4 s** |
| Claims re-measured on the second sweep | — | **0** |

Denominators: 31 moments, **590** source spans, of which 507 carry a
claim the source's own transcript decides.

**The 83 undecided spans are named, and they are a MEASUREMENT rather
than a gap.** 31 of them are spans the master transcript places no word
in (the plan's span covers a passage the master rows do not cover at
that rebase), 5 carry a claim with no content word to look for, and 47
are spans where the two transcriptions differ on one or two words —
spelling variance between the master mix and the source's own audio.
**Zero spans are contradicted**: on this corpus the plan's rebase agrees
with the source files' own transcripts wherever the two can be compared.
That is the honest result, and it is the result the captain's own re-cut
history predicts: the rebase is sound, and the lane's job is to say so at
a measured cost rather than to find a defect that is not there.

### Wrong-span edits avoided

**Before: 0 wrong spans detected, because nothing looked.** After:
590 spans are checked before the build places media, and **0 wrong
spans are found on this corpus**. The denominator is explicit: 0 of 590,
and the lane reports 507 decided / 83 undecided rather than a single
verdict.

This is a negative result on this corpus and it is reported as one. The
lane's value here is that it converts an UNCHECKED claim into a CHECKED
one at 0.004-0.008 s per span, and it makes the 83 spans whose claims cannot
be decided VISIBLE to a reader - which is what the M0 scenario shows the
cost of not doing: 0 frames inside the band, 8.333 s gaps, and an ordinal
answer carried as `inferred`.

### What was NOT measured

- **The wrong-span edits avoided on a corpus that HAS them.** No such
  corpus was available in this lane: the M0 fixture's occurrences are
  candidates and the reel plan's rebase is sound. The lane's
  contradiction path is exercised by unit tests with a synthetic wrong
  span, not by a real one, and that is stated here rather than implied.
- **A live reel build.** The measurement is the plan-level sweep, which
  is what runs before a build places media. No Resolve build was run:
  this lane does not touch the captain's timelines, and the machine's
  OpenCV 5 has no Haar cascade for the reels punch-in (see below).
- **The other three request kinds on real footage.** `more_frames` and
  `longer_segment` are measured on the M0 fixture; `speech_context` is
  measured on the reel plan; `alternate_camera` is exercised by unit
  test only, because no fixture on this machine carries a measured
  multicam group whose alternate camera has frames of its own.

## The pre-existing failures this lane did NOT fix

Both are red on `origin/main` (`3c04310c6`) and unchanged by this
branch:

- `tests/unit/context/test_ledgers.py::test_preflight_shared_code_references_are_declared`
  — 13 shared modules are referenced by preflight steps and not declared
  in `code_identity.STEP_IMPLEMENTATION_DEPS`. This branch's two new
  modules ARE declared, in the five preflight steps that declare
  `reel_build.py`, so the failing list is byte-identical to main's.
- `tests/scenarios/test_golden_conversation.py` and
  `tests/scenarios/test_reel_preview.py` — this machine's OpenCV 5
  dropped Haar cascades, so the reels punch-in refuses to aim.
- `ruff check --config ruff-ci-gate.toml library/ tests/` reports one
  `F811` (`span_frames` redefined in `reel_build.py`), also on main.

## Tests

- `tests/unit/tools/test_on_demand_verification.py` — 23 tests, each
  naming one defect the lane closes.
- `tests/unit/tools/test_plan_span_verification.py` — 9 tests, the same
  shape for the plan reader.

## Left undone

- The `alternate_camera` producer names the other camera and its
  measured group but does not inspect a frame of it; a producer that
  decodes and shows that camera's picture is the next step, and the
  request kind is already routed.
- The lane has no producer that runs a MODEL over the span. The
  cheapest check (the source's own transcript) decides the claims this
  corpus carries; where it cannot, the lane reports `unresolved` rather
  than spending a model call it has no budget for.
