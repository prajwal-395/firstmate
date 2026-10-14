"""Evidence-linked cross-modal grounding over the M1/M3/M5/M6/M7 model.

This is a projection over measured source-memory lanes. It does not run a
model or promote a text or visual hypothesis to fact. Speaker grounding is
made only when the M1 utterance, diarized M3b voice turn, and M7 lip-backed
voice/face span agree on the same source interval. Disagreement remains an
explicit report with the observations that disagree.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

METHOD = "cross-modal-grounding-v1"
SPEECH_FACE_METHOD = "speech_face_cooccurrence"
REFERENCE_TERMS = {"this", "that", "these", "those", "it", "they",
                   "he", "she", "him", "her", "them"}


def map_interval(start: float, end: float, offset_to_reference: float
                 ) -> tuple[float, float]:
    """Map one source interval to the M6 reference clock.

    M6 defines ``reference_time = source_time + offset``. Invalid or empty
    spans refuse rather than silently becoming zero-length evidence.
    """
    values = (start, end, offset_to_reference)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("cross-modal interval and offset must be finite")
    if start >= end:
        raise ValueError("cross-modal intervals must be non-empty")
    return (start + offset_to_reference, end + offset_to_reference)


def _overlap(a: dict, b: dict) -> int:
    if a["clock_id"] != b["clock_id"]:
        return 0
    return max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def _offsets_by_clock(model: dict[str, Any]) -> dict[str, dict]:
    return {row["interval"]["clock_id"]: row
            for row in model["observations"]
            if row["modality"] == "multicam_offset"
            and row["payload"].get("group_id")
            and row["payload"].get("reference_digest")
            and isinstance(row["payload"].get("offset_to_reference_seconds"),
                           (int, float))}


def _reference_ticks(interval: dict, offset: float) -> tuple[int, int]:
    start, end = map_interval(interval["start"] / 1000,
                              interval["end"] / 1000, offset)
    return (math.floor(start * 1000), math.ceil(end * 1000))


def apply_project_model(model: dict[str, Any]) -> dict[str, int]:
    """Add speaker grounded utterances and reported cross-modal conflicts.

    Existing observations and identity links are the evidence. The routine
    only adds links/relations when their endpoints and support are present.
    """
    observations = model["observations"]
    links = model["identity_links"]
    relations = model["relations"]
    entities = {row["id"] for row in model["entities"]}
    obs_by_id = {row["id"]: row for row in observations}
    speaker_links: dict[str, set[str]] = defaultdict(set)
    person_by_voice_obs: dict[str, set[str]] = defaultdict(set)
    face_people_by_track: dict[tuple[str, str], set[str]] = defaultdict(set)
    for link in links:
        if link["observation_id"] not in obs_by_id:
            continue
        anchored = obs_by_id[link["observation_id"]]
        modality = anchored["modality"]
        if modality == "voice_turn" and link["method"] == "diarization":
            speaker_links[anchored["id"]].add(link["entity_id"])
        elif modality == "voice_turn" and link["method"] == SPEECH_FACE_METHOD:
            person_by_voice_obs[anchored["id"]].add(link["entity_id"])
        elif modality == "face" and link["method"] == "face_embedding_cluster":
            face_track = anchored["payload"].get("face_track")
            if face_track is not None:
                face_people_by_track[(anchored["asset_id"],
                                      str(face_track))].add(link["entity_id"])

    lips_by_voice: dict[str, list[dict]] = defaultdict(list)
    for obs in observations:
        payload = obs["payload"]
        if (obs["modality"] == "event_span"
                and payload.get("predicate") == "speaking"
                and payload.get("voice_track") is not None
                and payload.get("face_track") is not None):
            lips_by_voice[str(payload["voice_track"])].append(obs)

    utterances = [row for row in observations
                  if row["modality"] == "speech"
                  and "utterance_index" in row["payload"]]
    voice_turns = [row for row in observations
                   if row["modality"] == "voice_turn"]
    existing_links = {(row["entity_id"], row["observation_id"], row["method"])
                      for row in links}
    event_evidence = {event["id"]: set(event.get("evidence_ids") or [])
                      for event in model["events"]}
    grounded = reports = 0
    for utterance in utterances:
        support: set[str] = set()
        candidates: set[str] = set()
        speaker_candidates: set[str] = set()
        attribution_disagrees = False
        for turn in voice_turns:
            if _overlap(utterance["interval"], turn["interval"]) <= 0:
                continue
            support.add(turn["id"])
            speaker_candidates.update(speaker_links.get(turn["id"], ()))
            voice_track = str(turn["payload"].get("voice_track"))
            for lip in lips_by_voice.get(voice_track, ()):
                if _overlap(utterance["interval"], lip["interval"]) > 0:
                    support.add(lip["id"])
                    face_track = str(lip["payload"]["face_track"])
                    face_people = face_people_by_track.get(
                        (lip["asset_id"], face_track), set())
                    voice_people = person_by_voice_obs.get(turn["id"], set())
                    candidates.update(face_people)
                    support.update(
                        row["id"] for row in observations
                        if row["asset_id"] == lip["asset_id"]
                        and row["modality"] == "face"
                        and row["payload"].get("face_track") == face_track
                        and _overlap(utterance["interval"], row["interval"]) > 0)
                    if (face_people and voice_people
                            and face_people != voice_people):
                        attribution_disagrees = True
                    # Keep both independently measured attributions in the
                    # reportable candidate set when they disagree.
                    if attribution_disagrees:
                        candidates.update(voice_people)

        if len(candidates) == 1 and support and not attribution_disagrees:
            person_id = next(iter(candidates))
            key = (person_id, utterance["id"], METHOD)
            if key not in existing_links:
                interval = utterance["interval"]
                links.append({
                    "id": f"link-{person_id}-{utterance['id']}-{METHOD}",
                    "entity_id": person_id,
                    "observation_id": utterance["id"],
                    "confidence": None,
                    "method": METHOD,
                    "status": "verified",
                    "provenance_id": utterance["provenance_id"],
                    "supporting_observation_ids": sorted(support),
                    "scope": dict(interval),
                    "uncertainty": None,
                })
                grounded += 1

            if len(speaker_candidates) == 1:
                speaker_id = next(iter(speaker_candidates))
                relation_id = (f"relation-cross-modal-{utterance['id']}-"
                               f"{speaker_id}-{person_id}")
                if (speaker_id in entities and person_id in entities
                        and not any(row["id"] == relation_id
                                    for row in relations)):
                    relations.append({
                        "id": relation_id,
                        "subject_id": speaker_id,
                        "predicate": "speaks_as",
                        "object_id": person_id,
                        "interval": dict(utterance["interval"]),
                        "confidence": None,
                        "provenance_id": utterance["provenance_id"],
                        "evidence_ids": sorted({utterance["id"], *support}),
                        "status": "verified",
                        "certainty": "inferred",
                    })

        # Deictic/coreferential language is grounded only against a unique
        # entity participant in a co-timed, evidence-backed event. This is a
        # candidate until a language model or editor resolves the reference;
        # ambiguous references are reported with their finite candidate set.
        words = {word.strip(".,!?;:\"'()[]{}").lower()
                 for word in str(utterance["payload"].get("text", "")).split()}
        if words & REFERENCE_TERMS:
            speaker_persons = candidates
            referents: set[str] = set()
            reference_support: set[str] = {utterance["id"]}
            for event in model["events"]:
                if (event["interval"]["clock_id"]
                        != utterance["interval"]["clock_id"]
                        or _overlap(event["interval"],
                                    utterance["interval"]) <= 0):
                    continue
                support_ids = event_evidence[event["id"]]
                if not support_ids:
                    continue
                reference_support.update(support_ids)
                referents.update(set(event.get("participant_ids") or [])
                                 - speaker_persons)
            referents &= entities
            if len(referents) == 1 and len(speaker_persons) == 1:
                speaker_person = next(iter(speaker_persons))
                referent = next(iter(referents))
                relation_id = f"relation-reference-{utterance['id']}-{referent}"
                if not any(row["id"] == relation_id for row in relations):
                    relations.append({
                        "id": relation_id,
                        "subject_id": speaker_person,
                        "predicate": "refers_to",
                        "object_id": referent,
                        "interval": dict(utterance["interval"]),
                        "confidence": None,
                        "provenance_id": utterance["provenance_id"],
                        "evidence_ids": sorted(reference_support),
                        "status": "candidate",
                        "certainty": "inferred",
                    })
            elif len(referents) > 1:
                report_id = f"obs-ambiguous-reference-{utterance['id']}"
                if report_id not in obs_by_id:
                    observations.append({
                        "id": report_id,
                        "asset_id": utterance["asset_id"],
                        "interval": dict(utterance["interval"]),
                        "modality": "cross_modal_report",
                        "payload": {
                            "result": "ambiguous_reference",
                            "claim": "dialogue_to_entity_grounding",
                            "candidate_entity_ids": sorted(referents),
                            "evidence_ids": sorted(reference_support),
                            "method": METHOD,
                        },
                        "confidence": None,
                        "provenance_id": utterance["provenance_id"],
                    })
                    reports += 1

        # A diarized speaker and a lip-backed face that disagree are kept as
        # a candidate relation, with both observations cited. No error flag
        # or verdict is emitted.
        if (len(speaker_candidates) > 1 or len(candidates) > 1
                or attribution_disagrees):
            report_id = f"obs-cross-modal-disagreement-{utterance['id']}"
            evidence_ids = sorted({utterance["id"], *support})
            if report_id not in obs_by_id:
                observations.append({
                    "id": report_id,
                    "asset_id": utterance["asset_id"],
                    "interval": dict(utterance["interval"]),
                    "modality": "cross_modal_report",
                    "payload": {
                        "result": "disagreement",
                        "claim": "speech_to_entity_grounding",
                        "candidate_entity_ids": sorted(candidates),
                        "candidate_speaker_ids": sorted(speaker_candidates),
                        "evidence_ids": evidence_ids,
                        "method": METHOD,
                    },
                    "confidence": None,
                    "provenance_id": utterance["provenance_id"],
                })
                reports += 1
            # M7's own relation store is extended only for known entity
            # endpoints; an ambiguous claim stays a candidate.
            for speaker in speaker_candidates:
                for person in candidates:
                    if speaker not in entities or person not in entities:
                        continue
                    relation_id = f"relation-cross-modal-{utterance['id']}-{speaker}-{person}"
                    if not any(row["id"] == relation_id for row in relations):
                        relations.append({
                            "id": relation_id,
                            "subject_id": speaker,
                            "predicate": "speaker_face_disagreement",
                            "object_id": person,
                            "interval": dict(utterance["interval"]),
                            "confidence": None,
                            "provenance_id": utterance["provenance_id"],
                            "evidence_ids": evidence_ids,
                            "status": "candidate",
                            "certainty": "inferred",
                        })

    # M5 acoustic events align to M7/VI-06 event intervals on their shared
    # source clock. This records overlap with both measurements cited; it
    # does not claim that the sound caused the visual event.
    m6_by_clock = _offsets_by_clock(model)
    for sound in (row for row in observations
                  if row["modality"] == "sound_event"
                  and row["payload"].get("label")):
        for event in model["events"]:
            sound_interval = sound["interval"]
            event_interval = event["interval"]
            report_interval = None
            alignment_evidence: set[str] = set()
            if _overlap(sound_interval, event_interval) > 0:
                report_interval = {
                    "clock_id": sound_interval["clock_id"],
                    "start": max(sound_interval["start"],
                                 event_interval["start"]),
                    "end": min(sound_interval["end"], event_interval["end"]),
                }
            elif sound_interval["clock_id"] != event_interval["clock_id"]:
                sound_m6 = m6_by_clock.get(sound_interval["clock_id"])
                event_m6 = m6_by_clock.get(event_interval["clock_id"])
                sound_offset = sound_m6["payload"] if sound_m6 else {}
                event_offset = event_m6["payload"] if event_m6 else {}
                if (sound_m6 and event_m6
                        and sound_offset["group_id"] == event_offset["group_id"]
                        and sound_offset["reference_digest"]
                        == event_offset["reference_digest"]):
                    sound_ticks = _reference_ticks(
                        sound_interval,
                        float(sound_offset["offset_to_reference_seconds"]))
                    event_ticks = _reference_ticks(
                        event_interval,
                        float(event_offset["offset_to_reference_seconds"]))
                    start = max(sound_ticks[0], event_ticks[0])
                    end = min(sound_ticks[1], event_ticks[1])
                    if start < end:
                        report_interval = {
                            "clock_id": f"clock-{sound_offset['reference_digest']}",
                            "start": start, "end": end,
                        }
                        alignment_evidence.update((sound_m6["id"],
                                                   event_m6["id"]))
            if report_interval is None:
                continue
            support_ids = sorted({sound["id"],
                                  *event_evidence.get(event["id"], set()),
                                  *alignment_evidence})
            if len(support_ids) < 2:
                continue
            report_id = f"obs-sound-event-alignment-{sound['id']}-{event['id']}"
            if report_id in obs_by_id:
                continue
            observations.append({
                "id": report_id,
                "asset_id": sound["asset_id"],
                "interval": report_interval,
                "modality": "cross_modal_report",
                "payload": {
                    "result": "temporal_overlap",
                    "claim": "audio_visual_event_alignment",
                    "sound_label": sound["payload"]["label"],
                    "event_kind": event["kind"],
                    "entity_ids": list(event.get("participant_ids") or []),
                    "evidence_ids": support_ids,
                    "method": METHOD,
                },
                "confidence": None,
                "provenance_id": sound["provenance_id"],
            })
            reports += 1

    return {"grounded_utterances": grounded,
            "cross_modal_reports": reports}
