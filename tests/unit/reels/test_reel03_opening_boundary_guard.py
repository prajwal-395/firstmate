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
    snap_moment_to_speech,
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


def test_opening_still_snaps_to_the_whole_first_word_when_rows_differ():
    for changes in ({"previous_end": 212.89},
                    {"previous_item": "different-resolve-item"},
                    {"current_item": None},
                    {"current_source_start": EDGE + 0.001}):
        transcript = _transcript(**changes)
        start, end = snap_to_speech(213.26, 218.26, transcript)
        assert (start, end) == pytest.approx((212.9, 218.26)), changes


def test_boundary_guard_does_not_leave_a_cut_inside_the_first_word():
    transcript = _transcript()
    assert partial_overlaps(212.95, 218.26, transcript) == [
        transcript["segments"][1]
    ]


def test_build_reel_timeline_places_audio_at_or_after_the_measured_edge(
        tmp_path):
    """The Resolve append path must not round a repaired word edge back.

    The stored proposal starts at 213.26, so the build-time repair first
    moves it to the shared edge at 212.9. That edge maps to source
    126.0215417s, between source frames. The master audio item is one
    frame behind the picture-bound transcript, so its independent map
    makes the old append conversion choose frame 3020. That readback is
    62ms before the edge and plays the ends of "about that." although
    the proposal and subtitle plan both start on "If".
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from library.tools.reel_build import build_reel_timeline

    transcript = _transcript()
    stored = ReelMoment(
        number=3, slug="people-stopped-searching",
        reason="opening boundary regression",
        timeline_start=213.26, timeline_end=218.26,
        approval=Approval.APPROVED,
    )
    moment, moves = snap_moment_to_speech(stored, transcript)
    assert moment.timeline_start == pytest.approx(212.9)
    assert moves[0]["boundary"] == "body_start"

    base = _clip()
    video_attrs = {**vars(base), "track_type": "video"}
    audio_attrs = {**vars(base), "track_type": "audio"}
    # The master audio item's own in-point is one source frame behind the
    # picture-bound transcript. The offline PR 1454 fixture used only the
    # picture clip, so it could not expose the build's separate audio map.
    audio_attrs["source_in"] -= 1 / float(FPS)
    audio_attrs["source_out"] -= 1 / float(FPS)
    video = SimpleNamespace(**video_attrs)
    audio = SimpleNamespace(**audio_attrs)
    clips = [video, audio]

    project = MagicMock()
    pool = MagicMock()
    project.GetMediaPool.return_value = pool
    timeline = MagicMock()
    timeline.GetUniqueId.return_value = "test-reel03"
    timeline.GetTrackCount.side_effect = lambda kind: (
        1 if kind in ("video", "audio") else 0)
    timeline.GetItemListInTrack.return_value = []
    pool.CreateEmptyTimeline.return_value = timeline
    project.GetCurrentTimeline.return_value = timeline

    root = MagicMock()
    pool.GetRootFolder.return_value = root
    root.GetSubFolderList.return_value = []
    source_item = MagicMock()
    source_item.GetClipProperty.side_effect = lambda prop: (
        SOURCE if prop == "File Path" else str(FPS) if prop == "FPS" else "")
    root.GetClipList.return_value = [source_item]

    build_reel_timeline(
        project, moment, clips, [], FPS, 1080, 1920,
        str(tmp_path), transcript, program_channels={"1": 1},
    )

    audio_append = next(
        call.args[0][0] for call in pool.AppendToTimeline.call_args_list
        if call.args[0][0]["mediaType"] == 2)
    assert audio_append["startFrame"] == 3022
    assert audio_append["endFrame"] - audio_append["startFrame"] == 129

    # F25 reads the source frames Resolve was actually asked to place.
    source_start = audio_append["startFrame"] / float(FPS)
    source_end = audio_append["endFrame"] / float(FPS)
    played = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], [{
            "source_file": SOURCE,
            "source_start": source_start,
            "source_end": source_end,
            "reel_start": 0.0,
        }])["words"]
    assert [word["word"] for word in played] == [
        "If", "you're", "attorney,"
    ]
