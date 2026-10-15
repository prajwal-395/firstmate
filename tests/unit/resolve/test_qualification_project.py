"""Ren Qualification: the borrow gives the captain back exactly what he had.

The Resolve here is a fake (and, end to end, the canonical double); the
media test runs the real ffmpeg.
"""

import json
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from library.tools import marker_feedback, resolve_lock
from library.tools import qualification_project as qp
from library.tools.resolved.server import Broker, _Handler, _Server
from library.tools.resolved.store import JobStore
from tests import resolve_double as rd


class _Timeline:
    def __init__(self, name, uid):
        self.name, self.uid = name, uid

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid


class _Project:
    def __init__(self, name, timelines):
        self.name, self.timelines = name, timelines
        self.current = timelines[0] if timelines else None

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True


class _Manager:
    """Projects by name; loading one opens it on its FIRST timeline, as
    a reopened project does not remember a script's cursor."""

    def __init__(self, *projects):
        self.projects = {p.name: p for p in projects}
        self.open = projects[0]
        self.saved = []
        self.opened_pages = ["edit"]

    def GetProjectManager(self):
        return self

    def GetCurrentPage(self):
        return self.opened_pages[-1]

    def OpenPage(self, page):
        self.opened_pages.append(page)
        return True

    def GetCurrentProject(self):
        return self.open

    def SaveProject(self):
        self.saved.append(self.open.name)
        return True

    def LoadProject(self, name):
        self.open = self.projects.get(name)
        if self.open and self.open.timelines:
            self.open.current = self.open.timelines[0]
        return self.open


def _captain():
    """His project, on the SECOND of two timelines that share a name."""
    first = _Timeline("Reel 07", "uid-first")
    second = _Timeline("Reel 07", "uid-second")
    podcast = _Project("Podcast (field test)", [first, second])
    podcast.current = second
    qualification = _Project(qp.QUALIFICATION_PROJECT,
                             [_Timeline("Q Spine", "uid-q")])
    return _Manager(podcast, qualification), podcast


def test_the_borrow_returns_the_exact_timeline_even_when_names_collide():
    resolve, podcast = _captain()
    with pytest.raises(RuntimeError, match="mid-qualification"):
        with qp.borrowed(resolve):
            resolve.LoadProject(qp.QUALIFICATION_PROJECT)
            raise RuntimeError("mid-qualification failure")
    assert resolve.GetCurrentProject() is podcast
    assert podcast.GetCurrentTimeline().GetUniqueId() == "uid-second"
    assert resolve.saved[0] == "Podcast (field test)"


def test_the_borrow_puts_the_captains_page_back():
    """Defect: a late AX page press flipped the captain's project to the
    media page after borrowed() restored it - restore put back the project
    and timeline but never the page."""
    resolve, _ = _captain()
    with qp.borrowed(resolve):
        resolve.LoadProject(qp.QUALIFICATION_PROJECT)
        resolve.OpenPage("media")
    assert resolve.GetCurrentPage() == "edit"
    assert resolve.opened_pages == ["edit", "media", "edit"]


def test_a_restore_that_does_not_read_back_raises():
    resolve, podcast = _captain()
    with pytest.raises(qp.RestoreFailed, match="by hand"):
        with qp.borrowed(resolve):
            resolve.LoadProject(qp.QUALIFICATION_PROJECT)
            podcast.timelines.pop()     # his timeline is gone on return


def test_a_switch_that_opens_a_stale_project_is_refused():
    """After recovery Resolve may answer LoadProject while a stale
    project stays current. The switch must refuse rather than hand
    back a project that is not the declared target."""
    resolve, _ = _captain()

    def stale_load(name):
        return resolve.projects.get(name)

    resolve.LoadProject = stale_load
    with pytest.raises(qp.QualificationError, match="did not read back"):
        qp.switch_project(resolve, qp.QUALIFICATION_PROJECT, settle=0)
    assert resolve.GetCurrentProject().GetName() == "Podcast (field test)"


def test_a_switch_past_a_dead_project_manager_is_refused():
    """After recovery the project manager may read dead behind a
    LoadProject that answered. The switch must refuse rather than
    proceed with no readable project."""
    resolve, _ = _captain()

    def dead_load(name):
        resolve.open = None
        return None

    resolve.LoadProject = dead_load
    with pytest.raises(qp.QualificationError, match="reads dead"):
        qp.switch_project(resolve, qp.QUALIFICATION_PROJECT, settle=0)


def test_borrowing_from_the_qualification_project_is_refused():
    resolve, _ = _captain()
    resolve.LoadProject(qp.QUALIFICATION_PROJECT)
    with pytest.raises(qp.QualificationError, match="captain's project"):
        with qp.borrowed(resolve):
            pytest.fail("borrowed with the captain's project unknown")


def test_qualification_only_borrow_keeps_the_existing_cursor(monkeypatch):
    resolve, _ = _captain()
    resolve.LoadProject(qp.QUALIFICATION_PROJECT)
    project = resolve.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    events = []
    monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: True)
    monkeypatch.setattr(
        qp, "begin_qualification_session",
        lambda name: events.append(("begin", name)))
    monkeypatch.setattr(
        qp, "end_qualification_session", lambda: events.append(("end",)))

    with qp.borrowed(resolve, qualification_only=True) as found:
        assert found["qualification_only"] is True
        assert resolve.GetCurrentProject() is project
        assert project.GetCurrentTimeline() is timeline

    assert resolve.GetCurrentProject() is project
    assert project.GetCurrentTimeline() is timeline
    assert resolve.saved == []
    assert resolve.opened_pages == ["edit"]
    assert events == [("begin", qp.QUALIFICATION_PROJECT), ("end",)]


def test_qualification_only_borrow_refuses_without_switching(monkeypatch):
    resolve, podcast = _captain()
    timeline = podcast.GetCurrentTimeline()
    monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: True)

    with pytest.raises(qp.QualificationError,
                       match="needs 'Ren Qualification' open"):
        with qp.borrowed(resolve, qualification_only=True):
            pytest.fail("wrong project reached the qualification body")

    assert resolve.GetCurrentProject() is podcast
    assert podcast.GetCurrentTimeline() is timeline
    assert resolve.saved == []


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="no ffprobe")
def test_the_synthetic_clips_have_the_declared_frame_counts(
        tmp_path, monkeypatch):
    monkeypatch.setenv(qp.MEDIA_ROOT_ENV, str(tmp_path))
    for name, path in qp.ensure_media().items():
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams",
             "v:0", "-show_entries", "stream=nb_read_frames,r_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, encoding="utf-8", check=True).stdout)
        stream = probe["streams"][0]
        assert stream["r_frame_rate"] == f"{qp.FPS}/1", name
        assert int(stream["nb_read_frames"]) == qp.frames(name), name


def test_qualify_passes_end_to_end_against_the_resolve_double(
        tmp_path, monkeypatch):
    """The live script, rehearsed: it found that a snapshot must make its
    timeline current, which no unit test of the broker had."""
    captain = rd.make_project("Podcast (field test)")
    for uid in ("c1", "c2"):
        captain.adopt(rd.FakeTimeline("Reel 07", project=captain, uid=uid))
    captain.SetCurrentTimeline(captain.GetTimelineByIndex(2))

    class Manager(rd.FakeProjectManager):
        def __init__(self):
            super().__init__(captain)
            self.projects = {captain.GetName(): captain}

        def SaveProject(self):
            return True

        def LoadProject(self, name):
            self._project = self.projects.get(name)
            return self._project

    resolve = rd.FakeResolve(captain)
    resolve._manager = manager = Manager()

    def rebuild(resolve, media):
        project = rd.make_project(qp.QUALIFICATION_PROJECT, width=1920,
                                  height=1080, frame_rate=qp.FPS)
        manager.projects[project.GetName()] = manager._project = project
        for spec in qp.TIMELINES:
            # Resolve's GetEndFrame is EXCLUSIVE (measured live
            # 2026-10-02); the double's default reads it inclusive.
            length = sum(qp.frames(clip) for clip in spec["clips"])
            timeline = project.adopt(rd.FakeTimeline(
                spec["name"], project=project, end_frame=length))
            for clip in spec["clips"]:
                pool = rd.make_pool_clip(f"{clip}.mp4", frames=qp.frames(clip))
                rd.place_clip(timeline, pool, 0, qp.frames(clip) - 1)
            for m in spec["markers"]:
                timeline.AddMarker(m["frame"], m["color"], m["name"], "", 1,
                                   "")
        return project

    @contextmanager
    def broker(directory):
        store = JobStore(directory / "db.sqlite3")
        served = Broker(store, connect=lambda: resolve)
        server = _Server(str(directory / "s.sock"), _Handler)
        server.broker = served
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield directory / "s.sock"
        finally:
            server.shutdown()
            served.stop()
            server.server_close()
            store.close()

    lock_dir = Path(tempfile.mkdtemp(prefix="rq", dir="/tmp"))
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lock_dir))
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    monkeypatch.setattr(marker_feedback, "connect_resolve", lambda: resolve)
    monkeypatch.setattr(qp, "ensure_media", lambda: {})
    monkeypatch.setattr(qp, "rebuild", rebuild)
    monkeypatch.setattr(qp, "_broker", broker)
    try:
        report = qp.qualify()
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)
    assert report["passed"], [c for c in report["checks"] if not c["ok"]]
    back = manager.GetCurrentProject()
    assert back is captain
    assert back.GetCurrentTimeline().GetUniqueId() == "c2"


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """A lease dir of our own, never the machine's - the session
    marker lives beside the lock, so it needs the same isolation."""
    from library.tools import resolve_lock

    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    return directory


def test_borrow_marks_the_session_and_clears_it(lock_dir):
    """The watcher stands down off this marker: it names the live
    borrower while borrowed() holds Resolve, and is gone on restore."""
    import os

    resolve, _ = _captain()
    with qp.borrowed(resolve):
        session = qp.qualification_session_active()
        assert session is not None
        assert session["project"] == "Podcast (field test)"
        assert session["pid"] == os.getpid()
    assert qp.qualification_session_active() is None


def test_session_marker_cleared_when_restore_fails(lock_dir):
    """A restore that raises must not leave the watcher stood down: the
    marker clears in the finally behind the restore."""
    resolve, podcast = _captain()
    with pytest.raises(qp.RestoreFailed):
        with qp.borrowed(resolve):
            resolve.LoadProject(qp.QUALIFICATION_PROJECT)
            podcast.timelines.pop()
    assert qp.qualification_session_active() is None


def _borrowing_job(monkeypatch, resolve, kind="qualification.workflow"):
    """A borrowing qualification job executed by the Rust-daemon worker
    path while the captain's project is open."""
    from library.tools.resolved import cancellation
    from library.tools.resolved import jobs as job_kinds
    from library.tools.resolved import worker

    @contextmanager
    def _lease(purpose, exclusive=False):
        yield

    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)
    monkeypatch.setattr(cancellation, "begin", lambda job_id: None)
    monkeypatch.setattr(worker, "_connect_cached", lambda: resolve)
    calls = []
    monkeypatch.setattr(
        job_kinds, "run",
        lambda job, handle: calls.append(job) or {"ran": kind})
    job = {"id": "qw-test", "kind": kind, "mode": "exclusive",
           "qualification": True, "executed": True,
           "project": qp.QUALIFICATION_PROJECT}
    return worker._execute_job(job), calls


def test_rust_worker_runs_borrowing_jobs_from_the_captains_project(monkeypatch):
    """Defect (Rust daemon default): worker.py rejected every qualification
    job unless Ren Qualification was already open, while server.py exempts
    borrowing kinds - so the workflow job died with 'needs Ren
    Qualification open' on the captain's project instead of borrowing it."""
    resolve, podcast = _captain()
    assert resolve.GetCurrentProject() is podcast

    reply, calls = _borrowing_job(monkeypatch, resolve)

    assert reply["ok"] is True, reply
    assert reply["result"] == {"ran": "qualification.workflow"}
    assert [job["kind"] for job in calls] == ["qualification.workflow"]


def test_rust_worker_runs_qualification_sessions_from_the_captains_project(
        monkeypatch):
    """A qualification session borrows and restores the captain context,
    so the Rust worker must allow it through the same open-project gate as
    the other borrowing qualification jobs.
    """
    resolve, podcast = _captain()
    assert resolve.GetCurrentProject() is podcast

    reply, calls = _borrowing_job(
        monkeypatch, resolve, kind="resolve.qualification_session")

    assert reply["ok"] is True, reply
    assert reply["result"] == {"ran": "resolve.qualification_session"}
    assert [job["kind"] for job in calls] == [
        "resolve.qualification_session"]


def test_rust_worker_still_refuses_a_wrong_target_for_borrowing_jobs(monkeypatch):
    """Defect: exempting borrowing jobs must not open any project - a
    borrowing job naming a user project is refused before the borrow."""
    from library.tools.resolved import cancellation
    from library.tools.resolved import worker

    resolve, _ = _captain()

    @contextmanager
    def _lease(purpose, exclusive=False):
        yield

    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)
    monkeypatch.setattr(cancellation, "begin", lambda job_id: None)
    monkeypatch.setattr(worker, "_connect_cached", lambda: resolve)
    job = {"id": "qw-test", "kind": "qualification.workflow",
           "mode": "exclusive", "qualification": True, "executed": True,
           "project": "Podcast (field test)"}

    reply = worker._execute_job(job)

    assert reply["ok"] is False
    assert reply.get("refused") is True
    assert "may only target 'Ren Qualification'" in reply["error"]


def test_rust_worker_keeps_the_open_project_gate_for_other_kinds(monkeypatch):
    """Defect: the mirror exemption must not leak - a non-borrowing
    qualification job on the captain's project is still refused."""
    from library.tools.resolved import worker

    resolve, podcast = _captain()
    job = {"id": "qw-test", "kind": "timeline.observe_current",
           "mode": "shared", "qualification": True, "executed": False,
           "project": qp.QUALIFICATION_PROJECT}

    @contextmanager
    def _lease(purpose, exclusive=False):
        yield

    from library.tools.resolved import cancellation
    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)
    monkeypatch.setattr(cancellation, "begin", lambda job_id: None)
    monkeypatch.setattr(worker, "_connect_cached", lambda: resolve)

    reply = worker._execute_job(job)

    assert reply["ok"] is False
    assert reply.get("refused") is True
    assert "needs 'Ren Qualification' open" in reply["error"]
    assert resolve.GetCurrentProject() is podcast
