"""J/L entries resolve through step 4.02 onto the transition spec.

Fidelity rung R5a (P3): the plan vocabulary gains `j_cut` / `l_cut`
at any V1 join, with the lead/lag stated by the plan or by a
sub-block anchor. These tests drive the real `resolve_transitions`:
a J-cut ships a hard-cut picture with the audio offset beside it; an
L-cut anchored to a word lands on that word; a picture decoration at
the same boundary merges onto one entry; and two audio offsets (or a
hold, a substitute, or an end anchor on an audio cut) refuse.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_02_plan_transitions.post_bridge import (
    resolve_transitions,
)
from library.tools.jl_cut import JLCutRefused
from library.tools.sub_block_anchor import AnchorRefused

FPS = 23.976


def _spine():
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
        plan, _spine(), {}, None, FPS, {}, {}, {})


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


def test_two_audio_offsets_at_one_join_refuse():
    with pytest.raises(JLCutRefused, match="two audio offsets"):
        _resolve([
            {"cut_point_position": 2, "type": "j_cut",
             "lead_seconds": 0.5},
            {"cut_point_position": 2, "type": "l_cut",
             "lag_seconds": 0.3},
        ])


def test_hold_on_a_j_cut_refuses():
    with pytest.raises(JLCutRefused, match="duration_feel"):
        _resolve([
            {"cut_point_position": 2, "type": "j_cut",
             "lead_seconds": 0.5, "duration_feel": "quick"},
        ])


def test_end_anchor_on_an_audio_cut_refuses():
    with pytest.raises(AnchorRefused, match="anchor_end"):
        _resolve([
            {"cut_point_position": 2, "type": "j_cut",
             "lead_seconds": 0.5, "anchor_end": {"word": "next"}},
        ])


def test_lead_through_speech_refuses_naming_the_word():
    with pytest.raises(JLCutRefused, match="'next'"):
        _resolve([
            {"cut_point_position": 2, "type": "l_cut",
             "lag_seconds": 2.0},
        ])


def test_plain_picture_entries_still_resolve_unchanged():
    (entry,) = _resolve([
        {"cut_point_position": 2, "type": "hard_cut",
         "rationale": "straight"},
    ])
    assert entry["transition_type"] == "hard_cut"
    assert "audio_offset" not in entry
