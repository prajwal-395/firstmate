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


def test_unknown_flag_fails_loud(patched, capsys):
    parser = resolve_axi.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["timeline", "list", "--stat", "closed"])
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert out.startswith("error: unrecognized arguments: --stat")


# ── The seven commands ───────────────────────────────────────────────


def test_timeline_list_flags_staging(patched, capsys):
    assert cmd_timeline_list(_ns(project="")) == 0
    out = capsys.readouterr().out
    assert "timelines[2]{name,frames,markers,colors,staging," in out
    assert "Reel 29 - salvage (rebuild staging)" in out
    assert "yes,yes" in out  # staging=yes, promotion_pending=yes


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


def test_timeline_get_resolves_unique_prefix(patched, notes, capsys):
    # A prefix matching exactly one timeline resolves to it and says
    # so, so the caller learns the full name without a second call.
    notes["Reel 29 - salvage (rebuild staging)"] = []
    assert cmd_timeline_get(
        _ns(project="", name="Reel 29 - salvage (rebuild")) == 0
    out = capsys.readouterr().out
    assert "resolved: 'Reel 29 - salvage (rebuild' is a unique prefix" in out
    assert "name: Reel 29 - salvage (rebuild staging)" in out


def test_bare_positional_reel_name(patched, notes, capsys):
    """`markers \"Reel 29 - salvage\"` is the obvious shape - it works."""
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10, note="hi")]
    assert resolve_axi.main(["markers", "Reel 29 - salvage"]) == 0
    assert "notes: 1" in capsys.readouterr().out


def test_bare_positional_unique_prefix(patched, notes, capsys):
    notes["Reel 29 - salvage (rebuild staging)"] = []
    assert resolve_axi.main(
        ["frames", "Reel 29 - salvage (rebuild"]) == 0
    out = capsys.readouterr().out
    assert "unique prefix" in out


def test_normalize_leaves_flags_and_subcommands_alone():
    assert resolve_axi._normalize(["markers", "--timeline", "X"]) == \
        ["markers", "--timeline", "X"]
    assert resolve_axi._normalize(
        ["markers", "snapshot", "--timeline", "X",
         "--out", "f"]) == ["markers", "snapshot", "--timeline", "X",
                            "--out", "f"]
    assert resolve_axi._normalize(
        ["markers", "Reel 29"]) == ["markers", "--timeline", "Reel 29"]
    assert resolve_axi._normalize(
        ["items", "Reel 29", "--full"]) == ["items", "--timeline",
                                            "Reel 29", "--full"]
    assert resolve_axi._normalize(["cursor"]) == ["cursor"]


def test_reel_flag_teaches_timeline(patched, capsys):
    parser = resolve_axi.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["markers", "--reel", "Reel 29"])
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "--timeline" in out
    assert "markers \"Reel 29\"" in out


def test_timeline_get_exact_name(patched, notes, capsys):
    notes["Reel 29 - salvage"] = [
        _Note("timeline_marker", 10, color="Green", name="feedback",
              note="good")]
    assert cmd_timeline_get(
        _ns(project="", name="Reel 29 - salvage")) == 0
    out = capsys.readouterr().out
    assert "frames: 100" in out


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


def test_markers_truncation_names_escape_hatch(patched, notes, capsys):
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        note="x" * 600)]
    assert cmd_markers(
        _ns(project="", timeline="Reel 29 - salvage", plane="",
            full=False)) == 0
    out = capsys.readouterr().out
    assert "(truncated, 600 chars total - use --full)" in out


def test_items_reports_source_spans(patched, capsys):
    assert cmd_items(
        _ns(project="", timeline="Reel 29 - salvage",
            transforms=False)) == 0
    out = capsys.readouterr().out
    assert "clips: 2" in out
    assert "/footage/LC0001.MXF" in out
    assert "pan" not in out


def test_items_transforms_columns(patched, capsys):
    reel = patched["timeline"]
    items = reel._tracks[("video", 1)]["items"]
    items[0]._transform = {"Pan": 12.5, "Tilt": -3.0, "ZoomX": 1.2,
                           "ZoomY": 1.2, "Opacity": 100.0}
    assert cmd_items(
        _ns(project="", timeline="Reel 29 - salvage",
            transforms=True)) == 0
    out = capsys.readouterr().out
    assert "pan,tilt,zoomx,zoomy,opacity" in out
    assert "12.5" in out


def test_captions_lists_cards_with_paths(patched, capsys):
    reel = patched["timeline"]
    reel._tracks[("video", 2)] = {
        "name": "Subtitles",
        "items": [_Item("sub_a.mov", 0, 23,
                        pool=_Pool("/renders/sub_a.mov"))]}
    assert cmd_captions(
        _ns(project="", timeline="Reel 29 - salvage")) == 0
    out = capsys.readouterr().out
    assert "cards: 1" in out
    assert "/renders/sub_a.mov" in out


def test_cursor_asserts(patched, capsys):
    assert cmd_cursor(_ns(expect="")) == 0
    assert "Reel 29 - salvage" in capsys.readouterr().out
    assert cmd_cursor(_ns(expect="Reel 29 - salvage")) == 0
    assert cmd_cursor(_ns(expect="Reel 16 - other")) == 1
    assert "may have moved it" in capsys.readouterr().out


def test_frames_reports_counts(patched, capsys):
    assert cmd_frames(
        _ns(project="", timeline="Reel 29 - salvage")) == 0
    out = capsys.readouterr().out
    assert "frames: 100" in out
    assert "video1,2,100" in out


# ── Snapshot / restore ───────────────────────────────────────────────


def test_snapshot_writes_content(patched, notes, tmp_path, capsys):
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        color="Green", name="feedback",
                                        note="good")]
    out_file = str(tmp_path / "markers.json")
    assert cmd_markers_snapshot(
        _ns(project="", timeline="Reel 29 - salvage",
            out=out_file)) == 0
    payload = json.loads(Path(out_file).read_text(encoding="utf-8"))
    assert payload["tool"] == "resolve-axi"
    assert payload["notes"][0]["note"] == "good"


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


def test_restore_apply_writes_missing_only(patched, tmp_path, capsys):
    # Frame 20 is absent live (the fixture holds frame 10), so the
    # apply path has exactly one note to write.
    payload = {"tool": "resolve-axi", "project": "Podcast (field test)",
               "timeline": "Reel 29 - salvage",
               "notes": [{"source": "timeline_marker",
                          "frame": 20, "frame_in_timeline_space": 20,
                          "timecode": "00:00:00:20", "color": "Blue",
                          "name": "note", "note": "new words",
                          "duration_frames": 1, "custom_data_raw": ""}]}
    in_file = tmp_path / "markers.json"
    in_file.write_text(json.dumps(payload), encoding="utf-8")
    # No --allow-partial passed (False, exactly as argparse leaves it):
    # a fully timeline-plane snapshot restores silently, as before.
    assert cmd_markers_restore(
        _ns(project="", timeline="Reel 29 - salvage",
            in_file=str(in_file), apply=True,
            allow_partial=False)) == 0
    assert patched["timeline"].added == [
        (20, "Blue", "note", "new words", 1, "")]
    assert "restored: 1" in capsys.readouterr().out


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


def test_snapshot_names_both_frames_per_plane(field_reel, tmp_path,
                                              capsys):
    out_file = str(tmp_path / "reel24.json")
    assert cmd_markers_snapshot(
        _ns(project="", timeline="Reel 24 - field",
            out=out_file)) == 0
    assert "deprecated:" in capsys.readouterr().out
    payload = json.loads(Path(out_file).read_text(encoding="utf-8"))
    assert "frame_in_timeline_space" in payload["deprecated_fields"]
    by_source = {n["source"]: n for n in payload["notes"]}
    clip = by_source["clip_marker"]
    assert (clip["timeline_frame"], clip["source_frame"]) == (426, 1026)
    assert (clip["frame"], clip["frame_in_timeline_space"]) == (426, 1026)
    moment = by_source["timeline_marker"]
    assert moment["timeline_frame"] == 40
    assert moment["source_frame"] is None
    assert (moment["frame"],
            moment["frame_in_timeline_space"]) == (40, 40)


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
        assert "CLIP-ANCHORED-MARKERS-2026-09-19.md" in out
        assert "--allow-partial" in out
    # The refusal lands before any Resolve contact: nothing written.
    assert field_reel["timeline"].added == []


def test_restore_allow_partial_dry_run_shows_skipped(field_reel,
                                                     tmp_path, capsys):
    in_file = tmp_path / "reel24.json"
    _write_round_trip_snapshot(field_reel["timeline"],
                               "Podcast (field test)", str(in_file))
    # Live holds nothing yet, so the timeline-plane note reads missing.
    field_reel["timeline"]._markers = {}
    assert cmd_markers_restore(
        _ns(project="", timeline="Reel 24 - field",
            in_file=str(in_file), apply=False,
            allow_partial=True)) == 0
    out = capsys.readouterr().out
    assert "to_restore: 1" in out
    assert "skipped[1]{plane,frame,name,note}:" in out
    assert "clip_marker" in out
    assert "CLIP-ANCHORED-MARKERS-2026-09-19.md" in out
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
    assert "CLIP-ANCHORED-MARKERS-2026-09-19.md" in out


def test_fully_restorable_round_trip_restores_silently(monkeypatch,
                                                       tmp_path, capsys):
    timeline = _field_timeline(with_clip_note=False)
    project = _Project("Podcast (field test)", [timeline],
                       current=timeline)
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    in_file = tmp_path / "reel24.json"
    _write_round_trip_snapshot(timeline, "Podcast (field test)",
                               str(in_file))
    timeline._markers = {}
    assert cmd_markers_restore(
        _ns(project="", timeline="Reel 24 - field",
            in_file=str(in_file), apply=True,
            allow_partial=False)) == 0
    out = capsys.readouterr().out
    assert "restored: 1" in out
    assert "skipped" not in out
    assert "cannot write" not in out


# ── Fusion inventory ───────────────────────────────────────────────


def test_fusion_lists_comps_and_coverage(patched, monkeypatch, capsys):
    import library.tools.reel_read as reel_read
    monkeypatch.setattr(reel_read, "read_tracks", lambda timeline: [{
        "type": "video", "index": 4, "name": "V4", "speaker": None,
        "clips": [{
            "name": "overlay.mov", "track_type": "video",
            "track_index": 4, "record_in": 100, "record_out": 200,
            "fusion": {
                "comp_count": 2,
                "comp_names": ["Comp1", "Comp2"],
                "media_windows": [
                    {"comp_index": 1, "window": {},
                     "uncovered_reason": ""},
                    {"comp_index": 2, "window": {},
                     "uncovered_reason": "MediaIn starts 12 frames late"},
                ],
            },
        }],
    }])
    assert cmd_fusion(
        _ns(project="", timeline="Reel 29 - salvage")) == 0
    out = capsys.readouterr().out
    assert "clips_with_comps: 1" in out
    assert "overlay.mov" in out
    assert "MediaIn starts 12 frames late" in out


def test_fusion_empty_states_exactly(patched, monkeypatch, capsys):
    import library.tools.reel_read as reel_read
    monkeypatch.setattr(reel_read, "read_tracks",
                        lambda timeline: [])
    assert cmd_fusion(
        _ns(project="", timeline="Reel 29 - salvage")) == 0
    assert "comps: 0 rows" in capsys.readouterr().out


# ── Marker replies ───────────────────────────────────────────────────


def _reply_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "frame": 20, "color": "Pink", "name": "verdict (axi)",
            "note": "salvageable - recut it", "duration": 1,
            "answers_frame": 10, "answers": "", "summary": "",
            "apply": False}
    base.update(over)
    return _ns(**base)


def test_reply_dry_run_links_answered_words(patched, notes, capsys):
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        color="Blue", name="feedback",
                                        note="is this salvageable?")]
    assert cmd_markers_reply(_reply_ns()) == 0
    out = capsys.readouterr().out
    assert "is this salvageable?" in out
    assert "blocked: no" in out
    # Dry run writes nothing to Resolve.
    assert patched["timeline"].added == []


def test_reply_requires_name_note_color(patched, notes, capsys):
    assert cmd_markers_reply(_reply_ns(note="")) == 1
    assert "--name, --note and --color" in capsys.readouterr().out


def test_reply_dry_run_reports_occupied_frame(patched, notes, capsys):
    notes["Reel 29 - salvage"] = []
    # Frame 10 carries the fixture's own timeline marker.
    assert cmd_markers_reply(_reply_ns(frame=10)) == 0
    assert "occupied" in capsys.readouterr().out


def test_reply_apply_places_verified_marker(patched, notes, capsys):
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        color="Blue", name="feedback",
                                        note="is this salvageable?")]
    assert cmd_markers_reply(_reply_ns(apply=True)) == 0
    out = capsys.readouterr().out
    assert "placed: yes (read back)" in out
    (key, color, name, text, duration, custom), = \
        patched["timeline"].added
    assert (key, color, name) == (20, "Pink", "verdict (axi)")
    assert "is this salvageable?" in custom


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


# ── run: the cheap escape hatch ────────────────────────────────────


def _run_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "script": "", "script_pos": "", "file": "", "full": False,
            "json": False, "unsafe": False}
    base.update(over)
    return _ns(**base)


def test_run_renders_list_of_dicts_as_table(patched, capsys):
    assert cmd_run(_run_ns(
        script="result = [{\"name\": n} for n in timeline_names]"
    )) == 0
    out = capsys.readouterr().out
    assert "result[2]{name}:" in out
    assert "Reel 29 - salvage" in out


def test_run_scope_names(patched, notes, monkeypatch, capsys):
    """The preamble is exactly RUN_SCOPE: no boilerplate in the script."""
    import library.tools.marker_feedback as feedback
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        note="hi")]
    reel = patched["timeline"]
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (reel, patched["project"]))
    assert cmd_run(_run_ns(
        timeline="",
        script="result = {" 
               "\"project\": project_name, "
               "\"timeline\": timeline_name, "
               "\"current\": is_current, "
               "\"notes\": len(read_notes(timeline)), "
               "\"second\": by_index(2).GetName()}"
    )) == 0
    out = capsys.readouterr().out
    assert "project: Podcast (field test)" in out
    assert "timeline: Reel 29 - salvage" in out
    assert "current: true" in out
    assert "notes: 1" in out
    assert "Reel 29 - salvage (rebuild staging)" in out
    # The scope the script saw is exactly the documented one.
    assert set(resolve_axi.RUN_SCOPE) == {
        "resolve", "manager", "project", "project_name",
        "timeline", "timeline_name", "is_current",
        "timeline_names", "by_index", "read_notes"}


def test_run_no_result_says_so(patched, capsys):
    assert cmd_run(_run_ns(script="x = 1")) == 0
    assert "no result" in capsys.readouterr().out


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


def test_run_json_escapes_to_json(patched, capsys):
    assert cmd_run(_run_ns(
        script="result = [{\"name\": timeline_name}]",
        json=True)) == 0
    out = capsys.readouterr().out
    assert '"Reel 29 - salvage"' in out
    # JSON shape, not a TOON table.
    assert "result[1]" not in out


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


def test_run_syntax_error_fails_loud(patched, capsys):
    assert cmd_run(_run_ns(script="result = [")) == 1
    assert "would not parse" in capsys.readouterr().out


def test_run_exception_fails_loud(patched, capsys):
    assert cmd_run(_run_ns(script="raise ValueError('boom')")) == 1
    assert "ValueError: boom" in capsys.readouterr().out


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


def test_run_unsafe_reports_cursor_movement(patched, monkeypatch, capsys):
    import library.tools.marker_feedback as feedback
    other = _Timeline("Reel 16 - other")
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (other, None))
    # A read-only script under --unsafe still reports the (foreign)
    # cursor it ran beside.
    assert cmd_run(_run_ns(
        script="result = [{'name': timeline_name}]",
        unsafe=True)) == 0
    out = capsys.readouterr().out
    assert "cursor_before: Reel 16 - other" in out


def test_run_wide_dicts_point_at_json(patched, capsys):
    wide = ", ".join(f"\"k{i}\": {i}" for i in range(15))
    assert cmd_run(_run_ns(
        script="result = [{" + wide + "}]")) == 0
    out = capsys.readouterr().out
    assert "+3 more columns" in out
    assert "--json" in out


def test_run_needs_a_script(patched, capsys):
    assert cmd_run(_run_ns()) == 1
    assert "--script" in capsys.readouterr().out


def test_run_main_routes_with_scope_flags(patched, capsys):
    assert resolve_axi.main([
        "run", "--timeline", "Reel 29 - salvage",
        "--script", "result = timeline_names"]) == 0
    assert "result[2]{value}:" in capsys.readouterr().out


def test_run_bare_positional_script(patched, capsys):
    """`run "result = ..."` is the obvious shape - it works."""
    assert resolve_axi.main([
        "run", "--timeline", "Reel 29 - salvage",
        "result = timeline_names"]) == 0
    assert "result[2]{value}:" in capsys.readouterr().out


def test_run_positional_and_flag_conflict(patched, capsys):
    assert cmd_run(_run_ns(
        script="result = 1", script_pos="result = 2")) == 1
    assert "not both" in capsys.readouterr().out


def test_run_refusal_echoes_the_form_used(patched, capsys):
    """The --unsafe fix names the form the caller actually used."""
    writer = ("timeline.AddMarker(20, 'Blue', 'n', 'w', 1, '')\n"
              "result = {'placed': True}")
    assert cmd_run(_run_ns(script=writer)) == 1
    out = capsys.readouterr().out
    assert "--script" in out
    assert "--file" not in out
    assert cmd_run(_run_ns(script_pos=writer)) == 1
    out = capsys.readouterr().out
    assert "--unsafe" in out
    assert "--script" not in out and "--file" not in out


def test_run_refusal_echoes_file_form(patched, tmp_path, capsys):
    script_file = tmp_path / "q.py"
    script_file.write_text(
        "timeline.AddMarker(20, 'Blue', 'n', 'w', 1, '')\n"
        "result = {'placed': True}", encoding="utf-8")
    assert cmd_run(_run_ns(file=str(script_file))) == 1
    out = capsys.readouterr().out
    assert f"--file {script_file}" in out
    assert "--script" not in out


@pytest.mark.parametrize("command", ["markers", "items", "captions",
                                     "frames", "fusion"])
def test_positional_primary_args(patched, notes, command, capsys):
    """The class, not the instances: every command taking one obvious
    primary argument - a reel name for the reads, a script for `run` -
    accepts it positionally. A future command built without a
    positional fails here instead of on first real use."""
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        note="hi")]
    assert resolve_axi.main([command, "Reel 29 - salvage"]) == 0
    out = capsys.readouterr().out
    assert "Reel 29 - salvage" in out
    assert resolve_axi.main(
        ["run", "--timeline", "Reel 29 - salvage",
         "result = timeline_names"]) == 0
    assert "result[2]{value}:" in capsys.readouterr().out


# ── Output format ────────────────────────────────────────────────────


def test_table_quotes_with_backticks():
    out = table("t", [{"a": "it's, quoted"}], ["a"])
    assert out.startswith("t[1]{a}:")
    assert "`it's, quoted`" in out


def test_version_answers_fast():
    assert resolve_axi.VERSION
    assert resolve_axi.main(["--version"]) == 0
