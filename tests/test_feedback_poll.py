"""`feedback_poll` - the trigger, the handover, and the contract.

No Resolve here and no fake of one's internals: the fakes below are
handles shaped like the three calls the scan makes
(`GetTimelineCount`/`GetTimelineByIndex`, `GetMarkers`,
`GetItemListInTrack`), and everything about frame spaces and marker
fields is asserted against the REAL Resolve separately, in the live
proof the PR carries.  What these pin is the poll's own logic: the
trigger word, the handover file, and the exact bytes firstmate polls
on.  Project folders are `tmp_path` (AGENTS.md 8).
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import feedback_poll, marker_feedback  # noqa: E402
from library.tools.marker_feedback import MarkerNote  # noqa: E402


# ── Handles shaped like Resolve's ───────────────────────────────────


class FakePoolItem:
    def __init__(self, path=""):
        self._path = path

    def GetClipProperty(self, key):
        return {"File Path": self._path}.get(key, "")

    def GetMarkers(self):
        return {}


class FakeItem:
    def __init__(self, name, start, duration, left=0, markers=None,
                 source_file=""):
        self._name = name
        self._start = start
        self._duration = duration
        self._left = left
        self._markers = markers or {}
        self._pool = FakePoolItem(source_file)

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._start + self._duration

    def GetLeftOffset(self):
        return self._left

    def GetDuration(self):
        return self._duration

    def GetSourceStartFrame(self):
        return self._left

    def GetMarkers(self):
        return dict(self._markers)

    def GetMediaPoolItem(self):
        return self._pool

    def GetClipColor(self):
        return ""

    def GetFlagList(self):
        return []


class FakeTimeline:
    def __init__(self, name, markers=None, items=None, start_frame=0):
        self._name = name
        self._markers = markers or {}
        self._items = items or []
        self._start = start_frame

    def GetName(self):
        return self._name

    def GetStartFrame(self):
        return self._start

    def GetEndFrame(self):
        return self._start + 10000

    def GetSetting(self, key):
        return "30" if key == "timelineFrameRate" else None

    def GetMarkers(self):
        return dict(self._markers)

    def GetTrackCount(self, track_type):
        return 1 if track_type == "video" else 0

    def GetItemListInTrack(self, track_type, index):
        return list(self._items) if track_type == "video" and index == 1 else []


class FakeProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = timelines

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        return self._timelines[0]

    def SetCurrentTimeline(self, timeline):  # noqa: ARG002
        raise AssertionError("the poll must never move the cursor")


class FakeManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class FakeResolve:
    def __init__(self, project):
        self._project = project

    def GetProjectManager(self):
        return FakeManager(self._project)


def _marker(name, note, color="Blue", duration=1, custom_data=""):
    return {"color": color, "duration": str(duration), "name": name,
            "note": note, "customData": custom_data}


@contextmanager
def _no_lease():
    yield None


@pytest.fixture
def live(monkeypatch):
    """A fake Resolve behind `connect_resolve`, and no real lease."""
    box = {}

    def _connect():
        return box["resolve"]

    monkeypatch.setattr(marker_feedback, "connect_resolve", _connect)
    monkeypatch.setattr(feedback_poll, "resolve_lease",
                        lambda *a, **k: _no_lease())
    monkeypatch.setattr(feedback_poll, "_expected_resolve_project",
                        lambda folder: "")
    return box


def _project_yaml(tmp_path, resolve_name=""):
    (tmp_path / "project.yaml").write_text(
        f"name: Poll Test\nslug: poll-test\n"
        + (f"resolve:\n  project_name: {resolve_name}\n" if resolve_name else ""),
        encoding="utf-8")


# ── The trigger ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name,expected", [
    ("feedback", True),
    ("Feedback", True),
    ("  feedback  ", True),
    ("FEEDBACK", True),
    ("feedback please", False),
    ("feedbacks", False),
    ("feed back", False),
    ("", False),
    ("reply: feedback", False),
])
def test_trigger_is_the_exact_word(name, expected):
    assert feedback_poll.is_feedback_name(name) is expected


def _note(name="feedback", note="change this", **kw):
    base = dict(source="clip_marker", name=name, note=note,
                text=f"{name}\n\n{note}", frame=2041,
                timecode="00:00:01:08", frame_in_timeline_space=20,
                color="Blue", custom_data={}, custom_data_raw="",
                attachments=[], clips=[],
                attached_clip={"name": "logo_reveal_23976.mov",
                               "track_type": "video", "track_index": 6,
                               "timeline_start": 2021, "timeline_end": 2093,
                               "source_start": 0, "source_end": 72,
                               "source_file": "/footage/logo_reveal_23976.mov"})
    base.update(kw)
    return MarkerNote(**base)


def test_replies_never_summon_even_named_feedback():
    assert feedback_poll.is_summons(_note(name="reply: feedback")) is False


def test_a_marker_carrying_our_reply_record_never_summons():
    custom = marker_feedback.reply_custom_data(
        "", answers="Reel_13:ab12cd34ef56ab78", answers_text="feedback\n\nchange this")
    assert feedback_poll.is_summons(_note(custom_data_raw=custom)) is False


def test_an_unmarked_note_is_the_captains():
    assert feedback_poll.is_summons(_note()) is True


# ── The scan reads both stores without touching the cursor ──────────


def test_scan_reads_timeline_and_clip_markers(live, tmp_path):
    clip = FakeItem("logo_reveal_23976.mov", 2021, 72,
                    markers={20: _marker("feedback", "darker blue, glow")},
                    source_file="/footage/logo_reveal_23976.mov")
    reel13 = FakeTimeline("Reel 13 - accounting", items=[clip])
    reel30 = FakeTimeline(
        "Reel 30 - map",
        markers={5: _marker("Master Limiter: -1.0dBTP", "threshold note"),
                 9: _marker("feedback", "mockup of google reviews")})
    live["resolve"] = FakeResolve(FakeProject("Field", [reel13, reel30]))
    summons, name = feedback_poll.scan(live["resolve"], str(tmp_path))
    assert name == "Field"
    assert [(t, n.note) for t, n in summons] == [
        ("Reel 13 - accounting", "darker blue, glow"),
        ("Reel 30 - map", "mockup of google reviews"),
    ]
    assert summons[0][1].attached_clip["name"] == "logo_reveal_23976.mov"
    assert summons[1][1].source == "timeline_marker"


def test_scan_refuses_another_projects_timelines(live, tmp_path,
                                                 monkeypatch):
    tl = FakeTimeline("Reel 13 - accounting")
    live["resolve"] = FakeResolve(FakeProject("Wrong Project", [tl]))
    monkeypatch.setattr(feedback_poll, "_expected_resolve_project",
                        lambda folder: "Podcast (field test)")
    with pytest.raises(feedback_poll.CannotLook, match="Wrong Project"):
        feedback_poll.scan(live["resolve"], str(tmp_path))


# ── Handled-ness: report once, edits re-arm, colour does not ────────


def _one_reel_live(name="feedback", note="darker blue, glow"):
    clip = FakeItem("logo_reveal_23976.mov", 2021, 72,
                    markers={20: _marker(name, note)},
                    source_file="/footage/logo_reveal_23976.mov")
    return FakeResolve(FakeProject(
        "Field", [FakeTimeline("Reel 13 - accounting", items=[clip])]))


def test_report_then_silence_then_edit_re_arms(live, tmp_path):
    live["resolve"] = _one_reel_live()
    rows, _ = feedback_poll.run_poll(str(tmp_path))
    assert len(rows) == 1 and rows[0]["note"] == "darker blue, glow"
    rows, _ = feedback_poll.run_poll(str(tmp_path))
    assert rows == []
    live["resolve"] = _one_reel_live(note="darker blue, glow, slower")
    rows, _ = feedback_poll.run_poll(str(tmp_path))
    assert len(rows) == 1 and rows[0]["note"] == "darker blue, glow, slower"


def test_recolouring_does_not_re_arm(live, tmp_path):
    live["resolve"] = _one_reel_live()
    assert len(feedback_poll.run_poll(str(tmp_path))[0]) == 1
    clip = FakeItem("logo_reveal_23976.mov", 2021, 72,
                    markers={20: _marker("feedback", "darker blue, glow",
                                         color="Red")},
                    source_file="/footage/logo_reveal_23976.mov")
    live["resolve"] = FakeResolve(FakeProject(
        "Field", [FakeTimeline("Reel 13 - accounting", items=[clip])]))
    assert feedback_poll.run_poll(str(tmp_path))[0] == []


def test_peek_reports_without_recording(live, tmp_path):
    live["resolve"] = _one_reel_live()
    rows, _ = feedback_poll.run_poll(str(tmp_path), peek=True)
    assert len(rows) == 1
    rows, _ = feedback_poll.run_poll(str(tmp_path), peek=True)
    assert len(rows) == 1  # still there: peek records nothing
    assert feedback_poll.read_handover(str(tmp_path)) == {}


def test_forget_re_arms_one_identity(live, tmp_path, capsys):
    live["resolve"] = _one_reel_live()
    assert len(feedback_poll.run_poll(str(tmp_path))[0]) == 1
    (identity,) = feedback_poll.read_handover(str(tmp_path))
    assert feedback_poll.main(
        ["--project", str(tmp_path), "--forget", identity]) == 0
    capsys.readouterr()  # forget speaks to stderr, never stdout
    rows, _ = feedback_poll.run_poll(str(tmp_path))
    assert [r["identity"] for r in rows] == [identity]


def test_corrupt_handover_fails_open(live, tmp_path):
    live["resolve"] = _one_reel_live()
    path = feedback_poll.handover_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    rows, _ = feedback_poll.run_poll(str(tmp_path))
    assert len(rows) == 1


def test_handover_lives_in_the_captured_area(tmp_path):
    path = feedback_poll.handover_path(str(tmp_path))
    assert path.parent.name == "marker_feedback"
    assert "pipeline_output" not in path.parts


# ── Archived copies are the same reel, not a new note ─────────────


REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
ARCHIVED = f"{REEL} (archived round 001)"
ARCHIVED_SIBLING = f"{REEL} (archived round 001.2)"


def _clip_with_note(note="fix the logo", name="feedback", color="Blue"):
    return FakeItem("logo_reveal_23976.mov", 2021, 72,
                    markers={20: _marker(name, note, color=color)},
                    source_file="/footage/logo_reveal_23976.mov")


def _live_plus_archived(note="fix the logo"):
    return FakeResolve(FakeProject("Field", [
        FakeTimeline(REEL, items=[_clip_with_note(note=note)]),
        FakeTimeline(ARCHIVED, items=[_clip_with_note(note=note)]),
    ]))


def test_live_and_archived_copies_are_one_identity(live, tmp_path):
    """The defect: a rebuild archives every reel it touches, so the
    same marker sits on two timelines. Before the fix the archived
    name survived `base_reel_name` and the poll printed the note
    twice under two identities; now it prints one row."""
    live["resolve"] = _live_plus_archived()
    rows, _ = feedback_poll.run_poll(str(tmp_path), peek=True)
    assert len(rows) == 1
    assert rows[0]["timeline"] == REEL
    assert rows[0]["reel"] == REEL
    assert feedback_poll.marker_identity(REEL, _note()) == \
        feedback_poll.marker_identity(ARCHIVED, _note())
    assert feedback_poll.marker_identity(REEL, _note()) == \
        feedback_poll.marker_identity(ARCHIVED_SIBLING, _note())


def test_a_new_note_on_an_archived_copy_still_reports(live, tmp_path):
    """Suppressing by timeline name alone would silence the captain
    where he compares versions. The content hash differs, so a
    genuinely new note on the archived copy is a new identity."""
    live["resolve"] = _live_plus_archived(note="fix the logo")
    assert len(feedback_poll.run_poll(str(tmp_path))[0]) == 1
    live["resolve"] = FakeResolve(FakeProject("Field", [
        FakeTimeline(REEL, items=[_clip_with_note(note="fix the logo")]),
        FakeTimeline(ARCHIVED, items=[
            _clip_with_note(note="the old cut holds longer here")]),
    ]))
    rows, _ = feedback_poll.run_poll(str(tmp_path))
    assert [r["note"] for r in rows] == ["the old cut holds longer here"]
    assert rows[0]["timeline"] == ARCHIVED
    assert rows[0]["reel"] == REEL


def test_two_different_reels_never_collide():
    """Engine suffixes fold; everything else - including hand-made
    parenthesised copies - stays its own reel."""
    assert feedback_poll.marker_identity("Reel 01 - a", _note()) != \
        feedback_poll.marker_identity("Reel 23 - b", _note())
    assert feedback_poll.marker_identity(f"{REEL} (final)", _note()) != \
        feedback_poll.marker_identity(REEL, _note())
    assert feedback_poll.marker_identity(f"{REEL} (final)", _note()) != \
        feedback_poll.marker_identity(ARCHIVED, _note())


def test_a_handed_note_stays_handed_across_a_rebuild(live, tmp_path):
    """The property the whole poll exists for: handed over on the
    live reel, the rebuild archives it, and the archived copy carrying
    the same words stays silent."""
    live["resolve"] = FakeResolve(FakeProject(
        "Field", [FakeTimeline(REEL, items=[_clip_with_note()])]))
    assert len(feedback_poll.run_poll(str(tmp_path))[0]) == 1
    live["resolve"] = FakeResolve(FakeProject(
        "Field", [FakeTimeline(ARCHIVED, items=[_clip_with_note()])]))
    assert feedback_poll.run_poll(str(tmp_path))[0] == []


# ── The byte contract ───────────────────────────────────────────────


def test_silence_is_zero_bytes(live, tmp_path, capsys):
    live["resolve"] = FakeResolve(FakeProject(
        "Field", [FakeTimeline("Reel 13 - accounting")]))
    assert feedback_poll.main(["--project", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out == "" and err == ""


def test_report_shape_is_pinned(live, tmp_path, capsys):
    live["resolve"] = _one_reel_live()
    assert feedback_poll.main(["--project", str(tmp_path)]) == 0
    out, _ = capsys.readouterr()
    document = json.loads(out)
    assert document["format"] == "feedback_poll/1"
    assert document["resolve_project"] == "Field"
    assert document["count"] == 1
    (row,) = document["markers"]
    assert row == {
        "identity": row["identity"],
        "timeline": "Reel 13 - accounting",
        "reel": "Reel 13 - accounting",
        "source": "clip_marker",
        "clip": "logo_reveal_23976.mov",
        "clip_track": "video1",
        "clip_source_file": "/footage/logo_reveal_23976.mov",
        "timeline_frame": 2041,
        "timecode": row["timecode"],
        "source_frame": 20,
        "name": "feedback",
        "note": "darker blue, glow",
        "color": "Blue",
        "still": "",
        "still_exists": False,
    }
    assert row["identity"].startswith("Reel_13_-_accounting:")


def test_cannot_look_is_exit_3(live, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(marker_feedback, "connect_resolve",
                        lambda: (_ for _ in ()).throw(
                            marker_feedback.ResolveUnavailable("closed")))
    assert feedback_poll.main(["--project", str(tmp_path)]) == 3
    out, err = capsys.readouterr()
    assert out == "" and "closed" in err
