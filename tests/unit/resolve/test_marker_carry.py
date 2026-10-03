"""A promotion says what it is about to do to the captain's words.

Reel 09, 2026-09-09: three typed markers, a rebuild, and
`marker_feedback show` reporting "0 note(s)". They were not cleared -
promotion replaced the timeline object and they went with it, and
nothing said so. These fakes stand in for Resolve; the API surface
they answer is the one `promote_staged_reels` drives.
"""
from library.tools import marker_carry
from unittest.mock import patch
import pytest
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
    no_a_roll_track_plans,
)
from library.tools.reel_build import (
    promote_staged_reels,
)
from tests.resolve_double import FakeProject, FakeTimeline, timeline_item
import json
from library.tools import reel_signoff as signoff
from library.tools import uncarried_notes as owed
from library.tools import feedback_ledger as ledger
import subprocess
import sys
from library.tools import marker_gate
from library.tools.reel_build import (
    ReelBuildError,
)
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeTimelineItem,
)
import importlib.util
from pathlib import Path


class _Pool:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else ""


class _Item:
    def __init__(self, start, end, path, left=0):
        self._start, self._end = start, end
        self._pool = _Pool(path) if path else None
        self._left = left

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetLeftOffset(self):
        return self._left

    def GetMediaPoolItem(self):
        return self._pool


class _Timeline:
    def __init__(self, rows, markers=None, start=0, decline=()):
        self._rows = rows
        self._markers = dict(markers or {})
        self._start = start
        self._decline = set(decline)
        self.added = []

    def GetStartFrame(self):
        return self._start

    def GetTrackCount(self, media):
        return len(self._rows) if media == "video" else 0

    def GetTrackName(self, media, index):
        return self._rows[index - 1][0]

    def GetItemListInTrack(self, media, index):
        return self._rows[index - 1][1]

    def GetMarkers(self):
        return self._markers

    def AddMarker(self, frame, color, name, note, duration, custom):
        if frame in self._decline:
            return False
        self.added.append((frame, color, name, note, duration, custom))
        return True


def _retiring():
    """The captain's Reel 13: SpeakerOne on V1, SpeakerTwo on V2, his Blue
    `feedback` marker at 1909 - the frame he typed it on."""
    return _Timeline(
        [("SpeakerOne", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)]),
         ("SpeakerTwo", [_Item(1909, 1921, "/f/LCATL0013.MXF", left=50)])],
        markers={1700: {"color": "Blue", "name": "feedback",
                        "note": "it cuts to speakertwo here at the end",
                        "duration": 1, "customData": "cd"}})


def test_the_anchor_is_the_picture_not_the_frame_number():
    timeline = _retiring()
    assert marker_carry.picture_at(timeline, 1700) == (
        "/f/LC4932.MXF", 1000 + (1700 - 1599))
    # The topmost row wins - what the viewer is actually looking at.
    stacked = _Timeline(
        [("under", [_Item(0, 100, "/f/a.mov")]),
         ("over", [_Item(0, 100, "/f/b.mov")])])
    assert marker_carry.picture_at(stacked, 10)[0] == "/f/b.mov"


def test_a_marker_carries_to_the_frame_showing_the_same_picture():
    retiring = _retiring()
    notes = marker_carry.read_markers(retiring, "Reel 13")
    assert len(notes) == 1 and notes[0]["note"].startswith("it cuts")
    # The rebuild moved everything twenty frames earlier.
    replacement = _Timeline(
        [("SpeakerOne", [_Item(1579, 1889, "/f/LC4932.MXF", left=1000)])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not uncarried and len(carried) == 1
    assert carried[0]["to_frame"] == 1680
    failed = marker_carry.place(replacement, carried)
    assert not failed
    assert replacement.added == [
        (1680, "Blue", "feedback", "it cuts to speakertwo here at the end", 1,
         "cd")]

    # A reel that plays one source twice has not moved the note to the
    # other saying of it: source frame 50 plays at reel 50 and at reel
    # 190, and 190 is nearer the marker's own 150.
    retiring = _Timeline(
        [("A", [_Item(100, 200, "/f/a.mov", left=0)])],
        markers={150: {"color": "Blue", "name": "n", "note": "x",
                       "duration": 1}})
    replacement = _Timeline([("A", [
        _Item(0, 100, "/f/a.mov", left=0),
        _Item(140, 240, "/f/a.mov", left=0)])])
    carried, _ = marker_carry.plan_carry(
        marker_carry.read_markers(retiring, "Reel 13"), replacement)
    assert carried[0]["to_frame"] == 190

    # Reel 04: the rebuild extended the body and re-rendered every
    # overlay under fresh content hashes; the footage did not move, so
    # the verdict anchored to picture stays at 65.
    replacement = _Timeline(
        [("SpeakerOne", [_Item(65, 511, "/f/LC4932.MXF", left=6505)]),
         ("SpeakerTwo", [_Item(0, 65, "/f/LCATL0013.MXF", left=6348)]),
         ("Frame", [_Item(0, 716, "/f/tv_frame.mov")]),
         ("Subtitles", [_Item(0, 32, "/f/sub.mov")]),
         ("Semantic", [_Item(0, 108, "/f/mg_new.mov")]),
         ("Motion Graphics", [_Item(0, 152, "/f/mg_new2.mov")])])
    carried, uncarried = marker_carry.plan_carry(
        marker_carry.read_markers(_reel04(), "Reel 04"), replacement)
    assert not uncarried and len(carried) == 1
    assert carried[0]["to_frame"] == 65


def test_a_marker_whose_picture_is_gone_is_named_not_dropped(capsys):
    """The whole point. A note on a shot the rebuild removed is
    REPORTED, with the captain's words, and left uncarried."""
    retiring = _Timeline(
        [("SpeakerTwo", [_Item(1909, 1921, "/f/LCATL0013.MXF", left=50)])],
        markers={1915: {"color": "Blue", "name": "feedback",
                        "note": "this cut is jarring", "duration": 1}})
    notes = marker_carry.read_markers(retiring, "Reel 13")
    replacement = _Timeline(
        [("SpeakerOne", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not carried and len(uncarried) == 1
    marker_carry.report("Reel 13", carried, uncarried)
    err = capsys.readouterr().err
    assert "MARKER NOT CARRIED" in err
    assert "this cut is jarring" in err


def _reel04():
    """Reel 04's shape at the 2026-09-19 rebuild: SpeakerOne body on V1
    from frame 65, a motion-graphics overlay across V5/V6 with
    per-build content hashes, and the pink verdict at 65 - the first
    body frame, under the overlay."""
    return _Timeline(
        [("SpeakerOne", [_Item(65, 395, "/f/LC4932.MXF", left=6505)]),
         ("SpeakerTwo", [_Item(0, 65, "/f/LCATL0013.MXF", left=6348)]),
         ("Frame", [_Item(0, 600, "/f/tv_frame.mov")]),
         ("Subtitles", [_Item(0, 32, "/f/sub.mov")]),
         ("Semantic", [_Item(0, 108, "/f/mg_old.mov")]),
         ("Motion Graphics", [_Item(0, 152, "/f/mg_old2.mov")])],
        markers={65: {"color": "Pink", "name": "verdict (firstmate)",
                      "note": "FIXABLE", "duration": 444,
                      "customData": "cd"}})


# ── Replies re-pair with their notes, by identity ───────────────────
#
# Reel 14, 2026-09-20: the blue sat at 162, our green answered at 163.
# The rebuild carried the blue to 461 and left the green at 163. Every
# test below replays that shape: the reply names its note by durable
# identity plus picture anchor, never by frame.


def _reply_payload(timeline_name, name, note, anchor):
    from library.tools import marker_feedback as _feedback
    from library.tools.feedback_ledger import (
        durable_identity as _identity)
    text = "\n\n".join(part for part in (name, note) if part)
    return _feedback.reply_custom_data(
        "", _identity(timeline_name, text), note, "",
        {"source_file": anchor[0],
         "source_frame": anchor[1]} if anchor else None)


def _reel14_retiring():
    """Blue at 162 on (/f/mid.MXF @5000), green at 163 answering it."""
    anchor = ("/f/mid.MXF", 5000)
    payload = _reply_payload(
        "Reel 14", "feedback", "there is like no value prop given",
        anchor)
    return _Timeline(
        [("V1", [_Item(0, 459, "/f/mid.MXF", left=4838)])],
        markers={
            162: {"color": "Blue", "name": "feedback",
                  "note": "there is like no value prop given",
                  "duration": 1, "customData": ""},
            163: {"color": "Green", "name": "reply: done",
                  "note": "added the missing middle",
                  "duration": 1, "customData": payload}})


def _reel14_rebuilt():
    """The structure wave's Reel 14: the same source frame at 461."""
    return _Timeline(
        [("V1", [_Item(300, 1057, "/f/mid.MXF", left=4839)])])


def test_a_reply_follows_its_note_to_the_new_frame():
    notes = marker_carry.read_markers(_reel14_retiring(), "Reel 14")
    carried, uncarried = marker_carry.plan_carry(
        notes, _reel14_rebuilt(), "Reel 14")
    assert not uncarried
    by_frame = {c["frame"]: c for c in carried}
    assert by_frame[162]["to_frame"] == 461
    assert by_frame[163]["to_frame"] == 462
    assert by_frame[163]["pairing"] == "repaired"
    assert by_frame[163]["paired_with"] == 162
    # Carried asks come before carried replies, so the note lands
    # before the answer that follows it.
    assert [c["frame"] for c in carried] == [162, 163]


def test_a_reply_whose_note_is_uncarried_is_reported_alongside_it():
    notes = marker_carry.read_markers(_reel14_retiring(), "Reel 14")
    gone = _Timeline([("V1", [_Item(0, 459, "/f/other.MXF", left=0)])])
    carried, uncarried = marker_carry.plan_carry(notes, gone, "Reel 14")
    assert not carried and len(uncarried) == 2
    stranded = next(u for u in uncarried if u["frame"] == 163)
    assert stranded["pairing"] == "stranded"
    assert stranded["reply_of"] == 162
    assert "@162" in stranded["why"] and "NOT CARRIED" in stranded["why"]


def test_a_reply_loses_the_frame_its_note_already_took(capsys):
    """One marker per frame: the reply yields and says so."""
    retiring = _Timeline(
        [("V1", [_Item(0, 459, "/f/mid.MXF", left=4838)]),
         ("V2", [_Item(164, 200, "/f/other.MXF", left=0)])],
        markers={
            162: {"color": "Blue", "name": "feedback",
                  "note": "there is like no value prop given",
                  "duration": 1, "customData": ""},
            163: {"color": "Green", "name": "reply: done",
                  "note": "added the missing middle",
                  "duration": 1, "customData": _reply_payload(
                      "Reel 14", "feedback",
                      "there is like no value prop given",
                      ("/f/mid.MXF", 5000))},
            164: {"color": "Blue", "name": "feedback",
                  "note": "a later note", "duration": 1,
                  "customData": ""}})
    notes = marker_carry.read_markers(retiring, "Reel 14")
    # The blue re-pairs to 461, so its reply wants 462 - but the later
    # note's own picture lands at 462, and one marker per frame wins.
    replacement = _Timeline(
        [("V1", [_Item(300, 462, "/f/mid.MXF", left=4839)]),
         ("V2", [_Item(462, 600, "/f/other.MXF", left=0)])])
    carried, uncarried = marker_carry.plan_carry(
        notes, replacement, "Reel 14")
    green = next((u for u in uncarried if u["frame"] == 163), None)
    assert green is not None and green["pairing"] == "stranded"
    assert "already taken" in green["why"]
    marker_carry.report("Reel 14", carried, uncarried)
    assert "REPLY NOT CARRIED" in capsys.readouterr().err


def test_the_anchor_picks_which_same_words_note_is_answered():
    """Two blues, one sentence, two moments: the recorded anchor wins."""
    retiring = _Timeline(
        [("V1", [_Item(0, 150, "/f/a.mov", left=0),
                 _Item(150, 300, "/f/b.mov", left=0)])],
        markers={
            100: {"color": "Blue", "name": "feedback", "note": "trim",
                  "duration": 1, "customData": ""},
            200: {"color": "Blue", "name": "feedback", "note": "trim",
                  "duration": 1, "customData": ""},
            201: {"color": "Green", "name": "reply: done",
                  "note": "trimmed the second",
                  "duration": 1, "customData": _reply_payload(
                      "Reel X", "feedback", "trim", ("/f/b.mov", 50))}})
    notes = marker_carry.read_markers(retiring, "Reel X")
    replacement = _Timeline(
        [("V1", [_Item(500, 650, "/f/a.mov", left=0),
                 _Item(650, 800, "/f/b.mov", left=0)])])
    carried, _ = marker_carry.plan_carry(notes, replacement, "Reel X")
    green = next(c for c in carried if c["frame"] == 201)
    assert green["paired_with"] == 200
    assert green["to_frame"] == 701

    # Same words, different picture (a relinked file): nearest wins,
    # flagged rather than exact.
    retiring._markers[201]["customData"] = _reply_payload(
        "Reel X", "feedback", "trim", ("/f/renamed.mov", 7))
    carried, _ = marker_carry.plan_carry(
        marker_carry.read_markers(retiring, "Reel X"), replacement, "Reel X")
    green = next(c for c in carried if c["frame"] == 201)
    assert green["paired_with"] == 200
    assert "anchor_mismatch" in green["pairing_flags"]


def test_the_audit_finds_the_reel14_stranding():
    """Live now: blue correctly carried to 461, green stranded at 163."""
    anchor = ("/f/mid.MXF", 5000)
    payload = _reply_payload(
        "Reel 14", "feedback", "there is like no value prop given",
        anchor)
    live = [
        {"frame": 163, "color": "Green", "name": "reply: done",
         "note": "added the missing middle", "duration": 1,
         "custom_data": payload, "anchor": ("/f/mid.MXF", 5001)},
        {"frame": 461, "color": "Blue", "name": "feedback",
         "note": "there is like no value prop given", "duration": 1,
         "custom_data": "", "anchor": anchor},
    ]
    rows = marker_carry.audit_replies(live, "Reel 14")
    assert len(rows) == 1
    assert rows[0]["status"] == "paired-drifted"
    assert rows[0]["ask_frame"] == 461
    assert rows[0]["distance"] == 163 - 462


# ── A marker the staged duplicate already carries is not lost ───────
#
# 2026-09-25: DuplicateTimeline keeps every marker, the carry re-adds
# each one, Resolve declines a second marker at the same frame, and 34
# "NOT CARRIED" lines read as the captain's notes being lost while every
# one was present exactly once (history: docs/evidence/marker_carry.md).


class _Occupied:
    """A target whose AddMarker declines: the frame already carries one."""

    def __init__(self, existing):
        self._existing = existing

    def GetStartFrame(self):
        return 0

    def AddMarker(self, *args):
        return False

    def GetMarkers(self):
        return self._existing


def test_an_identical_marker_already_there_is_not_a_loss_but_another_is():
    marker = {"to_frame": 523, "to_source_frame": 40, "color": "Blue",
              "name": "feedback", "note": "fix the fluff here", "duration": 1}
    there = {523: {"color": "Blue", "name": "feedback",
                   "note": "fix the fluff here"}}
    assert marker_carry.place(_Occupied(there), [marker]) == []
    # A DIFFERENT marker at that frame must still be reported.
    there = {523: {"color": "Green", "name": "rebuild (firstmate)",
                   "note": "something else"}}
    assert marker_carry.place(_Occupied(there), [marker]) == [marker]


# --------------------------------------------------------------------------
# From test_clip_marker_carry.py
#
# Promotion carries clip-anchored markers, not just timeline ones.
#
# A DaVinci marker lives either on the TIMELINE or on a CLIP ITEM, and
# promotion carried the timeline plane only - so a clip-anchored marker
# died with its item when a rebuild replaced it, and NOTHING REPORTED
# THE LOSS. Proven 2026-09-19 on Reel 09: the CTA-animation note (blue,
# the captain's verbatim words) had died that way on an earlier rebuild
# and was reported nowhere until a lane tripped over it and restored it
# by hand. Sixteen more reels needed the same restore.
#
# These fakes stand in for Resolve; the API surface they answer is the
# one `promote_staged_reels` drives. Every assertion is on marker TEXT
# and anchor, never on counts alone: a carry that preserves the count
# while attaching the words to the wrong item is the defect, not the
# fix.

@pytest.fixture
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
    body = timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF", left_offset=6505)
    return FakeTimeline("Reel 09", video=[
        ("SpeakerOne", [body]),
        ("Motion Graphics", [card]),
    ])


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_clip_marker_carries_by_file_not_by_timeline_frame():
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    # The rebuild moved the card seventy frames later; the file is the
    # same and still plays source frame 12.
    card = timeline_item("cta card", 570, 630, path="/f/mg_cta.mov", left_offset=0)
    replacement = FakeTimeline("staging", video=[
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
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


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_marker_on_the_audio_track_is_read():
    """The inventory held one note on the master MXF's audio track -
    the picture-rows-only filter would never see it."""
    wchar = timeline_item("master audio", 0, 600, path="/f/master.MXF", left_offset=0,
                      markers={300: {"color": "Blue", "name": "feedback",
                                     "note": "level dips here",
                                     "duration": 1, "customData": ""}})
    timeline = FakeTimeline("Reel 20", video=[
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=0)])],
        audio=[("Master", [wchar])])
    notes = marker_carry.read_clip_markers(timeline, "Reel 20")
    assert len(notes) == 1
    assert notes[0]["note"] == "level dips here"
    assert notes[0]["anchor"]["track_type"] == "audio"
    assert notes[0]["anchor"]["source_file"] == "/f/master.MXF"


@pytest.mark.usefixtures("fake_preservation_snapshots")
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


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_marker_whose_file_is_gone_names_the_file(capsys):
    """A re-rendered overlay under a fresh content hash plays no source
    frame of the old file: the report names the file and the words."""
    retiring = _retiring_cta()
    notes = marker_carry.read_clip_markers(retiring, "Reel 09")
    replacement = FakeTimeline("staging", video=[
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
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


@pytest.mark.usefixtures("fake_preservation_snapshots")
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
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item(
            "cta card", 500, 560, path="/f/mg_cta.mov", left_offset=0,
            markers={12: {"color": "Blue", "name": "feedback",
                          "note": CTA_NOTE, "duration": 1,
                          "customData": ""}})]),
    ])
    staging = FakeTimeline(FINAL_A + " (rebuild staging)", video=[
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item("cta card", 570, 630,
                                       path="/f/mg_cta.mov", left_offset=0)]),
    ])
    return retiring, staging


def _reel_b():
    retiring = FakeTimeline(FINAL_B, video=[
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [timeline_item(
            "doubled card", 100, 160, path="/f/mg_doubled.mov", left_offset=0,
            markers={20: {"color": "Blue", "name": "feedback",
                          "note": "this card flashes",
                          "duration": 1, "customData": ""}})]),
    ])
    # Same file twice, both playing source frame 20: no unique item.
    staging = FakeTimeline(FINAL_B + " (rebuild staging)", video=[
        ("SpeakerOne", [timeline_item("SpeakerOne A", 0, 600, path="/f/LC4932.MXF",
                               left_offset=6505)]),
        ("Motion Graphics", [
            timeline_item("doubled card", 100, 160, path="/f/mg_doubled.mov",
                      left_offset=0),
            timeline_item("doubled card encore", 160, 220,
                      path="/f/mg_doubled.mov", left_offset=0),
        ]),
    ])
    return retiring, staging


@pytest.mark.usefixtures("fake_preservation_snapshots")
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


# --------------------------------------------------------------------------
# From test_uncarried_blue_back.py
#
# Uncarried notes get their Blue back, at the seam, byte-identical.
#
# History: docs/evidence/resolve_test_history.md#test_uncarried_blue_back.

@pytest.fixture
def mock_dvr(stub_resolve_script):
    yield


FINAL = "Reel 08 - moment (final)"
PATH = "/footage/LC4932.MXF"


class _Item_2:
    def __init__(self, name, start, end, path, left=0):
        self._name = name
        self._start = start
        self._end = end
        self._pool = _Pool(path) if path else None
        self._left = left

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetClipEnabled(self):
        return True

    def GetLeftOffset(self):
        return self._left

    def GetMediaPoolItem(self):
        return self._pool


class _Timeline_2:
    """Resolve declined on an occupied frame, and so does this fake."""

    def __init__(self, name, rows, markers=None):
        self._name = name
        self._rows = list(rows)
        self._markers = dict(markers or {})
        self.added = []

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        end = 0
        for _name, items in self._rows:
            for item in items:
                end = max(end, item.GetEnd())
        return end

    def GetTrackCount(self, media):
        if media == "video":
            return len(self._rows)
        return 0

    def GetTrackName(self, media, index):
        return self._rows[index - 1][0]

    def GetItemListInTrack(self, media, index):
        return self._rows[index - 1][1]

    def GetMarkers(self):
        return dict(self._markers)

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        if frame in self._markers or any(
                added[0] == frame for added in self.added):
            return False
        self.added.append((frame, color, name, note, duration, custom))
        return True


WORDS = "this pause before the answer is dead air - cut it"


def _retiring_2():
    """Reel 08 before the cut: A runs 30808..30827, B runs the note's
    span 31016..31107, and his Blue sits at 640 duration 81 over B."""
    return _Timeline_2(
        FINAL,
        [("SpeakerOne", [_Item_2("SpeakerOne A", 600, 639, PATH, left=30788),
                       _Item_2("SpeakerOne B", 639, 730, PATH, left=31016)])],
        markers={640: {"color": "Blue", "name": "trim?",
                       "note": WORDS, "duration": 81,
                       "customData": ""}})


def _replacement():
    """Reel 08 after the cut: A survives, then a jump to 31097 - the
    anchored span is entirely gone and the seam is frame 639."""
    return _Timeline_2(
        "staging",
        [("SpeakerOne", [_Item_2("SpeakerOne A", 600, 639, PATH, left=30788),
                       _Item_2("SpeakerOne C", 639, 700, PATH, left=31097)])])


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_removed_span_comes_back_as_blue_at_the_seam(capsys):
    retiring, replacement = _retiring_2(), _replacement()
    notes = marker_carry.read_markers(retiring, FINAL)
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not carried and len(uncarried) == 1

    plans = marker_carry.plan_seams(uncarried, retiring, replacement)
    assert len(plans) == 1
    assert plans[0]["plan"]["seam"] == 639
    assert plans[0]["plan"]["ambiguous"] is False

    placed, declined = marker_carry.place_uncarried(
        replacement, plans, FINAL)
    assert not declined and len(placed) == 1

    blue = [added for added in replacement.added
            if added[1] == "Blue"]
    assert len(blue) == 1
    frame, color, name, note, duration, _custom = blue[0]
    assert frame == 639
    assert color == "Blue"
    assert name == "trim?"
    assert note == WORDS
    assert note == uncarried[0]["note"]
    assert duration == 1

    reply = [added for added in replacement.added
             if added[1] == "Green"]
    assert len(reply) == 1
    assert reply[0][0] == 640
    assert reply[0][2].startswith("re:")
    assert "639" in reply[0][3]
    assert WORDS not in reply[0][3]

    out = capsys.readouterr()
    assert "re-placed as Blue" in out.out


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_surviving_picture_still_carries_unchanged():
    """The cut the captain approved around his note: same picture,
    shifted twenty frames earlier - carried as today, no Blue added,
    no reply written."""
    retiring = _retiring_2()
    notes = marker_carry.read_markers(retiring, FINAL)
    shifted = _Timeline_2(
        "staging",
        [("SpeakerOne", [_Item_2("SpeakerOne A", 580, 619, PATH, left=30788),
                       _Item_2("SpeakerOne B", 619, 710, PATH, left=31016)])])
    carried, uncarried = marker_carry.plan_carry(notes, shifted)
    assert len(carried) == 1 and not uncarried
    assert carried[0]["to_frame"] == 620
    assert marker_carry.place(shifted, carried) == []
    assert shifted.added == [(620, "Blue", "trim?", WORDS, 81, "")]
    plans = marker_carry.plan_seams(uncarried, retiring, shifted)
    placed, declined = marker_carry.place_uncarried(
        shifted, plans, FINAL)
    assert (placed, declined) == ([], [])


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_ambiguous_seam_goes_at_the_replacing_item_and_says_so():
    """Nothing before the cut survives: the note goes at the start of
    the replacing item, and the reply says the seam was ambiguous."""
    retiring = _Timeline_2(
        FINAL,
        [("SpeakerOne", [_Item_2("gone", 0, 100, PATH, left=5000),
                       _Item_2("kept", 100, 200, PATH, left=9000)])],
        markers={10: {"color": "Blue", "name": "look",
                      "note": "this opening drags", "duration": 10,
                      "customData": ""}})
    replacement = _Timeline_2(
        "staging",
        [("SpeakerOne", [_Item_2("new", 0, 50, "/footage/OTHER.MXF", left=0),
                       _Item_2("kept", 50, 150, PATH, left=9000)])])
    notes = marker_carry.read_markers(retiring, FINAL)
    _, uncarried = marker_carry.plan_carry(notes, replacement)
    assert len(uncarried) == 1
    plans = marker_carry.plan_seams(uncarried, retiring, replacement)
    assert plans[0]["plan"]["ambiguous"] is True
    assert plans[0]["plan"]["seam"] == 50
    placed, declined = marker_carry.place_uncarried(
        replacement, plans, FINAL)
    assert not declined and len(placed) == 1
    blue = [added for added in replacement.added if added[1] == "Blue"]
    assert blue[0][0] == 50
    assert blue[0][3] == "this opening drags"
    reply = [added for added in replacement.added if added[1] == "Green"]
    assert len(reply) == 1
    assert "ambiguous" in reply[0][3]


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_an_occupied_seam_declines_the_blue_and_reports(capsys):
    """A byte-identical Blue at the wrong key is not a restored
    marker: when the seam is occupied the decline is named with his
    words, and only then does the reply carry them."""
    retiring, replacement = _retiring_2(), _replacement()
    replacement._markers[639] = {"color": "Green", "name": "other",
                                 "note": "someone else", "duration": 1,
                                 "customData": ""}
    notes = marker_carry.read_markers(retiring, FINAL)
    _, uncarried = marker_carry.plan_carry(notes, replacement)
    plans = marker_carry.plan_seams(uncarried, retiring, replacement)
    placed, declined = marker_carry.place_uncarried(
        replacement, plans, FINAL)
    assert not placed and len(declined) == 1
    assert declined[0]["seam"] == 639
    err = capsys.readouterr().err
    assert "MARKER NOT RE-PLACED" in err
    assert WORDS in err
    reply = [added for added in replacement.added if added[1] == "Green"]
    assert len(reply) == 1
    assert WORDS in reply[0][3]


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_pictureless_replacement_has_no_seam(capsys):
    retiring = _retiring_2()
    replacement = _Timeline_2("staging", [])
    notes = marker_carry.read_markers(retiring, FINAL)
    _, uncarried = marker_carry.plan_carry(notes, replacement)
    plans = marker_carry.plan_seams(uncarried, retiring, replacement)
    assert plans[0]["plan"] is None
    placed, declined = marker_carry.place_uncarried(
        replacement, plans, FINAL)
    assert not placed and len(declined) == 1
    assert "MARKER NOT RE-PLACED" in capsys.readouterr().err


class _Project:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        self.deleted = []

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        project = self

        class _PoolApi:
            def DeleteTimelines(self, timelines):
                for timeline in timelines:
                    project.deleted.append(timeline.GetName())
                    project.timelines.remove(timeline)
                return True

        return _PoolApi()

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_promotion_puts_the_blue_back_at_the_seam(tmp_path):
    """End to end through `promote_staged_reels`: the cut removes the
    anchored span, and the promoted timeline carries his Blue at 639
    byte-identical with the Green reply beside it - asserted on TEXT
    and colour, never on counts alone."""
    retired, staging = _retiring_2(), _replacement()
    staging._name = FINAL + " (rebuild staging)"
    resolve = _Project([_Timeline_2(MASTER, []), retired, staging])
    staged_to_final = {FINAL: staging.GetName()}
    project_dir = tmp_path / "project"
    review = project_dir / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "plan_provenance.json").write_text(json.dumps(
        {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")

    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve), \
            patch("library.tools.reel_build.timelines_to_replace",
                  side_effect=lambda project, names: [
                      t for t in project.timelines
                      if t.GetName() in set(names)]):
        record = promote_staged_reels(
            str(project_dir), "Mock Project", MASTER,
            staged_to_final, organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final))

    markers = record["markers"][FINAL]
    assert len(markers["uncarried"]) == 1
    assert len(markers["replaced"]) == 1
    assert markers["replaced"][0]["seam"] == 639
    assert markers["replace_declined"] == []

    blue = [added for added in staging.added if added[1] == "Blue"]
    assert len(blue) == 1
    assert blue[0][0] == 639
    assert blue[0][2] == "trim?"
    assert blue[0][3] == WORDS
    reply = [added for added in staging.added if added[1] == "Green"]
    assert len(reply) == 1
    assert reply[0][0] == 640


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_stranded_reply_is_reported_never_re_filed_as_blue(capsys):
    """Our green answered the note the cut removed: it is reported
    alongside its note, and the seam takes the genuine note only.

    Re-placing the stranded reply would file our answer text as a Blue
    note of his with a fresh Green beside it. Asserted on TEXT and
    colour: no Blue may carry our reply's words."""
    from library.tools import marker_feedback as _feedback
    from library.tools.feedback_ledger import (
        durable_identity as _identity)
    retiring = _retiring_2()
    retiring._markers[641] = {
        "color": "Green", "name": "reply: trimmed",
        "note": "trimmed the dead air per your note",
        "duration": 1, "customData": _feedback.reply_custom_data(
            "", _identity(FINAL, "trim?\n\n" + WORDS), WORDS, "")}
    replacement = _replacement()
    notes = marker_carry.read_markers(retiring, FINAL)
    carried, uncarried = marker_carry.plan_carry(
        notes, replacement, FINAL)
    assert not carried
    stranded = next(u for u in uncarried if u["frame"] == 641)
    assert stranded["pairing"] == "stranded"
    assert stranded["reply_of"] == 640
    marker_carry.report(FINAL, carried, uncarried)
    assert "REPLY NOT CARRIED" in capsys.readouterr().err

    # The seam filter promotion applies: genuine notes only.
    seam_input = [m for m in uncarried
                  if m.get("pairing") != "stranded"]
    assert [m["frame"] for m in seam_input] == [640]
    plans = marker_carry.plan_seams(seam_input, retiring, replacement)
    placed, declined = marker_carry.place_uncarried(
        replacement, plans, FINAL)
    assert not declined and len(placed) == 1
    blue = [added for added in replacement.added
            if added[1] == "Blue"]
    assert len(blue) == 1
    assert blue[0][3] == WORDS
    assert "trimmed the dead air" not in blue[0][3]


# --------------------------------------------------------------------------
# From test_uncarried_notes.py
#
# A dropped captain's note is an obligation, not a log line.
#
# History: docs/evidence/resolve_test_history.md#test_uncarried_notes.

REEL = "Reel 03 - the-blue-that-would-not-carry"
OTHER = "Reel 08 - the-take-he-asked-away"
WORDS_2 = "the talking-head holds too long, tighten by a breath"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


def _ask(frame=162, name="feedback", note=WORDS_2, color="Blue"):
    return {
        "frame": frame,
        "color": color,
        "name": name,
        "note": note,
        "duration": 1,
        "custom_data": "",
        "anchor": ("/footage/speakerone-a.mp4", 4410),
        "why": "its anchor picture is in the replacement nowhere",
    }


def _markers(final=REEL, uncarried=(), replaced=(), declined=()):
    return {final: {
        "carried": [],
        "uncarried": list(uncarried),
        "replaced": list(replaced),
        "replace_declined": list(declined),
    }}


# ── The durable record ────────────────────────────────────────────

def test_dropped_note_leaves_a_record_naming_reel_and_quoting_words(project):
    """Content, not count: the reel by base name and his words verbatim."""
    ask = _ask()
    report = owed.record(str(project), _markers(uncarried=[ask]))
    assert len(report["filed"]) == 1

    document = owed.read_notes(str(project))
    entry = document["open"][report["filed"][0]]
    assert entry["reel"] == REEL
    assert WORDS_2 in entry["text"]
    assert entry["name"] == "feedback"
    assert entry["note"] == WORDS_2
    assert entry["frame"] == 162
    assert entry["why"] == ask["why"]
    assert entry["discharged"] is None
    # The identity is the durable one - reel plus words, nothing a
    # rebuild moves - so it rejoins the ledger's own reading.
    assert entry["identity"] == ledger.durable_identity(REEL, entry["text"])


def test_the_record_carries_the_seam_outcome(tmp_path):
    """Where the Blue went back rides along, so the discharger can find
    it; a Blue the seam could not place leaves the record as the words."""
    for name, extra, check in (
        ("replaced", {"seam": 88, "ambiguous": False,
                      "explanation": "the cut removed it; the join is 88",
                      "reply_frame": 89},
         lambda seam: seam["replaced_at"] == 88
         and "the join is 88" in seam["explanation"]),
        ("declined", {"seam": None, "why": "the replacement plays no picture"},
         lambda seam: seam["declined"] is True and "no picture" in seam["why"]),
    ):
        root = tmp_path / name
        (root / "pipeline_output" / "review").mkdir(parents=True)
        ask = _ask(frame=40)
        owed.record(str(root), _markers(uncarried=[ask],
                                        **{name: [{**ask, **extra}]}))
        assert check(owed.open_for(str(root), REEL)[0]["seam"]), name


# ── The obligation ────────────────────────────────────────────────

def test_open_obligation_blocks_signoff_until_discharged(project):
    """The reel is not done while the captain's words are unaccounted for."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    with pytest.raises(signoff.UncarriedNotesOpen) as refused:
        signoff.sign_off(str(project), REEL, note="ships")
    # The refusal quotes his words and names the reel - the content a
    # later grep, human or machine, acts on.
    assert REEL in str(refused.value)
    assert WORDS_2 in str(refused.value)
    assert "discharge-uncarried" in str(refused.value)
    assert signoff.signed_off(str(project)) == {}

    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity, by="captain",
                   note="answered at the re-placed Blue at frame 88")
    entry = signoff.sign_off(str(project), REEL, note="ships")
    assert entry["reel"] == REEL


def test_discharge_is_recorded_never_erased(project):
    owed.record(str(project), _markers(uncarried=[_ask()]))
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity,
                   note="pinned to the removed take, correctly dropped")
    assert owed.open_for(str(project), REEL) == []
    document = owed.read_notes(str(project))
    kept = [e for e in document["discharged"]
            if e["identity"] == identity]
    assert kept[0]["discharged"]["note"] == \
        "pinned to the removed take, correctly dropped"


def test_a_discharge_needs_a_reason_and_a_real_identity(project):
    """A discharge with no stated reason is the bypass wearing the
    uniform; discharging what is not owed names what was asked."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    with pytest.raises(owed.DischargeRefused):
        owed.discharge(str(project), REEL, identity, note="  ")
    assert len(owed.open_for(str(project), REEL)) == 1
    with pytest.raises(owed.UncarriedNoteUnknown) as unknown:
        owed.discharge(str(project), REEL, "Reel_03:deadbeefdeadbeef",
                       note="mistyped identity")
    assert "deadbeef" in str(unknown.value)


def test_a_note_dropped_again_counts_once_and_reopens_after_discharge(
        project):
    """Re-reporting without a discharge never duplicates; a discharge
    answered that instance, so a new drop after it is a new fact."""
    ask = _ask()
    first = owed.record(str(project), _markers(uncarried=[ask]))
    second = owed.record(str(project), _markers(uncarried=[ask]))
    assert len(first["filed"]) == 1
    assert second["filed"] == []
    assert len(owed.open_for(str(project), REEL)) == 1
    assert owed.open_for(str(project), REEL)[0]["occurrences"] == 2
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity, note="answered")
    report = owed.record(str(project), _markers(uncarried=[ask]))
    assert report["reopened"] == [identity]
    assert report["filed"] == []
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["occurrences"] == 3
    assert entry["discharged"] is None
    assert entry["history"][0]["prior_discharge"]["note"] == "answered"
    with pytest.raises(signoff.UncarriedNotesOpen):
        signoff.sign_off(str(project), REEL)


# ── The clean path ────────────────────────────────────────────────

def test_carrying_everything_files_nothing_and_signs_off(project):
    """A promotion that drops nothing owes nothing - and writes nothing."""
    carried = [{**_ask(), "to_frame": 160, "pairing": "note"}]
    report = owed.record(
        str(project), {REEL: {"carried": carried, "uncarried": []}})
    assert report["filed"] == [] and report["reopened"] == []
    import os
    assert not os.path.exists(owed.notes_path_for(str(project)))
    assert signoff.sign_off(str(project), REEL)["reel"] == REEL


def test_an_unreadable_obligation_file_refuses(project):
    """An unreadable obligation reads exactly like no obligation, so it
    must refuse rather than let the sign-off through."""
    (project / "pipeline_output" / "review"
     / owed.NOTES_FILENAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(owed.UncarriedNotesUnreadable):
        owed.read_notes(str(project))
    with pytest.raises(owed.UncarriedNotesUnreadable):
        signoff.sign_off(str(project), REEL)


# ── Whose words ───────────────────────────────────────────────────

def test_our_replies_are_never_filed_as_his_words(project):
    """Stranded and independent replies stay reported, never re-filed as
    Blue notes of his - the same line the seam re-placement holds to."""
    ask = _ask(frame=162)
    stranded = {**_ask(frame=200, name="re: feedback",
                       note="your note did not carry"),
                "pairing": "stranded", "reply_of": 162,
                "why": "its note is itself NOT CARRIED"}
    independent = {**_ask(frame=210, name="re: feedback",
                          note="an old answer binding to nothing"),
                   "pairing": "independent-unpaired",
                   "pairing_flags": ["unpaired"],
                   "to_frame": 208,
                   "why": "carried by its own picture"}
    report = owed.record(
        str(project),
        _markers(uncarried=[ask, stranded, independent]))
    assert len(report["filed"]) == 1
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["frame"] == 162
    assert WORDS_2 in entry["text"]


def test_obligations_are_per_reel(project):
    """One reel owing a note never holds back a sibling's sign-off."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    assert signoff.sign_off(str(project), OTHER)["reel"] == OTHER
    with pytest.raises(signoff.UncarriedNotesOpen):
        signoff.sign_off(str(project), REEL)


# ── The step consumes the third key ───────────────────────────────

def _verify_step_module():
    from library.tools.operations import load_step_module

    return load_step_module("step_7_02_verify_reels", "step.py")


def test_step_helper_files_promoted_markers_and_continues(project):
    """`step_7_02` reads the key it used to drop - and never fails on it."""
    module = _verify_step_module()
    report = module.record_uncarried_notes(
        str(project), _markers(uncarried=[_ask()]))
    assert len(report["filed"]) == 1
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["reel"] == REEL
    assert WORDS_2 in entry["text"]


def test_partial_promotion_carries_its_marker_losses(project):
    """A batch that raises after its passing reels landed still hands
    their drops to the step - on the exception, where no return record
    exists to carry them. The message is unchanged."""
    from library.tools.reel_build import (
        ReelBuildError, _raise_partial_promotion)

    markers = _markers(uncarried=[_ask()])
    with pytest.raises(ReelBuildError) as partial:
        _raise_partial_promotion(["Reel 03"], ["Reel 03"],
                                 {"Reel 09": "REFUSING: rows differ"},
                                 markers=markers)
    assert "REFUSING to promote 1 reel(s)" in str(partial.value)
    assert partial.value.markers[REEL]["uncarried"][0]["note"] == WORDS_2


# --------------------------------------------------------------------------
# From test_marker_gate.py
#
# The gate audits the promotion from outside its own report.
#
# A promotion that destroyed two of the captain's clip markers printed
# "carried x3, uncarried 0" - a FALSE ALL-CLEAR. The carry machinery
# cannot report on a loss it never sees, so this gate re-reads the
# live timeline after the rename, both planes, and diffs by identity
# against a capture taken before it.
#
# These fakes stand in for Resolve; the API surface they answer is
# the one `promote_staged_reels` drives. Every failure assertion is
# on marker TEXT and colour, never on counts alone: a gate that fails
# on a number without naming the words is the count-only snapshot
# that already destroyed a note here.

def _clip_item(name, start, end, path, left=0, markers=None):
    pool_item = FakeMediaPoolItem(name)
    pool_item.SetClipProperty("File Path", path)
    return FakeTimelineItem(
        name,
        None,
        start=start,
        duration=end - start,
        left_offset=left,
        pool_item=pool_item,
        markers=markers,
    )


def _timeline(name, video=(), audio=(), markers=None, start=0):
    return FakeTimeline(
        name, start_frame=start, video=video, audio=audio, markers=markers
    )


FINAL_2 = "Reel 29 - clip notes (final)"
TIMELINE_NOTE = "tighten this pause before the reveal"
CLIP_NOTE = "the lower third clips her chin here"


def _body(left=6505):
    return _clip_item("Archana A", 0, 600, "/f/LC4932.MXF", left=left)


def _retiring_3():
    card = _clip_item(
        "cta card",
        500,
        560,
        "/f/mg_cta.mov",
        left=0,
        markers={
            12: {
                "color": "Blue",
                "name": "feedback",
                "note": CLIP_NOTE,
                "duration": 1,
                "customData": "",
            }
        },
    )
    return _timeline(
        FINAL_2,
        video=[("Archana", [_body()]), ("Motion Graphics", [card])],
        markers={
            100: {
                "color": "Blue",
                "name": "feedback",
                "note": TIMELINE_NOTE,
                "duration": 1,
                "customData": "",
            }
        },
    )


def _staging(moved_card_to=570):
    card = _clip_item(
        "cta card", moved_card_to, moved_card_to + 60, "/f/mg_cta.mov", left=0
    )
    return _timeline(
        FINAL_2 + " (rebuild staging)",
        video=[("Archana", [_body()]), ("Motion Graphics", [card])],
    )


@pytest.fixture
def project_dir_2(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


def _promote_2(project, project_dir_2, staged_to_final):
    (project_dir_2 / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    with patch(
        "library.tools.reel_build._connect_resolve_project", return_value=project
    ):
        return promote_staged_reels(
            str(project_dir_2),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )


def _captures(project_dir_2):
    capture_dir = project_dir_2 / "pipeline_output" / "review" / "marker_captures"
    return sorted(capture_dir.glob("*.json")) if capture_dir.is_dir() else []


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_promotion_that_carries_everything_passes_silently(project_dir_2):
    """Both planes carried: the gate files its capture and says nothing."""
    retired, staging = _retiring_3(), _staging()
    project = FakeProject([_timeline(MASTER), retired, staging])

    promoted = _promote_2(project, project_dir_2, {FINAL_2: staging.GetName()})

    assert promoted["promoted"] == [FINAL_2]
    # The live inventory holds his words on both planes - TEXT, not
    # counts: a carry that kept the count while moving the words to
    # the wrong item is the defect, not the fix.
    live_t = marker_carry.read_markers(staging, FINAL_2)
    assert [m["note"] for m in live_t if m["color"] == "Blue"] == [TIMELINE_NOTE]
    live_c = marker_carry.read_clip_markers(staging, FINAL_2)
    assert [m["note"] for m in live_c] == [CLIP_NOTE]
    assert live_c[0]["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert live_c[0]["source_frame"] == 12
    # And the capture was filed before the rename, holding them too.
    paths = _captures(project_dir_2)
    assert len(paths) == 1
    capture = marker_gate.read_capture(str(paths[0]))
    assert [m["note"] for m in capture["timeline"]] == [TIMELINE_NOTE]
    assert [m["note"] for m in capture["clip"]] == [CLIP_NOTE]


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_reported_loss_is_owned_not_failed(project_dir_2):
    """The ambiguous clip anchor is reported by the carry, so the gate
    that re-reads the same live timeline must not fail it again."""
    retired = _timeline(
        FINAL_2,
        video=[
            (
                "Motion Graphics",
                [
                    _clip_item(
                        "doubled card",
                        100,
                        160,
                        "/f/mg_doubled.mov",
                        left=0,
                        markers={
                            20: {
                                "color": "Blue",
                                "name": "feedback",
                                "note": "this card flashes",
                                "duration": 1,
                                "customData": "",
                            }
                        },
                    )
                ],
            ),
        ],
    )
    staging = _timeline(
        FINAL_2 + " (rebuild staging)",
        video=[
            (
                "Motion Graphics",
                [
                    _clip_item("doubled card", 100, 160, "/f/mg_doubled.mov", left=0),
                    _clip_item(
                        "doubled card encore", 160, 220, "/f/mg_doubled.mov", left=0
                    ),
                ],
            ),
        ],
    )
    project = FakeProject([_timeline(MASTER), retired, staging])

    promoted = _promote_2(project, project_dir_2, {FINAL_2: staging.GetName()})

    assert promoted["promoted"] == [FINAL_2]
    assert [m["note"] for m in promoted["markers"][FINAL_2]["clip_uncarried"]] == [
        "this card flashes"
    ]


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_silent_clip_loss_refuses_by_name(project_dir_2):
    """The 2026-09-20 shape: the machinery's lists say carried while
    the live item holds nothing. The gate fails on the words."""
    retired, staging = _retiring_3(), _staging()
    project = FakeProject([_timeline(MASTER), retired, staging])

    def _swallow_clip_markers(replacement, carried):
        return []  # claims success; places nothing

    with (
        patch.object(
            marker_carry, "place_clip_markers", side_effect=_swallow_clip_markers
        ),
        pytest.raises(ReelBuildError, match="MARKER GATE LOST") as lost,
    ):
        _promote_2(project, project_dir_2, {FINAL_2: staging.GetName()})

    message = str(lost.value)
    assert "Blue" in message
    assert CLIP_NOTE in message
    assert "mg_cta.mov" in message  # the alarm names where it lived
    # The timeline note survived, so it must NOT be named as lost.
    assert TIMELINE_NOTE not in message
    # The capture survives the refusal: recovery needs no archaeology.
    paths = _captures(project_dir_2)
    assert len(paths) == 1
    capture = marker_gate.read_capture(str(paths[0]))
    lost_clip = [m for m in capture["clip"] if m["note"] == CLIP_NOTE]
    assert len(lost_clip) == 1
    assert lost_clip[0]["anchor"]["source_file"] == "/f/mg_cta.mov"
    assert lost_clip[0]["anchor"]["source_frame"] == 12


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_silent_timeline_loss_refuses_by_name(project_dir_2):
    """The same false-all-clear on the timeline plane."""
    retired, staging = _retiring_3(), _staging()
    project = FakeProject([_timeline(MASTER), retired, staging])

    def _swallow_timeline_markers(timeline, carried):
        return []

    real_place_clip = marker_carry.place_clip_markers
    staging_card = staging.GetItemListInTrack("video", 2)[0]

    def _drop_clip_after_place(replacement, carried):
        failed = real_place_clip(replacement, carried)
        staging_card._markers.clear()  # gone after the plan vouched
        return failed

    with (
        patch.object(marker_carry, "place", side_effect=_swallow_timeline_markers),
        patch.object(
            marker_carry, "place_clip_markers", side_effect=_drop_clip_after_place
        ),
        pytest.raises(ReelBuildError, match="MARKER GATE LOST") as lost,
    ):
        _promote_2(project, project_dir_2, {FINAL_2: staging.GetName()})

    message = str(lost.value)
    assert TIMELINE_NOTE in message
    assert CLIP_NOTE in message


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_fleet_shrink_on_an_untouched_reel_refuses(project_dir_2):
    """The backstop: a reel this promotion never touched reads back
    smaller, so the run stops even though the promoted reel is clean."""
    retired, staging = _retiring_3(), _staging()
    other = _timeline(
        "Reel 30 - bystander (final)",
        video=[("Archana", [_body()])],
        markers={
            10: {
                "color": "Blue",
                "name": "feedback",
                "note": "bystander note",
                "duration": 1,
                "customData": "",
            }
        },
    )

    reads = {"count": 0}
    real_get_markers = other.GetMarkers

    def _shrinking():
        reads["count"] += 1
        if reads["count"] == 1:
            return real_get_markers()
        return {}  # changed under the promotion, off this reel

    other.GetMarkers = _shrinking
    project = FakeProject([_timeline(MASTER), retired, staging, other])

    with pytest.raises(ReelBuildError, match="did not touch"):
        _promote_2(project, project_dir_2, {FINAL_2: staging.GetName()})


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_fleet_addition_mid_run_only_reports(project_dir_2, capsys):
    """The captain adding a note to another reel mid-run is
    legitimate: noted on stdout, never a refusal."""
    retired, staging = _retiring_3(), _staging()
    other = _timeline("Reel 30 - bystander (final)", video=[("Archana", [_body()])])

    reads = {"count": 0}

    def _growing():
        reads["count"] += 1
        if reads["count"] == 1:
            return {}
        return {
            10: {
                "color": "Blue",
                "name": "feedback",
                "note": "typed mid-run",
                "duration": 1,
                "customData": "",
            }
        }

    other.GetMarkers = _growing
    project = FakeProject([_timeline(MASTER), retired, staging, other])

    promoted = _promote_2(project, project_dir_2, {FINAL_2: staging.GetName()})

    assert promoted["promoted"] == [FINAL_2]
    out = capsys.readouterr()
    assert "marker gate fleet note" in out.out
    assert "MARKER GATE" not in out.err


# ── The comparator from a separate process ──────────────────────
#
# An in-script GetMarkers once reported a marker on Reel 08 that did
# not exist, so the alarm half is also exercised across a process
# boundary: capture, live and accounted go to JSON files, and the
# module CLI diffs them in a fresh interpreter.


def _json_files(tmp_path, capture, live, accounted):
    paths = {}
    for name, payload in (
        ("capture", capture),
        ("live", live),
        ("accounted", accounted),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    return paths


def _capture_payload():
    return {
        "format": marker_gate.CAPTURE_FORMAT,
        "reel": FINAL_2,
        "captured_at": "2026-09-20T00:00:00+00:00",
        "timeline": [
            {
                "frame": 100,
                "color": "Blue",
                "name": "feedback",
                "note": TIMELINE_NOTE,
                "duration": 1,
                "custom_data": "",
                "anchor": ["/f/LC4932.MXF", 6605],
            }
        ],
        "clip": [
            {
                "plane": "clip",
                "frame": 512,
                "source_frame": 12,
                "color": "Blue",
                "name": "feedback",
                "note": CLIP_NOTE,
                "duration": 1,
                "custom_data": "",
                "anchor": {
                    "source_file": "/f/mg_cta.mov",
                    "track_type": "video",
                    "track_index": 2,
                    "clip_name": "cta card",
                    "timeline_start": 500,
                    "timeline_end": 560,
                    "source_start": 0,
                    "source_end": 60,
                    "source_frame": 12,
                },
                "unplaced_reason": "",
            }
        ],
    }


def _check(tmp_path, capture, live, accounted):
    paths = _json_files(tmp_path, capture, live, accounted)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "library.tools.marker_gate",
            "--check",
            paths["capture"],
            paths["live"],
            paths["accounted"],
            "--reel",
            FINAL_2,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_cli_fails_a_silent_loss_in_another_process(tmp_path):
    capture = _capture_payload()
    # The false all-clear: the live clip item holds nothing, and the
    # accounted lists claim nothing was lost.
    live = {"timeline": list(capture["timeline"]), "clip": []}
    accounted = {"timeline": [], "clip": []}

    completed = _check(tmp_path, capture, live, accounted)

    assert completed.returncode == 2
    assert "Blue" in completed.stderr
    assert CLIP_NOTE in completed.stderr
    assert TIMELINE_NOTE not in completed.stderr


# --------------------------------------------------------------------------
# From test_vep_marker_check.py
#
# The exit code is the gate: unit tests for the pure half.
#
# `compare()` never touches Resolve; the readers are exercised live by
# the lane that runs them (capture then verify on an untouched reel must
# exit 0; verify against another reel's capture must exit 1 naming the
# missing). Synthetic here, no projects, no timelines.

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_comparer():
    """`scripts/vep_marker_check.py` without touching `sys.path`.

    The old import-time insert was doubly fragile: process-global
    (collection order decides later bindings -
    `tests/tooling/test_static_check.py`) and relative (it resolves
    against whatever the working directory happens to be).
    """
    spec = importlib.util.spec_from_file_location(
        "_vep_marker_check", REPO_ROOT / "scripts" / "vep_marker_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare


compare = _load_comparer()


def _timeline_2(color, name, note, frame, source="timeline_marker"):
    return {"color": color, "name": name, "note": note, "frame": frame,
            "source": source, "timecode": "00:00:00:00", "duration_frames": 1,
            "custom_data_raw": ""}


def _clip(track, item, start, offset, color, name, note):
    return {"track": track, "item": item, "source_file": "/x.mov",
            "start": start, "end": start + 72,
            "markers": [{"offset": offset, "color": color, "duration": 1,
                         "name": name, "note": note, "custom_data": ""}]}


def test_identical_reads_are_clean():
    captured = {
        "timeline": "Reel 09",
        "timeline_markers": [_timeline_2("Blue", "feedback", "use this", 1525,
                                       source="clip_marker")],
        "clip_items": [_clip("video5", "mg_x.mov", 1499, 26, "Blue",
                             "feedback", "use this")],
    }
    # A clip note also appears in the snapshot half; the timeline half
    # skips non-timeline sources so it is not double-counted.
    live = {"timeline": "Reel 09",
            "timeline_markers": [],
            "clip_items": [_clip("video5", "mg_x.mov", 1499, 26, "Blue",
                                 "feedback", "use this")]}
    assert compare(captured, live) == []


def test_a_missing_blue_is_named():
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline_2("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09", "timeline_markers": [],
            "clip_items": []}
    failures = compare(captured, live)
    assert len(failures) == 1
    assert "use this" in failures[0]
    assert "Blue" in failures[0]


def test_a_recoloured_note_fails():
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline_2("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09",
            "timeline_markers": [_timeline_2("Green", "feedback", "use this",
                                           1525)],
            "clip_items": []}
    assert len(compare(captured, live)) == 1


def test_a_moved_timeline_note_fails():
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline_2("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09",
            "timeline_markers": [_timeline_2("Blue", "feedback", "use this",
                                           1526)],
            "clip_items": []}
    failures = compare(captured, live)
    assert len(failures) == 1
    assert "1525" in failures[0]


def test_a_clip_note_on_a_replaced_item_fails():
    """The Reel 29 class: the item is swapped, the note dies silently,
    and only the anchor half of this check can see it."""
    captured = {"timeline": "Reel 28",
                "timeline_markers": [],
                "clip_items": [_clip("video5", "mg_old.mov", 889, 30,
                                     "Blue", "feedback", "scale this")]}
    live = {"timeline": "Reel 28",
            "timeline_markers": [],
            "clip_items": [_clip("video5", "mg_new.mov", 889, 30,
                                 "Blue", "feedback", "scale this")]}
    failures = compare(captured, live)
    assert len(failures) == 1
    assert "mg_old.mov" in failures[0]


def test_an_added_green_reply_is_not_a_failure():
    """The check guards what was captured; a reply landing beside a
    blue note is the lane's own work, not a loss."""
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline_2("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09",
            "timeline_markers": [
                _timeline_2("Blue", "feedback", "use this", 1525),
                _timeline_2("Green", "reply", "done", 1526)],
            "clip_items": []}
    assert compare(captured, live) == []
