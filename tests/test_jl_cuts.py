"""J/L cuts: the audio cut moves, the gap fills with room, refusals name names.

Fidelity rung R5a (P3: doc J-cuts with room tone): no J/L vocabulary
exists on the master path, and the reel variant's reach-back
construction touches unplayed source. These tests drive the real
resolution (`resolve_audio_cut`) and the real trim + fill
(`apply_jl_cuts`, with fake room-tone functions) on a two-block
fixture spine: a J-cut trims the outgoing tail and fills the opened
gap with the incoming room; an L-cut trims the incoming head and fills
with the outgoing room; coverage stays continuous; and every
unbuildable request refuses through `JLCutRefused` - never a silent
straight cut.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.jl_cut import (
    JLCutRefused,
    apply_jl_cuts,
    normalise_kind,
    resolve_audio_cut,
    words_in_span,
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


def test_kind_spellings():
    assert normalise_kind("j_cut") == "j_cut"
    assert normalise_kind("L_CUT") == "l_cut"
    assert normalise_kind("cross_dissolve") is None
    assert normalise_kind("") is None


def test_j_cut_by_stated_lead_trims_the_tail():
    out, inn = _spine()
    # Boundary into block 2 is timeline 5.0 -> frame 120; the lead is
    # 12 frames, so the audio cut lands on frame 108 (4.504 s) and the
    # shipped lead is the frame-true 12/23.976 s.
    hit = resolve_audio_cut(
        kind="j_cut", entry={"lead_seconds": 0.5}, outgoing=out,
        incoming=inn, boundary_frame=120, frame_rate=FPS)
    assert hit["audio_cut_frame"] == 108
    assert hit["audio_cut_timeline"] == pytest.approx(4.504, abs=0.002)
    assert hit["lead_seconds"] == pytest.approx(0.5, abs=0.002)
    assert hit["picture_cut_frame"] == 120
    assert "states 0.500" in hit["method"]


def test_l_cut_by_word_anchor_resolves_the_word_start():
    out, inn = _spine()
    # "next" starts at source 201.0 -> timeline 6.0; the boundary into
    # block 2 is timeline 5.0 -> frame 120. The L-cut audio cut lands
    # on the word's frame, 24 frames after the boundary.
    hit = resolve_audio_cut(
        kind="l_cut", entry={"anchor": {"word": "next"}},
        outgoing=out, incoming=inn, boundary_frame=120,
        frame_rate=FPS)
    assert hit["audio_cut_frame"] == 144
    assert hit["audio_cut_timeline"] == pytest.approx(6.006, abs=0.002)
    assert hit["lead_seconds"] == pytest.approx(1.001, abs=0.002)


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


def test_anchor_and_seconds_must_agree():
    out, inn = _spine()
    with pytest.raises(JLCutRefused, match="agreeing"):
        resolve_audio_cut(
            kind="j_cut",
            entry={"lead_seconds": 0.5,
                   "anchor": {"word": "last", "edge": "end"}},
            outgoing=out, incoming=inn, boundary_frame=120,
            frame_rate=FPS)


def test_audio_cut_on_the_wrong_side_refuses():
    # A gapped spine: outgoing plays 0-6 s, incoming starts at 5.0 s,
    # so the boundary frame sits INSIDE the outgoing block. A "J-cut"
    # anchored at 5.5 s is past the boundary - an L-cut wearing the
    # wrong name.
    out = {"position": 1, "block_type": "speech",
           "source_start": 0.0, "source_end": 6.0,
           "timeline_start": 0.0, "timeline_end": 6.0,
           "word_timestamps": [], "alignment_method": "mfa"}
    inn = {"position": 2, "block_type": "speech",
           "source_start": 0.0, "source_end": 5.0,
           "timeline_start": 5.0, "timeline_end": 10.0,
           "word_timestamps": [], "alignment_method": "mfa"}
    with pytest.raises(JLCutRefused, match="wrong side|before.*boundary"):
        resolve_audio_cut(
            kind="j_cut",
            entry={"anchor": {"frame": int(5.5 * FPS)}},
            outgoing=out, incoming=inn,
            boundary_frame=int(round(5.0 * FPS)),
            frame_rate=FPS)


def test_trim_through_speech_refuses_naming_the_word():
    out, inn = _spine()
    with pytest.raises(JLCutRefused, match="'last'"):
        resolve_audio_cut(
            kind="j_cut", entry={"lead_seconds": 2.0}, outgoing=out,
            incoming=inn, boundary_frame=120,
            frame_rate=FPS)


def test_lead_escaping_the_block_refuses():
    out, inn = _spine()
    with pytest.raises(JLCutRefused, match="outside"):
        resolve_audio_cut(
            kind="l_cut", entry={"lag_seconds": 9.0}, outgoing=out,
            incoming=inn, boundary_frame=120,
            frame_rate=FPS)


def test_missing_offset_refuses():
    out, inn = _spine()
    with pytest.raises(JLCutRefused, match="no offset"):
        resolve_audio_cut(
            kind="j_cut", entry={}, outgoing=out, incoming=inn,
            boundary_frame=120, frame_rate=FPS)


def test_lag_on_a_j_cut_refuses():
    out, inn = _spine()
    with pytest.raises(JLCutRefused, match="lag_seconds"):
        resolve_audio_cut(
            kind="j_cut", entry={"lag_seconds": 0.5}, outgoing=out,
            incoming=inn, boundary_frame=120,
            frame_rate=FPS)


def test_words_in_span_reads_the_spine_contract():
    out, _ = _spine()
    assert words_in_span(out, 4.5, 5.0) == []
    assert words_in_span(out, 3.0, 4.5) == ["last"]


def test_apply_j_cut_trims_tail_and_fills_with_incoming_room(tmp_path):
    out = apply_jl_cuts(
        _clips(),
        [{"kind": "j_cut", "outgoing_position": 1,
          "incoming_position": 2, "picture_cut_timeline": 5.005,
          "picture_cut_frame": 120, "audio_cut_timeline": 4.504,
          "audio_cut_frame": 108, "lead_seconds": 0.5,
          "method": "plan states 0.500s"}],
        _spine(), FPS, _room, _stage, str(tmp_path))
    clips = out["v1_clips"]
    # Picture untouched; outgoing AUDIO ends on frame 108.
    assert clips[0]["timeline_in"] == 0.0
    assert clips[0]["timeline_out"] == 5.0
    assert clips[0]["audio_src_out"] == pytest.approx(104.5, abs=0.01)
    assert "audio_src_in" not in clips[0]
    assert "audio_src_out" not in clips[1]
    (fill,) = out["fills"]
    assert fill["timeline_in_frame"] == 108
    assert fill["timeline_out_frame"] == 120
    assert fill["timeline_in"] == pytest.approx(4.504, abs=0.002)
    assert fill["timeline_out"] == pytest.approx(5.005, abs=0.002)
    assert fill["room_source_file"] == "/footage/camB.mov"
    assert fill["room_tone_fill"] is True
    assert out["room_tone"]["/footage/camB.mov"]["level_dbfs"] == -50.0
    (record,) = out["applied"]
    assert record["kind"] == "j_cut"
    assert record["room_source"] == "camB.mov"


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
    (fill,) = out["fills"]
    assert fill["timeline_in_frame"] == 120
    assert fill["timeline_out_frame"] == 144
    assert fill["timeline_in"] == pytest.approx(5.005, abs=0.002)
    assert fill["timeline_out"] == pytest.approx(6.006, abs=0.002)
    assert fill["room_source_file"] == "/footage/camA.mov"


def test_apply_rechecks_speech_on_the_compiled_timeline(tmp_path):
    # Plan time said 0.5 s; the compiled words moved under it - the
    # trim now eats "last" and refuses rather than dropping speech.
    blocks = _spine()
    blocks[0]["word_timestamps"] = [
        {"word": "last", "source_start": 104.2, "source_end": 104.8}]
    with pytest.raises(JLCutRefused, match="'last'"):
        apply_jl_cuts(
            _clips(),
            [{"kind": "j_cut", "outgoing_position": 1,
              "incoming_position": 2, "picture_cut_timeline": 5.005,
              "picture_cut_frame": 120, "audio_cut_timeline": 4.504,
              "audio_cut_frame": 108, "lead_seconds": 0.5,
              "method": "x"}],
            blocks, FPS, _room, _stage, str(tmp_path))


def test_apply_refuses_a_join_with_no_v1_side(tmp_path):
    with pytest.raises(JLCutRefused, match="no V1 clip"):
        apply_jl_cuts(
            _clips(),
            [{"kind": "j_cut", "outgoing_position": 1,
              "incoming_position": 9, "picture_cut_timeline": 5.0,
              "audio_cut_timeline": 4.5, "lead_seconds": 0.5,
              "method": "x"}],
            _spine(), FPS, _room, _stage, str(tmp_path))


def test_apply_refuses_a_silent_side(tmp_path):
    clips = _clips()
    clips[1]["video_only"] = True
    with pytest.raises(JLCutRefused, match="no speech audio"):
        apply_jl_cuts(
            clips,
            [{"kind": "l_cut", "outgoing_position": 1,
              "incoming_position": 2, "picture_cut_timeline": 5.005,
              "picture_cut_frame": 120, "audio_cut_timeline": 6.006,
              "audio_cut_frame": 144, "lead_seconds": 1.001,
              "method": "x"}],
            _spine(), FPS, _room, _stage, str(tmp_path))


def test_apply_refuses_an_unfillable_gap(tmp_path):
    def no_room(source_file, speech_spans):
        from library.tools.room_tone import RoomToneRefused
        raise RoomToneRefused(
            what="no room", why="wall-to-wall speech",
            fix="drop the cut")
    with pytest.raises(JLCutRefused, match="needs room tone"):
        apply_jl_cuts(
            _clips(),
            [{"kind": "j_cut", "outgoing_position": 1,
              "incoming_position": 2, "picture_cut_timeline": 5.005,
              "picture_cut_frame": 120, "audio_cut_timeline": 4.504,
              "audio_cut_frame": 108, "lead_seconds": 0.5,
              "method": "x"}],
            _spine(), FPS, no_room, _stage, str(tmp_path))


def test_unknown_kind_refuses_by_name(tmp_path):
    with pytest.raises(JLCutRefused, match="not a J/L kind"):
        apply_jl_cuts(
            _clips(),
            [{"kind": "wipe", "outgoing_position": 1,
              "incoming_position": 2, "picture_cut_timeline": 5.0,
              "audio_cut_timeline": 4.5, "lead_seconds": 0.5,
              "method": "x"}],
            _spine(), FPS, _room, _stage, str(tmp_path))
