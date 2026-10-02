"""The audio-led spine (P4 lane a): speech is optional.

Before this lane, every seam below assumed speech: a spine block with a
clip but no words had no vocabulary (and assign_aroll skipped it, leaving
a hole V1 never filled), mesh_spine refused to run without a
speech_sequence, and an empty body failed the duration zone. Each test
names the defect its assertion prevents. Fixtures and temp dirs only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
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
