"""J/L cuts: the audio cut moves, the gap fills with room, refusals name names.

Fidelity rung R5a. Drives the real `resolve_audio_cut` and `apply_jl_cuts`
(fake room-tone functions) on a two-block spine; every unbuildable request
refuses through `JLCutRefused`, never a silent straight cut. The J-cut by
lead and the L-cut by word are held end to end by
`tests/unit/audio/test_plan_transitions_jl.py`, and the J-cut trim + fill by
`tests/unit/audio/test_compile_jl_cuts.py`.
"""
import os
import sys

import pytest

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
