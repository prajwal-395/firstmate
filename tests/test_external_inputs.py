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


def test_the_CLOSED_timeline_is_recorded_as_unassertable():
    """NARROWED 2026-09-04, and the narrowing is the point.

    This entry used to rule out any hand-built timeline. It conflated
    two things: a CLOSED project really is a database that has to be
    copied before it is opened and really does lack a typed mapping,
    while a LIVE one answers every field the checks demand through its
    scripting API. The closed half stays withdrawn and still names the
    artifacts to supply instead."""
    reason = external_inputs.WITHDRAWN["a Resolve timeline in a CLOSED project"]
    assert "audio_spine" in reason and "assembly_manifest" in reason
    assert "SQLite" in reason
    assert "a Resolve timeline in a CLOSED project" not in external_inputs.CHECKS
    # The old, wider claim is gone rather than sitting alongside it.
    assert "a Resolve timeline built by hand" not in external_inputs.WITHDRAWN


def test_the_narrowed_withdrawal_names_the_live_producer():
    """A narrowing that did not say what now handles the other half
    would read as an unexplained loosening of the contract."""
    reason = external_inputs.WITHDRAWN["a Resolve timeline in a CLOSED project"]
    assert "timeline_ingest" in reason
    assert "LIVE" in reason


def test_speech_sequence_left_the_taste_withdrawal_and_is_checkable():
    """The captain ruled that a sequence they cut by hand is a fact to
    be read, not taste to be invented. Taste is still withdrawn."""
    assert "speech_sequence" in external_inputs.CHECKS
    taste = external_inputs.WITHDRAWN[
        "creative_direction / the planning outputs, as TASTE"]
    assert "speech_sequence" in taste, "the narrowing must say what left"
    assert "MODEL proposes is still taste" in taste


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

    Two of the twenty-five are SUPPLIED rather than merely unneeded, and
    the distinction is the point: `compile_manifest` and `render` are the
    steps that would have MADE the two values, so they leave the universe
    entirely rather than sitting in `skipped` as work this run declined.
    A step that correctly did not run must not hold the run at PARTIAL.
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
    assert scope.supplied == {"compile_manifest": ("assembly_manifest",),
                              "render": ("render_output",)}
    assert "compile_manifest" not in scope.universe
    assert "render" not in scope.universe
    assert len(scope.skipped) + len(scope.supplied) == 25


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


# ── speech_sequence: a MEASUREMENT is accepted, an invention is not ──
#
# The captain ruled (2026-09-04) that a sequence they cut by hand is a
# fact to be read.  The risk in accepting it at all is that a
# plausible-looking invention passes the same door, so the refusals are
# what these test hardest.

def _sequence(tmp_path, count=3, **overrides):
    clip = _clip(tmp_path, "podcast.mov")
    segments = []
    for i in range(count):
        segments.append({
            "segment_id": f"uid-{i}",
            "order": i,
            "speaker": "Akshita" if i % 2 else "Craig",
            "source_file": clip,
            "source_start": 10.0 * i,
            "source_end": 10.0 * i + 5.0,
            "timeline_start": 6.0 * i,
            "timeline_end": 6.0 * i + 5.0,
            "previous_segment_id": f"uid-{i - 1}" if i else None,
            "next_segment_id": f"uid-{i + 1}" if i + 1 < count else None,
        })
    value = {"segments": segments, "speakers": ["Craig", "Akshita"]}
    value.update(overrides)
    return value


def test_a_measured_speech_sequence_is_accepted(tmp_path):
    project = _project(tmp_path)
    _supply(project, "speech_sequence", _sequence(tmp_path),
            source="read off the live timeline")
    supplied = external_inputs.load(str(project))
    assert "3 segments" in supplied["speech_sequence"].checked


def test_a_speech_sequence_naming_a_missing_file_is_refused(tmp_path):
    project = _project(tmp_path)
    value = _sequence(tmp_path)
    value["segments"][1]["source_file"] = "/nowhere/gone.mov"
    _supply(project, "speech_sequence", value)
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "not a file" in str(exc.value)


def test_a_speech_sequence_with_a_broken_chain_is_refused(tmp_path):
    """The link and the order disagreeing is what a well-shaped
    invention fails: a model emits positions, not a self-consistent
    doubly-linked chain over stable ids."""
    project = _project(tmp_path)
    value = _sequence(tmp_path)
    value["segments"][1]["next_segment_id"] = "uid-99"
    _supply(project, "speech_sequence", value)
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "disagree" in str(exc.value)


def test_a_speech_sequence_with_a_repeated_order_is_refused(tmp_path):
    project = _project(tmp_path)
    value = _sequence(tmp_path)
    value["segments"][2]["order"] = 1
    _supply(project, "speech_sequence", value)
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "permutation" in str(exc.value)


def test_a_speech_sequence_with_an_empty_range_is_refused(tmp_path):
    project = _project(tmp_path)
    value = _sequence(tmp_path)
    value["segments"][0]["source_end"] = value["segments"][0]["source_start"]
    _supply(project, "speech_sequence", value)
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "not a range" in str(exc.value)


def test_a_speech_sequence_with_no_segment_id_is_refused(tmp_path):
    """Without an addressable id, re-indexing one portion later means
    re-reading everything - which is the capability the captain asked
    for by name."""
    project = _project(tmp_path)
    value = _sequence(tmp_path)
    del value["segments"][0]["segment_id"]
    _supply(project, "speech_sequence", value)
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "segment_id" in str(exc.value)


def test_an_empty_speech_sequence_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "speech_sequence", {"segments": []})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "non-empty" in str(exc.value)


# ── The three entry points the pipeline had no way in for ────────────
#
# The captain, 2026-09-06: "account for more of the various ways that
# the pipeline would have to account for (things like the rough cut
# already being an input, the song/music spine being put in already, a
# user asking for certain specific sections of the video to be
# re-edited)".
#
# None of the three is a MODE.  Each is a set of state keys, and the run
# shape falls out of `run_scope.supplied_producers`.  These tests hold
# both halves: the checks refuse a claim that is not true, and the
# resolver really stops the step that would have made it.


def _bed(tmp_path, name="bed.wav", seconds=180):
    """A file ffprobe can decode as audio. Skips rather than faking one -
    the point of the check is that it is not a shape check."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not on this machine")
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    path = media / name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=220:duration={seconds}", str(path)],
        capture_output=True, check=True, timeout=120)
    return path


def _selection(tmp_path, **overrides):
    value = {"title": "Hand-picked bed", "source": "project",
             "audio_path": str(_bed(tmp_path)), "duration_seconds": 180.0}
    value.update(overrides)
    return value


def test_a_music_spine_chosen_by_hand_is_accepted(tmp_path):
    """Entry point two. The track is a file on disk with audio in it, and
    that is the whole of what is asserted - WHY it suits the piece is
    taste and is carried through unexamined."""
    project = _project(tmp_path)
    _supply(project, "music_selection",
            _selection(tmp_path,
                       direction_justification={"why_it_fits": "it does"}),
            source="chosen and placed by hand on A2")
    entry = external_inputs.load(str(project))["music_selection"]
    assert "bed.wav" in entry.checked
    assert "audio" in entry.checked
    assert entry.value["direction_justification"] == {"why_it_fits": "it does"}


def test_a_music_selection_naming_no_track_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "music_selection", {"title": "a vibe"})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "names no track" in str(exc.value)


def test_a_music_selection_naming_a_missing_file_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "music_selection",
            {"title": "a vibe", "audio_path": str(tmp_path / "gone.wav")})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "does not exist" in str(exc.value) or "not a file" in str(exc.value)


def test_a_music_selection_whose_file_carries_no_audio_is_refused(tmp_path):
    """A path that exists is not a bed. The one check here that is not
    about JSON."""
    project = _project(tmp_path)
    silent = tmp_path / "media" / "no_audio.mov"
    silent.parent.mkdir(exist_ok=True)
    _render_a_real_video(silent)
    _supply(project, "music_selection", {"title": "x",
                                         "audio_path": str(silent)})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "no audio stream" in str(exc.value)


def test_a_section_past_the_end_of_the_track_is_refused(tmp_path):
    """The silent failure this check exists for: the bed would start
    past the end of the file and play nothing, and every step downstream
    would carry on as though there were music."""
    project = _project(tmp_path)
    _supply(project, "music_selection",
            _selection(tmp_path, section={"source_in": 400.0, "why": "vibes"}))
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "past the end of the track" in str(exc.value)


def test_a_section_inside_the_track_is_accepted(tmp_path):
    project = _project(tmp_path)
    _supply(project, "music_selection",
            _selection(tmp_path, section={"source_in": 30.0, "why": "vibes"}))
    entry = external_inputs.load(str(project))["music_selection"]
    assert "1 named span(s) inside it" in entry.checked


def test_a_splice_past_the_end_of_the_track_is_refused(tmp_path):
    project = _project(tmp_path)
    _supply(project, "music_selection",
            _selection(tmp_path, splices=[{"source_in": 10.0,
                                           "source_out": 900.0}]))
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "900.0" in str(exc.value)


def test_a_further_bed_track_that_is_not_on_disk_is_refused(tmp_path):
    """The bed is a SEQUENCE, so every track it may splice from has to
    be a file this run can open."""
    project = _project(tmp_path)
    _supply(project, "music_selection",
            _selection(tmp_path, tracks=[{"title": "second",
                                          "audio_path": str(tmp_path / "no.wav")}]))
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "tracks[0]" in str(exc.value)


def _spine():
    """The same block `test_a_spine_that_passes_the_contract_is_accepted`
    supplies, as a helper the entry-point tests can reuse."""
    return {"structure": [
        {"block_type": "speech", "position": 1, "clip_id": "clip_1",
         "source_start": 0.132, "source_end": 2.417,
         "timeline_start": 0.0, "timeline_end": 2.285,
         "alignment_method": "whisperx",
         "word_timestamps": [{"word": "hello", "source_start": 0.132,
                              "source_end": 0.5}],
         "content": {"clip_id": "clip_1"}}], "frame_rate": 30.0}


def _placement(clip, timeline_start, timeline_end, video_in=5.0):
    return {"clip_id": "clip_1", "source_file": clip,
            "video_in": video_in,
            "video_out": video_in + (timeline_end - timeline_start),
            "timeline_start": timeline_start, "timeline_end": timeline_end}


def test_cutaways_supplied_from_outside_are_accepted(tmp_path):
    project = _project(tmp_path)
    clip = _clip(tmp_path)
    _supply(project, "b_roll_assignments",
            [_placement(clip, 1.0, 2.5), _placement(clip, 4.0, 5.0)])
    entry = external_inputs.load(str(project))["b_roll_assignments"]
    assert "none overlapping on V2" in entry.checked


def test_two_cutaways_over_the_same_seconds_are_refused(tmp_path):
    """V2 shows one clip at a time. Two overlapping placements are two
    descriptions of the same seconds, and compile_manifest's overlap
    resolution would delete one of them without saying which."""
    project = _project(tmp_path)
    clip = _clip(tmp_path)
    _supply(project, "b_roll_assignments",
            [_placement(clip, 1.0, 3.0), _placement(clip, 2.0, 4.0)])
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "one clip at a time" in str(exc.value)


def test_a_cut_with_no_standalone_cutaways_can_say_so(tmp_path):
    """`[]` for b_roll_interjections is a DECISION, and it is the only
    way a hand cut with no standalone cutaways can stop select_broll
    inventing some. Every other key still refuses an empty value."""
    project = _project(tmp_path)
    _supply(project, "b_roll_interjections", [])
    entry = external_inputs.load(str(project))["b_roll_interjections"]
    assert entry.value == []
    assert "0 placements" in entry.checked


def test_an_empty_value_is_still_refused_for_every_other_key(tmp_path):
    project = _project(tmp_path)
    _supply(project, "b_roll_assignments", [])
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "Nothing is not a value" in str(exc.value)
    assert "b_roll_interjections" in str(exc.value)


def test_the_second_name_the_spine_is_recorded_under_is_checkable(tmp_path):
    """`timed_spine` IS `audio_spine` (mesh_spine's post_bridge sets them
    equal), and four steps read one while three read the other. Supplying
    one does not supply the other, so both are checkable."""
    project = _project(tmp_path)
    _supply(project, "timed_spine", _spine())
    assert "spine_contract" in \
        external_inputs.load(str(project))["timed_spine"].checked

    _supply(project, "timed_spine", {"structure": [{"position": 0}]})
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project))
    assert "spine contract" in str(exc.value)


# ── The resolver really stops the step that would have redone it ─────


def _rough_cut(project, tmp_path):
    """Everything the rough cut is, supplied from outside."""
    clip = _clip(tmp_path, "hand_cut.mov")
    _supply(project, "audio_spine", _spine())
    _supply(project, "timed_spine", _spine())
    _supply(project, "a_roll_assignments", _a_roll(clip))
    _supply(project, "b_roll_assignments", [_placement(clip, 1.0, 2.0)])
    _supply(project, "b_roll_interjections", [])
    _supply(project, "speech_sequence", _sequence(tmp_path))
    return {k: v.value for k, v in
            external_inputs.load(str(project)).items()}


def test_a_supplied_rough_cut_is_not_cut_again_on_a_plain_full_run(tmp_path):
    """The rule `run_scope` states about itself, held on the run shape
    that used to ignore it.

    MEASURED before this held: a plain full run with the same supply
    selected 26 steps and reported `from_external` empty - `mesh_spine`
    and `assign_aroll` both ran and their output shadowed what was
    supplied, because `gather_step_inputs` reads `step_outputs` before
    `external`. The hand-made cut was silently rebuilt.
    """
    project = _project(tmp_path)
    external = _rough_cut(project, tmp_path)

    scope = run_scope.resolve(run_scope.Selection(), state={},
                              external=external)
    for node in ("mesh_spine", "assign_aroll", "select_broll",
                 "speech_sequence"):
        assert node not in scope.steps_to_run, f"{node} redid supplied work"
        assert node not in scope.universe, (
            f"{node} is still counted as never-completed, which holds an "
            f"otherwise finished run at PARTIAL for ever")
    assert scope.supplied["mesh_spine"] == ("audio_spine", "timed_spine")


def test_the_steps_that_still_have_work_still_run(tmp_path):
    """The other direction. Supplying the cut does not switch the
    pipeline off: everything downstream of it, and everything the cut
    does not answer, still runs."""
    project = _project(tmp_path)
    external = _rough_cut(project, tmp_path)

    scope = run_scope.resolve(run_scope.Selection(), state={},
                              external=external)
    for node in ("plan_subtitles", "plan_transitions", "compile_manifest",
                 "render", "catalog", "temporal_index"):
        assert node in scope.steps_to_run, f"{node} was dropped and should not be"


def test_supplying_only_half_a_producers_output_leaves_it_running(tmp_path):
    """ALL of a producer's routed keys, never some of them. `mesh_spine`
    hands out `audio_spine` AND `timed_spine`; leaving it out with only
    the first supplied would drop the second in silence."""
    project = _project(tmp_path)
    _supply(project, "audio_spine", _spine())
    external = {k: v.value for k, v in
                external_inputs.load(str(project)).items()}

    scope = run_scope.resolve(run_scope.Selection(), state={},
                              external=external)
    assert "mesh_spine" in scope.steps_to_run
    assert "mesh_spine" not in scope.supplied


def test_naming_a_supplied_step_on_the_command_line_is_refused(tmp_path):
    """"Run it" and "here is its answer" are two contradictory requests,
    and running the step would overwrite the second in silence - a
    step's own output is read before external state."""
    project = _project(tmp_path)
    _supply(project, "music_selection", _selection(tmp_path))
    external = {k: v.value for k, v in
                external_inputs.load(str(project)).items()}

    with pytest.raises(run_scope.ScopeError) as exc:
        run_scope.resolve(run_scope.Selection(only=("music_selection",)),
                          state={}, external=external)
    message = str(exc.value)
    assert "would overwrite what you supplied" in message
    assert "external/music_selection.json" in message


def test_not_naming_it_runs_without_a_word_of_complaint(tmp_path):
    """The mirror. The refusal above must not fire on a run that did
    nothing wrong."""
    project = _project(tmp_path)
    _supply(project, "music_selection", _selection(tmp_path))
    external = {k: v.value for k, v in
                external_inputs.load(str(project)).items()}

    scope = run_scope.resolve(run_scope.Selection(only=("music_analysis",)),
                              state={}, external=external)
    assert "music_selection" not in scope.steps_to_run
    assert "music_analysis" in scope.steps_to_run
    assert scope.supplied == {"music_selection": ("music_selection",)}


def test_a_step_that_hands_nothing_to_anybody_is_never_read_as_supplied():
    """The empty-side-passes shape, refused by construction. A node
    emitting no state onto an edge has no set of keys to satisfy, so
    "all of them are supplied" would be vacuously true and the step
    would vanish from any run that had an external/ directory at all."""
    dag = run_scope.load_dag()
    silent = set(n["id"] for n in dag["nodes"]) - set(
        run_scope.routed_state_keys(dag))
    assert "validate" in silent, (
        "this test is about a node that hands nothing onward; if the DAG "
        "changed, name another one")
    assert not (silent & set(run_scope.supplied_producers(dag, {})))
    everything = {key for keys in run_scope.routed_state_keys(dag).values()
                  for key in keys}
    assert not (silent & set(run_scope.supplied_producers(dag, everything)))
