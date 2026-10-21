"""ONE exclusive broker session for a multi-call Resolve operation.

The one Resolve is scheduled by ren-resolved, and a lease is a broker
CALL: the client asks for its turn, the broker grants it, and the grant
is held until the client releases it. A multi-call Resolve operation -
a reels build, a profile run, a delivery - takes MANY leases, one per
cursor-sensitive section, and between two of them the broker is free to
grant ANOTHER lane's turn. That lane's exclusive lease moves the cursor
to ITS project, and the next section of the first operation finds a
project that is not its own: the build places against the wrong
timeline, the delivery renders the wrong project, the profile run reads
a state nobody wrote.

The session is the fix, and it is built ON the existing grant path
rather than beside it: the operation takes ONE exclusive broker claim
and keeps it for its whole duration, every lease inside it is served by
that one claim, and no other lane's turn is granted in between. The
claim is released at the end, and a client that dies mid-session
releases it by closing its socket - the queue never wedges on a dead
holder.

What the session does and does not hold
----------------------------------------
The session holds the BROKER's turn, not the instance flock. The
instance lease is still taken per section, exactly as before, so a
build's preparation (graphics, placement plans) still runs with Resolve
free - the measured cost of holding it across a whole build. What the
session adds is that no other lane can be GRANTED a turn while it
runs, so no other lane can take an exclusive lease and move the cursor
between two sections of the same operation.

Waiting writers queue on the session. They wait for the session to end,
which is the safe direction, and the wait is bounded by the lease
timeout they asked for.

Three shapes, one rule
----------------------
* A session inside a session is the SAME session: the enclosing claim
  is the claim, and asking for a second one would queue behind our own
  claim and wait for a turn that cannot come.
* A session inside an operation that already holds the instance lease
  JOINS that lease: the enclosing grant is the claim.
* A session where no broker serves holds the instance lease alone (the
  flock is then the only fence), and says so in its record.

The deadlock this closes
------------------------
A lease taken inside a session must NOT ask the broker for another
turn: the session's own claim is holding the queue, so the new turn
would wait for the session that is waiting for it. `resolve_lock`
skips the turn for exactly that reason, and a broker JOB submitted
inside a session is refused rather than queued - the same deadlock,
one layer up.

`tests/unit/resolve/test_resolve_session.py`.
"""

from __future__ import annotations

import functools
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator, List, Optional

from library.tools import resolve_lock


@dataclass(frozen=True)
class Session:
    """One live session: what it is for, and what it is holding."""

    id: str
    purpose: str
    owner: str
    project: str
    timeline: str
    started_at: float
    claimed: bool = False
    claim_job_id: str = ""
    joined: bool = False

    def held_for(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def describe(self) -> str:
        if self.joined:
            state = "joined an enclosing claim"
        elif self.claimed:
            state = f"holding the broker's turn (job {self.claim_job_id})"
        else:
            state = "no broker claim (none is serving)"
        return (f"{self.purpose} [{self.owner}] - {state}, held "
                f"{self.held_for():.0f}s")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "purpose": self.purpose,
            "owner": self.owner,
            "project": self.project,
            "timeline": self.timeline,
            "started_at": self.started_at,
            "claimed": self.claimed,
            "claim_job_id": self.claim_job_id,
            "joined": self.joined,
            "held_seconds": round(self.held_for(), 3),
        }


#: The sessions live in THIS process. The claim is the process's, so
#: the registry is process-wide: a lease taken on another thread of the
#: same process must not ask the broker for a turn the session is
#: already holding.
_REGISTRY_LOCK = threading.Lock()
_SESSIONS: dict = {}

#: The session stack of THIS thread, so a nested session can find the
#: one it is inside.
_STACK = threading.local()


def _stack() -> list:
    stack = getattr(_STACK, "sessions", None)
    if stack is None:
        stack = []
        _STACK.sessions = stack
    return stack


def current_session() -> Optional[Session]:
    """The session THIS thread is inside, or None."""
    stack = _stack()
    return stack[-1] if stack else None


def live_sessions() -> List[Session]:
    """Every session live in this process, oldest first."""
    with _REGISTRY_LOCK:
        return sorted(_SESSIONS.values(), key=lambda s: s.started_at)


def claim_held() -> bool:
    """Whether this process holds a broker claim RIGHT NOW.

    The answer is what makes a lease inside a session skip the broker
    turn: the claim is already held, and asking again would wait for
    the session that is holding it.
    """
    with _REGISTRY_LOCK:
        return any(session.claimed for session in _SESSIONS.values())


def claim_job_id() -> str:
    """The broker job id of the claim this process holds, or ""."""
    with _REGISTRY_LOCK:
        for session in _SESSIONS.values():
            if session.claimed:
                return session.claim_job_id
    return ""


def describe_claim() -> str:
    """One line naming the claim this process holds, for a refusal."""
    with _REGISTRY_LOCK:
        for session in _SESSIONS.values():
            if session.claimed:
                return session.describe()
    return "a Resolve session"


def _register(session: Session) -> None:
    with _REGISTRY_LOCK:
        _SESSIONS[session.id] = session


def _unregister(session: Session) -> None:
    with _REGISTRY_LOCK:
        _SESSIONS.pop(session.id, None)


def _new_session(purpose: str, project: str, timeline: str,
                 owner: str) -> Session:
    return Session(
        id=uuid.uuid4().hex[:12],
        purpose=purpose,
        owner=owner or resolve_lock.default_owner(),
        project=project,
        timeline=timeline,
        started_at=time.time(),
    )


@contextmanager
def _claim(session: Session, *, description: str, timeout: float,
           qualification_project: str) -> Iterator[Optional[str]]:
    """The one broker claim, or nothing where no broker serves.

    A broker that refuses the claim is a contention refusal, not a
    reason to run the operation unclaimed: the operation's whole point
    is that no other lane's turn is granted between its calls.
    """
    from library.tools.resolved import client

    described = (description or session.purpose).strip() or session.purpose
    if len(described) > 80:
        described = described[:77].rstrip() + "..."
    try:
        client.ensure()
    except client.TestSpawnRefused:
        # Inside the test suite without the spawn opt-in: no broker may
        # be started here, so there is none to claim a turn from. The
        # instance lease is then the only fence, and the record says so.
        yield None
        return
    if not client.serving():
        yield None
        return
    params = {
        "purpose": session.purpose,
        "exclusive": True,
        "description": described,
        "progress": "",
        "interactive": False,
        "project": session.project or qualification_project,
        "timeline": session.timeline,
        "locality": ("timeline" if session.timeline else
                     "project" if (session.project or qualification_project)
                     else "none"),
    }
    try:
        with client.grant(
                params,
                owner=session.owner,
                qualification=bool(qualification_project),
                wait=timeout) as job_id:
            yield job_id
    except client.BrokerError as refused:
        raise resolve_lock.ResolveBusy(
            f"ren-resolved did not grant the session for "
            f"{session.purpose!r}: {refused}",
            "the broker schedules the one Resolve instance",
            "wait for the running jobs (`ren resolved list`), then re-run "
            "the same command") from refused
    except ConnectionError as gone:
        raise resolve_lock.ResolveBusy(
            f"ren-resolved stopped serving while the session for "
            f"{session.purpose!r} was being taken: {gone}",
            "the broker schedules the one Resolve instance",
            "start the broker (`ren resolved ensure`) and re-run the "
            "same command") from gone


@contextmanager
def resolve_session(purpose: str, *, project: str = "", timeline: str = "",
                    description: str = "", owner: str = "",
                    timeout: Optional[float] = None,
                    qualification_project: str = "") -> Iterator[Session]:
    """Hold ONE exclusive broker session across a whole operation.

    `purpose` names the operation in the broker's own records ("reels
    build"), so a lane waiting on the session can see what it is
    waiting for. `project` and `timeline` name what the session is
    about, which is what the broker's locality ordering reads.

    The session is released at the end of the block, and a client that
    dies inside it releases the claim by closing its socket.
    """
    stack = _stack()
    if stack:
        # A session inside a session: the enclosing claim IS the claim.
        # Asking for a second one would queue behind our own claim.
        yield stack[-1]
        return
    session = _new_session(purpose, project, timeline, owner)
    if claim_held() or resolve_lock.held():
        # The process already holds the claim - another thread's session,
        # or an enclosing lease - and with it the broker's turn. A second
        # claim would wait for the very turn that is holding it.
        joined = replace(session, joined=True)
        _register(joined)
        stack.append(joined)
        try:
            yield joined
        finally:
            stack.pop()
            _unregister(joined)
        return
    wait = (resolve_lock.default_timeout() if timeout is None
            else float(timeout))
    with _claim(session, description=description, timeout=wait,
                qualification_project=qualification_project) as job_id:
        claimed = replace(session, claimed=job_id is not None,
                          claim_job_id=job_id or "")
        _register(claimed)
        stack.append(claimed)
        try:
            yield claimed
        finally:
            stack.pop()
            _unregister(claimed)


def session_scope(purpose: str, *, description: str = "",
                  project: str = "", timeline: str = "",
                  owner: str = "",
                  timeout: Optional[float] = None) -> "functools":
    """Decorator form: the whole function body runs in ONE session.

    The decorator form of `resolve_session`, for an entry point that IS
    the multi-call operation - the reel build, the delivery. The
    function keeps its own name and signature, so the capability
    registry, the audit and every caller see the same entry point they
    saw before.
    """
    def wrap(function):
        @functools.wraps(function)
        def guarded(*args, **kwargs):
            with resolve_session(purpose, description=description,
                                 project=project, timeline=timeline,
                                 owner=owner, timeout=timeout):
                return function(*args, **kwargs)
        guarded.__resolve_session__ = purpose
        return guarded
    return wrap
