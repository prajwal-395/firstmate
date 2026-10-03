"""The shadow store numbers distinct timeline states, and only those.
"""
from __future__ import annotations
import pytest
from library.tools import timeline_shadow as shadow
from library.tools import timeline_read
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import FakeTimeline, make_pool_clip, make_project, place_clip
import json
import os
import tempfile
from unittest.mock import MagicMock
from library.tools.timeline_serializer import serialize_timeline_state, diff_timeline_states, _clean_dict
import shutil
import subprocess
from library.tools import otio_compile as C


@pytest.fixture
def reel():
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    timeline.AddMarker(10, "Blue", "note", "keep", 1)
    project.SetCurrentTimeline(timeline)
    with assume_sole_writer("canonical Resolve double"):
        yield project, timeline


def test_an_unchanged_reread_is_the_same_generation(reel, tmp_path):
    # Defect: marker notes carry `read_at`, so every read hashed as a
    # new state and the generation number named nothing.
    project, timeline = reel
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    first = shadow.observe(project, timeline, store)
    again = shadow.observe(project, timeline, store)
    assert (first.generation, again.generation) == (1, 1)
    assert again.verified_at >= first.verified_at

    timeline.AddMarker(20, "Red", "hand edit", "", 1)
    edited = shadow.observe(project, timeline, store)
    assert edited.generation == 2 and edited.source == shadow.OBSERVED
    change = shadow.diff(store.snapshot(first), store.snapshot(edited))
    assert [m["frame"] for m in change["markers_added"]] == [20]
    assert store.read_requests_since(0) == {
        "shadow_hits": 0, "live_refreshes": 3, "misses": 0}


def test_a_rename_keeps_its_history_and_a_shared_name_refuses(reel, tmp_path):
    project, timeline = reel
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    shadow.observe(project, timeline, store)
    timeline.SetName("Reel 09 - final")
    renamed = shadow.observe(project, timeline, store)
    assert renamed.generation == 2
    assert store.resolve_timeline("Podcast", "Reel 09 - final") == \
        renamed.timeline_id
    with pytest.raises(shadow.ShadowError):
        store.resolve_timeline("Podcast", "Reel 09")

    twin = project.adopt(FakeTimeline("Reel 09 - final", project=project,
                                      uid="timeline:twin"))
    project.SetCurrentTimeline(twin)
    shadow.observe(project, twin, store)
    with pytest.raises(shadow.ShadowError, match="2 timelines"):
        store.resolve_timeline("Podcast", "Reel 09 - final")


def test_two_writers_cannot_number_two_states_the_same(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    key = {"project": "P", "timeline_id": "t", "timeline_name": "T"}
    store.record(**key, snapshot={"a": 1}, source=shadow.OBSERVED,
                 expected_head=0)
    with pytest.raises(shadow.HeadMoved):
        store.record(**key, snapshot={"a": 2}, source=shadow.OBSERVED,
                     expected_head=0)


def test_structural_read_uses_a_recorded_generation_and_counts_the_hit(
        tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    snapshot = {"timeline": "Reel 09", "tracks": [{"clips": []}]}
    store.record(project="Podcast", timeline_id="tl-1",
                 timeline_name="Reel 09", snapshot=snapshot,
                 source=shadow.OBSERVED, expected_head=0)

    read = timeline_read.read("Podcast", "Reel 09", store=store)

    assert read.snapshot == snapshot
    assert read.generation.generation == 1
    assert store.read_requests_since(0) == {
        "shadow_hits": 1, "live_refreshes": 0, "misses": 0}
    assert store.answers_since(0) == 1


def test_structural_read_miss_is_counted_without_falling_back_live(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")

    with pytest.raises(shadow.ShadowError, match="refresh a timeline"):
        timeline_read.read("Podcast", "Reel 09", store=store)

    assert store.read_requests_since(0) == {
        "shadow_hits": 0, "live_refreshes": 0, "misses": 1}


def test_read_any_accepts_a_unique_timeline_id(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    store.record(project="Podcast", timeline_id="timeline-09",
                 timeline_name="Reel 09", snapshot={"timeline": "Reel 09"},
                 source=shadow.OBSERVED, expected_head=0)

    read = timeline_read.read_any("timeline-09", store=store)

    assert read.generation.timeline_name == "Reel 09"
    assert read.snapshot == {"timeline": "Reel 09"}


# --------------------------------------------------------------------------
# From test_timeline_serializer.py

@pytest.fixture
def mock_resolve():
    resolve = MagicMock()
    pm = MagicMock()
    project = MagicMock()
    timeline = MagicMock()
    
    resolve.GetProjectManager.return_value = pm
    pm.GetCurrentProject.return_value = project
    project.GetCurrentTimeline.return_value = timeline
    
    timeline.GetName.return_value = "Test Timeline"
    timeline.GetStartFrame.return_value = 0
    timeline.GetEndFrame.return_value = 100
    timeline.GetStartTimecode.return_value = "01:00:00:00"
    
    def get_setting(name):
        if name == "timelineFrameRate": return "24.0"
        if name == "timelineResolutionWidth": return "1920"
        if name == "timelineResolutionHeight": return "1080"
        return ""
    timeline.GetSetting.side_effect = get_setting
    
    timeline.GetMarkers.return_value = {
        10: {"color": "Red", "name": "Marker 1", "note": "Test", "duration": 1, "customData": "data"}
    }
    
    timeline.GetTrackCount.side_effect = lambda t: 1 if t == "video" else 0
    timeline.GetTrackName.return_value = "V1"
    
    # Mock item
    item = MagicMock()
    item.GetUniqueId.return_value = "uuid-1234"
    item.GetName.return_value = "Clip 1"
    item.GetStart.return_value = 0
    item.GetEnd.return_value = 50
    item.GetDuration.return_value = 50
    item.GetSourceStartFrame.return_value = 0
    item.GetSourceEndFrame.return_value = 50
    item.GetLeftOffset.return_value = 0
    item.GetRightOffset.return_value = 0
    item.GetClipColor.return_value = "Blue"
    item.GetClipEnabled.return_value = True
    
    item.GetProperty.return_value = {
        "Pan": 1.0,
        "ZoomX": 1.5,
        "Opacity": 100.0
    }
    
    mpi = MagicMock()
    mpi.GetMediaId.return_value = "media-1"
    def get_clip_property(name):
        if name == "Clip Path": return "/path/to/clip.mov"
        return ""
    mpi.GetClipProperty.side_effect = get_clip_property
    item.GetMediaPoolItem.return_value = mpi
    def item_get_clip_property(name):
        if name == "Color Group": return "Group1"
        return ""
    item.GetClipProperty.side_effect = item_get_clip_property
    
    item.GetCDL.return_value = {"Slope": "1.0 1.0 1.0"}
    item.GetFusionCompCount.return_value = 0
    item.GetFusionCompNameList.return_value = []
    item.GetMarkers.return_value = {}
    item.GetColorGroup.return_value = ""
    
    timeline.GetItemListInTrack.return_value = [item]
    
    return resolve


def test_diff_function():
    old_state = {
        "tracks": [{
            "clips": [
                {"unique_id": "1", "name": "Clip A", "record_in": 0, "color": {"cdl": {"Slope": "1"}}},
                {"unique_id": "2", "name": "Clip B", "record_in": 10, "color": {"cdl": {"Slope": "1"}}}
            ]
        }]
    }
    
    new_state = {
        "tracks": [{
            "clips": [
                {"unique_id": "1", "name": "Clip A", "record_in": 5, "color": {"cdl": {"Slope": "2"}}},
                {"unique_id": "3", "name": "Clip C", "record_in": 20, "color": {"cdl": {"Slope": "1"}}}
            ]
        }]
    }
    
    with tempfile.NamedTemporaryFile('w', delete=False) as f1, tempfile.NamedTemporaryFile('w', delete=False) as f2:
        json.dump(old_state, f1)
        json.dump(new_state, f2)
        f1_name = f1.name
        f2_name = f2.name
        
    diff = diff_timeline_states(f1_name, f2_name)
    os.remove(f1_name)
    os.remove(f2_name)
    
    assert "Clip C" in diff["added_clips"]
    assert "Clip B" in diff["removed_clips"]
    
    moved = [m for m in diff["moved_clips"] if m["name"] == "Clip A"]
    assert len(moved) == 1
    assert moved[0]["old_in"] == 0
    assert moved[0]["new_in"] == 5
    
    grades = [g for g in diff["changed_grades"] if g["name"] == "Clip A"]
    assert len(grades) == 1
    assert grades[0]["old_cdl"] == {"Slope": "1"}
    assert grades[0]["new_cdl"] == {"Slope": "2"}


def test_named_handle_read_never_touches_the_cursor(mock_resolve):
    """A caller that names its timeline moves no cursor.

    2026-09-20: a snapshot that set the current timeline per name
    walked the cursor across every final unleashed and killed a
    sibling lane's Fusion pass. The `timeline=` handle reads the same
    state with the cursor exactly where it was found.
    """
    project = mock_resolve.GetProjectManager().GetCurrentProject()
    other = MagicMock()
    other.GetName.return_value = "Reel 28"
    project.GetCurrentTimeline.return_value = other

    timeline = MagicMock()
    timeline.GetName.return_value = "Reel 06"
    timeline.GetStartFrame.return_value = 0
    timeline.GetEndFrame.return_value = 100
    timeline.GetStartTimecode.return_value = "01:00:00:00"
    timeline.GetSetting.return_value = ""
    timeline.GetMarkers.return_value = {}
    timeline.GetTrackCount.return_value = 0

    state = serialize_timeline_state(resolve_mock=mock_resolve,
                                     timeline=timeline)
    assert state["metadata"]["name"] == "Reel 06"
    project.SetCurrentTimeline.assert_not_called()
    project.GetCurrentTimeline.assert_not_called()


# --------------------------------------------------------------------------
# From test_otio_compile.py
#
# The measured laws of `library/tools/otio_compile.py`, each one a defect
# that would import a wrong timeline with no error from Resolve.

RATE = 24000 / 1001


def _clip(document, track=0, item=0):
    clips = [c for c in document["tracks"]["children"][track]["children"]
             if c["OTIO_SCHEMA"].startswith("Clip")]
    return clips[item]


def _params(clip):
    effect = clip["effects"][0]["metadata"]["Resolve_OTIO"]
    return {p["Parameter ID"]: p["Parameter Value"] for p in effect["Parameters"]}


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "LC4932.MXF"
    path.write_bytes(b"")
    return str(path)


def test_source_counts_from_the_start_timecode_and_transforms_from_the_frame(media):
    # Reel 09, Akshita at record 132: API startFrame 34305 on a file whose
    # timecode starts at frame 12296 is OTIO source 46601; API Pan -35 and
    # Tilt -1836 on a 1080x1920 frame are -0.032407 and -0.95625.
    track = C.Track("video", "Akshita", [C.Placement(
        media, source_in=34305, frames=392, record_in=132, media_start=12296,
        media_frames=118468,
        transform={"ZoomX": 2.307, "ZoomY": 2.307, "Pan": -35.0,
                   "Tilt": -1836.0})])
    clip = _clip(C.compile_timeline("R", [track], RATE, 1080, 1920))
    assert clip["source_range"]["start_time"]["value"] == 46601
    params = _params(clip)
    assert params["transformationPan"] == pytest.approx(-0.0324074, abs=1e-6)
    assert params["transformationTilt"] == pytest.approx(-0.95625)
    assert params["transformationZoomX"] == pytest.approx(2.307)


def test_a_record_offset_is_a_gap_and_an_overlap_refuses(media):
    first = C.Placement(media, 0, 10, 5, 0, 100)
    document = C.compile_timeline(
        "R", [C.Track("video", "V", [first])], RATE, 1080, 1920)
    gap = document["tracks"]["children"][0]["children"][0]
    assert gap["OTIO_SCHEMA"] == "Gap.1"
    assert gap["source_range"]["duration"]["value"] == 5
    with pytest.raises(C.OtioCompileError, match="overlaps"):
        C.compile_timeline("R", [C.Track("video", "V", [
            first, C.Placement(media, 0, 10, 10, 0, 100)])], RATE, 1080, 1920)


def test_channel_one_is_source_channel_zero_and_another_refuses(media):
    def audio(channel):
        return [C.Track("audio", "Akshita CH1", [C.Placement(
            media, 0, 10, 0, 0, 100, link_group=1, channel=channel)], "Mono")]
    clip = _clip(C.compile_timeline("R", audio(1), RATE, 1080, 1920))
    assert clip["metadata"]["Resolve_OTIO"]["Channels"] == [
        {"Source Channel ID": 0, "Source Track ID": 0}]
    with pytest.raises(C.OtioCompileError, match="channel 2"):
        C.compile_timeline("R", audio(2), RATE, 1080, 1920)


def test_a_missing_file_refuses_by_name_instead_of_a_silent_none(tmp_path):
    gone = str(tmp_path / "sub_gone.mov")
    with pytest.raises(C.OtioCompileError, match="sub_gone.mov"):
        C.compile_timeline("R", [C.Track("video", "Subtitles", [
            C.Placement(gone, 12, 30, 0, 0, 97)])], RATE, 1080, 1920)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_media_start_is_the_container_timecode_at_the_nominal_rate(tmp_path):
    path = tmp_path / "tc.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=64x64:rate=24000/1001:duration=0.2",
         "-timecode", "00:08:32:08", str(path)],
        check=True, capture_output=True, encoding="utf-8")
    assert C.media_start_frame(str(path), RATE) == 12296
    plain = tmp_path / "plain.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=64x64:rate=24:duration=0.2", str(plain)],
        check=True, capture_output=True, encoding="utf-8")
    assert C.media_start_frame(str(plain), 24) == 0
