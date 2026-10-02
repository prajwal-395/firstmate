"""The voiceover spine (P4 lane d): catalogued audio as speech.

A voiceover take the project holds becomes speech the spine can use:
its transcript feeds speech_sequence passages, passages cut from it
become speech blocks sourced from the audio file, B-roll covers their
picture, and the words play from the file on A1. A held music bed
joins music_selection's candidates rather than sitting invisible in
the intake.

Each test names the defect its assertion prevents. Fixtures and temp
dirs only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_3_01_assign_aroll.step import (  # noqa: E402
    assign_a_roll,
)
from library.steps.step_3_03_review_rough_cut.step import (  # noqa: E402
    build_actual_script,
)

SEQ_POST_BRIDGE = (
    REPO_ROOT / "library" / "steps" / "step_2_02_speech_sequence"
    / "post_bridge.py"
)
BROLL_BRIDGE = (
    REPO_ROOT / "library" / "steps" / "step_3_02_select_broll"
    / "bridge.py"
)


def _words(start):
    return [{"word": "hello", "source_start": start,
             "source_end": start + 1.0}]


def _voiceover_block(position=1):
    return {
        "position": position,
        "block_type": "speech",
        "clip_id": "audio_001",
        "source_start": 0.0,
        "source_end": 2.0,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "word_timestamps": _words(0.0),
        "alignment_method": "whisperx",
    }


def _catalog():
    return [
        {"clip_id": "clip_001", "source_file": "/tmp/a.mov",
         "path": "/tmp/a.mov", "duration_seconds": 30.0,
         "width": 1080, "height": 1920, "frame_rate": 30.0,
         "rotation": 0},
    ]


def _audio_catalog(path="/tmp/vo.wav"):
    return [
        {"audio_id": "audio_001", "source_file": path,
         "path": path, "filename": "vo.wav",
         "duration_seconds": 30.0},
    ]


# (V1 membership and cutaway slots for a voiceover block are pinned in
# tests/unit/audio/test_audio_led_spine.py beside the picture and music cases.)

# ── assign_aroll places the words, not the picture ────────────────

def test_assign_a_roll_refuses_what_a_sound_only_file_cannot_be():
    """A picture block naming an audio id has no picture to cut; a
    voiceover block on a run whose catalog predates the audio intake
    names that lack rather than reading as missing footage."""
    picture = dict(_voiceover_block(), block_type="picture",
                   word_timestamps=[], alignment_method=None)
    for block, audio_catalog, fragment in (
            (picture, _audio_catalog(), "no picture to cut"),
            (_voiceover_block(), [], "audio catalog")):
        with pytest.raises(ValueError, match=fragment):
            assign_a_roll({"structure": [block]}, _catalog(),
                          1080, 1920, 30.0, audio_catalog=audio_catalog)


# ── The review hears the narration ───────────────────────────────

def test_actual_script_includes_voiceover_words():
    """Check 5's script carries the voiceover's words.

    Defect prevented: the review rebuilt its script off A-roll
    assignments alone, so a voiceover-led cut was judged on a script
    with the narration missing.
    """
    spine = {"structure": [_voiceover_block()]}
    script = build_actual_script(
        [], spine,
        voiceover_assignments=[{
            "spine_block_position": 1, "block_type": "speech",
            "audio_id": "audio_001", "audio_in": 0.0, "audio_out": 2.0,
            "timeline_start": 0.0,
        }])
    assert script["full_text"] == "hello"
    assert script["blocks"][0]["clip_id"] == "audio_001"


# ── A failed voiceover index refuses by name ─────────────────────

def test_errored_audio_index_refuses_through_main(tmp_path):
    """The post_bridge main reads the `audio_indices` edge.

    Defect prevented: the edge carried but never read - the survey's
    reader going quiet while the failure it names still lands late.
    """
    ti_dir = tmp_path / "temporal_index"
    ti_dir.mkdir()
    payload = {
        "speech_sequence": {"body_sequence": [{
            "clip_id": "audio_001", "source_start": 0.0,
            "source_end": 2.0, "text": "hello world",
        }]},
        "temporal_index": {"index_dir": str(ti_dir)},
        "audio_indices": [{"audio_id": "audio_001",
                           "error": "whisper blew up"}],
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get(
        "PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(SEQ_POST_BRIDGE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    assert proc.returncode != 0
    assert "audio_001" in proc.stderr or "audio_001" in proc.stdout


# ── A held bed joins the choosing candidates ─────────────────────

def test_project_audio_joins_the_candidates_labelled_for_what_it_is(
        tmp_path):
    """A bed the project holds is offered to the choice (the bridge once
    catalogued only the shared library); a voiceover take joins marked
    as speech, never hidden (a second, quieter chooser) or offered bare."""
    from library.steps.step_2_04_music_selection.bridge import (
        catalogue_project_audio,
    )
    bed = tmp_path / "bed.wav"
    bed.write_bytes(b"\x00" * 64)
    candidates = catalogue_project_audio(
        {"project_folder": "",
         "audio_catalog": [{
             "audio_id": "audio_001", "path": str(bed),
             "filename": "bed.wav", "duration_seconds": 120.0}]},
        60.0, 600.0)
    assert len(candidates) == 1
    assert candidates[0]["audio_path"] == str(bed)
    assert candidates[0]["source"] == "project"
    assert "no transcribed speech" in candidates[0]["catalog_note"]

    take = tmp_path / "narration.wav"
    take.write_bytes(b"\x00" * 64)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pipeline_data.json").write_text(json.dumps({"step_outputs": {
        "catalog": {"audio_catalog": [{
            "audio_id": "audio_001", "path": str(take),
            "filename": "narration.wav", "duration_seconds": 120.0}]},
        "temporal_index": {"audio_indices": [{
            "audio_id": "audio_001", "total_words": 200}]},
    }}))
    candidates = catalogue_project_audio(
        {"project_folder": str(project), "audio_catalog": None},
        60.0, 600.0)
    assert len(candidates) == 1
    assert "voiceover take" in candidates[0]["catalog_note"]


# ── One block, one picture ───────────────────────────────────────

def test_broll_bridge_refuses_a_doubly_claimed_block():
    """A block with video and voiceover assignments refuses.

    Defect prevented: one block playing footage and
    voiceover-over-B-roll at once, caught only as a V2 overlap
    downstream.
    """
    payload = {
        "clip_catalog": [],
        "a_roll_assignments": [{"spine_block_position": 1,
                                "video_segments": []}],
        "voiceover_assignments": [{"spine_block_position": 1}],
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get(
        "PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BROLL_BRIDGE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    assert proc.returncode != 0
    assert "both" in proc.stdout


# ── The shape declaration reads where it changes behaviour ──────

def test_the_declared_shape_decides_whether_speech_is_expected(tmp_path):
    """Undeclared stays speech-led; music and picture-led do not expect it.

    Defect prevented: a music/montage project asked for speech it
    never planned to carry - or an undeclared project silently
    re-read as speechless, changing every run that declared nothing.
    """
    from library.tools.project_shape import declared_shape, expects_speech
    assert expects_speech("") is True
    assert expects_speech("speech") is True
    assert expects_speech("both") is True
    assert expects_speech("music") is False
    assert expects_speech("picture-led") is False

    project = tmp_path / "p"
    project.mkdir()
    assert declared_shape(str(project)) == ""
    (project / "project.yaml").write_text("source:\n  shape: music\n")
    assert declared_shape(str(project)) == "music"
    (project / "project.yaml").write_text("source:\n  shape: podcast\n")
    assert declared_shape(str(project)) == ""


# ── Through compile: the narration lands on A1 ───────────────────

def _compile_inputs(spine, a_roll, voiceover, b_roll, catalog):
    return {
        "a_roll_assignments": a_roll,
        "voiceover_assignments": voiceover,
        "b_roll_assignments": b_roll,
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "subtitle_overlay": {"available": False, "segments": []},
        "transition_spec": [],
        "enhancement_spec": [],
        "color_grade_spec": {},
        "audio_mix_spec": {},
        "music_selection": {},
        "audio_spine": dict(
            spine, total_estimated_duration_seconds=4.0,
            frame_rate=30.0),
        "timed_spine": dict(
            spine, total_estimated_duration_seconds=4.0,
            frame_rate=30.0),
        "clip_catalog": catalog,
        "semantic_analysis": {"semantic_analysis_documents": [
            {"clip_id": "clip_001",
             "analysis": {"motion": "Locked off.", "scene": "A lake."},
             "assessment": {"clip_type": "b-roll",
                            "keywords": ["lake"]}},
        ]},
        "project_fps": 30.0,
    }


def _compile(inputs):
    from unittest.mock import patch
    from library.steps.step_5_04_compile_manifest import step as CM
    with patch.object(
            CM, "load",
            side_effect=lambda out_dir, filename: inputs):
        return CM.compile_manifest("dummy")


def test_voiceover_spine_compiles_narration_on_a1(tmp_path):
    """A voiceover block compiles: B-roll is the picture, the take is A1.

    Defect prevented: the A1/V1 parity check failing every voiceover
    run (A1 carries words no V1 clip mirrors), and the narration
    missing from the manifest because A1 only ever mirrored V1.
    """
    from library.steps.step_3_01_assign_aroll.step import assign_a_roll

    a = tmp_path / "a.mov"
    a.write_bytes(b"\x00" * 64)
    vo = tmp_path / "vo.wav"
    vo.write_bytes(b"\x00" * 64)
    catalog = [{"clip_id": "clip_001", "source_file": str(a),
                "path": str(a), "duration_seconds": 30.0,
                "width": 1080, "height": 1920, "frame_rate": 30.0,
                "rotation": 0}]
    spine = {"structure": [dict(
        _voiceover_block(), timeline_start=0.0, timeline_end=2.0)]}
    placed = assign_a_roll(dict(spine), catalog, 1080, 1920, 30.0,
                           audio_catalog=_audio_catalog(str(vo)))
    broll = [{"spine_block_position": 1, "block_type": "speech",
              "clip_id": "clip_001", "source_file": str(a),
              "video_in": 0.0, "video_out": 2.0,
              "duration_seconds": 2.0,
              "timeline_start": 0.0, "timeline_end": 2.0,
              "coverage_shortfall_seconds": 0.0, "needs_conform": False,
              "selection_rationale": "trace",
              "window_basis": "trace", "video_only": True}]
    manifest = _compile(_compile_inputs(
        spine, placed["a_roll_assignments"],
        placed["voiceover_assignments"], broll, catalog))
    assert manifest["tracks"]["V1"]["clips"] == []
    assert len(manifest["tracks"]["V2"]["clips"]) == 1
    a1 = manifest["tracks"]["A1"]["clips"]
    assert len(a1) == 1
    assert a1[0]["source_file"] == str(vo)
    assert a1[0].get("voiceover") is True
    assert (a1[0]["timeline_in"], a1[0]["timeline_out"]) == (0.0, 2.0)
    vo_row = placed["voiceover_assignments"][0]
    assert (vo_row["audio_id"], vo_row["audio_in"], vo_row["audio_out"]) == (
        "audio_001", 0.0, 2.0)
