"""The Ren Project Graph: a persistent relational model of the project.

The gap this closes
-------------------
The world model (M9) is per-source: it joins the lanes within one source
into a timeline. The edit graph is per-run: it derives the decisions a
run's planners made. Neither is a project-level relational model that
spans sources and answers cross-source questions: "find the best
reaction shot" (Person A says X in shot 17 while Person B reacts in
shot 22), "alternate takes" (shot 431 covers the same content as shot
183), "coverage" (which shots cover this timeline span), "B-roll
matching" (this shot's content matches what's being said).

This package is that graph. It is DERIVED, never authored: it reads the
project's existing state (catalog, world models, person tracks, edit
graph, intent model) and emits the relational model. No new measurement.

The model
---------
Nodes are the project's entities:

    person              a tracked person (cross-source, from M3b + resolve_person_tracks)
    scene               a scene segment (M4)
    shot                a source clip (catalog entry)
    transcript_segment  a speech segment (M1 utterances)
    audio_event         a sound event (M5)
    graphic             a graphic overlay (edit_graph)
    reference           a reference to external material
    timeline            a built timeline/reel
    edit                an edit decision (edit_graph)
    variant             one variant branch of a variant exploration

Edges are typed relationships:

    says                person -> transcript_segment
    appears_in          person -> shot
    speaks_in           person -> shot
    shown_in            person -> shot (visible, not speaking)
    reacts_to           person -> person
    covers              shot -> timeline_span
    matches             shot -> transcript_segment
    music_changes_at    audio_event -> time
    contains            timeline -> shot
    serves              edit -> intent

Queries
-------
    find_reaction_shots(graph, person_id, around_t)
        Shots where another person is visible while person_id speaks
        around time t (in person_id's source timeline).
    find_alternate_takes(graph, clip_id)
        Other shots covering the same source content (same digest or
        overlapping source ranges).
    find_coverage(graph, timeline_start, timeline_end)
        Shots that cover a timeline span (from edit_graph spine blocks).
    find_broll_matches(graph, transcript_segment_id)
        Shots whose content matches a speech segment (from edit_graph
        broll_cover nodes).
    find_shot_colour(graph, clip_id)
        The colour track's registration for one shot: its measured
        row (exposure, white balance, contrast, saturation, skin
        vector), its delivered grade (the per-clip CDL) and its
        camera match against the scene's reference - or None where
        the run registered none for this shot.
    find_reaction_decisions(graph, clip_id, at_time)
        The reaction-cutaway EDIT DECISIONs the run recorded,
        optionally for one clip (speech or reaction) or one timeline
        time (inside the speech span the decision reacts to).
    find_variant_branches(graph, exploration, timeline)
        The variant branches the explorations recorded: which branch
        each variant owns, the base it forked and the direction it
        tries - the comparison surface the merge queue evaluates.

The colour registration
-----------------------
Shot nodes carry a `colour` payload when the colour track has run:
the measurement the grade step took from the frames, the grade it
delivered, and the camera match it derived between the angles on the
same scene. Other tracks read it through `find_shot_colour` - the
style track's "color palette, contrast" extraction, a future
scene-consistency track - without re-measuring the footage. A shot
the colour track never measured carries no `colour` key: an absent
measurement is absent, never a default.

This package imports nothing from steps or processes: it reads state
through the same readers every consumer has.

Layout
------
One module per derivation family so lanes stop rebasing past each
other: ``core`` (vocabulary + graph object), ``persistence`` (cache
file, guard, fingerprint, load_or_build), ``footage``, ``audio``,
``colour``, ``analysis``, ``editorial``, ``decisions``, ``history``,
``variants``, ``operations``, ``queries``, ``builder``, ``cli``. The
public surface stays exactly what the single module offered: ``from
library.tools.project_graph import X`` keeps working for every name
below.
"""
from __future__ import annotations

from .analysis import (
    _add_colour_readings,
    _add_dead_sections,
    _add_onset_classes,
    _add_vfx_detections,
    _derive_analysis_nodes,
    _read_temporal_index,
)
from .audio import (
    _build_audio_events,
    _build_room_tone_dropouts,
    _derive_audio_edges,
)
from .builder import build_graph
from .cli import main
from .colour import COLOUR_CAPABILITY, _build_colour, _colour_for_clip
from .core import (
    APPEARS_IN,
    AUDIO_EVENT,
    COVERS,
    COLOUR_READING,
    COMMIT,
    CONTAINS,
    DEAD_SECTION,
    EDIT,
    EDITORIAL_FINDING,
    FINDING_DEAD_SECTION,
    FINDING_DEAD_TRIM,
    FINDING_JL_DECISION,
    FINDING_JL_PLACEMENT,
    FINDING_KINDS,
    FINDING_REACTION_CUTAWAY,
    GRAPHIC,
    MATCHES,
    MUSIC_CHANGES_AT,
    NODE_TYPES,
    ONSET_CLASS,
    OPERATION,
    PARTICIPANT,
    PERSON,
    PROPOSAL,
    REACTS_TO,
    REFERENCE,
    RELATIONSHIP_TYPES,
    SAYS,
    SCENE,
    SERVES,
    SHOT,
    SHOWN_IN,
    SPEAKS_IN,
    TAG,
    BRANCH,
    TIMELINE,
    TRANSCRIPT_SEGMENT,
    VARIANT,
    VERSION,
    VFX_DETECTION,
    _as_dict,
    _as_list,
    _load_state,
    _overlap,
    _read_raw_bytes,
    add_edge,
    add_node,
    edges_from,
    edges_of_type,
    edges_to,
    new_graph,
    node,
)
from .decisions import _derive_edit_graph_nodes, _derive_intent_nodes
from .editorial import (
    _assembly_manifest_path,
    _derive_editorial_nodes,
    _finding_clips,
    _read_assembly_manifest,
    _spine_block_clips,
)
from .footage import (
    _build_persons,
    _build_scenes,
    _build_shots,
    _build_transcript_segments,
    _build_world_models,
    _derive_person_edges,
    _derive_scene_edges,
    _read_identity,
)
from .history import (
    _branch_records_digest,
    _commit_records_digest,
    _derive_branch_nodes,
    _derive_commit_nodes,
    _derive_tag_nodes,
    _derive_version_nodes,
    _reel_versions_path,
    _tag_records_digest,
)
from .operations import _derive_event_nodes, _event_log_path
from .permissions import _derive_participant_nodes
from .persistence import (
    GRAPH_FILE_NAME,
    _graph_path,
    _input_fingerprint,
    _refuse_engine_checkout,
    load_or_build,
)
from .proposals import _derive_proposal_nodes
from .queries import (
    _finding_clip_ids,
    _transcript_words,
    find_alternate_takes,
    find_audio_events,
    find_branches,
    find_broll_matches,
    find_commits,
    find_coverage,
    find_editorial_findings,
    find_project_events,
    find_reaction_decisions,
    find_reaction_shots,
    find_reel_versions,
    find_shot_colour,
    find_tags,
    find_variant_branches,
    find_vfx_detections,
    open_proposals,
    participants_with,
)
from .variants import _derive_variant_nodes, _variant_explorations_digest

__all__ = [
    "APPEARS_IN",
    "AUDIO_EVENT",
    "COVERS",
    "COLOUR_CAPABILITY",
    "COLOUR_READING",
    "COMMIT",
    "CONTAINS",
    "DEAD_SECTION",
    "EDIT",
    "EDITORIAL_FINDING",
    "FINDING_DEAD_SECTION",
    "FINDING_DEAD_TRIM",
    "FINDING_JL_DECISION",
    "FINDING_JL_PLACEMENT",
    "FINDING_KINDS",
    "FINDING_REACTION_CUTAWAY",
    "GRAPHIC",
    "GRAPH_FILE_NAME",
    "MATCHES",
    "MUSIC_CHANGES_AT",
    "NODE_TYPES",
    "ONSET_CLASS",
    "OPERATION",
    "PARTICIPANT",
    "PERSON",
    "PROPOSAL",
    "REACTS_TO",
    "REFERENCE",
    "RELATIONSHIP_TYPES",
    "SAYS",
    "SCENE",
    "SERVES",
    "SHOT",
    "SHOWN_IN",
    "SPEAKS_IN",
    "TIMELINE",
    "TRANSCRIPT_SEGMENT",
    "TAG",
    "BRANCH",
    "VARIANT",
    "VERSION",
    "VFX_DETECTION",
    "_add_colour_readings",
    "_add_dead_sections",
    "_add_onset_classes",
    "_add_vfx_detections",
    "_as_dict",
    "_as_list",
    "_assembly_manifest_path",
    "_build_audio_events",
    "_build_colour",
    "_build_persons",
    "_build_room_tone_dropouts",
    "_build_scenes",
    "_build_shots",
    "_build_transcript_segments",
    "_build_world_models",
    "_colour_for_clip",
    "_branch_records_digest",
    "_tag_records_digest",
    "_commit_records_digest",
    "_derive_analysis_nodes",
    "_derive_audio_edges",
    "_derive_commit_nodes",
    "_derive_branch_nodes",
    "_derive_tag_nodes",
    "_derive_edit_graph_nodes",
    "_derive_editorial_nodes",
    "_derive_event_nodes",
    "_derive_intent_nodes",
    "_derive_participant_nodes",
    "_derive_person_edges",
    "_derive_proposal_nodes",
    "_derive_scene_edges",
    "_derive_variant_nodes",
    "_derive_version_nodes",
    "_event_log_path",
    "_finding_clip_ids",
    "_finding_clips",
    "_graph_path",
    "_input_fingerprint",
    "_load_state",
    "_overlap",
    "_read_assembly_manifest",
    "_read_identity",
    "_read_raw_bytes",
    "_read_temporal_index",
    "_reel_versions_path",
    "_refuse_engine_checkout",
    "_spine_block_clips",
    "_transcript_words",
    "_variant_explorations_digest",
    "add_edge",
    "add_node",
    "build_graph",
    "edges_from",
    "edges_of_type",
    "edges_to",
    "find_alternate_takes",
    "find_audio_events",
    "find_branches",
    "find_broll_matches",
    "find_commits",
    "find_coverage",
    "find_editorial_findings",
    "find_project_events",
    "find_reaction_decisions",
    "find_reaction_shots",
    "find_reel_versions",
    "find_shot_colour",
    "find_tags",
    "find_variant_branches",
    "find_vfx_detections",
    "load_or_build",
    "main",
    "new_graph",
    "node",
    "open_proposals",
    "participants_with",
]
