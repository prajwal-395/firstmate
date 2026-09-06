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
    """`requires` comes from requirements.py, keyed by owning_node.

    A hand-written list here would be a second requirement vocabulary
    beside the one that exists, and the hand-written half would be prose
    in a costume - the defect this refactor removes.
    """
    import inspect
    source = inspect.getsource(operations)
    body = source[source.index("_REGISTRY"):source.index("def all(")]
    assert "requires=" not in body, (
        "the registry declares a requirement literal; requires is derived")


@pytest.mark.parametrize("op", operations.all(), ids=lambda o: o.name)
def test_every_operation_derives_its_steps_requirements(op):
    derived = {r.name for r in op.requires}
    expected = {r.name for r in requirements.all_requirements()
                if op.owning_node in r.consumers}
    assert derived == expected, (
        f"{op.name} does not ask exactly what {op.owning_node} asks")


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


def test_gathering_calls_the_runners_own_assembler():
    """`gather_step_inputs` is called, never copied. A second
    implementation would drift within a month, invisibly, because both
    would 'work'."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(operations.Operation.gather).strip())
    called = {ast.unparse(n.func) for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    assert any(c.endswith("gather_step_inputs") for c in called), (
        f"gather does not call the runner's assembler; it calls {called}")


def test_only_the_arguments_the_step_names_are_passed(satisfied_project):
    """The gathered dict is the STEP's whole input; a function taking four
    keys must not be handed forty."""
    plan = operations.get("subtitles.plan")
    gathered = plan.gather(satisfied_project)
    passed = plan._arguments(gathered)
    import inspect
    names = set(inspect.signature(plan.run).parameters)
    assert set(passed) <= names
    assert "audio_spine" in passed


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


def test_the_merged_dict_is_the_same_dict_the_runner_would_write(tmp_path):
    """Not a subset and not a re-derivation: `gather` IS the argument.

    A binding that passed some smaller dict would look identical on a
    fixture and diverge on a real project, which is the failure mode a
    "does it crash" test would miss.
    """
    project = _project_with_mesh_spine_inputs(tmp_path)
    op = operations.get("duration_zone.build")
    gathered = op.gather(project)
    assert op._arguments(gathered) == {"data": gathered}


def test_a_real_gathered_key_still_wins_over_the_merged_dict():
    """The merged-dict fill only ever reaches a parameter nothing else
    could satisfy. If a step ever declares an input literally called
    `data`, that value must be bound, not the whole dict."""
    op = operations.get("duration_zone.build")
    assert op._arguments({"data": {"real": True}}) == {"data": {"real": True}}


def test_no_operation_takes_a_merged_dict_under_a_name_this_module_does_not_know():
    """The enumeration is asserted, never assumed.

    `MERGED_INPUT_PARAMETERS` is three MEASURED spellings, and a fourth
    appearing without being added to it would be an operation that binds
    `{}` and raises `TypeError` again - the exact defect this fixed. So
    this measures the tree rather than trusting the list, the same shape
    as `test_operations.py::test_the_transcript_is_the_only_required_
    input_no_edge_carries`.

    Two shapes are checked, because there are two ways a body ends up
    holding the whole dict:

    * a BRIDGE or POST-BRIDGE, which the runner feeds by writing the
      merged dict to its stdin - every required parameter of one of
      those is the whole dict by construction;
    * a `step.py` whose ONE required parameter is not an input any
      manifest declares, which is `validate_sfx_library(inputs)` and
      both reel nodes.

    Deliberately NOT checked: a body with several required parameters
    none of which is a declared input. Those are the region-scoped
    operations - `transcript.splice(index_doc, fresh_regions, ...)` -
    whose arguments come from a CALLER rather than from gathering, and
    calling them unbindable would be a finding about a different thing.
    """
    import inspect

    unexplained = []
    for op in operations.all():
        parameters = inspect.signature(op.run).parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in parameters.values()):
            continue
        required = [name for name, p in parameters.items()
                    if p.default is inspect.Parameter.empty
                    and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                   inspect.Parameter.KEYWORD_ONLY)]

        if op.body in ("bridge.py", operations.POST_BRIDGE):
            suspects = required
        elif len(required) == 1 and not _any_step_declares(required[0]):
            suspects = required
        else:
            continue

        for name in suspects:
            if name not in operations.MERGED_INPUT_PARAMETERS:
                unexplained.append(f"{op.name} ({op.body}): {name}")

    assert unexplained == [], (
        "these operations take the whole input dict under a name "
        "operations.MERGED_INPUT_PARAMETERS does not know, so they would "
        "bind nothing and raise TypeError. Add the spelling there "
        f"deliberately: {unexplained}")


def test_the_enumeration_names_only_spellings_that_are_really_used():
    """The mirror. A spelling nobody uses is a name this module would
    bind the whole dict to on the day some step happens to pick it for
    something else - which is the confidently-wrong result the finding
    said was worse than a TypeError."""
    import inspect

    used = set()
    for op in operations.all():
        used |= set(inspect.signature(op.run).parameters)
    stale = [n for n in operations.MERGED_INPUT_PARAMETERS if n not in used]
    assert stale == [], (
        f"operations.MERGED_INPUT_PARAMETERS names {stale}, which no "
        f"registered body takes. Remove it: an unused spelling is a trap "
        f"for whichever step picks that word next.")


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


def test_the_post_bridge_guard_reads_the_runners_own_declaration():
    """One rule, two readers. `present_llm_step` asks the model for these
    keys; the guard refuses when they are absent. Two spellings of the
    rule would let those disagree about what the model owes."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(
        operations.Operation.missing_model_answer).strip())
    called = {ast.unparse(n.func) for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    assert any(c.endswith("llm_output_declarations") for c in called), (
        f"the guard derives the model's contribution itself; it calls "
        f"{called}")


def test_a_pre_bridge_is_not_guarded(tmp_path):
    """The distinction, from the other side. `duration_zone.build` is a
    pre-bridge, so nothing is owed and nothing is refused - asserted here
    so the guard cannot quietly widen to every merged-dict body."""
    op = operations.get("duration_zone.build")
    assert op.missing_model_answer({"anything": 1}) == ()


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
