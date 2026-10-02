"""The reel path is addressable, and its contract can REFUSE.

The captain's ask: *"don't try to run functionality on something we
don't have requirements for"*. Each reel capability refuses without what
it is cut from and passes with it - both directions on one fixture, so
neither assertion survives the check collapsing to a constant
(AGENTS.md 10.4). `tests/unit/context/test_operations.py` owns the general
execute/refuse machinery.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest
from library.tools import operations, requirements
import copy
from library.tools import native_ops
from library.tools.native_ops import (
    NativeSpeedRefused,
    granted_categories,
    native_canonical,
    refused_native_canonical,
    resolve_transition_name,
)
from library.tools.ren_refusal import RenRefusal
from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import (
    is_native,
    known_type,
    native_canonical_type,
    refused_native_reason,
    route_of,
)


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

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


# --------------------------------------------------------------------------
# From test_operations_execute.py
#
# An operation EXECUTES, or refuses saying what is missing and who makes it.
#
# Before this, every operation resolved its entry point and stopped:
#
#     REFUSED: an operation cannot gather its own inputs yet
#
# so the operation surface was a resolver rather than an executor, and the
# LLM-native claim was half true.
#
# The test that matters most here is
# `test_the_same_requirement_defers_in_a_run_and_refuses_for_an_operation`.
# It pins the DISTINCTION rather than the two outcomes: one requirement,
# one state, two run sets, opposite verdicts. Two tests asserting each
# outcome alone would both keep passing if the distinction collapsed.

def test_a_result_that_cannot_say_what_it_is_is_refused():
    """A REFUSED result must say why, and a status must be one the
    vocabulary knows."""
    scope = operations.scope_mod.project()
    with pytest.raises(operations.OperationError):
        operations.OperationResult(
            operation="x", legacy_node="render_subtitles", scope=scope,
            status=operations.REFUSED)
    with pytest.raises(operations.OperationError):
        operations.OperationResult(
            operation="o", legacy_node="render_subtitles", scope=scope,
            status="maybe")


def test_produced_nothing_separates_empty_hollow_and_refused_results():
    scope = operations.scope_mod.project()
    common = dict(operation="o", legacy_node="render_subtitles", scope=scope)
    real = operations.OperationResult(
        status=operations.COMPLETED, payload={"segments": [1]}, **common
    )
    empty = operations.OperationResult(
        status=operations.COMPLETED, payload={}, **common
    )
    hollow = operations.OperationResult(
        status=operations.COMPLETED,
        payload={"segments": []},
        hollow=("segments",),
        **common,
    )
    refused = operations.OperationResult(
        status=operations.REFUSED, error="no transcript", **common
    )

    assert real.produced_nothing is False
    assert empty.produced_nothing is True
    assert hollow.produced_nothing is True
    assert refused.produced_nothing is True


def _word(text, start, end):
    return {"word": text, "source_start": start, "source_end": end}


def _spine():
    words = [_word(w, 10.0 + i * 0.4, 10.0 + (i + 1) * 0.4)
             for i, w in enumerate(
                 ["the", "thing", "nobody", "tells", "you", "about", "shipping", "software"])]
    return {"structure": [{
        "position": 0, "block_type": "speech", "clip_id": "clip_001",
        "source_start": 10.0, "source_end": 13.2,
        "timeline_start": 0.0, "timeline_end": 3.2,
        "word_timestamps": words, "alignment_method": "whisperx",
        "speaker": "host",
        "content": {"text": "the thing nobody tells you about shipping software"},
    }]}


@pytest.fixture
def satisfied_project(tmp_path):
    """A project carrying what plan_subtitles needs."""
    spine = _spine()
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "mesh_spine": {"audio_spine": spine, "timed_spine": spine},
            "review_rough_cut": {"rough_cut_review": {"passed": True}},
            "temporal_index": {"index_dir": str(tmp_path)},
            "speech_sequence": {"speech_sequence": {}},
        }}))
    return str(tmp_path)


@pytest.fixture
def empty_project(tmp_path):
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({"step_outputs": {}}))
    return str(tmp_path)


# ── THE DISTINCTION ─────────────────────────────────────────────────


def test_the_same_requirement_defers_in_a_run_and_refuses_for_an_operation(
        empty_project):
    """One requirement, one state, two run sets, opposite verdicts.

    Inside a DAG run, a requirement whose producer is scheduled ahead is
    DEFERRED - correctly, because there is a first. An operation runs
    alone: nothing is scheduled ahead of it, so the same requirement must
    REFUSE. Deferring on a promise nobody made looks like tolerance and
    behaves like blindness.

    Asserting both outcomes from one input is what pins the distinction.
    Two separate tests would both keep passing if it collapsed.
    """
    plan = operations.get("subtitles.plan")
    # ONE requirement, chosen because it has a single producer: a
    # coverage requirement with three possible producers does not defer
    # on one of them being scheduled, and mixing the two shapes would
    # make this test about the deferral RULE rather than the distinction.
    spine_req = [r for r in plan.requires
                 if r.name == "state.plan_subtitles.audio_spine"]
    assert spine_req, "fixture is stale: plan_subtitles no longer needs audio_spine"
    assert spine_req[0].produced_by == ("mesh_spine",)

    # AS PART OF A RUN: mesh_spine is scheduled, so this defers.
    in_a_run = requirements.check(
        {"plan_subtitles", "mesh_spine"},
        requirements.Context(project_folder=empty_project,
                             run_set=frozenset({"plan_subtitles", "mesh_spine"})),
        spine_req)
    assert in_a_run == [], (
        "inside a run with its producer scheduled, this must DEFER")

    # AS AN OPERATION: nothing else executes, so the same requirement refuses.
    as_operation = plan.unmet(empty_project)
    assert any(u.requirement.name == spine_req[0].name for u in as_operation), (
        "running alone, the same requirement must REFUSE - there is no "
        "producer scheduled ahead to satisfy it")


# ── the refusal teaches ─────────────────────────────────────────────


def test_a_refusal_names_the_input_its_producer_and_why(empty_project):
    """The refusal is SURPRISING - the same state runs fine in a DAG - so
    it names what is missing, who makes it, that running alone is why,
    and the way out."""
    result = operations.get("subtitles.plan").execute(empty_project)
    assert result.refused
    assert "audio_spine" in result.error
    assert "mesh_spine" in result.error
    assert "runs ALONE" in result.error
    assert "run the DAG" in result.error


# ── the success path ────────────────────────────────────────────────


def test_an_operation_runs_to_completion(satisfied_project):
    result = operations.get("subtitles.plan").execute(satisfied_project)
    assert result.completed, result.error
    assert not result.produced_nothing
    entries = result.payload["subtitle_plan"]["subtitle_entries"]
    assert entries, "completed but produced no caption cards"
    assert all(e["speaker"] == "host" for e in entries)


# ── The merged-dict binding: an operation that could not EXECUTE ─────
#
# `docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` measured this and deliberately
# did not fix it:
#
#     `Operation._arguments` binds only parameters the function names,
#     and a bridge/post_bridge body takes one parameter called `data`
#     holding the merged dict. It is not a key in the gathered inputs, so
#     `{}` is bound and the call raises `TypeError`.
#
# Seven operations were in that shape, so seven registry entries were
# listed, `--emit-skill`-ed and unable to run. An operation that cannot
# execute is a catalogue entry, which is what this refactor set out to
# stop being - so the binding is fixed, and this file EXECUTES one of
# them rather than asserting about the fix.


def _project_with_mesh_spine_inputs(tmp_path, target_seconds=45.0):
    """A project carrying what `mesh_spine` needs, and a duration target.

    Everything mesh_spine's six edges ask for is present, because
    `duration_zone.build` is its BRIDGE and an operation runs ALONE -
    nothing is scheduled ahead of it to satisfy a producer.
    """
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "project_config": {"target_duration_seconds": target_seconds},
        "step_outputs": {
            "speech_sequence": {"speech_sequence": {}},
            "music_selection": {"music_selection": {}},
            "creative_direction": {"creative_direction": {}},
            "music_analysis": {"music_analysis": {}},
            "catalog": {"clip_catalog": []},
            "semantic_analysis": {"semantic_analysis_documents": []},
        }}), encoding="utf-8")
    return str(tmp_path)


def test_an_operation_whose_body_takes_the_merged_dict_executes(tmp_path):
    """THE FIX, proved by running one of the seven.

    `duration_zone.build` is `mesh_spine`'s pre-bridge and its whole
    signature is `build_duration_zone(data: dict)`. Before this it raised

        TypeError: build_duration_zone() missing 1 required positional
        argument: 'data'

    on every call, because `data` is not a key in the gathered inputs.
    It now receives the gathered dict, which for a PRE-bridge is exactly
    what the runner writes to its stdin - so the call is not merely
    non-crashing, it is the same call.
    """
    project = _project_with_mesh_spine_inputs(tmp_path, target_seconds=45.0)
    result = operations.get("duration_zone.build").execute(project)

    assert result.completed, result.error
    zone = result.payload["duration_zone"]
    assert zone["target_seconds"] == 45.0, (
        "the operation ran but did not see the project's own declared "
        "target, so `data` is not the gathered dict")
    assert zone["minimum_seconds"] < 45.0 < zone["maximum_seconds"]


def _state(tmp_path, outputs):
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path), "step_outputs": outputs}),
        encoding="utf-8")
    return str(tmp_path)


def test_a_destructured_post_bridge_unit_refuses_naming_its_args(tmp_path):
    """A post-bridge unit registered by its inner deterministic function
    takes arguments the DAG routes under no such name (the model's plan,
    renamed gathered keys, a derived frame). With requirements satisfied
    and the model's answer supplied, each must REFUSE naming the argument
    and the `--set` way to supply it - a `TypeError` here is the crash.
    Never fix by binding the merged dict to the name: that hands a step a
    confidently wrong value. Folded from three tests, one row per lane.
    """
    spine = {"structure": []}
    rows = [
        ("speech.enrich", {
            "creative_direction": {"creative_direction": {"mood": "measured"}},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "temporal_index": {"temporal_event_indices": [],
                               "index_dir": str(tmp_path)}},
         {"speech_sequence": {"body_sequence": []},
          "topics_toon": "", "transcripts_toon": ""},
         ("temporal_index_dir",), "--set temporal_index_dir"),
        ("music.resolve", {
            "creative_direction": {"creative_direction": {"mood": "measured"}},
            "catalog": {"clip_catalog": []},
            "mesh_spine": {"timed_spine": spine}},
         {"music_selection": {"title": "t", "source": "library"}},
         ("selection", "candidates", "target_duration"), "--set selection"),
        ("broll.resolve", {
            "creative_direction": {"creative_direction": {"mood": "measured"}},
            "catalog": {"clip_catalog": []},
            "assign_aroll": {"a_roll_assignments": {}},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "temporal_index": {"temporal_event_indices": []},
            "mesh_spine": {"timed_spine": spine}},
         {"broll_creative": [], "b_roll_interjections": []},
         ("broll_interjections", "semantic_docs", "temporal_indices",
          "target_resolution"), "--set broll_interjections"),
        ("transitions.resolve", {
            "assign_aroll": {"a_roll_assignments": []},
            "catalog": {"clip_catalog": [], "project_fps": 30.0},
            "creative_direction": {"creative_direction": {"mood": "measured"}},
            "mesh_spine": {"audio_spine": spine, "timed_spine": spine},
            "review_rough_cut": {"rough_cut_review": {"passed": True}},
            "temporal_index": {"temporal_event_indices": [],
                               "index_dir": str(tmp_path)},
            "music_selection": {"music_selection": {"track": "t"}},
            "music_analysis": {"music_analysis": {}},
            "select_broll": {"b_roll_assignments": []},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "plan_transitions": {"transition_spec": []}},
         {"transition_creative": []}, ("creative_plan",),
         "--set creative_plan"),
    ]
    for i, (name, outputs, answer, args, teach) in enumerate(rows):
        project = _state(tmp_path / f"row{i}", outputs)
        result = operations.get(name).execute(project, **answer)
        assert result.refused, (
            f"{name} called its body with an argument unbound rather "
            f"than refusing")
        for arg in args:
            assert arg in result.error, (name, arg, result.error)
        assert teach in result.error, (name, result.error)


def test_a_post_bridge_refuses_rather_than_resolving_a_plan_nobody_wrote(
        tmp_path):
    """The other half of the fix, and the reason the finding deferred it.

        Binding `data=gathered` would hand a post-bridge a dict missing
        the model's answer and let it produce a confidently wrong result
        instead of raising. A `TypeError` is honest; a silently
        incomplete `data` is not.

    Both true. The resolution is neither: a post-bridge REFUSES, naming
    the keys the model owes and how to supply them. A pre-bridge is
    unaffected - its `data` IS the step's inputs, which is why
    `duration_zone.build` above completes.
    """
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "compile_manifest": {"assembly_manifest": {}},
            "render": {"render_output": {}},
        }}), encoding="utf-8")

    result = operations.get("validation.resolve").execute(str(tmp_path))
    assert result.refused, (
        "a post-bridge resolved the model's answer against a dict that "
        "carries no model answer")
    assert "POST-BRIDGE" in result.error
    assert "validation_result" in result.error, (
        "the refusal does not name what the model owes this step")
    assert "run validate" in result.error


def test_a_prompt_capability_refuses_naming_the_runner(tmp_path, empty_project):
    """A capability that IS a prompt has no function to call and an
    operation runs alone with no model: with its contract satisfied it
    REFUSES naming the prompt and the way out. With its contract unmet,
    the precondition refusal comes first and is not swallowed."""
    project = _state(tmp_path / "ok", {
        "catalog": {"clip_catalog": []},
        "semantic_analysis": {"semantic_analysis_documents": []},
        "temporal_index": {"temporal_event_indices": []}})
    op = operations.get("creative.direct")
    assert op.unmet(project) == []
    result = op.execute(project)
    assert result.refused
    assert "handoff.md" in result.error
    assert "creative_direction" in result.error
    assert "run the DAG" in result.error

    result = op.execute(empty_project)
    assert result.refused
    assert "clip_catalog" in result.error
    assert "catalog" in result.error


# ── The address, which used to reach no step at all ──────────────────
#
# The captain's third entry point, 2026-09-06: "a user asking for
# certain specific sections of the video to be re-edited".
#
# Six operations declare REGION scope and NOT ONE of them honoured it
# through the registry.  Measured before the fix:
#
#   subtitles.plan, subtitles.render            ran at PROJECT scope and
#                                               returned a whole-project
#                                               answer looking like a
#                                               region's
#   subtitles.splice, subtitles.render_segment,
#   transcript.reindex, transcript.splice       raised TypeError
#
# The first pair is the worse half.  `subtitles.plan` at a region was a
# whole-plan overwrite wearing a region's clothes, which is the one thing
# `subtitles.splice` exists to prevent.


def _three_block_spine():
    """Three blocks, so a region can cover exactly one of them."""
    def block(position, timeline_start, source_start, words):
        stamped = [{"word": w,
                    "source_start": source_start + i * 0.4,
                    "source_end": source_start + (i + 1) * 0.4}
                   for i, w in enumerate(words)]
        span = len(words) * 0.4
        return {"position": position, "block_type": "speech",
                "clip_id": "clip_001", "speaker": "host",
                "source_start": source_start,
                "source_end": source_start + span,
                "timeline_start": timeline_start,
                "timeline_end": timeline_start + span,
                # A real mesh_spine block carries this
                # (step_2_05/post_bridge.py:147) and `subtitle_splice`
                # refuses a splice that cannot show the timing unchanged
                # without it.
                "duration_seconds": round(span, 3),
                "word_timestamps": stamped, "alignment_method": "whisperx",
                "content": {"text": " ".join(words)}}
    return {"structure": [
        block(0, 0.0, 10.0, ["one", "two", "three", "four", "five"]),
        block(1, 2.0, 40.0, ["six", "seven", "eight", "nine", "ten"]),
        block(2, 4.0, 70.0, ["eleven", "twelve", "thirteen", "fourteen"]),
    ]}


@pytest.fixture
def three_block_project(tmp_path):
    spine = _three_block_spine()
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "mesh_spine": {"audio_spine": spine, "timed_spine": spine},
            "review_rough_cut": {"rough_cut_review": {"passed": True}},
            "temporal_index": {"index_dir": str(tmp_path)},
            "speech_sequence": {"speech_sequence": {}},
        }}), encoding="utf-8")
    return str(tmp_path), spine


def _block_one(spine):
    from library.tools import region as region_mod
    from library.tools import scope as scope_mod

    block = spine["structure"][1]
    return scope_mod.region(region_mod.Region(
        None, block["timeline_start"], block["timeline_end"]))


def test_an_operation_at_a_region_plans_exactly_what_the_step_would(
        three_block_project):
    """The gate FIRES on the defect: a region-scoped plan must not be a
    whole-project plan (measured before the fix: PROJECT 4 cards, REGION
    4 cards, the step called directly 1 card). And not just fewer - THE
    SAME as the step's own answer for that scope."""
    project, spine = three_block_project
    plan = operations.get("subtitles.plan")
    where = _block_one(spine)

    whole = plan.execute(project)
    part = plan.execute(project, scope=where)
    direct = plan.run(audio_spine=spine, scope=where)

    def cards(payload):
        return payload["subtitle_plan"]["subtitle_entries"]

    assert whole.completed and part.completed
    assert len(cards(part.payload)) < len(cards(whole.payload)), (
        "the region planned as much as the whole project, so the address "
        "did not reach the step")
    assert cards(part.payload) == cards(direct)


# ── An operation that cannot be called REFUSES, it does not crash ────


def test_an_operation_whose_caller_owes_it_an_argument_refuses_by_name(
        three_block_project):
    """`subtitles.splice` needs the plan it is splicing INTO, and nothing
    the DAG routes to `plan_subtitles` carries it. Before this it raised

        TypeError: splice_region_plan() missing 2 required positional
        arguments: 'stored_plan' and 'scope'

    which is a crash, not a refusal."""
    project, spine = three_block_project
    result = operations.get("subtitles.splice").execute(
        project, scope=_block_one(spine))

    assert result.refused
    assert "stored_plan" in result.error
    assert "--set stored_plan=@stored_plan.json" in result.error

    # The mirror: doing what the refusal says works.
    stored = operations.get("subtitles.plan").execute(project).payload
    result = operations.get("subtitles.splice").execute(
        project, scope=_block_one(spine), stored_plan=stored["subtitle_plan"])
    assert result.completed, result.error
    assert result.payload["splice"]


# ── --set: the way out the refusal names ─────────────────────────────


def test_set_refuses_a_bare_word_rather_than_guessing_it_is_a_string():
    """A value here is a structure. Falling back to "it must be a string"
    would hand a step the text `[1, 2]` and let it fail further in."""
    with pytest.raises(operations.OperationError) as exc:
        operations.parse_overrides(["stored_plan=not json"])
    assert "not valid JSON" in str(exc.value)


# --------------------------------------------------------------------------
# From test_ren_entry_motion_and_property_ops.py
#
# Ren's entry-motion and property-set operations (`reel.entry_motion`,
# `reel.set_properties`): owned by `build_reels`, routed through the
# touchup's stage-conform-write-verify-promote path, written in place and
# judged by re-read. Design history: `docs/evidence/reel_touchup.md`.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composer as C  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from tests.composed_edit_harness import (  # noqa: E402
    FakeComp, FakeTool, build_reel, covering_window, duplicate,
    media_pool,
)

#: The effect both operations derive - the same requirement their
#: owning node's siblings carry.  One node, one effect, four routes.
REEL_GOAL = "state.verify_reels.reel_build"

NEW_OPERATIONS = ("reel.entry_motion", "reel.set_properties")


# ── Registration: the 1327 shape ──────────────────────────────────────


# ── Effect and requires: derived, in the vocabulary ───────────────────


# ── Reachability through compose, without steering it ─────────────────


def test_the_representative_stays_the_rebuild():
    """The forbidden move, pinned as a refusal to make it.

    Two new siblings share `build_reels`' effect, so compose has four
    routes to one goal.  Bending a declaration to steer the choice -
    making one operation's effect differ from another's - is exactly
    what 1327 forbade, and choosing between equivalent routes is the
    sibling lane's open problem, not this one's.  The representative
    stays the rebuild."""
    assert C.representative("build_reels") == "reel.build"
    assert C.compose(REEL_GOAL).operations == ("reel.build",)


# ── The step bodies refuse what is malformed ──────────────────────────


def test_step_bodies_refuse_malformed_specs_and_each_others_edits():
    """The contract `touch_reel` keeps: no project folder, no mapping, or
    nothing to do raise before Resolve is touched. And one body, one
    kind - `reel.touchup` runs mixed kinds, so nothing servable is
    refused."""
    from library.steps.step_7_01_build_reels.step import (
        animate_entry, set_clip_properties)

    for body in (animate_entry, set_clip_properties):
        with pytest.raises(ValueError, match="project_folder"):
            body("", {"reel": 1, "edits": [
                {"op": "set_properties", "row": "V4", "item": 0,
                 "properties": {"ZoomX": 1.5}}]})
        with pytest.raises(TypeError, match="spec mapping"):
            body("/project", ["not", "a", "mapping"])
        with pytest.raises(ValueError, match="at least one edit"):
            body("/project", {"reel": 1, "edits": []})

    motion = {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6}]}
    props = {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]}
    with pytest.raises(ValueError, match="entry_motion"):
        set_clip_properties("/project", motion)
    with pytest.raises(ValueError, match="set_properties"):
        animate_entry("/project", props)


# ── The gate: what routes, what refuses ───────────────────────────────


def _tracks(timeline):
    return reel_read.read_tracks(timeline)


def test_in_place_kinds_qualify_composed_with_no_delete_or_place(tmp_path):
    """Both in-place kinds qualify COMPOSED and the composition plan is
    empty: no change, insertion or removal."""
    timeline, _pool, _media = build_reel(tmp_path)
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5, "Opacity": 80.0}}]})
    assert qualification.gate_class == tu.COMPOSED
    (entry,) = qualification.in_place
    assert entry["kind"] == "set_properties"
    assert (entry["row"], entry["item_index"]) == ("V4", 0)
    assert entry["properties"] == {"ZoomX": 1.5, "Opacity": 80.0}
    assert (qualification.changes, qualification.insertions,
            qualification.removals) == ([], [], [])

    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6, "fade_out_frames": 6}]})
    assert qualification.gate_class == tu.COMPOSED
    (entry,) = qualification.in_place
    assert entry["kind"] == "entry_motion"
    assert (entry["fade_in_frames"], entry["fade_out_frames"]) == (6, 6)
    assert entry["duration"] == 40
    assert (qualification.changes, qualification.insertions,
            qualification.removals) == ([], [], [])


def test_an_in_place_edit_it_cannot_write_refuses_before_staging(tmp_path):
    """Each row refuses loudly, before anything is staged, rather than
    being skipped: a property mapping that is empty or not a mapping, an
    item that is not there, an animation that is nothing or negative, a
    ramp with no neutral frame (it would hold across the clip,
    `fusion.played_window`), and the comp-bearing or audio rows - V1/V2
    treatments belong to the comp pass, and a second treatment the
    recorded manifest does not know is dropped by the next
    re-derivation."""
    timeline, _pool, _media = build_reel(tmp_path)
    props = {"op": "set_properties", "row": "V4", "item": 0}
    motion = {"op": "entry_motion", "row": "V4", "item": 0}
    rows = [
        (dict(props, properties={}), "names no properties"),
        (dict(props, properties="ZoomX"), "a `properties` mapping"),
        (dict(props, item=9, properties={"ZoomX": 1.5}), None),
        (dict(motion, fade_in_frames=0, fade_out_frames=0),
         "animates nothing"),
        (dict(motion, fade_in_frames=-4), "negative"),
        (dict(motion, fade_in_frames=20, fade_out_frames=20),
         "one frame more"),
        # Exactly filling the clip still leaves no neutral frame.
        (dict(motion, fade_in_frames=40), None),
        (dict(motion, row="V1", fade_in_frames=6), "comp-bearing row"),
        (dict(motion, row="A1", fade_in_frames=6), "audio"),
    ]
    for edit, match in rows:
        with pytest.raises(tu.TouchupRefused, match=match):
            tu.qualify(_tracks(timeline), {"reel": 1, "edits": [edit]})


def test_entry_motion_refuses_an_item_that_already_carries_a_comp(
        tmp_path):
    """Stacking a second treatment beside one the manifest does not
    know is how a re-derivation drops work silently - so the gate
    refuses it the way `swap_pixels` refuses a comp-carrying swap."""
    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn", covering_window(40, 0, 200)),
        "Transform1": FakeTool("Transform", {"Size": 1.0})})]
    with pytest.raises(tu.TouchupRefused, match="drawing comp"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 6}]})
    # Resolve's own empty auto composition still qualifies: it draws
    # nothing, so it is not a treatment - the 10.4 direction.
    timeline.rows["V4"][0].comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn", covering_window(40, 0, 200)),
        "MediaOut1": FakeTool("MediaOut", {}),
        "AudioDisplay1": FakeTool("AudioDisplay", {})})]
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6}]})
    assert qualification.gate_class == tu.COMPOSED


def test_one_source_item_means_one_edit(tmp_path):
    """Two edits addressing the same item refuse by position - the
    existing rule, extended to the in-place kinds.  State those as
    two touchups."""
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused, match="same item"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "set_properties", "row": "V4", "item": 0,
             "properties": {"ZoomX": 1.5}},
            {"op": "move", "row": "V4", "item": 0, "to_row": "V4",
             "to_record": 1300}]})
    with pytest.raises(tu.TouchupRefused, match="same item"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 6},
            {"op": "set_properties", "row": "V4", "item": 0,
             "properties": {"ZoomX": 1.5}}]})


def test_a_rewrite_shadowed_by_an_in_place_write_is_pruned(tmp_path):
    """A remove re-places every kept item on its row - but an item
    another edit writes onto in place needs no re-place: the
    composition's capture reads the staged item after the write, so
    pruning the rewrite loses nothing and avoids the churn."""
    timeline, _pool, _media = build_reel(tmp_path)
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1},
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]})
    assert qualification.gate_class == tu.COMPOSED
    assert [(c.row, c.item_index) for c in qualification.changes] == [
        ("V4", 2)]
    assert len(qualification.in_place) == 1


# ── The write: onto staging, judged by re-read ────────────────────────


def _staged_pair(tmp_path):
    approved = build_reel(tmp_path)[0]
    staged = duplicate(approved)
    return approved, staged, media_pool(staged)


def test_property_sets_write_with_no_delete_and_no_place(tmp_path):
    """The property-set half of the row: `SetProperty` with read-back,
    and the delete/place machinery never runs - the calls that would
    destroy and rebuild the row stay empty."""
    _approved, staged, _pool = _staged_pair(tmp_path)
    before = staged.rows["V4"][0].GetProperty("ZoomX")
    assert before != 1.5
    qualification = tu.qualify(_tracks(staged), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]})
    applied = tu._apply_in_place(staged, qualification,
                                 str(tmp_path / "comps"))
    assert staged.rows["V4"][0].GetProperty("ZoomX") == 1.5
    assert staged.delete_calls == []
    assert _pool.append_calls == []
    assert applied["properties"][0]["properties"] == {"ZoomX": 1.5}
    assert applied["entry_motion"] == []


def test_a_property_that_will_not_read_back_refuses(tmp_path):
    """The write is judged by re-read, never by `SetProperty`'s
    return: an item whose read-back disagrees refuses mid-flight,
    with the staging left standing and the approved reel untouched."""
    _approved, staged, _pool = _staged_pair(tmp_path)
    original = staged.rows["V4"][0].GetProperty

    def _lying_get(key=None):
        current = dict(original())
        current["ZoomX"] = 999.0
        return current if key is None else current.get(key)

    staged.rows["V4"][0].GetProperty = _lying_get
    qualification = tu.qualify(_tracks(staged), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]})
    with pytest.raises(tu.TouchupError, match="did not take"):
        tu._apply_in_place(staged, qualification,
                           str(tmp_path / "comps"))


def test_entry_motion_imports_a_drawing_comp_with_a_covering_window(
        tmp_path):
    """The entry-motion half: a builder-authored fade lands on the
    staged item, draws something, and its MediaIn covers every frame
    the item plays - the conform the comp pass runs on its own
    imports, read off the re-read rather than the return value."""
    _approved, staged, _pool = _staged_pair(tmp_path)
    item = staged.rows["V4"][0]
    assert item.GetFusionCompCount() == 0
    qualification = tu.qualify(_tracks(staged), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6, "fade_out_frames": 6}]})
    applied = tu._apply_in_place(staged, qualification,
                                 str(tmp_path / "comps"))
    assert item.GetFusionCompCount() == 1
    assert staged.delete_calls == []
    assert _pool.append_calls == []
    record = applied["entry_motion"][0]
    assert record["fade_in_frames"] == 6
    assert Path(record["comp"]).is_file()
    # The authored comp really is a fade over the played window.
    text = Path(record["comp"]).read_text(encoding="utf-8")
    assert "BezierSpline" in text
    assert record["window"]["repaired"] is False
    assert record["window"]["reason"] is None
    # And the re-read agrees the new comp draws something.
    tools = item.GetFusionCompByIndex(1).GetToolList(False)
    reg_ids = sorted(t.GetAttrs("TOOLS_RegID") for t in tools.values())
    assert "Merge" in reg_ids
    assert reel_read.comp_draws_something({"tools": reg_ids}) is True


# --------------------------------------------------------------------------
# From test_ren_find_row_and_batch.py
#
# Ren locates the named clip and runs the swap per reel, not per row.
#
# Three probe findings off the captain's first real Ren test (geo-podcast:
# 27 of 30 reels carry the end logo on V6, so 27 dry runs refused with the
# V7 default): Ren took `--row` as given instead of locating the named
# clip, it ran one reel per invocation, and its refusal sent the model
# back to re-check a name that was right when the row was wrong.
#
# Nothing here reaches Resolve or a real project: tracks are hand-built
# in the shape `reel_read.read_tracks` returns, the project is a
# `tmp_path` folder carrying only a proposal file, and the touchup batch
# runs against fake reader/applier seams.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reel_touchup as T  # noqa: E402
from library.tools import ren_dry_run as D  # noqa: E402


def _clip(name, start, duration, *, comps=0):
    return {
        "name": name,
        "record_in": start,
        "record_out": start + duration,
        "duration": duration,
        "left_offset": 0,
        "right_offset": 0,
        "fusion": {"comp_count": comps, "comp_names": [], "media_windows": []},
    }


def _tracks_2(*rows):
    """`read_tracks`-shaped tracks: `[("V6", [clips]), ...]`."""
    tracks = []
    for row, clips in rows:
        tracks.append(
            {
                "type": ("audio" if str(row).upper().startswith("A") else "video"),
                "index": int(str(row)[1:]),
                "name": str(row).upper(),
                "speaker": None,
                "clips": list(clips),
            }
        )
    return tracks


def _project_with_reels(tmp_path, *numbers):
    """A project folder naming the given reels, each approved."""
    from library.tools import reel_proposal as proposal

    moments = [
        proposal.ReelMoment(
            number=int(number),
            slug=f"reel-{number}",
            reason="the batch test reel",
            timeline_start=0.0,
            timeline_end=60.0,
            approval=proposal.Approval.APPROVED,
        )
        for number in numbers
    ]
    proposal.write_proposal(proposal.proposal_path(str(tmp_path)), moments, {})
    return str(tmp_path)


def _media(tmp_path):
    path = tmp_path / "logo_bulb_lines_23976.mov"
    path.write_bytes(b"RIFF....fake-replacement")
    return str(path)


# ── Finding 1: the row is located, not taken as given ───────────────


def test_search_finds_the_named_clip_off_the_default_row():
    """The defect: 27 dry runs refused because Ren looked only on V7
    while the logo sat on V6 - the search itself crosses the rows. And
    `--row` as a narrowing keeps working: it stops being a requirement,
    not a capability."""
    tracks = _tracks_2(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
        ("V7", [_clip("caption_overlay.mov", 1440, 72)]),
    )
    for row in (None, "V6"):
        spec = D.build_change_spec(
            tracks, reel=26, row=row,
            old_clip="logo_bulb_23976.mov", new_media="/lab/new.mov")
        assert spec["edits"][0]["row"] == "V6"
        assert spec["edits"][0]["item"] == 0


# ── Finding 3: the refusal names rows, not the name ─────────────────


def test_a_refusal_names_the_rows_not_the_name():
    """The defect: the refusal said 're-read the reel and state the clip
    by its current name' when the name was right and the row was wrong,
    so the model re-checked the name in circles. Each refusal says where
    it looked: the row it searched and the row holding the name; the
    rows searched for an absent clip; both positions of a clip on two
    rows (one spec meaning two items)."""
    tracks = _tracks_2(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
    )
    with pytest.raises(D.DryRunRefused) as caught:
        D.build_change_spec(tracks, reel=26, row="V7",
                            old_clip="logo_bulb_23976.mov",
                            new_media="/lab/new.mov")
    message = str(caught.value)
    assert "V7" in message and "V6" in message
    assert "state the clip by its current name" not in message

    absent = _tracks_2(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V6", [_clip("something_else.mov", 1440, 72)]),
    )
    with pytest.raises(D.DryRunRefused, match="no item named"):
        D.build_change_spec(absent, reel=26,
                            old_clip="logo_bulb_23976.mov",
                            new_media="/lab/new.mov")

    doubled = _tracks_2(
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
        ("V7", [_clip("logo_bulb_23976.mov", 0, 72)]),
    )
    with pytest.raises(D.DryRunRefused, match="2 times") as caught:
        D.build_change_spec(doubled, reel=26,
                            old_clip="logo_bulb_23976.mov",
                            new_media="/lab/new.mov")
    assert "V6" in str(caught.value) and "V7" in str(caught.value)


# ── Finding 2: the batch, dry ───────────────────────────────────────


def test_batch_dry_run_reports_each_reel_and_never_stops(tmp_path):
    """The defect: one reel per invocation, so a refused reel ended
    the whole change - the batch carries every reel's answer."""
    project = _project_with_reels(tmp_path, 26, 27)
    tracks_by_reel = {
        26: _tracks_2(
            ("V1", [_clip("interview_a.mp4", 0, 1440)]),
            ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
        ),
        27: _tracks_2(
            ("V1", [_clip("interview_b.mp4", 0, 1440)]),
            ("V6", [_clip("unrelated.mov", 1440, 72)]),
        ),
    }
    summary = D.dry_run_all_reels(
        project_folder=project,
        project_label="demo",
        old_clip="logo_bulb_23976.mov",
        new_media=_media(tmp_path),
        tracks_by_reel=tracks_by_reel,
        offline=True,
    )
    by_reel = {entry["reel"]: entry for entry in summary["reels"]}
    assert set(by_reel) == {26, 27}
    assert by_reel[26]["ok"] is True
    assert by_reel[26]["record"] is not None
    assert by_reel[26]["record"]["change"]["row"] == "V6"
    assert by_reel[27]["ok"] is False
    assert "no item named" in by_reel[27]["refused"]
    assert summary["go"] is False

    # A reel the tracks directory never described is REPORTED refused,
    # never live-read, and never stops the reels that were described.
    summary = D.dry_run_all_reels(
        project_folder=project, project_label="demo",
        old_clip="logo_bulb_23976.mov", new_media=_media(tmp_path),
        tracks_by_reel={26: tracks_by_reel[26]}, offline=True)
    by_reel = {entry["reel"]: entry for entry in summary["reels"]}
    assert by_reel[26]["ok"] is True
    assert by_reel[27]["ok"] is False
    assert "no off-disk tracks" in by_reel[27]["refused"]


def test_batch_dry_run_report_says_nothing_executed(tmp_path, capsys):
    """The defect the batch must not introduce: an all-reels loop
    that executes - the report is still print-and-stop per reel."""
    project = _project_with_reels(tmp_path, 26, 27)
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    (tracks_dir / "26.json").write_text(
        json.dumps(
            {"tracks": _tracks_2(
                ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]))}),
        encoding="utf-8",
    )
    (tracks_dir / "27.json").write_text(
        json.dumps(
            {"tracks": _tracks_2(
                ("V6", [_clip("unrelated.mov", 1440, 72)]))}),
        encoding="utf-8",
    )
    code = D.main(
        [
            project,
            "--all-reels",
            "--old-clip",
            "logo_bulb_23976.mov",
            "--new-media",
            _media(tmp_path),
            "--tracks-dir",
            str(tracks_dir),
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "Reel 26" in out and "Reel 27" in out
    assert "REFUSED" in out
    assert "BATCH VERDICT" in out
    assert "Nothing executed." in out


# ── Finding 2: the batch, real ──────────────────────────────────────


def test_touchup_batch_continues_past_a_refused_reel(tmp_path):
    """The defect: one reel's refusal ending the change for every
    reel after it - the refused reel is reported, the rest still
    land, in plan order."""
    project = _project_with_reels(tmp_path, 26, 27)
    reads = {
        26: _tracks_2(("V6", [_clip("unrelated.mov", 1440, 72)])),
        27: _tracks_2(("V6", [_clip("logo_bulb_23976.mov", 1440, 72)])),
    }
    applied = []

    def reader(number, _final):
        return reads[int(number)]

    def applier(folder, spec):
        applied.append(spec)
        return {"reel": spec["reel"], "landed": True}

    summary = T.touchup_all_reels(
        project,
        old_clip="logo_bulb_23976.mov",
        new_media="/lab/new.mov",
        reader=reader,
        applier=applier,
    )
    assert [entry["reel"] for entry in summary["reels"]] == [26, 27]
    assert summary["reels"][0]["ok"] is False
    assert "no item named" in summary["reels"][0]["refused"]
    assert summary["reels"][1]["ok"] is True
    assert applied and applied[0]["reel"] == 27
    assert applied[0]["edits"][0]["row"] == "V6"
    assert summary["ok"] is False


def test_touchup_batch_reports_an_execute_refusal_without_raising(
    tmp_path,
):
    """The defect: the applier's own refusal propagating as a
    traceback - it lands in the per-reel report instead."""
    project = _project_with_reels(tmp_path, 26, 27)

    def reader(_number, _final):
        return _tracks_2(
            ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]))

    def applier(_folder, spec):
        raise T.TouchupRefused(
            f"reel {spec['reel']} is signed off",
            "a touch-up over a signed-off reel replaces an approval",
            "declare it with --supersede, then re-run")

    summary = T.touchup_all_reels(
        project,
        old_clip="logo_bulb_23976.mov",
        new_media="/lab/new.mov",
        reader=reader,
        applier=applier,
    )
    assert all(entry["ok"] is False for entry in summary["reels"])
    assert all("refused" in entry["refused"]
               for entry in summary["reels"])
    assert summary["landed"] == 0


# --------------------------------------------------------------------------
# From test_ren_selection_between_equivalent_routes.py
#
# Selection between equivalent routes: the post-composition selector.
#
# The captain's complaint that started Ren: "doing some minor edits
# should not force a rebuild". Items 1-3 and both gap-closers landed
# the machinery and the edit STILL routed to a rebuild, because
# `composer.representative` picks among `build_reels` siblings by a
# static tie-break (PROJECT scope, non-`post_bridge` body, registry
# order) that always lands on `reel.build`. `_closure` plans over
# NODES, so the search cannot even see that four routes exist.
#
# This file pins the last piece of item 4
# (`vep-ren-selection-between-equivalent-routes`), built to the design
# in `data/vep-ren-two-routes-one-goal-no-basis-to-choose/report.md`
# sections 4-6:
#
# * `_closure` unchanged, `compose(goal)` unchanged and still purely
#   the representative - the context-free default stays the rebuild.
# * `compose_with_change(goal, change_spec, tracks)` selects among the
#   siblings using runtime context: the structured change plus the
#   `reel_touchup.qualify` verdict over a live track read.
# * The plan record narrates the choice (report 7.2/7.3): which route
#   won and why, what the other route would have done differently,
#   and rebuild viability when the rebuild is the fallback.
#
# What is FORBIDDEN here, and pinned as such: bending one operation's
# declared effect to steer the choice. All four siblings genuinely
# produce the same state key and must keep declaring the same thing -
# `test_siblings_keep_declaring_the_same_effect` refuses the trap the
# whole rebuild rests on. No registry, requirement, or step-body
# change backs this file; if one becomes necessary, the design is
# wrong, not the test.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import operations as O  # noqa: E402

#: The goal every `build_reels` sibling's derived effect names.

#: The four change-serving routes to it: the full rebuild plus the
#: three caller-supplied touchup-family operations. `reel.ask` shares
#: the derived effect but takes no change spec, so it is out of the
#: candidate set by its own contract.
CHANGE_ROUTES = ("reel.build", "reel.touchup", "reel.entry_motion",
                 "reel.set_properties")


def _tracks_3(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    return reel_read.read_tracks(timeline)


def _swap_spec():
    """The measured class: an ending swap, `swap_pixels` on V4."""
    return {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": "/lab/new_card.mov"}]}


# ── The default must not move ──────────────────────────────────────


def test_free_text_goal_still_refuses_by_name():
    """Free text never reaches the selector: the goal vocabulary is
    unchanged, so the ending swap phrased plainly refuses - with no
    selection attached, because there is no route to choose."""
    comp = C.compose_with_change("reel 26 ending swap", _swap_spec(),
                                 tracks=[])
    assert comp.refused
    assert comp.unknown_goal
    assert comp.selection == ()


# ── The gate decides ───────────────────────────────────────────────


def test_gate_composed_routes_to_touchup_with_the_basis_cited(tmp_path):
    """The ending-swap class takes the cheap route, narrated."""
    comp = C.compose_with_change(REEL_GOAL, _swap_spec(),
                                 _tracks_3(tmp_path))
    assert comp.completed
    assert comp.operations == ("reel.touchup",)
    (selection,) = comp.selection
    assert selection.node == "build_reels"
    assert selection.decided_by == C.SELECTOR
    assert selection.gate_class == tu.COMPOSED
    assert selection.measured_basis_cited is True
    # The narration: which route and why, the measured basis, and
    # what the other route would have done differently.
    assert "reel.touchup" in selection.reason
    assert "88x" in selection.reason
    assert "Reel 26" in selection.reason
    assert "re-derives" in selection.reason
    assert set(selection.alternatives) == set(CHANGE_ROUTES) - {
        "reel.touchup"}


def test_gate_refusal_routes_to_rebuild_with_the_caveat_surfaced(
        tmp_path):
    """A refused touchup falls back to the rebuild - and the plan
    says the fallback is not an always-available slow path."""
    spec = {"reel": 1, "edits": [{"op": "dissolve"}]}
    comp = C.compose_with_change(REEL_GOAL, spec, _tracks_3(tmp_path))
    assert comp.completed
    assert comp.operations == ("reel.build",)
    (selection,) = comp.selection
    assert selection.decided_by == C.SELECTOR
    assert selection.gate_class == "refused"
    assert "refused" in selection.reason
    assert "dissolve" in selection.reason
    # Report 7.3: on Reel 26 the rebuild refuses today, so routing
    # here risks a ~208s refusal, not a success - surfaced now,
    # not 208 seconds later.
    assert "208" in selection.reason
    assert "not an always-available" in selection.reason


def test_length_changing_spec_stays_composed_with_the_receipt(tmp_path):
    """`composed_with_rederivation` still takes the composed path -
    the gate never falls back to a rebuild - with its own cost
    statement in the record rather than the 88x basis, which was
    measured on a class with no length change."""
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V4", "item": 0, "duration": 42}]}
    comp = C.compose_with_change(REEL_GOAL, spec, _tracks_3(tmp_path))
    assert comp.completed
    assert comp.operations == ("reel.touchup",)
    (selection,) = comp.selection
    assert selection.gate_class == tu.COMPOSED_WITH_REDERIVATION
    assert selection.measured_basis_cited is False
    assert "88x" not in selection.reason


# ── Four routes, not two ───────────────────────────────────────────


def test_single_kind_specs_route_to_the_narrowest_operation(tmp_path):
    """One-kind in-place specs take the operation that owns the kind;
    anything mixed (or composed at all) rides the touchup. All four are
    chosen, never inherited. Risk 5: the 2.37s-vs-208s basis was taken
    on an ending swap and testifies about that class only - these route
    cheap without citing it, naming it only to decline it."""
    for edits, expected in (
        ([{"op": "set_properties", "row": "V4", "item": 0,
           "properties": {"ZoomX": 1.5}}], "reel.set_properties"),
        ([{"op": "entry_motion", "row": "V4", "item": 0,
           "fade_in_frames": 6}], "reel.entry_motion"),
    ):
        comp = C.compose_with_change(
            REEL_GOAL, {"reel": 1, "edits": edits},
            _tracks_3(tmp_path / expected))
        assert comp.completed
        assert comp.operations == (expected,)
        (selection,) = comp.selection
        assert selection.decided_by == C.SELECTOR
        assert selection.gate_class == tu.COMPOSED
        assert selection.measured_basis_cited is False
        assert "testifies about nothing else" in selection.reason


def test_spec_without_tracks_stands_on_the_representative(tmp_path):
    """A change with no track read cannot be qualified: the gate
    reads tracks, not prose, so the static tie-break stands - and
    says so, rather than guessing the cheap route."""
    comp = C.compose_with_change(REEL_GOAL, _swap_spec())
    assert comp.completed
    assert comp.operations == ("reel.build",)
    (selection,) = comp.selection
    assert selection.decided_by == C.REPRESENTATIVE_FALLBACK


def test_free_text_change_spec_raises_rather_than_routing(tmp_path):
    """Garbage in the change slot is a caller error, not a slow
    rebuild: prose must never route to a 208s run in silence."""
    with pytest.raises(C.ComposerError):
        C.compose_with_change(REEL_GOAL, "swap the ending",
                              _tracks_3(tmp_path))


# ── Risk 4: no route arrives unchosen-by-design ─────────────────────


def _routable_multi_operation_nodes():
    """Nodes where two capabilities produce the same requirement.

    Each capability declares its own effect, so a bridge, a receipt or
    a region unit is not a route; two capabilities producing one
    requirement (a project route and its region splice) are."""
    from collections import defaultdict

    producers: dict = defaultdict(set)
    for op in O.all():
        for r in op.effect:
            producers[(op.legacy_node, r.name)].add(op.name)
    return sorted({node for (node, _), ops in producers.items()
                   if len(ops) > 1})


def test_every_routable_node_has_a_selector():
    """Dropped in the suite halving (#1351) with its helper left behind,
    and the hole was live: the `*.splice` operations each gave their
    node a second route with no selector, so `compose_with_change`
    raised for every plan through `assign_aroll` - measured 2026-10-02
    on `optional.compile_manifest.cohesion_review`. That plan now
    selects its routes."""
    missing = sorted(set(_routable_multi_operation_nodes())
                     - set(C.selector_coverage()))
    assert not missing, f"routable nodes with no selector: {missing}"

    comp = C.compose_with_change("optional.compile_manifest.cohesion_review")
    assert comp.completed
    assert "aroll.assign" in comp.operations
    assert not any(op.endswith(".splice") for op in comp.operations)


# ── The plan record narrates the choice ────────────────────────────


# --------------------------------------------------------------------------
# From test_native_ops.py
#
# Native Resolve operations in the plan (fidelity rung 3b).
#
# PR 1376 measured which Resolve 21.1 operations really work
# (`TimelineItem.SetSpeed`, `TimelineItem.AddTransition`, each judged by
# read-back). This rung makes them reachable from the plan: `speed_ramp`
# and `freeze_frame` in `plan_vfx`, the granted transition names in
# `plan_transitions`, routed through the 0.6.0 verbs during the timeline
# build. A name 1376 measured as refused is refused by name - never a
# silently downgraded hard cut.

# ── The vocabulary ───────────────────────────────────────────────────

def test_the_vocabulary_is_well_formed():
    native_ops.assert_vocabulary_is_well_formed()


def test_names_resolve_to_one_side_and_route_where_measured():
    """Granted aliases resolve as granted, refused ones as refused, never
    both; each routes to Resolve (not a Fusion builder) in the categories
    PR 1376 measured."""
    assert native_canonical("cross_dissolve") == "cross_dissolve"
    assert native_canonical("Dissolve") == "cross_dissolve"
    assert native_canonical("slide") == "slide"
    assert native_canonical("Smooth Cut") == "smooth_cut"
    assert native_canonical("spin") == "spin"
    assert native_canonical("whip_pan") is None
    assert native_canonical("utter_nonsense") is None

    assert refused_native_canonical("whip_pan") == "whip_pan"
    assert refused_native_canonical("Whip") == "whip_pan"
    assert refused_native_canonical("dip") == "dip"
    assert refused_native_canonical("push") == "push"
    assert refused_native_canonical("Blur Dissolve") == "blur_dissolve"
    assert refused_native_canonical("cross_dissolve") is None

    assert granted_categories("cross_dissolve") == ("simple", "fusion")
    assert granted_categories("slide") == ("simple",)
    assert granted_categories("smooth_cut") == ("simple",)
    assert granted_categories("spin") == ("fusion",)
    assert resolve_transition_name("cross_dissolve") == "Cross Dissolve"

    assert route_of("cross_dissolve") == "native_resolve"
    assert route_of("dissolve") == "native_resolve"
    assert is_native("slide")
    assert known_type("spin") == "spin"
    assert not is_native("fade_to_black")
    assert refused_native_reason("whip_pan").strip()
    assert refused_native_reason("cross_dissolve") == ""

    # A native type reaching a Fusion builder is a misroute.
    from library.tools.transition_vocabulary import canonical_type
    assert canonical_type("cross_dissolve") is None
    assert native_canonical_type("cross_dissolve") == "cross_dissolve"


def test_a_refusal_carries_what_why_and_fix():
    refused = native_ops.refuse_native_transition("whip_pan")
    assert isinstance(refused, RenRefusal)
    assert "whip_pan" in refused.what.lower() or "Whip Pan" in refused.what
    assert refused.why.strip()
    assert refused.fix.strip()


# ── plan_vfx admits speed ops ────────────────────────────────────────

def _spine_2(*positions):
    blocks = []
    for i, (position, clip_id) in enumerate(positions):
        blocks.append({
            "position": position,
            "block_type": "speech",
            "clip_id": clip_id,
            "timeline_start": float(i * 5),
            "timeline_end": float(i * 5 + 5),
            "word_timestamps": [],
            "alignment_method": "whisperx",
        })
    return {"structure": blocks, "total_estimated_duration_seconds": 20.0}


def test_a_whole_block_ramp_and_freeze_resolve_natively():
    """One step spanning the whole block, or a freeze, matches the one
    item the block places as."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    (entry,) = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {"segments": [{"percent": 50}]},
          "rationale": "slow the whole beat"}],
        _spine_2((1, "clip_a")), 30.0, dropped=[])
    assert entry["route"] == "native_resolve"
    (only,) = entry["params"]["segments"]
    assert only["percent"] == 50.0
    assert (only["timeline_start"], only["timeline_end"]) == (0.0, 5.0)

    (entry,) = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "freeze_frame",
          "rationale": "hold the look"}],
        _spine_2((1, "clip_a")), 30.0, dropped=[])
    assert entry["route"] == "native_resolve"
    assert entry["effect_type"] == "freeze_frame"
    assert (entry["timeline_start"], entry["timeline_end"]) == (0.0, 5.0)


def test_an_unplayable_speed_entry_is_dropped_with_its_reason():
    """Finding 35: a ramp whose steps subdivide the one block (B8:
    24.400-25.057 s inside the b-roll block) failed the whole build with
    "no timeline item spans ... - nothing was written", because nothing
    blades the item. Each row here is dropped, with its reason, instead.
    A freeze is spelled `freeze_frame`, never a 0% step."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    rows = [
        ({"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {"segments": [{"percent": 200}, {"percent": 50}]},
          "rationale": "montage ramp"},
         "speed_span_subdivides_block", ("0.000-2.500", "blade")),
        # A sub-block word span matches no placed item either.
        ({"target_block_position": 1, "effect_type": "freeze_frame",
          "anchor": {"frame": 30}, "anchor_end": {"frame": 60},
          "rationale": "hold the word"},
         "speed_span_subdivides_block", ()),
        ({"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {}}, "not_a_speed_step", ()),
        ({"target_block_position": 1, "effect_type": "speed_ramp",
          "params": {"segments": [100, 0]}},
         "not_a_speed_step", ("freeze_frame",)),
    ]
    for entry, reason, words in rows:
        dropped = []
        assert resolve_vfx([entry], _spine_2((1, "clip_a")), 30.0,
                           dropped=dropped) == [], entry
        assert dropped[0].reason == reason, entry
        for word in words:
            assert word in dropped[0].detail, (word, dropped[0].detail)


def test_a_curve_param_refuses_rather_than_rounding():
    """Rounding a curve to constants would invent pacing the plan
    declined to step out - the model re-plans with `segments`."""
    from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
    with pytest.raises(NativeSpeedRefused, match="stepped"):
        resolve_vfx(
            [{"target_block_position": 1, "effect_type": "speed_ramp",
              "params": {"segments": [50, 150], "curve": "ease-in-out"}}],
            _spine_2((1, "clip_a")), 30.0, dropped=[])


# ── The applicator, against fake items ───────────────────────────────

class _FakeItem:
    def __init__(self, name, start, end, speed=100.0, no_get_speed=False):
        self._name = name
        self._start = start
        self._end = end
        self._speed = speed
        self._no_get_speed = no_get_speed
        self.writes = []

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetSpeed(self):
        if self._no_get_speed:
            raise AttributeError("no GetSpeed")
        return {"Percentage": self._speed}

    def SetSpeed(self, opts):
        self.writes.append(dict(opts))
        self._speed = float(opts["Percentage"])
        return True

    def AddTransition(self, payload):
        if getattr(self, "_transition refused", False):
            return None
        tr = _FakeItem(payload["type"], self._start, self._start + 10)
        tr._payload = payload
        return tr


class _FakeTimeline:
    def __init__(self, v1=(), v2=()):
        self._tracks = {1: list(v1), 2: list(v2)}

    def GetItemListInTrack(self, track_type, index):
        return list(self._tracks.get(index, []))


def test_a_speed_step_is_applied_and_read_back():
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 50.0, "timeline_start": 0.0,
                        "timeline_end": 2.5},
                       {"percent": 150.0, "timeline_start": 2.5,
                        "timeline_end": 5.0}]}],
        fps=30.0)
    # Both steps address the one item's sub-spans: blading one item
    # into steps is unmeasured, so each step refuses loudly.
    assert report["applied"] == []
    assert len(report["failed"]) == 2
    assert "blade" in report["failed"][0]["fix"]


def test_one_step_per_item_applies():
    from library.tools import native_ops_apply as apply
    first = _FakeItem("seg_a", 0, 75)
    second = _FakeItem("seg_b", 75, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[first, second]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 50.0, "timeline_start": 0.0,
                        "timeline_end": 2.5},
                       {"percent": 150.0, "timeline_start": 2.5,
                        "timeline_end": 5.0}]}],
        fps=30.0)
    assert [r["percent"] for r in report["applied"]] == [50.0, 150.0]
    assert report["failed"] == []
    assert first.writes == [{"Percentage": 50.0, "RippleTimeline": False}]


def test_a_lying_read_back_is_not_claimed():
    from library.tools import native_ops_apply as apply

    class _Liar(_FakeItem):
        def GetSpeed(self):
            return {"Percentage": 100.0}

    item = _Liar("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 40.0, "timeline_start": 0.0,
                        "timeline_end": 5.0}]}],
        fps=30.0)
    assert report["applied"] == []
    assert "re-reads" in report["failed"][0]["what"]


def test_a_freeze_applies_on_a_fresh_item():
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "freeze_frame",
          "timeline_start": 0.0, "timeline_end": 5.0}],
        fps=30.0)
    assert len(report["applied"]) == 1
    assert report["applied"][0]["percent"] == 0.0
    assert report["failed"] == []


def test_a_freeze_on_an_item_that_is_not_at_100_percent_refuses():
    """The measured case: after a retime the write answers True while
    `GetSpeed` re-reads 100.0, so no read-back could judge it. Both ops
    ride one applicator call, as they do in the build. An item already
    slowed refuses the same way."""
    from library.tools import native_ops_apply as apply
    item = _FakeItem("clip_a", 0, 150)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[item]),
        [{"op_id": "speed_001", "effect_type": "speed_ramp",
          "timeline_start": 0.0, "timeline_end": 5.0,
          "segments": [{"percent": 40.0, "timeline_start": 0.0,
                        "timeline_end": 5.0}]},
         {"op_id": "speed_002", "effect_type": "freeze_frame",
          "timeline_start": 0.0, "timeline_end": 5.0}],
        fps=30.0)
    assert len(report["applied"]) == 1
    assert report["applied"][0]["op_id"] == "speed_001"
    assert len(report["failed"]) == 1
    assert report["failed"][0]["op_id"] == "speed_002"
    assert "retime" in report["failed"][0]["what"]

    slowed = _FakeItem("clip_a", 0, 150, speed=40.0)
    report = apply.apply_native_speed_ops(
        _FakeTimeline(v1=[slowed]),
        [{"op_id": "speed_001", "effect_type": "freeze_frame",
          "timeline_start": 0.0, "timeline_end": 5.0}],
        fps=30.0)
    assert report["applied"] == []
    assert "40" in report["failed"][0]["what"]


def test_a_native_transition_is_placed_and_its_span_reads():
    from library.tools import native_ops_apply as apply
    outgoing = _FakeItem("clip_a", 0, 150)
    incoming = _FakeItem("clip_b", 150, 300)
    report = apply.apply_native_transitions(
        _FakeTimeline(), [outgoing, incoming],
        [{"transition_id": "trans_001",
          "resolve_name": "Cross Dissolve", "category": "simple",
          "after_clip": 0, "duration_frames": 12}],
        fps=30.0)
    assert len(report["applied"]) == 1
    row = report["applied"][0]
    assert row["type"] == "Cross Dissolve"
    assert row["verified"].startswith("returned a transition item")
    assert report["failed"] == []


def test_a_transition_that_cannot_be_placed_fails_by_name():
    """An empty AddTransition answer names type and category; a cut past
    the last V1 clip names the missing cut."""
    from library.tools import native_ops_apply as apply

    class _Refusing(_FakeItem):
        def AddTransition(self, payload):
            return None

    report = apply.apply_native_transitions(
        _FakeTimeline(), [_FakeItem("a", 0, 150), _Refusing("b", 150, 300)],
        [{"transition_id": "trans_001", "resolve_name": "Spin",
          "category": "fusion", "after_clip": 0, "duration_frames": 10}],
        fps=30.0)
    assert report["applied"] == []
    assert "Spin" in report["failed"][0]["what"]
    assert "fusion" in report["failed"][0]["what"]

    report = apply.apply_native_transitions(
        _FakeTimeline(), [_FakeItem("a", 0, 150)],
        [{"transition_id": "trans_001", "resolve_name": "Cross Dissolve",
          "category": "simple", "after_clip": 0, "duration_frames": 12}],
        fps=30.0)
    assert report["applied"] == []
    assert "no V1 cut" in report["failed"][0]["what"]


# ── compile_manifest carries native ops to the build ────────────────

def _compile_with(project, plan_transitions=None, plan_vfx=None):
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    if plan_transitions is not None:
        outputs["plan_transitions"] = {"transition_spec": plan_transitions}
    if plan_vfx is not None:
        outputs["plan_vfx"] = plan_vfx
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


@pytest.fixture
def project(tmp_path):
    """Two abutting V1 clips, under tmp_path only."""
    import tests.scenarios.test_compile_manifest_without_the_decoration as base
    from library.tools import music_audit_trail as audit
    from library.tools.project_layout import ProjectLayout

    media = tmp_path / "media"
    media.mkdir()
    names = {}
    for name in ("a_roll.mov", "b_roll.mov", "bed.wav", "whoosh.wav",
                 "sub_seg_000.mov"):
        path = media / name
        path.write_bytes(b"\x00" * 64)
        names[name] = str(path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    layout = ProjectLayout(str(project_dir))
    layout.ensure()
    outputs = base._step_outputs(
        names["a_roll.mov"], names["b_roll.mov"], names["bed.wav"],
        names["whoosh.wav"], names["sub_seg_000.mov"])
    audit.write_audit_trail(
        str(project_dir), outputs["music_selection"]["music_selection"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def test_a_native_transition_reaches_the_build_not_the_comp(project):
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_001", "transition_type": "cross_dissolve",
         "cut_point_timeline": 2.285, "duration": 0.4, "after_clip": 0}])
    (native,) = manifest["native_transitions"]
    assert native["transition_type"] == "cross_dissolve"
    assert native["resolve_name"] == "Cross Dissolve"
    assert native["category"] == "simple"
    assert native["after_clip"] == 0
    assert native["duration_frames"] == 12
    # Nothing drawn reaches the comp engine for it.
    assert manifest["fusion_effects"]["transitions"] == []
    assert manifest["transitions_downgraded"] == []


def test_a_measured_refusal_at_compile_refuses_by_name(project):
    # Matched on the shared `RenRefusal` base: the step imports its
    # tools as `tools.*` (its sys.path shim) while this file reads
    # `library.tools.*`, so the subclass object is doubled - the base
    # and the rendered text are not.
    with pytest.raises(RenRefusal, match="Whip Pan"):
        _compile_with(project, plan_transitions=[
            {"transition_id": "trans_001", "transition_type": "whip_pan",
             "cut_point_timeline": 2.285, "duration": 0.4,
             "after_clip": 0}])


def test_native_speed_ops_reach_the_build_not_the_comp(project):
    manifest = _compile_with(project, plan_vfx={"enhancement_spec": {
        "visual_effects": [
            {"target_block_position": 1, "effect_type": "speed_ramp",
             "timeline_start": 0.0, "timeline_end": 2.285,
             "params": {"segments": [
                 {"percent": 50.0, "timeline_start": 0.0,
                  "timeline_end": 2.285}]},
             "rationale": "ramp in", "route": "native_resolve"},
            {"target_block_position": 2, "effect_type": "freeze_frame",
             "timeline_start": 2.285, "timeline_end": 5.418,
             "params": {}, "rationale": "hold",
             "route": "native_resolve"},
        ],
        "planning_basis": {"basis": "planned", "proposed": 2,
                           "resolved": 2, "dropped": []}}})
    assert manifest["fusion_effects"]["per_clip"] == {} or all(
        v.get("_preset") not in ("speed_ramp", "freeze_frame")
        for v in manifest["fusion_effects"]["per_clip"].values()), \
        "native speed ops must not reach the comp engine"
    (ramp, freeze) = manifest["native_speed_ops"]
    assert ramp["effect_type"] == "speed_ramp"
    assert ramp["segments"][0]["percent"] == 50.0
    assert freeze["effect_type"] == "freeze_frame"


def test_a_native_transition_with_no_hold_ships_a_hard_cut(project):
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_001", "transition_type": "cross_dissolve",
         "cut_point_timeline": 2.285}])
    assert manifest["transitions"][0]["transition_type"] == "hard_cut"
    assert manifest["native_transitions"] == []
    assert len(manifest["transitions_downgraded"]) == 1


# ── The selector's native outcomes ───────────────────────────────────

def test_a_native_request_wins_over_the_heuristic():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="spin")
    assert res["type"] == "spin"


def test_a_brand_that_forbids_a_native_type_gets_a_hard_cut():
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["hard_cut"]}, {},
        requested_type="cross_dissolve")
    assert res["type"] == "hard_cut"
    assert res["downgrade_reason"]
