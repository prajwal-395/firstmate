"""The DAG, reached as a COMPATIBILITY ADAPTER beneath the capability registry.

Why this exists
---------------
A capability's identity is its id (`library/tools/capabilities.py`).  The
DAG node it used to be addressed by is legacy metadata - but metadata a
great deal of current state is still KEYED by: `step_outputs`, the two
step ledgers, `step_errors`, the review gates, `pipeline_output/steps/`,
the derived requirements (`state.<consumer>.<key>`), the run status and
the traceback.  None of those can be dropped yet, so the node is still
reached - through this module and nowhere else.

The migration order this serves (punch list items 4 and 5): make the
capability id authoritative first, keep writing the legacy node fields
until nothing reads them, then remove the graph.  Every "which node?"
question a capability-keyed caller asks is answered here, so the day a
reader stops needing the node is the day one function here goes, rather
than an audit of every `owning_node` read in the tree.

Effects are not answered by the node: a capability declares what it
`produces`, and a node's effect is DERIVED from its capabilities
(`node_effects`) - a computed view, never a declaration.

Requirements are read BY CAPABILITY ID: `requirement_index` turns each
requirement's legacy node tuples (`produced_by`, `consumers`) into the
capability ids that produce and consume it, once, and every capability
-keyed reader (`capabilities.producers_of` / `consumers_of`, the
composer, a standalone refusal, the override record) reads that.  The
node tuples stay on the requirement for the runner, which executes
nodes, and for a node no capability covers yet.

What it refuses
---------------
A capability with no legacy node raises `NoLegacyNode` rather than
answering with an empty set.  The index derives a capability's
requirements from its node, so "no node" would read as "requires
nothing, produces nothing" - a confidently wrong answer that would let a
composer schedule it anywhere.  No capability lacks a node today.
"""
from __future__ import annotations

from typing import Any

from library.tools.ren_refusal import RenRefusal


class NoLegacyNode(RenRefusal):
    """A capability that has no DAG node was asked a node-keyed question."""


def node_of(op: Any) -> str:
    """The legacy DAG node a registry entry is attributed to.

    The one read of `Operation.owning_node` outside the registry's own
    definition: everything node-keyed (ledgers, `step_outputs`, the
    derived requirements, provenance's `step_id`) asks here.
    """
    node = getattr(op, "owning_node", "") or ""
    if not node:
        raise NoLegacyNode(
            f"capability {op.name!r} has no legacy DAG node",
            "requirements, the step ledgers and step_outputs are still "
            "keyed by node id, so a capability without one cannot be "
            "checked, gathered for or recorded yet - answering with "
            "nothing would read as 'requires nothing'",
            "attribute it to the node that owns its decision until its "
            "requirements are keyed by capability id")
    return node


def node_ids() -> frozenset:
    """Every node every process declares - the legacy identity space."""
    from library.tools import processes
    return frozenset(processes.node_owners())


def capabilities_at(node_id: str) -> tuple:
    """Every registered capability attributed to one legacy node."""
    from library.tools import operations
    return tuple(op for op in operations.all()
                 if getattr(op, "owning_node", "") == node_id)


def nodes_with_capabilities() -> frozenset:
    """The legacy nodes at least one capability can stand in for."""
    from library.tools import operations
    return frozenset(node_of(op) for op in operations.all())


def legacy_only_nodes() -> tuple:
    """Nodes reachable ONLY by running the DAG: no capability names them.

    These are the producers a composer cannot select and an operator
    cannot address by capability id - the remaining surface the DAG is
    the only route to.
    """
    return tuple(sorted(node_ids() - nodes_with_capabilities()))


def declaring_dag(op: Any) -> dict:
    """The graph that declares a capability's legacy node."""
    from library.tools import processes
    return processes.dag_declaring(node_of(op))


def _consumes(op: Any, r: Any) -> bool:
    """The requirement names its consumer NODE; the capability is asked
    it when it declares reading the requirement's `consumed_key`, or
    when no state key carries the requirement (the machine, an
    approval, a binding - asked of every capability of the node)."""
    return node_of(op) in r.consumers and (
        not r.consumed_key
        or r.consumed_key in (getattr(op, "consumes", ()) or ()))


def _produces(op: Any, r: Any) -> bool:
    """The requirement names its producer NODE (the DAG's data_mapping
    is still where producers come from) and the state key that node
    writes (`Requirement.key_at`); the capability is credited only when
    it declares that key.  An empty key is a whole-output requirement,
    met by any capability of the node that produces anything."""
    node = node_of(op)
    produces = set(getattr(op, "produces", ()) or ())
    return bool(produces) and node in r.produced_by and (
        r.key_at(node) in produces or not r.key_at(node))


def requirements_consumed(op: Any) -> tuple:
    """Every requirement the capability's declared `consumes` asks."""
    from library.tools import requirements
    return tuple(r for r in requirements.all_requirements()
                 if _consumes(op, r))


def requirements_produced(op: Any) -> tuple:
    """Every requirement the capability's declared `produces` satisfies."""
    from library.tools import requirements
    return tuple(r for r in requirements.all_requirements()
                 if _produces(op, r))


def requirement_index(ops=None, reqs=None) -> tuple:
    """`(consumers, producers)`: requirement name -> capability ids.

    The ONE place a requirement's node-keyed producers and consumers are
    turned into capability ids, in a single pass over the live registry
    (`capabilities.consumers_of` / `producers_of` are the readers).  Not
    cached: the composer and the tests read the LIVE registry.
    """
    from library.tools import operations, requirements
    ops = operations.all() if ops is None else ops
    reqs = requirements.all_requirements() if reqs is None else reqs
    consumers: dict = {r.name: [] for r in reqs}
    producers: dict = {r.name: [] for r in reqs}
    for op in ops:
        for r in reqs:
            if _consumes(op, r):
                consumers[r.name].append(op.name)
            if _produces(op, r):
                producers[r.name].append(op.name)
    return ({n: tuple(v) for n, v in consumers.items()},
            {n: tuple(v) for n, v in producers.items()})


def node_effects(node_id: str) -> tuple:
    """A legacy node's effect: DERIVED, the union of its capabilities'.

    No node declares an effect.  Each capability declares what it
    `produces`; this view exists for the adapter and legacy readers
    that still ask by node, and a node with no capability has none.
    """
    seen: dict = {}
    for op in capabilities_at(node_id):
        for r in requirements_produced(op):
            seen.setdefault(r.name, r)
    return tuple(seen.values())
