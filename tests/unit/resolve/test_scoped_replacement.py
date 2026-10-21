"""AXI-007: a replacement changes EXACTLY the item it names.

The defect these tests name: `clip.replace` re-points the MEDIA POOL
ITEM the target draws from, and a media pool item is project-wide, so a
second occurrence of the same source changed with it - silently, and
without the caller asking for it.

The doubles below are a Resolve that behaves the way Resolve does: a
media pool item is shared by every timeline item that references it,
`ReplaceClip` re-points the POOL, and `AppendToTimeline` places a NEW
item with a NEW native id.

`library/tools/scoped_replacement.py` owns the rule; this file owns the
proof that the rule holds and that the paths it cannot carry are
refused BEFORE anything is written.
"""

from __future__ import annotations

import os

import pytest

from library.tools import scoped_replacement


# ── The doubles ───────────────────────────────────────────────────────


class FakePoolItem:
    def __init__(self, uid: str, path: str):
        self.uid = uid
        self.path = path
        self.replaced: list[str] = []

    def GetUniqueId(self):
        return self.uid

    def GetClipProperty(self, name):
        return self.path if name == "File Path" else ""

    def ReplaceClip(self, path):
        self.replaced.append(path)
        self.path = path
        return True


class FakeGraph:
    def __init__(self, nodes: int = 1):
        self.nodes = nodes
        self.luts: dict[int, str] = {}

    def GetNumNodes(self):
        return self.nodes

    def GetLUT(self, node):
        return self.luts.get(int(node), "")

    def SetLUT(self, node, path):
        self.luts[int(node)] = path
        return True


class FakeItem:
    def __init__(self, world, uid, pool_item, *, track_type="video",
                 track_index=1, record_in=0, duration=48, source_in=0,
                 properties=None, graph=None, cdl=None):
        self.world = world
        self.uid = uid
        self.pool_item = pool_item
        self.track_type = track_type
        self.track_index = track_index
        self.record_in = record_in
        self.duration = duration
        self.source_in = source_in
        self.properties = dict(properties or {"ZoomX": 1.0, "Pan": 0.0})
        self.graph = graph if graph is not None else FakeGraph()
        self.cdl = dict(cdl or {"Slope": "1.0000 1.0000 1.0000",
                                "Offset": "0.0000 0.0000 0.0000",
                                "Power": "1.0000 1.0000 1.0000",
                                "Saturation": "1.0000"})
        self.enabled = True
        self.clip_color = ""
        self.flags: list[str] = []
        self.markers: dict = {}
        self.linked: list = []
        self.comps: list[str] = []
        self.comp_count = 0
        self.name = uid

    # identity
    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.name

    def SetName(self, name):
        self.name = name
        return True

    def GetMediaPoolItem(self):
        return self.pool_item

    # spans
    def GetStart(self):
        return self.record_in

    def GetEnd(self):
        # Resolve's GetEnd and GetSourceEndFrame read the EXCLUSIVE end
        # of the window (measured live, 2026-10-21).
        return self.record_in + self.duration

    def GetDuration(self):
        return self.duration

    def GetSourceStartFrame(self):
        return self.source_in

    def GetSourceEndFrame(self):
        return self.source_in + self.duration

    def GetTrackTypeAndIndex(self):
        return self.track_type, self.track_index

    # picture
    def GetProperty(self):
        return dict(self.properties)

    def SetProperty(self, key, value):
        self.properties[key] = value
        return True

    # metadata
    def GetClipEnabled(self):
        return self.enabled

    def SetClipEnabled(self, value):
        self.enabled = bool(value)
        return True

    def GetClipColor(self):
        return self.clip_color

    def SetClipColor(self, color):
        self.clip_color = color
        return True

    def ClearClipColor(self):
        self.clip_color = ""
        return True

    def GetFlagList(self):
        return list(self.flags)

    def AddFlag(self, flag):
        self.flags.append(flag)
        return True

    def ClearFlags(self, _which):
        self.flags = []
        return True

    def GetMarkers(self):
        return dict(self.markers)

    def AddMarker(self, frame, color, name, note, duration, custom):
        self.markers[int(frame)] = {"color": color, "name": name,
                                    "note": note, "duration": duration,
                                    "customData": custom}
        return True

    def GetLinkedItems(self):
        return list(self.linked)

    # fusion
    def GetFusionCompCount(self):
        return self.comp_count

    def ExportFusionComp(self, path, index):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"comp-{index}")
        self.comps.append(path)
        return True

    def ImportFusionComp(self, path):
        self.comps.append(path)
        return True

    # colour
    def GetNodeGraph(self):
        return self.graph

    def GetCDL(self):
        return dict(self.cdl)

    def SetCDL(self, payload):
        self.cdl = {key: value for key, value in payload.items()
                    if key != "NodeIndex"}
        return True


class FakeTimeline:
    def __init__(self, name="scratch"):
        self.name = name
        self.rows: dict[tuple[str, int], list[FakeItem]] = {}
        self.linked_pairs: list = []

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return f"tl-{self.name}"

    def GetProject(self):
        return None

    def GetTrackCount(self, kind):
        indexes = {index for row_kind, index in self.rows
                   if row_kind == kind}
        return len(indexes)

    def GetItemListInTrack(self, kind, index):
        return list(self.rows.get((kind, int(index)), ()))

    def DeleteClips(self, items, ripple):
        removed = []
        for item in items:
            for key, row in self.rows.items():
                if item in row:
                    row.remove(item)
                    removed.append(item)
        return bool(removed)

    def SetClipsLinked(self, items, linked):
        self.linked_pairs.append(list(items))
        for item in items:
            item.linked = [other for other in items if other is not item]
        return True


class FakeMediaPool:
    def __init__(self, world):
        self.world = world
        self.imported: list[str] = []

    def GetRootFolder(self):
        return self.world.root_folder

    def ImportMedia(self, paths):
        self.imported.extend(paths)
        items = []
        for path in paths:
            pool_item = FakePoolItem(f"pool-{len(self.world.pool_items) + 1}",
                                     path)
            self.world.pool_items.append(pool_item)
            items.append(pool_item)
        return items

    def AppendToTimeline(self, payloads):
        placed = []
        for payload in payloads:
            pool_item = payload["mediaPoolItem"]
            start = int(payload["startFrame"])
            end = int(payload["endFrame"])
            duration = end - start
            kind = "video" if int(payload.get("mediaType", 1)) == 1 else "audio"
            item = FakeItem(
                self.world, f"item-{len(self.world.items) + 1}", pool_item,
                track_type=kind, track_index=int(payload["trackIndex"]),
                record_in=int(payload["recordFrame"]), duration=duration,
                source_in=start)
            self.world.items.append(item)
            self.world.timeline_obj.rows.setdefault(
                (kind, int(payload["trackIndex"])), []).append(item)
            placed.append(item)
        return placed


class FakeRootFolder:
    def __init__(self, clips):
        self.clips = clips

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return []


class FakeProject:
    def __init__(self, world):
        self.world = world

    def GetName(self):
        return "Ren Scratch - test"

    def GetTimelineCount(self):
        return len(self.world.timelines)

    def GetTimelineByIndex(self, index):
        return self.world.timelines[int(index) - 1]

    def GetMediaPool(self):
        return self.world.media_pool


class FakeWorld:
    def __init__(self):
        self.items: list[FakeItem] = []
        self.pool_items: list[FakePoolItem] = []
        self.timelines: list[FakeTimeline] = []
        self.timeline_obj: FakeTimeline | None = None
        self.media_pool = FakeMediaPool(self)
        self.root_folder = FakeRootFolder(self.pool_items)

    def timeline(self, name="scratch"):
        timeline = FakeTimeline(name)
        self.timelines.append(timeline)
        self.timeline_obj = timeline
        return timeline

    def project(self):
        return FakeProject(self)


@pytest.fixture
def world():
    return FakeWorld()


def _context(tmp_path):
    """The transform write log's context: a project folder to write to."""
    return {"project": "Ren Scratch - test", "project_folder": str(tmp_path),
            "timeline_name": "scratch"}


# ── The tests ─────────────────────────────────────────────────────────


def test_a_replacement_onto_a_shared_pool_item_leaves_the_other_use_alone(
        world, tmp_path):
    """The defect: the other occurrence of the same source changed too.

    Two timeline items draw from ONE media pool item.  Replacing the
    source of one of them must not touch the other - the other still
    plays what it played.
    """
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=0)
    other = FakeItem(world, "other", pool_item, record_in=100)
    timeline.rows[("video", 1)] = [target, other]
    world.pool_items.append(pool_item)
    world.pool_items.append(FakePoolItem("pool-2", "/media/b.mov"))
    project = world.project()
    world.root_folder.clips = world.pool_items

    receipt = scoped_replacement.replace_source(
        timeline=timeline, project=project, item=target,
        source_file="/media/b.mov", capture_dir=str(tmp_path),
        media_pool=world.media_pool, write_context=_context(tmp_path))

    assert receipt["route"] == "scoped_re_place"
    assert receipt["unrelated_uses"], "the other use was not measured"
    # The other occurrence still plays the ORIGINAL media.
    assert other.pool_item.path == "/media/a.mov"
    assert target.pool_item.path == "/media/a.mov"
    # The target plays the new media.
    placed = [item for item in world.items if item is not target
              and item is not other]
    assert len(placed) == 1
    assert placed[0].pool_item.path == "/media/b.mov"


def test_a_replacement_onto_an_unshared_pool_item_re_points_the_pool(
        world, tmp_path):
    """Nothing else can see the pool item, so the pool item is re-pointed.

    This is the route that keeps Resolve's own behaviour: the item's
    Fusion compositions and its grade stay on the item, because the
    item is never replaced.
    """
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=0)
    timeline.rows[("video", 1)] = [target]
    world.pool_items.append(pool_item)
    world.pool_items.append(FakePoolItem("pool-2", "/media/b.mov"))
    project = world.project()
    world.root_folder.clips = world.pool_items

    receipt = scoped_replacement.replace_source(
        timeline=timeline, project=project, item=target,
        source_file="/media/b.mov", capture_dir=str(tmp_path),
        media_pool=world.media_pool, write_context=_context(tmp_path))

    assert receipt["route"] == "pool_item_replace"
    assert pool_item.path == "/media/b.mov"
    assert pool_item.replaced == ["/media/b.mov"]
    assert receipt["unrelated_uses"] == []


def test_the_receipt_maps_the_old_id_to_the_new_id(world, tmp_path):
    """Resolve gives a re-placed item a NEW id, and the receipt says so.

    The id mapping is what lets a caller find the item again: the old
    id is gone, and a request that still names it would address
    nothing.
    """
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=0)
    other = FakeItem(world, "other", pool_item, record_in=100)
    timeline.rows[("video", 1)] = [target, other]
    world.pool_items.append(pool_item)
    world.pool_items.append(FakePoolItem("pool-2", "/media/b.mov"))
    project = world.project()
    world.root_folder.clips = world.pool_items

    receipt = scoped_replacement.replace_source(
        timeline=timeline, project=project, item=target,
        source_file="/media/b.mov", capture_dir=str(tmp_path),
        media_pool=world.media_pool, write_context=_context(tmp_path))

    assert receipt["previous_unique_id"] == "target"
    assert receipt["unique_id"] != "target"
    assert receipt["id_map"] == {"target": receipt["unique_id"]}


def test_the_replaced_item_keeps_its_transform_timing_and_link(
        world, tmp_path):
    """The state the item carried is carried onto the item that replaced it.

    A replacement that dropped the transform or the audio link would
    change the picture, and the change would be invisible in the
    receipt.
    """
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=24,
                      properties={"ZoomX": 1.4, "Pan": 12.0})
    twin = FakeItem(world, "twin", pool_item, track_type="audio",
                    track_index=1, record_in=24)
    target.linked = [twin]
    other = FakeItem(world, "other", pool_item, record_in=100)
    timeline.rows[("video", 1)] = [target, other]
    timeline.rows[("audio", 1)] = [twin]
    world.pool_items.append(pool_item)
    world.pool_items.append(FakePoolItem("pool-2", "/media/b.mov"))
    project = world.project()
    world.root_folder.clips = world.pool_items

    receipt = scoped_replacement.replace_source(
        timeline=timeline, project=project, item=target,
        source_file="/media/b.mov", capture_dir=str(tmp_path),
        media_pool=world.media_pool, write_context=_context(tmp_path))

    placed = [item for item in world.items
              if item.uid not in {"target", "other", "twin"}]
    assert len(placed) == 1
    new_item = placed[0]
    assert new_item.properties["ZoomX"] == 1.4
    assert new_item.properties["Pan"] == 12.0
    assert (new_item.GetStart(), new_item.GetEnd()) == (24, 72)
    assert (new_item.GetSourceStartFrame(),
            new_item.GetSourceEndFrame()) == (0, 48)
    assert receipt["preserved"]
    assert timeline.linked_pairs, "the audio link was not re-made"


def test_a_grade_of_more_than_one_node_is_refused_before_any_write(
        world, tmp_path):
    """A multi-node grade cannot be rebuilt on a re-placed item.

    Resolve's Graph API has no AddNode, so a re-placed item comes back
    with one node.  The refusal happens BEFORE the original is
    removed: the timeline is exactly as it was.
    """
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=0,
                      graph=FakeGraph(nodes=3))
    other = FakeItem(world, "other", pool_item, record_in=100)
    timeline.rows[("video", 1)] = [target, other]
    world.pool_items.append(pool_item)
    world.pool_items.append(FakePoolItem("pool-2", "/media/b.mov"))
    project = world.project()
    world.root_folder.clips = world.pool_items

    with pytest.raises(scoped_replacement.ScopedReplacementError) as caught:
        scoped_replacement.replace_source(
            timeline=timeline, project=project, item=target,
            source_file="/media/b.mov", capture_dir=str(tmp_path),
            media_pool=world.media_pool, write_context=_context(tmp_path))

    assert caught.value.reason == "uncarrable_grade"
    assert timeline.rows[("video", 1)] == [target, other]
    assert pool_item.path == "/media/a.mov"


def test_a_failed_placement_puts_the_original_back(world, tmp_path):
    """A failure after the removal leaves the timeline as it was."""
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=0)
    other = FakeItem(world, "other", pool_item, record_in=100)
    timeline.rows[("video", 1)] = [target, other]
    world.pool_items.append(pool_item)
    world.pool_items.append(FakePoolItem("pool-2", "/media/b.mov"))
    project = world.project()
    world.root_folder.clips = world.pool_items

    media_pool = world.media_pool
    original_append = media_pool.AppendToTimeline

    def failing_append(payloads):
        # The REPLACEMENT media cannot be placed; the original's own
        # media can, so the recovery has something to put back.
        for payload in payloads:
            if payload["mediaPoolItem"].path == "/media/b.mov":
                return []
        return original_append(payloads)

    media_pool.AppendToTimeline = failing_append
    with pytest.raises(scoped_replacement.ScopedReplacementError) as caught:
        scoped_replacement.replace_source(
            timeline=timeline, project=project, item=target,
            source_file="/media/b.mov", capture_dir=str(tmp_path),
            media_pool=media_pool, write_context=_context(tmp_path))

    assert caught.value.reason == "placement_failed"
    rows = timeline.rows[("video", 1)]
    assert len(rows) == 2, "the original was not put back"
    assert any(item.pool_item.path == "/media/a.mov" for item in rows)
    assert pool_item.path == "/media/a.mov"


def test_a_move_carries_the_state_and_maps_the_id(world, tmp_path):
    """A move is a re-place: the state rides across and the id changes."""
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    timeline = world.timeline()
    target = FakeItem(world, "target", pool_item, record_in=0,
                      properties={"ZoomX": 1.25})
    timeline.rows[("video", 1)] = [target]
    world.pool_items.append(pool_item)

    receipt = scoped_replacement.move_item(
        timeline=timeline, item=target, to_frame=240,
        capture_dir=str(tmp_path), media_pool=world.media_pool,
        write_context=_context(tmp_path))

    moved = [item for item in world.items if item.uid != "target"]
    assert len(moved) == 1
    assert moved[0].GetStart() == 240
    assert moved[0].properties["ZoomX"] == 1.25
    assert receipt["id_map"] == {"target": moved[0].uid}
    assert receipt["record_in"] == 240


def test_the_editorial_selector_survives_a_re_place(world):
    """The selector that finds the item again is not the native id."""
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    item = FakeItem(world, "target", pool_item, record_in=0, source_in=12)

    selector = scoped_replacement.editorial_selector(item)

    assert selector["source_file"] == "/media/a.mov"
    assert selector["source_in_frame"] == 12
    assert selector["source_out_frame"] == 60
    assert selector["resolve_item_id"] == "target"


def test_the_capture_directory_holds_the_exported_composition(
        world, tmp_path):
    """A composition that is not on disk cannot be carried."""
    pool_item = FakePoolItem("pool-1", "/media/a.mov")
    item = FakeItem(world, "target", pool_item, record_in=0)
    item.comp_count = 1

    def export(path, index):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("comp")

    item.ExportFusionComp = export
    state = scoped_replacement.capture_state(item, capture_dir=str(tmp_path))

    assert state.comps
    assert os.path.exists(state.comps[0]["path"])
