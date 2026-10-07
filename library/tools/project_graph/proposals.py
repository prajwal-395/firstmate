"""Project-graph proposals family: federation open-proposal nodes."""
from __future__ import annotations

from .core import PROPOSAL, add_node


def _derive_proposal_nodes(graph: dict, project_folder: str) -> None:
    """Proposal nodes from the federation's open proposals.

    Each undecided proposal is a node carrying its author, operation
    and rationale, so the graph answers "what awaits approval" without
    reading the event log directly. Decided proposals register no node:
    an accepted proposal's outcome is the merge commit it applied.
    """
    try:
        from library.tools import fabric_permissions
        from library.tools import event_log
        recorded = [event for event in event_log.read_events(project_folder)
                    if event.get("type") == event_log.PROPOSAL_RECORDED]
        accepted = {(event.get("fields") or {}).get("proposal_id")
                    for event in event_log.read_events(project_folder)
                    if event.get("type") == event_log.PROPOSAL_ACCEPTED}
    except (OSError, ValueError):
        return
    for event in recorded:
        fields = event.get("fields") or {}
        proposal_id = fields.get("proposal_id")
        if not proposal_id or proposal_id in accepted:
            continue
        operation = fields.get("operation") or {}
        add_node(graph, {
            "node_id": f"proposal:{proposal_id}",
            "node_type": PROPOSAL,
            "label": f"proposal by {fields.get('author')}",
            "payload": {
                "proposal_id": proposal_id,
                "author": fields.get("author"),
                "operation": operation.get("kind"),
                "timeline": operation.get("timeline"),
                "rationale": fields.get("rationale"),
            },
        })
