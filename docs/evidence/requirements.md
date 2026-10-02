# `library.tools.requirements` - the history behind its contract

This is the module docstring of `library/tools/requirements.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
One owner for "may this step run", and the only vocabulary for saying no.

The defect this exists to remove
--------------------------------
Every step manifest declared `interface.preconditions` and
`interface.postconditions` as prose - `"'audio_spine' exists in state"`,
`"'rough_cut_review.passed' is true in state"`.  126 strings across 29
manifests, and **nothing evaluated one of them**.  The only consumers
were a `library/schema/manifest.schema.json` that required the FIELD to
exist (deleted - nothing ever loaded it) and one test asserting the
substring `"parselmouth"` appeared in step 1.05's list.

Meanwhile the real gating lived in three places that read two different
declarations: `run_scope._assert_dependencies_met` (selection time),
`run_pipeline.gather_step_inputs` (mid-run), and each step's own code.
So the document that READ like the contract was the one with no teeth,
and the contract with teeth could only ever say one thing: *a key is
present*.

That limit is the whole problem.  Measured on `b5f6cdd`, driving the
real `gather_step_inputs`:

    CASE 3 - all keys present, transcript EMPTY, review FAILED:
      NO REFUSAL. inputs = ['audio_spine', 'brand_effect', 'brand_style',
                            'rough_cut_review', 'speech_sequence']

An empty transcript and a rejected rough cut both passed the contract and
reached the step.  End to end through the runner, the rejected cut then
produced four real subtitles, and the empty transcript produced a
`subtitle_plan` with zero entries, printed `✓ Completed`, printed no
warning, and recorded a ledger entry.

So the defect is not "there is no contract".  It is **the contract could
only say a key was present, never that its content satisfied the
requirement.**

The six kinds
--------------
All six are MECHANICAL.  None encodes taste and none carries a number
this module invented (the standing ruling on thresholds - a mechanical
proxy fitted to the captain's verdicts failed to predict them, so what is
checkable here is presence, provenance from a declared list, and
buildability; coherence is not).

* ``state_key``   - a key is present.  **Auto-derived**, never hand
  written: `derive_state_keys` wraps `run_scope.prerequisites`, which is
  the same reading of `inputs[].required` + `data_mapping` that
  `gather_step_inputs` raises on.  One derivation, so the refusal and the
  crash it prevents cannot drift apart.
* ``predicate``   - a value satisfies a test.  No expression before this
  module.
* ``environment`` - the machine can do the work.  No expression before
  this module, which is the structural reason "Node.js and npx are
  available" could only ever be prose.
* ``coverage``    - the data spans what was asked for.  No expression
  before this module.
* ``verdict``     - a node produced its judgement.  The captain's ruling,
  2026-09-23: a verdict is expressible as a goal, AS ITS OWN KIND - not
  by loosening what a requirement means.  A verdict requirement is a
  GOAL ONLY: it names a producer and no consumers, so no run ever asks
  it as a precondition and the composer alone reads it.  Making a
  node's own judgement its prerequisite would be the circular
  `sfx.index_loads` shape recorded under DELETED - the step refused
  before the step that diagnoses the problem could say so.  Scoped to
  the three VERDICT rows of `operations.EMPTY_EFFECT_REASONS`
  (`VERDICTS` below); the artifact cases are a separate question and
  get no requirement here.
* ``optional``    - an edge that MAY OR MAY NOT carry state.  The
  captain's ruling, 2026-09-23, board answer "add-optional": ADD
  OPTIONALITY TO THE VOCABULARY.  An optional requirement is a GOAL
  ONLY, like a verdict: it names the producing node and no consumers,
  so no run ever asks it as a precondition and the composer alone
  reads it.  Asking one as a precondition would be either vacuous (an
  absent optional input is legitimate, so it could never refuse) or a
  lie (refusing one would refuse a run the runner accepts) - and a
  requirement that cannot refuse violates the anti-vacuity gate this
  layer is built on.  Scoped to the eight OPTIONAL edges of the seven
  blind nodes (`OPTIONALS` below); the artifact cases stay out, and a
  required edge never needs one.

A refusal says what to run; a pass says how it passed
-----------------------------------------------------
`Satisfaction` is never a bare bool.  `SATISFIED(source)` names one of
`IN_STATE` / `RECORDED` / `SUPPLIED` / `PRODUCED_BY`, and
`UNSATISFIED(reason, ...)` carries `produced_by` - the operations that
would satisfy it.  A refusal that cannot say what to run is not a
refusal, it is a dead end.

Checked against what will EXECUTE, not against the plan
--------------------------------------------------------
`--step` and `--from` narrow the step list AFTER `run_scope.resolve` has
already agreed to the selection (`run_pipeline`, where `steps_to_run` is
built from `scope.steps_to_run`).  So the refusal was computed against
the wider set and the run executed the narrower one, and `--step
plan_subtitles` on a fresh project did not refuse - it died forty lines
later inside `gather_step_inputs` with an unhandled traceback:

    RuntimeError: Step 'plan_subtitles': data_mapping expects key
    'audio_spine' from upstream step 'mesh_spine', but it is missing from
    that step's outputs. Available keys: []

`check` therefore takes the EXECUTE set.  `run_pipeline` calls it after
`--from`/`--step` narrowing and before the first step runs, so a
prerequisite that will not be produced is a REFUSED rather than a step
failure - which matters, because a step failure is recorded and colours
`status` on every later run until that step succeeds.

Witnesses are mandatory, and that is the anti-vacuity gate
-----------------------------------------------------------
A gate that cannot fail is worse than no gate, because it reads as
coverage.  This repository has already paid for that lesson twice, and
there is a third live instance in the reel conformance verifier, where an
empty expected side silently disables a check.

So every `Requirement` must carry BOTH witnesses -
`refuting_context()` and `satisfying_context()` - and they have no
defaults, so a requirement **cannot be registered without them**.  The
registration is the gate; `tests/test_every_requirement_can_refuse.py`
only reads it.  A requirement that genuinely cannot refuse is DELETED,
never exempted.

`tests/test_no_requirement_refuses_correct_input.py` is the mirror, so
the layer cannot be vacuously strict either.

    python3 -m library.tools.requirements          # the registry
    python3 -m library.tools.requirements --kinds  # counts by kind

`tests/test_requirements.py`,
`tests/test_every_requirement_can_refuse.py`,
`tests/test_no_requirement_refuses_correct_input.py`.
```


## Test-module history (moved 2026-10-02)

Module docstrings of the two requirement test files, moved verbatim when the tests kept only their invariant.

### `tests/test_every_requirement_can_refuse.py`

```text
Every requirement must be able to say no, and say what to run.

Why this test exists
--------------------
A gate that cannot fail is worse than no gate, because it reads as
coverage (AGENTS.md 10.4).  This repository has already paid for that
lesson twice, and there is a third live instance:
`reel_conformance_verifier.py:1519` guards its caption check with
`if plan.captions and timeline.caption_items:`, `plan.captions` defaults
to `()` at `:177`, and `:1597` reports `captions_expected=0` - so an
empty expected side silently DISABLES the check and 762 real captions
pass verified by nothing.

Replacing 126 prose strings with executable requirements is exactly the
kind of change that could produce a fourth instance: a registry of
checks that all return SATISFIED because nobody ever fed them a state
they should refuse.

So this walks the WHOLE registry - never a curated list - and asserts of
each requirement that:

* there is a context in which it returns UNSATISFIED,
* that refusal carries a non-empty reason,
* that refusal names what would produce the missing thing, or is an
  environment requirement, where the remedy is in the reason instead,
* and there is a context in which it returns SATISFIED with a declared
  `source`.

The witnesses live on the `Requirement`, not here
--------------------------------------------------
`refuting_context` and `satisfying_context` are constructor arguments
with no defaults, so a requirement cannot be REGISTERED without them.
That is deliberate: a test that built witnesses centrally would be
silently outgrown by a new requirement nobody wrote one for, and would
still pass on the ones it knew.  The registration is the gate; this test
only reads it.

A requirement that genuinely cannot refuse is DELETED, not exempted.
There is no skip list in this file and there must not be one.
```

### `tests/test_no_requirement_refuses_correct_input.py`

```text
The mirror: the layer must not be vacuously STRICT either.

Why this test exists
--------------------
A gate that FAILS correct output is no more coverage than one that
cannot fail (AGENTS.md 10.4).  Converting 126 prose strings into
executable requirements is a change with a specific, predictable failure
mode: the prose was never executed, so nobody ever found out which of it
was WRONG.  Four of the deleted preconditions would each have refused a
CORRECT run:

* `semantic_analysis: 'clip_catalog' exists in state` - 1.03 declares
  only `raw_footage_files` and no edge routes a catalog to it.
* `color_grade: 'brand_template' exists in state` - `gather_step_inputs`
  deliberately does not broadcast it; the resolved template arrives as
  `brand_style`/`brand_effect`, so the raw key is never in state.
* `review_rough_cut` / `color_grade: 'b_roll_interjections' exists in
  state` - both steps declare it OPTIONAL, and a cut with no cutaways is
  a legitimate run.
* `object_segmentation`'s two - written when the step was UNWIRED and
  never ran; wired matte-triggered on 2026-09-24, and the prose pair
  stays deleted while the DAG edges derive real ones.

Each is recorded in `requirements.DELETED` with the reason, rather than
silently dropped: a deleted requirement with no reason reads as an
oversight.  This file is what would have caught them had they been
converted, and what catches the next one.

Two halves, because there are two ways to be vacuously strict
-------------------------------------------------------------
1. A requirement that refuses a state which is genuinely fine.
2. A requirement whose expected side is derived from the same value as
   its actual side, which is tautological - it can never disagree with
   itself, so it passes whatever happens.  That is a structural defect
   and is invisible behaviourally: the check passes, and passing is what
   it looks like when it is broken.
```
