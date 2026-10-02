"""A word-timed cut edge one frame off a master-clip edge must not strand a nub.

Measured 2026-09-20 on Reel 08: LC4932/LCATL0013 start 614.697, the
mechanical take cut starts 614.720. `placements` rounds both to frames
and keeps the clip's 1-frame head - a picture+audio item the F7 floor
refuses. The range-level remnant rule cannot see it (the RANGE is 26s),
so `absorb_wordless_clip_edge_dust` shrinks the range to the clip
boundary where the dust between them is wordless: same policy as the
range rule (silence moves, speech refuses), at clip granularity.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from library.tools.reel_build import (
    absorb_wordless_clip_edge_dust,
    placements,
)

FPS = 24000 / 1001
FLOOR_FRAMES = 12


def _clip(start, end):
    return SimpleNamespace(timeline_start=start, timeline_end=end,
                           source_in=100.0, track_index=1, speaker="host")


def _transcript(words):
    """Timed words (start, end, text) as one transcript segment EACH.

    One row per word: neighbouring tellings never share a row, the
    way a cut's two sides never do.
    """
    return {"segments": [{
        "speaker": "host", "text": w,
        "timeline_start": s, "timeline_end": e,
        "words": [{"word": w, "start": s, "end": e, "timed": True}],
    } for s, e, w in words]}


def _subfloor(ranges, clips):
    return [p for p in placements(ranges, clips, FPS)
            if 0 < round((p["source_out"] - p["source_in"]) * FPS)
            < FLOOR_FRAMES]


def test_wordless_dust_snaps_to_the_clip_edge_both_ways():
    """The Reel 08 shape: clip starts 1 frame before the cut edge."""
    clips = [_clip(600.0, 614.697), _clip(614.697, 620.0)]
    transcript = _transcript([(612.0, 613.0, "kept"),
                              (615.0, 616.0, "struck")])
    out = absorb_wordless_clip_edge_dust([(600.0, 614.72)], clips,
                                         transcript, FPS)
    assert out == [(600.0, 614.697)]
    assert _subfloor(out, clips) == []

    # Mirror: clip ends just past the range start.
    clips = [_clip(600.0, 616.53), _clip(616.53, 622.0)]
    transcript = _transcript([(615.0, 616.0, "struck"),
                              (617.0, 618.0, "kept")])
    out = absorb_wordless_clip_edge_dust([(616.48, 622.0)], clips,
                                         transcript, FPS)
    assert out == [(616.53, 622.0)]
    assert _subfloor(out, clips) == []


def test_dust_carrying_speech_timed_or_not_is_left_for_the_gate():
    """Shrinking over a timed word would delete speech: refuse to snap."""
    clips = [_clip(600.0, 614.697), _clip(614.697, 620.0)]
    transcript = _transcript([(612.0, 613.0, "kept"),
                              (614.70, 614.90, "overlap")])
    out = absorb_wordless_clip_edge_dust([(600.0, 614.72)], clips,
                                         transcript, FPS)
    assert out == [(600.0, 614.72)]
    assert len(_subfloor(out, clips)) == 1

    # An untimed Mm-hmm is audible speech the timed scan cannot see.
    transcript = {"segments": [{
        "speaker": "host", "text": "Mm-hmm.",
        "timeline_start": 613.64, "timeline_end": 614.71,
        "words": [],
    }]}
    out = absorb_wordless_clip_edge_dust([(600.0, 614.72)], clips,
                                         transcript, FPS)
    assert out == [(600.0, 614.72)]


