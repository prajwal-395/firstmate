"""Exact native still placement through Resolve's preference preset API."""

import pytest

from library.tools.resolve_lock import assume_sole_writer
from library.tools.still_placement import (
    StillPlacementRefused,
    place_still_exact,
)
from tests.resolve_double import FakeProject, FakeResolve, FakeTimeline


def _still_setup(tmp_path, *, duration_override=None):
    project = FakeProject(name="Still placement test")
    timeline = FakeTimeline("Still placement target", project)
    project.adopt(timeline)
    project.SetCurrentTimeline(timeline)
    timeline._ensure_track("video", 2)
    path = tmp_path / "overlay.png"
    path.write_bytes(b"png fixture")
    pool = project.GetMediaPool()
    media_item = pool.ImportMedia([str(path)])[0]
    pool.still_duration_override = duration_override
    resolve = FakeResolve(project)
    return resolve, project, pool, timeline, media_item


def test_places_png_still_at_requested_frames_and_restores_all_preferences(
        tmp_path):
    resolve, project, pool, timeline, media_item = _still_setup(tmp_path)
    resolve._preferences["Language"] = "fr"
    resolve.SaveUserPreferencesPreset("captain_custom")
    original = dict(resolve._preferences)
    original_names = resolve.GetUserPreferencesPresetList()

    with assume_sole_writer("Resolve still placement double"):
        record = place_still_exact(
            resolve, project, pool, timeline, media_item,
            duration_frames=1464, record_frame=48, track_index=2,
            fps=24000 / 1001, timeline_name="Still placement target")

    assert record["start_frame"] == 48
    assert record["duration_frames"] == 1464
    assert record["media_type"] == "Still"
    assert record["media_path"].endswith("overlay.png")
    assert resolve._preferences == original
    assert resolve.GetUserPreferencesPresetList() == original_names
    assert project._still_duration_frames == 120
    items = timeline.GetItemListInTrack("video", 2)
    assert len(items) == 1
    assert items[0].GetStart() == 48
    assert items[0].GetDuration() == 1464


def test_wrong_still_duration_refuses_after_restoring_preferences(tmp_path):
    resolve, project, pool, timeline, media_item = _still_setup(
        tmp_path, duration_override=120)
    original = dict(resolve._preferences)

    with assume_sole_writer("Resolve still placement double"):
        with pytest.raises(StillPlacementRefused,
                           match="reads 120 frames; requested 60"):
            place_still_exact(
                resolve, project, pool, timeline, media_item,
                duration_frames=60, record_frame=0, track_index=2,
                fps=24000 / 1001, timeline_name="Still placement target")

    assert resolve._preferences == original
    assert resolve.GetUserPreferencesPresetList() == []
    assert project._still_duration_frames == 120
