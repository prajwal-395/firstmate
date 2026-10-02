# `library.tools.decided_value` - the history behind its contract

This is the module docstring of `library/tools/decided_value.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
decided_value.py - how a creative value gets decided, in one place.

The captain, 2026-09-16, over nine constants that decide creative
outcomes:

    *"i did not allow for any hardcoding when possible anywhere in the
    video editing pipeline -- these all stand as things that should be
    covered by creative reasoning by the LLM to see if music is sitting
    too loud, too soft, or just right. there is no hard coded number that
    hits this. perhaps a formula or some kind of audio anaysis that
    allows that judgement to be made is what im referring to"*

    *"these all seem hardcoded values that may not actually be able to
    generalize. it could very well be possible that we need to use values
    outside of these bounds but it also requires the LLM to be able to
    build the context and understanding, and then from there the
    reasoning to then be able to make those decisions and generalize them
    to what works for a video"*

And, answering what a surviving default is allowed to mean:

    *"the captions that are being rendered is the style that is preferred
    for the Lucie videos, this means that for another project they may
    not prefer that, so it is not something that should be hardcoded
    persay, but rather exist as a fallback if no preference is mentioned
    or there is no other way to see if something better works (like based
    on the video)"*

That is a PRECEDENCE, not nine answers, and this module is the only
implementation of it.

**Why one module and not a sixteenth private ladder.**  The pipeline
already carries ten `BASES` enumerations - `cohesion_scope`,
`cutaway_window`, `explainer_plan`, `motion_graphics_plan`, `reel_look`,
`reel_semantic_visual`, `run_restart`, `speaker_identity`,
`timeline_decisions`, `vfx_plan_basis` - and a further crop of one-off
`*_basis` fields, each a per-module answer to "why is this value what it
is", each invented on the day its own incident landed.  They are good.
There are just ten of them, none is asked the same question the same
way, and so the cheapest correct-looking thing to write for an eleventh
creative value is a constant.  That is how nine constants that decide
creative outcomes came to sit behind a guard written about the incidents
it already had.

**It is step 5.01 generalised, not a new invention.**  `color_grade` was
DETERMINISTIC until 2026-09-03: it measured project 001's nine clips at a
2.7x luma spread and answered with the identity CDL on all nine, because
the only authority that could act on the measurement was a brand-template
slot and 001 names no template.  The remedy was not a different constant -
it was that the decision belongs to somebody, the model read the
measurement, `why` became REQUIRED on every entry, nothing was clamped,
nothing was substituted for a term the answer left out, and
`correction_basis` recorded which of four things an ungraded run is.
Everything here is that, named once so the next value does not have to
re-derive it.

**Five readings, spelled differently on purpose.**

    STATED        the project said so in project.yaml or a brand slot, or
                  the user said so in the per-user taste profile. The
                  project wins; the record names the source and speaker.
    DIRECTED      a value the creative direction really DECLARED.  A rule
                  acting on a declared value is not a fallback (AGENTS.md
                  10.5), which is what makes this rung legal at all.
    REASONED      the model answered THIS RUN, over measurements it was
                  SHOWN.  The record carries its own `why` and the
                  measurements it read.  An answer with no `why` is
                  DROPPED, not kept - 5.01's rule, unchanged.
    FALLBACK      nothing above answered and the slot has a REGISTERED
                  fallback.  Recorded AS a fallback, naming WHOSE
                  preference it is and what would have superseded it.
    UNDETERMINED  nothing above answered and there is no registered
                  fallback.  There is NO VALUE.  The consumer drops with
                  the reason or refuses.  Never 0, never a stand-in.

That is the same line this repository draws everywhere between an
admitted absence and a measured emptiness: `usable_ranges` `[]` with
method `unmeasured` against `[]` with `deterministic_v1` (AGENTS.md
10.3), `primary_subject_visible` None against `[]`,
`undetermined`'s three states.

**A rung that cannot be reached is SKIPPED WITH ITS REASON, never
guessed through.**  If a slot's measurement was not taken - an unmeasured
bed, a hollow prosody profile, a range nothing measured - the model is
not asked to reason about a number nobody measured, and the decision says
so.  A run that presented a guess as reasoning would be the defect this
exists to remove, one level up.

**The model may answer in units a person can judge, and the engine
SOLVES the delivered value.**  A slot may declare a `solver`: the model
names how far the voice should sit above the bed - a thing an ear can be
asked about - and the gain that reaches the renderer is arithmetic over
that answer and two measurements.  This is the captain's "formula or some
kind of audio analysis that allows that judgement to be made", and the
solver is REGISTERED on the slot so a second formula cannot exist.

**A stated preference is the person's number, never the engine's.**  A
project preference or a per-user profile value exists because a person
stated it. Neither is required to be filled in, and an absent preference
falls to the model reasoning over measurement - never to a value sitting
in a shipped configuration file. A default config file carrying the
engine's numbers would be this defect wearing configuration's clothes,
and neither source ships with the engine.

**Nothing reads a decision to decide something else about taste.**
`decide` returns a value and a record.  Whatever ranked, filtered or
second-guessed the model's answer would become the chooser (AGENTS.md
10.5).

**A value with no decision record is not a value.**  `assert_decided`
refuses one, fail-closed, the way `reel_rebuild_need` refuses.  A
decision with no trace is how the pipeline got into this state, so an
absent trace is the failure rather than a gap.

Rules
-----
**A creative value is DECIDED, and the decision says what decided it.**
One enumeration, `library/tools/decided_value.py`: `SLOTS` is the
registry and `decide` is the only ladder.
- **Five readings, and `FALLBACK` is visible AS one.** A fallback names
  WHOSE preference it is and what supersedes it; an engine-owned
  fallback is refused at import.
- **A slot's deciding step must REACH A MODEL** (`undetermined.DECLARING_STEPS`),
  must declare the slot in its manifest's top-level `decides`, and its
  prompt must carry the question on the same run.
- **A declared slot has a READER**, the way a declared output does
  (`library/tools/output_contract.py`).
- **The trace is MERGED, never replaced**, and a row from an earlier run
  is marked so it is never read as fresh.
- `tests/unit/context/test_decided_value.py`, and the derived sweep in
  `tests/contracts/test_no_creative_floors.py`.
```
