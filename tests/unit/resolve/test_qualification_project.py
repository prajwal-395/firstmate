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

    def GetProjectManager(self):
        return self

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


def test_a_restore_that_does_not_read_back_raises():
    resolve, podcast = _captain()
    with pytest.raises(qp.RestoreFailed, match="by hand"):
        with qp.borrowed(resolve):
            resolve.LoadProject(qp.QUALIFICATION_PROJECT)
            podcast.timelines.pop()     # his timeline is gone on return


def test_borrowing_from_the_qualification_project_is_refused():
    resolve, _ = _captain()
    resolve.LoadProject(qp.QUALIFICATION_PROJECT)
    with pytest.raises(qp.QualificationError, match="captain's project"):
        with qp.borrowed(resolve):
            pytest.fail("borrowed with the captain's project unknown")


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
