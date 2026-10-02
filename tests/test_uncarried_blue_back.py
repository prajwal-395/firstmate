"""Uncarried notes get their Blue back, at the seam, byte-identical.

History: docs/evidence/resolve_test_history.md#test_uncarried_blue_back.
"""

import json
from unittest.mock import patch

import pytest
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
    no_a_roll_track_plans,
)

from library.tools import marker_carry
from library.tools.reel_build import promote_staged_reels


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture(autouse=True)
def fake_preservation_snapshots(monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)

MASTER = "Podcast - Synced"
FINAL = "Reel 08 - moment (final)"
PATH = "/footage/LC4932.MXF"


class _Pool:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else ""


class _Item:
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


class _Timeline:
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


def _retiring():
    """Reel 08 before the cut: A runs 30808..30827, B runs the note's
    span 31016..31107, and his Blue sits at 640 duration 81 over B."""
    return _Timeline(
        FINAL,
        [("Akshita", [_Item("Akshita A", 600, 639, PATH, left=30788),
                       _Item("Akshita B", 639, 730, PATH, left=31016)])],
        markers={640: {"color": "Blue", "name": "trim?",
                       "note": WORDS, "duration": 81,
                       "customData": ""}})


def _replacement():
    """Reel 08 after the cut: A survives, then a jump to 31097 - the
    anchored span is entirely gone and the seam is frame 639."""
    return _Timeline(
        "staging",
        [("Akshita", [_Item("Akshita A", 600, 639, PATH, left=30788),
                       _Item("Akshita C", 639, 700, PATH, left=31097)])])


def test_removed_span_comes_back_as_blue_at_the_seam(capsys):
    retiring, replacement = _retiring(), _replacement()
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


def test_surviving_picture_still_carries_unchanged():
    """The cut the captain approved around his note: same picture,
    shifted twenty frames earlier - carried as today, no Blue added,
    no reply written."""
    retiring = _retiring()
    notes = marker_carry.read_markers(retiring, FINAL)
    shifted = _Timeline(
        "staging",
        [("Akshita", [_Item("Akshita A", 580, 619, PATH, left=30788),
                       _Item("Akshita B", 619, 710, PATH, left=31016)])])
    carried, uncarried = marker_carry.plan_carry(notes, shifted)
    assert len(carried) == 1 and not uncarried
    assert carried[0]["to_frame"] == 620
    assert marker_carry.place(shifted, carried) == []
    assert shifted.added == [(620, "Blue", "trim?", WORDS, 81, "")]
    plans = marker_carry.plan_seams(uncarried, retiring, shifted)
    placed, declined = marker_carry.place_uncarried(
        shifted, plans, FINAL)
    assert (placed, declined) == ([], [])


def test_ambiguous_seam_goes_at_the_replacing_item_and_says_so():
    """Nothing before the cut survives: the note goes at the start of
    the replacing item, and the reply says the seam was ambiguous."""
    retiring = _Timeline(
        FINAL,
        [("Akshita", [_Item("gone", 0, 100, PATH, left=5000),
                       _Item("kept", 100, 200, PATH, left=9000)])],
        markers={10: {"color": "Blue", "name": "look",
                      "note": "this opening drags", "duration": 10,
                      "customData": ""}})
    replacement = _Timeline(
        "staging",
        [("Akshita", [_Item("new", 0, 50, "/footage/OTHER.MXF", left=0),
                       _Item("kept", 50, 150, PATH, left=9000)])])
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


def test_an_occupied_seam_declines_the_blue_and_reports(capsys):
    """A byte-identical Blue at the wrong key is not a restored
    marker: when the seam is occupied the decline is named with his
    words, and only then does the reply carry them."""
    retiring, replacement = _retiring(), _replacement()
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


def test_a_pictureless_replacement_has_no_seam(capsys):
    retiring = _retiring()
    replacement = _Timeline("staging", [])
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


def test_promotion_puts_the_blue_back_at_the_seam(tmp_path):
    """End to end through `promote_staged_reels`: the cut removes the
    anchored span, and the promoted timeline carries his Blue at 639
    byte-identical with the Green reply beside it - asserted on TEXT
    and colour, never on counts alone."""
    retired, staging = _retiring(), _replacement()
    staging._name = FINAL + " (rebuild staging)"
    resolve = _Project([_Timeline(MASTER, []), retired, staging])
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


def test_a_stranded_reply_is_reported_never_re_filed_as_blue(capsys):
    """Our green answered the note the cut removed: it is reported
    alongside its note, and the seam takes the genuine note only.

    Re-placing the stranded reply would file our answer text as a Blue
    note of his with a fresh Green beside it. Asserted on TEXT and
    colour: no Blue may carry our reply's words."""
    from library.tools import marker_feedback as _feedback
    from library.tools.feedback_ledger import (
        durable_identity as _identity)
    retiring = _retiring()
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
