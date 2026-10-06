"""Ren Fabric permissions: who may do what, as project data.

The spec's authority table (ren-fabric-spec 2026-10-06, "Permissions
become extremely important"): Observe, Suggest, Branch, Execute,
Merge, Approve, Deliver, Admin - assignable per participant per
project. A client's Ren gets observe + suggest, never delete-media or
modify-master-timeline; the lead editor's Ren holds broader authority.

The table's own order IS the authority order: each row grants what the
rows above it grant, plus one more thing. `grants` reads that order,
so the source of "merge includes execute" is the spec's table, not a
threshold this module invented.

One roster per project, beside the other review ledgers
(`pipeline_output/review/`, the `reel_versions` home): the
participants and the level each holds. A project with no roster file
is a pre-federation project - one operator, no checks, everything
allowed, so a stranger's project keeps working untouched. A project
WITH a roster is federated: a NAMED actor is checked at the existing
write points (`resource_leases.resource_lease` refuses an actor
lacking Execute, `undo_journal.merge_commits` refuses an actor lacking
Merge), by name, and the refusal is an event in the log.

The local operator names no actor (`actor=None`): direct runs by the
human at the machine are the machine owner's own acts (the
`resolve_lock.default_owner` philosophy) and are never checked. The
federation binds only named remote participants. An actor that cannot
be verified - no roster, or not on it - is refused rather than
allowed: a federated identity nobody granted is not an operator.

Reachability
------------
    python -m library.tools.fabric_permissions <project> roster

Proposals (the federation's negotiation) live here too, because every
step of one is an authority check: recording and answering need
Suggest, accepting needs Approve, and the accept runs the operation
through the normal branch/merge path under the approver's own name -
so the merge gate checks the approver like any other merger.

`tests/unit/resolve/test_fabric_permissions.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from library.tools import event_log
from library.tools.ren_refusal import RenRefusal

#: The spec's authority table, in the spec's own order. The order is
#: the authority: each level grants what the levels before it grant.
OBSERVE = "observe"
SUGGEST = "suggest"
BRANCH = "branch"
EXECUTE = "execute"
MERGE = "merge"
APPROVE = "approve"
DELIVER = "deliver"
ADMIN = "admin"

LEVELS = (OBSERVE, SUGGEST, BRANCH, EXECUTE, MERGE, APPROVE,
          DELIVER, ADMIN)

PERMISSIONS_FILENAME = "fabric_permissions.json"
PERMISSIONS_FORMAT = "fabric_permissions/1"


class RosterUnreadable(RuntimeError):
    """The roster exists and cannot be read. Never replaced with an empty one."""


class PermissionRefused(RenRefusal):
    """A named participant was refused an operation their level does not grant."""


class ProposalRefused(RenRefusal):
    """A proposal act was refused: unknown id, decided already, or malformed."""


def path_for(project_folder) -> str:
    """Where the roster lives: beside the other review ledgers."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        PERMISSIONS_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_roster(project_folder) -> dict:
    """This project's participant roster, or an empty one.

    No file is not an error: a project that never federated has no
    roster, and "nobody is restricted" is the honest answer - the
    single-operator Ren keeps working. A file that exists but cannot
    be read raises `RosterUnreadable`: it is the only account of who
    may do what, and reading it as empty would grant a refused actor
    a clean refusal-free pass.
    """
    path = path_for(project_folder)
    if not os.path.exists(path):
        return {"format": PERMISSIONS_FORMAT, "participants": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise RosterUnreadable(
            f"{path} exists but could not be read ({unreadable}). It is "
            f"the only account of which participant holds which level; "
            f"a gate that read it as empty would allow everyone.") \
            from unreadable
    if (not isinstance(document, dict)
            or not isinstance(document.get("participants"), dict)):
        raise RosterUnreadable(
            f"{path} exists but holds no participants map. It is the "
            f"only account of who may do what; a gate that read it as "
            f"empty would allow everyone.")
    return {"format": PERMISSIONS_FORMAT,
            "participants": document["participants"]}


def has_roster(project_folder) -> bool:
    """Whether this project federated: a roster file stands on disk."""
    return os.path.exists(path_for(project_folder))


def _write_roster(project_folder, roster: dict) -> None:
    """Write the roster back, atomically.

    Temp plus `os.replace` in the same directory, so a reader never
    sees half a file - the same rule `resource_leases._write_leases`
    holds for the lease store.
    """
    path = path_for(project_folder)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(roster, indent=2, sort_keys=True) + "\n"
    handle, tmp = tempfile.mkstemp(dir=parent, prefix="fabric_permissions.",
                                   suffix=".tmp")
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


def grant(project_folder, participant: str, level: str,
          *, granted_by: str = "") -> dict:
    """Give a participant a level. Returns the roster as written.

    `level` is one of `LEVELS` - anything else is refused rather than
    stored, because a level nothing grants is not a level. Granting
    over an existing entry replaces it; the roster records who granted
    it and when, so "who let the client's Ren merge" has an answer.
    """
    name = str(participant or "").strip()
    if not name:
        raise ValueError(
            "a grant needs a participant name - an unnamed grant "
            "matches nobody and restricts nothing.")
    if level not in LEVELS:
        raise ValueError(
            f"{level!r} is not an authority level. The levels are the "
            f"spec's own table, in order: {', '.join(LEVELS)}.")
    roster = read_roster(project_folder)
    roster["participants"][name] = {
        "level": level,
        "granted_by": granted_by,
        "at": _now(),
    }
    _write_roster(project_folder, roster)
    return roster


def revoke(project_folder, participant: str) -> dict:
    """Take a participant off the roster. Idempotent: revoking one who
    was never listed returns the roster unchanged."""
    roster = read_roster(project_folder)
    roster["participants"].pop(str(participant or "").strip(), None)
    _write_roster(project_folder, roster)
    return roster


def level_of(roster: dict, participant: str) -> Optional[str]:
    """The level a participant holds, or None where they hold none."""
    entry = (roster.get("participants") or {}).get(str(participant or ""))
    if not isinstance(entry, dict):
        return None
    level = entry.get("level")
    return level if level in LEVELS else None


def grants(held: str, required: str) -> bool:
    """Whether holding `held` grants `required`.

    The ladder is the spec's table order: merge outranks execute, so a
    participant trusted to merge may also take a lease; suggest does
    not grant execute, so a client's Ren may propose but never touch
    the timeline.
    """
    return LEVELS.index(held) >= LEVELS.index(required)


def _emit_refusal(project_folder, *, actor: str, required: str,
                  held: Optional[str], task: str) -> None:
    """Record the refusal in the project's event log, or skip silently.

    The refusal stands whether or not the event lands: the log is the
    record of the gate, never the gate itself. A lease whose caller
    named no project leaves no event, which is the honest answer - the
    event log is per-project and the project is the caller's to name.
    """
    if not project_folder:
        return
    try:
        event_log.emit(project_folder, event_log.PERMISSION_REFUSED,
                       task=task, actor=actor, required=required,
                       held=held or "none")
    except OSError:
        pass


def require(project_folder, actor: str, required: str,
            *, task: str = "") -> str:
    """Check that a named participant grants `required`. Returns the level held.

    Raises `PermissionRefused` - by name, as an event in the log -
    where the actor is unnamed, the project never federated, the actor
    is not on the roster, or the level they hold does not grant what
    the operation needs. `actor=None` never reaches here: the write
    points only call `require` for a NAMED actor, and the local
    operator's unnamed acts are unchecked.
    """
    if required not in LEVELS:
        raise ValueError(
            f"{required!r} is not an authority level - the levels are "
            f"{', '.join(LEVELS)}.")
    name = str(actor or "").strip()
    if not name:
        _emit_refusal(project_folder, actor="", required=required,
                      held=None, task=task)
        raise PermissionRefused(
            f"a federated operation needs a named participant, for {task!r}"
            if task else "a federated operation needs a named participant",
            "an unnamed act cannot be checked against any roster",
            "name the participant (director, editor, client, ...) the "
            "operation runs as, or run unnamed as the local operator")
    if not has_roster(project_folder or ""):
        _emit_refusal(project_folder, actor=name, required=required,
                      held=None, task=task)
        raise PermissionRefused(
            f"{name} claims a federated identity but this project has "
            f"no permission roster",
            f"{name} cannot be verified against a roster that does not exist",
            f"grant {name} a level first (fabric_permissions.grant), or "
            f"run unnamed as the local operator")
    roster = read_roster(project_folder)
    held = level_of(roster, name)
    if held is None or not grants(held, required):
        _emit_refusal(project_folder, actor=name, required=required,
                      held=held, task=task)
        raise PermissionRefused(
            f"{name} holds {held or 'no level'}, which does not grant "
            f"{required}" + (f" for {task!r}" if task else ""),
            f"this project's roster grants {name} "
            f"{held or 'nothing'} - {required} needs a level at or above "
            f"it in the spec's own order ({', '.join(LEVELS)})",
            f"have a participant with {required} (or above) approve or "
            f"perform this, or grant {name} a higher level")
    return held


# ── Proposals: federated negotiation on the event log ───────────────

#: What a proposal may ask to run. One entry today: merging a prepared
#: branch tip through the normal merge path. The table grows only with
#: a path that applies it - an operation nothing applies is not Proposed.
PROPOSAL_OPERATIONS = ("merge",)

#: How another participant's Ren may answer a proposal. An `accept`
#: answer is support, never approval: nothing applies until a
#: participant with Approve accepts.
PROPOSAL_ANSWERS = ("conflict", "alternative", "accept")


def _proposal_events(project_folder) -> list:
    """Every proposal-lifecycle event on this project, oldest first."""
    return [event for event in event_log.read_events(project_folder)
            if event.get("type") in (event_log.PROPOSAL_RECORDED,
                                    event_log.PROPOSAL_ANSWERED,
                                    event_log.PROPOSAL_ACCEPTED)]


def _check_operation(operation: dict) -> dict:
    """The operation as the apply path will run it, or refused.

    A merge names the timeline and both commits. Anything else -
    unknown kind, missing side - is refused at record time: a proposal
    that cannot be applied must never reach an approver.
    """
    if not isinstance(operation, dict):
        raise ProposalRefused(
            "a proposal needs an operation dict, not "
            f"{type(operation).__name__}",
            "the accept runs the operation through the branch/merge "
            "path, and a path cannot run what is not described",
            "pass operation={'kind': 'merge', 'timeline': ..., "
            "'left': ..., 'right': ...}")
    kind = operation.get("kind")
    if kind not in PROPOSAL_OPERATIONS:
        raise ProposalRefused(
            f"proposal operation {kind!r} is not applicable",
            f"the operations an accept can run are "
            f"{', '.join(PROPOSAL_OPERATIONS)} - nothing else has an "
            f"apply path",
            "propose a merge of a prepared branch tip, or leave the "
            "change to a participant who can branch it")
    missing = [key for key in ("timeline", "left", "right")
               if not operation.get(key)]
    if missing:
        raise ProposalRefused(
            f"a merge proposal needs {', '.join(missing)}",
            "the accept merges two commits on one timeline, and a "
            "merge with an unnamed side cannot run",
            f"name {', '.join(missing)} in the operation")
    return {"kind": "merge", "timeline": str(operation["timeline"]),
            "left": str(operation["left"]), "right": str(operation["right"]),
            "note": str(operation.get("note") or "")}


def record_proposal(project_folder, author: str, operation: dict,
                    rationale: str) -> dict:
    """Record a proposal: an operation plus why. Needs Suggest.

    Returns the event as written, carrying the proposal id answers
    and the accept name. The rationale is required and non-empty: a
    proposal with nothing to say is not negotiation, and the
    negotiation in the spec's example ("hold the reaction 1.4s
    longer") always says why.
    """
    require(project_folder, author, SUGGEST,
            task="record a proposal")
    checked = _check_operation(operation)
    if not str(rationale or "").strip():
        raise ProposalRefused(
            f"{author}'s proposal carries no rationale",
            "a proposal is an operation plus why - the answerers "
            "negotiate against the reason, not just the edit",
            "say why the change serves the production")
    proposal_id = "prop-" + uuid.uuid4().hex[:10]
    return event_log.emit(
        project_folder, event_log.PROPOSAL_RECORDED, task=str(author),
        proposal_id=proposal_id, author=str(author),
        operation=checked, rationale=str(rationale).strip())


def proposal_status(project_folder, proposal_id: str) -> dict:
    """Where a proposal stands: recorded answers plus decided or not.

    Read from the log's own events, so two Rens never disagree about
    what was proposed: the log is the shared state, not messages
    passed between them.
    """
    events = [event for event in _proposal_events(project_folder)
              if (event.get("fields") or {}).get("proposal_id") == proposal_id]
    if not events:
        raise ProposalRefused(
            f"proposal {proposal_id!r} was never recorded",
            "answers and accepts name a recorded proposal id",
            "record it first with record_proposal, or check the id")
    recorded = events[0]
    answers = [event for event in events
               if event.get("type") == event_log.PROPOSAL_ANSWERED]
    accepted = [event for event in events
                if event.get("type") == event_log.PROPOSAL_ACCEPTED]
    return {
        "proposal_id": proposal_id,
        "status": "accepted" if accepted else "recorded",
        "recorded": recorded,
        "answers": answers,
        "accepted": accepted[0] if accepted else None,
    }


def answer_proposal(project_folder, proposal_id: str, author: str,
                    kind: str, *, note: str = "") -> dict:
    """Answer a proposal: conflict, alternative, or accept. Needs Suggest.

    An `accept` answer is one participant's support - it applies
    nothing. Only `accept_proposal` by a participant with Approve
    applies, which is what keeps the client's Ren (observe+suggest)
    from merging by agreeing with itself. Answering a decided
    proposal is refused: the negotiation is over.
    """
    require(project_folder, author, SUGGEST,
            task=f"answer proposal {proposal_id}")
    if kind not in PROPOSAL_ANSWERS:
        raise ProposalRefused(
            f"{kind!r} is not an answer",
            f"a proposal is answered with {', '.join(PROPOSAL_ANSWERS)}",
            "say whether the proposal conflicts, offer an alternative, "
            "or accept it")
    state = proposal_status(project_folder, proposal_id)
    if state["status"] != "recorded":
        raise ProposalRefused(
            f"proposal {proposal_id} is already {state['status']}",
            "the negotiation ended when it was accepted - a later "
            "answer would negotiate against applied work",
            "record a new proposal against the current chain")
    return event_log.emit(
        project_folder, event_log.PROPOSAL_ANSWERED, task=str(author),
        proposal_id=proposal_id, author=str(author), kind=kind,
        note=str(note or ""))


def accept_proposal(project_folder, proposal_id: str, approver: str) -> dict:
    """Accept a proposal: approve AND apply. Needs Approve.

    The operation runs through the normal merge path under the
    approver's own name (`merge_commits(actor=approver)`), so the
    merge gate checks the approver like any other merger. The accept
    event lands AFTER the merge succeeds and carries its id: a log
    that claims "accepted" for a merge that never landed would be the
    hole the event-sourcing exists to close. A merge the path refuses
    (conflicting clips) propagates, and the proposal stays open.
    """
    require(project_folder, approver, APPROVE,
            task=f"accept proposal {proposal_id}")
    state = proposal_status(project_folder, proposal_id)
    if state["status"] != "recorded":
        raise ProposalRefused(
            f"proposal {proposal_id} is already {state['status']}",
            "an accept applies the operation, and applying twice "
            "would merge the same change a second time",
            "record a new proposal against the current chain")
    operation = (state["recorded"].get("fields") or {}).get("operation") or {}
    from library.tools import undo_journal as _journal
    merged = _journal.merge_commits(
        project_folder, timeline=operation["timeline"],
        left=operation["left"], right=operation["right"],
        note=operation.get("note") or f"proposal {proposal_id}",
        actor=approver)
    return event_log.emit(
        project_folder, event_log.PROPOSAL_ACCEPTED, task=str(approver),
        proposal_id=proposal_id, approver=str(approver),
        merge=merged["id"])


def _main(argv=None) -> int:
    """`python -m library.tools.fabric_permissions <project> roster`."""
    parser = argparse.ArgumentParser(
        prog="python -m library.tools.fabric_permissions",
        description="This project's federated participants: who holds "
                    "which authority level.")
    parser.add_argument("project", help="the project folder")
    parser.add_argument("command", nargs="?", default="roster",
                        help="roster: list the participants (default)")
    args = parser.parse_args(argv)
    if args.command == "roster":
        roster = read_roster(args.project)
        participants = roster.get("participants") or {}
        if not participants:
            print("fabric permissions: no roster - this project never "
                  "federated, and every act runs as the local operator.")
            return 0
        print("fabric permissions:")
        for name in sorted(participants):
            print(f"  {name}: "
                  f"{(participants[name] or {}).get('level', 'none')}")
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - argparse exits first


if __name__ == "__main__":
    raise SystemExit(_main())
