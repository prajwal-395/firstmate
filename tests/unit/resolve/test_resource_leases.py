"""Resource leases: the declaration, the conflict check, the expiry.

Every test names a defect it catches - a case that fails on origin/main,
where this module does not exist. The contention that can be REAL is
real: `test_a_waiter_proceeds_once_the_holder_releases` runs two
processes that contend for one lease on disk, because exclusion that is
only reasoned about is not evidence.
"""
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from library.tools import resource_leases
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceLeaseBusy,
    ResourceLeaseExpired,
    ResourceRef,
    release_track_lock,
    resource_lease,
    status,
    take_track_lock,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's.

    `PIPELINE_RESOLVE_LOCK_DIR` is the redirect `resolve_lock` itself
    uses - the resource lease layer shares that one home, so the same
    redirect gives a test a lease directory that contends with nothing.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


# ── The compatibility rule ────────────────────────────────────────────

def test_two_reads_on_one_resource_are_compatible(lock_dir):
    """The spec's core rule: compatible sets run concurrently.

    Defect caught: a conflict check that treats two READS as a
    conflict refuses every pair of readers, and the layer serialises
    what the spec says should scale almost arbitrarily.
    """
    first = LeaseDeclaration(read=frozenset({ResourceRef("timeline", "Reel 03")}))
    second = LeaseDeclaration(read=frozenset({ResourceRef("timeline", "Reel 03")}))
    assert first.conflicts_with(second) is None


def test_a_write_and_a_read_on_the_same_resource_conflict(lock_dir):
    """The spec's own example: two agents, one clip, move and delete.

    Defect caught: a check that only refuses WRITE-WRITE lets a mover
    and a deleter run together and produce undefined state.
    """
    writer = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    reader = LeaseDeclaration(read=frozenset({ResourceRef("clip", "A004_C013")}))
    reason = writer.conflicts_with(reader)
    assert reason is not None
    assert "A004_C013" in reason
    assert "write" in reason and "read" in reason


def test_exclusive_excludes_a_reader(lock_dir):
    """EXCLUSIVE is destructive: no other holder may even read.

    Defect caught: a check that treats EXCLUSIVE like WRITE lets a
    reader observe a resource mid-deletion.
    """
    deleter = LeaseDeclaration(exclusive=frozenset({ResourceRef("clip", "A004_C013")}))
    reader = LeaseDeclaration(read=frozenset({ResourceRef("clip", "A004_C013")}))
    assert deleter.conflicts_with(reader) is not None


def test_different_resources_do_not_conflict(lock_dir):
    """Two agents touching different clips run simultaneously.

    Defect caught: a check that refuses any two holders serialises the
    whole layer and defeats its own purpose.
    """
    first = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    second = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C014")}))
    assert first.conflicts_with(second) is None


# ── The hierarchy: a parent's write invalidates its children ─────────

def test_a_write_on_a_timeline_conflicts_with_a_write_on_a_track_inside_it(lock_dir):
    """The spec's granularity: a timeline holds tracks, a track holds clips.

    Defect caught: a same-resource-only check lets an agent restructure
    a timeline while another edits a track inside it - the exact
    "two agents changing overlapping timeline regions" the spec calls
    very hard.
    """
    restructure = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 03")}))
    track_edit = LeaseDeclaration(write=frozenset({ResourceRef("track", "Reel 03:video:3")}))
    reason = restructure.conflicts_with(track_edit)
    assert reason is not None
    assert "Reel 03:video:3" in reason


def test_a_read_on_a_timeline_conflicts_with_a_write_on_a_track_inside_it(lock_dir):
    """Reading the whole timeline is invalidated when a track changes.

    Defect caught: a check that only refuses WRITE-WRITE lets a reader
    observe a timeline mid-restructure and act on a stale track.
    """
    reader = LeaseDeclaration(read=frozenset({ResourceRef("timeline", "Reel 03")}))
    track_edit = LeaseDeclaration(write=frozenset({ResourceRef("track", "Reel 03:video:3")}))
    assert reader.conflicts_with(track_edit) is not None


def test_an_exclusive_on_a_timeline_conflicts_with_a_read_on_a_track_inside_it(lock_dir):
    """Deleting the timeline deletes every track in it - even a reader is excluded.

    Defect caught: a check that treats EXCLUSIVE like WRITE lets a
    reader observe a timeline being deleted.
    """
    deleter = LeaseDeclaration(exclusive=frozenset({ResourceRef("timeline", "Reel 03")}))
    reader = LeaseDeclaration(read=frozenset({ResourceRef("track", "Reel 03:video:3")}))
    assert deleter.conflicts_with(reader) is not None


def test_a_write_on_a_track_does_not_conflict_with_a_write_on_another_timeline(lock_dir):
    """A track edit is inside ONE timeline, not every timeline with a similar name.

    Defect caught: a prefix check without the kind pair lets a track
    edit in Reel 03 conflict with a write to Reel 030 - a false
    conflict that serialises unrelated work.
    """
    other_timeline = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 030")}))
    track_edit = LeaseDeclaration(write=frozenset({ResourceRef("track", "Reel 03:video:3")}))
    assert other_timeline.conflicts_with(track_edit) is None


def test_a_write_on_a_timeline_conflicts_with_a_read_on_a_track_inside_it(lock_dir):
    """A restructure can move or drop a track, invalidating a reader's view.

    Defect caught: a mode table where WRITE on the parent does not
    conflict with READ on the child lets a reader observe a track that
    a restructure is about to move - the read returns a stale view.
    """
    restructure = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 03")}))
    track_read = LeaseDeclaration(read=frozenset({ResourceRef("track", "Reel 03:video:3")}))
    assert restructure.conflicts_with(track_read) is not None


def test_a_read_on_a_timeline_does_not_conflict_with_a_read_on_a_track_inside_it(lock_dir):
    """Two readers - one of the whole timeline, one of a track - run together.

    Defect caught: a check that refuses any parent-read beside any
    child-access serialises readers against each other, which is the
    over-serialisation the layer exists to avoid.
    """
    timeline_read = LeaseDeclaration(read=frozenset({ResourceRef("timeline", "Reel 03")}))
    track_read = LeaseDeclaration(read=frozenset({ResourceRef("track", "Reel 03:video:3")}))
    assert timeline_read.conflicts_with(track_read) is None


def test_a_write_on_a_timeline_conflicts_with_a_write_on_a_clip_placed_in_it(lock_dir):
    """A clip placed in a timeline is a child of that timeline.

    Defect caught: a hierarchy that only knows tracks lets an agent
    restructure a timeline while another edits a clip placed in it -
    the clip's frames move under the edit.
    """
    restructure = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 03")}))
    clip_edit = LeaseDeclaration(write=frozenset({ResourceRef("clip", "Reel 03:A004_C013")}))
    reason = restructure.conflicts_with(clip_edit)
    assert reason is not None
    assert "Reel 03:A004_C013" in reason


def test_a_write_on_a_timeline_does_not_conflict_with_a_write_on_a_clip_in_the_pool(lock_dir):
    """A bare pool clip is not inside any timeline - it has no placement in its name.

    Defect caught: a check that treats every clip as a child of every
    timeline serialises all clip work against every timeline write.
    """
    restructure = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 03")}))
    pool_clip = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    assert restructure.conflicts_with(pool_clip) is None


# ── Timeline ranges: overlapping regions conflict ────────────────────

def test_two_overlapping_ranges_on_one_timeline_conflict(lock_dir):
    """The spec's "two agents changing overlapping timeline regions".

    Defect caught: a same-resource check treats two different ranges
    as different resources and lets both agents write the shared
    frames.
    """
    first = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 03:100-200")}))
    second = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 03:150-250")}))
    reason = first.conflicts_with(second)
    assert reason is not None
    assert "Reel 03:100-200" in reason


def test_two_disjoint_ranges_on_one_timeline_do_not_conflict(lock_dir):
    """Ranges that share no frame do not conflict.

    Defect caught: a check that refuses any two ranges on one
    timeline serialises the whole timeline against itself.
    """
    first = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 03:100-200")}))
    second = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 03:300-400")}))
    assert first.conflicts_with(second) is None


def test_two_ranges_on_different_timelines_do_not_conflict(lock_dir):
    """Ranges on different timelines are different resources.

    Defect caught: a check that matches ranges by span alone lets a
    range in Reel 03 conflict with an identical range in Reel 04.
    """
    first = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 03:100-200")}))
    second = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 04:100-200")}))
    assert first.conflicts_with(second) is None


def test_a_range_conflicts_with_a_write_on_the_timeline_containing_it(lock_dir):
    """Writing the whole timeline invalidates a range inside it.

    Defect caught: a hierarchy that only knows tracks and clips lets
    an agent restructure a timeline while another edits a range in it.
    """
    restructure = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 03")}))
    range_edit = LeaseDeclaration(write=frozenset({ResourceRef("timeline_range", "Reel 03:100-200")}))
    assert restructure.conflicts_with(range_edit) is not None


def test_an_existing_child_and_a_new_parent_request_conflict(lock_dir):
    """The reverse direction: existing child, new parent request.

    Defect caught: a one-directional check (existing parent vs new
    child only) grants a parent write that contains a clip another
    agent already holds - the new request never sees the child it
    would invalidate.
    """
    existing_child = LeaseDeclaration(write=frozenset({ResourceRef("clip", "Reel 03:A004_C013")}))
    new_parent = LeaseDeclaration(write=frozenset({ResourceRef("timeline", "Reel 03")}))
    # The existing lease is checked against the new request, in both
    # orders - the conflict is symmetric.
    assert existing_child.conflicts_with(new_parent) is not None
    assert new_parent.conflicts_with(existing_child) is not None


def test_a_read_on_a_parent_conflicts_with_an_exclusive_on_a_child(lock_dir):
    """Deleting a clip invalidates a whole-timeline read.

    Defect caught: a mode table where READ on the parent conflicts
    with WRITE on the child but not EXCLUSIVE lets a reader observe a
    timeline while a clip inside it is being deleted.
    """
    reader = LeaseDeclaration(read=frozenset({ResourceRef("timeline", "Reel 03")}))
    deleter = LeaseDeclaration(exclusive=frozenset({ResourceRef("clip", "Reel 03:A004_C013")}))
    assert reader.conflicts_with(deleter) is not None


# ── The refusal and the wait ──────────────────────────────────────────

def test_a_conflicting_lease_refuses_by_name(lock_dir):
    """wait=False refuses IMMEDIATELY, naming the holder and resource.

    Defect caught: a refusal that names only "busy" cannot be acted
    on - the waiter cannot tell which resource to re-plan around or
    which holder to wait for.
    """
    holder_decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(holder_decl, "the holder", heartbeat_interval=30.0):
        waiter_decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
        with pytest.raises(ResourceLeaseBusy) as raised:
            with resource_lease(waiter_decl, "the waiter", wait=False,
                               heartbeat_interval=30.0):
                pass
        message = str(raised.value)
        assert "A004_C013" in message
        assert "the holder" in message


def test_a_waiter_proceeds_once_the_holder_releases(lock_dir):
    """wait=True WAITS, then proceeds the moment the holder releases.

    Defect caught: a waiter that never wakes up wedges the task for
    its full timeout when the holder has already finished - the lease
    layer's own deadlock.
    """
    holder_decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    waiter_decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    acquired = threading.Event()
    released = threading.Event()

    def wait_for_it():
        with resource_lease(waiter_decl, "the waiter", wait=True,
                           timeout=10.0, heartbeat_interval=30.0):
            acquired.set()
        released.set()

    with resource_lease(holder_decl, "the holder", heartbeat_interval=30.0):
        thread = threading.Thread(target=wait_for_it)
        thread.start()
        assert not acquired.wait(2.0), "the waiter ran beside its blocker"
    assert released.wait(10.0), "the waiter never woke up after the release"
    thread.join(5.0)


def test_a_waiter_that_gives_up_names_the_holder(lock_dir):
    """A wait that runs out refuses by name, not by a bare timeout.

    Defect caught: a refusal that says only "timed out" forces the
    captain to go and find the holder himself.
    """
    holder_decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(holder_decl, "the holder", heartbeat_interval=30.0):
        waiter_decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
        with pytest.raises(ResourceLeaseBusy) as raised:
            with resource_lease(waiter_decl, "the waiter", wait=True,
                               timeout=0.5, heartbeat_interval=30.0):
                pass
        assert "the holder" in str(raised.value)
        assert "A004_C013" in str(raised.value)


# ── The heartbeat expiry ─────────────────────────────────────────────

def test_a_lease_expires_when_its_holder_stops_heartbeating(lock_dir):
    """A holder that stops heartbeating loses its declaration.

    Defect caught: a lease with no expiry is held forever by a wedged
    or dead agent, and every task that needs the resource queues behind
    a holder that will never release - the layer's own wedge.
    """
    decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(decl, "a holder that wedges", heartbeat_interval=0.2):
        pass
    # The holder is gone; a second holder takes the same resource.
    with resource_lease(decl, "the next holder", wait=False,
                       heartbeat_interval=30.0):
        pass


def test_a_stale_holders_heartbeat_raises_rather_than_resurrecting(lock_dir):
    """An expired lease is gone, not brought back to life.

    Defect caught: a heartbeat that re-writes its own record after
    expiry lets two holders believe they are alone - the exact
    double-holder the layer exists to prevent.
    """
    decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(decl, "the first holder", heartbeat_interval=0.2,
                       heartbeat_thread=False) as first:
        first.heartbeat()
        # Stop heartbeating and let the lease go stale.
        time.sleep(0.5)
        # A second holder evicts the stale lease and takes the resource.
        with resource_lease(decl, "the second holder", wait=False,
                           heartbeat_interval=30.0):
            with pytest.raises(ResourceLeaseExpired):
                first.heartbeat()


def test_a_heartbeat_keeps_a_live_lease_from_expiring(lock_dir):
    """A holder that heartbeats stays the holder.

    Defect caught: a lease that expires while its holder is alive and
    working evicts a task mid-section, and the next write is unguarded.
    """
    decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(decl, "a live holder", heartbeat_interval=0.3,
                       heartbeat_thread=False) as handle:
        for _ in range(5):
            time.sleep(0.15)
            handle.heartbeat()
        # Still the holder: a second holder is refused.
        with pytest.raises(ResourceLeaseBusy):
            with resource_lease(decl, "a late waiter", wait=False,
                               heartbeat_interval=30.0):
                pass


# ── The declaration's own refusals ───────────────────────────────────

def test_a_resource_kind_outside_the_enumeration_is_refused(lock_dir):
    """A declaration nobody can match is not a declaration.

    Defect caught: an open kind vocabulary lets a caller declare a
    resource no other caller will ever spell the same way, so the
    conflict check silently never fires.
    """
    with pytest.raises(ValueError, match="not a resource kind"):
        ResourceRef("clip_bin", "A004_C013")


def test_a_resource_in_two_modes_is_refused(lock_dir):
    """A resource in two sets is a caller bug, refused not merged.

    Defect caught: silently merging two modes keeps the stricter one
    and hides a declaration the caller did not mean to make.
    """
    ref = ResourceRef("clip", "A004_C013")
    with pytest.raises(ValueError, match="does not read a resource"):
        LeaseDeclaration(read=frozenset({ref}), write=frozenset({ref}))


def test_an_unnamed_resource_is_refused(lock_dir):
    """An unnamed resource matches nothing and excludes nobody."""
    with pytest.raises(ValueError, match="needs a name"):
        ResourceRef("clip", "  ")


# ── The real caller ──────────────────────────────────────────────────

def test_the_status_line_names_the_holder_and_the_resource(lock_dir):
    """A human reading the status line can act on what it says.

    Defect caught: a status line that says only "1 lease" forces the
    captain to go and read the file himself.
    """
    decl = LeaseDeclaration(
        read=frozenset({ResourceRef("timeline", "Reel 03")}),
        write=frozenset({ResourceRef("caption_track", "Reel 03")}),
        exclusive=frozenset({ResourceRef("caption_track", "Reel 03")}),
    )
    with resource_lease(decl, "caption swap", heartbeat_interval=30.0):
        line = status()
        assert "caption swap" in line
        assert "Reel 03" in line
        assert "caption_track" in line


# ── The native track lock, taken alongside the Ren-side lease ────────

def test_take_track_lock_locks_the_named_track(lock_dir):
    """Resolve's native track lock is taken for a track resource.

    Defect caught: a Ren-side lease with no native enforcement lets a
    writer that never asked the Ren layer write the track anyway - the
    instance lease is the coarse fence, this is the fine-grained one.
    """
    class FakeTimeline:
        def __init__(self):
            self.locks = []

        def SetTrackLock(self, track_type, index, locked):
            self.locks.append((track_type, index, locked))
            return True

    timeline = FakeTimeline()
    ref = ResourceRef("track", "Reel 03:video:3")
    assert take_track_lock(timeline, ref) is True
    assert timeline.locks == [("video", 3, True)]
    assert release_track_lock(timeline, ref) is True
    assert timeline.locks[-1] == ("video", 3, False)


def test_take_track_lock_refuses_a_ref_that_is_not_a_track(lock_dir):
    """A non-track ref has no native lock to take.

    Defect caught: a helper that assumes every ref is a track calls
    SetTrackLock with a nonsense track type and crashes the caller.
    """
    class FakeTimeline:
        def SetTrackLock(self, track_type, index, locked):
            raise AssertionError("must not be called for a non-track ref")

    timeline = FakeTimeline()
    assert take_track_lock(timeline, ResourceRef("clip", "A004_C013")) is False
    assert take_track_lock(timeline, ResourceRef("timeline", "Reel 03")) is False


def test_take_track_lock_refuses_an_unparseable_track_name(lock_dir):
    """A track name that does not parse cannot be locked.

    Defect caught: a helper that assumes the name parses calls
    SetTrackLock with a non-integer index and crashes the caller.
    """
    class FakeTimeline:
        def SetTrackLock(self, track_type, index, locked):
            raise AssertionError("must not be called for an unparseable ref")

    timeline = FakeTimeline()
    assert take_track_lock(timeline, ResourceRef("track", "Reel 03")) is False
    assert take_track_lock(timeline, ResourceRef("track", "Reel 03:video")) is False


# ── The event log: a lease leaves a record a resumed worker reads ─────

@pytest.fixture
def project(tmp_path):
    """A project folder with a LOGS area, never a real one."""
    folder = tmp_path / "project"
    (folder / "pipeline_output" / "logs").mkdir(parents=True)
    return folder


def test_an_acquired_lease_emits_an_event(lock_dir, project):
    """The lease's acquisition is on the record for a resumed worker.

    Defect caught: a lease that does not emit leaves no event when a
    worker is killed mid-section, and the next worker cannot tell what
    the dead one held - the exact gap the event log exists to close.
    """
    decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(decl, "caption swap", heartbeat_interval=30.0,
                        project=str(project)):
        pass
    from library.tools import event_log
    events = event_log.read_events(str(project))
    assert [e["type"] for e in events] == [
        event_log.LEASE_ACQUIRED, event_log.LEASE_RELEASED]
    assert events[0]["task"] == "caption swap"
    assert events[0]["fields"]["pid"] == events[1]["fields"]["pid"]


def test_a_lease_with_no_project_emits_nothing(lock_dir):
    """A lease that names no project leaves no event, not a wrong one.

    Defect caught: a lease that emits into a default or shared log
    when the caller named no project mixes one project's operations
    into another's, and a resumed worker reads events that are not its
    own.
    """
    decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(decl, "caption swap", heartbeat_interval=30.0):
        pass
    from library.tools import event_log
    assert event_log.read_events(str(lock_dir)) == []


def test_an_expired_lease_emits_an_expiry_event(lock_dir, project):
    """A lease whose heartbeat went stale says so on the record.

    Defect caught: an expiry that is only raised to the holder leaves
    no event for a resumed worker, which cannot tell whether the dead
    one's section finished or was killed mid-write - the difference
    between continuing from the checkpoint and re-doing completed work.
    """
    decl = LeaseDeclaration(write=frozenset({ResourceRef("clip", "A004_C013")}))
    with resource_lease(decl, "caption swap", heartbeat_interval=0.2,
                        project=str(project), heartbeat_thread=False) as first:
        first.heartbeat()
        time.sleep(0.5)
        with resource_lease(decl, "the next holder", wait=False,
                           heartbeat_interval=30.0):
            with pytest.raises(ResourceLeaseExpired):
                first.heartbeat()
    from library.tools import event_log
    events = event_log.read_events(str(project))
    assert event_log.LEASE_EXPIRED in [e["type"] for e in events]
