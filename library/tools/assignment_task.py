"""Where the task stands, and the one thing standing in its way.

Two answers about a task that no record answers on its own.

WHERE IT STANDS is the project state the task acts on: the clip count
the catalog measured, the timelines the task's reels live on, and the
timeline generation the ledger last recorded. A piece that cannot be
measured is None - a state that says `None` says "not measured", and one
that says 0 would say "there is nothing here". The capture is a READING,
and a reading that cannot be taken must not fail the act that was
reading: the failure is recorded on the task, where a reader sees it.

WHAT IS IN THE WAY is the smallest actionable thing, not the whole
unfinished workflow. A task waiting on one dependency says which
dependency; a task waiting on one piece of evidence says which criterion;
a task waiting on a question says the question. The next verb, when
there is one, is the last thing the blocker names - and
`assignment_spine.resume_point` is where that verb comes from.

This module does not own the task's other answers. `assignment_view`
projects what Ren understood, proposed, applied and verified;
`assignment_contract` owns the acceptance contract and the completion
verdict. What lives here is the STANDING and the BLOCKER, because both
are read from the task's own records and neither is a projection of
somebody else's.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path

from library.tools import assignment_lifecycle as lifecycle

BLOCKER_KINDS = (
    "cancelled",
    "paused",
    "question",
    "dependency",
    "acceptance",
    "act",
    "none",
)


def _root(project) -> str:
    return str(Path(project).expanduser().resolve())


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def capture_project_state(project: str, assignment_id: str) -> dict:
    """Record the project state this task acts on.

    It is a reading, not a copy: the clip count the catalog measured, the
    timelines the task's reels are on, and the timeline generation the
    ledger last recorded. A piece that cannot be measured is None.
    """
    from library.tools.project_layout import ProjectLayout

    root = _root(project)
    state: dict = {"project": root, "captured_at": _now(),
                   "clips": None, "timeline": None, "generation": None}
    data_path = ProjectLayout(root).pipeline_data_path
    try:
        data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    clips = data.get("total_clips")
    if isinstance(clips, int):
        state["clips"] = clips
    record = lifecycle.load(root, assignment_id)
    if record is not None:
        reels = record.get("reels") or {}
        names = [reel.get("name") for reel in reels.values()
                 if isinstance(reel, Mapping) and reel.get("name")]
        if names:
            state["timeline"] = names[0] if len(names) == 1 else names
        state["generation"] = _last_generation(root)
    with lifecycle.assignment_lock(root, assignment_id):
        stored = lifecycle.load(root, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        stored["project_state"] = state
        lifecycle.save(root, stored)
    return state


def _last_generation(project: str) -> int | None:
    """The newest timeline generation the edit ledger recorded, if any."""
    from library.tools import edit_ledger

    try:
        rows = edit_ledger.load_rows(project)
    except Exception:  # noqa: BLE001 - an unreadable ledger is not a state
        return None
    generations = []
    for row in rows:
        params = row.get("params") or {}
        value = params.get("base_generation")
        try:
            generations.append(int(value))
        except (TypeError, ValueError):
            continue
    return max(generations) if generations else None


def blocker(project: str, assignment_id: str) -> dict:
    """The smallest thing standing in this task's way.

    One blocker, not the whole unfinished workflow. The order is the
    order a host can act in: a task that is over says so, a task that is
    held says how to release it, a task waiting on a question names the
    question, a task waiting on a dependency names the dependency, a task
    waiting on evidence names the criterion, and only then does the next
    verb come up.
    """
    from library.tools import (
        assignment_actions,
        assignment_contract,
        assignment_plan,
        assignment_spine,
    )

    root = _root(project)
    record = lifecycle.load(root, assignment_id)
    if record is None:
        return {"kind": "none", "action": "none", "command": None,
                "why": "the task has no record", "needs": []}
    quoted = shlex.quote(root)
    if record.get("state") == "cancelled":
        return {
            "kind": "cancelled", "action": "reopen",
            "command": (f"ren assignment {quoted} --id "
                        f"{shlex.quote(assignment_id)} --reopen"),
            "why": "the task was cancelled; reopening starts a new request "
                   "rather than resuming this one",
            "needs": []}
    if assignment_actions.is_paused(record):
        return {
            "kind": "paused", "action": "resume",
            "command": assignment_actions.resume_command(root, assignment_id),
            "why": (f"the task is paused: "
                    f"{(record.get('pause') or {}).get('reason', '')}"),
            "needs": []}
    refusal = record.get("last_refusal")
    if isinstance(refusal, Mapping) \
            and refusal.get("kind") == "missing_editorial_information":
        return {
            "kind": "question", "action": "answer",
            "command": refusal.get("fix") or None,
            "why": (f"the last refusal needs an editorial answer: "
                    f"{refusal.get('what', '')}"),
            "needs": list(refusal.get("unsatisfied") or [])}
    waiting = assignment_plan.blocked_by(root, assignment_id)
    if waiting:
        node_id = min(waiting)
        unmet = waiting[node_id]
        planned = assignment_plan.plan(root, assignment_id)["operations"]
        target = planned.get(node_id, {})
        return {
            "kind": "dependency", "action": "run_dependency",
            "command": None,
            "why": (f"plan node {node_id!r} is waiting on "
                    f"{', '.join(unmet)}"),
            "needs": [f"{name} (operation "
                      f"{planned.get(name, {}).get('operation_id', '?')})"
                      for name in unmet],
            "node_id": node_id,
            "operation_id": target.get("operation_id", "")}
    evaluated = assignment_contract.evaluate(root, assignment_id)
    if evaluated["status"] == "undeclared":
        return {
            "kind": "acceptance", "action": "declare_contract",
            "command": None,
            "why": ("the task has no acceptance contract: it never said "
                    "what done means, so nothing can say it is done"),
            "needs": []}
    missing = [row for row in evaluated["criteria"]
               if row["status"] == "missing"]
    if missing:
        row = missing[0]
        return {
            "kind": "acceptance", "action": "record_evidence",
            "command": None,
            "why": (f"acceptance criterion {row['id']!r} has no evidence: "
                    f"{row['statement']}"),
            "needs": [row["id"]]}
    resume = assignment_spine.resume_point(root, record)
    if resume.get("command"):
        return {
            "kind": "act", "action": resume.get("action", "next"),
            "command": resume["command"],
            "why": resume.get("why", ""), "needs": []}
    return {"kind": "none", "action": "none", "command": None,
            "why": "nothing is blocking this task", "needs": []}
