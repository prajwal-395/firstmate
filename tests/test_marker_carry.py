"""A promotion says what it is about to do to the captain's words.

Reel 09, 2026-09-09: three typed markers, a rebuild, and
`marker_feedback show` reporting "0 note(s)". They were not cleared -
promotion replaced the timeline object and they went with it, and
nothing said so. These fakes stand in for Resolve; the API surface
they answer is the one `promote_staged_reels` drives.
"""

import pytest

from library.tools import marker_carry


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
    """The captain's Reel 13: Akshita on V1, Craig on V2, his Blue
    `feedback` marker at 1909 - the frame he typed it on."""
    return _Timeline(
        [("Akshita", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)]),
         ("Craig", [_Item(1909, 1921, "/f/LCATL0013.MXF", left=50)])],
        markers={1700: {"color": "Blue", "name": "feedback",
                        "note": "it cuts to craig here at the end",
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
        [("Akshita", [_Item(1579, 1889, "/f/LC4932.MXF", left=1000)])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not uncarried and len(carried) == 1
    assert carried[0]["to_frame"] == 1680
    failed = marker_carry.place(replacement, carried)
    assert not failed
    assert replacement.added == [
        (1680, "Blue", "feedback", "it cuts to craig here at the end", 1,
         "cd")]


def test_a_marker_whose_picture_is_gone_is_named_not_dropped(capsys):
    """The whole point. A note on a shot the rebuild removed is
    REPORTED, with the captain's words, and left uncarried."""
    retiring = _Timeline(
        [("Craig", [_Item(1909, 1921, "/f/LCATL0013.MXF", left=50)])],
        markers={1915: {"color": "Blue", "name": "feedback",
                        "note": "this cut is jarring", "duration": 1}})
    notes = marker_carry.read_markers(retiring, "Reel 13")
    replacement = _Timeline(
        [("Akshita", [_Item(1599, 1909, "/f/LC4932.MXF", left=1000)])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not carried and len(uncarried) == 1
    marker_carry.report("Reel 13", carried, uncarried)
    err = capsys.readouterr().err
    assert "MARKER NOT CARRIED" in err
    assert "this cut is jarring" in err


def test_the_nearest_saying_wins_when_a_shot_repeats():
    """A reel that plays one source twice has not moved the captain's
    note to the other saying of it."""
    retiring = _Timeline(
        [("A", [_Item(100, 200, "/f/a.mov", left=0)])],
        markers={150: {"color": "Blue", "name": "n", "note": "x",
                       "duration": 1}})
    notes = marker_carry.read_markers(retiring, "Reel 13")
    replacement = _Timeline([("A", [
        _Item(0, 100, "/f/a.mov", left=0),
        _Item(140, 240, "/f/a.mov", left=0)])])
    carried, _ = marker_carry.plan_carry(notes, replacement)
    # Source frame 50 plays at reel 50 and at reel 190; 190 is nearer
    # the marker's own 150.
    assert carried[0]["to_frame"] == 190


def _reel04():
    """Reel 04's shape at the 2026-09-19 rebuild: Akshita body on V1
    from frame 65, a motion-graphics overlay across V5/V6 with
    per-build content hashes, and the pink verdict at 65 - the first
    body frame, under the overlay."""
    return _Timeline(
        [("Akshita", [_Item(65, 395, "/f/LC4932.MXF", left=6505)]),
         ("Craig", [_Item(0, 65, "/f/LCATL0013.MXF", left=6348)]),
         ("Frame", [_Item(0, 600, "/f/tv_frame.mov")]),
         ("Subtitles", [_Item(0, 32, "/f/sub.mov")]),
         ("Semantic", [_Item(0, 108, "/f/mg_old.mov")]),
         ("Motion Graphics", [_Item(0, 152, "/f/mg_old2.mov")])],
        markers={65: {"color": "Pink", "name": "verdict (firstmate)",
                      "note": "FIXABLE", "duration": 444,
                      "customData": "cd"}})


def test_a_verdict_anchored_to_picture_survives_rerendered_overlays():
    notes = marker_carry.read_markers(_reel04(), "Reel 04")
    # The rebuild extended the body and re-rendered every overlay
    # under fresh content hashes; the footage did not move.
    replacement = _Timeline(
        [("Akshita", [_Item(65, 511, "/f/LC4932.MXF", left=6505)]),
         ("Craig", [_Item(0, 65, "/f/LCATL0013.MXF", left=6348)]),
         ("Frame", [_Item(0, 716, "/f/tv_frame.mov")]),
         ("Subtitles", [_Item(0, 32, "/f/sub.mov")]),
         ("Semantic", [_Item(0, 108, "/f/mg_new.mov")]),
         ("Motion Graphics", [_Item(0, 152, "/f/mg_new2.mov")])])
    carried, uncarried = marker_carry.plan_carry(notes, replacement)
    assert not uncarried and len(carried) == 1
    assert carried[0]["to_frame"] == 65


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


def test_an_anchor_mismatch_binds_nearest_and_says_so():
    """Same words, different picture (a relinked file): nearest wins,
    flagged rather than exact."""
    retiring = _Timeline(
        [("V1", [_Item(0, 150, "/f/a.mov", left=0),
                 _Item(150, 300, "/f/b.mov", left=0)])],
        markers={
            100: {"color": "Blue", "name": "feedback", "note": "trim",
                  "duration": 1, "customData": ""},
            200: {"color": "Blue", "name": "feedback", "note": "trim",
                  "duration": 1, "customData": ""},
            201: {"color": "Green", "name": "reply: done",
                  "note": "trimmed",
                  "duration": 1, "customData": _reply_payload(
                      "Reel X", "feedback", "trim",
                      ("/f/renamed.mov", 7))}})
    notes = marker_carry.read_markers(retiring, "Reel X")
    replacement = _Timeline(
        [("V1", [_Item(500, 650, "/f/a.mov", left=0),
                 _Item(650, 800, "/f/b.mov", left=0)])])
    carried, _ = marker_carry.plan_carry(notes, replacement, "Reel X")
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
