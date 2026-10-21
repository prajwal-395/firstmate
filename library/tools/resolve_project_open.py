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


class ProjectOpenRefused(RuntimeError):
    """The named project could not be opened. Says why."""


def _report(message: str) -> None:
    """Say a restore did not happen, where a reader will see it."""
    try:
        print(f"ren-resolved: {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - a report never stops the work
        pass


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
            f"opening {name!r} needs the EXCLUSIVE instance lease: the "
            f"open project is global session state, and a shared lease "
            f"lets another writer move it while this block runs")
    manager = resolve.GetProjectManager()
    if manager is None:
        raise ProjectOpenRefused(
            f"refusing to open {name!r}: GetProjectManager() returned None "
            f"- Resolve is mid-switch or not fully loaded; retry after it "
            f"settles instead of switching projects under it")
    current = manager.GetCurrentProject()
    current_name = (current.GetName() or "") if current else ""
    if current_name == name:
        yield current
        return
    found = qualification_project.capture_open_project(resolve)
    # The save protects the borrowed project's unsaved work from the
    # reload the restore does. A project Resolve cannot reopen by name
    # is never reloaded, so there is nothing to save either.
    if found.get("reopenable", True) and not manager.SaveProject():
        raise ProjectOpenRefused(
            f"could not save {found['project']!r}; not opening {name!r}")
    qualification_project.switch_project(manager, name)
    live = manager.GetCurrentProject()
    if live is None or (live.GetName() or "") != name:
        raise ProjectOpenRefused(
            f"{name!r} did not read back after the switch (Resolve has "
            f"{(live.GetName() or '') if live else 'no project'!r}); "
            f"reopen it by hand")
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
