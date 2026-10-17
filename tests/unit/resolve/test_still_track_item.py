"""Tests for placing a still image as its own timeline item.

These tests name the defects from the 2026-10-16 incident:
1. track.add returned False (page not Edit, timeline not current)
2. clip.insert produced no timeline item (still needs preference preset)
3. The still must be reversible by its recorded item id
"""

import pytest

from library.tools.resolve_lock import assume_sole_writer
from library.tools.still_track_item import (
    StillTrackItemError,
    place_still_as_track_item,
    remove_still_track_item,
)
from tests.resolve_double import FakeProject, FakeResolve, FakeTimeline


def _still_setup(tmp_path, *, duration_override=None):
    project = FakeProject(name="Still track item test")
    timeline = FakeTimeline("Still track item target", project)
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


class TestPlaceStillAsTrackItem:
    def test_places_still_as_own_timeline_item(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)

        with assume_sole_writer("Resolve still track item double"):
            result = place_still_as_track_item(
                resolve, project, pool, timeline, media_item,
                duration_frames=1464, record_frame=48,
                track_index=2, fps=24000 / 1001,
                timeline_name="Still track item target")

        assert result["start_frame"] == 48
        assert result["duration_frames"] == 1464
        assert result["media_type"] == "Still"
        assert result["media_path"].endswith("overlay.png")
        assert result["track_index"] == 2
        assert result["unique_id"]

    def test_adds_new_track_when_no_index_given(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)

        with assume_sole_writer("Resolve still track item double"):
            result = place_still_as_track_item(
                resolve, project, pool, timeline, media_item,
                duration_frames=100, record_frame=0,
                fps=24000 / 1001,
                timeline_name="Still track item target")

        assert result["track_index"] > 2

    def test_uses_chosen_track_when_index_given(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)

        with assume_sole_writer("Resolve still track item double"):
            result = place_still_as_track_item(
                resolve, project, pool, timeline, media_item,
                duration_frames=100, record_frame=0,
                track_index=2, fps=24000 / 1001,
                timeline_name="Still track item target")

        assert result["track_index"] == 2

    def test_refuses_when_not_on_edit_page(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)
        resolve.OpenPage("color")

        with assume_sole_writer("Resolve still track item double"):
            with pytest.raises(StillTrackItemError, match="Edit page"):
                place_still_as_track_item(
                    resolve, project, pool, timeline, media_item,
                    duration_frames=100, record_frame=0,
                    track_index=2, fps=24000 / 1001,
                    timeline_name="Still track item target")

    def test_makes_timeline_current_before_placing(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)
        other = FakeTimeline("Other", project)
        project.adopt(other)
        project.SetCurrentTimeline(other)

        with assume_sole_writer("Resolve still track item double"):
            result = place_still_as_track_item(
                resolve, project, pool, timeline, media_item,
                duration_frames=100, record_frame=0,
                track_index=2, fps=24000 / 1001,
                timeline_name="Still track item target")

        assert result["start_frame"] == 0
        assert project.GetCurrentTimeline() is timeline

    def test_refuses_negative_track_index(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)

        with assume_sole_writer("Resolve still track item double"):
            with pytest.raises(StillTrackItemError, match="positive"):
                place_still_as_track_item(
                    resolve, project, pool, timeline, media_item,
                    duration_frames=100, record_frame=0,
                    track_index=-1, fps=24000 / 1001,
                    timeline_name="Still track item target")


class TestRemoveStillTrackItem:
    def test_removes_item_by_unique_id(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)

        with assume_sole_writer("Resolve still track item double"):
            result = place_still_as_track_item(
                resolve, project, pool, timeline, media_item,
                duration_frames=100, record_frame=0,
                track_index=2, fps=24000 / 1001,
                timeline_name="Still track item target")

        removed = remove_still_track_item(
            timeline, result["track_index"], result["unique_id"],
            timeline_name="Still track item target")
        assert removed is True

        items = timeline.GetItemListInTrack("video", 2) or []
        for item in items:
            assert str(item.GetUniqueId()) != result["unique_id"]

    def test_remove_returns_false_for_unknown_id(self, tmp_path):
        resolve, project, pool, timeline, media_item = _still_setup(tmp_path)

        removed = remove_still_track_item(
            timeline, 2, "nonexistent-id",
            timeline_name="Still track item target")
        assert removed is False
