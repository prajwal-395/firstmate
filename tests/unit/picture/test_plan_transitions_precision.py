"""Rung 7 precision vocabulary on step 4.02 (K1: TR3.1, TR3.2, C3.1):
stated frame/second holds, the `"end"` slot, and J/L offsets in frames.
History: `docs/evidence/transition_precision.md`.
"""
import sys
from pathlib import Path

import pytest
import library.steps.step_4_02_plan_transitions.post_bridge as transition_bridge

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_02_plan_transitions.post_bridge import (
    TransitionSpecRefused,
    resolve_transitions,
)

FPS = 30.0


def _spine():
    # Three speech blocks, back to back, each with one word filling it
    # so the word-end resolution sits exactly on the boundary.
    blocks = []
    cursor = 0.0
    for pos, word in ((1, "one"), (2, "two"), (3, "three")):
        dur = 2.0
        blocks.append({
            "position": pos,
            "block_type": "speech",
            "clip_id": "clip_001",
            "source_start": cursor,
            "source_end": cursor + dur,
            "timeline_start": cursor,
            "timeline_end": cursor + dur,
            "word_timestamps": [
                {"word": word, "source_start": cursor,
                 "source_end": cursor + dur},
            ],
        })
        cursor += dur
    return {"structure": blocks, "frame_rate": FPS}


def _base(**over):
    params = {
        "music_selection": {},
        "music_analysis": {},
        "frame_rate": FPS,
    }
    params.update(over)
    return params


def test_the_hold_resolves_from_what_the_plan_stated_and_says_so():
    """TR3.1's 12-frame dissolve and 1s end fade: the number survives,
    sourced; a feel word alone still resolves."""
    rows = [
        ({"cut_point_position": 2, "type": "cross_dissolve",
          "duration_frames": 12}, None, 12, "stated_frames"),
        ({"cut_point_position": "end", "type": "fade_to_black",
          "duration_seconds": 1.0}, 1.0, 30, "stated_seconds"),
        ({"cut_point_position": 2, "type": "cross_dissolve",
          "duration_seconds": 0.4, "duration_frames": 12},
         0.4, 12, "stated_frames_and_seconds"),
        ({"cut_point_position": 2, "type": "cross_dissolve",
          "duration_feel": "medium"}, None, 10, "feel"),
    ]
    for plan, seconds, frames, source in rows:
        (entry,) = resolve_transitions(
            [dict(plan, rationale="x")], _spine(), **_base())
        assert entry["transition_type"] == plan["type"]
        assert entry["duration_frames"] == frames, plan
        assert entry["duration_source"] == source, plan
        if seconds is not None:
            assert entry["duration_seconds"] == pytest.approx(seconds)
        if "duration_feel" in plan:
            assert entry["duration_feel"] == "medium"


def test_an_ambiguous_hold_refuses():
    """Seconds and frames that disagree, or a number beside a different
    feel, are two holds."""
    for extra, match in [
            ({"duration_seconds": 0.4, "duration_frames": 13}, "disagree"),
            ({"duration_seconds": 0.4, "duration_feel": "quick"},
             "duration_feel")]:
        with pytest.raises(TransitionSpecRefused, match=match):
            resolve_transitions(
                [dict({"cut_point_position": 2, "type": "cross_dissolve",
                       "rationale": "two holds"}, **extra)],
                _spine(), **_base())


def test_stated_frame_anchor_survives_as_an_exact_cut_coordinate():
    """A timecode anchor remains an integer frame through transition planning."""
    (entry,) = resolve_transitions(
        [{"cut_point_position": 2, "type": "cross_dissolve",
          "anchor": {"frame": 45}, "duration_frames": 12,
          "rationale": "at the stated timecode"}],
        _spine(), **_base())
    assert entry["cut_point_frame"] == 45
    assert entry["cut_point_timeline"] == pytest.approx(1.5)


def test_end_fade_resolves_tail_only_at_the_timeline_end():
    """TR3.1's dip: fade_to_black at "end" sits on the last frame."""
    (entry,) = resolve_transitions(
        [{"cut_point_position": "end", "type": "fade_to_black",
          "duration_frames": 30, "rationale": "dip out"}],
        _spine(), **_base())
    assert entry["at_end"] is True
    assert entry["transition_type"] == "fade_to_black"
    assert entry["duration_frames"] == 30
    assert entry["duration_source"] == "stated_frames"
    assert entry["cut_point_timeline"] == pytest.approx(6.0)
    assert entry["placement_method"] == "timeline-end"


def test_runner_attempt_does_not_treat_end_fade_as_a_cut_carrier():
    """The carrier retry check must not dereference an absent end block."""
    (entry,) = resolve_transitions(
        [{"cut_point_position": "end", "type": "fade_to_black",
          "duration_seconds": 1.0, "rationale": "one second out"}],
        _spine(), **_base(attempt=1, v2_spans=[]))
    assert entry["at_end"] is True
    assert entry["transition_type"] == "fade_to_black"
    assert entry["duration_seconds"] == pytest.approx(1.0)


def test_j_lead_in_frames_reaches_the_audio_offset():
    """TR3.3's shape through the plan: 20 frames, on the boundary."""
    spine = _spine()
    # A pause before the join: block 1's word ends a second early, so
    # the 20-frame lead trims silence, never speech.
    spine["structure"][0]["word_timestamps"] = [
        {"word": "one", "source_start": 0.0, "source_end": 1.0},
    ]
    (entry,) = resolve_transitions(
        [{"cut_point_position": 2, "type": "j_cut",
          "lead_frames": 20, "rationale": "early ear"}],
        spine, **_base())
    assert entry["transition_type"] == "hard_cut"
    offset = entry["audio_offset"]
    assert offset["kind"] == "j_cut"
    assert offset["picture_cut_frame"] - offset["audio_cut_frame"] == 20


def test_jl_cut_uses_the_mesh_shared_boundary_frame(monkeypatch):
    """Rounding the displayed seconds must not shift the shared join frame."""
    from library.tools import jl_cut

    readback = {}

    def capture_boundary(**kwargs):
        readback["boundary_frame"] = kwargs["boundary_frame"]
        frame = kwargs["boundary_frame"]
        return {
            "picture_cut_timeline": frame / FPS,
            "picture_cut_frame": frame,
            "audio_cut_timeline": (frame - 1) / FPS,
            "audio_cut_frame": frame - 1,
            "audio_cut_exact": True,
            "lead_seconds": 1 / FPS,
            "method": "stated 1 frame",
        }

    monkeypatch.setattr(jl_cut, "resolve_audio_cut", capture_boundary)
    incoming = {
        "position": 2, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 25.576, "timeline_start_frame": 767,
    }
    outgoing = {
        "position": 1, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 0.0, "timeline_end": 25.576,
    }

    result = transition_bridge._attach_jl_offset(
        {"type": "j_cut", "lead_frames": 1}, None, incoming, outgoing,
        [], FPS, {}, {}, index=0)

    assert readback["boundary_frame"] == 767
    assert result["audio_offset"]["picture_cut_frame"] == 767


def test_jl_cut_refuses_an_explicit_but_malformed_shared_frame(monkeypatch):
    """Finding 13: None must not be mistaken for an absent frame boundary."""
    from library.tools import jl_cut
    from library.tools.jl_cut import JLCutRefused

    monkeypatch.setattr(
        jl_cut, "resolve_audio_cut",
        lambda **kwargs: pytest.fail("malformed boundary reached frame resolver"),
    )
    incoming = {
        "position": 2, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 25.576, "timeline_start_frame": None,
    }
    outgoing = {
        "position": 1, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 0.0, "timeline_end": 25.576,
    }

    with pytest.raises(JLCutRefused, match="unreadable shared boundary"):
        transition_bridge._attach_jl_offset(
            {"type": "j_cut", "lead_frames": 1}, None, incoming, outgoing,
            [], FPS, {}, {}, index=0)
