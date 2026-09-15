"""One Resolve, many writers: the unit of exclusion, and the fence.

`assert_current_timeline` at the bottom is the enforcement; the rest of
this module is what makes it enforceable when more than one process is
driving the same DaVinci Resolve.

What the unit of exclusion is, and why
--------------------------------------
It is the instance's CURSOR - the (current project, current timeline)
pair - held for a CRITICAL SECTION, not for a build.

Nothing finer is available. `MediaPool.AppendToTimeline` writes to the
project's CURRENT timeline; there is no per-timeline write handle to
take a lock on, so "lock the timeline I am writing to" is not a thing
the API can express. Two agents writing two different timelines still
contend, because they contend for the one cursor.

Nothing coarser is affordable. A build is mostly not Resolve: vision,
ffmpeg, Remotion renders, planning, the LLM calls. Holding the instance
for a whole build would serialise all of that and delete the
parallelism this exists to protect. So the lease is taken around the
section that establishes the cursor and writes through it, and released
the moment that section ends.

`library/tools/concurrency_routing.py` is the table that says which
operation class needs which of these, stated so a supervisor applies it
without judgement.

Why a lock is not enough, and what the fence is for
---------------------------------------------------
The captain edits by hand in Resolve while agents build. That is the
workflow, not a hazard to design out - and a human will never take a
lock. Neither will a test that connects to Resolve incidentally.

So exclusion is only half. The other half is DETECTION: a holder
records the cursor it established and re-reads it before every write
and once more at release. `assert_current_timeline` is the per-write
check; `cursor_fence` is the whole section. A foreign move is raised by
name rather than silently written through - which is the only thing
that works against a writer who never cooperates.

What was measured, 2026-09-12, and what it settled
--------------------------------------------------
`docs/DUAL_WORKFLOW_SYNC_2026-09-12.md` is the measurement, taken from
opposite sides while a sibling lane built into the captain's project:

* From outside: 366 samples over twelve minutes, two seconds apart,
  found the placement lock UNHELD every time while that lane created a
  staging timeline, promoted it, and moved the current timeline six
  times.
* From inside: a process HELD the lock exclusively for two minutes and
  watched that lane move the current timeline three times regardless.
  `flock` behaved exactly as written. The acquisition cost 0.000s
  because there was no one to wait for.

So the lock was not merely untested. It was exercised and measured to
protect nothing, because the state it guarded is reached by a route
that does not pass through it. A mutual-exclusion primitive only
excludes parties that take it.

That is a finding about WIRING, not about locking, and this module is
the repair: the route now passes through the lease, because
`assert_current_timeline` - the 22-call-site check the measurement
found was the only real guard - refuses to run outside one.

ONE CONCLUSION OF THAT MEASUREMENT IS OVERTURNED HERE, deliberately.
It read: *"a read-only reader cannot take the placement lock to protect
itself and would not be helped by it - it does not place - so a reader
racing a writer is unmediated by design."* True of a lock only writers
take. A reader IS helped by a SHARED lease: it does not exclude other
readers, and it does exclude a writer that would delete the timeline
its handle points at halfway through the read.
`concurrency_routing.RESOLVE_READ` is that class. The rest of the
measurement stands unchanged.

Why the guard cannot be forgotten
----------------------------------
`resolve_placement_lock()` was removed on 2026-09-12 with zero callers
in library, tests, docs or scripts, while `assert_current_timeline` had
22. A guard nobody enters is worse than no guard, because it reads as
coverage (AGENTS.md 10.4). It does not come back under that name: the
name went with the shape that failed, which was a lock a caller had to
remember to take.

`resolve_lease` is the replacement and it is WIRED, with the failure
repaired at its source. Taking it is no longer a rule a caller may
forget, because `assert_current_timeline` REFUSES without one. Every
one of those 22 write paths is guarded by the check it already called,
and the work was placing the lease at eight entry points rather than at
every write. `tests/test_resolve_guard_wiring.py` pins that the entry
points hold it and that the refusal cannot be turned off from inside
`library/`.

The captain's signal: deference, not just detection
---------------------------------------------------
Detection protects the agent FROM the captain. The other direction -
protecting the captain from the agents - is a file the captain owns,
next to the lease, whose mere presence means "hands off, I am
editing": `captain_hold()` sets it, `captain_release()` clears it,
`captain_present()` reads it, and
`python -m library.tools.resolve_lock captain-status` says aloud
whether it is set. Every `resolve_lease` acquisition waits for it to
clear before contending for the flock; `prefer_lease` does not, because
a human-initiated action never queues behind its own owner's signal.

Four properties, stated so a later reader does not renegotiate them:

* An agent that finds the captain present WAITS, not fails. Failing
  would punish the captain for touching their own editor, and failing
  mid-build is its own damage. This is the opposite of what
  `cursor_fence` does to a foreign cursor move on purpose, and both
  behaviours are correct for their own direction.
* A held lease is NEVER revoked. An agent inside its critical section
  when the signal appears finishes the section - seconds long, cursor
  already established - and releases. A half-written timeline is worse
  than a delayed one, which is the same reason the handbrake finishes
  the step it is in before stopping (`run_control.py`). The fence
  still detects any cursor move that actually happened meanwhile.
* A stale signal raises instead of wedging or expiring. The wait is
  bounded by the acquisition's own timeout; past it, `CaptainPresent`
  (a `ResolveBusy`, so the live-Resolve suite still skips on it) names
  the signal path, its age, and the exact clear command. Nothing
  auto-clears it: expiring the hold behind the captain's back would
  make the guarantee a lie exactly when they are relying on it, and
  proceeding while it stands would too.
* Presence is the signal, and presence fail-closed. An unreadable
  signal file still counts as set - failing open would run the build
  the captain asked to stop, the same rule `run_control` holds for
  `pipeline.hold`.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

PLACEMENT_REQUIRES_CURRENT = (
    "MediaPool.AppendToTimeline appends to the project's CURRENT "
    "timeline. Call project.SetCurrentTimeline(timeline) before placing, "
    "and read back what landed. AddTrack and SetTrackName DO act on the "
    "handle you pass, which is what makes a timeline handle look like a "
    "destination when it is not."
)
"""Why a timeline handle is not a destination.

Measured 2026-09-04: all 579 captions for sixteen reels were appended
while Reel 01 was current, so Reel 01 collected what it could and the
other fifteen ended up with an empty V3 - while every call returned True
and the run reported "placed 26/26" sixteen times.

The reel builder appeared to work only because `CreateEmptyTimeline`
makes its result current, so each reel happened to be current when its
own clips were appended. That is luck, not correctness, and it stops
being luck the moment anything is placed onto a timeline that already
exists.
"""


class ResolveRaceError(RuntimeError):
    pass


class UnguardedPlacementError(RuntimeError):
    """A write into Resolve was attempted outside a lease."""


class ResolveBusy(RuntimeError):
    """Another writer holds the instance. Carries who, since when."""


class CaptainPresent(ResolveBusy):
    """The captain is in Resolve, by their own signal. Wait, then this.

    Raised only where a lease acquisition waited the whole timeout for
    the captain's signal to clear and it never did. A `ResolveBusy`
    subclass, so anything that already handles "could not take the
    instance" - including the live-Resolve suite's skip - keeps
    working, with the message naming the signal, its age, and how to
    clear it instead of naming a lane.
    """


# ── Where the lease lives ───────────────────────────────────────────
#
# One Resolve per machine (the scripting bridge is a single fixed port
# and there is no instance selector, so a second headless Resolve is
# unaddressable by our own code - filed captain decision
# `vep-resolve-statefulness-hazards`). The lease is therefore
# machine-wide and lives in the system temp directory, NOT in a
# project: two lanes on two worktrees of two projects still drive the
# one app, and a per-project lock would let them both in.
#
# `PIPELINE_RESOLVE_LOCK_DIR` redirects it, which is how a test
# exercises contention without competing with a live captain.

LOCK_DIR_ENV = "PIPELINE_RESOLVE_LOCK_DIR"
LOCK_FILENAME = "resolve_instance.lock"
LEASE_FILENAME = "resolve_instance.lease.json"

#: How long a waiter waits before naming the holder and giving up. A
#: build's cursor sections are seconds; a whole reel build that takes
#: the lease per reel still finishes inside this. Overridable per run.
TIMEOUT_ENV = "PIPELINE_RESOLVE_LEASE_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 900.0

#: A HOLDER's pid, exported into the environment while it holds, so a
#: child process it spawns inherits the lease instead of deadlocking on
#: it.  This is not an optimisation - it is required for correctness.
#: `resolve_build_timeline` holds the instance and then launches
#: `apply_fusion_comps` in its own process, because creating a timeline
#: and calling `ImportFusionComp` in one process corrupts the comp
#: (AGENTS.md 5).  The parent is blocked waiting on the child, so the
#: child asking for a lock the parent holds is a self-deadlock with a
#: fifteen-minute fuse, and no amount of waiting resolves it.
#:
#: Safe because the environment only travels DOWNWARD: seeing this
#: variable means an ancestor of this process holds the lease, and an
#: ancestor blocked on us is not a concurrent writer.  The pid is
#: checked alive, so a stale value inherited from a dead holder falls
#: through to a real acquisition.
INHERIT_ENV = "PIPELINE_RESOLVE_LEASE_HELD_BY"

_POLL_SECONDS = 0.25


def lock_dir() -> Path:
    directory = os.environ.get(LOCK_DIR_ENV) or tempfile.gettempdir()
    return Path(directory)


def lock_path() -> Path:
    return lock_dir() / LOCK_FILENAME


def lease_path() -> Path:
    return lock_dir() / LEASE_FILENAME


def default_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Lease:
    """Who is holding the instance, and what for."""

    owner: str
    purpose: str
    pid: int
    host: str
    since: float

    def held_for(self) -> float:
        return max(0.0, time.time() - self.since)

    def describe(self) -> str:
        return (f"{self.owner} (pid {self.pid} on {self.host}): "
                f"{self.purpose} - held {self.held_for():.0f}s")


def default_owner() -> str:
    """A name a waiter can act on.

    `FIRSTMATE_TASK` is the lane name when a crewmate is driving, which
    is the case a waiter most needs to read back; otherwise the process
    name and pid, which at least says it is not the captain.
    """
    for variable in ("FIRSTMATE_TASK", "PIPELINE_RUN_OWNER"):
        value = os.environ.get(variable)
        if value:
            return value
    return f"pid-{os.getpid()}"


# ── The captain's signal ────────────────────────────────────────────
#
# One Resolve per machine, so the signal lives next to the lease:
# machine-wide, in the same directory, under the same
# `PIPELINE_RESOLVE_LOCK_DIR` redirect the tests use. Presence is the
# whole protocol - the captain creates it and deletes it, the way
# firstmate's own away posture is the presence of its record.
#
# At a terminal, from the repository root:
#
#     python -m library.tools.resolve_lock captain-hold --note "grading"
#     python -m library.tools.resolve_lock captain-status
#     python -m library.tools.resolve_lock captain-release
#
# A bare `touch` of the signal path also counts (an unreadable file is
# still a set signal - see the module docstring), but the CLI records
# when it was set and why, which is what the wait notice reads back.

CAPTAIN_FILENAME = "resolve_captain.flag"

#: How often a waiter waiting on the captain says so aloud. Silence
#: would read as a hang, and a signal left on by accident must be
#: visible in the very log the waiter is writing.
CAPTAIN_NOTICE_SECONDS = 30.0


def captain_path() -> Path:
    return lock_dir() / CAPTAIN_FILENAME


@dataclass(frozen=True)
class CaptainHold:
    """The captain's signal, read back: when it was set, and why."""

    since: float
    note: str
    path: str

    def age(self) -> float:
        return max(0.0, time.time() - self.since)

    def describe(self) -> str:
        when = time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(self.since))
        note = f": {self.note}" if self.note else ""
        return (f"captain's signal {self.path}, set {when} "
                f"({self.age():.0f}s ago){note}")


def captain_present() -> Optional[CaptainHold]:
    """The captain's signal if it stands, else None. Fail-closed.

    A file that is present but unreadable still counts as set. Failing
    open here would run the build the captain asked to stop.
    """
    path = captain_path()
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        since = float(body.get("since", 0) or 0)
        note = str(body.get("note", "") or "")
    except (OSError, ValueError, TypeError, AttributeError):
        since, note = 0.0, ""
    if not since:
        try:
            since = path.stat().st_mtime
        except OSError:
            since = time.time()
    return CaptainHold(since=since, note=note, path=str(path))


def captain_hold(note: str = "") -> CaptainHold:
    """Set the captain's signal. Agents wait until it is released."""
    lock_dir().mkdir(parents=True, exist_ok=True)
    record = {"since": time.time(), "note": note,
              "pid": os.getpid(), "host": socket.gethostname()}
    captain_path().write_text(json.dumps(record, indent=2) + "\n",
                              encoding="utf-8")
    return captain_present()  # the record as a waiter will read it


def captain_release() -> bool:
    """Clear the captain's signal. True if one stood."""
    try:
        captain_path().unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def captain_status() -> str:
    """One line saying whether the signal stands, for a human."""
    record = captain_present()
    if record is None:
        return "captain: not in Resolve - the instance is free for agents."
    return (f"captain: IN RESOLVE - {record.describe()} - agents wait. "
            f"Clear with: "
            f"python -m library.tools.resolve_lock captain-release")


def _wait_for_captain(purpose: str, timeout: float) -> None:
    """Wait for the captain's signal to clear, bounded by `timeout`.

    Raises `CaptainPresent` naming the hold where it never clears.
    Never clears it, never proceeds past it: both would make the
    guarantee a lie. This wait has its own full timeout budget,
    separate from the flock contention wait that follows it, so each
    refusal names the phase that actually ran out.
    """
    deadline = time.time() + timeout
    last_notice = 0.0
    first = True
    while True:
        record = captain_present()
        if record is None:
            return
        now = time.time()
        if now >= deadline:
            raise CaptainPresent(
                f"the captain is in Resolve ({record.describe()}) - "
                f"waited {timeout:g}s for {purpose!r} without the signal "
                f"clearing. The hold is theirs to clear and nothing "
                f"auto-expires it: python -m library.tools.resolve_lock "
                f"captain-release. Not starting is the guarantee - no "
                f"agent writes while the signal stands.")
        if first or now - last_notice >= CAPTAIN_NOTICE_SECONDS:
            print(f"waiting: {record.describe()} - holding this lease "
                  f"acquisition for {purpose!r} until "
                  f"`captain-release` clears it.",
                  file=sys.stderr)
            first, last_notice = False, now
        time.sleep(min(_POLL_SECONDS, max(0.0, deadline - now)))


# ── The lease ───────────────────────────────────────────────────────

_depth = 0
_mode: Optional[str] = None
_sole_writer_reason: Optional[str] = None


def _flock(handle, exclusive: bool, blocking: bool) -> bool:
    """True if taken. False only for a non-blocking miss."""
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


def holder() -> Optional[Lease]:
    """The recorded holder, or None. A DIAGNOSTIC, never a decision.

    Read without the lock, so it can be stale by the time it is
    printed. Nothing may branch on it - the flock is the truth. It
    exists so a waiter can say WHO it is waiting for instead of
    hanging silently, which is the whole difference between the test
    suite failing and the test suite wedging at 0% CPU.
    """
    try:
        body = json.loads(lease_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Lease(**body)
    except TypeError:
        return None


def held() -> bool:
    """Does THIS process hold the instance (or stand in for holding it)?"""
    return _depth > 0 or _sole_writer_reason is not None


@contextmanager
def assume_sole_writer(reason: str):
    """Declare that no other writer exists, satisfying the guard.

    For a context where the claim is TRUE and a real flock would be
    theatre: a test whose Resolve is a mock has no instance to contend
    for, and a lease taken there would contend with the captain's live
    session for nothing.

    Production code must never call this - `library/` is not asking the
    question, it is answering it, and a module that declares itself the
    sole writer has simply turned the guard off.
    `tests/test_resolve_guard_wiring.py` fails if any file under
    `library/` calls it.
    """
    global _sole_writer_reason
    if not reason or not reason.strip():
        raise ValueError(
            "assume_sole_writer needs a reason: it turns the guard off, "
            "and an unexplained exemption is how a guard stops meaning "
            "anything.")
    previous, _sole_writer_reason = _sole_writer_reason, reason
    try:
        yield
    finally:
        _sole_writer_reason = previous


@contextmanager
def resolve_lease(purpose: str, exclusive: bool = True,
                  timeout: Optional[float] = None,
                  owner: Optional[str] = None,
                  honor_captain: bool = True):
    """Hold the Resolve instance for one critical section.

    Exclusive by default: a cursor operation is a write even when it
    reads, because establishing the cursor IS a write. `exclusive=False`
    is for a read that names its own handle and never touches the
    cursor; several of those run together.

    Reentrant within a process - a nested exclusive section inside an
    exclusive one is a no-op, so an entry point may take the lease
    without every inner helper having to know whether it already has
    it. A SHARED holder asking to upgrade RAISES rather than
    deadlocking on itself.

    Where the captain's signal stands, the acquisition WAITS for it to
    clear first - up to the same `timeout` - and only then contends for
    the flock, which gets its own full budget. `honor_captain=False`
    is the human-initiated case and belongs to `prefer_lease` alone:
    the owner's own action never queues behind the owner's signal.

    Raises `ResolveBusy` naming the current holder rather than waiting
    forever. A wait with no bound and no diagnostic is what wedged four
    full-suite runs at 0% CPU on 2026-09-11. Where the wait was on the
    captain's signal, the refusal is `CaptainPresent`, which names the
    signal, its age, and how to clear it.
    """
    global _depth, _mode
    inherited = inherited_holder()
    if _depth == 0 and inherited is not None:
        _depth, _mode = 1, "inherited"
        try:
            yield holder()
        finally:
            _depth, _mode = 0, None
        return
    if _depth > 0:
        if exclusive and _mode == "shared":
            raise ResolveBusy(
                "this process holds the Resolve instance SHARED and is "
                "asking for it EXCLUSIVE. flock cannot upgrade in place "
                "without dropping first, and dropping mid-section is "
                "exactly the window a foreign writer moves the cursor "
                "in. Take the exclusive lease at the outer call.")
        _depth += 1
        try:
            yield holder()
        finally:
            _depth -= 1
        return

    resolved_timeout = (default_timeout() if timeout is None
                        else float(timeout))
    if honor_captain:
        _wait_for_captain(purpose, resolved_timeout)
    deadline = time.time() + resolved_timeout
    lock_dir().mkdir(parents=True, exist_ok=True)
    handle = open(lock_path(), "a+", encoding="utf-8")
    try:
        while not _flock(handle, exclusive, blocking=False):
            if time.time() >= deadline:
                current = holder()
                raise ResolveBusy(
                    f"Resolve is held by "
                    f"{current.describe() if current else 'another process'}"
                    f" - waited {resolved_timeout:g}s "
                    f"for {purpose!r}. One instance, no isolation: the "
                    f"only route is to wait or to come back.")
            time.sleep(_POLL_SECONDS)

        lease = Lease(owner=owner or default_owner(), purpose=purpose,
                      pid=os.getpid(), host=socket.gethostname(),
                      since=time.time())
        _depth, _mode = 1, "exclusive" if exclusive else "shared"
        previous_inherit = os.environ.get(INHERIT_ENV)
        os.environ[INHERIT_ENV] = str(os.getpid())
        if exclusive:
            _write_lease(lease)
        try:
            yield lease
        finally:
            _depth, _mode = 0, None
            if previous_inherit is None:
                os.environ.pop(INHERIT_ENV, None)
            else:
                os.environ[INHERIT_ENV] = previous_inherit
            if exclusive:
                _clear_lease()
            _unflock(handle)
    finally:
        handle.close()


@contextmanager
def prefer_lease(purpose: str, timeout: float = 2.0):
    """Take the lease if it is free; otherwise proceed and SAY SO.

    For the captain's own surfaces - the capture button, the panel -
    where a human pressed something and the answer "wait fifteen
    minutes for an agent" is not an answer. The human is a writer and
    the requirement is that they keep writing; making their button
    queue behind a build would be designing them out of their own
    workflow.

    Yields the `Lease` where it was taken and `None` where it was not,
    so a caller can record that it went ahead unguarded rather than
    pretending it was guarded. Contention here is not silent: the agent
    holding the instance finds out through its own fence, which is the
    detection half doing exactly the job it exists for.

    The captain's signal does not stop this path: a human-initiated
    action never queues behind its own owner's hold, so the acquisition
    below bypasses the captain wait (`honor_captain=False`). An agent
    holding the instance still finds out through its fence.
    """
    try:
        with resolve_lease(purpose, exclusive=True, timeout=timeout,
                           honor_captain=False) as lease:
            yield lease
        return
    except ResolveBusy:
        pass
    global _sole_writer_reason
    previous = _sole_writer_reason
    _sole_writer_reason = (
        f"{purpose}: the instance is held by "
        f"{(holder().describe() if holder() else 'another writer')}, and a "
        f"human-initiated action does not queue behind a build")
    try:
        yield None
    finally:
        _sole_writer_reason = previous


def under_lease(purpose: str, exclusive: bool = True,
                prefer: bool = False):
    """Decorator form, for an entry point that IS the critical section.

    `prefer=True` is the human-initiated case: take the instance if it
    is free, go ahead if it is not (`prefer_lease`). A row routed
    `RESOLVE_CURSOR` that a person presses by hand carries it, and the
    routing table records which rows those are - so "the captain does
    not queue" is a declared property of an operation rather than a
    thing one module quietly does.

    A `RESOLVE_CURSOR` operation in `concurrency_routing.OPERATIONS`
    whose whole body drives Resolve takes the lease here rather than at
    a seam inside it. That is deliberately COARSE, and the cost is
    stated where it is paid: `rebuild_reels_in_project` renders captions
    inside its own body, so a second reel build waits through that
    render as well as through the placement. It is the honest trade -
    two reel builds into one instance is the thing that must not
    happen - and the parallelism the routing table protects is ACROSS
    operations, not within this one.
    """
    import functools

    def wrap(function):
        @functools.wraps(function)
        def guarded(*args, **kwargs):
            if prefer:
                with prefer_lease(purpose):
                    return function(*args, **kwargs)
            with resolve_lease(purpose, exclusive=exclusive):
                return function(*args, **kwargs)
        guarded.__resolve_lease__ = (purpose, exclusive, prefer)
        return guarded
    return wrap


def inherited_holder() -> Optional[int]:
    """An ANCESTOR's pid where this process inherited the lease.

    None where nothing was inherited, or where the recorded holder is
    no longer alive - a stale value from a killed build must not let a
    fresh process write unguarded.
    """
    raw = os.environ.get(INHERIT_ENV)
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    if pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:  # alive, owned by somebody else
        return pid
    return pid


def _write_lease(lease: Lease) -> None:
    try:
        lease_path().write_text(
            json.dumps(asdict(lease), indent=2) + "\n", encoding="utf-8")
    except OSError:  # pragma: no cover - diagnostic only, never a decision
        pass


def _clear_lease() -> None:
    try:
        lease_path().unlink()
    except OSError:  # pragma: no cover - diagnostic only
        pass


# ── The fence ───────────────────────────────────────────────────────

@dataclass
class CursorDrift:
    """A foreign writer moved the cursor while the lease was held."""

    expected: str
    found: str
    at: str

    def describe(self) -> str:
        return (f"{self.at}: expected the current timeline to be "
                f"{self.expected!r}, found {self.found!r}")


def _timeline_name(timeline) -> str:
    if timeline is None:
        return "None"
    try:
        return str(timeline.GetName())
    except Exception:  # pragma: no cover - a handle Resolve invalidated
        return "<unreadable>"


def _timeline_id(timeline):
    if timeline is None:
        return None
    try:
        return timeline.GetUniqueId()
    except Exception:  # pragma: no cover - a handle Resolve invalidated
        return None


@contextmanager
def cursor_fence(project, timeline, purpose: str,
                 timeout: Optional[float] = None):
    """Hold the instance, establish the cursor, and WATCH it.

    The lease stops a cooperating writer. The fence catches the one who
    never takes it: the cursor is established on entry, re-read on exit,
    and every `assert_current_timeline` in between re-reads it too.

    On exit a foreign move RAISES `ResolveRaceError` carrying the drift,
    because a section that finished against a timeline it did not mean
    to write has already written somewhere wrong - reporting it is the
    only thing left that is true. `drift_seen` on the yielded record
    accumulates every move detected during the section, so a caller that
    wants to count interference rather than fail on it can read it back.
    """
    with resolve_lease(purpose, exclusive=True, timeout=timeout):
        assert_current_timeline(project, timeline)
        record = _FenceRecord(expected_id=_timeline_id(timeline),
                              expected_name=_timeline_name(timeline),
                              purpose=purpose)
        token = _push_fence(record)
        try:
            yield record
        finally:
            _pop_fence(token)
        current = project.GetCurrentTimeline()
        if _timeline_id(current) != record.expected_id:
            record.drift_seen.append(CursorDrift(
                expected=record.expected_name,
                found=_timeline_name(current),
                at=f"exit of {purpose!r}"))
        if record.drift_seen:
            raise ResolveRaceError(
                "Timeline race: "
                + "; ".join(d.describe() for d in record.drift_seen)
                + ". A writer that does not take the lease moved the "
                "cursor during this section - the captain editing by "
                "hand, or a test that connected to Resolve incidentally. "
                "Whatever this section wrote after the move went "
                "somewhere else.")


@dataclass
class _FenceRecord:
    expected_id: object
    expected_name: str
    purpose: str

    def __post_init__(self):
        self.drift_seen: list = []


_fences: list = []


def _push_fence(record: _FenceRecord):
    _fences.append(record)
    return len(_fences)


def _pop_fence(token: int) -> None:
    del _fences[token - 1:]


def current_fence() -> Optional[_FenceRecord]:
    return _fences[-1] if _fences else None


@contextmanager
def cursor_excursion(project, timeline, purpose: str = "read"):
    """Move the cursor, do something, and put it back - under the guard.

    The holder sometimes has to READ a timeline that is not the one its
    section is writing.  A carried read-back is the case this was built
    for: what Resolve returns for a clip's transform depends on which
    timeline is current, so the only comparable reading of a reel is
    taken with that reel current (`reel_rebuild_need.carried_digest_live`,
    `docs/READING_A_TRANSFORM.md`).

    Both moves are real writes and go through `assert_current_timeline`,
    so they refuse outside a lease exactly as every other write does.
    What they must NOT do is read as INTERFERENCE.  An enclosing
    `cursor_fence` measures every move against ITS expected timeline, so
    a holder's own excursion - leased, deliberate, and returned - would
    be added to `drift_seen` and raised at the fence's exit as "a writer
    that does not take the lease moved the cursor".  Measured on the
    merge of #1040: a self-read inside a fence made the fence raise,
    naming the captain, while the cursor had in fact been put back
    exactly where the fence expected it.  A guard that cries wolf about
    its own holder is worse than no guard, because the fence exists to
    catch the one writer who never cooperates.

    So each move inside the excursion is measured against the cursor as
    it ACTUALLY IS at that moment rather than against the enclosing
    section's expectation.  The enclosing fence's own exit check is
    untouched: if the excursion fails to restore the cursor, that fence
    still catches it, which is the property worth keeping.
    """
    previous = None
    try:
        previous = project.GetCurrentTimeline()
    except Exception:                                     # noqa: BLE001
        previous = None

    def _move(target):
        if current_fence() is None:
            assert_current_timeline(project, target)
            return
        # Under a fence, measure this move against where the cursor
        # actually IS, so the holder's own move is not charged to the
        # enclosing section as interference.
        here = project.GetCurrentTimeline()
        token = _push_fence(_FenceRecord(
            expected_id=_timeline_id(here),
            expected_name=_timeline_name(here),
            purpose=f"cursor excursion: {purpose}"))
        try:
            assert_current_timeline(project, target)
        finally:
            _pop_fence(token)

    _move(timeline)
    try:
        yield previous
    finally:
        if previous is not None:
            _move(previous)


# ── The per-write check, which is where the refusal lands ───────────

def assert_current_timeline(project, expected_timeline):
    """Verify the current timeline is the expected one, under a lease.

    Two refusals, and the second is the one that is new.

    A cursor that MOVED raises `ResolveRaceError` - that is the 2026-09-04
    defect (`PLACEMENT_REQUIRES_CURRENT` above), and the reason this
    function has 22 callers.

    A write with NO LEASE raises `UnguardedPlacementError`. Setting the
    cursor is itself a write to global state, so doing it without the
    instance is not a smaller act than appending - it is the act that
    breaks the other holder. Taking the lease is therefore not a rule a
    caller may remember or forget: the check every write path already
    makes now refuses without it.
    """
    if not held():
        raise UnguardedPlacementError(
            f"placing into {_timeline_name(expected_timeline)!r} without "
            f"holding the Resolve instance. `project.SetCurrentTimeline` "
            f"moves state every other writer is reading, so it is a "
            f"write. Wrap the section in "
            f"`resolve_lock.cursor_fence(project, timeline, purpose)` - "
            f"or `resolve_lease(...)` where the cursor is set more than "
            f"once inside it. {PLACEMENT_REQUIRES_CURRENT}")
    # READ BEFORE SETTING. What the cursor was on arrival is the only
    # evidence a foreign writer moved it; setting first destroys it,
    # which is why the original check could enforce the precondition
    # and still not know it had been violated.
    fence = current_fence()
    if fence is not None and fence.expected_id is not None:
        found = project.GetCurrentTimeline()
        if _timeline_id(found) != fence.expected_id:
            fence.drift_seen.append(CursorDrift(
                expected=fence.expected_name, found=_timeline_name(found),
                at=f"inside {fence.purpose!r}"))

    project.SetCurrentTimeline(expected_timeline)
    current = project.GetCurrentTimeline()
    if not current or current.GetUniqueId() != expected_timeline.GetUniqueId():
        raise ResolveRaceError(
            f"Timeline race: expected {expected_timeline.GetName()}, but "
            f"got {current.GetName() if current else 'None'}. Mutator "
            f"changed it!")


# ── The captain's terminal: set it, see it, clear it ────────────────

def _main(argv=None) -> int:
    """`python -m library.tools.resolve_lock captain-hold|status|release`.

    The whole human interface to the signal: one command to set it,
    one to see whether it stands, one to clear it. Exit 0 where the
    instance is free for agents, 1 where the signal stands, 2 on a
    usage error - so a glance at the prompt after `captain-status`
    answers the question even before the line is read.
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m library.tools.resolve_lock",
        description="The captain's hold on the Resolve instance: while "
                    "the signal stands, every agent lease acquisition "
                    "waits instead of writing.")
    sub = parser.add_subparsers(dest="command", required=True)
    hold = sub.add_parser("captain-hold",
                          help="set the signal: agents wait until it clears")
    hold.add_argument("--note", default="",
                      help="what you are doing, read back to waiting agents")
    sub.add_parser("captain-status",
                   help="say whether the signal stands")
    sub.add_parser("captain-release",
                   help="clear the signal: agents may build again")
    args = parser.parse_args(argv)
    if args.command == "captain-hold":
        record = captain_hold(note=args.note)
        print(f"captain: HOLDING Resolve - {record.describe()} - agents "
              f"will wait. Clear with: python -m library.tools.resolve_lock "
              f"captain-release")
        return 1
    if args.command == "captain-status":
        line = captain_status()
        print(line)
        return 1 if captain_present() is not None else 0
    if args.command == "captain-release":
        if captain_release():
            print("captain: released - the instance is free for agents.")
        else:
            print("captain: no signal stood - nothing was holding agents.")
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - argparse exits first


if __name__ == "__main__":
    raise SystemExit(_main())
