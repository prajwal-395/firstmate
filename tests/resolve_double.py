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
  reads back there (``marker_capture`` live test).
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
  the item carries; unknown single-key reads answer ``""``
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
        "render queue (AddRenderJob/GetRenderJobList/StartRendering)",
        "Fusion comp import (ImportFusionComp)",
        "gallery stills (GrabStill/GetStills)",
        "voice isolation (GetVoiceIsolationState/SetVoiceIsolationState)",
        "multicam (CreateMulticamClip)",
        "project databases and folders on disk (LoadProject/SaveProject)",
    }
)

_DEFAULT_SETTINGS = {
    "timelineResolutionWidth": "1080",
    "timelineResolutionHeight": "1920",
    "timelineFrameRate": "30",
}


class FakeResolve:
    """``DaVinciResolveScript.scriptapp("Resolve")`` stood in."""

    def __init__(self, project=None) -> None:
        self._manager = FakeProjectManager(project)

    def GetProjectManager(self):
        return self._manager

    def GetProductName(self):
        return "DaVinci Resolve (canonical test double)"

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
        for timeline in timelines:
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
        self._root = FakeFolder("Root")
        self._current_folder = self._root
        self.audio_channels = (1,)
        self.media_properties: dict = {}
        self.import_failures: set = set()
        self.next_timeline = None
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
        if name is None:
            parent, name = self._root, parent
        folder = FakeFolder(name)
        parent._subfolders.append(folder)
        return folder

    def CreateEmptyTimeline(self, name):
        timeline = self.next_timeline or FakeTimeline(name, self._project)
        self.next_timeline = None
        timeline.SetName(name)
        if timeline not in self._project._timelines:
            self._project.adopt(timeline)
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
        return True

    def AppendToTimeline(self, specs):
        """Place clips; pool markers present NOW copy onto the new items.

        Appends to the CURRENT timeline only - never to a timeline named
        in the spec (there is no such parameter). Measured 2026-09-04:
        579 captions aimed at fifteen reels landed on Reel 01, which
        happened to be current, while every call returned True
        (`resolve_lock.PLACEMENT_REQUIRES_CURRENT`). With nothing
        current there is no destination, and the call returns False.

        A pool marker added AFTER placement does not reach existing items
        (measured, marker_feedback live tests) - so only the markers
        present at this call are inherited.
        """
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
                    start=spec.get("recordFrame", start),
                    duration=end - start + 1,
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
                timeline._ensure_track(kind, destination)
                timeline._tracks[kind][destination - 1][1].append(item)
                if offset == 0:
                    placed.append(item)
        return placed or True

    def ImportMedia(self, paths):
        clips = []
        for path in paths:
            if path in self.import_failures:
                continue
            clip = FakeMediaPoolItem(path.rsplit("/", 1)[-1])
            clip._props["File Path"] = path
            for key, value in self.media_properties.get(path, {}).items():
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


def make_pool_clip(
    name="clip.mov", frames=1000, resolution="3840x2160", clip_type="Video"
):
    """A media-pool clip carrying the properties ingest reads."""
    clip = FakeMediaPoolItem(name)
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
    def __init__(self, name="clip.mov") -> None:
        self._name = name
        self._props = {
            "File Name": name,
            "Type": "Video",
            "Frames": "1000",
            "Resolution": "3840x2160",
        }
        self._markers: dict = {}

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        self._props["File Name"] = name
        return True

    def GetMediaId(self):
        return self._name

    def GetUniqueId(self):
        return f"pool:{self._name}"

    def GetClipProperty(self, key=None):
        if key is None:
            return dict(self._props)
        return self._props.get(key, "")

    def GetMetadata(self, key=None):
        return ""

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
    ) -> None:
        self._name = name
        self.rename_ok = rename_ok
        self._media_pool_item = FakeMediaPoolItem(name)
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
        self.deletes = 0
        self.marker_delete_calls: list = []
        self.refuse_marker_delete_frames: set = set()
        self.explode_marker_delete_frames: set = set()
        self.refuse_marker_update_frames: set = set()
        self.forbid_marker_add = False
        self.raise_on_methods: dict = {}
        self._timecode = None
        self.link_calls: list = []
        self.deleted_items: list = []
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

    # ── edit verbs used by the reel writers ──
    def DeleteClips(self, clips, ripple=False):
        self.deletes += 1
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
    ) -> None:
        FakeTimelineItem._next_id += 1
        self._uid = uid or f"item-{FakeTimelineItem._next_id}"
        self._name = name
        self._timeline = timeline
        self._start = start
        self._duration = duration
        self._left_offset = left_offset
        self._pool_item = pool_item or FakeMediaPoolItem(name)
        self._source_start_frame = (
            left_offset if source_start_frame is None else source_start_frame
        )
        self._source_end_frame = (
            left_offset + duration if source_end_frame is None else source_end_frame
        )
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
        self._speed = 1.0
        self._fusion_comps = list(fusion_comps)
        self.export_calls: list = []
        self._source_audio_channel_mapping = source_audio_channel_mapping or ""

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
        return self._left_offset + self._duration

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
        return self._props.get(key, "")

    def SetProperty(self, key, value):
        self._props[key] = value
        return True

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

    def GetFusionCompCount(self):
        return len(self._fusion_comps)

    def GetFusionCompNameList(self):
        return [f"Comp {index}" for index in range(1, len(self._fusion_comps) + 1)]

    def ExportFusionComp(self, path, index):
        self.export_calls.append((path, index))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            self._fusion_comps[index - 1], encoding="utf-8", newline=""
        )
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
