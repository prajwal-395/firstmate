"""Project-graph operations family: event-log operation nodes."""
from __future__ import annotations

from pathlib import Path

from .core import OPERATION, add_node

def _event_log_path(project_folder: str) -> Path | None:
    """Where the project's event log lives, or None when unknown.

    Resolved through the log's own module - the graph never hardcodes
    the log's location, for the same reason it never hardcodes the
    manifest's (`_assembly_manifest_path`).
    """
    try:
        from library.tools import event_log
    except ImportError:
        from tools import event_log
    try:
        return Path(event_log.log_path(project_folder))
    except Exception:
        return None


def _derive_event_nodes(graph: dict, project_folder: str) -> None:
    """Operation nodes from the project's event log.

    The event log is the operation history the Fabric spec asks for:
    every lease a worker took, every checkpoint it left, every reel it
    signed off. The graph answers content questions ("what covers this
    span"); without these nodes it cannot answer "what did the workers
    DO" - which is the question a resumed task and the scheduler ask.
    Each event becomes one node keyed by its sequence number, carrying
    the event verbatim, so the log stays the canonical record and the
    graph never re-describes an operation. A project with no log yet
    contributes nothing - the graph builds from whatever exists.
    """
    try:
        from library.tools import event_log
    except ImportError:
        from tools import event_log
    try:
        events = event_log.read_events(project_folder)
    except (OSError, ValueError):
        return
    for event in events:
        if not isinstance(event, dict):
            continue
        seq = event.get("seq")
        if seq is None:
            continue
        add_node(graph, {
            "node_id": f"operation:{seq}",
            "node_type": OPERATION,
            "label": f"{event.get('type')} {seq}",
            "payload": {
                "seq": seq,
                "at": event.get("at"),
                "host": event.get("host"),
                "task": event.get("task"),
                "type": event.get("type"),
                "fields": event.get("fields") or {},
            },
        })
