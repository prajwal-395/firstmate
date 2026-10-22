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
from library.tools.ren_refusal import RenRefusal


class SessionProjectRefused(RenRefusal):
    """The project a session is for is not the one Resolve has open.

    A session holds the broker's turn, so no other lane can be granted
    a turn while it runs - and no other lane can move the cursor to its
    own project. What the session finds instead is a project left open
    by whoever ran before it: the session writes into a project it never
    opened, and the edit lands in the wrong place (measured 2026-10-21:
    four lanes failed on exactly this - a scratch project another lane
    had left open).

    So a session that names a project OPENS it, reads it back by exact
    name, and refuses by name if the open project is ever something
    else.
    """

    CODE = "REN_SESSION_PROJECT_MISMATCH"
    RECOVERY_CLASS = "needs_question"
    ASK_REASON = "insufficient_evidence"
    USER_MESSAGE = ("Resolve has a different project open than the one "
                    "this work is for, so I changed nothing. Open the "
                    "right project and run the step again.")

    def __init__(self, what: str, why: str = "", fix: str = ""):
        super().__init__(
            what,
            why or ("the open project is not the one this session is "
                   "for, so a write here would land in the wrong place"),
            fix or (f"open the project by hand and run the command again; "
                    f"the session refuses to write into {what}"))


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
    opened_project: str = ""
    """The project this session OPENED and verified, by exact name.

    Empty where the session names no project: a session with no target
    keeps whatever project is open, and says nothing about it.
    """

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
            "opened_project": self.opened_project,
            "held_seconds": round(self.held_for(), 3),
        }


#: The Resolve handle each session opened its project with, by session
#: id. Nothing is kept there on purpose: a handle taken at the start
#: goes stale the moment the cursor moves, and a stale handle is how a
#: write lands in a project nobody can name.
_SESSION_RESOLVE: dict = {}


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


def _session_handle(session: Session):
    """The Resolve handle this session opened its project with."""
    handle = _SESSION_RESOLVE.get(session.id)
    if handle is None:
        from library.tools.marker_feedback import connect_resolve
        handle = connect_resolve()
        _SESSION_RESOLVE[session.id] = handle
    return handle


def _open_project_name(resolve) -> str:
    """The name of the project Resolve has OPEN, or "" where it cannot say."""
    try:
        manager = resolve.GetProjectManager()
    except Exception:  # noqa: BLE001 - a dead handle answers nothing
        return ""
    if manager is None:
        return ""
    try:
        current = manager.GetCurrentProject()
    except Exception:  # noqa: BLE001 - a stale handle answers nothing
        return ""
    if not current:
        return ""
    try:
        return current.GetName() or ""
    except Exception:  # noqa: BLE001 - a stale handle answers nothing
        return ""


def open_project(session: Session, timeout: float) -> Session:
    """Open the project the session is for, and read it back by exact name.

    A session holds the broker's turn, so no other lane can be granted a
    turn while it runs - and no other lane can move the cursor to its
    own project. What the session finds instead is whatever project the
    last lane left open, and a write inside it lands in the wrong place
    (measured 2026-10-21: four lanes failed on exactly this).

    So the session opens ITS project, under its own exclusive lease, and
    reads the open project back by exact name. A project that is already
    open is not loaded again (`switch_project` counts the switch it
    avoided), so this costs nothing when the session's project is
    already there.

    A project FILED IN ANOTHER FOLDER is reached by that folder's path:
    Resolve resolves a project name inside the folder the Project
    Manager is showing, so the session navigates to the folder the
    project is filed in and loads it there. The folder path comes from
    the Resolve database on disk, which mirrors the Project Manager tree
    (measured 2026-10-22; `library/tools/resolve_project_folders.py`).
    A machine whose Project Manager cannot move refuses with the folder
    NAMED, which is the one thing the caller cannot work out for
    itself.

    A session that names no project opens nothing and verifies nothing:
    it is not about a project, and it says so.
    """
    name = (session.project or "").strip()
    if not name:
        return session
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import switch_project
    from library.tools.resolve_switch_guard import wait_for_settle

    with resolve_lock.resolve_lease(f"resolve session: open {name!r}",
                                    exclusive=True, timeout=timeout):
        wait_for_settle()
        resolve = connect_resolve()
        manager = resolve.GetProjectManager()
        if manager is None:
            raise SessionProjectRefused(
                f"{name!r} (Resolve's project manager is not available)")
        folder = _folder_path(manager, name)
        if folder:
            from library.tools import resolve_project_folders

            resolve_project_folders.open_project_by_folder(
                manager, name, folder)
        else:
            switch_project(manager, name)
        found = _open_project_name(resolve)
        if found != name:
            raise SessionProjectRefused(
                f"{name!r} (Resolve has {found or 'no project'!r})")
    return replace(session, opened_project=name)


def _folder_path(manager, name: str) -> tuple[str, ...]:
    """The Project Manager folder a project is filed in, or the root.

    The current folder's listing is folder-scoped, so a project filed in
    another folder reads as absent - and the folder it is filed in is
    readable from the database on disk, which mirrors the Project
    Manager tree. A name that two folders hold is REFUSED with the
    folders named: Resolve resolves a name inside the current folder,
    so the two are different projects and the wrong one is not ours to
    open.
    """
    from library.tools import resolve_project_folders

    try:
        listed = manager.GetProjectListInCurrentFolder() or []
    except Exception:  # noqa: BLE001 - a manager that cannot list is no evidence
        listed = []
    if name in listed:
        return ()
    found = resolve_project_folders.database_folder_paths(manager, name)
    if len(found) > 1:
        folders = ", ".join(
            resolve_project_folders.folder_label(path) for path in found)
        raise SessionProjectRefused(
            what=f"{name!r} is filed in more than one Project Manager folder",
            why=(f"{folders} - Resolve resolves a name inside the current "
                 "folder, so opening the wrong one would put this "
                 "session's writes in somebody else's project"),
            fix=("move the project to one folder, or name the folder this "
                 "session should open it from"))
    return found[0] if found else ()


def adopt_project(session: Session, name: str) -> Session:
    """The session's project, learned from a section that names one.

    The session is about a project the moment a section inside it names
    one: that IS the project the session is for. So the session opens
    it, reads it back by exact name, and refuses by name if it is not
    there - and every later section inside it verifies the name before
    it writes.

    A session that already opened a project keeps it: the first section
    that named one is the session's project, and a later section that
    names another one is that section's business, not the session's.
    """
    name = (name or "").strip()
    if not name or session.opened_project:
        return session
    opened = open_project(replace(session, project=name),
                          timeout=resolve_lock.default_timeout())
    _register(opened)
    stack = _stack()
    if stack and stack[-1].id == session.id:
        stack[-1] = opened
    return opened


def verify_project(session: Session) -> str:
    """The open project must still be the session's project, by exact name.

    Called before every write inside a session: a session that names a
    project refuses by name if the open project is anything else, rather
    than writing into it.

    The read is FRESH - a handle taken at the session's start goes stale
    the moment the cursor moves (measured 2026-10-21: "Resolve returned
    no timeline count"), and a stale handle answers nothing, which is
    not evidence of a change but is exactly the state a write must not
    happen in. So the name is read through a new handle, and a project
    that cannot be named at all refuses too.
    """
    expected = (session.opened_project or "").strip()
    if not expected:
        return ""
    from library.tools.marker_feedback import connect_resolve

    found = _open_project_name(connect_resolve())
    if found != expected:
        raise SessionProjectRefused(
            f"{expected!r} (Resolve has {found or 'no project'!r})")
    return found


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

    A session that names a project OPENS it when the session starts and
    reads it back by exact name (`open_project`), and every write inside
    it verifies the open project by exact name (`verify_project`). A
    session that names no project does neither.

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
        # The session is REGISTERED before its project is opened: the
        # open takes a lease, and a lease inside a session is served by
        # the session's own claim - asking for a second turn would wait
        # for the turn that is waiting for it.
        _register(claimed)
        stack.append(claimed)
        opened = claimed
        try:
            opened = open_project(claimed, wait)
            if opened is not claimed:
                _unregister(claimed)
                _register(opened)
                stack[-1] = opened
            yield opened
        finally:
            stack.pop()
            _unregister(opened)
            _SESSION_RESOLVE.pop(opened.id, None)


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
