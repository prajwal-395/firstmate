"""Rung 7 on step 2.05 (K1: findings 13/14, PA3.2/PA2.2, CT1.3, SD3.2).

1. Finding 13 - blocks are not frame-aligned: the cursor accumulated
   rounded seconds while the frame fields rounded each edge
   independently, so a cut at frame 767 read as outside a block
   starting at 25.576s. The cursor runs in frames now and the seconds
   derive from it: every boundary sits exactly on a frame.
2. Finding 14 - no tail handle: a cut could not move past its block's
   end except by restating the spine. Blocks cut from media carry
   `head_handle_frames` / `tail_handle_frames` - the unplayed source
   on either side, measured off the catalog - so a trim knows what it
   may extend into. Unknown headroom reads as absent, never as zero.
3. PA3.2/PA2.2 - an ASL/cut-rate target or feel: `pacing` windows
   over block positions, validated here and measured by
   `compile_manifest` into `pacing_report` (report-only). The feel-word
   case keeps the requester's words instead of inventing an ASL (E3).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import library.steps.step_2_05_mesh_spine.post_bridge as mb

FPS = 30.0


def _passage(n, clip="clip_1", start=0.0, end=2.285):
    return {
        "clip_id": clip, "source_start": start, "source_end": end,
        "text": f"line {n}",
        "word_timestamps": [
            {"word": "line", "source_start": start,
             "source_end": start + 0.5},
            {"word": str(n), "source_start": end - 0.5,
             "source_end": end},
        ],
        "alignment_method": "whisperx_word_alignment",
        "duration_seconds": round(end - start, 3),
    }


def _spine(blocks, **over):
    base = {"structure": blocks, "frame_rate": FPS}
    base.update(over)
    return base


def _speech_block(pos, ref, duration):
    return {"position": pos, "block_type": "speech",
            "duration_seconds": duration,
            "content": {"passage_ref": ref}}


def _enrich(blocks, passages, data=None):
    seq = {"body_sequence": passages}
    return mb.enrich_spine(_spine(blocks), seq, {}, data or {})


def test_boundaries_sit_exactly_on_frames():
    """Finding 13: seconds derive from the frame cursor, never round."""
    out = _enrich(
        [_speech_block(1, 1, 2.285), _speech_block(2, 2, 3.133)],
        [_passage(1, end=2.285), _passage(2, start=10.0, end=13.133)])
    structure = out["audio_spine"]["structure"]
    for block in structure:
        # Stored seconds are millisecond-rounded; the frame fields are
        # exact, and neighbouring seconds are EQUAL (one cursor).
        assert block["timeline_start"] == pytest.approx(
            block["timeline_start_frame"] / FPS, abs=1e-3)
        assert block["timeline_end"] == pytest.approx(
            block["timeline_end_frame"] / FPS, abs=1e-3)
    assert structure[0]["timeline_end_frame"] == (
        structure[1]["timeline_start_frame"])
    assert structure[0]["timeline_end"] == structure[1]["timeline_start"]
    assert structure[1]["duration_frames"] == (
        structure[1]["timeline_end_frame"]
        - structure[1]["timeline_start_frame"])


def test_a_frame_cut_is_inside_the_block_it_addresses():
    """Finding 13's own shape: frame 767 inside block 4's span."""
    out = _enrich(
        [_speech_block(1, 1, 25.567), _speech_block(2, 2, 3.0)],
        [_passage(1, end=25.567), _passage(2, start=40.0, end=43.0)])
    structure = out["audio_spine"]["structure"]
    block4 = structure[1]
    assert block4["timeline_start_frame"] == 767
    assert block4["timeline_start"] == pytest.approx(767 / FPS, abs=1e-3)
    # Membership is a frame question, never a rounded-seconds one: the
    # millisecond rounding above can sit a ten-thousandth past the
    # frame's exact time, which is finding 13 all over again.
    assert (block4["timeline_start_frame"]
            <= 767 <= block4["timeline_end_frame"])


def test_handles_measure_unplayed_source_in_frames():
    """Finding 14: 20 s of media, playing 4.083-7.216, leaves both."""
    out = _enrich(
        [_speech_block(1, 1, 3.133)],
        [_passage(1, start=4.083, end=7.216)],
        data={"clip_catalog": [
            {"clip_id": "clip_1", "duration_seconds": 20.0}]})
    (block,) = out["audio_spine"]["structure"]
    assert block["head_handle_frames"] == round(4.083 * FPS)
    assert block["tail_handle_frames"] == round((20.0 - 7.216) * FPS)


def test_pacing_windows_ride_the_spine():
    for window in (
            # PA3.2's shape: 2.5 s ASL over blocks 1-2, validated.
            {"start_block": 1, "end_block": 2, "asl_seconds": 2.5},
            # PA2.2 says faster cuts: the feel rides verbatim, no ASL
            # invented.
            {"start_block": 1, "end_block": 2,
             "feel": "faster cuts as it builds"}):
        out = mb.enrich_spine(
            _spine([_speech_block(1, 1, 2.285), _speech_block(2, 2, 3.133)],
                   pacing=[dict(window)]),
            {"body_sequence": [_passage(1, end=2.285),
                               _passage(2, start=10.0, end=13.133)]},
            {}, {})
        assert out["audio_spine"]["pacing"] == [window]


def test_pacing_window_refuses_a_number_and_feel_for_the_same_stretch():
    """A window must not turn E3's two representations into two targets."""
    with pytest.raises(ValueError, match="exactly one"):
        mb.enrich_spine(
            _spine([_speech_block(1, 1, 2.285)], pacing=[{
                "start_block": 1, "end_block": 1,
                "asl_seconds": 2.5, "feel": "faster"}]),
            {"body_sequence": [_passage(1, end=2.285)]}, {}, {})


def test_caption_word_limit_survives_and_refuses_non_counts():
    """Finding 31's requested count must reach plan_subtitles unchanged;
    a word ceiling is a positive whole-word count, never a default."""
    out = mb.enrich_spine(
        _spine([_speech_block(1, 1, 2.285)], max_words=2),
        {"body_sequence": [_passage(1, end=2.285)]}, {}, {})
    assert out["audio_spine"]["max_words"] == 2
    for invalid in (2.5, True, 0):
        with pytest.raises(ValueError, match="max_words"):
            mb.enrich_spine(
                _spine([_speech_block(1, 1, 2.285)],
                       max_words=invalid),
                {"body_sequence": [_passage(1, end=2.285)]}, {}, {})
