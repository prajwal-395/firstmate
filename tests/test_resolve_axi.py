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
    cmd_audio_isolate,
    cmd_captions,
    cmd_color_group,
    cmd_color_lut,
    cmd_cursor,
    cmd_edit_delete,
    cmd_edit_move,
    cmd_edit_place,
    cmd_edit_speed,
    cmd_edit_title,
    cmd_edit_transition,
    cmd_edit_trim,
    cmd_frames,
    cmd_fusion,
    cmd_ingest,
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
    cmd_multicam_build,
    cmd_multicam_sync,
    cmd_pool,
    cmd_project,
    cmd_project_set,
    cmd_render_queue,
    cmd_render_start,
    cmd_render_stop,
    cmd_renders,
    cmd_run,
    cmd_sense_classify,
    cmd_sense_cuts,
    cmd_sense_intellisearch,
    cmd_sense_mask,
    cmd_sense_reframe,
    cmd_sense_switch,
    cmd_sense_transcribe,
    cmd_timeline_duplicate,
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
    def __init__(self, name, props=None, metadata=None,
                 transcript=None):
        self._name = name
        self._props = dict(props or {})
        self._metadata = dict(metadata or {})
        # GetTranscription's answer: None means Resolve holds nothing
        # yet; a dict with wordless segments is the still-processing
        # placeholder the transcribe verb refuses on.
        self._transcript = transcript
        self._transcribe_ok = True
        self._classify_ok = True
        self._classify_files = True
        self._intelli_raise = ""
        self._intelli_ok = True
        self._intelli_files = False

    def GetName(self):
        return self._name

    def GetClipProperty(self, name=None):
        if name is None:
            return dict(self._props)
        return self._props.get(name, "")

    def GetMetadata(self, name=None):
        if name is None:
            return dict(self._metadata)
        return self._metadata.get(name, "")

    def TranscribeAudio(self, use_speakers=None):
        self._transcribe_seen = use_speakers
        return self._transcribe_ok

    def GetTranscription(self):
        if self._transcript is None:
            return {}
        return self._transcript

    def PerformAudioClassification(self):
        if not self._classify_ok:
            return False
        if self._classify_files:
            self._metadata["Category"] = "Dialogue"
            self._metadata["Subcategory"] = "Dialogue"
        return True

    def AnalyzeForIntellisearch(self, identify_faces, is_better):
        self._intelli_seen = (identify_faces, is_better)
        if self._intelli_raise:
            raise RuntimeError(self._intelli_raise)
        if not self._intelli_ok:
            return False
        if self._intelli_files:
            self._metadata["IntelliSearch"] = "analysed"
        return True


class _MediaPool:
    def __init__(self, root):
        self._root = root
        self._current = None
        self._append_empty = False

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._root

    def ImportMedia(self, paths):
        made = []
        for path in paths:
            import os as _os
            clip = _PoolClip(_os.path.basename(path) or path,
                             {"Type": "Video", "File Path": path})
            self._root._clips.append(clip)
            made.append(clip)
        return made

    def AppendToTimeline(self, payloads):
        """Onto the wired current timeline's video track 1, honouring
        the payload's record/source addressing like the plan claims."""
        made = []
        for entry in payloads or []:
            clip = entry.get("mediaPoolItem")
            name = clip.GetName() if clip is not None else "placed"
            start = entry.get("recordFrame")
            src_in = entry.get("startFrame", 0)
            src_out = entry.get("endFrame", src_in + 49)
            if start is None:
                items = self._current._tracks.get(("video", 1),
                                                  {}).get("items", [])
                start = max([i.GetEnd() for i in items] or [0])
            end = start + (src_out - src_in + 1)
            item = _Item(name, start, end, pool=clip,
                         track_type="video",
                         track_index=entry.get("trackIndex", 1))
            # Source spans travel on the item the way Resolve reports
            # them: the fake stores them where the getters read.
            item.GetSourceStartFrame = lambda s=src_in: s
            item.GetSourceEndFrame = lambda s=src_out: s
            self._current._tracks.setdefault(
                ("video", 1), {"name": "V1",
                                "items": []})["items"].append(item)
            made.append(item)
        self.appended = getattr(self, "appended", []) + list(payloads)
        return [] if self._append_empty else made

    def CreateMulticamClip(self, clips, options):
        """Mirror the 21.1 contract: the new pool items, or []."""
        self._multicam_seen = (list(clips), dict(options or {}))
        if getattr(self, "_multicam_none", False):
            return []
        name = (options or {}).get("name", "multicam")
        made = _PoolClip(name, {"Type": "Multicam"})
        self._root._clips.append(made)
        return [made]


class _Transition:
    """What a successful AddTransition answers: an item whose span
    reads (the read-back `edit transition --apply` is judged by)."""

    def __init__(self, options):
        self._options = dict(options)

    def GetName(self):
        return self._options.get("type", "transition")

    def GetStart(self):
        return 50

    def GetEnd(self):
        return 62

    def GetDuration(self):
        return 13


class _Graph:
    """A clip node graph: LUTs per node, re-readable."""

    def __init__(self):
        self._luts: dict = {}
        self._nodes = 3
        self._set_refuse = False
        self._lut_echo = None

    def GetNumNodes(self):
        return self._nodes

    def SetLUT(self, node, path):
        if self._set_refuse:
            return False
        self._luts[node] = path
        return True

    def GetLUT(self, node):
        if self._lut_echo is not None:
            return self._lut_echo
        return self._luts.get(node, "")


class _ColorGroup:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class _Item:
    def __init__(self, name, start, end, pool=None, markers=None,
                 transform=None, track_type="video", track_index=1,
                 properties=None, uid="", stubborn=False):
        self._name = name
        self._start = start
        self._end = end
        self._pool = pool
        self._markers = dict(markers or {})
        self._transform = dict(transform or {})
        self._track_type = track_type
        self._track_index = track_index
        self._properties = dict(properties or {})
        self._uid = uid or f"uid-{self._name}"
        self._stubborn = stubborn
        # 21.1 native-op state: each defaults to the success shape;
        # defect tests flip one flag to the disagreement shape.
        self._speed_set = None
        self._graph = None
        self._group = None

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
        return self._uid

    def GetClipColor(self):
        return ""

    def GetFlagList(self):
        return []

    def GetClipEnabled(self):
        return True

    def GetProperty(self, key=None):
        if key is None:
            return {**self._transform, **self._properties}
        if key in self._properties:
            return self._properties[key]
        if key in self._transform:
            return self._transform[key]
        raise KeyError(key)

    def GetProperties(self):
        """The 21.1 plural reading the reframe verb is judged by."""
        return {**self._transform, **self._properties}

    def SmartReframe(self):
        """Measured shape: True while moving nothing. A test that
        wants the success shape flips `_reframe_moves`."""
        if getattr(self, "_reframe_refuse", False):
            return False
        if getattr(self, "_reframe_moves", False):
            self._transform["Pan"] = 1.5
        return True

    def CreateMagicMask(self, mode):
        """Measured shape: False with the nodes unchanged. A test
        that wants the success shape flips `_mask_ok`."""
        self._mask_seen = mode
        if not getattr(self, "_mask_ok", False):
            return False
        self.GetNodeGraph()._nodes += 1
        return True

    def PerformMulticamSmartSwitch(self, settings):
        self._switch_seen = dict(settings)
        return not getattr(self, "_switch_refuse", False)

    def GetCDL(self):
        return {}

    def GetFusionCompCount(self):
        return 0

    def GetFusionCompNameList(self):
        return []

    def GetMarkers(self):
        return dict(self._markers)

    def GetMediaPoolItem(self):
        return self._pool

    def GetTrackTypeAndIndex(self):
        return [self._track_type, self._track_index]

    def SetProperty(self, key, value):
        if self._stubborn:
            return False
        self._properties[key] = value
        return True

    def SetSpeed(self, options):
        self._speed_set = dict(options)
        return not getattr(self, "_speed_refuse", False)

    def GetSpeed(self):
        echo = getattr(self, "_speed_echo", None)
        if echo is not None:
            return dict(echo)
        if self._speed_set is not None:
            return dict(self._speed_set)
        return {"Percentage": 100.0}

    def AddTransition(self, options):
        self._transition_options = dict(options)
        if getattr(self, "_transition_broken", False):
            return object()
        if getattr(self, "_transition_none", False):
            return None
        return _Transition(dict(options))

    def GetNodeGraph(self):
        if getattr(self, "_no_graph", False):
            return None
        if self._graph is None:
            self._graph = _Graph()
        return self._graph

    def AssignToColorGroup(self, group):
        if getattr(self, "_assign_refuse", False):
            return False
        self._group = group
        return True

    def GetColorGroup(self):
        echo = getattr(self, "_group_echo", "echo-unset")
        if echo != "echo-unset":
            return echo
        return self._group


class _Timeline:
    def __init__(self, name, markers=None, tracks=None, start=0,
                 end=99):
        self._name = name
        self._markers = dict(markers or {})
        self._tracks = tracks or {}  # (type, index) -> {"name":, "items":}
        self._start = start
        self._end = end
        self.added = []
        self.deleted = []
        self.copied = []
        self._delete_fail_once = False
        self._delete_fail_always = False
        self._project_timelines = []
        self._voice: dict = {}
        self._voice_refuse = False
        self._voice_echo = None

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

    def DeleteClips(self, items, ripple=False):
        self.deleted.append(([i.GetUniqueId() for i in items], ripple))
        if self._delete_fail_always:
            return False
        if self._delete_fail_once:
            self._delete_fail_once = False
            return False
        gone = {i.GetUniqueId() for i in items}
        for key in self._tracks:
            self._tracks[key]["items"] = [
                i for i in self._tracks[key]["items"]
                if i.GetUniqueId() not in gone]
        return True

    def DuplicateTimeline(self, name):
        twin = _Timeline(name, tracks={})
        self._project_timelines.append(twin)
        return twin

    def SetVoiceIsolationState(self, index, state):
        if self._voice_refuse:
            return False
        self._voice[index] = dict(state)
        return True

    def DetectSceneCuts(self):
        """Measured shapes: True splitting items, True splitting
        nothing (one continuous take), or False. Tests pick with
        `_cuts_refuse` / `_cuts_nothing`."""
        if getattr(self, "_cuts_refuse", False):
            return False
        if getattr(self, "_cuts_nothing", False):
            return True
        key = ("video", 1)
        if key in self._tracks and self._tracks[key]["items"]:
            first = self._tracks[key]["items"][0]
            mid = (first.GetStart() + first.GetEnd()) // 2
            second = _Item(first.GetName() + " (cut)", mid,
                           first.GetEnd(), pool=first.GetMediaPoolItem(),
                           track_type="video", track_index=1)
            first._end = mid
            self._tracks[key]["items"].insert(1, second)
        return True

    def GetVoiceIsolationState(self, index):
        if self._voice_echo is not None:
            return dict(self._voice_echo)
        return dict(self._voice.get(index, {"isEnabled": False,
                                            "amount": 0}))


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
        self._groups: list = []
        self._presets = list(presets or [])
        self._render_presets = list(render_presets or [])
        if current is not None:
            self._pool._current = current
        for timeline in self._timelines:
            timeline._project_timelines = self._timelines

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

    def SetSettings(self, settings):
        self._settings.update(dict(settings))
        return True

    def AddColorGroup(self, name):
        group = _ColorGroup(name)
        self._groups.append(group)
        return group

    def GetColorGroupsList(self):
        return list(self._groups)

    def SetSetting(self, key, value):
        self._settings[key] = value
        return True

    def LoadRenderPreset(self, name):
        return name in self._render_presets

    def AddRenderJob(self):
        job_id = str(len(self._render_jobs) + 1)
        current = (self._current.GetName()
                   if self._current is not None else "")
        self._render_jobs.append(
            {"JobId": job_id, "RenderJobName": f"job-{job_id}",
             "TimelineName": current, "TargetDir": "/tmp/out",
             "OutputFilename": f"out-{job_id}.mov"})
        self._render_status[job_id] = {"JobStatus": "Ready",
                                       "CompletionPercentage": 0}
        return job_id

    def StartRendering(self, job_ids, interactive=False):
        self._started = (list(job_ids), interactive)
        self._rendering = True
        return True

    def StopRendering(self):
        self._rendering = False
        return True


class _Manager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _Resolve:
    def __init__(self, project):
        self._manager = _Manager(project)
        # The resolve.* enum constants the multicam/sync verbs read
        # off the live object (values are opaque here).
        for const in ("MULTICAM_ANGLE_SYNC_AUDIO",
                      "MULTICAM_ANGLE_SYNC_TIMECODE",
                      "MULTICAM_ANGLE_SYNC_IN",
                      "MULTICAM_ANGLE_SYNC_OUT",
                      "MULTICAM_ANGLE_SYNC_MARKER",
                      "MULTICAM_AUDIO_SOURCE",
                      "AUDIO_SYNC_WAVEFORM",
                      "AUDIO_SYNC_TIMECODE"):
            setattr(self, const, const)

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

    Reads never move the cursor, and the declared writes assert it
    (or report it) rather than moving it. Cursor and project-lifecycle
    movers may not appear anywhere in the module. Every other Resolve
    writer must be a DECLARED one: a future edit that reaches for a
    new writer fails here until it is named below, lease-guarded,
    and covered by a read-back test.
    """
    from library.tools.resolve_axi import _RUN_WRITE_PREFIXES
    source = Path(resolve_axi.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    movers = {"SetCurrentTimeline", "SetCurrentProject",
              "SetCurrentTimeLine", "OpenProject", "CreateTimeline",
              "DeleteTimeline"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in movers:
            hits.append((node.attr, node.lineno))
    assert hits == [], f"cursor-moving calls in resolve_axi: {hits}"
    declared = {"AddMarker", "AppendToTimeline", "DeleteClips",
                "SetProperty", "SetSettings", "SetSetting",
                "ImportMedia", "AddRenderJob", "StartRendering",
                "StopRendering", "LoadRenderPreset",
                "DuplicateTimeline", "SetSpeed", "AddTransition",
                "CreateMulticamClip", "AutoSyncAudio", "SetLUT",
                "AddColorGroup", "AssignToColorGroup",
                "SetVoiceIsolationState", "TranscribeAudio",
                "PerformAudioClassification", "AnalyzeForIntellisearch",
                "DetectSceneCuts", "SmartReframe", "CreateMagicMask",
                "PerformMulticamSmartSwitch"}
    undeclared = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and node.attr.startswith(_RUN_WRITE_PREFIXES)
                and node.attr not in declared):
            undeclared.append((node.attr, node.lineno))
    assert undeclared == [], \
        f"undeclared Resolve writers in resolve_axi: {undeclared}"


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


# ── edit: the write verbs ──────────────────────────────────────
#
# Every write here runs against fakes - never the captain's project.
# The suite proves the discipline, not the pixels: dry runs write
# nothing, applies verify by re-read, and the two measured traps
# (no append retry, delete re-read before one retry) hold.


@pytest.fixture()
def edit_patched(monkeypatch, tmp_path):
    src = tmp_path / "a.mov"
    src.write_text("footage", encoding="utf-8")
    pool_clip = _PoolClip("a.mov", {"Type": "Video",
                                    "File Path": str(src)})
    root = _Folder("root", clips=[pool_clip],
                   subs=[_Folder("Day 1", clips=[_PoolClip(
                       "b.mov", {"Type": "Video",
                                 "File Path": "/nope/b.mov"})])])
    first = _Item("LC0001.MXF", 0, 50,
                  pool=_PoolClip("LC0001.MXF", {"Type": "Video",
                                                "File Path":
                                                "/footage/LC0001.MXF"}),
                  uid="uid-1", track_type="video", track_index=1)
    second = _Item("LC0002.MXF", 50, 100,
                   pool=_PoolClip("LC0002.MXF", {"Type": "Video",
                                                 "File Path":
                                                 "/footage/LC0002.MXF"}),
                   uid="uid-2", track_type="video", track_index=1)
    timeline = _Timeline(
        "Reel 29 - salvage",
        tracks={("video", 1): {"name": "V1",
                                "items": [first, second]},
                ("audio", 1): {"name": "A1", "items": []}},
        start=0, end=99)
    project = _Project("Podcast (field test)", [timeline],
                       current=timeline, pool=_MediaPool(root),
                       render_presets=["H.265 Master"])
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return {"timeline": timeline, "project": project,
            "resolve": _Resolve(project)}


def _place_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "clip": "a.mov", "bin": "", "source_in": None,
            "source_out": None, "media": "both", "track": None,
            "record": None, "apply": False}
    base.update(over)
    return _ns(**base)


def _track_count(edit):
    return len(edit["timeline"]._tracks[("video", 1)]["items"])


def test_place_dry_run_plans_without_appending(edit_patched, capsys):
    assert cmd_edit_place(_place_ns()) == 0
    out = capsys.readouterr().out
    assert "place_plan" in out
    assert "a.mov" in out
    assert _track_count(edit_patched) == 2


def test_place_apply_appends_verified(edit_patched, capsys):
    assert cmd_edit_place(_place_ns(apply=True)) == 0
    out = capsys.readouterr().out
    assert "items_delta: 1" in out
    assert "verified" in out
    assert _track_count(edit_patched) == 3


def test_place_refuses_unknown_clip(edit_patched, capsys):
    assert cmd_edit_place(_place_ns(clip="nope.mov")) == 1
    assert "no clip named 'nope.mov'" in capsys.readouterr().out
    assert _track_count(edit_patched) == 2


def test_place_refuses_an_ambiguous_name(edit_patched, tmp_path,
                                         capsys):
    root = edit_patched["project"]._pool._root
    root._subs[0]._clips.append(
        _PoolClip("a.mov", {"Type": "Video", "File Path": "/x/a.mov"}))
    assert cmd_edit_place(_place_ns(clip="a.mov")) == 1
    out = capsys.readouterr().out
    assert "names 2 clips" in out
    assert "--bin" in out


def test_place_reports_landed_despite_falsy_return(edit_patched,
                                                   capsys):
    """The measured trap: a falsy answer with a moved count means the
    append LANDED. Retrying would place it twice, so this reports
    success and never retries."""
    edit_patched["project"]._pool._append_empty = True
    assert cmd_edit_place(_place_ns(apply=True)) == 0
    out = capsys.readouterr().out
    assert "items_delta: 1" in out
    assert "no retry" in out
    assert _track_count(edit_patched) == 3


def test_place_cursor_mismatch_refuses(edit_patched, monkeypatch,
                                       capsys):
    import library.tools.marker_feedback as feedback
    other = _Timeline("Reel 16 - other")
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (other, None))
    assert cmd_edit_place(_place_ns(apply=True)) == 1
    assert "cursor sits on" in capsys.readouterr().out
    assert _track_count(edit_patched) == 2


def _item_ns(cmd, **over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "track": "video1", "index": 0, "ripple": False,
            "apply": False}
    base.update(over)
    return _ns(**base)


def test_delete_dry_run_names_the_victim(edit_patched, capsys):
    assert cmd_edit_delete(_item_ns(cmd_edit_delete)) == 0
    out = capsys.readouterr().out
    assert "delete_plan" in out
    assert "LC0001.MXF" in out
    assert _track_count(edit_patched) == 2


def test_delete_apply_removes_verified(edit_patched, capsys):
    assert cmd_edit_delete(_item_ns(cmd_edit_delete, apply=True)) == 0
    out = capsys.readouterr().out
    assert "unique id absent" in out
    assert _track_count(edit_patched) == 1
    assert edit_patched["timeline"].deleted == [(["uid-1"], False)]


def test_delete_flaky_first_attempt_still_verifies(edit_patched,
                                                   capsys):
    edit_patched["timeline"]._delete_fail_once = True
    assert cmd_edit_delete(_item_ns(cmd_edit_delete, apply=True)) == 0
    assert "unique id absent" in capsys.readouterr().out
    assert _track_count(edit_patched) == 1


def test_delete_persistent_failure_names_the_edit_page(edit_patched,
                                                       capsys):
    edit_patched["timeline"]._delete_fail_always = True
    assert cmd_edit_delete(_item_ns(cmd_edit_delete, apply=True)) == 1
    out = capsys.readouterr().out
    assert "Edit page" in out
    assert "past once" in out
    assert _track_count(edit_patched) == 2


def test_delete_ripple_is_explicit(edit_patched, capsys):
    assert cmd_edit_delete(_item_ns(cmd_edit_delete, apply=True,
                                     ripple=True)) == 0
    assert edit_patched["timeline"].deleted == [(["uid-1"], True)]


def test_delete_refuses_a_bad_address(edit_patched, capsys):
    assert cmd_edit_delete(_item_ns(cmd_edit_delete, track="video9",
                                     index=0, apply=True)) == 1
    assert cmd_edit_delete(_item_ns(cmd_edit_delete, track="video1",
                                     index=9, apply=True)) == 1
    assert "holds 2" in capsys.readouterr().out


def test_move_apply_replaces_at_the_new_frame(edit_patched, capsys):
    assert cmd_edit_move(_item_ns(
        cmd_edit_move, to=200, track_to="", apply=True)) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    assert [i.GetUniqueId() for i in items] == ["uid-2",
                                                "uid-LC0001.MXF"]
    moved = items[1]
    assert (moved.GetStart(), moved.GetEnd()) == (200, 250)
    assert (moved.GetSourceStartFrame(),
            moved.GetSourceEndFrame()) == (1000, 1049)


def test_move_place_failure_keeps_the_original(edit_patched,
                                               monkeypatch, capsys):
    def raising(payloads):
        raise RuntimeError("nope")
    monkeypatch.setattr(edit_patched["project"]._pool,
                        "AppendToTimeline", raising)
    assert cmd_edit_move(_item_ns(
        cmd_edit_move, to=200, track_to="", apply=True)) == 1
    assert "untouched" in capsys.readouterr().out
    assert _track_count(edit_patched) == 2


def test_trim_apply_narrows_in_place(edit_patched, capsys):
    assert cmd_edit_trim(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, source_in=1010, source_out=1049, apply=True)) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    assert [i.GetUniqueId() for i in items] == ["uid-2",
                                                "uid-LC0001.MXF"]
    trimmed = items[1]
    assert (trimmed.GetSourceStartFrame(),
            trimmed.GetSourceEndFrame()) == (1010, 1049)
    assert trimmed.GetStart() == 10


def test_trim_refuses_past_the_handles(edit_patched, capsys):
    assert cmd_edit_trim(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, source_in=999, source_out=1049, apply=True)) == 1
    assert "handles" in capsys.readouterr().out
    assert _track_count(edit_patched) == 2


def test_title_dry_run_shows_current_text(edit_patched, capsys):
    edit_patched["timeline"]._tracks[("video", 1)]["items"][0]._properties[
        "Styled Text"] = "old words"
    assert cmd_edit_title(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, text="new words", apply=False)) == 0
    out = capsys.readouterr().out
    assert "old words" in out
    assert "new words" in out


def test_title_apply_verifies_by_readback(edit_patched, capsys):
    assert cmd_edit_title(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, text="new words", apply=True)) == 0
    out = capsys.readouterr().out
    assert "read back equal" in out
    assert "Styled Text" in out


def test_title_refuses_when_no_key_takes(edit_patched, capsys):
    edit_patched["timeline"]._tracks[("video", 1)]["items"][
        0]._stubborn = True
    assert cmd_edit_title(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, text="new words", apply=True)) == 1
    assert "Fusion" in capsys.readouterr().out


def test_transition_lists_carriers(edit_patched, capsys):
    assert cmd_edit_transition(_ns(
        project="", timeline="Reel 29 - salvage", apply=False,
        index=None, type="Cross Dissolve", category="simple",
        position="start", alignment="center", duration=None)) == 0
    out = capsys.readouterr().out
    assert "cuts: 1" in out
    assert "LC0001.MXF" in out
    assert "LC0002.MXF" in out


def test_transition_plan_names_the_payload(edit_patched, capsys):
    assert cmd_edit_transition(_ns(
        project="", timeline="Reel 29 - salvage", apply=False,
        index=1, type="Cross Dissolve", category="simple",
        position="start", alignment="center", duration=12)) == 0
    out = capsys.readouterr().out
    assert "transition_plan" in out
    assert "Cross Dissolve" in out
    assert "dry run" in out


def test_transition_apply_places_the_proven_pair(edit_patched,
                                                 capsys):
    assert cmd_edit_transition(_ns(
        project="", timeline="Reel 29 - salvage", apply=True,
        index=1, type="Cross Dissolve", category="simple",
        position="start", alignment="center", duration=12)) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    assert "Cross Dissolve" in out


def test_transition_apply_needs_an_explicit_cut(edit_patched,
                                                capsys):
    assert cmd_edit_transition(_ns(
        project="", timeline="Reel 29 - salvage", apply=True,
        index=None, type="Cross Dissolve", category="simple",
        position="start", alignment="center", duration=None)) == 1
    assert "--index" in capsys.readouterr().out


def test_transition_apply_refuses_an_empty_answer(edit_patched,
                                                  capsys):
    """The defect this verb exists for: AddTransition answers None
    for an unknown type/category, and the write must refuse naming
    both rather than claim a dissolve."""
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][1]._transition_none = True
    assert cmd_edit_transition(_ns(
        project="", timeline="Reel 29 - salvage", apply=True,
        index=1, type="Nope Wipe", category="fusion",
        position="start", alignment="center",
        duration=None)) == 1
    out = capsys.readouterr().out
    assert "Nope Wipe" in out
    assert "fusion" in out


def test_transition_apply_refuses_an_unreadable_span(edit_patched,
                                                     capsys):
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][1]._transition_broken = True
    assert cmd_edit_transition(_ns(
        project="", timeline="Reel 29 - salvage", apply=True,
        index=1, type="Cross Dissolve", category="simple",
        position="start", alignment="center",
        duration=None)) == 1
    assert "would not read its span" in capsys.readouterr().out


def test_writes_keep_explicit_flags():
    """The positional rule stops at reads: a destructive path with a
    bare primary argument is one typo from the wrong reel, so `edit`
    owns no positional - missing flags fail loud at argparse."""
    with pytest.raises(SystemExit) as exc:
        resolve_axi.main(["edit", "delete"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        resolve_axi.main(["edit", "delete", "--timeline", "Reel 29"])
    assert exc.value.code == 2


# ── ingest: pool imports ───────────────────────────────────────


def test_ingest_refuses_missing_files(edit_patched, capsys):
    assert cmd_ingest(_ns(project="", paths=["/nope/m.mov"],
                           apply=True)) == 1
    assert "not on disk" in capsys.readouterr().out


def test_ingest_dry_run_plans(edit_patched, tmp_path, capsys):
    src = str(tmp_path / "a.mov")
    assert cmd_ingest(_ns(project="", paths=[src],
                           apply=False)) == 0
    out = capsys.readouterr().out
    assert "ingest_plan" in out
    assert "root" in out


def test_ingest_apply_imports_named(edit_patched, tmp_path, capsys):
    src = tmp_path / "c.mov"
    src.write_text("footage", encoding="utf-8")
    assert cmd_ingest(_ns(project="", paths=[str(src)],
                           apply=True)) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    assert "c.mov" in out


# ── render: queue, start, stop ─────────────────────────────────


def test_render_queue_cursor_mismatch_refuses(edit_patched,
                                              monkeypatch, capsys):
    import library.tools.marker_feedback as feedback
    other = _Timeline("Reel 16 - other")
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (other, None))
    assert cmd_render_queue(_ns(
        project="", timeline="Reel 29 - salvage", preset="",
        apply=True)) == 1
    assert "cursor sits on" in capsys.readouterr().out


def test_render_queue_refuses_unknown_preset(edit_patched, capsys):
    assert cmd_render_queue(_ns(
        project="", timeline="Reel 29 - salvage", preset="Nope",
        apply=True)) == 1
    assert "no render preset" in capsys.readouterr().out


def test_render_queue_apply_verifies_the_job(edit_patched, capsys):
    assert cmd_render_queue(_ns(
        project="", timeline="Reel 29 - salvage",
        preset="H.265 Master", apply=True)) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    assert "tmp/out/out-1.mov" in out


def test_render_start_refuses_an_empty_queue(edit_patched, capsys):
    assert cmd_render_start(_ns(project="", job=[], all=False,
                                 apply=True)) == 1
    assert "empty" in capsys.readouterr().out


def test_render_start_apply_reports_progress(edit_patched, capsys):
    assert cmd_render_queue(_ns(
        project="", timeline="Reel 29 - salvage", preset="",
        apply=True)) == 0
    capsys.readouterr()
    assert cmd_render_start(_ns(project="", job=[], all=True,
                                 apply=True)) == 0
    assert "in progress" in capsys.readouterr().out


def test_render_stop_apply_verifies_stopped(edit_patched, capsys):
    edit_patched["project"]._rendering = True
    assert cmd_render_stop(_ns(project="", apply=True)) == 0
    assert "not rendering" in capsys.readouterr().out


# ── project set / timeline duplicate ───────────────────────────


def test_project_set_dry_run_shows_old_to_new(edit_patched, capsys):
    assert cmd_project_set(_ns(project="", key="timelineFrameRate",
                                value="29.97", apply=False)) == 0
    out = capsys.readouterr().out
    assert "23.976" in out
    assert "29.97" in out


def test_project_set_apply_verifies(edit_patched, capsys):
    assert cmd_project_set(_ns(project="", key="timelineFrameRate",
                                value="29.97", apply=True)) == 0
    out = capsys.readouterr().out
    assert "read back equal" in out
    assert edit_patched["project"]._settings["timelineFrameRate"] == \
        "29.97"


def test_project_set_refuses_a_silent_write(edit_patched, monkeypatch,
                                             capsys):
    monkeypatch.setattr(_Project, "SetSettings",
                        lambda self, settings: True)
    assert cmd_project_set(_ns(project="", key="timelineFrameRate",
                                value="29.97", apply=True)) == 1
    assert "refusing to claim it" in capsys.readouterr().out


def test_duplicate_refuses_a_taken_name(edit_patched, capsys):
    assert cmd_timeline_duplicate(_ns(
        project="", timeline="Reel 29 - salvage",
        name="Reel 29 - salvage", apply=True)) == 1
    assert "already exists" in capsys.readouterr().out


def test_duplicate_apply_versions_listed(edit_patched, capsys):
    assert cmd_timeline_duplicate(_ns(
        project="", timeline="Reel 29 - salvage", name="Reel 29 - v2",
        apply=True)) == 0
    out = capsys.readouterr().out
    assert "listed on re-read" in out
    assert "Reel 29 - v2" in [
        edit_patched["project"].GetTimelineByIndex(i + 1).GetName()
        for i in range(
            edit_patched["project"].GetTimelineCount())]


# ── edit speed: constant speed plus freeze ───────────────────────


def _speed_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "track": "video1", "index": 0, "percent": 40.0,
            "freeze": False, "ripple": False, "apply": False}
    base.update(over)
    return _ns(**base)


def test_speed_dry_run_plans_without_writing(edit_patched, capsys):
    assert cmd_edit_speed(_speed_ns()) == 0
    out = capsys.readouterr().out
    assert "speed_plan" in out
    assert "40.0" in out
    assert "dry run" in out
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    assert items[0]._speed_set is None


def test_speed_apply_verifies_by_readback(edit_patched, capsys):
    assert cmd_edit_speed(_speed_ns(apply=True)) == 0
    out = capsys.readouterr().out
    assert "read back" in out or "re-reads equal" in out
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    assert items[0]._speed_set == {"Percentage": 40.0,
                                   "RippleTimeline": False}


def test_speed_freeze_spells_zero(edit_patched, capsys):
    assert cmd_edit_speed(_speed_ns(percent=None, freeze=True,
                                    apply=True)) == 0
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    assert items[0]._speed_set == {"Percentage": 0.0,
                                   "RippleTimeline": False}
    assert "0.0" in capsys.readouterr().out


def test_speed_refuses_percent_zero_without_freeze(edit_patched,
                                                   capsys):
    assert cmd_edit_speed(_speed_ns(percent=0.0, apply=True)) == 1
    assert "--freeze" in capsys.readouterr().out


def test_speed_refuses_a_false_answer(edit_patched, capsys):
    """SetSpeed answers False while writing nothing: the verb must
    refuse rather than claim the speed."""
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][0]._speed_refuse = True
    assert cmd_edit_speed(_speed_ns(apply=True)) == 1
    assert "answered False" in capsys.readouterr().out


def test_speed_refuses_a_disagreeing_reread(edit_patched, capsys):
    """The read-back decides: True with a different Percentage is a
    refusal naming both numbers."""
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][0]._speed_echo = {"Percentage": 100.0}
    assert cmd_edit_speed(_speed_ns(apply=True)) == 1
    out = capsys.readouterr().out
    assert "re-reads 100.0 for 40.0" in out
    assert "refusing to claim it" in out


# ── multicam: build writes, sync plans ───────────────────────────


def _multicam_project():
    cam_a = _PoolClip("camA.mp4", {"Type": "Video",
                                   "File Path": "/footage/camA.mp4"})
    cam_b = _PoolClip("camB.mp4", {"Type": "Video",
                                   "File Path": "/footage/camB.mp4"})
    root = _Folder("root", clips=[cam_a, cam_b])
    project = _Project("Podcast (field test)", [], None,
                       pool=_MediaPool(root))
    return project


@pytest.fixture()
def multicam_patched(monkeypatch):
    project = _multicam_project()
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return project


def test_multicam_build_dry_run_plans(multicam_patched, capsys):
    assert cmd_multicam_build(_ns(
        project="", clip=["camA.mp4", "camB.mp4"], bin="",
        name="probe-mc", sync="audio", apply=False)) == 0
    out = capsys.readouterr().out
    assert "multicam_plan" in out
    assert "probe-mc" in out
    assert "dry run" in out


def test_multicam_build_apply_verifies_the_listing(multicam_patched,
                                                   capsys):
    assert cmd_multicam_build(_ns(
        project="", clip=["camA.mp4", "camB.mp4"], bin="",
        name="probe-mc", sync="audio", apply=True)) == 0
    out = capsys.readouterr().out
    assert "listed on re-read" in out
    assert "probe-mc" in out


def test_multicam_build_refuses_when_nothing_lists(multicam_patched,
                                                   capsys):
    """CreateMulticamClip answers [] while listing nothing: the
    verb refuses rather than claim a multicam clip."""
    multicam_patched._pool._multicam_none = True
    assert cmd_multicam_build(_ns(
        project="", clip=["camA.mp4", "camB.mp4"], bin="",
        name="probe-mc", sync="audio", apply=True)) == 1
    assert "not in the pool on re-read" in capsys.readouterr().out


def test_multicam_build_refuses_a_missing_sync_enum(
        multicam_patched, monkeypatch, capsys):
    """A guessed sync mode is worse than a refused one: without the
    live constant the verb names it and stops."""
    import library.tools.marker_feedback as feedback  # noqa: F401
    resolve = resolve_axi._connect()
    del resolve.MULTICAM_ANGLE_SYNC_AUDIO
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    assert cmd_multicam_build(_ns(
        project="", clip=["camA.mp4", "camB.mp4"], bin="",
        name="probe-mc", sync="audio", apply=True)) == 1
    assert "MULTICAM_ANGLE_SYNC_AUDIO" in capsys.readouterr().out


def test_multicam_sync_apply_refuses_without_a_readback(
        multicam_patched, capsys):
    """AutoSyncAudio returns a bare bool with no measured property
    to re-read: --apply refuses naming that, the dry run plans."""
    assert cmd_multicam_sync(_ns(
        project="", clip=["camA.mp4", "camB.mp4"], bin="",
        mode="waveform", apply=False)) == 0
    assert "sync_plan" in capsys.readouterr().out
    assert cmd_multicam_sync(_ns(
        project="", clip=["camA.mp4", "camB.mp4"], bin="",
        mode="waveform", apply=True)) == 1
    out = capsys.readouterr().out
    assert "no measured property to re-read" in out
    assert "hands probe" in out


# ── color: node LUTs and group assignment ────────────────────────


def test_color_lut_dry_run_plans(edit_patched, capsys):
    assert cmd_color_lut(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, lut="Film Looks/Rec709 Kodak 2383 D65.cube", node=1,
        apply=False)) == 0
    out = capsys.readouterr().out
    assert "lut_plan" in out
    assert "Rec709" in out


def test_color_lut_apply_verifies_by_reread(edit_patched, capsys):
    assert cmd_color_lut(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, lut="Film Looks/Rec709 Kodak 2383 D65.cube", node=1,
        apply=True)) == 0
    assert "re-reads the LUT" in capsys.readouterr().out


def test_color_lut_refuses_a_silent_set(edit_patched, capsys):
    """SetLUT answers False while setting nothing: the verb refuses
    naming the node and path."""
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][0].GetNodeGraph()._set_refuse = True
    assert cmd_color_lut(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, lut="Film Looks/x.cube", node=1,
        apply=True)) == 1
    assert "answered False" in capsys.readouterr().out


def test_color_lut_refuses_a_disagreeing_reread(edit_patched,
                                                capsys):
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][0].GetNodeGraph()._lut_echo = "other.cube"
    assert cmd_color_lut(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, lut="Film Looks/x.cube", node=1,
        apply=True)) == 1
    out = capsys.readouterr().out
    assert "refusing to claim it" in out
    assert "other.cube" in out


def test_color_lut_refuses_without_a_graph(edit_patched, capsys):
    edit_patched["timeline"]._tracks[("video", 1)][
        "items"][0]._no_graph = True
    assert cmd_color_lut(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, lut="Film Looks/x.cube", node=1,
        apply=True)) == 1
    assert "no node graph" in capsys.readouterr().out


def test_color_group_create_assigns_verified(edit_patched, capsys):
    assert cmd_color_group(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, group="probe-A", create=True, apply=True)) == 0
    assert "re-reads the name" in capsys.readouterr().out


def test_color_group_refuses_an_unknown_group(edit_patched, capsys):
    assert cmd_color_group(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, group="nope", create=False, apply=True)) == 1
    assert "--create" in capsys.readouterr().out


def test_color_group_refuses_a_silent_assign(edit_patched, capsys):
    """AssignToColorGroup answers True while grouping nothing: the
    re-read disagrees and the verb refuses naming both."""
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    items[0]._group_echo = None
    assert cmd_color_group(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, group="probe-A", create=True, apply=True)) == 1
    assert "refusing to claim it" in capsys.readouterr().out


# ── audio isolate: per-track voice isolation ─────────────────────


def test_isolate_dry_run_plans(edit_patched, capsys):
    assert cmd_audio_isolate(_ns(
        project="", timeline="Reel 29 - salvage", track=1,
        amount=60, disable=False, apply=False)) == 0
    out = capsys.readouterr().out
    assert "isolate_plan" in out
    assert "audio1" in out


def test_isolate_apply_verifies_by_reread(edit_patched, capsys):
    assert cmd_audio_isolate(_ns(
        project="", timeline="Reel 29 - salvage", track=1,
        amount=60, disable=False, apply=True)) == 0
    assert "re-reads equal" in capsys.readouterr().out


def test_isolate_refuses_a_false_answer(edit_patched, capsys):
    edit_patched["timeline"]._voice_refuse = True
    assert cmd_audio_isolate(_ns(
        project="", timeline="Reel 29 - salvage", track=1,
        amount=60, disable=False, apply=True)) == 1
    assert "answered False" in capsys.readouterr().out


def test_isolate_refuses_a_disagreeing_reread(edit_patched, capsys):
    """True with isolation still off on re-read is a refusal naming
    both states."""
    edit_patched["timeline"]._voice_echo = {"isEnabled": False,
                                            "amount": 0}
    assert cmd_audio_isolate(_ns(
        project="", timeline="Reel 29 - salvage", track=1,
        amount=60, disable=False, apply=True)) == 1
    out = capsys.readouterr().out
    assert "refusing to claim it" in out


def test_isolate_refuses_a_missing_track(edit_patched, capsys):
    assert cmd_audio_isolate(_ns(
        project="", timeline="Reel 29 - salvage", track=9,
        amount=60, disable=False, apply=True)) == 1
    assert "names nothing" in capsys.readouterr().out


# ── run: the 21.1 mutators refuse by default ─────────────────────


def test_run_refuses_21_1_mutators_by_default(patched, capsys):
    """Perform/Transcribe/Auto/Assign/Smart/Detect/Generate/Analyze calls
    are writes: `run` refuses them without --unsafe like every
    other mutator."""
    assert cmd_run(_run_ns(
        script="x = item.PerformMulticamSmartSwitch\n"
               "result = {'switched': True}")) == 1
    assert "PerformMulticamSmartSwitch" in capsys.readouterr().out
    assert cmd_run(_run_ns(
        script="x = pool.AutoSyncAudio\n"
               "result = {'synced': True}")) == 1
    assert "AutoSyncAudio" in capsys.readouterr().out
    assert cmd_run(_run_ns(
        script="x = item.AssignToColorGroup\n"
               "result = {'grouped': True}")) == 1
    assert "AssignToColorGroup" in capsys.readouterr().out
    assert cmd_run(_run_ns(
        script="x = clip.AnalyzeForIntellisearch\n"
               "result = {'searched': True}")) == 1
    assert "AnalyzeForIntellisearch" in capsys.readouterr().out


# ── sense: Resolve's own AI, judged by read-back ────────────────


def _sense_clip(name, **kw):
    """A placeable pool clip for the sense verbs: `_pool_find_clip`
    only returns clips whose properties carry a File Path."""
    props = {"Type": "Video + Audio", "File Path": f"/footage/{name}"}
    return _PoolClip(name, props, **kw)


def _voiced_clip():
    return _PoolClip(
        "talk.mov", {"Type": "Video + Audio",
                     "File Path": "/footage/talk.mov"},
        transcript={
            "language": "en",
            "segments": [{
                "start": "00:00:14:16", "end": "00:00:16:02",
                "speaker": "Speaker 1", "text": "okay we are here",
                "words": [
                    {"start": "00:00:14:16", "end": "00:00:15:06",
                     "text": "okay"},
                    {"start": "00:00:15:06", "end": "00:00:16:02",
                     "text": "here"},
                ],
            }],
        })


def test_sense_transcribe_reads_segments_words_speakers(
        multicam_patched, capsys):
    """The read path returns only voiced segments: words with timing
    plus speaker labels, verbatim."""
    multicam_patched._pool._root._clips.append(_voiced_clip())
    assert cmd_sense_transcribe(_ns(
        project="", clip="talk.mov", bin="", apply=False,
        wait=None, full=False)) == 0
    out = capsys.readouterr().out
    assert "segments: 1" in out
    assert "Speaker 1" in out
    assert "words: 2" in out


def test_sense_transcribe_refuses_a_wordless_answer(
        multicam_patched, capsys):
    """One placeholder segment with no words is a refusal naming the
    re-read, never a transcription."""
    clip = _sense_clip("quiet.mov", transcript={
        "language": "en",
        "segments": [{"start": "00:00:00:00", "end": "00:00:00:00",
                      "speaker": None, "text": "",
                      "words": [{"start": "00:00:00:00",
                                 "end": "00:00:00:00", "text": ""}]}]})
    multicam_patched._pool._root._clips.append(clip)
    assert cmd_sense_transcribe(_ns(
        project="", clip="quiet.mov", bin="", apply=False,
        wait=None, full=False)) == 1
    assert "no words" in capsys.readouterr().out


def test_sense_transcribe_apply_starts_and_reads_back(
        multicam_patched, capsys):
    multicam_patched._pool._root._clips.append(_voiced_clip())
    assert cmd_sense_transcribe(_ns(
        project="", clip="talk.mov", bin="", apply=True,
        wait=30, full=False)) == 0
    assert "segments: 1" in capsys.readouterr().out


def test_sense_transcribe_apply_refuses_after_an_empty_wait(
        multicam_patched, capsys):
    """True then nothing within the wait is a refusal naming the
    wait, not a claimed transcription."""
    multicam_patched._pool._root._clips.append(_sense_clip("slow.mov"))
    assert cmd_sense_transcribe(_ns(
        project="", clip="slow.mov", bin="", apply=True,
        wait=0, full=False)) == 1
    out = capsys.readouterr().out
    assert "holds no words after 0s" in out


def test_sense_transcribe_refuses_a_false_start(
        multicam_patched, capsys):
    clip = _sense_clip("stuck.mov")
    clip._transcribe_ok = False
    multicam_patched._pool._root._clips.append(clip)
    assert cmd_sense_transcribe(_ns(
        project="", clip="stuck.mov", bin="", apply=True,
        wait=0, full=False)) == 1
    assert "answered False" in capsys.readouterr().out


def test_sense_cuts_dry_run_lists_carriers(edit_patched, capsys):
    assert cmd_sense_cuts(_ns(
        project="", timeline="Reel 29 - salvage",
        apply=False)) == 0
    out = capsys.readouterr().out
    assert "cuts_plan" in out
    assert "v1_items: 2" in out


def test_sense_cuts_apply_reports_cut_frames(edit_patched, capsys):
    assert cmd_sense_cuts(_ns(
        project="", timeline="Reel 29 - salvage",
        apply=True)) == 0
    out = capsys.readouterr().out
    assert "items_before: 2" in out
    assert "items_after: 3" in out
    assert "cuts: 1" in out


def test_sense_cuts_apply_reports_zero_cuts(edit_patched, capsys):
    """True that splits nothing is zero cuts (one continuous take),
    not a refusal and not cuts."""
    edit_patched["timeline"]._cuts_nothing = True
    assert cmd_sense_cuts(_ns(
        project="", timeline="Reel 29 - salvage",
        apply=True)) == 0
    assert "0 new cuts" in capsys.readouterr().out


def test_sense_cuts_refuses_a_false_answer(edit_patched, capsys):
    edit_patched["timeline"]._cuts_refuse = True
    assert cmd_sense_cuts(_ns(
        project="", timeline="Reel 29 - salvage",
        apply=True)) == 1
    assert "answered False" in capsys.readouterr().out


def test_sense_classify_reads_existing_classes(
        multicam_patched, capsys):
    multicam_patched._pool._root._clips.append(_sense_clip(
        "mix.mov", metadata={"Category": "Dialogue",
                             "Subcategory": "Dialogue"}))
    assert cmd_sense_classify(_ns(
        project="", clip="mix.mov", bin="", apply=False)) == 0
    out = capsys.readouterr().out
    assert "Dialogue" in out


def test_sense_classify_read_refuses_without_classes(
        multicam_patched, capsys):
    multicam_patched._pool._root._clips.append(
        _sense_clip("raw.mov"))
    assert cmd_sense_classify(_ns(
        project="", clip="raw.mov", bin="", apply=False)) == 1
    assert "--apply" in capsys.readouterr().out


def test_sense_classify_apply_verifies_by_reread(
        multicam_patched, capsys):
    multicam_patched._pool._root._clips.append(
        _sense_clip("raw.mov"))
    assert cmd_sense_classify(_ns(
        project="", clip="raw.mov", bin="", apply=True)) == 0
    assert "read back after True" in capsys.readouterr().out


def test_sense_classify_refuses_a_true_that_files_nothing(
        multicam_patched, capsys):
    """True with no Category/Subcategory on re-read is a refusal."""
    clip = _sense_clip("hollow.mov")
    clip._classify_files = False
    multicam_patched._pool._root._clips.append(clip)
    assert cmd_sense_classify(_ns(
        project="", clip="hollow.mov", bin="", apply=True)) == 1
    assert "refusing to claim it" in capsys.readouterr().out


def test_sense_intellisearch_surfaces_resolves_refusal(
        multicam_patched, capsys):
    """The measured shape: Resolve refuses the call itself (the AI
    package is not installed) and the verb reports those words."""
    clip = _sense_clip("face.mov")
    clip._intelli_raise = ("Required package 'AI Intellisearch - "
                           "Faster' is not installed.")
    multicam_patched._pool._root._clips.append(clip)
    assert cmd_sense_intellisearch(_ns(
        project="", clip="face.mov", bin="", apply=True,
        faces=True, better=False)) == 1
    out = capsys.readouterr().out
    assert "AI Intellisearch - Faster" in out


def test_sense_intellisearch_refuses_an_unverified_true(
        multicam_patched, capsys):
    """True with no metadata change is unverified, never analysed."""
    multicam_patched._pool._root._clips.append(
        _sense_clip("face.mov"))
    assert cmd_sense_intellisearch(_ns(
        project="", clip="face.mov", bin="", apply=True,
        faces=False, better=False)) == 1
    assert "unverified" in capsys.readouterr().out


def test_sense_reframe_refuses_an_unchanged_reread(
        edit_patched, capsys):
    """The twice-measured shape: True with Pan/Tilt/ZoomX unchanged
    is a refusal with the measured reason."""
    assert cmd_sense_reframe(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, apply=True)) == 1
    out = capsys.readouterr().out
    assert "read back unchanged" in out


def test_sense_reframe_reports_a_moved_transform(
        edit_patched, capsys):
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    items[0]._reframe_moves = True
    assert cmd_sense_reframe(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, apply=True)) == 0
    assert "transform moved" in capsys.readouterr().out


def test_sense_mask_refuses_a_false_answer(edit_patched, capsys):
    """The measured shape: False with the nodes unchanged."""
    assert cmd_sense_mask(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, mode="F", apply=True)) == 1
    out = capsys.readouterr().out
    assert "answered False" in out


def test_sense_mask_reports_a_gained_node(edit_patched, capsys):
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    items[0]._mask_ok = True
    assert cmd_sense_mask(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, mode="BI", apply=True)) == 0
    assert "node gained" in capsys.readouterr().out


def test_sense_mask_refuses_a_bad_mode(edit_patched, capsys):
    assert cmd_sense_mask(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, mode="sideways", apply=True)) == 1
    assert "bad --mode" in capsys.readouterr().out


def test_sense_switch_reports_counts(edit_patched, capsys):
    assert cmd_sense_switch(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, min_edit=1.0, apply=True)) == 0
    out = capsys.readouterr().out
    assert "count re-read" in out


def test_sense_switch_refuses_a_false_answer(edit_patched, capsys):
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    items[0]._switch_refuse = True
    assert cmd_sense_switch(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, min_edit=1.0, apply=True)) == 1
    assert "answered False" in capsys.readouterr().out
