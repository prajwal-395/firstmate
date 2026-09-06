"""A rejected rough cut refuses re-entry, and the override is recorded.

The defect
----------
Four planning steps - 4.01, 4.02, 4.03, 4.04 - each declared the prose
precondition `"'rough_cut_review.passed' is true in state"`, and nothing
read it: `grep -rn rough_cut_review --include=*.py library/` found no
read of `.passed` anywhere. Measured, a run against a recorded review
with `passed: false` planned four real subtitles and said nothing.

A full run already stops at a failed review, because
`step_3_03_review_rough_cut/step.py:386-387` exits 1. So the hole was
only ever on RE-ENTRY - a `--resume`, a `--from`, a scoped `--only`,
anything that reads the recorded review instead of re-running 3.03.

The ruling
----------
REFUSE WITH A DELIBERATE OVERRIDE (firstmate, 2026-09-05,
`data/decisions/rough-cut-gate.md`). Refusing outright would remove the
iterate-on-a-rejected-cut loop the captain plausibly wants; proceeding
silently is the defect itself. Refuse-with-override keeps the loop and
removes the silence.

Three constraints, and this file tests all three:

1. the override is EXPLICIT - nothing reaches it by default;
2. it is RECORDED in the run's own outputs, with the verdict it
   overrode, so a later reader can see the cut was rejected when this
   was planned;
3. it is NOT REACHABLE BY DEFAULT - a requirement must opt in, and
   naming one that has not is refused.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import requirements as R

CONSUMERS = ("plan_subtitles", "plan_transitions", "plan_vfx", "plan_sfx")


@pytest.fixture
def gate():
    return next(r for r in R.registry() if r.name == "rough_cut.approved")


def _ctx(review, overrides=()):
    return R.Context(
        run_set=frozenset({"plan_subtitles"}),
        state={"step_outputs": {"review_rough_cut": {
            "rough_cut_review": review}}} if review is not None else {},
        overrides=frozenset(overrides))


# ── The requirement exists and binds where the prose claimed ─────────

def test_the_requirement_is_registered_and_no_longer_held(gate):
    assert R.UNAUTHORISED == {}, (
        "rough_cut.approved is built; nothing should still be held")
    assert gate.kind == R.KIND_PREDICATE
    assert gate.produced_by == ("review_rough_cut",)


def test_it_binds_to_all_four_steps_that_declared_the_prose(gate):
    """The prose was on 4.01, 4.02, 4.03 and 4.04. All four, or the
    requirement is narrower than the declaration it replaces."""
    assert set(gate.consumers) == set(CONSUMERS)


def test_every_consumer_manifest_declares_it():
    from pathlib import Path
    from library.tools import run_scope

    steps = Path(__file__).resolve().parents[1] / "library" / "steps"
    dir_of = {n["id"]: n["step_ref"].split("/")[-1]
              for n in run_scope.load_dag()["nodes"]}
    for consumer in CONSUMERS:
        manifest = json.loads(
            (steps / dir_of[consumer] / "manifest.json").read_text(
                encoding="utf-8"))
        assert "rough_cut.approved" in manifest["interface"]["requirements"], (
            f"{consumer} does not declare rough_cut.approved")


# ── Mutation, both ways ─────────────────────────────────────────────

def test_a_rejected_cut_refuses(gate):
    verdict = gate.check(_ctx({"passed": False,
                               "rejection_reasons": ["gap at 12.4s"]}))
    assert verdict.is_unsatisfied
    assert "REJECTED" in verdict.reason
    assert "gap at 12.4s" in verdict.reason, (
        "the refusal must carry WHY the cut was rejected, or the "
        "operator has to go and look it up")
    assert verdict.produced_by == ("review_rough_cut",)


def test_an_approved_cut_passes(gate):
    verdict = gate.check(_ctx({"passed": True}))
    assert verdict.is_satisfied
    assert verdict.source in R.SOURCES


def test_a_missing_measurement_is_not_a_failed_one(gate):
    """AGENTS.md 10.3: no field reports a default as though it were
    measured. A review with no `passed` was never measured, and saying
    it FAILED would be inventing a verdict."""
    absent = gate.check(_ctx({"verdict": "looks fine"}))
    failed = gate.check(_ctx({"passed": False}))
    assert absent.is_unsatisfied and failed.is_unsatisfied
    assert absent.reason != failed.reason
    assert "never measured" in absent.reason
    assert "REJECTED" not in absent.reason


def test_no_review_at_all_refuses_and_says_so(gate):
    verdict = gate.check(_ctx(None))
    assert verdict.is_unsatisfied
    assert "nothing says whether" in verdict.reason


# ── Constraint 1 and 3: explicit, and not reachable by default ──────

def test_without_the_override_a_rejected_cut_refuses_the_run(gate):
    rejected = _ctx({"passed": False, "rejection_reasons": ["gap"]})
    verdict = R.evaluate({"plan_subtitles"}, rejected, [gate])
    assert len(verdict.unmet) == 1
    assert verdict.overridden == []


def test_with_the_override_the_run_proceeds(gate):
    rejected = _ctx({"passed": False, "rejection_reasons": ["gap"]},
                    overrides=("rough_cut.approved",))
    verdict = R.evaluate({"plan_subtitles"}, rejected, [gate])
    assert verdict.unmet == []
    assert len(verdict.overridden) == 1


def test_only_this_requirement_is_overridable():
    overridable = [r.name for r in R.all_requirements() if r.overridable]
    assert overridable == ["rough_cut.approved"], (
        f"the override is meant to be opt-in, one requirement at a time. "
        f"Overridable now: {overridable}")


def test_overriding_something_that_did_not_opt_in_is_refused():
    with pytest.raises(R.OverrideError, match="not overridable"):
        R.assert_overrides_are_real(["spine.word_timings"])


def test_a_typo_is_refused_rather_than_ignored():
    """An override that silently disables nothing is still a lie about
    what the run did."""
    with pytest.raises(R.OverrideError, match="names no requirement"):
        R.assert_overrides_are_real(["rough_cut.aproved"])


def test_an_override_of_a_state_key_is_refused():
    """The escape-hatch guard. `state_key` requirements are what stop
    the mid-run crashes; an override that reached them would put those
    back."""
    state_key = next(r for r in R.all_requirements()
                     if r.kind == R.KIND_STATE_KEY)
    with pytest.raises(R.OverrideError, match="not overridable"):
        R.assert_overrides_are_real([state_key.name])


def test_an_override_does_nothing_when_the_requirement_is_satisfied(gate):
    """Passing the flag on a healthy cut must not record an override -
    nothing was overridden."""
    approved = _ctx({"passed": True}, overrides=("rough_cut.approved",))
    verdict = R.evaluate({"plan_subtitles"}, approved, [gate])
    assert verdict.unmet == [] and verdict.overridden == []


# ── Constraint 2: the override is RECORDED ──────────────────────────

def test_the_override_record_carries_the_verdict_it_overrode(gate):
    """Not merely THAT something was overridden - WHAT it refused with.

    A later reader asking "was the cut rejected when these captions were
    planned?" must get an answer from the record alone.
    """
    rejected = _ctx({"passed": False,
                     "rejection_reasons": ["timeline continuity: gap at 12.4s"]},
                    overrides=("rough_cut.approved",))
    record = R.evaluate({"plan_subtitles"}, rejected,
                        [gate]).overridden[0].as_record()

    assert record["requirement"] == "rough_cut.approved"
    assert "REJECTED" in record["refused_because"]
    assert "gap at 12.4s" in record["refused_because"], (
        "the record must carry the reason the cut was rejected, or it "
        "says only that a rule was bypassed")
    assert record["needed_by"]
    assert json.loads(json.dumps(record)) == record, (
        "the record must survive being written to pipeline_data.json")


def test_the_runner_writes_the_record_into_the_project_state():
    """The recording is itself testable, and this is that test."""
    from library.processes.edit_video import run_pipeline as rp

    source = (rp.__file__)
    text = open(source, encoding="utf-8").read()
    assert "REQUIREMENT_OVERRIDES_KEY" in text
    assert "state[REQUIREMENT_OVERRIDES_KEY] = override_records" in text, (
        "the runner stopped writing overrides into the state file, so a "
        "later reader of pipeline_data.json can no longer see that the "
        "cut was rejected when this was planned")
    assert rp.REQUIREMENT_OVERRIDES_KEY == "requirement_overrides"


def test_an_override_is_announced_on_stderr_not_only_filed():
    from library.processes.edit_video import run_pipeline as rp
    text = open(rp.__file__, encoding="utf-8").read()
    assert "OVERRIDDEN" in text, (
        "an override that is filed but not announced is still quiet at "
        "the moment it matters")
