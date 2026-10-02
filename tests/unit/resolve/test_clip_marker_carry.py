"""Promotion carries clip-anchored markers, not just timeline ones.

A DaVinci marker lives either on the TIMELINE or on a CLIP ITEM, and
promotion carried the timeline plane only - so a clip-anchored marker
died with its item when a rebuild replaced it, and NOTHING REPORTED
THE LOSS. Proven 2026-09-19 on Reel 09: the CTA-animation note (blue,
the captain's verbatim words) had died that way on an earlier rebuild
and was reported nowhere until a lane tripped over it and restored it
by hand. Sixteen more reels needed the same restore.

These fakes stand in for Resolve; the API surface they answer is the
one `promote_staged_reels` drives. Every assertion is on marker TEXT
and anchor, never on counts alone: a carry that preserves the count
while attaching the words to the wrong item is the defect, not the
fix.
"""

from unittest.mock import patch

import pytest
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
    no_a_roll_track_plans,
)

from library.tools import marker_carry
from library.tools.reel_build import (
    promote_staged_reels,
)
from tests.resolve_double import FakeProject, FakeTimeline, timeline_item


@pytest.fixture(autouse=True)
def fake_preservation_snapshots(monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)


CTA_NOTE = "punch in on 'twenty percent' here"


def _retiring_cta():
    """Reel 09's shape: the blue CTA note on the motion-graphics card."""
    card = timeline_item(
        "cta card", 500, 560, path="/f/mg_cta.mov", left_offset=0,
        markers={12: {"color": "Blue", "name": "feedback",
                      "note": CTA_NOTE, "duration": 1,
                      "customData": ""}})
    body = timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF", left_offset=6505)
    return FakeTimeline("Reel 09", video=[
        ("Akshita", [body]),
        ("Motion Graphics", [card]),
    ])


def test_a_clip_marker_carries_by_file_not_by_timeline_frame():
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    # The rebuild moved the card seventy frames later; the file is the
    # same and still plays source frame 12.
    card = timeline_item("cta card", 570, 630, path="/f/mg_cta.mov", left_offset=0)
    replacement = FakeTimeline("staging", video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [card]),
    ])
    carried, uncarried = marker_carry.plan_clip_carry(
        notes, replacement, "Reel 09")
    assert not uncarried and len(carried) == 1
    assert carried[0]["note"] == CTA_NOTE
    assert carried[0]["to_source_frame"] == 12
    assert carried[0]["to_frame"] == 582
    assert carried[0]["target"]["source_file"] == "/f/mg_cta.mov"
    failed = marker_carry.place_clip_markers(replacement, carried)
    assert not failed
    assert card.placed == [(12, "Blue", "feedback", CTA_NOTE, 1, "")]
    # And the placed marker reads back with the captain's words on the
    # same source frame of the same file.
    back = marker_carry.read_clip_markers(replacement, "Reel 09")
    assert len(back) == 1
    assert back[0]["note"] == CTA_NOTE
    assert back[0]["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert back[0]["source_frame"] == 12


def test_a_marker_on_the_audio_track_is_read():
    """The inventory held one note on the master MXF's audio track -
    the picture-rows-only filter would never see it."""
    wchar = timeline_item("master audio", 0, 600, path="/f/master.MXF", left_offset=0,
                      markers={300: {"color": "Blue", "name": "feedback",
                                     "note": "level dips here",
                                     "duration": 1, "customData": ""}})
    timeline = FakeTimeline("Reel 20", video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=0)])],
        audio=[("Master", [wchar])])
    notes = marker_carry.read_clip_markers(timeline, "Reel 20")
    assert len(notes) == 1
    assert notes[0]["note"] == "level dips here"
    assert notes[0]["anchor"]["track_type"] == "audio"
    assert notes[0]["anchor"]["source_file"] == "/f/master.MXF"


def test_two_placements_of_one_file_refuse_rather_than_guess(capsys):
    """The anchor that cannot be resolved uniquely is REPORTED, never
    placed: a marker silently re-anchored to the wrong item is worse
    than one honestly reported missing."""
    retiring = FakeTimeline("Reel 09", video=[
        ("Motion Graphics", [timeline_item(
            "cta card", 500, 560, path="/f/mg_cta.mov", left_offset=0,
            markers={12: {"color": "Blue", "name": "feedback",
                          "note": CTA_NOTE, "duration": 1,
                          "customData": ""}})]),
    ])
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    # The rebuild says the same card twice; both play source frame 12.
    replacement = FakeTimeline("staging", video=[
        ("Motion Graphics", [
            timeline_item("cta card", 500, 560, path="/f/mg_cta.mov", left_offset=0),
            timeline_item("cta card copy", 560, 620, path="/f/mg_cta.mov",
                      left_offset=0),
        ]),
    ])
    carried, uncarried = marker_carry.plan_clip_carry(
        notes, replacement, "Reel 09")
    assert not carried and len(uncarried) == 1
    assert uncarried[0]["note"] == CTA_NOTE
    assert "refusing to guess" in uncarried[0]["why"]
    marker_carry.report_clip("Reel 09", carried, uncarried)
    err = capsys.readouterr().err
    assert "CLIP MARKER NOT CARRIED" in err
    assert CTA_NOTE in err
    assert "/f/mg_cta.mov" in err
    for item in replacement.GetItemListInTrack("video", 1):
        assert item.placed == []


def test_a_marker_whose_file_is_gone_names_the_file(capsys):
    """A re-rendered overlay under a fresh content hash plays no source
    frame of the old file: the report names the file and the words."""
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    replacement = FakeTimeline("staging", video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item("cta card", 500, 560,
                                       path="/f/mg_cta_NEW.mov", left_offset=0)]),
    ])
    carried, uncarried = marker_carry.plan_clip_carry(
        notes, replacement, "Reel 09")
    assert not carried and len(uncarried) == 1
    assert uncarried[0]["note"] == CTA_NOTE
    assert "/f/mg_cta.mov" in uncarried[0]["why"]
    marker_carry.report_clip("Reel 09", carried, uncarried)
    err = capsys.readouterr().err
    assert "CLIP MARKER NOT CARRIED" in err
    assert CTA_NOTE in err


def test_a_pool_inherited_copy_is_not_carried_twice():
    """A pool marker seeds onto every placement at build time - the
    replacement's own items already inherit it, so carrying it again
    would only earn a decline for a marker that is already there."""
    pool_markers = {12: {"color": "Blue", "name": "feedback",
                         "note": CTA_NOTE, "duration": 1,
                         "customData": ""}}
    item = timeline_item("cta card", 500, 560, path="/f/mg_cta.mov",
                         markers=dict(pool_markers),
                         pool_markers=pool_markers)
    timeline = FakeTimeline("Reel 09", video=[("Motion Graphics", [item])])
    assert marker_carry.read_clip_markers(timeline, "Reel 09") == []


# ── Through the promotion ─────────────────────────────────────────
#
# The defect was never in the plan or the place alone: promotion read
# the timeline plane only. This drives `promote_staged_reels` with one
# reel whose clip note carries and one whose anchor is ambiguous, and
# asserts on TEXT and anchor at both ends.

FINAL_A = "Reel 09 - cta (final)"
FINAL_B = "Reel 17 - doubled card (final)"
MASTER = "Podcast - Synced"


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


def _promote(project, project_dir, staged_to_final):
    import json
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    with patch("library.tools.reel_build._connect_resolve_project",
               return_value=project):
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER, staged_to_final,
            organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final))


def _reel_a():
    retiring = FakeTimeline(FINAL_A, video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item(
            "cta card", 500, 560, path="/f/mg_cta.mov", left_offset=0,
            markers={12: {"color": "Blue", "name": "feedback",
                          "note": CTA_NOTE, "duration": 1,
                          "customData": ""}})]),
    ])
    staging = FakeTimeline(FINAL_A + " (rebuild staging)", video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item("cta card", 570, 630,
                                       path="/f/mg_cta.mov", left_offset=0)]),
    ])
    return retiring, staging


def _reel_b():
    retiring = FakeTimeline(FINAL_B, video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item(
            "doubled card", 100, 160, path="/f/mg_doubled.mov", left_offset=0,
            markers={20: {"color": "Blue", "name": "feedback",
                          "note": "this card flashes",
                          "duration": 1, "customData": ""}})]),
    ])
    # Same file twice, both playing source frame 20: no unique item.
    staging = FakeTimeline(FINAL_B + " (rebuild staging)", video=[
        ("Akshita", [timeline_item("Akshita A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [
            timeline_item("doubled card", 100, 160, path="/f/mg_doubled.mov",
                      left_offset=0),
            timeline_item("doubled card encore", 160, 220,
                      path="/f/mg_doubled.mov", left_offset=0),
        ]),
    ])
    return retiring, staging


def test_promotion_carries_the_unique_clip_note_and_reports_the_other(
        project_dir, capsys):
    retired_a, staging_a = _reel_a()
    retired_b, staging_b = _reel_b()
    project = FakeProject([FakeTimeline(MASTER), retired_a, staging_a,
                           retired_b, staging_b])

    promoted = _promote(project, project_dir,
                        {FINAL_A: staging_a.GetName(),
                         FINAL_B: staging_b.GetName()})

    assert sorted(promoted["promoted"]) == [FINAL_A, FINAL_B]

    markers_a = promoted["markers"][FINAL_A]
    assert [m["note"] for m in markers_a["clip_carried"]] == [CTA_NOTE]
    assert markers_a["clip_carried"][0]["target"]["source_file"] == \
        "/f/mg_cta.mov"
    assert markers_a.get("clip_uncarried", []) == []
    # The note is on the replacement's item, at the same source frame
    # of the same file - TEXT and anchor, never counts alone.
    back = marker_carry.read_clip_markers(staging_a, FINAL_A)
    assert [m["note"] for m in back] == [CTA_NOTE]
    assert back[0]["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert back[0]["source_frame"] == 12

    markers_b = promoted["markers"][FINAL_B]
    assert markers_b.get("clip_carried", []) == []
    assert [m["note"] for m in markers_b["clip_uncarried"]] == \
        ["this card flashes"]
    assert "refusing to guess" in \
        markers_b["clip_uncarried"][0]["why"]
    # Reported, never placed: no item on the replacement carries it.
    back_b = marker_carry.read_clip_markers(staging_b, FINAL_B)
    assert [m["note"] for m in back_b] == []
    err = capsys.readouterr().err
    assert "CLIP MARKER NOT CARRIED" in err
    assert "this card flashes" in err
