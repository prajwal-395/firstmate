"""resolve-axi: the safe behaviour is the default, and the tests prove it.

Every test here drives `library.tools.resolve_axi` against fakes - no
Resolve, no real project (AGENTS.md 8). Two invariants get permanent
coverage because sibling lanes build while this tool reads:

1. READS NEVER MOVE THE CURSOR. The fake project raises on
   `SetCurrentTimeline`/`SetCurrentProject`/opens/creates, and every
   read command runs against it. A cursor-moving read would fail here
   rather than killing a sibling lane's Fusion pass mid-build.
2. Timeline-scoped writes are lease-guarded and cursor-asserted; the
   incremental marker, transform, enable and local-delete paths submit
   generation patches.
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
    cmd_ingest,
    cmd_launch,
    cmd_luts_delete,
    cmd_luts_generate,
    cmd_luts_list,
    cmd_luts_update,
    cmd_markers,
    cmd_markers_audit_replies,
    cmd_markers_reply,
    cmd_markers_restore,
    cmd_multicam_build,
    cmd_multicam_sync,
    cmd_ownership_adopt,
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
    _item_span,
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

    def GetMarkers(self):
        return {}

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

    def GetUniqueId(self):
        return f"timeline:{self._name}"

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


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """`main()` routes through a serving `ren-resolved` broker to the live
    Resolve; these tests drive the fakes, so the broker is never serving."""
    from library.tools.resolved import client
    monkeypatch.setattr(client, "serving", lambda *a, **k: False)


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


def test_timeline_get_refuses_ambiguous_prefix(
        patched, capsys, tmp_path, monkeypatch):
    # "Reel 29" is a prefix of BOTH the final and its staging sibling -
    # the incident-2 collision shape. It must refuse, compactly, and
    # name the staging sibling as the cause (that is the case that
    # bites: the caller asked for the reel and the rebuild answered).
    from library.tools import timeline_read, timeline_shadow
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    for index, name in enumerate((
            "Reel 29 - salvage",
            "Reel 29 - salvage (rebuild staging)"), start=1):
        store.record(project="Podcast (field test)",
                     timeline_id=f"timeline-{index}", timeline_name=name,
                     snapshot={"timeline": name},
                     source=timeline_shadow.OBSERVED, expected_head=0)
    assert cmd_timeline_get(_ns(project="", name="Reel 29")) == 1
    out = capsys.readouterr().out
    assert "prefix of 2 recorded timelines" in out
    assert "(rebuild staging)" in out
    # The refusal must NOT dump the whole project listing: that dump
    # is the token cost this tool removes.
    assert len(out) < 800


def test_timeline_get_reads_the_recorded_generation(patched, capsys,
                                                    tmp_path, monkeypatch):
    from library.tools import timeline_read, timeline_shadow
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    snapshot = {
        "timeline": "Reel 29 - salvage",
        "reported_fps": 23.976,
        "start_frame": 0,
        "end_frame": 99,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": [{"name": "LC0001.MXF"},
                              {"name": "LC0002.MXF"}]},
                   {"type": "audio", "index": 1, "name": "A1",
                    "clips": []}],
        "markers": {
            "timeline": [{"color": "Green"}],
            "notes": [{"source": "timeline_marker", "color": "Green"},
                      {"source": "clip_marker", "color": "Blue"}],
        },
    }
    generation = store.record(
        project="Podcast (field test)", timeline_id="timeline-29",
        timeline_name="Reel 29 - salvage", snapshot=snapshot,
        source=timeline_shadow.OBSERVED, expected_head=0)

    assert cmd_timeline_get(_ns(project="", name="Reel 29 - salvage")) == 0

    out = capsys.readouterr().out
    assert "source: recorded generation" in out
    assert f"generation: {generation.generation}" in out
    assert "video1" in out and "V1" in out
    assert "clip_marker" in out and "Blue" in out
    assert store.read_requests_since(0)["shadow_hits"] == 1


def test_default_timeline_read_bypasses_resolve_lease_and_broker(
        patched, capsys, tmp_path, monkeypatch):
    from library.tools import timeline_read, timeline_shadow
    from library.tools.resolved import client
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    store.record(
        project="Podcast (field test)", timeline_id="timeline-29",
        timeline_name="Reel 29 - salvage",
        snapshot={
            "reported_fps": 24, "start_frame": 0, "end_frame": 9,
            "tracks": [],
            "markers": {"timeline": [], "notes": []},
        },
        source=timeline_shadow.OBSERVED, expected_head=0)
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: pytest.fail("offline read connected to Resolve"))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda _exclusive: pytest.fail("offline read took lease"))
    monkeypatch.setattr(client, "serving", lambda: True)
    monkeypatch.setattr(resolve_axi, "_through_broker",
                        lambda *_args: pytest.fail("offline read used broker"))

    assert resolve_axi.main([
        "timeline", "get", "Reel 29 - salvage", "--project",
        "Podcast (field test)"]) == 0

    assert "source: recorded generation" in capsys.readouterr().out
    assert store.read_requests_since(0)["shadow_hits"] == 1


def test_explicit_refresh_reads_only_the_exact_current_timeline(
        patched, capsys, monkeypatch):
    from library.tools import timeline_read, timeline_shadow
    snapshot = {
        "reported_fps": 24, "start_frame": 0, "end_frame": 9,
        "tracks": [], "markers": {"timeline": [], "notes": []},
    }
    generation = timeline_shadow.Generation(
        project="Podcast (field test)", timeline_id="timeline-29",
        generation=1, timeline_name=patched["timeline"].GetName(),
        hash="snapshot-hash", source=timeline_shadow.OBSERVED,
        patch=None, receipt=None, recorded_at=1.0, verified_at=2.0)
    expected = timeline_read.TimelineRead(generation, snapshot)
    calls = []

    def refresh(project, timeline, **kwargs):
        calls.append((project, timeline, kwargs))
        return expected

    monkeypatch.setattr(timeline_read, "refresh", refresh)

    assert cmd_timeline_get(_ns(
        project="", name="Reel 29 - salvage", refresh_live=True)) == 0

    assert calls == [(patched["project"], patched["timeline"], {})]
    assert "source: live refresh" in capsys.readouterr().out


def test_timeline_list_uses_recorded_heads(patched, capsys, tmp_path,
                                           monkeypatch):
    from library.tools import timeline_read, timeline_shadow
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    for index, name in enumerate((
            "Reel 29 - salvage",
            "Reel 29 - salvage (rebuild staging)"), start=1):
        snapshot = {
            "start_frame": 100,
            "end_frame": 199,
            "markers": {"timeline": [{"color": "Green"}]
                        if index == 1 else []},
        }
        store.record(project="Podcast (field test)",
                     timeline_id=f"timeline-{index}", timeline_name=name,
                     snapshot=snapshot,
                     source=timeline_shadow.OBSERVED, expected_head=0)

    assert resolve_axi.cmd_timeline_list(_ns(project="")) == 0

    out = capsys.readouterr().out
    assert "source: recorded generations" in out
    assert "promotion_pending" in out
    assert ",yes,yes," in out
    assert "100" in out
    assert store.read_requests_since(0)["shadow_hits"] == 1


def test_markers_reads_both_planes(patched, capsys, tmp_path, monkeypatch):
    """Incident 1's trap: a timeline-plane-only read reports 1 of 2."""
    from library.tools import timeline_read, timeline_shadow
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    notes = [
        _Note("timeline_marker", 10, color="Green", name="feedback",
              note="good"),
        _Note("clip_marker", 0, color="Blue", name="fix", note="trim"),
    ]
    store.record(
        project="Podcast (field test)", timeline_id="timeline-29",
        timeline_name="Reel 29 - salvage",
        snapshot={"markers": {"notes": [{
            "source": note.source, "frame": note.frame,
            "timecode": note.timecode, "color": note.color,
            "name": note.name, "note": note.note, "text": note.text,
        } for note in notes]}},
        source=timeline_shadow.OBSERVED, expected_head=0)
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
def field_reel(monkeypatch, tmp_path):
    timeline = _field_timeline()
    project = _Project("Podcast (field test)", [timeline],
                       current=timeline)
    project.SetCurrentTimeline = lambda target: (
        setattr(project, "_current", target) or True)
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
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


@pytest.fixture()
def run_opt_in(monkeypatch):
    """The developer opt-in `resolve-axi run` requires: these tests
    exercise the runner itself, so they declare a developer shell."""
    monkeypatch.setenv(resolve_axi.RUN_OPT_IN_ENV, "1")


def test_run_refuses_without_developer_opt_in(patched, monkeypatch, capsys):
    """`run` is developer tooling: without RESOLVE_AXI_ALLOW_RUN=1 it
    refuses before doing anything else - no script runs, and a --file
    script is not even read (the refusal names the opt-in, not the
    file). Customer runtime therefore cannot reach caller Python."""
    monkeypatch.delenv(resolve_axi.RUN_OPT_IN_ENV, raising=False)
    assert cmd_run(_run_ns(script="result = {'a': 1}")) == 1
    out = capsys.readouterr().out
    assert resolve_axi.RUN_OPT_IN_ENV in out
    assert cmd_run(_run_ns(
        script="timeline.AddMarker(1, 'Blue', 'n', 'w', 1, '')\n"
               "result = {'placed': True}",
        unsafe=True)) == 1
    assert resolve_axi.RUN_OPT_IN_ENV in capsys.readouterr().out
    assert patched["timeline"].added == []
    # The gate sits before script-file reads: a missing file still
    # answers with the opt-in refusal, never a file error.
    assert cmd_run(_run_ns(file="/nonexistent/run-script.py")) == 1
    out = capsys.readouterr().out
    assert resolve_axi.RUN_OPT_IN_ENV in out
    assert "cannot read script file" not in out


def _run_ns(**over):
    base = {"project": "", "timeline": "Reel 29 - salvage",
            "script": "", "script_pos": "", "file": "", "full": False,
            "json": False, "unsafe": False,
            "acknowledge_copy_grades": False}
    base.update(over)
    return _ns(**base)


def test_run_truncation_names_escape_hatch(patched, run_opt_in, capsys):
    assert cmd_run(_run_ns(
        script="result = [{\"note\": \"x\" * 600}]")) == 0
    out = capsys.readouterr().out
    assert "(truncated, 600 chars total - use --full)" in out
    assert cmd_run(_run_ns(
        script="result = [{\"note\": \"x\" * 600}]", full=True)) == 0
    out = capsys.readouterr().out
    assert "(truncated," not in out
    assert "x" * 100 in out


def test_run_refuses_every_writer_without_its_flag(patched, run_opt_in,
                                                  capsys):
    """`run` refuses a mutator without --unsafe: the classic writers and
    the 21.1 Perform/Transcribe/Auto/Assign/Smart/Detect/Generate/Analyze
    calls alike. `--unsafe` declares a write, not CopyGrades: that one
    replaces the target's whole grade, returns True and versions
    nothing, so it also needs --acknowledge-copy-grades."""
    rows = [
        ("timeline.AddMarker(20, 'Blue', 'n', 'w', 1, '')\n"
         "result = {'placed': True}", False, ["AddMarker", "--unsafe"]),
        ("x = item.PerformMulticamSmartSwitch\nresult = {'s': True}",
         False, ["PerformMulticamSmartSwitch"]),
        ("x = pool.AutoSyncAudio\nresult = {'s': True}", False,
         ["AutoSyncAudio"]),
        ("x = item.AssignToColorGroup\nresult = {'g': True}", False,
         ["AssignToColorGroup"]),
        ("x = clip.AnalyzeForIntellisearch\nresult = {'a': True}", False,
         ["AnalyzeForIntellisearch"]),
        ("x = timeline.CopyGrades", True,
         ["CopyGrades", "--acknowledge-copy-grades"]),
    ]
    for script, unsafe, needles in rows:
        assert cmd_run(_run_ns(script=script, unsafe=unsafe)) == 1, script
        out = capsys.readouterr().out
        assert all(n in out for n in needles), (script, out)
    # Nothing was written on any refusal path.
    assert patched["timeline"].added == []


def test_run_mention_is_not_a_call(patched, run_opt_in, capsys):
    """A string that NAMES a writer is not a writer call."""
    assert cmd_run(_run_ns(
        script="result = [{'note': 'AddMarker is mentioned'}]")) == 0
    assert "AddMarker is mentioned" in capsys.readouterr().out


def test_run_unsafe_writes_under_exclusive_lease(patched, run_opt_in,
                                                  capsys):
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


def test_cli_dispatch_connects_only_after_taking_exclusive_lease(monkeypatch):
    """The first scriptapp handshake is inside the write lease.

    A command-level lease taken after `_connect()` leaves the Connect
    handshake exposed to a concurrent Resolve operation. The CLI dispatch
    must hold the exclusive lease before its handler can connect.
    """
    events = []
    active = []

    @contextlib.contextmanager
    def tracked_lease(*, exclusive):
        events.append(("lease_enter", exclusive))
        active.append(exclusive)
        try:
            yield None
        finally:
            active.pop()
            events.append(("lease_exit", exclusive))

    def connect():
        assert active and active[-1] is True
        events.append(("connect", None))

    monkeypatch.setattr(resolve_axi, "_lease", tracked_lease)
    monkeypatch.setattr(resolve_axi, "_connect", connect)

    def handler(_args):
        resolve_axi._connect()
        return 0

    assert resolve_axi._dispatch(
        handler, _ns(unsafe=True, apply=False)) == 0
    assert events[0] == ("lease_enter", True)
    assert events[1] == ("connect", None)
    assert events[-1] == ("lease_exit", True)


def test_ownership_adoption_is_a_read_lease_and_records_without_timeline_writes(
        patched, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from library.tools.plan_provenance import (
        adopted_timeline_identities,
        read_provenance,
    )
    from library.tools import project_registry

    config = SimpleNamespace(
        slug="podcast-field-test",
        resolve=SimpleNamespace(project_name="Podcast (field test)"),
        project_root=tmp_path,
    )
    monkeypatch.setattr(project_registry, "get_project",
                        lambda _project: config)

    events = []
    active = []

    @contextlib.contextmanager
    def tracked_lease(*, exclusive):
        events.append(("lease_enter", exclusive))
        active.append(exclusive)
        try:
            yield None
        finally:
            active.pop()
            events.append(("lease_exit", exclusive))

    def connect():
        assert active and active[-1] is False
        events.append(("connect", None))
        return patched["resolve"]

    monkeypatch.setattr(resolve_axi, "_lease", tracked_lease)
    monkeypatch.setattr(resolve_axi, "_connect", connect)

    result = resolve_axi._dispatch(
        cmd_ownership_adopt,
        _ns(project="podcast-field-test", who="Prajwal"))

    assert result == 0
    output = capsys.readouterr().out
    assert "ownership_adoption_before:" in output
    assert "ownership_adoption_after:" in output
    assert "resolve_timeline_writes: 0" in output
    assert "adopted_reels[1]" in output
    reel_id = str(patched["timeline"].GetUniqueId())
    entry = adopted_timeline_identities(
        str(tmp_path / "pipeline_output" / "review"))[f"id:{reel_id}"]
    assert entry["owner"] == "Ren"
    assert entry["who"] == "Prajwal"
    assert entry["name"] == "Reel 29 - salvage"
    assert "ren_timeline_snapshots" not in read_provenance(
        str(tmp_path / "pipeline_output" / "review"))
    assert events == [
        ("lease_enter", False), ("connect", None), ("lease_exit", False)]


def test_run_after_report_survives_a_mid_run_project_switch(
        patched, run_opt_in, monkeypatch, capsys):
    """A script that switches projects (setup/teardown scripts
    legitimately do) leaves the held timeline proxy stale - GetName
    reads None. The after-report must read the cursor fresh rather
    than crash with TypeError and lose the script's own result. Exercise
    the unsafe path required for LoadProject and make the fake cursor
    switch to a second scratch project, like the live run that exposed
    this defect (measured 2026-09-25)."""
    import library.tools.resolve_axi as axi

    original_project = patched["project"]
    switched_timeline = _Timeline("Scratch run target")
    switched_project = _Project("run scratch", [switched_timeline],
                                current=switched_timeline)

    class _Manager:
        def __init__(self):
            self.current = original_project

        def GetCurrentProject(self):
            return self.current

        def LoadProject(self, name):
            assert name == switched_project.GetName()
            self.current = switched_project
            return self.current

    class _Resolve:
        def __init__(self, manager):
            self.manager = manager

        def GetProjectManager(self):
            return self.manager

    manager = _Manager()
    resolve = _Resolve(manager)

    class _Stale:
        def GetName(self):
            if manager.current is not original_project:
                return None
            return patched["timeline"].GetName()

        def __getattr__(self, name):
            return getattr(patched["timeline"], name)

    monkeypatch.setattr(axi, "_connect", lambda: resolve)
    monkeypatch.setattr(axi, "_target_timeline",
                        lambda project, name: (_Stale(), True, ""))
    assert cmd_run(_run_ns(
        script="manager.LoadProject('run scratch'); "
               "result = {'switched': True}",
        unsafe=True)) == 0
    out = capsys.readouterr().out
    assert "switched" in out
    assert "Scratch run target (current)" in out
    assert "cursor_before: Reel 29 - salvage" in out
    assert "cursor_after: Scratch run target" in out
    assert "cursor_moved: yes" in out


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


def test_submit_returns_receipt_without_waiting(monkeypatch, capsys):
    from library.tools.resolved import client

    captured = {}

    def submit(kind, params, owner=""):
        captured.update(kind=kind, params=params, owner=owner)
        return {"id": "job-123", "coalesced": False}

    monkeypatch.setattr(client, "in_broker", lambda: False)
    monkeypatch.setattr(client, "submit", submit)
    monkeypatch.setattr(
        client, "result",
        lambda *args, **kwargs: pytest.fail("submit mode must not wait"))
    monkeypatch.setattr(resolve_axi.os, "getcwd", lambda: "/work/project")

    assert resolve_axi.main(["--submit", "pool", "Day 1"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "id": "job-123", "coalesced": False}
    assert captured["kind"] == "resolve_axi"
    assert captured["params"] == {
        "argv": ["pool", "Day 1"], "cwd": "/work/project"}
    assert captured["owner"]


def test_submit_refuses_without_broker_instead_of_connecting(
        monkeypatch, capsys):
    from library.tools.resolved import client

    monkeypatch.setattr(client, "in_broker", lambda: False)

    def no_broker(*args, **kwargs):
        raise ConnectionError("ren-resolved is not serving")

    monkeypatch.setattr(client, "submit", no_broker)
    monkeypatch.setattr(
        resolve_axi, "_connect",
        lambda: pytest.fail("explicit submit must not connect to Resolve"))

    assert resolve_axi.main(["--submit", "pool"]) == 1
    assert "cannot submit to ren-resolved" in capsys.readouterr().out


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


def test_run_unsafe_runs_copygrades_once_named(patched, run_opt_in, capsys):
    assert cmd_run(_run_ns(
        script="timeline.CopyGrades([])\nresult = {'copied': True}",
        unsafe=True, acknowledge_copy_grades=True)) == 0
    assert "unsafe: yes" in capsys.readouterr().out
    assert patched["timeline"].copied == []


# ── The positional rule ──────────────────────────────────────────


def test_positional_primary_args(patched, run_opt_in, notes, canned, shelf,
                                 capsys, tmp_path, monkeypatch):
    """The class, not the instances: every command taking one obvious
    primary argument - a reel name for the reads, a script for `run`,
    a pattern for `api search`, a file for `luts delete` - accepts it
    positionally. A future command built without a positional fails
    here instead of on first use."""
    notes["Reel 29 - salvage"] = [_Note("timeline_marker", 10,
                                        note="hi")]
    from library.tools import timeline_read, timeline_shadow
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    clip = {
        "track_type": "video", "track_index": 1, "name": "LC0001.MXF",
        "record_in": 0, "record_out": 50, "duration": 50,
        "source_in_frame": 0, "source_out_frame": 50,
        "source_file": "/footage/LC0001.MXF", "transform": {},
        "fusion": {"comp_count": 0, "comp_names": [],
                   "media_windows": []},
    }
    snapshot = {
        "timeline": "Reel 29 - salvage", "reported_fps": 23.976,
        "start_frame": 0, "end_frame": 99,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": [clip]},
                   {"type": "audio", "index": 1, "name": "A1",
                    "clips": []}],
        "markers": {
            "timeline": [],
            "notes": [{"source": "timeline_marker", "frame": 10,
                       "timecode": "00:00:00:10", "color": "Green",
                       "name": "", "note": "hi", "text": "hi"}],
        },
    }
    store.record(project="Podcast (field test)",
                 timeline_id="timeline-29",
                 timeline_name="Reel 29 - salvage", snapshot=snapshot,
                 source=timeline_shadow.OBSERVED, expected_head=0)
    canned.responses["search_scripting_api"] = "  def GetMarkers() ..."
    rows = [([command, "Reel 29 - salvage"], "Reel 29 - salvage")
            for command in ("markers", "items", "captions", "frames",
                            "fusion")]
    rows += [
        (["run", "--timeline", "Reel 29 - salvage",
          "result = timeline_names"], "result[2]{value}:"),
        (["api", "search", "marker"], "matches:"),
        (["luts", "delete", "cool.dctl"], "dry run"),
    ]
    for argv, needle in rows:
        assert resolve_axi.main(argv) == 0, argv
        assert needle in capsys.readouterr().out, argv


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
    # Plain text passes through untouched.
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


def test_luts_dry_runs_write_nothing(shelf, monkeypatch, capsys):
    def no_call(tool, args, fix):
        raise AssertionError("dry run must not reach the backend")
    monkeypatch.setattr(resolve_axi, "_native", no_call)
    assert cmd_luts_update(_ns(name="new.dctl", name_pos="",
                                text="__DEVICE__ float x;",
                                file="", apply=False)) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert not (shelf / "MCP" / "new.dctl").exists()
    assert cmd_luts_generate(_ns(name="", name_pos="warm.cube",
                                  size=33, transform="return (r, g, b)",
                                  transform_file="",
                                  apply=False)) == 0
    out = capsys.readouterr().out
    assert "size: 33" in out
    assert "dry run" in out


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


def test_audio_does_not_report_mute_off_the_cursor(monkeypatch, capsys):
    """Resolve answers GetIsTrackEnabled False for every track of a
    timeline that is not current; read off the cursor, that is not a
    mute and must not be reported as one."""
    reel = _Timeline("Reel 22 - a-score", tracks={
        ("audio", 1): {"name": "SpeakerOne CH1", "items": [],
                       "enabled": False}}, start=0, end=99)
    other = _Timeline("Reel 08 - top-three", tracks={}, start=0, end=9)
    project = _Project("Podcast (field test)", [reel, other],
                       current=other)
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    assert cmd_audio(_ns(project="", timeline="Reel 22 - a-score",
                          full=False)) == 0
    out = capsys.readouterr().out
    assert "audio1,SpeakerOne CH1,not-current,0" in out
    assert ",no," not in out


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
# The suite proves the discipline, not the pixels, as three tables
# with one row per verb and case: dry runs write nothing, applies
# verify by re-read, and a refusal (a bad argument, a falsy answer, a
# disagreeing re-read) claims nothing. Every row builds a fresh fake
# Resolve, so one row's trap cannot leak into the next.


def _edit_env(monkeypatch, tmp_path):
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
    project.SetCurrentTimeline = lambda target: (
        setattr(project, "_current", target) or True)
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return {"timeline": timeline, "project": project,
            "resolve": _Resolve(project), "dir": tmp_path}


@pytest.fixture()
def edit_patched(monkeypatch, tmp_path):
    return _edit_env(monkeypatch, tmp_path)


def _multicam_project():
    cam_a = _PoolClip("camA.mp4", {"Type": "Video",
                                   "File Path": "/footage/camA.mp4"})
    cam_b = _PoolClip("camB.mp4", {"Type": "Video",
                                   "File Path": "/footage/camB.mp4"})
    root = _Folder("root", clips=[cam_a, cam_b])
    project = _Project("Podcast (field test)", [], None,
                       pool=_MediaPool(root))
    return project


def _pool_env(monkeypatch, tmp_path):
    project = _multicam_project()
    resolve = _Resolve(project)
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    return {"project": project, "resolve": resolve, "dir": tmp_path}


_ENVS = {"edit": _edit_env, "pool": _pool_env}
_V1 = ("video", 1)
_TL = {"project": "", "timeline": "Reel 29 - salvage"}


def _items(env):
    return env["timeline"]._tracks[_V1]["items"]


def _item0(env):
    return _items(env)[0]


def _track_count(env):
    return len(_items(env))


def _set(target, **attrs):
    """A row's setup: arm one trap on the fake `target(env)` returns."""
    def setup(env, _monkeypatch):
        obj = target(env)
        for key, value in attrs.items():
            setattr(obj, key, value)
    return setup


def _add_pool_clip(clip_factory):
    def setup(env, _monkeypatch):
        env["project"]._pool._root._clips.append(clip_factory())
    return setup


def _foreign_cursor(env, monkeypatch):
    import library.tools.marker_feedback as feedback
    other = _Timeline("Reel 16 - other")
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (other, None))


def _place_ns(**over):
    return _ns(**{**_TL, "clip": "a.mov", "bin": "", "source_in": None,
                  "source_out": None, "media": "both", "track": None,
                  "record": None, "apply": False, **over})


def _at(**over):
    """Args addressing V1 index 0 of the reel."""
    return _ns(**{**_TL, "track": "video1", "index": 0, **over})


def _item_ns(**over):
    return _at(**{"ripple": False, "apply": False, **over})


def _speed_ns(**over):
    return _at(**{"percent": 40.0, "freeze": False, "ripple": False,
                  "apply": False, **over})


def _transition_ns(**over):
    return _ns(**{**_TL, "apply": False, "index": 1,
                  "type": "Cross Dissolve", "category": "simple",
                  "position": "start", "alignment": "center",
                  "duration": None, **over})


def _lut_ns(**over):
    return _at(**{"lut": "Film Looks/x.cube", "node": 1, "apply": True,
                  **over})


def _group_ns(**over):
    return _at(**{"group": "probe-A", "create": True, "apply": True,
                  **over})


def _isolate_ns(**over):
    return _ns(**{**_TL, "track": 1, "amount": 60, "disable": False,
                  "apply": True, **over})


def _multicam_ns(**over):
    return _ns(**{"project": "", "clip": ["camA.mp4", "camB.mp4"],
                  "bin": "", "name": "probe-mc", "sync": "audio",
                  "apply": True, **over})


def _sync_ns(**over):
    return _ns(**{"project": "", "clip": ["camA.mp4", "camB.mp4"],
                  "bin": "", "mode": "waveform", "apply": True, **over})


def _transcribe_ns(clip, **over):
    return _ns(**{"project": "", "clip": clip, "bin": "", "apply": True,
                  "wait": 0, "full": False, **over})


def _clip_ns(clip, **over):
    return _ns(**{"project": "", "clip": clip, "bin": "", "apply": True,
                  **over})


def _project_set_ns(**over):
    return _ns(**{"project": "", "key": "timelineFrameRate",
                  "value": "29.97", "apply": True, **over})


def _queue_ns(**over):
    return _ns(**{**_TL, "preset": "", "apply": True, **over})


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


def _wordless_clip():
    """One placeholder segment with no words: never a transcription."""
    return _sense_clip("quiet.mov", transcript={
        "language": "en",
        "segments": [{"start": "00:00:00:00", "end": "00:00:00:00",
                      "speaker": None, "text": "",
                      "words": [{"start": "00:00:00:00",
                                 "end": "00:00:00:00", "text": ""}]}]})


def _clip_with(name, **attrs):
    def make():
        clip = _sense_clip(name)
        for key, value in attrs.items():
            setattr(clip, key, value)
        return clip
    return make


def _run_rows(rows, monkeypatch, tmp_path, capsys):
    """Run each (label, env, setup, cmd, args, rc, needles, check) row
    on a fresh fake and report every row that disagrees, by label."""
    from library.tools.transform_write_log import write_scope

    failures = []
    for label, kind, setup, cmd, args, rc, needles, check in rows:
        with monkeypatch.context() as patch:
            row_dir = tmp_path / label
            row_dir.mkdir()
            env = _ENVS[kind](patch, row_dir)
            if setup:
                setup(env, patch)
            with write_scope(project_folder=str(row_dir)):
                got = cmd(args(env) if callable(args) else args)
            out = capsys.readouterr().out
            checked = check(env, out) if check else True
        missing = [n for n in needles if n not in out]
        if got != rc or missing or not checked:
            failures.append((label, got, missing, checked, out[-400:]))
    assert not failures, failures


def _untouched(env, _out):
    return _track_count(env) == 2


# A dry run plans, names what it would do, and writes nothing.
_DRY_RUNS = [
    ("place", "edit", None, cmd_edit_place, _place_ns(), 0,
     ["place_plan", "a.mov"], _untouched),
    ("delete", "edit", None, cmd_edit_delete, _item_ns(), 0,
     ["delete_plan", "LC0001.MXF"], _untouched),
    ("title", "edit",
     lambda env, _m: _item0(env)._properties.__setitem__(
         "Styled Text", "old words"),
     cmd_edit_title, _at(text="new words", apply=False), 0,
     ["old words", "new words"], None),
    ("transition-carriers", "edit", None, cmd_edit_transition,
     _transition_ns(index=None), 0,
     ["cuts: 1", "LC0001.MXF", "LC0002.MXF"], None),
    ("transition-plan", "edit", None, cmd_edit_transition,
     _transition_ns(duration=12), 0,
     ["transition_plan", "Cross Dissolve", "dry run"], None),
    ("speed", "edit", None, cmd_edit_speed, _speed_ns(), 0,
     ["speed_plan", "40.0", "dry run"],
     lambda env, _o: _item0(env)._speed_set is None),
    ("multicam-build", "pool", None, cmd_multicam_build,
     _multicam_ns(apply=False), 0,
     ["multicam_plan", "probe-mc", "dry run"], None),
    ("multicam-sync", "pool", None, cmd_multicam_sync,
     _sync_ns(apply=False), 0, ["sync_plan"], None),
    ("lut", "edit", None, cmd_color_lut,
     _lut_ns(lut="Film Looks/Rec709 Kodak 2383 D65.cube", apply=False),
     0, ["lut_plan", "Rec709"], None),
    ("isolate", "edit", None, cmd_audio_isolate,
     _isolate_ns(apply=False), 0, ["isolate_plan", "audio1"], None),
    ("ingest", "edit", None, cmd_ingest,
     lambda env: _ns(project="", paths=[str(env["dir"] / "a.mov")],
                     apply=False), 0,
     ["ingest_plan", "root"], None),
    ("project-set", "edit", None, cmd_project_set,
     _project_set_ns(apply=False), 0, ["23.976", "29.97"],
     lambda env, _o: env["project"]._settings["timelineFrameRate"]
     == "23.976"),
    ("sense-cuts", "edit", None, cmd_sense_cuts,
     _ns(**_TL, apply=False), 0, ["cuts_plan", "v1_items: 2"], None),
]


def test_every_dry_run_plans_without_writing(monkeypatch, tmp_path,
                                             capsys):
    _run_rows(_DRY_RUNS, monkeypatch, tmp_path, capsys)


def _moved(env, _out):
    items = _items(env)
    moved = items[1]
    return ([i.GetUniqueId() for i in items] == ["uid-2", "uid-LC0001.MXF"]
            and (moved.GetStart(), moved.GetEnd()) == (200, 250)
            and (moved.GetSourceStartFrame(),
                 moved.GetSourceEndFrame()) == (1000, 1049))


def _trimmed(env, _out):
    items = _items(env)
    trimmed = items[1]
    return ([i.GetUniqueId() for i in items] == ["uid-2", "uid-LC0001.MXF"]
            and (trimmed.GetSourceStartFrame(),
                 trimmed.GetSourceEndFrame()) == (1010, 1049)
            and trimmed.GetStart() == 10)


def _listed(env, _out):
    project = env["project"]
    return "Reel 29 - v2" in [
        project.GetTimelineByIndex(i + 1).GetName()
        for i in range(project.GetTimelineCount())]


def _speed_set(percentage, readback=True):
    def check(env, out):
        return (_item0(env)._speed_set == {"Percentage": percentage,
                                           "RippleTimeline": False}
                and (not readback
                     or "read back" in out or "re-reads equal" in out))
    return check


# An apply writes and then PROVES the write by re-reading Resolve.
_VERIFIED_APPLIES = [
    ("place", "edit", None, cmd_edit_place, _place_ns(apply=True), 0,
     ["items_delta: 1", "verified"],
     lambda env, _o: _track_count(env) == 3),
    # The measured trap: a falsy answer with a moved count LANDED.
    # Retrying would place it twice, so it reports success, no retry.
    ("place-falsy-landed", "edit",
     _set(lambda env: env["project"]._pool, _append_empty=True),
     cmd_edit_place, _place_ns(apply=True), 0,
     ["items_delta: 1", "no retry"],
     lambda env, _o: _track_count(env) == 3),
    ("delete", "edit", None, cmd_edit_delete, _item_ns(apply=True), 0,
     ["unique id absent"],
     lambda env, _o: _track_count(env) == 1
     and env["timeline"].deleted == [(["uid-1"], False)]),
    ("delete-flaky-first-attempt", "edit",
     _set(lambda env: env["timeline"], _delete_fail_once=True),
     cmd_edit_delete, _item_ns(apply=True), 0, ["unique id absent"],
     lambda env, _o: _track_count(env) == 1),
    ("delete-ripple-explicit", "edit", None, cmd_edit_delete,
     _item_ns(apply=True, ripple=True), 0, [],
     lambda env, _o: env["timeline"].deleted == [(["uid-1"], True)]),
    ("move", "edit", None, cmd_edit_move,
     _item_ns(to=200, track_to="", apply=True), 0, ["verified"], _moved),
    ("trim", "edit", None, cmd_edit_trim,
     _at(source_in=1010, source_out=1049, apply=True), 0, ["verified"],
     _trimmed),
    ("title", "edit", None, cmd_edit_title,
     _at(text="new words", apply=True), 0,
     ["read back equal", "Styled Text"], None),
    ("transition", "edit", None, cmd_edit_transition,
     _transition_ns(apply=True, duration=12), 0,
     ["verified", "Cross Dissolve"], None),
    ("render-queue", "edit", None, cmd_render_queue,
     _queue_ns(preset="H.265 Master"), 0,
     ["verified", "tmp/out/out-1.mov"], None),
    ("render-stop", "edit",
     _set(lambda env: env["project"], _rendering=True),
     cmd_render_stop, _ns(project="", apply=True), 0,
     ["not rendering"], None),
    ("project-set", "edit", None, cmd_project_set, _project_set_ns(), 0,
     ["read back equal"],
     lambda env, _o: env["project"]._settings["timelineFrameRate"]
     == "29.97"),
    ("duplicate", "edit", None, cmd_timeline_duplicate,
     _ns(**_TL, name="Reel 29 - v2", apply=True), 0,
     ["listed on re-read"], _listed),
    ("speed", "edit", None, cmd_edit_speed, _speed_ns(apply=True), 0,
     [], _speed_set(40.0)),
    ("speed-freeze-spells-zero", "edit", None, cmd_edit_speed,
     _speed_ns(percent=None, freeze=True, apply=True), 0, ["0.0"],
     _speed_set(0.0, readback=False)),
    ("multicam-build", "pool", None, cmd_multicam_build, _multicam_ns(),
     0, ["listed on re-read", "probe-mc"], None),
    ("lut", "edit", None, cmd_color_lut,
     _lut_ns(lut="Film Looks/Rec709 Kodak 2383 D65.cube"), 0,
     ["re-reads the LUT"], None),
    ("color-group-create", "edit", None, cmd_color_group, _group_ns(), 0,
     ["re-reads the name"], None),
    ("isolate", "edit", None, cmd_audio_isolate, _isolate_ns(), 0,
     ["re-reads equal"], None),
    ("ingest", "edit",
     lambda env, _m: (env["dir"] / "c.mov").write_text(
         "footage", encoding="utf-8"),
     cmd_ingest,
     lambda env: _ns(project="", paths=[str(env["dir"] / "c.mov")],
                     apply=True), 0,
     ["verified", "c.mov"], None),
    # The read path returns only voiced segments: words with timing
    # plus speaker labels, verbatim.
    ("transcribe-read", "pool", _add_pool_clip(_voiced_clip),
     cmd_sense_transcribe, _transcribe_ns("talk.mov", apply=False,
                                          wait=None), 0,
     ["segments: 1", "Speaker 1", "words: 2"], None),
    ("transcribe-apply", "pool", _add_pool_clip(_voiced_clip),
     cmd_sense_transcribe, _transcribe_ns("talk.mov", wait=30), 0,
     ["segments: 1"], None),
    ("cuts", "edit", None, cmd_sense_cuts, _ns(**_TL, apply=True), 0,
     ["items_before: 2", "items_after: 3", "cuts: 1"], None),
    # True that splits nothing is zero cuts (one continuous take), not
    # a refusal and not cuts.
    ("cuts-zero", "edit",
     _set(lambda env: env["timeline"], _cuts_nothing=True),
     cmd_sense_cuts, _ns(**_TL, apply=True), 0, ["0 new cuts"], None),
    ("classify-read", "pool",
     _add_pool_clip(lambda: _sense_clip(
         "mix.mov", metadata={"Category": "Dialogue",
                              "Subcategory": "Dialogue"})),
     cmd_sense_classify, _clip_ns("mix.mov", apply=False), 0,
     ["Dialogue"], None),
    ("classify-apply", "pool",
     _add_pool_clip(lambda: _sense_clip("raw.mov")),
     cmd_sense_classify, _clip_ns("raw.mov"), 0,
     ["read back after True"], None),
    ("reframe-moved", "edit", _set(_item0, _reframe_moves=True),
     cmd_sense_reframe, _at(apply=True), 0, ["transform moved"], None),
    ("mask-gained-node", "edit", _set(_item0, _mask_ok=True),
     cmd_sense_mask, _at(mode="BI", apply=True), 0, ["node gained"],
     None),
    ("switch", "edit", None, cmd_sense_switch,
     _at(min_edit=1.0, apply=True), 0, ["count re-read"], None),
]


def test_every_apply_verifies_by_reread(monkeypatch, tmp_path, capsys):
    _run_rows(_VERIFIED_APPLIES, monkeypatch, tmp_path, capsys)


def _raise_on_append(env, monkeypatch):
    def raising(payloads):
        raise RuntimeError("nope")
    monkeypatch.setattr(env["project"]._pool, "AppendToTimeline", raising)


def _ambiguous_a(env, _monkeypatch):
    env["project"]._pool._root._subs[0]._clips.append(
        _PoolClip("a.mov", {"Type": "Video", "File Path": "/x/a.mov"}))


def _no_sync_enum(env, _monkeypatch):
    del env["resolve"].MULTICAM_ANGLE_SYNC_AUDIO


def _silent_settings(env, _monkeypatch):
    env["project"].SetSettings = lambda settings: True


# A refusal - a bad argument, a falsy answer, a re-read that
# disagrees - names its cause and claims nothing. Where the original
# write could have landed, the row also proves nothing was written.
_REFUSALS = [
    ("place-unknown-clip", "edit", None, cmd_edit_place,
     _place_ns(clip="nope.mov"), 1, ["no clip named 'nope.mov'"],
     _untouched),
    ("place-ambiguous-name", "edit", _ambiguous_a, cmd_edit_place,
     _place_ns(clip="a.mov"), 1, ["names 2 clips", "--bin"], None),
    ("place-foreign-cursor", "edit", _foreign_cursor, cmd_edit_place,
     _place_ns(apply=True), 1, ["cursor sits on"], _untouched),
    ("delete-persistent-failure", "edit",
     _set(lambda env: env["timeline"], _delete_fail_always=True),
     cmd_edit_delete, _item_ns(apply=True), 1,
     ["local-delete EditPatch did not verify"], _untouched),
    ("delete-bad-track", "edit", None, cmd_edit_delete,
     _item_ns(track="video9", apply=True), 1, [], _untouched),
    ("delete-bad-index", "edit", None, cmd_edit_delete,
     _item_ns(index=9, apply=True), 1, ["holds 2"], _untouched),
    ("move-place-failure-keeps-original", "edit", _raise_on_append,
     cmd_edit_move, _item_ns(to=200, track_to="", apply=True), 1,
     ["untouched"], _untouched),
    ("trim-past-handles", "edit", None, cmd_edit_trim,
     _at(source_in=999, source_out=1049, apply=True), 1, ["handles"],
     _untouched),
    ("title-no-key-takes", "edit", _set(_item0, _stubborn=True),
     cmd_edit_title, _at(text="new words", apply=True), 1, ["Fusion"],
     None),
    ("transition-needs-explicit-cut", "edit", None, cmd_edit_transition,
     _transition_ns(apply=True, index=None), 1, ["--index"], None),
    # AddTransition answers None for an unknown type/category: refuse
    # naming both rather than claim a dissolve.
    ("transition-empty-answer", "edit",
     _set(lambda env: _items(env)[1], _transition_none=True),
     cmd_edit_transition,
     _transition_ns(apply=True, type="Nope Wipe", category="fusion"), 1,
     ["Nope Wipe", "fusion"], None),
    ("transition-unreadable-span", "edit",
     _set(lambda env: _items(env)[1], _transition_broken=True),
     cmd_edit_transition, _transition_ns(apply=True), 1,
     ["would not read its span"], None),
    ("ingest-missing-file", "edit", None, cmd_ingest,
     _ns(project="", paths=["/nope/m.mov"], apply=True), 1,
     ["not on disk"], None),
    ("render-queue-foreign-cursor", "edit", _foreign_cursor,
     cmd_render_queue, _queue_ns(), 1, ["cursor sits on"], None),
    ("render-queue-unknown-preset", "edit", None, cmd_render_queue,
     _queue_ns(preset="Nope"), 1, ["no render preset"], None),
    ("render-start-empty-queue", "edit", None, cmd_render_start,
     _ns(project="", job=[], all=False, apply=True), 1, ["empty"], None),
    ("project-set-silent-write", "edit", _silent_settings,
     cmd_project_set, _project_set_ns(), 1, ["refusing to claim it"],
     None),
    ("duplicate-taken-name", "edit", None, cmd_timeline_duplicate,
     _ns(**_TL, name="Reel 29 - salvage", apply=True), 1,
     ["already exists"], None),
    ("speed-zero-without-freeze", "edit", None, cmd_edit_speed,
     _speed_ns(percent=0.0, apply=True), 1, ["--freeze"], None),
    # SetSpeed answers False while writing nothing.
    ("speed-false-answer", "edit", _set(_item0, _speed_refuse=True),
     cmd_edit_speed, _speed_ns(apply=True), 1, ["answered False"], None),
    # True with a different Percentage on re-read names both numbers.
    ("speed-disagreeing-reread", "edit",
     _set(_item0, _speed_echo={"Percentage": 100.0}),
     cmd_edit_speed, _speed_ns(apply=True), 1,
     ["re-reads 100.0 for 40.0", "refusing to claim it"], None),
    # CreateMulticamClip answers [] while listing nothing.
    ("multicam-nothing-lists", "pool",
     _set(lambda env: env["project"]._pool, _multicam_none=True),
     cmd_multicam_build, _multicam_ns(), 1,
     ["not in the pool on re-read"], None),
    # A guessed sync mode is worse than a refused one.
    ("multicam-missing-sync-enum", "pool", _no_sync_enum,
     cmd_multicam_build, _multicam_ns(), 1,
     ["MULTICAM_ANGLE_SYNC_AUDIO"], None),
    # AutoSyncAudio returns a bare bool with nothing to re-read.
    ("multicam-sync-no-readback", "pool", None, cmd_multicam_sync,
     _sync_ns(), 1, ["no measured property to re-read", "hands probe"],
     None),
    ("lut-silent-set", "edit",
     _set(lambda env: _item0(env).GetNodeGraph(), _set_refuse=True),
     cmd_color_lut, _lut_ns(), 1, ["answered False"], None),
    ("lut-disagreeing-reread", "edit",
     _set(lambda env: _item0(env).GetNodeGraph(), _lut_echo="other.cube"),
     cmd_color_lut, _lut_ns(), 1, ["refusing to claim it", "other.cube"],
     None),
    ("lut-no-graph", "edit", _set(_item0, _no_graph=True), cmd_color_lut,
     _lut_ns(), 1, ["no node graph"], None),
    ("group-unknown", "edit", None, cmd_color_group,
     _group_ns(group="nope", create=False), 1, ["--create"], None),
    # AssignToColorGroup answers True while grouping nothing.
    ("group-silent-assign", "edit", _set(_item0, _group_echo=None),
     cmd_color_group, _group_ns(), 1, ["refusing to claim it"], None),
    ("isolate-false-answer", "edit",
     _set(lambda env: env["timeline"], _voice_refuse=True),
     cmd_audio_isolate, _isolate_ns(), 1, ["answered False"], None),
    ("isolate-disagreeing-reread", "edit",
     _set(lambda env: env["timeline"],
          _voice_echo={"isEnabled": False, "amount": 0}),
     cmd_audio_isolate, _isolate_ns(), 1, ["refusing to claim it"], None),
    ("isolate-missing-track", "edit", None, cmd_audio_isolate,
     _isolate_ns(track=9), 1, ["names nothing"], None),
    ("transcribe-wordless", "pool", _add_pool_clip(_wordless_clip),
     cmd_sense_transcribe,
     _transcribe_ns("quiet.mov", apply=False, wait=None), 1,
     ["no words"], None),
    # True then nothing within the wait names the wait.
    ("transcribe-empty-wait", "pool",
     _add_pool_clip(lambda: _sense_clip("slow.mov")),
     cmd_sense_transcribe, _transcribe_ns("slow.mov"), 1,
     ["holds no words after 0s"], None),
    ("transcribe-false-start", "pool",
     _add_pool_clip(_clip_with("stuck.mov", _transcribe_ok=False)),
     cmd_sense_transcribe, _transcribe_ns("stuck.mov"), 1,
     ["answered False"], None),
    ("cuts-false-answer", "edit",
     _set(lambda env: env["timeline"], _cuts_refuse=True),
     cmd_sense_cuts, _ns(**_TL, apply=True), 1, ["answered False"], None),
    ("classify-read-without-classes", "pool",
     _add_pool_clip(lambda: _sense_clip("raw.mov")),
     cmd_sense_classify, _clip_ns("raw.mov", apply=False), 1,
     ["--apply"], None),
    ("classify-true-files-nothing", "pool",
     _add_pool_clip(_clip_with("hollow.mov", _classify_files=False)),
     cmd_sense_classify, _clip_ns("hollow.mov"), 1,
     ["refusing to claim it"], None),
    # The measured shape: Resolve refuses the call (the AI package is
    # not installed) and the verb reports those words.
    ("intellisearch-resolve-refusal", "pool",
     _add_pool_clip(_clip_with(
         "face.mov", _intelli_raise=("Required package 'AI Intellisearch"
                                     " - Faster' is not installed."))),
     cmd_sense_intellisearch,
     _clip_ns("face.mov", faces=True, better=False), 1,
     ["AI Intellisearch - Faster"], None),
    ("intellisearch-unverified-true", "pool",
     _add_pool_clip(lambda: _sense_clip("face.mov")),
     cmd_sense_intellisearch,
     _clip_ns("face.mov", faces=False, better=False), 1,
     ["unverified"], None),
    # Twice measured: True with Pan/Tilt/ZoomX unchanged.
    ("reframe-unchanged-reread", "edit", None, cmd_sense_reframe,
     _at(apply=True), 1, ["read back unchanged"], None),
    ("mask-false-answer", "edit", None, cmd_sense_mask,
     _at(mode="F", apply=True), 1, ["answered False"], None),
    ("mask-bad-mode", "edit", None, cmd_sense_mask,
     _at(mode="sideways", apply=True), 1, ["bad --mode"], None),
    ("switch-false-answer", "edit", _set(_item0, _switch_refuse=True),
     cmd_sense_switch, _at(min_edit=1.0, apply=True), 1,
     ["answered False"], None),
]


def test_every_refusal_names_its_cause_and_claims_nothing(
        monkeypatch, tmp_path, capsys):
    _run_rows(_REFUSALS, monkeypatch, tmp_path, capsys)


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


def test_render_start_apply_waits_for_completion(edit_patched, monkeypatch,
                                               capsys):
    assert cmd_render_queue(_queue_ns()) == 0
    capsys.readouterr()
    project = edit_patched["project"]
    reads = iter([True, False])

    def render_state():
        project._rendering = next(reads)
        return project._rendering

    monkeypatch.setattr(project, "IsRenderingInProgress", render_state)
    monkeypatch.setattr(resolve_axi, "RENDER_POLL_SECONDS", 0)
    assert cmd_render_start(_ns(project="", job=[], all=True,
                                 apply=True)) == 0
    output = capsys.readouterr().out
    assert "completed:" in output
    assert "idle on completion read-back" in output


# ── Finding 6: spans off Resolve proxies ─────────────────────────────

class _ProxyItem(_Item):
    """A Resolve scripting proxy (finding 6, measured 2026-09-24).

    The instance shadows `__getattribute__` with None, so the
    explicit `item.__getattribute__('GetName')` spelling answers
    None - calling it raises `TypeError: 'NoneType' object is not
    callable` - while `getattr` serves the bound method."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__["__getattribute__"] = None


def test_item_span_reads_every_field_off_a_proxy():
    """Finding 6: `_item_span` through `getattr` reads name, spans
    and uid off a Resolve proxy. The old `__getattribute__`
    spelling read every field blank, so `edit trim` and `edit move`
    refused ("would not report its spans") and every verb printed
    `name: ''`."""
    item = _ProxyItem("clip_017", 100, 172, uid="uid-clip_017")
    span = _item_span(item)
    assert span["name"] == "clip_017"
    assert span["record_in"] == 100
    assert span["record_out"] == 172
    assert span["uid"] == "uid-clip_017"
    assert span["source_in"] != "" and span["source_out"] != ""


def test_delete_dry_run_names_the_clip_it_will_touch(edit_patched,
                                                     capsys):
    """Finding 6, second half: `edit delete` by index names the clip
    in its dry run, so removing index 2 with transitions interleaved
    in the listing says which clip `--apply` will touch."""
    items = edit_patched["timeline"]._tracks[("video", 1)]["items"]
    items[0].__dict__["__getattribute__"] = None
    assert cmd_edit_delete(_ns(
        project="", timeline="Reel 29 - salvage", track="video1",
        index=0, ripple=False, apply=False)) == 0
    out = capsys.readouterr().out
    assert items[0].GetName() in out
