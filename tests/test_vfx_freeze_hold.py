"""Rung 7 (K1, RT3.3): freeze duration uses the requester's units.

`hold_seconds` and `hold_frames` set the freeze span from its anchor.
Resolve's native speed write needs one timeline item spanning exactly
that operation, so a sub-block hold is refused until the builder can
place a separate item for it. These tests pin both the exact value that
reaches a matching item and the refusal that prevents a partial span
from being claimed as built.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import (
    resolve_vfx,
)
from library.tools.native_ops import NativeSpeedRefused

FPS = 30.0


def _block(duration=10.0, quit_at=2.0):
    return {
        "position": 1, "block_type": "speech", "clip_id": "clip_001",
        "source_start": 0.0, "source_end": duration,
        "timeline_start": 0.0, "timeline_end": duration,
        "word_timestamps": [
            {"word": "quit", "source_start": quit_at,
             "source_end": min(quit_at + 0.5, duration)},
            {"word": "now", "source_start": min(quit_at + 0.6, duration),
             "source_end": min(quit_at + 1.0, duration)},
        ],
    }


def _resolve(entries, block=None, dropped=None):
    return resolve_vfx(
        entries, {"structure": [block or _block()]}, frame_rate=FPS,
        dropped=dropped)


def test_hold_frames_reaches_a_matching_timeline_item():
    """42 stated frames resolve to an item whose whole span is 42 frames."""
    (entry,) = _resolve([{
        "target_block_position": 1, "effect_type": "freeze_frame",
        "anchor": {"word": "quit"}, "hold_frames": 42,
        "rationale": "stop time on the word"}],
        block=_block(duration=42 / FPS, quit_at=0.0))
    assert entry["effect_type"] == "freeze_frame"
    assert entry["timeline_start"] == pytest.approx(0.0)
    assert entry["timeline_end"] == pytest.approx(42 / FPS)
    assert "holds" in entry.get("anchor_method", "")


def test_hold_seconds():
    (entry,) = _resolve([{
        "target_block_position": 1, "effect_type": "freeze_frame",
        "anchor": {"word": "quit"}, "hold_seconds": 1.0,
        "rationale": "stop time"}],
        block=_block(duration=1.0, quit_at=0.0))
    assert entry["timeline_end"] == pytest.approx(1.0)


def test_agreeing_seconds_and_frames_ship_the_frames():
    (entry,) = _resolve([{
        "target_block_position": 1, "effect_type": "freeze_frame",
        "anchor": {"word": "quit"}, "hold_seconds": 1.4,
        "hold_frames": 42, "rationale": "one number"}],
        block=_block(duration=42 / FPS, quit_at=0.0))
    assert entry["timeline_end"] == pytest.approx(42 / FPS)


def test_sub_block_hold_is_reported_until_builder_can_split_the_item():
    dropped = []
    assert _resolve([{
        "target_block_position": 1, "effect_type": "freeze_frame",
        "anchor": {"word": "quit"}, "hold_frames": 42,
        "rationale": "stop time on the word"}], dropped=dropped) == []
    assert dropped[0].reason == "speed_span_subdivides_block"
    assert "2.000-3.400s" in dropped[0].detail


def test_disagreeing_seconds_and_frames_refuse():
    with pytest.raises(NativeSpeedRefused):
        _resolve([{
            "target_block_position": 1, "effect_type": "freeze_frame",
            "anchor": {"word": "quit"}, "hold_seconds": 1.0,
            "hold_frames": 42, "rationale": "two numbers"}])
