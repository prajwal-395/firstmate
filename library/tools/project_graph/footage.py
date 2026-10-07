"""Project-graph footage family: persons, shots, world models, scenes, transcript."""
from __future__ import annotations

from pathlib import Path

try:
    from library.tools import (
        person_entity,
        source_memory,
    )
except ImportError:
    from tools import (
        person_entity,
        source_memory,
    )
from .core import (
    APPEARS_IN,
    CONTAINS,
    PERSON,
    SAYS,
    SCENE,
    SHOT,
    SHOWN_IN,
    SPEAKS_IN,
    TRANSCRIPT_SEGMENT,
    add_edge,
)

def _read_identity(digest: str, root: Path | None) -> dict | None:
    try:
        from library.tools import person_entity
    except ImportError:
        from tools import person_entity
    return person_entity.read_identity(digest, root)


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
