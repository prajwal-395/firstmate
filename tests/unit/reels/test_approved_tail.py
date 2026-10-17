"""A declared source-locked tail shot survives the rebuild.

Reel 26, 2026-10-17: the approved 46.1 s cut carries the LCATL0013
Craig shot after the body plus a native ending hold, and the rebuild
planned neither - so promotion refused with
REN_REPLACE_GUARD_PROMOTION_REFUSED rather than drop the captain's
approved rows. A reel whose ending declares `approved_tail` must plan
picture plus speech entries abutting the body's end, through the
ordinary placement path; anything less than a complete declaration
refuses before a timeline exists.
"""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from library.tools import reel_ending
from library.tools.reel_build import (
    ReelBuildError, _freeze_tail_matches, append_approved_tail)
from library.tools.reel_ending import ReelEndingError

FPS = 24000 / 1001


def _body(end_frame=793):
    clip = SimpleNamespace(
        source_file="/media/LC4932.MXF", source_fps=FPS,
        track_index=1, speaker="Akshita", track_type="video",
        timeline_start=0.0, timeline_end=1.0, track_name="Akshita")
    return [{
        "clip": clip,
        "source_in": 102392 / FPS,
        "source_out": 103063 / FPS,
        "record": 122 / FPS,
        "snapped_record": 122,
        "record_frames": end_frame - 122,
        "track_index": 1,
        "speaker": "Akshita",
        "master": (2138.050, 2171.059),
    }]


def _tail_declaration(duration=220):
    return {
        "reel": "Reel 26 - write-for-the-question-your-customer-ask",
        "ends_on": {"anchor_phrase": "And AI really likes that"},
        "tail_element": "tv_power_tail",
        "tail_hold": "freeze",
        "reason": "test",
        "approved_tail": {
            "source_file": ("/Users/prajwal/Documents/work_stuff/"
                            "Lucie consulting/Social Media/podcast "
                            "media/LCATL0013.MXF"),
            "source_start_frame": 11538,
            "source_duration_frames": duration,
            "source_fps": FPS,
            "track_index": 2,
            "speaker": "Craig",
        },
    }


def test_tail_picture_and_speech_abut_the_body_end():
    placements = _body()
    appended = append_approved_tail(
        placements, _tail_declaration(), FPS, "Reel 26")
    assert len(appended) == 2
    assert len(placements) == 3
    kinds = sorted(entry["clip"].track_type for entry in appended)
    assert kinds == ["audio", "video"]
    for entry in appended:
        assert entry["snapped_record"] == 793
        assert entry["record_frames"] == 220
        assert entry["record"] == pytest.approx(793 / FPS)
        assert entry["source_in"] == pytest.approx(11538 / FPS)
        assert entry["source_out"] == pytest.approx(11758 / FPS)
        assert entry["track_index"] == 2
        assert entry["speaker"] == "Craig"
        assert entry["master"] == (0.0, 0.0)
        assert entry["approved_tail"] is True


def test_no_declaration_appends_nothing():
    for ending in (None, {}, {"tail_hold": "freeze"}):
        placements = _body()
        assert append_approved_tail(placements, ending, FPS, "Reel 26") == []
        assert len(placements) == 1


def test_picture_only_append_for_the_verifier_re_derivation():
    # The verifier re-derives picture only: the tail audio rides its
    # own row and F4 grades items per picture row, so an audio entry
    # in the planned picture would read as a fifth item.
    placements = _body()
    appended = append_approved_tail(
        placements, _tail_declaration(), FPS, "Reel 26",
        track_types=("video",))
    assert len(appended) == 1
    assert appended[0]["clip"].track_type == "video"
    assert len(placements) == 2


def test_incomplete_declaration_refuses():
    ending = _tail_declaration()
    del ending["approved_tail"]["source_file"]
    with pytest.raises(ReelBuildError, match="incomplete"):
        append_approved_tail(_body(), ending, FPS, "Reel 26")


@pytest.mark.parametrize("field,value", [
    ("source_file", "relative/path/LCATL0013.MXF"),
    ("source_start_frame", -1),
    ("source_duration_frames", 0),
    ("source_fps", 0.0),
    ("track_index", 0),
    ("speaker", "  "),
])
def test_invalid_declaration_refuses(field, value):
    ending = _tail_declaration()
    ending["approved_tail"][field] = value
    with pytest.raises(ReelBuildError, match="invalid"):
        append_approved_tail(_body(), ending, FPS, "Reel 26")


def test_tail_card_starts_after_the_carried_shot():
    # The closing logo must start after body + carried tail + hold
    # (793 + 220 + 19 = 1032 on Reel 26), never on top of the tail.
    assert reel_ending.approved_tail_frames(
        _tail_declaration(), FPS) == 220


def test_no_tail_declaration_needs_no_card_room():
    for ending in (None, {}, {"tail_hold": "freeze"}):
        assert reel_ending.approved_tail_frames(ending, FPS) == 0


def test_unsizable_tail_refuses_the_card_plan():
    ending = _tail_declaration()
    del ending["approved_tail"]["source_fps"]
    with pytest.raises(ReelEndingError, match="cannot be sized"):
        reel_ending.approved_tail_frames(ending, FPS)


# ── The hold reads its held frame, not its span start ────────────────
#
# Reel 26, 2026-10-17: the appended span [11739, 11758) collapses under
# a 0% retime onto the ending shot's last frame (11757), so every
# post-retime read-back must name the held frame. Expecting the span
# start refused a correctly placed hold after the Fusion pass.

HELD_FROM = ("/Users/prajwal/Documents/work_stuff/Lucie consulting/"
             "Social Media/podcast media/LCATL0013.MXF")


def _hold_item(source_start, speed=0.0):
    pool = SimpleNamespace(
        GetClipProperty=lambda key: HELD_FROM
        if key == "File Path" else None)
    return SimpleNamespace(
        GetStart=lambda: 1013, GetEnd=lambda: 1032,
        GetMediaPoolItem=lambda: pool,
        GetSourceStartFrame=lambda: source_start,
        GetSpeed=lambda: {"Percentage": speed})


def _hold_timeline(item):
    return SimpleNamespace(
        GetItemListInTrack=lambda media, row: [item]
        if (media, row) == ("video", 2) else [])


def test_post_retime_hold_matches_by_held_frame():
    timeline = _hold_timeline(_hold_item(11757))
    assert _freeze_tail_matches(timeline, 2, 1013, 1032, HELD_FROM,
                                11757, require_hold=True) != []


def test_span_start_does_not_match_a_retimed_hold():
    timeline = _hold_timeline(_hold_item(11757))
    assert _freeze_tail_matches(timeline, 2, 1013, 1032, HELD_FROM,
                                11739, require_hold=True) == []


def test_unretimed_span_still_matches_its_start():
    timeline = _hold_timeline(_hold_item(11739, speed=100.0))
    assert _freeze_tail_matches(timeline, 2, 1013, 1032, HELD_FROM,
                                11739) != []


# ── The tail's master span, for the caption spine ────────────────────
#
# Reel 26, 2026-10-17: the LCATL0013 tail speaks 33 words no keep range
# covers, so the caption spine must cover the tail's master span or the
# rebuild ships (and verification refuses) uncaptioned speech.

_TAIL_FILE = ("/Users/prajwal/Documents/work_stuff/Lucie consulting/"
              "Social Media/podcast media/LCATL0013.MXF")


def _master_clip(source_file=_TAIL_FILE, source_in_frame=11375,
                 source_out_frame=11758,
                 timeline_start=7492 / 23.976, timeline_end=7875 / 23.976,
                 track_type="video"):
    return SimpleNamespace(
        source_file=source_file,
        source_in_frame=source_in_frame,
        source_out_frame=source_out_frame,
        timeline_start=timeline_start, timeline_end=timeline_end,
        track_type=track_type, track_index=2, speaker="Craig")


def _tail_ending_220():
    return {"approved_tail": {
        "source_file": _TAIL_FILE,
        "source_start_frame": 11538,
        "source_duration_frames": 220,
        "source_fps": 23.976,
        "track_index": 2,
        "speaker": "Craig",
    }}


def test_no_tail_declaration_needs_no_master_span():
    assert reel_ending.approved_tail_master_span(
        [_master_clip()], None) is None
    assert reel_ending.approved_tail_master_span(
        [_master_clip()], {}) is None


def test_tail_maps_through_the_master_clip_that_plays_it():
    span = reel_ending.approved_tail_master_span(
        [_master_clip()], _tail_ending_220(), 23.976)
    assert span is not None
    # The recorded master geometry: source frame 11538 sits 163
    # frames into the clip starting at record frame 7492.
    assert span[0] == pytest.approx((7492 + 163) / 23.976)
    assert span[1] == pytest.approx(span[0] + 220 / 23.976)


def test_tail_with_no_master_coverage_refuses():
    with pytest.raises(ReelEndingError, match="no master clip"):
        reel_ending.approved_tail_master_span([], _tail_ending_220(), 23.976)
    with pytest.raises(ReelEndingError, match="no master clip"):
        reel_ending.approved_tail_master_span(
            [_master_clip(source_file="/media/other.MXF")],
            _tail_ending_220(), 23.976)
    with pytest.raises(ReelEndingError, match="no master clip"):
        reel_ending.approved_tail_master_span(
            [_master_clip(source_in_frame=0, source_out_frame=1000)],
            _tail_ending_220(), 23.976)
