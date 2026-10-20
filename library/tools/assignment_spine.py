"""The assignment layer as the spine of every user request.

`assignment_lifecycle` holds one record per creative assignment and the D2
autonomy table; `assignment_interaction` is the host-neutral envelope. Until
this module, nothing a user ran reached either, so `ren work-state` always
showed no assignments. This is where the verbs a user actually runs write
the record.

Where an assignment is CREATED
------------------------------
Only by the act that starts a request: `ren propose` (a reels request),
`ren edit` (a full edit) and `ren revise` (`attach_revision`, as its
change lands) when no request owns the reel its note is about. Each attaches to the project's open assignment of its
kind while that is still being planned (`brief`/`draft`) and opens a new
one otherwise. Build, sign-off and deliver never create one: a reel no
assignment owns (a project that predates this, or a hand-made proposal) is
left untracked rather than given an invented history.

Which verbs TRANSITION it
-------------------------
Every act appends one history entry `{event, from, to, at}`, so the
history reads as the sequence of verbs that happened:

    propose   brief -> draft      the reels are proposed
    approve   draft -> draft      the PLAN is approved; nothing is built
    build     draft -> review     every approved reel is on a timeline
    sign_off  review -> approved  every built reel is signed off
    deliver   approved -> delivered  every signed-off reel is a file
    revise    -> review           a note changed a built reel in place
    preview   (no move)           a watchable file of a built reel exists

Approving a proposal does not make the assignment `approved`. `approved`
is the D2 word for work the creator has accepted, and a plan with no
timeline is not work; were it, the first build of every request would
be refused as a replacement of approved work.

A request covers several reels, so each reel carries its own status
(`REEL_STAGE`) and the assignment state is the LEAST advanced active reel:
signing off reel 1 of 3 records the act and leaves the request in review.

Interrupt and recover
---------------------
A build or delivery is a checkpoint (`lifecycle.begin_operation`) carrying
the state it started from and the process that runs it. The state moves
only when the act completes, so a refusal or a crash leaves the assignment
exactly where it was; the next act (or `recover_interrupted`) marks a
checkpoint whose process is gone as `interrupted`, and `resume_point`
names the verb to run again.

D2: replacing approved work asks
--------------------------------
A build that would re-place a signed-off or delivered reel, a delivery
that would overwrite an approved delivery with unapproved work, and an
edit run over an approved edit all go through
`lifecycle.operation_effect`. Without the per-assignment authorization
(`ren assignment <project> --authorize-replacement`) they raise
`AssignmentDecisionRequired`, recovery class `needs_question`: a question
for the host to put to the person, not an error. The authorization lasts
until the work is approved again.
"""

from __future__ import annotations

import os
import secrets
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from library.tools import assignment_lifecycle as lifecycle
from library.tools.ren_refusal import RenRefusal

KIND_REELS = "reels"
KIND_EDIT = "edit"

# A reel's status inside its assignment, in order, and the assignment
# state a reel at that status holds the request at.
REEL_STAGE = {
    "approved": "draft",
    "built": "review",
    "signed_off": "approved",
    "delivered": "delivered",
}
INACTIVE_REEL_STATUSES = ("proposed", "rejected")
APPROVED_WORK = ("signed_off", "delivered")

_SPINE_LOCK = "__assignment_spine__"


class AssignmentDecisionRequired(RenRefusal):
    """The act would replace approved work, and the creator has not said yes."""

    CODE = "REN_ASSIGNMENT_DECISION_REQUIRED"
    RECOVERY_CLASS = "needs_question"

    def __init__(self, what: str, *, assignment_id: str, effect: dict,
                 question: str, authorize: str) -> None:
        super().__init__(
            what,
            "replacing work the creator approved needs their explicit yes "
            "for this assignment (decision D2)",
            f"ask: {question} On yes, run `{authorize}` and repeat the "
            f"same command",
            user_message=question)
        self.assignment_id = assignment_id
        self.effect = effect
        self.question = question
        self.authorize = authorize

    def to_dict(self) -> dict:
        return {**super().to_dict(), "assignment_id": self.assignment_id,
                "effect": self.effect, "question": self.question,
                "authorize": self.authorize}


@dataclass
class Act:
    """One running build or delivery, per owning assignment."""

    project: str
    verb: str
    operation_id: str
    checkpoints: list = field(default_factory=list)
    """`(assignment_id, checkpoint_key, moment_ids)` per owner."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _root(project) -> str:
    return str(Path(project).expanduser().resolve())


def _new_id(kind: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{kind}-{stamp}-{secrets.token_hex(3)}"


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _moments(project: str) -> list:
    """The proposal's moments with their keys, or none when there is none."""
    from library.tools import reel_proposal

    path = reel_proposal.proposal_path(project)
    if not path.is_file():
        return []
    try:
        moments = reel_proposal.read_proposal(path)
    except (OSError, ValueError):
        # An unreadable proposal is the build's refusal to make, by name;
        # the record simply has no reels to attach to.
        return []
    return [reel_proposal.ensure_moment_id(m) for m in moments]


def _key(record: dict) -> tuple:
    return (record.get("updated_at") or "", record.get("created_at") or "")


def current(project, kind: str | None = None) -> dict | None:
    """The newest assignment of `kind` that is not yet delivered."""
    open_records = [r for r in lifecycle.list_records(_root(project))
                    if r["state"] != "delivered"
                    and (kind is None or r.get("kind") == kind)]
    return max(open_records, key=_key) if open_records else None


def _owner(records: list, moment_id: str) -> dict | None:
    holding = [r for r in records if moment_id in (r.get("reels") or {})]
    return max(holding, key=_key) if holding else None


def _aggregate(record: dict) -> str:
    """The state the least advanced active reel holds the request at."""
    if record.get("kind") != KIND_REELS:
        return record["state"]
    active = [reel["status"] for reel in (record.get("reels") or {}).values()
              if reel["status"] not in INACTIVE_REEL_STATUSES]
    if not active:
        return "draft"
    order = list(REEL_STAGE)
    return REEL_STAGE[min(active, key=order.index)]


def _settle(project: str, record: dict, event: str, detail: dict) -> dict:
    """Persist reel changes, then record `event` moving to the aggregate."""
    lifecycle.save(project, record)
    target = _aggregate(record)
    if target != record["state"] and \
            target not in lifecycle.TRANSITIONS[record["state"]]:
        target = record["state"]
    return lifecycle.transition(project, record["assignment_id"], target,
                                event=event, detail=detail)


def _reel_entry(moment) -> dict:
    return {"number": int(moment.number), "name": moment.timeline_name,
            "status": moment.approval.value}


# ── Where an assignment is created ──────────────────────────────────


def open_request(project, kind: str, *, event: str,
                 request: str | None = None) -> dict:
    """Attach to the open assignment still being planned, or open one."""
    root = _root(project)
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        record = current(root, kind)
        if record is None or record["state"] not in ("brief", "draft"):
            record = lifecycle.create(
                root, _new_id(kind), kind=kind, request=request,
                reels={} if kind == KIND_REELS else None)
        if kind == KIND_REELS:
            known = record.get("reels") or {}
            reels = {}
            for moment in _moments(root):
                entry = _reel_entry(moment)
                prior = known.get(moment.moment_id)
                # A re-proposal keeps what already happened to a reel
                # the proposal still approves.
                if prior and entry["status"] == "approved":
                    entry = {**prior, **entry, "status": prior["status"]}
                reels[moment.moment_id] = entry
            record["reels"] = reels
            return _settle(root, record, event,
                           {"reels": sorted(r["number"] for r in
                                            record["reels"].values())})
        target = "draft" if record["state"] == "brief" else record["state"]
        return lifecycle.transition(root, record["assignment_id"], target,
                                    event=event)


def on_propose(project) -> dict:
    """`ren propose` published the proposal: the request's reels exist."""
    return open_request(project, KIND_REELS, event="propose")


def on_approve(project, *, source: str = "ren approve") -> dict | None:
    """Record approvals the proposal now carries and the record has not.

    The approval itself is the proposal file's (`reel_proposal`); this
    records that it happened, once per newly ruled reel. Called by the
    verb that writes an approval and, because approvals have also been
    written by hand, by every build before it reads them.
    """
    root = _root(project)
    if not lifecycle.list_records(root):
        return None
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        records = lifecycle.list_records(root)
        ruled: dict[str, list] = {}
        for moment in _moments(root):
            owner = _owner(records, moment.moment_id)
            if owner is None:
                continue
            reel = owner["reels"][moment.moment_id]
            if moment.approval.value != reel["status"] and \
                    reel["status"] in ("proposed", "approved", "rejected"):
                reel["status"] = moment.approval.value
                ruled.setdefault(owner["assignment_id"], []).append(
                    (owner, moment))
        settled = None
        for pairs in ruled.values():
            owner = pairs[0][0]
            approved = sorted(int(m.number) for _, m in pairs
                              if m.approval.value == "approved")
            if not approved:
                lifecycle.save(root, owner)
                continue
            settled = _settle(root, owner, "approve",
                              {"reels": approved, "source": source})
        return settled


# ── Interrupt and recover ───────────────────────────────────────────


def recover_interrupted(project) -> list[dict]:
    """Mark checkpoints whose process is gone as interrupted.

    The state was never moved by the act that died, so marking the
    checkpoint is the whole recovery: the assignment is where it was
    before the act began, and `resume_point` names the verb to repeat.
    """
    root = _root(project)
    recovered = []
    for record in lifecycle.list_records(root):
        key = record.get("checkpoint")
        checkpoint = (record.get("operations") or {}).get(key) if key else None
        if not checkpoint or checkpoint.get("status") != "running":
            continue
        request = checkpoint.get("request") or {}
        if _pid_alive(request.get("pid")) and request.get("pid") != os.getpid():
            continue

        def _mark(r, key=key):
            r["operations"][key].update(status="interrupted",
                                        interrupted_at=_now(),
                                        recovered_to=r["state"])

        recovered.append(lifecycle.update(root, record["assignment_id"], _mark))
    return recovered


def _refuse_concurrent(record: dict) -> None:
    key = record.get("checkpoint")
    checkpoint = (record.get("operations") or {}).get(key) if key else None
    if checkpoint and checkpoint.get("status") == "running":
        request = checkpoint.get("request") or {}
        raise RenRefusal(
            f"assignment {record['assignment_id']} is already running "
            f"`{request.get('verb')}` (process {request.get('pid')})",
            "two acts on one assignment would race for its state",
            "wait for it to finish, then repeat this command")


def _begin(root: str, record: dict, verb: str, operation_id: str,
           numbers: list) -> str:
    request = {"verb": verb, "reels": numbers, "pid": os.getpid(),
               "act": secrets.token_hex(6), "pre_state": record["state"],
               "started_at": _now()}
    lifecycle.begin_operation(root, record["assignment_id"], operation_id,
                              request)
    return lifecycle.load(root, record["assignment_id"])["checkpoint"]


def _finish_checkpoint(record: dict, key: str, status: str, detail: str):
    checkpoint = record["operations"][key]
    checkpoint.update(status=status, finished_at=_now())
    if detail:
        checkpoint["detail"] = detail


# ── D2 ──────────────────────────────────────────────────────────────


def authorize_command(project, assignment_id: str) -> str:
    return (f"ren assignment {shlex.quote(_root(project))} "
            f"--id {shlex.quote(assignment_id)} --authorize-replacement")


def _require_decision(root: str, record: dict, operation_id: str,
                      what: str, question: str, inputs: dict) -> None:
    effect = lifecycle.operation_effect(root, record["assignment_id"],
                                        operation_id, inputs)
    if not effect["allowed"]:
        raise AssignmentDecisionRequired(
            what, assignment_id=record["assignment_id"], effect=effect,
            question=question,
            authorize=authorize_command(root, record["assignment_id"]))


# ── Build ───────────────────────────────────────────────────────────


def begin_build(project, only_reels=None) -> Act | None:
    """Gate and checkpoint a reels build. None when no assignment owns it."""
    root = _root(project)
    if not lifecycle.list_records(root):
        return None
    on_approve(root, source="proposal file, observed at build")
    recover_interrupted(root)
    wanted = {int(n) for n in only_reels} if only_reels else None
    moments = [m for m in _moments(root) if m.approval.value == "approved"
               and (wanted is None or int(m.number) in wanted)]
    records = lifecycle.list_records(root)
    for record in records:
        replaced = [m for m in moments
                    if (record.get("reels") or {}).get(m.moment_id, {})
                    .get("status") in APPROVED_WORK]
        if replaced:
            names = ", ".join(m.timeline_name for m in replaced)
            _require_decision(
                root, record, "reel.build",
                f"building would replace approved work: {names}",
                f"{names} {'is' if len(replaced) == 1 else 'are'} already "
                f"approved. Rebuild and replace {'it' if len(replaced) == 1 else 'them'}?",
                {"replace_approved_work": True})
    act = Act(root, "build", "reel.build")
    by_owner: dict[str, list] = {}
    for moment in moments:
        owner = _owner(records, moment.moment_id)
        if owner is not None:
            by_owner.setdefault(owner["assignment_id"], []).append(moment)
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        owners = [lifecycle.load(root, aid) for aid in by_owner]
        for record in owners:
            _refuse_concurrent(record)
        for record in owners:
            owned = by_owner[record["assignment_id"]]
            key = _begin(root, record, "build", act.operation_id,
                         sorted(int(m.number) for m in owned))
            act.checkpoints.append((record["assignment_id"], key,
                                    [m.moment_id for m in owned]))
    return act if act.checkpoints else None


def end_act(act: Act | None, *, ok: bool, status: str = "",
            detail: str = "") -> None:
    """Close the act: on success move the reels and the state, else not.

    A failed act leaves the state where it began; `status` says how it
    ended (`refused` by default) and `detail` why.
    """
    if act is None:
        return
    for assignment_id, key, moment_ids in act.checkpoints:
        with lifecycle.assignment_lock(act.project, assignment_id):
            record = lifecycle.load(act.project, assignment_id)
            if not ok:
                _finish_checkpoint(record, key, status or "refused", detail)
                lifecycle.save(act.project, record)
                continue
            _finish_checkpoint(record, key, "completed", detail)
            numbers = []
            if act.verb == "revise" and record.get("kind") != KIND_REELS:
                lifecycle.save(act.project, record)
                target = ("review" if record["state"] in
                          ("approved", "delivered") else record["state"])
                lifecycle.transition(act.project, assignment_id, target,
                                     event="revise",
                                     detail={"detail": detail} if detail
                                     else None)
                continue
            for moment_id in moment_ids:
                reel = record["reels"][moment_id]
                numbers.append(reel["number"])
                if act.verb == "revise":
                    # Changed work is unapproved work: a signed-off or
                    # delivered reel goes back to waiting for a sign-off.
                    reel["revised_at"] = _now()
                    if reel["status"] in APPROVED_WORK:
                        reel["status"] = "built"
                elif act.verb == "build":
                    reel["status"] = "built"
                    reel["built_at"] = _now()
                elif act.verb == "deliver":
                    reel["delivered_at"] = _now()
                    if reel["status"] in APPROVED_WORK:
                        reel["status"] = "delivered"
                        reel["delivered_approved"] = True
            if act.operation_id == "render.build":
                lifecycle.save(act.project, record)
                lifecycle.transition(act.project, assignment_id, "review",
                                     event="build")
                continue
            _settle(act.project, record, act.verb, {"reels": sorted(numbers)})


# ── Sign-off ────────────────────────────────────────────────────────


def _moment_for_timeline(root: str, timeline: str):
    from library.tools import reel_signoff

    base = reel_signoff.base_name(timeline)
    return next((m for m in _moments(root) if m.timeline_name == base), None)


def on_sign_off(project, timeline: str, *, withdrawn: bool = False) -> dict | None:
    """A built reel was signed off (or the sign-off withdrawn)."""
    root = _root(project)
    if not lifecycle.list_records(root):
        return None
    event = "withdraw_sign_off" if withdrawn else "sign_off"
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        moment = _moment_for_timeline(root, timeline)
        owner = (_owner(lifecycle.list_records(root), moment.moment_id)
                 if moment is not None else None)
        if owner is None:
            edit = current(root, KIND_EDIT)
            if edit is None:
                return None
            target = ("review" if withdrawn and edit["state"] == "approved"
                      else "approved" if not withdrawn
                      and edit["state"] == "review" else edit["state"])
            return lifecycle.transition(root, edit["assignment_id"], target,
                                        event=event,
                                        detail={"timeline": timeline})
        reel = owner["reels"][moment.moment_id]
        if withdrawn and reel["status"] == "signed_off":
            reel["status"] = "built"
        elif not withdrawn and reel["status"] == "built":
            reel["status"] = "signed_off"
        return _settle(root, owner, event, {"reels": [reel["number"]]})


# ── Deliver ─────────────────────────────────────────────────────────


def begin_deliver(project, reel_number: int) -> Act | None:
    """Gate and checkpoint one delivery. None when no assignment owns it."""
    root = _root(project)
    if not lifecycle.list_records(root):
        return None
    recover_interrupted(root)
    moment = next((m for m in _moments(root)
                   if int(m.number) == int(reel_number)), None)
    if moment is None:
        return None
    owner = _owner(lifecycle.list_records(root), moment.moment_id)
    if owner is None:
        return None
    reel = owner["reels"][moment.moment_id]
    if reel.get("delivered_approved") and reel["status"] not in APPROVED_WORK:
        _require_decision(
            root, owner, "reel.deliver",
            f"delivering would overwrite the approved delivery of "
            f"{moment.timeline_name} with a build nobody approved",
            f"{moment.timeline_name} was rebuilt after its approved "
            f"delivery and has not been signed off again. Deliver the "
            f"new version anyway?",
            {"replace_approved_work": True})
    _refuse_concurrent(owner)
    key = _begin(root, owner, "deliver", "reel.deliver", [int(moment.number)])
    act = Act(root, "deliver", "reel.deliver")
    act.checkpoints.append((owner["assignment_id"], key, [moment.moment_id]))
    return act


# ── Preview ─────────────────────────────────────────────────────────


def on_preview(project, reel_number: int, preview: dict) -> dict | None:
    """A watchable preview of a built reel exists (`ren preview`).

    Records the file on the reel's entry - the newest preview replaces
    the last, since only the newest matches the reel - and appends one
    `preview` history entry naming it and whether it was rendered or a
    cache hit. A preview moves no state: watching is not approving.
    None when no assignment owns the reel.
    """
    root = _root(project)
    if not lifecycle.list_records(root):
        return None
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        moment = next((m for m in _moments(root)
                       if int(m.number) == int(reel_number)), None)
        owner = (_owner(lifecycle.list_records(root), moment.moment_id)
                 if moment is not None else None)
        if owner is None:
            return None
        entry = {key: preview[key] for key in (
            "path", "content_key", "engine", "outcome", "duration_seconds")
            if key in preview}
        entry["at"] = _now()
        owner["reels"][moment.moment_id]["preview"] = entry
        lifecycle.save(root, owner)
        return lifecycle.transition(
            root, owner["assignment_id"], owner["state"], event="preview",
            detail={"reels": [int(reel_number)], "path": entry.get("path"),
                    "outcome": entry.get("outcome")})


# ── Edit ────────────────────────────────────────────────────────────


def begin_edit(project, request: str | None = None) -> Act:
    """`ren edit` starts or continues the project's edit assignment."""
    root = _root(project)
    recover_interrupted(root)
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        record = current(root, KIND_EDIT)
        if record is not None and record["state"] == "approved":
            _require_decision(
                root, record, "render.build",
                "an edit run would replace the approved edit",
                "This edit is already approved. Re-run the edit and "
                "replace it?", {})
        if record is None:
            record = lifecycle.create(root, _new_id(KIND_EDIT),
                                      kind=KIND_EDIT, request=request)
        if record["state"] == "brief":
            record = lifecycle.transition(root, record["assignment_id"],
                                          "draft", event="edit")
        _refuse_concurrent(record)
        act = Act(root, "edit", "render.build")
        key = _begin(root, record, "edit", act.operation_id, [])
        act.checkpoints.append((record["assignment_id"], key, []))
    return act


def end_edit(act: Act, *, returncode: int, summary_status: str | None) -> None:
    """A complete run is a built edit; a partial one only closes the act."""
    if summary_status == "SUCCESS" and returncode == 0:
        end_act(act, ok=True)
        return
    if returncode == 0:
        for assignment_id, key, _ in act.checkpoints:
            def _close(record, key=key):
                _finish_checkpoint(record, key, "completed",
                                   f"run ended {summary_status or 'partial'}")
            lifecycle.update(act.project, assignment_id, _close)
        return
    end_act(act, ok=False, status="refused" if returncode == 4 else "failed",
            detail=f"run exited {returncode} ({summary_status or 'no summary'})")


# ── Revise ──────────────────────────────────────────────────────────


def revision_owner(project, reel: str = "") -> dict | None:
    """The assignment a revision note on `reel` belongs to, if one does.

    A note on a reel some request owns revises THAT request - it is the
    same work, asked again. A note naming no owned reel belongs to the
    project's open request of its kind. Read only.
    """
    root = _root(project)
    moment = _moment_for_timeline(root, reel) if reel else None
    if moment is not None:
        owner = _owner(lifecycle.list_records(root), moment.moment_id)
        if owner is not None:
            return owner
    return current(root, KIND_REELS if _moments(root) else KIND_EDIT)


def named_assignment(project, assignment_id: str) -> dict:
    """The request an id names, or a refusal naming what to do instead.

    One place, because a caller that names a request and a reader that
    lists the requests must agree on what an id means.
    """
    record = lifecycle.load(_root(project), assignment_id)
    if record is None:
        raise RenRefusal(
            f"assignment {assignment_id} has no record",
            "a correction is attached to the request it updates, and that "
            "request has to exist",
            f"run `ren assignment-state {shlex.quote(_root(project))}` to "
            f"list the requests on record")
    return record


def attach_revision(project, reel: str = "", *, request: str,
                    assignment_id: str = "") -> dict:
    """The assignment a revision that is about to change work joins.

    Called when the change is made, never when the note arrives: a note
    that only asks a question changes no assignment. Joining an owner
    writes no history entry - the entry is the revision itself, when it
    lands (`end_act`). A note no request owns opens one, which is an act
    and is recorded as `revise`.

    A NAMED assignment is honoured as given. Guessing the owner is right
    when nobody says otherwise, and wrong when the person says which
    request they mean: a focused correction must update THAT task, not
    the one the reel happens to belong to.
    """
    root = _root(project)
    if assignment_id:
        return named_assignment(project, assignment_id)
    owner = revision_owner(project, reel)
    if owner is not None:
        return owner
    return open_request(root, KIND_REELS if _moments(root) else KIND_EDIT,
                        event="revise", request=request)


def begin_revision(project, assignment_id: str, reel: str = "") -> Act:
    """Gate and checkpoint a change made to built work in place.

    Revising a signed-off or delivered reel (or an approved edit) changes
    approved work, so it goes through D2 exactly as a rebuild does.
    """
    root = _root(project)
    recover_interrupted(root)
    record = lifecycle.load(root, assignment_id)
    if record is None:
        raise RenRefusal(
            f"assignment {assignment_id} has no record",
            "a revision is checkpointed on the assignment it revises",
            "run `ren revise` again so it attaches the note first")
    moment = _moment_for_timeline(root, reel) if reel else None
    entry = ((record.get("reels") or {}).get(moment.moment_id)
             if moment is not None else None)
    approved = (entry["status"] in APPROVED_WORK if entry is not None
                else record.get("kind") != KIND_REELS
                and record["state"] in ("approved", "delivered"))
    if approved:
        name = moment.timeline_name if moment is not None else "This edit"
        _require_decision(
            root, record, "reel.touchup",
            f"revising would change approved work: {name}",
            f"{name} is already approved. Change it anyway?",
            {"replace_approved_work": True})
    act = Act(root, "revise", "reel.touchup")
    with lifecycle.assignment_lock(root, _SPINE_LOCK):
        record = lifecycle.load(root, assignment_id)
        _refuse_concurrent(record)
        numbers = [int(entry["number"])] if entry is not None else []
        key = _begin(root, record, "revise", act.operation_id, numbers)
        act.checkpoints.append(
            (assignment_id, key,
             [moment.moment_id] if entry is not None else []))
    return act


# ── Where the host reads it ─────────────────────────────────────────


def resume_point(project, record: dict) -> dict:
    """The next act this assignment is waiting on, as a runnable verb."""
    root = _root(project)
    quoted = shlex.quote(root)
    key = record.get("checkpoint")
    checkpoint = (record.get("operations") or {}).get(key) if key else None
    if checkpoint and checkpoint.get("status") in ("running", "interrupted"):
        request = checkpoint.get("request") or {}
        verb = request.get("verb")
        reels = request.get("reels") or []
        if verb is None:
            # A host-submitted operation: the host resumes it through
            # the envelope's recorded request, not a verb.
            return {"action": "resume_operation", "command": None,
                    "why": f"operation {checkpoint.get('operation_id')} did "
                           f"not finish; resubmit its recorded request"}
        if verb == "deliver" and reels:
            command = f"ren deliver {quoted} {reels[0]}"
        elif verb == "revise":
            command = f"ren revise {quoted}"
        elif verb == "build":
            command = f"ren build {quoted}" + "".join(
                f" --only-reel {n}" for n in reels)
        else:
            command = f"ren edit {quoted}"
        return {"action": f"rerun_{verb}", "command": command,
                "why": f"the last {verb} did not finish; the assignment is "
                       f"still {record['state']}"}
    if record.get("kind") == KIND_EDIT:
        return {
            "brief": {"action": "edit", "command": f"ren edit {quoted}",
                      "why": "the edit has not run yet"},
            "draft": {"action": "edit", "command": f"ren edit {quoted}",
                      "why": "the edit has not finished building"},
            "review": {"action": "sign_off",
                       "command": f"ren sign-off {quoted} <timeline>",
                       "why": "the edit is built and waiting for approval"},
        }.get(record["state"], {"action": "none", "command": None,
                                "why": f"the edit is {record['state']}"})
    reels = sorted((record.get("reels") or {}).values(),
                   key=lambda r: r["number"])
    by_status = {status: [r for r in reels if r["status"] == status]
                 for status in ("proposed", "approved", "built", "signed_off")}
    if by_status["approved"]:
        return {"action": "build", "command": f"ren build {quoted}",
                "why": "approved reels are waiting to be built: " +
                       ", ".join(r["name"] for r in by_status["approved"])}
    if by_status["built"]:
        reel = by_status["built"][0]
        watch = (reel.get("preview") or {}).get("path")
        return {"action": "sign_off",
                "command": f"ren sign-off {quoted} {shlex.quote(reel['name'])}",
                "why": f"{reel['name']} is built and waiting for approval"
                       + (f"; watch {watch}, or every reel of the round "
                          f"with `ren round-page {quoted} --open`"
                          if watch else
                          f"; `ren preview {quoted}` makes a file to watch")}
    if by_status["signed_off"]:
        reel = by_status["signed_off"][0]
        return {"action": "deliver",
                "command": f"ren deliver {quoted} {reel['number']}",
                "why": f"{reel['name']} is approved and not yet delivered"}
    if by_status["proposed"]:
        return {"action": "approve", "command": _approve_command(quoted),
                "why": "proposed reels are waiting for a ruling: " +
                       ", ".join(r["name"] for r in by_status["proposed"])}
    return {"action": "none", "command": None,
            "why": f"the assignment is {record['state']}; a new note reopens it"}


def _approve_command(quoted: str) -> str | None:
    """`ren approve` when the front door has it; otherwise there is none."""
    from ren.commands import VERBS

    if any(verb.name == "approve" for verb in VERBS):
        return f"ren approve {quoted} <reel numbers>"
    return None
