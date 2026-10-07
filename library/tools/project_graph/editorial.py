"""Project-graph editorial family: compile-manifest findings as nodes."""
from __future__ import annotations

import json
from pathlib import Path

from .core import (
    COVERS,
    EDIT,
    EDITORIAL_FINDING,
    FINDING_DEAD_SECTION,
    FINDING_DEAD_TRIM,
    FINDING_JL_DECISION,
    FINDING_JL_PLACEMENT,
    FINDING_REACTION_CUTAWAY,
    REACTS_TO,
    _as_list,
    add_edge,
    add_node,
    edges_from,
    node,
)

def _assembly_manifest_path(project_folder: str) -> Path | None:
    """Where the compile manifest lives, or None when unknown.

    Resolved through the project layout - the manifest's directory
    names the step that wrote it, so the graph never hardcodes it.
    """
    try:
        from library.tools.project_layout import ProjectLayout
    except ImportError:
        from tools.project_layout import ProjectLayout
    try:
        return ProjectLayout(project_folder).step_dir("compile_manifest") \
            / "assembly_manifest.json"
    except Exception:
        return None


def _read_assembly_manifest(project_folder: str) -> dict:
    """The compile manifest, or {} when the compile has not run.

    A project the compile never reached has no editorial findings -
    the graph builds from whatever exists, like every other input.
    """
    path = _assembly_manifest_path(project_folder)
    if path is None or not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _spine_block_clips(graph: dict) -> dict:
    """`{str(position): clip_id}` for the edit graph's spine blocks.

    Editorial findings name block positions, not clips; the spine
    blocks recorded in the graph resolve a position to the clip
    playing there.
    """
    clips = {}
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDIT:
            continue
        payload = candidate["payload"].get("edit_payload") or {}
        if candidate["payload"].get("node_type") != "spine_block":
            continue
        position = payload.get("position")
        clip_id = payload.get("clip_id")
        if position is not None and clip_id:
            clips[str(position)] = clip_id
    return clips


def _finding_clips(kind: str, record: dict, spine_clips: dict) -> list:
    """The clips a finding is about, in graph terms.

    A dead section names its source clip; a trim, a J/L placement and
    a J/L decision name the spine position they land on, resolved to
    the clip playing there; a reaction cutaway names both the speech
    clip and the reaction clip. A position with no recorded block
    resolves to nothing - never to a guessed clip.
    """
    if kind == FINDING_REACTION_CUTAWAY:
        return [c for c in (record.get("speech_clip_id"),
                            record.get("reaction_clip_id")) if c]
    if kind == FINDING_DEAD_SECTION and record.get("clip_id"):
        return [record["clip_id"]]
    position = record.get("block_position",
                          record.get("cut_point_position"))
    if position is not None and str(position) in spine_clips:
        return [spine_clips[str(position)]]
    return []


def _derive_editorial_nodes(graph: dict, project_folder: str) -> None:
    """Editorial finding nodes from the compile manifest.

    The compile reports dead sections, proposed trims, J/L placements
    with the plan's decisions on each, and selected reaction cutaways.
    Each becomes a node carrying the finding verbatim, so the event
    log and the version work can reference a finding by node id
    instead of re-describing a timeline span. A finding links to the
    shots it names with a COVERS edge; a reaction cutaway links to
    the reacting person with REACTS_TO - the spec's "camera shows
    person B reacting". A link whose target is not in the graph is
    simply absent.
    """
    manifest = _read_assembly_manifest(project_folder)
    if not manifest:
        return
    spine_clips = _spine_block_clips(graph)
    sources = (
        (FINDING_DEAD_SECTION, manifest.get("dead_sections")),
        (FINDING_DEAD_TRIM, manifest.get("dead_section_trims")),
        (FINDING_JL_PLACEMENT, manifest.get("jl_placements")),
        (FINDING_JL_DECISION, manifest.get("jl_placement_results")),
        (FINDING_REACTION_CUTAWAY, manifest.get("reaction_cutaways")),
    )
    for kind, entries in sources:
        for index, entry in enumerate(_as_list(entries)):
            if not isinstance(entry, dict):
                continue
            node_id = f"editorial:{kind}:{index}"
            add_node(graph, {
                "node_id": node_id,
                "node_type": EDITORIAL_FINDING,
                "label": f"{kind} {index}",
                "payload": {
                    "kind": kind,
                    "record": entry,
                },
            })
            for clip_id in _finding_clips(kind, entry, spine_clips):
                if node(graph, f"shot:{clip_id}"):
                    add_edge(graph, {
                        "from": node_id,
                        "to": f"shot:{clip_id}",
                        "type": COVERS,
                        "payload": {"finding": kind},
                    })
            if kind == FINDING_REACTION_CUTAWAY:
                reacting = entry.get("reacting_person_id")
                if reacting and node(graph, reacting):
                    add_edge(graph, {
                        "from": node_id,
                        "to": reacting,
                        "type": REACTS_TO,
                        "payload": {},
                    })
