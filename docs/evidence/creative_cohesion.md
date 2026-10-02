# `creative_cohesion` (5.03) - test module history

Moved from the module docstrings of the cohesion test files when the suite was halved (2026-10-02).


## What the cohesion review reports, and to whom (`tests/test_creative_cohesion.py`, merged into `tests/unit/context/test_cohesion.py`)

What the cohesion review reports, and to whom.

Step 5.03 runs next to last.  Every plan it reads is made in steps 4.02
to 5.01, so it cannot be moved upstream of the decisions it reviews - it
would arrive before its own inputs exist.  What it can do is stop
presenting a recommendation it has no route to apply as though it were
one, which is #238.

Three things these tests used to assert, and no longer can:

- **The four energy thresholds.**  "high energy means every transition
  under 500 ms, so make it 10 frames", "calm means a dissolve of at least
  1000 ms, so make it 30", "high means at least 10 SFX per minute", "calm
  means at most 15".  Every one is a creative value the step chose, two of
  them reached the picture through `duration_frames`, and nothing declares
  a pace or a density to derive a replacement from.  Removed rather than
  re-tuned (AGENTS.md 10.5); the step reports the counts instead.

- **`cohesion_score`.**  100 minus a hand-picked weight per finding, read
  by nothing outside the step.  Removed; see the comment at the end of
  `review_creative_cohesion` for the three reasons.
- **`color_grade_spec["mood"]`.**  Step 5.01 emits no such key - project
  001's real spec carries `grade_pipeline`, `fusion_look`, `series_look`,
  `look_notes` and five more - so the substring match ran against "" on
  every real run.  The old fixtures supplied `mood` themselves, which is
  a fixture proving a fixture.  The check is removed with the same three
  reasons the pacing check was (docs/PIPELINE_PLAN.md P4.2).


## Two gates, one that could not fire and one that measured the wrong quantity (`tests/unit/context/test_cohesion.py`)

Two gates in `creative_cohesion`, one that could not fire and one
that measured the wrong quantity.

**The engagement check could not fire.** It tested
`isinstance(hook["engagement"], (int, float))`, and `speech_sequence`
wrote `engagement` as a DICT - `{hook, flow, value, composite,
rationale}`. So `hook_eng` was permanently 0 and `eng_values` permanently
`[]` on every real project. A gate that cannot fail reads as coverage;
this one now compares composites where they exist.

The scorer that produced those composites is itself withdrawn (#236): it
gave nine of project 001's eleven passages an identical 49, and the
composite it fed went the same way on the captain's ruling of 2026-09-02
- only the rank ordering was ever consumed. What writes a judgement now
is step 2.02's own model, asked in the handoff to place the passages it
selected in ONE ordering, and the gate compares nothing else: two
orderings the model itself wrote, the order the passages PLAY in against
the order it RANKED them in. Where a sequence carries no judgement at all
the gate still STATES that it has no basis; see
tests/unit/picture/test_mesh_spine.py.

**The duration check measured the wrong quantity.** It used
`body_sequence[-1]["end_time"]`, a SOURCE timestamp - where the last
passage ends inside its own clip - as the timeline length. For project
001 it reported "actual duration (40.1s) is below the minimum target zone
(54.0s)" about a 54.77 s timeline that `review_rough_cut` had already
measured as inside the zone, one step earlier, from the spine. Both now
call `library/tools/timeline_duration.measure_timeline_duration`.


## A cohesion finding is applicable where it runs, or it is an observation (`tests/unit/context/test_cohesion.py`)

A cohesion finding is applicable where it runs, or it is an observation.

Step 5.03's one finding on project 001's 2026-08-26 run was
`speech_sequence.segment_order`, and step 5.04 refused it: re-ordering the
narrative would invalidate every downstream timing.  `applied_adjustments`
was empty on that run and on every run before it.  A recommendation nobody
can apply, reported in an array named `adjustments`, reads as a change that
was made (#238).

The review cannot move upstream of what it reviews - it reads the
transition, SFX and VFX plans, all made in steps 4.02 to 4.04 - so it is
scoped where it runs instead.  `library/tools/cohesion_scope.py` is that
scope, and these tests hold the two halves of it against the code that
really acts:

- every pair the enumeration calls ACTIONABLE is really applied by
  `apply_cohesion_adjustments`, driven here rather than believed
  (AGENTS.md section 10.2);
- every pair it calls OWNED_UPSTREAM is really refused by it, with the
  enumeration's own sentence;
- a pair in neither list raises rather than becoming an adjustment that
  is then silently dropped.
