"""A fake Resolve that reproduces the behaviour a composed edit defends against.

Not a mock: every defect the spike measured against Resolve Studio 21.1
is modelled here, so a test that passes against this fake is testing the
defence and not the absence of one.

  - `AppendToTimeline` REFUSES to place over a live item, returns a
    truthy list containing a live-looking handle, and places nothing.
  - A placed item comes back at IDENTITY: no Fusion comp, one colour
    node, every transform property at its default.
  - `ImportFusionComp` re-binds `MediaIn` to the whole pool clip and
    throws the played window away.
  - `SetProperty` on `AnchorPointX` returns `False` and sets the value.
  - `SetInput` returns `None` whether it took or not.
  - `GetEnd()` is EXCLUSIVE, and `endFrame` in an append info is too.

Nothing here reaches a real project or a real Resolve
(`tests/test_tests_never_reach_real_projects.py`).
"""

from __future__ import annotations

import copy
import os

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


#: The four terms that say WHICH FRAMES a `MediaIn` reads, and the three
#: that say what it is bound to. Mirrors `composed_edit`'s split.
FRAME_TERMS = ("GlobalIn", "GlobalOut", "ClipTimeStart", "ClipTimeEnd")
BINDING_TERMS = ("MediaSource", "MediaID", "AudioTrack")


class FakeTool:
    """A Fusion tool. `SetInput` returns None whether it took or not.

    `derived` reproduces the behaviour measured on Resolve Studio 21.1
    (`composed_edit.apply_window`): with `MediaSource` set back to
    `Timeline` the window is RECOMPUTED from the item and is already
    correct, and writing the four frame terms on top of a correct window
    moves `GlobalIn` one frame later and `ClipTimeEnd` one frame
    earlier. Every write returns `None` either way.
    """

    def __init__(self, reg_id="MediaIn", inputs=None, inert=False,
                 derived=None):
        self._reg_id = reg_id
        self._inputs = dict(inputs or {})
        #: An inert tool accepts every write and changes nothing - the
        #: shape `comp_media_window` measured, where `SetInput` cannot
        #: pull a window back.
        self.inert = inert
        #: The window the timeline binding derives, when it derives one.
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
            # Writing a frame term onto a window the binding already
            # derived corrupts it, by exactly one frame.
            if key == "GlobalIn":
                self._inputs[key] = value + 1
            elif key == "ClipTimeEnd":
                self._inputs[key] = value - 1
        return None


class FakeComp:
    def __init__(self, tools=None):
        self.tools = dict(tools or {})

    def GetToolList(self, _selected=False):
        return dict(self.tools)


class FakeMediaPoolItem:
    def __init__(self, path, frames=10_000, fps="30.0", name=""):
        self.path = path
        self.frames = frames
        self.fps = fps
        self.name = name or os.path.basename(path)

    def GetClipProperty(self, key):
        return {"File Path": self.path, "Clip Path": self.path,
                "Frames": str(self.frames), "FPS": self.fps,
                "Resolution": "1080x1920"}.get(key, "")

    def GetMediaId(self):
        return f"mpi-{self.name}"

    def GetUniqueId(self):
        return f"mpi-{self.name}"

    def GetName(self):
        return self.name


class FakeItem:
    """One timeline item. `GetEnd()` is EXCLUSIVE, as Resolve's is."""

    _next_id = [0]

    def __init__(self, mpi, start, duration, left_offset=0,
                 properties=None, comps=None, nodes=1, name=None,
                 comp_windows=None, inert_media_in=False):
        FakeItem._next_id[0] += 1
        self.uid = f"item-{FakeItem._next_id[0]}"
        self.mpi = mpi
        self.start = int(start)
        self.duration = int(duration)
        self.left_offset = int(left_offset)
        self.properties = dict(properties or IDENTITY_PROPERTIES)
        self.nodes = int(nodes)
        self.name = name or mpi.name
        self.linked = False
        self.inert_media_in = inert_media_in
        self.comps = []
        for window in (comp_windows or []):
            self.comps.append(FakeComp({
                "MediaIn1": FakeTool("MediaIn", window,
                                     inert=inert_media_in),
                "Transform1": FakeTool("Transform", {"Size": 1.0})}))
        if comps:
            self.comps = list(comps)

    # ── geometry ──
    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.duration

    def GetDuration(self):
        return self.duration

    def GetLeftOffset(self):
        return self.left_offset

    def GetRightOffset(self):
        return max(0, self.mpi.frames - self.left_offset - self.duration)

    def GetSourceStartFrame(self):
        return self.left_offset

    def GetSourceEndFrame(self):
        return self.left_offset + self.duration - 1

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetMediaPoolItem(self):
        return self.mpi

    def GetClipColor(self):
        return ""

    def GetClipEnabled(self):
        return True

    def GetFlagList(self):
        return []

    def GetMarkers(self):
        return {}

    def GetCDL(self):
        return {}

    def GetColorGroup(self):
        return ""

    # ── transform ──
    def GetProperty(self, key=None):
        if key is None:
            return dict(self.properties)
        return self.properties.get(key)

    def SetProperty(self, key, value):
        self.properties[key] = value
        # Measured: this pair returns False and sets the value anyway.
        return key not in ("AnchorPointX", "AnchorPointY")

    # ── grade ──
    def GetNumNodes(self):
        return self.nodes

    def CopyGrades(self, targets):
        for target in targets:
            target.nodes = self.nodes
        return True

    # ── Fusion ──
    def GetFusionCompCount(self):
        return len(self.comps)

    def GetFusionCompNameList(self):
        return [f"Comp {i + 1}" for i in range(len(self.comps))]

    def GetFusionCompByIndex(self, index):
        if 1 <= index <= len(self.comps):
            return self.comps[index - 1]
        return None

    def DeleteFusionCompByName(self, name):
        index = int(str(name).split()[-1]) - 1
        if 0 <= index < len(self.comps):
            self.comps.pop(index)
            return True
        return False

    def ExportFusionComp(self, path, index):
        comp = self.GetFusionCompByIndex(index)
        if comp is None:
            return False
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        window = comp.GetToolList()["MediaIn1"]._inputs
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(repr({"window": window, "keys": self.duration}))
        return True

    def ImportFusionComp(self, path):
        """Measured: re-binds MediaIn to the POOL clip, window discarded."""
        with open(path, "r", encoding="utf-8") as handle:
            payload = eval(handle.read())  # noqa: S307 - our own file
        rebound = {"MediaSource": "MediaPool", "MediaID": self.mpi.GetMediaId(),
                   "AudioTrack": "No_Audo_Track", "GlobalIn": 0,
                   "GlobalOut": self.mpi.frames - 1, "ClipTimeStart": 0,
                   "ClipTimeEnd": self.mpi.frames - 1}
        self.comps = [FakeComp({
            "MediaIn1": FakeTool("MediaIn", rebound,
                                 inert=self.inert_media_in),
            "Transform1": FakeTool("Transform",
                                   {"Size": 1.0, "_keys": payload["keys"]})})]
        return True


class FakeTimeline:
    def __init__(self, name, rows, track_names=None):
        """`rows` is `{"V1": [FakeItem, ...], "A1": [...]}`."""
        self.name = name
        self.rows = {key: list(items) for key, items in rows.items()}
        self.track_names = dict(track_names or {})
        self.delete_calls = []
        self.link_calls = []

    # ── the row protocol `reel_read.live_items` needs ──
    def GetName(self):
        return self.name

    def GetTrackCount(self, track_type):
        prefix = "V" if track_type == "video" else "A"
        indexes = [int(key[1:]) for key in self.rows if key.startswith(prefix)]
        return max(indexes) if indexes else 0

    def GetTrackName(self, track_type, index):
        prefix = "V" if track_type == "video" else "A"
        return self.track_names.get(f"{prefix}{index}", f"{prefix}{index}")

    def GetItemListInTrack(self, track_type, index):
        prefix = "V" if track_type == "video" else "A"
        return list(self.rows.get(f"{prefix}{index}", []))

    def GetSetting(self, _key):
        return "30"

    # ── mutation ──
    def DeleteClips(self, items, _ripple=False):
        self.delete_calls.append(len(items))
        wanted = {id(item) for item in items}
        for key, row in self.rows.items():
            self.rows[key] = [i for i in row if id(i) not in wanted]
        return True

    def SetClipsLinked(self, items, linked):
        self.link_calls.append(len(items))
        for item in items:
            item.linked = bool(linked)
        return True


class FakeMediaPool:
    """`AppendToTimeline` with Resolve's measured collision behaviour."""

    def __init__(self, timeline):
        self.timeline = timeline
        self.calls = []

    def AppendToTimeline(self, infos):
        self.calls.append(len(infos))
        returned = []
        for info in infos:
            prefix = "V" if info.get("mediaType", 1) == 1 else "A"
            key = f"{prefix}{info['trackIndex']}"
            start = int(info["recordFrame"])
            duration = int(info["endFrame"]) - int(info["startFrame"])
            row = self.timeline.rows.setdefault(key, [])
            collides = any(not (start + duration <= i.GetStart()
                                or i.GetEnd() <= start) for i in row)
            placed = FakeItem(info["mediaPoolItem"], start, duration,
                              int(info["startFrame"]))
            # THE defect: on a collision the API returns a truthy,
            # live-looking handle and places NOTHING.
            returned.append(placed)
            if collides:
                continue
            row.append(placed)
            row.sort(key=lambda item: item.GetStart())
        return returned


def build_reel(tmp_path, *, head_duration=479, head_comp=True):
    """A small reel with the shape the spike's Reel 01 has.

    V1: three A-roll clips.  V2: one clip that predates the cut and must
    NOT move.  V4: three caption cards after the cut - the row the prior
    spike left out of its plan and measured as "caption desync".  A1:
    two audio items linked to V1.
    """
    aroll = FakeMediaPoolItem("/lab/LC4930.MXF", frames=5400)
    broll = FakeMediaPoolItem("/lab/opener.mov", frames=2000)
    card = FakeMediaPoolItem("/lab/card.mov", frames=200)
    tone = FakeMediaPoolItem("/lab/LC4930.MXF", frames=5400, name="audio")
    # The freeze hold is exactly as long as the file: left_offset 0,
    # right_offset 0, no source to trim into. Lengthening it is a file
    # operation, so the plan must REFUSE it rather than place a hole.
    freeze = FakeMediaPoolItem("/lab/reel_freeze.mov", frames=19,
                               name="freeze")
    overlay = FakeMediaPoolItem("/lab/tv_frame.mov", frames=1675,
                                name="tv_frame")

    def picture(start, duration, left, comps):
        return FakeItem(
            aroll, start, duration, left,
            properties={**IDENTITY_PROPERTIES, "ZoomX": 2.307,
                        "Pan": 5.972, "Tilt": 1.0},
            nodes=8,
            comp_windows=[covering_window(duration, left, aroll.frames)]
            if comps else [])

    rows = {
        "V1": [picture(590, head_duration, 3151, head_comp),
               picture(590 + head_duration, 186, 1200, True),
               # The freeze inherits the transform of the shot in front
               # of it, as the build gives it one.
               FakeItem(freeze, 590 + head_duration + 186, 19, 0, nodes=8,
                        properties={**IDENTITY_PROPERTIES, "ZoomX": 2.307,
                                    "Pan": 99.656, "Tilt": 1.0},
                        comp_windows=[covering_window(19, 0, 19)])],
        "V2": [FakeItem(broll, 0, 590, 0, nodes=8,
                        comp_windows=[covering_window(590, 0, 2000)])],
        # The overlay that spans the whole reel. It ends where the reel
        # ends, so an ending change must carry it out - the spike's
        # `tv_frame`, which had 401 frames of source headroom.
        # An overlay row carries a rendered artefact and a transform,
        # NOT a builder-generated per-clip comp: the pass writes comps on
        # V1 and V2 only (`execution/fusion_tracks.py`).
        "V3": [FakeItem(overlay, 0, 590 + head_duration + 186 + 19, 0,
                        nodes=1)],
        "V4": [FakeItem(card, 590 + head_duration + 10, 40, 0, nodes=1),
               FakeItem(card, 590 + head_duration + 60, 40, 0, nodes=1),
               FakeItem(card, 590 + head_duration + 110, 40, 0, nodes=1)],
        "A1": [FakeItem(tone, 590, head_duration, 3151),
               FakeItem(tone, 590 + head_duration, 186, 1200)],
    }
    timeline = FakeTimeline("LAB staged", rows)
    return (timeline, FakeMediaPool(timeline),
            {"aroll": aroll, "card": card, "freeze": freeze,
             "broll": broll, "tone": tone, "overlay": overlay})


def duplicate(timeline, name="LAB control", drift=()):
    """A copy of a timeline. `drift` names windows the copy normalises.

    `DuplicateTimeline` was measured to alter a comp's media window with
    nothing else in the readable structure differing, which is why step
    1 conforms a staging copy before it is trusted.
    """
    rows = {}
    for key, items in timeline.rows.items():
        copied = []
        for index, item in enumerate(items):
            windows = []
            for comp_index, comp in enumerate(item.comps, start=1):
                window = dict(comp.GetToolList()["MediaIn1"]._inputs)
                if (key, index, comp_index) in set(drift):
                    window["GlobalIn"] = int(window["GlobalIn"]) + 1
                    window["GlobalOut"] = int(window["GlobalOut"]) + 1
                windows.append(window)
            copied.append(FakeItem(
                item.mpi, item.start, item.duration, item.left_offset,
                properties=copy.deepcopy(item.properties), nodes=item.nodes,
                name=item.name, comp_windows=windows))
        rows[key] = copied
    return FakeTimeline(name, rows)
