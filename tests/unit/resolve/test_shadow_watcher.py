"""Continuous edit detection: the watcher, the soft flag, and the build.

The defects these tests name:

- A hand edit between builds was invisible until Ren next touched the
  reel, so a later build could silently overwrite it.
- The watcher fired on no schedule, so detection stayed point-in-time.
- The watcher took an exclusive lease, slowing the captain's live session.
- The soft flag never reached the build, so the build wrote beside a
  live hand edit instead of refusing.
- The watcher's jobs did not use the lowest priority, so they could
  delay the captain or a build.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from library.tools import reel_edit_history, shadow_watcher, timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import FakeTimeline, make_pool_clip, make_project, place_clip


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """A lease of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    shadow_watcher.clear_captain_editing_inference()
    yield directory
    shadow_watcher.clear_captain_editing_inference()


@pytest.fixture
def reel(tmp_path, lock_dir):
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("shadow watcher test"):
        shadow.observe(project, timeline, store)
        yield project, timeline, store, str(tmp_path)


def _mock_broker(monkeypatch, project, timeline, store):
    """Mock the broker so `client.result` runs a real observe."""
    def fake_submit(kind, params, owner=""):
        return {"id": "job-1"}

    def fake_result(job_id, wait=0.0):
        generation = shadow.observe(project, timeline, store)
        return generation.summary()

    monkeypatch.setattr("library.tools.resolved.client.submit", fake_submit)
    monkeypatch.setattr("library.tools.resolved.client.result", fake_result)


def test_watcher_detects_a_hand_edit_between_two_ren_reads(reel, monkeypatch):
    """Defect: a hand edit between builds was invisible until Ren next
    touched the reel. The watcher detects it continuously."""
    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)

    timeline.AddMarker(20, "Red", "hand edit", "", 1)

    watcher = shadow_watcher.ShadowWatcher(store=store)
    detected = watcher.tick("Podcast", project_folder)

    assert len(detected) == 1
    assert detected[0].source == shadow.OBSERVED
    assert detected[0].generation == 2
    assert shadow_watcher.captain_may_be_editing()

    review_dir = str(Path(project_folder) / "pipeline_output" / "review")
    entries = reel_edit_history.recorded_history(review_dir, "Reel 09")
    manual_edits = [e for e in entries if e.get("act") == "manual_edit"]
    assert len(manual_edits) == 1


def test_watcher_is_a_noop_when_the_digest_is_unchanged(reel, monkeypatch):
    """Defect: the watcher filed duplicate manual_edit entries when the
    digest was unchanged, so one edit read as many."""
    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)

    watcher = shadow_watcher.ShadowWatcher(store=store)
    detected = watcher.tick("Podcast", project_folder)

    assert detected == []
    assert not shadow_watcher.captain_may_be_editing()

    review_dir = str(Path(project_folder) / "pipeline_output" / "review")
    entries = reel_edit_history.recorded_history(review_dir, "Reel 09")
    manual_edits = [e for e in entries if e.get("act") == "manual_edit"]
    assert manual_edits == []


def test_watcher_never_takes_an_exclusive_lease(reel, monkeypatch):
    """Defect: the watcher took an exclusive lease, slowing the captain's
    live session. The watcher submits jobs to the broker and never takes
    an exclusive lease itself."""
    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)

    import contextlib
    from library.tools import resolve_lock

    exclusive_calls = []
    original = resolve_lock.resolve_lease

    @contextlib.contextmanager
    def tracking(*args, **kwargs):
        if kwargs.get("exclusive", True):
            exclusive_calls.append(kwargs.get("purpose", ""))
        with original(*args, **kwargs) as lease:
            yield lease

    monkeypatch.setattr(resolve_lock, "resolve_lease", tracking)

    watcher = shadow_watcher.ShadowWatcher(store=store)
    watcher.tick("Podcast", project_folder)

    assert exclusive_calls == []


def test_watcher_fires_on_schedule(reel, monkeypatch):
    """Defect: the watcher fired on no schedule, so detection stayed
    point-in-time. The watcher fires every cadence while a project is
    open and is silent when none is."""
    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)

    ticks = []
    original_tick = shadow_watcher.ShadowWatcher.tick

    def tracking_tick(self, name, folder=""):
        ticks.append(name)
        return original_tick(self, name, folder)

    monkeypatch.setattr(shadow_watcher.ShadowWatcher, "tick", tracking_tick)

    watcher = shadow_watcher.ShadowWatcher(store=store, cadence_seconds=0.01)
    try:
        watcher.start(lambda: "Podcast", lambda: project_folder)
        deadline = time.time() + 2.0
        while not ticks and time.time() < deadline:
            time.sleep(0.01)
        assert ticks == ["Podcast"]
    finally:
        watcher.stop()

    ticks.clear()
    watcher2 = shadow_watcher.ShadowWatcher(store=store, cadence_seconds=0.01)
    try:
        watcher2.start(lambda: None, lambda: "")
        time.sleep(0.05)
        assert ticks == []
    finally:
        watcher2.stop()


def test_soft_flag_expires_so_a_stale_hint_does_not_make_every_build_refuse(
        reel, monkeypatch):
    """Defect: a stale soft flag made every later build refuse, so a build
    long after the edit stopped for nothing. The flag expires."""
    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)

    shadow_watcher.infer_captain_editing(timeline="Reel 09", generation=2,
                                          project="Podcast")
    assert shadow_watcher.captain_may_be_editing()

    flag_path = shadow_watcher.inferred_flag_path()
    record = json.loads(flag_path.read_text(encoding="utf-8"))
    record["since"] = time.time() - (
        shadow_watcher.INFERENCE_TIMEOUT_SECONDS + 1)
    flag_path.write_text(json.dumps(record), encoding="utf-8")

    assert not shadow_watcher.captain_may_be_editing()


def test_clear_captain_editing_inference_removes_the_flag(reel):
    """Defect: the soft flag was never cleared, so every build after the
    first edit refused forever. Clearing removes it."""
    shadow_watcher.infer_captain_editing(timeline="Reel 09", generation=2,
                                          project="Podcast")
    assert shadow_watcher.captain_may_be_editing()

    assert shadow_watcher.clear_captain_editing_inference()
    assert not shadow_watcher.captain_may_be_editing()
    assert not shadow_watcher.clear_captain_editing_inference()


def test_captain_editing_reel_names_the_reel_and_generation(reel):
    """Defect: the build could not name the reel and the observed
    hand-edit generation in its refusal. The flag carries both."""
    shadow_watcher.infer_captain_editing(timeline="Reel 09", generation=7,
                                          project="Podcast")
    details = shadow_watcher.captain_editing_reel()
    assert details is not None
    assert details["timeline"] == "Reel 09"
    assert details["generation"] == 7
    assert details["project"] == "Podcast"


def test_watcher_jobs_use_the_brokers_lowest_priority(reel, monkeypatch):
    """Defect: the watcher's snapshot jobs did not use the lowest
    priority, so they could delay the captain or a build."""
    project, timeline, store, project_folder = reel

    priorities = []

    def fake_submit(kind, params, owner=""):
        priorities.append(params.get("priority", ""))
        return {"id": "job-1"}

    def fake_result(job_id, wait=0.0):
        generation = shadow.observe(project, timeline, store)
        return generation.summary()

    monkeypatch.setattr("library.tools.resolved.client.submit", fake_submit)
    monkeypatch.setattr("library.tools.resolved.client.result", fake_result)

    watcher = shadow_watcher.ShadowWatcher(store=store)
    watcher.tick("Podcast", project_folder)

    assert priorities == [shadow_watcher.BROKER_PRIORITY]
    assert shadow_watcher.BROKER_PRIORITY == "export"
