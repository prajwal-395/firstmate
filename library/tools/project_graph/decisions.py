"""Project-graph decision family: edit-graph and intent-model nodes."""
from __future__ import annotations
try:
    from library.tools import (
        edit_graph,
        intent_model,
    )
except ImportError:
    from tools import (
        edit_graph,
        intent_model,
    )
from .core import (
    COVERS,
    EDIT,
    REFERENCE,
    SERVES,
    _as_dict,
    _as_list,
    _load_state,
    add_edge,
    add_node,
)

def _derive_edit_graph_nodes(graph: dict, project_folder: str) -> None:
    """Edit decision nodes from the edit graph, with their intent and
    evidence links as edges."""
    state = _load_state(project_folder)
    if not state:
        return
    egraph = edit_graph.build_graph(state)
    for enode in egraph.get("nodes") or []:
        node_id = enode["node_id"]
        add_node(graph, {
            "node_id": f"edit:{node_id}",
            "node_type": EDIT,
            "label": enode.get("label") or node_id,
            "payload": {
                "edit_node_id": node_id,
                "node_type": enode.get("node_type"),
                "producer": enode.get("producer"),
                "intent_refs": enode.get("intent_refs") or [],
                "evidence_refs": enode.get("evidence_refs") or [],
                "constraints": enode.get("constraints") or {},
                "depends_on": enode.get("depends_on") or [],
                "edit_payload": enode.get("payload") or {},
            },
        })
        for intent_ref in enode.get("intent_refs") or []:
            add_edge(graph, {
                "from": f"edit:{node_id}",
                "to": f"intent:{intent_ref}",
                "type": SERVES,
                "payload": {},
            })
        for evidence_ref in enode.get("evidence_refs") or []:
            parts = evidence_ref.split(":", 1)
            if len(parts) == 2 and parts[0] == "clip_catalog":
                add_edge(graph, {
                    "from": f"edit:{node_id}",
                    "to": f"shot:{parts[1]}",
                    "type": COVERS,
                    "payload": {"evidence": evidence_ref},
                })


def _derive_intent_nodes(graph: dict, project_folder: str) -> None:
    """Intent nodes from the intent model."""
    state = _load_state(project_folder)
    if not state:
        return
    model = _as_dict(state.get("intent_model"))
    if not model:
        return
    for beat in intent_model.beats(model):
        beat_id = beat.get("beat_id")
        if not beat_id:
            continue
        add_node(graph, {
            "node_id": f"intent:beat:{beat_id}",
            "node_type": REFERENCE,
            "label": f"beat {beat_id}",
            "payload": {
                "beat_id": beat_id,
                "purpose": beat.get("purpose"),
                "setup_for": beat.get("setup_for"),
                "payoff_of": beat.get("payoff_of"),
                "span": beat.get("span"),
            },
        })
    for moment in intent_model.key_moments(model):
        moment_id = moment.get("moment_id")
        if not moment_id:
            continue
        add_node(graph, {
            "node_id": f"intent:moment:{moment_id}",
            "node_type": REFERENCE,
            "label": f"key moment {moment_id}",
            "payload": {
                "moment_id": moment_id,
                "description": moment.get("description"),
                "clip_id": moment.get("clip_id"),
                "source_start": moment.get("source_start"),
                "source_end": moment.get("source_end"),
            },
        })
