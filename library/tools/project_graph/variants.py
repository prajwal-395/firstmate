"""Project-graph variant family: variant-exploration branches."""
from __future__ import annotations

from pathlib import Path

from .core import VARIANT, _record_dir_digest, add_node

def _variant_explorations_digest(project_folder) -> str | None:
    """One digest over the variant exploration records, or None.

    Sorted record paths plus bytes - see `_record_dir_digest`. A
    project with no explorations reads as None, and a new or changed
    exploration changes the digest, so the cache rebuilds exactly when
    the variant set moves.
    """
    try:
        from library.tools import variant_exploration
        root = Path(variant_exploration.exploration_root(project_folder))
    except (OSError, ValueError):
        return None
    return _record_dir_digest(
        root, variant_exploration.EXPLORATION_FILENAME)


def _derive_variant_nodes(graph: dict, project_folder: str) -> None:
    """Variant-branch nodes from the variant explorations.

    Each variant of each RECORDED exploration is a node carrying its branch,
    base, direction and planning agent verbatim, so the critic - and
    any other lane - references a variant by node id instead of
    re-reading the exploration records. A planned-but-undispatched
    exploration gains no nodes: its branches do not exist yet, and a
    node for a branch that is not there would read as a comparison of
    nothing. Like the version nodes, these carry no edges: a variant links
    to nothing until the merge queue evaluates it. A project with no explorations
    gains no nodes.
    """
    try:
        from library.tools import variant_exploration
        records = variant_exploration.list_explorations(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for record in records:
        if not record.get("recorded", True):
            continue
        exploration = record.get("id") or ""
        for variant in record.get("variants") or ():
            name = variant.get("name") or ""
            # The node's timeline is the line the variant explores -
            # the exploration's timeline, not the branch double's own
            # name. The double's name rides on `branch`.
            node_timeline = record.get("timeline") or variant.get("timeline")
            add_node(graph, {
                "node_id": f"variant:{exploration[:12]}:{name}",
                "node_type": VARIANT,
                "label": f"variant {name}",
                "payload": {
                    "exploration": exploration,
                    "name": name,
                    "direction": variant.get("direction"),
                    "branch": variant.get("branch"),
                    "timeline": node_timeline,
                    "base": variant.get("base") or record.get("base"),
                "agent": record.get("agent"),
                "at": record.get("created_at"),
            },
        })
