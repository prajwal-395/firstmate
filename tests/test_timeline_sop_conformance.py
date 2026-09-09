"""The master builder obeys the SOP, and the verifier reads it back.

Defects covered here (fake Resolve, no live connection):
  2. two cameras mean TWO audio rows, one program stream each,
  3. a non-program stream is stopped at placement, never leaking on,
  4. every track created gets occupied (empties are deleted, not kept),
  5. a-roll picture links to its speech; a caption inside a speech span
     joins that link group in ONE call (linking is exclusive, not
     additive - a second pair-call would break the first),
  6. every row is named from the plan.

`library/tools/timeline_conformance.py` reads a built timeline back and
reports every SOP violation it finds. It is deterministic and it can
fail, so it is a real gate.
"""

import json
import sys
from unittest.mock import patch

import pytest

from library.steps.step_6_01_render.resolve_build_timeline import build_timeline
from library.tools.timeline_conformance import (
    CHECKS,
    verify_timeline,
)
from library.tools.timeline_layout import plan_layout


# ── Faithful fakes ─────────────────────────────────────────────
# The link fake emulates MEASURED Resolve semantics (2026-09-09):
# SetClipsLinked with three items forms one three-group; linking a pair
# afterwards BREAKS the group rather than adding to it.

class FakeItem:
    _ids = iter(range(100000, 1000000))

    def __init__(self, name, start, end, pool_path="", channel=1):
        self._name = name
        self._start = start
        self._end = end
        self._pool_path = pool_path
        self._channel = channel
        self._uid = f"item-{next(FakeItem._ids)}"
        self._group = {self._uid}

    def GetName(self): return self._name
    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetDuration(self): return self._end - self._start
    def GetUniqueId(self): return self._uid
    def GetSourceStartFrame(self): return 0
    def GetSourceEndFrame(self): return self._end - self._start
    def GetMediaPoolItem(self):
        class _P:
            def __init__(self, p): self._p = p
            def GetClipProperty(self, k):
                return self._p if k == "File Path" else ""
        return _P(self._pool_path)
    def GetLinkedItems(self):
        return [i for i in FakeTimeline._registry.values()
                if i._uid in self._group and i._uid != self._uid]
    def GetSourceAudioChannelMapping(self):
        return json.dumps({
            "embedded_audio_channels": 4, "linked_audio": {},
            "track_mapping": {"1": {"channel_idx": [self._channel],
                                    "mute": False, "type": "mono"}}})
    def SetProperty(self, *a): return True
    def SetCDL(self, *a): return True
    def GetProperty(self): return {}


class FakeTimeline:
    _registry = {}

    def __init__(self):
        self.tracks = {("video", 1): [], ("audio", 1): []}
        self.names = {("video", 1): "Video 1", ("audio", 1): "Audio 1"}
        self.link_calls = []
        self.deleted_items = []
        self.deleted_tracks = []

    def GetTrackCount(self, mt): return max(
        (i for (t, i) in self.tracks if t == mt), default=0)
    def AddTrack(self, mt, *a):
        n = self.GetTrackCount(mt) + 1
        self.tracks[(mt, n)] = []
        self.names[(mt, n)] = f"{mt.capitalize()} {n}"
        return True
    def DeleteTrack(self, mt, idx):
        self.deleted_tracks.append((mt, idx))
        self.tracks.pop((mt, idx), None)
        self.names.pop((mt, idx), None)
        return True
    def GetItemListInTrack(self, mt, idx):
        return list(self.tracks.get((mt, idx), []))
    def GetTrackName(self, mt, idx): return self.names.get((mt, idx), "")
    def SetTrackName(self, mt, idx, name):
        self.names[(mt, idx)] = name
        return True
    def SetClipsLinked(self, items, link):
        self.link_calls.append((list(items), link))
        if link:
            group = {i.GetUniqueId() for i in items}
            for i in items:
                i._group = set(group)
        else:
            for i in items:
                i._group = {i.GetUniqueId()}
        return True
    def DeleteClips(self, items, ripple=False):
        for it in items:
            self.deleted_items.append(it.GetUniqueId())
            for key, lst in self.tracks.items():
                if it in lst:
                    lst.remove(it)
        return True
    def GetUniqueId(self): return "fake-timeline"
    def GetName(self): return "Fake"
    def SetSetting(self, k, v): return True
    def GetSetting(self, k): return ""


class FakePoolItem:
    def __init__(self, path):
        self._path = path
    def GetName(self):
        return self._path.split("/")[-1]
    def GetClipProperty(self, k):
        if k == "File Path": return self._path
        if k == "FPS": return "23.976"
        return ""
    def SetClipProperty(self, *a): return True


class FakeFolder:
    def __init__(self, name="root"):
        self._name = name
        self.subs = []
        self.clips = []
    def GetName(self): return self._name
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class FakePool:
    def __init__(self, timeline, paths, channel=1):
        self.timeline = timeline
        self.root = FakeFolder()
        self.imported = []
        self._channel = channel
        self._items = {p: FakePoolItem(p) for p in paths}

    def GetRootFolder(self): return self.root
    def SetCurrentFolder(self, f): return True
    def AddSubFolder(self, parent, name):
        f = FakeFolder(name)
        parent.subs.append(f)
        return f
    def ImportMedia(self, paths):
        self.imported.extend(paths)
        return [self._items.get(p, FakePoolItem(p)) for p in paths]
    def CreateEmptyTimeline(self, name): return self.timeline
    def DeleteTimelines(self, tls): return True
    def DeleteClips(self, clips): return True
    def AppendToTimeline(self, clip_infos):
        out = []
        for info in clip_infos:
            item = info["mediaPoolItem"]
            path = item._path if isinstance(item, FakePoolItem) else ""
            start, end = info["startFrame"], info["endFrame"]
            rec = info["recordFrame"]
            dur = end - start
            placed = FakeItem(path.split("/")[-1], rec, rec + dur,
                              pool_path=path, channel=self._channel)
            FakeTimeline._registry[placed.GetUniqueId()] = placed
            self.timeline.tracks.setdefault(
                ("video" if info.get("mediaType", 1) == 1 else "audio",
                 info["trackIndex"]), []).append(placed)
            out.append(placed)
        return out


@pytest.fixture
def fake_world():
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    paths = ["/media/LC4930.MXF", "/media/LCATL0011.MXF",
             "/media/cap_akshita.mov"]
    pool = FakePool(timeline, paths)
    # Pool starts EMPTY so imports happen; pool items resolve by name.
    root = pool.root
    project = type("P", (), {})()
    project.GetMediaPool = lambda: pool
    project.GetTimelineCount = lambda: 0
    project.GetName = lambda: "FakeProject"
    project.SetCurrentTimeline = lambda tl: True
    project.GetCurrentTimeline = lambda: timeline
    _store = {}
    project.GetSetting = lambda k=None: dict(_store) if k is None else _store.get(str(k), "")
    def _set_setting(k, v):
        _store[str(k)] = str(v)
        return True
    project.SetSetting = _set_setting
    project.ApplyFairlightPresetToCurrentTimeline = lambda name: False
    resolve = type("R", (), {})()
    resolve.GetProjectManager = lambda: type("PM", (), {
        "GetCurrentProject": lambda self: project})()
    resolve.OpenPage = lambda page: True

    real_import = FakePool.ImportMedia
    def import_and_register(self, paths):
        items = real_import(self, paths)
        # Imported media lands in the pool and is findable afterwards.
        for it in items:
            root.clips.append(it)
        return items
    pool.ImportMedia = import_and_register.__get__(pool, FakePool)

    def find_or_register(path):
        for c in root.clips:
            if c.GetClipProperty("File Path") == path:
                return c
        it = pool._items.get(path)
        if it is None:
            it = FakePoolItem(path)
            pool._items[path] = it
        root.clips.append(it)
        return it
    pool._find = find_or_register
    return {"timeline": timeline, "pool": pool, "project": project,
            "resolve": resolve}


def _run_builder(fake_world, manifest, monkeypatch):
    import DaVinciResolveScript as _real  # noqa: F401  (rebound below)
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        type("M", (), {"scriptapp": staticmethod(
                            lambda name: fake_world["resolve"])})())
    # Media exists on disk for the import pass.
    monkeypatch.setattr("os.path.exists", lambda p: True)
    # Pool lookup must find the pre-registered items.
    from unittest.mock import MagicMock
    fusion_proc = MagicMock()
    fusion_proc.returncode = 0
    fusion_proc.stdout = ""
    fusion_proc.stderr = ""
    with patch("library.steps.step_6_01_render.resolve_build_timeline.subprocess.run",
               return_value=fusion_proc):
        return build_timeline(manifest)


def _two_angle_manifest():
    def clip(path, angle, tin):
        return {"source_file": path, "source_in": 0.0, "source_out": 2.0,
                "timeline_in_frame": tin, "timeline_out_frame": tin + 48,
                "angle": angle, "label": f"{angle}_1"}
    return {
        "project": {"name": "SOP", "resolution": [3840, 2160],
                    "frame_rate": 23.976, "duration_seconds": 10.0},
        "angles": [
            {"key": "akshita", "label": "Akshita",
             "speech_name": "Akshita CH1", "program_channel": 1},
            {"key": "craig", "label": "Craig",
             "speech_name": "Craig CH1", "program_channel": 1},
        ],
        "tracks": {"V1": {"clips": [
            clip("/media/LC4930.MXF", "akshita", 0),
            clip("/media/LCATL0011.MXF", "craig", 48),
        ]}},
    }


def test_two_angles_place_on_two_picture_rows_and_two_speech_rows(fake_world, monkeypatch):
    """Defect 2: two cameras mean TWO audio rows, each with its program stream."""
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    assert result["success"], result["errors"]

    tl = fake_world["timeline"]
    assert tl.GetTrackCount("video") == 2, "caption-less proof has no V3"
    assert tl.GetTrackCount("audio") == 2
    v1 = tl.GetItemListInTrack("video", 1)
    v2 = tl.GetItemListInTrack("video", 2)
    a1 = tl.GetItemListInTrack("audio", 1)
    a2 = tl.GetItemListInTrack("audio", 2)
    assert len(v1) == len(a1) == 1 and len(v2) == len(a2) == 1
    assert "LC4930" in v1[0].GetName() and "LCATL" in v2[0].GetName()
    # Pairs are linked picture-to-speech.
    pair_calls = [c for c in tl.link_calls if len(c[0]) == 2 and c[1]]
    assert len(pair_calls) == 2
    linked_names = sorted(n for c in pair_calls for n in
                          (c[0][0].GetName(), c[0][1].GetName()))
    assert linked_names == sorted([v1[0].GetName(), a1[0].GetName(),
                                   v2[0].GetName(), a2[0].GetName()])


def test_every_row_is_named_from_the_plan(fake_world, monkeypatch):
    """Defect 6: no 'Video 1' / 'Audio 1' rows survive the build."""
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    assert result["success"], result["errors"]
    tl = fake_world["timeline"]
    assert tl.GetTrackName("video", 1) == "Akshita"
    assert tl.GetTrackName("video", 2) == "Craig"
    assert tl.GetTrackName("audio", 1) == "Akshita CH1"
    assert tl.GetTrackName("audio", 2) == "Craig CH1"


def test_caption_inside_speech_joins_one_three_group(fake_world, monkeypatch):
    """Defect 5 (captions): the caption, its speech AND its picture link
    in a single call - a later pair-call would break the group."""
    manifest = _two_angle_manifest()
    manifest["subtitle_overlay"] = {"segments": [{
        "overlay_path": "/media/cap_akshita.mov",
        "timeline_start": 0.5, "timeline_end": 1.5,
        "total_frames": 24,
    }]}
    result = _run_builder(fake_world, manifest, monkeypatch)
    assert result["success"], result["errors"]

    tl = fake_world["timeline"]
    assert tl.GetTrackName("video", 3) == "Subtitles"
    triples = [c for c in tl.link_calls if len(c[0]) == 3 and c[1]]
    assert len(triples) == 1, (
        f"expected one three-group link call, saw {tl.link_calls}")
    members = triples[0][0]
    kinds = sorted((m.GetStart(), m.GetEnd()) for m in members)
    assert len(kinds) == 3
    # The group holds: nothing was re-linked afterwards to break it.
    cap = tl.GetItemListInTrack("video", 3)[0]
    assert len(cap.GetLinkedItems()) == 2


def test_non_program_stream_is_deleted_not_placed(fake_world, monkeypatch):
    """Defect 3 at placement: a stray item carrying the wrong channel is
    removed from the timeline, and the build says so."""
    fake_world["pool"]._channel = 2  # Resolve hands back CH2, program is CH1
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    tl = fake_world["timeline"]
    assert tl.deleted_items, "stray stream items must be deleted"
    assert result["stream_enforcement"]["deleted"], \
        "the build must record what it removed"
    assert tl.GetItemListInTrack("audio", 1) == []
    assert tl.GetItemListInTrack("audio", 2) == []


def test_verifier_reads_back_a_clean_build(fake_world, monkeypatch):
    """The verifier passes the timeline the conformed builder just made."""
    result = _run_builder(fake_world, _two_angle_manifest(), monkeypatch)
    assert result["success"], result["errors"]
    plan = plan_layout(result["track_plan"]["material"])
    report = verify_timeline(fake_world["timeline"], plan=plan)
    assert report["passed"], report["violations"]
    assert report["checks_skipped"] == []


def test_verifier_flags_every_sop_violation():
    """Each of defects 2-6, as the verifier sees it on a bad timeline."""
    FakeTimeline._registry = {}
    tl = FakeTimeline()
    # V1 a-roll picture, unlinked; A1 its speech, unlinked AND carrying
    # the wrong stream. V2 is the plan's caption row with a caption
    # inside the speech span, unlinked. V3 is an unplanned blank row
    # with a default name; A2 repeats A1's role name.
    pic = FakeItem("LC4930.MXF", 0, 100, pool_path="/media/LC4930.MXF")
    speech = FakeItem("LC4930.MXF", 0, 100, pool_path="/media/LC4930.MXF",
                      channel=3)
    cap = FakeItem("cap.mov", 10, 50, pool_path="/media/cap.mov")
    dup = FakeItem("LC4930.MXF", 0, 100, pool_path="/media/LC4930.MXF")
    for it in (pic, speech, cap, dup):
        FakeTimeline._registry[it.GetUniqueId()] = it
    tl.tracks = {("video", 1): [pic], ("video", 2): [cap],
                 ("video", 3): [], ("audio", 1): [speech],
                 ("audio", 2): [dup]}
    tl.names = {("video", 1): "Akshita", ("video", 2): "Subtitles",
                ("video", 3): "Video 3", ("audio", 1): "Akshita CH1",
                ("audio", 2): "Akshita CH1"}

    plan = plan_layout({
        "angles": [{"key": "akshita", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False,
        "caption_spans": [(0, 200)],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    report = verify_timeline(tl, plan=plan)
    assert not report["passed"]
    by_check = {v["check"] for v in report["violations"]}
    assert "empty_track" in by_check      # defect 4: V2
    assert "unnamed_track" in by_check    # defect 6: "Video 2"
    assert "duplicate_role" in by_check   # two rows named "Akshita CH1"
    assert "aroll_unlinked" in by_check   # defect 5: picture+speech
    assert "caption_unlinked" in by_check  # defect 5: caption in span
    assert "program_stream" in by_check   # defects 2/3: CH3 placed, CH1 due
    assert set(CHECKS) <= by_check | set(report["checks_run"])


def test_verifier_without_a_plan_runs_structure_only_and_says_so():
    FakeTimeline._registry = {}
    tl = FakeTimeline()
    report = verify_timeline(tl)
    assert "aroll_linked" in report["checks_skipped"]
    assert "program_stream" in report["checks_skipped"]
    assert "no_empty_tracks" in report["checks_run"]
