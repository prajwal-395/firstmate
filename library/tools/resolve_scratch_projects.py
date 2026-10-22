"""Guarded lifecycle for Ren-owned Resolve scratch projects.

Scratch projects are deliberately namespaced so lifecycle operations can
never target a captain-owned project by accident. The current project is
saved first and restored after: dirty-state handling follows
`qualification_project.borrowed` - save (which the captain's rule allows
a borrow-shaped flow), never discard. A boolean dirty check is not used
because the scripting bridge answers `GetIsProjectDirty` with None
(measured 2026-10-13, see `timeline_ingest`), so any such check would
refuse every project on this Resolve, forever. A failed save refuses
before any switch, exactly like `borrowed`.

One machine has ONE Resolve and one current project, and it can have
SEVERAL writers: five lanes that each need their own Ren-owned scratch
project. The route back from a scratch project is therefore recorded per
SCRATCH PROJECT - one record per scratch project, in one machine-wide
ledger - so opening a scratch project never has to clear anybody else's
record, and a session nested inside another one unwinds one link at a
time.

The scratch origin is also a SLOT the machine can only fill once, so an
open that finds it held by a LIVE writer QUEUES for it the way the
instance lease queues, with a bounded timeout that names the holder -
it does not refuse on the first sight. The queue runs BEFORE the
Resolve lease, because the holder has to be able to take that lease to
restore and release the origin.

A session whose writer has exited is taken back by the next writer: the
recorded origin is restored when it is Ren's own, and a captain project
is never switched onto because another writer decided to - that origin
is NAMED and left where it is.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from library.tools import artifact_lifecycle, resolve_switch_guard
from library.tools.qualification_project import QualificationError, switch_project
from library.tools.resolve_lock import assert_current_timeline

SCRATCH_PREFIX = "Ren Scratch - "

SCRATCH_BACKUP_RETENTION = 5
"""How many exports of ONE scratch project are kept.

A scratch project is deleted, and the `.drp` written just before the
deletion is how it comes back. Without a bound that export is a
permanent addition: every scratch project the machine ever retired left
one behind, and nothing ever collected them. The count is the bound, and
the NEWEST exports are the ones kept - a restore wants the most recent
shape of the project, not its first.

This is the scratch owner's own policy, and it is deliberately separate
from the project purge's rule that a `.drt`, `.drp` or `.otio` is never
purged in any class: that rule protects a project's RECOVERY archive
from a project cleanup, and this is a scratch project that no longer
exists at all. Only files this module wrote are ever named, by the exact
name it writes.
"""


class ScratchProjectError(RuntimeError):
    pass


class ScratchOriginHeld(ScratchProjectError):
    """The machine's scratch-origin slot is held by a live writer.

    A SLOT, not a fault: the writer that holds it is inside Resolve with
    its own session, and it needs the Resolve turn to restore the origin
    and release the slot. So the WAIT belongs to the caller, before the
    turn is taken - never inside one, where it holds the very turn the
    holder needs (measured 2026-10-21: three `project scratch open`
    jobs held the machine's turn for the whole 900 s lease timeout,
    each ending in "the scratch origin is held by", and every other
    lane waited behind them for fifteen minutes).

    The refusal is its own class so the caller can WAIT for the slot
    and try again, instead of reading the message and giving up.
    """


#: The exit code a scratch lifecycle command returns when the slot is
#: held. It is distinct from a failure so the caller can tell "the
#: machine is busy with somebody else's scratch session" from "this
#: command could not do its work".
SLOT_HELD_EXIT_CODE = 75


ORIGIN_LEDGER_FORMAT = "ren-resolve-scratch-origins/2"
"""The format of the scratch-origin ledger.

The ledger holds ONE record per scratch project, not one record per
machine. A record is the route back from ONE scratch project, so a
writer that needs its own scratch project never has to clear another
writer's record to get it.

A file written by the earlier single-receipt format is read as a ledger
of ONE record: a receipt left on disk by an older writer stays readable
instead of becoming a refusal.
"""


def _lifecycle_log_path() -> Path:
    """Use one machine-wide event stream for projects with no Ren folder."""
    from library.tools.resolve_lock import instance_lock_dir

    return instance_lock_dir() / "event_log.jsonl"


def _record_created_project(project, timeline=None, *, job_id="") -> list[str]:
    lifecycle = artifact_lifecycle
    project_id = str(project.GetUniqueId() or "")
    if not project_id:
        raise ScratchProjectError(
            "Resolve did not return a stable project identity; ownership was not recorded")
    owner_job = job_id or uuid.uuid4().hex
    run_id = owner_job
    project_identity = {"object_type": "project",
                        "project_unique_id": project_id,
                        "object_unique_id": project_id,
                        "project_name": project.GetName()}
    artifact_ids = []
    project_artifact_id = lifecycle.stable_artifact_id(
        project_id, run_id, owner_job, "resolve_scratch_project", project_id)
    lifecycle.register_resolve_artifact(
        _lifecycle_log_path(), artifact_id=project_artifact_id,
        kind="resolve_scratch_project", project_id=project_id,
        run_id=run_id, job_id=owner_job, identity=project_identity,
        retention_class="retained", state=lifecycle.RETAINED)
    artifact_ids.append(project_artifact_id)
    if timeline is not None:
        timeline_id = str(timeline.GetUniqueId() or "")
        if not timeline_id:
            raise ScratchProjectError(
                "Resolve did not return a stable timeline identity; ownership was not recorded")
        timeline_identity = {"object_type": "timeline",
                             "project_unique_id": project_id,
                             "object_unique_id": timeline_id,
                             "project_name": project.GetName(),
                             "object_name": timeline.GetName()}
        timeline_artifact_id = lifecycle.stable_artifact_id(
            project_id, run_id, owner_job, "resolve_scratch_timeline", timeline_id)
        lifecycle.register_resolve_artifact(
            _lifecycle_log_path(), artifact_id=timeline_artifact_id,
            kind="resolve_scratch_timeline", project_id=project_id,
            run_id=run_id, job_id=owner_job, identity=timeline_identity,
            retention_class="retained", state=lifecycle.RETAINED)
        artifact_ids.append(timeline_artifact_id)
    return artifact_ids


def _name(name: str) -> str:
    name = str(name or "").strip()
    if not name.startswith(SCRATCH_PREFIX) or not name[len(SCRATCH_PREFIX):].strip():
        raise ScratchProjectError(
            f"scratch project names must start with {SCRATCH_PREFIX!r} and include a suffix")
    return name


def scratch_project_name(name: str) -> str:
    """The validated name of a Ren-owned scratch project.

    The public way in: a caller that creates a scratch project names it
    here first, so a name that is not Ren's is refused before anything
    is created rather than half way through a lifecycle.
    """
    return _name(name)


def _settle_after_lifecycle_call() -> None:
    resolve_switch_guard.note_project_switch()
    resolve_switch_guard.wait_for_settle()


def _starting_state(resolve, manager, *, save: bool = True) -> dict:
    project = manager.GetCurrentProject()
    if project is None:
        raise ScratchProjectError("Resolve has no current project to restore")
    name = project.GetName()
    if save:
        from library.tools.resolve_project_open import save_outcome

        # An untitled project holds no work - it is what a relaunch of
        # Resolve leaves behind, and it cannot be saved because it has
        # no name. Refusing to leave it would leave EVERY scratch
        # operation stuck, with no way out that does not need a person
        # in Resolve's UI. An empty one is not borrowed work, so it is
        # left as it is.
        empty = (str(name) == "Untitled Project"
                 and not int(project.GetTimelineCount() or 0))
        outcome, why = save_outcome(manager)
        if outcome == "refused" and not empty:
            raise ScratchProjectError(
                f"cannot switch from {name!r}: Resolve would not save it "
                f"({why}); not borrowing unsavable work")
        if (outcome == "unknown" and not empty
                and not _ren_owned_origin(name)):
            raise ScratchProjectError(
                f"cannot switch from {name!r}: {why}, and a project that "
                "is not Ren's own is not borrowed on an unknown save")
    timeline = project.GetCurrentTimeline()
    return {
        "project": name,
        "timeline_id": timeline.GetUniqueId() if timeline else None,
        "timeline": timeline.GetName() if timeline else None,
        "page": resolve.GetCurrentPage(),
    }


def _origin_path() -> Path:
    """Where the scratch-origin ledger lives: one file, one record per project.

    MACHINE-WIDE, beside the instance lease: the ledger protects the one
    Resolve's current project, so two brokers driving that one Resolve
    have to see each other's sessions. A per-broker directory would let
    each broker believe the machine was free.
    """
    from library.tools.resolve_lock import instance_lock_dir
    return instance_lock_dir() / "resolve_axi_scratch_origin.json"


def _legacy_origin_path() -> Path:
    """Where the ledger used to live: beside the broker's own state."""
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / "resolve_axi_scratch_origin.json"


def _read_ledger() -> list[dict]:
    """Every recorded origin, oldest first.

    A ledger nobody can read is refused by name: it may be a
    half-written file, and guessing at its contents is how a scratch
    project gets left behind with no route back.
    """
    path = _origin_path()
    if not path.exists():
        legacy = _legacy_origin_path()
        if legacy != path and legacy.exists():
            path = legacy
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ScratchProjectError(
            "the recorded scratch-project origin could not be read; "
            "remove it by hand once you have checked what it says") from exc
    if not isinstance(document, dict):
        raise ScratchProjectError(
            "the recorded scratch-project origin is not a receipt; "
            "remove it by hand once you have checked what it says")
    records = document.get("records")
    if records is None:
        # The single-receipt format: one route back, written by one writer.
        records = [document]
    if (not isinstance(records, list)
            or not all(isinstance(record, dict) for record in records)):
        raise ScratchProjectError(
            "the recorded scratch-project origin is not a receipt; "
            "remove it by hand once you have checked what it says")
    return [dict(record) for record in records]


def _write_ledger(records: list[dict]) -> None:
    """Write the ledger whole: a record is never edited in place.

    An empty ledger is REMOVED, not left behind as an empty file: the
    file's presence is the signal that a route back is recorded, and a
    reader must not have to open it to learn that there is none.
    """
    path = _origin_path()
    if not records:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"format": ORIGIN_LEDGER_FORMAT, "records": records},
                   sort_keys=True, indent=2),
        encoding="utf-8")
    os.replace(temporary, path)


def _record_for(records: list[dict], scratch_project: str | None) -> dict | None:
    """The record of ONE scratch project: the route back from it."""
    for record in records:
        if str(record.get("scratch_project") or "") == str(scratch_project or ""):
            return record
    return None


def _record_for_position(records: list[dict], project: str | None) -> dict | None:
    """The record that says where THIS project belongs.

    The machine is normally inside the scratch project a record names -
    that is the session it is in. It is sometimes already back at the
    ORIGIN a record names, because Resolve reopened it after a restart;
    that record is then finished, and completing it is the restore.
    """
    record = _record_for(records, project)
    if record is not None:
        return record
    for candidate in reversed(records):
        if str(candidate.get("project") or "") == str(project or ""):
            return candidate
    return None


def _drop_record(records: list[dict], scratch_project: str | None) -> list[dict]:
    """Every record but the one for this scratch project."""
    return [record for record in records
            if str(record.get("scratch_project") or "") != str(scratch_project or "")]


def _forget_origin(records: list[dict], state: dict) -> list[dict]:
    """Drop the record that has just been restored, and write the ledger."""
    remaining = _drop_record(records, state.get("scratch_project"))
    _write_ledger(remaining)
    return remaining


def _write_origin(state: dict, records: list[dict] | None = None) -> None:
    """Record the route back from one scratch project.

    The owner is stamped on every write, so a record left behind by a
    process that exited can be told from one a live writer still holds.
    The stamp is the ONLY way to tell them apart: the ledger is
    machine-wide and outlives the process that wrote it. The process's
    START TIME is stamped with it, because a pid alone says nothing
    once the machine has reused the number - and a reused pid read as a
    live holder on 2026-10-20, which starved every other writer.

    The record replaces any record for the SAME scratch project and
    leaves every other record alone: another writer's route back is
    never cleared to make room for this one.
    """
    current = _read_ledger() if records is None else records
    remaining = _drop_record(current, state.get("scratch_project"))
    remaining.append({**state,
                      "owner_pid": os.getpid(),
                      "owner_machine_id": _machine_id(),
                      "owner_start": _process_start(os.getpid()),
                      "owner_host": socket.gethostname(),
                      "owner": _owner_name(),
                      "recorded_at": time.time()})
    _write_ledger(remaining)


def _owner_name() -> str:
    """A name for the process holding a receipt, for a human reading it."""
    from library.tools.resolve_lock import default_owner
    return default_owner()


def _held_by_us(record: dict) -> bool:
    """Whether THIS writer is the one that recorded this route back.

    The holder is the writer's own name: the lane in a fleet, the run
    owner, or the process when neither is set. Two writers with
    different names are two writers, and one writer's session is never
    taken back by another.
    """
    return str(record.get("owner") or "") == _owner_name()


def _process_start(pid: int) -> str:
    """The wall-clock start of `pid`, empty when the pid is gone.

    `ps -o lstart=` is stable across a boot and distinct across pid
    reuse: a row whose recorded start differs from the live pid's start
    is a different process wearing a recycled number. The same reading
    `library/tools/resource_scheduler.py` takes.
    """
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, encoding="utf-8", timeout=3,
            check=False).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


def _machine_id() -> str:
    """This machine's stable identity, or "" where it cannot be read.

    A HOSTNAME is not an identity: it changes (the short name "Mac"
    became "Mac.attlocal.net"), and a record that names the old one
    reads as ANOTHER MACHINE's - which is fail-closed to "alive", so
    the session it holds is never released (measured 2026-10-21: seven
    abandoned sessions held the scratch-origin slot on this machine,
    every one of them stamped with the hostname the machine had before
    it was renamed).
    """
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            continue
        if value:
            return value
    try:
        from library.tools.shared_environment import vep_home

        return (vep_home() / "machine-id").read_text(
            encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def _same_machine(state: dict) -> bool:
    """Whether the record names THIS machine.

    The machine id answers it exactly. A record with no machine id
    falls back to the hostname, and a hostname that is a prefix of this
    machine's name (or the other way round) is the SAME machine: the
    short name and the FQDN are one host.

    A record from ANOTHER machine is not this machine's, and the caller
    treats it as alive (fail-closed): "we could not prove the owner is
    gone" must never be read as "the owner is gone", because taking
    such a record would take the route back from a live writer's
    scratch project.
    """
    recorded = str(state.get("owner_machine_id") or "")
    if recorded:
        mine = _machine_id()
        return not mine or recorded == mine
    host = str(state.get("owner_host") or "")
    if not host:
        return True
    mine = socket.gethostname()
    if host == mine:
        return True
    return host == mine.split(".")[0] or mine.startswith(host + ".")


def _owner_alive(state: dict) -> bool:
    """Whether the process that recorded this origin may still be working.

    Fail-closed. A record that names no pid, or one recorded on another
    machine, counts as ALIVE: "we could not prove the owner is gone"
    must never be read as "the owner is gone", because taking such a
    record would take the route back from a live writer's scratch
    project.

    A record from THIS machine whose owner is gone counts as GONE, even
    when it names the machine by a hostname this machine has since
    changed (the dead owner's slot is released - measured 2026-10-21:
    seven abandoned sessions held the slot because every record was read
    as another machine's).

    A pid whose START no longer matches the recorded one counts as
    GONE: the number was reused, and the process wearing it now is not
    the writer that recorded the origin. Reading a reused pid as a live
    holder is what starved four other lanes on 2026-10-20.
    """
    try:
        pid = int(state.get("owner_pid") or 0)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if not _same_machine(state):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by somebody else
    except OSError:
        return True
    recorded_start = str(state.get("owner_start") or "")
    if recorded_start:
        live_start = _process_start(pid)
        if live_start and live_start != recorded_start:
            return False
    return True


def _instance_lease_is_ours_or_free() -> bool:
    """Whether the Resolve instance is free for THIS writer.

    The flock is the truth: an exclusive instance lease means no other
    writer is inside Resolve. The lease this process holds - or the one
    it inherited from an ancestor that holds it for us - is this
    writer's own.
    """
    from library.tools import resolve_lock

    status = resolve_lock.lease_status()
    active = status.active
    if active is None:
        return True
    if active.pid == os.getpid():
        return True
    inherited = resolve_lock.inherited_holder()
    return inherited is not None and inherited == active.pid


def _return_to_recorded_scratch(resolve, manager, state: dict,
                                reason: str) -> dict:
    """Keep Ren-owned scratch work reachable when its origin cannot return."""
    scratch_name = _name(state.get("scratch_project"))
    if not _listed(manager, scratch_name):
        raise ScratchProjectError(
            f"cannot recover scratch origin: recorded Ren-owned project "
            f"{scratch_name!r} is not available")
    project = manager.GetCurrentProject()
    if not project or project.GetName() != scratch_name:
        project = switch_project(manager, scratch_name)
    if not project or project.GetName() != scratch_name:
        raise ScratchProjectError(
            f"cannot recover scratch origin: could not reopen {scratch_name!r}")
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise ScratchProjectError(
            f"cannot recover scratch origin: {scratch_name!r} has no current timeline")
    page = state.get("page")
    if (page is not None and resolve.GetCurrentPage() != page
            and (not resolve.OpenPage(page)
                 or resolve.GetCurrentPage() != page)):
        raise ScratchProjectError(
            f"cannot recover scratch origin: could not return to page {page!r}")
    return {"project": scratch_name, "timeline": timeline.GetName(),
            "page": page, "verified": True, "timeline_restored": False,
            "reason": reason}


def _missing_recorded_timeline(error: ScratchProjectError) -> bool:
    message = str(error)
    return ("cannot find timeline" in message
            or "cannot recover timeline" in message)


def recover_stale_origin(resolve, manager, state: dict,
                         records: list[dict]) -> tuple[dict, list[dict]]:
    """Put the machine back on a record whose owner process has exited.

    The record is the only route back from a scratch project, so it is
    never cleared blindly: the machine goes back to the recorded origin
    and READS BACK there before the record goes. A recorded origin
    that is not Ren's own is refused BY NAME - a name the caller can
    act on - because an agent must not move the captain's project on
    its own judgement.

    Returns the restored state and the ledger without that record.
    """
    origin_name = str(state.get("project") or "")
    if not _ren_owned_origin(origin_name):
        raise ScratchProjectError(
            f"the recorded scratch-project origin {origin_name!r} is not "
            "Ren-owned; restore it by hand before opening another scratch "
            "project")
    current = manager.GetCurrentProject()
    # The open project may hold work the dead owner never saved.
    if (current is not None and current.GetName() != origin_name
            and not manager.SaveProject()):
        raise ScratchProjectError(
            "cannot recover the recorded scratch-project origin: "
            "Resolve would not save the open project")
    try:
        result = restore(resolve, manager, state)
    except QualificationError as exc:
        raise ScratchProjectError(
            f"cannot recover the recorded scratch-project origin "
            f"{origin_name!r}: {exc}") from exc
    except ScratchProjectError as exc:
        if not _missing_recorded_timeline(exc):
            raise
        # The ORIGIN is Ren's own, but the timeline it recorded is gone.
        # The project is still where the receipt wants the machine, so
        # the receipt completes with the reason rather than pretending
        # the timeline came back.
        try:
            project = switch_project(manager, origin_name)
        except QualificationError as switch_error:
            raise ScratchProjectError(
                f"cannot recover the recorded scratch-project origin "
                f"{origin_name!r}: {switch_error}") from exc
        if not project or project.GetName() != origin_name:
            raise ScratchProjectError(
                f"cannot recover the recorded scratch-project origin: "
                f"could not reopen {origin_name!r}") from exc
        page = state.get("page")
        if (page is not None and resolve.GetCurrentPage() != page
                and (not resolve.OpenPage(page)
                     or resolve.GetCurrentPage() != page)):
            raise ScratchProjectError(
                "cannot recover the recorded scratch-project origin: "
                f"could not return to page {page!r}") from exc
        result = {"project": origin_name,
                  "timeline": state.get("timeline"),
                  "page": page,
                  "verified": False,
                  "timeline_restored": False,
                  "reason": str(exc)}
    records = _forget_origin(records, state)
    return result, records


def restore_recorded(resolve, manager, *, timeline_drts=()) -> dict:
    """Restore the origin recorded for the OPEN scratch project.

    The record is the one for the project the machine is in: the
    session that put it there. Every writer keeps its own record, so
    one writer's session never decides another writer's restore, and a
    session nested inside another unwinds one link at a time.

    A DRT re-import creates a new Resolve timeline identity. For Ren-owned
    projects only, `restore` may recover the uniquely named timeline after
    verifying its identity changed; captain projects remain UID-only.
    """
    records = _read_ledger()
    if not records:
        raise ScratchProjectError("there is no recorded scratch-project origin to restore")
    current = manager.GetCurrentProject()
    if current is None:
        raise ScratchProjectError("Resolve has no current project to restore from")
    state = _record_for_position(records, current.GetName())
    if state is None:
        expected = ", ".join(repr(str(record.get("scratch_project") or ""))
                             for record in records)
        raise ScratchProjectError(
            f"cannot restore recorded origin: expected {expected} open, "
            f"found {current.GetName()!r}")
    imported = []
    if timeline_drts:
        origin_name = str(state.get("project") or "")
        if origin_name != "Ren Qualification":
            raise ScratchProjectError(
                "DRT recovery imports are restricted to the Ren Qualification "
                "origin project")
        if current.GetName() == state.get("scratch_project"):
            if not manager.SaveProject():
                raise ScratchProjectError(
                    "cannot restore: Resolve would not save the open scratch "
                    "project; not leaving unsavable work behind")
            current = switch_project(manager, origin_name)
            _settle_after_lifecycle_call()
        if not current or current.GetName() != origin_name:
            raise ScratchProjectError(
                f"DRT recovery imports require {origin_name!r} open; "
                f"found {current.GetName() if current else None!r}")
        imported = _import_qualification_drts(current, timeline_drts)
        if imported and not manager.SaveProject():
            raise ScratchProjectError(
                "Resolve did not save the restored qualification timelines")
    if current.GetName() != state.get("scratch_project"):
        if current.GetName() == state.get("project"):
            # Resolve reopened the ORIGIN's project - a broker restart
            # does exactly that - so the machine is already where the
            # record wanted it and the record has nothing left to
            # restore. `open_project` refuses while a record exists and
            # `restore_recorded` used to refuse unless the SCRATCH
            # project was open, so after a restart neither could move
            # and the record had to be removed by hand. The record is
            # completed here instead.
            ren_owned_origin = str(state.get("project", "")).startswith("Ren ")
            if (not timeline_drts and ren_owned_origin
                    and state.get("timeline_id") is None):
                result = _return_to_recorded_scratch(
                    resolve, manager, state,
                    "origin had no current timeline; reopened the recorded "
                    f"scratch project {state['scratch_project']!r}")
                _forget_origin(records, state)
                return {**result, "imported_timeline_drts": imported}
            try:
                result = restore(resolve, manager, state)
            except ScratchProjectError as exc:
                if not _missing_recorded_timeline(exc):
                    raise
                if str(state.get("project", "")).startswith("Ren "):
                    # A Ren-owned origin can be rebuilt independently of
                    # its old timeline ids (qualification fixtures do this
                    # during refresh). Keep scratch recovery independent of
                    # those fixtures: reopen the recorded scratch project,
                    # which is where the user's copied work lives.
                    scratch_name = _name(state.get("scratch_project"))
                    if not _listed(manager, scratch_name):
                        raise ScratchProjectError(
                            f"cannot recover scratch origin: recorded Ren-owned "
                            f"project {scratch_name!r} is not available") from exc
                    project = switch_project(manager, scratch_name)
                    if not project or project.GetName() != scratch_name:
                        raise ScratchProjectError(
                            f"cannot recover scratch origin: could not reopen "
                            f"{scratch_name!r}") from exc
                    timeline = project.GetCurrentTimeline()
                    page = state.get("page")
                    if (page is not None and resolve.GetCurrentPage() != page
                            and (not resolve.OpenPage(page)
                                 or resolve.GetCurrentPage() != page)):
                        raise ScratchProjectError(
                            f"cannot recover scratch origin: could not return "
                            f"to page {page!r}") from exc
                    result = {
                        "project": scratch_name,
                        "timeline": timeline.GetName() if timeline else None,
                        "page": page,
                        "verified": bool(timeline),
                        "timeline_restored": False,
                        "reason": (f"origin timeline is gone; reopened recorded "
                                   f"scratch project {scratch_name!r}"),
                    }
                    _forget_origin(records, state)
                    return result
                # The ORIGIN's project is open and its recorded timeline
                # is gone - the reel was rebuilt since. The machine is
                # back where the record wanted it, and a timeline that
                # no longer exists cannot be restored onto; saying so
                # and completing the record beats refusing to move.
                result = {"project": state.get("project"),
                          "timeline": state.get("timeline"),
                          "page": state.get("page"),
                          "verified": False,
                          "timeline_restored": False,
                          "reason": str(exc)}
            _forget_origin(records, state)
            return {**result, "imported_timeline_drts": imported}
        raise ScratchProjectError(
            f"cannot restore recorded origin: expected {state.get('scratch_project')!r} open, "
            f"found {current.GetName()!r}")
    if not manager.SaveProject():
        raise ScratchProjectError(
            "cannot restore: Resolve would not save the open scratch "
            "project; not leaving unsavable work behind")
    ren_owned_origin = str(state.get("project", "")).startswith("Ren ")
    if (not timeline_drts and ren_owned_origin
            and state.get("timeline_id") is None):
        result = _return_to_recorded_scratch(
            resolve, manager, state,
            "origin had no current timeline; kept the recorded scratch "
            f"project {state['scratch_project']} open")
        _forget_origin(records, state)
        return {**result, "imported_timeline_drts": imported}
    try:
        result = restore(resolve, manager, state)
    except ScratchProjectError as exc:
        if (not _missing_recorded_timeline(exc) or timeline_drts
                or not ren_owned_origin):
            raise
        result = _return_to_recorded_scratch(
            resolve, manager, state,
            "origin timeline is gone; reopened the recorded "
            f"scratch project {state['scratch_project']!r}")
    _forget_origin(records, state)
    return {**result, "imported_timeline_drts": imported}


def _import_qualification_drts(project, archives) -> list[dict]:
    """Import and measure one-timeline DRTs into Ren Qualification."""
    from library.tools import drt_reader, resolve_archive

    prepared = []
    names = set()
    for value in archives:
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() != ".drt" or not path.is_file():
            raise ScratchProjectError(f"expected an existing .drt archive: {path}")
        try:
            archive = resolve_archive.read_archive(path)
            measured = drt_reader.read_drt_archive(path)
        except Exception as exc:
            raise ScratchProjectError(
                f"cannot inspect DRT {path.name!r}: {exc}") from exc
        if archive["format"] != "drt" or len(archive["timelines"]) != 1:
            raise ScratchProjectError(
                f"DRT {path.name!r} must contain exactly one timeline")
        if len(measured["timelines"]) != 1:
            raise ScratchProjectError(
                f"DRT {path.name!r} has no unique sequence container")
        name = str(archive["project"] or "").strip()
        if not name or name in names:
            raise ScratchProjectError(
                f"DRT {path.name!r} has an empty or duplicate timeline name {name!r}")
        names.add(name)
        expected_tracks = []
        for track in measured["timelines"][0]["tracks"]:
            kind = str(track["kind"] or "").lower()
            if "video" in kind:
                kind = "video"
            elif "audio" in kind:
                kind = "audio"
            else:
                raise ScratchProjectError(
                    f"DRT {path.name!r} has an unsupported track type {kind!r}")
            expected_tracks.append((kind, len(track["items"])))
        prepared.append({"path": path, "name": name,
                         "tracks": tuple(sorted(expected_tracks)),
                         "items": int(measured["item_count"])})

    def facts(timeline):
        tracks = []
        for kind in ("video", "audio"):
            count = int(timeline.GetTrackCount(kind) or 0)
            for index in range(1, count + 1):
                items = timeline.GetItemListInTrack(kind, index) or []
                tracks.append((kind, len(items)))
        return tuple(sorted(tracks)), sum(count for _kind, count in tracks)

    timelines = [project.GetTimelineByIndex(index)
                 for index in range(1, int(project.GetTimelineCount() or 0) + 1)]
    timelines = [timeline for timeline in timelines if timeline]
    pool = project.GetMediaPool()
    if pool is None:
        raise ScratchProjectError("Resolve returned no MediaPool for DRT recovery")
    result = []
    for row in prepared:
        matches = [timeline for timeline in timelines
                   if timeline.GetName() == row["name"]]
        if len(matches) > 1:
            raise ScratchProjectError(
                f"Ren Qualification has multiple timelines named {row['name']!r}")
        if matches:
            timeline = matches[0]
            source = "existing"
        else:
            archive_name = row["path"].stem
            recovered = [timeline for timeline in timelines
                          if timeline.GetName() == archive_name]
            if len(recovered) > 1:
                raise ScratchProjectError(
                    f"Ren Qualification has multiple recovery timelines "
                    f"named {archive_name!r}")
            if recovered:
                timeline = recovered[0]
                source = "recovered-renamed"
            else:
                timeline = pool.ImportTimelineFromFile(
                    str(row["path"]),
                    {"timelineName": row["name"],
                     "importSourceClips": False})
                if timeline is None:
                    raise ScratchProjectError(
                        f"Resolve refused to import DRT {row['path'].name!r}")
                source = "imported"
        actual_tracks, item_count = facts(timeline)
        if actual_tracks != row["tracks"] or item_count != row["items"]:
            raise ScratchProjectError(
                f"timeline {timeline.GetName()!r} differs from DRT "
                f"{row['path'].name!r}: tracks/items "
                f"{actual_tracks!r}/{item_count}, expected "
                f"{row['tracks']!r}/{row['items']}")
        if timeline.GetName() != row["name"]:
            if timeline.GetName() != row["path"].stem:
                raise ScratchProjectError(
                    f"DRT {row['path'].name!r} read back as timeline "
                    f"{timeline.GetName()!r}, expected {row['name']!r}")
            if not timeline.SetName(row["name"]):
                raise ScratchProjectError(
                    f"Resolve refused to name imported DRT timeline "
                    f"{row['name']!r}")
            if timeline.GetName() != row["name"]:
                raise ScratchProjectError(
                    f"DRT timeline rename did not read back as "
                    f"{row['name']!r}")
            source = "recovered-renamed"
        actual_tracks, item_count = facts(timeline)
        if actual_tracks != row["tracks"] or item_count != row["items"]:
            raise ScratchProjectError(
                f"timeline {row['name']!r} differs from its DRT: "
                f"tracks/items {actual_tracks!r}/{item_count}, expected "
                f"{row['tracks']!r}/{row['items']}")
        uid = str(timeline.GetUniqueId() or "")
        if not uid:
            raise ScratchProjectError(
                f"Resolve did not return a stable identity for {row['name']!r}")
        if timeline not in timelines:
            timelines.append(timeline)
        result.append({"name": row["name"], "timeline_id": uid,
                       "source": source, "items": item_count,
                       "tracks": [list(track) for track in actual_tracks],
                       "archive": str(row["path"])})
    return result


@contextmanager
def _preserve_origin(resolve, manager, *, save: bool = True):
    """Record the origin, and put it back on the way out.

    `save=False` is for a caller that has already established the
    origin is not ours to write - the reel-level export reads a
    captain's project and must leave it exactly as it found it
    (`library/tools/reel_export.py`).  The origin is still restored and
    read back; it is simply not saved on the way in.
    """
    state = _starting_state(resolve, manager, save=save)
    try:
        yield state
    finally:
        current = manager.GetCurrentProject()
        if current is None or current.GetName() != state["project"]:
            restore(resolve, manager, state)
        else:
            # Still verify the timeline and page: lifecycle APIs can move
            # either without changing the project name.
            restore(resolve, manager, state)


def _timeline_by_id(project, uid):
    if uid is None:
        return None
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetUniqueId() == uid:
            return timeline
    return None


def restore(resolve, manager, state: dict) -> dict:
    """Restore the recorded project, timeline and page and verify each."""
    project = manager.GetCurrentProject()
    if project is None or project.GetName() != state["project"]:
        project = switch_project(manager, state["project"])
    if not project or project.GetName() != state["project"]:
        raise ScratchProjectError(f"restore did not reopen {state['project']!r}")
    timeline = _timeline_by_id(project, state["timeline_id"])
    identity_recovered = False
    if state["timeline_id"] is not None:
        if timeline is None:
            if not _ren_owned_origin(state["project"]):
                raise ScratchProjectError(
                    f"restore cannot find timeline {state['timeline']!r} "
                    "by its recorded id; captain projects require exact identity")
            matches = [project.GetTimelineByIndex(index)
                       for index in range(1, int(project.GetTimelineCount() or 0) + 1)
                       if project.GetTimelineByIndex(index)
                       and project.GetTimelineByIndex(index).GetName()
                       == state["timeline"]]
            if len(matches) != 1:
                raise ScratchProjectError(
                    f"restore cannot recover timeline {state['timeline']!r} "
                    f"by unique name in Ren-owned project {state['project']!r}; "
                    f"found {len(matches)}")
            timeline = matches[0]
            identity_recovered = True
        assert_current_timeline(project, timeline)
        current = project.GetCurrentTimeline()
        timeline_id = str(timeline.GetUniqueId() or "")
        if not current or str(current.GetUniqueId() or "") != timeline_id:
            raise ScratchProjectError("restored timeline did not read back")
    page = state["page"]
    if (page is not None and resolve.GetCurrentPage() != page
            and (not resolve.OpenPage(page)
                 or resolve.GetCurrentPage() != page)):
        raise ScratchProjectError(f"restore did not return to page {page!r}")
    result = {"project": state["project"], "timeline": state["timeline"],
              "page": state["page"], "verified": True}
    if identity_recovered:
        result.update({
            "timeline_id": str(timeline.GetUniqueId() or ""),
            "timeline_identity_recovered": True,
            "recorded_timeline_id": state["timeline_id"],
        })
    return result


def _ren_owned_origin(project_name: str) -> bool:
    """Whether a project name belongs to Ren's disposable project namespace."""
    return (project_name in {"Ren Qualification", "Resolve Qualification"}
            or project_name.startswith(SCRATCH_PREFIX))


def _listed(manager, name: str) -> bool:
    return name in (manager.GetProjectListInCurrentFolder() or [])


def _parse_fps(fps) -> float | None:
    """Validate an optional frame-rate request; None means leave the default."""
    if fps is None or (isinstance(fps, str) and not fps.strip()):
        return None
    try:
        value = float(str(fps).strip())
    except (TypeError, ValueError) as exc:
        raise ScratchProjectError(
            f"scratch frame rate must be a number like 23.976, got {fps!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ScratchProjectError(
            f"scratch frame rate must be a positive number, got {fps!r}")
    return value


def _parse_shape(width, height) -> tuple[int, int] | None:
    """Validate an optional resolution request; both or neither, positive ints."""
    missing = [label for label, value in (("width", width), ("height", height))
               if value is None or (isinstance(value, str) and not str(value).strip())]
    if len(missing) == 2:
        return None
    if len(missing) == 1:
        raise ScratchProjectError(
            f"scratch resolution needs both width and height, missing {missing[0]}")
    try:
        parsed = (float(str(width).strip()), float(str(height).strip()))
    except (TypeError, ValueError) as exc:
        raise ScratchProjectError(
            f"scratch resolution must be positive integers, "
            f"got {width!r}x{height!r}") from exc
    if (any(not math.isfinite(value) for value in parsed)
            or any(value != int(value) for value in parsed)
            or any(value <= 0 for value in parsed)):
        raise ScratchProjectError(
            f"scratch resolution must be positive integers, "
            f"got {width!r}x{height!r}")
    return (int(parsed[0]), int(parsed[1]))


def _write_project_setting(project, key: str, value: str) -> bool:
    """Write one project setting, preferring the plural API Resolve 21 offers."""
    try:
        return bool(project.SetSettings({key: value}))
    except AttributeError:
        return bool(project.SetSetting(key, value))


def _apply_project_shape(project, name: str, fps: float | None,
                         shape: tuple[int, int] | None) -> dict:
    """Set frame rate / resolution on a timeline-less project, verified by read-back.

    Runs between `CreateProject` and the first `CreateEmptyTimeline`: changing
    either setting once a timeline exists deadlocks Resolve, so a non-empty
    project refuses here instead of writing.
    """
    try:
        timeline_count = int(project.GetTimelineCount() or 0)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise ScratchProjectError(
            f"cannot set the shape of {name!r}: Resolve did not return "
            f"a readable timeline count ({exc})") from exc
    if timeline_count:
        raise ScratchProjectError(
            f"cannot set the shape of {name!r}: it already holds "
            f"{timeline_count} timeline(s), and changing the project frame "
            "rate or resolution under existing timelines deadlocks Resolve")
    pending: dict[str, str] = {}
    if fps is not None:
        pending["timelineFrameRate"] = (
            str(int(fps)) if float(fps).is_integer() else str(fps))
    if shape is not None:
        pending["timelineResolutionWidth"] = str(shape[0])
        pending["timelineResolutionHeight"] = str(shape[1])
    for key, value in pending.items():
        try:
            wrote = _write_project_setting(project, key, value)
            back = project.GetSetting(key)
        except Exception as exc:
            raise ScratchProjectError(
                f"cannot set {key}={value} on {name!r} ({exc}); "
                "verify by hand") from exc
        try:
            actual = float(back)
            wanted = float(value)
        except (TypeError, ValueError):
            actual, wanted = back, value
        matches = (abs(actual - wanted) <= 0.0001
                   if isinstance(actual, float) and isinstance(wanted, float)
                   else actual == wanted)
        if not wrote or not matches:
            raise ScratchProjectError(
                f"Resolve refused {key}={value} on {name!r}: "
                f"write returned {wrote!r}, read back {back!r}")
    return pending


def read_project_shape(project) -> dict:
    """Read back the project frame rate and resolution; None where unreadable.

    A caller that copies a timeline OUT of a project needs the shape it
    was in, so the copy lands in the same frame rate and resolution
    rather than Resolve's default.
    """
    try:
        fps = float(project.GetSetting("timelineFrameRate"))
        fps = fps if math.isfinite(fps) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        fps = None
    shape = []
    for key in ("timelineResolutionWidth", "timelineResolutionHeight"):
        try:
            value = float(project.GetSetting(key))
            shape.append(int(value) if math.isfinite(value) and value.is_integer() else None)
        except (AttributeError, TypeError, ValueError, OverflowError):
            shape.append(None)
    return {"fps": fps,
            "resolution": (f"{shape[0]}x{shape[1]}"
                           if all(value is not None for value in shape) else None)}


def create(resolve, manager, name: str, fps=None, width=None, height=None) -> dict:
    name = _name(name)
    fps_value = _parse_fps(fps)
    shape = _parse_shape(width, height)
    if _listed(manager, name):
        raise ScratchProjectError(f"project {name!r} already exists in the current folder")
    _refuse_if_another_writer_holds(resolve, manager)
    with _preserve_origin(resolve, manager) as state:
        created = manager.CreateProject(name)
        _settle_after_lifecycle_call()
        if not created or created.GetName() != name:
            raise ScratchProjectError(f"Resolve did not create scratch project {name!r}")
        requested: dict = {}
        if fps_value is not None or shape is not None:
            requested = _apply_project_shape(created, name, fps_value, shape)
        pool = created.GetMediaPool()
        timeline = pool.CreateEmptyTimeline(f"{name} - Empty")
        if not timeline:
            raise ScratchProjectError(f"Resolve did not create an empty timeline in {name!r}")
        assert_current_timeline(created, timeline)
        if not manager.SaveProject():
            raise ScratchProjectError(f"Resolve did not save the new scratch project {name!r}")
        readback = manager.GetCurrentProject()
        current_timeline = readback.GetCurrentTimeline() if readback else None
        if (not readback or readback.GetName() != name or not current_timeline
                or current_timeline.GetUniqueId() != timeline.GetUniqueId()
                or int(readback.GetTimelineCount() or 0) != 1):
            raise ScratchProjectError(f"empty timeline did not read back in {name!r}")
        owned_artifacts = _record_created_project(readback, current_timeline)
        return {"project": name, "timeline": current_timeline.GetName(),
                "timeline_count": 1,
                "owned_artifacts": owned_artifacts,
                **({"requested": requested} if requested else {}),
                **read_project_shape(readback),
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}


def import_project(resolve, manager, source: str | Path, name: str, *,
                   fps=None, width=None, height=None,
                   save_origin: bool = True) -> dict:
    """Import a project or timeline into a new, Ren-owned scratch project.

    A `.drp` keeps the existing whole-project import behavior. A `.drt`
    creates an empty scratch project, applies its requested delivery shape
    before the first timeline exists, and imports the timeline there. The
    source is read only; the new project is the only project this operation
    creates or changes.

    `save_origin=False` is for a caller whose origin is a project this
    operation must not write - the reel-level export copies a timeline out
    of a captain's project and leaves that project exactly as it found it
    (`library/tools/reel_export.py`).  The origin is still restored and
    read back; it is simply not saved on the way in.
    """
    name = _name(name)
    artifact = Path(source).expanduser().resolve()
    suffix = artifact.suffix.lower()
    if not artifact.is_file() or suffix not in {".drp", ".drt"}:
        raise ScratchProjectError(
            f"import requires an existing .drp project or .drt timeline: {artifact}")
    if suffix == ".drp" and any(value is not None
                                  for value in (fps, width, height)):
        raise ScratchProjectError(
            "frame rate and resolution options apply only to .drt timeline imports")
    fps_value = _parse_fps(fps) if suffix == ".drt" else None
    shape = _parse_shape(width, height) if suffix == ".drt" else None
    if suffix == ".drt" and (fps_value is None or shape is None):
        raise ScratchProjectError(
            "a .drt scratch import needs --fps, --width and --height so "
            "the new project matches the source timeline")
    if _listed(manager, name):
        raise ScratchProjectError(f"project {name!r} already exists in the current folder")
    _refuse_if_another_writer_holds(resolve, manager)
    with _preserve_origin(resolve, manager, save=save_origin) as state:
        if suffix == ".drp":
            imported = manager.ImportProject(str(artifact), name)
            _settle_after_lifecycle_call()
            if not imported:
                raise ScratchProjectError(
                    f"Resolve refused to import {artifact.name!r} as {name!r}")
            if not _listed(manager, name):
                raise ScratchProjectError(
                    f"imported project {name!r} did not read back in the project list")
            project = switch_project(manager, name)
            if not project or project.GetName() != name:
                raise ScratchProjectError(
                    f"imported project {name!r} did not open by exact name")
        else:
            project = manager.CreateProject(name)
            _settle_after_lifecycle_call()
            current = manager.GetCurrentProject()
            if (not project or project.GetName() != name
                    or not current or current.GetName() != name):
                raise ScratchProjectError(
                    f"Resolve did not open the newly created scratch project "
                    f"{name!r} (returned {project.GetName() if project else None!r}; "
                    f"current {current.GetName() if current else None!r})")
            project = current
            requested = _apply_project_shape(project, name, fps_value, shape)
            pool = project.GetMediaPool()
            if pool is None:
                raise ScratchProjectError(
                    f"new scratch project {name!r} has no media pool")
            timeline = pool.ImportTimelineFromFile(str(artifact))
            _settle_after_lifecycle_call()
            if timeline is None:
                raise ScratchProjectError(
                    f"Resolve refused to import {artifact.name!r} as a timeline")
            assert_current_timeline(project, timeline)
            current_timeline = project.GetCurrentTimeline()
            if (not current_timeline
                    or current_timeline.GetUniqueId() != timeline.GetUniqueId()
                    or int(project.GetTimelineCount() or 0) != 1):
                raise ScratchProjectError(
                    f"imported timeline did not read back as the sole current "
                    f"timeline in {name!r}")
            if not manager.SaveProject():
                raise ScratchProjectError(
                    f"Resolve did not save imported scratch project {name!r}")
            project = manager.GetCurrentProject()
            current_timeline = project.GetCurrentTimeline() if project else None
            if (not project or project.GetName() != name or not current_timeline
                    or current_timeline.GetUniqueId() != timeline.GetUniqueId()
                    or int(project.GetTimelineCount() or 0) != 1):
                raise ScratchProjectError(
                    f"imported scratch project {name!r} failed its saved read-back")
            owned_artifacts = _record_created_project(project, current_timeline)
            return {"project": name, "source": str(artifact),
                    "imported": True, "timeline": current_timeline.GetName(),
                    "timeline_id": current_timeline.GetUniqueId(),
                    "timeline_count": 1, "owned_artifacts": owned_artifacts,
                    "requested": requested, **read_project_shape(project),
                    "restored": {"project": state["project"],
                                 "timeline": state["timeline"],
                                 "page": state["page"], "verified": True}}
        current = manager.GetCurrentProject()
        if not current or current.GetName() != name:
            raise ScratchProjectError(
                f"imported project {name!r} did not read back as current")
        owned_artifacts = _record_created_project(current)
        return {"project": name, "source": str(artifact), "imported": True,
                "owned_artifacts": owned_artifacts,
                "timeline_count": int(project.GetTimelineCount() or 0),
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}


SLOT_TIMEOUT_ENV = "PIPELINE_RESOLVE_SCRATCH_SLOT_TIMEOUT"
SLOT_POLL_SECONDS = 1.0

#: How often a waiter waiting for the scratch origin says so aloud.
#: Silence would read as a hang, and the wait is bounded by the
#: instance lease's own budget - 900 s by default - so a lane that
#: waited silently for a quarter of an hour learned nothing from it.
#: The instance lease's captain notice is the same shape and the same
#: interval (`library/tools/resolve_lock.py`).
SLOT_NOTICE_SECONDS = 30.0


def slot_timeout() -> float:
    """How long a writer waits for the scratch-origin slot.

    The instance lease's own budget by default: the scratch origin is
    queued like the lease, so it waits like the lease.
    """
    raw = os.environ.get(SLOT_TIMEOUT_ENV)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    from library.tools.resolve_lock import default_timeout
    return default_timeout()


def _slot_holder(records: list[dict]) -> dict | None:
    """The record that fills the machine's scratch-origin slot.

    The slot is the INNERMOST session: the one the machine is inside.
    The newest record is that session, because every session is opened
    and restored through this module - a restore clears its own record.
    """
    return records[-1] if records else None


def slot_holder() -> dict | None:
    """The LIVE holder of the machine's scratch-origin slot, or None.

    The newest record, when it belongs to a writer that is still alive
    and is not this one. A record of this writer's own session is not a
    holder (we are inside it), and a record whose owner has exited is
    not one either (the next writer takes that session back).

    This is the READ the caller needs before it queues work that moves
    the machine: it says whether the machine is inside somebody else's
    session, without touching Resolve.
    """
    record = _slot_holder(_read_ledger())
    if record is None:
        return None
    if _held_by_us(record):
        return None
    if not _owner_alive(record):
        return None
    return record


def wait_for_origin_slot(timeout: float | None = None,
                         poll: float = SLOT_POLL_SECONDS) -> dict:
    """Wait for the scratch-origin slot, the way the instance lease queues.

    A writer that finds the slot held by a LIVE holder waits for that
    holder to release it - restore the origin and clear its record -
    instead of being refused on the first sight. The wait is bounded and
    names the holder it is waiting for.

    It ends when the record is released, when the holder's process is
    gone (the next writer takes that session back), or at the timeout.
    It reads the ledger only: no Resolve connection, so it runs BEFORE
    the lease - the holder needs that lease to restore and release.

    A timeout is REPORTED, not raised: only the machine knows whether
    the session it waits for is the one it is inside, and the open
    under the lease is what decides that.
    """
    limit = slot_timeout() if timeout is None else float(timeout)
    started = time.time()
    deadline = started + limit
    last_notice = 0.0
    while True:
        records = _read_ledger()
        record = _slot_holder(records)
        if record is None:
            return {"waited": time.time() - started, "holder": "",
                    "released": True, "stale": False, "timed_out": False}
        holder = str(record.get("owner") or "another writer")
        if _held_by_us(record):
            return {"waited": time.time() - started, "holder": holder,
                    "released": False, "stale": False, "timed_out": False}
        if not _owner_alive(record):
            return {"waited": time.time() - started, "holder": holder,
                    "released": False, "stale": True, "timed_out": False}
        now = time.time()
        if now - last_notice >= SLOT_NOTICE_SECONDS:
            # The contention is SAID, not waited through in silence: who
            # holds the origin, and how long this waiter has been
            # waiting for it. Said BEFORE the deadline is checked, so a
            # wait that ends at once still says what it was waiting
            # for.
            print(f"waiting: the scratch origin is held by {holder} "
                  f"(pid {record.get('owner_pid')}); waited "
                  f"{now - started:.0f}s of {limit:.0f}s for it to be "
                  f"released.", file=sys.stderr, flush=True)
            last_notice = now
        if now >= deadline:
            return {"waited": now - started, "holder": holder,
                    "released": False, "stale": False, "timed_out": True}
        time.sleep(poll)


def _refuse_if_another_writer_holds(resolve, manager) -> None:
    """Refuse to move the machine while another writer is inside Resolve.

    A live holder's session is the machine's current project. Switching
    away from it puts that writer's next command in the wrong project,
    so a verb that moves the machine waits for the slot instead - the
    AXI CLI queues for it before taking the lease.
    """
    records = _read_ledger()
    current = manager.GetCurrentProject()
    current_name = current.GetName() if current else None
    record = _record_for(records, current_name)
    if record is None or _held_by_us(record):
        return
    if _owner_alive(record):
        raise ScratchOriginHeld(
            f"the scratch origin is held by {record.get('owner')!r} "
            f"(pid {record.get('owner_pid')}); wait for it to be released "
            "before moving the machine")


def _take_back_abandoned_session(resolve, manager, records: list[dict]):
    """Move the machine out of a session whose writer has exited.

    A session nobody is in any more is not left to block the machine,
    and it is not taken back by judgement either: the recorded origin
    is restored only when it is Ren's own. A captain project is never
    switched onto because another writer decided to - that origin is
    NAMED, left where it is, and the new session nests inside the
    abandoned one so the route back stays whole.
    """
    recovered = None
    held = None
    while True:
        current = manager.GetCurrentProject()
        current_name = current.GetName() if current else None
        record = _record_for(records, current_name)
        if record is None:
            break
        if _held_by_us(record):
            # This writer's own session: the new one nests inside it.
            break
        if _owner_alive(record):
            raise ScratchOriginHeld(
                f"the scratch origin is held by {record.get('owner')!r} "
                f"(pid {record.get('owner_pid')}); wait for it to be "
                "released before opening another scratch project")
        origin_name = str(record.get("project") or "")
        if not _ren_owned_origin(origin_name):
            held = {"origin": origin_name,
                    "scratch_project": str(record.get("scratch_project") or ""),
                    "reason": ("the recorded origin is not Ren-owned, so it "
                               "is named and left in place rather than "
                               "restored by judgement")}
            break
        if not _instance_lease_is_ours_or_free():
            raise ScratchProjectError(
                "the recorded scratch-project origin belongs to a process "
                "that has exited, but the Resolve instance is held by "
                "another writer; wait for it to finish")
        # The writer that recorded this session has exited. Left alone,
        # its record blocks every other writer until a person removes it
        # by hand, so the next open puts the machine back on the
        # recorded origin and clears the record the only safe way.
        result, records = recover_stale_origin(resolve, manager, record, records)
        recovered = result
    return records, recovered, held


def open_project(resolve, manager, name: str) -> dict:
    name = _name(name)
    records, recovered, held = _take_back_abandoned_session(
        resolve, manager, _read_ledger())
    state = _starting_state(resolve, manager)
    if name == state["project"]:
        raise ScratchProjectError(f"scratch project {name!r} is already open")
    if not _listed(manager, name):
        raise ScratchProjectError(f"scratch project {name!r} is not in the current folder")
    _write_origin({**state, "scratch_project": name}, records)
    try:
        project = switch_project(manager, name)
    except Exception:
        # Keep the record when Resolve may have partially switched. It
        # remains the only safe route back after a failed switch.
        raise
    timeline = project.GetCurrentTimeline()
    result = {"project": project.GetName(),
              "timeline": timeline.GetName() if timeline else None,
              "opened": True,
              "origin": {"project": state["project"], "timeline": state["timeline"],
                         "page": state["page"]},
              "recovered_origin": recovered,
              "restore_with": "resolve-axi project scratch restore --apply"}
    if held:
        result["held_origin"] = held
    return result


def _scratch_backup_name(safe_name: str) -> str:
    """The exact prefix this module writes a scratch export under."""
    return f"{safe_name}_"


def _prune_scratch_backups(safe_name: str, *, keep: int = SCRATCH_BACKUP_RETENTION) -> list[dict]:
    """Keep the newest exports of one scratch project, and name the rest.

    Only files this module wrote are ever named: the name is the safe
    project name, an underscore, a UTC stamp this module's format
    writes, and `.drp` (with its `.json` receipt). A file that does not
    match that shape is left alone, and so is a symlink.
    """
    from library.tools.shared_environment import vep_home

    root = vep_home() / "resolve-scratch-backups"
    if not root.is_dir():
        return []
    prefix = _scratch_backup_name(safe_name)
    exports = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() != ".drp" or not path.name.startswith(prefix):
            continue
        stamp = path.name[len(prefix):-len(".drp")]
        if not _is_own_stamp(stamp):
            continue
        exports.append((stamp, path))
    exports.sort(reverse=True)
    pruned = []
    for stamp, path in exports[keep:]:
        receipt = path.with_suffix(".json")
        for victim in (path, receipt):
            if victim.is_symlink() or not victim.is_file():
                continue
            try:
                size = victim.stat().st_size
                victim.unlink()
            except OSError:
                continue
            pruned.append({"path": str(victim), "bytes": size})
    return pruned


def _is_own_stamp(stamp: str) -> bool:
    """Whether a stamp is the one this module writes: 20261020T171234123456Z."""
    if len(stamp) != 22 or not stamp.endswith("Z"):
        return False
    body = stamp[:-1]
    if body[8:9] != "T":
        return False
    return (body[:8].isdigit() and body[9:15].isdigit()
            and body[15:21].isdigit())


def delete(resolve, manager, name: str, *, artifact_id: str = "") -> dict:
    name = str(name or "").strip()
    if name == "Podcast (field test)" or not name.startswith(SCRATCH_PREFIX):
        raise ScratchProjectError(f"refusing to delete non-scratch project {name!r}")
    name = _name(name)
    current = manager.GetCurrentProject()
    if current is not None and current.GetName() == name:
        raise ScratchProjectError(f"refusing to delete the open project {name!r}")
    if not _listed(manager, name):
        raise ScratchProjectError(f"scratch project {name!r} is not in the current folder")
    _refuse_if_another_writer_holds(resolve, manager)
    lifecycle = artifact_lifecycle
    log_path = _lifecycle_log_path()
    candidates = [row for row in lifecycle.resolve_artifacts(log_path)
                  if row["kind"] == "resolve_scratch_project"
                  and row["identity"].get("project_name") == name
                  and (not artifact_id or row["artifact_id"] == artifact_id)
                  and row.get("state") != lifecycle.PURGED]
    if len(candidates) != 1:
        raise ScratchProjectError(
            f"project {name!r} has no unique recorded Resolve identity; "
            "unrecorded resources are protected")
    ownership = candidates[0]
    identity = ownership["identity"]
    if identity.get("object_type") != "project" or not identity.get("object_unique_id"):
        raise ScratchProjectError("recorded scratch project identity is incomplete")
    if ownership["state"] == lifecycle.RETAINED:
        lifecycle.transition_resolve_artifact(
            log_path, ownership["artifact_id"], lifecycle.DISPOSABLE,
            reason="explicit guarded scratch-project retirement requested")
    elif ownership["state"] != lifecycle.DISPOSABLE:
        raise ScratchProjectError(
            f"scratch project is not eligible for retirement: {ownership['state']}")

    def retire_exact(recorded_identity):
        with _preserve_origin(resolve, manager) as state:
            target = switch_project(manager, name)
            target_id = str(target.GetUniqueId() or "") if target else ""
            if target_id != recorded_identity["object_unique_id"]:
                raise ScratchProjectError(
                    "Resolve project identity changed since ownership was recorded")
            if not manager.SaveProject():
                raise ScratchProjectError(
                    "Resolve would not save the exact scratch project; deletion refused")
        from library.tools.shared_environment import vep_home

        backup_root = vep_home() / "resolve-scratch-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_"
                            for c in name)
        backup = backup_root / f"{safe_name}_{stamp}.drp"
        if not manager.ExportProject(name, str(backup)):
            raise ScratchProjectError(
                f"Resolve could not export {name!r}; project was not deleted")
        if not backup.is_file() or backup.stat().st_size <= 0:
            raise ScratchProjectError(
                f"Resolve export for {name!r} did not produce a readable "
                "non-empty .drp; project was not deleted")
        receipt_path = backup.with_suffix(".json")
        receipt_path.write_text(json.dumps({
            "project": name, "resolve_project_id": recorded_identity["object_unique_id"],
            "backup": str(backup), "exported_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }, indent=2) + "\n", encoding="utf-8")
        if not manager.DeleteProject(name):
            raise ScratchProjectError(f"Resolve refused to delete scratch project {name!r}")
        _settle_after_lifecycle_call()
        if _listed(manager, name):
            raise ScratchProjectError(f"deleted project {name!r} remains in the project folder")
        # The project is gone, so its route back is gone with it: a
        # record naming a project that no longer exists is a slot
        # nobody can fill and a wait nobody can end.
        records = _read_ledger()
        if _record_for(records, name) is not None:
            _write_ledger(_drop_record(records, name))
        # The project is already gone, so bounding its exports must not
        # fail the retirement: a prune that cannot run is reported, not
        # raised over a deletion that already happened.
        try:
            pruned = _prune_scratch_backups(safe_name)
            prune_error = ""
        except OSError as exc:
            pruned = []
            prune_error = repr(exc)
        return {"verified": True, "project": name,
                "backup": str(backup), "receipt": str(receipt_path),
                "pruned_backups": pruned, "prune_error": prune_error,
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}

    result = lifecycle.retire_resolve_artifact(
        log_path, ownership["artifact_id"], identity, retire_exact)
    return {"project": name, "deleted": True, **result["receipt"]}


def plan_collection(manager) -> Path:
    """Write a manifest from exact owned identities; legacy rows stay protected."""
    from library.tools.shared_environment import vep_home
    from library.tools import artifact_lifecycle as lifecycle

    current = manager.GetCurrentProject()
    current_id = str(current.GetUniqueId() or "") if current else ""
    rows = [row for row in lifecycle.resolve_artifacts(_lifecycle_log_path())
            if row["kind"] == "resolve_scratch_project"
            and row.get("state") == lifecycle.RETAINED
            and row["identity"].get("object_unique_id") != current_id]
    rows.sort(key=lambda row: row["artifact_id"])
    candidates = [{"artifact_id": row["artifact_id"],
                   "identity": row["identity"],
                   "project": row["identity"]["project_name"],
                   "selected": False} for row in rows]
    rows_digest = hashlib.sha256(json.dumps(
        [{key: row[key] for key in ("artifact_id", "identity", "project")}
         for row in candidates], sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    directory = vep_home() / "resolve-scratch-gc"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    manifest = directory / f"plan_{stamp}.json"
    manifest.write_text(json.dumps({
        "format": "ren-resolve-scratch-gc/1",
        "created_at": datetime.now(UTC).isoformat(),
        "current_project_id_kept": current_id,
        "candidate_identities_sha256": rows_digest,
        "candidates": candidates,
    }, indent=2) + "\n", encoding="utf-8")
    return manifest


def apply_collection(resolve, manager, manifest: str | Path) -> dict:
    """Apply only reviewed manifest rows, exporting each .drp first."""
    path = Path(manifest).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScratchProjectError(f"cannot read scratch collection manifest: {exc}") from exc
    if document.get("format") != "ren-resolve-scratch-gc/1":
        raise ScratchProjectError("unsupported scratch collection manifest")
    rows = document.get("candidates")
    if not isinstance(rows, list):
        raise ScratchProjectError("scratch collection manifest has no candidate list")
    planned = [{"artifact_id": row["artifact_id"],
                "identity": row["identity"],
                "project": _name(row["project"]),
                "selected": bool(row.get("selected"))} for row in rows
               if isinstance(row, dict)]
    digest = hashlib.sha256(json.dumps(
        [{key: row[key] for key in ("artifact_id", "identity", "project")}
         for row in planned], sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != document.get("candidate_identities_sha256"):
        raise ScratchProjectError(
            "candidate identities changed since planning; create a fresh manifest")
    selected = [row for row in planned if row["selected"]]
    available = set(manager.GetProjectListInCurrentFolder() or [])
    current = manager.GetCurrentProject()
    current_id = str(current.GetUniqueId() or "") if current else ""
    for row in selected:
        name = row["project"]
        if (row["identity"].get("object_unique_id") == current_id
                or name not in available):
            raise ScratchProjectError(
                f"{name!r} is open or no longer present; nothing was collected")
    removed = []
    for row in selected:
        removed.append(delete(resolve, manager, row["project"],
                              artifact_id=row["artifact_id"]))
        document["collected"] = removed
        document["updated_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(document, indent=2) + "\n",
                        encoding="utf-8")
    return {"manifest": str(path), "removed": removed}
