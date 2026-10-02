"""Builders for a composed-edit reel on the canonical Resolve double.

Every defect the composed-edit spike measured against Resolve Studio 21.1
lives in `tests/resolve_double.py`, where every offline test meets it:

  - `AppendToTimeline` REFUSES to place over a live item, returns a
    truthy list containing a live-looking handle, and places nothing.
  - A placed item comes back at IDENTITY: no Fusion comp, one colour
    node, every transform property at its default.
  - `ImportFusionComp` re-binds `MediaIn` to the whole pool clip and
    throws the played window away.
  - `SetProperty` on `AnchorPointX` returns `False` and sets the value.
  - `SetInput` returns `None` whether it took or not.
  - `GetEnd()` is EXCLUSIVE, and `endFrame` in an append info is too.

This module only BUILDS the spike's reel shape out of those classes.
Nothing here reaches a real project or a real Resolve
(`tests/tooling/test_tests_never_reach_real_projects.py`).
"""

from __future__ import annotations

import copy
import os

from tests.resolve_double import (
    BINDING_TERMS,
    FRAME_TERMS,
    FakeComp,
    FakeMediaPoolItem,
    FakeProject,
    FakeTimeline,
    FakeTimelineItem,
    FakeTool,
)

__all__ = ["BINDING_TERMS", "FRAME_TERMS", "FakeComp", "FakeTool"]

#: The identity a freshly placed item comes back at. The whole reason
#: step 7 exists: a re-placed item keeps none of these.
IDENTITY_PROPERTIES = {
    "ZoomX": 1.0, "ZoomY": 1.0, "ZoomGang": 1, "Pan": 0.0, "Tilt": 0.0,
    "RotationAngle": 0.0, "AnchorPointX": 0.0, "AnchorPointY": 0.0,
    "CropLeft": 0.0, "CropRight": 0.0, "CropTop": 0.0, "CropBottom": 0.0,
    "Opacity": 100.0, "Scaling": 0, "ResizeFilter": 0,
}

#: A window that covers a `played` frame span, written the way the
#: engine's generated comps carry it (`fusion/engine.py`).
def covering_window(played: int, left_offset: int = 0,
                    source_frames: int = 10_000) -> dict:
    return {"MediaSource": "Timeline", "MediaID": "",
            "AudioTrack": "Timeline Audio",
            "GlobalIn": -int(left_offset),
            "GlobalOut": int(source_frames) - int(left_offset) - 1,
            "ClipTimeStart": -int(left_offset),
            "ClipTimeEnd": int(source_frames) - int(left_offset) - 1}


def pool_clip(path, frames=10_000, fps="30.0", name="", on_disk=None):
    """A pool clip of ``frames`` frames; ``on_disk`` is what the file
    holds NOW, read only by `ReplaceClip`."""
    clip = FakeMediaPoolItem(name or os.path.basename(path))
    clip._props.update({"File Path": path, "Clip Path": path,
                        "Frames": str(frames), "FPS": fps,
                        "Resolution": "1080x1920"})
    clip.on_disk_frames = None if on_disk is None else on_disk
    return clip


def frames_of(clip):
    return int(clip.GetClipProperty("Frames"))


def item(mpi, start, duration, left_offset=0, properties=None, comps=None,
         nodes=1, name=None, comp_windows=None, inert_media_in=False,
         enabled=True):
    """One timeline item at ``start`` for ``duration`` frames, playing
    ``mpi`` from ``left_offset``; ``comp_windows`` gives it one comp
    per covering window."""
    placed = FakeTimelineItem(
        name or mpi.GetName(), None, start=int(start),
        duration=int(duration), left_offset=int(left_offset),
        pool_item=mpi, nodes=nodes, inert_media_in=inert_media_in,
        enabled=enabled)
    placed.properties = dict(properties or IDENTITY_PROPERTIES)
    placed.comps = [
        FakeComp({"MediaIn1": FakeTool("MediaIn", window,
                                       inert=inert_media_in),
                  "Transform1": FakeTool("Transform", {"Size": 1.0})})
        for window in (comp_windows or [])]
    if comps:
        placed.comps = list(comps)
    return placed


def rows_timeline(name, rows, track_names=None):
    """``rows`` is ``{"V1": [item, ...], "A1": [...]}``; rows are named
    ``V1``/``A1`` unless ``track_names`` says otherwise. In no project."""
    track_names = dict(track_names or {})

    def row_list(prefix):
        count = max([int(key[1:]) for key in rows if key[0] == prefix],
                    default=0)
        return [(track_names.get(f"{prefix}{index}", f"{prefix}{index}"),
                 rows.get(f"{prefix}{index}", []))
                for index in range(1, count + 1)]

    return FakeTimeline(name, video=row_list("V"), audio=row_list("A"))


def reel_timeline(name, rows, track_names=None, project=None):
    """`rows_timeline`, CURRENT in its project (a new one unless given),
    so `AppendToTimeline` lands on it."""
    timeline = rows_timeline(name, rows, track_names)
    project = project or FakeProject()
    project.adopt(timeline)
    project.SetCurrentTimeline(timeline)
    return timeline


def media_pool(timeline):
    """The pool of the timeline's project, with that timeline current."""
    project = timeline._project
    project.SetCurrentTimeline(timeline)
    return project.GetMediaPool()


def build_reel(tmp_path, *, head_duration=479, head_comp=True):
    """A small reel with the shape the spike's Reel 01 has.

    V1: three A-roll clips.  V2: one clip that predates the cut and must
    NOT move.  V4: three caption cards after the cut - the row the prior
    spike left out of its plan and measured as "caption desync".  A1:
    two audio items linked to V1.
    """
    aroll = pool_clip("/lab/LC4930.MXF", frames=5400)
    broll = pool_clip("/lab/opener.mov", frames=2000)
    card = pool_clip("/lab/card.mov", frames=200)
    tone = pool_clip("/lab/LC4930.MXF", frames=5400, name="audio")
    # The freeze hold is exactly as long as the file: left_offset 0,
    # right_offset 0, no source to trim into. Lengthening it is a file
    # operation, so the plan must REFUSE it rather than place a hole.
    freeze = pool_clip("/lab/reel_freeze.mov", frames=19, name="freeze")
    overlay = pool_clip("/lab/tv_frame.mov", frames=1675, name="tv_frame")

    def picture(start, duration, left, comps):
        return item(
            aroll, start, duration, left,
            properties={**IDENTITY_PROPERTIES, "ZoomX": 2.307,
                        "Pan": 5.972, "Tilt": 1.0},
            nodes=8,
            comp_windows=[covering_window(duration, left, frames_of(aroll))]
            if comps else [])

    rows = {
        "V1": [picture(590, head_duration, 3151, head_comp),
               picture(590 + head_duration, 186, 1200, True),
               # The freeze inherits the transform of the shot in front
               # of it, as the build gives it one.
               item(freeze, 590 + head_duration + 186, 19, 0, nodes=8,
                    properties={**IDENTITY_PROPERTIES, "ZoomX": 2.307,
                                "Pan": 99.656, "Tilt": 1.0},
                    comp_windows=[covering_window(19, 0, 19)])],
        "V2": [item(broll, 0, 590, 0, nodes=8,
                    comp_windows=[covering_window(590, 0, 2000)])],
        # The overlay that spans the whole reel. It ends where the reel
        # ends, so an ending change must carry it out - the spike's
        # `tv_frame`, which had 401 frames of source headroom.
        # An overlay row carries a rendered artefact and a transform,
        # NOT a builder-generated per-clip comp: the pass writes comps on
        # V1 and V2 only (`execution/fusion_tracks.py`).
        "V3": [item(overlay, 0, 590 + head_duration + 186 + 19, 0, nodes=1)],
        "V4": [item(card, 590 + head_duration + 10, 40, 0, nodes=1),
               item(card, 590 + head_duration + 60, 40, 0, nodes=1),
               item(card, 590 + head_duration + 110, 40, 0, nodes=1)],
        "A1": [item(tone, 590, head_duration, 3151),
               item(tone, 590 + head_duration, 186, 1200)],
    }
    timeline = reel_timeline("LAB staged", rows)
    return (timeline, media_pool(timeline),
            {"aroll": aroll, "card": card, "freeze": freeze,
             "broll": broll, "tone": tone, "overlay": overlay})


def duplicate(timeline, name="LAB control", drift=()):
    """A copy of a timeline. `drift` names windows the copy normalises.

    `DuplicateTimeline` was measured to alter a comp's media window with
    nothing else in the readable structure differing, which is why step
    1 conforms a staging copy before it is trusted. The copy joins the
    source's project and does not move its cursor.
    """
    rows = {}
    for key, items in timeline.rows.items():
        copied = []
        for index, source in enumerate(items):
            windows = []
            for comp_index, comp in enumerate(source.comps, start=1):
                window = dict(comp.media_in()._inputs)
                if (key, index, comp_index) in set(drift):
                    window["GlobalIn"] = int(window["GlobalIn"]) + 1
                    window["GlobalOut"] = int(window["GlobalOut"]) + 1
                windows.append(window)
            copied.append(item(
                source.GetMediaPoolItem(), source.GetStart(),
                source.GetDuration(), source.GetLeftOffset(),
                properties=copy.deepcopy(source.properties),
                nodes=source.nodes, name=source.GetName(),
                comp_windows=windows))
        rows[key] = copied
    project = timeline._project
    current = project.GetCurrentTimeline()
    copy_of = reel_timeline(name, rows, project=project)
    project.SetCurrentTimeline(current)
    return copy_of
