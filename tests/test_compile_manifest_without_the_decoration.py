"""What `compile_manifest` can be compiled without, measured by running it.

Issue #260.  The step declared `transition_spec`, `enhancement_spec`,
`sfx_spec` and `color_grade_spec` REQUIRED while its own code called
three of them "optional enhancement specs" and read them with defaults.
Because `required` is what `run_pipeline.gather_step_inputs` raises on
and what `run_scope` derives its refusal from, the stricter of the two
won and the `rough_cut_subtitles` target could not skip four planners
that cost 446.5s on 001's last run.

`library/tools/input_contract.py` surveys the whole pipeline for who
REFUSES when an input is absent.  That is enforcement, and enforcement
is not warrant: `compile_manifest`'s four were enforced perfectly and
still wrong.  Warrant is established by running the step without the
input and looking at what comes out, which is what this file does - for
every declared input of the one step that reads state directly rather
than taking the runner's word for it.

The assertions, and why they are asymmetric
-------------------------------------------
* An input declared OPTIONAL must really compile when absent.  A lie in
  that direction kills a run that legitimately left it out, so it is
  checked mechanically with no exemption list.
* An input declared REQUIRED that nonetheless compiles is not
  automatically wrong: `[]` for transitions is the absence of decoration
  (AGENTS.md 10.5), while `{}` for the audio mix is the spine's declared
  `music_behavior` going silently missing.  That distinction is a
  judgement, so it is RECORDED, in
  `input_contract.REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT`, and this
  file checks the record against the measurement in both directions - a
  recorded entry for an input that really refuses is stale and fails.
"""

from __future__ import annotations

import copy
import json

import pytest

from library.tools import input_contract, run_scope
from library.tools import music_audit_trail as audit
from library.tools.project_layout import ProjectLayout


# ── The fixture: one recorded run, per producer, every value non-empty ──
#
# Keyed by DAG node id, as `pipeline_data.json` keys `step_outputs`, so
# dropping one key is dropping exactly what one step recorded.  Every
# value is REAL - a transition with a type, an effect with a range, a
# caption with a span - because an input whose baseline is empty cannot
# be measured by removing it.

def _step_outputs(a_roll: str, b_roll: str, music: str, sfx: str,
                  overlay: str) -> dict:
    return {
        "mesh_spine": {"audio_spine": {"structure": [
            {"block_type": "hook", "position": 1, "clip_id": "clip_1",
             "source_start": 0.132, "source_end": 2.417,
             "timeline_start": 0.0, "timeline_end": 2.285,
             "content": {"clip_id": "clip_1", "link_group_id": "lg_1"}},
            {"block_type": "speech", "position": 2, "clip_id": "clip_1",
             "source_start": 4.083, "source_end": 7.216,
             "timeline_start": 2.285, "timeline_end": 5.418,
             "content": {"clip_id": "clip_1", "link_group_id": "lg_2"}},
        ], "frame_rate": 30.0}},
        "assign_aroll": {"a_roll_assignments": [
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": a_roll, "video_in": 0.132, "video_out": 2.417,
             "timeline_start": 0.0, "timeline_end": 2.285},
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": a_roll, "video_in": 4.083, "video_out": 7.216,
             "timeline_start": 2.285, "timeline_end": 5.418},
        ]},
        "select_broll": {
            "b_roll_assignments": [
                {"spine_block_position": 2, "block_type": "speech",
                 "clip_id": "clip_2", "source_file": b_roll,
                 "video_in": 1.204, "video_out": 3.104,
                 "duration_seconds": 1.9, "timeline_start": 2.285,
                 "timeline_end": 4.185, "video_only": True}],
            "b_roll_interjections": []},
        "catalog": {
            "clip_catalog": [
                {"clip_id": "clip_1", "path": a_roll, "width": 1080,
                 "height": 1920, "duration_seconds": 20.0},
                {"clip_id": "clip_2", "path": b_roll, "width": 1080,
                 "height": 1920, "duration_seconds": 20.0}],
            "project_fps": 30.0},
        "semantic_analysis": {"semantic_analysis_documents": [
            {"clip_id": "clip_1",
             "analysis": {"motion": "The camera is handheld throughout, "
                                    "with visible shake.",
                          "scene": "A speaker on a city street."},
             "assessment": {"clip_type": "a-roll",
                            "keywords": ["handheld", "speaker"]}}]},
        "plan_subtitles": {"subtitle_plan": {"subtitle_entries": [
            {"text": "hello there", "spine_block_position": 1,
             "timeline_start": 0.05, "timeline_end": 2.2},
            {"text": "and here we go", "spine_block_position": 2,
             "timeline_start": 2.35, "timeline_end": 5.3}]}},
        "render_subtitles": {"subtitle_overlay": {"segments": [
            {"block_position": 1, "timeline_start": 0.0,
             "timeline_end": 2.285, "overlay_path": overlay},
            {"block_position": 2, "timeline_start": 2.285,
             "timeline_end": 5.418, "overlay_path": overlay}]}},
        "plan_transitions": {"transition_spec": [
            {"transition_type": "crash_zoom", "cut_point_timeline": 2.285,
             "duration": 0.4, "after_clip": 0}]},
        "plan_vfx": {"enhancement_spec": {"vfx": [
            {"effect_type": "glow", "timeline_start": 0.0,
             "timeline_end": 2.285,
             "params": {"glow_gain": 5.0, "glow_threshold": 0.75,
                        "glow_size": 3.5}}]}},
        "plan_sfx": {"sfx_spec": {"sfx": [
            {"sfx_id": "whoosh.wav", "source_file": sfx, "source_in": 0.0,
             "label": "sfx_1", "timeline_in": 2.285,
             "timeline_out": 2.785, "volume_db": -14}]}},
        "color_grade": {"color_grade_spec": {
            "series_look": "warm_street",
            "per_clip_adjustments": [
                {"clip_id": "clip_1", "cdl": {"saturation": 1.1}}],
            # Declared WHOLE: an armed glow with no threshold or size is
            # refused by `comp_builder.UndeclaredEffectStrength`, the
            # same rule `series_look` applies at the template.
            "fusion_look": {"glow_gain": 1.2, "glow_threshold": 0.75,
                            "glow_size": 3.5}}},
        "audio_mix": {"audio_mix_spec": {
            "fairlight_preset": "dialogue_clarity",
            "music_bed": [{"timeline_start": 0.0, "timeline_end": 5.418,
                           "gain_db": -18.0}]}},
        "music_selection": {"music_selection": {
            "audio_path": music, "title": "a bed"}},
        "creative_cohesion": {"cohesion_review": {"adjustments": []}},
        "render_motion_graphics": {
            "motion_graphics_overlay": {"segments": []},
            "timed_text_overlay": {"segments": []}},
        "temporal_index": {"temporal_event_indices": [
            {"clip_id": "clip_1", "face_presence": []}]},
    }


@pytest.fixture(scope="module")
def compile_inputs():
    """`{input name: (producer node id, the key it is recorded under)}`.

    Walked off the DAG, so an edge that is re-pointed cannot leave this
    file measuring the wrong key.
    """
    dag = run_scope.load_dag()
    out = {}
    for edge in dag["edges"]:
        if edge["to"] != "compile_manifest":
            continue
        for src, dst in (edge.get("data_mapping") or {}).items():
            out[dst] = (edge["from"], src)
    return out


@pytest.fixture
def project(tmp_path):
    """A project with one recorded run on file, under tmp_path only."""
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
    outputs = _step_outputs(names["a_roll.mov"], names["b_roll.mov"],
                            names["bed.wav"], names["whoosh.wav"],
                            names["sub_seg_000.mov"])
    # A resolved selection owes its audit sidecar: the pre-render check
    # refuses without it, so the fixture stages what 2.04's post-bridge
    # writes on a real run.
    audit.write_audit_trail(
        str(project_dir), outputs["music_selection"]["music_selection"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def _compile(project, drop=None):
    """Run the real step against the project, with one recorded key gone.

    The SFX library is the one thing stubbed: `load_sfx_catalog` reads
    `PIPELINE_SFX_LIBRARY`, which a test may not reach. Stubbing it keeps
    the measurement about the ABSENT INPUT rather than about the machine
    the test runs on. The plan itself carries the file it chose, so
    nothing here has to resolve one.
    """
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    if drop is not None:
        producer, key = drop
        outputs.get(producer, {}).pop(key, None)
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


# ── The baseline: everything on file compiles ────────────────────────

def test_the_baseline_compiles_with_every_input_present(project):
    manifest = _compile(project)
    assert len(manifest["tracks"]["V1"]["clips"]) == 2
    assert len(manifest["tracks"]["V2"]["clips"]) == 1
    assert len(manifest["tracks"]["A2"]["clips"]) == 1
    assert len(manifest["tracks"]["A3"]["clips"]) == 1
    assert len(manifest["subtitles"]) == 2
    assert len(manifest["transitions"]) == 1
    assert len(manifest["vfx"]) == 1
    assert manifest["color_grade"]
    assert manifest["audio_mix"]


# ── The four the issue is about ──────────────────────────────────────

def test_a_resolved_selection_with_no_sidecar_refuses_the_compile(project):
    """2.04's warn-and-continue write must not pass silently downstream.

    Step 2.04 writes `music_audit_trail.json` warn-and-continue: a
    failed write is one WARNING line in a long stderr stream nobody is
    looking for. The pre-render check is the other half of that trade -
    a run that kept the choice but lost the record of why it was
    chosen refuses here, loudly, instead of quietly reverting half of
    the 2026-09-16 ruling while reporting success.
    """
    project_dir, layout, _outputs, _sfx_file = project
    (layout.step_dir("music_selection") / audit.AUDIT_FILENAME).unlink()
    with pytest.raises(audit.AuditTrailMissing,
                       match="audit sidecar is missing"):
        _compile(project)











def test_a_manifest_compiles_with_none_of_the_four(project):
    """The rough cut the captain asked for: hard cuts, no effects, no
    sound design, ungraded - and everything else intact."""
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, _sfx_file = project
    outputs = copy.deepcopy(outputs)
    for producer in ("plan_transitions", "plan_vfx", "plan_sfx",
                     "color_grade"):
        outputs.pop(producer)
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}), encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[]):
        manifest = step.compile_manifest(str(layout.output_root))

    assert manifest["transitions"] == []
    assert manifest["vfx"] == []
    assert manifest["tracks"]["A3"]["clips"] == []
    assert manifest["color_grade"] == {}
    # The cut itself, and the captions, are untouched.
    assert len(manifest["tracks"]["V1"]["clips"]) == 2
    assert len(manifest["tracks"]["V2"]["clips"]) == 1
    assert len(manifest["subtitles"]) == 2
    assert manifest["subtitle_overlay"]["segments"]


# ── The survey: every declared input, measured ───────────────────────

def _measure_all(project, compile_inputs):
    """`{input name: True if the step compiled without it}`."""
    verdicts = {}
    for name, drop in sorted(compile_inputs.items()):
        try:
            _compile(project, drop)
        except Exception:
            verdicts[name] = False
        else:
            verdicts[name] = True
    return verdicts


def test_every_optional_input_really_compiles_when_absent(
        project, compile_inputs):
    """No exemptions here. An input declared optional that the step
    cannot run without is a run that dies for obeying the declaration."""
    manifests = run_scope.load_manifests(run_scope.load_dag())
    optional = run_scope.optional_inputs(manifests["compile_manifest"])
    verdicts = _measure_all(project, compile_inputs)
    broken = sorted(name for name in optional
                    if name in verdicts and not verdicts[name])
    assert not broken, (
        f"compile_manifest declares {broken} optional and cannot compile "
        f"without them")


def test_a_required_input_the_step_runs_without_is_recorded(
        project, compile_inputs):
    """The declaration/code disagreement of #260, checked both ways.

    Unrecorded and the step compiles: the requirement is unwarranted and
    costs a scoped run the producer's whole runtime. Recorded and the
    step refuses: the record is stale and claims a cost that is not
    there."""
    manifests = run_scope.load_manifests(run_scope.load_dag())
    optional = run_scope.optional_inputs(manifests["compile_manifest"])
    verdicts = _measure_all(project, compile_inputs)
    recorded = {
        name for (node_id, name)
        in input_contract.REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT
        if node_id == "compile_manifest"}

    unrecorded = sorted(
        name for name, compiled in verdicts.items()
        if compiled and name not in optional and name not in recorded)
    assert not unrecorded, (
        f"compile_manifest declares {unrecorded} required and compiles "
        f"without them. Either declare them optional, or record in "
        f"input_contract.REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT what "
        f"goes silently missing.")

    stale = sorted(name for name in recorded
                   if verdicts.get(name) is False)
    assert not stale, (
        f"{stale} are recorded as 'required though the step runs without "
        f"it', but the step refuses without them. Delete the entry.")




# ── A drawn transition with no hold is a hard cut, never 15 frames ────
#
# AGENTS.md 10.5: how long a drawn transition holds comes from the plan's
# `duration_feel` (bounded by the brand template), and the engine supplies
# neither. Step 4.02's post-bridge drops such entries at plan time; the
# compile serves only state that reached it some other way (stale
# pipeline_data, a revised review gate). What ships is the hard cut the
# boundary already is - the absence of decoration - recorded in
# `transitions_downgraded` beside the withdrawn types, never silent, and
# never a half-second effect nothing chose.

def _compile_with_transitions(project, spec):
    """Run the real step with `plan_transitions.transition_spec` replaced."""
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    outputs["plan_transitions"] = {"transition_spec": spec}
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


def test_a_drawn_transition_with_no_hold_ships_a_hard_cut_and_says_so(
        project, capsys):
    """No duration anywhere: no 15-frame invention, a recorded downgrade."""
    manifest = _compile_with_transitions(project, [
        {"transition_id": "trans_001", "transition_type": "crash_zoom",
         "cut_point_timeline": 2.285}])
    shipped = manifest["transitions"][0]
    assert shipped["transition_type"] == "hard_cut"
    assert shipped["duration_frames"] == 0
    # A hard cut needs no comp: nothing drawn reaches the renderer.
    assert manifest["fusion_effects"]["transitions"] == []
    # The downgrade is on the record, with what was asked and why.
    (record,) = manifest["transitions_downgraded"]
    assert record["transition_id"] == "trans_001"
    assert record["requested_type"] == "crash_zoom"
    assert record["shipped_type"] == "hard_cut"
    assert record["reason"]
    assert "trans_001" in capsys.readouterr().err
    # The cut boundary itself survives: both A-roll clips still play.
    assert len(manifest["tracks"]["V1"]["clips"]) == 2


def test_a_drawn_transition_with_zero_frames_is_not_given_fifteen(project):
    """An explicit zero is undecided too: nothing completes it to 15."""
    manifest = _compile_with_transitions(project, [
        {"transition_id": "trans_001", "transition_type": "crash_zoom",
         "cut_point_timeline": 2.285, "duration_frames": 0}])
    assert manifest["transitions"][0]["transition_type"] == "hard_cut"
    assert manifest["fusion_effects"]["transitions"] == []
    assert len(manifest["transitions_downgraded"]) == 1


def test_a_declared_hold_still_reaches_the_fusion_comp(project):
    """The control: a hold somebody stated is carried, not downgraded."""
    manifest = _compile_with_transitions(project, [
        {"transition_id": "trans_001", "transition_type": "crash_zoom",
         "cut_point_timeline": 2.285, "duration": 0.4, "after_clip": 0}])
    (comp,) = manifest["fusion_effects"]["transitions"]
    assert comp["type"] == "zoom_blur"  # canonical form of crash_zoom
    assert comp["duration_frames"] == 12  # 0.4s at 30fps, as declared
    assert manifest["transitions_downgraded"] == []


def test_validation_refuses_a_durationless_drawn_transition():
    """The backstop: validation never completes a hold, it stops the run.

    The fusion emit path above downgrades such entries before validation
    runs, so this fires only for data that reached the validator some
    other way - and that data refuses with the transition named and the
    remedy stated, rather than compiling past an undecided effect.
    """
    from library.steps.step_5_04_compile_manifest.step import (
        _apply_manifest_qa_checks,
    )

    with pytest.raises(ValueError) as exc:
        _apply_manifest_qa_checks({"transitions": [
            {"transition_id": "trans_001", "transition_type": "crash_zoom",
             "cut_point_timeline": 2.285}]})
    message = str(exc.value)
    assert "trans_001" in message
    assert "--rerun plan_transitions" in message


def test_validation_leaves_cuts_and_held_transitions_alone():
    """Cuts are zero-length by definition; stated holds are carried."""
    from library.steps.step_5_04_compile_manifest.step import (
        _apply_manifest_qa_checks,
    )

    manifest = {"transitions": [
        {"transition_id": "trans_001", "transition_type": "hard_cut"},
        {"transition_id": "trans_002", "transition_type": "crash_zoom",
         "cut_point_timeline": 2.285, "duration_frames": 12},
    ]}
    _apply_manifest_qa_checks(manifest)  # must not raise
    assert manifest["transitions"][0]["duration_frames"] == 0
