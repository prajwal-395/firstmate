"""An operation EXECUTES, or refuses saying what is missing and who makes it.

Before this, every operation resolved its entry point and stopped:

    REFUSED: an operation cannot gather its own inputs yet

so the operation surface was a resolver rather than an executor, and the
LLM-native claim was half true.

The test that matters most here is
`test_the_same_requirement_defers_in_a_run_and_refuses_for_an_operation`.
It pins the DISTINCTION rather than the two outcomes: one requirement,
one state, two run sets, opposite verdicts. Two tests asserting each
outcome alone would both keep passing if the distinction collapsed.
"""
import json

import pytest

from library.tools import operations, requirements


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


