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
from __future__ import annotations
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
from types import SimpleNamespace
from library.tools import edit_ledger, reel_angle_plan
import math
from collections import Counter
from library.tools.reel_build import suppress_mic_bleed_audio
import os
from library.tools import resolve_bin_layout as bins
from library.tools.project_layout import AREAS, Area
import json
import subprocess
import sys
from pathlib import Path


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

POOL_PATHS = ["/m/speakerone.MXF", "/m/speakertwo.MXF", "/m/cap.mov"]


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


def _master_clips(speakerone_audio=True):
    """Two SpeakerTwo takes abutting at the join, SpeakerOne continuous.

    Mirrors the Reel 09 seam: one camera, two takes, the join at reel
    10s, the listener rolling through on her own angle.
    """
    clips = [
        _clip("video", 1, "SpeakerOne", "SpeakerOne", "/m/speakerone.MXF",
              0.0, 20.0, src_in=2000.0),
        _clip("video", 2, "SpeakerTwo", "SpeakerTwo", "/m/speakertwo.MXF",
              0.0, 10.0, src_in=1000.0),
        _clip("video", 2, "SpeakerTwo", "SpeakerTwo", "/m/speakertwo.MXF",
              10.0, 20.0, src_in=1500.0),
        _clip("audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "/m/speakertwo.MXF",
              0.0, 10.0, src_in=1000.0),
        _clip("audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "/m/speakertwo.MXF",
              10.0, 20.0, src_in=1500.0),
    ]
    if speakerone_audio:
        clips.append(_clip("audio", 1, "SpeakerOne CH1", "SpeakerOne",
                           "/m/speakerone.MXF", 0.0, 20.0, src_in=2000.0))
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
    speakertwo_head = [p for p in plan.placements
                  if _is_audio(p) and p["clip"].track_index == 2
                  and int(p["snapped_record"]) == CUT_FRAME]
    assert len(speakertwo_head) == 1
    assert _span_frames(speakertwo_head[0])[0] == CUT_FRAME
    assert speakertwo_head[0]["source_in"] == pytest.approx(1500.0 - 0.5)
    speakertwo_tail = [p for p in plan.placements
                  if _is_audio(p) and p["clip"].track_index == 2
                  and _span_frames(p)[1] == CUT_FRAME]
    assert len(speakertwo_tail) == 1
    assert plan.report["kind"] == "j_cut"
    assert plan.report["lead_ins"], \
        "the unplayed lead-in source is named for the operator"
    assert len(plan.links) == 2, \
        "one link per moved head: SpeakerTwo's take and SpeakerOne's split head"


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
    """SpeakerTwo's picture is trimmed off a one-second window around the
    join; SpeakerOne's continuous picture shows through; audio never
    moves."""
    before = placements([(0.0, 20.0)], _master_clips(), FPS)
    window = (CUT_FRAME, CUT_FRAME + 24)
    plan = plan_cutaway(before, FPS, "2", window, cover_words=[])
    speakertwo_pics = sorted(
        _span_frames(p) for p in plan.placements
        if not _is_audio(p) and str(p["clip"].track_index) == "2")
    assert speakertwo_pics == [(0, CUT_FRAME), (CUT_FRAME + 24, 480)], \
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
        "angles": [{"key": "1", "label": "SpeakerOne",
                    "speech_name": "SpeakerOne CH1", "program_channel": 1}],
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
    question's first words arrive early, over SpeakerTwo's take-1 frame -
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
    speakertwo_audio = timeline.GetItemListInTrack("audio", 2)
    starts = sorted(item.GetStart() for item in speakertwo_audio)
    assert starts[1] == CUT_FRAME, \
        "take-2's audio starts the lead early, under take-1's picture"
    speakertwo_pic = timeline.GetItemListInTrack("video", 2)
    assert sorted(item.GetStart() for item in speakertwo_pic) == [0, JOIN_FRAME], \
        "picture cuts where it always did"


# ── version B: the reaction cutaway ──


# ── a HELD FRAME is not an unlinked picture ─────────────────────
# Measured 2026-09-12 rebuilding the captain's field-test project:
# every reel whose ending declares `tail_hold: freeze` refused the
# OFFSET build with "Offset build leaves 1 a-roll item(s) unlinked:
# picture at 1650-1669 on SpeakerTwo", after placing correctly. A held
# frame is rendered by `reel_ending` onto the ending shot's own row
# and carries no audio anywhere on the timeline, so there is nothing
# for it to link TO. The ordinary rebuild's link pass records a
# warning and carries on; only this census raised, which made the
# whole variant path unreachable for any reel with a freeze ending.

def _offset_census_timeline(tail_name, tail_pool):
    """One linkable speech+picture pair plus one unlinked tail item."""
    plan = plan_layout({
        "angles": [{"key": "1", "label": "SpeakerOne",
                    "speech_name": "SpeakerOne CH1", "program_channel": 1}],
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
    return _clip("video", 1, "SpeakerOne", "SpeakerOne", "/m/speakerone-cover.MXF",
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


# --------------------------------------------------------------------------
# From test_reel_angle_plan.py
#
# The E5 angle plan is a per-reel declaration, not master-row copying.

def _row(kind, phrase, camera, *, min_shot=2.0, lead=0):
    anchor = {"kind": kind}
    if phrase:
        anchor["phrase"] = phrase
    return {"op": "angle_plan", "anchor": anchor,
            "reel": "Reel 09 - hook",
            "params": {"camera": camera,
                       "min_shot_seconds": min_shot,
                       "lead_frames": lead},
            "stated_by": "requester", "reason": "show the speaker"}


def _transcript():
    return {"segments": [{"words": [
        {"word": "guest", "start": 4.0, "end": 4.2, "timed": True},
        {"word": "speaks", "start": 4.2, "end": 4.5, "timed": True},
    ]}]}


def _angles():
    return [{"key": "1", "label": "Wide"},
            {"key": "2", "label": "Close"}]


def _place(track, start=0, end=10, *, media="video"):
    return {"clip": SimpleNamespace(track_type=media,
                                    track_index=track,
                                    source_file=f"camera-{track}.mov"),
            "source_in": float(start), "source_out": float(end),
            "record": float(start), "snapped_record": int(start * 10),
            "master": (float(start), float(end)),
            "track_index": track, "speaker": "speaker"}


def test_picture_switch_keeps_audio_and_splits_the_chosen_camera():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0,
        "Reel 09 - hook")
    # The word anchor, its lead and the declared minimum resolve to frames.
    assert [(shot["start_frame"], shot["end_frame"], shot["camera_key"])
            for shot in plan["shots"]] == [(0, 30, "1"), (30, 100, "2")]
    assert plan["shots"][0]["min_shot_frames"] == 20
    assert plan["shots"][1]["lead_frames"] == 10
    audio = _place(1, media="audio")
    result, record = reel_angle_plan.select_picture_placements(
        [_place(1), _place(2), audio], plan, 10.0)

    pictures = [place for place in result
                if place["clip"].track_type == "video"]
    assert [(place["clip"].track_index, place["snapped_record"],
             round(place["source_in"], 3), round(place["source_out"], 3))
            for place in pictures] == [(1, 0, 0.0, 3.0),
                                       (2, 30, 3.0, 10.0)]
    assert result[0] is audio
    assert record["picture_placements"] == 2


def test_camera_without_full_interval_coverage_refuses_black_picture():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0,
        "Reel 09 - hook")

    # No camera at all is placed over frames 80-100: nothing to sync from.
    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="would show black"):
        reel_angle_plan.select_picture_placements(
            [_place(1, end=8), _place(2, end=8)], plan, 10.0)


def test_lead_that_breaks_minimum_shot_refuses_instead_of_ignoring_one():
    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="minimum"):
        reel_angle_plan.resolve(
            [_row("reel", "", "Wide", min_shot=2),
             _row("words", "guest speaks", "Close", min_shot=2,
                  lead=25)],
            _angles(), [(0.0, 10.0)], _transcript(), 10.0,
            "Reel 09 - hook")


def test_same_camera_overlapping_placements_cannot_fake_full_coverage():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide")], _angles(), [(0.0, 10.0)],
        _transcript(), 10.0, "Reel 09 - hook")
    first = _place(1)
    duplicate = _place(1)
    duplicate["clip"].source_file = "overlapping-wide-take.mov"

    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="two picture placements overlapping"):
        reel_angle_plan.select_picture_placements(
            [first, duplicate], plan, 10.0)


def test_camera_plan_declaration_refuses_by_name():
    """A non-positive minimum, an omitted lead and an unread parameter."""
    zero_min = _row("reel", "", "Wide", min_shot=0)
    no_lead = _row("reel", "", "Wide")
    del no_lead["params"]["lead_frames"]
    unread = _row("reel", "", "Wide")
    unread["params"]["cut_style"] = "whip"
    for row, match in ((zero_min, "positive minimum"),
                       (no_lead, "lead_frames"),
                       (unread, "does not read")):
        with pytest.raises(edit_ledger.EditLedgerError, match=match):
            edit_ledger.validate_rows([row])


# ── A podcast master places each camera only while its person speaks ──
#
# Measured 2026-09-25 on a scratch copy of geo-podcast: every plan that
# chose the LISTENER (MC1.1's reaction switches), cut ahead of the next
# speaker (MC3.1/C3.3's 12-frame lead) or held a shot past a speaker
# change (PA2.3's 3 s minimum) refused with "covers frames through N ...
# would show black", because the reel's only picture of a camera was the
# master's own placement of it. The camera rolled the whole time; its
# frames come from its file through the offset the master's cuts measure.

def _clip_2(track, file, start, end, source_in, *, frames=10_000):
    return SimpleNamespace(track_type="video", track_index=track,
                           track_name=f"Camera {track}", speaker=f"S{track}",
                           source_file=file, source_in=source_in,
                           source_out=source_in + (end - start),
                           timeline_start=start, timeline_end=end,
                           source_frames=frames)


def _speaker_place(track, start, end, source_in):
    """A placement exactly as `reel_build.placements` makes one, at 10fps."""
    return {"clip": SimpleNamespace(track_type="video", track_index=track,
                                    source_file=f"camera-{track}.mov"),
            "source_in": float(source_in),
            "source_out": float(source_in + end - start),
            "record": float(start), "snapped_record": int(start * 10),
            "master": (float(start), float(end)),
            "track_index": track, "speaker": f"S{track}"}


def _podcast_master():
    # Camera 2's file runs 5 s ahead of camera 1's: frame f of camera-1
    # was recorded with frame f + 50 of camera-2. Three gapless cuts say so.
    return [_clip_2(1, "camera-1.mov", 0.0, 3.0, 100.0),
            _clip_2(2, "camera-2.mov", 3.0, 10.0, 108.0),
            _clip_2(1, "camera-1.mov", 10.0, 12.0, 110.0),
            _clip_2(2, "camera-2.mov", 12.0, 15.0, 117.0)]


def test_camera_sync_is_what_a_majority_of_gapless_cuts_agree_on():
    """Cuts that disagree give no sync rather than an average."""
    from library.tools import camera_sync

    measured = camera_sync.measure(_podcast_master(), 10.0,
                                   angle_key=lambda c: str(c.track_index))
    assert measured["offsets"][("camera-1.mov", "camera-2.mov")] == 50
    assert measured["offsets"][("camera-2.mov", "camera-1.mov")] == -50
    assert camera_sync.agreed_offset([50, 50, 51, 400, -30]) == 50
    assert camera_sync.agreed_offset([50, 400]) is None
    assert camera_sync.agreed_offset([50, 50, 400, 400]) is None


def test_listener_shot_plays_the_listening_camera_from_its_own_file():
    from library.tools import camera_sync

    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0, "Reel 09 - hook")
    # The master placed camera 1 for the whole reel and camera 2 nowhere:
    # the plan's camera-2 shot (frames 30-100) is the listener.
    speaking = _speaker_place(1, 0, 10, 100)
    master = _podcast_master()
    result, record = reel_angle_plan.select_picture_placements(
        [speaking], plan, 10.0, master_clips=master,
        sync=camera_sync.measure(master, 10.0,
                                 angle_key=lambda c: str(c.track_index)))

    pictures = [(p["clip"].source_file, p["snapped_record"],
                 round(p["source_in"], 3), round(p["source_out"], 3))
                for p in result]
    assert pictures == [("camera-1.mov", 0, 100.0, 103.0),
                        ("camera-2.mov", 30, 108.0, 115.0)]
    assert record["synced_placements"] == 1
    # The words under the listener shot are still the speaker's.
    assert result[1]["master"] == (3.0, 10.0)


def test_listener_shot_with_no_measured_sync_refuses_by_name():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0, "Reel 09 - hook")

    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="no agreed offset"):
        reel_angle_plan.select_picture_placements(
            [_speaker_place(1, 0, 10, 100)], plan, 10.0,
            master_clips=_podcast_master(), sync={"offsets": {}})


def test_synced_frames_outside_the_file_refuse_instead_of_clamping():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0, "Reel 09 - hook")
    short = [_clip_2(2, "camera-2.mov", 3.0, 10.0, 108.0, frames=1_100)]

    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="outside the file"):
        reel_angle_plan.select_picture_placements(
            [_speaker_place(1, 0, 10, 100)], plan, 10.0,
            master_clips=short,
            sync={"offsets": {("camera-1.mov", "camera-2.mov"): 50}})


def test_motion_ask_describes_the_shots_the_angle_plan_places(tmp_path):
    """Measured 2026-09-25 on Reel 02 of a scratch geo-podcast: the motion
    ask was spelled over the master's per-speaker shots, the build placed
    the angle plan's shots, and the Fusion pass refused every comp as
    reaching no timeline item. The ask (`write_visual_asks`, shared by
    `reel.ask` and the build) must describe the planned picture."""
    import json

    from library.tools import reel_build

    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "external").mkdir()
    name = "Reel 09 - hook"
    (project / "external" / "edit_ledger.json").write_text(json.dumps({
        "version": 1,
        "rows": [{"op": "angle_plan", "anchor": {"kind": "reel"},
                  "reel": name,
                  "params": {"camera": "Camera 2", "min_shot_seconds": 1.0,
                             "lead_frames": 0},
                  "stated_by": "requester", "reason": "hold the listener"}]}),
        encoding="utf-8")
    # Camera 1 speaks 0-12 and 13-30, camera 2 12-13: two gapless cuts
    # agree that camera-2 runs 5 s ahead of camera-1.
    master = [_clip_2(1, "cam-1.mov", 0.0, 12.0, 100.0),
              _clip_2(2, "cam-2.mov", 12.0, 13.0, 117.0),
              _clip_2(1, "cam-1.mov", 13.0, 30.0, 113.0)]
    moment = SimpleNamespace(number=9, timeline_name=name,
                             timeline_start=10.0, timeline_end=18.0)
    fps = 24000 / 1001

    reel_build.write_visual_asks(
        moment, {"segments": []}, [(10.0, 18.0)], master, str(project),
        fps, name + " (rebuild staging)", [], {"origin": "test"})

    ask = json.loads((project / "pipeline_output" / "llm_requests"
                      / "reel_motion_09.json").read_text(encoding="utf-8"))
    shots = ask["context"]["shots"]
    assert [s["speaker"] for s in shots] == ["S2", "S2", "S2"]
    assert shots[0]["timeline_start"] == 0.0
    assert shots[-1]["timeline_end"] == pytest.approx(8.0, abs=1 / fps)


def test_a_one_frame_gap_is_played_as_a_handle_not_a_sliver():
    """Scratch Reel 04, 2026-09-25: the plan's cut fell one frame before
    the camera's own clip, the gap was filled with a 1-frame synced
    piece, and the F7 readability floor refused the reel. The camera's
    own clip plays that frame as a handle - no sync needed for it."""
    plan = {"declared": True, "shots": [
        {"start_frame": 0, "end_frame": 30, "camera_key": "1",
         "camera": "Wide", "anchor": {"kind": "reel"},
         "min_shot_seconds": 2.0, "lead_frames": 0, "name": "opening"},
        {"start_frame": 30, "end_frame": 100, "camera_key": "2",
         "camera": "Close", "anchor": {"kind": "words", "phrase": "x"},
         "min_shot_seconds": 2.0, "lead_frames": 0, "name": "switch"}]}
    first = _speaker_place(1, 0, 3.1, 100)
    second = _speaker_place(2, 3.1, 10, 200)
    second["snapped_record"] = 31
    result, record = reel_angle_plan.select_picture_placements(
        [first, second], plan, 10.0, sync={"offsets": {}})

    assert [(p["clip"].track_index, p["snapped_record"],
             round(p["source_in"], 3), round(p["source_out"], 3))
            for p in result] == [(1, 0, 100.0, 103.0), (2, 30, 199.9, 206.9)]
    assert record["synced_placements"] == 0


def test_a_plan_opening_on_the_second_camera_keeps_the_master_row_order():
    """Scratch Reels 04/18/19/25, 2026-09-25: a plan whose first shot is
    the master's SECOND camera put that camera on V1, and the verifier -
    reading rows in the master's order - counted the freeze tail on the
    other speaker ("SpeakerTwo planned 16.43s, got 15.64s")."""
    from library.tools import timeline_layout

    angles = [{"key": "1", "label": "SpeakerOne", "speech_name": "SpeakerOne CH1",
               "program_channel": 1},
              {"key": "2", "label": "SpeakerTwo", "speech_name": "SpeakerTwo CH1",
               "program_channel": 1}]
    plan = timeline_layout.plan_layout({"angles": angles,
                                        "picture_angles": ["2", "1"]})
    assert [(row.occupant, row.name) for row in plan.aroll_rows()] == [
        ("1", "SpeakerOne"), ("2", "SpeakerTwo")]


# --------------------------------------------------------------------------
# From test_reel_build_mic_bleed.py

FPS_2 = 24000 / 1001


def _placement(speaker, track_type="audio"):
    duration = round(10 * FPS_2) / FPS_2
    return {
        "clip": SimpleNamespace(
            track_type=track_type,
            source_file=f"/{speaker}.MXF",
        ),
        "source_in": 10.0,
        "source_out": 10.0 + duration,
        "record": 0.0,
        "snapped_record": 0,
        "speaker": speaker,
        "master": (100.0, 100.0 + duration),
    }


def _decision(speaker="SpeakerTwo", start=103.0, end=106.0, status="dropped"):
    return {
        "status": status,
        "dropped_speaker": speaker,
        "start": start,
        "end": end,
        "passage": "the clearer microphone owns this line",
    }


def _transcript_2(*speakers):
    return {"segments": [
        {"speaker": speaker, "timeline_start": start,
         "timeline_end": end, "text": text}
        for speaker, start, end, text in speakers
    ]}


def _stream_row(person, index, track_type):
    name = person if track_type == "video" else f"{person} CH1"
    clip = SimpleNamespace(track_type=track_type, track_index=index,
                           track_name=name, speaker=name,
                           source_file=f"/{person}.MXF")
    return {**_placement(name, track_type=track_type), "clip": clip}


def _mic_bleed_cases():
    speakertwo = _placement("SpeakerTwo")
    speakerone = _placement("SpeakerOne")
    measured = _transcript_2(("SpeakerOne", 103.0, 106.0,
                            "the clearer microphone owns this line"))
    measured["mic_bleed_resolution"] = [_decision()]

    channel_speakerone = _stream_row("SpeakerOne", 1, "audio")
    channel_speakertwo = _stream_row("SpeakerTwo", 2, "audio")
    pictures = [_stream_row("SpeakerOne", 1, "video")["clip"],
                _stream_row("SpeakerTwo", 2, "video")["clip"]]

    return [
        pytest.param(
            [speakertwo, speakerone], measured, None,
            {"/SpeakerTwo.MXF": 2, "/SpeakerOne.MXF": 1},
            [("SpeakerTwo", ["SpeakerOne"], int(3 * FPS_2), round(6 * FPS_2))],
            id="split-only-the-losing-mic"),
        pytest.param(
            [speakertwo], _transcript_2(
                ("SpeakerTwo", 103.0, 106.0, "a distinct SpeakerTwo sentence"),
                ("SpeakerOne", 103.0, 106.0,
                 "a distinct SpeakerOne sentence")), None,
            {"/SpeakerTwo.MXF": 1}, [], id="keep-genuine-overlap"),
        pytest.param(
            [_placement("SpeakerTwo", track_type="video")],
            _transcript_2(("SpeakerOne", 103.0, 106.0, "SpeakerOne speaks")),
            None, {"/SpeakerTwo.MXF": 1}, [], id="never-mute-picture"),
        pytest.param(
            [channel_speakerone, channel_speakertwo],
            _transcript_2(("SpeakerOne", 100.0, 109.0,
                         "SpeakerOne holds the turn")), pictures,
            {"/SpeakerOne.MXF": 1, "/SpeakerTwo.MXF": 1},
            [("SpeakerTwo", ["SpeakerOne"], 0, math.ceil(9 * FPS_2))],
            id="resolve-channel-row-through-its-picture-angle"),
    ]


@pytest.mark.parametrize(
    ("placements", "transcript", "master_clips", "kept_counts",
     "expected_suppressions"),
    _mic_bleed_cases(),
)
def test_mic_bleed_only_suppresses_the_losing_audio_angle(
        placements, transcript, master_clips, kept_counts,
        expected_suppressions):
    """See `docs/evidence/mic_bleed_audio.md` for the measured incident."""
    kept, suppressed = suppress_mic_bleed_audio(
        placements, transcript, FPS_2, master_clips=master_clips)

    kept_by_source = Counter(item["clip"].source_file for item in kept)
    assert dict(kept_by_source) == kept_counts
    assert [(entry["speaker"], entry["speaking_speakers"],
             entry["record_start_frame"], entry["record_end_frame"])
            for entry in suppressed] == expected_suppressions

    if expected_suppressions and expected_suppressions[0][2] == int(3 * FPS_2):
        speakertwo_parts = [part for part in kept if part["speaker"] == "SpeakerTwo"]
        [speakertwo] = [item for item in placements if item["speaker"] == "SpeakerTwo"]
        speakerone = next(item for item in placements
                       if item["speaker"] == "SpeakerOne")
        assert len(speakertwo_parts) == 2
        assert speakertwo_parts[0]["master"][0] == pytest.approx(100.0)
        assert speakertwo_parts[0]["master"][1] <= 103.0
        assert speakertwo_parts[0]["source_in"] == pytest.approx(10.0)
        assert speakertwo_parts[0]["source_out"] == pytest.approx(
            speakertwo_parts[0]["master"][1] - 90.0)
        assert speakertwo_parts[1]["master"][0] >= 106.0
        assert speakertwo_parts[1]["master"][1] == pytest.approx(
            speakertwo["master"][1])
        assert speakertwo_parts[1]["source_in"] == pytest.approx(
            speakertwo_parts[1]["master"][0] - 90.0)
        assert speakertwo_parts[1]["source_out"] == pytest.approx(
            speakertwo["source_out"])
        assert speakertwo_parts[1]["snapped_record"] == round(6 * FPS_2)
        assert speakerone in kept
        assert suppressed == [{
            "speaker": "SpeakerTwo",
            "speaking_speakers": ["SpeakerOne"],
            "source_file": "/SpeakerTwo.MXF",
            "passage": "the clearer microphone owns this line",
            "master_start": 100 + int(3 * FPS_2) / FPS_2,
            "master_end": 100 + math.ceil(6 * FPS_2) / FPS_2,
            "record_start_frame": int(3 * FPS_2),
            "record_end_frame": round(6 * FPS_2),
        }]
    elif master_clips:
        assert next(item for item in placements
                    if item["speaker"] == "SpeakerOne CH1") in kept


def _sliver_placement(speaker, start, end):
    duration = end - start
    return {
        "clip": SimpleNamespace(
            track_type="audio",
            source_file=f"/{speaker}.MXF",
        ),
        "source_in": 50.0,
        "source_out": 50.0 + duration,
        "record": 0.0,
        "snapped_record": 0,
        "speaker": speaker,
        "master": (start, end),
    }


def test_mic_bleed_drops_wordless_sub_floor_slivers():
    """`docs/GOLDEN_PROJECTS.md` item 4: the golden conversation's timings
    (SpeakerTwo to 7.35s, SpeakerOne 8.1-10.75s) with the body end the captain
    moves by hand to 11.0s - 0.25s of silence after SpeakerOne's last word.
    The split stranded a 6-frame tail of the silent mic and F7 refused
    the whole reel. The suppression drops wordless pieces under the
    floor instead of keeping them, so F7 passes; speech survives."""
    from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS
    from library.tools.reel_conformance_verifier import check_short_av_items

    floor_frames = int(math.ceil(MIN_CAPTION_DISPLAY_SECONDS * FPS_2))
    transcript = _transcript_2(
        ("SpeakerTwo", 4.1, 7.35, "it depends on what it read"),
        ("SpeakerOne", 8.1, 10.75, "which means the content you publish"))
    kept, _suppressed = suppress_mic_bleed_audio(
        [_sliver_placement("SpeakerTwo", 7.0, 11.0),
         _sliver_placement("SpeakerOne", 7.0, 11.0)],
        transcript, FPS_2)

    assert kept, "the suppression deleted audible speech, not dust"
    for piece in kept:
        frames = int(round((piece["source_out"] - piece["source_in"])
                           * FPS_2))
        assert frames >= floor_frames, (
            f"{piece['speaker']}'s {frames}-frame remnant "
            f"{piece['master']} survives for F7 to refuse")
    findings = check_short_av_items(
        "Reel 01 - check-the-answer", [],
        [{"name": piece["speaker"],
          "duration_frames": int(round(
              (piece["source_out"] - piece["source_in"]) * FPS_2))}
         for piece in kept],
        FPS_2)
    assert findings == []
    # Both turns still play: SpeakerTwo before SpeakerOne's line, SpeakerOne through
    # the hand-moved end.
    by_speaker = {}
    for piece in kept:
        by_speaker.setdefault(piece["speaker"], []).append(piece["master"])
    assert by_speaker["SpeakerTwo"][0][0] == pytest.approx(7.0)
    assert by_speaker["SpeakerTwo"][-1][1] <= 8.1 + 1 / FPS_2
    assert by_speaker["SpeakerOne"][-1][1] == pytest.approx(11.0, abs=2 / FPS_2)


def test_mic_bleed_keeps_a_sub_floor_remnant_carrying_speech():
    """The floor drop above must not delete words: SpeakerTwo's 0.3s "quite"
    sits 0.05s before SpeakerOne's line, so his kept head is 10 frames -
    under the floor, but carrying speech, so it stays for F7 to refuse
    rather than vanishing silently."""
    transcript = _transcript_2(
        ("SpeakerTwo", 8.0, 8.3, "quite"),
        ("SpeakerOne", 8.35, 10.0, "which means the content"))
    kept, _suppressed = suppress_mic_bleed_audio(
        [_sliver_placement("SpeakerTwo", 7.9, 10.5)], transcript, FPS_2)

    assert any(piece["master"][0] < 8.3 < piece["master"][1]
               for piece in kept), (
        "the suppression deleted SpeakerTwo's audible word with the dust")


# --------------------------------------------------------------------------
# From test_reel_build_pool_filing.py
#
# A build files where it imports; the organiser has nothing to repair.
#
# Every destination is decided up front through `resolve_bin_layout`, and every
# path is looked up before importing it. History: docs/evidence/reel_build.md.

class _FakeClip:
    def __init__(self, file_path, name="clip", uid="uid"):
        self._path = file_path
        self._name = name
        self._uid = uid

    def GetClipProperty(self, key):
        if key == "File Path":
            return self._path
        return ""

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid


class _FakeFolder:
    def __init__(self, name):
        self._name = name
        self._subs = []
        self._clips = []

    def GetName(self):
        return self._name

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _FakePool:
    """A pool that files like Resolve: imports land in CURRENT."""

    def __init__(self):
        self.root = _FakeFolder("Master")
        self._current = self.root
        self.imports = []
        self.created_bins = []
        self.timelines = []
        self._uid = 0

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self._current

    def SetCurrentFolder(self, folder):
        self._current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = _FakeFolder(name)
        parent._subs.append(folder)
        self.created_bins.append(name)
        return folder

    def ImportMedia(self, paths):
        self.imports.append((self._current.GetName(), list(paths)))
        items = []
        for path in paths:
            self._uid += 1
            item = _FakeClip(path, name=os.path.basename(path),
                             uid=f"uid-{self._uid}")
            self._current._clips.append(item)
            items.append(item)
        return items

    def CreateEmptyTimeline(self, name):
        self.timelines.append((self._current.GetName(), name))
        return _FakeClip("", name=name, uid=f"timeline-{name}")


def _area_path(root, area, *names):
    return os.path.join(str(root), AREAS[area].relpath, *names)


def test_a_rebuild_imports_nothing_for_a_path_already_in_the_pool():
    """The done-check's first half: count pool items for a known path
    before and after. A second build of the same reel adds no second
    entry, because the lookup runs before the import."""
    from library.tools.reel_build import import_pool_item

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _area_path(tmp, Area.SUBTITLE_SEGMENTS, "sub_a.mov")
        pool = _FakePool()
        first = import_pool_item(pool, path, str(tmp))
        assert first is not None
        before = len(pool.imports)
        second = import_pool_item(pool, path, str(tmp))
        assert second is first
        assert pool.imports == pool.imports[:before]
        assert len(pool.imports) == before == 1


def test_a_new_subtitle_import_lands_in_the_subtitles_bin():
    """The destination is decided by the file, never inherited from
    the current folder - even when current is a motion-graphics bin -
    and an existing destination bin is never forked."""
    from library.tools.reel_build import import_pool_item

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pool = _FakePool()
        mg = _FakeFolder(bins.MOTION_GRAPHICS_BIN)
        pool.root._subs.append(mg)
        pool.SetCurrentFolder(mg)
        path = _area_path(tmp, Area.SUBTITLE_SEGMENTS, "sub_a.mov")
        found = import_pool_item(pool, path, str(tmp))
        assert found is not None
        folder, _ = pool.imports[0]
        assert folder == bins.SUBTITLES_BIN
        assert pool.GetCurrentFolder() is mg

        # `AddSubFolder` makes a second same-named bin rather than
        # refusing, so the build looks the name up first: a bin that
        # already exists is reused, never forked.
        assert pool.created_bins == [bins.SUBTITLES_BIN]
        again = _FakePool()
        again.root._subs.append(_FakeFolder(bins.SUBTITLES_BIN))
        import_pool_item(again, path, str(tmp))
        assert again.created_bins == []


def test_a_pooled_frame_sequence_is_reused_not_reimported():
    """A sequence reports one bracketed File Path, so no single frame
    path matches it - the old first-frame lookup missed every time and
    each rebuild imported the captions beside themselves. The lookup
    is by directory now."""
    from library.tools.reel_build import import_pool_sequence

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = os.path.join(
            str(tmp), AREAS[Area.SUBTITLE_SEGMENTS].relpath, "seg_00")
        pool = _FakePool()
        sub = _FakeFolder(bins.SUBTITLES_BIN)
        pool.root._subs.append(sub)
        sub._clips.append(
            _FakeClip(os.path.join(frame_dir, "frame-[000-059].png")))
        frames = [os.path.join(frame_dir, f"frame-{i:03d}.png")
                  for i in range(60)]
        found = import_pool_sequence(pool, frames, frame_dir, str(tmp))
        assert found is not None
        assert pool.imports == []


def test_a_reel_timeline_is_created_in_the_reels_bin():
    """`CreateEmptyTimeline` inherits the current folder, so the build
    sets it first - a reel created while a motion-graphics bin is
    current still lands in the reels bin, and current is restored."""
    from library.tools.reel_build import create_reel_timeline

    pool = _FakePool()
    mg = _FakeFolder(bins.MOTION_GRAPHICS_BIN)
    pool.root._subs.append(mg)
    pool.SetCurrentFolder(mg)
    create_reel_timeline(pool, "Reel 09 - slug")
    assert pool.timelines == [(bins.REELS_BIN, "Reel 09 - slug")]
    assert pool.GetCurrentFolder() is mg


# --------------------------------------------------------------------------
# From test_jcut_lead.py
#
# Sound may LEAD picture: `lead_seconds` on an `sfx_creative` entry starts
# the whole sound earlier, bounded by the previous cut and the top of the
# reel; a lead outside those bounds is refused.
#
# History: docs/evidence/jl_cuts.md.

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

DRONE = "test_drone.wav"
IMPACT = "test_impact.wav"


def _library(tmp_path):
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((DRONE, 8.0, "sustained"),
                                  (IMPACT, 0.3, "punchy")):
        audio = lib / name
        audio.write_bytes(b"RIFF....WAVEfmt ")
        entries.append({
            "file": name,
            "path": str(audio),
            "folder_category": "Accents",
            "description": f"a {shape} test sound",
            "technical": {
                "basic": {"duration": duration},
                "energy_profile": {"envelope_shape": shape},
            },
            "transient_offset_sec": 0.0,
        })
    (lib / "sfx_index.json").write_text(json.dumps(entries))
    return {"PIPELINE_SFX_LIBRARY": str(lib)}


def _block(position, tl_start, tl_end, words=()):
    return {
        "position": position, "block_type": "speech",
        "clip_id": "clip_001",
        "source_start": tl_start, "source_end": tl_end,
        "timeline_start": float(tl_start), "timeline_end": float(tl_end),
        "word_timestamps": [{"source_end": float(w)} for w in words],
        "alignment_method": "whisperx",
    }


def _blocks():
    return [_block(1, 0, 6), _block(2, 6, 12), _block(3, 12, 18)]


def _payload(plan, blocks):
    return {
        "sfx_creative": plan,
        "timed_spine": {"structure": blocks},
        # No onsets, peaks or scenes: a sustained sound stays at the
        # block start, so the lead arithmetic reads plainly.
        "temporal_event_indices": [{
            "clip_id": "clip_001",
            "onset_times": [],
            "energy_curve": {"peak_times": []},
            "scene_boundaries": [],
        }],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


def _run(payload, env_lib):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_lib)
    proc = subprocess.run(
        [sys.executable, str(SFX / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env, check=False)
    return proc


def test_lead_starts_the_sound_before_the_cut(tmp_path):
    """Block 2 starts at 6.0; a 1.5s lead starts the 8s drone at 4.5
    and the duration is preserved, not re-anchored. The boundary is
    inclusive: block 3's hit led 6.0s lands exactly ON the previous cut
    and reaches into no earlier moment."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": 1.5,
         "rationale": "the riser swells before the cut it belongs to"},
        {"spine_block_position": 3, "sfx_id": IMPACT, "volume_db": -8,
         "lead_seconds": 6.0, "rationale": "a hit across the whole tail"},
    ], _blocks()), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = {s["sfx_id"]: s
              for s in json.loads(proc.stdout)["sfx_spec"]["sfx_list"]}
    assert set(placed) == {DRONE, IMPACT}
    assert placed[DRONE]["timeline_in"] == 4.5
    assert placed[DRONE]["timeline_out"] == 12.5
    assert placed[DRONE]["duration_seconds"] == 8.0
    assert placed[DRONE]["lead_seconds"] == 1.5
    assert placed[IMPACT]["timeline_in"] == 6.0


def test_a_lead_reaching_outside_its_block_is_refused(tmp_path):
    """Past the previous cut (block 2 at 6.0 led 8s starts at -2.0),
    off the top of the reel (block 1 has no previous cut), and a
    negative lead each drop their entry by name; the run survives and
    an unled hit still lands."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": 8.0, "rationale": "too early a swell"},
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": 1.0, "rationale": "a swell with nowhere early"},
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": -1.0, "rationale": "a lead backwards"},
        {"spine_block_position": 3, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "the hit that still lands"},
    ], _blocks()), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert [s["sfx_id"] for s in placed] == [IMPACT]
    assert "previous cut" in proc.stderr
    assert "top of the reel" in proc.stderr


def test_layer_with_a_lead_swells_early_and_under_speech(tmp_path):
    """The three compose: a reasoned layer with a lead starts before
    the cut AND is not shifted off the previous block's words."""
    lib = _library(tmp_path)
    words = [4.5, 5.0, 5.5, 6.5, 7.5, 8.5]
    blocks = [_block(1, 0, 6, [w for w in words if w < 6]),
              _block(2, 6, 12, [w for w in words if w >= 6]),
              _block(3, 12, 18)]
    proc = _run(_payload([
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer", "lead_seconds": 1.5,
         "rationale": "the bed arrives early under the tail"},
    ], blocks), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert len(placed) == 1
    assert placed[0]["timeline_in"] == 4.5
    assert placed[0]["role"] == "layer"


# --------------------------------------------------------------------------
# From test_jl_cut_frames.py
#
# Rung 7 (K1, TR3.3/MX3.3): a J/L lead stated in frames survives to the frame.
#
# The execution-frontier scout found J/L offsets expressible only in
# seconds (`lead_seconds` / `lag_seconds`), so a request like TR3.3's
# "bring its audio in 20 frames before the picture cut" had no plan
# spelling in the requester's units - E3's rule (numbers when stated)
# failed at the vocabulary. `lead_frames` / `lag_frames` carry the
# stated count; seconds and frames disagreeing past half a frame refuse,
# exactly like a stated offset disagreeing with an anchor.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.jl_cut import resolve_audio_cut

FPS_3 = 30.0


def _blocks_2():
    # A breathing join: outgoing speech ends at 8.0, a 2 s pause, the
    # boundary at 10.0, incoming speech starts at 10.5.
    outgoing = {
        "position": 1, "timeline_start": 0.0, "timeline_end": 10.0,
        "word_timestamps": [
            {"word": "done", "source_start": 7.0, "source_end": 8.0},
        ],
    }
    incoming = {
        "position": 2, "timeline_start": 10.0, "timeline_end": 20.0,
        "word_timestamps": [
            {"word": "next", "source_start": 10.5, "source_end": 11.0},
        ],
    }
    return outgoing, incoming


def test_a_stated_offset_places_the_audio_cut_on_the_frame():
    """TR3.3's 20 frames before the picture cut, MX3.3's 30 frames
    after it, and MX3.3's stated second as a 30-frame offset - each to
    the frame, the method naming the stated unit."""
    outgoing, incoming = _blocks_2()
    cases = [
        ("j_cut", {"lead_frames": 20}, 280, 20 / FPS_3, "20 frames"),
        ("l_cut", {"lag_frames": 30}, 330, 30 / FPS_3, "30 frames"),
        ("l_cut", {"lag_seconds": 1.0}, 330, 1.0, "1.000s"),
    ]
    for kind, offset, frame, seconds, method in cases:
        hit = resolve_audio_cut(
            kind=kind, entry={"type": kind, **offset},
            outgoing=outgoing, incoming=incoming,
            boundary_frame=300, frame_rate=FPS_3)
        assert hit["audio_cut_frame"] == frame, offset
        assert hit["picture_cut_frame"] == 300
        assert hit["lead_seconds"] == pytest.approx(seconds, abs=1e-3)
        assert method in hit["method"]


def test_seconds_and_frames_agreeing_ship_the_frames():
    """Both stated and agreeing: the frame-true value wins, said so."""
    outgoing, incoming = _blocks_2()
    hit = resolve_audio_cut(
        kind="j_cut",
        entry={"type": "j_cut", "lead_seconds": 0.667,
               "lead_frames": 20},
        outgoing=outgoing, incoming=incoming,
        boundary_frame=300, frame_rate=FPS_3)
    assert hit["audio_cut_frame"] == 280
    assert "agrees" in hit["method"]


# --------------------------------------------------------------------------
# From test_transition_cut_placement.py
#
# A beat snap may adjust a cut to the last word (or just behind it); it
# may not move the cut to an earlier word and truncate speech.
#
# History: docs/evidence/transition_placement.md.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_02_plan_transitions.post_bridge import (  # noqa: E402
    MAX_WORD_END_BACKTRACK,
    resolve_cut_point,
)


def speech_block(start, end, word_ends):
    """A spine block whose words end at the given TIMELINE times.

    The spine contract keeps clip_id/source_start/source_end/
    word_timestamps at the TOP level of a block, not under content.
    """
    return {
        "block_type": "speech",
        "timeline_start": start,
        "timeline_end": end,
        "clip_id": "clip_011",
        "source_start": 0.0,
        "source_end": end - start,
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": f"w{i}",
             "source_start": max(0.0, we - start - 0.2),
             "source_end": we - start}
            for i, we in enumerate(word_ends)
        ],
        "content": {"clip_id": "clip_011"},
    }


def non_speech(start, end):
    return {"block_type": "transition_slot",
            "timeline_start": start, "timeline_end": end,
            "clip_id": None, "source_start": None, "source_end": None,
            "word_timestamps": [], "alignment_method": None, "content": {}}


def test_a_beat_on_an_earlier_word_cannot_move_the_cut():
    """The exact 001 failures: the hook 0.0-2.4 whose first word ends on
    a beat (cut relocated to 0.196s), and the 18.37 -> 11.33 case that
    would have dropped seven seconds of speech."""
    cases = [
        (speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.35]),
         non_speech(2.4, 5.4), [0.196, 0.72, 1.25, 1.79], 2.0, 2.4),
        (speech_block(8.38, 18.37, word_ends=[11.33, 15.0, 18.3]),
         non_speech(18.37, 20.87), [11.33, 12.0, 13.0], 18.0, 18.37),
    ]
    for outgoing, incoming, beats, floor, ceiling in cases:
        info = resolve_cut_point(incoming=incoming, outgoing=outgoing,
                                 beat_grid=beats)
        assert floor < info["cut_time"] <= ceiling, (
            f"cut relocated to {info['cut_time']}, truncating speech")


def test_a_beat_on_or_just_behind_the_last_word_is_used():
    """The feature itself survives the fix: a beat on the last word end,
    or within the backtrack window behind it, is the cut."""
    last = 2.30
    earlier = last - (MAX_WORD_END_BACKTRACK / 2)
    cases = [
        ([0.196, 1.1, 2.3], [0.196, 2.3], 2.3),
        ([0.196, earlier, last], [0.196, earlier], earlier),
    ]
    for word_ends, beats, expected in cases:
        info = resolve_cut_point(
            incoming=non_speech(2.4, 5.4),
            outgoing=speech_block(0.0, 2.4, word_ends=word_ends),
            beat_grid=beats)
        assert info["word_beat_coincidence"] is True
        assert abs(info["cut_time"] - expected) < 1e-6


def test_no_usable_beat_cuts_at_the_last_word():
    """A beat further back than the window is ignored, and no beat grid
    at all still cuts at the last word."""
    far = resolve_cut_point(
        incoming=non_speech(6.0, 8.0),
        outgoing=speech_block(0.0, 6.0, word_ends=[1.0, 5.9]),
        beat_grid=[1.0])
    assert far["word_beat_coincidence"] is False
    assert far["cut_time"] > 5.0
    none = resolve_cut_point(
        incoming=non_speech(2.4, 5.4),
        outgoing=speech_block(0.0, 2.4, word_ends=[0.196, 1.1, 2.3]),
        beat_grid=[])
    assert abs(none["cut_time"] - 2.3) < 1e-6
    assert none["word_beat_coincidence"] is False
