"""Project-graph analysis family: temporal-index and VFX detections."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from library.tools import (
        edit_graph,
        source_memory,
    )
except ImportError:
    from tools import (
        edit_graph,
        source_memory,
    )
from .core import (
    COLOUR_READING,
    DEAD_SECTION,
    ONSET_CLASS,
    VFX_DETECTION,
    _as_list,
    _load_state,
    add_node,
)

def _derive_analysis_nodes(graph: dict, project_folder: str,
                           catalog: list, recorded: dict) -> None:
    """Analysis nodes from the temporal index and vfx detections.

    Colour readings, onset classes and dead sections come from each
    clip's temporal index file. VFX detections come from the edit
    graph's vfx nodes. A clip with no temporal index contributes
    nothing - the graph builds from whatever exists.
    """
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None:
            continue
        index = _read_temporal_index(
            project_folder, clip_id, clip.get("source_file") or "")
        if not index:
            continue
        _add_colour_readings(graph, clip_id, index)
        _add_onset_classes(graph, clip_id, index)
        _add_dead_sections(graph, clip_id, index)

    _add_vfx_detections(graph, project_folder)


def _read_temporal_index(project_folder: str, clip_id: str,
                         source_file: str) -> dict | None:
    base = Path(project_folder)
    stem = Path(source_file).stem if source_file else clip_id
    for sub in (
        base / "pipeline_output" / "steps" / "1_04_temporal_index" / "index",
        base / "raw" / "analysis" / "temporal_index",
    ):
        if not sub.is_dir():
            continue
        for idx_file in sorted(sub.glob("clip_*.json")):
            try:
                idx = json.loads(idx_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if stem in idx.get("source_file", ""):
                return idx
    return None


def _add_colour_readings(graph: dict, clip_id: str, index: dict) -> None:
    curves = index.get("color_curves") or {}
    hues = curves.get("hue_values") or []
    if not hues:
        return
    add_node(graph, {
        "node_id": f"colour:{clip_id}",
        "node_type": COLOUR_READING,
        "label": f"colour readings for {clip_id}",
        "payload": {
            "clip_id": clip_id,
            "hue_values": hues,
            "brightness_values": curves.get("brightness_values") or [],
            "temperature_values": curves.get("temperature_values") or [],
        },
    })


def _add_onset_classes(graph: dict, clip_id: str, index: dict) -> None:
    onsets = index.get("onset_classes") or []
    if not onsets:
        return
    add_node(graph, {
        "node_id": f"onset:{clip_id}",
        "node_type": ONSET_CLASS,
        "label": f"onset classes for {clip_id}",
        "payload": {
            "clip_id": clip_id,
            "onsets": onsets,
        },
    })


def _add_dead_sections(graph: dict, clip_id: str, index: dict) -> None:
    unusable = index.get("unusable_ranges") or []
    if not unusable:
        return
    add_node(graph, {
        "node_id": f"dead:{clip_id}",
        "node_type": DEAD_SECTION,
        "label": f"dead sections for {clip_id}",
        "payload": {
            "clip_id": clip_id,
            "unusable_ranges": unusable,
        },
    })


def _add_vfx_detections(graph: dict, project_folder: str) -> None:
    state = _load_state(project_folder)
    if not state:
        return
    egraph = edit_graph.build_graph(state)
    for enode in egraph.get("nodes") or []:
        if enode.get("node_type") != edit_graph.VFX:
            continue
        payload = enode.get("payload") or {}
        add_node(graph, {
            "node_id": f"vfx:{enode['node_id']}",
            "node_type": VFX_DETECTION,
            "label": enode.get("label") or enode["node_id"],
            "payload": {
                "edit_node_id": enode["node_id"],
                "clip_id": payload.get("clip_id"),
                "position": payload.get("position"),
                "effect": payload.get("effect"),
            },
        })
