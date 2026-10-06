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

from library.tools import resolve_lock
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

        The compatibility rule: two READS on one resource are
        compatible; every other pair of modes on the same resource
        conflicts. The reason names the resource and both modes, so a
        waiter can act on it - a conflict that cannot be described
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


def _conflict(lease: ResourceLease, declaration: LeaseDeclaration) -> Optional[str]:
    return lease.declaration.conflicts_with(declaration)


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
    """

    record: ResourceLease
    _heartbeat: Optional[_Heartbeat] = field(default=None, repr=False)

    def heartbeat(self) -> None:
        """Say "I am still here", or raise where the lease is gone.

        Re-reads the file under the flock and updates THIS lease's
        heartbeat in place. A lease that expired while the holder was
        working is no longer on disk - the heartbeat raises
        `ResourceLeaseExpired` rather than resurrecting it, because a
        declaration that came back to life after expiring would let two
        holders believe they are alone.
        """
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
        with _leases_file(exclusive=True):
            leases = _read_leases()
            remaining = [lease for lease in leases
                         if not (lease.pid == self.record.pid
                                 and lease.owner == self.record.owner
                                 and lease.since == self.record.since)]
            if len(remaining) != len(leases):
                _write_leases(remaining)


@contextmanager
def resource_lease(declaration: LeaseDeclaration, purpose: str,
                   wait: bool = True, timeout: Optional[float] = None,
                   heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
                   owner: Optional[str] = None,
                   heartbeat_thread: bool = True):
    """Hold a resource lease for one task's destructive section.

    Acquire the lease, heartbeat it, and release it on exit. The
    instance lease (`resolve_lock.resolve_lease`) is the coarse fence
    and is taken separately by the caller where the section writes
    through the cursor; this is the fine-grained declaration that lets
    two compatible tasks be in flight at once and stops two conflicting
    ones from being so.

    `wait=True` (the default): a conflicting lease makes the acquisition
    WAIT - polling until the holder releases or its heartbeat expires -
    and refuse by name past `timeout`, naming the holder and the
    resource in conflict. `wait=False`: refuse immediately, which is
    the "come back later" answer for a task that would rather not queue.

    `heartbeat_interval` is the holder's own declaration of how often
    it will heartbeat - the source of the expiry. The default is
    `DEFAULT_HEARTBEAT_INTERVAL`, a technical fallback for a holder
    that states none.

    Raises `ResourceLeaseBusy` naming the conflicting holder and
    resource where the wait ran out, and `ResourceLeaseExpired` from
    the heartbeat where this lease was evicted mid-section.
    """
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
        handle = ResourceLeaseHandle(record=acquired)
        if heartbeat_thread:
            handle._heartbeat = _Heartbeat(handle)
            handle._heartbeat.start()
        try:
            yield handle
        finally:
            handle.release()
    except BaseException:
        if acquired is not None:
            with _leases_file(exclusive=True):
                leases = _read_leases()
                remaining = [lease for lease in leases
                             if not (lease.pid == acquired.pid
                                     and lease.owner == acquired.owner
                                     and lease.since == acquired.since)]
                if len(remaining) != len(leases):
                    _write_leases(remaining)
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
