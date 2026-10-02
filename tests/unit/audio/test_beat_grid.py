"""P4.1: the real beat grid, and the two ways nobody was reaching it.

`plan_transitions` synthesised `[i * 60/bpm for i in ...]` from t=0. No
track's first beat lands at 0.000s, so every "beat-snapped" cut was
snapped to a grid offset from the music by the track's lead-in.

`plan_sfx` read `music_analysis["beat_grid"]["bars"]`, and the producer
emits no `beat_grid` key at all. So `bars` was always `[]` and the SFX
snapping never ran either - even though the standing plan credited
`plan_sfx` as the one honest consumer of the real grid.

The producer/consumer agreement is asserted here against the real
producer, `library/tools/analysis/music_pipeline.py`, so a rename on
either side fails rather than degrading to silence.
"""
import ast
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.beat_grid import (
    MIN_USABLE_BEATS,
    assert_music_offset_is_the_chosen_section,
    beat_positions,
)

# The bed plays from the head of the file unless a section says otherwise;
# these cases are about the grid's shape, not about the offset. The offset
# has its own tests in tests/unit/audio/test_music_section.py.
NO_SECTION = None

MUSIC_PIPELINE = os.path.join(PROJECT_ROOT, "library", "tools", "analysis",
                              "music_pipeline.py")


def analysis(beats=None, downbeats=None, tempo_bpm=120.0, **extra):
    a = {"tempo": {"bpm": tempo_bpm,
                   "beats": beats if beats is not None else [],
                   "downbeats": downbeats if downbeats is not None else []}}
    a.update(extra)
    return a


BEATS = [round(0.37 + i * 0.5, 3) for i in range(32)]


# ─────────────────────────────────────────────────────────
# The producer and the consumer must agree on the names
# ─────────────────────────────────────────────────────────

def test_the_producer_really_emits_tempo_beats_and_downbeats():
    """Read off music_pipeline itself, not off a fixture.

    A fixture agreeing with the reader proves the fixture. This asserts
    the shipped producer builds the keys this module reads.
    """
    with open(MUSIC_PIPELINE, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    emitted.add(key.value)

    assert "tempo" in emitted
    assert "beats" in emitted
    assert "downbeats" in emitted
    assert "bpm" in emitted


# ─────────────────────────────────────────────────────────
# Reading the grid
# ─────────────────────────────────────────────────────────


class TestRefusesToInvent:
    """Empty means "do not snap", which every caller already honours."""


    def test_too_few_beats_is_not_a_rhythm(self):
        few = BEATS[:MIN_USABLE_BEATS - 1]
        assert beat_positions(analysis(beats=few), NO_SECTION) == []


    def test_malformed_entries_are_skipped_not_crashed(self):
        messy = list(BEATS) + ["x", None, {}, -1.0]
        assert beat_positions(analysis(beats=messy), NO_SECTION) == sorted(BEATS)


# ─────────────────────────────────────────────────────────
# The time domain the grid depends on
# ─────────────────────────────────────────────────────────

def test_legitimate_music_placements_pass():
    """B1 collapse: the three no-raise offset validators share one
    assert helper, so one test keeps all three shapes covered."""
    assert_music_offset_is_the_chosen_section(
        {"tracks": {"A2": {"clips": [{"source_in": 0.0, "timeline_in": 0.0}]}}},
        NO_SECTION)
    assert_music_offset_is_the_chosen_section({"tracks": {}}, NO_SECTION)
    assert_music_offset_is_the_chosen_section({}, NO_SECTION)
    assert_music_offset_is_the_chosen_section(
        {"tracks": {"A2": {"clips": [
            {"source_in": 4.5, "timeline_in": 0.0}]}}},
        {"section": {"source_in": 4.5}})


def test_music_placed_somewhere_other_than_the_chosen_section_raises():
    """A snapped cut that is off by the music's offset looks exactly like
    a snapped cut that is correct."""
    with pytest.raises(ValueError, match="section"):
        assert_music_offset_is_the_chosen_section(
            {"tracks": {"A2": {"clips": [
                {"source_in": 4.5, "timeline_in": 0.0}]}}},
            None)
