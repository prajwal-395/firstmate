"""A shared speech edge must open on the whole first word."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.tools import reel_look, subtitle_coverage
from library.tools.reel_build import placements
from library.tools.reel_proposal import (
    Approval,
    ReelMoment,
    partial_overlaps,
    snap_to_speech,
    validate_proposal,
)
from library.tools.sub_block_anchor import resolve_anchor

SOURCE = "/media/LCATL0013.MXF"
ITEM = "same-resolve-item"
FPS = 24000 / 1001
EDGE = 126.02154166666666


def _transcript(previous_item=ITEM, current_item=ITEM,
                previous_end=212.9, previous_source_end=EDGE,
                current_source_start=EDGE):
    previous_start = 211.55
    previous_source_start = 124.67154166666667
    current_start = 212.9
    current_end = 218.26
    return {"derived_from": {"duration_seconds": 500.0}, "segments": [
        {
            "speaker": "Craig",
            "text": "So we are hearing about that.",
            "timeline_start": previous_start,
            "timeline_end": previous_end,
            "source_file": SOURCE,
            "resolve_item_id": previous_item,
            "source_start": previous_source_start,
            "source_end": previous_source_end,
            "words": [
                {"word": "about", "start": 212.55, "end": 212.87},
                {"word": "that.", "start": 212.87, "end": 212.9},
            ],
        },
        {
            "speaker": "Craig",
            "text": "If you're attorney,",
            "timeline_start": current_start,
            "timeline_end": current_end,
            "source_file": SOURCE,
            "resolve_item_id": current_item,
            "source_start": current_source_start,
            "source_end": current_source_start + (current_end - current_start),
            "words": [
                {"word": "If", "start": 212.9, "end": 213.28},
                {"word": "you're", "start": 213.28, "end": 213.39},
                {"word": "attorney,", "start": 213.39, "end": 213.82},
            ],
        },
    ]}


def _clip():
    # The source and timeline offsets match both transcript rows exactly.
    return SimpleNamespace(
        resolve_item_id=ITEM,
        track_type="video",
        track_index=1,
        track_name="Craig",
        speaker="Craig",
        source_file=SOURCE,
        source_in=124.67154166666667,
        source_out=131.38154166666664,
        timeline_start=211.55,
        timeline_end=218.26,
    )


def _captioned(played):
    entries = []
    norms = set()
    for word in played:
        norm = subtitle_coverage.normalize_word(word["word"])
        norms.add(norm)
        entries.append({
            "word": word["word"],
            "norm": norm,
            "reel_start": word["reel_start"],
            "reel_end": word["reel_end"],
            "card": "opening.mov",
        })
    cards = [{"card": "opening.mov", "reel_start": 0.0,
              "reel_end": 0.92, "text_norms": norms}]
    return entries, cards


def test_reel03_shared_edge_excludes_prior_sentence_and_resolves_if_anchor():
    transcript = _transcript()
    start, end = snap_to_speech(213.26, 218.26, transcript)

    # "that." ends exactly where "If" begins. Keep that measured edge;
    # moving 50ms into If loses its onset after placement rounding.
    assert start == pytest.approx(212.9)
    assert end == pytest.approx(218.26)
    assert partial_overlaps(start, end, transcript) == []
    validate_proposal(
        [ReelMoment(number=3, slug="people-stopped-searching",
                    reason="opening boundary regression",
                    timeline_start=start, timeline_end=end,
                    approval=Approval.APPROVED)],
        transcript, 500.0,
    )

    placed = placements([(start, end)], [_clip()], FPS)
    assert len(placed) == 1
    assert placed[0]["source_in"] == pytest.approx(EDGE, abs=1e-9)

    played = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], [{
            "source_file": SOURCE,
            "source_start": placed[0]["source_in"],
            "source_end": placed[0]["source_out"],
            "reel_start": placed[0]["record"],
        }])["words"]
    assert [word["word"] for word in played] == [
        "If", "you're", "attorney,"
    ]
    assert played[0]["reel_start"] == pytest.approx(0.0)
    assert played[0]["reel_end"] == pytest.approx(0.38)
    captioned, cards = _captioned(played)
    gate = subtitle_coverage.check_word_coverage(played, captioned, cards)
    assert [finding for finding in gate["findings"]
            if finding["severity"] == "error"] == []

    spine = reel_look.motion_spine(placed, FPS, transcript["segments"])
    block = spine["structure"][0]
    assert block["source_start"] == pytest.approx(EDGE, abs=1e-9)
    assert [word["word"] for word in block["word_timestamps"]] == [
        "If", "you're", "attorney,"
    ]
    anchor = resolve_anchor(
        {"word": "If"}, block=block, frame_rate=FPS,
        step="plan_vfx", plan="reel_motion", index=0,
    )
    assert anchor["timeline_seconds"] == pytest.approx(0.0)
    assert 0 <= anchor["timeline_seconds"] < block["timeline_end"]


@pytest.mark.parametrize("changes", [
    {"previous_end": 212.89},
    {"previous_item": "different-resolve-item"},
    {"current_item": None},
    {"current_source_start": EDGE + 0.001},
])
def test_opening_still_snaps_to_the_whole_first_word_when_rows_differ(changes):
    transcript = _transcript(**changes)
    start, end = snap_to_speech(213.26, 218.26, transcript)
    assert (start, end) == pytest.approx((212.9, 218.26))


def test_boundary_guard_does_not_leave_a_cut_inside_the_first_word():
    transcript = _transcript()
    assert partial_overlaps(212.95, 218.26, transcript) == [
        transcript["segments"][1]
    ]
