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
    cmd_api_docs,
    cmd_api_search,
    cmd_api_stubs,
    cmd_api_whats_new,
    cmd_audio,
    cmd_captions,
    cmd_cursor,
    cmd_frames,
    cmd_fusion,
    cmd_items,
    cmd_launch,
    cmd_luts_delete,
    cmd_luts_generate,
    cmd_luts_list,
    cmd_luts_update,
    cmd_markers,
    cmd_markers_audit_replies,
    cmd_markers_reply,
    cmd_markers_restore,
    cmd_markers_snapshot,
    cmd_pool,
    cmd_project,
    cmd_renders,
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


class _Folder:
    def __init__(self, name, clips=None, subs=None):
        self._name = name
        self._clips = list(clips or [])
        self._subs = list(subs or [])

    def GetName(self):
        return self._name

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _PoolClip:
    def __init__(self, name, props=None):
        self._name = name
        self._props = dict(props or {})

    def GetName(self):
        return self._name

    def GetClipProperty(self, name=None):
        if name is None:
            return dict(self._props)
        return self._props.get(name, "")


class _MediaPool:
    def __init__(self, root):
        self._root = root

    def GetRootFolder(self):
        return self._root


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

    def GetIsTrackEnabled(self, track_type, index):
        return bool(self._tracks[(track_type, index)].get("enabled",
                                                           True))

    def GetIsTrackLocked(self, track_type, index):
        return bool(self._tracks[(track_type, index)].get("locked",
                                                           False))

    def GetTrackSubType(self, track_type, index):
        return self._tracks[(track_type, index)].get("subtype", "")

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

    def CopyGrades(self, items):
        self.copied = list(items)
        return True


class _Project:
    """A project whose cursor cannot be moved: any attempt raises."""

    def __init__(self, name, timelines, current, pool=None,
                 render_jobs=None, render_status=None,
                 rendering=False, settings=None, presets=None,
                 render_presets=None):
        self._name = name
        self._timelines = list(timelines)
        self._current = current
        self._pool = pool or _MediaPool(_Folder("root"))
        self._render_jobs = list(render_jobs or [])
        self._render_status = dict(render_status or {})
        self._rendering = rendering
        self._settings = dict(settings or {
            "timelineFrameRate": "23.976",
            "timelineResolutionWidth": "3840",
            "timelineResolutionHeight": "2160"})
        self._presets = list(presets or [])
        self._render_presets = list(render_presets or [])

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

    def GetMediaPool(self):
        return self._pool

    def GetRenderJobList(self):
        return [dict(job) for job in self._render_jobs]

    def GetRenderJobStatus(self, job_id):
        return dict(self._render_status.get(job_id, {}))

    def IsRenderingInProgress(self):
        return self._rendering

    def GetSetting(self, key):
        return self._settings.get(key, "")

    def GetPresetList(self):
        return list(self._presets)

    def GetRenderPresetList(self):
        return list(self._render_presets)


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
            "json": False, "unsafe": False,
            "acknowledge_copy_grades": False}
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


# ── pool: the ingest/catalog read ────────────────────────────────


def _pool_project(tmp_path):
    real = tmp_path / "a.mov"
    real.write_text("footage", encoding="utf-8")
    root = _Folder(
        "root",
        clips=[_PoolClip("a.mov", {"Type": "Video",
                                   "File Path": str(real)})],
        subs=[_Folder(
            "Day 1",
            clips=[_PoolClip("b.mov", {"Type": "Video",
                                       "File Path": "/nope/b.mov"}),
                   _PoolClip("Reel 29 - salvage", {"Type": "Timeline"})])])
    project = _Project("Podcast (field test)", [], None,
                       pool=_MediaPool(root))
    return project


@pytest.fixture()
def pool_patched(monkeypatch, tmp_path):
    project = _pool_project(tmp_path)
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return project


def test_pool_walks_subfolders_and_counts_offline(pool_patched, capsys):
    """A flat root-only listing reports 1 of 3: the bins hold the rest."""
    assert cmd_pool(_ns(project="", bin="")) == 0
    out = capsys.readouterr().out
    assert "clips: 3" in out
    assert "bins: 2" in out
    assert "offline: 1" in out
    assert "Day 1" in out
    assert "b.mov" in out


def test_pool_bin_scopes_the_walk(pool_patched, capsys):
    assert cmd_pool(_ns(project="", bin="Day 1")) == 0
    out = capsys.readouterr().out
    assert "clips: 2" in out
    assert "a.mov" not in out
    assert resolve_axi.main(["pool", "Day 1"]) == 0
    assert "clips: 2" in capsys.readouterr().out


def test_pool_refuses_unknown_bin(pool_patched, capsys):
    assert cmd_pool(_ns(project="", bin="Day 9")) == 1
    out = capsys.readouterr().out
    assert "no bin 'Day 9'" in out
    assert "A/B/C" in out


# ── renders: the queue read ──────────────────────────────────────


def _render_project():
    jobs = [
        {"JobId": "1", "RenderJobName": "Reel 29",
         "TimelineName": "Reel 29 - salvage", "TargetDir": "/tmp/out",
         "OutputFilename": "r29.mov"},
        {"JobId": "2", "RenderJobName": "Reel 30",
         "TimelineName": "Reel 30", "TargetDir": "/tmp/out",
         "OutputFilename": "r30.mov"},
        {"JobId": "3", "RenderJobName": "Reel 31",
         "TimelineName": "Reel 31", "TargetDir": "/tmp/out",
         "OutputFilename": "r31.mov"},
    ]
    status = {
        # An Italian install: the English literal never appears, and a
        # reader comparing against "Complete" calls this queued.
        "1": {"JobStatus": "Concluso", "CompletionPercentage": 100},
        "2": {"JobStatus": "Rendering", "CompletionPercentage": 45},
        "3": {"JobStatus": "Fallito", "CompletionPercentage": 12,
              "Error": "codec missing"},
    }
    return _Project("Podcast (field test)", [], None,
                    render_jobs=jobs, render_status=status,
                    rendering=True)


@pytest.fixture()
def render_patched(monkeypatch):
    project = _render_project()
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return project


def test_renders_derives_state_without_reading_english(render_patched,
                                                       capsys):
    assert cmd_renders(_ns(project="", full=False)) == 0
    out = capsys.readouterr().out
    assert "jobs: 3" in out
    assert "rendering: yes" in out
    assert "complete: 1" in out
    assert "failed: 1" in out
    assert "codec missing" in out
    # States derived from percent/error, never the localized string:
    # the Italian "Concluso" at 100% is complete, not queued.
    assert "Reel 29 - salvage,100,complete" in out
    assert "Reel 30,45,queued" in out
    assert "Reel 31,12,failed" in out


# ── run: the grade-destroying call ───────────────────────────────


def test_run_unsafe_refuses_copygrades_without_ack(patched, capsys):
    """`--unsafe` declares a write, not THIS one: CopyGrades replaces
    the target's whole grade, returns True, and versions nothing."""
    assert cmd_run(_run_ns(
        script="x = timeline.CopyGrades",
        unsafe=True)) == 1
    out = capsys.readouterr().out
    assert "CopyGrades" in out
    assert "--acknowledge-copy-grades" in out


def test_run_unsafe_runs_copygrades_once_named(patched, capsys):
    assert cmd_run(_run_ns(
        script="timeline.CopyGrades([])\nresult = {'copied': True}",
        unsafe=True, acknowledge_copy_grades=True)) == 0
    assert "unsafe: yes" in capsys.readouterr().out
    assert patched["timeline"].copied == []


# ── The positional rule, restored ────────────────────────────────
#
# The suite-halving deleted `test_positional_primary_args` while the
# module docstring still cites it. `pool` is the next command the
# rule covers, so the test comes back with the bin on it.


@pytest.mark.parametrize("command", ["markers", "items", "captions",
                                     "frames", "fusion"])
def test_positional_primary_args(patched, notes, command, capsys):
    """The class, not the instances: every command taking one obvious
    primary argument - a reel name for the reads, a script for `run`,
    a bin for `pool` - accepts it positionally. A future command
    built without a positional fails here instead of on first use."""
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        note="hi")]
    assert resolve_axi.main([command, "Reel 29 - salvage"]) == 0
    out = capsys.readouterr().out
    assert "Reel 29 - salvage" in out


def test_positional_run_script(patched, capsys):
    assert resolve_axi.main(
        ["run", "--timeline", "Reel 29 - salvage",
         "result = timeline_names"]) == 0
    assert "result[2]{value}:" in capsys.readouterr().out


# ── api: the wrapped knowledge tools ───────────────────────────


@pytest.fixture()
def canned(monkeypatch):
    import types as _types
    box = _types.SimpleNamespace(seen={}, responses={})

    def fake_native(tool, args, fix):
        box.seen["tool"] = tool
        box.seen["args"] = args
        return box.responses[tool], ""

    monkeypatch.setattr(resolve_axi, "_native", fake_native)
    return box


def test_api_search_caps_at_fifty(canned, capsys):
    canned.responses["search_scripting_api"] = "\n".join(
        f"  def GetMarker{i}() ..." for i in range(60))
    assert cmd_api_search(_ns(pattern="marker", full=False)) == 0
    out = capsys.readouterr().out
    assert "matches: 60" in out
    assert "+10 more line(s)" in out
    assert cmd_api_search(_ns(pattern="marker", full=True)) == 0
    assert "GetMarker59" in capsys.readouterr().out
    assert canned.seen["tool"] == "search_scripting_api"
    assert canned.seen["args"] == {"pattern": "marker"}


def test_api_search_surfaces_a_dead_backend(monkeypatch, capsys):
    from library.tools.native_mcp import NativeMcpError
    monkeypatch.setattr(
        "library.tools.native_mcp.call",
        lambda tool, args=None, timeout=90: (_ for _ in ()).throw(
            NativeMcpError("wrapper is down")))
    assert cmd_api_search(_ns(pattern="marker", full=False)) == 1
    out = capsys.readouterr().out
    assert "wrapper is down" in out
    assert "help:" in out


def test_api_stubs_returns_the_whole_unit(canned, capsys):
    canned.responses["get_scripting_api"] = "class RenderJobInfo:\n\tJobId: str"
    assert cmd_api_stubs(_ns(types=["RenderJobInfo"])) == 0
    out = capsys.readouterr().out
    assert "class RenderJobInfo" in out
    assert canned.seen["args"] == {"types": ["RenderJobInfo"]}


def test_api_docs_unwraps_the_envelope(canned, capsys):
    canned.responses["get_scripting_docs"] = json.dumps({
        "content": [{"type": "text", "text": "Hello docs"}]})
    assert cmd_api_docs(_ns(document="README.md",
                             section="Audio Mapping")) == 0
    out = capsys.readouterr().out
    assert "Hello docs" in out
    assert '{"content"' not in out


def test_api_docs_passes_plain_text_through(canned, capsys):
    canned.responses["get_scripting_docs"] = "plain"
    assert cmd_api_docs(_ns(document="README.md", section="TOC")) == 0
    assert "plain" in capsys.readouterr().out


def test_api_whats_new_tables_releases(canned, capsys):
    canned.responses["get_whats_new"] = json.dumps({"entries": [
        {"version": "21.0.4", "date": "2026-08-05",
         "changelog": "* fixes galore"},
        {"version": "21.0.3", "date": "2026-07-22",
         "changelog": "* older fixes"},
    ]})
    assert cmd_api_whats_new(_ns(since="21.0", full=False)) == 0
    out = capsys.readouterr().out
    assert "entries: 2" in out
    assert "21.0.4" in out
    assert "fixes galore" not in out
    assert cmd_api_whats_new(_ns(since="21.0", full=True)) == 0
    assert "fixes galore" in capsys.readouterr().out


def test_api_whats_new_refuses_garbage(canned, capsys):
    canned.responses["get_whats_new"] = "not json at all"
    assert cmd_api_whats_new(_ns(since="21.0", full=False)) == 1
    assert "unparseable" in capsys.readouterr().out


def test_positional_api_search_and_luts_delete(canned, shelf, capsys):
    canned.responses["search_scripting_api"] = "  def GetMarkers() ..."
    assert resolve_axi.main(["api", "search", "marker"]) == 0
    assert "matches:" in capsys.readouterr().out
    assert resolve_axi.main(["luts", "delete", "cool.dctl"]) == 0
    assert "dry run" in capsys.readouterr().out


# ── luts: the shared shelf ─────────────────────────────────────


@pytest.fixture()
def shelf(monkeypatch, tmp_path):
    monkeypatch.setattr(resolve_axi, "LUT_DIR", str(tmp_path))
    (tmp_path / "cool.dctl").write_text("dctl", encoding="utf-8")
    (tmp_path / "warm.cube").write_text("cube", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a lut", encoding="utf-8")
    return tmp_path


def test_luts_list_counts_kinds(shelf, capsys):
    assert cmd_luts_list(_ns()) == 0
    out = capsys.readouterr().out
    assert "files: 2" in out
    assert "cool.dctl" in out
    assert "warm.cube" in out
    assert "notes.txt" not in out


def test_luts_update_dry_run_writes_nothing(shelf, monkeypatch, capsys):
    def no_call(tool, args, fix):
        raise AssertionError("dry run must not reach the backend")
    monkeypatch.setattr(resolve_axi, "_native", no_call)
    assert cmd_luts_update(_ns(name="new.dctl", name_pos="",
                                text="__DEVICE__ float x;",
                                file="", apply=False)) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert not (shelf / "MCP" / "new.dctl").exists()


def test_luts_update_apply_verifies_by_readback(shelf, monkeypatch,
                                                capsys):
    seen = {}

    def fake_native(tool, args, fix):
        seen["tool"] = tool
        return '{"written": true}', ""

    monkeypatch.setattr(resolve_axi, "_native", fake_native)
    target = shelf / "MCP" / "new.dctl"
    assert cmd_luts_update(_ns(name="new.dctl", name_pos="",
                                text="x", file="", full=False,
                                apply=True)) == 1
    assert seen["tool"] == "update_dctl"
    assert "NO - the server said written" in capsys.readouterr().out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    assert cmd_luts_update(_ns(name="new.dctl", name_pos="",
                                text="x", file="", full=False,
                                apply=True)) == 0
    assert "yes (read back)" in capsys.readouterr().out


def test_luts_delete_refuses_shelf_escape(shelf, capsys):
    assert cmd_luts_delete(_ns(name="../evil.dctl", name_pos="",
                                apply=False)) == 1
    assert "escapes" in capsys.readouterr().out
    assert cmd_luts_delete(_ns(name="/abs/evil.dctl", name_pos="",
                                apply=False)) == 1
    assert "escapes" in capsys.readouterr().out


def test_luts_delete_routes_by_extension(shelf, monkeypatch, capsys):
    seen = {}

    def fake_native(tool, args, fix):
        seen["tool"] = tool
        return "gone", ""

    monkeypatch.setattr(resolve_axi, "_native", fake_native)
    assert cmd_luts_delete(_ns(name="cool.dctl", name_pos="",
                                full=False, apply=True)) == 0
    assert seen["tool"] == "delete_dctl"
    assert cmd_luts_delete(_ns(name="warm.cube", name_pos="",
                                full=False, apply=True)) == 0
    assert seen["tool"] == "delete_lut"


def test_luts_generate_dry_run_names_size(shelf, capsys):
    assert cmd_luts_generate(_ns(name="", name_pos="warm.cube",
                                  size=33, transform="return (r, g, b)",
                                  transform_file="",
                                  apply=False)) == 0
    out = capsys.readouterr().out
    assert "size: 33" in out
    assert "dry run" in out


# ── launch: the idempotent start ───────────────────────────────


def test_launch_reports_an_open_session(patched, capsys):
    assert cmd_launch(_ns()) == 0
    out = capsys.readouterr().out
    assert "already - nothing launched" in out
    assert "Reel 29 - salvage" in out


def test_launch_starts_a_closed_resolve(monkeypatch, capsys):
    import contextlib as _cl
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: _cl.nullcontext())
    calls = {"connects": 0}

    def flaky_connect():
        calls["connects"] += 1
        if calls["connects"] == 1:
            raise AxiError("closed", "open it")
        project = _Project("Podcast (field test)", [], None)
        return _Resolve(project)

    monkeypatch.setattr(resolve_axi, "_connect", flaky_connect)
    launched = {}

    def fake_open():
        launched["ran"] = True
        return True, ""

    monkeypatch.setattr(resolve_axi, "_launch_app", fake_open)
    assert cmd_launch(_ns()) == 0
    out = capsys.readouterr().out
    assert launched["ran"]
    assert "launched now" in out


def test_launch_reports_a_failed_open(monkeypatch, capsys):
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: (_ for _ in ()).throw(
                            AxiError("closed", "open it")))
    monkeypatch.setattr(resolve_axi, "_launch_app",
                        lambda: (False, "denied by policy"))
    assert cmd_launch(_ns()) == 1
    assert "denied by policy" in capsys.readouterr().out


def test_launch_argv_names_the_real_app():
    """`open -a` with the wrong name opens the wrong app or nothing:
    the argv is pinned here, and the name verified against the
    installed bundle id (see `_LAUNCH_ARGV`)."""
    assert list(resolve_axi._LAUNCH_ARGV) == [
        "open", "-a", "DaVinci Resolve"]


# ── project: identity plus delivery settings ───────────────────


def test_project_reports_delivery_settings(patched, capsys):
    assert cmd_project(_ns(project="")) == 0
    out = capsys.readouterr().out
    assert "Podcast (field test)" in out
    assert "23.976" in out
    assert "3840x2160" in out
    assert "Reel 29 - salvage" in out


# ── audio: the read-only probe ─────────────────────────────────


@pytest.fixture()
def audio_patched(monkeypatch):
    first = _Item("a.wav", 0, 48, pool=_Pool("/audio/a.wav"))
    second = _Item("b.wav", 48, 96, pool=_Pool("/audio/b.wav"))
    timeline = _Timeline(
        "Reel 29 - salvage",
        tracks={("audio", 1): {"name": "A1",
                                "items": [first, second]},
                ("audio", 2): {"name": "A2", "items": [],
                                "enabled": False, "locked": True,
                                "subtype": "Stereo"}},
        start=0, end=99)
    project = _Project("Podcast (field test)", [timeline],
                       current=timeline)
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return timeline


def test_audio_lists_enable_state(audio_patched, capsys):
    """A muted row must read muted: the mix decision needs it."""
    assert cmd_audio(_ns(project="", timeline="Reel 29 - salvage",
                          full=False)) == 0
    out = capsys.readouterr().out
    assert "tracks: 2" in out
    assert "audio1,A1,yes,2" in out
    assert "audio2,A2,no,0" in out


def test_audio_full_tolerates_a_missing_voice_call(audio_patched,
                                                   capsys):
    """The fake defines no GetVoiceIsolationState: an older Resolve
    answers the same way, with absence rather than an exception."""
    assert cmd_audio(_ns(project="", timeline="Reel 29 - salvage",
                          full=True)) == 0
    out = capsys.readouterr().out
    assert "Stereo" in out
    assert "voice_isolation" in out
