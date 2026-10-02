"""J/L cuts: the audio cut moves, the gap fills with room, refusals name names.

Fidelity rung R5a. Drives the real `resolve_audio_cut` and `apply_jl_cuts`
(fake room-tone functions) on a two-block spine; every unbuildable request
refuses through `JLCutRefused`, never a silent straight cut. The J-cut by
lead and the L-cut by word are held end to end by
`tests/unit/audio/test_jl_cuts.py`, and the J-cut trim + fill by
`tests/unit/audio/test_jl_cuts.py`.
"""
import os
import sys
import pytest
import json
import math
import struct
import wave
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.jl_cut import (
    JLCutRefused,
    apply_jl_cuts,
    normalise_kind,
    resolve_audio_cut,
)

FPS = 23.976


def _spine():
    # Two contiguous speech blocks: block 1 speaks 100-105 s of source
    # over timeline 0-5 s with a pause at the end (last word ends 104,
    # block ends 105); block 2 speaks 200-204 s over timeline 5-10 s
    # with a leading pause (first word starts 201).
    return [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": 100.0, "source_end": 105.0,
         "timeline_start": 0.0, "timeline_end": 5.0,
         "word_timestamps": [
             {"word": "first", "source_start": 100.0,
              "source_end": 100.5},
             {"word": "last", "source_start": 103.5,
              "source_end": 104.0},
         ],
         "alignment_method": "mfa"},
        {"position": 2, "block_type": "speech", "clip_id": "clip_002",
         "source_start": 200.0, "source_end": 205.0,
         "timeline_start": 5.0, "timeline_end": 10.0,
         "word_timestamps": [
             {"word": "next", "source_start": 201.0,
              "source_end": 201.5},
             {"word": "final", "source_start": 203.5,
              "source_end": 204.0},
         ],
         "alignment_method": "mfa"},
    ]


def _clips():
    return [
        {"source_file": "/footage/camA.mov",
         "source_in": 100.0, "source_out": 105.0,
         "timeline_in": 0.0, "timeline_out": 5.0,
         "timeline_in_frame": 0, "timeline_out_frame": 120,
         "label": "speech_1", "spine_position": 1},
        {"source_file": "/footage/camB.mov",
         "source_in": 200.0, "source_out": 205.0,
         "timeline_in": 5.0, "timeline_out": 10.0,
         "timeline_in_frame": 120, "timeline_out_frame": 240,
         "label": "speech_2", "spine_position": 2},
    ]


def _room(source_file, speech_spans):
    return {"source_file": source_file, "segment_start": 0.0,
            "segment_end": 1.0, "level_dbfs": -50.0,
            "peak_dbfs": -40.0, "spectrum_db": [-100.0] * 8,
            "spectrum_edges_hz": [20.0] * 9, "gaps_considered": 1,
            "method": "fake"}


def _stage(source_file, record, fill_seconds, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as handle:
        handle.write(b"fake-wav")
    return {"path": out_path, "fill_seconds": round(fill_seconds, 3),
            "loops": 0, "level_dbfs": -50.0}


def test_j_cut_word_anchor_in_the_outgoing_block():
    out, inn = _spine()
    # "last" ends at source 104.0 -> timeline 4.0: audio cut on frame
    # 96, a 24-frame lead over the boundary frame 120.
    hit = resolve_audio_cut(
        kind="j_cut", entry={"anchor": {"word": "last", "edge": "end"}},
        outgoing=out, incoming=inn, boundary_frame=120,
        frame_rate=FPS)
    assert hit["audio_cut_frame"] == 96
    assert hit["lead_seconds"] == pytest.approx(1.001, abs=0.002)


def test_an_unresolvable_offset_refuses_by_name():
    """Each row is one way a plan's J/L entry cannot be built."""
    out, inn = _spine()
    # A gapped spine: the boundary frame sits INSIDE the outgoing block,
    # so a "J-cut" anchored at 5.5 s is past it - an L-cut misnamed.
    gapped_out = {"position": 1, "block_type": "speech",
                  "source_start": 0.0, "source_end": 6.0,
                  "timeline_start": 0.0, "timeline_end": 6.0,
                  "word_timestamps": [], "alignment_method": "mfa"}
    gapped_in = {"position": 2, "block_type": "speech",
                 "source_start": 0.0, "source_end": 5.0,
                 "timeline_start": 5.0, "timeline_end": 10.0,
                 "word_timestamps": [], "alignment_method": "mfa"}
    rows = [
        ("j_cut", {"lead_seconds": 0.5,
                   "anchor": {"word": "last", "edge": "end"}},
         "agreeing", None),
        ("j_cut", {"anchor": {"frame": int(5.5 * FPS)}},
         "wrong side|before.*boundary", (gapped_out, gapped_in,
                                         int(round(5.0 * FPS)))),
        # The trim would eat a word: refuse naming it.
        ("j_cut", {"lead_seconds": 2.0}, "'last'", None),
        ("l_cut", {"lag_seconds": 9.0}, "outside", None),
        ("j_cut", {}, "no offset", None),
        ("j_cut", {"lag_seconds": 0.5}, "lag_seconds", None),
    ]
    for kind, entry, said, gapped in rows:
        outgoing, incoming, boundary = gapped or (out, inn, 120)
        with pytest.raises(JLCutRefused, match=said):
            resolve_audio_cut(kind=kind, entry=entry, outgoing=outgoing,
                              incoming=incoming, boundary_frame=boundary,
                              frame_rate=FPS)


def test_apply_l_cut_trims_head_and_fills_with_outgoing_room(tmp_path):
    out = apply_jl_cuts(
        _clips(),
        [{"kind": "l_cut", "outgoing_position": 1,
          "incoming_position": 2, "picture_cut_timeline": 5.005,
          "picture_cut_frame": 120, "audio_cut_timeline": 6.006,
          "audio_cut_frame": 144, "audio_cut_exact": 6.0,
          "lead_seconds": 1.001,
          "method": "anchor"}],
        _spine(), FPS, _room, _stage, str(tmp_path))
    clips = out["v1_clips"]
    assert clips[1]["audio_src_in"] == pytest.approx(201.001, abs=0.01)
    assert clips[1]["timeline_in"] == 5.0
    assert "audio_src_out" not in clips[0]
    (fill,) = out["fills"]
    assert fill["timeline_in_frame"] == 120
    assert fill["timeline_out_frame"] == 144
    assert fill["timeline_in"] == pytest.approx(5.005, abs=0.002)
    assert fill["timeline_out"] == pytest.approx(6.006, abs=0.002)
    assert fill["room_source_file"] == "/footage/camA.mov"
    (record,) = out["applied"]
    assert record["kind"] == "l_cut"
    assert record["room_source"] == "camA.mov"


def test_apply_refuses_what_it_cannot_build(tmp_path):
    """Each row is one compile-time refusal; none ships a straight cut."""
    def no_room(source_file, speech_spans):
        from library.tools.room_tone import RoomToneRefused
        raise RoomToneRefused(
            what="no room", why="wall-to-wall speech",
            fix="drop the cut")

    j_cut = {"kind": "j_cut", "outgoing_position": 1,
             "incoming_position": 2, "picture_cut_timeline": 5.005,
             "picture_cut_frame": 120, "audio_cut_timeline": 4.504,
             "audio_cut_frame": 108, "lead_seconds": 0.5, "method": "x"}
    l_cut = {**j_cut, "kind": "l_cut", "audio_cut_timeline": 6.006,
             "audio_cut_frame": 144, "lead_seconds": 1.001}
    # Plan time said 0.5 s; the compiled words moved under it, so the
    # trim now eats "last" and refuses rather than dropping speech.
    moved = _spine()
    moved[0]["word_timestamps"] = [
        {"word": "last", "source_start": 104.2, "source_end": 104.8}]
    silent = _clips()
    silent[1]["video_only"] = True
    rows = [
        (_clips(), j_cut, moved, _room, "'last'"),
        (_clips(), {**j_cut, "incoming_position": 9}, _spine(), _room,
         "no V1 clip"),
        (silent, l_cut, _spine(), _room, "no speech audio"),
        (_clips(), j_cut, _spine(), no_room, "needs room tone"),
        (_clips(), {**j_cut, "kind": "wipe"}, _spine(), _room,
         "not a J/L kind"),
    ]
    # The kind vocabulary is case-blind and has exactly two words.
    assert normalise_kind("L_CUT") == "l_cut"
    assert normalise_kind("cross_dissolve") is None
    for clips, cut, spine, room, said in rows:
        with pytest.raises(JLCutRefused, match=said):
            apply_jl_cuts(clips, [cut], spine, FPS, room, _stage,
                          str(tmp_path))


# --------------------------------------------------------------------------
# From test_compile_jl_cuts.py
#
# A planned J/L offset reaches the manifest as trimmed audio plus room.
#
# Fidelity rung R5a (P3): step 4.02 resolves each J/L entry onto its cut
# as `audio_offset`; `compile_manifest` must turn that into sound - the
# trimmed side's `audio_src_in/out` moves while picture ranges stay put,
# and the opened gap is staged with the joining source's measured room
# tone. This test drives the real `compile_manifest` (step-file reads
# patched, like `test_compile_manifest.py`) on two speech blocks with
# real WAV sources: the outgoing tail trims by the lead, the fill lands
# on disk exactly over the gap, and the manifest carries `jl_cuts`,
# `room_tone` and `room_tone_fills` - with A1/V1 parity and manifest
# validation passing inside the step itself.

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


# --------------------------------------------------------------------------
# From test_plan_transitions_jl.py
#
# J/L entries resolve through step 4.02 onto the transition spec.
#
# Fidelity rung R5a (P3): the plan vocabulary gains `j_cut` / `l_cut`
# at any V1 join, with the lead/lag stated by the plan or by a
# sub-block anchor. These tests drive the real `resolve_transitions`:
# a J-cut ships a hard-cut picture with the audio offset beside it; an
# L-cut anchored to a word lands on that word; a picture decoration at
# the same boundary merges onto one entry; and two audio offsets (or a
# hold, or an end anchor on an audio cut) refuse. A lead through speech
# refusing by word is `tests/unit/audio/test_jl_cuts.py`'s.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_02_plan_transitions.post_bridge import (
    resolve_transitions,
)
from library.tools.sub_block_anchor import AnchorRefused


def _spine_2():
    # A picture-led beat (0-2 s, no words) into speech (2-7 s, first
    # word at 2.5 s). The join at 2.0 s carries a pause on both sides,
    # which is what a J/L offset must sit inside.
    return {"structure": [
        {"position": 1, "block_type": "picture", "clip_id": "clip_001",
         "source_start": 50.0, "source_end": 52.0,
         "timeline_start": 0.0, "timeline_end": 2.0,
         "word_timestamps": [],
         "alignment_method": "mfa"},
        {"position": 2, "block_type": "speech", "clip_id": "clip_002",
         "source_start": 200.0, "source_end": 205.0,
         "timeline_start": 2.0, "timeline_end": 7.0,
         "word_timestamps": [
             {"word": "next", "source_start": 200.5,
              "source_end": 201.0},
             {"word": "final", "source_start": 203.5,
              "source_end": 204.0},
         ],
         "alignment_method": "mfa"},
    ]}


def _resolve(plan):
    return resolve_transitions(
        plan, _spine_2(), {}, None, FPS, {}, {}, {})


def test_j_cut_by_lead_seconds_ships_hard_cut_plus_offset():
    # The V1 boundary into block 2 is timeline 2.0 -> frame 48; the
    # 0.5 s lead is 12 frames, so the audio cut lands on frame 36 and
    # the shipped lead is the frame-true 12/23.976 s.
    (entry,) = _resolve([
        {"cut_point_position": 2, "type": "j_cut",
         "lead_seconds": 0.5, "rationale": "room arrives early"},
    ])
    assert entry["transition_type"] == "hard_cut"
    assert entry["cut_point_timeline"] == pytest.approx(2.002, abs=0.002)
    offset = entry["audio_offset"]
    assert offset["kind"] == "j_cut"
    assert offset["picture_cut_frame"] == 48
    assert offset["picture_cut_timeline"] == pytest.approx(2.002, abs=0.002)
    assert offset["audio_cut_frame"] == 36
    assert offset["audio_cut_timeline"] == pytest.approx(1.502, abs=0.002)
    assert offset["lead_seconds"] == pytest.approx(0.5, abs=0.002)
    assert offset["outgoing_position"] == 1
    assert offset["incoming_position"] == 2


def test_l_cut_by_word_anchor_lands_on_the_word():
    (entry,) = _resolve([
        {"cut_point_position": 2, "type": "l_cut",
         "anchor": {"word": "next"}},
    ])
    offset = entry["audio_offset"]
    assert offset["kind"] == "l_cut"
    # "next" starts at source 200.5 -> timeline 2.5 -> frame 60, 12
    # frames after the boundary frame 48.
    assert offset["audio_cut_frame"] == 60
    assert offset["audio_cut_timeline"] == pytest.approx(2.503, abs=0.002)
    assert offset["lead_seconds"] == pytest.approx(0.5, abs=0.002)


def test_picture_decoration_and_j_cut_merge_onto_one_entry():
    (entry,) = _resolve([
        {"cut_point_position": 2, "type": "cross_dissolve",
         "duration_feel": "quick", "rationale": "soft join"},
        {"cut_point_position": 2, "type": "j_cut",
         "lead_seconds": 0.5, "rationale": "room arrives early"},
    ])
    assert entry["transition_type"] == "cross_dissolve"
    assert entry["audio_offset"]["kind"] == "j_cut"


def test_an_unbuildable_audio_cut_entry_refuses():
    """Two offsets at one join, a hold, or an end anchor on an audio cut."""
    rows = [
        ([{"cut_point_position": 2, "type": "j_cut", "lead_seconds": 0.5},
          {"cut_point_position": 2, "type": "l_cut", "lag_seconds": 0.3}],
         JLCutRefused, "two audio offsets"),
        ([{"cut_point_position": 2, "type": "j_cut", "lead_seconds": 0.5,
           "duration_feel": "quick"}], JLCutRefused, "duration_feel"),
        ([{"cut_point_position": 2, "type": "j_cut", "lead_seconds": 0.5,
           "anchor_end": {"word": "next"}}], AnchorRefused, "anchor_end"),
    ]
    for plan, error, said in rows:
        with pytest.raises(error, match=said):
            _resolve(plan)


def test_plain_picture_entries_still_resolve_unchanged():
    (entry,) = _resolve([
        {"cut_point_position": 2, "type": "hard_cut",
         "rationale": "straight"},
    ])
    assert entry["transition_type"] == "hard_cut"
    assert "audio_offset" not in entry


# --------------------------------------------------------------------------
# From test_room_tone.py
#
# Room tone is measured from the quietest speech-free stretch, never defaulted.
#
# Fidelity rung R5a (P3: doc J-cuts with room tone): the mix has no
# room-tone measurement, so a J/L cut's gap would drop to digital
# silence. These tests drive the real measurement on synthetic WAV
# fixtures (written with stdlib `wave`, decoded the same way - no ffmpeg
# needed): speech spans exclude the loud words, the quietest remaining
# gap wins, its level and spectrum are recorded, and staging loops it to
# the fill length. The refusal half: a source with no measurable gap
# raises `RoomToneRefused` instead of returning a default level.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.room_tone import (
    RoomToneRefused,
    measure_room_tone,
    rms_dbfs,
    spectrum_db,
    speech_free_gaps,
    stage_fill,
)


def _write_wav_2(path, samples, rate=SR):
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(packed)


def _fixture(path):
    """4 s: loud word 0.5-1.0 s, quiet room 1.0-3.0 s, loud word 3.0-3.5 s.

    The room is low-level noise plus a 120 Hz hum; the words are a loud
    440 Hz tone. Speech spans cover the words.
    """
    samples = []
    for i in range(4 * SR):
        t = i / SR
        if 0.5 <= t < 1.0 or 3.0 <= t < 3.5:
            samples.append(0.7 * math.sin(2 * math.pi * 440.0 * t))
        else:
            noise = 0.004 * math.sin(2 * math.pi * 120.0 * t)
            noise += 0.001 * math.sin(2 * math.pi * 3000.0 * t + 1.0)
            samples.append(noise)
    _write_wav_2(path, samples)
    return [(0.5, 1.0), (3.0, 3.5)]


def test_gaps_exclude_speech_and_short_breaths():
    gaps = speech_free_gaps(4.0, [(0.5, 1.0), (3.0, 3.5)])
    assert gaps[0] == pytest.approx((1.0, 3.0))
    # The 0.0-0.5 s lead and 3.5-4.0 s tail survive (both >= MIN_GAP).
    assert (0.0, 0.5) in gaps
    assert (3.5, 4.0) in gaps
    # A 0.1 s breath between words is not room tone.
    assert speech_free_gaps(4.0, [(0.0, 1.95), (2.05, 4.0)]) == []


def test_quietest_gap_wins_and_the_fill_matches_the_measurement(tmp_path):
    src = str(tmp_path / "take.wav")
    speech = _fixture(src)
    record = measure_room_tone(src, speech)
    assert record["segment_start"] == pytest.approx(1.0, abs=0.01)
    assert record["segment_end"] == pytest.approx(3.0, abs=0.01)
    # The room hum at 0.004 amplitude is about -48 dBFS: measured, and
    # far from both speech (~-6 dBFS) and digital silence.
    assert -60.0 < record["level_dbfs"] < -35.0
    assert record["peak_dbfs"] < -20.0
    assert record["gaps_considered"] == 3
    assert len(record["spectrum_db"]) == 8
    # The hum lives in the low bands: the 80-250 Hz band must read
    # far hotter than the empty 10-15 kHz band above the fixture's own
    # Nyquist.
    assert record["spectrum_db"][1] > record["spectrum_db"][6] + 20.0
    # The staged fill is that room, at the measured level.
    staged = stage_fill(src, record, 0.5, str(tmp_path / "room" / "fill.wav"))
    assert staged["loops"] == 0
    assert staged["fill_seconds"] == pytest.approx(0.5, abs=0.01)
    assert abs(staged["level_dbfs"] - record["level_dbfs"]) < 3.0


def test_short_gap_loops_to_the_fill_length(tmp_path):
    # Speech everywhere except a 0.4 s pause: the fill is longer than
    # the room, so it loops - and stays room, not silence.
    samples = [0.5 * math.sin(2 * math.pi * 440.0 * i / SR)
               if (i / SR < 1.0 or i / SR >= 1.4) else 0.003
               for i in range(4 * SR)]
    src = str(tmp_path / "tight.wav")
    _write_wav_2(src, samples)
    record = measure_room_tone(src, [(0.0, 1.0), (1.4, 4.0)])
    assert record["segment_end"] - record["segment_start"] == pytest.approx(
        0.4, abs=0.02)
    staged = stage_fill(src, record, 1.0, str(tmp_path / "fill.wav"))
    assert staged["loops"] >= 1
    assert staged["fill_seconds"] == pytest.approx(1.0, abs=0.01)
    assert staged["level_dbfs"] < -30.0


def test_no_measurable_room_refuses_by_name(tmp_path):
    """Never a default level: wall-to-wall speech, a missing source, and
    pauses of pure digital silence (-inf must not win as "the quietest
    gap", or the staged fill would be silence) all refuse."""
    wall = str(tmp_path / "wall.wav")
    _write_wav_2(wall, [0.5 * math.sin(2 * math.pi * 440.0 * i / SR)
                      for i in range(2 * SR)])
    silent = [0.0] * (2 * SR)
    for i in range(int(0.2 * SR), int(0.8 * SR)):
        silent[i] = 0.5 * math.sin(2 * math.pi * 440.0 * i / SR)
    silent_path = str(tmp_path / "silent.wav")
    _write_wav_2(silent_path, silent)
    for source, speech, said in (
            (wall, [(0.0, 2.0)], "no speech-free stretch"),
            (str(tmp_path / "absent.wav"), [], "not on disk"),
            (silent_path, [(0.2, 0.8)], "digital silence")):
        with pytest.raises(RoomToneRefused, match=said):
            measure_room_tone(source, speech)


def test_silence_reads_as_silence_not_zero():
    assert rms_dbfs([0.0] * 100) == float("-inf")
    assert rms_dbfs([]) == float("-inf")
    assert all(b == float("-inf") for b in spectrum_db([], SR))
