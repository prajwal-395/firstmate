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
           look=None, program_channels=None):
    return build_reel_timeline(
        project, FakeMoment(), clips, list(captions),
        23.976, 1080, 1920, "/tmp/no-such-project", _transcript(),
        look=look, semantic_segments=list(semantic) or None,
        master_timeline=None, program_channels=program_channels)


# ── Angles come from the master's own picture rows ──

def test_angles_are_read_off_the_master_not_assumed():
    angles = reel_angles(_master_clips())
    assert [(a["key"], a["label"]) for a in angles] == [
        ("1", "Akshita"), ("2", "Craig")]


def test_a_layer_row_is_not_an_angle():
    clips = _master_clips() + [
        _clip("video", 3, "B-Roll", None, "/m/broll.MXF", 0.0, 5.0),
        _clip("video", 4, "Subtitles", None, "/m/cap.mov", 0.0, 5.0),
    ]
    assert [a["key"] for a in reel_angles(clips)] == ["1", "2"]


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
    v1 = timeline.GetItemListInTrack("video", 1)
    a1 = timeline.GetItemListInTrack("audio", 1)
    v2 = timeline.GetItemListInTrack("video", 2)
    a2 = timeline.GetItemListInTrack("audio", 2)
    assert len(v1) == len(a1) == 1 and len(v2) == len(a2) == 1
    assert "akshita" in v1[0].GetName() and "craig" in v2[0].GetName()
    assert "akshita" in a1[0].GetName() and "craig" in a2[0].GetName()
    assert record["track_plan"]["material"]["angles"][0]["label"] == "Akshita"


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


def test_verifier_reads_back_a_clean_build():
    """The SOP proof in miniature: the recorded plan grades the built
    timeline with zero violations and nothing skipped."""
    timeline, pool, project = _world()
    record = _build(timeline, pool, project, _master_clips(),
                    captions=[_caption(2.0, 4.0)],
                    semantic=[_semantic(0.0, 48)],
                    program_channels={"1": 1, "2": 1})
    from library.tools.timeline_layout import TrackPlan, TrackSpec
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
        material=raw.get("material", {}))
    report = verify_timeline(timeline, plan=plan)
    assert report["passed"], report["violations"]
    assert report["checks_skipped"] == []
    assert set(CHECKS) <= set(report["checks_run"])


def test_verifier_flags_the_old_shape():
    """Each defect, as the verifier sees it on a timeline the old
    builder would have produced: default names, an empty row, unlinked
    pairs, a stray stream."""
    FakeTimeline._registry = {}
    tl = FakeTimeline()
    pic = FakeItem("akshita.MXF", 0, 100, pool_path="/m/akshita.MXF")
    speech = FakeItem("akshita.MXF", 0, 100, pool_path="/m/akshita.MXF",
                      channel=3)
    cap = FakeItem("cap.mov", 10, 50, pool_path="/m/cap.mov")
    for it in (pic, speech, cap):
        FakeTimeline._registry[it.GetUniqueId()] = it
    tl.tracks = {("video", 1): [pic], ("video", 2): [cap],
                 ("video", 3): [], ("audio", 1): [speech],
                 ("audio", 2): []}
    tl.names = {("video", 1): "Video 1", ("video", 2): "Captions",
                ("video", 3): "Video 3", ("audio", 1): "Audio 1",
                ("audio", 2): "Audio 2"}
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False,
        "caption_spans": [(0, 200)],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    report = verify_timeline(tl, plan=plan)
    assert not report["passed"]
    by_check = {v["check"] for v in report["violations"]}
    assert "empty_track" in by_check
    assert "unnamed_track" in by_check
    assert "aroll_unlinked" in by_check
    assert "caption_unlinked" in by_check
    assert "program_stream" in by_check


# ── The look keeps one picture row per speaker ──

def test_reel_track_material_has_no_collapse_rule():
    """The captain overruled the collapse: `reel_track_material` takes
    no `collapse_picture` argument at all, so no caller can ask for
    one row and no later TV-frame declaration can re-fire it."""
    import inspect

    from library.tools.reel_build import reel_track_material
    assert "collapse_picture" not in inspect.signature(
        reel_track_material).parameters


def test_look_mints_two_picture_rows_named_for_their_speakers():
    """Captain's ruling on Reel 09: under the TV-frame look the reel is
    a two-picture-row edit - one row per speaker, named for them,
    matching the two speech rows - with the frame row above the
    picture and the captions above that."""
    from library.tools.reel_build import reel_track_material

    material = reel_track_material(
        _master_clips(),
        {"1": 1, "2": 1},
        caption_spans=[(0, 200)],
        has_transitions=True, has_explainer=False, has_semantic=True,
        has_frame=True)
    assert "collapse_picture" not in material
    plan = plan_layout(material)
    assert [(t.index, t.role, t.name) for t in plan.video_tracks] == [
        (1, "a_roll", "Akshita"), (2, "a_roll", "Craig"),
        (3, "frame", "Frame"), (4, "captions", "Subtitles"),
        (5, "transitions", "Transitions"), (6, "semantic", "Semantic")]
    assert [(t.index, t.role, t.name) for t in plan.audio_tracks] == [
        (1, "speech", "Akshita CH1"), (2, "speech", "Craig CH1")]


def test_two_speeches_sharing_one_start_link_once():
    """Live catch, kept across the un-collapse: two speeches starting
    on the same frame link in ONE call with the picture on it. Two
    pair-calls would let the second steal the picture out of the first
    group - linking is exclusive - so one start is one call with
    everything on it."""
    FakeTimeline._registry = {}
    tl = FakeTimeline()
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1},
                   {"key": "2", "label": "Craig",
                    "speech_name": "Craig CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False,
        "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    pic1 = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    pic2 = FakeItem("c.MXF", 0, 100, pool_path="/m/c.MXF")
    sp1 = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    sp2 = FakeItem("c.MXF", 0, 100, pool_path="/m/c.MXF")
    for it in (pic1, pic2, sp1, sp2):
        FakeTimeline._registry[it.GetUniqueId()] = it
    tl.tracks = {("video", 1): [pic1], ("video", 2): [pic2],
                 ("audio", 1): [sp1], ("audio", 2): [sp2]}
    record = link_reel_groups(tl, plan)
    assert len(tl.link_calls) == 1
    assert len(tl.link_calls[0][0]) == 4
    assert len(record["link_groups"]) == 1
    assert not record["warnings"]
    for it in (pic1, pic2, sp1, sp2):
        assert len(it.GetLinkedItems()) == 3, \
            "one group of four, unbroken by a second call"


def test_link_pass_matches_each_speech_to_its_own_picture_row():
    """On a two-row reel every speech item finds the picture on its own
    angle's row - the match runs across all a-roll rows by start frame,
    not down one angle's row."""
    FakeTimeline._registry = {}
    tl = FakeTimeline()
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1},
                   {"key": "2", "label": "Craig",
                    "speech_name": "Craig CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False,
        "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    pic1 = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    pic2 = FakeItem("c.MXF", 100, 200, pool_path="/m/c.MXF")
    sp1 = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    sp2 = FakeItem("c.MXF", 100, 200, pool_path="/m/c.MXF")
    for it in (pic1, pic2, sp1, sp2):
        FakeTimeline._registry[it.GetUniqueId()] = it
    tl.tracks = {("video", 1): [pic1], ("video", 2): [pic2],
                 ("audio", 1): [sp1], ("audio", 2): [sp2]}
    record = link_reel_groups(tl, plan)
    assert len(record["link_groups"]) == 2
    assert not record["warnings"]
    assert {sp1.GetUniqueId()} <= {i.GetUniqueId() for i in
                                   pic1.GetLinkedItems()}
    assert {sp2.GetUniqueId()} <= {i.GetUniqueId() for i in
                                   pic2.GetLinkedItems()}


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
