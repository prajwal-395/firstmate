"""resolve-axi: the safe behaviour is the default, and the tests prove it.

Every test here drives `library.tools.resolve_axi` against fakes - no
Resolve, no real project (AGENTS.md 8). Two invariants get permanent
coverage because sibling lanes build while this tool reads:

1. READS NEVER MOVE THE CURSOR. The fake project raises on
   `SetCurrentTimeline`/`SetCurrentProject`/opens/creates, and every
   read command runs against it. A cursor-moving read would fail here
   rather than killing a sibling lane's Fusion pass mid-build.
2. The module source carries no cursor-moving call except the one
   `AddMarker` inside `cmd_markers_restore` (the single declared
   write, lease-guarded, cursor-asserted).
"""

import ast
import contextlib
import json
from pathlib import Path

import pytest

from library.tools import resolve_axi
from library.tools.resolve_axi import (
    AxiError,
    cmd_captions,
    cmd_cursor,
    cmd_frames,
    cmd_fusion,
    cmd_items,
    cmd_markers,
    cmd_markers_audit_replies,
    cmd_markers_reply,
    cmd_markers_restore,
    cmd_markers_snapshot,
    cmd_run,
    cmd_timeline_get,
    cmd_timeline_list,
    table,
)


# ── Fakes ────────────────────────────────────────────────────────────


class _CursorMoved(AssertionError):
    pass


class _Pool:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, name):
        return {"File Path": self._path}.get(name, "")

    def GetMarkers(self):
        return {}


class _Item:
    def __init__(self, name, start, end, pool=None, markers=None,
                 transform=None):
        self._name = name
        self._start = start
        self._end = end
        self._pool = pool
        self._markers = dict(markers or {})
        self._transform = dict(transform or {})

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetSourceStartFrame(self):
        return 1000

    def GetSourceEndFrame(self):
        # Inclusive, like Resolve: the off-by-one the frames command
        # keeps visible.
        return 1000 + (self._end - self._start) - 1

    def GetLeftOffset(self):
        return 1000

    def GetRightOffset(self):
        return 0

    def GetUniqueId(self):
        return f"uid-{self._name}"

    def GetClipColor(self):
        return ""

    def GetFlagList(self):
        return []

    def GetClipEnabled(self):
        return True

    def GetProperty(self):
        return dict(self._transform)

    def GetCDL(self):
        return {}

    def GetColorGroup(self):
        return ""

    def GetFusionCompCount(self):
        return 0

    def GetFusionCompNameList(self):
        return []

    def GetMarkers(self):
        return dict(self._markers)

    def GetMediaPoolItem(self):
        return self._pool


class _Timeline:
    def __init__(self, name, markers=None, tracks=None, start=0,
                 end=99):
        self._name = name
        self._markers = dict(markers or {})
        self._tracks = tracks or {}  # (type, index) -> {"name":, "items":}
        self._start = start
        self._end = end
        self.added = []

    def GetName(self):
        return self._name

    def GetStartFrame(self):
        return self._start

    def GetEndFrame(self):
        return self._end

    def GetSetting(self, key):
        return {"timelineFrameRate": "23.976"}.get(key, "")

    def GetMarkers(self):
        return dict(self._markers)

    def GetTrackCount(self, track_type):
        return sum(1 for (t, _i) in self._tracks if t == track_type)

    def GetTrackName(self, track_type, index):
        return self._tracks[(track_type, index)]["name"]

    def GetItemListInTrack(self, track_type, index):
        return list(self._tracks[(track_type, index)]["items"])

    def AddMarker(self, frame, color, name, note, duration,
                  custom_data=""):
        self.added.append((frame, color, name, note, duration,
                           custom_data))
        # Behave like Resolve for read-back: the marker lands in the
        # timeline-space map `place_reply_marker` verifies against.
        self._markers[int(frame)] = {
            "color": color, "name": name, "note": note,
            "duration": duration, "customData": custom_data}
        return True


class _Project:
    """A project whose cursor cannot be moved: any attempt raises."""

    def __init__(self, name, timelines, current):
        self._name = name
        self._timelines = list(timelines)
        self._current = current

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        return self._current

    def SetCurrentTimeline(self, _timeline):
        raise _CursorMoved("reads must not move the cursor")

    def SetCurrentProject(self, _project):
        raise _CursorMoved("reads must not move the cursor")


class _Manager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _Resolve:
    def __init__(self, project):
        self._manager = _Manager(project)

    def GetProjectManager(self):
        return self._manager


class _Note:
    def __init__(self, source, frame, color="", name="", note="",
                 timecode="00:00:00:00"):
        self.source = source
        self.frame = frame
        self.frame_in_timeline_space = frame
        self.timecode = timecode
        self.color = color
        self.name = name
        self.note = note
        self.text = note
        self.duration_frames = 1
        self.custom_data_raw = ""


def _ns(**kwargs):
    return type("Args", (), kwargs)()


@pytest.fixture()
def reel():
    clip_marked = _Item("LC0001.MXF", 0, 50,
                        pool=_Pool("/footage/LC0001.MXF"),
                        markers={200: {"color": "Blue", "name": "fix",
                                       "note": "trim", "duration": 1,
                                       "customData": ""}})
    plain = _Item("LC0002.MXF", 50, 100,
                  pool=_Pool("/footage/LC0002.MXF"))
    timeline = _Timeline(
        "Reel 29 - salvage",
        markers={10: {"color": "Green", "name": "feedback",
                      "note": "good", "duration": 1, "customData": ""}},
        tracks={("video", 1): {"name": "V1",
                               "items": [clip_marked, plain]},
                ("audio", 1): {"name": "A1", "items": []}},
        start=0, end=99)
    staging = _Timeline("Reel 29 - salvage (rebuild staging)")
    project = _Project("Podcast (field test)", [timeline, staging],
                       current=timeline)
    return {"timeline": timeline, "staging": staging, "project": project,
            "resolve": _Resolve(project)}


@pytest.fixture()
def patched(reel, monkeypatch):
    """Wire resolve_axi's seams to the fakes: no Resolve, no lease."""
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: reel["resolve"])
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return reel


@pytest.fixture()
def notes(monkeypatch):
    seen = {}

    def fake_read_notes(timeline, project_folder=None):
        return list(seen.get(timeline.GetName(), []))

    import library.tools.marker_feedback as feedback
    monkeypatch.setattr(feedback, "read_notes", fake_read_notes)
    return seen


# ── The cursor invariant ─────────────────────────────────────────────


def test_source_carries_no_cursor_moving_call_outside_writes():
    """The invariant that lets this tool run while builds run.

    `SetCurrentTimeline`/`SetCurrentProject` (and opens/creates) may
    not appear anywhere in the module: reads never move the cursor,
    and the two writes (`markers restore --apply`, `markers reply
    --apply`) assert it rather than moving it. A future edit that
    reaches for the cursor to read fails here, not on a sibling's
    Fusion pass.
    """
    source = Path(resolve_axi.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    movers = {"SetCurrentTimeline", "SetCurrentProject", "SetCurrentTimeLine",
              "OpenProject", "CreateTimeline", "DeleteTimeline",
              "SetSetting"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in movers:
            hits.append((node.attr, node.lineno))
    assert hits == [], f"cursor-moving calls in resolve_axi: {hits}"


# ── The seven commands ───────────────────────────────────────────────


def test_timeline_get_refuses_ambiguous_prefix(patched, capsys):
    # "Reel 29" is a prefix of BOTH the final and its staging sibling -
    # the incident-2 collision shape. It must refuse, compactly, and
    # name the staging sibling as the cause (that is the case that
    # bites: the caller asked for the reel and the rebuild answered).
    assert cmd_timeline_get(_ns(project="", name="Reel 29")) == 1
    out = capsys.readouterr().out
    assert "prefix of 2 timelines" in out
    assert "(rebuild staging) sibling" in out
    # The refusal must NOT dump the whole project listing: that dump
    # is the token cost this tool removes.
    assert len(out) < 800


def test_markers_reads_both_planes(patched, notes, capsys):
    """Incident 1's trap: a timeline-plane-only read reports 1 of 2."""
    notes["Reel 29 - salvage"] = [
        _Note("timeline_marker", 10, color="Green", name="feedback",
              note="good"),
        _Note("clip_marker", 0, color="Blue", name="fix", note="trim"),
    ]
    assert cmd_markers(
        _ns(project="", timeline="Reel 29 - salvage", plane="",
            full=False)) == 0
    out = capsys.readouterr().out
    assert "notes: 2" in out
    assert "clip_marker" in out


def test_cursor_asserts(patched, capsys):
    assert cmd_cursor(_ns(expect="")) == 0
    assert "Reel 29 - salvage" in capsys.readouterr().out
    assert cmd_cursor(_ns(expect="Reel 29 - salvage")) == 0
    assert cmd_cursor(_ns(expect="Reel 16 - other")) == 1
    assert "may have moved it" in capsys.readouterr().out


# ── Snapshot / restore ───────────────────────────────────────────────


def test_restore_dry_run_diffs_without_writing(patched, tmp_path,
                                               capsys):
    payload = {"tool": "resolve-axi", "project": "Podcast (field test)",
               "timeline": "Reel 29 - salvage",
               "notes": [{"source": "timeline_marker",
                          "frame": 10, "frame_in_timeline_space": 10,
                          "timecode": "00:00:00:10", "color": "Green",
                          "name": "feedback", "note": "good",
                          "duration_frames": 1, "custom_data_raw": ""},
                         {"source": "clip_marker", "frame": 0,
                          "frame_in_timeline_space": 200,
                          "timecode": None, "color": "Blue",
                          "name": "fix", "note": "trim",
                          "duration_frames": 1,
                          "custom_data_raw": ""}]}
    in_file = tmp_path / "markers.json"
    in_file.write_text(json.dumps(payload), encoding="utf-8")
    # Live holds one of the two timeline-plane notes already.
    patched["timeline"]._markers = {
        10: {"color": "Green", "name": "feedback", "note": "good",
             "duration": 1, "customData": ""}}
    assert cmd_markers_restore(
        _ns(project="", timeline="Reel 29 - salvage",
            in_file=str(in_file), apply=False,
            allow_partial=True)) == 0
    out = capsys.readouterr().out
    assert "already_present: 1" in out
    # Dry run writes nothing to Resolve.
    assert patched["timeline"].added == []


def test_restore_refuses_foreign_timeline(patched, tmp_path, capsys):
    payload = {"tool": "resolve-axi", "project": "Podcast (field test)",
               "timeline": "Reel 29 - salvage", "notes": []}
    in_file = tmp_path / "markers.json"
    in_file.write_text(json.dumps(payload), encoding="utf-8")
    assert cmd_markers_restore(
        _ns(project="", timeline="Reel 29 - salvage (rebuild staging)",
            in_file=str(in_file), apply=False,
            allow_partial=False)) == 1
    assert "refusing to restore" in capsys.readouterr().out


# ── Snapshot refusal + frame fields (round-trip fixtures) ──────────
#
# These tests never hand-write snapshot JSON. The fixture is built by
# the REAL `marker_feedback.read_notes` over a fake timeline and the
# REAL `_snapshot_payload` writer - a reader's output is not the file
# format, and a tool quote must never become a fixture. The clip note
# below sits at source frame 1026 playing at timeline frame 426, so
# the two frame numbers cannot agree by accident.


def _field_timeline(with_clip_note=True):
    marked = _Item("LC0024.MXF", 400, 450,
                   pool=_Pool("/footage/LC0024.MXF"),
                   markers=({1026: {"color": "Blue", "name": "sub fix",
                                    "note": "caption drops a word",
                                    "duration": 1, "customData": ""}}
                            if with_clip_note else {}))
    return _Timeline(
        "Reel 24 - field",
        markers={40: {"color": "Green", "name": "feedback",
                      "note": "good take", "duration": 1,
                      "customData": ""}},
        tracks={("video", 1): {"name": "V1", "items": [marked]},
                ("audio", 1): {"name": "A1", "items": []}},
        start=0, end=499)


@pytest.fixture()
def field_reel(monkeypatch):
    timeline = _field_timeline()
    project = _Project("Podcast (field test)", [timeline],
                       current=timeline)
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return {"timeline": timeline, "project": project}


def _write_round_trip_snapshot(timeline, project_name, path):
    """A snapshot file exactly as the tool would write it live."""
    from library.tools import marker_feedback
    notes = marker_feedback.read_notes(timeline)
    payload = resolve_axi._snapshot_payload(project_name,
                                            timeline.GetName(), notes)
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True,
                                     default=str) + "\n",
                          encoding="utf-8")
    return payload


def test_restore_refuses_snapshot_with_clip_notes(field_reel, tmp_path,
                                                  capsys):
    in_file = tmp_path / "reel24.json"
    _write_round_trip_snapshot(field_reel["timeline"],
                               "Podcast (field test)", str(in_file))
    for apply in (False, True):
        assert cmd_markers_restore(
            _ns(project="", timeline="Reel 24 - field",
                in_file=str(in_file), apply=apply,
                allow_partial=False)) == 1
        out = capsys.readouterr().out
        assert "holds 1 note(s)" in out
        assert "clip_marker: 1" in out
        assert "re-apply the clip-plane note(s) by hand" in out
        assert "--allow-partial" in out
    # The refusal lands before any Resolve contact: nothing written.
    assert field_reel["timeline"].added == []


def test_restore_allow_partial_apply_writes_timeline_plane_only(
        field_reel, tmp_path, capsys):
    in_file = tmp_path / "reel24.json"
    _write_round_trip_snapshot(field_reel["timeline"],
                               "Podcast (field test)", str(in_file))
    # Live holds nothing yet, so the timeline-plane note is missing.
    field_reel["timeline"]._markers = {}
    assert cmd_markers_restore(
        _ns(project="", timeline="Reel 24 - field",
            in_file=str(in_file), apply=True,
            allow_partial=True)) == 0
    out = capsys.readouterr().out
    assert field_reel["timeline"].added == [
        (40, "Green", "feedback", "good take", 1, "")]
    assert "restored: 1" in out
    assert "skipped: 1" in out
    assert "re-apply them by hand" in out


# ── Marker replies ───────────────────────────────────────────────────


def _reply_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "frame": 20, "color": "Pink", "name": "verdict (axi)",
            "note": "salvageable - recut it", "duration": 1,
            "answers_frame": 10, "answers": "", "summary": "",
            "apply": False}
    base.update(over)
    return _ns(**base)


def test_reply_apply_refuses_foreign_cursor(patched, notes, monkeypatch,
                                            capsys):
    import library.tools.marker_feedback as feedback
    notes["Reel 29 - salvage"] = []
    other = _Timeline("Reel 16 - other")
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (other, None))
    assert cmd_markers_reply(_reply_ns(apply=True)) == 1
    assert "cursor sits on" in capsys.readouterr().out
    assert patched["timeline"].added == []


def test_reply_refuses_a_prose_answers(patched, notes, capsys):
    """Reel 04's pink verdict shape never becomes a record again."""
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        color="Blue", name="feedback",
                                        note="is this salvageable?")]
    assert cmd_markers_reply(_reply_ns(answers="R04 blue feedback")) == 1
    assert "not a note identity" in capsys.readouterr().out
    assert patched["timeline"].added == []


# ── Marker reply audit ─────────────────────────────────────────────


def _audit_timeline(green_frame):
    from library.tools import marker_feedback as feedback
    from library.tools.feedback_ledger import durable_identity
    pool = _Pool("/footage/a.mov")
    item = _Item("a.mov", 0, 99, pool=pool)
    identity = durable_identity("Reel 29 - salvage", "feedback\n\ngood")
    payload = feedback.reply_custom_data(
        "", identity, "good", "",
        {"source_file": "/footage/a.mov", "source_frame": 1010})
    return _Timeline(
        "Reel 29 - salvage",
        markers={
            10: {"color": "Blue", "name": "feedback", "note": "good",
                 "duration": 1, "customData": ""},
            green_frame: {"color": "Green", "name": "reply: done",
                          "note": "done", "duration": 1,
                          "customData": payload}},
        tracks={("video", 1): {"name": "V1", "items": [item]}},
        start=0, end=99)


def test_audit_replies_finds_a_drifted_reply(patched, monkeypatch, capsys):
    import library.tools.marker_feedback as feedback
    monkeypatch.setattr(feedback, "read_notes", lambda *a, **k: [])
    drifted = _audit_timeline(50)
    patched["timeline"]._markers = drifted._markers
    patched["timeline"]._tracks = drifted._tracks
    assert cmd_markers_audit_replies(
        _ns(project="", timeline="Reel 29 - salvage", full=False)) == 0
    out = capsys.readouterr().out
    assert "paired-drifted" in out
    assert "drifted: 1" in out


# ── run: the cheap escape hatch ────────────────────────────────────


def _run_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "script": "", "script_pos": "", "file": "", "full": False,
            "json": False, "unsafe": False}
    base.update(over)
    return _ns(**base)


def test_run_truncation_names_escape_hatch(patched, capsys):
    assert cmd_run(_run_ns(
        script="result = [{\"note\": \"x\" * 600}]")) == 0
    out = capsys.readouterr().out
    assert "(truncated, 600 chars total - use --full)" in out
    assert cmd_run(_run_ns(
        script="result = [{\"note\": \"x\" * 600}]", full=True)) == 0
    out = capsys.readouterr().out
    assert "(truncated," not in out
    assert "x" * 100 in out


def test_run_refuses_writers_by_default(patched, capsys):
    assert cmd_run(_run_ns(
        script="timeline.AddMarker(20, 'Blue', 'n', 'w', 1, '')\n"
               "result = {'placed': True}")) == 1
    out = capsys.readouterr().out
    assert "AddMarker" in out
    assert "--unsafe" in out
    # Nothing was written on the refusal path.
    assert patched["timeline"].added == []


def test_run_mention_is_not_a_call(patched, capsys):
    """A string that NAMES a writer is not a writer call."""
    assert cmd_run(_run_ns(
        script="result = [{'note': 'AddMarker is mentioned'}]")) == 0
    assert "AddMarker is mentioned" in capsys.readouterr().out


def test_run_unsafe_writes_under_exclusive_lease(patched, capsys):
    assert cmd_run(_run_ns(
        script="timeline.AddMarker(20, 'Blue', 'note', 'new words', 1, '')\n"
               "result = {'placed': True}",
        unsafe=True)) == 0
    out = capsys.readouterr().out
    assert "unsafe: yes" in out
    assert "cursor_before: Reel 29 - salvage" in out
    assert "cursor_after: Reel 29 - salvage" in out
    assert "cursor_moved: no" in out
    assert patched["timeline"].added == [
        (20, "Blue", "note", "new words", 1, "")]
