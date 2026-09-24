"""The mirror: the layer must not be vacuously STRICT either.

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
* `object_segmentation`'s two - the step is UNWIRED and never runs.

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
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import input_contract, requirements as R, run_scope


HAND_WRITTEN = list(R.registry())


def _ids(reqs):
    return [r.name for r in reqs]


# ── Half 1: a correct state must not be refused ──────────────────────

def _full_default_run() -> frozenset:
    """The steps a plain full run really schedules.

    NOT "every node in the DAG", which is what this said until a
    requirement existed for a step the pipeline does not schedule on its
    own. `run_scope.DESELECTED_BY_DEFAULT` is the pipeline's own answer
    to "what does a default run leave out", and `select_reels` is in it
    for exactly the reason its requirement refuses:

        "Requires a timeline_transcript produced outside the pipeline
         (needs Resolve open and WhisperX). Running it by default would
         crash on the missing transcript."

    Asking a deselected step's requirement of a run that never includes
    it is the scope dimension of vacuous strictness -
    `test_a_requirement_is_only_asked_of_a_step_that_is_running` below
    is the same point for the machine side. The requirement's own
    refusal, on a run that DOES select the step, is asserted in
    `tests/test_operations.py`.
    """
    dag = run_scope.load_dag()
    return frozenset(n["id"] for n in dag["nodes"]
                     if n["id"] not in run_scope.DESELECTED_BY_DEFAULT)


def test_a_full_default_run_is_not_refused_for_state():
    """A plain full run must not be refused by ANY state-side requirement.

    This assertion used to exclude the predicate and coverage kinds "because
    they read real values a synthetic state does not carry". That exclusion
    was a hole, and the full suite fell straight through it: a plain
    `--full-auto` run was REFUSED with

        no clip in temporal_index carries a non-empty speech_regions list
        no audio_spine is available, so there is no transcript to time
        captions against

    on a run that schedules `temporal_index` and `mesh_spine` before their
    consumers. The requirements were asking whether a value existed BEFORE
    the step that makes it had run - vacuous strictness, and exactly the
    class this file exists to catch.

    `_producer_will_make_it` is the fix, and this assertion is now
    unrestricted so the hole cannot reopen. Only environment requirements
    are excluded, because no step produces a machine.
    """
    run_set = _full_default_run()
    context = R.Context(run_set=run_set)

    state_side = [r for r in R.all_requirements()
                  if r.kind != R.KIND_ENVIRONMENT]
    unmet = R.check(run_set, context, state_side)

    assert unmet == [], (
        "these requirements refuse a run in which every producer "
        "executes, which is a correct full run:\n  "
        + "\n  ".join(f"{u.requirement.name} ({u.requirement.kind}): "
                      f"{u.satisfaction.reason}" for u in unmet))


def test_a_requirement_is_deferred_to_its_producer_when_it_runs():
    """The mechanism behind the assertion above, pinned directly.

    A coverage requirement on the spine is NOT asked when `mesh_spine` is
    in the run - the producer owns the value and its own step refuses if
    it cannot make one. It IS asked on a scoped re-entry that expects the
    spine to be on file already, which is the case it exists for.
    """
    coverage = next(r for r in HAND_WRITTEN if r.name == "spine.word_timings")
    empty = R.Context(state={"step_outputs": {
        "mesh_spine": {"audio_spine": {"structure": []}}}})

    with_producer = {"mesh_spine", "temporal_index", "speech_sequence",
                     "plan_subtitles"}
    assert R.check(with_producer, empty, [coverage]) == [], (
        "the spine requirement was asked before mesh_spine had run")

    assert len(R.check({"plan_subtitles"}, empty, [coverage])) == 1, (
        "the spine requirement was NOT asked on a re-entry that depends "
        "on a spine already being on file - which is the whole case it "
        "exists for")


def test_a_requirement_is_only_asked_of_a_step_that_is_running():
    """A machine with no Node.js must not refuse a run with no renderer.

    Vacuous strictness has a scope dimension too: asking every
    requirement of every run would refuse work that never needed it.
    """
    npx = next(r for r in HAND_WRITTEN if r.name == "env.npx")
    assert npx.applies_to({"render_subtitles"})
    assert not npx.applies_to({"plan_subtitles", "scan", "catalog"})

    unmet = R.check({"scan", "catalog"},
                    R.Context(run_set=frozenset({"scan", "catalog"})),
                    HAND_WRITTEN)
    assert unmet == [], (
        "a run of scan+catalog was refused by a requirement belonging to "
        "some other step")


# ── Half 2: no requirement may be tautological ───────────────────────

def test_no_requirement_derives_expected_from_actual():
    """The two witnesses must produce DIFFERENT verdicts.

    This is the structural form of "the expected side may not be derived
    from the same value as the actual side". A requirement whose two
    declared witnesses both pass, or both fail, is one that is not
    reading what it thinks it is reading - and the live instance in
    `reel_conformance_verifier` shows that shape passes silently.
    """
    tautological = []
    for req in HAND_WRITTEN:
        refuses = req.check(req.refuting_context()).is_unsatisfied
        passes = req.check(req.satisfying_context()).is_satisfied
        if not (refuses and passes):
            tautological.append(
                f"{req.name}: refuting_context refuses={refuses}, "
                f"satisfying_context passes={passes}")
    assert tautological == [], (
        "a requirement whose two witnesses do not disagree is not "
        "measuring anything:\n  " + "\n  ".join(tautological))


def test_an_empty_expected_side_refuses_rather_than_skipping():
    """Design rule: an empty reference set is a REFUSAL, never a skip.

    The live counter-example is `reel_conformance_verifier.py:1519`,
    where `if plan.captions and ...` turns an empty plan side into a
    silent pass. The coverage requirement must do the opposite.
    """
    empty_spine = R.Context(state={"step_outputs": {
        "mesh_spine": {"audio_spine": {"structure": []}}}})
    coverage = next(r for r in HAND_WRITTEN if r.name == "spine.word_timings")
    verdict = coverage.check(empty_spine)
    assert verdict.is_unsatisfied, (
        "an audio_spine with an empty structure was accepted. An empty "
        "expected side must refuse - a zero-entry subtitle plan is not a "
        "captioned video, and it is exactly what the runner was measured "
        "producing with a `✓ Completed` and no warning.")
    assert "empty" in verdict.reason.lower()


# ── The prose may not come back ──────────────────────────────────────
#
# Kept here rather than appended to `test_input_declarations_are_true.py`
# deliberately: that file's verdicts depend on `read_step_code`, which
# AST-walks each step directory, and a parallel increment is moving
# refusal code between step bodies. This assertion is about MANIFESTS and
# is independent of where a step's code lives, so it is kept where that
# churn cannot redden it.

def test_no_manifest_carries_prose_preconditions():
    """126 strings across 29 manifests, and nothing evaluated one.

    A manifest that grows the field back is declaring a contract with
    nothing behind it, which is worse than declaring none - it reads as
    coverage.
    """
    assert input_contract.prose_preconditions() == [], (
        "these manifests carry prose preconditions/postconditions again. "
        "They are replaced by interface.requirements, naming executable "
        "Requirements in library/tools/requirements.py.")


def test_the_replacement_field_is_declared_where_it_is_needed():
    """Every hand-written requirement is named by each consumer manifest.

    Derived from the registry rather than listed twice, so the manifest
    and the registry cannot drift.
    """
    import json
    from pathlib import Path

    from library.tools import processes

    steps = Path(__file__).resolve().parents[1] / "library" / "steps"
    # Every process, so a requirement consumed by a node of the reel
    # process is checked against ITS manifest rather than skipped for
    # not being in edit_video's graph - which would have made the
    # declaration optional exactly where it is new.
    dir_of = processes.step_dirnames()

    missing = []
    unknown_consumers = []
    for req in HAND_WRITTEN:
        for consumer in req.consumers:
            step_dir = dir_of.get(consumer)
            if not step_dir:
                unknown_consumers.append(f"{req.name} -> {consumer}")
                continue
            manifest = json.loads(
                (steps / step_dir / "manifest.json").read_text(
                    encoding="utf-8"))
            declared = manifest["interface"].get("requirements", [])
            if req.name not in declared:
                missing.append(f"{step_dir} does not declare {req.name}")
    assert missing == [], "\n  ".join(missing)
    assert unknown_consumers == [], (
        "these requirements name a consumer no process declares, so they "
        "can never be asked:\n  " + "\n  ".join(unknown_consumers))


def test_no_manifest_declares_a_requirement_that_does_not_exist():
    """The mirror - a manifest naming a requirement nothing implements."""
    import json
    from pathlib import Path

    steps = Path(__file__).resolve().parents[1] / "library" / "steps"
    known = {r.name for r in R.HAND_WRITTEN}
    unknown = []
    for path in sorted(steps.glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for name in manifest["interface"].get("requirements", []):
            if name not in known:
                unknown.append(f"{path.parent.name}: {name}")
    assert unknown == [], (
        "these manifests name requirements that do not exist:\n  "
        + "\n  ".join(unknown))


# ── The fresh-checkout condition, which local runs cannot otherwise see ──

def test_a_fresh_checkout_can_still_run_the_pipeline(tmp_path, monkeypatch):
    """A clone of this repository with NO locally-built assets must run.

    This is the condition the clean room found and no local run could:
    a developer machine has the SFX index built, so
    `test_a_full_default_run_is_not_refused_for_state` passed here while
    failing on a fresh Linux runner with

        the SFX index loads to zero entries: no readable sfx_index.json
        and no profiles/*.json under the library

    and the run was REFUSED before it started - a fresh clone could not
    run the pipeline at all. A second failure,
    `test_dashboard_run_control.py::test_handbrake_stops_the_runner`,
    was the same defect downstream: the run never reached the handbrake.

    Pointing the library at an empty directory reproduces that exactly,
    so the class is catchable locally from now on.
    """
    monkeypatch.setenv("PIPELINE_SFX_LIBRARY", str(tmp_path))

    run_set = _full_default_run()
    unmet = R.check(run_set, R.Context(run_set=run_set),
                    [r for r in R.all_requirements()
                     if r.kind != R.KIND_ENVIRONMENT])

    assert unmet == [], (
        "a fresh checkout with no locally-built assets cannot run the "
        "pipeline:\n  "
        + "\n  ".join(f"{u.requirement.name} ({u.requirement.kind}): "
                      f"{u.satisfaction.reason}" for u in unmet))


def test_a_predicate_without_a_producer_cannot_be_registered():
    """The structural cause, made unrepresentable.

    A predicate or coverage requirement with no `produced_by` can never
    be deferred to the step that makes the value, so it fires on every
    run - a run-blocker by construction. That is exactly what
    `sfx.index_loads` was. The empty producer list is the tell: if
    nothing in the pipeline produces the thing, it is the machine (an
    `environment` requirement, which reports rather than refuses) or it
    is some step's own subject matter.
    """
    with pytest.raises(ValueError, match="must name what produces"):
        R.Requirement(
            name="no.producer", kind=R.KIND_PREDICATE,
            describe="something nothing in the pipeline makes",
            produced_by=(), consumers=("scan",),
            check=lambda ctx: R.SATISFIED(R.MEASURED),
            refuting_context=lambda: R.Context(),
            satisfying_context=lambda: R.Context(),
        )

    # And the mirror: no step produces a machine.
    with pytest.raises(ValueError, match="must not name a producer"):
        R.Requirement(
            name="env.wrong", kind=R.KIND_ENVIRONMENT,
            describe="an environment requirement claiming a producer",
            produced_by=("scan",), consumers=("scan",),
            check=lambda ctx: R.SATISFIED(R.MEASURED),
            refuting_context=lambda: R.Context(),
            satisfying_context=lambda: R.Context(),
        )
