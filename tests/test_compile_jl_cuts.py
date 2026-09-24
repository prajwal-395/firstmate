"""A planned J/L offset reaches the manifest as trimmed audio plus room.

Fidelity rung R5a (P3): step 4.02 resolves each J/L entry onto its cut
as `audio_offset`; `compile_manifest` must turn that into sound - the
trimmed side's `audio_src_in/out` moves while picture ranges stay put,
and the opened gap is staged with the joining source's measured room
tone. This test drives the real `compile_manifest` (step-file reads
patched, like `test_compile_manifest.py`) on two speech blocks with
real WAV sources: the outgoing tail trims by the lead, the fill lands
on disk exactly over the gap, and the manifest carries `jl_cuts`,
`room_tone` and `room_tone_fills` - with A1/V1 parity and manifest
validation passing inside the step itself.
"""
import json
import math
import os
import struct
import sys
import wave
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_5_04_compile_manifest.step import compile_manifest

SR = 8000


def _write_wav(path, samples):
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(packed)


def _tone(start, end, total=5.0, freq=440.0, amp=0.45, room=0.004):
    samples = []
    for i in range(int(total * SR)):
        t = i / SR
        if start <= t < end:
            samples.append(amp * math.sin(2 * math.pi * freq * t))
        else:
            samples.append(room * math.sin(2 * math.pi * 120.0 * t))
    return samples


def _inputs(path_a, path_b):
    block1 = {
        "block_type": "speech", "position": 1, "clip_id": "clip_1",
        "source_file": path_a,
        "source_start": 0.37, "source_end": 5.37,
        "timeline_start": 0.0, "timeline_end": 5.0,
        "word_timestamps": [
            {"word": "first", "source_start": 0.37, "source_end": 4.8},
        ],
        "alignment_method": "mfa",
        "content": {"clip_id": "clip_1", "link_group_id": "lg_1"},
    }
    block2 = {
        "block_type": "speech", "position": 2, "clip_id": "clip_2",
        "source_file": path_b,
        "source_start": 0.61, "source_end": 5.61,
        "timeline_start": 5.0, "timeline_end": 10.0,
        "word_timestamps": [
            {"word": "next", "source_start": 1.61, "source_end": 4.61},
        ],
        "alignment_method": "mfa",
        "content": {"clip_id": "clip_2", "link_group_id": "lg_2"},
    }
    return {
        "a_roll_assignments": [],
        "voiceover_assignments": [],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [
            {
                "transition_id": "trans_001",
                "cut_point_timeline": 5.0,
                "cut_point_original": 5.0,
                "transition_type": "hard_cut",
                "duration_frames": 0,
                "beat_aligned": False,
                "snap_delta_seconds": 0.0,
                "displacement_seconds": 0.0,
                "placement_method": "block-boundary",
                "word_beat_coincidence": False,
                "rationale": "doc join",
                "requested_type": "j_cut",
                "downgrade_reason": "",
                "audio_offset": {
                    "kind": "j_cut",
                    "picture_cut_timeline": 5.0,
                    "audio_cut_timeline": 4.5,
                    "lead_seconds": 0.5,
                    "method": "plan states 0.500s",
                    "outgoing_position": 1,
                    "incoming_position": 2,
                },
            },
        ],
        "enhancement_spec": [],
        "color_grade_spec": {},
        "audio_mix_spec": {},
        "music_selection": {},
        "audio_spine": {
            "structure": [block1, block2],
            "frame_rate": 23.976,
        },
        "clip_catalog": [
            {"clip_id": "clip_1", "path": path_a,
             "width": 1080, "height": 1920},
            {"clip_id": "clip_2", "path": path_b,
             "width": 1080, "height": 1920},
        ],
        "semantic_analysis": {"semantic_analysis_documents": [
            {"clip_id": "clip_1", "analysis": {},
             "assessment": {"clip_type": "a-roll"}},
            {"clip_id": "clip_2", "analysis": {},
             "assessment": {"clip_type": "a-roll"}},
        ]},
    }


def test_j_cut_trims_audio_and_stages_room_fill(tmp_path):
    path_a = str(tmp_path / "camA.wav")
    path_b = str(tmp_path / "camB.wav")
    _write_wav(path_a, _tone(0.37, 4.8, total=6.0))
    _write_wav(path_b, _tone(1.61, 4.61, total=6.0))
    mock_inputs = _inputs(path_a, path_b)
    out_dir = str(tmp_path / "pipeline_output")

    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda _d, _f: mock_inputs):
        manifest = compile_manifest(out_dir)

    v1 = manifest["tracks"]["V1"]["clips"]
    assert len(v1) == 2
    # Picture untouched; outgoing AUDIO ends on the audio-cut frame:
    # source_out minus the 12-frame lead at 23.976 fps.
    assert v1[0]["timeline_in"] == 0.0
    assert v1[0]["timeline_out"] == 5.0
    assert v1[0]["audio_src_out"] == pytest.approx(5.37 - 12 / 23.976,
                                                  abs=0.002)
    assert "audio_src_in" not in v1[0]
    assert "audio_src_out" not in v1[1]

    (record,) = manifest["jl_cuts"]
    assert record["kind"] == "j_cut"
    assert record["audio_cut_frame"] == 108
    assert record["audio_cut_timeline"] == pytest.approx(108 / 23.976,
                                                        abs=0.002)

    measurement = manifest["room_tone"][path_b]
    assert measurement["level_dbfs"] < -30.0
    assert len(measurement["spectrum_db"]) == 8

    (fill,) = manifest["room_tone_fills"]
    assert fill["timeline_in_frame"] == 108
    assert fill["timeline_out_frame"] == 120
    assert fill["timeline_in"] == pytest.approx(108 / 23.976, abs=0.002)
    assert fill["timeline_out"] == pytest.approx(120 / 23.976, abs=0.002)
    assert fill["room_tone_fill"] is True
    assert os.path.isfile(fill["source_file"])
    json.dumps(manifest)
