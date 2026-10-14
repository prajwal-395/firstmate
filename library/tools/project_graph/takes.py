"""Cross-source take and coverage groups derived from M1 and M6."""
from __future__ import annotations

import hashlib

from .core import TAKE_GROUP, TAKE_MEMBER, add_edge, add_node


def _interval_evidence(model: dict | None, start: float, end: float) -> dict:
    """Existing M9 picture and M5 audio evidence touching an M1 span."""
    picture, audio = [], []
    for row in (model or {}).get("timeline") or []:
        t = row["t"]
        if not (start < float(t) + 1 and float(t) < end):
            continue
        picture.append({key: row.get(key) for key in
                        ("t", "faces", "scene", "events", "relationships")
                        if row.get(key) is not None})
        sound = (row.get("sound") or {}).get("events") or []
        audio.extend(event for event in sound
                     if start < float(event["end"])
                     and float(event["start"]) < end)
    return {"picture": picture, "audio": audio}


def _group_kind(comparison: dict, link: dict) -> str:
    if comparison["same_instant"] is True:
        return "alternate_coverage"
    if comparison["same_instant"] is False:
        return "retake"
    return "unresolved_take_relation"


def derive_take_groups(graph: dict, project_folder: str, catalog: list,
                       recorded: dict, world_models: dict,
                       root=None) -> None:
    """Materialize FI-13 links as Project Graph group nodes and membership edges.

    Links retain both utterances. M6 can establish alternate coverage only
    when the measured direct-edge uncertainty supports the same instant.
    """
    from library.tools import conversation_clock, source_memory, speech_take

    clips_by_digest = {}
    utterances_by_digest = {}
    clocks = {}
    for clip in catalog:
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None or digest in utterances_by_digest:
            continue
        doc, status = source_memory.read_m1(digest, root)
        if status != "fresh" or doc is None:
            continue
        utterances, _dropped = speech_take.utterance_spans(doc)
        utterances_by_digest[digest] = utterances
        clocks[digest] = conversation_clock.read_clock(digest, root)
        clips_by_digest[digest] = clip["clip_id"]

    for link in speech_take.cross_source_links(utterances_by_digest, clocks):
        first, second = link["utterance_a"], link["utterance_b"]
        comparison = conversation_clock.compare_instants(
            clocks.get(first["digest"]), first["start_s"],
            clocks.get(second["digest"]), second["start_s"])
        group_kind = _group_kind(comparison, link)
        pair_key = (f"{first['digest']}:{first['utterance_index']}:"
                    f"{second['digest']}:{second['utterance_index']}")
        group_id = "take-group:" + hashlib.sha256(
            pair_key.encode("utf-8")).hexdigest()[:24]
        member_records = []
        for side in (first, second):
            evidence = _interval_evidence(
                world_models.get(side["digest"]),
                float(side["start_s"]), float(side["end_s"]))
            member_records.append({
                "clip_id": clips_by_digest.get(side["digest"]),
                "content_digest": side["digest"],
                "utterance_index": side["utterance_index"],
                "source_start": side["start_s"],
                "source_end": side["end_s"],
                "speech_evidence": {
                    "status": "present", "method": "M1 transcript",
                    "text": side["text"],
                    "start": side["start_s"], "end": side["end_s"],
                },
                "picture_evidence": {
                    "status": "available" if world_models.get(side["digest"])
                    else "not_analyzed",
                    "method": "M9 joined source observations",
                    "items": evidence["picture"],
                },
                "audio_evidence": {
                    "status": "available" if world_models.get(side["digest"])
                    else "not_analyzed",
                    "method": "M5 sound events via M9",
                    "items": evidence["audio"],
                },
            })
        payload = {
            "kind": group_kind,
            "speech_relation": link["kind"],
            "shared_run": link["shared_run"],
            "abandoned_first": link["abandoned_first"],
            "members": member_records,
            "m6_comparison": comparison,
            "same_instant": comparison["same_instant"],
        }
        add_node(graph, {
            "node_id": group_id, "node_type": TAKE_GROUP,
            "label": f"{group_kind}: {link['kind']} speech",
            "payload": payload,
        })
        for member in member_records:
            if member["clip_id"] is not None:
                add_edge(graph, {"from": group_id,
                                 "to": f"shot:{member['clip_id']}",
                                 "type": TAKE_MEMBER,
                                 "payload": {"source_start": member["source_start"],
                                             "source_end": member["source_end"],
                                             "content_digest": member["content_digest"]}})


def find_take_comparisons(graph: dict, clip_id: str) -> list[dict]:
    """Comparison payloads for every preserved take group containing a clip."""
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != TAKE_GROUP:
            continue
        payload = candidate["payload"]
        if any(member["clip_id"] == clip_id for member in payload["members"]):
            rows.append({"group_id": candidate["node_id"], **payload})
    return rows
