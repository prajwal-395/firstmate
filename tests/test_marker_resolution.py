"""The other end of the captain's timeline notes: resolve, verify, clear.

History: docs/evidence/resolve_test_history.md#test_marker_resolution.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import marker_resolution as mr
from tests.resolve_double import FakeTimeline

# ── Notes ─────────────────────────────────────────────────────────


def _timeline_note(**kw):
    note = {
        "note_id": "reel-09:timeline_marker:1674",
        "source": "timeline_marker",
        "name": "Marker 2",
        "note": "the a-roll row needs to be 2 rows, one for akshita and one for craig",
        "text": "Marker 2\n\nthe a-roll row needs to be 2 rows, one for akshita and one for craig",
        "frame": 108000 + 1674,
        "frame_in_timeline_space": 1674,
        "timecode": "01:00:55:24",
        "timeline": "reel-09",
        "pull_file": "reel-09.json",
        "collected_at": "2026-09-09T00:00:00+00:00",
        "outcome": "routed",
        "steps": ["render"],
    }
    note.update(kw)
    return note


def _taste_note(**kw):
    note = _timeline_note(
        note_id="reel-09:clip_marker:528",
        source="clip_marker",
        name="Marker 1",
        note="this clip segment doesn't add anything and makes it look choppy",
        text="Marker 1\n\nthis clip segment doesn't add anything and makes it look choppy",
        frame=528,
        frame_in_timeline_space=528,
        outcome="routed",
        steps=["mesh_spine"],
    )
    note.update(kw)
    return note


def _timeline_with(note):
    key = note["frame_in_timeline_space"]
    timeline = FakeTimeline("Reel 09", start_frame=108000)
    timeline.AddMarker(key, "Red", note["name"], note["note"], 1, "")
    return timeline


def _timeline_with_forbidden_delete(note):
    timeline = _timeline_with(note)
    timeline.explode_marker_delete_frames.add(note["frame_in_timeline_space"])
    return timeline


def _timeline_with_refused_delete(note):
    timeline = _timeline_with(note)
    timeline.refuse_marker_delete_frames.add(note["frame_in_timeline_space"])
    return timeline


# ── The record ────────────────────────────────────────────────────


def test_record_preserves_the_captains_words_verbatim(tmp_path):
    note = _timeline_note()
    record = mr.record_resolution(
        str(tmp_path),
        note,
        mr.STATUS_DECLINED,
        action="decline: the row split is the captain's call",
        rationale="one row is what the build produced; not changing it unseen",
    )
    assert record["name"] == note["name"]
    assert record["note"] == note["note"]
    assert record["text"] == note["text"]
    path = mr.resolution_path(str(tmp_path), note["note_id"])
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["text"] == note["text"]


# ── The checks carry a verdict ────────────────────────────────────


def test_unknown_check_is_refused():
    with pytest.raises(mr.UnknownCheck):
        mr.verify("framing_looks_fine", {"a_roll_video_rows": 2})


# ── Verified fixes clear; everything else stays ───────────────────


def test_verified_fix_clears_the_timeline_marker(tmp_path):
    note = _timeline_note()
    timeline = _timeline_with(note)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="split the a-roll onto two rows",
        rationale="rebuilt with one row per angle",
        check=mr.CHECK_A_ROLL_ROWS,
        measured={"a_roll_video_rows": 2},
        timeline=timeline,
    )
    record = result["record"]
    assert result["marker_touched"] is True
    assert record["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert record["marker_removed"] is True
    assert record["marker_still_present"] is False
    assert record["text"] == note["text"]
    assert record["evidence"]["a_roll_video_rows"] == 2
    assert timeline.GetMarkers() == {}
    # The durable record shows the evidence that cleared it.
    on_disk = mr.read_resolution(str(tmp_path), note["note_id"])
    assert on_disk["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert on_disk["evidence"]["a_roll_video_rows"] == 2


def test_only_a_verified_fix_clears_a_marker(tmp_path):
    """A decline never clears, even beside a passing check; a taste note
    is unverifiable; a failed check is addressed-unverified. Each keeps
    its marker - the delete is forbidden on the fake and would raise."""
    rows = (
        (_timeline_note(), dict(
            action="decline: the single row is deliberate for this reel",
            rationale="the captain's layout note is about the series template",
            check=mr.CHECK_A_ROLL_ROWS, measured={"a_roll_video_rows": 2}),
         mr.STATUS_DECLINED, None),
        (_taste_note(), dict(action="recut the segment tighter",
                             rationale="removed the choppy passage"),
         mr.STATUS_UNVERIFIABLE, None),
        (_timeline_note(), dict(
            action="split the a-roll onto two rows", rationale="rebuilt",
            check=mr.CHECK_A_ROLL_ROWS, measured={"a_roll_video_rows": 1}),
         mr.STATUS_ADDRESSED_UNVERIFIED, 1),
    )
    for index, (note, kwargs, status, rows_measured) in enumerate(rows):
        timeline = _timeline_with_forbidden_delete(note)
        if "check" in kwargs:
            kwargs["timeline"] = timeline
        result = mr.resolve_note(str(tmp_path / str(index)), note, **kwargs)
        record = result["record"]
        assert record["status"] == status
        assert result["marker_touched"] is False
        assert record["marker_removed"] is False
        assert len(timeline.GetMarkers()) == 1
        if rows_measured is not None:
            assert record["evidence"]["a_roll_video_rows"] == rows_measured
        if status == mr.STATUS_DECLINED:
            assert record["marker_still_present"] is True


# ── Failure keeps the words ───────────────────────────────────────


def test_failed_removal_keeps_the_words_and_says_the_marker_is_there(tmp_path):
    note = _timeline_note()
    timeline = _timeline_with_refused_delete(note)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="split the a-roll onto two rows",
        rationale="rebuilt with one row per angle",
        check=mr.CHECK_A_ROLL_ROWS,
        measured={"a_roll_video_rows": 2},
        timeline=timeline,
    )
    record = result["record"]
    assert record["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert record["text"] == note["text"]
    assert record["evidence"]["a_roll_video_rows"] == 2
    assert record["marker_removed"] is False
    assert record["marker_still_present"] is True
    assert len(timeline.GetMarkers()) == 1
    on_disk = mr.read_resolution(str(tmp_path), note["note_id"])
    assert on_disk["text"] == note["text"]
    assert on_disk["marker_still_present"] is True


def test_stale_frame_refuses_the_deletion(tmp_path):
    note = _timeline_note()
    # The timeline was rebuilt since: another note sits at that frame now.
    timeline = FakeTimeline("Reel 09", start_frame=108000)
    timeline.AddMarker(
        1674, "Red", "Marker 9", "something the captain typed since", 1, ""
    )
    timeline.explode_marker_delete_frames.add(1674)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="split the a-roll onto two rows",
        rationale="rebuilt",
        check=mr.CHECK_A_ROLL_ROWS,
        measured={"a_roll_video_rows": 2},
        timeline=timeline,
    )
    record = result["record"]
    assert record["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert record["marker_removed"] is False
    assert record["marker_still_present"] is True
    assert "stale frame" in record["removal"]["reason"]
    assert len(timeline.GetMarkers()) == 1


# ── A "done" claim is backed by the artefact, or it is not done ───
#
# `declared_element_reaches_reels` is the check for the commonest
# instruction the captain gives - "apply this to all of the reels" -
# and the one that failed on 2026-09-11: the mechanism landed, the
# engine's capability was reported as the project's state, and six of
# eight reels did not have it. Remove the check and a note like that
# has no mechanical proof at all, so `verifiability_of` returns False
# and a worker's assertion is the only thing on offer.


def _survey(tmp_path, **reels):
    """A real `reel_divergence` survey over fake reel snapshots."""
    from library.tools import reel_divergence as rd

    (tmp_path / "project.yaml").write_text(
        "effect:\n"
        "  full_frame_elements:\n"
        "    - element: full_frame_clip\n"
        "      placement: tail\n"
        "      asset: /shared/logo_reveal.mov\n"
        "      reason: captain\n",
        encoding="utf-8",
    )

    class Clip:
        def __init__(self, name):
            self.name = name
            self.track_type = "video"
            self.track_name = "V1"
            self.source_file = ""

    class Snap:
        def __init__(self, names):
            self.clips = [Clip(n) for n in names]

    return rd.survey(
        str(tmp_path), {reel: Snap(names) for reel, names in reels.items()}
    )


def test_a_declaration_absent_from_one_reel_or_never_read_FAILS(tmp_path):
    passed, evidence = mr.verify(
        mr.CHECK_DECLARATION_REACHES,
        {
            "divergence": _survey(
                tmp_path, **{"Reel 01": ["a.mxf"], "Reel 26": ["logo_reveal.mov"]}
            ),
            "declaration": "full_frame_elements",
        },
    )
    assert passed is False
    assert "Reel 01" in evidence["reason"]
    # A measurement that never looked refuses rather than passing.
    for missing in (
        {"declaration": "full_frame_elements"},
        {"divergence": _survey(tmp_path)},
    ):
        passed, evidence = mr.verify(mr.CHECK_DECLARATION_REACHES, missing)
        assert passed is False
        assert "never read" in evidence["reason"]


def test_a_marker_is_NOT_cleared_when_the_reels_do_not_back_the_claim(tmp_path):
    """The 2026-09-11 failure, refused where it would have cleared."""
    note = _timeline_note()
    timeline = _timeline_with(note)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="declared the card for every reel",
        rationale="PR 995 landed the mechanism",
        check=mr.CHECK_DECLARATION_REACHES,
        measured={
            "divergence": _survey(tmp_path, **{"Reel 01": ["a.mxf"]}),
            "declaration": "full_frame_elements",
        },
        timeline=timeline,
    )
    assert result["record"]["status"] == mr.STATUS_ADDRESSED_UNVERIFIED
    assert result["marker_touched"] is False
    assert len(timeline.GetMarkers()) == 1
