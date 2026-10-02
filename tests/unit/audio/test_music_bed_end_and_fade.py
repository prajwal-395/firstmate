"""Rung 7 (K1, SD3.2): the bed ends early and fades out on the plan's numbers.

"Music out at 0:48 with a 2-second fade, then the last line dry" had
no plan spelling: a bed piece ran until the next piece came in (the
manifest's own words: "do not name an end"), and no fade-out existed
beside the crossfade. `ends_at_block` (+ `end_offset_seconds` /
`end_offset_frames`, E3) stops the piece; `fade_out_seconds` /
`fade_out_frames` ramps its last seconds through `otio_mix`. What
follows the end is silence under the picture - never a slide of the
next piece. A fade beside a crossfade on one segment refuses (two
endings), as does an end the piece cannot play (before its start,
past the next piece's start, longer than the piece).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.music_bed import (
    BED_KEY,
    bed_clips,
    resolve_bed,
)
from library.tools.otio_mix import MIN_VOLUME_DB, music_curve

TRACK_A = "/music/one.wav"


def _selection(**over):
    base = {"title": "One", "audio_path": TRACK_A,
            "duration_seconds": 300.0}
    base.update(over)
    return base


def _spine(bed, blocks=4, block_seconds=15.0):
    structure = [{
        "position": i + 1,
        "block_type": "speech",
        "timeline_start": round(i * block_seconds, 3),
        "timeline_end": round((i + 1) * block_seconds, 3),
    } for i in range(blocks)]
    return {"structure": structure, "frame_rate": 30.0, BED_KEY: bed}


def test_a_piece_ends_early_and_the_rest_is_silence():
    """SD3.2's shape: out at block 4's start with a 2 s fade, dry after."""
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0, "ends_at_block": 4,
         "fade_out_seconds": 2.0, "why": "out before the last line"},
    ]), 60.0)
    (seg,) = bed.segments
    assert seg.timeline_start == 0.0
    assert seg.timeline_end == 45.0
    assert seg.fade_out_seconds == 2.0
    assert seg.ends_at_block == 4
    assert seg.played_seconds == 45.0
    assert seg.source_out == 45.0


def test_end_offset_in_frames_moves_the_end_off_the_block():
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0, "ends_at_block": 4, "end_offset_frames": 30,
         "why": "a second into the last block"},
    ]), 60.0)
    (seg,) = bed.segments
    assert seg.timeline_end == pytest.approx(46.0)


def test_the_fade_reaches_the_a2_curve():
    """The clip carries the fade; the curve ramps to silence at its end."""
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0, "ends_at_block": 4,
         "fade_out_seconds": 2.0, "why": "out"},
    ]), 60.0)
    (clip,) = bed_clips(bed, fps=30.0)
    assert clip["fade_out_seconds"] == 2.0
    assert clip["timeline_out"] == 45.0
    keys = music_curve(
        [{"timeline_start": 0.0, "timeline_end": 45.0,
          "target_level_db": -14.0}],
        fps=30.0, clip_start_frame=0,
        clip_frame_count=1350, fade_seconds=1.0,
        fade_out_seconds=2.0)
    frames = sorted(keys)
    assert frames[-1] == 1349
    assert keys[frames[-1]] == MIN_VOLUME_DB
    hold = 1349 - 60
    assert keys[hold] > MIN_VOLUME_DB
