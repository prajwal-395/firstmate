"""Project-graph builder: derive the whole graph from project state."""
from __future__ import annotations
try:
    from library.tools import (
        source_memory,
    )
except ImportError:
    from tools import (
        source_memory,
    )
from .analysis import _derive_analysis_nodes
from .audio import (
    _build_audio_events,
    _build_room_tone_dropouts,
    _derive_audio_edges,
)
from .colour import _build_colour, _colour_for_clip
from .core import (
    add_node,
    new_graph,
)
from .decisions import _derive_edit_graph_nodes, _derive_intent_nodes
from .editorial import _derive_editorial_nodes
from .footage import (
    _build_persons,
    _build_scenes,
    _build_shots,
    _build_transcript_segments,
    _build_world_models,
    _derive_person_edges,
    _derive_scene_edges,
)
from .history import (
    _derive_branch_nodes,
    _derive_commit_nodes,
    _derive_tag_nodes,
    _derive_version_nodes,
)
from .operations import _derive_event_nodes
from .permissions import _derive_participant_nodes
from .proposals import _derive_proposal_nodes
from .variants import _derive_variant_nodes

# ── The builder ───────────────────────────────────────────────────


def build_graph(project_folder: str, root: Path | None = None) -> dict:
    """Derive the project graph from the project's existing state.

    Reads the catalog, world models, person tracks, edit graph,
    intent model, compile manifest, versions ledger and variant
    explorations. A state key the run has not produced is simply absent
    - the graph builds from whatever exists.
    """
    graph = new_graph()
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)

    persons = _build_persons(project_folder, root)
    shots = _build_shots(catalog)
    colour = _build_colour(project_folder)
    world_models = _build_world_models(project_folder, catalog, recorded, root)
    scenes = _build_scenes(world_models)
    transcript_segments = _build_transcript_segments(world_models)
    audio_events = _build_audio_events(world_models)
    audio_events += _build_room_tone_dropouts(project_folder, catalog)

    for person in persons:
        add_node(graph, person)
    for shot in shots:
        if colour:
            record = _colour_for_clip(colour, shot["payload"].get("clip_id"))
            if record:
                shot["payload"]["colour"] = record
        add_node(graph, shot)
    for scene in scenes:
        add_node(graph, scene)
    for segment in transcript_segments:
        add_node(graph, segment)
    for event in audio_events:
        add_node(graph, event)

    _derive_person_edges(graph, persons, transcript_segments)
    _derive_scene_edges(graph, scenes)
    _derive_audio_edges(graph, audio_events)
    _derive_edit_graph_nodes(graph, project_folder)
    _derive_intent_nodes(graph, project_folder)
    _derive_analysis_nodes(graph, project_folder, catalog, recorded)
    _derive_version_nodes(graph, project_folder)
    _derive_editorial_nodes(graph, project_folder)
    _derive_variant_nodes(graph, project_folder)
    _derive_commit_nodes(graph, project_folder)
    _derive_event_nodes(graph, project_folder)
    _derive_participant_nodes(graph, project_folder)
    _derive_proposal_nodes(graph, project_folder)
    _derive_tag_nodes(graph, project_folder)
    _derive_branch_nodes(graph, project_folder)

    return graph
