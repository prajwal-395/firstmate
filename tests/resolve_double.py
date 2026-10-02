"""One canonical Resolve test double for the offline suite.

Every offline Resolve-dependent test uses THESE classes. Do not define
another ``FakeTimeline`` / ``FakeProject`` / ``FakeItem`` / ``MagicMock``
Resolve in a test file - extend this module instead, so a newly measured
Resolve rule lands once and every test lives in the corrected world.

The semantics below are MEASURED against real Resolve (Studio 21.1.0.14),
not assumed. Each rule names the field evidence:

- the cursor: listing/reading never moves it; only ``SetCurrentTimeline``
  does (``resolve_axi._target_timeline`` goes through ``GetTimelineByIndex``).
- ``GetIsTrackEnabled`` answers False for every track of a timeline that
  is not current (Podcast field test, 2026-10-01; ``resolve_axi.cmd_audio``).
- ``TimelineItem.SetClipEnabled`` writes only on the current timeline:
  off it Resolve returns False and the clip keeps its state (measured on
  Reel 09's staging, 2026-10-02; ``reel_disabled_clip_carry``).
- stored Pan/Tilt are in the project resolution's unit: changing the
  project timeline resolution rescales every stored value while the
  picture stays put (``drift_check``, 2026-10-02: x4 across the epoch).
- ``SetCurrentTimecode`` returns True even past the end, and the playhead
  reads back there (``marker_capture`` live test). NOTE: the shared
  contract for this FAILED against live Resolve 21.1 on 2026-10-02 (an
  unplanned qualification run); which half is wrong is not yet measured.
- ``AddMarker`` accepts past-the-end frames too - the bounds check is the
  CALLER's (``place_reply_marker`` live test).
- ``DeleteMarkerAtFrame`` / item ``remove`` returns False for "nothing
  there", the same falsy outcome as a delete that found nothing - the
  verdict is the read-back, never the return
  (``marker_feedback`` live test).
- a pool marker present at placement is copied onto the new item; one
  added AFTER placement does not reach existing items
  (``marker_feedback`` live tests).
- ``GetProperty()`` with no argument is the only reading that says what
  the item carries; an unknown single-key read is falsy - NOT ``""``
  (live qualification 2026-10-02), answered here as None
  (``timeline_ingest._item_transform``).
- audio rows are named for their STREAM (``Akshita CH1``), never for the
  transcript speaker (``Akshita``) - joining speech to picture goes by
  track index, not row name (mic-bleed fix, 2026-10-02).

What this double does NOT model is said at ``UNSUPPORTED``: reaching for
it raises ``ResolveDoubleError`` rather than answering ``MagicMock``
silence, so a test that needs new surface extends the double instead of
passing against a shrug.
"""

from __future__ import annotations

import json
from pathlib import Path


class ResolveDoubleError(AttributeError):
    """The test reached Resolve surface the canonical double has no model of.

    An `AttributeError` on purpose: production probes tolerated surface
    with `getattr(obj, name, default)`, which only swallows this type -
    so a tolerated probe defaults exactly as on real Resolve, while a
    direct call still fails LOUD, naming the missing surface, instead
    of answering `MagicMock` silence.
    """


#: Surface the double deliberately does not model. A test needing one of
#: these extends this module (with a measured behaviour note) instead of
#: growing a private fake that answers differently.
UNSUPPORTED = frozenset(
    {
        "rendering files (StartRendering queues and records, writes nothing"
        " unless a test hands the project a `render_engine`)",
        "gallery stills (GrabStill/GetStills)",
        "voice isolation (GetVoiceIsolationState/SetVoiceIsolationState)",
        "multicam (CreateMulticamClip)",
        "project databases and folders on disk (LoadProject/SaveProject)",
    }
)

#: The Inspector Transform properties Resolve exposes on a video
#: TimelineItem, read off `GetProperty()` on Resolve 21.0.0b.28. NOT here:
#: `PanX`/`PanY` - position is `Pan` and `Tilt`. Resolve does not raise on
#: another name, it DECLINES (False), which is what let `_apply_conform`
#: write `PanX` for the life of the feature under a `MagicMock`
#: (`test_framing_parameter.py`).
VIDEO_ITEM_PROPERTIES = frozenset(
    {
        "AnchorPointX", "AnchorPointY", "CompositeMode", "CropBottom",
        "CropLeft", "CropRetain", "CropRight", "CropSoftness", "CropTop",
        "Distortion", "DynamicZoomEase", "FlipX", "FlipY", "MotionEstimation",
        "Opacity", "Pan", "Pitch", "ResizeFilter", "RetimeProcess",
        "RotationAngle", "Scaling", "Tilt", "Yaw", "ZoomGang", "ZoomX", "ZoomY",
    }
)

#: The metadata keys `MediaPoolItem.SetMetadata` accepts, measured on
#: 21.0.0b.28; any other key returns False and stores nothing
#: (`execution/organise_media_pool`).
METADATA_KEYS = frozenset(
    {
        "Comments", "Keywords", "Description", "Scene", "Shot", "Take",
        "Angle", "Reel Number", "Move", "Day / Night", "Camera #",
        "Production Name", "Episode Name", "Shot Type", "Environment",
        "Genre", "People", "Location",
    }
)

#: Measured on Resolve Studio 21.1 (composed-edit spike): this pair
#: returns False from `SetProperty` AND sets the value - judge by read-back.
FALSE_BUT_SET_PROPERTIES = frozenset({"AnchorPointX", "AnchorPointY"})

_DEFAULT_SETTINGS = {
    "timelineResolutionWidth": "1080",
    "timelineResolutionHeight": "1920",
    "timelineFrameRate": "30",
}


class FakeResolve:
    """``DaVinciResolveScript.scriptapp("Resolve")`` stood in."""

    EXPORT_OTIO = 15

    def __init__(self, project=None) -> None:
        self._manager = FakeProjectManager(project)

    def GetProjectManager(self):
        return self._manager

    def GetProductName(self):
        return "DaVinci Resolve (canonical test double)"

    def GetFairlightPresets(self):
        """No presets: the captain's limiter preset is not installed."""
        return {}

    def GetCurrentPage(self):
        return getattr(self, "opened_pages", ["edit"])[-1]

    def OpenPage(self, page):
        self.opened_pages = getattr(self, "opened_pages", []) + [page]
        return True


class FakeProjectManager:
    def __init__(self, project=None, project_names=None) -> None:
        self._project = project
        self._project_names = list(project_names or ())

    def GetCurrentProject(self):
        return self._project

    def SetCurrentProject(self, project):
        self._project = project
        return True

    def GetProjectListInCurrentFolder(self):
        if self._project_names:
            return list(self._project_names)
        return [self._project.GetName()] if self._project else []


def make_project(
    name="Fixture Project",
    width=1080,
    height=1920,
    frame_rate=30,
    timelines=(),
    current=None,
    moves=(),
):
    """A project with a media pool, no timelines, and nothing current."""
    project = FakeProject(name, timelines=timelines, current=current, moves=moves)
    project._settings = {
        "timelineResolutionWidth": str(width),
        "timelineResolutionHeight": str(height),
        "timelineFrameRate": str(frame_rate),
    }
    return project


class FakeProject:
    def __init__(
        self,
        name="Fixture Project",
        timelines=(),
        current=None,
        moves=(),
        delete_ok=True,
    ) -> None:
        if isinstance(name, (list, tuple)):
            if timelines:
                raise TypeError("timeline list was supplied twice")
            timelines, name = name, "Fixture Project"
        self._name = name
        self._settings = dict(_DEFAULT_SETTINGS)
        self._timelines: list = []
        self._current = None
        self.moves = list(moves)
        self.set_calls: list = []
        self.deleted: list = []
        self.delete_ok = delete_ok
        self._pool = FakeMediaPool(self)
        self.render_settings: dict = {}
        self.render_format_codec = {"format": "mov", "codec": "H.264"}
        self.render_jobs: list = []
        #: Optional ``engine(job, timeline) -> bool``: renders one queued
        #: job's file. Without it ``StartRendering`` records and renders
        #: nothing, and asking for a job's status raises (`UNSUPPORTED`).
        self.render_engine = None
        self.render_status: dict = {}
        self.started_renders: list = []
        self.deleted_render_jobs: list = []
        #: Fault knob: the 2026-09-11 shape - both `SetRenderSettings` and
        #: `AddRenderJob` succeed and the job queues the WHOLE timeline.
        #: True for every job, or a collection of MarkIn frames it hits.
        self.ignore_render_marks = False
        for timeline in timelines:
            # A bare name stands for an empty timeline of that name.
            if isinstance(timeline, str):
                timeline = FakeTimeline(timeline)
            self.adopt(timeline)
        if current is not None:
            self._current = current
            if current not in self._timelines:
                self.adopt(current)

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return f"project:{self._name}"

    # ── settings: the Pan/Tilt unit epoch ──
    def GetSetting(self, key):
        return self._settings.get(key, "")

    def SetSetting(self, key, value):
        return self.SetSettings({key: value})

    def SetSettings(self, values):
        """Write settings; a resolution change rescales stored Pan/Tilt.

        Measured (drift_check, 2026-10-02): Resolve rescales every stored
        Pan/Tilt value when the project timeline resolution changes while
        the picture stays put. Pan scales with width, Tilt with height.
        """
        try:
            old_w = float(self._settings.get("timelineResolutionWidth", 0))
            old_h = float(self._settings.get("timelineResolutionHeight", 0))
            new_w = float(values.get("timelineResolutionWidth", old_w))
            new_h = float(values.get("timelineResolutionHeight", old_h))
        except (TypeError, ValueError):
            old_w = old_h = new_w = new_h = 0
        self._settings.update({k: str(v) for k, v in values.items()})
        if old_w > 0 and old_h > 0 and (new_w != old_w or new_h != old_h):
            for timeline in self._timelines:
                for _name, items in (
                    timeline._tracks["video"] + timeline._tracks["audio"]
                ):
                    for item in items:
                        props = item._props
                        if "Pan" in props:
                            props["Pan"] = props["Pan"] * (new_w / old_w)
                        if "Tilt" in props:
                            props["Tilt"] = props["Tilt"] * (new_h / old_h)
        return True

    # ── timelines and the cursor ──
    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        if self.moves:
            self._current = self.moves.pop(0)
        return self._current

    def SetCurrentTimeline(self, timeline):
        self.set_calls.append(timeline.GetName() if timeline else None)
        self._current = timeline
        return True

    # ── the render queue ──
    def GetCurrentRenderFormatAndCodec(self):
        return dict(self.render_format_codec)

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.render_format_codec = {"format": fmt, "codec": codec}
        return True

    def SetRenderSettings(self, settings):
        self.render_settings.update(settings)
        return True

    def AddRenderJob(self):
        """Queue the current timeline under the current settings.

        WITHOUT ``SelectAllFrames: False`` Resolve IGNORES MarkIn/MarkOut
        and queues the whole timeline, while every call succeeds
        (measured 2026-09-11, `segment_renderer`).
        """
        timeline = self.GetCurrentTimeline()
        if timeline is None:
            return ""
        settings = self.render_settings
        ignored = self.ignore_render_marks
        if not isinstance(ignored, bool):
            ignored = settings.get("MarkIn") in ignored
        whole = ignored or settings.get("SelectAllFrames") is not False
        job = {
            "JobId": f"job-{len(self.render_jobs) + 1}",
            "TimelineName": timeline.GetName(),
            "MarkIn": timeline.GetStartFrame() if whole else settings.get("MarkIn"),
            "MarkOut": timeline.GetEndFrame() if whole else settings.get("MarkOut"),
            "TargetDir": settings.get("TargetDir", ""),
            "OutputFilename": settings.get("CustomName", ""),
        }
        self.render_jobs.append(job)
        return job["JobId"]

    def GetRenderJobList(self):
        return [dict(job) for job in self.render_jobs]

    def DeleteRenderJob(self, job_id):
        before = len(self.render_jobs)
        self.render_jobs = [j for j in self.render_jobs if j["JobId"] != job_id]
        self.deleted_render_jobs.append(job_id)
        return len(self.render_jobs) < before

    def StartRendering(self, jobs, isInteractiveMode=False):
        """Records the start; renders only through ``render_engine``."""
        self.started_renders.append(list(jobs))
        if self.render_engine is not None:
            for job in self.render_jobs:
                if job["JobId"] not in jobs:
                    continue
                timeline = next((t for t in self._timelines
                                 if t.GetName() == job["TimelineName"]), None)
                done = timeline is not None and self.render_engine(
                    dict(job), timeline)
                self.render_status[job["JobId"]] = {
                    "JobStatus": "Complete" if done else "Failed",
                    "CompletionPercentage": 100 if done else 0,
                }
        return True

    def GetRenderJobStatus(self, job_id):
        if self.render_engine is None:
            raise ResolveDoubleError(
                "GetRenderJobStatus needs a render_engine - the double "
                "renders no files by default (UNSUPPORTED)")
        return dict(self.render_status.get(job_id) or {})

    def StopRendering(self):
        return None

    def IsRenderingInProgress(self):
        return False

    def ApplyFairlightPresetToCurrentTimeline(self, preset):
        return False

    def GetMediaPool(self):
        return self._pool

    @property
    def timelines(self):
        """Live list of timelines, for membership assertions in tests."""
        return self._timelines

    def names(self):
        return [timeline.GetName() for timeline in self._timelines]

    def adopt(self, timeline):
        """Take ownership of a timeline built off-project.

        Test-side only: production always creates through the pool
        (`CreateEmptyTimeline`). Adopting wires the cursor identity the
        double's write rules judge by.
        """
        timeline._project = self
        self._timelines.append(timeline)
        return timeline


class FakeMediaPool:
    def __init__(self, project) -> None:
        self._project = project
        self._root = FakeFolder("Master")  # Resolve's own root bin name
        self._current_folder = self._root
        self.audio_channels = (1,)
        self.media_properties: dict = {}
        #: Optional ``probe(path) -> {property: value}``: what Resolve
        #: reads off a file it imports (Resolution, FPS, Frames). Real
        #: Resolve reads the file itself; a test that renders real media
        #: hands the double a reader of it. ``media_properties`` wins.
        self.probe = None
        self.import_failures: set = set()
        self.next_timeline = None
        #: One entry per `AppendToTimeline` call: how many specs it held.
        self.append_calls: list = []
        self.move_calls: list = []
        self.move_ok = True
        self.delete_folder_calls: list = []
        self.delete_folders_ok = True

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._current_folder

    def SetCurrentFolder(self, folder):
        self._current_folder = folder
        return True

    def AddSubFolder(self, parent, name=None):
        """Measured on 21.0.0b.28 (`execution/organise_media_pool`): makes
        a SECOND folder when the name exists, and SETS the current folder
        to the one it made."""
        if name is None:
            parent, name = self._root, parent
        folder = FakeFolder(name)
        parent._subfolders.append(folder)
        self._current_folder = folder
        return folder

    def CreateEmptyTimeline(self, name):
        """The new timeline's pool item lands in the CURRENT folder."""
        timeline = self.next_timeline or FakeTimeline(name, self._project)
        self.next_timeline = None
        timeline.SetName(name)
        if timeline not in self._project._timelines:
            self._project.adopt(timeline)
        self._current_folder._clips.append(timeline.GetMediaPoolItem())
        return timeline

    def DeleteTimelines(self, timelines):
        if not self._project.delete_ok:
            return False
        for timeline in list(timelines):
            if timeline in self._project._timelines:
                name = timeline.GetName()
                self._project._timelines.remove(timeline)
                self._project.deleted.append(name)
                timeline._deleted = True
            if self._project._current is timeline:
                self._project._current = None
            self._unfile([timeline.GetMediaPoolItem()])
        return True

    def ImportTimelineFromFile(self, path, options=None):
        """Rebuild a timeline from an OTIO file - LOSING every Fusion comp
        and grade, which is what Resolve really does.

        Answers None, naming nothing, when the name is taken or when ANY
        referenced file is not on disk (`otio_mix`'s third format fact).
        """
        import os

        from library.tools import otio_mix

        options = options or {}
        name = options.get("timelineName", "")
        if any(t.GetName() == name for t in self._project._timelines):
            return None
        otio = json.loads(Path(path).read_text(encoding="utf-8"))
        rebuilt = FakeTimeline(name, self._project)
        for track in otio["tracks"]["children"]:
            kind = "video" if track["kind"] == "Video" else "audio"
            index = int(track["name"][1:])
            rebuilt._ensure_track(kind, index)
            position = rebuilt.GetStartFrame()
            for child in track["children"]:
                source = child["source_range"]
                duration = int(source["duration"]["value"])
                if str(child["OTIO_SCHEMA"]).startswith("Clip"):
                    media = otio_mix.clip_media_path(child)
                    if not os.path.exists(media):
                        return None
                    pool_item = FakeMediaPoolItem(Path(media).name)
                    pool_item.SetClipProperty("File Path", media)
                    item = FakeTimelineItem(
                        pool_item.GetName(), rebuilt, start=position,
                        duration=duration,
                        left_offset=int(source["start_time"]["value"]),
                        pool_item=pool_item)
                    item._otio_volume = otio_mix._volume_parameter_of(child)
                    rebuilt._tracks[kind][index - 1][1].append(item)
                position += duration
        self._project.adopt(rebuilt)
        self._current_folder._clips.append(rebuilt.GetMediaPoolItem())
        return rebuilt

    def DeleteClips(self, clips):
        """Remove pool items; a TIMELINE's pool item deletes the timeline.

        AGENTS.md §5: `DeleteClips` on a timeline's pool item DELETES THE
        TIMELINE - the catastrophic case `orphan_removal` guards and the
        intended act in `execution/remove_proof`.
        """
        clips = list(clips)
        doomed = [
            timeline
            for timeline in self._project._timelines
            if any(timeline.GetMediaPoolItem() is clip for clip in clips)
        ]
        self._unfile(clips)
        if doomed:
            delete_ok = self._project.delete_ok
            self._project.delete_ok = True
            self.DeleteTimelines(doomed)
            self._project.delete_ok = delete_ok
        return True

    def _unfile(self, clips):
        folders = [self._root]
        while folders:
            folder = folders.pop()
            folder._clips[:] = [
                item for item in folder._clips
                if not any(item is clip for clip in clips)
            ]
            folders.extend(folder._subfolders)

    def AppendToTimeline(self, specs):
        """Place clips; pool markers present NOW copy onto the new items.

        Appends to the CURRENT timeline only - never to a timeline named
        in the spec (there is no such parameter). Measured 2026-09-04:
        579 captions aimed at fifteen reels landed on Reel 01, which
        happened to be current, while every call returned True
        (`resolve_lock.PLACEMENT_REQUIRES_CURRENT`). With nothing
        current there is no destination, and the call returns False.

        ``endFrame`` is EXCLUSIVE: the item plays ``endFrame - startFrame``
        frames. An inclusive reading left a one-frame black hole between
        abutting placements (`reel_build` caption placement, the
        composed-edit spike's two black frames).

        Placing over a live item on the destination row is REFUSED in
        the worst way (measured, composed-edit spike): the call still
        returns a truthy list holding a live-looking handle, and places
        NOTHING - only a read-back of the row tells.

        A pool marker added AFTER placement does not reach existing items
        (measured, marker_feedback live tests) - so only the markers
        present at this call are inherited.
        """
        self.append_calls.append(len(specs))
        timeline = self._project._current
        if timeline is None:
            return False
        placed = []
        for spec in specs:
            pool_item = spec["mediaPoolItem"]
            start = int(spec["startFrame"])
            end = int(spec["endFrame"])
            media_type = int(spec.get("mediaType", 1))
            track_index = int(spec.get("trackIndex", 1))
            kind = "video" if media_type == 1 else "audio"
            channels = self.audio_channels if kind == "audio" else (None,)
            audio_rows = [
                index
                for index in range(1, timeline.GetTrackCount("audio") + 1)
                if index != track_index
            ]
            for offset, channel in enumerate(channels):
                item = FakeTimelineItem(
                    pool_item.GetName(),
                    timeline,
                    # No recordFrame: APPENDED after the row's last item.
                    start=(int(spec["recordFrame"]) if "recordFrame" in spec
                           else _append_end(timeline, kind, track_index)),
                    duration=end - start,
                    left_offset=start,
                    pool_item=pool_item,
                    source_audio_channel_mapping=(
                        json.dumps(
                            {
                                "embedded_audio_channels": 4,
                                "linked_audio": {},
                                "track_mapping": {
                                    "1": {
                                        "channel_idx": [channel],
                                        "mute": False,
                                        "type": "mono",
                                    }
                                },
                            }
                        )
                        if channel is not None
                        else ""
                    ),
                )
                for key, marker in (pool_item.GetMarkers() or {}).items():
                    item._markers[int(key)] = dict(marker)
                destination = track_index
                if kind == "audio" and offset > 0 and audio_rows:
                    destination = audio_rows[(offset - 1) % len(audio_rows)]
                elif kind == "audio" and offset > 0:
                    raise ResolveDoubleError(
                        "multistream audio spill needs a second existing audio row"
                    )
                row = timeline._ensure_track(kind, destination)[1]
                if offset == 0:
                    placed.append(item)
                # The refusal is measured for the placement itself; a
                # multistream spill copy is not judged (unmeasured).
                if offset == 0 and any(
                        item.GetStart() < live.GetEnd()
                        and live.GetStart() < item.GetEnd() for live in row):
                    continue
                row.append(item)
                row.sort(key=lambda live: live.GetStart())
        return placed or True

    def ImportMedia(self, paths):
        clips = []
        for path in paths:
            if path in self.import_failures:
                continue
            clip = FakeMediaPoolItem(path.rsplit("/", 1)[-1])
            clip._props["File Path"] = path
            read = (self.media_properties.get(path)
                    or (self.probe(path) if self.probe else {}) or {})
            for key, value in read.items():
                clip.SetClipProperty(key, value)
            self._current_folder._clips.append(clip)
            clips.append(clip)
        return clips

    def MoveClips(self, clips, folder):
        clips = list(clips)
        self.move_calls.append((clips, folder))
        if not self.move_ok or folder is None:
            return False
        folders = [self._root]
        while folders:
            current = folders.pop()
            current._clips[:] = [item for item in current._clips if item not in clips]
            folders.extend(current.GetSubFolderList())
        for item in clips:
            if item not in folder._clips:
                folder._clips.append(item)
        return True

    def DeleteFolders(self, folders):
        folders = list(folders)
        self.delete_folder_calls.append(folders)
        if not self.delete_folders_ok:
            return False
        for folder in folders:
            if folder.GetClipList() or folder.GetSubFolderList():
                return False
            parent = self._parent_folder(folder)
            if parent is None:
                return False
            parent._subfolders.remove(folder)
        return True

    def _parent_folder(self, target):
        pending = [self._root]
        while pending:
            folder = pending.pop()
            for subfolder in folder.GetSubFolderList():
                if subfolder is target:
                    return folder
                pending.append(subfolder)
        return None


class FakeFolder:
    def __init__(self, name, uid=None) -> None:
        self._name = name
        self._uid = uid or f"folder:{name}:{id(self)}"
        self._clips: list = []
        self._subfolders: list = []

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subfolders)

    def add_clip(self, clip):
        """Test-side helper: file an existing pool item in this bin."""
        self._clips.append(clip)
        return clip


def make_pool_clip(
    name="clip.mov",
    frames=1000,
    resolution="3840x2160",
    clip_type="Video",
    path=None,
    uid=None,
):
    """A media-pool clip carrying the properties ingest reads."""
    clip = FakeMediaPoolItem(name, uid=uid)
    if path is not None:
        clip._props["File Path"] = path
    clip._props.update(
        {
            "File Name": name,
            "Type": clip_type,
            "Frames": str(frames),
            "Resolution": resolution,
        }
    )
    return clip


class FakeMediaPoolItem:
    def __init__(self, name="clip.mov", uid=None) -> None:
        self._name = name
        self._uid = uid
        self._props = {
            "File Name": name,
            "Type": "Video",
            "Frames": "1000",
            "Resolution": "3840x2160",
        }
        self._markers: dict = {}
        self._metadata: dict = {}
        self.replace_calls: list = []
        self.refuse_replace = False
        self.on_disk_frames = None

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        self._props["File Name"] = name
        return True

    def GetMediaId(self):
        return self._name

    def ReplaceClip(self, path):
        """Point the pool item at ``path``; every placement follows.

        ``refuse_replace`` is the fault knob (a falsy answer, nothing
        changed). ``on_disk_frames`` is what the file holds NOW: the pool
        keeps the length it read at import until a replace re-reads it.
        """
        self.replace_calls.append(path)
        if self.refuse_replace:
            return False
        self._props["File Path"] = path
        if self.on_disk_frames is not None:
            self._props["Frames"] = str(self.on_disk_frames)
        return True

    def GetUniqueId(self):
        return self._uid or f"pool:{self._name}"

    def GetClipProperty(self, key=None):
        if key is None:
            return dict(self._props)
        return self._props.get(key, "")

    def GetMetadata(self, key=None):
        if key is None:
            return dict(self._metadata)
        return self._metadata.get(key, "")

    def SetMetadata(self, key, value):
        """False and nothing stored for a key Resolve does not know
        (measured, `execution/organise_media_pool`)."""
        if key not in METADATA_KEYS:
            return False
        self._metadata[key] = value
        return True

    def SetClipColor(self, color):
        self._props["Clip Color"] = color
        return True

    def ClearClipColor(self):
        self._props["Clip Color"] = ""
        return True

    def SetClipProperty(self, key, value):
        self._props[key] = str(value)
        return True

    def GetMarkers(self):
        return {k: dict(v) for k, v in self._markers.items()}

    def AddMarker(self, frame, color, name, note, duration, custom_data=""):
        self._markers[int(frame)] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": int(duration or 1),
            "customData": custom_data or "",
        }
        return True

    def DeleteMarkerAtFrame(self, frame):
        return self._markers.pop(int(frame), None) is not None

    def DeleteMarkersByColor(self, color):
        if color == "All":
            self._markers.clear()
            return True
        self._markers = {
            k: v for k, v in self._markers.items() if v.get("color") != color
        }
        return True

    def GetMarkerCustomData(self, frame):
        marker = self._markers.get(int(frame))
        return marker["customData"] if marker else ""


class _Track:
    """One named row; audio rows carry STREAM names (see module docstring)."""

    def __init__(self, name) -> None:
        self.name = name
        self.enabled = True
        self.locked = False
        self.items: list = []


class FakeTimeline:
    def __init__(
        self,
        name="Fixture Timeline",
        project=None,
        start_frame=0,
        frame_rate=None,
        uid=None,
        video=(),
        audio=(),
        markers=None,
        end_frame=None,
        settings=None,
        rename_ok=True,
        pool_uid=None,
        timecode=None,
        stuck_playhead=False,
    ) -> None:
        self._name = name
        self.rename_ok = rename_ok
        # A timeline is also a pool item, of Type "Timeline" - the one
        # `DeleteClips` deletes the timeline through (AGENTS.md §5).
        self._media_pool_item = FakeMediaPoolItem(name, uid=pool_uid)
        self._media_pool_item._props["Type"] = "Timeline"
        self._deleted = False
        # Stable across renames, like Resolve's own id (a rename must
        # not read as a new timeline).
        self._unique_id = uid or f"timeline:{name}"
        self._project = project
        self._start_frame = start_frame
        self._frame_rate = str(frame_rate) if frame_rate is not None else None
        self._settings = dict(settings or {})
        self._tracks: dict = {"video": [], "audio": [], "subtitle": []}
        self._markers: dict = {
            int(frame): dict(marker) for frame, marker in (markers or {}).items()
        }
        self._end_frame_override = end_frame
        self._timecode = timecode
        #: Fault injection: `SetCurrentTimecode` still answers True but
        #: the playhead does not move - the read-back is the verdict.
        self.stuck_playhead = stuck_playhead
        self.timecode_calls: list = []
        self.deletes = 0
        self.marker_delete_calls: list = []
        self.refuse_marker_delete_frames: set = set()
        self.explode_marker_delete_frames: set = set()
        self.refuse_marker_update_frames: set = set()
        self.forbid_marker_add = False
        self.raise_on_methods: dict = {}
        self.link_calls: list = []
        self.deleted_items: list = []
        #: One entry per `DeleteClips` call: how many items it named.
        self.delete_calls: list = []
        self._init_rows("video", video)
        self._init_rows("audio", audio)

    # ── identity ──
    def GetName(self):
        return None if self._deleted else self._name

    def SetName(self, name):
        if not self.rename_ok:
            return False
        self._name = name
        self._media_pool_item.SetName(name)
        return True

    def GetMediaPoolItem(self):
        return self._media_pool_item

    def GetUniqueId(self):
        return self._unique_id

    def SetSetting(self, key, value):
        self._settings[key] = str(value)
        return True

    @property
    def _is_current(self):
        return self._project is not None and self._project.GetCurrentTimeline() is self

    # ── frames ──
    def GetStartFrame(self):
        return self._start_frame

    def SetStartTimecode(self, timecode):
        return True

    def GetEndFrame(self):
        if self._end_frame_override is not None:
            return self._end_frame_override
        end = self._start_frame
        for _name, items in self._tracks["video"] + self._tracks["audio"]:
            for item in items:
                end = max(end, item.GetEnd() - 1)
        return end

    def GetSetting(self, key=None):
        settings = dict(_DEFAULT_SETTINGS)
        if self._project is not None:
            settings.update(self._project._settings)
        settings.update(self._settings)
        if self._frame_rate is not None:
            settings["timelineFrameRate"] = self._frame_rate
        return settings if key is None else settings.get(key, "")

    def GetCurrentTimecode(self):
        return self._timecode

    def SetCurrentTimecode(self, timecode):
        """Always True - even past the end, where the playhead reads back
        exactly what was set (measured, marker_capture live test)."""
        self.timecode_calls.append(timecode)
        if not self.stuck_playhead:
            self._timecode = timecode
        return True

    # ── tracks ──
    def _ensure_track(self, kind, index):
        while len(self._tracks[kind]) < index:
            number = len(self._tracks[kind]) + 1
            prefix = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}[kind]
            self._tracks[kind].append([f"{prefix} {number}", []])
        return self._tracks[kind][index - 1]

    def add_track(self, kind, name):
        """Test-side helper: append a named row (e.g. "Semantic")."""
        self._tracks[kind].append([name, []])
        return len(self._tracks[kind])

    def add_item(self, kind, index, item):
        """Test-side helper: materialize and append one item to a row."""
        if hasattr(item, "_as_item"):
            item = item._as_item(self)
        if hasattr(item, "_timeline"):
            item._timeline = self
        self._ensure_track(kind, index)[1].append(item)
        return item

    def GetTrackCount(self, kind):
        error = self.raise_on_methods.get("GetTrackCount")
        if error is not None:
            raise error
        return len(self._tracks[kind])

    def _init_rows(self, kind, rows):
        if isinstance(rows, dict):
            normalized = []
            if rows and all(isinstance(key, tuple) for key in rows):
                for (row_kind, index), value in sorted(rows.items()):
                    if row_kind != kind:
                        continue
                    if isinstance(value, tuple) and len(value) == 2:
                        row_name, items = value
                    else:
                        row_name, items = f"{kind.title()} {index}", value
                    normalized.append((index, row_name, items))
            else:
                for index, value in sorted(rows.items()):
                    if isinstance(value, tuple) and len(value) == 2:
                        row_name, items = value
                    else:
                        row_name, items = f"{kind.title()} {index}", value
                    normalized.append((index, row_name, items))
        else:
            normalized = [
                (index, row[0], row[1])
                if isinstance(row, tuple) and len(row) == 2
                else (index, f"{kind.title()} {index}", row)
                for index, row in enumerate(rows or [], start=1)
            ]
        for _index, row_name, items in normalized:
            self.AddTrack(kind)
            index = self.GetTrackCount(kind)
            self.SetTrackName(kind, index, row_name)
            for item in items:
                self.add_item(kind, index, item)

    def GetTrackName(self, kind, index):
        return self._tracks[kind][index - 1][0]

    def SetTrackName(self, kind, index, name):
        self._tracks[kind][index - 1][0] = name
        return True

    def AddTrack(self, kind, name=""):
        self.add_track(kind, name or f"Track {len(self._tracks[kind]) + 1}")
        return True

    def DeleteTrack(self, kind, index):
        del self._tracks[kind][index - 1]
        return True

    def GetItemListInTrack(self, kind, index):
        return self._tracks[kind][index - 1][1]

    def GetIsTrackEnabled(self, kind, index):
        """False on every track of a non-current timeline (measured
        2026-10-01: 34 reels read muted by name, enabled when current)."""
        if not self._is_current:
            return False
        tracks = self._tracks[kind]
        if 1 <= index <= len(tracks):
            return bool(getattr(self, "_track_enabled", {}).get((kind, index), True))
        return False

    def SetTrackEnable(self, kind, index, enabled):
        if not hasattr(self, "_track_enabled"):
            self._track_enabled = {}
        self._track_enabled[(kind, index)] = bool(enabled)
        return True

    def GetIsTrackLocked(self, kind, index):
        return False

    def GetTrackSubType(self, kind, index):
        return ""

    def SetClipsLinked(self, items, linked):
        items = list(items)
        self.link_calls.append((items, linked))
        for item in items:
            for peer in item._linked_items:
                peer._linked_items = [
                    linked_item
                    for linked_item in peer._linked_items
                    if linked_item is not item
                ]
            item._linked_items = []
        if linked:
            for item in items:
                item._linked_items = [other for other in items if other is not item]
        return True

    # ── markers (timeline plane; keys are RELATIVE frame offsets) ──
    def GetMarkers(self):
        return {k: dict(v) for k, v in self._markers.items()}

    def AddMarker(self, frame, color, name, note, duration, custom_data=""):
        """Accepted at any frame - including past the end. Resolve takes
        it (measured, place_reply_marker live test); the bounds check is
        the caller's, never this return."""
        if self.forbid_marker_add:
            raise AssertionError("this test forbids adding timeline markers")
        self._markers[int(frame)] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": int(duration or 1),
            "customData": custom_data or "",
        }
        return True

    def DeleteMarkerAtFrame(self, frame):
        """False for "nothing there" - judge by re-read, not the return."""
        frame = int(frame)
        self.marker_delete_calls.append(frame)
        if frame in self.explode_marker_delete_frames:
            raise AssertionError("this test forbids deleting timeline markers")
        if frame in self.refuse_marker_delete_frames:
            return False
        return self._markers.pop(frame, None) is not None

    def DeleteMarkersByColor(self, color):
        if color == "All":
            self._markers.clear()
            return True
        self._markers = {
            k: v for k, v in self._markers.items() if v.get("color") != color
        }
        return True

    def GetMarkerCustomData(self, frame):
        marker = self._markers.get(int(frame))
        return marker["customData"] if marker else ""

    def UpdateMarkerCustomData(self, frame, custom_data):
        frame = int(frame)
        if frame in self.refuse_marker_update_frames:
            return False
        marker = self._markers.get(frame)
        if marker is None:
            return False
        marker["customData"] = custom_data
        return True

    @property
    def markers(self):
        """Test-side view of the timeline markers."""
        return self._markers

    @property
    def rows(self):
        """Test-side view: ``{"V1": [items], "A1": [...]}``, live lists."""
        return {
            f"{kind[0].upper()}{index}": row[1]
            for kind in ("video", "audio")
            for index, row in enumerate(self._tracks[kind], start=1)
        }

    # ── edit verbs used by the reel writers ──
    def DeleteClips(self, clips, ripple=False):
        self.deletes += 1
        self.delete_calls.append(len(clips))
        self.deleted_items.extend(clip.GetUniqueId() for clip in clips)
        spans = {(clip.GetStart(), clip.GetEnd()) for clip in clips}
        for _name, items in self._tracks["video"] + self._tracks["audio"]:
            for clip in [c for c in items if c in clips]:
                items.remove(clip)
        if ripple and spans:
            if len(spans) != 1:
                raise ResolveDoubleError(
                    "ripple deletion of multiple spans is not modelled"
                )
            start, end = next(iter(spans))
            for _name, items in self._tracks["video"] + self._tracks["audio"]:
                for item in items:
                    if item.GetStart() >= end:
                        item._start -= end - start
        return True

    def DuplicateTimeline(self, name):
        """A copy in the same project, every item copied with its
        transform, grade, enabled state, colour and comps; the cursor
        does not move."""
        import copy as _copy

        twin = FakeTimeline(name, self._project, start_frame=self._start_frame,
                            frame_rate=self._frame_rate,
                            settings=self._settings)
        for kind in ("video", "audio"):
            for row_name, items in self._tracks[kind]:
                index = twin.add_track(kind, row_name)
                for source in items:
                    copied = _copy.copy(source)
                    FakeTimelineItem._next_id += 1
                    copied._uid = f"item-{FakeTimelineItem._next_id}"
                    copied._timeline = twin
                    copied._props = dict(source._props)
                    copied._markers = {k: dict(v) for k, v in source._markers.items()}
                    copied._fusion_comps = _copy.deepcopy(source._fusion_comps)
                    copied._linked_items = []
                    copied.property_writes, copied.refused = [], []
                    twin._tracks[kind][index - 1][1].append(copied)
        if self._project is not None:
            self._project.adopt(twin)
        return twin

    def Export(self, path, kind):
        """Write the OTIO Resolve writes for this timeline (EXPORT_OTIO).

        One track per row, gaps between items, each clip naming its file
        and carrying the Fairlight volume effect - with an EMPTY
        parameter list while every value is at its default, the reason
        `otio_mix` inserts the parameter rather than patching it.
        """
        from library.tools import otio_mix

        rate = float(self.GetSetting("timelineFrameRate") or 30)

        def span(start, duration):
            return {
                "OTIO_SCHEMA": "TimeRange.1",
                "start_time": {"OTIO_SCHEMA": "RationalTime.1",
                               "rate": rate, "value": float(start)},
                "duration": {"OTIO_SCHEMA": "RationalTime.1",
                             "rate": rate, "value": float(duration)},
            }

        tracks = []
        for kind_name, label in (("video", "Video"), ("audio", "Audio")):
            for index, (_row, items) in enumerate(self._tracks[kind_name], 1):
                children, position = [], self._start_frame
                for item in sorted(items, key=lambda i: i.GetStart()):
                    if item.GetStart() > position:
                        children.append({
                            "OTIO_SCHEMA": "Gap.1",
                            "source_range": span(0, item.GetStart() - position)})
                    pool = item.GetMediaPoolItem()
                    media = pool.GetClipProperty("File Path") if pool else ""
                    volume = item._otio_volume
                    children.append({
                        "OTIO_SCHEMA": "Clip.2",
                        "name": Path(media).name if media else item.GetName(),
                        "active_media_reference_key": "DEFAULT_MEDIA",
                        "media_references": {"DEFAULT_MEDIA": {
                            "OTIO_SCHEMA": "ExternalReference.1",
                            "target_url": f"file://{media}"}},
                        "source_range": span(item.GetLeftOffset() or 0,
                                             item.GetDuration()),
                        "effects": [{
                            "OTIO_SCHEMA": "Effect.1", "name": "",
                            "effect_name": "Resolve Effect",
                            "metadata": {"Resolve_OTIO": {
                                "Effect Name": otio_mix.VOLUME_EFFECT_NAME,
                                "Enabled": True, "Name": "Volume",
                                "Type": 62,
                                "Parameters": [volume] if volume else []}}}],
                    })
                    position = item.GetEnd()
                tracks.append({"OTIO_SCHEMA": "Track.1",
                               "name": f"{label[0]}{index}", "kind": label,
                               "children": children})
        Path(path).write_text(json.dumps({
            "OTIO_SCHEMA": "Timeline.1", "name": self._name,
            "tracks": {"OTIO_SCHEMA": "Stack.1", "children": tracks},
        }), encoding="utf-8")
        return True

    def AddTransition(self, *args):
        raise ResolveDoubleError(
            "AddTransition is not modelled - extend the double with the "
            "measured carrier behaviour first"
        )

    # ── anything else is a loud absence, never a shrug ──
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise ResolveDoubleError(
            f"FakeTimeline has no {name} - the test reached Resolve "
            f"surface the canonical double does not model; extend "
            f"tests/resolve_double.py with the measured behaviour"
        )


def place_clip(
    timeline, pool_clip, start_frame, end_frame, kind="video", track_index=1, name=None
):
    """Test-side helper: put one pool clip on a row, inheriting markers."""
    item = FakeTimelineItem(
        name or pool_clip.GetName(),
        timeline,
        start=_append_end(timeline, kind, track_index),
        duration=end_frame - start_frame + 1,
        left_offset=start_frame,
        pool_item=pool_clip,
    )
    for key, marker in (pool_clip.GetMarkers() or {}).items():
        item._markers[int(key)] = dict(marker)
    timeline._ensure_track(kind, track_index)
    timeline._tracks[kind][track_index - 1][1].append(item)
    return item


def _append_end(timeline, kind, track_index):
    timeline._ensure_track(kind, track_index)
    items = timeline._tracks[kind][track_index - 1][1]
    if not items:
        return timeline.GetStartFrame()
    return max(item.GetEnd() for item in items)


#: The four `MediaIn` inputs that say WHICH FRAMES it reads, and the
#: three that say what it is bound to (`composed_edit`'s split).
FRAME_TERMS = ("GlobalIn", "GlobalOut", "ClipTimeStart", "ClipTimeEnd")
BINDING_TERMS = ("MediaSource", "MediaID", "AudioTrack")


class FakeTool:
    """A Fusion tool. ``SetInput`` returns None whether it took or not.

    ``inert``: accepts every write and changes nothing - the shape
    `comp_media_window` measured, where `SetInput` cannot pull a window
    back. ``derived``: measured on Resolve Studio 21.1
    (`composed_edit.apply_window`) - with `MediaSource` set back to
    `Timeline` the window is RECOMPUTED from the item and already right,
    and writing the four frame terms on top moves `GlobalIn` one frame
    later and `ClipTimeEnd` one frame earlier.
    """

    def __init__(self, reg_id="MediaIn", inputs=None, inert=False,
                 derived=None):
        self._reg_id = reg_id
        self._inputs = dict(inputs or {})
        self.inert = inert
        self.derived = dict(derived) if derived else None

    def GetAttrs(self, key):
        return self._reg_id if key == "TOOLS_RegID" else None

    def GetInput(self, key, *_frame):
        return self._inputs.get(key)

    def SetInput(self, key, value, *_frame):
        if self.inert:
            return None
        self._inputs[key] = value
        if self.derived is None:
            return None
        if key == "MediaSource" and value == "Timeline":
            self._inputs.update(self.derived)
        elif key in FRAME_TERMS and self._inputs.get("MediaSource") == "Timeline":
            if key == "GlobalIn":
                self._inputs[key] = value + 1
            elif key == "ClipTimeEnd":
                self._inputs[key] = value - 1
        return None


class FakeComp:
    """A Fusion composition: named tools."""

    def __init__(self, tools=None):
        self.tools = dict(tools or {})

    def GetToolList(self, _selected=False):
        return dict(self.tools)

    def media_in(self):
        return self.tools["MediaIn1"]


class FakeTimelineItem:
    _next_id = 0

    def __init__(
        self,
        name,
        timeline,
        start=0,
        duration=48,
        left_offset=0,
        pool_item=None,
        enabled=True,
        markers=None,
        uid=None,
        source_start_frame=None,
        source_end_frame=None,
        pool_frame_count=None,
        fusion_comps=(),
        source_audio_channel_mapping=None,
        has_media=True,
        nodes=1,
        inert_media_in=False,
    ) -> None:
        FakeTimelineItem._next_id += 1
        self._uid = uid or f"item-{FakeTimelineItem._next_id}"
        self._name = name
        self._timeline = timeline
        self._start = start
        self._duration = duration
        self._left_offset = left_offset
        self._pool_item = (pool_item or FakeMediaPoolItem(name)) if has_media else None
        # None stays None: an unreadable trim reads as unknown.
        self._source_start_frame = (
            left_offset if source_start_frame is None else source_start_frame
        )
        self._source_end_frame = source_end_frame
        if source_end_frame is None and left_offset is not None:
            # Read here as left + duration. Real Resolve's reading is
            # contested: `resolve_axi` calls it inclusive, and
            # `timeline_ingest` measured it a frame off on about a third
            # of clips - so a test that cares passes `source_end_frame`.
            self._source_end_frame = left_offset + duration
        if pool_frame_count is not None:
            self._pool_item.SetClipProperty("Frames", pool_frame_count)
        self.start_tc_frames = 0
        # None stays None: an unreadable enabled state must read as
        # unknown, never as False (a rebuild would silently restore it).
        self._enabled = None if enabled is None else bool(enabled)
        self._color = ""
        self._markers: dict = {
            int(frame): dict(marker) for frame, marker in (markers or {}).items()
        }
        self._linked_items: list = []
        self.placed: list = []
        self.marker_delete_calls: list = []
        self.refuse_marker_delete_frames: set = set()
        self._props: dict = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0}
        #: Every accepted `SetProperty` write, in order: `(name, value)`.
        self.property_writes: list = []
        #: Every declined one - a name Resolve does not expose, or one in
        #: the `refuse_properties` fault knob.
        self.refused: list = []
        self.refuse_properties: frozenset = frozenset()
        self._speed = 1.0
        self._fusion_comps = list(fusion_comps)
        self.export_calls: list = []
        #: Colour-page node count (`GetNumNodes`; `CopyGrades` copies it).
        self.nodes = nodes
        #: Comps imported onto this item get an inert MediaIn.
        self.inert_media_in = inert_media_in
        self._source_audio_channel_mapping = source_audio_channel_mapping or ""
        #: The clip-volume parameter as OTIO carries it (`Export`).
        self._otio_volume = None

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._start + self._duration

    def GetDuration(self):
        return self._duration

    def GetLeftOffset(self):
        return self._left_offset

    def GetSourceStartFrame(self):
        return self._source_start_frame

    def GetSourceEndFrame(self):
        return self._source_end_frame

    def GetSourceStartTime(self):
        return (self._source_start_frame + self.start_tc_frames) * 1001 / 24000

    def GetSourceEndTime(self):
        return (self._source_end_frame + self.start_tc_frames) * 1001 / 24000

    def GetRightOffset(self):
        """Source frames left after the out point - how far the item
        can extend rightwards (0 for an item playing to the file end)."""
        pool = self._pool_item
        frames = int((pool.GetClipProperty("Frames") if pool else 0) or 0)
        return max(0, frames - self._left_offset - self._duration)

    def GetMediaPoolItem(self):
        return self._pool_item

    def GetClipEnabled(self):
        return self._enabled

    def SetClipEnabled(self, enabled):
        """Off-current the call returns False and keeps state (measured
        on Reel 09's staging, 2026-10-02)."""
        timeline = self._timeline
        project = getattr(timeline, "_project", None)
        if project is not None and project.GetCurrentTimeline() is not timeline:
            return False
        self._enabled = bool(enabled)
        return True

    def GetClipColor(self):
        return self._color

    def SetClipColor(self, color):
        self._color = color
        return True

    def GetProperty(self, key=None):
        if key is None:
            return dict(self._props)
        return self._props.get(key)

    @property
    def properties(self):
        """Test-side view of the item's transform properties (live)."""
        return self._props

    @properties.setter
    def properties(self, values):
        self._props = dict(values)

    def SetProperty(self, key, value):
        """Declines a name Resolve does not expose; see the constants."""
        if key not in VIDEO_ITEM_PROPERTIES or key in self.refuse_properties:
            self.refused.append((key, value))
            return False
        self._props[key] = value
        self.property_writes.append((key, value))
        return key not in FALSE_BUT_SET_PROPERTIES

    def GetSpeed(self):
        return self._speed

    def SetSpeed(self, speed):
        self._speed = float(speed)
        return True

    def GetCDL(self):
        return dict(self._cdl) if hasattr(self, "_cdl") else {}

    def SetCDL(self, values):
        self._cdl = dict(values)
        return True

    def GetMarkers(self):
        return {k: dict(v) for k, v in self._markers.items()}

    def AddMarker(self, source_frame, color, name, note, duration, custom_data=""):
        self._markers[int(source_frame)] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": int(duration or 1),
            "customData": custom_data or "",
        }
        self.placed.append((source_frame, color, name, note, duration, custom_data))
        return True

    def DeleteMarkerAtFrame(self, source_frame):
        source_frame = int(source_frame)
        self.marker_delete_calls.append(source_frame)
        if source_frame in self.refuse_marker_delete_frames:
            return False
        return self._markers.pop(source_frame, None) is not None

    def GetMarkerCustomData(self, source_frame):
        marker = self._markers.get(int(source_frame))
        return marker["customData"] if marker else ""

    def GetLinkedItems(self):
        return list(self._linked_items)

    def GetSourceAudioChannelMapping(self):
        return self._source_audio_channel_mapping

    # ── read surface production tolerates as absent ──
    # `reel_read._call` probes these with a default; the double answers
    # the way an empty real item does rather than raising, so a read is
    # complete instead of half. Anything NOT listed here stays loud in
    # `__getattr__` below.

    def GetFlagList(self):
        return []

    @property
    def comps(self):
        """Test-side view of the item's Fusion comps (live list)."""
        return self._fusion_comps

    @comps.setter
    def comps(self, comps):
        self._fusion_comps = list(comps)

    def GetFusionCompCount(self):
        return len(self._fusion_comps)

    def GetFusionCompNameList(self):
        return [f"Comp {index}" for index in range(1, len(self._fusion_comps) + 1)]

    def GetFusionCompByIndex(self, index):
        if 1 <= index <= len(self._fusion_comps):
            return self._fusion_comps[index - 1]
        return None

    def DeleteFusionCompByName(self, name):
        index = int(str(name).split()[-1]) - 1
        if 0 <= index < len(self._fusion_comps):
            self._fusion_comps.pop(index)
            return True
        return False

    def ExportFusionComp(self, path, index):
        """Write comp ``index`` to ``path``: a text comp verbatim, a
        ``FakeComp`` as its MediaIn window plus the item's length."""
        if not 1 <= index <= len(self._fusion_comps):
            return False
        self.export_calls.append((path, index))
        comp = self._fusion_comps[index - 1]
        if isinstance(comp, FakeComp):
            comp = json.dumps({"window": comp.media_in()._inputs,
                               "keys": self._duration})
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(comp, encoding="utf-8", newline="")
        return True

    def ImportFusionComp(self, path):
        """Measured (composed-edit spike): Resolve re-binds the imported
        comp's MediaIn to the WHOLE pool clip and throws the authored
        window away - which is why callers conform after an import.

        A conform export (`ExportFusionComp` above) REPLACES what the item
        carries; any other comp text - builder output, as the entry-motion
        path writes - is ADDED beside it. Its Merge stands in for the
        authored drawing, so `comp_draws_something` reads True.
        """
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:
            payload = None
        pool = self._pool_item
        frames = int((pool.GetClipProperty("Frames") if pool else 0) or 0)
        rebound = {
            "MediaSource": "MediaPool",
            "MediaID": pool.GetMediaId() if pool else "",
            "AudioTrack": "No_Audo_Track",
            "GlobalIn": 0, "GlobalOut": frames - 1,
            "ClipTimeStart": 0, "ClipTimeEnd": frames - 1,
        }
        media_in = FakeTool("MediaIn", rebound, inert=self.inert_media_in)
        if isinstance(payload, dict) and "keys" in payload:
            self._fusion_comps = [FakeComp({
                "MediaIn1": media_in,
                "Transform1": FakeTool("Transform", {"Size": 1.0,
                                                     "_keys": payload["keys"]}),
            })]
        else:
            self._fusion_comps.append(FakeComp({
                "MediaIn1": media_in,
                "EntryFade1": FakeTool("Merge", {"Blend": 1.0}),
            }))
        return True

    # ── grade ──
    def GetNumNodes(self):
        return self.nodes

    def CopyGrades(self, targets):
        for target in targets:
            target.nodes = self.nodes
        return True

    def GetColorGroup(self):
        return ""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise ResolveDoubleError(
            f"FakeTimelineItem has no {name} - extend "
            f"tests/resolve_double.py with the measured behaviour"
        )


def timeline_item(
    name,
    start,
    end,
    *,
    path=None,
    left_offset=0,
    markers=None,
    pool_markers=None,
    **knobs,
):
    """Test-side builder: one item spanning ``[start, end)`` on no timeline
    yet, its pool clip at ``path``. ``FakeTimeline(video=...)`` (or
    ``add_item``) binds it; the test keeps the live handle."""
    pool_item = FakeMediaPoolItem(name)
    if path:
        pool_item.SetClipProperty("File Path", path)
    for frame, marker in (pool_markers or {}).items():
        pool_item._markers[int(frame)] = dict(marker)
    return FakeTimelineItem(
        name,
        None,
        start=start,
        duration=end - start,
        left_offset=left_offset,
        pool_item=pool_item,
        markers=markers,
        **knobs,
    )


class TimelineItemSpec:
    """Compact data for a timeline item; materializes as the canonical item.

    This is a test fixture description, not another Resolve model. It keeps
    assertions about source spans readable while all API behavior remains in
    ``FakeTimelineItem``.
    """

    def __init__(
        self,
        name,
        start,
        end,
        enabled=True,
        path=None,
        left_offset=None,
        transform=None,
        markers=None,
        uid=None,
        source_start_frame=None,
        source_end_frame=None,
        pool_frame_count=None,
        has_media=True,
        fusion_comps=(),
        source_audio_channel_mapping=None,
    ):
        self.name = name
        self.start = start
        self.end = end
        self.enabled = enabled
        self.path = path
        self.left_offset = start if left_offset is None else left_offset
        self.transform = dict(transform or {})
        self.markers = dict(markers or {})
        self.uid = uid
        self.source_start_frame = source_start_frame
        self.source_end_frame = source_end_frame
        self.pool_frame_count = pool_frame_count
        self.has_media = has_media
        self.fusion_comps = list(fusion_comps)
        self.source_audio_channel_mapping = source_audio_channel_mapping

    def _as_item(self, timeline):
        pool_item = FakeMediaPoolItem(self.name)
        if self.path:
            pool_item.SetClipProperty("File Path", self.path)
        item = FakeTimelineItem(
            self.name,
            timeline,
            start=self.start,
            duration=self.end - self.start,
            left_offset=self.left_offset,
            pool_item=pool_item,
            enabled=self.enabled,
            markers=self.markers,
            uid=self.uid,
            source_start_frame=self.source_start_frame,
            source_end_frame=self.source_end_frame,
            pool_frame_count=self.pool_frame_count,
            fusion_comps=self.fusion_comps,
            source_audio_channel_mapping=self.source_audio_channel_mapping,
        )
        for key, value in self.transform.items():
            item.SetProperty(key, value)
        if not self.has_media:
            item._pool_item = None
        return item


# ── The behavioural contract ──────────────────────────────────────────
#
# Each entry is `(name, check)` where `check(project_like)` builds what it
# needs off the given project factory and raises on mismatch. The offline
# contract suite runs every check against the double; the qualification
# suite runs the SAME checks against a live scratch project. A fake that
# passes and reality that fails is the finding - never a reason to weaken
# the check.


def _needs_timeline(project, name="Contract Timeline"):
    timeline = project.GetMediaPool().CreateEmptyTimeline(name)
    project.SetCurrentTimeline(timeline)
    return timeline


def _contract_pool_clip(project, pool_clip=None):
    """Use imported test footage on live Resolve, or seed the double.

    The live qualification passes a clip imported into its scratch project.
    Offline contracts have no filesystem dependency and seed the in-memory
    media pool with the same clip shape.
    """
    if pool_clip is not None:
        return pool_clip
    pool_clip = make_pool_clip()
    project.GetMediaPool().GetRootFolder()._clips.append(pool_clip)
    return pool_clip


def _append_contract_clip(
    project, pool_clip, start, end, *, kind="video", track_index=1
):
    """Append and read back the item using Resolve's public surface."""
    media_type = 1 if kind == "video" else 2
    project.GetMediaPool().AppendToTimeline(
        [
            {
                "mediaPoolItem": pool_clip,
                "startFrame": start,
                "endFrame": end,
                "mediaType": media_type,
                "trackIndex": track_index,
            }
        ]
    )
    return project.GetCurrentTimeline().GetItemListInTrack(kind, track_index)[-1]


def _ensure_track(timeline, kind, index):
    while timeline.GetTrackCount(kind) < index:
        timeline.AddTrack(kind)


def check_exact_names_win(project, pool_clip=None):
    project.GetMediaPool().CreateEmptyTimeline("Reel 16 - dawn")
    project.GetMediaPool().CreateEmptyTimeline("Reel 16 - dusk")
    assert project.GetTimelineCount() == 2
    names = [project.GetTimelineByIndex(i + 1).GetName() for i in range(2)]
    assert names == ["Reel 16 - dawn", "Reel 16 - dusk"]


def check_listing_never_moves_the_cursor(project, pool_clip=None):
    first = project.GetMediaPool().CreateEmptyTimeline("First")
    second = project.GetMediaPool().CreateEmptyTimeline("Second")
    project.SetCurrentTimeline(first)
    _ = project.GetTimelineByIndex(2)
    _ = project.GetTimelineCount()
    assert project.GetCurrentTimeline().GetUniqueId() == first.GetUniqueId()
    assert second.GetName() == "Second"


def check_track_enable_reads_on_the_cursor(project, pool_clip=None):
    timeline = _needs_timeline(project)
    _ensure_track(timeline, "audio", 1)
    _ensure_track(timeline, "audio", 2)
    assert timeline.GetIsTrackEnabled("audio", 1) is True
    assert timeline.GetIsTrackEnabled("audio", 2) is True


def check_track_enable_lies_off_the_cursor(project, pool_clip=None):
    timeline = _needs_timeline(project)
    _ensure_track(timeline, "audio", 1)
    other = project.GetMediaPool().CreateEmptyTimeline("Other")
    project.SetCurrentTimeline(other)
    assert timeline.GetIsTrackEnabled("audio", 1) is False


def check_clip_enable_writes_on_the_cursor(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 100, 219)
    assert item.SetClipEnabled(False) is True
    assert item.GetClipEnabled() is False
    assert item.SetClipEnabled(True) is True
    assert item.GetClipEnabled() is True


def check_clip_enable_refuses_off_the_cursor(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 100, 219)
    assert item.SetClipEnabled(False) is True
    other = project.GetMediaPool().CreateEmptyTimeline("Other")
    project.SetCurrentTimeline(other)
    assert item.SetClipEnabled(True) is False
    assert item.GetClipEnabled() is False


def check_resolution_change_rescales_pan_tilt(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 0, 47)
    assert item.SetProperty("Pan", 100.0) is True
    assert item.SetProperty("Tilt", 50.0) is True
    old_w = float(project.GetSetting("timelineResolutionWidth"))
    old_h = float(project.GetSetting("timelineResolutionHeight"))
    assert (
        project.SetSettings(
            {
                "timelineResolutionWidth": str(int(old_w * 2)),
                "timelineResolutionHeight": str(int(old_h * 2)),
            }
        )
        is True
    )
    assert item.GetProperty("Pan") == 200.0
    assert item.GetProperty("Tilt") == 100.0


def check_unrelated_setting_keeps_pan_tilt(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 0, 47)
    item.SetProperty("Pan", 100.0)
    project.SetSetting("timelineFrameRate", "24")
    assert item.GetProperty("Pan") == 100.0


def check_set_timecode_past_the_end_reads_back(project, pool_clip=None):
    timeline = _needs_timeline(project)
    assert timeline.SetCurrentTimecode("00:01:00:00") is True
    assert timeline.GetCurrentTimecode() == "00:01:00:00"


def check_timeline_marker_round_trip(project, pool_clip=None):
    timeline = _needs_timeline(project)
    assert timeline.AddMarker(10, "Blue", "Q", "a question", 1, "") is True
    marker = timeline.GetMarkers()[10]
    assert marker["name"] == "Q"
    assert marker["note"] == "a question"
    assert timeline.DeleteMarkerAtFrame(10) is True
    assert timeline.GetMarkers() == {}
    assert timeline.DeleteMarkerAtFrame(10) is False


def check_add_marker_past_the_end_is_accepted(project, pool_clip=None):
    timeline = _needs_timeline(project)
    end = int(timeline.GetEndFrame())
    assert timeline.AddMarker(end + 50, "Green", "late", "past it", 1, "") is True
    assert (end + 50) in timeline.GetMarkers()


def check_clip_marker_maps_by_source_frame(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 100, 199)
    assert item.GetLeftOffset() == 100
    assert item.AddMarker(125, "Cyan", "SUBJECT", "left of frame", 1, "") is True
    expected = item.GetStart() + (125 - item.GetLeftOffset())
    assert expected == item.GetStart() + 25


def check_placement_lands_on_the_current_timeline(project, pool_clip=None):
    first = project.GetMediaPool().CreateEmptyTimeline("First")
    second = project.GetMediaPool().CreateEmptyTimeline("Second")
    pool_clip = _contract_pool_clip(project, pool_clip)
    # Aimed at nothing in particular: the spec names no timeline, and
    # Resolve appends to the CURRENT one (2026-09-04: 579 captions
    # aimed at fifteen reels landed on Reel 01).
    project.SetCurrentTimeline(first)
    project.GetMediaPool().AppendToTimeline(
        [
            {
                "mediaPoolItem": pool_clip,
                "startFrame": 0,
                "endFrame": 47,
                "mediaType": 1,
                "trackIndex": 1,
            }
        ]
    )
    assert len(first.GetItemListInTrack("video", 1)) == 1
    assert all(
        not (second.GetItemListInTrack("video", row) or [])
        for row in range(1, second.GetTrackCount("video") + 1)
    )
    project.SetCurrentTimeline(second)
    project.GetMediaPool().AppendToTimeline(
        [
            {
                "mediaPoolItem": pool_clip,
                "startFrame": 0,
                "endFrame": 47,
                "mediaType": 1,
                "trackIndex": 1,
            }
        ]
    )
    assert len(first.GetItemListInTrack("video", 1)) == 1
    assert len(second.GetItemListInTrack("video", 1)) == 1


def check_pool_marker_inherited_at_placement(project, pool_clip=None):
    pool_clip = _contract_pool_clip(project, pool_clip)
    pool_clip.DeleteMarkersByColor("All")
    pool_clip.AddMarker(110, "Pink", "POOL", "seen once", 1, "")
    _needs_timeline(project)  # current: the append target below
    item = _append_contract_clip(project, pool_clip, 100, 199)
    assert 110 in (item.GetMarkers() or {})


def check_pool_marker_after_placement_does_not_propagate(project, pool_clip=None):
    pool_clip = _contract_pool_clip(project, pool_clip)
    pool_clip.DeleteMarkersByColor("All")
    _needs_timeline(project)  # current: the append target below
    item = _append_contract_clip(project, pool_clip, 100, 199)
    assert pool_clip.AddMarker(110, "Pink", "POOL", "late arrival", 1, "") is True
    assert 110 not in (item.GetMarkers() or {})


def check_property_whole_dict_or_nothing(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 0, 47)
    whole = item.GetProperty()
    assert isinstance(whole, dict) and "Pan" in whole
    assert not item.GetProperty("NoSuchProperty")
    assert item.SetProperty("Pan", 12.5) is True
    assert item.GetProperty("Pan") == 12.5


def check_audio_rows_carry_stream_names(project, pool_clip=None):
    timeline = _needs_timeline(project)
    named_rows = []
    for name in ("Akshita CH1", "Craig CH2"):
        timeline.AddTrack("audio")
        index = timeline.GetTrackCount("audio")
        timeline.SetTrackName("audio", index, name)
        named_rows.append((index, name))
    for index, name in named_rows:
        assert timeline.GetTrackName("audio", index) == name
    assert timeline.GetTrackName("audio", named_rows[0][0]) != "Akshita"


def check_append_end_frame_is_exclusive(project, pool_clip=None):
    _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    first = _append_contract_clip(project, pool_clip, 100, 148)
    assert first.GetDuration() == 48
    assert first.GetEnd() - first.GetStart() == 48


def check_append_over_a_live_item_places_nothing(project, pool_clip=None):
    timeline = _needs_timeline(project)
    pool_clip = _contract_pool_clip(project, pool_clip)
    _append_contract_clip(project, pool_clip, 100, 148)
    returned = project.GetMediaPool().AppendToTimeline([{
        "mediaPoolItem": pool_clip, "startFrame": 100, "endFrame": 148,
        "mediaType": 1, "trackIndex": 1,
        "recordFrame": timeline.GetStartFrame() + 24,
    }])
    assert returned  # truthy, with a live-looking handle
    assert len(timeline.GetItemListInTrack("video", 1)) == 1


def check_unknown_property_declines(project, pool_clip=None):
    _needs_timeline(project)
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 0, 48)
    assert item.SetProperty("PanX", 10.0) is False
    assert not item.GetProperty("PanX")


def check_anchor_point_returns_false_and_sets(project, pool_clip=None):
    _needs_timeline(project)
    pool_clip = _contract_pool_clip(project, pool_clip)
    item = _append_contract_clip(project, pool_clip, 0, 48)
    assert item.SetProperty("AnchorPointX", 12.0) is False
    assert item.GetProperty("AnchorPointX") == 12.0


def check_add_sub_folder_becomes_current(project, pool_clip=None):
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    folder = pool.AddSubFolder(root, "Contract Bin")
    try:
        assert pool.GetCurrentFolder().GetUniqueId() == folder.GetUniqueId()
    finally:
        pool.SetCurrentFolder(root)
        pool.DeleteFolders([folder])


def check_set_metadata_refuses_an_unknown_key(project, pool_clip=None):
    pool_clip = _contract_pool_clip(project, pool_clip)
    assert pool_clip.SetMetadata("Reel Name", "x") is False
    assert not pool_clip.GetMetadata("Reel Name")


def check_delete_clips_on_a_timeline_item_deletes_it(project, pool_clip=None):
    timeline = project.GetMediaPool().CreateEmptyTimeline("Doomed")
    assert project.GetTimelineCount() == 1
    assert project.GetMediaPool().DeleteClips([timeline.GetMediaPoolItem()])
    assert project.GetTimelineCount() == 0


def check_render_range_needs_select_all_frames_off(project, pool_clip=None):
    """Queues and deletes jobs; never starts a render."""
    _needs_timeline(project)
    pool_clip = _contract_pool_clip(project, pool_clip)
    _append_contract_clip(project, pool_clip, 0, 96)
    jobs = []
    try:
        project.SetRenderSettings({"SelectAllFrames": False,
                                   "MarkIn": 10, "MarkOut": 10})
        jobs.append(project.AddRenderJob())
        queued = next(j for j in project.GetRenderJobList()
                      if j["JobId"] == jobs[-1])
        assert (queued["MarkIn"], queued["MarkOut"]) == (10, 10)
    finally:
        for job in jobs:
            project.DeleteRenderJob(job)


def check_delete_timelines_updates_count(project, pool_clip=None):
    timeline = _needs_timeline(project)
    assert project.GetTimelineCount() == 1
    assert project.GetMediaPool().DeleteTimelines([timeline]) is True
    assert project.GetTimelineCount() == 0
    assert project.GetCurrentTimeline() is None
    assert timeline.GetName() is None


def check_delete_clips_removes_items(project, pool_clip=None):
    timeline = _needs_timeline(project)  # current: the append target below
    pool_clip = _contract_pool_clip(project, pool_clip)
    items = []
    for start in (0, 48):
        items.append(_append_contract_clip(project, pool_clip, start, start + 47))
    assert timeline.DeleteClips([items[0]]) is True
    remaining = timeline.GetItemListInTrack("video", 1)
    assert [item.GetUniqueId() for item in remaining] == [items[1].GetUniqueId()]


def check_custom_data_round_trip(project, pool_clip=None):
    timeline = _needs_timeline(project)
    timeline.AddMarker(5, "Blue", "Q", "words", 1, '{"by":"test"}')
    assert timeline.GetMarkerCustomData(5) == '{"by":"test"}'
    assert timeline.UpdateMarkerCustomData(5, '{"by":"test2"}') is True
    assert timeline.GetMarkerCustomData(5) == '{"by":"test2"}'
    assert timeline.UpdateMarkerCustomData(999, "x") is False


def check_track_add_delete(project, pool_clip=None):
    timeline = _needs_timeline(project)
    before = timeline.GetTrackCount("video")
    # Production shape (`resolve_build_timeline`): AddTrack takes the
    # kind only; the name goes through SetTrackName and is read back.
    assert timeline.AddTrack("video") is not None
    index = timeline.GetTrackCount("video")
    assert index == before + 1
    assert timeline.SetTrackName("video", index, "Semantic") is True
    assert timeline.GetTrackName("video", index) == "Semantic"
    assert timeline.DeleteTrack("video", index) is True
    assert timeline.GetTrackCount("video") == before


def check_linked_items_round_trip(project, pool_clip=None):
    _needs_timeline(project)
    pool_clip = _contract_pool_clip(project, pool_clip)
    first = _append_contract_clip(project, pool_clip, 0, 47)
    second = _append_contract_clip(project, pool_clip, 48, 95)
    timeline = project.GetCurrentTimeline()
    assert timeline.SetClipsLinked([first, second], True) is True
    first_linked = {item.GetUniqueId() for item in first.GetLinkedItems()}
    second_linked = {item.GetUniqueId() for item in second.GetLinkedItems()}
    assert first_linked == {second.GetUniqueId()}
    assert second_linked == {first.GetUniqueId()}
    assert timeline.SetClipsLinked([first, second], False) is True
    assert first.GetLinkedItems() == []
    assert second.GetLinkedItems() == []


CONTRACT_CHECKS = [
    ("exact names win; listing never moves the cursor", check_exact_names_win),
    ("listing never moves the cursor", check_listing_never_moves_the_cursor),
    ("track enable reads on the cursor", check_track_enable_reads_on_the_cursor),
    ("track enable lies off the cursor", check_track_enable_lies_off_the_cursor),
    ("clip enable writes on the cursor", check_clip_enable_writes_on_the_cursor),
    ("clip enable refuses off the cursor", check_clip_enable_refuses_off_the_cursor),
    ("resolution change rescales Pan/Tilt", check_resolution_change_rescales_pan_tilt),
    ("unrelated setting keeps Pan/Tilt", check_unrelated_setting_keeps_pan_tilt),
    ("timecode past the end reads back", check_set_timecode_past_the_end_reads_back),
    ("timeline marker round trip", check_timeline_marker_round_trip),
    ("past-the-end marker is accepted", check_add_marker_past_the_end_is_accepted),
    ("clip marker maps by source frame", check_clip_marker_maps_by_source_frame),
    (
        "placement lands on the current timeline",
        check_placement_lands_on_the_current_timeline,
    ),
    ("pool marker inherited at placement", check_pool_marker_inherited_at_placement),
    (
        "pool marker after placement does not propagate",
        check_pool_marker_after_placement_does_not_propagate,
    ),
    ("property whole-dict-or-nothing", check_property_whole_dict_or_nothing),
    ("audio rows carry stream names", check_audio_rows_carry_stream_names),
    ("append endFrame is exclusive", check_append_end_frame_is_exclusive),
    (
        "append over a live item places nothing",
        check_append_over_a_live_item_places_nothing,
    ),
    ("unknown property declines", check_unknown_property_declines),
    (
        "AnchorPointX returns False and sets",
        check_anchor_point_returns_false_and_sets,
    ),
    ("AddSubFolder becomes current", check_add_sub_folder_becomes_current),
    (
        "SetMetadata refuses an unknown key",
        check_set_metadata_refuses_an_unknown_key,
    ),
    (
        "DeleteClips on a timeline item deletes it",
        check_delete_clips_on_a_timeline_item_deletes_it,
    ),
    (
        "render range needs SelectAllFrames off",
        check_render_range_needs_select_all_frames_off,
    ),
    ("delete timelines updates count", check_delete_timelines_updates_count),
    ("delete clips removes items", check_delete_clips_removes_items),
    ("custom data round trip", check_custom_data_round_trip),
    ("track add and delete", check_track_add_delete),
    ("linked items round trip", check_linked_items_round_trip),
]


def run_contract_on(factory, *, pool_clip_factory=None, cleanup=None):
    """Run every contract against isolated projects from ``factory``.

    A live backend can pass an imported clip and a cleanup callback that
    removes each check's scratch timelines. Offline checks use fresh
    in-memory projects and seed an in-memory pool clip as needed.
    """
    for _name, check in CONTRACT_CHECKS:
        project = factory()
        pool_clip = (
            pool_clip_factory(project) if pool_clip_factory is not None else None
        )
        try:
            check(project, pool_clip)
        finally:
            if pool_clip is not None:
                pool_clip.DeleteMarkersByColor("All")
            if cleanup is not None:
                cleanup(project)
    return len(CONTRACT_CHECKS)
