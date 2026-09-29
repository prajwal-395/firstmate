"""The reel builder obeys the timeline SOP, and the verifier reads it back.

Defects covered here (fake Resolve, no live connection):
  1. two speakers collapsed onto one video row with mixed audio rows -
     a-roll gets one video row per angle and speech one audio row per
     angle, named from the master's own rows;
  2. the MXF program stream never explicitly selected - exactly one
     recorded program stream per source reaches the timeline, and a
     stray is deleted on the spot and recorded;
  3. nothing linked - picture links to its speech, and a caption whose
     span falls inside a speech span joins that group in ONE call
     (linking is exclusive, not additive);
  4. unnamed and empty rows - every row is named from the plan, and a
     row whose placements all fail is deleted, never kept blank.

`library/tools/timeline_conformance.py` reads a built timeline back
against the plan the build recorded. It is deterministic and it can
fail, so it is a real gate.
"""

import json

import pytest

from library.tools.reel_build import (
    ReelBuildError,
    build_reel_timeline,
    link_reel_groups,
    reel_angles,
    resolve_reel_program_channels,
)
from library.tools.timeline_conformance import (
    CHECKS,
    verify_timeline,
)
from library.tools.timeline_ingest import TimelineClip
from library.tools.timeline_layout import plan_layout
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN


# ── Faithful fakes ─────────────────────────────────────────────
# The link fake emulates MEASURED Resolve semantics (2026-09-09):
# SetClipsLinked with three items forms one three-group; linking a
# pair afterwards BREAKS the group rather than adding to it. DeleteTrack
# shifts every row above the deletion down, the way Resolve does.

class FakeItem:
    _ids = iter(range(200000, 2000000))

    def __init__(self, name, start, end, pool_path="", channel=1):
        self._name = name
        self._start = start
        self._end = end
        self._pool_path = pool_path
        self._channel = channel
        self._uid = f"ritem-{next(FakeItem._ids)}"
        self._group = {self._uid}

    def GetName(self): return self._name
    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetDuration(self): return self._end - self._start
    def GetUniqueId(self): return self._uid
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
        # Resolve shifts every row above the deletion down.
        higher = sorted(k for k in self.tracks
                        if k[0] == mt and k[1] > idx)
        for (t, i) in higher:
            self.tracks[(t, i - 1)] = self.tracks.pop((t, i))
            if (t, i) in self.names:
                self.names[(t, i - 1)] = self.names.pop((t, i))
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
    def SetSetting(self, k, v): return True
    def GetUniqueId(self): return "fake-reel-timeline"
    def GetName(self): return "Fake Reel"


class FakePoolItem:
    def __init__(self, path):
        self._path = path
    def GetClipProperty(self, k):
        if k == "File Path": return self._path
        if k == "FPS": return "23.976"
        if k == "Resolution": return "3840x2160"
        return ""


class FakeFolder:
    def __init__(self, name="Master"):
        self._name = name
        self.clips = []
        self.subs = []
    def GetName(self): return self._name
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class FakePool:
    """Measured live Resolve behavior for an MXF audio append: the call
    RETURNS one item and PLACES two - the program stream on the named
    row plus a non-program spill on the next audio row that exists.
    The sweep, not the return value, is what finds the spill."""

    def __init__(self, timeline, paths, audio_channels=(1,),
                 fail_paths=()):
        self.timeline = timeline
        self.root = FakeFolder()
        self._items = {p: FakePoolItem(p) for p in paths}
        self._channels = tuple(audio_channels)
        self._fail = set(fail_paths)
        for item in self._items.values():
            self.root.clips.append(item)

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.root
    def SetCurrentFolder(self, folder): return True
    def AddSubFolder(self, parent, name):
        folder = FakeFolder(name)
        parent.subs.append(folder)
        return folder
    def ImportMedia(self, paths):
        out = []
        for p in paths:
            if p in self._fail:
                continue
            item = self._items.get(p, FakePoolItem(p))
            self._items[p] = item
            self.root.clips.append(item)
            out.append(item)
        return out
    def CreateEmptyTimeline(self, name):
        self.created_name = name
        return self.timeline
    def AppendToTimeline(self, clip_infos):
        out = []
        for info in clip_infos:
            item = info["mediaPoolItem"]
            path = (item._path if isinstance(item, FakePoolItem) else "")
            start, end = info["startFrame"], info["endFrame"]
            rec = info["recordFrame"]
            dur = end - start
            media_type = info.get("mediaType", 1)
            if media_type == 2:
                returned = []
                others = [i for (t, i) in self.timeline.tracks
                          if t == "audio" and i != info["trackIndex"]]
                for n, channel in enumerate(self._channels):
                    placed = FakeItem(path.split("/")[-1], rec, rec + dur,
                                      pool_path=path, channel=channel)
                    FakeTimeline._registry[placed.GetUniqueId()] = placed
                    if n == 0:
                        self.timeline.tracks.setdefault(
                            ("audio", info["trackIndex"]), []).append(placed)
                        returned.append(placed)
                    elif others:
                        # The spill: a non-program stream on the next
                        # audio row that exists. Returned never includes
                        # it - only a row read-back finds it.
                        self.timeline.tracks.setdefault(
                            ("audio", others[(n - 1) % len(others)]),
                            []).append(placed)
                out.extend(returned)
            else:
                placed = FakeItem(path.split("/")[-1], rec, rec + dur,
                                  pool_path=path)
                FakeTimeline._registry[placed.GetUniqueId()] = placed
                self.timeline.tracks.setdefault(
                    ("video", info["trackIndex"]), []).append(placed)
                out.append(placed)
        return out


class FakeMoment:
    timeline_name = "Reel 99 - sop-proof"
    timeline_start = 0.0
    timeline_end = 20.0
    number = 99
    call_to_action = None


def _clip(track_type, index, track_name, speaker, source, tl_start,
          tl_end, src_in=100.0):
    return TimelineClip(
        resolve_item_id=f"{track_name}-{tl_start}", track_type=track_type,
        track_index=index, track_name=track_name, speaker=speaker,
        source_file=source, source_in=src_in,
        source_out=src_in + (tl_end - tl_start),
        source_in_frame=int(src_in * 24), source_out_frame=int(src_in * 24) + 1,
        source_frames=100000, timeline_start=tl_start, timeline_end=tl_end,
        name="clip")


def _master_clips():
    return [
        _clip("video", 1, "Akshita", "Akshita", "/m/akshita.MXF", 0.0, 10.0),
        _clip("video", 2, "Craig", "Craig", "/m/craig.MXF", 10.0, 20.0),
        _clip("audio", 1, "Akshita CH1", "Akshita", "/m/akshita.MXF",
              0.0, 10.0),
        _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF", 10.0, 20.0),
    ]


def _world(audio_channels=(1,), fail_paths=()):
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    paths = ["/m/akshita.MXF", "/m/craig.MXF", "/m/cap.mov",
             "/m/sem.mov"]
    pool = FakePool(timeline, paths, audio_channels=audio_channels,
                    fail_paths=fail_paths)
    project = type("P", (), {})()
    project.GetMediaPool = lambda: pool
    project.SetCurrentTimeline = lambda tl: True
    project.GetCurrentTimeline = lambda: timeline
    return timeline, pool, project


def _transcript():
    return {"segments": []}


def _caption(start, end, name="cap"):
    return {"overlay_path": "/m/cap.mov", "timeline_start": start,
            "timeline_end": end, "source_in_frame": 0,
            "segment_id": name}


def _semantic(start, total, name="sem"):
    return {"overlay_path": "/m/sem.mov", "timeline_start": start,
            "total_frames": total}


def _build(timeline, pool, project, clips, captions=(), semantic=(),
           look=None, program_channels=None, edit_ledger_rows=None,
           draw_gain=FALLBACK_DRAW_GAIN):
    return build_reel_timeline(
        project, FakeMoment(), clips, list(captions),
        23.976, 1080, 1920, "/tmp/no-such-project", _transcript(),
        look=look, semantic_segments=list(semantic) or None,
        master_timeline=None, program_channels=program_channels,
        edit_ledger_rows=edit_ledger_rows, draw_gain=draw_gain)


# ── Angles come from the master's own picture rows ──

def test_angles_are_read_off_the_master_not_assumed():
    angles = reel_angles(_master_clips())
    assert [(a["key"], a["label"]) for a in angles] == [
        ("1", "Akshita"), ("2", "Craig")]


def test_an_unresolvable_program_stream_refuses_before_creating():
    timeline, pool, project = _world()
    with pytest.raises(ReelBuildError, match="no recorded program stream"):
        _build(timeline, pool, project, _master_clips())
    assert not hasattr(pool, "created_name"), \
        "the refusal must fire before a timeline exists"


# ── The three defects ──

def test_two_angles_get_two_picture_rows_and_two_named_speech_rows():
    """Defect 3: each speaker their own video AND audio row, named from
    the master - never "Video 1" / "Audio 2"."""
    timeline, pool, project = _world()
    record = _build(timeline, pool, project, _master_clips(),
                    program_channels={"1": 1, "2": 1})
    assert timeline.GetTrackCount("video") == 2
    assert timeline.GetTrackCount("audio") == 2
    assert timeline.GetTrackName("video", 1) == "Akshita"
    assert timeline.GetTrackName("video", 2) == "Craig"
    assert timeline.GetTrackName("audio", 1) == "Akshita CH1"
    assert timeline.GetTrackName("audio", 2) == "Craig CH1"


def test_captain_override_window_check_uses_the_builds_measured_draw_gain(
        monkeypatch):
    """The override recheck must use the same 1.0 gain as the placed aim.

    Geo Podcast measured 1.0 while the machine fallback is 2.0. Using
    that fallback only for the captain's Pan=-8 recheck doubles the
    predicted vertical shift and falsely refuses a picture that covers
    the TV window at the measured gain.
    """
    import library.tools.reel_build as reel_build

    received = {}

    def capture_override_recheck(*args, **kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(reel_build, "apply_transform_overrides",
                        capture_override_recheck)
    timeline, pool, project = _world()

    record = _build(timeline, pool, project, _master_clips(),
                    program_channels={"1": 1, "2": 1}, draw_gain=1.0)

    assert received["draw_gain"] == pytest.approx(1.0)
    v1 = timeline.GetItemListInTrack("video", 1)
    a1 = timeline.GetItemListInTrack("audio", 1)
    v2 = timeline.GetItemListInTrack("video", 2)
    a2 = timeline.GetItemListInTrack("audio", 2)
    assert len(v1) == len(a1) == 1 and len(v2) == len(a2) == 1
    assert "akshita" in v1[0].GetName() and "craig" in v2[0].GetName()
    assert "akshita" in a1[0].GetName() and "craig" in a2[0].GetName()
    assert record["track_plan"]["material"]["angles"][0]["label"] == "Akshita"


def test_declared_angle_plan_limits_picture_rows_but_keeps_all_speech():
    """An E5 camera plan must drive picture placement independently of
    speech placement, or the reel keeps copying every master camera row."""
    timeline, pool, project = _world()
    clips = [
        _clip("video", 1, "Akshita", "Akshita", "/m/akshita.MXF",
              0.0, 20.0),
        _clip("video", 2, "Craig", "Craig", "/m/craig.MXF",
              0.0, 20.0),
        _clip("audio", 1, "Akshita CH1", "Akshita", "/m/akshita.MXF",
              0.0, 20.0),
        _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF",
              0.0, 20.0),
    ]
    row = {"op": "angle_plan", "anchor": {"kind": "reel"},
           "reel": FakeMoment.timeline_name,
           "params": {"camera": "Akshita", "min_shot_seconds": 3,
                      "lead_frames": 0},
           "stated_by": "requester", "reason": "stay on the host"}
    record = _build(timeline, pool, project, clips,
                    program_channels={"1": 1, "2": 1},
                    edit_ledger_rows=[row])
    assert timeline.GetTrackCount("video") == 1
    assert timeline.GetTrackCount("audio") == 2
    assert timeline.GetTrackName("video", 1) == "Akshita"
    assert timeline.GetTrackName("audio", 1) == "Akshita CH1"
    assert timeline.GetTrackName("audio", 2) == "Craig CH1"
    assert record["angle_plan"]["declared"] is True
    assert record["angle_plan"]["picture_placements"] == 1


def test_missing_ledger_powergrade_refuses_before_timeline_creation():
    """A grade row whose declared asset vanished must not leave a
    half-built reel behind without that look."""
    timeline, pool, project = _world()
    grade = {"op": "grade", "anchor": {"kind": "reel"},
             "params": {
                 "drx": "missing.drx",
                 "provenance": {"source": "Resolve export",
                                "authorised_by": "captain",
                                "licence": "captain's own asset"}},
             "stated_by": "requester", "reason": "apply the look"}

    with pytest.raises(ReelBuildError,
                       match="grade cannot be resolved before the timeline"):
        _build(timeline, pool, project, _master_clips(),
               program_channels={"1": 1, "2": 1},
               edit_ledger_rows=[grade])
    assert not hasattr(pool, "created_name")


def test_non_program_streams_are_deleted_on_the_spot_and_recorded():
    """Defect 1: the MXF's four streams reach placement, and only the
    recorded program stream stays - the rest are deleted and said.

    The spill is the live shape, not the return value's: the append
    returns the program item while a non-program copy lands on the
    next audio row. Enforcement reads the rows back, so the copy is
    found whatever the call admitted to."""
    timeline, pool, project = _world(audio_channels=(1, 2, 3, 4))
    record = _build(timeline, pool, project, _master_clips(),
                    program_channels={"1": 1, "2": 1})
    enforcement = record["stream_enforcement"]
    assert enforcement["checked"] == 8, "four streams over two placements"
    assert len(enforcement["deleted"]) == 6
    assert {d["row"] for d in enforcement["deleted"]} == {1, 2}, \
        "strays land on both rows and both are swept"
    assert {tuple(sorted(d["placed_channels"])) for d in
            enforcement["deleted"]} == {(2,), (3,), (4,)}
    for index in (1, 2):
        items = timeline.GetItemListInTrack("audio", index)
        assert len(items) == 1
        import json as _json
        mapping = _json.loads(items[0].GetSourceAudioChannelMapping())
        assert (mapping["track_mapping"]["1"]["channel_idx"] == [1]), \
            "no stray stream survives on a speech row"


def test_picture_links_to_speech_in_one_call_per_pair():
    """Defect 2: picture and speech travel together - one link call per
    pair, read back."""
    timeline, pool, project = _world()
    record = _build(timeline, pool, project, _master_clips(),
                    program_channels={"1": 1, "2": 1})
    pair_calls = [c for c in timeline.link_calls if len(c[0]) == 2 and c[1]]
    assert len(pair_calls) == 2
    assert len(record["link_groups"]) == 2
    for row in (1, 2):
        for item in (timeline.GetItemListInTrack("video", row)
                     + timeline.GetItemListInTrack("audio", row)):
            assert item.GetLinkedItems(), \
                f"every a-roll item is linked, found {item.GetName()} alone"


def test_seven_frame_audio_lead_links_same_angle_a_roll():
    """Reel 11's source-edge offset still links picture and speech.

    Craig's speech starts seven frames before his picture item. The
    placement entry point must join the overlapping items from the
    same angle despite their different start frames.
    """
    from library.tools.timeline_layout import TrackPlan, TrackSpec

    timeline, pool, project = _world()
    clips = _master_clips()
    clips[-1] = _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF",
                      9.7, 20.0)
    record = _build(timeline, pool, project, clips,
                    program_channels={"1": 1, "2": 1})
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**row) for row in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**row) for row in raw["audio_tracks"]],
        material=raw.get("material", {}))

    picture = timeline.GetItemListInTrack("video", 2)[0]
    speech = timeline.GetItemListInTrack("audio", 2)[0]
    assert picture.GetStart() == 240
    assert speech.GetStart() == 233
    assert picture.GetLinkedItems() == [speech]
    assert speech.GetLinkedItems() == [picture]
    report = verify_timeline(timeline, plan=plan)
    assert report["passed"]
    assert "aroll_linked" in report["checks_run"]
    assert not [v for v in report["violations"]
                if v["check"] == "aroll_unlinked"]


def test_caption_inside_speech_joins_one_three_group():
    """Defect 2 (captions): picture, speech and caption link in a single
    call - a later pair-call would break the group."""
    timeline, pool, project = _world()
    record = _build(timeline, pool, project, _master_clips(),
                    captions=[_caption(2.0, 4.0)],
                    program_channels={"1": 1, "2": 1})
    assert timeline.GetTrackName("video", 3) == "Subtitles"
    triples = [c for c in timeline.link_calls if len(c[0]) == 3 and c[1]]
    assert len(triples) == 1, (
        f"expected one three-group link call, saw {timeline.link_calls}")
    assert len(record["caption_links"]) == 1
    cap = timeline.GetItemListInTrack("video", 3)[0]
    assert len(cap.GetLinkedItems()) == 2, \
        "the group holds: nothing re-linked afterwards to break it"


def test_sparse_overlay_rows_pack_with_no_empty_row_left():
    """Defect 4 as the verifier sees it: a semantic row with no
    transitions or explainer above it packs onto V4 - no blank V4/V5
    kept, and every surviving row named."""
    timeline, pool, project = _world()
    record = _build(timeline, pool, project, _master_clips(),
                    captions=[_caption(2.0, 4.0)],
                    semantic=[_semantic(0.0, 48)],
                    program_channels={"1": 1, "2": 1})
    assert timeline.GetTrackCount("video") == 4
    assert timeline.GetTrackName("video", 4) == "Semantic"
    for index in range(1, 5):
        assert timeline.GetItemListInTrack("video", index), \
            f"V{index} is empty and must have been deleted"
    assert record["deleted_empty_tracks"] == [], \
        "packing means no empty row is ever created, so none is deleted"


def test_a_row_whose_placements_all_fail_is_deleted_not_kept():
    """Defect 4 as the SOP writes it: when every caption fails to
    import, the caption row leaves the timeline - deleted and on the
    record, never kept blank."""
    timeline, pool, project = _world(fail_paths=("/m/cap.mov",))
    # The caption file is not already pooled, so the failed import is
    # really exercised rather than short-circuited by the lookup.
    pool.root.clips = [c for c in pool.root.clips
                       if c.GetClipProperty("File Path") != "/m/cap.mov"]
    pool._items.pop("/m/cap.mov", None)
    record = _build(timeline, pool, project, _master_clips(),
                    captions=[_caption(2.0, 4.0)],
                    semantic=[_semantic(0.0, 48)],
                    program_channels={"1": 1, "2": 1})
    assert timeline.GetTrackCount("video") == 3
    assert timeline.GetTrackName("video", 3) == "Semantic", \
        "deleting the empty middle row shifts the survivor down"
    assert [t["name"] for t in record["deleted_empty_tracks"]] == [
        "Subtitles"]


# ── The look keeps one picture row per speaker ──


def test_program_channels_prefer_the_catalog_then_the_master():
    """Known unknown, answered: the reel path reaches the recorded
    program stream through the catalog first and the live master's own
    speech rows second - and refuses when neither names one."""
    clips = [_clip("audio", 1, "Akshita CH1", "Akshita", "/m/a.MXF",
                   0.0, 10.0)]
    angles = [{"key": "1", "label": "Akshita", "track_index": 1}]
    assert resolve_reel_program_channels(
        angles, clips, "", explicit={"1": 3}) == {"1": 3}
    with pytest.raises(ReelBuildError, match="no recorded program stream"):
        resolve_reel_program_channels(angles, clips, "/tmp/no-such-project")
