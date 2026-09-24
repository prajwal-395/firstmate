"""The reel path is addressable, and its contract can REFUSE.

The captain's ask, verbatim: *"don't try to run functionality on
something we don't have requirements for"*.  Before this file, reels were
the one thing the registry could not say that about - `grep -n reel
library/tools/operations.py` on `834a29b` returned nothing, so the
captain's own use case was reachable only by knowing which script to run.

Two halves, and the second is the load-bearing one.

**Registration.**  `reel.candidates` and `reel.select` are `select_reels`'
own two bodies, named.  Reel BUILDING is deliberately absent and
`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` says why; `test_no_reel_operation
_claims_a_node_that_does_not_own_it` is what stops it being registered
under a node whose contract is about something else.

**The contract.**  `select_reels` derived ZERO requirements until
`requirements.derive_runner_injected_keys` existed, so an operation owned
by it could not have refused for any reason at all - a gate that cannot
fail, which reads as coverage and is worse than no gate (AGENTS.md 10.4).
So this file asserts BOTH directions on one project fixture: the same
operation refuses without the transcript and passes with it.  Either
assertion alone would keep passing if the check collapsed to a constant.

`tests/test_operations_execute.py` owns the general execute/refuse
machinery; this file is about reels and about the enumeration that keeps
the derivation honest.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import operations, run_scope
from library.tools import requirements as R
from library.tools.timeline_transcript import transcript_path

REPO = Path(__file__).resolve().parents[1]

REEL_OPERATIONS = tuple(op for op in operations.all()
                        if op.name.startswith("reel."))

SELECTION_OPERATIONS = tuple(op for op in REEL_OPERATIONS
                             if op.owning_node == "select_reels")
"""PROPOSING a reel: step 3.04's two halves, inside `edit_video`."""

PROCESS_OPERATIONS = tuple(op for op in REEL_OPERATIONS
                           if op.owning_node != "select_reels")
"""BUILDING and VERIFYING one: the two nodes of `library/processes/reels`,
which is the second process the finding asked for and the captain
authorised.  They are separated from the pair above because they have
different contracts, not because they are a different kind of thing."""


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


# ── The reel path is in the registry ────────────────────────────────




def test_building_a_reel_is_addressable_and_not_only_a_subcommand():
    """The gap the SECOND process closes.

    When `docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` was written, the only
    way to build a reel was to know that `manage_project.py build-reels`
    existed - the captain's stop condition was that reels be made *"using
    the pipeline and not any standalone scripts"*, and a subcommand
    reaching past the registry into `reel_build` is that gap however thin.

    Now both halves are named, owned by nodes of `library/processes/reels`,
    and `build-reels` runs that process through its own runner
    (`library/processes/reels/run_reels.py`) rather than holding a second
    copy of the path.
    """
    build = operations.get("reel.build")
    verify = operations.get("reel.verify")
    assert build.owning_node == "build_reels"
    assert verify.owning_node == "verify_reels"

    from library.tools import processes
    assert processes.process_of("build_reels") == processes.REELS
    assert processes.process_of("verify_reels") == processes.REELS
    # The order comes off the process's own graph, never from a list.
    assert processes.execution_order(processes.REELS) == [
        "build_reels", "verify_reels"]


def test_the_build_command_holds_no_second_copy_of_the_build_path():
    """`build-reels` is a CALLER of the process, not a second route.

    Read off the source rather than described: neither the subcommand
    nor the process's runner may reach into `reel_build` directly,
    because two entry points into one build is exactly the shape Ruling
    1 forbids.
    """
    import ast

    def _body(path, name):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == name)

    def _names(body):
        # The AST, not the text: the docstring NAMES the old route in
        # order to say it is gone, and a substring check would read
        # that as the route still being there.
        called = {ast.unparse(n) for n in ast.walk(body)
                  if isinstance(n, (ast.Call, ast.Attribute, ast.Name))}
        imported = {alias.name for n in ast.walk(body)
                    if isinstance(n, ast.ImportFrom) for alias in n.names}
        return called | imported

    command = _names(_body(REPO / "manage_project.py", "cmd_build_reels"))
    runner = _names(_body(REPO / "library" / "processes" / "reels"
                          / "run_reels.py", "run"))
    for where, names in (("manage_project.cmd_build_reels", command),
                         ("run_reels.run", runner)):
        assert "rebuild_reels_in_project" not in names, (
            f"{where} calls reel_build directly again, so there are two "
            f"routes into the build and only one of them checks a "
            f"requirement")
    assert any("run_reels.run" in n for n in command), (
        "cmd_build_reels no longer runs the reels process through its "
        "own runner")
    assert any("execution_order" in n for n in runner), (
        "the reels runner no longer takes its node order off the reel "
        "process's own graph")
    assert any("op.execute" in n for n in runner), (
        "the reels runner no longer runs the reel process's nodes "
        "through the operation registry, so nothing checks their "
        "requirements")








# ── The contract BITES, in both directions ──────────────────────────




@pytest.mark.parametrize("op", REEL_OPERATIONS, ids=lambda o: o.name)
def test_a_reel_operation_refuses_without_the_transcript(op, tmp_path):
    """The captain's ask, made mechanical - and for the BUILD too.

    The transcript reaches all four operations by one route and it is
    DERIVED: `derive_runner_injected_keys` reads which manifests declare
    `timeline_transcript` required, so the two new nodes inherited this
    requirement by declaring the input, with nothing hand-listed.
    """
    result = op.execute(_project(tmp_path, with_transcript=False))

    assert result.refused, (
        f"{op.name} agreed to run against a project with no timeline "
        f"transcript - the one input the whole reel path is cut from")
    assert "timeline_transcript.on_file" in [r.name for r in result.unsatisfied]
    assert "timeline_transcript" in result.error
    # The remedy, because no step produces this and "run the producer"
    # is not an answer that exists.
    assert "library.tools.timeline_transcript" in result.error
    assert "NO STEP MAKES ONE" in result.error


@pytest.mark.parametrize("op", SELECTION_OPERATIONS, ids=lambda o: o.name)
def test_the_transcript_is_all_selection_asks_for(op, tmp_path):
    """3.04's contract is exactly one requirement, and it stays exactly
    one: a widened contract here would refuse a run that is fine."""
    result = op.execute(_project(tmp_path, with_transcript=False))
    assert [r.name for r in result.unsatisfied] == [
        "timeline_transcript.on_file"]


@pytest.mark.parametrize("op", SELECTION_OPERATIONS, ids=lambda o: o.name)
def test_the_same_operation_passes_its_contract_with_the_transcript(
        op, tmp_path):
    """The other direction, on the same fixture.

    A gate that FAILS correct input is no more coverage than one that
    cannot fail (AGENTS.md 10.4), and a refusal-only test would keep
    passing if the check collapsed to `return UNSATISFIED(...)`.
    """
    assert op.unmet(_project(tmp_path, with_transcript=True)) == []


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
        Approval, ReelMoment, proposal_path, write_proposal)

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


@pytest.mark.parametrize("absent,expected", [
    ("approved", "reel_plan.approved"),
    ("binding", "resolve.timeline_binding"),
])
def test_each_half_of_the_builds_contract_can_refuse_on_its_own(
        tmp_path, absent, expected):
    """Every input the finding's table named must be able to REFUSE.

    One at a time, from the project that otherwise passes, so a refusal
    is attributable to the half that was removed rather than to whatever
    else the fixture happens to lack.
    """
    folder = _reel_project(tmp_path, **{absent: False})
    unmet = [u.requirement.name
             for u in operations.get("reel.build").unmet(folder)]
    assert unmet == [expected], (
        f"removing {absent!r} should refuse exactly {expected}; got {unmet}")


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


def test_the_verify_node_refuses_when_nothing_was_built(tmp_path):
    """The edge between the two nodes is a real requirement.

    `state.verify_reels.reel_build` is DERIVED from the edge in
    `library/processes/reels/dag.json`, so verify cannot grade timelines
    that were never placed - and the refusal names `build_reels` as the
    producer, because here there really is one.
    """
    folder = _reel_project(tmp_path, built=False)
    result = operations.get("reel.verify").execute(folder)
    assert result.refused
    assert "state.verify_reels.reel_build" in [
        r.name for r in result.unsatisfied]
    assert "build_reels" in result.error


def test_the_verify_node_passes_once_the_build_recorded_one(tmp_path):
    """The other direction on the same fixture."""
    folder = _reel_project(tmp_path, built=True)
    assert operations.get("reel.verify").unmet(folder) == []


def test_the_refusal_names_no_producer_because_there_is_none():
    """`_teach` offers "run that step first" only for a requirement that
    names a producer.  Suggesting one here would be confidently wrong:
    the transcript is written by a CLI tool that needs Resolve open."""
    requirement = next(r for r in R.all_requirements()
                       if r.name == "timeline_transcript.on_file")
    assert requirement.produced_by == ()
    assert requirement.kind == R.KIND_STATE_KEY


# ── The enumeration that keeps the derivation honest ────────────────








# ── What is NOT registered, and the shape it would have taken ───────




