"""The shadow store numbers distinct timeline states, and only those."""

from __future__ import annotations

import pytest

from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import FakeTimeline, make_pool_clip, make_project, place_clip


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
