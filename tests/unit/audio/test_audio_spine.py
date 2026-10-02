"""The audio-led spine (P4 lane a): speech is optional.

Before this lane, every seam below assumed speech: a spine block with a
clip but no words had no vocabulary (and assign_aroll skipped it, leaving
a hole V1 never filled), mesh_spine refused to run without a
speech_sequence, and an empty body failed the duration zone. Each test
names the defect its assertion prevents. Fixtures and temp dirs only.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
import copy
from unittest.mock import MagicMock, patch
from library.tools.footage_identity import (
    enumerate_audio,
    enumerate_footage,
    fingerprints_for,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_2_05_mesh_spine.post_bridge import (  # noqa: E402
    enrich_spine,
)
from library.steps.step_3_01_assign_aroll.step import (  # noqa: E402
    assign_a_roll,
)
from library.steps.step_3_02_select_broll.bridge import (  # noqa: E402
    cutaway_slot_seconds,
)
from library.steps.step_3_03_review_rough_cut.step import (  # noqa: E402
    build_actual_script,
)
from library.tools.spine_contract import (  # noqa: E402
    SpineContractError,
    validate_passage_coverage,
    validate_spine_blocks,
)
from library.tools.transition_carriers import (  # noqa: E402
    block_reaches_v1,
)

MESH_POST_BRIDGE = (
    REPO_ROOT / "library" / "steps" / "step_2_05_mesh_spine" / "post_bridge.py"
)
SEQ_POST_BRIDGE = (
    REPO_ROOT / "library" / "steps" / "step_2_02_speech_sequence"
    / "post_bridge.py"
)


def _words(start):
    return [{"word": "hello", "source_start": start,
             "source_end": start + 1.0}]


def _speech_block(position=1, clip_id="clip_001"):
    return {
        "position": position,
        "block_type": "speech",
        "clip_id": clip_id,
        "source_start": 0.0,
        "source_end": 2.0,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "word_timestamps": _words(0.0),
        "alignment_method": "whisperx",
    }


def _picture_block(position=2, clip_id="clip_002"):
    return {
        "position": position,
        "block_type": "picture",
        "clip_id": clip_id,
        "source_start": 5.0,
        "source_end": 8.0,
        "timeline_start": 2.0,
        "timeline_end": 5.0,
        "word_timestamps": [],
        "alignment_method": None,
    }


def _music_block(position=3):
    return {
        "position": position,
        "block_type": "music",
        "clip_id": None,
        "source_start": None,
        "source_end": None,
        "timeline_start": 5.0,
        "timeline_end": 9.0,
        "word_timestamps": [],
        "alignment_method": None,
        "content": {"track": "track_01",
                    "source_in": 10.0, "source_out": 14.0},
    }


# ── The contract admits the new vocabulary ──────────────────────────

def test_the_contract_admits_picture_and_music_blocks_and_refuses_holes():
    """A picture block naming its clip span and a music block naming its
    track are legal spine blocks. Each malformed shape refuses by name:
    a clipless picture block is a hole wearing a name; words on a
    picture-led moment read two sources for one moment; a music span
    with no track is a plan nothing can play; and a block that plays a
    clip is never held on black (the declared hole would excuse the very
    range the coverage assertion should see filled)."""
    validate_spine_blocks([_speech_block(), _picture_block()])
    validate_spine_blocks([_music_block()])

    clipless = _picture_block(clip_id=None)
    worded = dict(_picture_block(), word_timestamps=_words(5.0),
                  alignment_method="whisperx")
    trackless = dict(_music_block(),
                     content={"source_in": 10.0, "source_out": 14.0})
    black = dict(_picture_block(), intentional_black_beat=True,
                 black_beat_reason="hold before the reveal")
    for block, fragment in ((clipless, "no clip_id"),
                            (worded, "word_timestamps"),
                            (trackless, "no track"),
                            (black, "never held on black")):
        with pytest.raises(SpineContractError) as excinfo:
            validate_spine_blocks([block])
        assert fragment in str(excinfo.value), fragment


def test_empty_body_covers_zero_of_zero():
    """No speech passages and no speech blocks is covered, not lost.

    Defect prevented: the coverage check reading an empty body as
    content loss and refusing every speechless spine.
    """
    validate_passage_coverage([], 0)
    validate_passage_coverage([_music_block()], 0)


# ── mesh_spine runs without speech ───────────────────────────────────

def _passage(clip_id, start, end, text):
    return {
        "clip_id": clip_id,
        "source_start": start,
        "source_end": end,
        "text": text,
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": text.split()[0], "source_start": start,
             "source_end": start + 1.0}
        ],
    }


def test_enrich_spine_with_no_speech_sequence_plans_music_and_picture():
    """mesh_spine enriches a speechless spine on an empty body.

    Defect prevented: enrich_spine requiring speech CONTENT, so a
    music/picture-led run died before planning.
    """
    spine = {"structure": [
        {"position": 1, "block_type": "picture",
         "duration_seconds": 3.0,
         "content": {"clip_id": "clip_002",
                     "source_start": 5.0, "source_end": 8.0}},
        {"position": 2, "block_type": "music", "duration_seconds": 4.0,
         "content": {"track": "track_01",
                     "source_in": 10.0, "source_out": 14.0}},
    ]}
    result = enrich_spine(spine, None, {}, {})
    blocks = result["audio_spine"]["structure"]
    assert len(blocks) == 2
    assert blocks[0]["clip_id"] == "clip_002"
    assert blocks[0]["word_timestamps"] == []
    assert blocks[1]["clip_id"] is None


def test_mesh_spine_main_runs_on_an_empty_body(tmp_path):
    """The step's contract is a present sequence with zero passages.

    Defect prevented: main() demanding speech CONTENT (a non-empty
    body) while the manifest demands only the sequence - the edge is
    hard, the body is not.
    """
    payload = {
        "structure": [
            {"position": 1, "block_type": "music",
             "duration_seconds": 4.0, "content": {}},
        ],
        "speech_sequence": {"body_sequence": [],
                            "excluded_passages": []},
        "music_selection": {},
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get(
        "PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(MESH_POST_BRIDGE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert len(out["audio_spine"]["structure"]) == 1


def test_speech_sequence_main_accepts_an_empty_body(tmp_path):
    """An empty body is a decision, not a missing duration.

    Defect prevented: the zone check refusing a body of zero passages,
    which forced speech into every project.
    """
    ti_dir = tmp_path / "temporal_index"
    ti_dir.mkdir()
    payload = {
        "speech_sequence": {"body_sequence": [],
                            "excluded_passages": [
                                {"reason_excluded":
                                 "picture leads this piece"}]},
        "temporal_index": {"index_dir": str(ti_dir)},
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
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["speech_sequence"]["body_sequence"] == []


# ── Downstream of the spine reads the new types ─────────────────────

def _catalog():
    return [
        {"clip_id": "clip_001", "source_file": "/tmp/a.mov",
         "duration_seconds": 30.0, "width": 1080, "height": 1920,
         "frame_rate": 30.0, "rotation": 0},
        {"clip_id": "clip_002", "source_file": "/tmp/b.mov",
         "duration_seconds": 30.0, "width": 1080, "height": 1920,
         "frame_rate": 30.0, "rotation": 0},
    ]


def test_picture_block_reaches_v1_and_music_does_not():
    """V1 membership follows the picture, not the speech.

    Defect prevented: compile_manifest and the transition planner
    disagreeing about picture cuts (no V1 clip ends there) or drawing
    transitions off a music block (no V1 clip at all).
    """
    assert block_reaches_v1(_picture_block()) is True
    assert block_reaches_v1(_music_block()) is False
    assert block_reaches_v1(_speech_block()) is True
    # A speech block sourced from a catalogued voiceover (`audio_001`)
    # plays from the audio file on A1: nothing reaches V1.
    assert block_reaches_v1(_speech_block(clip_id="audio_001")) is False


def test_cutaway_slots_skip_blocks_that_already_have_picture():
    """Candidate windows are sized for blocks that need cover.

    Defect prevented: demanding B-roll cover for a picture block that
    already plays its own clip - non-A-roll read as non-speech.
    """
    spine = {"structure": [_speech_block(), _picture_block(),
                           _music_block()]}
    slots = cutaway_slot_seconds(spine)
    assert slots == [4.0]
    # A voiceover speech block is a slot its picture must cover, or the
    # timeline carries black where the narration plays.
    voiceover = {"structure": [_speech_block(clip_id="audio_001")]}
    assert cutaway_slot_seconds(voiceover) == [2.0]


def test_actual_script_is_empty_for_a_speechless_cut():
    """A cut with no A-roll hears nothing the transcript could name.

    Defect prevented: the review rebuilding a monologue that was never
    spoken instead of seeing the empty script its no-speech section
    judges.
    """
    script = build_actual_script([], {"structure": [_music_block()]})
    assert script["full_text"] == ""
    assert script["blocks"] == []


# ── Through compile: both shapes reach a manifest ────────────────────

def _media_catalog(tmp_path):
    a = tmp_path / "a.mov"
    b = tmp_path / "b.mov"
    a.write_bytes(b"\x00" * 64)
    b.write_bytes(b"\x00" * 64)
    return [
        {"clip_id": "clip_001", "source_file": str(a),
         "path": str(a), "duration_seconds": 30.0,
         "width": 1080, "height": 1920, "frame_rate": 30.0,
         "rotation": 0},
        {"clip_id": "clip_002", "source_file": str(b),
         "path": str(b), "duration_seconds": 30.0,
         "width": 1080, "height": 1920, "frame_rate": 30.0,
         "rotation": 0},
    ]


def _semantic():
    def doc(cid, scene, kind):
        return {"clip_id": cid,
                "analysis": {"motion": "Locked off.", "scene": scene},
                "assessment": {"clip_type": kind,
                               "keywords": [kind]}}
    return {"semantic_analysis_documents": [
        doc("clip_001", "A sunny plaza.", "b-roll"),
        doc("clip_002", "A lake.", "a-roll"),
    ]}


def _broll_cover(pos, btype, clip_id, catalog, src, tl):
    src_file = next(
        c["source_file"] for c in catalog if c["clip_id"] == clip_id)
    return {"spine_block_position": pos, "block_type": btype,
            "clip_id": clip_id, "source_file": src_file,
            "video_in": src[0], "video_out": src[1],
            "duration_seconds": round(src[1] - src[0], 3),
            "timeline_start": tl[0], "timeline_end": tl[1],
            "coverage_shortfall_seconds": 0.0, "needs_conform": False,
            "selection_rationale": "trace",
            "window_basis": "trace", "video_only": True}


def _compile_inputs(spine, a_roll, b_roll, catalog):
    return {
        "a_roll_assignments": a_roll,
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
            spine, total_estimated_duration_seconds=8.0,
            frame_rate=30.0),
        "timed_spine": dict(
            spine, total_estimated_duration_seconds=8.0,
            frame_rate=30.0),
        "clip_catalog": catalog,
        "semantic_analysis": _semantic(),
        "project_fps": 30.0,
    }


def _compile(inputs):
    from unittest.mock import patch
    from library.steps.step_5_04_compile_manifest import step as CM
    with patch.object(
            CM, "load",
            side_effect=lambda out_dir, filename: inputs):
        return CM.compile_manifest("dummy")


def test_music_only_spine_compiles_to_broll_over_silence(tmp_path):
    """A music-only spine reaches a manifest: B-roll is the picture.

    Defect prevented: no step between the spine and the manifest
    accepting a cut with no V1, so a music-led shape died at compile.
    """
    catalog = _media_catalog(tmp_path)
    spine = {
        "structure": [
            dict(_music_block(position=1),
                 timeline_start=0.0, timeline_end=4.0),
            dict(_music_block(position=2),
                 timeline_start=4.0, timeline_end=8.0),
        ],
    }
    inputs = _compile_inputs(
        spine, [],
        [_broll_cover(1, "music", "clip_001", catalog,
                      (0.0, 4.0), (0.0, 4.0)),
         _broll_cover(2, "music", "clip_002", catalog,
                      (0.0, 4.0), (4.0, 8.0))],
        catalog)
    manifest = _compile(inputs)
    assert len(manifest["tracks"]["V1"]["clips"]) == 0
    assert len(manifest["tracks"]["V2"]["clips"]) == 2


def test_picture_only_spine_compiles_with_v1_from_picture(tmp_path):
    """A picture-led spine reaches a manifest: its span is V1.

    Defect prevented: the fabricated-range heuristic refusing a
    picture cut for whole-second bounds - speech aligns to words,
    a picture span is chosen, and the two were judged by one rule.
    """
    catalog = _media_catalog(tmp_path)
    spine = {
        "structure": [
            dict(_picture_block(position=1),
                 timeline_start=0.0, timeline_end=3.0),
            dict(_music_block(position=2),
                 timeline_start=3.0, timeline_end=6.0),
        ],
    }
    a_roll = assign_a_roll(
        dict(spine, total_estimated_duration_seconds=6.0),
        catalog, 1080, 1920, 30.0)["a_roll_assignments"]
    inputs = _compile_inputs(
        spine, a_roll,
        [_broll_cover(2, "music", "clip_001", catalog,
                      (0.0, 3.0), (3.0, 6.0))],
        catalog)
    manifest = _compile(inputs)
    v1 = manifest["tracks"]["V1"]["clips"]
    assert len(v1) == 1
    assert v1[0]["label"].startswith("picture_")
    assert v1[0].get("picture_led") is True
    assert v1[0]["source_file"] == catalog[1]["source_file"]


def test_picture_v1_clips_are_exempt_from_the_fabricated_range_rule():
    """Whole seconds are legitimate on a chosen span.

    Defect prevented: the validator failing a correct picture cut
    because speech cut to word boundaries never lands on whole
    seconds - the exemption the marker above carries.
    """
    from library.tools.manifest_validator import (
        validate_manifest_semantics,
    )
    clip = {"label": "picture_1_seg0", "source_file": "/tmp/x.mov",
            "source_in": 5.0, "source_out": 8.0,
            "timeline_in": 0.0, "timeline_end": 3.0,
            "timeline_out": 3.0, "picture_led": True}
    manifest = {"tracks": {"V1": {"clips": [clip]},
                           "V2": {"clips": []},
                           "A1": {"clips": []},
                           "A2": {"clips": []}}}
    fabricated = [e for e in validate_manifest_semantics(manifest)
                  if "fabricated" in e]
    assert fabricated == []
    unmarked = dict(clip)
    del unmarked["picture_led"]
    unmarked["label"] = "speech_1_seg0"
    manifest["tracks"]["V1"]["clips"] = [unmarked]
    fabricated = [e for e in validate_manifest_semantics(manifest)
                  if "fabricated" in e]
    assert len(fabricated) == 1


# --------------------------------------------------------------------------
# From test_voiceover_spine.py
#
# The voiceover spine (P4 lane d): catalogued audio as speech.
#
# A voiceover take the project holds becomes speech the spine can use:
# its transcript feeds speech_sequence passages, passages cut from it
# become speech blocks sourced from the audio file, B-roll covers their
# picture, and the words play from the file on A1. A held music bed
# joins music_selection's candidates rather than sitting invisible in
# the intake.
#
# Each test names the defect its assertion prevents. Fixtures and temp
# dirs only.

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


BROLL_BRIDGE = (
    REPO_ROOT / "library" / "steps" / "step_3_02_select_broll"
    / "bridge.py"
)


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


def _catalog_2():
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
# tests/unit/audio/test_audio_spine.py beside the picture and music cases.)

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
            assign_a_roll({"structure": [block]}, _catalog_2(),
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

def _compile_inputs_2(spine, a_roll, voiceover, b_roll, catalog):
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
    manifest = _compile(_compile_inputs_2(
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


# --------------------------------------------------------------------------
# From test_black_beat_producer.py
#
# The black-beat escape hatch: a planner can declare a deliberate hold on
# black, and compile_manifest will accept it.  An undeclared gap still fails.
#
# A gap must be deliberate and defensible: the planner can emit the
# declaration, it passes the spine contract and the coverage assertion, and
# every malformed or missing declaration still hard-fails.
#
# Uses real captured run data from `tests/fixtures/captured_run/` so the
# tests exercise the full data flow, not just synthetic shapes.
#
# Both gates that judge black are covered: `compile_manifest` on the
# manifest and `render_qa` on the rendered file.  They read one bound and
# one declaration helper from the spine contract, so a beat that survives
# compilation cannot be failed after a full render.

sys.path.insert(0, str(REPO_ROOT))

from library.tools.spine_contract import (
    MAX_DECLARED_BLACK_BEAT_SECONDS,
)
from library.tools.render_qa import detect_black_frames
from library.steps.step_5_04_compile_manifest.step import (
    _assert_timeline_fully_covered,
    _video_coverage_gaps,
)
from library.steps.step_6_02_validate_output.bridge import _declared_black_beats


# ─── Helpers ──────────────────────────────────────────────────────────

def _make_spine_block(position, block_type, start, end, **extra):
    """A minimal spine block that satisfies the contract."""
    block = {
        "position": position,
        "block_type": block_type,
        "clip_id": None,
        "source_start": None,
        "source_end": None,
        "timeline_start": start,
        "timeline_end": end,
        "word_timestamps": [],
        "alignment_method": None,
    }
    block.update(extra)
    return block


def _manifest_with_spine(v1, spine_blocks, duration=10.0, fps=30.0, v2=()):
    """A manifest whose _spine_blocks are set from the given blocks."""
    def clips(spans, prefix):
        return [
            {"timeline_in": s, "timeline_out": e,
             "timeline_in_frame": int(round(s * fps)),
             "timeline_out_frame": int(round(e * fps)),
             "label": f"{prefix}_{i}"}
            for i, (s, e) in enumerate(spans)
        ]
    return {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {
            "V1": {"label": "A-Roll", "clips": clips(v1, "aroll")},
            "V2": {"label": "B-Roll", "clips": clips(v2, "broll")},
        },
        "_spine_blocks": spine_blocks,
    }


# ─── Spine contract validation ────────────────────────────────────────

class TestSpineContractBlackBeatValidation:
    """The spine contract gate catches malformed declarations at emit time."""

    @pytest.mark.parametrize("case", [
        "valid_declaration",
        "false_flag_is_not_a_declaration",
        "captured_block_with_declaration",
    ])
    def test_valid_black_beat_shapes_pass(self, case, captured_run):
        """Accepted shapes. The captured spine's undeclared blocks ride
        along in the last case, so an undeclared block is covered too."""
        if case == "valid_declaration":
            blocks = [_make_spine_block(
                1, "transition_slot", 4.0, 5.0,
                intentional_black_beat=True,
                black_beat_reason="hold on black before the tonal shift",
            )]
        elif case == "false_flag_is_not_a_declaration":
            blocks = [_make_spine_block(
                1, "transition_slot", 4.0, 5.0,
                intentional_black_beat=False,
            )]
        else:
            blocks = copy.deepcopy(captured_run["timed_spine_structure"])
            slot = next(
                b for b in blocks if b["block_type"] == "transition_slot"
            )
            slot["intentional_black_beat"] = True
            slot["black_beat_reason"] = "silence before the next section"
        # Should not raise
        validate_spine_blocks(blocks)

    def test_each_malformed_declaration_fails_by_name(self):
        """A declaration on speech, and one without a reason, both refuse."""
        on_speech = _make_spine_block(
            1, "speech", 4.0, 5.0,
            clip_id="clip_001",
            source_start=10.0, source_end=11.0,
            word_timestamps=[{"word": "test", "source_start": 10.0,
                              "source_end": 10.5}],
            alignment_method="whisperx",
            intentional_black_beat=True,
            black_beat_reason="dramatic pause",
        )
        no_reason = _make_spine_block(
            1, "transition_slot", 4.0, 5.0, intentional_black_beat=True)
        for block, match in (
            (on_speech, "speech block declares intentional_black_beat"),
            (no_reason, "black_beat_reason is missing or empty"),
        ):
            with pytest.raises(SpineContractError, match=match):
                validate_spine_blocks([block])


# ─── Post-bridge passthrough ──────────────────────────────────────────

class TestPostBridgeBlackBeatPassthrough:
    """The post_bridge's shallow copy carries the declaration through."""

    def _minimal_spine_with_beat(self):
        return {
            "structure": [
                {
                    "position": 1,
                    "block_type": "transition_slot",
                    "duration_seconds": 2.0,
                    "music_behavior": "prominent",
                    "visual_note": "B-roll cutaway",
                    "content": None,
                    "intentional_black_beat": True,
                    "black_beat_reason": "silence before the reveal",
                },
            ],
        }

    def test_declared_beat_survives_enrichment(self):
        spine = self._minimal_spine_with_beat()
        result = enrich_spine(spine, {}, {}, {"project_config": {"target_duration_seconds": 2.0}})
        blocks = result["audio_spine"]["structure"]
        assert len(blocks) == 1
        assert blocks[0]["intentional_black_beat"] is True
        assert blocks[0]["black_beat_reason"] == "silence before the reveal"


# ─── The render gate honours the same ruling ──────────────────────────

def _blackdetect_stderr(*segments):
    """ffmpeg stderr for the given (start, end) black segments."""
    return "\n".join(
        f"[blackdetect @ 0x123] black_start:{s} black_end:{e} "
        f"black_duration:{e - s:.3f}"
        for s, e in segments
    )


class TestRenderQADeclaredBeats:
    """The render gate must judge black by the same ruling as the manifest
    gate - otherwise a beat that survives compilation burns a full render
    and then fails at the last step."""

    def _detect(self, stderr, **kwargs):
        with patch("subprocess.run",
                   return_value=MagicMock(stderr=stderr, returncode=0)):
            return detect_black_frames("dummy.mp4", **kwargs)

    def test_render_gate_judges_black_by_the_declaration(self):
        """Each row: blackdetect segments against one declared beat."""
        declared = [(24.259, 26.259)]
        rows = [
            # exactly the bound compile_manifest accepts
            ((24.5, 24.5 + MAX_DECLARED_BLACK_BEAT_SECONDS), True),
            # blackdetect reports whole frames: a frame wide still passes
            ((24.492, 25.025), True),
            # black nobody declared
            ((4.0, 4.4), False),
            # longer than a beat, even inside the declaration
            ((24.4, 26.0), False),
            # straddling the declaration's edge
            ((24.0, 24.4), False),
        ]
        for segment, passes in rows:
            res = self._detect(_blackdetect_stderr(segment),
                               declared_beats=declared)
            assert res.passed is passes, (segment, res.detail)
            if segment == (4.0, 4.4):
                assert res.severity == "error"
                assert res.value[0]["declared"] is False
                assert "undeclared" in res.detail
            if passes and segment[0] == 24.5:
                assert res.value[0]["declared"] is True


# ─── End-to-end with real captured run data ───────────────────────────

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "captured_run"


@pytest.fixture(scope="module")
def captured_run():
    """The spine and manifest spine blocks from the real captured run.

    Committed under `tests/fixtures/captured_run/` - the convention this
    repo already uses for run-derived fixtures - so these tests exercise
    real planner output on every machine and on CI, not only where the
    project directory happens to live.
    """
    with open(FIXTURES / "black_beat_spine.json") as f:
        return json.load(f)


def _first_declarable_block(spine_blocks):
    """A non-speech block long enough to hold a beat, or skip.

    A block shorter than the bound cannot contain a gap the declaration
    would excuse, and building one anyway inverts the test's V1 spans.
    """
    for block in spine_blocks:
        if block.get("block_type") in ("speech", "hook"):
            continue
        if (block["timeline_end"] - block["timeline_start"]
                >= MAX_DECLARED_BLACK_BEAT_SECONDS):
            return block
    pytest.skip("captured run has no non-speech block long enough for a beat")


def _manifest_with_gap_in(block, spine_blocks, project):
    """A manifest whose only picture hole is a 0.3s gap inside `block`."""
    middle = (block["timeline_start"] + block["timeline_end"]) / 2
    gap_start, gap_end = middle - 0.15, middle + 0.15
    duration = project["duration_seconds"]
    return _manifest_with_spine(
        v1=[(0.0, gap_start), (gap_end, duration)],
        spine_blocks=spine_blocks,
        duration=duration,
        fps=project["frame_rate"],
    ), (gap_start, gap_end)


class TestCapturedRunBlackBeat:
    """Verify the feature against real data from the captured run.

    That run has no intentional black beats - the feature did not exist
    when it was captured - so it is the honest baseline for both
    directions: declare on one of its blocks and the hole is excused,
    leave it undeclared and the same hole hard-fails.
    """


    def test_captured_manifest_gap_with_declaration_passes(self, captured_run):
        """A gap inside a declared block passes compilation, and the render
        gate excuses the black it produces."""
        spine_blocks = copy.deepcopy(captured_run["spine_blocks"])
        target = _first_declarable_block(spine_blocks)
        target["intentional_black_beat"] = True
        target["black_beat_reason"] = "breath before the next section"

        manifest, (gap_start, gap_end) = _manifest_with_gap_in(
            target, spine_blocks, captured_run["project"]
        )

        gaps = _video_coverage_gaps(manifest)
        assert len(gaps) == 1
        _assert_timeline_fully_covered(manifest)

        with patch("subprocess.run", return_value=MagicMock(
                stderr=_blackdetect_stderr((gap_start, gap_end)),
                returncode=0)):
            res = detect_black_frames(
                "dummy.mp4",
                declared_beats=_declared_black_beats(manifest),
            )
        assert res.passed

    def test_captured_manifest_gap_without_declaration_fails(
        self, captured_run
    ):
        """The same gap without a declaration hard-fails at compilation,
        and the render gate fails the same black - proving the escape
        hatch is not a universal gap pass."""
        spine_blocks = copy.deepcopy(captured_run["spine_blocks"])
        target = _first_declarable_block(spine_blocks)
        target.pop("intentional_black_beat", None)
        target.pop("black_beat_reason", None)

        manifest, (gap_start, gap_end) = _manifest_with_gap_in(
            target, spine_blocks, captured_run["project"]
        )

        with pytest.raises(ValueError, match="no spine block declares"):
            _assert_timeline_fully_covered(manifest)

        with patch("subprocess.run", return_value=MagicMock(
                stderr=_blackdetect_stderr((gap_start, gap_end)),
                returncode=0)):
            res = detect_black_frames(
                "dummy.mp4",
                declared_beats=_declared_black_beats(manifest),
            )
        assert not res.passed


# --------------------------------------------------------------------------
# From test_enumerate_audio.py
#
# Voiceover and music audio enumerate separately from video.
#
# The defect: `footage_identity` accepted video extensions only, so a
# voiceover take or music bed in `raw/` never entered the pipeline at
# all. Audio enumerates in its own `audio_001` space - admitting it
# into the video numbering would renumber every video clip after it
# and orphan every cached per-clip analysis.

def _project(tmp_path, files):
    project = tmp_path / "project"
    raw = project / "raw"
    raw.mkdir(parents=True)
    for name, body in files.items():
        (raw / name).write_bytes(body)
    return str(project)


def test_audio_files_enumerate_in_their_own_space(tmp_path):
    folder = _project(tmp_path, {
        "b.mp4": b"x" * 64, "a.mp4": b"y" * 64,
        "voiceover.wav": b"z" * 64, "bed.mp3": b"w" * 64,
        "notes.txt": b"not media",
    })
    audio, skipped = enumerate_audio(folder)
    assert skipped == []
    assert [(e["audio_id"], e["filename"]) for e in audio] == [
        ("audio_001", "bed.mp3"), ("audio_002", "voiceover.wav")]
    assert all("clip_id" not in e for e in audio)


def test_video_numbering_is_untouched_by_audio(tmp_path):
    """The renumbering this separation exists to prevent: adding a
    voiceover must not move clip_001."""
    folder = _project(tmp_path, {"a.mp4": b"x" * 64})
    before, _ = enumerate_footage(folder)
    assert [e["clip_id"] for e in before] == ["clip_001"]
    open(folder + "/raw/voiceover.wav", "wb").write(b"z" * 64)
    after, _ = enumerate_footage(folder)
    assert [e["clip_id"] for e in after] == ["clip_001"]
    audio, _ = enumerate_audio(folder)
    assert [e["audio_id"] for e in audio] == ["audio_001"]


def test_audio_and_video_share_one_fingerprint_record(tmp_path):
    """The identity check holds both spaces in one record without a
    collision: `audio_001` is not `clip_001`."""
    folder = _project(tmp_path, {
        "a.mp4": b"x" * 64, "voiceover.wav": b"z" * 64})
    video, _ = enumerate_footage(folder)
    audio, _ = enumerate_audio(folder)
    record = fingerprints_for(video + audio)
    assert sorted(record) == ["audio_001", "clip_001"]
    assert (record["audio_001"]["path"]
            != record["clip_001"]["path"])


def test_audio_enumeration_skips_the_empty_and_refuses_the_missing(tmp_path):
    """A zero-byte take is skipped with its reason, a video-only project
    (the normal case) enumerates to [], and a missing raw/ raises."""
    folder = _project(tmp_path / "zero", {"empty.wav": b"",
                                          "bed.mp3": b"w" * 64})
    audio, skipped = enumerate_audio(folder)
    assert [e["audio_id"] for e in audio] == ["audio_001"]
    assert len(skipped) == 1 and "zero-byte" in skipped[0]["reason"]

    folder = _project(tmp_path / "video_only", {"a.mp4": b"x" * 64})
    assert enumerate_audio(folder) == ([], [])

    with pytest.raises(FileNotFoundError):
        enumerate_audio(str(tmp_path / "nope"))
