"""The task's plan: which operations it will run, what each waits for, and
which group it belongs to.

A plan is a set of NODES. A node names one registry operation, the
dependencies it must wait for, and optionally the group it belongs to.
Groups are how a task says "these four operations are one editorial move"
- they carry no order of their own, and a node in a group is still a node
with its own dependencies.

A dependency is satisfied when the node it names has COMPLETED. A node
whose dependency has not completed is not ready, and `ready` names the
ones that are. Nothing here runs anything: the plan says what the task
will do, and the runner decides what to run next.

The node also says WHAT IT RESTS ON: the evidence it consumes and the
decisions it applies. That is the link from source to choice to edit, and
`assignment_provenance.stamp` turns it into the provenance an operation
carries into execution.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence

from library.tools import assignment_lifecycle as lifecycle

NODE_STATUSES = ("planned", "running", "completed", "refused", "skipped")

TERMINAL_STATUSES = ("completed", "refused", "skipped")


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _plain(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if value.strip() != value or any(char in value for char in "/\\\0"):
        raise ValueError(f"{label} must be a plain identifier")
    return value


def _operations(record: Mapping) -> dict:
    plan = record.get("plan")
    if not isinstance(plan, Mapping):
        return {}
    nodes = plan.get("operations")
    return dict(nodes) if isinstance(nodes, Mapping) else {}


def _known_operation(operation_id: str) -> str:
    """The registry's own name for this operation, or a refusal."""
    from library.tools import operations

    try:
        operations.get(operation_id)
    except Exception as exc:
        raise ValueError(
            f"unknown operation {operation_id!r}: {exc}") from exc
    return operation_id


def add(project: str, assignment_id: str, *, node_id: str, operation_id: str,
        group: str = "", depends_on: Sequence[str] = (), label: str = "",
        evidence_ids: Sequence[str] = (),
        decision_ids: Sequence[str] = ()) -> dict:
    """Add one node to the task's plan.

    The node id is the unit: it names the plan entry, the dependency
    other nodes wait for, and the operation's checkpoint. Adding the same
    node twice with the same content is the same node; adding it with
    different content is a REFUSAL, because a plan that changes under a
    running operation is how a task loses track of what it is doing.
    """
    node_id = _plain(node_id, "node_id")
    _known_operation(operation_id)
    waits = [_plain(name, "dependency") for name in depends_on]
    if node_id in waits:
        raise ValueError(f"node {node_id!r} cannot depend on itself")
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        nodes = _operations(stored)
        record = {
            "node_id": node_id,
            "operation_id": operation_id,
            "group": (group or "").strip(),
            "label": (label or "").strip(),
            "depends_on": waits,
            "evidence_ids": [str(name) for name in evidence_ids],
            "decision_ids": [str(name) for name in decision_ids],
            "status": "planned",
            "detail": "",
        }
        prior = nodes.get(node_id)
        if prior is not None:
            comparable = {key: prior.get(key) for key in record}
            if comparable != record:
                raise ValueError(
                    f"plan node {node_id!r} is already planned with different "
                    f"content; replan it instead of redefining it")
            return prior
        # A dependency may name a node that is planned LATER: a plan is
        # declared in the order the reader thinks in, not the order the
        # work runs. What is refused is a CYCLE, which would make "ready"
        # meaningless.
        _assert_acyclic(nodes, node_id, waits)
        nodes[node_id] = {**record, "planned_at": _now()}
        stored["plan"] = {"operations": nodes}
        lifecycle.save(project, stored)
        return nodes[node_id]


def _assert_acyclic(nodes: Mapping, node_id: str, waits: Sequence[str]) -> None:
    """A dependency cycle would make "ready" meaningless; refuse it here."""
    seen = {node_id}
    frontier = list(waits)
    while frontier:
        current = frontier.pop()
        if current in seen:
            raise ValueError(
                f"plan dependency cycle through {current!r}")
        seen.add(current)
        node = nodes.get(current)
        if node:
            frontier.extend(node.get("depends_on", ()))


def plan(project: str, assignment_id: str) -> dict:
    """The task's plan: its nodes, in the order they were planned."""
    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return {"operations": {}}
    nodes = _operations(stored)
    return {"operations": {name: copy.deepcopy(node)
                           for name, node in nodes.items()}}


def node(project: str, assignment_id: str, node_id: str) -> dict | None:
    """One planned node, or None when the task did not plan it."""
    return _operations(lifecycle.load(project, assignment_id) or {}).get(node_id)


def find_node(project: str, assignment_id: str, *, node_id: str = "",
              operation_id: str = "") -> dict | None:
    """The node an operation belongs to.

    A node id names the node outright. An operation id names it only when
    it is unambiguous: two nodes running the same operation are two
    nodes, and guessing between them would attribute one edit's
    provenance to another.
    """
    nodes = _operations(lifecycle.load(project, assignment_id) or {})
    if node_id:
        return nodes.get(node_id)
    if not operation_id:
        return None
    matching = [node for node in nodes.values()
                if node.get("operation_id") == operation_id]
    return matching[0] if len(matching) == 1 else None


def mark(project: str, assignment_id: str, node_id: str, status: str, *,
         detail: str = "") -> dict:
    """Record what happened to a planned node."""
    if status not in NODE_STATUSES:
        raise ValueError(f"unknown plan node status {status!r}; expected one "
                         f"of {', '.join(NODE_STATUSES)}")
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        nodes = _operations(stored)
        if node_id not in nodes:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no plan node {node_id!r}")
        nodes[node_id]["status"] = status
        if detail:
            nodes[node_id]["detail"] = detail
        nodes[node_id][f"{status}_at"] = _now()
        stored["plan"] = {"operations": nodes}
        lifecycle.save(project, stored)
        return nodes[node_id]


def blocked_by(project: str, assignment_id: str) -> dict:
    """Each planned node and the dependencies it is still waiting for.

    A dependency on a node nobody planned is a dependency nobody can
    satisfy, so it waits like any other.
    """
    nodes = _operations(lifecycle.load(project, assignment_id) or {})
    waiting = {}
    for name, node in nodes.items():
        if node.get("status") in TERMINAL_STATUSES:
            continue
        unmet = [dep for dep in node.get("depends_on", ())
                 if nodes.get(dep, {}).get("status") != "completed"]
        if unmet:
            waiting[name] = unmet
    return waiting


def ready(project: str, assignment_id: str) -> list[dict]:
    """The planned nodes whose dependencies are met and that have not run."""
    nodes = _operations(lifecycle.load(project, assignment_id) or {})
    waiting = blocked_by(project, assignment_id)
    return [copy.deepcopy(node) for name, node in nodes.items()
            if name not in waiting and node.get("status") == "planned"]


def groups(project: str, assignment_id: str) -> dict:
    """The task's groups: a name, the nodes in it, and where each stands.

    Groups are DERIVED from the nodes, so a node's group and the group it
    appears in can never disagree.
    """
    nodes = _operations(lifecycle.load(project, assignment_id) or {})
    grouped: dict[str, dict] = {}
    for name, node in nodes.items():
        group = node.get("group") or ""
        if not group:
            continue
        entry = grouped.setdefault(group, {"group": group, "node_ids": [],
                                           "statuses": {}})
        entry["node_ids"].append(name)
        status = node.get("status", "planned")
        entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
    for entry in grouped.values():
        entry["node_ids"].sort()
        if entry["statuses"].get("completed") == len(entry["node_ids"]):
            entry["status"] = "completed"
        elif entry["statuses"].get("refused"):
            entry["status"] = "refused"
        elif entry["statuses"].get("running"):
            entry["status"] = "running"
        else:
            entry["status"] = "planned"
    return grouped


def unlinked(project: str, assignment_id: str) -> list[str]:
    """The nodes that name evidence or decisions the task never recorded."""
    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return []
    known_evidence = {item.get("id")
                      for item in (stored.get("evidence") or [])}
    known_decisions = {item.get("id")
                       for item in (stored.get("decisions") or [])}
    missing = []
    for name, node in _operations(stored).items():
        for wanted in node.get("evidence_ids", ()):
            if wanted not in known_evidence:
                missing.append(f"{name}: evidence {wanted}")
        for wanted in node.get("decision_ids", ()):
            if wanted not in known_decisions:
                missing.append(f"{name}: decision {wanted}")
    return missing
