"""Resource leases: a task declares what it will touch, and waits or refuses by name.

The Ren Fabric spec's RESOURCE LEASES section: tasks declare READ / WRITE
/ EXCLUSIVE over granular resources - project, timeline, timeline range,
track, clip, media pool bin, Fusion comp, colour grade, Fairlight track,
audio bus, graphic, caption track, deliverable - and acquire a temporary
lease before a destructive change, so two agents cannot move and delete
the same clip at once.

This is the declaration + conflict check + expiry layer, built ON
`resolve_lock` rather than beside it: the same lock directory, the same
`flock` primitives, the same owner/purpose conventions. The instance
lease is the coarse fence (one writer at a time, machine-wide); this
layer is the fine-grained scheduler above it - a task declares its
resource set, a compatible set runs concurrently, a conflicting set
waits or refuses by name, and a lease whose holder stopped heartbeating
expires so a wedged agent does not hold its declaration forever.

The compatibility rule is the spec's own: two READS on one resource are
compatible, everything else on the same resource conflicts. A conflict
is per exact resource ref - a WRITE on a timeline and a READ on a clip
inside it do not contend until a caller declares the hierarchy, which
is a later slice.

Expiry is a heartbeat timeout. The holder declares how often it will
heartbeat (`heartbeat_interval`); the lease expires one interval after
the last heartbeat, so a holder that died or wedged stops holding its
declaration. The default interval is a technical fallback for a holder
that states none of its own, in the `resolve_lock.DEFAULT_TIMEOUT_SECONDS`
style - not a creative value.

Where Resolve has native enforcement, it is taken ALONGSIDE this lease,
never instead of it: the per-track lock (`Timeline.SetTrackLock`, the one
lock Resolve scripts can take per the collaboration scout) is a native
fence for the `track` resource, and a caller that writes a track takes
both. This module does not call Resolve - it is the Ren-side declaration
and the conflict check, testable with the application closed.

Reachability
------------
    python -m library.tools.resource_leases status

`tests/unit/resolve/test_resource_leases.py`.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Set, Tuple

from library.tools import event_log, resolve_lock
from library.tools.resolve_lock import ResolveBusy, default_owner, lock_dir

#: The spec's resource kinds, verbatim. One enumeration - a caller may
#: not declare a resource outside it, because a declaration nobody can
#: match is not a declaration.
RESOURCE_KINDS: Tuple[str, ...] = (
    "project",
    "timeline",
    "timeline_range",
    "track",
    "clip",
    "media_pool_bin",
    "fusion_comp",
    "colour_grade",
    "fairlight_track",
    "audio_bus",
    "graphic",
    "caption_track",
    "deliverable",
)

READ = "read"
WRITE = "write"
EXCLUSIVE = "exclusive"
MODES: Tuple[str, ...] = (READ, WRITE, EXCLUSIVE)

LEASES_FILENAME = "resource_leases.json"
"""Where active leases live - the `resolve_lock` lock directory, so the
two layers share one machine-wide home and one `PIPELINE_RESOLVE_LOCK_DIR`
redirect (AGENTS.md 5, the redirect the tests use)."""

#: The default heartbeat interval, for a holder that states none of its
#: own. Source: a technical fallback in the `resolve_lock` style, not a
#: creative value - long enough that a background heartbeat never
#: contends with itself, short enough that a dead holder's declaration
#: is noticed within a minute. A holder's own declaration overrides it.
DEFAULT_HEARTBEAT_INTERVAL = 30.0

#: How often a background heartbeat fires, as a fraction of the
#: interval. The holder heartbeats twice per interval so a single late
#: heartbeat does not expire a live lease.
_HEARTBEAT_FRACTION = 0.5


class ResourceLeaseBusy(ResolveBusy):
    """A conflicting lease is held. Carries who, and which resource."""


class ResourceLeaseExpired(ResolveBusy):
    """This lease's heartbeat went stale and another holder took it.

    Raised by `ResourceLease.heartbeat` when the lease is no longer on
    disk - the holder stopped heartbeating past its interval, the lease
    expired, and a waiter evicted it. The holder must stop and re-read
    state: anything it wrote before the eviction stands, but its
    declaration is gone and its next write is unguarded.
    """


@dataclass(frozen=True)
class ResourceRef:
    """One granular resource a task may touch: a kind and a name.

    `name` is the owner's own identifier for the resource - a timeline
    name, a clip id, a track's `timeline:row` key. The kind is one of
    `RESOURCE_KINDS`; the name is whatever the caller's Resolve calls
    that resource, so two agents declaring the same resource match
    without a shared registry.
    """

    kind: str
    name: str

    def __post_init__(self):
        if self.kind not in RESOURCE_KINDS:
            raise ValueError(
                f"{self.kind!r} is not a resource kind. `RESOURCE_KINDS` "
                f"enumerates them: {', '.join(RESOURCE_KINDS)}. A "
                f"declaration nobody can match is not a declaration.")
        if not self.name or not str(self.name).strip():
            raise ValueError(
                f"a {self.kind} resource needs a name - an unnamed "
                f"resource matches nothing and excludes nobody.")

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"

    @classmethod
    def parse(cls, text: str) -> "ResourceRef":
        """Read back the `kind:name` form the lease file stores."""
        kind, sep, name = str(text).partition(":")
        if not sep:
            raise ValueError(
                f"{text!r} is not a resource ref - it has no kind. The "
                f"form is `kind:name`, one of {', '.join(RESOURCE_KINDS)}.")
        return cls(kind=kind, name=name)


@dataclass(frozen=True)
class LeaseDeclaration:
    """What a task will touch: its read, write and exclusive sets.

    The spec's own three modes. A resource in `write` is one the task
    will modify; in `exclusive` is one it will modify destructively -
    move, delete, replace - so no other holder may even read it. A
    resource may appear in only one set; the same ref in two sets is a
    caller bug, refused rather than silently merged.
    """

    read: FrozenSet[ResourceRef] = frozenset()
    write: FrozenSet[ResourceRef] = frozenset()
    exclusive: FrozenSet[ResourceRef] = frozenset()

    def __post_init__(self):
        # WRITE + EXCLUSIVE on one resource is the spec's own example
        # ("WRITE: caption-track / EXCLUSIVE: caption-track") - the
        # exclusive subsumes the write. READ beside either is a
        # contradiction: a task does not read a resource it is
        # modifying, and an exclusive holder excludes even readers.
        for mode_a, set_a, mode_b, set_b in (
                (READ, self.read, WRITE, self.write),
                (READ, self.read, EXCLUSIVE, self.exclusive)):
            shared = set_a & set_b
            if shared:
                ref = sorted(str(r) for r in shared)[0]
                raise ValueError(
                    f"{ref} is declared {mode_a} AND {mode_b} - a task "
                    f"does not read a resource it is modifying. Keep "
                    f"the stricter mode; WRITE + EXCLUSIVE is the one "
                    f"pairing the spec's own example uses.")

    def all_refs(self) -> Set[ResourceRef]:
        return set(self.read) | set(self.write) | set(self.exclusive)

    def conflicts_with(self, other: "LeaseDeclaration") -> Optional[str]:
        """None if the two declarations may run concurrently, else why.

        Two rules. The same-resource rule: two READS on one resource
        are compatible; every other pair of modes on the same resource
        conflicts. The hierarchy rule: a WRITE on a parent conflicts
        with a WRITE or EXCLUSIVE on a child inside it, a READ on a
        parent conflicts with a WRITE on a child, and an EXCLUSIVE on
        a parent conflicts with anything on a child - because writing,
        reading or deleting the timeline invalidates every track and
        clip inside it. The reason names the resource and both modes,
        so a waiter can act on it - a conflict that cannot be described
        cannot be resolved.
        """
        mine = ((READ, self.read), (WRITE, self.write),
                (EXCLUSIVE, self.exclusive))
        theirs = ((READ, other.read), (WRITE, other.write),
                  (EXCLUSIVE, other.exclusive))
        for mode_a, set_a in mine:
            for mode_b, set_b in theirs:
                if mode_a == READ and mode_b == READ:
                    continue
                shared = set_a & set_b
                if shared:
                    ref = sorted(str(r) for r in shared)[0]
                    return f"{ref}: {mode_a} vs {mode_b}"
        for mode_a, set_a in mine:
            for mode_b, set_b in theirs:
                for ref_a in set_a:
                    for ref_b in set_b:
                        if _ranges_overlap(ref_a, ref_b):
                            return (f"{ref_a} overlaps {ref_b}: "
                                    f"{mode_a} vs {mode_b}")
                        # Both directions: the existing lease may hold
                        # the parent and the new request the child, or
                        # the existing lease may hold the child and the
                        # new request the parent. A check that only
                        # walks one way grants a parent write that
                        # contains a child another agent holds.
                        if _is_ancestor(ref_a, ref_b):
                            if _hierarchical_conflict(mode_a, mode_b):
                                return (f"{ref_b} is inside {ref_a}: "
                                        f"{mode_a} vs {mode_b}")
                        if _is_ancestor(ref_b, ref_a):
                            if _hierarchical_conflict(mode_b, mode_a):
                                return (f"{ref_a} is inside {ref_b}: "
                                        f"{mode_b} vs {mode_a}")
        return None

    def to_record(self) -> Dict[str, list]:
        return {
            READ: sorted(str(r) for r in self.read),
            WRITE: sorted(str(r) for r in self.write),
            EXCLUSIVE: sorted(str(r) for r in self.exclusive),
        }

    @classmethod
    def from_record(cls, record: dict) -> "LeaseDeclaration":
        return cls(
            read=frozenset(ResourceRef.parse(r) for r in record.get(READ, ())),
            write=frozenset(ResourceRef.parse(r) for r in record.get(WRITE, ())),
            exclusive=frozenset(ResourceRef.parse(r)
                                for r in record.get(EXCLUSIVE, ())),
        )


#: Which kind contains which. The spec's resources nest - a project
#: holds timelines, a timeline holds tracks, clips and ranges - and a
#: write to the parent invalidates the child inside it. The child's
#: NAME carries the placement: a track is `timeline:row`, a clip
#: placed in a timeline is `timeline:clip_id`, a range is
#: `timeline:start-end`. A clip in the media pool has no timeline in
#: its name, so it is not a child of any timeline until a caller
#: declares the placement.
CONTAINS: Dict[str, Set[str]] = {
    "project": {"timeline"},
    "timeline": {"track", "clip", "timeline_range"},
    "track": {"clip"},
}


def _is_ancestor(parent: ResourceRef, child: ResourceRef) -> bool:
    """True where `parent` is a strict ancestor of `child`.

    The child's kind must be one the parent's kind contains, and the
    child's name must be the parent's name plus a `:` path - the track
    `Reel 03:video:3` is inside the timeline `Reel 03`, the clip
    `Reel 03:A004_C013` is the clip as placed in `Reel 03`. A name
    that is merely a prefix (a clip named `Reel 03` and a track named
    `Reel 03:video:3`) is not a hierarchy: the kind pair decides, and
    a bare clip is not contained in a timeline by name.
    """
    if CONTAINS.get(parent.kind, set()) and child.kind in CONTAINS[parent.kind]:
        return child.name.startswith(parent.name + ":")
    return False


def _range_bounds(ref: ResourceRef) -> Optional[Tuple[str, int, int]]:
    """`(timeline, start, end)` for a `timeline_range` ref, else None.

    The name is `timeline:start-end` - the last `:` separates the
    timeline from the span, so a timeline whose own name contains a
    colon still parses. None where the name is not a range: a range
    that cannot be read cannot overlap anything.
    """
    if ref.kind != "timeline_range":
        return None
    head, sep, tail = ref.name.rpartition(":")
    if not sep or not head:
        return None
    start_text, dash, end_text = tail.partition("-")
    if not dash:
        return None
    try:
        return head, int(start_text), int(end_text)
    except ValueError:
        return None


def _ranges_overlap(first: ResourceRef, second: ResourceRef) -> bool:
    """True where two `timeline_range` refs on one timeline overlap.

    Two ranges on the same timeline conflict only where they share a
    frame - the spec's "two agents changing overlapping timeline
    regions". Ranges on different timelines never overlap, and a range
    that cannot be read overlaps nothing (fail-open for the reader,
    fail-closed for the writer, which the same-resource rule already
    covers).
    """
    a, b = _range_bounds(first), _range_bounds(second)
    if a is None or b is None:
        return False
    timeline_a, start_a, end_a = a
    timeline_b, start_b, end_b = b
    if timeline_a != timeline_b:
        return False
    return start_a < end_b and start_b < end_a


def _hierarchical_conflict(parent_mode: str, child_mode: str) -> bool:
    """Does a parent's access mode conflict with a child's, given the
    child is inside the parent?

    The full parent-mode x child-mode table:

    | parent \\ child | READ | WRITE | EXCLUSIVE |
    |-----------------|------|-------|-----------|
    | READ            | no   | YES   | YES       |
    | WRITE           | YES  | YES   | YES       |
    | EXCLUSIVE       | YES  | YES   | YES       |

    READ on the parent conflicts with WRITE and EXCLUSIVE on the child
    (reading the whole timeline is invalidated when a track changes or
    is deleted - deleting a clip invalidates the read at least as much
    as editing it). WRITE on the parent conflicts with anything on the
    child: a restructure can move, drop or rename a track or clip, so a
    child reader's view is invalidated exactly as a child writer's is.
    EXCLUSIVE on the parent conflicts with anything on the child
    (deleting the timeline deletes every track and clip in it).
    """
    if parent_mode in (WRITE, EXCLUSIVE):
        return True
    if parent_mode == READ and child_mode in (WRITE, EXCLUSIVE):
        return True
    return False


def _track_lock_parts(ref: ResourceRef) -> Optional[Tuple[str, int]]:
    """`(trackType, index)` for a `track` resource ref, else None.

    A track is named `timeline:trackType:index` - the last two
    colons separate the timeline, the track type and the 1-based track
    number. None where the ref is not a track or the name does not
    parse: a track that cannot be read cannot be locked.
    """
    if ref.kind != "track":
        return None
    head, sep, tail = ref.name.rpartition(":")
    if not sep or not head:
        return None
    timeline, track_type = head.rsplit(":", 1) if ":" in head else (head, "")
    if not track_type or not tail:
        return None
    try:
        return track_type, int(tail)
    except ValueError:
        return None


def take_track_lock(timeline, ref: ResourceRef) -> bool:
    """Take Resolve's NATIVE track lock for a track resource.

    The one lock Resolve scripts can take (per the collaboration
    scout), taken ALONGSIDE the Ren-side resource lease, never instead
    of it: the Ren-side lease is what lets a compatible task be in
    flight, and this is what stops a writer that never asked the Ren
    layer at all. Returns True where the lock was taken, False where
    the ref is not a track or the name does not parse - a track that
    cannot be read cannot be locked, and the Ren-side lease still
    applies.
    """
    parts = _track_lock_parts(ref)
    if parts is None:
        return False
    track_type, index = parts
    try:
        return bool(timeline.SetTrackLock(track_type, index, True))
    except Exception:  # noqa: BLE001 - a lock that cannot be taken is reported
        return False


def release_track_lock(timeline, ref: ResourceRef) -> bool:
    """Release the native track lock `take_track_lock` took."""
    parts = _track_lock_parts(ref)
    if parts is None:
        return False
    track_type, index = parts
    try:
        return bool(timeline.SetTrackLock(track_type, index, False))
    except Exception:  # noqa: BLE001 - a release that cannot be taken is reported
        return False


@dataclass
class ResourceLease:
    """One active lease, as recorded on disk.

    `heartbeat` is the last time the holder heartbeated; the lease
    expires one `heartbeat_interval` after it. `heartbeat_interval` is
    the holder's OWN declaration of how often it will heartbeat - the
    source of the expiry, not a constant this module invented.
    """

    owner: str
    purpose: str
    pid: int
    host: str
    since: float
    heartbeat: float
    heartbeat_interval: float
    declaration: LeaseDeclaration

    def is_expired(self, now: Optional[float] = None) -> bool:
        """True where the holder stopped heartbeating past its interval."""
        moment = time.time() if now is None else now
        return (moment - self.heartbeat) > self.heartbeat_interval

    def describe(self) -> str:
        return (f"{self.owner} (pid {self.pid} on {self.host}): "
                f"{self.purpose} - held {max(0.0, time.time() - self.since):.0f}s")

    def to_record(self) -> dict:
        record = {
            "owner": self.owner,
            "purpose": self.purpose,
            "pid": self.pid,
            "host": self.host,
            "since": self.since,
            "heartbeat": self.heartbeat,
            "heartbeat_interval": self.heartbeat_interval,
        }
        record.update(self.declaration.to_record())
        return record

    @classmethod
    def from_record(cls, record: dict) -> "ResourceLease":
        return cls(
            owner=str(record.get("owner", "")),
            purpose=str(record.get("purpose", "")),
            pid=int(record.get("pid", 0) or 0),
            host=str(record.get("host", "")),
            since=float(record.get("since", 0.0) or 0.0),
            heartbeat=float(record.get("heartbeat", 0.0) or 0.0),
            heartbeat_interval=float(record.get("heartbeat_interval", 0.0)
                                     or DEFAULT_HEARTBEAT_INTERVAL),
            declaration=LeaseDeclaration.from_record(record),
        )


def leases_path() -> Path:
    """Where active leases live - the `resolve_lock` lock directory."""
    return lock_dir() / LEASES_FILENAME


def _flock(handle, exclusive: bool, blocking: bool) -> bool:
    """True if taken. False only for a non-blocking miss.

    The same primitive `resolve_lock` uses - this layer builds on it,
    not beside it.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        return True
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except OSError:
        return False
    return True


def _unflock(handle) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_leases() -> list:
    """Every lease on disk, expired or not. A diagnostic read."""
    try:
        body = json.loads(leases_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(body, list):
        return []
    leases = []
    for record in body:
        if not isinstance(record, dict):
            continue
        try:
            leases.append(ResourceLease.from_record(record))
        except (TypeError, ValueError):
            continue
    return leases


def _write_leases(leases: list) -> None:
    """Write the lease list back, atomically.

    Temp plus `os.replace` in the same directory, so a reader never
    sees half a file - the same rule `declaration_keys._write_atomic`
    holds for the keyed stores.
    """
    path = leases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps([lease.to_record() for lease in leases], indent=2) + "\n"
    import tempfile
    handle, tmp = tempfile.mkstemp(dir=str(path.parent),
                                   prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover - best effort
            pass
        raise


@contextmanager
def _leases_file(exclusive: bool):
    """Hold the lease file's flock across a read-modify-write.

    The file is the shared state; the flock is what makes a
    read-modify-write atomic against another holder. Absence of the
    flock is not correctness - a holder that never takes it is caught
    by the heartbeat expiry, which is the load-bearing half.
    """
    path = leases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        if exclusive:
            _flock(handle, exclusive=True, blocking=True)
        else:
            _flock(handle, exclusive=False, blocking=True)
        yield
    finally:
        _unflock(handle)
        handle.close()


def _live_leases(now: Optional[float] = None) -> list:
    """Leases whose heartbeat has not gone stale. Expired ones drop."""
    return [lease for lease in _read_leases()
            if not lease.is_expired(now=now)]


def _emit(project: str, event_type: str, **fields) -> None:
    """Emit one lease event into the project's event log, or silently skip.

    The event log is per-project; a lease whose caller named no project
    leaves no event, which is the honest answer - the lease is
    machine-wide and the project is the caller's to declare. A failure
    to emit must never take down a real lease: the lease is the fence,
    the event log is the record of it.
    """
    if not project:
        return
    try:
        event_log.emit(project, event_type, **fields)
    except OSError:
        pass


def _conflict(lease: ResourceLease, declaration: LeaseDeclaration) -> Optional[str]:
    return lease.declaration.conflicts_with(declaration)


#: Leases THIS process holds, for same-thread reentrancy. A nested
#: declaration in the same thread that the outer lease already covers
#: is granted without a conflict check - the way `resolve_lease` is
#: reentrant within a process. Without it a touch-up that triggers a
#: timeline build on the same timeline refuses itself: the outer
#: touch-up holds WRITE on the timeline and the inner build asks the
#: same, and the disk check sees the outer lease as a blocker.
#: Entries are `(thread_ident, lease)`: another thread in this process
#: is a different contender and still waits on the disk state, which
#: is what keeps `test_a_waiter_proceeds_once_the_holder_releases`
#: (threads) blocking while nested (same-thread) work proceeds.
_held_leases: list = []

_MODE_RANK: Dict[str, int] = {READ: 0, WRITE: 1, EXCLUSIVE: 2}


def _held_modes(lease: ResourceLease) -> Dict[ResourceRef, str]:
    """The modes in which one lease holds each resource."""
    modes: Dict[ResourceRef, str] = {}
    for mode in MODES:
        for ref in getattr(lease.declaration, mode):
            modes[ref] = mode
    return modes


def _covered_by_held(declaration: LeaseDeclaration, owner: str) -> bool:
    """True where this thread already holds a lease covering every
    resource the new declaration names, in a mode equal or stricter,
    for the same owner.

    A held EXCLUSIVE covers a requested WRITE or READ on the same
    resource; a held WRITE covers a requested READ. A resource the
    outer lease does not hold at all is NOT covered - the nested
    request must still contend for it on disk against other holders.
    A held READ does not cover a requested WRITE: upgrading mid-section
    would silently exclude the readers the outer lease admitted, so
    the nested write contends (and refuses on its own outer lease -
    a caller bug, widen the outer declaration). A held lease whose
    heartbeat went stale covers nothing: the outer section already lost
    its declaration, so the nested request contends on disk like anyone
    and the holder learns the expiry instead of silently re-entering.
    """
    ident = threading.get_ident()
    now = time.time()
    for mode in MODES:
        for ref in getattr(declaration, mode):
            needed = _MODE_RANK[mode]
            covered = False
            for held_ident, held in _held_leases:
                if held_ident != ident or held.owner != owner:
                    continue
                if held.is_expired(now=now):
                    continue
                held_mode = _held_modes(held).get(ref)
                if held_mode is not None and _MODE_RANK[held_mode] >= needed:
                    covered = True
                    break
            if not covered:
                return False
    return True


def _track_held(record: ResourceLease) -> None:
    _held_leases.append((threading.get_ident(), record))


def _untrack_held(record: ResourceLease) -> None:
    for index in range(len(_held_leases) - 1, -1, -1):
        if _held_leases[index][1] is record:
            del _held_leases[index]
            return


class _Heartbeat:
    """A holder's background heartbeat, so a live lease never expires.

    A daemon thread firing twice per interval. The holder does not have
    to remember to heartbeat - a task that is mid-LLM-call for longer
    than its interval is exactly the case the heartbeat exists for, and
    a thread that fires while the holder is thinking is what keeps the
    lease alive. The thread is a daemon so it never blocks exit.
    """

    def __init__(self, lease: "ResourceLeaseHandle"):
        self._lease = lease
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="resource-lease-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = self._lease.record.heartbeat_interval * _HEARTBEAT_FRACTION
        while not self._stop.wait(max(0.05, interval)):
            try:
                self._lease.heartbeat()
            except ResourceLeaseExpired:
                return


@dataclass
class ResourceLeaseHandle:
    """The lease this process holds, yielded by `resource_lease`.

    `heartbeat` is the explicit form - a caller that prefers to
    heartbeat itself (a long synchronous section with no thread) can
    call it and pass `heartbeat_thread=False` to the context manager.

    `project` is the project folder this lease's events are emitted
    into, where the caller named one - the event log is per-project,
    and a lease that cannot name its project leaves no event for a
    resumed worker to read.
    """

    record: ResourceLease
    _heartbeat: Optional[_Heartbeat] = field(default=None, repr=False)
    _reentrant: bool = field(default=False, repr=False)
    project: str = ""

    def heartbeat(self) -> None:
        """Say "I am still here", or raise where the lease is gone.

        A reentrant (nested, memory-only) handle is kept alive by its
        outer lease's heartbeat, so heartbeating it is a no-op.

        Re-reads the file under the flock and updates THIS lease's
        heartbeat in place. A lease that expired while the holder was
        working is no longer on disk - the heartbeat raises
        `ResourceLeaseExpired` rather than resurrecting it, because a
        declaration that came back to life after expiring would let two
        holders believe they are alone.
        """
        if self._reentrant:
            return
        with _leases_file(exclusive=True):
            leases = _read_leases()
            for index, lease in enumerate(leases):
                if (lease.pid == self.record.pid
                        and lease.owner == self.record.owner
                        and lease.since == self.record.since):
                    self.record.heartbeat = time.time()
                    leases[index] = self.record
                    _write_leases(leases)
                    return
            _emit(self.project, event_log.LEASE_EXPIRED,
                  task=self.record.purpose,
                  owner=self.record.owner, pid=self.record.pid,
                  host=self.record.host,
                  heartbeat_interval=self.record.heartbeat_interval)
            raise ResourceLeaseExpired(
                f"the lease for {self.record.purpose!r} is gone - its "
                f"heartbeat went stale and a waiter evicted it",
                "the holder stopped heartbeating past its interval, so "
                "the lease expired. Stop and re-read state: what was "
                "written before the eviction stands, but this "
                "declaration is gone and the next write is unguarded.",
                "re-acquire the lease with a current read of the "
                "resource, then re-apply what you still need")

    def release(self) -> None:
        """Give the lease back. Idempotent - a released lease is gone."""
        if self._heartbeat is not None:
            self._heartbeat.stop()
            self._heartbeat = None
        _untrack_held(self.record)
        if self._reentrant:
            return
        with _leases_file(exclusive=True):
            leases = _read_leases()
            remaining = [lease for lease in leases
                         if not (lease.pid == self.record.pid
                                 and lease.owner == self.record.owner
                                 and lease.since == self.record.since)]
            if len(remaining) != len(leases):
                _write_leases(remaining)
                _emit(self.project, event_log.LEASE_RELEASED,
                      task=self.record.purpose,
                      owner=self.record.owner, pid=self.record.pid,
                      host=self.record.host)


@contextmanager
def resource_lease(declaration: LeaseDeclaration, purpose: str,
                   wait: bool = True, timeout: Optional[float] = None,
                   heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
                   owner: Optional[str] = None,
                   heartbeat_thread: bool = True,
                   project: str = "",
                   actor: Optional[str] = None):
    """Hold a resource lease for one task's destructive section.

    Acquire the lease, heartbeat it, and release it on exit. The
    instance lease (`resolve_lock.resolve_lease`) is the coarse fence
    and is taken separately by the caller where the section writes
    through the cursor; this is the fine-grained declaration that lets
    two compatible tasks be in flight at once and stops two conflicting
    ones from being so.

    `actor` names the federated participant the acquisition runs as
    (director, editor, client, ...). A named actor must hold Execute
    on this project's permission roster (`fabric_permissions`): a
    client's Ren may observe and suggest but never take a lease, and
    the refusal is an event in the log. The check runs BEFORE the wait
    loop and fails fast - a refused actor waits for nothing. `None`
    (the default) is the local operator's own act and is never
    checked, so every existing caller keeps working unchanged.

    `wait=True` (the default): a conflicting lease makes the acquisition
    WAIT - polling until the holder releases or its heartbeat expires -
    and refuse by name past `timeout`, naming the holder and the
    resource in conflict. `wait=False`: refuse immediately, which is
    the "come back later" answer for a task that would rather not queue.

    `heartbeat_interval` is the holder's own declaration of how often
    it will heartbeat - the source of the expiry. The default is
    `DEFAULT_HEARTBEAT_INTERVAL`, a technical fallback for a holder
    that states none.

    `project` names the project folder this lease's events are emitted
    into - the event log is per-project, and a lease that names none
    leaves no event for a resumed worker to read.

    Raises `ResourceLeaseBusy` naming the conflicting holder and
    resource where the wait ran out, and `ResourceLeaseExpired` from
    the heartbeat where this lease was evicted mid-section.
    Raises `fabric_permissions.PermissionRefused` where a named actor
    lacks Execute - before waiting, and without taking any lease.

    Reentrant in the same thread: a nested declaration the outer lease
    already covers (same owner, same resources, mode equal or
    stricter) is granted memory-only, the way `resolve_lease` nests -
    so a touch-up holding a timeline may build that same timeline
    without refusing itself. The nested handle writes nothing to disk;
    the outer lease's heartbeat keeps the declaration alive.
    """
    if actor is not None:
        from library.tools import fabric_permissions as _permissions
        _permissions.require(project, actor, _permissions.EXECUTE,
                             task=purpose)
    if not isinstance(declaration, LeaseDeclaration):
        raise TypeError(
            f"resource_lease needs a LeaseDeclaration, not "
            f"{type(declaration).__name__} - a declaration is the read "
            f"write exclusive sets, and a task that cannot state them "
            f"cannot be checked for compatibility.")
    interval = (float(heartbeat_interval) if heartbeat_interval
                else DEFAULT_HEARTBEAT_INTERVAL)
    if interval <= 0:
        raise ValueError(
            f"heartbeat_interval must be positive, got {interval!r} - a "
            f"lease that expires instantly excludes its own holder.")
    resolved_timeout = (resolve_lock.default_timeout() if timeout is None
                        else float(timeout))
    owner_name = owner or default_owner()
    if _covered_by_held(declaration, owner_name):
        record = ResourceLease(
            owner=owner_name, purpose=purpose,
            pid=os.getpid(), host=socket.gethostname(),
            since=time.time(), heartbeat=time.time(),
            heartbeat_interval=interval,
            declaration=declaration)
        _track_held(record)
        try:
            yield ResourceLeaseHandle(record=record, _reentrant=True,
                                      project=project)
        finally:
            _untrack_held(record)
        return
    acquired: Optional[ResourceLease] = None
    try:
        deadline = time.time() + resolved_timeout
        while True:
            with _leases_file(exclusive=True):
                leases = _read_leases()
                now = time.time()
                live = [lease for lease in leases
                        if not lease.is_expired(now=now)]
                if len(live) != len(leases):
                    _write_leases(live)
                blockers = [(lease, _conflict(lease, declaration))
                            for lease in live]
                blockers = [(lease, why) for lease, why in blockers
                            if why is not None]
                if not blockers:
                    record = ResourceLease(
                        owner=owner_name, purpose=purpose,
                        pid=os.getpid(), host=socket.gethostname(),
                        since=time.time(), heartbeat=time.time(),
                        heartbeat_interval=interval,
                        declaration=declaration)
                    _write_leases([*live, record])
                    acquired = record
                    _track_held(record)
                    _emit(project, event_log.LEASE_ACQUIRED,
                          task=purpose, owner=owner_name,
                          pid=record.pid, host=record.host,
                          heartbeat_interval=interval,
                          resources=str(declaration.to_record()))
                    break
            if not wait:
                lease, why = blockers[0]
                raise ResourceLeaseBusy(
                    f"resource lease refused for {purpose!r}: {why} - "
                    f"held by {lease.describe()}",
                    "two tasks would touch the same resource in "
                    "conflicting modes",
                    "wait for the holder to finish (wait=True), or come "
                    "back later - re-run the same command")
            if time.time() >= deadline:
                lease, why = blockers[0]
                raise ResourceLeaseBusy(
                    f"resource lease waited {resolved_timeout:g}s for "
                    f"{purpose!r}: {why} - held by {lease.describe()}",
                    "one resource, conflicting modes",
                    "wait for the holder to finish, or come back later - "
                    "re-run the same command")
            time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.time())))
        handle = ResourceLeaseHandle(record=acquired, project=project)
        if heartbeat_thread:
            handle._heartbeat = _Heartbeat(handle)
            handle._heartbeat.start()
        try:
            yield handle
        finally:
            handle.release()
    except BaseException:
        if acquired is not None:
            _untrack_held(acquired)
            with _leases_file(exclusive=True):
                leases = _read_leases()
                remaining = [lease for lease in leases
                             if not (lease.pid == acquired.pid
                                     and lease.owner == acquired.owner
                                     and lease.since == acquired.since)]
                if len(remaining) != len(leases):
                    _write_leases(remaining)
                    _emit(project, event_log.LEASE_RELEASED,
                          task=acquired.purpose, owner=acquired.owner,
                          pid=acquired.pid, host=acquired.host)
        raise


# ── The terminal: who holds what, for a human ─────────────────────────

def status() -> str:
    """One line per active lease, or a line saying none stand."""
    leases = _live_leases()
    if not leases:
        return ("resource leases: none stand - every resource is free "
                "for a declaring task.")
    lines = ["resource leases:"]
    for lease in sorted(leases, key=lambda item: item.since):
        lines.append(f"  {lease.describe()}")
        for mode in MODES:
            refs = getattr(lease.declaration, mode)
            if refs:
                lines.append(f"    {mode}: {', '.join(sorted(str(r) for r in refs))}")
    return "\n".join(lines)


def _main(argv=None) -> int:
    """`python -m library.tools.resource_leases status`."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m library.tools.resource_leases",
        description="The resource leases standing on this machine: who "
                    "holds what, in which mode.")
    parser.add_argument("command", nargs="?", default="status",
                        help="status: list the active leases (default)")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(status())
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - argparse exits first


_POLL_SECONDS = resolve_lock._POLL_SECONDS


if __name__ == "__main__":
    raise SystemExit(_main())
