"""Where a Resolve project is filed, and the route that reaches it.

A Resolve project lives in a Project Manager FOLDER, and the folder is
part of its address: Resolve resolves a project NAME inside the folder
the Project Manager is showing, so two projects can answer to one name
and only the one in the current folder is reachable by that name.

Ren's scratch projects are filed wherever the Project Manager was when
they were created, and the lane that opens one is usually not the lane
that created it. So a project's address is a FOLDER PATH, and this
module owns it: where the project is filed, how to reach it, and what
to say when this machine cannot get there.

Measured live on 2026-10-22 on the machine's own broker (Resolve
21.1.0.14, the `poetic` Disk database):

- The Project Manager's folder cursor could not be moved through the
  API. `GotoRootFolder`, `OpenFolder` and `GotoParentFolder` answered
  `None` and left the cursor where it was: `GetCurrentFolder()` kept
  answering `None` and the folder listing kept answering the ROOT's
  folders - right after the navigation, and again after settles of
  0.5s, 1s, 2s and 3s, and again on the media page. The same reading
  came back from a lane's own navigation (its refusal: "Resolve did
  not read back project folder 'trials'").
- `LoadProject` does not reach a project filed in another folder: it
  answered `None` and the open project read back as the one that was
  already open. A path was refused the same way - the folder path
  ("Lucie Content/Social Media/Podcast/<name>"), the absolute path of
  the project's database, and the folder path with backslashes.
- The DISK database mirrors the Project Manager tree exactly: the
  projects and the folders the API listed at the root were the entries
  the database holds at the root, one for one. So the folder path of a
  project is READABLE from the database on disk, without opening it.
  `library/tools/execution/prune_orphans.py` already builds that path
  from the database root Resolve itself reports (`GetCurrentDatabase`),
  and `docs/FIELD_TEST_PODCAST_FINDINGS.md` found a project the same
  way - "enumerating the database on disk rather than by navigating the
  captain's project manager".

So the route this module owns is: the folder path is RECORDED (the
caller's ledger) or DISCOVERED (the database), the Project Manager is
navigated to it, the project is loaded by its exact name, and the OPEN
project is read back by that exact name. A machine whose Project
Manager cannot move says so in a typed refusal that NAMES the folder -
which is the whole difference between "the project is not there" and
"the project is filed in 'Lucie Content/Social Media/Podcast' and this
machine cannot reach that folder".

The DISCOVERY reads the DATABASE DIRECTORY, never the database file: a
project's folder is a directory under the database's project tree, and
the rule that a `Project.db` is copied before it is read
(AGENTS.md section 5) is about reading one.
"""

from __future__ import annotations

import os
from pathlib import Path

from library.tools.ren_refusal import RenRefusal

PROJECT_DB_NAME = "Project.db"
"""The file that marks a directory as a Resolve project on disk."""

PROJECTS_SUBDIR = ("Resolve Projects", "Users", "guest", "Projects")
"""Where the Project Manager's tree lives inside a Disk database."""


class ProjectFolderRefused(RenRefusal):
    """The project is filed in a folder this machine cannot reach.

    A REFUSAL, not a failure: nothing was opened, nothing was written,
    and the receipt says which folder the project is filed in - so the
    reader knows the project EXISTS and that the machine's Project
    Manager is the thing that cannot get there.

    The blocker is `unsupported_operation`: the engine has no route that
    reaches that folder on this Resolve build, so the caller has to
    open the project in Resolve's Project Manager (or move the machine
    to a folder it can reach) before the work can run.
    """

    CODE = "REN_RESOLVE_PROJECT_FOLDER"
    BLOCKER = "unsupported_operation"
    USER_MESSAGE = ("I could not open that project because Resolve's "
                    "Project Manager cannot reach the folder it is filed "
                    "in, so nothing changed. The folder is named below.")

    def __init__(self, what: str, why: str = "", fix: str = ""):
        super().__init__(
            what,
            why or ("Resolve's Project Manager did not move to the "
                   "folder this project is filed in, so the project "
                   "cannot be reached by name"),
            fix or ("open the project in Resolve's Project Manager by "
                   "hand, or create the scratch project in a folder this "
                   "machine's Project Manager can reach, then re-run the "
                   "same command"))


def database(manager) -> dict | None:
    """The Resolve database the Project Manager is showing.

    Read through what the call RETURNED: a bridge that answers nothing
    is read as "no database", never as a database.

    `None` means the manager has no such call at all - a double, not a
    Resolve - and nothing is read from the machine's disk for it.
    """
    try:
        info = manager.GetCurrentDatabase() or {}
    except AttributeError:
        return None
    except Exception:  # noqa: BLE001 - an unanswerable manager is no evidence
        return {}
    return dict(info) if isinstance(info, dict) else {}


DATABASE_LIST_NAME = ("Library/Preferences/Blackmagic Design/"
                      "DaVinci Resolve/dblist.conf")
"""Where the machine lists its Resolve databases.

Colon-delimited text, NOT JSON - one database per line,
`<name>:<path>:*:::DISK`, e.g.
`poetic:<home>/Pictures/Davinci:*:::DISK`, where `<home>` is the
user's home directory - the machine's own, read here through
`Path.home()` and never written down by a checkout.
"""


def machine_databases() -> list[tuple[str, str]]:
    """Every Resolve database the machine lists, by name and root.

    A machine may have several, and the first line is not necessarily
    the open one - so a caller that wants ONE of them names it.
    """
    conf = Path.home() / DATABASE_LIST_NAME
    try:
        text = conf.read_text(encoding="utf-8")
    except OSError:
        return []
    listed = []
    for line in text.splitlines():
        fields = line.strip().split(":")
        if len(fields) < 2 or not fields[1]:
            continue
        listed.append((fields[0], fields[1]))
    return listed


def machine_disk_databases() -> list[tuple[str, str]]:
    """The machine's DISK databases, by name and root.

    A Disk database has its project tree on this machine's disk; a
    Project Server database has none, so there is no folder path to
    read out of it.
    """
    disks = []
    conf = Path.home() / DATABASE_LIST_NAME
    try:
        text = conf.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        fields = line.strip().split(":")
        if len(fields) < 2 or not fields[1]:
            continue
        if fields[-1] == "DISK":
            disks.append((fields[0], fields[1]))
    return disks


def database_root(manager) -> str:
    """The root of the Disk database Resolve reports, or "".

    A Project Server database has no project tree on this machine's
    disk, so there is no folder path to read and none to record.

    Resolve's own answer is the first source. Measured live on
    2026-10-22, `GetCurrentDatabase()` answered NOTHING on this build,
    so the machine's own database list is the second: the ONE disk
    database it has. A machine with several has no way to say which one
    the Project Manager is showing, and none is guessed at.
    """
    info = database(manager)
    if info is None:
        # Not a Resolve Project Manager: there is no machine database
        # to read for it.
        return ""
    if info and (info.get("DbType") or "") != "Disk":
        # A Project Server database: no project tree on this machine's
        # disk, so there is no folder path to read.
        return ""
    root = info.get("DbPath") or ""
    if root:
        return str(root)
    name = info.get("DbName") or ""
    if name:
        for listed_name, listed_root in machine_databases():
            if listed_name == name:
                return listed_root
        return ""
    disks = machine_disk_databases()
    return disks[0][1] if len(disks) == 1 else ""


def projects_root(manager) -> Path | None:
    """The directory the Project Manager's tree lives in, or None."""
    root = database_root(manager)
    if not root:
        return None
    return Path(root).joinpath(*PROJECTS_SUBDIR)


def database_folder_paths(manager, name: str) -> list[tuple[str, ...]]:
    """Every Project Manager folder path that holds a project called `name`.

    The database on disk mirrors the Project Manager tree, so the folder
    a project is filed in is a directory under the database's project
    tree. The DIRECTORY is read; the `Project.db` inside it is never
    opened (AGENTS.md section 5: a project database is copied before it
    is read, and this reads nothing at all).

    Sorted, so the answer does not depend on the order the filesystem
    hands the entries back in. More than one path means two projects
    answer to one name - the caller refuses that with the folders named
    rather than picking one.
    """
    base = projects_root(manager)
    if base is None or not base.is_dir() or not name:
        return []
    found: list[tuple[str, ...]] = []
    for directory, subdirectories, files in os.walk(base):
        subdirectories.sort()
        if PROJECT_DB_NAME not in files:
            continue
        project = Path(directory).name
        if project != name:
            continue
        relative = Path(directory).relative_to(base)
        found.append(tuple(relative.parts[:-1]))
    return sorted(found)


def folder_label(path) -> str:
    """The folder path as one label, for a message that names it."""
    parts = [str(part) for part in (path or ()) if str(part)]
    return "/".join(parts) if parts else "(the Project Manager root)"


def navigate_to_folder(manager, path) -> None:
    """Move the Project Manager to `path`, and read the move back.

    The route is Resolve's own: `GotoRootFolder`, then `OpenFolder` for
    each segment. The read-back is `GetCurrentFolder()`, which answers
    the LEAF folder's name - so every segment is verified, not just the
    last.

    A machine whose Project Manager does not move refuses HERE, with
    the folder named: the refusal says which folder the project is in,
    which is the one thing the caller cannot work out for itself.
    """
    segments = [str(segment) for segment in (path or ()) if str(segment)]
    if manager.GotoRootFolder() is False:
        raise ProjectFolderRefused(
            what=(f"Resolve's Project Manager would not move to the "
                  f"Project Manager root, so it cannot reach "
                  f"{folder_label(path)!r}"),
            why=("Resolve refused to navigate to the Project Manager root, "
                 "so the folder the project is filed in was never "
                 "entered"),
            fix=("open the project in Resolve's Project Manager by hand, "
                 "then re-run the same command"))
    for segment in segments:
        if not manager.OpenFolder(segment):
            raise ProjectFolderRefused(
                what=(f"Resolve's Project Manager would not open the "
                      f"folder {segment!r} on the way to "
                      f"{folder_label(path)!r}"),
                why=(f"OpenFolder({segment!r}) was refused, so the folder "
                     "the project is filed in was never entered"),
                fix=("open the project in Resolve's Project Manager by "
                     "hand, then re-run the same command"))
        if manager.GetCurrentFolder() != segment:
            raise ProjectFolderRefused(
                what=(f"Resolve's Project Manager did not read back the "
                      f"folder {segment!r} on the way to "
                      f"{folder_label(path)!r}"),
                why=(f"after OpenFolder({segment!r}) the Project Manager "
                     f"answered {manager.GetCurrentFolder()!r}, so the "
                     "folder the project is filed in was never entered"),
                fix=("open the project in Resolve's Project Manager by "
                     "hand, then re-run the same command"))


def open_project_by_folder(manager, name: str, path):
    """Open the project `name` filed in `path`, and read it back.

    The navigation happens INSIDE the switch, after the current project
    is read: reading the current project is measured to put the Project
    Manager back to the root, so a navigation done before that read is
    undone by it.

    The read-back is the OPEN PROJECT's exact name - the one reading on
    this Resolve that says which project is open. A project that does
    not read back refuses with the folder named.
    """
    from library.tools.qualification_project import (
        QualificationError,
        switch_project,
    )

    try:
        project = switch_project(manager, name, folder=path)
    except QualificationError as exc:
        raise ProjectFolderRefused(
            what=(f"{name!r} did not open from the folder it is filed in "
                  f"({folder_label(path)})"),
            why=(f"Resolve refused the switch: {exc}"),
            fix=("open the project in Resolve's Project Manager by hand, "
                 "then re-run the same command")) from exc
    if project is None or (project.GetName() or "") != name:
        opened = (project.GetName() if project else "no project") or ""
        raise ProjectFolderRefused(
            what=(f"{name!r} did not open from the folder it is filed "
                  f"in ({folder_label(path)})"),
            why=(f"Resolve opened {opened!r} instead, so the project in "
                 f"{folder_label(path)} was not the one that opened"),
            fix=("open the project in Resolve's Project Manager by hand, "
                 "then re-run the same command"))
    return project
