"""The bed is a SEQUENCE - several sections, several tracks, spliced.

Captain, 2026-09-01: *"its not one continuous stretch from the music we
have to use, like we can use bits and pieces, or multiple tracks, and
splice pieces from different tracks"*.

These drive `library/tools/music_bed.py` and the real `compile_manifest`
against a fake project, so the thing asserted is a manifest that was
compiled rather than one that was written down.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.music_bed import (  # noqa: E402
    BED_KEY,
    MusicBedError,
    THE_SNAP_QUESTION,
    bed_clips,
    describe,
    read_bed,
    resolve_bed,
    track_table,
)
from library.tools.otio_mix import MIN_VOLUME_DB, music_curve  # noqa: E402

TRACK_A = "/music/one.wav"
TRACK_B = "/music/two.wav"


def _selection(**over):
    base = {
        "title": "One", "audio_path": TRACK_A, "duration_seconds": 300.0,
        "tracks": [{"title": "Two", "audio_path": TRACK_B,
                    "duration_seconds": 240.0}],
    }
    base.update(over)
    return base


def _spine(bed=None, blocks=4, block_seconds=15.0):
    structure = []
    for i in range(blocks):
        structure.append({
            "position": i + 1,
            "block_type": "speech",
            "timeline_start": round(i * block_seconds, 3),
            "timeline_end": round((i + 1) * block_seconds, 3),
            "music_behavior": "background",
        })
    spine = {"structure": structure, "frame_rate": 30.0}
    if bed is not None:
        spine[BED_KEY] = bed
    return spine


# ── Ceiling 1: one section ───────────────────────────────────────────

def test_two_disjoint_sections_of_one_track_are_playable():
    """The ceiling `music_section` could not clear: 12s and 180s of the
    same file, in one bed."""
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 12.0, "why": "the sparse intro"},
        {"source_in": 180.0, "starts_at_block": 3, "why": "the settled body"},
    ]), 60.0)
    assert bed.declared
    assert [s.source_in for s in bed.segments] == [12.0, 180.0]
    assert [s.timeline_start for s in bed.segments] == [0.0, 30.0]
    assert bed.tracks_used == [TRACK_A]
    assert bed.splice_count == 1


# ── Ceiling 2: one track ─────────────────────────────────────────────

def test_the_bed_can_be_pieces_of_several_tracks():
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0},
        {"track": "Two", "source_in": 90.0, "starts_at_block": 2},
        {"source_in": 200.0, "starts_at_block": 4},
    ]), 60.0)
    assert [s.audio_path for s in bed.segments] == [TRACK_A, TRACK_B, TRACK_A]
    assert bed.tracks_used == [TRACK_A, TRACK_B]


def test_a_track_the_selection_did_not_choose_is_refused_by_name():
    """No nearest match: choosing a different track is a decision."""
    with pytest.raises(MusicBedError, match="which the selection did not"):
        resolve_bed(_selection(), _spine([
            {"source_in": 0.0},
            {"track": "/music/three.wav", "source_in": 0.0,
             "starts_at_block": 2},
        ]), 60.0)


def test_the_primary_track_is_the_one_a_segment_naming_none_plays():
    rows = track_table(_selection())
    assert [r["role"] for r in rows] == ["primary", "additional"]
    bed = resolve_bed(_selection(), _spine([{"source_in": 5.0}]), 60.0)
    assert bed.segments[0].audio_path == TRACK_A


# ── Ceiling 3: no conducting, and the absence of one ─────────────────

def test_a_spine_declaring_no_bed_gets_exactly_the_one_section_placement():
    """Every run before this one, unchanged: one clip, from the chosen
    section, under the whole video."""
    bed = resolve_bed(_selection(section={"source_in": 42.0}), _spine(), 60.0)
    assert not bed.declared
    assert len(bed.segments) == 1
    assert bed.segments[0].source_in == 42.0
    assert bed.segments[0].timeline_start == 0.0
    assert bed.segments[0].timeline_end == 60.0
    assert "one chosen section" in bed.reason


def test_declaring_nothing_at_all_still_plays_from_the_head():
    bed = resolve_bed(_selection(), _spine(), 60.0)
    assert not bed.declared
    assert bed.segments[0].source_in == 0.0


# ── The crossfade, and the refusal to invent one ─────────────────────

def test_a_splice_with_no_declared_crossfade_is_a_hard_splice():
    """No length is substituted - the absence of decoration, not a
    choice of it."""
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0},
        {"source_in": 120.0, "starts_at_block": 3},
    ]), 60.0)
    assert bed.segments[1].crossfade_in_seconds == 0.0
    assert bed.segments[0].crossfade_out_seconds == 0.0
    assert bed.segments[0].placed_end == bed.segments[1].placed_start


def test_a_declared_crossfade_makes_the_two_pieces_OVERLAP():
    bed = resolve_bed(_selection(), _spine([
        {"source_in": 0.0},
        {"source_in": 120.0, "starts_at_block": 3, "crossfade_seconds": 2.0},
    ]), 60.0)
    out, incoming = bed.segments
    assert out.placed_end == 32.0
    assert incoming.placed_start == 30.0
    assert out.placed_end > incoming.placed_start


def test_a_crossfade_longer_than_the_piece_it_fades_into_is_refused():
    with pytest.raises(MusicBedError, match="outlast"):
        resolve_bed(_selection(), _spine([
            {"source_in": 0.0},
            {"source_in": 120.0, "starts_at_block": 4,
             "crossfade_seconds": 30.0},
        ]), 60.0)


# ── Refusals, none of which repair the plan ──────────────────────────

def test_a_segment_running_past_the_end_of_its_file_is_refused():
    with pytest.raises(MusicBedError, match="silent"):
        resolve_bed(_selection(), _spine([
            {"source_in": 235.0, "track": "Two"},
        ]), 60.0)


def test_an_out_of_order_bed_is_refused_and_never_reordered():
    with pytest.raises(MusicBedError, match="out of order"):
        resolve_bed(_selection(), _spine([
            {"source_in": 0.0},
            {"source_in": 10.0, "starts_at_block": 4},
            {"source_in": 20.0, "starts_at_block": 2},
        ]), 60.0)


def test_a_segment_naming_an_unknown_block_is_refused_by_name():
    with pytest.raises(MusicBedError, match="not a\\s+position on the spine"):
        resolve_bed(_selection(), _spine([
            {"source_in": 0.0},
            {"source_in": 10.0, "starts_at_block": "nowhere"},
        ]), 60.0)


def test_a_later_segment_must_say_where_it_comes_in():
    with pytest.raises(MusicBedError, match="names no starts_at_block"):
        resolve_bed(_selection(), _spine([
            {"source_in": 0.0}, {"source_in": 10.0},
        ]), 60.0)


def test_no_crossfade_length_is_invented_for_a_malformed_one():
    with pytest.raises(MusicBedError, match="absence of a crossfade"):
        resolve_bed(_selection(), _spine([
            {"source_in": 0.0},
            {"source_in": 10.0, "starts_at_block": 2,
             "crossfade_seconds": "long"},
        ]), 60.0)


# ── The snap question is recorded, not answered ──────────────────────

# ── The clips a bed becomes ──────────────────────────────────────────

