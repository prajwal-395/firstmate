# `library.tools.composer` - the history behind its contract

This is the module docstring of `library/tools/composer.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Resolve a goal backwards to the shortest capability set that reaches it.

Given a goal - one requirement NAME, in the vocabulary `Operation.requires`
and `Operation.effect` already share - work backwards through effect and
requires to the capability set that reaches it, shortest first.  A goal no
capability produces is REFUSED BY NAME - "no capability produces X" -
rather than run and failed.  That refusal is the deliverable as much as
the resolution is: the operator learns the goal is unreachable before
anything runs.

What this reads, and what it never does
---------------------------------------
This reads ONLY the derived contracts: `requirements.all_requirements()`
for the vocabulary, `Operation.requires` for preconditions and
`Operation.effect` for what satisfies them.  It runs nothing, writes
nothing, and owns no step logic - Ruling 1 (`library/tools/operations.py`)
holds because a plan only NAMES operations; executing one is still
`Operation.execute`'s own refusal-or-run.

The blindness, stated rather than patched around
------------------------------------------------
The composer can only select capabilities - registered operations - so it
inherits both limits of the layer underneath, and both are TRUE rather
than gaps:

* THE FOUR.  Four operations have a deliberately empty derived effect
  (`operations.EMPTY_EFFECT_REASONS`: four ARTIFACTS written to disk
  rather than state).  A composer working backwards from requirements
  alone can never select them, because artifact productions are
  invisible as goals.  No plan this module returns ever names one, and
  `tests/test_composer.py` pins that.  (`reel.gate_stills`, the third
  artifact, shares `verify_reels`' verdict effect by the node
  granularity `Operation.effect` declares - and is still never
  planned: that test pins it explicitly, and the route-selection
  guard covers the selector naming `reel.verify`.)
*
  THE VERDICTS, WHICH ARE NO LONGER BLIND.  The tree was checked
  against a third kind - VERDICT, the effect that is pass/fail rather
  than a state key - and the captain ruled on it 2026-09-23: a verdict
  is expressible as a goal AS ITS OWN requirement kind, not by
  loosening what a requirement means.  `requirements.VERDICTS` is that
  kind, so `sfx_library.validate`, `reel.verify` and
  `validation.resolve` derive non-empty effects and compose as
  `verdict.*` goals.  Whether each one REACHES is measured per goal,
  not promised by the kind: a verdict whose own preconditions strand
  still refuses, naming what stops it.
*
  THE OPTIONALS, WHICH ARE NO LONGER BLIND EITHER.  The tree was
  checked against a fourth kind - OPTIONAL, the edge that may or may
  not carry state - and the captain ruled on it 2026-09-23, board
  answer "add-optional": ADD OPTIONALITY TO THE VOCABULARY, as its
  own kind and not by loosening `produced_by` or widening an existing
  kind.  `requirements.OPTIONALS` is that kind, so `prosody.analyse`,
  `color_grade.resolve`, `ocr.extract`, `transitions.resolve`,
  `sfx.resolve`, `vfx.resolve` and `cohesion.review` derive non-empty
  effects and compose as `optional.*` goals - the three mechanism-B
  edges (`transition_spec`, `sfx_spec`, `enhancement_spec` for
  `compile_manifest`) alongside the four mechanism-A productions and
  `cohesion_review`.  Whether
  each one REACHES is measured per goal, not promised by the kind.
* THE MIDDLE OF THE DAG.  A producer node with no registered operation
  (`dag_adapter.legacy_only_nodes()`: thirteen at introduction, two
  now - `compile_manifest` and `object_segmentation`, whose bodies take
  an output directory `main()` derives rather than a declared input).
  A goal whose chain passes through one is unreachable BY
  CAPABILITIES ALONE - reaching it would mean adding a capability, which
  this module will not do on the caller's behalf.  It refuses instead,
  naming the deepest requirement nothing reaches and which step produces
  it, so the operator knows the DAG run (or the outside supply) that the
  plan would need first.

  Measured at introduction: exactly two goals close through capabilities
  alone (`state.judge_reels.reel_selection`,
  `state.verify_reels.reel_build`).  Everything else refuses.  That
  proportion is a finding about registry coverage, not about this
  module: as operations are registered for middle-DAG nodes, more goals
  resolve with nothing changed here.  `creative_cohesion` is the
  measured instance: `optional.compile_manifest.cohesion_review`
  refused naming the node until `cohesion.review` was registered, and
  then closed with nothing here changed.

What a precondition without a producer becomes
----------------------------------------------
A requirement nothing produces cannot be reached by running, but it can
still HOLD at run time - and `Operation.execute` checks exactly that
before it runs.  So a producer-less precondition is an ASSUMPTION the
plan reports, never a refusal:

* `environment` kind - a machine fact (`env.npx`, `env.resolve_scripting`
  and the rest).  The requirement layer reports these rather than
  refusing on them, and so does the plan: `assumes_machine`.
* anything else producer-less - satisfiable only from outside the
  pipeline (the captain's approval, `project.yaml`, the transcript the
  runner reads off disk).  `assumes_outside`, quoting the requirement's
  own `describe`, which already says what must be supplied.

Asymmetry, deliberately: as a GOAL, a producer-less requirement still
refuses - no sequence of capabilities makes approval, a binding or a
tool on PATH exist.  As a PRECONDITION it is something the operator
supplies once and the plan runs under.  That mirrors `run_scope`: a goal
whose output was SUPPLIED is REACHED, not stranded.

Reachability
------------
    python3 -m library.tools.composer state.verify_reels.reel_build
    python3 -m library.tools.composer --list
```


## `tests/test_ren_one_real_edit.py` - how the gap closed (moved 2026-10-02)

```text
Item 4: one real edit through the composer and the oracle, no rebuild.

The captain's ask: a minor edit (the Reel 26 ending swap -
`logo_bulb_23976.mov` for `logo_bulb_lines_23976.mov` on V7, same span,
no played-length change) must go through the COMPOSER to the shortest
capability set, with preconditions checked against the LIVE TIMELINE
through the oracle, executing as a TOUCHUP - never a rebuild.

Reconciled post-fix form (vep-reconcile-the-two-gap-closers). PR #1324
pinned the gap OPEN with five tests; two lanes closed it from opposite
sides and this file records, assertion by assertion, what flipped:

1. TOUCHUP REGISTERED (`vep-ren-register-the-touchup-capability`):
   `reel.touchup` is now a registered operation owned by `build_reels`,
   its effect DERIVED in the requirement vocabulary like every other
   operation's (effect = `state.verify_reels.reel_build`, requires
   identical to its sibling `reel.build`). The old pin asserted no name
   contains "touch"; the new pin asserts the registration and derivation.
2. ORACLE SPEAKS A DECLARED NAME (both lanes; reconciled):
   `evaluate_precondition_against_live` now evaluates
   `state.verify_reels.reel_build` - the reel goal itself - against the
   live rows, the same picture-presence judgement as `rough_cut_exists`
   under a name the plans declare. The oracle lane
   (`vep-ren-oracle-speaks-a-declared-name`) additionally answers every
   OTHER declared name via its own requirement check; the touchup lane
   named the two live names `LIVE_PRECONDITIONS`. Both spellings resolve
   here (`LIVE_PRECONDITIONS == (LEGACY_PRECONDITION,) +
   LIVE_REQUIREMENTS`) with one three-case evaluation. The old pins
   asserted the oracle knew no plan name and raised on all six rebuild
   requires; the new pins assert it evaluates the reel goal live and
   answers rather than raising.

What is deliberately UNCHANGED, and pinned as such: free text is
still refused by name (a goal has to name a requirement - that
refusal is correct behaviour, not a defect), and the reel goal
still completes as the rebuild route (the touchup is chosen BY
NAME, never by rewriting the goal's default plan). No new
vocabulary was added on either side to close the gap.
```
