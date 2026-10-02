"""A started Resolve render keeps the instance lease through completion."""

import os
import select
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

from library.tools import resolve_axi, resolve_lock

REPO = Path(__file__).resolve().parents[3]

_WAITER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from library.tools import resolve_lock
    original_flock = resolve_lock._flock
    reported_contention = False
    def observed_flock(handle, exclusive, blocking):
        global reported_contention
        acquired = original_flock(handle, exclusive, blocking)
        if not acquired and not reported_contention:
            reported_contention = True
            print("WAITING", flush=True)
        return acquired
    resolve_lock._flock = observed_flock
    with resolve_lock.resolve_lease("second leased client", timeout=5):
        print("ACQUIRED", flush=True)
""")


class _Project:
    def __init__(self):
        self.rendering = False
        self.queue_reads = 0
        self.render_observed = threading.Event()
        self.release_queue_read = threading.Event()

    def GetName(self):
        return "Podcast (field test)"

    def GetRenderJobList(self):
        self.queue_reads += 1
        if self.queue_reads > 1:
            # The old implementation returns after this read despite the
            # render still running. Hold it here until the other process has
            # proved it is contending for the real temporary flock.
            self.release_queue_read.wait(timeout=5)
        return [{"JobId": "job-1", "RenderJobName": "stub render",
                 "TimelineName": "Reel 01", "TargetDir": "/tmp",
                 "OutputFilename": "stub.mov"}]

    def GetRenderJobStatus(self, _job_id):
        return {"JobStatus": "Rendering" if self.rendering else "Complete",
                "CompletionPercentage": 50 if self.rendering else 100}

    def StartRendering(self, _job_ids, _interactive=False):
        self.rendering = True
        return True

    def IsRenderingInProgress(self):
        self.render_observed.set()
        return self.rendering


class _Manager:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class _Resolve:
    def __init__(self, project):
        self.manager = _Manager(project)

    def GetProjectManager(self):
        return self.manager


def test_render_start_keeps_lease_until_resolve_reports_completion(
        tmp_path, monkeypatch):
    lock_dir = tmp_path / "resolve-lock"
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lock_dir))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

    project = _Project()
    monkeypatch.setattr(resolve_axi, "_connect", lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "RENDER_POLL_SECONDS", 0.01,
                        raising=False)
    results = []
    command = threading.Thread(
        target=lambda: results.append(resolve_axi.cmd_render_start(
            type("Args", (), {"project": "", "job": [], "all": True,
                               "apply": True})())))
    command.start()

    waiter = None
    try:
        assert project.render_observed.wait(timeout=2)
        waiter_env = dict(os.environ)
        # The subprocess is an independent client, not a child worker that
        # should inherit the caller's lease.
        waiter_env.pop(resolve_lock.INHERIT_ENV, None)
        waiter_env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
        waiter = subprocess.Popen(
            [sys.executable, "-c", _WAITER.format(repo=str(REPO))],
            cwd=REPO,
            env=waiter_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        assert waiter.stdout.readline().strip() == "WAITING"

        # Let any post-start queue read return. The fixed command continues
        # polling under the lease; the old command released it here.
        project.release_queue_read.set()
        acquired_before_completion, _, _ = select.select(
            [waiter.stdout], [], [], 0.4)
        assert not acquired_before_completion, \
            "a second Resolve client entered while the render was in progress"

        project.rendering = False
        command.join(timeout=3)
        assert not command.is_alive(), "render command did not finish"
        assert results == [0]
        assert waiter.stdout.readline().strip() == "ACQUIRED"
        waiter.communicate(timeout=3)
    finally:
        project.release_queue_read.set()
        project.rendering = False
        command.join(timeout=3)
        if waiter is not None:
            if waiter.poll() is None:
                waiter.terminate()
            waiter.communicate(timeout=3)
