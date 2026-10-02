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

import pytest

from library.tools import external_inputs, run_scope
from library.tools.external_inputs import ExternalStateError
from library.tools.project_layout import ProjectLayout


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


# ── A claim that is not true is refused by name, before the run ─────

def test_every_untrue_claim_is_refused_by_name(tmp_path):
    """Each row is one claim the checks must refuse, and the words that
    say which one. Folded from nineteen one-row tests; every row still
    runs on its own project."""
    clip = _clip(tmp_path)
    vo = _clip(tmp_path, "vo.wav")

    def missing_file():
        rows = _a_roll(clip)
        rows[1]["source_file"] = str(tmp_path / "media" / "never_shot.mov")
        return rows

    def backwards():
        rows = _a_roll(clip)
        rows[0]["video_out"] = 0.1
        return rows

    def overrun():
        rows = _a_roll(clip)
        rows[1]["video_out"] = 41.5
        return rows

    def broken_chain():
        value = _sequence(tmp_path)
        value["segments"][1]["next_segment_id"] = "uid-99"
        return value

    def repeated_order():
        value = _sequence(tmp_path)
        value["segments"][2]["order"] = 1
        return value

    def vo_without_file():
        entry = _voiceover(vo, 1.0, 2.5)
        del entry["audio_id"]
        return [entry]

    measured = {"step_outputs": {"catalog": {"clip_catalog": [
        {"clip_id": "clip_1", "path": clip, "duration_seconds": 20.0}]}}}
    rows = [
        ("a_roll_assignments", missing_file(), None,
         ("never_shot.mov", "not a file")),
        # The strongest check without opening the media: the pipeline
        # already measured this clip and the cut plays past its end.
        ("a_roll_assignments", overrun(), measured, ("41.5", "20.0")),
        ("a_roll_assignments", backwards(), None, ("not a range",)),
        # "Trust me, it exists" with nothing in it.
        ("a_roll_assignments", [], None, ("empty list",)),
        ("b_roll_assignments", [], None,
         ("Nothing is not a value", "b_roll_interjections")),
        # State nothing can verify is refused, not taken on faith, and
        # the refusal says what CAN be supplied.
        ("creative_direction", {"target_mood": "warm"}, None,
         ("cannot be supplied from outside", "not a check that passes",
          "a_roll_assignments")),
        # A model emits positions, not a self-consistent linked chain.
        ("speech_sequence", broken_chain(), None, ("disagree",)),
        ("speech_sequence", repeated_order(), None, ("permutation",)),
        # V2 shows one clip at a time; overlap resolution would delete
        # one without saying which.
        ("b_roll_assignments",
         [_placement(clip, 1.0, 3.0), _placement(clip, 2.0, 4.0)], None,
         ("one clip at a time",)),
        ("voiceover_assignments",
         [_voiceover(vo, 1.0, 2.5), _voiceover(vo, 2.0, 3.0)], None,
         ("one voice at a time",)),
        ("voiceover_assignments", vo_without_file(), None, ("audio_id",)),
        ("timed_spine", {"structure": [{"position": 0}]}, None,
         ("spine contract",)),
        ("music_selection", {"title": "a vibe"}, None, ("names no track",)),
    ]
    for i, (key, value, state, words) in enumerate(rows):
        project = tmp_path / f"row{i}"
        project.mkdir()
        ProjectLayout(str(project)).ensure()
        _supply(project, key, value)
        with pytest.raises(ExternalStateError) as exc:
            external_inputs.load(str(project), state)
        for word in words:
            assert word in str(exc.value), (i, key, word, str(exc.value))


def test_every_true_claim_is_accepted_and_says_what_it_checked(tmp_path):
    """The mirror: a measurement passes, and `checked` names the check.
    `[]` for b_roll_interjections / voiceover_assignments is a DECISION
    (no standalone cutaways, no narration), not an empty claim."""
    clip = _clip(tmp_path)
    vo = _clip(tmp_path, "vo.wav")
    rows = [
        ("audio_spine", _spine(), "validate_spine_blocks"),
        # `timed_spine` IS `audio_spine` under a second name; supplying
        # one does not supply the other, so both are checkable.
        ("timed_spine", _spine(), "spine_contract"),
        ("speech_sequence", _sequence(tmp_path), "3 segments"),
        ("b_roll_assignments",
         [_placement(clip, 1.0, 2.5), _placement(clip, 4.0, 5.0)],
         "none overlapping on V2"),
        ("b_roll_interjections", [], "0 placements"),
        ("voiceover_assignments", [_voiceover(vo, 1.0, 2.5)],
         "none overlapping on A1"),
        ("voiceover_assignments", [], None),
    ]
    for i, (key, value, checked) in enumerate(rows):
        project = tmp_path / f"row{i}"
        project.mkdir()
        ProjectLayout(str(project)).ensure()
        _supply(project, key, value)
        entry = external_inputs.load(str(project))[key]
        assert entry.value == value, key
        if checked:
            assert checked in entry.checked, (key, entry.checked)


# ── The resolver counts it, and the step receives it ────────────────


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


def test_a_whole_pipeline_collapses_to_the_step_that_still_has_work(
        tmp_path):
    """The captain's case end to end: the cut and the master both exist
    because they were made elsewhere, so the only thing left to do is
    judge the master.

    Twenty-six steps become one, and nothing was taken on faith - the
    manifest passed the validator step 5.04 runs on its own output and
    the master was decoded by ffprobe.

    Two of the twenty-six are SUPPLIED rather than merely unneeded, and
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
    assert len(scope.skipped) + len(scope.supplied) == 26


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
    taste and is carried through unexamined. A named section inside the
    track is accepted too."""
    project = _project(tmp_path)
    _supply(project, "music_selection",
            _selection(tmp_path,
                       direction_justification={"why_it_fits": "it does"},
                       section={"source_in": 30.0, "why": "vibes"}),
            source="chosen and placed by hand on A2")
    entry = external_inputs.load(str(project))["music_selection"]
    assert "bed.wav" in entry.checked
    assert "audio" in entry.checked
    assert "1 named span(s) inside it" in entry.checked
    assert entry.value["direction_justification"] == {"why_it_fits": "it does"}


def test_a_music_selection_that_cannot_play_is_refused(tmp_path):
    """Each row is a bed that would play nothing or the wrong thing. A
    section past the end is the silent one: downstream steps would carry
    on as though there were music. The bed is a SEQUENCE, so every track
    it may splice from must be a file this run can open."""
    silent = tmp_path / "media" / "no_audio.mov"
    silent.parent.mkdir(exist_ok=True)
    _render_a_real_video(silent)
    rows = [
        ({"title": "a vibe", "audio_path": str(tmp_path / "gone.wav")},
         ("does not exist", "not a file"), any),
        # A path that exists is not a bed.
        ({"title": "x", "audio_path": str(silent)}, ("no audio stream",), all),
        (_selection(tmp_path, section={"source_in": 400.0, "why": "vibes"}),
         ("past the end of the track",), all),
        (_selection(tmp_path, splices=[{"source_in": 10.0,
                                        "source_out": 900.0}]),
         ("900.0",), all),
        (_selection(tmp_path, tracks=[{"title": "second",
                                       "audio_path": str(tmp_path / "no.wav")}]),
         ("tracks[0]",), all),
    ]
    for i, (value, words, quantifier) in enumerate(rows):
        project = tmp_path / f"row{i}"
        project.mkdir()
        ProjectLayout(str(project)).ensure()
        _supply(project, "music_selection", value)
        with pytest.raises(ExternalStateError) as exc:
            external_inputs.load(str(project))
        assert quantifier(w in str(exc.value) for w in words), (i, str(exc.value))


def _spine():
    """A spine block that passes the spine contract."""
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


def _voiceover(clip, timeline_start, timeline_end, audio_in=0.0):
    return {"spine_block_position": 1, "block_type": "speech",
            "audio_id": "audio_001", "source_file": clip,
            "audio_in": audio_in,
            "audio_out": audio_in + (timeline_end - timeline_start),
            "timeline_start": timeline_start, "timeline_end": timeline_end}


# ── The resolver really stops the step that would have redone it ─────


def _rough_cut(project, tmp_path):
    """Everything the rough cut is, supplied from outside."""
    clip = _clip(tmp_path, "hand_cut.mov")
    _supply(project, "audio_spine", _spine())
    _supply(project, "timed_spine", _spine())
    _supply(project, "a_roll_assignments", _a_roll(clip))
    _supply(project, "b_roll_assignments", [_placement(clip, 1.0, 2.0)])
    _supply(project, "b_roll_interjections", [])
    _supply(project, "voiceover_assignments", [])
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
    # The other direction: supplying the cut does not switch the pipeline
    # off - everything downstream of it, and everything it does not
    # answer, still runs.
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


# ── Declarations share the directory and are NOT supplied state ────

def test_a_declaration_is_checked_by_its_owner_and_not_supplied(tmp_path):
    """Measured 2026-09-11 on `lucie/geo-podcast`: the moment the
    captain's `overlay_intent.json` was written into `external/`, every
    `build-reels` on that project refused at input gathering with
    "declares key None" - a message about a contract that file was
    never written to. A declaration is read by its OWNER."""
    import json as _json

    from library.tools import external_inputs

    root = tmp_path / "project"
    external = root / "external"
    external.mkdir(parents=True)
    (external / "reel_ending.json").write_text(_json.dumps({
        "version": 1,
        "endings": [{"reel": "Reel 13", "tail_element": "tv_power_tail",
                     "ends_on": {"anchor_phrase": "in our bio"},
                     "reason": "captain 2026-09-11"}]}), encoding="utf-8")
    assert external_inputs.checked_declarations(root) == {
        "reel_ending": "1 entry"}
    # Checked, and then left OUT of the supplied state: a standing
    # decision about the project is not a step's output.
    assert external_inputs.load(root, {}) == {}

    # 2026-09-25, one owner later: the post-header lane's file, which no
    # row named, refused every build-reels the same way.
    from library.tools import reel_post_header

    (external / reel_post_header.HOOKS_FILE).write_text(_json.dumps({
        "format": "reel_post_header/1",
        "hooks": {"3": {"hook": "People stopped searching.",
                        "basis": "Akshita, verbatim"}}}), encoding="utf-8")
    assert external_inputs.checked_declarations(root) == {
        "reel_ending": "1 entry", "reel_post_header": "1 entry"}
    assert external_inputs.load(root, {}) == {}


def test_a_malformed_declaration_still_refuses_in_its_owners_words(tmp_path):
    import json as _json

    from library.tools import external_inputs
    from library.tools import reel_ending

    root = tmp_path / "project"
    external = root / "external"
    external.mkdir(parents=True)
    (external / "reel_ending.json").write_text(_json.dumps({
        "version": 1,
        "endings": [{"reel": "Reel 13", "reason": "r"}]}), encoding="utf-8")
    with pytest.raises(reel_ending.ReelEndingError, match="no ends_on"):
        external_inputs.load(root, {})


def test_verify_names_the_owner_rather_than_the_wrong_contract(tmp_path):
    import json as _json

    from library.tools import external_inputs

    root = tmp_path / "project"
    external = root / "external"
    external.mkdir(parents=True)
    path = external / "overlay_intent.json"
    path.write_text(_json.dumps({"version": 1, "targets": {}}),
                    encoding="utf-8")
    context = external_inputs.Context(project_folder=root, state={})
    with pytest.raises(external_inputs.ExternalStateError) as excinfo:
        external_inputs.verify(path, context)
    message = str(excinfo.value)
    assert "is a DECLARATION" in message
    assert "library.tools.overlay_intent" in message
    assert "declares key" not in message


def test_every_declaration_names_a_reader_that_exists():
    """A registry row pointing at a reader nobody wrote would refuse
    every project that carries that file."""
    import importlib

    from library.tools import external_inputs

    for stem, (module_name, reader) in (
            external_inputs.DECLARATIONS.items()):
        module = importlib.import_module(module_name)
        assert callable(getattr(module, reader)), (
            f"{stem} names {module_name}.{reader}, which is not callable")
