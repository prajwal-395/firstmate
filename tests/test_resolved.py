"""ren-resolved: what each scheduling and fencing rule exists to prevent.

No Resolve: the broker's connection is a fake, and its socket and job
table live in a temp lock dir (`PIPELINE_RESOLVE_LOCK_DIR`).
"""

import shutil
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.resolved import client, jobs, scheduler
from library.tools.resolved.server import Broker, _Handler, _Server
from library.tools.resolved.store import JobStore


def _pending(job_id, priority="read", mode="shared", submitted=0.0,
             project="", timeline="", executed=False):
    return scheduler.Pending(id=job_id, priority=priority, mode=mode,
                             submitted=submitted, project=project,
                             timeline=timeline, executed=executed)


# ── Ordering ────────────────────────────────────────────────────────

def test_an_old_export_is_not_starved_by_fresh_interactive_work():
    old = _pending("export", priority="export", submitted=0.0)
    fresh = _pending("human", priority="interactive", submitted=300.0)
    now = 300.0 + scheduler.AGING_SECONDS
    assert scheduler.next_job([old, fresh], [], ("", ""), now).id == "export"


def test_readers_arriving_after_a_writer_do_not_overtake_it():
    writer = _pending("write", priority="read", mode="exclusive",
                      submitted=0.0)
    reader = _pending("read2", priority="read", submitted=1.0)
    running = [_pending("read1", priority="read")]
    assert scheduler.next_job([writer, reader], running, ("", ""), 2.0) is None


def test_a_job_on_the_current_timeline_goes_before_one_elsewhere():
    away = _pending("away", project="P", timeline="Reel09", submitted=0.0)
    here = _pending("here", project="P", timeline="Reel03", submitted=1.0)
    chosen = scheduler.next_job([away, here], [], ("P", "Reel03"), 2.0)
    assert chosen.id == "here"


# ── The broker, with a fake Resolve ─────────────────────────────────

class _Timeline:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.name


class _Project:
    def __init__(self, name, timelines):
        self.name, self.timelines = name, [_Timeline(t) for t in timelines]
        self.current = self.timelines[0]

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]


class _Resolve:
    def __init__(self, project):
        self.project = project

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self.project


@pytest.fixture
def lock_dir(monkeypatch):
    # A Unix socket path is capped near 104 bytes, which a pytest
    # tmp_path can exceed, so the broker gets a short directory.
    directory = Path(tempfile.mkdtemp(prefix="rr", dir="/tmp"))
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def serving(lock_dir):
    """A broker on a real socket, over a fake Resolve with Podcast open."""
    resolve = _Resolve(_Project("Podcast", ["Master", "Reel 01"]))
    store = JobStore(lock_dir / "ren-resolved.sqlite3")
    broker = Broker(store, connect=lambda: resolve)
    path = lock_dir / "ren-resolved.sock"
    server = _Server(str(path), _Handler)
    server.broker = broker
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield broker, resolve
    server.shutdown()
    broker.stop()
    server.server_close()
    store.close()


def test_identical_snapshots_coalesce_into_one_resolve_read(
        serving, unguarded, monkeypatch):
    broker, _ = serving
    reads = []
    release = threading.Event()

    def observe(project, timeline):
        # The shadow refuses a non-current timeline (Pan/Tilt read scaled).
        assert project.GetCurrentTimeline() is timeline
        reads.append(timeline.GetName())
        release.wait(5)
        return types.SimpleNamespace(
            summary=lambda: {"name": timeline.GetName()})

    monkeypatch.setattr("library.tools.timeline_shadow.observe", observe)
    params = {"project": "Podcast", "timeline": "Reel 01"}
    first = client.submit("timeline.snapshot", params)
    second = client.submit("timeline.snapshot", params)
    release.set()
    receipt = client.result(first["id"], wait=5)
    assert second == {"id": first["id"], "coalesced": True}
    assert reads == ["Reel 01"]
    assert receipt["state"] == "done"
    assert receipt["subscribers"] == 2
    assert receipt["result"] == {"name": "Reel 01"}
    assert receipt["wait_seconds"] is not None
    assert receipt["hold_seconds"] is not None


def test_a_qualification_job_aimed_at_a_user_project_is_refused():
    with pytest.raises(jobs.JobRefused):
        jobs.prepare("timeline.snapshot",
                     {"project": "Podcast", "timeline": "Reel 01"},
                     qualification=True)


def test_a_qualification_grant_is_refused_while_a_user_project_is_open(
        serving, unguarded):
    with pytest.raises(resolve_lock.ResolveBusy, match="Podcast"):
        with resolve_lock.resolve_lease(
                "a live test", timeout=5,
                qualification_project=jobs.QUALIFICATION_PROJECT):
            pytest.fail("the broker granted a test the captain's project")


@pytest.fixture
def unguarded(monkeypatch):
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


def test_a_lease_waits_for_the_broker_grant_another_client_holds(
        serving, unguarded):
    broker, _ = serving
    order = []
    holding = threading.Event()
    done = threading.Event()

    def first():
        with client.grant({"purpose": "first", "exclusive": True}):
            order.append("first in")
            holding.set()
            time.sleep(0.5)
            order.append("first out")
        done.set()

    threading.Thread(target=first, daemon=True).start()
    assert holding.wait(5)
    with resolve_lock.resolve_lease("second", timeout=10):
        order.append("second in")
    done.wait(5)
    assert order == ["first in", "first out", "second in"]
    assert any(job["params"].get("purpose") == "second"
               and job["state"] == "done"
               for job in broker.store.recent())


def test_a_stale_patch_is_rejected_with_what_a_rebase_needs(
        serving, unguarded, monkeypatch):
    from library.tools import edit_patch
    broker, resolve = serving
    patch = {"id": "patch_1", "project": "Podcast", "timeline": "Reel 01"}

    def apply_patch(patch, *, resolve, project, timeline):
        assert project.GetCurrentTimeline() is timeline
        raise edit_patch.StalePatch(
            types.SimpleNamespace(base_generation=104, **patch), 105,
            observed=True, rebase_possible=True, reason="the timeline moved")

    monkeypatch.setattr(edit_patch, "apply_patch", apply_patch)
    submitted = client.submit("timeline.apply_patch", {"patch": patch})
    receipt = client.result(submitted["id"], wait=5)
    assert receipt["state"] == "rejected"
    assert receipt["result"]["refusal"] == "StalePatch"
    assert receipt["result"]["head"] == 105
    assert receipt["result"]["rebase_possible"] is True
    # The captain's cursor is put back where the broker found it.
    assert resolve.project.GetCurrentTimeline().GetName() == "Master"
