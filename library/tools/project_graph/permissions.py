"""Project-graph permissions family: fabric-roster participant nodes."""
from __future__ import annotations

from .core import PARTICIPANT, add_node


def _derive_participant_nodes(graph: dict, project_folder: str) -> None:
    """Participant nodes from the fabric permission roster.

    Each federated participant is a node carrying the level they hold,
    so the graph answers "who may approve this" without reading the
    roster file directly. A project that never federated registers no
    nodes - absence of the roster is absence of participants, not an
    error.
    """
    try:
        from library.tools import fabric_permissions
        roster = fabric_permissions.read_roster(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for name in sorted((roster.get("participants") or {})):
        entry = (roster.get("participants") or {}).get(name) or {}
        if entry.get("level") not in fabric_permissions.LEVELS:
            continue
        add_node(graph, {
            "node_id": f"participant:{name}",
            "node_type": PARTICIPANT,
            "label": name,
            "payload": {
                "participant": name,
                "level": entry.get("level"),
            },
        })
