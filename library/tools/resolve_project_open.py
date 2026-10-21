"""The project a write names, OPEN under the lease that write holds.

THE OPEN PROJECT IS GLOBAL SESSION STATE. One Resolve instance has one
open project, and every lane's job sees it: a write that names a
project has to make that project the open one, or it writes into
whatever project the last lane left open. That is what the round-2
measurement found (ten live write attempts, every one refused, seven of
them because another lane had opened its own project first - once the
captain's own `Podcast (field test)`).

Two things make the fix hard, and this module is the answer to both.

**THE OPEN MUST HAPPEN UNDER THE LEASE THAT COVERS THE WRITE.** Opening
a project in its own job leaves a window between the open and the
write, and every other lane's job lands in that window. So the open
happens inside the lease the write already holds: the lease is
exclusive, no other writer can move the cursor while it is held, and
the project is opened and read back before the write begins.

**THE BORROWED PROJECT IS PUT BACK.** The open project is not the
engine's to keep: it is the captain's, or another lane's. So it is
borrowed - its exact name, its Project Manager folder, its timeline and
its page - the named project is opened by EXACT name, and the borrowed
project is restored, with a read-back, when the block ends.

`qualification_project.borrowed()` is this mechanism for the
qualification project. This is the general one, and it is what
`resolve_lock.resolve_lease` uses when the lease names a project.

A RESTORE THAT FAILS IS REPORTED, NEVER RAISED. A write that landed
must not be reported as failed because the machine could not put a
project back (2026-10-20: every live qualification run on the machine
failed on the restore and took its own evidence with it).

`tests/unit/resolve/test_resolve_project_open.py`.
"""

from __future__ import annotations

import contextlib
import sys

from library.tools.ren_refusal import RenRefusal


class ProjectOpenRefused(RenRefusal):
    """The named project could not be opened. Says why, and what to do.

    A REFUSAL, not a crash. The open project is global session state,
    so refusing to open one is the engine protecting a write - and the
    person waiting for that write is owed the refusal's own words
    (what, why, fix), not a Python traceback.

    Measured 2026-10-21 on this machine: a touch that waited 77 minutes
    for the one Resolve was finally granted the lease, and then refused
    to open its project - and reported `REN_UNEXPECTED` with a
    traceback, because this family was a plain `RuntimeError` while
    every refusal beside it (`TouchupRefused`, `ResolveBusy`) is a
    `RenRefusal`. The write was refused either way; the operator could
    not tell a refusal from a crash.
    """

    USER_MESSAGE = ("I could not open the project this change needs, so "
                    "nothing changed. Resolve is busy with another "
                    "session, or the project did not open. Try again once "
                    "the other work finishes.")

    BLOCKER = "resource_contention"
    """Resolve's session is not free to be moved: another lane holds it,
    or the project did not open where it was asked to."""


def _report(message: str) -> None:
    """Say a restore did not happen, where a reader will see it."""
    try:
        print(f"ren-resolved: {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - a report never stops the work
        pass


def ren_owned(name: str) -> bool:
    """Whether a project name belongs to REN's own namespace.

    Ren's scratch projects and the qualification project are Ren's own:
    they are disposable and rebuildable (`ren qualification ensure`
    recreates what is missing in the qualification project, and a
    scratch project is created by the run that uses it).  A CAPTAIN's
    project is not, and a borrow of one is held to the strict rule.
    """
    text = str(name or "").strip()
    if not text:
        return False
    return text in {"Ren Qualification", "Resolve Qualification"} \
        or text.startswith("Ren Scratch - ")


def save_outcome(manager) -> tuple[str, str]:
    """What the save of the borrowed project actually did.

    The bridge answers None for a boolean on this Resolve - measured for
    `GetIsProjectDirty` on 2026-10-13, and measured again for
    `SaveProject()` on 2026-10-21, when every borrow on the machine
    refused with "Resolve would not save it" while the save call had
    answered NOTHING.  A None answer is UNKNOWN, not a proven refusal:
    reading it as a refusal refuses every borrow, forever, on a machine
    where the answer is silence.

    Returns `(outcome, why)` where outcome is `saved`, `refused` (the
    call said no) or `unknown` (the call said nothing).
    """
    try:
        result = manager.SaveProject()
    except Exception as exc:  # noqa: BLE001 - a raised save is a refusal
        return "refused", f"the save raised {type(exc).__name__}: {exc}"
    if result is True:
        return "saved", ""
    if result is False:
        return "refused", "Resolve refused the save"
    return "unknown", ("the bridge answered None for the save - the "
                       "measured silence of a boolean on this Resolve")


@contextlib.contextmanager
def opened(resolve, name: str):
    """The project `name` OPEN, under the lease the caller holds.

    Yields the open project - the one Resolve has open, read back by
    exact name. A project that is ALREADY open is not loaded again and
    nothing is restored: the block leaves the machine as it found it.

    The lease must be EXCLUSIVE. The open project is global session
    state, and a shared lease lets another writer move it in the middle
    of the block - which is the race this module exists to close.
    """
    from library.tools import qualification_project
    from library.tools.resolve_lock import exclusive_held

    name = (name or "").strip()
    if not name:
        yield None
        return
    if not exclusive_held():
        raise ProjectOpenRefused(
            what=f"opening {name!r} needs the EXCLUSIVE instance lease: "
                 f"the open project is global session state, and a shared "
                 f"lease lets another writer move it while this block runs",
            why="the instance lease was not held exclusively, so the open "
                "project is not this write's to move",
            fix="run the change under the exclusive instance lease - a "
                "write that touches Resolve takes it - then re-run the "
                "same command")
    manager = resolve.GetProjectManager()
    if manager is None:
        raise ProjectOpenRefused(
            what=f"refusing to open {name!r}: GetProjectManager() returned "
                 f"None - Resolve is mid-switch or not fully loaded",
            why="Resolve's Project Manager was not available to open the "
                "project with",
            fix="retry after Resolve settles - the machine is switching "
                "projects - then re-run the same command")
    current = manager.GetCurrentProject()
    current_name = (current.GetName() or "") if current else ""
    if current_name == name:
        yield current
        return
    found = qualification_project.capture_open_project(resolve)
    # The save protects the borrowed project's unsaved work from the
    # reload the restore does. A project Resolve cannot reopen by name
    # is never reloaded, so there is nothing to save either.
    if found.get("reopenable", True):
        outcome, why = save_outcome(manager)
        if outcome == "refused" or (
                outcome == "unknown" and not ren_owned(found.get("project"))):
            raise ProjectOpenRefused(
                what=f"could not save {found['project']!r}; not opening "
                     f"{name!r}",
                why=f"the borrowed project could not be saved ({why}), and "
                    "reopening it would lose whatever it holds that Resolve "
                    "has not written",
                fix="wait for the session holding that project to finish, "
                    "then re-run the same command")
    qualification_project.switch_project(manager, name)
    live = manager.GetCurrentProject()
    if live is None or (live.GetName() or "") != name:
        raise ProjectOpenRefused(
            what=f"{name!r} did not read back after the switch (Resolve "
                 f"has {(live.GetName() or '') if live else 'no project'!r})",
            why="the project did not open, so a write would land in "
                "whatever project is open instead",
            fix=f"reopen {name!r} in Resolve by hand, then re-run the same "
                "command")
    try:
        yield live
    finally:
        _restore(resolve, found, name)


def _restore(resolve, found: dict, opened_name: str) -> None:
    """Put the borrowed project back. Never raises."""
    from library.tools import qualification_project

    try:
        qualification_project.restore(resolve, found)
    except Exception as exc:  # noqa: BLE001 - the write already happened
        _report(
            f"the project borrowed before {opened_name!r} could not be put "
            f"back ({exc}); {found.get('project')!r} is no longer open - "
            f"reopen it by hand")
