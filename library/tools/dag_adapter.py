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

What it refuses
---------------
A capability with no legacy node raises `NoLegacyNode` rather than
answering with an empty set.  Requirements are still keyed by node id, so
"no node" would read as "requires nothing, produces nothing" - a
confidently wrong answer that would let a composer schedule it anywhere.
No capability lacks a node today; the refusal is what a capability-first
entry must meet until requirements are keyed by capability id.
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


def requirements_consumed(op: Any) -> tuple:
    """Every requirement whose consumers include the capability's node."""
    from library.tools import requirements
    node = node_of(op)
    return tuple(r for r in requirements.all_requirements()
                 if node in r.consumers)


def requirements_produced(op: Any) -> tuple:
    """Every requirement whose producers include the capability's node."""
    from library.tools import requirements
    node = node_of(op)
    return tuple(r for r in requirements.all_requirements()
                 if node in r.produced_by)
