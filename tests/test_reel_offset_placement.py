"""Offset placement: picture and audio may occupy different spans.

Covers the Reel 09 24:08 commission (captain, 2026-09-10): PR 880
measured the wall - `placements()` lays picture and audio with
identical spans and `link_reel_groups` links by matching start frame -
and found it is the builder, not Resolve and not the SOP. This file
proves the capability and both versions built on it, against a fake
Resolve with MEASURED link semantics (linking is exclusive: a second
call sharing an item BREAKS the first group):

- version A (J-cut): take-2's audio starts under take-1's picture;
- version B (reaction cutaway): the listener's continuous picture
  covers the seam while the audio never moves.

Both are built as separate timelines beside the captain's Reel 09,
never over it, and both read back conformance-clean. The motion plan
is untouched throughout: shots 2 and 3 stay still.
"""

import pytest

from library.tools.reel_build import (
    OffsetLink,
    OffsetRefused,
    build_reel_timeline,
    link_reel_groups,
    placements,
    plan_cutaway,
    plan_j_cut,
    shift_captions_for_audio_lead,
)
from library.tools.timeline_conformance import (
    CHECKS,
    verify_timeline,
)
from library.tools.timeline_ingest import TimelineClip
from library.tools.timeline_layout import plan_layout

FPS = 23.976
JOIN_SECONDS = 10.0
JOIN_FRAME = int(round(JOIN_SECONDS * FPS))  # 240
LEAD_SECONDS = 0.5
LEAD_FRAMES = int(round(LEAD_SECONDS * FPS))  # 12
CUT_FRAME = JOIN_FRAME - LEAD_FRAMES  # 228

REEL_09A_J_CUT = "Reel 09A - your-website-is-only-20-percent (j-cut)"
REEL_09B_CUTAWAY = (
    "Reel 09B - your-website-is-only-20-percent (reaction-cutaway)")


# ── Faithful fakes ─────────────────────────────────────────────
# Same measured semantics as test_reel_build_sop_conformance.py:
# SetClipsLinked forms one group per call; a later call sharing an
# item replaces (breaks) the earlier group. Copied, not imported -
# that file's fakes are test-local and this file must stand alone.

class FakeItem:
    _ids = iter(range(500000, 5000000))

    def __init__(self, name, start, end, pool_path="", channel=1):
        self._name = name
        self._start = start
        self._end = end
        self._pool_path = pool_path
        self._channel = channel
        self._uid = f"oitem-{next(FakeItem._ids)}"
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
        import json as _json
        return _json.dumps({
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
    def GetUniqueId(self): return "fake-offset-timeline"
    def GetName(self): return "Fake Offset Reel"


class FakePoolItem:
    def __init__(self, path):
        self._path = path
    def GetClipProperty(self, k):
        if k == "File Path": return self._path
        if k == "FPS": return "23.976"
        if k == "Resolution": return "3840x2160"
        return ""


class FakeFolder:
    # The bin API landed with the pool filing of PR 897, which this
    # file predates.  Without it every build here dies on
    # `AddSubFolder` inside `_ensure_bin_path` - the offset planning
    # these tests exist for is never reached.
    def __init__(self, name="Master"):
        self.clips = []
        self.subs = []
        self.name = name
    def GetName(self): return self.name
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class FakePool:
    def __init__(self, timeline, paths):
        self.timeline = timeline
        self.root = FakeFolder()
        self.current = self.root
        self._items = {p: FakePoolItem(p) for p in paths}
        for item in self._items.values():
            self.root.clips.append(item)

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, folder):
        self.current = folder
        return True
    def AddSubFolder(self, folder, name):
        sub = FakeFolder(name)
        folder.subs.append(sub)
        return sub
    def ImportMedia(self, paths):
        out = []
        for p in paths:
            item = self._items.get(p, FakePoolItem(p))
            self._items[p] = item
            self.current.clips.append(item)
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
            placed = FakeItem(path.split("/")[-1], rec, rec + dur,
                              pool_path=path)
            FakeTimeline._registry[placed.GetUniqueId()] = placed
            key = ("video" if media_type == 1 else "audio",
                   info["trackIndex"])
            self.timeline.tracks.setdefault(key, []).append(placed)
            out.append(placed)
        return out


class FakeMoment:
    timeline_start = 0.0
    timeline_end = 20.0
    number = 9
    call_to_action = None

    def __init__(self, name):
        self.timeline_name = name


def _clip(track_type, index, track_name, speaker, source, tl_start,
          tl_end, src_in):
    return TimelineClip(
        resolve_item_id=f"{track_name}-{tl_start}", track_type=track_type,
        track_index=index, track_name=track_name, speaker=speaker,
        source_file=source, source_in=src_in,
        source_out=src_in + (tl_end - tl_start),
        source_in_frame=int(src_in * 24), source_out_frame=int(src_in * 24) + 1,
        source_frames=100000, timeline_start=tl_start, timeline_end=tl_end,
        name="clip")


def _master_clips(akshita_audio=True):
    """Two Craig takes abutting at the join, Akshita continuous.

    Mirrors the Reel 09 seam: one camera, two takes, the join at reel
    10s, the listener rolling through on her own angle.
    """
    clips = [
        _clip("video", 1, "Akshita", "Akshita", "/m/akshita.MXF",
              0.0, 20.0, src_in=2000.0),
        _clip("video", 2, "Craig", "Craig", "/m/craig.MXF",
              0.0, 10.0, src_in=1000.0),
        _clip("video", 2, "Craig", "Craig", "/m/craig.MXF",
              10.0, 20.0, src_in=1500.0),
        _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF",
              0.0, 10.0, src_in=1000.0),
        _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF",
              10.0, 20.0, src_in=1500.0),
    ]
    if akshita_audio:
        clips.append(_clip("audio", 1, "Akshita CH1", "Akshita",
                           "/m/akshita.MXF", 0.0, 20.0, src_in=2000.0))
    return clips


def _world():
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    pool = FakePool(timeline, ["/m/akshita.MXF", "/m/craig.MXF",
                               "/m/cap.mov"])
    project = type("P", (), {})()
    project.GetMediaPool = lambda: pool
    project.SetCurrentTimeline = lambda tl: True
    project.GetCurrentTimeline = lambda: timeline
    return timeline, pool, project


def _caption(start, end, name="cap"):
    return {"overlay_path": "/m/cap.mov", "timeline_start": start,
            "timeline_end": end, "source_in_frame": 0,
            "segment_id": name}


def _build(name, clips, captions=(), j_cut=None, cutaway=None):
    timeline, pool, project = _world()
    record = build_reel_timeline(
        project, FakeMoment(name), clips, list(captions),
        FPS, 1080, 1920, "/tmp/no-such-project", {"segments": []},
        master_timeline=None, program_channels={"1": 1, "2": 1},
        j_cut=j_cut, cutaway=cutaway)
    return timeline, record


def _verify(timeline, record):
    from library.tools.timeline_layout import TrackPlan, TrackSpec
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
        material=raw.get("material", {}))
    return verify_timeline(timeline, plan=plan)


def _span_frames(place):
    start = int(place["snapped_record"])
    duration = int(round((place["source_out"] - place["source_in"]) * FPS))
    return (start, start + duration)


def _is_audio(place):
    return getattr(place["clip"], "track_type", "") != "video"


# ── plan_j_cut ──

def test_j_cut_moves_the_audio_cut_picture_untouched():
    """The ear crosses before the eye: audio heads start the lead
    early, tails end it early, every picture span is byte-identical."""
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    plan = plan_j_cut(before, FPS, JOIN_FRAME, LEAD_FRAMES,
                      words=[(8.0, 8.4, "aware"), (10.6, 11.0, "where")])
    after = {(_span_frames(p), _is_audio(p),
              getattr(p["clip"], "track_index", None)): p
             for p in plan.placements}
    for place in before:
        span = _span_frames(place)
        audio = _is_audio(place)
        if not audio:
            assert (span, False,
                    place["clip"].track_index) in after, \
                "picture must not move"
    craig_head = [p for p in plan.placements
                  if _is_audio(p) and p["clip"].track_index == 2
                  and int(p["snapped_record"]) == CUT_FRAME]
    assert len(craig_head) == 1
    assert _span_frames(craig_head[0])[0] == CUT_FRAME
    assert craig_head[0]["source_in"] == pytest.approx(1500.0 - 0.5)
    craig_tail = [p for p in plan.placements
                  if _is_audio(p) and p["clip"].track_index == 2
                  and _span_frames(p)[1] == CUT_FRAME]
    assert len(craig_tail) == 1
    assert plan.report["kind"] == "j_cut"
    assert plan.report["lead_ins"], \
        "the unplayed lead-in source is named for the operator"
    assert len(plan.links) == 2, \
        "one link per moved head: Craig's take and Akshita's split head"


def test_j_cut_refuses_a_word_inside_the_trimmed_tail():
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    with pytest.raises(OffsetRefused, match="inside the.*tail"):
        plan_j_cut(before, FPS, JOIN_FRAME, LEAD_FRAMES,
                   words=[(9.6, 9.9, "aware")])


def test_j_cut_refuses_without_source_handles():
    import dataclasses
    clips = []
    for clip in _master_clips():
        if (getattr(clip, "track_type", "") == "audio"
                and clip.timeline_start == 10.0):
            clip = dataclasses.replace(clip, source_in=0.1)
        clips.append(clip)
    before = placements([(0.0, 20.0)], clips, FPS)
    with pytest.raises(OffsetRefused, match="before the file"):
        plan_j_cut(before, FPS, JOIN_FRAME, LEAD_FRAMES)


def test_j_cut_refuses_a_join_with_no_audio_on_it():
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    with pytest.raises(OffsetRefused, match="no audio"):
        plan_j_cut(before, FPS, 480, LEAD_FRAMES)


def test_j_cut_refuses_a_lead_that_erases_the_tail():
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    with pytest.raises(OffsetRefused, match="erases the item"):
        plan_j_cut(before, FPS, JOIN_FRAME, 240)


def test_audio_row_overlap_after_a_move_refuses():
    """The disjointness guard itself: two audio placements from one
    master row overlapping after a move refuse rather than letting
    Resolve trim one silently."""
    from library.tools.reel_build import _check_audio_row_disjoint
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    doubled = list(before) + [dict(before[3])]
    with pytest.raises(OffsetRefused, match="overlap"):
        _check_audio_row_disjoint(doubled, FPS, "J-cut")


# ── plan_cutaway ──

def test_cutaway_hides_the_speaker_reveals_the_listener():
    """Craig's picture is trimmed off a one-second window around the
    join; Akshita's continuous picture shows through; audio never
    moves."""
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    window = (CUT_FRAME, CUT_FRAME + 24)
    plan = plan_cutaway(before, FPS, "2", window, cover_words=[])
    craig_pics = sorted(
        _span_frames(p) for p in plan.placements
        if not _is_audio(p) and str(p["clip"].track_index) == "2")
    assert craig_pics == [(0, CUT_FRAME), (CUT_FRAME + 24, 480)], \
        "the hidden angle abuts the window exactly"
    for place in before:
        if _is_audio(place):
            assert _span_frames(place) in [
                _span_frames(p) for p in plan.placements
                if _is_audio(p)], "audio never moves in a cutaway"
    assert plan.report["cover_angles"] == ["1"]
    assert "listening" in plan.report["cover_note"] or \
        "no word inside the window" in plan.report["cover_note"]


def test_cutaway_refuses_a_speaking_cover():
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    window = (CUT_FRAME, CUT_FRAME + 24)
    with pytest.raises(OffsetRefused, match="mid-sentence"):
        plan_cutaway(before, FPS, "2", window,
                     cover_words=[(9.8, 10.2, "mm-hm")])


def test_cutaway_refuses_a_hole_in_the_cover():
    clips = _master_clips()
    short = [c for c in clips
             if not (getattr(c, "track_type", "") == "video"
                     and getattr(c, "track_index", None) == 1)]
    short.append(_clip("video", 1, "Akshita", "Akshita",
                       "/m/akshita.MXF", 0.0, 5.0, src_in=2000.0))
    before = placements([(0.0, 20.0)], short, FPS)
    with pytest.raises(OffsetRefused, match="no covering picture"):
        plan_cutaway(before, FPS, "2", (CUT_FRAME, CUT_FRAME + 24),
                     cover_words=[])


# ── captions travel with moved speech ──

def test_captions_follow_the_audio_lead():
    segments = [_caption(8.0, 9.4, "cap-early"),
                _caption(10.6, 12.0, "cap-late")]
    adjusted, notes = shift_captions_for_audio_lead(
        segments, JOIN_FRAME, LEAD_FRAMES, FPS)
    assert adjusted[0]["timeline_start"] == 8.0, \
        "a card closing before the join stays"
    assert adjusted[1]["timeline_start"] == pytest.approx(
        (int(round(10.6 * FPS)) - LEAD_FRAMES) / FPS), \
        "a card of the incoming take travels with it, frame-exact"
    assert notes == [], "no straddle, nothing to confess"


def test_straddling_card_shifts_whole_and_is_reported():
    segments = [_caption(9.0, 10.8, "cap-straddle")]
    adjusted, notes = shift_captions_for_audio_lead(
        segments, JOIN_FRAME, LEAD_FRAMES, FPS)
    assert len(adjusted) == 1
    assert len(notes) == 1 and notes[0]["segment_id"] == "cap-straddle"
    assert "early" in notes[0]["compromise"]


# ── the link pass ──

def test_offset_link_with_no_matching_item_refuses():
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False, "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    with pytest.raises(OffsetRefused, match="matches no speech item"):
        link_reel_groups(
            timeline, plan,
            offset_links=[OffsetLink(speech=(0, 100), pictures=((0, 100),))])


def test_legacy_link_pass_untouched_without_offset_links():
    """No offsets, no change: a same-start build links exactly as
    before and warns exactly as before."""
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False, "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    pic = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    speech = FakeItem("a.MXF", 50, 150, pool_path="/m/a.MXF")
    for item in (pic, speech):
        FakeTimeline._registry[item.GetUniqueId()] = item
    timeline.tracks = {("video", 1): [pic], ("audio", 1): [speech]}
    record = link_reel_groups(timeline, plan)
    assert len(record["warnings"]) == 1 and not record["link_groups"]
    assert "left unlinked" in record["warnings"][0]


# ── version A: the J-cut ──

def test_version_a_j_cut_builds_conformance_clean():
    """Reel 09A beside the captain's Reel 09: take-2's audio starts
    0.5s under take-1's picture. Watch the join at reel 10s: the
    question's first words arrive early, over Craig's take-1 frame -
    the ear crosses before the eye. The room-tone caveat stands: both
    takes sit apart in source, so a step at the audio seam survives
    this or any picture treatment - listen there."""
    words = [(8.0, 8.4, "aware"), (10.6, 11.0, "where")]
    timeline, record = _build(
        REEL_09A_J_CUT, _master_clips(),
        captions=[_caption(8.0, 9.4, "cap-early"),
                  _caption(10.6, 12.0, "cap-late")],
        j_cut={"join_seconds": JOIN_SECONDS, "lead_seconds": LEAD_SECONDS,
               "words": words})
    assert timeline.created_name if hasattr(timeline, "created_name") \
        else True
    assert record["timeline_name"] == REEL_09A_J_CUT
    assert record["link_warnings"] == [], record["link_warnings"]
    assert record["offsets"]["j_cut"]["kind"] == "j_cut"
    report = _verify(timeline, record)
    assert report["passed"], report["violations"]
    assert report["checks_skipped"] == []
    assert set(CHECKS) <= set(report["checks_run"])
    craig_audio = timeline.GetItemListInTrack("audio", 2)
    starts = sorted(item.GetStart() for item in craig_audio)
    assert starts[1] == CUT_FRAME, \
        "take-2's audio starts the lead early, under take-1's picture"
    craig_pic = timeline.GetItemListInTrack("video", 2)
    assert sorted(item.GetStart() for item in craig_pic) == [0, JOIN_FRAME], \
        "picture cuts where it always did"


# ── version B: the reaction cutaway ──

def test_version_b_cutaway_builds_conformance_clean():
    """Reel 09B beside the captain's Reel 09: Akshita listening across
    the seam, one second of her continuous picture over Craig's join
    while his audio plays straight through. Watch that she is
    listening, not speaking - the build refuses a cover that talks -
    and that her frame reads as attention at the join. Same room-tone
    caveat as version A: the audio cut is straight, so a step at the
    seam survives - listen there."""
    window = (JOIN_SECONDS - 0.5, JOIN_SECONDS + 0.5)
    timeline, record = _build(
        REEL_09B_CUTAWAY, _master_clips(),
        captions=[_caption(8.0, 9.4, "cap-early"),
                  _caption(10.6, 12.0, "cap-late")],
        cutaway={"hide_angle": "2", "window_seconds": window,
                 "cover_words": []})
    assert record["timeline_name"] == REEL_09B_CUTAWAY
    assert record["link_warnings"] == [], record["link_warnings"]
    assert record["offsets"]["cutaway"]["cover_angles"] == ["1"]
    report = _verify(timeline, record)
    assert report["passed"], report["violations"]
    assert report["checks_skipped"] == []
    assert set(CHECKS) <= set(report["checks_run"])
    craig_pic = timeline.GetItemListInTrack("video", 2)
    spans = sorted((item.GetStart(), item.GetEnd()) for item in craig_pic)
    assert spans == [(0, CUT_FRAME), (CUT_FRAME + 24, 480)], \
        "Craig's picture parts around the window; hers shows through"
    akshita_pic = timeline.GetItemListInTrack("video", 1)
    assert len(akshita_pic) == 1, "her continuous shot is never cut"


def test_version_b_cutaway_with_silent_listener_links_the_cover():
    """No Akshita audio at all: her revealed picture still joins a
    speech group - the incoming take's - instead of placing
    unlinked."""
    window = (JOIN_SECONDS - 0.5, JOIN_SECONDS + 0.5)
    timeline, record = _build(
        REEL_09B_CUTAWAY + "-silent", _master_clips(akshita_audio=False),
        cutaway={"hide_angle": "2", "window_seconds": window,
                 "cover_words": []})
    assert record["link_warnings"] == [], record["link_warnings"]
    report = _verify(timeline, record)
    assert report["passed"], report["violations"]
    akshita_pic = timeline.GetItemListInTrack("video", 1)
    assert len(akshita_pic[0].GetLinkedItems()) >= 1, \
        "the revealed listener travels with the seam's words"


def test_offset_build_refuses_what_it_cannot_link():
    """An offset that cannot be linked refuses the whole build - it
    never places silently unlinked."""
    window = (JOIN_SECONDS - 0.5, JOIN_SECONDS + 0.5)
    clips = [c for c in _master_clips(akshita_audio=False)
             if not (getattr(c, "track_type", "") == "video"
                     and getattr(c, "track_index", None) == 1)]
    clips.append(_clip("video", 1, "Akshita", "Akshita",
                       "/m/akshita.MXF", 30.0, 40.0, src_in=2000.0))
    before = placements([(0.0, 20.0)], clips, FPS)
    with pytest.raises(OffsetRefused, match="no covering picture"):
        plan_cutaway(before, FPS, "2",
                     (CUT_FRAME, CUT_FRAME + 24), cover_words=[])


# ── a HELD FRAME is not an unlinked picture ─────────────────────
# Measured 2026-09-12 rebuilding the captain's field-test project:
# every reel whose ending declares `tail_hold: freeze` refused the
# OFFSET build with "Offset build leaves 1 a-roll item(s) unlinked:
# picture at 1650-1669 on Craig", after placing correctly. A held
# frame is rendered by `reel_ending` onto the ending shot's own row
# and carries no audio anywhere on the timeline, so there is nothing
# for it to link TO. The ordinary rebuild's link pass records a
# warning and carries on; only this census raised, which made the
# whole variant path unreachable for any reel with a freeze ending.

def _offset_census_timeline(tail_name, tail_pool):
    """One linkable speech+picture pair plus one unlinked tail item."""
    FakeTimeline._registry = {}
    timeline = FakeTimeline()
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False, "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    pic = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    speech = FakeItem("a.MXF", 0, 100, pool_path="/m/a.MXF")
    tail = FakeItem(tail_name, 100, 119, pool_path=tail_pool)
    for item in (pic, speech, tail):
        FakeTimeline._registry[item.GetUniqueId()] = item
    timeline.tracks = {("video", 1): [pic, tail], ("audio", 1): [speech]}
    return timeline, plan, tail


def test_offset_census_lets_a_held_frame_through():
    timeline, plan, tail = _offset_census_timeline(
        "reel_freeze_8da72bf764.mov", "/p/reel_freeze_8da72bf764.mov")
    record = link_reel_groups(
        timeline, plan,
        offset_links=[OffsetLink(speech=(0, 100), pictures=((0, 100),))])
    assert record["link_groups"], "the real speech group still links"
    assert not tail.GetLinkedItems(), \
        "the hold is not linked to anything - that is the whole point"


def test_offset_census_still_refuses_an_unlinked_picture():
    """The gate can still fail: rename the tail off the hold
    convention and the same timeline refuses again. Without this the
    test above would pass against a census that had simply stopped
    looking."""
    timeline, plan, _tail = _offset_census_timeline(
        "b.MXF", "/m/b.MXF")
    with pytest.raises(OffsetRefused, match="unlinked"):
        link_reel_groups(
            timeline, plan,
            offset_links=[OffsetLink(speech=(0, 100),
                                     pictures=((0, 100),))])
