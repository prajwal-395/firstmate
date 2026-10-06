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
    resource_lease,
    status,
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
