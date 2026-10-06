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

This module is that graph. It is DERIVED, never authored: it reads the
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

This module imports nothing from steps or processes: it reads state
through the same readers every consumer has.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from library.tools import (
        edit_graph,
        intent_model,
        person_entity,
        source_memory,
    )
except ImportError:
    from tools import (
        edit_graph,
        intent_model,
        person_entity,
        source_memory,
    )


# ── Node types, one enumeration ────────────────────────────────────

PERSON = "person"
SCENE = "scene"
SHOT = "shot"
TRANSCRIPT_SEGMENT = "transcript_segment"
AUDIO_EVENT = "audio_event"
GRAPHIC = "graphic"
REFERENCE = "reference"
TIMELINE = "timeline"
EDIT = "edit"
COLOUR_READING = "colour_reading"
VFX_DETECTION = "vfx_detection"
ONSET_CLASS = "onset_class"
DEAD_SECTION = "dead_section"
VERSION = "version"
EDITORIAL_FINDING = "editorial_finding"

NODE_TYPES = (
    PERSON,
    SCENE,
    SHOT,
    TRANSCRIPT_SEGMENT,
    AUDIO_EVENT,
    GRAPHIC,
    REFERENCE,
    TIMELINE,
    EDIT,
    COLOUR_READING,
    VFX_DETECTION,
    ONSET_CLASS,
    DEAD_SECTION,
    VERSION,
    EDITORIAL_FINDING,
)


# ── Relationship types, one enumeration ───────────────────────────

SAYS = "says"
APPEARS_IN = "appears_in"
SPEAKS_IN = "speaks_in"
SHOWN_IN = "shown_in"
REACTS_TO = "reacts_to"
COVERS = "covers"
MATCHES = "matches"
MUSIC_CHANGES_AT = "music_changes_at"
CONTAINS = "contains"
SERVES = "serves"

RELATIONSHIP_TYPES = (
    SAYS,
    APPEARS_IN,
    SPEAKS_IN,
    SHOWN_IN,
    REACTS_TO,
    COVERS,
    MATCHES,
    MUSIC_CHANGES_AT,
    CONTAINS,
    SERVES,
)


# ── Editorial finding kinds ───────────────────────────────────────

FINDING_DEAD_SECTION = "dead_section"
FINDING_DEAD_TRIM = "dead_section_trim"
FINDING_JL_PLACEMENT = "jl_placement"
FINDING_JL_DECISION = "jl_decision"
FINDING_REACTION_CUTAWAY = "reaction_cutaway"

FINDING_KINDS = (
    FINDING_DEAD_SECTION,
    FINDING_DEAD_TRIM,
    FINDING_JL_PLACEMENT,
    FINDING_JL_DECISION,
    FINDING_REACTION_CUTAWAY,
)
"""The compile manifest's report-only findings that become graph nodes.

Each kind names the manifest key it is read from: `dead_sections`,
`dead_section_trims`, `jl_placements`, `jl_placement_results` (one node
per entry, carrying the entry's `status`), `reaction_cutaways`.
`screen_direction_issues` stays out: no lane references a direction
finding by node yet, and a node nothing reads is not shipped.
"""


# ── Small readers ─────────────────────────────────────────────────


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _overlap(start_a, end_a, start_b, end_b) -> bool:
    return start_a < end_b and start_b < end_a


# ── The graph object ───────────────────────────────────────────────


def new_graph() -> dict:
    """An empty graph, the shape `build_graph` returns."""
    return {
        "graph_id": "project_graph",
        "node_count": 0,
        "edge_count": 0,
        "nodes": [],
        "edges": [],
    }


def add_node(graph: dict, node: dict) -> None:
    graph["nodes"].append(node)
    graph["node_count"] = len(graph["nodes"])


def add_edge(graph: dict, edge: dict) -> None:
    graph["edges"].append(edge)
    graph["edge_count"] = len(graph["edges"])


def node(graph: dict, node_id: str) -> dict:
    for candidate in graph["nodes"]:
        if candidate["node_id"] == node_id:
            return candidate
    return {}


def edges_from(graph: dict, node_id: str) -> list:
    return [e for e in graph["edges"] if e["from"] == node_id]


def edges_to(graph: dict, node_id: str) -> list:
    return [e for e in graph["edges"] if e["to"] == node_id]


def edges_of_type(graph: dict, rel_type: str) -> list:
    return [e for e in graph["edges"] if e["type"] == rel_type]


# ── Persistence ───────────────────────────────────────────────────

GRAPH_FILE_NAME = "project_graph.json"
"""The state file the graph is written to, beside `pipeline_data.json`."""

COLOUR_CAPABILITY = "color_grade.resolve"
"""The capability id the colour track's output is recorded under.

The grade step's post-bridge: its `color_grade_spec`, the bridge's
measured `clip_exposure` rows and the derived `camera_match` all land
in this one record (library/tools/capability_outputs.py partitions a
node's output; the undeclared keys belong to the run capability).
"""


def _graph_path(project_folder: str) -> Path:
    return Path(project_folder) / GRAPH_FILE_NAME


def _refuse_engine_checkout(project_folder: str) -> None:
    """Refuse to read or write the graph at the engine checkout itself.

    The graph is project state: it is written beside the project's
    own `pipeline_data.json`. A test that passes a cwd-relative root
    (compile_manifest's `"dummy"` out_dir resolves its project to the
    empty string) lands the write in whatever directory the suite ran
    from - the audio suite left `project_graph.json` at the repository
    root. No stranger's project ever contains this module, so a root
    that does is the engine checkout, and the call is refused before
    any read or write.
    """
    try:
        from library.tools.ren_refusal import RenRefusal
    except ImportError:
        from tools.ren_refusal import RenRefusal
    resolved = Path(project_folder).expanduser().resolve()
    try:
        Path(__file__).resolve().relative_to(resolved)
    except ValueError:
        return
    raise RenRefusal(
        f"project graph root {str(resolved)!r} is the engine checkout",
        "the graph is project state, written beside the project's own "
        "pipeline_data.json - a root that contains this module is a "
        "cwd-relative test root, never a project",
        "pass the test's tmp_path project (a test builds its project "
        "under tmp_path, never the repo)")


def _input_fingerprint(project_folder: str, root: Path | None = None) -> str:
    """A fingerprint of the inputs the graph is built from.

    The fingerprint covers the catalog, the world models, the person
    tracks and the edit graph state - every input that can change the
    graph. It is a SHA-256 of the canonical JSON of those inputs, so
    two runs over the same inputs produce the same fingerprint.
    """
    import hashlib
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    state = _load_state(project_folder)

    inputs = {
        "catalog": catalog,
        "recorded": recorded,
        "state": state,
        # The colour track's output is a graph input like any other: a
        # re-grade changes the shot payloads, so the cache must rebuild.
        "colour": _build_colour(project_folder),
        # The assembly manifest carries the editorial findings and the
        # versions ledger carries the reel versions: a new compile or a
        # new version changes the graph, so the cache must rebuild.
        # Raw bytes, not parsed records - the fingerprint only asks
        # whether the input changed.
        "assembly_manifest": _read_raw_bytes(
            _assembly_manifest_path(project_folder)),
        "reel_versions": _read_raw_bytes(
            _reel_versions_path(project_folder)),
    }
    for clip in catalog:
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None:
            continue
        model = source_memory.read_world_model(digest, root)
        if model:
            inputs[f"world_model:{digest}"] = model
        identity = _read_identity(digest, root)
        if identity:
            inputs[f"identity:{digest}"] = identity

    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _reel_versions_path(project_folder: str) -> Path | None:
    try:
        from library.tools.versions import reel_versions
    except ImportError:
        from tools.versions import reel_versions
    try:
        return Path(reel_versions.path_for(project_folder))
    except Exception:
        return None


def _read_raw_bytes(path: Path | None) -> str | None:
    """The hex digest of a file's bytes, or None when absent.

    For the input fingerprint: the cache asks whether an input
    changed, never what it says, so bytes are enough. An unreadable
    file reads as absent - the builder deriving from it already
    treats absence as "the run has not produced this yet".
    """
    if path is None:
        return None
    try:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _read_identity(digest: str, root: Path | None) -> dict | None:
    try:
        from library.tools import person_entity
    except ImportError:
        from tools import person_entity
    return person_entity.read_identity(digest, root)


def load_or_build(project_folder: str, root: Path | None = None) -> dict:
    """Load the cached graph, or rebuild when an input changed.

    The graph is persistent: it is written to `project_graph.json`
    beside `pipeline_data.json` with the fingerprint of the inputs it
    was built from. A query checks the fingerprint first - unchanged
    inputs reuse the cached graph, changed inputs rebuild and rewrite.
    """
    _refuse_engine_checkout(project_folder)
    path = _graph_path(project_folder)
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if cached and isinstance(cached, dict):
            fingerprint = _input_fingerprint(project_folder, root)
            if cached.get("input_fingerprint") == fingerprint:
                return cached.get("graph") or new_graph()

    graph = build_graph(project_folder, root)
    fingerprint = _input_fingerprint(project_folder, root)
    record = {
        "graph_id": "project_graph",
        "input_fingerprint": fingerprint,
        "graph": graph,
    }
    from library.tools.stable_json import write_stable
    write_stable(path, record)
    return graph


# ── The builder ───────────────────────────────────────────────────


def build_graph(project_folder: str, root: Path | None = None) -> dict:
    """Derive the project graph from the project's existing state.

    Reads the catalog, world models, person tracks, edit graph and
    intent model. A state key the run has not produced is simply absent
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

    return graph


def _build_persons(project_folder: str, root: Path | None) -> list:
    try:
        roster = person_entity.resolve_person_tracks(project_folder, root)
    except (OSError, ValueError, KeyError):
        return []
    persons = []
    for entry in roster.get("persons") or []:
        persons.append({
            "node_id": entry["person_id"],
            "node_type": PERSON,
            "label": entry.get("name") or entry["person_id"],
            "payload": {
                "name": entry.get("name"),
                "face_spans": entry.get("face_spans") or [],
                "voice_spans": entry.get("voice_spans") or [],
                "speech_face_links": entry.get("speech_face_links") or [],
            },
        })
    return persons


def _build_shots(catalog: list) -> list:
    shots = []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        shots.append({
            "node_id": f"shot:{clip_id}",
            "node_type": SHOT,
            "label": clip.get("source_file") or clip_id,
            "payload": {
                "clip_id": clip_id,
                "source_file": clip.get("source_file") or clip.get("path"),
                "duration_seconds": clip.get("duration_seconds"),
                "content_digest": clip.get("content_digest"),
            },
        })
    return shots


def _build_world_models(project_folder: str, catalog: list, recorded: dict,
                        root: Path | None) -> dict:
    """`{content_digest: world_model}` for each catalog source."""
    models = {}
    for clip in catalog:
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None or digest in models:
            continue
        model = source_memory.read_world_model(digest, root)
        if model:
            models[digest] = model
    return models


def _build_scenes(world_models: dict) -> list:
    scenes = []
    for digest, model in world_models.items():
        for entry in model.get("timeline") or []:
            scene = entry.get("scene")
            if scene:
                scenes.append({
                    "node_id": f"scene:{digest[:12]}:{entry['t']}",
                    "node_type": SCENE,
                    "label": scene.get("location") or f"scene at {entry['t']}s",
                    "payload": {
                        "content_digest": digest,
                        "t": entry["t"],
                        "location": scene.get("location"),
                        "type": scene.get("type"),
                        "lighting": scene.get("lighting"),
                    },
                })
    return scenes


def _build_transcript_segments(world_models: dict) -> list:
    segments = []
    for digest, model in world_models.items():
        for entry in model.get("timeline") or []:
            speech = entry.get("speech") or {}
            words = speech.get("words") or []
            if not words:
                continue
            speaker = speech.get("speaker") or {}
            segments.append({
                "node_id": f"transcript:{digest[:12]}:{entry['t']}",
                "node_type": TRANSCRIPT_SEGMENT,
                "label": f"speech at {entry['t']}s",
                "payload": {
                    "content_digest": digest,
                    "t": entry["t"],
                    "words": words,
                    "speaker": speaker,
                    "text": " ".join(w.get("word", "") for w in words),
                },
            })
    return segments


def _build_audio_events(world_models: dict) -> list:
    events = []
    for digest, model in world_models.items():
        for entry in model.get("timeline") or []:
            sound = entry.get("sound") or {}
            for event in sound.get("events") or []:
                events.append({
                    "node_id": f"audio:{digest[:12]}:{entry['t']}:{event.get('start', 0)}",
                    "node_type": AUDIO_EVENT,
                    "label": event.get("label") or "sound event",
                    "payload": {
                        "content_digest": digest,
                        "t": entry["t"],
                        "label": event.get("label"),
                        "start": event.get("start"),
                        "end": event.get("end"),
                        "confidence": event.get("confidence"),
                    },
                })
    return events


def _build_room_tone_dropouts(project_folder: str, catalog: list) -> list:
    """Room-tone dropouts the mix measured, registered as audio events.

    5.02's measure pass reports each digital-silence span inside a
    played range (`room_tone_continuity` on `audio_mix_spec`). The
    graph is track 2's module; this is the minimal hook the audio
    lane's outputs need so the graph lane can reason about them - the
    same registration the brief asks for when the graph has landed.
    The position is in SOURCE seconds (the mix measures the source,
    not the timeline), recorded as such rather than mapped through a
    timeline the graph does not hold. The digest is the source's own,
    read off the catalog. A project that has not run the mix carries
    no such events; the graph builds from whatever exists.
    """
    from library.tools import capability_outputs
    state = _load_state(project_folder)
    spec = capability_outputs.value(state, "audio_mix", "audio_mix_spec") or {}
    continuity = spec.get("room_tone_continuity", {}) or {}
    by_source = {}
    for clip in catalog or []:
        if isinstance(clip, dict) and clip.get("source_file"):
            by_source[clip["source_file"]] = clip.get("content_digest")
    events = []
    for plan in continuity.get("sources", []) or []:
        source_file = plan.get("source_file")
        digest = by_source.get(source_file)
        if not digest:
            continue
        for dropout in plan.get("dropouts", []) or []:
            events.append({
                "node_id": f"audio:dropout:{digest[:12]}:"
                           f"{dropout.get('start', 0)}",
                "node_type": AUDIO_EVENT,
                "label": "room_tone_dropout",
                "payload": {
                    "content_digest": digest,
                    "t": dropout.get("start"),
                    "label": "room_tone_dropout",
                    "start": dropout.get("start"),
                    "end": dropout.get("end"),
                    "unit": "source_seconds",
                },
            })
    return events


def _derive_person_edges(graph: dict, persons: list,
                          transcript_segments: list) -> None:
    """Person -> shot (appears_in, speaks_in, shown_in) and
    person -> transcript_segment (says) edges."""
    for person in persons:
        person_id = person["node_id"]
        payload = person["payload"]

        for span in payload.get("face_spans") or []:
            clip_id = span.get("clip_id")
            if clip_id:
                add_edge(graph, {
                    "from": person_id,
                    "to": f"shot:{clip_id}",
                    "type": APPEARS_IN,
                    "payload": {"start": span.get("start"), "end": span.get("end")},
                })
                add_edge(graph, {
                    "from": person_id,
                    "to": f"shot:{clip_id}",
                    "type": SHOWN_IN,
                    "payload": {"start": span.get("start"), "end": span.get("end")},
                })

        for span in payload.get("voice_spans") or []:
            clip_id = span.get("clip_id")
            if clip_id:
                add_edge(graph, {
                    "from": person_id,
                    "to": f"shot:{clip_id}",
                    "type": SPEAKS_IN,
                    "payload": {"start": span.get("start"), "end": span.get("end")},
                })

        for link in payload.get("speech_face_links") or []:
            clip_id = link.get("clip_id")
            if clip_id:
                add_edge(graph, {
                    "from": person_id,
                    "to": f"shot:{clip_id}",
                    "type": SPEAKS_IN,
                    "payload": {"voice_track": link.get("voice_track"),
                                "face_track": link.get("face_track")},
                })

    for segment in transcript_segments:
        digest = segment["payload"]["content_digest"]
        t = segment["payload"]["t"]
        speaker = segment["payload"].get("speaker") or {}
        face_track = speaker.get("face_track")
        voice_track = speaker.get("voice_track")
        for person in persons:
            person_id = person["node_id"]
            for link in person["payload"].get("speech_face_links") or []:
                if (link.get("face_track") == face_track
                        and link.get("voice_track") == voice_track
                        and link.get("clip_id")):
                    add_edge(graph, {
                        "from": person_id,
                        "to": segment["node_id"],
                        "type": SAYS,
                        "payload": {"content_digest": digest, "t": t},
                    })


def _derive_scene_edges(graph: dict, scenes: list) -> None:
    """Scene -> shot (contains) edges."""
    for scene in scenes:
        digest = scene["payload"]["content_digest"]
        for shot in graph["nodes"]:
            if shot["node_type"] != SHOT:
                continue
            if shot["payload"].get("content_digest") == digest:
                add_edge(graph, {
                    "from": scene["node_id"],
                    "to": shot["node_id"],
                    "type": CONTAINS,
                    "payload": {},
                })


def _derive_audio_edges(graph: dict, audio_events: list) -> None:
    """Audio event -> shot (music_changes_at) edges."""
    for event in audio_events:
        digest = event["payload"]["content_digest"]
        for shot in graph["nodes"]:
            if shot["node_type"] != SHOT:
                continue
            if shot["payload"].get("content_digest") == digest:
                add_edge(graph, {
                    "from": event["node_id"],
                    "to": shot["node_id"],
                    "type": MUSIC_CHANGES_AT,
                    "payload": {"t": event["payload"]["t"]},
                })


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


def _load_state(project_folder: str) -> dict:
    path = Path(project_folder) / "pipeline_data.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_colour(project_folder: str) -> dict:
    """The colour track's recorded output, or {} where the run produced none.

    Read through `capability_outputs`, the same reader
    `source_memory.load_catalog` uses - never a direct state key.
    """
    state = _load_state(project_folder)
    from library.tools import capability_outputs
    record = capability_outputs.read(state, COLOUR_CAPABILITY)
    return record if isinstance(record, dict) else {}


def _colour_for_clip(colour: dict, clip_id: str) -> dict | None:
    """The colour record for one clip: measurement, grade, match.

    Each half is the verbatim row the colour track produced - the
    measured `clip_exposure` row, the `per_clip_adjustments` entry
    carrying the delivered CDL, and the `camera_match` row whose
    camera group holds this clip. A half that was never produced is
    None, and a clip with no half at all is None: the graph does not
    invent a measurement it does not have.
    """
    measurement = None
    for row in _as_list(colour.get("clip_exposure")):
        if isinstance(row, dict) and row.get("clip_id") == clip_id:
            measurement = row
            break

    grade = None
    spec = colour.get("color_grade_spec")
    if isinstance(spec, dict):
        for adj in _as_list(spec.get("per_clip_adjustments")):
            if isinstance(adj, dict) and adj.get("clip_id") == clip_id:
                grade = adj
                break

    match = None
    match_record = colour.get("camera_match")
    if isinstance(match_record, dict):
        for row in _as_list(match_record.get("matches")):
            if isinstance(row, dict) and clip_id in _as_list(row.get("clips")):
                match = row
                break

    if measurement is None and grade is None and match is None:
        return None
    return {
        "measurement": measurement,
        "grade": grade,
        "match": match,
    }


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


def _derive_version_nodes(graph: dict, project_folder: str) -> None:
    """Version nodes from the reel versions ledger.

    Each version of each reel is a node, so the graph answers "what
    versions exist for this reel" and "what did version 3 change".
    """
    try:
        from library.tools.versions import reel_versions
        ledger = reel_versions.read(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for final, versions in (ledger.get("reels") or {}).items():
        for entry in versions or []:
            version_num = entry.get("version")
            if version_num is None:
                continue
            add_node(graph, {
                "node_id": f"version:{final}:{version_num}",
                "node_type": VERSION,
                "label": f"version {version_num} of {final}",
                "payload": {
                    "final": final,
                    "version": version_num,
                    "kind": entry.get("kind"),
                    "at": entry.get("at"),
                    "rows_digest": entry.get("rows_digest"),
                },
            })


# ── The query API ─────────────────────────────────────────────────


def find_reaction_shots(graph: dict, person_id: str,
                        around_t: float) -> list:
    """Shots where another person is visible while person_id speaks
    around time t (in person_id's source timeline).

    A reaction shot is a shot where a DIFFERENT person appears during
    the time person_id is speaking. The reaction is temporal: the other
    person's appearance overlaps the speaking span.
    """
    speaking_shots = set()
    for edge in edges_of_type(graph, SPEAKS_IN):
        if edge["from"] == person_id:
            speaking_shots.add(edge["to"])

    reaction_shots = []
    for edge in edges_of_type(graph, APPEARS_IN):
        other_person = edge["from"]
        if other_person == person_id:
            continue
        shot_id = edge["to"]
        if shot_id in speaking_shots:
            continue
        for speak_edge in edges_of_type(graph, SPEAKS_IN):
            if speak_edge["from"] != person_id:
                continue
            speak_start = speak_edge["payload"].get("start", 0)
            speak_end = speak_edge["payload"].get("end", 0)
            appear_start = edge["payload"].get("start", 0)
            appear_end = edge["payload"].get("end", 0)
            if _overlap(speak_start, speak_end, appear_start, appear_end):
                reaction_shots.append({
                    "shot_id": shot_id,
                    "person_id": other_person,
                    "reaction_start": appear_start,
                    "reaction_end": appear_end,
                })
                break
    return reaction_shots


def find_alternate_takes(graph: dict, clip_id: str) -> list:
    """Other shots covering the same CONTENT.

    An alternate take is a different clip where the same line is spoken
    - the spec's "shot 431 covers the same content as shot 183". The
    match is by transcript overlap: two shots are alternates when they
    share spoken words (the same sentence or phrase was recorded in
    both). A shot sharing a digest or file with the target is a
    DUPLICATE, not an alternate - it is reported separately.
    """
    target = node(graph, f"shot:{clip_id}")
    if not target:
        return []
    target_words = _transcript_words(graph, clip_id)
    target_digest = target["payload"].get("content_digest")
    target_file = target["payload"].get("source_file")

    alternates = []
    duplicates = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != SHOT:
            continue
        if candidate["node_id"] == f"shot:{clip_id}":
            continue
        candidate_id = candidate["payload"].get("clip_id")
        candidate_digest = candidate["payload"].get("content_digest")
        candidate_file = candidate["payload"].get("source_file")

        if target_digest and candidate_digest == target_digest:
            duplicates.append({
                "clip_id": candidate_id,
                "reason": "same-digest",
            })
            continue
        if target_file and candidate_file == target_file:
            duplicates.append({
                "clip_id": candidate_id,
                "reason": "same-file",
            })
            continue

        candidate_words = _transcript_words(graph, candidate_id)
        shared = target_words & candidate_words
        if shared:
            alternates.append({
                "clip_id": candidate_id,
                "reason": "transcript-overlap",
                "shared_words": sorted(shared)[:5],
            })

    return alternates + duplicates


def _transcript_words(graph: dict, clip_id: str) -> set:
    """The set of words spoken in a clip, from the transcript segments."""
    words = set()
    for candidate in graph["nodes"]:
        if candidate["node_type"] != TRANSCRIPT_SEGMENT:
            continue
        if candidate["payload"].get("content_digest") is None:
            continue
        shot = node(graph, f"shot:{clip_id}")
        if shot and candidate["payload"]["content_digest"] == shot["payload"].get("content_digest"):
            text = candidate["payload"].get("text", "")
            words.update(text.lower().split())
    return words


def find_coverage(graph: dict, timeline_start: float,
                  timeline_end: float) -> list:
    """Shots that cover a timeline span.

    Coverage comes from the edit graph's spine blocks: a block that
    plays a clip over a timeline range that overlaps the query span.
    """
    covering = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDIT:
            continue
        payload = candidate["payload"].get("edit_payload") or {}
        block_start = payload.get("timeline_start")
        block_end = payload.get("timeline_end")
        clip_id = payload.get("clip_id")
        if block_start is None or block_end is None or not clip_id:
            continue
        if _overlap(block_start, block_end, timeline_start, timeline_end):
            covering.append({
                "clip_id": clip_id,
                "timeline_start": block_start,
                "timeline_end": block_end,
                "edit_node_id": candidate["payload"].get("edit_node_id"),
            })
    return covering


def find_broll_matches(graph: dict, transcript_segment_id: str) -> list:
    """Shots whose content matches a speech segment.

    B-roll matching comes from the edit graph's broll_cover nodes: a
    B-roll assignment covers a spine block, and the block plays a
    passage cut from a transcript segment.
    """
    segment = node(graph, transcript_segment_id)
    if not segment:
        return []

    matches = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDIT:
            continue
        if candidate["payload"].get("node_type") != "broll_cover":
            continue
        payload = candidate["payload"].get("edit_payload") or {}
        clip_id = payload.get("clip_id")
        if not clip_id:
            continue
        block_position = payload.get("spine_block_position")
        for block in graph["nodes"]:
            if block["node_type"] != EDIT:
                continue
            if block["payload"].get("node_type") != "spine_block":
                continue
            block_payload = block["payload"].get("edit_payload") or {}
            if str(block_payload.get("position")) != str(block_position):
                continue
            block_clip = block_payload.get("clip_id")
            if block_clip:
                matches.append({
                    "clip_id": clip_id,
                    "covers_block": block_position,
                    "broll_clip": clip_id,
                })
    return matches


def find_shot_colour(graph: dict, clip_id: str) -> dict | None:
    """The colour track's registration for one shot, or None.

    The shot node's `colour` payload: `measurement` (the measured row -
    exposure, white balance, contrast, saturation, skin vector),
    `grade` (the delivered per-clip CDL) and `match` (this shot's
    camera against the scene's reference). A shot the colour track
    never measured returns None - an absent measurement is absent,
    never a default.
    """
    shot = node(graph, f"shot:{clip_id}")
    if not shot:
        return None
    return shot["payload"].get("colour")


def find_reel_versions(graph: dict, final: str | None = None) -> list:
    """The reel versions the graph registered, oldest first.

    Each row names the final it versions, the version number, the
    kind the versions store recorded (build, touch, undo, rollback,
    redo) and when it landed. A final the ledger never versioned
    reads as no rows - an absent version is absent, never version 0.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != VERSION:
            continue
        payload = candidate["payload"] or {}
        if final is not None and payload.get("final") != final:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "final": payload.get("final"),
            "version": payload.get("version"),
            "kind": payload.get("kind"),
            "at": payload.get("at"),
            "rows_digest": payload.get("rows_digest"),
        })
    rows.sort(key=lambda r: (str(r["final"]), r["version"] or 0))
    return rows


def find_editorial_findings(graph: dict, kind: str | None = None,
                            clip_id: str | None = None) -> list:
    """The compile's report-only findings the graph registered.

    Each row carries the finding verbatim - a dead section's timeline
    span and reasons, a trim proposal's edge and seconds, a J/L
    placement's measured bound, a J/L decision's chosen offset and
    status, a reaction cutaway's speech and reaction clips. Filter by
    finding kind, by clip, or both; a kind nothing recorded reads as
    no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDITORIAL_FINDING:
            continue
        payload = candidate["payload"] or {}
        if kind is not None and payload.get("kind") != kind:
            continue
        record = payload.get("record") or {}
        if clip_id is not None:
            clips = _finding_clip_ids(record)
            for edge in edges_from(graph, candidate["node_id"]):
                if edge["type"] == COVERS and edge["to"].startswith("shot:"):
                    clips.add(edge["to"][len("shot:"):])
            if clip_id not in clips:
                continue
        rows.append({
            "node_id": candidate["node_id"],
            "kind": payload.get("kind"),
            "record": record,
        })
    return rows


def _finding_clip_ids(record: dict) -> set:
    """Every clip id a finding record names, whatever the key."""
    ids = set()
    for key in ("clip_id", "speech_clip_id", "reaction_clip_id"):
        value = record.get(key)
        if value:
            ids.add(value)
    return ids


def find_reaction_decisions(graph: dict, clip_id: str | None = None,
                            at_time: float | None = None) -> list:
    """The reaction-cutaway EDIT DECISIONs the run recorded.

    Each row is one planner's decision: what it did (action), why
    (reason), what it rests on (evidence refs to measured graph nodes),
    what it must satisfy (constraints), whose taste it serves
    (style source, absent unless stated) and how strongly the measured
    overlap supports it (confidence). Filter by clip - the speech clip
    or the reaction clip the decision names - or by timeline time,
    which matches the speech span the decision reacts to. A finding the
    compile recorded before decisions existed carries no decision and
    reads as no row: an absent decision is absent, never synthesised.
    A time filter a decision cannot be placed in (no speech span on
    record) matches nothing.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDITORIAL_FINDING:
            continue
        payload = candidate["payload"] or {}
        if payload.get("kind") != FINDING_REACTION_CUTAWAY:
            continue
        record = payload.get("record") or {}
        decision = record.get("decision")
        if not isinstance(decision, dict):
            continue
        if clip_id is not None and clip_id not in _finding_clip_ids(record):
            continue
        if at_time is not None:
            start = record.get("speech_timeline_start")
            end = record.get("speech_timeline_end")
            if (not isinstance(start, (int, float))
                    or not isinstance(end, (int, float))):
                continue
            if not (start <= at_time <= end):
                continue
        rows.append({
            "node_id": candidate["node_id"],
            "speech_block_position": record.get("speech_block_position"),
            "speech_clip_id": record.get("speech_clip_id"),
            "reaction_clip_id": record.get("reaction_clip_id"),
            "speech_timeline_start": record.get("speech_timeline_start"),
            "speech_timeline_end": record.get("speech_timeline_end"),
            "reaction_land": (record.get("timing") or {}).get(
                "reaction_land"),
            "decision": decision,
        })
    return rows


def find_vfx_detections(graph: dict, clip_id: str | None = None) -> list:
    """The VFX detections the graph registered, optionally for one clip.

    Each row names the clip, the detected effect and the timeline
    position the edit graph recorded, with the edit node it came from.
    A clip with no detection reads as no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != VFX_DETECTION:
            continue
        payload = candidate["payload"] or {}
        if clip_id is not None and payload.get("clip_id") != clip_id:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "clip_id": payload.get("clip_id"),
            "effect": payload.get("effect"),
            "position": payload.get("position"),
            "edit_node_id": payload.get("edit_node_id"),
        })
    return rows


# ── CLI ───────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="project_graph",
        description="The Ren Project Graph - a persistent relational model "
                    "of people, scenes, shots, transcript, audio events, "
                    "graphics, references, timelines and edits.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="build the project graph")
    p_build.add_argument("project")
    p_build.add_argument("--memory-root")

    p_query = sub.add_parser("query", help="query the project graph")
    p_query.add_argument("project")
    p_query.add_argument("--person", help="person id for reaction shots")
    p_query.add_argument("--t", type=float, help="time for reaction shots")
    p_query.add_argument("--clip", help="clip id for alternate takes")
    p_query.add_argument("--colour", action="store_true",
                         help="the colour registration for --clip")
    p_query.add_argument("--timeline-start", type=float)
    p_query.add_argument("--timeline-end", type=float)
    p_query.add_argument("--decisions", action="store_true",
                         help="reaction-cutaway EDIT DECISIONs, "
                              "optionally for --clip or at --t")
    p_query.add_argument("--memory-root")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    root = Path(args.memory_root).expanduser() if args.memory_root else None

    if args.command == "build":
        graph = build_graph(args.project, root)
        print(json.dumps(graph, indent=2))
        return 0

    if args.command == "query":
        graph = build_graph(args.project, root)
        if args.decisions:
            results = find_reaction_decisions(
                graph, args.clip, args.t)
            print(json.dumps(results, indent=2))
            return 0
        if args.person and args.t is not None:
            results = find_reaction_shots(graph, args.person, args.t)
            print(json.dumps(results, indent=2))
            return 0
        if args.clip:
            results = find_alternate_takes(graph, args.clip)
            print(json.dumps(results, indent=2))
            return 0
        if args.colour and args.clip:
            results = find_shot_colour(graph, args.clip)
            print(json.dumps(results, indent=2))
            return 0
        if args.timeline_start is not None and args.timeline_end is not None:
            results = find_coverage(graph, args.timeline_start, args.timeline_end)
            print(json.dumps(results, indent=2))
            return 0
        print("query needs --decisions, --person + --t, --clip, "
              "--colour + --clip, or --timeline-start + --timeline-end",
              file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
