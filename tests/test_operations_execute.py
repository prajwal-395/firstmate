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


# ── requires is DERIVED ─────────────────────────────────────────────


def test_no_operation_hand_writes_a_requirement():
    """`requires` comes from requirements.py, keyed by its legacy node.

    A hand-written list here would be a second requirement vocabulary
    beside the one that exists, and the hand-written half would be prose
    in a costume - the defect this refactor removes.
    """
    import inspect
    source = inspect.getsource(operations)
    body = source[source.index("_REGISTRY"):source.index("def all(")]
    assert "requires=" not in body, (
        "the registry declares a requirement literal; requires is derived")




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


def test_a_refusal_names_the_input_and_what_produces_it(empty_project):
    result = operations.get("subtitles.plan").execute(empty_project)
    assert result.refused
    assert "audio_spine" in result.error
    assert "mesh_spine" in result.error


def test_a_refusal_says_running_alone_is_why(empty_project):
    """The refusal is SURPRISING - the same state runs fine in a DAG - so
    it must say why, and name the two ways out."""
    result = operations.get("subtitles.plan").execute(empty_project)
    assert "runs ALONE" in result.error
    assert "run the DAG" in result.error


def test_the_refusal_never_names_an_operation_it_cannot_prove_produces_it():
    """`duration_zone.build` is owned by `mesh_spine` but is its BRIDGE
    half and emits no spine. Suggesting it as the producer of audio_spine
    would be confidently wrong, which is worse than suggesting nothing."""
    import inspect
    teach = inspect.getsource(operations.Operation._teach)
    assert "op.name for op in all()" not in teach, (
        "the refusal is guessing which operation produces a key")


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








CALLER_DECIDED_POST_BRIDGE_ARGS: dict[str, tuple[str, ...]] = {
    # `speech.enrich` registers its post_bridge file's inner
    # deterministic unit, not the stdin-driven entry point, so
    # `temporal_index_dir` binds from nothing the DAG routes: the
    # gathered inputs carry `temporal_index` (the manifest's declared
    # input) and `main()` derives the directory from
    # `temporal_index.index_dir` itself
    # (`library/steps/step_2_02_speech_sequence/post_bridge.py:main`).
    # The registry can only ever receive the directory from the
    # caller via `--set`, and the test below proves the refusal
    # teaches exactly that instead of binding nothing. Do NOT add
    # the name to `MERGED_INPUT_PARAMETERS` instead: that would bind
    # the whole gathered dict as the directory - the confidently
    # wrong result the enumeration exists to stop.
    "speech.enrich": ("temporal_index_dir",),
    # `music.resolve` registers its post_bridge file's inner
    # deterministic unit, not the stdin-driven entry point, so its
    # three destructured parameters bind from nothing the DAG routes
    # under those names: `selection` is the model's answer
    # (`music_selection` in the merged dict, refused by
    # `missing_model_answer` until supplied), and `candidates` /
    # `target_duration` are the pre-bridge's catalogue output, which
    # `main()` reads off `music_candidates` itself
    # (`library/steps/step_2_04_music_selection/post_bridge.py:main`).
    # The registry can only ever receive all three from the caller via
    # `--set`, and the test below proves the refusal teaches exactly
    # that instead of binding nothing.
    "music.resolve": ("selection", "candidates", "target_duration"),
    # `broll.resolve` registers its post_bridge file's inner
    # deterministic unit, so its destructured parameters bind from
    # nothing the DAG routes under those names: `broll_creative` is the
    # model's answer (refused by `missing_model_answer` until
    # supplied), `broll_interjections` / `semantic_docs` /
    # `temporal_indices` are the model's second answer and two renamed
    # gathered keys, which `main()` reads as `b_roll_interjections` /
    # `semantic_analysis_documents` / `temporal_event_indices` itself,
    # and `target_resolution` is derived by `main()` from the delivery
    # format (`library/steps/step_3_02_select_broll/post_bridge.py:main`).
    # The registry can only ever receive them from the caller via
    # `--set`. Do NOT add any of these names to
    # `MERGED_INPUT_PARAMETERS`: that would bind the whole gathered
    # dict as a cutaway list - the confidently wrong result the
    # enumeration exists to stop.
    "broll.resolve": ("broll_creative", "broll_interjections",
                      "semantic_docs", "temporal_indices",
                      "target_resolution"),
    # `transitions.resolve`, `vfx.resolve` and `sfx.resolve` register
    # their post_bridge files' inner deterministic units, not the
    # stdin-driven entry points, so `creative_plan` - the model's plan,
    # which each `main()` reads off the merged dict under its own
    # llm key (`transition_creative`, `vfx_creative`, `sfx_creative`)
    # and passes positionally - binds from nothing the DAG routes.
    # The registry can only ever receive the plan from the caller via
    # `--set`, and the test below proves the refusal teaches exactly
    # that instead of binding nothing. Do NOT add the name to
    # `MERGED_INPUT_PARAMETERS` instead: that would bind the whole
    # gathered dict as the plan - the confidently wrong result the
    # enumeration exists to stop.
    "transitions.resolve": ("creative_plan",),
    "vfx.resolve": ("creative_plan",),
    "sfx.resolve": ("creative_plan",),
}


def test_destructured_post_bridge_unit_refuses_naming_its_args(tmp_path):
    """The classified half of the check above, proved by running it.

    With its requirements satisfied and the model's answer supplied
    as overrides, `speech.enrich` reaches the argument binding - and
    `temporal_index_dir` is not there to bind. It must REFUSE naming
    the argument and the `--set` way to supply it: a `TypeError`
    here would be the defect the static check exists to stop,
    wearing a new shape.
    """
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "creative_direction": {
                "creative_direction": {"mood": "measured"}},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "temporal_index": {"temporal_event_indices": [],
                               "index_dir": str(tmp_path)},
        }}), encoding="utf-8")

    result = operations.get("speech.enrich").execute(
        str(tmp_path),
        speech_sequence={"body_sequence": []},
        topics_toon="", transcripts_toon="")

    assert result.refused, (
        "speech.enrich called its body with no temporal_index_dir "
        "rather than refusing")
    assert "temporal_index_dir" in result.error, (
        "the refusal does not name the caller-decided argument")
    assert "--set temporal_index_dir" in result.error, (
        "the refusal does not teach the way to supply it")


def test_intake_post_bridge_units_refuse_naming_their_args(tmp_path):
    """The classified half of the check above, for the intake lane's two
    post-bridge units - proved by running them, not trusted.

    With their requirements satisfied and the model's answer supplied
    as overrides, `music.resolve` and `broll.resolve` reach the
    argument binding - and the pre-bridge outputs, renamed gathered
    keys and derived frame main() reads off the merged dict are not
    there to bind. Each must REFUSE naming its arguments and the
    `--set` way to supply them: a `TypeError` here would be the
    defect the static check exists to stop, wearing a new shape.
    """
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "creative_direction": {
                "creative_direction": {"mood": "measured"}},
            "catalog": {"clip_catalog": []},
            "assign_aroll": {"a_roll_assignments": {}},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "temporal_index": {"temporal_event_indices": []},
            "mesh_spine": {"timed_spine": {"structure": []}},
        }}), encoding="utf-8")

    music = operations.get("music.resolve").execute(
        str(tmp_path),
        music_selection={"title": "t", "source": "library"})
    assert music.refused, (
        "music.resolve called its body with no selection, candidates "
        "or target_duration rather than refusing")
    for name in ("selection", "candidates", "target_duration"):
        assert name in music.error, (
            f"the refusal does not name the caller-decided {name}")
    assert "--set selection" in music.error, (
        "the refusal does not teach the way to supply it")

    broll = operations.get("broll.resolve").execute(
        str(tmp_path),
        broll_creative=[],
        b_roll_interjections=[])
    assert broll.refused, (
        "broll.resolve called its body with no placements rather than "
        "refusing")
    for name in ("broll_interjections", "semantic_docs",
                 "temporal_indices", "target_resolution"):
        assert name in broll.error, (
            f"the refusal does not name the caller-decided {name}")
    assert "--set broll_interjections" in broll.error, (
        "the refusal does not teach the way to supply it")


@pytest.mark.parametrize("operation,answer_key", [
    ("transitions.resolve", "transition_creative"),
])
def test_planner_post_bridge_units_refuse_naming_the_plan(
        tmp_path, operation, answer_key):
    """The classified half of the check above, proved by running it,
    for the three planner lanes.

    With their requirements satisfied and the model's answer supplied
    as overrides, `transitions.resolve`, `vfx.resolve` and
    `sfx.resolve` reach the argument binding - and `creative_plan`
    is not there to bind. Each must REFUSE naming the plan and the
    `--set` way to supply it: a `TypeError` here would be the defect
    the static check exists to stop, wearing a new shape.
    """
    spine = {"structure": []}
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "assign_aroll": {"a_roll_assignments": []},
            "catalog": {"clip_catalog": [], "project_fps": 30.0},
            "creative_direction": {
                "creative_direction": {"mood": "measured"}},
            "mesh_spine": {"audio_spine": spine, "timed_spine": spine},
            "review_rough_cut": {"rough_cut_review": {"passed": True}},
            "temporal_index": {"temporal_event_indices": [],
                               "index_dir": str(tmp_path)},
            "music_selection": {"music_selection": {"track": "t"}},
            "music_analysis": {"music_analysis": {}},
            "select_broll": {"b_roll_assignments": []},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "plan_transitions": {"transition_spec": []},
        }}), encoding="utf-8")

    result = operations.get(operation).execute(
        str(tmp_path), **{answer_key: []})

    assert result.refused, (
        f"{operation} called its body with no creative_plan "
        f"rather than refusing")
    assert "creative_plan" in result.error, (
        "the refusal does not name the caller-decided argument")
    assert "--set creative_plan" in result.error, (
        "the refusal does not teach the way to supply it")




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






def test_a_prompt_capability_refuses_naming_the_runner(tmp_path):
    """A capability that IS a prompt has no function to call: the runner
    asks a model to answer it, and an operation runs alone with no
    model. With its contract satisfied it must REFUSE naming the prompt
    and the way out - a `TypeError` here would be the crash the
    unbindable-argument refusal exists to stop, wearing a new shape."""
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {
            "catalog": {"clip_catalog": []},
            "semantic_analysis": {"semantic_analysis_documents": []},
            "temporal_index": {"temporal_event_indices": []},
        }}), encoding="utf-8")

    op = operations.get("creative.direct")
    assert op.unmet(str(tmp_path)) == []
    result = op.execute(str(tmp_path))

    assert result.refused
    assert "handoff.md" in result.error
    assert "creative_direction" in result.error
    assert "run the DAG" in result.error


def test_a_prompt_capability_still_refuses_its_missing_inputs_first(
        empty_project):
    """The contract bites before the prompt does. A prompt capability
    with unsatisfied requirements refuses naming what is missing and
    who produces it - the runner refusal above must not swallow the
    precondition refusal."""
    result = operations.get("creative.direct").execute(empty_project)

    assert result.refused
    assert "clip_catalog" in result.error
    assert "catalog" in result.error


def _any_step_declares(name: str) -> bool:
    """Is `name` an input some step's manifest declares, in any process?"""
    import json as _json
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1] / "library" / "steps"
    for manifest_path in root.glob("*/manifest.json"):
        interface = (_json.loads(manifest_path.read_text(encoding="utf-8"))
                     .get("interface") or {})
        for declared in interface.get("inputs") or []:
            if declared.get("name") == name:
                return True
    return name in ("project_folder", "project_config")


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


def test_an_operation_at_a_region_plans_only_that_region(three_block_project):
    """The gate FIRES on the defect: a region-scoped plan must not be a
    whole-project plan. Measured before the fix - PROJECT 4 cards, REGION
    4 cards, the step called directly with the same scope 1 card."""
    project, spine = three_block_project
    plan = operations.get("subtitles.plan")

    whole = plan.execute(project)
    part = plan.execute(project, scope=_block_one(spine))

    def cards(result):
        return result.payload["subtitle_plan"]["subtitle_entries"]

    assert whole.completed and part.completed
    assert len(cards(part)) < len(cards(whole)), (
        "the region planned as much as the whole project, so the address "
        "did not reach the step")


def test_the_region_the_registry_passes_is_the_one_the_step_would_get(
        three_block_project):
    """Not just fewer - THE SAME. The step is the authority on what a
    region means, so the registry's answer has to equal the answer the
    step gives when it is handed the scope directly."""
    project, spine = three_block_project
    plan = operations.get("subtitles.plan")
    where = _block_one(spine)

    through_registry = plan.execute(project, scope=where)
    direct = plan.run(audio_spine=spine, scope=where)

    assert (through_registry.payload["subtitle_plan"]["subtitle_entries"]
            == direct["subtitle_plan"]["subtitle_entries"])






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


def test_supplying_the_argument_the_refusal_named_makes_it_run(
        three_block_project):
    """The mirror, and the half that makes the refusal a control surface
    rather than a wall: doing what it says works."""
    project, spine = three_block_project
    where = _block_one(spine)
    stored = operations.get("subtitles.plan").execute(project).payload

    result = operations.get("subtitles.splice").execute(
        project, scope=where, stored_plan=stored["subtitle_plan"])

    assert result.completed, result.error
    assert result.payload["splice"]






# ── --set: the way out the refusal names ─────────────────────────────




def test_set_refuses_a_bare_word_rather_than_guessing_it_is_a_string():
    """A value here is a structure. Falling back to "it must be a string"
    would hand a step the text `[1, 2]` and let it fail further in."""
    with pytest.raises(operations.OperationError) as exc:
        operations.parse_overrides(["stored_plan=not json"])
    assert "not valid JSON" in str(exc.value)




