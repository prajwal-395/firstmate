# `library.tools.hooks` - the history behind its contract

This is the module docstring of `library/tools/hooks.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
One enumeration for AUTOMATIC behaviour: a condition, and what it fires.

The captain asked for two things the pipeline had neither of: "a trigger
layer that runs scripts automatically on a condition, and a hook layer
that steers the LLM automatically".  Measured before building
(`data/vep-audit-control/report.md`): a search for hook, trigger, event,
watch, listener and daemon modules, and for `register_hook`, `on_event`,
`emit_event`, `dispatch_event`, `add_listener`, `subscribe(`, `publish(`,
`EventBus`, `pubsub` and `observer`, returned NOTHING across `.py`, `.js`
and `.json`.  There is no cron, no launchd plist and no file watcher.
This module is the whole of it.

It is NOT the first conditional behaviour in the runner, and it should
not pretend to be
-------------------------------------------------------------------
Two things already fire work on a condition, and they are the precedent
this is argued against rather than a blank page:

* `run_pipeline.apply_source_identity` invalidates cached preflight work
  when a footage fingerprint moves, and `apply_code_identity` does the
  same when a step's code hash moves.  Both ADOPT rather than invalidate
  the first time they see a project - a guard worth copying.
* `post_bridge_retry`, `second_pass` and the QA loop all steer the model
  on a condition, through one seam: `present_llm_step(retry_feedback=)`.
  Three cargoes, one mechanism, and three DIFFERENT bound policies.  This
  module's budget copies `second_pass`'s (§ Three guards) rather than
  inventing a fourth.

What a hook is
--------------
A CONDITION the pipeline observes, and an ACTION.  Both are closed
vocabularies and both are DATA - `library/hooks.json`, or a project's own
`<project>/hooks.json`.  There is no code in a config string and no shell
string anywhere: a `run` action names a script that must already be in
`scripts/hooks/`, and the payload reaches it on stdin.

Why JSON, when `run_profile` argued for YAML
--------------------------------------------
`run_profile` chose YAML because "a profile that leaves a step out wants
to say WHY on the line above it, and that is the one thing JSON cannot
carry".  That reasoning applies here and JSON was still the instruction,
so the reason is carried IN THE DATA instead: `describe` is REQUIRED on
every hook, and it is printed in the run header and in the fired record.
That is strictly better than a comment - the reason reaches the report a
reader actually opens, not only the file they might.

A condition not in the vocabulary is REFUSED AT LOAD
----------------------------------------------------
The same shape `run_scope._reject_unknown` gives an unknown step: named,
before anything runs, with the known set listed.  A misspelled `when:`
that silently armed nothing is exactly the trap AGENTS.md section 3
exists to stop, and a hook layer that quietly does nothing is
indistinguishable from one with nothing to do.

Three guards, and none of them is a preference
----------------------------------------------
1. **Depth zero only, and NOT configurable.**  A hook fires only when
   `PIPELINE_HOOK_DEPTH` is unset; a `run` action sets it to 1 in the
   child's environment, and `run_pipeline._run_step_subprocess` passes no
   explicit env, so every descendant inherits it.  An operation invoked
   BY a hook therefore fires nothing, however deep it goes.  There is no
   setting for this: a configurable loop guard is a loop guard somebody
   turns off.
2. **A condition instance fires a hook ONCE.**  The ledger is keyed by
   `(run_id, hook_name, condition_fingerprint)` and appended to
   `pipeline_output/provenance/hooks.jsonl` - beside `runs.jsonl` and
   `artifacts.jsonl`, under the area whose stated purpose is already
   "append-only: a later run overwriting a file does not unmake the
   record of the earlier one".  The fingerprint is READABLE
   (`qa_finding_raised:subtitle_gaps`), the shape `marker_routing._note_id`
   takes, because a hook that fired has to be explicable to the captain
   in the run summary and a hex digest is not.
3. **A per-run budget, whose exhaustion is REPORTED and does not fail.**
   `MAX_FIRES_PER_RUN` is a backstop, not a tuning knob.  At the bound
   further dispatches decline and SAY SO, naming which hooks fired and
   how many times.  That is `second_pass`'s policy, not
   `post_bridge_retry`'s: an unfired hook leaves the run's own output
   perfectly valid, so failing the run would throw away good work to
   enforce a round trip.  A hook layer that quietly gave up would be
   indistinguishable from one with nothing to do.

Every automated steer lands in the feed the captain already reads
-----------------------------------------------------------------
A `steer` writes into `library/dashboard/review_channel.py` - the same
store, the same anchors and the same surface as the captain's own notes -
tagged `origin="hook"`.  Never a second feed: automation the captain
cannot see is the failure mode worth designing against, and a parallel
channel is how it happens.

Two rules follow from measuring that channel:

* **A hook WRITES to the channel and never POLLS it.**  `wait_for_batch`
  and `/api/review/poll` both `mark_delivered` the batch they see, so a
  second poller would consume the captain's batch and the agent parked on
  it would never wake.
* **A hook's note is anchored like anybody else's.**  `normalise_anchor`
  REFUSES an empty `selector` - "a note with no anchor is a page comment,
  not a review note" (AGENTS.md section 4) - and that rule is satisfied
  here rather than weakened: a steer anchors to `[data-step-id="<step>"]`,
  which `pipeline-view.js` really renders and `resolveAnchor` really
  finds.

What is NOT here yet, and why
-----------------------------
The FIRING SITES.  Nothing in `run_pipeline.py` calls `dispatch` yet, and
that is deliberate rather than unfinished:

* `operation_completed` and `operation_refused` need the operation
  registry, which is increment 4.
* `requirement_unsatisfied` is `requirements.py`, which is increment 3;
  wiring it to today's `run_scope.ScopeError` would bind it to a refusal
  that increment is about to replace.
* `qa_finding_raised`, `gate_verdict` and `output_empty` all have a
  computed payload TODAY, in the run summary's reporter blocks.  But
  those blocks run after `status` is decided and are explicitly "reading
  is not gating"; a hook that fires there and then ACTS changes that
  property, and which side of it a `run` action belongs on is a decision
  for the captain rather than an implication of landing this module.

`tests/test_hooks.py` drives every path through the public API, so
nothing here is unexercised while the sites are pending.

Where the operation conditions get their payload, when they are wired
-----------------------------------------------------------------------
FOUR of the six - `operation_completed`, `operation_refused`,
`requirement_unsatisfied` and `output_empty` - all come off ONE type,
`operations.OperationResult` (increment 4).  It already carries the
operation, the owning node, the scope, the status, the UNSATISFIED
REQUIREMENT OBJECTS so a refusal can name its own producer, the payload,
the artifacts, the error, and a `hollow` field carrying
`run_pipeline.check_output_is_real`'s verdict rather than a second
opinion about it.

So, as a standing instruction to whoever wires the sites - which is the
one thing most likely to go wrong here:

* **Do NOT define a result type in this module, and do not define a
  second hollow rule.**  That is the parallel implementation Ruling 1
  forbids.  `dispatch` deliberately takes a plain `Mapping` and computes
  no verdict of its own: it is handed what the operation already decided.
* **A REFUSAL WITH NO REQUIREMENTS IS LEGAL, and must still reach a
  hook.**  `OperationResult.__post_init__` requires `unsatisfied` OR
  `error`, so an environment refusal - `error="npx is not on PATH"`,
  `unsatisfied=()` - is a valid REFUSED result.  `requirement_unsatisfied`
  fires ZERO times for it, because the site loops a collection that is
  empty.  So the site MUST dispatch `operation_refused` unconditionally
  for every refusal, or that whole class reaches no hook at all - which
  is the silent gap this layer exists to remove.
  `tests/test_hooks.py::test_a_refusal_with_no_requirements_still_reaches_a_hook`
  pins it.
* The `identity` fields below for those four are PROVISIONAL.
  `operations.py` was not yet pushed to any branch when this landed
  (`git ls-remote` plus a search of every remote branch: `operations.py`
  is absent, though `SubtitleRenderRefused` is on
  `fm/vep-audit-decomposition`), so they could not be reconciled by
  reading the real type.  `tests/test_hooks.py` carries a tripwire that
  FAILS the moment `library.tools.operations` becomes importable with
  fields these do not match, so the reconciliation cannot be skipped
  silently.
```
