"""The shadow store numbers distinct timeline states, and only those.
"""
from __future__ import annotations
import pytest
from library.tools import timeline_shadow as shadow
from library.tools import timeline_read
from library.tools import edit_patch
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)
import json
import os
import sqlite3
import tempfile
import time
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


def test_shadow_kpi_counts_are_bounded_to_a_benchmark_window(tmp_path):
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with store._connect() as db:
        db.execute("INSERT INTO answers (answered_at, command, project,"
                   " timeline_id) VALUES (?, ?, ?, ?)",
                   (10.0, "clips", "Podcast", "timeline:1"))
        db.execute("INSERT INTO answers (answered_at, command, project,"
                   " timeline_id) VALUES (?, ?, ?, ?)",
                   (20.0, "markers", "Podcast", "timeline:1"))
        db.execute("INSERT INTO read_requests (requested_at, command,"
                   " project, timeline_id, outcome)"
                   " VALUES (?, ?, ?, ?, ?)",
                   (10.0, "clips", "Podcast", "timeline:1", "shadow_hit"))
        db.execute("INSERT INTO read_requests (requested_at, command,"
                   " project, timeline_id, outcome)"
                   " VALUES (?, ?, ?, ?, ?)",
                   (20.0, "markers", "Podcast", "timeline:1", "miss"))

    assert store.answers_since(0, until=15) == 1
    assert store.answers_since(0, until=25) == 2
    assert store.read_requests_since(0, until=15) == {
        "shadow_hits": 1, "live_refreshes": 0, "misses": 0,
    }
    assert store.read_requests_since(0, until=25) == {
        "shadow_hits": 1, "live_refreshes": 0, "misses": 1,
    }


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


def test_a_transform_question_is_answered_from_the_shadow_with_the_timeline_not_current(
        tmp_path):
    # Defect: a transform question with the timeline not current was answered
    # by a live read under the lease (`read_live` requires the timeline to be
    # current), so it counted as a live_refresh, not a shadow_hit. The shadow
    # stores transforms in canonical form, so the question is answerable
    # without a live read.
    project = make_project("Podcast")
    reel_a = project.adopt(FakeTimeline("Reel A", project=project))
    reel_b = project.adopt(FakeTimeline("Reel B", project=project))
    item = place_clip(reel_b, make_pool_clip("a.mov"), 0, 47)
    item.properties = {"Pan": -24.0, "Tilt": -1728.0, "ZoomX": 1.0,
                       "ZoomY": 1.0}
    store = shadow.ShadowStore(tmp_path / "shadow.db")

    project.SetCurrentTimeline(reel_b)
    with assume_sole_writer("canonical Resolve double"):
        shadow.observe(project, reel_b, store)
    assert store.read_requests_since(0) == {
        "shadow_hits": 0, "live_refreshes": 1, "misses": 0}

    project.SetCurrentTimeline(reel_a)
    assert project.GetCurrentTimeline() is reel_a

    transform = timeline_read.read_transform(
        "Podcast", "Reel B", item.GetUniqueId(), store=store)

    assert transform == {"Pan": -24.0, "Tilt": -1728.0,
                         "ZoomX": 1.0, "ZoomY": 1.0}
    assert store.read_requests_since(0) == {
        "shadow_hits": 1, "live_refreshes": 1, "misses": 0}


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
    # Reel 09, SpeakerOne at record 132: API startFrame 34305 on a file whose
    # timecode starts at frame 12296 is OTIO source 46601; API Pan -35 and
    # Tilt -1836 on a 1080x1920 frame are -0.032407 and -0.95625.
    track = C.Track("video", "SpeakerOne", [C.Placement(
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
        return [C.Track("audio", "SpeakerOne CH1", [C.Placement(
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


# --------------------------------------------------------------------------
# F-11: the shadow is project state, not machine-local runtime state.
#
# The defect: the shadow lived only in one machine's SQLite store, so a
# second machine (or a restarted Ren) had no generations and had to
# re-read every timeline live. These tests pin the mirror that makes the
# shadow travel with the project git repo.

def _machine_a_store(tmp_path):
    """Machine A: a Resolve double whose timeline is observed twice."""
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    timeline.AddMarker(10, "Blue", "note", "keep", 1)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "machine_a.db")
    with assume_sole_writer("canonical Resolve double"):
        shadow.observe(project, timeline, store)
        timeline.AddMarker(20, "Red", "hand edit", "", 1)
        shadow.observe(project, timeline, store)
    return project, timeline, store


def test_a_second_machine_sees_the_same_generations_after_sync(tmp_path):
    # Defect: a second machine had no shadow and had to re-read every
    # timeline live. After the mirror is committed and synced, a fresh
    # store answers from the same generations with no live read.
    project, timeline, store_a = _machine_a_store(tmp_path)
    mirror = tmp_path / "project"
    shadow.export_project("Podcast", mirror, store=store_a)
    assert shadow.project_shadow_path(mirror).is_file()

    store_b = shadow.ShadowStore(tmp_path / "machine_b.db")
    report = shadow.sync_project(mirror, store=store_b)
    assert report["generations"] == 2

    timeline_id = store_a.resolve_timeline("Podcast", "Reel 09")
    head = store_b.head("Podcast", timeline_id)
    assert head is not None and head.generation == 2
    assert head.source == shadow.OBSERVED

    read = timeline_read.read("Podcast", "Reel 09", store=store_b)
    assert read.generation.generation == 2
    assert store_b.read_requests_since(0) == {
        "shadow_hits": 1, "live_refreshes": 0, "misses": 0}


def test_a_sync_is_idempotent_and_never_clobbers_local_data(tmp_path):
    # Defect: a sync that rewrites what is already there would let a
    # second machine's older data regress a machine's own newer head.
    project, timeline, store_a = _machine_a_store(tmp_path)
    mirror = tmp_path / "project"
    shadow.export_project("Podcast", mirror, store=store_a)

    store_b = shadow.ShadowStore(tmp_path / "machine_b.db")
    shadow.sync_project(mirror, store=store_b)
    timeline_id = store_a.resolve_timeline("Podcast", "Reel 09")
    head_before = store_b.head("Podcast", timeline_id).generation

    again = shadow.sync_project(mirror, store=store_b)
    assert again["generations"] == 0 and again["snapshots"] == 0
    assert store_b.head("Podcast", timeline_id).generation == head_before


def test_a_write_still_rereads_live_after_a_sync(tmp_path):
    # Defect: if the mirror made the shadow a write precondition, a
    # write would trust the synced snapshot instead of re-reading the
    # live timeline. The write path must re-read live regardless of what
    # the sync pulled in.
    project, timeline, store_a = _machine_a_store(tmp_path)
    mirror = tmp_path / "project"
    shadow.export_project("Podcast", mirror, store=store_a)

    store_b = shadow.ShadowStore(tmp_path / "machine_b.db")
    shadow.sync_project(mirror, store=store_b)

    calls = {"live": 0}
    real_read_live = shadow.read_live

    def counting_read_live(*args, **kwargs):
        calls["live"] += 1
        return real_read_live(*args, **kwargs)

    patch = {
        "id": "p-after-sync", "project": "Podcast", "timeline": "Reel 09",
        "base_generation": 2, "capability": "reel.touchup",
        "affected_spans": [[0, 48]], "conflict_domains": ["markers"],
        "operations": [{"op": "marker.add", "frame": 30, "color": "Blue",
                         "name": "beat"}],
    }
    with assume_sole_writer("canonical Resolve double"):
        import library.tools.timeline_shadow as ts
        original = ts.read_live
        ts.read_live = counting_read_live
        try:
            receipt = edit_patch.apply_patch(
                patch, resolve=FakeResolve(project), project=project,
                timeline=timeline, store=store_b,
                project_folder=str(tmp_path))
        finally:
            ts.read_live = original
    assert calls["live"] >= 1
    assert receipt["status"] in ("committed", "verification_failed")


def test_a_sync_of_a_twenty_reel_project_is_under_50ms(tmp_path):
    # Defect: a sync that reads and writes more than the mirror holds
    # would make opening a project on a second machine slower than a
    # live read. One read of the mirror, merged, is the bound.
    store_a = shadow.ShadowStore(tmp_path / "machine_a.db")
    for reel in range(20):
        store_a.record(
            project="Podcast", timeline_id=f"timeline:{reel}",
            timeline_name=f"Reel {reel:02d}",
            snapshot={"timeline": f"Reel {reel:02d}",
                       "tracks": [{"clips": [{"unique_id": f"c{reel}"}]}]},
            source=shadow.OBSERVED, expected_head=0)
    mirror = tmp_path / "project"
    shadow.export_project("Podcast", mirror, store=store_a)

    # Use the best of several identical syncs: process scheduling and
    # parallel machine load can delay an otherwise fast operation, while
    # the minimum still measures its uncontended wall-clock cost. Each
    # sample gets a fresh destination so it measures the full 20-reel merge.
    samples_ms = []
    for sample in range(5):
        store_b = shadow.ShadowStore(tmp_path / f"machine_b_{sample}.db")
        start = time.monotonic()
        report = shadow.sync_project(mirror, store=store_b)
        samples_ms.append((time.monotonic() - start) * 1000)

    assert report["generations"] == 20
    best_ms = min(samples_ms)
    assert best_ms < 50.0, f"best sync took {best_ms:.1f}ms; samples={samples_ms}"


def test_a_divergent_generation_is_reported_and_the_local_one_kept(
        tmp_path):
    """A generation number both machines hold with different hashes is
    a real divergence. The merge reports it and keeps this machine's
    own observation - the shadow is a cache, not a source of truth, so
    a conflict is surfaced for a reader, never silently resolved.
    """
    project, timeline, store_a = _machine_a_store(tmp_path)
    mirror = tmp_path / "project"
    shadow.export_project("Podcast", mirror, store=store_a)

    store_b = shadow.ShadowStore(tmp_path / "machine_b.db")
    timeline_id = store_a.resolve_timeline("Podcast", "Reel 09")
    store_b.record(project="Podcast", timeline_id=timeline_id,
                   timeline_name="Reel 09", snapshot={"diverged": True},
                   source=shadow.OBSERVED, expected_head=0)
    document = store_a.export_document("Podcast")
    report = store_b.merge_document(document)

    assert report["generations"] == 1
    assert len(report["conflicts"]) == 1
    conflict = report["conflicts"][0]
    assert conflict["generation"] == 1
    assert conflict["local_hash"] == shadow.snapshot_hash({"diverged": True})
    assert conflict["mirror_hash"] == store_a.get("Podcast", timeline_id, 1).hash
    # The local observation is kept, not overwritten by the mirror.
    assert store_b.get("Podcast", timeline_id, 1).hash == \
        shadow.snapshot_hash({"diverged": True})


# ── 2026-10-10: named INSERTs, and sessions that do not migrate ────────
#
# An unmerged lane's live proof ALTERed the shared shadow DB, and main's
# positional `INSERT INTO generations VALUES (10 ...)` then broke every
# lane with "12 columns but 10 values". Every INSERT here names its
# columns, and a borrowed session refuses to migrate the shared store.


def _old_schema_db(path):
    """A pre-Lane-A3 store file: 10-column generations, no model linkage."""
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE snapshots (hash TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE generations (
            project TEXT NOT NULL, timeline_id TEXT NOT NULL,
            generation INTEGER NOT NULL, timeline_name TEXT NOT NULL,
            hash TEXT NOT NULL, source TEXT NOT NULL, patch TEXT,
            receipt TEXT, recorded_at REAL NOT NULL,
            verified_at REAL NOT NULL,
            PRIMARY KEY (project, timeline_id, generation));
    """)
    db.execute("INSERT INTO snapshots (hash, body)"
               " VALUES ('h', '{\"a\":1}')")
    db.execute("INSERT INTO generations (project, timeline_id, generation,"
               " timeline_name, hash, source, patch, receipt, recorded_at,"
               " verified_at) VALUES "
               "('P', 't', 1, 'T', 'h', 'observed', NULL, NULL, 1.0, 2.0)")
    db.commit()
    db.close()


def test_writers_survive_columns_they_do_not_know(tmp_path):
    """The incident replay: the table is wider than the writer, and every
    row still lands - because every INSERT names its columns."""
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with store._connect() as db:
        db.execute("ALTER TABLE answers ADD COLUMN extra TEXT")
        db.execute("ALTER TABLE read_requests ADD COLUMN extra TEXT")
        db.execute("ALTER TABLE snapshots ADD COLUMN extra TEXT")
        db.execute("ALTER TABLE generations ADD COLUMN extra TEXT")
    store.record(project="P", timeline_id="t", timeline_name="T",
                 snapshot={"a": 1}, source=shadow.OBSERVED, expected_head=0,
                 model_revision=3, nle_binding_ids=["nle:x"])
    store.answered("clips", "P", "t")
    store.record_read_request("clips", "P", "t", "shadow_hit")
    assert store.get("P", "t", 1).model_revision == 3
    assert store.answers_since(0) == 1
    assert store.read_requests_since(0)["shadow_hits"] == 1

    document = store.export_document("P")
    wider = shadow.ShadowStore(tmp_path / "wider.db")
    with wider._connect() as db:
        db.execute("ALTER TABLE snapshots ADD COLUMN extra TEXT")
        db.execute("ALTER TABLE generations ADD COLUMN extra TEXT")
    report = wider.merge_document(document)
    assert report == {"generations": 1, "snapshots": 1, "conflicts": []}
    assert wider.get("P", "t", 1).model_revision == 3


def test_a_session_refuses_to_migrate_the_shared_store(tmp_path,
                                                       monkeypatch):
    """Inside a borrowed session the DEFAULT store refuses the ALTER -
    naming the copied-DB remedy - and the file stays unmigrated."""
    from library.tools import qualification_project as qp
    from library.tools import resolve_lock

    shared = tmp_path / "shared.sqlite3"
    _old_schema_db(shared)
    monkeypatch.setattr(shadow, "default_db_path", lambda: shared)
    monkeypatch.delenv(shadow.DB_ENV, raising=False)
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "locks"))
    qp.begin_qualification_session("Ren Qualification")
    try:
        with pytest.raises(shadow.SharedMigrationRefused,
                           match="REN_SHADOW_DB"):
            shadow.ShadowStore()
    finally:
        qp.end_qualification_session()
    names = [row[1] for row in sqlite3.connect(shared).execute(
        "PRAGMA table_info(generations)")]
    assert "model_revision" not in names


def test_a_session_may_migrate_a_store_it_names(tmp_path, monkeypatch):
    """The remedy the refusal names: a proof that carries a migration
    runs it against its own copy."""
    from library.tools import qualification_project as qp
    from library.tools import resolve_lock

    copy = tmp_path / "copy.sqlite3"
    _old_schema_db(copy)
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "locks"))
    qp.begin_qualification_session("Ren Qualification")
    try:
        store = shadow.ShadowStore(copy)
        joined = store.attach_model_link(
            project="P", timeline_id="t", generation=1, model_revision=3,
            nle_binding_ids=["nle:x"])
    finally:
        qp.end_qualification_session()
    assert (joined.model_revision, joined.nle_binding_ids) == (3, ("nle:x",))


def test_the_default_store_migrates_outside_a_session(tmp_path,
                                                     monkeypatch):
    """No session, no refusal: the ordinary first run after an upgrade
    still migrates the shared store in place."""
    shared = tmp_path / "shared.sqlite3"
    _old_schema_db(shared)
    monkeypatch.setattr(shadow, "default_db_path", lambda: shared)
    monkeypatch.delenv(shadow.DB_ENV, raising=False)
    store = shadow.ShadowStore()
    assert store.get("P", "t", 1).model_revision is None


# ── The stale-worker guard ────────────────────────────────────────────
#
# On 2026-10-11 a broker worker running pre-Lane-A3 code failed every
# first-record shadow write machine-wide with `OperationalError: table
# generations has 12 columns but 10 values were supplied`, while
# re-reads of known timelines still succeeded - so every other lane
# looked healthy for a day. A worker whose code predates the shadow DB
# schema now refuses with the restart remedy instead of failing writes,
# and the broker turns that into a rejected job.

def _newer_schema_db(path):
    """A store file stamped newer than this worker's code: the DB a
    future migration leaves behind for an older worker to find."""
    store = shadow.ShadowStore(path)
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version={shadow.SHADOW_SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    return store


def test_a_worker_older_than_the_db_refuses_before_any_write(tmp_path,
                                                             monkeypatch):
    """The incident's shape: the DB is v2 and the worker reports v1.

    The open itself refuses - naming both versions and the broker
    restart - so no first record is ever attempted, and the file is
    left untouched for current code to keep serving."""
    current = tmp_path / "current.sqlite3"
    shadow.ShadowStore(current)
    monkeypatch.setattr(shadow, "SHADOW_SCHEMA_VERSION", 1)
    with pytest.raises(shadow.StaleShadowWorker, match="Restart the"):
        shadow.ShadowStore(current)
    try:
        with pytest.raises(shadow.StaleShadowWorker) as refused:
            shadow.check_schema_current(current)
    finally:
        monkeypatch.undo()
    assert "v2" in str(refused.value) and "v1" in str(refused.value)
    # Untouched: current code still opens it and reads what it wrote.
    store = shadow.ShadowStore(current)
    store.record(project="P", timeline_id="t", timeline_name="T",
                 snapshot={"a": 1}, source=shadow.OBSERVED, expected_head=0)
    assert store.get("P", "t", 1).hash == shadow.snapshot_hash({"a": 1})


def test_a_db_stamped_newer_than_the_worker_refuses_first_record(tmp_path):
    """A future migration's stamp refuses this worker at the boundary,
    instead of failing its first write with a column-count error."""
    newer = tmp_path / "newer.sqlite3"
    _newer_schema_db(newer)
    with pytest.raises(shadow.StaleShadowWorker, match="Restart the"):
        shadow.ShadowStore(newer)
    with pytest.raises(shadow.StaleShadowWorker, match="v3"):
        shadow.check_schema_current(newer)
    assert shadow.check_schema_current(tmp_path / "missing.sqlite3") == 0


def test_the_incidents_positional_insert_becomes_a_clear_refusal(tmp_path):
    """The exact silent failure, replayed: a pre-A3 positional 10-value
    INSERT against the 12-column table raises OperationalError today,
    and the write path translates it into the restart remedy."""
    path = tmp_path / "incident.sqlite3"
    shadow.ShadowStore(path)
    db = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError, match="12 columns but 10"):
        db.execute("INSERT INTO generations VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("P", "t", 1, "T", "h", "observed", None, None, 1.0, 2.0))
    try:
        db.execute("INSERT INTO generations VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("P", "t", 1, "T", "h", "observed", None, None, 1.0, 2.0))
    except sqlite3.OperationalError as exc:
        translated = shadow._translate_column_mismatch(exc, path)
    assert isinstance(translated, shadow.StaleShadowWorker)
    assert "Restart the" in str(translated)
    # Anything else passes through untouched - the translator only
    # converts the column-count mismatch, never a real constraint.
    other = sqlite3.OperationalError("no such table: generations")
    assert shadow._translate_column_mismatch(other, path) is other


def test_current_code_stamps_fresh_and_migrated_stores(tmp_path):
    """The guard never fires on the ordinary paths: a fresh store is
    stamped at this worker's version, and a pre-A3 store migrates and
    stamps in place before its first write lands."""
    fresh = tmp_path / "fresh.sqlite3"
    shadow.ShadowStore(fresh)
    assert sqlite3.connect(fresh).execute(
        "PRAGMA user_version").fetchone()[0] == shadow.SHADOW_SCHEMA_VERSION

    migrated = tmp_path / "migrated.sqlite3"
    _old_schema_db(migrated)
    store = shadow.ShadowStore(migrated)
    assert sqlite3.connect(migrated).execute(
        "PRAGMA user_version").fetchone()[0] == shadow.SHADOW_SCHEMA_VERSION
    store.record(project="P", timeline_id="t2", timeline_name="T2",
                 snapshot={"b": 2}, source=shadow.OBSERVED, expected_head=0)
    assert store.get("P", "t2", 1).model_revision is None


def test_a_shadow_job_refuses_when_the_worker_is_stale(tmp_path,
                                                       monkeypatch):
    """The broker's half: a stale write inside `jobs.run` is a rejected
    job carrying the restart remedy, never a failed one."""
    from library.tools.resolved import jobs as job_kinds

    def stale_impl(job, resolve):
        raise shadow.StaleShadowWorker(
            "stale shadow worker: the shadow DB at X is schema v9, but "
            "this worker's code writes v2 and predates it. Restart the "
            "Resolve broker from the current tree.")

    monkeypatch.setattr(job_kinds, "_run_impl", stale_impl)
    with pytest.raises(job_kinds.JobRefused, match="Restart the"):
        job_kinds.run({"id": "j1", "kind": "timeline.snapshot",
                       "params": {"project": "P", "timeline": "T"}},
                      object())


def test_the_worker_reports_its_shadow_schema_version():
    """The worker's report: a supervisor compares this against the DB's
    version to find a stale worker without running a Resolve job."""
    from library.tools.resolved import worker

    reply = worker.execute({"id": "v1", "kind": "version"})
    assert reply["ok"] is True
    assert reply["result"] == {
        "shadow_schema_version": shadow.SHADOW_SCHEMA_VERSION}


def test_the_worker_refuses_a_stale_shadow_write(tmp_path, monkeypatch):
    """End to end on the Rust-daemon worker path: the real store guard
    fires inside the job, `jobs.run` converts it, and the reply is a
    refusal naming the restart - the receipt reads `rejected`."""
    from contextlib import contextmanager

    from library.tools import resolve_lock
    from library.tools.resolved import cancellation
    from library.tools.resolved import jobs as job_kinds
    from library.tools.resolved import worker

    stale = tmp_path / "stale.sqlite3"
    _newer_schema_db(stale)
    monkeypatch.setattr(shadow, "default_db_path", lambda: stale)
    monkeypatch.delenv(shadow.DB_ENV, raising=False)

    @contextmanager
    def _lease(purpose, exclusive=False):
        yield

    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)
    monkeypatch.setattr(cancellation, "begin", lambda job_id: None)
    monkeypatch.setattr(cancellation, "clear", lambda job_id: None)
    monkeypatch.setattr(cancellation, "end", lambda job_id: None)
    monkeypatch.setattr(worker, "_connect_cached", lambda: object())

    def guarded_impl(job, resolve):
        return shadow.ShadowStore().record(
            project="P", timeline_id="t", timeline_name="T",
            snapshot={"a": 1}, source=shadow.OBSERVED, expected_head=0)

    monkeypatch.setattr(job_kinds, "_run_impl", guarded_impl)
    reply = worker._execute_job({"id": "j-stale", "kind": "timeline.snapshot",
                                 "mode": "exclusive", "executed": True,
                                 "qualification": False})

    assert reply["ok"] is False
    assert reply.get("refused") is True
    assert "restart the" in reply["error"].lower() or \
        "Restart the" in reply["error"]
