"""Project-graph audio family: sound events and their shot links."""
from __future__ import annotations
from .core import AUDIO_EVENT, MUSIC_CHANGES_AT, SHOT, _load_state, add_edge, add_node

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
