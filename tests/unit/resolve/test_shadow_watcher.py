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
        return {"id": job_id, "state": "done", "result": generation.summary()}

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
        watcher.start(lambda: "Podcast", lambda name: project_folder)
        deadline = time.time() + 2.0
        while len(ticks) < 3 and time.time() < deadline:
            time.sleep(0.01)
        assert len(ticks) >= 3, "the watcher did not keep firing on its cadence"
        assert all(name == "Podcast" for name in ticks)
    finally:
        watcher.stop()

    ticks.clear()
    watcher2 = shadow_watcher.ShadowWatcher(store=store, cadence_seconds=0.01)
    try:
        watcher2.start(lambda: None, lambda name: "")
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
        return {"id": job_id, "state": "done", "result": generation.summary()}

    monkeypatch.setattr("library.tools.resolved.client.submit", fake_submit)
    monkeypatch.setattr("library.tools.resolved.client.result", fake_result)

    watcher = shadow_watcher.ShadowWatcher(store=store)
    watcher.tick("Podcast", project_folder)

    assert priorities == [shadow_watcher.BROKER_PRIORITY]
    assert shadow_watcher.BROKER_PRIORITY == "export"


def test_watcher_writes_a_heartbeat_so_a_dead_watcher_is_visible(
        reel, monkeypatch):
    """Defect: the watcher died silently - no heartbeat, no log, no
    supervisor. Nothing reported the death. The watcher writes a
    heartbeat every tick so `ren doctor` can name a dead watcher."""
    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)

    watcher = shadow_watcher.ShadowWatcher(store=store, cadence_seconds=0.01)
    try:
        watcher.start(lambda: "Podcast", lambda name: project_folder)
        deadline = time.time() + 2.0
        while not shadow_watcher.read_heartbeat() and time.time() < deadline:
            time.sleep(0.01)
        heartbeat = shadow_watcher.read_heartbeat()
        assert heartbeat is not None
        assert heartbeat["project"] == "Podcast"
        assert heartbeat["cadence"] == 0.01
    finally:
        watcher.stop()


def test_watcher_heartbeat_not_written_when_tick_fails(reel, monkeypatch):
    """Defect: the heartbeat was written even when tick() raised, so a
    watcher whose every tick fails read as alive. The heartbeat reflects
    the last successful observation."""
    project, timeline, store, project_folder = reel

    def failing_submit(kind, params, owner=""):
        raise RuntimeError("broker gone")

    monkeypatch.setattr("library.tools.resolved.client.submit", failing_submit)

    watcher = shadow_watcher.ShadowWatcher(store=store, cadence_seconds=0.01)
    try:
        watcher.start(lambda: "Podcast", lambda name: project_folder)
        time.sleep(0.1)
        assert shadow_watcher.read_heartbeat() is None
    finally:
        watcher.stop()


def test_doctor_names_a_dead_watcher(reel, monkeypatch):
    """Defect: a dead watcher was invisible - `ren doctor` did not
    mention it. Doctor names the watcher's state: alive, running but
    not recording, or dead."""
    from ren import doctor

    monkeypatch.setattr(doctor, "_engine_root", lambda: Path("/nonexistent"))

    check = doctor.shadow_watcher_check()
    assert check.name == "shadow watcher"
    assert not check.ok

    shadow_watcher.write_heartbeat(project_name="Podcast",
                                   project_folder="/projects/podcast",
                                   cadence=30.0)
    check = doctor.shadow_watcher_check()
    assert check.ok
    assert "alive" in check.detail
    assert "Podcast" in check.detail

    heartbeat_path = shadow_watcher.heartbeat_path()
    record = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    record["since"] = time.time() - 120.0
    heartbeat_path.write_text(json.dumps(record), encoding="utf-8")

    check = doctor.shadow_watcher_check()
    assert not check.ok
    assert "running but not recording" in check.detail

    heartbeat_path.unlink()
    check = doctor.shadow_watcher_check()
    assert not check.ok


def test_main_hands_its_project_folder_to_every_tick(reel, monkeypatch):
    """Defects: main() passed a no-argument folder factory to start(), so
    every tick raised TypeError; and it asked Resolve for the open project
    outside the instance lease, which is refused, so it never ticked at
    all. Drives main() itself: the project comes from the folder's
    project.yaml and its tick runs on that folder."""
    import yaml

    project, timeline, store, project_folder = reel
    _mock_broker(monkeypatch, project, timeline, store)
    (Path(project_folder) / "project.yaml").write_text(yaml.safe_dump({
        "slug": "podcast", "name": "Podcast",
        "resolve": {"project_name": "Podcast"},
    }), encoding="utf-8")

    names = []
    monkeypatch.setenv(shadow.DB_ENV, str(Path(project_folder) / "shadow.db"))

    ticked = threading.Event()
    folders = []
    original_tick = shadow_watcher.ShadowWatcher.tick

    def tracking_tick(self, name, folder=""):
        names.append(name)
        folders.append(folder)
        try:
            return original_tick(self, name, folder)
        finally:
            ticked.set()

    monkeypatch.setattr(shadow_watcher.ShadowWatcher, "tick", tracking_tick)

    def sleep_until_ticked(seconds):
        ticked.wait(5.0)
        raise KeyboardInterrupt

    monkeypatch.setattr(shadow_watcher, "_sleep", sleep_until_ticked)

    assert shadow_watcher.main([project_folder, "--cadence", "60"]) == 0
    assert names == ["Podcast"]
    assert folders == [project_folder]
    assert shadow_watcher.read_heartbeat()["project_folder"] == project_folder


def test_a_snapshot_job_that_fails_fails_the_tick(reel, monkeypatch):
    """Defect: tick() read the job record as though it were the snapshot,
    so a failed job (Resolve closed, project gone) returned normally and
    the loop wrote a heartbeat for a watcher that observed nothing."""
    project, timeline, store, project_folder = reel

    monkeypatch.setattr("library.tools.resolved.client.submit",
                        lambda kind, params, owner="": {"id": "job-1"})
    monkeypatch.setattr(
        "library.tools.resolved.client.result",
        lambda job_id, wait=0.0: {"id": job_id, "state": "failed",
                                  "error": "Resolve is not running"})

    watcher = shadow_watcher.ShadowWatcher(store=store, cadence_seconds=0.01)
    with pytest.raises(shadow_watcher.WatcherTickFailed,
                       match="Resolve is not running"):
        watcher.tick("Podcast", project_folder)

    try:
        watcher.start(lambda: "Podcast", lambda name: project_folder)
        time.sleep(0.1)
        assert shadow_watcher.read_heartbeat() is None
    finally:
        watcher.stop()


def test_a_reel_resolve_no_longer_has_does_not_stop_the_others(
        reel, monkeypatch):
    """Defect, seen live: the store keeps a head for every timeline ever
    observed, including promoted staging timelines Resolve has deleted.
    The broker rejects those snapshots, and the first one failed the
    whole tick - so on a real project the watcher never recorded at all.
    A gone reel is skipped; a tick where every reel is rejected (another
    project is open) still fails."""
    project, timeline, store, project_folder = reel
    gone = project.adopt(FakeTimeline("Reel 08 (rebuild staging)",
                                      project=project))
    place_clip(gone, make_pool_clip("b.mov"), 0, 47)
    project.SetCurrentTimeline(gone)
    with assume_sole_writer("shadow watcher test"):
        shadow.observe(project, gone, store)
    project.SetCurrentTimeline(timeline)

    def submit(kind, params, owner=""):
        return {"id": params["timeline"]}

    def result(job_id, wait=0.0):
        if job_id == "Reel 08 (rebuild staging)":
            return {"id": job_id, "state": "rejected",
                    "error": f"no timeline named exactly {job_id!r}"}
        with assume_sole_writer("shadow watcher test"):
            summary = shadow.observe(project, timeline, store).summary()
        return {"id": job_id, "state": "done", "result": summary}

    monkeypatch.setattr("library.tools.resolved.client.submit", submit)
    monkeypatch.setattr("library.tools.resolved.client.result", result)

    timeline.AddMarker(20, "Red", "hand edit", "", 1)
    watcher = shadow_watcher.ShadowWatcher(store=store)
    detected = watcher.tick("Podcast", project_folder)
    assert [g.timeline_name for g in detected] == ["Reel 09"]

    monkeypatch.setattr(
        "library.tools.resolved.client.result",
        lambda job_id, wait=0.0: {"id": job_id, "state": "rejected",
                                  "error": "Resolve has 'Other' open"})
    with pytest.raises(shadow_watcher.WatcherTickFailed, match="Other"):
        watcher.tick("Podcast", project_folder)


def test_broker_hosts_the_watcher_and_records_for_the_open_project(
        tmp_path, monkeypatch):
    """Defect: the watcher was opt-in and nothing ever started it, so
    hand edits were never recorded. The broker hosts the watcher: it
    starts when the broker starts and records for the open project.

    This test would have caught the signature mismatch (factory called
    with no args) and the wrong mapping (project_root takes a slug, not
    a Resolve project name).
    """
    import threading

    import yaml

    from library.tools.resolved import client, server as server_mod
    from library.tools.resolve_lock import assume_sole_writer
    from tests.resolve_double import FakeResolve, FakeTimeline, make_pool_clip, make_project, place_clip

    projects_root = tmp_path / "projects"
    project_dir = projects_root / "geo-podcast"
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(yaml.safe_dump({
        "slug": "geo-podcast",
        "name": "Podcast (field test)",
        "resolve": {"project_name": "Podcast (field test)"},
    }), encoding="utf-8")
    from library.tools import project_registry
    monkeypatch.setattr(project_registry, "PROJECTS_ROOT", projects_root)

    import tempfile
    lock_dir = Path(tempfile.mkdtemp(prefix="ren-watch-", dir="/tmp"))
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(lock_dir))
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.db"))
    monkeypatch.delenv(shadow_watcher.HOSTED_ENV)
    shadow_watcher.clear_captain_editing_inference()

    project = make_project("Podcast (field test)")
    timeline = project.adopt(FakeTimeline("Reel 01", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    assert project.GetName() == "Podcast (field test)"

    fake_resolve = FakeResolve(project)

    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("hosting test"):
        shadow.observe(project, timeline, store)
    timeline.AddMarker(20, "Red", "hand edit", "", 1)

    # The hosted watcher must reach its own broker's queue, never the
    # socket client: `ensure` from a thread inside the broker started a
    # second broker onto the same socket (seen: a test broker's child
    # broker), and after the test restored its lock dir it would reach
    # the production socket.
    ensured = []
    real_ensure = client.ensure
    monkeypatch.setattr(client, "ensure", lambda *a, **k: (
        ensured.append(threading.current_thread().name) or real_ensure(*a, **k)))

    serve_thread = threading.Thread(
        target=server_mod.serve,
        kwargs={"path": lock_dir / "ren-resolved.sock",
                "connect": lambda: fake_resolve},
        daemon=True)
    serve_thread.start()

    try:
        deadline = time.time() + 10.0
        heartbeat = None
        while time.time() < deadline:
            heartbeat = shadow_watcher.read_heartbeat()
            if heartbeat is not None:
                break
            time.sleep(0.1)
        assert heartbeat is not None, "watcher never wrote a heartbeat"
        assert heartbeat["project"] == "Podcast (field test)"
        assert heartbeat["project_folder"] == str(project_dir)

        # The hand edit is filed under the project's absolute root, not a
        # bare slug resolved against the broker's working directory.
        review_dir = project_dir / "pipeline_output" / "review"
        entries = reel_edit_history.recorded_history(str(review_dir), "Reel 01")
        assert [e for e in entries if e.get("act") == "manual_edit"]
        assert ensured == []
    finally:
        try:
            client.call({"op": "shutdown"}, lock_dir / "ren-resolved.sock")
        except Exception:
            pass
        serve_thread.join(timeout=5.0)
        assert not serve_thread.is_alive(), "the broker did not shut down"


def test_doctor_names_an_unmapped_project(reel, monkeypatch):
    """Defect: an open project that maps to no Ren project observes
    nothing, and doctor must name it."""
    from ren import doctor

    monkeypatch.setattr(doctor, "_engine_root", lambda: Path("/nonexistent"))

    shadow_watcher.write_heartbeat(project_name="Unknown Project",
                                   project_folder="",
                                   cadence=30.0)
    check = doctor.shadow_watcher_check()
    assert not check.ok
    assert "Unknown Project" in check.detail
    assert "not a Ren project" in check.detail
