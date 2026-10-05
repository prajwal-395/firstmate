"""A non-empty VFX plan reaches the manifest, and the picture.

Step 4.03 has emitted `{"visual_effects": []}` on every run the
repository can show, and the question that had to be settled before
anything was built was WHICH of four things that means: nothing asks it
for effects in a form it can answer, it answers and the answer is
dropped, the vocabulary does not exist, or it decided none on the merits.

This file is the half of the answer that has to be RUN rather than read.
It drives the real post-bridge as a subprocess, feeds its actual output
into the real `compile_manifest`, and follows the effect through to the
Fusion comp string the renderer would import - so "the answer is dropped"
is refuted by a plan arriving in the picture, not by an argument.

`tests/unit/picture/test_vfx.py` is the other half: an empty plan is still
accepted, and now says why it is empty.
"""
from __future__ import annotations
import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.project_layout import ProjectLayout
from tests.scenarios.test_compile_manifest_without_the_decoration import (  # noqa: E402
    _step_outputs,
)
import os


REPO = Path(__file__).resolve().parents[2]
POST_BRIDGE = "library.steps.step_4_03_plan_vfx.post_bridge"
_REAL_PATH_EXISTS = os.path.exists
_FAKE_MEDIA_PATHS = {"/a.mov", "/m.wav", "/whoosh.mp3", "/click.wav"}


def _exists_with_fake_media(path):
    """Pretend only the Resolve double's media files exist.

    Patching `os.path.exists` patches the shared `os.path` module, so a
    blanket True also makes `Path.exists()` report both external declaration
    layouts present. Keep actual filesystem checks real outside these files.
    """
    return str(path) in _FAKE_MEDIA_PATHS or _REAL_PATH_EXISTS(path)


@pytest.fixture
def recorded_run(tmp_path):
    """One recorded run on file, under tmp_path only (AGENTS.md 8)."""
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
    # refuses without it, so stage what 2.04's post-bridge writes on a
    # real run.
    from library.tools import music_audit_trail as audit
    audit.write_audit_trail(
        str(project_dir), outputs["music_selection"]["music_selection"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def _spine_of(outputs):
    return {"structure": outputs["mesh_spine"]["audio_spine"]["structure"]}


def _plan_through_the_post_bridge(outputs, plan):
    """The REAL post-bridge, run the way `run_pipeline` runs it."""
    payload = {
        "a_roll_assignments": outputs["assign_aroll"]["a_roll_assignments"],
        "timed_spine": _spine_of(outputs),
        "vfx_creative": plan,
    }
    proc = subprocess.run(
        [sys.executable, "-m", POST_BRIDGE],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["enhancement_spec"], proc.stderr


def _compile_with(recorded_run, enhancement_spec):
    """The REAL compile_manifest, over that post-bridge's own output."""
    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = recorded_run
    outputs = copy.deepcopy(outputs)
    outputs["plan_vfx"] = {"enhancement_spec": enhancement_spec}
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


def test_a_planned_effect_reaches_the_manifest_and_the_comp(recorded_run):
    """End to end: a model answer, through both bridges, into a comp.

    The plan names one block of the recorded spine, one effect from the
    step's own toolkit, and its own values under the parameter names the
    renderer reads - which is the whole of what the handoff asks a
    planner for.
    """
    _, _, outputs, _ = recorded_run
    block = outputs["mesh_spine"]["audio_spine"]["structure"][0]["position"]

    spec, _ = _plan_through_the_post_bridge(outputs, [{
        "target_block_position": block,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "a long static hold that wants a drift",
    }])

    # 1. the post-bridge resolved it, and says the plan was planned.
    assert len(spec["visual_effects"]) == 1
    assert spec["planning_basis"]["basis"] == "planned"
    effect = spec["visual_effects"][0]
    assert effect["effect_type"] == "slow_zoom_in"
    assert effect["params"] == {"zoom_start": 1.0, "zoom_end": 1.03}

    # 2. compile_manifest carried it onto the assembly manifest.
    manifest = _compile_with(recorded_run, spec)
    assert len(manifest["vfx"]) == 1
    assert manifest["vfx"][0]["effect_type"] == "slow_zoom_in"

    # 3. it reached the V1 clip's own Fusion effect, by parameter name -
    #    which is the only thing the renderer dispatches on (AGENTS.md
    #    10.2).
    per_clip = manifest["fusion_effects"]["per_clip"]
    carrying = [c for c in per_clip.values()
                if c.get("_preset") == "slow_zoom_in"]
    assert len(carrying) == 1, per_clip
    assert carrying[0]["zoom_start"] == 1.0
    assert carrying[0]["zoom_end"] == 1.03

    # 4. and those parameters draw real nodes.
    params = {k: v for k, v in carrying[0].items() if not k.startswith("_")}
    params["vignette"] = False
    comp = build_effect_comp(params, 120, source_res=(1080, 1920))
    assert "Tools = {" in comp
    assert "Transform" in comp


def test_an_empty_plan_still_compiles_and_still_says_why(recorded_run):
    """The accepting half is untouched: 275's ruling still holds."""
    _, _, outputs, _ = recorded_run
    spec, _ = _plan_through_the_post_bridge(outputs, [])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"]["basis"] == "no_effects_planned"

    manifest = _compile_with(recorded_run, spec)
    assert manifest["vfx"] == []
    assert manifest["generator_overlays"] == []
    # An empty plan is the absence of decoration, not a broken build.
    assert len(manifest["tracks"]["V1"]["clips"]) == 2


def test_compile_manifest_reads_the_basis_and_names_the_casualties(
        recorded_run, caplog):
    """An unbuildable plan still compiles, as an empty one, and the
    record has a reader outside the step that writes it.

    `compile_manifest` is where an empty VFX plan becomes an absence in
    the picture, so it is where an unbuildable one is said out loud.
    Reading is not gating: the compile still succeeds, and whether a
    dropped entry should refuse the step is recorded in
    `vfx_plan_basis.THE_REFUSAL_QUESTION` rather than answered here.
    """
    import logging

    _, _, outputs, _ = recorded_run
    block = outputs["mesh_spine"]["audio_spine"]["structure"][0]["position"]
    spec, _ = _plan_through_the_post_bridge(outputs, [{
        "target_block_position": block,
        "effect_type": "glitch", "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "an effect the toolkit has not got",
    }])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"]["basis"] == "every_entry_dropped"

    with caplog.at_level(logging.INFO):
        manifest = _compile_with(recorded_run, spec)

    assert manifest["vfx"] == []
    text = caplog.text
    assert "every_entry_dropped" in text
    assert "glitch" in text
    assert "unknown_effect_type" in text


# --------------------------------------------------------------------------
# From test_spliced_bed_reaches_the_manifest.py
#
# A real assembly manifest carrying a multi-track, multi-section bed.
#
# The end-to-end half of `tests/unit/audio/test_music_bed.py`: the REAL
# `compile_manifest` compiles a real spine into a real manifest, and what is
# asserted is A2 as the renderer will read it - several clips, from two
# files, overlapping where a crossfade was declared - plus the volume ramps
# `otio_mix` writes across each splice.
#
# It also holds the DELIVERY end: the renderer's own track allocator puts
# two overlapping bed clips on two lanes, because two clips cannot share one
# Resolve audio track, and pushes SFX above whatever the bed used.

REPO_2 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_2)

from library.steps.step_5_04_compile_manifest.step import (  # noqa: E402
    compile_manifest,
)
from library.tools.otio_mix import (  # noqa: E402
    MIN_VOLUME_DB, mix_targets,
)


@pytest.fixture
def manifest(tmp_path):
    aroll = str(tmp_path / "aroll.mov")
    broll = str(tmp_path / "broll.mov")
    one = str(tmp_path / "one.wav")
    two = str(tmp_path / "two.wav")
    for path in (aroll, broll, one, two):
        with open(path, "w", encoding="utf-8") as f:
            f.write("dummy")

    blocks = [
        {"block_type": "speech", "position": 1, "clip_id": "clip_1",
         "source_start": 0.117, "source_end": 10.117, "timeline_start": 0.0,
         "timeline_end": 10.0, "music_behavior": "background",
         "content": {"clip_id": "clip_1", "link_group_id": "lg_1"}},
        {"block_type": "transition_slot", "position": 2, "clip_id": None,
         "source_start": None, "source_end": None, "timeline_start": 10.0,
         "timeline_end": 16.0, "music_behavior": "prominent", "content": {}},
        {"block_type": "speech", "position": 3, "clip_id": "clip_1",
         "source_start": 20.317, "source_end": 32.317, "timeline_start": 16.0,
         "timeline_end": 28.0, "music_behavior": "background",
         "content": {"clip_id": "clip_1", "link_group_id": "lg_2"}},
    ]

    inputs = {
        "a_roll_assignments": [
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": aroll, "video_in": 0.117, "video_out": 10.117,
             "timeline_start": 0.0, "timeline_end": 10.0},
            {"clip_id": "clip_1", "source_clip_id": "clip_1",
             "source_file": aroll, "video_in": 20.317, "video_out": 32.317,
             "timeline_start": 16.0, "timeline_end": 28.0},
        ],
        "b_roll_assignments": [
            {"spine_block_position": 2, "block_type": "transition_slot",
             "clip_id": "clip_2", "source_file": broll,
             "video_in": 0.213, "video_out": 6.213, "duration_seconds": 6.0,
             "timeline_start": 10.0, "timeline_end": 16.0,
             "video_only": True},
        ],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []}, "transition_spec": [],
        "enhancement_spec": [], "color_grade_spec": {},
        "audio_mix_spec": {
            "track_levels": {"A2_music": {"fade_duration_seconds": 1.0}},
            "music_automation": [
                {"spine_block_position": b["position"],
                 "timeline_start": b["timeline_start"],
                 "timeline_end": b["timeline_end"],
                 "music_behavior": b["music_behavior"],
                 "target_level_db": -18 if b["music_behavior"] == "background"
                                    else -6}
                for b in blocks
            ],
        },
        "sfx_spec": [{
            "label": "sfx_001", "sfx_id": "camera soft click.wav",
            "source_file": str(tmp_path / "click.wav"),
            "source_in": 0.0, "timeline_in": 12.0, "timeline_out": 12.459,
            "volume_db": -4,
        }],
        # ── The whole point: two tracks, three pieces, one crossfade ──
        "music_selection": {
            "title": "One", "audio_path": one, "duration_seconds": 300.0,
            "tracks": [{"title": "Two", "audio_path": two,
                        "duration_seconds": 240.0}],
            "splices": [
                {"intended_use": "the sparse opening", "source_in": 12.0,
                 "source_out": 22.0},
                {"track": "Two", "intended_use": "the lift under the cutaway",
                 "source_in": 90.0, "source_out": 96.0},
                {"intended_use": "the settled body", "source_in": 180.0,
                 "source_out": 192.0},
            ],
        },
        "audio_spine": {
            "frame_rate": 30.0,
            "structure": blocks,
            "music_bed": [
                {"source_in": 12.0, "why": "the sparse opening sits under "
                                           "the first passage"},
                {"track": "Two", "source_in": 90.0, "starts_at_block": 2,
                 "crossfade_seconds": 1.5,
                 "why": "the cutaway wants the other track's lift"},
                {"source_in": 180.0, "starts_at_block": 3,
                 "crossfade_seconds": 1.0,
                 "why": "back to the first track, at its settled body"},
            ],
        },
        "clip_catalog": [
            {"clip_id": "clip_1", "path": aroll, "width": 1080, "height": 1920},
            {"clip_id": "clip_2", "path": broll, "width": 1080, "height": 1920},
        ],
        "semantic_analysis": {"semantic_analysis_documents": [
            {"clip_id": "clip_1",
             "analysis": {"motion": "Locked off.", "scene": "A speaker."},
             "assessment": {"clip_type": "a-roll"}}]},
    }
    with open(str(tmp_path / "click.wav"), "w", encoding="utf-8") as f:
        f.write("dummy")

    # A resolved selection owes its audit sidecar: the pre-render check
    # refuses without it, so the fixture stages what 2.04's post-bridge
    # writes on a real run. The compile reads the project root off
    # `out_dir`, hence the output directory under tmp_path.
    from library.tools import music_audit_trail as audit
    audit.write_audit_trail(str(tmp_path), inputs["music_selection"])

    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        return (compile_manifest(str(tmp_path / "pipeline_output")),
                one, two)


def test_the_manifest_carries_a_multi_track_spliced_bed(manifest):
    built, one, two = manifest
    clips = built["tracks"]["A2"]["clips"]
    assert len(clips) == 3, "the bed is three pieces"
    assert [os.path.basename(c["source_file"]) for c in clips] == [
        "one.wav", "two.wav", "one.wav"]
    assert [c["source_in"] for c in clips] == [12.0, 90.0, 180.0]
    # Disjoint sections of ONE track, in one bed: 12s and 180s of one.wav.
    assert clips[0]["source_file"] == clips[2]["source_file"]
    assert clips[0]["source_in"] != clips[2]["source_in"]
    # Every declared crossfade is a real overlap.
    for outgoing, incoming in zip(clips, clips[1:]):
        assert incoming["crossfade_in_seconds"] > 0
        assert outgoing["timeline_out"] > incoming["timeline_in"], (
            "a crossfade needs the two pieces to play at once")
        assert round(outgoing["timeline_out"] - incoming["timeline_in"], 3) \
            == incoming["crossfade_in_seconds"]


def test_the_ramps_across_each_splice_reach_the_mix(manifest):
    built, _, _ = manifest
    targets = [t for t in mix_targets(built, fps=30.0) if t["role"] == "music"]
    assert len(targets) == 3
    # Piece one fades OUT at its tail; pieces two and three fade IN at
    # their head; the level between the ramps is the block's own planned
    # level and nothing here revises it.
    assert targets[0]["keyframes"][max(targets[0]["keyframes"])] == MIN_VOLUME_DB
    for target in targets[1:]:
        assert target["keyframes"][0] == MIN_VOLUME_DB
    assert -6.0 in targets[1]["keyframes"].values(), (
        "the cutaway block planned `prominent`, and the piece over it "
        "carries that level between its ramps")


def test_the_renderer_puts_the_overlapping_pieces_on_separate_lanes(manifest):
    """Two clips cannot share one Resolve audio track."""
    from library.steps.step_6_01_render.resolve_build_timeline import (
        _allocate_audio_tracks,
    )

    built, _, _ = manifest
    allocations = _allocate_audio_tracks(
        built["tracks"]["A2"]["clips"], base_track_index=2, fps=30.0)
    lanes = [t for _, t in allocations]
    assert lanes == [2, 3, 2], (
        "an overlapping piece goes on a lane of its own, and the lane is "
        "reused once it is free")
    sfx = _allocate_audio_tracks(
        built["tracks"]["A3"]["clips"], base_track_index=max(lanes) + 1,
        fps=30.0)
    assert all(t > max(lanes) for _, t in sfx), (
        "SFX start above whatever the bed used")


# --------------------------------------------------------------------------
# From test_audio_mix_delivery.py
#
# The planned mix reaches the timeline, and the comps survive the trip.
#
# `audio_mix` (5.02) has always turned each spine block's `music_behavior`
# into a dB target.  Nothing ever set a level: the renderer wrote a cyan
# timeline marker per target and moved on, so a block the spine planned
# `silent` played music at full level in the finished video.
#
# The route that does work is an OTIO round trip - see
# `library/tools/otio_mix.py` for the three format facts it rests on, each
# of which fails silently when got wrong.  The one architectural cost is
# that the import REBUILDS the timeline and Fusion comps do not survive it,
# which is why the round trip runs at placement time and why the last test
# here drives a whole build and asserts the comps are still on it.
#
# Nothing in this file asserts a dB VALUE that `music_behavior.py` owns.
# The vocabulary decides the numbers; this layer only has to deliver them.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import otio_mix
from library.tools.music_behavior import SILENT_LEVEL_DB
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeProject,
    FakeResolve,
    FakeTimeline,
    timeline_item,
)

FPS = 30.0


@pytest.fixture
def _sole_writer():
    """The mix runs inside the render build's exclusive hold in
    production; the fakes here have no instance to contend for, so
    the cursor establishment goes through unguarded-with-a-reason
    rather than queueing behind a live captain for nothing."""
    with assume_sole_writer(
            "test: FakeResolve has no instance to contend for"):
        yield


# ── Fixtures shaped like the real thing ─────────────────────────────

def _clip(path, start, duration, effects=True, volume=None):
    return {
        "OTIO_SCHEMA": "Clip.2",
        "name": Path(path).name,
        "active_media_reference_key": "DEFAULT_MEDIA",
        "media_references": {"DEFAULT_MEDIA": {
            "OTIO_SCHEMA": "ExternalReference.1",
            "target_url": f"file://{path}"}},
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": FPS,
                           "value": float(start)},
            "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": FPS,
                         "value": float(duration)}},
        # Resolve writes the effect with an EMPTY parameter list whenever
        # every value is at its default. That is the whole reason the
        # parameter has to be inserted rather than patched.
        "effects": [{
            "OTIO_SCHEMA": "Effect.1", "name": "", "effect_name": "Resolve Effect",
            "metadata": {"Resolve_OTIO": {
                "Effect Name": otio_mix.VOLUME_EFFECT_NAME,
                "Enabled": True, "Name": "Volume", "Type": 62,
                "Parameters": [volume] if volume else []}}}] if effects else [],
    }


def _gap(duration):
    return {"OTIO_SCHEMA": "Gap.1", "source_range": {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": FPS, "value": 0.0},
        "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": FPS,
                     "value": float(duration)}}}


def _otio(tracks):
    return {"OTIO_SCHEMA": "Timeline.1", "name": "",
            "tracks": {"OTIO_SCHEMA": "Stack.1", "children": [
                {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind,
                 "children": children}
                for name, kind, children in tracks]}}


# What a mix engineer decided on one run, solved into clip gains by step
# 5.02.  Until 2026-09-16 these came from a table in `music_behavior` and
# this file read them from there; there is no such table now, so the
# levels are the fixture's own - which is the right shape for this file
# anyway.  `otio_mix` DELIVERS whatever the automation carries and never
# revises one, and that is the only thing tested here.
#
# `fade_in` carries the level of the block it moves to, because a fade is
# a move between two levels rather than a third one of its own.
LEVELS = {
    "silent": SILENT_LEVEL_DB,
    "background": -19.5,
    "prominent": -7.5,
    "fade_in": -19.5,
}

AUTOMATION = [
    {"spine_block_position": "hook", "timeline_start": 0.0, "timeline_end": 2.4,
     "music_behavior": "silent", "target_level_db": LEVELS["silent"]},
    {"spine_block_position": 1, "timeline_start": 2.4, "timeline_end": 5.4,
     "music_behavior": "fade_in", "target_level_db": LEVELS["fade_in"]},
    {"spine_block_position": 2, "timeline_start": 5.4, "timeline_end": 8.4,
     "music_behavior": "background", "target_level_db": LEVELS["background"]},
    {"spine_block_position": 3, "timeline_start": 8.4, "timeline_end": 18.4,
     "music_behavior": "background", "target_level_db": LEVELS["background"]},
    {"spine_block_position": 4, "timeline_start": 18.4, "timeline_end": 20.9,
     "music_behavior": "prominent", "target_level_db": LEVELS["prominent"]},
    {"spine_block_position": 5, "timeline_start": 20.9, "timeline_end": 30.0,
     "music_behavior": "silent", "target_level_db": LEVELS["silent"]},
]


def _level_at(curve, frame):
    """The dB the curve holds at *frame*, interpolated as Resolve does."""
    keys = sorted(curve)
    if frame <= keys[0]:
        return curve[keys[0]]
    if frame >= keys[-1]:
        return curve[keys[-1]]
    for low, high in zip(keys, keys[1:]):
        if low <= frame <= high:
            span = high - low
            if span == 0:
                return curve[high]
            t = (frame - low) / span
            return curve[low] + t * (curve[high] - curve[low])
    raise AssertionError(frame)


# ── The curve carries what the plan decided ─────────────────────────

def _curve():
    return otio_mix.music_curve(
        AUTOMATION, fps=FPS, clip_start_frame=0, clip_frame_count=900,
        fade_seconds=1.0)


@pytest.mark.usefixtures("_sole_writer")
def test_every_block_holds_its_planned_level_in_its_middle():
    """A plateau at the planned dB in every block - which is what makes
    the plan measurable in a render rather than merely present."""
    curve = _curve()
    for entry in AUTOMATION:
        middle = int(round(((entry["timeline_start"] + entry["timeline_end"])
                            / 2.0) * FPS))
        assert _level_at(curve, middle) == pytest.approx(
            entry["target_level_db"], abs=0.01), entry["spine_block_position"]


@pytest.mark.usefixtures("_sole_writer")
def test_a_planned_silence_is_silent_in_the_middle_of_its_block():
    """The one the captain cares about most. `silent` is a decision, and
    -96 dB is inside Resolve's own parameter bounds, so it needs no
    special case - only delivering."""
    curve = _curve()
    assert _level_at(curve, 15) == LEVELS["silent"]      # 0.5s
    assert _level_at(curve, 750) == LEVELS["silent"]     # 25.0s


@pytest.mark.usefixtures("_sole_writer")
def test_runs_of_one_level_become_one_plateau():
    """Two `background` blocks in a row are one plateau, not two with a
    pointless ramp between them."""
    curve = _curve()
    seam = int(8.4 * FPS)      # where the two background blocks meet
    near_seam = [f for f in curve if abs(f - seam) <= FPS]
    assert near_seam == [], f"a ramp at a seam between equal levels: {near_seam}"
    assert _level_at(curve, seam) == LEVELS["background"]


@pytest.mark.usefixtures("_sole_writer")
def test_frames_are_measured_from_the_clip_not_the_timeline():
    """Measured with two renders (see otio_mix's module docstring): a key
    lands relative to where the clip STARTS, so a bed placed late must
    have the whole curve shifted back by that much."""
    late = otio_mix.music_curve(
        AUTOMATION, fps=FPS, clip_start_frame=150, clip_frame_count=900,
        fade_seconds=1.0)
    early = _curve()
    shifted = {f - 150 for f in late if 0 < f < 899}
    assert shifted <= set(early) | {f - 150 for f in late}
    assert _level_at(late, int(18.4 * FPS) - 150 + 15) == LEVELS["prominent"]


@pytest.mark.usefixtures("_sole_writer")
def test_a_short_block_still_keeps_a_plateau():
    """The fade may not eat a block whole; a level with no plateau is a
    level nothing can measure."""
    tight = [
        {"timeline_start": 0.0, "timeline_end": 0.4, "target_level_db": -96},
        {"timeline_start": 0.4, "timeline_end": 0.8, "target_level_db": -6},
    ]
    curve = otio_mix.music_curve(tight, fps=FPS, clip_start_frame=0,
                                 clip_frame_count=24, fade_seconds=1.0)
    assert _level_at(curve, 1) == -96
    assert _level_at(curve, 22) == -6


# ── The parameter Resolve reads ─────────────────────────────────────

@pytest.mark.usefixtures("_sole_writer")
def test_the_parameter_is_inserted_not_patched():
    """Resolve exports `"Parameters": []` for an untouched clip, so a
    patcher looking for an existing volume entry changes nothing and the
    render comes back at full level."""
    otio = _otio([("Music", "Audio", [_clip("/m.wav", 0, 300)])])
    before = otio["tracks"]["children"][0]["children"][0]
    assert before["effects"][0]["metadata"]["Resolve_OTIO"]["Parameters"] == []

    otio_mix.apply_mix(otio, [{"role": "music", "source_file": "/m.wav",
                               "start_frame": 0, "level_db": -18.0,
                               "keyframes": {}, "label": "bed"}])
    written = before["effects"][0]["metadata"]["Resolve_OTIO"]["Parameters"]
    assert [p["Parameter ID"] for p in written] == [otio_mix.VOLUME_PARAMETER_ID]
    assert written[0]["Parameter Value"] == -18.0


@pytest.mark.usefixtures("_sole_writer")
def test_the_level_is_db_with_no_conversion():
    parameter = otio_mix.volume_parameter(-18.0)
    assert parameter["Parameter Value"] == -18.0
    assert parameter["minValue"] == otio_mix.MIN_VOLUME_DB
    assert parameter["maxValue"] == otio_mix.MAX_VOLUME_DB


@pytest.mark.usefixtures("_sole_writer")
def test_a_level_outside_the_bounds_is_clamped_not_dropped():
    assert otio_mix.volume_parameter(-500.0)["Parameter Value"] == otio_mix.MIN_VOLUME_DB
    assert otio_mix.volume_parameter(500.0)["Parameter Value"] == otio_mix.MAX_VOLUME_DB


# ── Matching a plan to a placed clip ────────────────────────────────

MANIFEST = {
    "project": {"name": "Test_Edit", "resolution": [1080, 1920],
                "frame_rate": 30, "duration_seconds": 30.0},
    "tracks": {
        "A2": {"clips": [{"source_file": "/m.wav", "timeline_in": 0.0,
                          "timeline_out": 30.0, "label": "bed"}]},
        "A3": {"clips": [
            {"source_file": "/whoosh.mp3", "timeline_in_frame": 72,
             "timeline_out_frame": 80, "volume_db": -18, "label": "sfx_001"},
            {"source_file": "/click.wav", "timeline_in_frame": 251,
             "timeline_out_frame": 254, "volume_db": -12, "label": "sfx_002"}]},
    },
    "audio_mix": {
        "music_automation": AUTOMATION,
        "track_levels": {"A2_music": {"fade_duration_seconds": 1.0}},
        "master_limiter": {"enabled": True, "threshold_db": -1.0},
    },
}


@pytest.mark.usefixtures("_sole_writer")
def test_sfx_volume_db_now_has_a_route():
    """`SetProperty("Volume", ...)` returns False on every audio item, so
    per-clip SFX volume reached nothing at all until this route existed.
    A target for it is the whole fix."""
    targets = otio_mix.mix_targets(MANIFEST, fps=FPS)
    assert {t["source_file"] for t in targets if t["role"] == "sfx"} == \
        {"/whoosh.mp3", "/click.wav"}


@pytest.mark.usefixtures("_sole_writer")
def test_a_clip_the_allocator_moved_to_another_track_is_still_matched():
    """Overlapping SFX are spread across A3, A4, A5 by the timeline
    builder. A target names a FILE and a FRAME, so it follows."""
    otio = _otio([
        ("Music", "Audio", [_clip("/m.wav", 0, 900)]),
        ("SFX-1", "Audio", [_gap(72), _clip("/whoosh.mp3", 16, 8)]),
        ("SFX-2", "Audio", [_gap(251), _clip("/click.wav", 0, 3)]),
    ])
    result = otio_mix.apply_mix(otio, otio_mix.mix_targets(MANIFEST, fps=FPS))
    assert result["unmatched"] == []
    assert len(result["applied"]) == 3


@pytest.mark.usefixtures("_sole_writer")
def test_a_target_that_matches_nothing_is_reported_not_dropped():
    """A level nobody applied is the defect this whole route exists to
    remove; a silent miss would reproduce it one layer down."""
    otio = _otio([("Music", "Audio", [_clip("/m.wav", 0, 900)])])
    result = otio_mix.apply_mix(otio, otio_mix.mix_targets(MANIFEST, fps=FPS))
    assert [t["label"] for t in result["unmatched"]] == ["sfx_001", "sfx_002"]


@pytest.mark.usefixtures("_sole_writer")
def test_the_level_is_read_back_off_the_timeline():
    otio = _otio([("Music", "Audio", [_clip("/m.wav", 0, 900)])])
    targets = [t for t in otio_mix.mix_targets(MANIFEST, fps=FPS)
               if t["role"] == "music"]
    applied = otio_mix.apply_mix(otio, targets)["applied"]
    assert otio_mix.verify(otio, applied) == []

    levels = otio_mix.read_levels(otio)
    assert len(levels) == 1
    assert levels[0]["keyframes"] == {int(f): v
                                      for f, v in targets[0]["keyframes"].items()}


@pytest.mark.usefixtures("_sole_writer")
def test_resolve_float_rounding_does_not_false_alarm_keyframe_verification():
    """Resolve can serialize the same dB keyframe as a nearby float.

    Exact dictionary equality reported "86 keyframes on the timeline, 86
    planned" for a curve whose frames matched and whose values differed only
    below the mix's existing 0.05 dB readback tolerance.
    """
    actual = otio_mix.volume_parameter(
        0.0, {10: -18.299999999999997, 20: -24.0})
    otio = _otio([("Music", "Audio", [
        _clip("/m.wav", 0, 900, volume=actual)])])
    target = {
        "source_file": "/m.wav",
        "start_frame": 0,
        "matched_start_frame": 0,
        "level_db": 0.0,
        "keyframes": {10: -18.3, 20: -24.0},
        "label": "background_music",
    }

    assert otio_mix.verify(otio, [target]) == []

    actual["Key Frames"]["10"]["Value"] = -18.36
    assert "keyframes on the timeline differ" in otio_mix.verify(
        otio, [target])[0]


# ── The traps that answer None ──────────────────────────────────────

@pytest.mark.usefixtures("_sole_writer")
def test_missing_media_is_named_before_the_import_can_swallow_it(tmp_path):
    """`ImportTimelineFromFile` returns None for the whole timeline when
    one referenced file is gone, and says nothing about which."""
    present = tmp_path / "here.wav"
    present.write_bytes(b"x")
    otio = _otio([("Music", "Audio", [
        _clip(str(present), 0, 300), _clip("/gone/missing.mov", 0, 300)])])
    assert otio_mix.unresolvable_media(otio) == ["/gone/missing.mov"]


# ── The round trip, driven against a fake Resolve ───────────────────

def _levels(timeline, tmp_path):
    """The levels the timeline carries, read off its own OTIO export."""
    path = tmp_path / f"{timeline.GetName()}.levels.otio"
    assert timeline.Export(str(path), FakeResolve.EXPORT_OTIO)
    return otio_mix.read_levels(otio_mix.load(str(path)))


def _fake_setup(tmp_path):
    tl = FakeTimeline("Test_Edit", frame_rate=int(FPS), video=[[
        timeline_item("a.mov", 0, 900, path="/a.mov")]], audio=[
        [],
        [timeline_item("m.wav", 0, 900, path="/m.wav")],
        [timeline_item("whoosh.mp3", 72, 80, path="/whoosh.mp3"),
         timeline_item("click.wav", 251, 254, path="/click.wav")],
    ])
    project = FakeProject([tl], current=tl)
    return FakeResolve(project), project, project.GetMediaPool(), tl


@pytest.mark.usefixtures("_sole_writer")
def test_the_round_trip_puts_every_planned_level_on_the_new_timeline(tmp_path):
    from library.tools.execution.deliver_audio_mix import deliver_mix
    resolve, project, pool, timeline = _fake_setup(tmp_path)
    with patch("library.tools.otio_mix.os.path.exists",
               side_effect=_exists_with_fake_media):
        report = deliver_mix(resolve, project, pool, timeline, MANIFEST,
                             fps=FPS, project_folder=str(tmp_path))
    assert report["delivered"], report["reason"]
    assert report["unmatched"] == []
    assert report["complaints"] == []
    assert report["picture_fingerprint_before"]
    assert report["picture_fingerprint_before"] == report["picture_fingerprint_after"]
    assert report["picture_differences"] == []
    assert len(report["applied"]) == 3
    assert report["timeline"] is not timeline
    assert report["timeline"].GetName() == "Test_Edit"
    assert project.GetCurrentTimeline() is report["timeline"]

    levels = {entry["source_file"]: entry
              for entry in _levels(report["timeline"], tmp_path)}
    assert levels["/whoosh.mp3"]["level_db"] == -18.0
    assert levels["/m.wav"]["keyframes"], "the bed carries a curve"


@pytest.mark.usefixtures("_sole_writer")
def test_picture_frame_drift_refuses_import_and_keeps_original_timeline(
        tmp_path, monkeypatch):
    from library.tools.execution.deliver_audio_mix import deliver_mix

    resolve, project, pool, timeline = _fake_setup(tmp_path)
    import_timeline = pool.ImportTimelineFromFile

    def import_with_source_drift(path, options=None):
        replacement = import_timeline(path, options)
        picture = replacement.GetItemListInTrack("video", 1)[0]
        picture._source_end_frame -= 1
        return replacement

    monkeypatch.setattr(pool, "ImportTimelineFromFile", import_with_source_drift)
    with patch("library.tools.otio_mix.os.path.exists",
               side_effect=_exists_with_fake_media):
        report = deliver_mix(resolve, project, pool, timeline, MANIFEST,
                             fps=FPS, project_folder=str(tmp_path))

    assert not report["delivered"]
    assert "source_end" in report["reason"]
    assert len(report["picture_differences"]) == 1
    assert report["timeline"] is timeline
    assert timeline.GetName() == "Test_Edit"
    assert project.GetCurrentTimeline() is timeline
    assert project.GetTimelineCount() == 1


@pytest.mark.usefixtures("_sole_writer")
def test_missing_media_refuses_the_trip_and_keeps_the_timeline(tmp_path):
    from library.tools.execution.deliver_audio_mix import deliver_mix
    resolve, project, pool, timeline = _fake_setup(tmp_path)
    report = deliver_mix(resolve, project, pool, timeline, MANIFEST,
                         fps=FPS, project_folder=str(tmp_path))
    assert not report["delivered"]
    assert "not on disk" in report["reason"]
    assert report["timeline"] is timeline
    assert timeline.GetName() == "Test_Edit", "the name was put back"


# ── The order the whole build depends on ────────────────────────────
#
# The OTIO import REBUILDS the timeline. Fusion comps do not survive it,
# so the mix has to be delivered BEFORE the Fusion pass draws anything.
# The renderer already placed every clip before drawing a comp, so this
# costs nothing - but it is now load-bearing, and this is what holds it.
# (The step_6_01_render directory this file once added to sys.path here
# now lives in tests/conftest.py; the bare `import
# resolve_build_timeline` below reaches it by absolute package path.)


def _build_manifest(tmp_path):
    """001's shape, shrunk: two A-roll clips, a bed, one SFX, comps on both."""
    media = {}
    for name in ("a0.mov", "a1.mov", "bed.wav", "whoosh.mp3"):
        path = tmp_path / "raw" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * 1024)
        media[name] = str(path)
    return media, {
        "project": {"name": "Pipeline_Edit", "resolution": [1080, 1920],
                    "frame_rate": 30, "duration_seconds": 30.0},
        "tracks": {
            "V1": {"clips": [
                {"source_file": media["a0.mov"], "label": "a_roll_0",
                 "source_in": 0.0, "source_out": 15.0, "timeline_in": 0.0,
                 "timeline_out": 15.0, "timeline_in_frame": 0},
                {"source_file": media["a1.mov"], "label": "a_roll_1",
                 "source_in": 0.0, "source_out": 15.0, "timeline_in": 15.0,
                 "timeline_out": 30.0, "timeline_in_frame": 450}]},
            "A2": {"clips": [{"source_file": media["bed.wav"], "label": "bed",
                              "source_in": 0.0, "source_out": 30.0,
                              "timeline_in": 0.0, "timeline_out": 30.0}]},
            "A3": {"clips": [{"source_file": media["whoosh.mp3"],
                              "label": "sfx_001", "source_in": 0.0,
                              "timeline_in": 2.4, "timeline_out": 2.7,
                              "timeline_in_frame": 72, "timeline_out_frame": 81,
                              "volume_db": -18}]},
        },
        "audio_mix": {"music_automation": AUTOMATION,
                      "track_levels": {"A2_music": {"fade_duration_seconds": 1.0}},
                      "master_limiter": {"enabled": True, "threshold_db": -1.0}},
        "fusion_effects": {"per_clip": {"a_roll_0": {"glow_gain": 1.4},
                                        "a_roll_1": {"glow_gain": 1.4}},
                           "transitions": []},
    }


def _run_build(tmp_path, manifest, media, configure=None):
    """Drive the real build_timeline against the fake, with the Fusion
    pass modelled as what it is: a subprocess that draws comps on
    whatever timeline is CURRENT when it runs. `configure`, when given,
    is called with (resolve, project) before the build runs, so a test
    can reshape the fake scripting surface."""
    import library.steps.step_6_01_render.resolve_build_timeline as rbt

    project = FakeProject("Pipeline_Edit")
    resolve = FakeResolve(project)

    if configure is not None:
        configure(resolve, project)

    pool = project.GetMediaPool()
    for path in media.values():
        # Single-stream fixtures: the build resolves the speech channel
        # off "Audio Ch", and an unreadable one refuses rather than
        # defaults.
        pool.media_properties[path] = {
            "Frames": "3000", "FPS": "30.0", "Resolution": "1080x1920",
            "Audio Ch": "1"}
    pool.ImportMedia(list(media.values()))

    def _fusion_pass(cmd, **kwargs):
        for item in project.GetCurrentTimeline().GetItemListInTrack("video", 1):
            item.comps.append("Composition 1")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    subprocess_proxy = SimpleNamespace(
        run=_fusion_pass, TimeoutExpired=subprocess.TimeoutExpired)
    with patch.object(rbt, "_connect_resolve", return_value=resolve), \
            patch.object(rbt, "subprocess", subprocess_proxy), \
            patch.object(rbt, "verify_audio", None), \
            patch.object(rbt, "verify_clip_placement", None), \
            patch.object(rbt, "verify_fusion_comps", None), \
            patch.object(rbt, "verify_transitions", None), \
            patch.object(rbt, "run_full_timeline_qa", None), \
            patch.object(rbt, "plan_qa_checks", None):
        result = rbt.build_timeline(
            manifest, project_name="Pipeline_Edit",
            project_folder=str(tmp_path))
    return project, result


@pytest.mark.usefixtures("_sole_writer")
def test_fusion_comps_survive_a_mixed_build(tmp_path):
    """The whole reason the round trip happens at placement time. If it
    ever moves after the Fusion pass, the import discards every comp and
    this fails."""
    media, manifest = _build_manifest(tmp_path)
    project, result = _run_build(tmp_path, manifest, media)

    assert result["audio_mix_delivery"]["delivered"], \
        result["audio_mix_delivery"]["reason"]

    final = project.GetCurrentTimeline()
    comps = [item.comps for item in final.GetItemListInTrack("video", 1)]
    assert comps == [["Composition 1"], ["Composition 1"]], \
        "the Fusion pass ran before the OTIO import, or its comps were lost"


@pytest.mark.usefixtures("_sole_writer")
def test_a_mixed_build_carries_the_levels_and_drops_the_markers(tmp_path):
    media, manifest = _build_manifest(tmp_path)
    project, result = _run_build(tmp_path, manifest, media)
    final = project.GetCurrentTimeline()

    levels = {entry["source_file"]: entry
              for entry in _levels(final, tmp_path)}
    assert levels[media["whoosh.mp3"]]["level_db"] == -18.0
    assert levels[media["bed.wav"]]["keyframes"], "the bed carries a curve"

    names = [m["name"] for m in final.markers.values()]
    assert not [n for n in names if "Target Level" in n or "UNAPPLIED" in n], \
        f"a level was left as a marker: {names}"
    assert any("Master Limiter" in n for n in names), \
        "the master limiter is a bus setting and stays a marker"


@pytest.mark.usefixtures("_sole_writer")
def test_the_build_falls_back_to_markers_and_says_so(tmp_path):
    """A gate that cannot fail is worse than no gate, and so is a
    fallback nobody can see. When the route declines, the markers come
    back AND the run records why."""
    media, manifest = _build_manifest(tmp_path)
    import library.steps.step_6_01_render.resolve_build_timeline as rbt
    with patch.object(rbt, "deliver_mix", return_value={
            "delivered": False, "reason": "Resolve said no",
            "timeline": None, "timeline_name": "", "applied": [],
            "unmatched": [], "complaints": [], "otio_path": "",
            "mixed_otio_path": ""}) as declined:
        project, result = _run_build(tmp_path, manifest, media)
    assert declined.called

    final = project.GetCurrentTimeline()
    names = [m["name"] for m in final.markers.values()]
    assert any("UNAPPLIED target" in n for n in names), names
    assert any("Resolve said no" in w for w in result["warnings"])
    assert result["audio_mix_delivery"]["delivered"] is False


def _limiter_marker_names(project):
    return [m["name"] for m in
            project.GetCurrentTimeline().markers.values()]


@pytest.mark.usefixtures("_sole_writer")
def test_an_unreadable_fairlight_api_still_leaves_a_limiter_marker(tmp_path):
    """D6's cause, not its symptom: the limiter guard judges whether the
    Fairlight route answered, never how a decline is spelled. A build
    whose GetFairlightPresets answers with anything but presets - here a
    RuntimeError, the shape a scripting host without the method raises -
    must fall back to the marker, not fail the build. Only the
    None-call shape (TypeError) was caught; every other shape escaped."""
    media, manifest = _build_manifest(tmp_path)
    threshold_db = -2.5
    manifest["audio_mix"]["master_limiter"]["threshold_db"] = threshold_db

    def _break_the_api(resolve, project):
        def no_such_method():
            raise RuntimeError("no such method on this build")

        resolve.GetFairlightPresets = no_such_method

    project, result = _run_build(
        tmp_path, manifest, media, configure=_break_the_api)

    expected = f"Master Limiter: {threshold_db}dBTP"
    assert expected in _limiter_marker_names(project), \
        "the limiter fell back to nothing instead of a marker"


@pytest.mark.usefixtures("_sole_writer")
def test_a_declined_fairlight_apply_still_leaves_a_limiter_marker(tmp_path):
    """Same guard, second half: the preset is listed but applying it
    declines - here a RuntimeError, the shape a timeline race or a
    foreign build raises (assert_current_timeline's own ResolveRaceError
    escapes a TypeError-only catch the same way). The build must complete
    with the marker, not die inside the guard."""
    media, manifest = _build_manifest(tmp_path)
    threshold_db = -2.5
    manifest["audio_mix"]["master_limiter"]["threshold_db"] = threshold_db

    def _list_then_decline(resolve, project):
        # The key is the protocol name the captain's preset must carry -
        # the same name the guard looks up - so the guard takes the
        # "preset exists" branch and reaches the apply it must survive.
        resolve.GetFairlightPresets = lambda: {"Pipeline_Master_Limiter": {}}

        def declined(preset):
            raise RuntimeError("preset route declined")

        project.ApplyFairlightPresetToCurrentTimeline = declined

    project, result = _run_build(
        tmp_path, manifest, media, configure=_list_then_decline)

    expected = f"Master Limiter: {threshold_db}dBTP"
    assert expected in _limiter_marker_names(project), \
        "a declined apply left no marker behind"
