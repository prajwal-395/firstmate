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

from unittest.mock import MagicMock, patch

import pytest

from library.tools import marker_carry
from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)


class _Pool:
    def __init__(self, path, markers=None):
        self.path = path
        self._markers = dict(markers or {})

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else ""

    def GetMarkers(self):
        return dict(self._markers)


class _ClipItem:
    """One timeline item with a source file, an offset, and its markers."""

    def __init__(self, name, start, end, path, left=0, markers=None,
                 decline=()):
        self._name = name
        self._start = start
        self._end = end
        self._pool = _Pool(path) if path else None
        self._left = left
        self._markers = dict(markers or {})
        self._decline = set(decline)
        self.placed = []

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetLeftOffset(self):
        return self._left

    def GetMediaPoolItem(self):
        return self._pool

    def GetMarkers(self):
        return dict(self._markers)

    def AddMarker(self, frame, color, name, note, duration,
                  custom=""):
        if frame in self._decline:
            return False
        self._markers[int(frame)] = {
            "color": color, "name": name, "note": note,
            "duration": duration, "customData": custom}
        self.placed.append((frame, color, name, note, duration, custom))
        return True


class _BareItem:
    """A double without the marker API: nothing to read, nothing lost."""

    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start


class _Timeline:
    def __init__(self, name, video=(), audio=(), markers=None, start=0):
        self._name = name
        self._video = list(video)
        self._audio = list(audio)
        self._markers = dict(markers or {})
        self._start = start

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetStartFrame(self):
        return self._start

    def GetTrackCount(self, media):
        return len(self._video) if media == "video" else len(self._audio)

    def GetTrackName(self, media, index):
        rows = self._video if media == "video" else self._audio
        return rows[index - 1][0]

    def GetItemListInTrack(self, media, index):
        rows = self._video if media == "video" else self._audio
        return rows[index - 1][1]

    def GetMarkers(self):
        return dict(self._markers)

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self._markers[int(frame)] = {
            "color": color, "name": name, "note": note,
            "duration": duration, "customData": custom}
        return True


CTA_NOTE = "punch in on 'twenty percent' here"


def _retiring_cta():
    """Reel 09's shape: the blue CTA note on the motion-graphics card."""
    card = _ClipItem(
        "cta card", 500, 560, "/f/mg_cta.mov", left=0,
        markers={12: {"color": "Blue", "name": "feedback",
                      "note": CTA_NOTE, "duration": 1,
                      "customData": ""}})
    body = _ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF", left=6505)
    return _Timeline("Reel 09", video=[
        ("Akshita", [body]),
        ("Motion Graphics", [card]),
    ])


def test_the_anchor_is_the_source_file_and_frame():
    notes = marker_carry.read_clip_markers(_retiring_cta(), "Reel 09")
    assert len(notes) == 1
    note = notes[0]
    assert note["note"] == CTA_NOTE
    assert note["source_frame"] == 12
    assert note["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert note["anchor"]["source_frame"] == 12
    assert note["anchor"]["track_type"] == "video"
    assert note["frame"] == 512  # 500 + (12 - 0), never a clamp


def test_a_clip_marker_carries_by_file_not_by_timeline_frame():
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    # The rebuild moved the card seventy frames later; the file is the
    # same and still plays source frame 12.
    card = _ClipItem("cta card", 570, 630, "/f/mg_cta.mov", left=0)
    replacement = _Timeline("staging", video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=6505)]),
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
    wchar = _ClipItem("master audio", 0, 600, "/f/master.MXF", left=0,
                      markers={300: {"color": "Blue", "name": "feedback",
                                     "note": "level dips here",
                                     "duration": 1, "customData": ""}})
    timeline = _Timeline("Reel 20", video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=0)])],
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
    retiring = _Timeline("Reel 09", video=[
        ("Motion Graphics", [_ClipItem(
            "cta card", 500, 560, "/f/mg_cta.mov", left=0,
            markers={12: {"color": "Blue", "name": "feedback",
                          "note": CTA_NOTE, "duration": 1,
                          "customData": ""}})]),
    ])
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    # The rebuild says the same card twice; both play source frame 12.
    replacement = _Timeline("staging", video=[
        ("Motion Graphics", [
            _ClipItem("cta card", 500, 560, "/f/mg_cta.mov", left=0),
            _ClipItem("cta card copy", 560, 620, "/f/mg_cta.mov",
                      left=0),
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
    for row in replacement._video:
        for item in row[1]:
            assert item.placed == []


def test_a_marker_whose_file_is_gone_names_the_file(capsys):
    """A re-rendered overlay under a fresh content hash plays no source
    frame of the old file: the report names the file and the words."""
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    replacement = _Timeline("staging", video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=6505)]),
        ("Motion Graphics", [_ClipItem("cta card", 500, 560,
                                       "/f/mg_cta_NEW.mov", left=0)]),
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


def test_a_key_outside_what_the_clip_plays_is_kept_not_clamped():
    item = _ClipItem("cta card", 500, 560, "/f/mg_cta.mov", left=0,
                     markers={999: {"color": "Blue", "name": "feedback",
                                    "note": CTA_NOTE, "duration": 1,
                                    "customData": ""}})
    timeline = _Timeline("Reel 09", video=[("Motion Graphics", [item])])
    notes = marker_carry.read_clip_markers(timeline, "Reel 09")
    assert len(notes) == 1
    assert notes[0]["frame"] is None
    assert "outside" in notes[0]["unplaced_reason"]
    carried, uncarried = marker_carry.plan_clip_carry(
        notes, timeline, "Reel 09")
    assert not carried and uncarried[0]["note"] == CTA_NOTE


def test_a_pool_inherited_copy_is_not_carried_twice():
    """A pool marker seeds onto every placement at build time - the
    replacement's own items already inherit it, so carrying it again
    would only earn a decline for a marker that is already there."""
    pool_markers = {12: {"color": "Blue", "name": "feedback",
                         "note": CTA_NOTE, "duration": 1,
                         "customData": ""}}
    item = _ClipItem("cta card", 500, 560, "/f/mg_cta.mov", left=0,
                     markers=dict(pool_markers))
    item._pool = _Pool("/f/mg_cta.mov", markers=pool_markers)
    timeline = _Timeline("Reel 09", video=[("Motion Graphics", [item])])
    assert marker_carry.read_clip_markers(timeline, "Reel 09") == []


def test_a_marker_resolve_declines_is_named(capsys):
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    card = _ClipItem("cta card", 500, 560, "/f/mg_cta.mov", left=0,
                     decline={12})
    replacement = _Timeline("staging", video=[
        ("Motion Graphics", [card])])
    carried, uncarried = marker_carry.plan_clip_carry(
        notes, replacement, "Reel 09")
    assert len(carried) == 1
    failed = marker_carry.place_clip_markers(replacement, carried)
    assert len(failed) == 1 and failed[0]["note"] == CTA_NOTE
    assert "CLIP MARKER NOT CARRIED" in capsys.readouterr().err


def test_an_item_without_the_marker_api_reads_as_no_markers():
    timeline = _Timeline("Reel 09", video=[
        ("Akshita", [_BareItem("Akshita A", 0, 600)])])
    assert marker_carry.read_clip_markers(timeline, "Reel 09") == []


def test_an_unreadable_clip_row_refuses():
    class _Broken(_Timeline):
        def GetTrackCount(self, media):
            raise RuntimeError("Resolve is busy")

    with pytest.raises(marker_carry.MarkerCarryUnreadable,
                       match="could not be read"):
        marker_carry.read_clip_markers(_Broken("Reel 09"), "Reel 09")


# ── Through the promotion ─────────────────────────────────────────
#
# The defect was never in the plan or the place alone: promotion read
# the timeline plane only. This drives `promote_staged_reels` with one
# reel whose clip note carries and one whose anchor is ambiguous, and
# asserts on TEXT and anchor at both ends.

FINAL_A = "Reel 09 - cta (final)"
FINAL_B = "Reel 17 - doubled card (final)"
MASTER = "Podcast - Synced"


class FakeProject:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool
        self.deleted = []

    def _delete(self, timelines):
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
        return True

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]


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
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=project):
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER, staged_to_final,
            organise=False)


def _reel_a():
    retiring = _Timeline(FINAL_A, video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=6505)]),
        ("Motion Graphics", [_ClipItem(
            "cta card", 500, 560, "/f/mg_cta.mov", left=0,
            markers={12: {"color": "Blue", "name": "feedback",
                          "note": CTA_NOTE, "duration": 1,
                          "customData": ""}})]),
    ])
    staging = _Timeline(FINAL_A + " (rebuild staging)", video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=6505)]),
        ("Motion Graphics", [_ClipItem("cta card", 570, 630,
                                       "/f/mg_cta.mov", left=0)]),
    ])
    return retiring, staging


def _reel_b():
    retiring = _Timeline(FINAL_B, video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=6505)]),
        ("Motion Graphics", [_ClipItem(
            "doubled card", 100, 160, "/f/mg_doubled.mov", left=0,
            markers={20: {"color": "Blue", "name": "feedback",
                          "note": "this card flashes",
                          "duration": 1, "customData": ""}})]),
    ])
    # Same file twice, both playing source frame 20: no unique item.
    staging = _Timeline(FINAL_B + " (rebuild staging)", video=[
        ("Akshita", [_ClipItem("Akshita A", 0, 600, "/f/LC4932.MXF",
                               left=6505)]),
        ("Motion Graphics", [
            _ClipItem("doubled card", 100, 160, "/f/mg_doubled.mov",
                      left=0),
            _ClipItem("doubled card encore", 160, 220,
                      "/f/mg_doubled.mov", left=0),
        ]),
    ])
    return retiring, staging


def test_promotion_carries_the_unique_clip_note_and_reports_the_other(
        project_dir, capsys):
    retired_a, staging_a = _reel_a()
    retired_b, staging_b = _reel_b()
    project = FakeProject([_Timeline(MASTER), retired_a, staging_a,
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


def test_promotion_still_passes_timelines_without_clip_markers(
        project_dir):
    """The old fakes (items without the marker API) promote exactly
    as before: no clip markers read, nothing carried, nothing lost."""
    retired = _Timeline(FINAL_A, video=[
        ("Akshita", [_BareItem("Akshita A", 0, 600)])])
    staging = _Timeline(FINAL_A + " (rebuild staging)", video=[
        ("Akshita", [_BareItem("Akshita A", 0, 600)])])
    project = FakeProject([_Timeline(MASTER), retired, staging])

    promoted = _promote(project, project_dir,
                        {FINAL_A: staging.GetName()})

    assert promoted["promoted"] == [FINAL_A]
    assert FINAL_A not in promoted["markers"]
