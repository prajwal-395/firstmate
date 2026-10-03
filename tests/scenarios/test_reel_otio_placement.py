"""The OTIO placement lands the SAME timeline the default build does.

`build_reel_timeline(placement_mode="otio")` records every placement,
imports the compiled timeline once and replays the deferred transforms
(`library/tools/reel_otio_placement.py`). The defect this guards is a
recorded build that differs from the default one - a row, a source
frame, a channel or a transform lost between the recording and the
import - which nothing in Resolve would report.
"""

import json
import shutil
import subprocess

import pytest

from library.tools.reel_build import build_reel_timeline
from library.tools.reel_otio_placement import OtioPlacementRefused
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
        "akshita": _media(root / "akshita.mov", 130, "00:00:10:00"),
        "craig": _media(root / "craig.mov", 130, "00:00:10:00"),
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


def _build(media, mode, tmp_path):
    project = make_project(width=1080, height=1920, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    footage = [media["akshita"], media["craig"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    pool.ImportMedia(footage)          # the master's footage is pooled
    clips = [
        _clip(media, "video", 1, "Akshita", "Akshita", "akshita", 0, 10),
        _clip(media, "video", 2, "Craig", "Craig", "craig", 10, 20),
        _clip(media, "audio", 1, "Akshita CH1", "Akshita", "akshita", 0, 10),
        _clip(media, "audio", 2, "Craig CH1", "Craig", "craig", 10, 20),
    ]
    caption = {"overlay_path": media["cap"], "timeline_start": 2.0,
               "timeline_end": 4.0, "source_in_frame": 0,
               "segment_id": "cap-1",
               "tight_box": {"width": 800, "height": 400, "placement": {
                   "scaling": 1, "pan": 140.0, "tilt": -1720.0}}}
    semantic = {"overlay_path": media["sem"], "timeline_start": 6.0,
                "total_frames": 48}
    record = build_reel_timeline(
        project, _Moment(), clips, [caption], FPS, 1080, 1920,
        str(tmp_path / "project"), {"segments": []},
        semantic_segments=[semantic], program_channels={"1": 1, "2": 1},
        placement_mode=mode)
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
                })
            rows.append((kind, index, timeline.GetTrackName(kind, index),
                         items))
    return rows


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
        media, "append", tmp_path / "a")
    project, pool, record = _build(media, "otio", tmp_path / "b")

    expected_resolution = [("useCustomSettings", "1"),
                           ("timelineResolutionWidth", "1080"),
                           ("timelineResolutionHeight", "1920")]
    assert len(writes) == 2, "both placement paths size their own timeline"
    assert all(calls == expected_resolution for calls in writes.values())
    assert pool.append_calls == [], "a recorded build appends nothing"
    assert default_pool.append_calls, "the default build appends"
    assert _timeline_state(project) == _timeline_state(default_project)
    assert record["stream_enforcement"]["checked"] == 2
    assert record["stream_enforcement"]["unverified"] == []


def test_a_resolution_timeout_refuses_the_otio_import_by_name(media, tmp_path,
                                                               monkeypatch):
    from library.tools import resolve_deadline

    project = make_project(width=1080, height=1920, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    footage = [media["akshita"], media["craig"]]
    pool.media_properties = {p: {"FPS": "23.976", "Resolution": "32x32"}
                             for p in footage}
    pool.ImportMedia(footage)
    clips = [
        _clip(media, "video", 1, "Akshita", "Akshita", "akshita", 0, 10),
        _clip(media, "video", 2, "Craig", "Craig", "craig", 10, 20),
        _clip(media, "audio", 1, "Akshita CH1", "Akshita", "akshita", 0, 10),
        _clip(media, "audio", 2, "Craig CH1", "Craig", "craig", 10, 20),
    ]

    def timeout(*_args, **_kwargs):
        raise resolve_deadline.ResolveCallTimeout(
            "timeline SetSetting timelineResolutionWidth did not return")

    monkeypatch.setattr(resolve_deadline, "apply_timeline_resolution",
                        timeout)

    with pytest.raises(OtioPlacementRefused,
                       match="Reel 99 - otio-proof: timeline SetSetting"):
        build_reel_timeline(
            project, _Moment(), clips, [], FPS, 1080, 1920,
            str(tmp_path / "project"), {"segments": []},
            program_channels={"1": 1, "2": 1}, placement_mode="otio")

    assert project.GetCurrentTimeline() is None


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
