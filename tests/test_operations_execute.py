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
