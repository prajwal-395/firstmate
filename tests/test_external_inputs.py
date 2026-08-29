"""A prerequisite satisfied from outside the pipeline, and CHECKED.

Issue #260, the captain: "given that all necessary prerequisties have
been fulfilled -- like for example you should not be able to add
transitions or effects when there exists no roughcut either already on
the timeline manually or automated by the LLM during the process".

The risk in that sentence is the word "manually".  A flag saying "trust
me, the rough cut exists" would dissolve exactly the contract
enforcement the same sentence asks to keep, so the part worth testing
hardest is the REFUSAL: a claim that is not true has to be refused, by
name, before the run starts.

The other half is that the verified value really reaches the step.  A
resolver that believed something the run then could not use would have
moved the failure rather than removed it, so
`test_the_verified_value_reaches_the_step` drives the real
`gather_step_inputs`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import external_inputs, run_scope
from library.tools.external_inputs import ExternalStateError
from library.tools.project_layout import Area, ProjectLayout


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _supply(project, key, value, source="cut by hand, 2026-08-28"):
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    path = directory / f"{key}.json"
    path.write_text(json.dumps({"key": key, "source": source,
                                "value": value}), encoding="utf-8")
    return path


def _clip(tmp_path, name="hand_cut.mov", size=64):
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    path = media / name
    path.write_bytes(b"\x00" * size)
    return str(path)


def _a_roll(clip):
    return [
        {"clip_id": "clip_1", "source_clip_id": "clip_1",
         "source_file": clip, "video_in": 0.132, "video_out": 2.417,
         "timeline_start": 0.0, "timeline_end": 2.285},
        {"clip_id": "clip_1", "source_clip_id": "clip_1",
         "source_file": clip, "video_in": 4.083, "video_out": 7.216,
         "timeline_start": 2.285, "timeline_end": 5.418},
    ]


# ── The check accepts a claim that is true ──────────────────────────

def test_a_true_claim_is_accepted_and_says_what_it_checked(tmp_path):
    project = _project(tmp_path)
    clip = _clip(tmp_path)
    _supply(project, "a_roll_assignments", _a_roll(clip))

    supplied = external_inputs.load(str(project))
    assert set(supplied) == {"a_roll_assignments"}
    entry = supplied["a_roll_assignments"]
    assert entry.value[0]["source_file"] == clip
    assert "2 assignments" in entry.checked
    assert "present on disk" in entry.checked
    assert entry.source == "cut by hand, 2026-08-28"


def test_a_project_with_no_external_directory_supplies_nothing(tmp_path):
    assert external_inputs.load(str(_project(tmp_path))) == {}


def test_the_run_reports_what_it_did_not_produce(tmp_path):
    project = _project(tmp_path)
    _supply(project, "a_roll_assignments", _a_roll(_clip(tmp_path)))
    lines = external_inputs.describe(external_inputs.load(str(project)))
    assert any("Supplied from outside" in line for line in lines)
    assert any("source (recorded, not checked)" in line for line in lines)


# ── The check refuses claims that are not true ──────────────────────

def test_a_claim_naming_a_file_that_is_not_there_is_refused(tmp_path):
    project = _project(tmp_path)
    assignments = _a_roll(_clip(tmp_path))
    assignments[1]["source_file"] = str(tmp_path / "media" / "never_shot.mov")
    _supply(project, "a_roll_assignments", assignments)

    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "never_shot.mov" in str(exc.value)
    assert "not a file" in str(exc.value)


def test_a_claim_that_overruns_the_measured_clip_is_refused(tmp_path):
    """The strongest check available without opening the media: the
    pipeline already measured this clip, and the supplied cut plays past
    the end of it."""
    project = _project(tmp_path)
    clip = _clip(tmp_path)
    assignments = _a_roll(clip)
    assignments[1]["video_out"] = 41.5
    _supply(project, "a_roll_assignments", assignments)
    state = {"step_outputs": {"catalog": {"clip_catalog": [
        {"clip_id": "clip_1", "path": clip, "duration_seconds": 20.0}]}}}

    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project), state)
    assert "41.5" in str(exc.value)
    assert "20.0" in str(exc.value)


def test_a_backwards_range_is_refused(tmp_path):
    project = _project(tmp_path)
    assignments = _a_roll(_clip(tmp_path))
    assignments[0]["video_out"] = 0.1
    _supply(project, "a_roll_assignments", assignments)
    with pytest.raises(ExternalStateError, match="not a range"):
        external_inputs.load(str(project))


def test_a_relative_media_path_is_refused(tmp_path):
    project = _project(tmp_path)
    assignments = _a_roll(_clip(tmp_path))
    assignments[0]["source_file"] = "media/hand_cut.mov"
    _supply(project, "a_roll_assignments", assignments)
    with pytest.raises(ExternalStateError, match="relative path"):
        external_inputs.load(str(project))


def test_an_empty_value_is_refused(tmp_path):
    """"Trust me, it exists" with nothing in it is the exact claim this
    module exists to refuse."""
    project = _project(tmp_path)
    _supply(project, "a_roll_assignments", [])
    with pytest.raises(ExternalStateError, match="empty list"):
        external_inputs.load(str(project))


def test_a_value_with_no_source_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "a_roll_assignments", _a_roll(_clip(tmp_path)),
            source="   ")
    with pytest.raises(ExternalStateError, match="no 'source'"):
        external_inputs.load(str(project))


def test_a_file_whose_name_and_key_disagree_is_refused(tmp_path):
    project = _project(tmp_path)
    directory = project / "external"
    directory.mkdir()
    (directory / "a_roll_assignments.json").write_text(
        json.dumps({"key": "audio_spine", "source": "x", "value": {"a": 1}}),
        encoding="utf-8")
    with pytest.raises(ExternalStateError, match="file name IS the"):
        external_inputs.load(str(project))


def test_broken_json_is_refused_rather_than_ignored(tmp_path):
    project = _project(tmp_path)
    directory = project / "external"
    directory.mkdir()
    (directory / "a_roll_assignments.json").write_text("{ nope",
                                                       encoding="utf-8")
    with pytest.raises(ExternalStateError, match="not valid JSON"):
        external_inputs.load(str(project))


# ── What cannot be asserted is refused by name ──────────────────────

def test_a_key_with_no_check_cannot_be_supplied(tmp_path):
    """The honest outcome for state nothing can verify: it is refused,
    not taken on faith."""
    project = _project(tmp_path)
    _supply(project, "creative_direction", {"target_mood": "warm"})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    message = str(exc.value)
    assert "cannot be supplied from outside" in message
    assert "not a check that passes" in message
    assert "a_roll_assignments" in message, (
        "the refusal must say what CAN be supplied")


def test_every_withdrawn_entry_says_why(tmp_path):
    assert external_inputs.WITHDRAWN
    for claim, reason in external_inputs.WITHDRAWN.items():
        assert len(reason.split()) >= 15, f"{claim} carries no real reason"


def test_the_hand_built_timeline_is_recorded_as_unassertable():
    """The captain's own example. A Resolve timeline is not refusable at
    resolve time, so the module says so and names the artifacts that
    are - rather than adding a flag that believes it."""
    reason = external_inputs.WITHDRAWN["a Resolve timeline built by hand"]
    assert "audio_spine" in reason and "assembly_manifest" in reason
    assert "a Resolve timeline built by hand" not in external_inputs.CHECKS


# ── The spine and the manifest are checked with the repo's own contracts

def test_a_spine_that_fails_the_spine_contract_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "audio_spine", {"structure": [
        {"block_type": "speech", "position": 1, "timeline_start": 0.0,
         "timeline_end": 2.0}]})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "spine contract" in str(exc.value)


def test_a_spine_that_passes_the_contract_is_accepted(tmp_path):
    project = _project(tmp_path)
    _supply(project, "audio_spine", {"structure": [
        {"block_type": "speech", "position": 1, "clip_id": "clip_1",
         "source_start": 0.132, "source_end": 2.417,
         "timeline_start": 0.0, "timeline_end": 2.285,
         "alignment_method": "whisperx",
         "word_timestamps": [{"word": "hello", "source_start": 0.132,
                              "source_end": 0.5}],
         "content": {"clip_id": "clip_1"}}], "frame_rate": 30.0})
    supplied = external_inputs.load(str(project))
    assert "validate_spine_blocks" in supplied["audio_spine"].checked


def test_a_manifest_that_fails_the_validator_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "assembly_manifest", {"tracks": {"V1": {"clips": []}}})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "assembly_manifest" in str(exc.value)


def test_a_render_that_is_not_a_video_is_refused(tmp_path):
    project = _project(tmp_path)
    fake = tmp_path / "media" / "not_a_render.mov"
    fake.parent.mkdir(exist_ok=True)
    fake.write_bytes(b"\x00" * 200_000)
    _supply(project, "render_output", {"output_path": str(fake)})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "not_a_render.mov" in str(exc.value)


def test_a_render_that_is_too_small_is_refused_before_ffprobe(tmp_path):
    project = _project(tmp_path)
    tiny = tmp_path / "media" / "tiny.mov"
    tiny.parent.mkdir(exist_ok=True)
    tiny.write_bytes(b"\x00" * 128)
    _supply(project, "render_output", {"output_path": str(tiny)})
    with pytest.raises(ExternalStateError, match="not that small"):
        external_inputs.load(str(project))


# ── The layout owns where it goes ───────────────────────────────────

def test_the_external_area_is_an_input_and_no_step_may_write_it(tmp_path):
    from library.tools.project_layout import ProjectLayoutViolation

    layout = ProjectLayout(str(_project(tmp_path)))
    with pytest.raises(ProjectLayoutViolation):
        layout.write_dir(Area.EXTERNAL_STATE)
    assert layout.read_dir(Area.EXTERNAL_STATE).name == "external"


def test_the_scaffold_does_not_create_it(tmp_path):
    """An input area exists because the captain made it."""
    project = _project(tmp_path)
    assert not (project / "external").exists()


# ── The resolver counts it, and the step receives it ────────────────

def test_a_supplied_key_satisfies_a_prerequisite(tmp_path):
    """`--only render` on a project with no recorded run: the manifest
    the captain supplied is what makes it resolvable."""
    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)

    with pytest.raises(run_scope.ScopeError) as exc:
        run_scope.resolve(run_scope.Selection(only=("render",),
                                              skip=("compile_manifest",)),
                          dag=dag, manifests=manifests, external={})
    assert "assembly_manifest" in str(exc.value)
    assert "<project>/external/" in str(exc.value), (
        "the refusal should name the route out that exists")

    scope = run_scope.resolve(
        run_scope.Selection(only=("render",)), dag=dag, manifests=manifests,
        external={"assembly_manifest": {"tracks": {}}})
    assert scope.steps_to_run == ("render",), (
        "a supplied manifest means the pipeline does not compile one")
    assert scope.from_external["assembly_manifest"] == ("render",)


def test_a_recorded_output_does_not_collapse_the_run_the_way_a_supply_does(
        tmp_path):
    """History is not a request. A previous run's manifest keeps the
    steps in the run; supplying one says "do not make this"."""
    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    state = {"edit_completed": {"compile_manifest": {"at": "now"}},
             "step_outputs": {"compile_manifest": {"assembly_manifest": {}}}}
    scope = run_scope.resolve(run_scope.Selection(only=("render",)), dag=dag,
                              manifests=manifests, state=state, external={})
    assert "compile_manifest" in scope.steps_to_run


def test_the_verified_value_reaches_the_step(tmp_path):
    """The half that makes this not a lie: the resolver counted the
    supplied manifest, and `gather_step_inputs` hands the render step
    that same manifest."""
    from library.processes.edit_video.run_pipeline import gather_step_inputs

    project = _project(tmp_path)
    manifest = {
        "project": {"name": "hand cut", "resolution": [1080, 1920],
                    "frame_rate": 30.0, "duration_seconds": 2.285},
        "tracks": {"V1": {"clips": [
            {"label": "speech_1", "source_file": _clip(tmp_path),
             "source_in": 0.132, "source_out": 2.417,
             "timeline_in": 0.0, "timeline_out": 2.285}]}},
        "subtitles": [],
    }
    _supply(project, "assembly_manifest", manifest,
            source="assembled by hand in Resolve and exported")

    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    state = {"project_folder": str(project), "step_outputs": {}}
    supplied = external_inputs.load(str(project), state)

    inputs = gather_step_inputs("render", dag, state,
                                manifest=manifests["render"],
                                external=supplied)
    assert inputs["assembly_manifest"] == manifest


def test_without_the_supply_the_same_call_raises(tmp_path):
    from library.processes.edit_video.run_pipeline import gather_step_inputs

    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    with pytest.raises(RuntimeError, match="assembly_manifest"):
        gather_step_inputs("render", dag, {"step_outputs": {}},
                           manifest=manifests["render"], external={})


def test_a_real_upstream_output_outranks_a_supply(tmp_path):
    """A step that really ran wins: supplying state is for the case
    where nothing produced it, not a way to override a run."""
    from library.processes.edit_video.run_pipeline import gather_step_inputs

    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    state = {"step_outputs": {"compile_manifest": {
        "assembly_manifest": {"from": "the run"}}}}
    entry = external_inputs.Supplied(
        key="assembly_manifest", value={"from": "outside"}, source="x",
        path=Path("x"), checked="x")
    inputs = gather_step_inputs("render", dag, state,
                                manifest=manifests["render"],
                                external={"assembly_manifest": entry})
    assert inputs["assembly_manifest"] == {"from": "the run"}


def test_the_consumer_side_name_is_refused_with_the_state_name(tmp_path):
    """`validate` declares `rendered_output`; step 6.01 records
    `render_output`. A file supplies STATE, so the refusal has to name
    the producer's key rather than leave the captain guessing which of
    the two spellings the pipeline meant."""
    project = _project(tmp_path)
    _supply(project, "rendered_output", {"output_path": "/nowhere"})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    message = str(exc.value)
    assert "'render_output'" in message
    assert "render_output.json" in message


def test_a_whole_pipeline_collapses_to_the_step_that_still_has_work(
        tmp_path):
    """The captain's case end to end: the cut and the master both exist
    because they were made elsewhere, so the only thing left to do is
    judge the master.

    Twenty-five steps become one, and nothing was taken on faith - the
    manifest passed the validator step 5.04 runs on its own output and
    the master was decoded by ffprobe.
    """
    project = _project(tmp_path)
    master = tmp_path / "media" / "master.mov"
    master.parent.mkdir(exist_ok=True)
    _render_a_real_video(master)
    clip = str(master)
    _supply(project, "assembly_manifest", {
        "project": {"name": "hand cut", "resolution": [1080, 1920],
                    "frame_rate": 30.0, "duration_seconds": 5.285},
        "tracks": {"V1": {"clips": [
            {"label": "speech_1", "source_file": clip,
             "source_in": 0.132, "source_out": 5.417,
             "timeline_in": 0.0, "timeline_out": 5.285}]}},
        "subtitles": [],
    }, source="assembled by hand in Resolve and exported")
    _supply(project, "render_output", {"output_path": clip},
            source="rendered by hand out of Resolve")

    scope = run_scope.resolve(run_scope.Selection(only=("validate",)),
                              project_folder=str(project))
    assert scope.steps_to_run == ("validate",)
    assert set(scope.from_external) == {"assembly_manifest", "render_output"}
    assert len(scope.skipped) == 24


def _render_a_real_video(path):
    """A file ffprobe can actually decode. Skips rather than faking one:
    the whole point of the render check is that it is not a shape
    check."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not on this machine")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1080x1920:rate=30:duration=12",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True, timeout=120)
    assert path.stat().st_size > 100_000


def test_the_run_record_says_the_state_came_from_outside():
    """`pipeline_run.json` is the runner's own account of itself. A run
    that did one step of twenty-six because the captain supplied the
    rest has to say so there, or the record is unreadable."""
    from library.tools import run_control

    scope = SimpleNamespace(
        is_scoped=True,
        steps_to_run=("validate",),
        universe=tuple(str(i) for i in range(26)),
        selection=SimpleNamespace(target=None),
        from_external={"assembly_manifest": ("validate",),
                       "render_output": ("validate",)})

    line = run_control.describe_mode(scope=scope)
    assert "state supplied from outside: assembly_manifest, render_output" \
        in line


def test_an_ordinary_run_says_nothing_about_supplied_state():
    from library.tools import run_control

    assert "supplied from outside" not in run_control.describe_mode()
