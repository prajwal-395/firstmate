"""The reel path is addressable, and its contract can REFUSE.

The captain's ask: *"don't try to run functionality on something we
don't have requirements for"*. Each reel capability refuses without what
it is cut from and passes with it - both directions on one fixture, so
neither assertion survives the check collapsing to a constant
(AGENTS.md 10.4). `tests/unit/context/test_operations_execute.py` owns the general
execute/refuse machinery.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import operations
from library.tools.timeline_transcript import transcript_path

REEL_OPERATIONS = tuple(op for op in operations.all()
                        if op.name.startswith("reel."))

SELECTION_OPERATIONS = tuple(op for op in REEL_OPERATIONS
                             if op.legacy_node == "select_reels")
"""PROPOSING a reel: step 3.04's two halves, inside `edit_video`."""

def _project(tmp_path, with_transcript: bool) -> str:
    """A project under tmp_path, never a real one (AGENTS.md 8)."""
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path), "step_outputs": {}}),
        encoding="utf-8")
    if with_transcript:
        path = transcript_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "segments": [], "segment_count": 0,
            "derived_from": {"duration_seconds": 0.0}}), encoding="utf-8")
    return str(tmp_path)


TRANSCRIPT_READERS = tuple(op for op in REEL_OPERATIONS
                           if "timeline_transcript" in op.consumes)
"""Every reel capability that READS the transcript.  The touch-ups and
the stills grab read none - their arguments come from the caller - so
since `Operation.consumes` they are not asked for one."""


def test_every_reel_route_that_cuts_from_the_transcript_reads_it():
    assert {"reel.candidates", "reel.select", "reel.build",
            "reel.verify"} <= {op.name for op in TRANSCRIPT_READERS}
    assert {op.name for op in REEL_OPERATIONS
            if op not in TRANSCRIPT_READERS} == {
        op.name for op in REEL_OPERATIONS if op.caller_supplied}


def test_every_transcript_reader_refuses_without_it_and_says_how_to_make_it(
        tmp_path):
    """The captain's ask, made mechanical - and for the BUILD too.

    The requirement is DERIVED (`derive_runner_injected_keys` reads which
    manifests declare `timeline_transcript` required). No step produces
    it, so the refusal carries the command rather than "run the producer".
    One row per reel capability that reads the transcript.
    """
    assert TRANSCRIPT_READERS
    for i, op in enumerate(TRANSCRIPT_READERS):
        result = op.execute(_project(tmp_path / f"p{i}",
                                     with_transcript=False))
        assert result.refused, (
            f"{op.name} agreed to run against a project with no timeline "
            f"transcript - the one input the whole reel path is cut from")
        assert "timeline_transcript.on_file" in [
            r.name for r in result.unsatisfied], op.name
        assert "timeline_transcript" in result.error, op.name
        assert "library.tools.timeline_transcript" in result.error, op.name
        assert "NO STEP MAKES ONE" in result.error, op.name


@pytest.mark.parametrize("op", SELECTION_OPERATIONS, ids=lambda o: o.name)
def test_selection_asks_for_the_transcript_and_nothing_else(op, tmp_path):
    """3.04's contract is exactly one requirement: it refuses on that
    alone, and passes once it is on file. A widened contract would refuse
    a run that is fine."""
    result = op.execute(_project(tmp_path / "without", with_transcript=False))
    assert [r.name for r in result.unsatisfied] == [
        "timeline_transcript.on_file"]
    assert op.unmet(_project(tmp_path / "with", with_transcript=True)) == []


# ── The BUILD's contract, requirement by requirement ────────────────


def _reel_project(tmp_path, *, transcript=True, approved=True, binding=True,
                   resolve_present=True, face_detector=True,
                   build_libraries=True, built=False) -> str:
    """A project under tmp_path carrying whichever half is being tested.

    Never a real project (AGENTS.md 8), and every half is BUILT rather
    than mocked, because each check reads the disk through the same
    module the build itself reads it through - a fixture that satisfied
    the check by some other route would prove a door nobody can open.
    """
    from library.tools import requirements as _R
    from library.tools.reel_proposal import (
        Approval,
        ReelMoment,
        proposal_path,
        write_proposal,
    )

    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    state = {"project_folder": str(tmp_path), "step_outputs": {}}
    if built:
        state["step_outputs"]["build_reels"] = {"reel_build": {
            "timelines_built": ["Reel 01 - a-witness"],
            "plan_path": str(proposal_path(tmp_path))}}
    # The environment probe seam. `env.resolve_scripting`,
    # `env.face_detector` and `env.reel_build_libraries` read the
    # MACHINE, and a machine with no Resolve, no Haar cascades or a
    # partial venv on it is the normal case in CI - so the witness
    # drives the probes rather than the test installing an NLE, an
    # OpenCV and a full venv. Nothing in library/processes or
    # library/steps ever writes this key;
    # tests/test_requirements.py pins that.
    state[_R._FORCE] = {
        "resolve_scripting": bool(resolve_present),
        "face_detector": bool(face_detector),
        "reel_build_libraries": bool(build_libraries),
    }
    (tmp_path / "pipeline_data.json").write_text(json.dumps(state),
                                                 encoding="utf-8")

    if transcript:
        path = transcript_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "segments": [], "segment_count": 0,
            "derived_from": {"duration_seconds": 0.0}}), encoding="utf-8")

    if approved is not None:
        moment = ReelMoment(
            number=1, slug="a-witness", reason="a moment to build",
            timeline_start=10.0, timeline_end=40.0,
            approval=Approval.APPROVED if approved else Approval.PROPOSED)
        write_proposal(proposal_path(tmp_path), [moment],
                       {"derived_from": {"duration_seconds": 60.0}})

    if binding:
        (tmp_path / "project.yaml").write_text(
            "name: fixture\nresolve:\n"
            "  project_name: Fixture Project\n"
            "  timeline_name: Fixture Timeline\n", encoding="utf-8")
    return str(tmp_path)


def test_the_build_passes_a_project_that_has_everything(tmp_path):
    """The satisfying direction. A contract that refuses a correct
    project is no more coverage than one that cannot fail."""
    build = operations.get("reel.build")
    assert build.unmet(_reel_project(tmp_path)) == [], (
        "reel.build refused a project with an approved plan, a transcript, "
        "a Resolve binding and a machine that can reach Resolve, load "
        "the Haar cascade and import the build libraries")


def test_a_missing_resolve_binding_refuses_the_build_on_its_own(tmp_path):
    """From the project that otherwise passes, so the refusal is
    attributable to the binding. The approval half is
    `test_a_plan_nobody_approved_refuses_and_says_whose_act_that_is`."""
    folder = _reel_project(tmp_path, binding=False)
    unmet = [u.requirement.name
             for u in operations.get("reel.build").unmet(folder)]
    assert unmet == ["resolve.timeline_binding"], unmet


def test_a_plan_nobody_approved_refuses_and_says_whose_act_that_is(tmp_path):
    """The captain's rule, made mechanical BEFORE Resolve is touched.

    `reel_proposal.for_building` already refuses a proposed moment - but
    only after the build has connected, deleted the existing reel
    timelines and started work. This is the same refusal, before the run.
    """
    folder = _reel_project(tmp_path, approved=False)
    result = operations.get("reel.build").execute(folder)
    assert result.refused
    assert [r.name for r in result.unsatisfied] == ["reel_plan.approved"]
    assert "NOT ONE APPROVED" in result.error
    assert "captain" in result.error
    # No producer is named, because no step makes an approval.
    assert "run that step first" not in result.error


def test_the_verify_node_refuses_until_the_build_recorded_one(tmp_path):
    """The edge between the two nodes is a real requirement, DERIVED from
    `library/processes/reels/dag.json`: verify cannot grade timelines
    that were never placed, and the refusal names `build_reels` because
    here there really is a producer. The other direction on the same
    fixture shape passes."""
    folder = _reel_project(tmp_path / "unbuilt", built=False)
    result = operations.get("reel.verify").execute(folder)
    assert result.refused
    assert "state.verify_reels.reel_build" in [
        r.name for r in result.unsatisfied]
    assert "build_reels" in result.error

    folder = _reel_project(tmp_path / "built", built=True)
    assert operations.get("reel.verify").unmet(folder) == []
