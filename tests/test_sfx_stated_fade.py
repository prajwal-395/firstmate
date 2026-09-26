"""Rung 7 (E3, "fade lengths in frames"): a stated SFX fade reaches the mix.

A sound's fade had no plan spelling: step 4.04 measured only the
de-click floor, and a `fade_*` key on a plan entry was refused as
unknown. `fade_in_seconds` / `fade_in_frames` and `fade_out_seconds`
/ `fade_out_frames` carry the requester's number - seconds or frames,
both agreeing. The shipped out-fade is the max of the stated fade and
the measured de-click floor (a stated ramp shorter than the click it
would leave is the floor winning, said on the placement method), and
both ramps must fit inside what plays. The head ramp renders through
`otio_mix.declick_curve` beside the out ramp.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_04_plan_sfx.post_bridge import (
    resolve_sfx,
)
from library.tools.otio_mix import MIN_VOLUME_DB, declick_curve

FPS = 30.0


def _spine():
    return {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": 100.0, "source_end": 110.0,
         "timeline_start": 8.0, "timeline_end": 18.0,
         "word_timestamps": [
             {"word": "tell", "source_start": 100.0,
              "source_end": 100.4},
         ],
         "alignment_method": "mfa"},
    ]}


def _catalog():
    return [{
        "sfx_id": "test_whoosh.wav",
        "path": "/nonexistent/test_whoosh.wav",
        "category": "Accents",
        "duration_seconds": 1.2,
        "envelope": "punchy",
        "transient_offset_sec": 0.05,
    }]


def _plan(**over):
    base = {"sfx_id": "test_whoosh.wav", "spine_block_position": 1,
            "volume_db": -12.0,
            "anchor": {"word": "tell"},
            "rationale": "on the word"}
    base.update(over)
    return [base]


def _resolve(plan):
    return resolve_sfx(plan, _spine(), [], {}, {}, FPS, {}, {},
                       catalog=_catalog())


def test_a_stated_out_fade_ships():
    (entry,) = _resolve(_plan(fade_out_seconds=0.5))["sfx_list"]
    assert entry["fade_out_seconds"] == pytest.approx(0.5)
    assert "fades out over 0.500s (stated)" in entry["placement_method"]


def test_frames_match_seconds():
    seconds = _resolve(_plan(fade_out_seconds=0.5))["sfx_list"][0]
    frames = _resolve(_plan(fade_out_frames=15))["sfx_list"][0]
    assert frames["fade_out_seconds"] == pytest.approx(
        seconds["fade_out_seconds"])


def test_a_stated_in_fade_ships():
    (entry,) = _resolve(_plan(fade_in_seconds=0.25))["sfx_list"]
    assert entry["fade_in_seconds"] == pytest.approx(0.25)
    assert "fades in over 0.250s (stated)" in entry["placement_method"]


def test_the_head_ramp_reaches_the_curve():
    """The stated fade-in renders as silence-to-level at the head."""
    keys = declick_curve(0.0, fps=FPS, clip_frame_count=36,
                         level_db=-12.0, fade_in_seconds=0.5)
    frames = sorted(keys)
    assert keys[frames[0]] == MIN_VOLUME_DB
    assert keys[15] == -12.0
    assert keys[frames[-1]] == -12.0
