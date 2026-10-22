"""The OTIO placement lands the SAME timeline the default build does.

`build_reel_timeline(placement_mode="otio")` records every placement,
imports the compiled timeline once and replays the deferred transforms
(`library/tools/reel_otio_placement.py`). The defect this guards is a
recorded build that differs from the default one - a row, a source
frame, a channel or a transform lost between the recording and the
import - which nothing in Resolve would report.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

from library.tools.reel_build import build_reel_timeline
from library.tools.reel_otio_placement import (
    OtioPlacementRefused, record_duration)
from library.tools.timeline_ingest import TimelineClip
from tests.resolve_double import FakeTimeline, make_project

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="needs ffmpeg to make the media")

FPS = 23.976


def _media(path, seconds, timecode=None):
    command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
               f"testsrc2=size=32x32:rate=24000/1001:duration={seconds}",
               "-c:v", "libx264", "-preset", "ultrafast"]
    if timecode:
        command += ["-timecode", timecode]
    subprocess.run(command + [str(path)], check=True, capture_output=True,
                   encoding="utf-8")
    return str(path)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("media")
    return {
        # Footage with a start timecode, as the master's MXFs carry.
        # The source-frame law itself is `test_otio_compile`'s: the
        # double reads a source frame off the range the document
        # declares, so it cannot see a law broken on both sides.
        "speakerone": _media(root / "speakerone.mov", 130, "00:00:10:00"),
        "speakertwo": _media(root / "speakertwo.mov", 130, "00:00:10:00"),
        "cap": _media(root / "cap.mov", 3),
        "sem": _media(root / "sem.mov", 3),
    }


def _clip(media, track_type, index, track_name, speaker, angle, start, end):
    return TimelineClip(
        resolve_item_id=f"{track_name}-{start}", track_type=track_type,
        track_index=index, track_name=track_name, speaker=speaker,
        source_file=media[angle], source_in=100.0,
        source_out=100.0 + (end - start), source_in_frame=2398,
        source_out_frame=2399, source_frames=100000,
        timeline_start=start, timeline_end=end, name="clip")


class _Moment:
    timeline_name = "Reel 99 - otio-proof"
    timeline_start = 0.0
    timeline_end = 20.0
    number = 99
    call_to_action = None


def _build(media, mode, tmp_path, *, project_size=(1080, 1920),
           with_caption=True, with_semantic=True, lands_current=False,
           park_previous=False):
    project = make_project(width=project_size[0], height=project_size[1],
                           frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    pool.otio_import_lands_current = lands_current
    footage = [media["speakerone"], media["speakertwo"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    pooled = pool.ImportMedia(footage)  # the master's footage is pooled
    pooled[0].AddMarker(2400, "Blue", "review", "source note", 12,
                        '{"source":"captain"}')
    pooled[1].AddMarker(2401, "Green", "keep", "another note", 1, "")
    if park_previous:
        pool.next_timeline = FakeTimeline()
        project.SetCurrentTimeline(pool.CreateEmptyTimeline("previous"))
    clips = [
        _clip(media, "video", 1, "SpeakerOne", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "video", 2, "SpeakerTwo", "SpeakerTwo", "speakertwo", 10, 20),
        _clip(media, "audio", 1, "SpeakerOne CH1", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "speakertwo", 10, 20),
    ]
    caption = {"overlay_path": media["cap"], "timeline_start": 2.0,
               "timeline_end": 4.0, "source_in_frame": 0,
               "segment_id": "cap-1",
               "tight_box": {"width": 800, "height": 400, "placement": {
                   "scaling": 1, "pan": 140.0, "tilt": -1720.0}}}
    semantic = {"overlay_path": media["sem"], "timeline_start": 6.0,
                "total_frames": 48}
    record = build_reel_timeline(
        project, _Moment(), clips, [caption] if with_caption else [], FPS,
        1080, 1920, str(tmp_path / "project"), {"segments": []},
        semantic_segments=[semantic] if with_semantic else [],
        program_channels={"1": 1, "2": 1}, placement_mode=mode)
    return project, pool, record


def _timeline_state(project):
    timeline = project.GetCurrentTimeline()
    rows = []
    for kind in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(kind) + 1):
            items = []
            for item in timeline.GetItemListInTrack(kind, index) or []:
                mapping = item.GetSourceAudioChannelMapping() or ""
                items.append({
                    "file": item.GetMediaPoolItem().GetClipProperty(
                        "File Path"),
                    "span": (item.GetStart(), item.GetEnd()),
                    "left_offset": item.GetLeftOffset(),
                    "transform": {key: item.GetProperty(key) for key in
                                  ("Pan", "Tilt", "ZoomX", "ZoomY",
                                   "Scaling")},
                    "channel": (json.loads(mapping)["track_mapping"]["1"]
                                ["channel_idx"] if mapping else None),
                    "markers": item.GetMarkers(),
                })
            rows.append((kind, index, timeline.GetTrackName(kind, index),
                         items))
    return {"rows": rows, "timeline_markers": timeline.GetMarkers()}


def test_the_recorded_build_lands_the_default_builds_timeline(media,
                                                              tmp_path,
                                                              monkeypatch):
    keys = ("useCustomSettings", "timelineResolutionWidth",
            "timelineResolutionHeight")
    writes = {}
    original_set_setting = FakeTimeline.SetSetting

    def refuse_if_current(target, key, value):
        if key in keys:
            assert not target._is_current, (
                f"{key} was set after {target.GetName()} became current")
            writes.setdefault(id(target), []).append((key, value))
        return original_set_setting(target, key, value)

    monkeypatch.setattr(FakeTimeline, "SetSetting", refuse_if_current)

    default_project, default_pool, _ = _build(
        media, "append", tmp_path / "a", project_size=(1080, 1920))
    project, pool, record = _build(
        media, "otio", tmp_path / "b", project_size=(1080, 1920))

    expected_resolution = [("useCustomSettings", "1"),
                           ("timelineResolutionWidth", "1080"),
                           ("timelineResolutionHeight", "1920")]
    # Only the append path writes the size: the import inherits the
    # matching project default, so the post-import check makes no write.
    # Measured live 2026-10-08, the import makes the new timeline
    # current - sizing it then would risk the settings-sync deadlock.
    assert len(writes) == 1, "only append sizes its empty timeline"
    assert all(calls == expected_resolution for calls in writes.values())
    assert pool.append_calls == [], "a recorded build appends nothing"
    assert default_pool.append_calls, "the default build appends"
    assert _timeline_state(project) == _timeline_state(default_project)
    assert record["stream_enforcement"]["checked"] == 2
    assert record["stream_enforcement"]["unverified"] == []
    assert record["placement_profile"]["source_markers_restored"] == 4
    profile = record["placement_profile"]
    assert profile["counts"] == {
        "recorded_items": 6,
        "deferred_transforms": 2,
        "recorded_overlay_bins": 2,
    }
    assert all(value >= 0 for key, value in profile.items()
               if key.endswith("_s"))
    assert profile["timeline_import_s"] >= 0
    assert profile["imported_resolution_verify_s"] >= 0
    assert profile["placement_restore_readback_s"] >= 0
    assert profile["pool_organization_s"] >= 0
    assert profile["channel_checks_s"] >= 0
    assert profile["deferred_transform_s"] >= 0
    assert profile["ledger_retime_s"] >= 0
    assert profile["grade_s"] >= 0
    assert profile["linking_s"] >= 0


def test_completed_phase_is_written_to_kpi_ledger(monkeypatch):
    from library.tools import perf_ledger
    from library.tools.reel_otio_placement import start_phase

    rows = []
    monkeypatch.setattr(
        perf_ledger, "record",
        lambda layer, wall_s, **fields: rows.append(
            (layer, wall_s, fields)))
    profile = {"timeline_name": "Reel 09 - scratch"}

    started = start_phase()
    time.sleep(0.02)
    record_duration(profile, "timeline_import_s", started)

    assert profile["timeline_import_s"] >= 0.01
    assert rows == [(
        "otio_placement.timeline_import_s", profile["timeline_import_s"],
        {"phase": "timeline_import_s",
         "reel": "Reel 09 - scratch", "placement": "otio"})]


def test_averaged_phase_uses_elapsed_not_duration_as_start(monkeypatch):
    """A per-reel average is already elapsed seconds, not a timestamp.

    Passing it to record_duration subtracts it from the monotonic clock
    and reports hours (measured 134590 s on a 37-hour-uptime Mac);
    record_elapsed stores the average itself.
    """
    from library.tools import perf_ledger
    from library.tools.reel_otio_placement import record_elapsed

    rows = []
    monkeypatch.setattr(
        perf_ledger, "record",
        lambda layer, wall_s, **fields: rows.append(
            (layer, wall_s, fields)))
    profile = {"timeline_name": "Reel 26 - scratch"}

    record_elapsed(profile, "fusion_comps_s", 0.042)

    assert profile["fusion_comps_s"] == 0.042
    assert rows == [(
        "otio_placement.fusion_comps_s", 0.042,
        {"phase": "fusion_comps_s",
         "reel": "Reel 26 - scratch", "placement": "otio"})]


def test_resolution_mismatch_takes_the_mismatch_route_or_falls_back(
        media, tmp_path):
    # A readable mismatch is the mismatch route's, not a pre-import
    # refusal: explicit `otio` attempts it live. This double models the
    # live landing (`otio_import_lands_current`) with a previous
    # timeline to park on, so the import lands custom-sized with the
    # project default restored. With nowhere to park, explicit `otio`
    # refuses; automatic placement appends without trying this route.
    from tests.resolve_double import FakeTimeline
    project = make_project(width=3840, height=2160, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    pool.otio_import_lands_current = True
    footage = [media["speakerone"], media["speakertwo"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    pool.ImportMedia(footage)
    pool.next_timeline = FakeTimeline()
    previous = pool.CreateEmptyTimeline("previous")
    project.SetCurrentTimeline(previous)
    clips = [
        _clip(media, "video", 1, "SpeakerOne", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "video", 2, "SpeakerTwo", "SpeakerTwo", "speakertwo", 10, 20),
        _clip(media, "audio", 1, "SpeakerOne CH1", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "speakertwo", 10, 20),
    ]

    record = build_reel_timeline(
        project, _Moment(), clips, [], FPS, 1080, 1920,
        str(tmp_path / "mismatch"), {"segments": []},
        program_channels={"1": 1, "2": 1}, placement_mode="otio")

    timeline = project.GetCurrentTimeline()
    assert timeline.GetName() == _Moment.timeline_name
    assert (timeline.GetSetting("timelineResolutionWidth"),
            timeline.GetSetting("timelineResolutionHeight")) == (
        "1080", "1920")
    assert timeline.GetSetting("useCustomSettings") == "1"
    assert (project.GetSetting("timelineResolutionWidth"),
            project.GetSetting("timelineResolutionHeight")) == (
        "3840", "2160")
    assert record["placement_profile"].get("mismatch_route") is True
    assert record["placement_profile"].get("project_default_restored") is True

    bare = make_project(width=3840, height=2160, frame_rate=FPS)
    bare_pool = bare.GetMediaPool()
    bare_pool.next_timeline = FakeTimeline()
    bare_pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                                  for p in footage}
    bare_pool.ImportMedia(footage)
    build_reel_timeline(
        bare, _Moment(), clips, [], FPS, 1080, 1920,
        str(tmp_path / "mismatch-fallback"), {"segments": []},
        program_channels={"1": 1, "2": 1}, placement_mode="auto")

    assert bare.GetTimelineCount() == 1
    assert bare_pool.append_calls, "auto falls back to the append path"


def test_mismatch_route_refuses_explicit_otio_with_nowhere_to_park(
        media, tmp_path):
    project = make_project(width=3840, height=2160, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    footage = [media["speakerone"], media["speakertwo"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    pool.ImportMedia(footage)
    clips = [
        _clip(media, "video", 1, "SpeakerOne", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "video", 2, "SpeakerTwo", "SpeakerTwo", "speakertwo", 10, 20),
        _clip(media, "audio", 1, "SpeakerOne CH1", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "speakertwo", 10, 20),
    ]

    with pytest.raises(OtioPlacementRefused,
                       match="no timeline is current"):
        build_reel_timeline(
            project, _Moment(), clips, [], FPS, 1080, 1920,
            str(tmp_path / "project"), {"segments": []},
            program_channels={"1": 1, "2": 1}, placement_mode="otio")

    assert project.GetTimelineCount() == 0
    assert project.GetCurrentTimeline() is None
    assert pool.append_calls == [], "a refused build appends nothing"
    assert (project.GetSetting("timelineResolutionWidth"),
            project.GetSetting("timelineResolutionHeight")) == (
        "3840", "2160")


def test_what_the_import_cannot_carry_refuses_by_name(media, tmp_path):
    project = make_project(width=1080, height=1920, frame_rate=FPS)
    project.GetMediaPool().next_timeline = FakeTimeline()

    class _Element:
        element_path = media["sem"]

    with pytest.raises(OtioPlacementRefused, match="transition elements"):
        build_reel_timeline(
            project, _Moment(), [], [], FPS, 1080, 1920,
            str(tmp_path / "project"), {"segments": []},
            overlay_placements=[_Element()], placement_mode="otio")


def test_unlinked_import_is_relinked_to_the_pooled_clip(media, tmp_path):
    """An import that reports no media pool item is repaired, not kept.

    The double's `unlinked_otio_imports` knob reproduces what PR 1853's
    first live run measured on Resolve 21.1 (2026-10-06): the imported
    clip plays the pooled file, yet `GetMediaPoolItem()` returns None.
    The build must delete each such item and place the already-pooled
    clip at the recorded span - matched by file path and span - filing
    no pool-ROOT copy. Without the repair the timeline below reads a
    `None` pool item, which is what the freeze-tail inheritance, the
    crop aiming and the comp mapping refuse on.
    """
    from library.tools.reel_build import pool_items_for

    project = make_project(width=1080, height=1920, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    pool.unlinked_otio_imports = True
    footage = [media["speakerone"], media["speakertwo"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    root = pool.GetRootFolder()
    source_bin = pool.AddSubFolder(root, "Source footage")
    before = pool.GetCurrentFolder()
    pool.SetCurrentFolder(source_bin)
    pool.ImportMedia(footage)
    pool.SetCurrentFolder(before)
    clips = [
        _clip(media, "video", 1, "SpeakerOne", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "video", 2, "SpeakerTwo", "SpeakerTwo", "speakertwo", 10, 20),
        _clip(media, "audio", 1, "SpeakerOne CH1", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "speakertwo", 10, 20),
    ]
    record = build_reel_timeline(
        project, _Moment(), clips, [], FPS, 1080, 1920,
        str(tmp_path / "project"), {"segments": []},
        program_channels={"1": 1, "2": 1}, placement_mode="otio")
    timeline = project.GetCurrentTimeline()
    for kind in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(kind) + 1):
            for item in timeline.GetItemListInTrack(kind, index) or []:
                pool_item = item.GetMediaPoolItem()
                assert pool_item is not None, (
                    f"{kind}{index}@{item.GetStart()} plays unlinked - "
                    f"the import was not repaired")
                assert pool_item.GetClipProperty("File Path"), (
                    f"{kind}{index}@{item.GetStart()} is bound to a "
                    f"pool item with no file")
    for path in footage:
        items = pool_items_for(pool, path)
        assert len(items) == 1, (
            f"{os.path.basename(path)} holds {len(items)} pool items "
            f"after the repair - the fallback must reuse, never import")
        assert items[0] in source_bin.GetClipList()
    root_files = [clip.GetClipProperty("File Path") or ""
                  for clip in root.GetClipList() or ()]
    assert [path for path in root_files if path] == [], (
        f"the repair filed pool-ROOT copies: {root_files}")
    assert record["placement_profile"]["relinked_items"] == 4


def test_source_clips_already_pooled_are_not_duplicated(media, tmp_path):
    """A source clip already in the pool is reused, not re-imported.

    The defect: the OTIO import (importSourceClips) created a second
    pool item for a source clip the project already held, filing the
    new item into the pool ROOT. The reel's timeline then bound the
    ROOT copy - a different media id than the captain's master plays -
    so an in-place edit on the master's item could not carry.
    """
    from library.tools.reel_build import pool_items_for

    project = make_project(width=1080, height=1920, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    footage = [media["speakerone"], media["speakertwo"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    # Pool the footage into a bin, as the master build files it - NOT
    # into the pool root, which is where the defect filed the duplicate.
    root = pool.GetRootFolder()
    source_bin = pool.AddSubFolder(root, "Source footage")
    before = pool.GetCurrentFolder()
    pool.SetCurrentFolder(source_bin)
    pool.ImportMedia(footage)
    pool.SetCurrentFolder(before)
    clips = [
        _clip(media, "video", 1, "SpeakerOne", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "video", 2, "SpeakerTwo", "SpeakerTwo", "speakertwo", 10, 20),
        _clip(media, "audio", 1, "SpeakerOne CH1", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "speakertwo", 10, 20),
    ]
    build_reel_timeline(
        project, _Moment(), clips, [], FPS, 1080, 1920,
        str(tmp_path / "project"), {"segments": []},
        program_channels={"1": 1, "2": 1}, placement_mode="otio")
    for path in footage:
        items = pool_items_for(pool, path)
        assert len(items) == 1, (
            f"{os.path.basename(path)} holds {len(items)} pool items "
            f"after the OTIO import - the import duplicated an "
            f"already-pooled source clip")
        assert items[0] in source_bin.GetClipList(), (
            f"{os.path.basename(path)} is not in the 'Source footage' bin")


@pytest.mark.parametrize(
    ("with_caption", "with_semantic"),
    [(True, True), (True, False), (False, True), (False, False)],
    ids=("full", "no-semantic", "no-captions", "bare"),
)
def test_mismatch_route_lands_the_default_build_on_every_shape(
        media, tmp_path, monkeypatch, with_caption, with_semantic):
    """Explicit OTIO's mismatch route matches append on four reel shapes.

    Each shape builds twice: append on a 3840x2160 project (the
    captain's default - append sizes its empty timeline custom, so the
    default never matters to it) and the mismatch OTIO route on the
    same default with the live landing modelled (`lands_current` plus
    a previous timeline to park on). Every row, item, transform,
    channel and marker reads back identical, the imported timeline is
    custom-sized, the project default is restored, and no timeline
    setting was ever written on a current timeline.
    """
    keys = ("useCustomSettings", "timelineResolutionWidth",
            "timelineResolutionHeight")
    original_set_setting = FakeTimeline.SetSetting

    def refuse_if_current(target, key, value):
        if key in keys:
            assert not target._is_current, (
                f"{key} was set after {target.GetName()} became current")
        return original_set_setting(target, key, value)

    monkeypatch.setattr(FakeTimeline, "SetSetting", refuse_if_current)

    default_project, default_pool, _ = _build(
        media, "append", tmp_path / "a", project_size=(3840, 2160),
        with_caption=with_caption, with_semantic=with_semantic)
    # Explicit `otio` keeps the mismatch route available by request.
    project, pool, record = _build(
        media, "otio", tmp_path / "b", project_size=(3840, 2160),
        with_caption=with_caption, with_semantic=with_semantic,
        lands_current=True, park_previous=True)

    assert pool.append_calls == [], "a recorded build appends nothing"
    assert default_pool.append_calls, "the default build appends"
    assert _timeline_state(project) == _timeline_state(default_project)
    timeline = project.GetCurrentTimeline()
    assert (timeline.GetSetting("useCustomSettings"),
            timeline.GetSetting("timelineResolutionWidth"),
            timeline.GetSetting("timelineResolutionHeight")) == (
        "1", "1080", "1920")
    assert (project.GetSetting("timelineResolutionWidth"),
            project.GetSetting("timelineResolutionHeight")) == (
        "3840", "2160")
    assert record["placement_profile"].get("mismatch_route") is True
    assert record["placement_profile"].get("project_default_restored") is True


def test_mismatch_route_discards_its_import_when_sizing_fails(
        media, tmp_path, monkeypatch):
    """A wedged sizing deletes the import and restores the project.

    The route was proven before recording, so a failure this late
    means Resolve itself is not answering: the imported timeline is
    deleted, the project default is restored, the cursor is parked
    back, and the refusal names all three - a genuine refusal, never
    a silent fallback.
    """
    from library.tools import resolve_deadline
    project = make_project(width=3840, height=2160, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    pool.otio_import_lands_current = True
    footage = [media["speakerone"], media["speakertwo"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    pool.ImportMedia(footage)
    pool.next_timeline = FakeTimeline()
    previous = pool.CreateEmptyTimeline("previous")
    project.SetCurrentTimeline(previous)
    clips = [
        _clip(media, "video", 1, "SpeakerOne", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "video", 2, "SpeakerTwo", "SpeakerTwo", "speakertwo", 10, 20),
        _clip(media, "audio", 1, "SpeakerOne CH1", "SpeakerOne", "speakerone", 0, 10),
        _clip(media, "audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "speakertwo", 10, 20),
    ]

    def wedge(_timeline, _width, _height, **_kwargs):
        raise resolve_deadline.ResolutionNotApplied("wedged")

    monkeypatch.setattr(resolve_deadline, "apply_timeline_resolution", wedge)

    with pytest.raises(OtioPlacementRefused, match="imported timeline deleted"):
        build_reel_timeline(
            project, _Moment(), clips, [], FPS, 1080, 1920,
            str(tmp_path / "project"), {"segments": []},
            program_channels={"1": 1, "2": 1}, placement_mode="otio")

    assert _Moment.timeline_name not in project.names()
    assert project.GetCurrentTimeline() is previous
    assert (project.GetSetting("timelineResolutionWidth"),
            project.GetSetting("timelineResolutionHeight")) == (
        "3840", "2160")
