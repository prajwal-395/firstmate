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
from tests.resolve_double import FakeProject, FakeTimeline, timeline_item

FPS = 23.976
JOIN_SECONDS = 10.0
JOIN_FRAME = int(round(JOIN_SECONDS * FPS))  # 240
LEAD_SECONDS = 0.5
LEAD_FRAMES = int(round(LEAD_SECONDS * FPS))  # 12
CUT_FRAME = JOIN_FRAME - LEAD_FRAMES  # 228

REEL_09A_J_CUT = "Reel 09A - your-website-is-only-20-percent (j-cut)"
REEL_09B_CUTAWAY = (
    "Reel 09B - your-website-is-only-20-percent (reaction-cutaway)")


# ── The canonical double (tests/resolve_double.py) ─────────────
# Its SetClipsLinked forms one group per call, and a later call sharing
# an item replaces (breaks) the earlier group - the measured semantics.

POOL_PATHS = ["/m/akshita.MXF", "/m/craig.MXF", "/m/cap.mov"]


def _item(path, start, end):
    """A placed item playing ``path`` over ``[start, end)``."""
    return timeline_item(path.split("/")[-1], start, end, path=path)


def _reel_timeline(video=((),), audio=((),)):
    """The reel container: one video and one audio row to start."""
    return FakeTimeline("Fake Offset Reel", video=[list(r) for r in video],
                        audio=[list(r) for r in audio])


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
    timeline = _reel_timeline()
    project = FakeProject()
    pool = project.GetMediaPool()
    for path in POOL_PATHS:
        pool.media_properties[path] = {"FPS": "23.976",
                                       "Resolution": "3840x2160"}
    pool.ImportMedia(POOL_PATHS)
    pool.next_timeline = timeline
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


# ── the link pass ──


def test_ordinary_link_pass_joins_overlapping_same_angle_spans():
    """Ordinary placement links source-edge offsets by angle and span."""
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False, "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    pic = _item("/m/a.MXF", 0, 100)
    speech = _item("/m/a.MXF", 50, 150)
    timeline = _reel_timeline(video=[[pic]], audio=[[speech]])
    record = link_reel_groups(timeline, plan)
    assert not record["warnings"]
    assert len(record["link_groups"]) == 1
    assert len(timeline.link_calls) == 1
    assert set(pic.GetLinkedItems()) == {speech}
    assert set(speech.GetLinkedItems()) == {pic}


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
    plan = plan_layout({
        "angles": [{"key": "1", "label": "Akshita",
                    "speech_name": "Akshita CH1", "program_channel": 1}],
        "has_broll": False, "has_frame": False, "caption_spans": [],
        "mg_spans": [], "has_generators": False, "timed_text_spans": [],
        "music_spans": [], "sfx_spans": [],
    })
    pic = _item("/m/a.MXF", 0, 100)
    speech = _item("/m/a.MXF", 0, 100)
    tail = timeline_item(tail_name, 100, 119, path=tail_pool)
    timeline = _reel_timeline(video=[[pic, tail]], audio=[[speech]])
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


# ── a recorded reel-seconds window goes stale across rebuilds ──
# Reel 09, 2026-09-13: the recorded cutaway window ([23.9406,
# 24.9416], frames 574..598) silently meant different content after a
# rebuild moved the reel's timing, because `window_seconds` are REEL
# seconds and `plan_cutaway` compares them against reel frames. The
# cover's SOURCE span is the anchor that does not move, so the window
# is re-derived from where the cover lands on THIS build
# (`resolve_cutaway_window_frames`) and a stale recording refuses
# loudly rather than being used.

COVER_MASTER_START = 9.5
COVER_MASTER_END = 10.5
COVER_START_F = int(round(COVER_MASTER_START * FPS))
COVER_SPAN_F = int(round((COVER_MASTER_END - COVER_MASTER_START) * FPS))
LEAD_SHIFT = 24
"""One head-card second of moved timing between the two builds."""


def _cover_clip(tl_start, tl_end):
    """An external cutaway cover already checked onto the master clock.

    `verify_cover_clip` is out of reach here (ffprobe, media reads);
    what the window resolution needs is only the clip's identity in
    the placements, so a plain TimelineClip on the neighbour's row
    stands in for it.
    """
    return _clip("video", 1, "Akshita", "Akshita", "/m/akshita-cover.MXF",
                 tl_start, tl_end, src_in=3000.0)


def _cover_spans(placed, cover):
    return sorted(_span_frames(p) for p in placed if p["clip"] is cover)


def test_a_stale_recorded_window_is_used_silently_by_plan_cutaway():
    """The defect as found: the same recorded window builds cleanly on
    moved timing while covering different content - nothing refuses,
    nothing says the meaning changed."""
    cover = _cover_clip(COVER_MASTER_START, COVER_MASTER_END)
    clips = _master_clips() + [cover]
    recorded_window = [COVER_START_F / FPS,
                       (COVER_START_F + COVER_SPAN_F) / FPS]
    assert _cover_spans(placements([(0.0, 20.0)], clips, FPS),
                        cover) == [(COVER_START_F,
                                    COVER_START_F + COVER_SPAN_F)], \
        "on the build the window was recorded from, it IS the cover"
    moved = placements([(0.0, 20.0)], clips, FPS,
                       lead_frames=LEAD_SHIFT)
    assert _cover_spans(moved, cover) == [
        (COVER_START_F + LEAD_SHIFT,
         COVER_START_F + COVER_SPAN_F + LEAD_SHIFT)], \
        "the cover re-derives onto the moved timing; the recording does not"
    stale_frames = (int(round(recorded_window[0] * FPS)),
                    int(round(recorded_window[1] * FPS)))
    plan = plan_cutaway(moved, FPS, "2", stale_frames, cover_words=[])
    assert plan.report["window_record_frames"] == list(stale_frames), \
        "the stale window is used exactly as recorded"
    assert tuple(plan.report["window_record_frames"]) != tuple(
        _cover_spans(moved, cover)[0]), \
        "and it no longer covers the cover - used silently"


def test_resolve_cutaway_window_refuses_a_stale_recorded_window():
    """The fix: the moved build above refuses, naming the recording
    and where the cover now plays - never used silently."""
    from library.tools.reel_build import resolve_cutaway_window_frames
    cover = _cover_clip(COVER_MASTER_START, COVER_MASTER_END)
    clips = _master_clips() + [cover]
    stale = {"hide_angle": "2",
             "window_seconds": [COVER_START_F / FPS,
                                (COVER_START_F + COVER_SPAN_F) / FPS]}
    moved = placements([(0.0, 20.0)], clips, FPS,
                       lead_frames=LEAD_SHIFT)
    with pytest.raises(OffsetRefused, match="stale"):
        resolve_cutaway_window_frames(stale, cover, moved, FPS)
    with pytest.raises(OffsetRefused, match="re-record"):
        resolve_cutaway_window_frames(stale, cover, moved, FPS)

