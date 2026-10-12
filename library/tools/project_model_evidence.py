"""Unit 3: evidence and identity migration into the canonical Project Model.

Maps source-memory lanes M0-M10, person-entity tracks, face/voice links,
verified spans, masks and known object tracks into Project Model IDs with
explicit clocks (plan Units 1-2 provide the contract and the store; this
module is Unit 3 and owns ONLY the evidence/identity half).

Read-only on live project data: every lane, the catalog and the step
directories are opened for reading and never written. The only writes are
the returned model dict (the caller commits it through
``project_model_store``) and this module's own CLI ``--out``/``--commit``
paths. Migration is proven on fixtures plus the bridge's dual-read parity
report, never by rewriting a live project.

Clock policy: source lanes measure in float seconds on no shared clock,
so each source digest gets one millisecond clock (``ticks_per_second``
1000, no ``frame_rate``). ``sec_to_ticks`` refuses non-positive spans;
they land in the migration report as dropped, never as empty intervals.

Identity policy (fail-closed, never guessed):

- Person entities come from ``person_entity.resolve_person_tracks``:
  face-embedding clusters, face-only across sources. Cross-source voice
  identity stays ABSENT while ``VOICE_MATCH_THRESHOLD`` is unmeasured;
  voice tracks become per-source Speaker entities and reach a Person
  only through a within-source measured speech-face link.
- Object instances stay per-clip: no cross-clip object re-identification
  is measured, so no link is minted for one.
- A face-seeded (1.06) subject mask reaches the roster Person only when
  exactly one roster person holds a face span covering the track's first
  sample; otherwise it stays on its per-clip object entity and the
  ambiguity is reported. Blob-seeded (category ``unknown``) objects get
  no ``ObjectClass``: an ``auto_object_N`` label is not a semantic class.
- M9 (the joined per-second world model) is a compatibility/query view
  over these same lanes, not new evidence, so it is inventoried in
  coverage but not re-migrated as observations. M2 (the shared frame
  sample) is transport: its times survive inside M3 frame ``t`` values
  and its counts in asset metadata; per-frame thumbnails are not
  observations. M10 relationship candidates stay observations: without a
  resolved object instance there is no measured object end for a
  Relation, and minting one would be a guess.

Editorial data (clip uses, intents, edits, effects, renders, frame maps,
NLE bindings) is Unit 4's lane: this migration emits those collections
empty. ``resolve_person`` still traverses them, so a model Unit 4 (or a
fixture) fills resolves a measured person from source observations
through masks and A-roll to a rendered range with provenance.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    from library.tools import (
        conversation_clock,
        event_spans,
        mask_seed,
        person_entity,
        person_measurements,
        project_model_masks,
        region_query,
        source_memory,
        span_verification,
    )
    from library.tools.project_model import SCHEMA_VERSION, validate_project_model
except ImportError:  # imported as `tools.*` from inside library/
    from tools import (
        conversation_clock,
        event_spans,
        mask_seed,
        person_entity,
        person_measurements,
        project_model_masks,
        region_query,
        source_memory,
        span_verification,
    )
    from tools.project_model import SCHEMA_VERSION, validate_project_model

TICKS_PER_SECOND = 1000
"""Millisecond source clocks: lanes measure float seconds, so integer
millisecond ticks preserve every measured span without a frame-rate
assumption no lane recorded."""

M3_POINT_RADIUS_S = 0.1
"""Half-width for an M3 frame point sample, the same convention
``person_entity`` uses for face observations (``FACE_SPAN_RADIUS_S``):
a sample is evidence about its neighbourhood, not an invented track."""

FACE_SEED_CATEGORY = "person"
FACE_SEED_LABEL = "face_seeded_subject"
UNKNOWN_CATEGORY = "unknown"


def sec_to_ticks(start: float, end: float) -> tuple[int, int] | None:
    """Float seconds to half-open integer ticks, or None when refused.

    Refuses non-finite bounds and non-positive spans: a zero-length
    measurement cannot be a half-open interval, and widening it would
    invent evidence.
    """
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        return None
    start_tick = math.floor(start_f * TICKS_PER_SECOND)
    end_tick = math.ceil(end_f * TICKS_PER_SECOND)
    if end_tick <= start_tick:
        return None
    return (start_tick, end_tick)


def _confidence(value: Any) -> float | None:
    """A measured confidence, or None when absent or out of range.

    An out-of-range score is not evidence about certainty; carrying it
    would fail validation, and clamping it would rewrite the lane.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= number <= 1.0):
        return None
    return number


def _asset_id(digest: str) -> str:
    return f"asset-{digest}"


def _clock_id(digest: str) -> str:
    return f"clock-{digest}"


def _stream_id(digest: str) -> str:
    return f"stream-{digest}"


def _prov_id(digest: str, lane: str) -> str:
    return f"prov-{digest}-{lane}"


class _Builder:
    """Accumulates one Project Model document plus its migration report."""

    def __init__(self, project_id: str) -> None:
        self.model: dict[str, Any] = {
            "id": project_id,
            "schema_version": SCHEMA_VERSION,
            "timebase_policy": "integer_ticks",
            "clocks": [],
            "assets": [],
            "streams": [],
            "entities": [],
            "observations": [],
            "identity_links": [],
            "masks": [],
            "object_classes": [],
            "tracks": [],
            "relations": [],
            "intents": [],
            "clip_uses": [],
            "edits": [],
            "effects": [],
            "renders": [],
            "frame_maps": [],
            "nle_bindings": [],
            "receipts": [],
            "provenance": [],
        }
        self.dropped: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self._obs_counter: dict[str, int] = {}
        self._seen_prov: set[str] = set()

    def provenance(self, digest: str, lane: str, producer: str,
                   source_file: str | None = None) -> str:
        prov_id = _prov_id(digest, lane)
        if prov_id not in self._seen_prov:
            self._seen_prov.add(prov_id)
            self.model["provenance"].append({
                "id": prov_id,
                "producer": producer,
                "producer_version": None,
                "source_digest": digest,
                "recorded_at": None,
            })
        _ = source_file
        return prov_id

    def observation(self, digest: str, lane: str, asset_id: str,
                    clock_id: str, start: float, end: float, modality: str,
                    payload: dict[str, Any], confidence: Any,
                    producer: str) -> str | None:
        ticks = sec_to_ticks(start, end)
        if ticks is None:
            self.dropped.append({"digest": digest, "lane": lane,
                                 "modality": modality,
                                 "reason": "non-positive-interval",
                                 "start": start, "end": end})
            return None
        key = f"{digest}-{lane}"
        self._obs_counter[key] = self._obs_counter.get(key, 0) + 1
        obs_id = f"obs-{key}-{self._obs_counter[key]:04d}"
        self.model["observations"].append({
            "id": obs_id,
            "asset_id": asset_id,
            "interval": {"clock_id": clock_id,
                         "start": ticks[0], "end": ticks[1]},
            "modality": modality,
            "payload": payload,
            "confidence": _confidence(confidence),
            "provenance_id": self.provenance(digest, lane, producer),
        })
        return obs_id

    def link(self, entity_id: str, observation_id: str, method: str,
             digest: str, lane: str, producer: str,
             confidence: Any = None, status: str = "verified") -> str:
        link_id = f"link-{entity_id}-{observation_id}-{method}"
        self.model["identity_links"].append({
            "id": link_id,
            "entity_id": entity_id,
            "observation_id": observation_id,
            "confidence": _confidence(confidence),
            "method": method,
            "status": status,
            "provenance_id": self.provenance(digest, lane, producer),
        })
        return link_id


def _migrate_digest(builder: _Builder, digest: str, asset_uri: str,
                    clip_ids: list[str], duration: float | None,
                    root: Path | None) -> None:
    """Migrate every measured lane of one source digest."""
    asset_id = _asset_id(digest)
    clock_id = _clock_id(digest)
    builder.model["clocks"].append({
        "id": clock_id,
        "domain": {"kind": "source", "asset_id": asset_id,
                   "stream_id": _stream_id(digest)},
        "ticks_per_second": TICKS_PER_SECOND,
        "frame_rate": None,
    })
    builder.model["streams"].append({
        "id": _stream_id(digest),
        "asset_id": asset_id,
        "stream_index": 0,
        "clock_id": clock_id,
        "metadata": {},
    })

    m0 = source_memory.read_m0(digest, root)
    if m0 is not None:
        video_streams = m0.get("video_streams") or []
        kind = "video" if video_streams else "audio"
        duration = m0.get("duration_seconds") or duration
    else:
        kind = "other"
        builder.notes.append(f"{digest[:12]}: M0 absent; asset kept for "
                             f"catalog parity with kind other")
    m2 = source_memory.read_m2(digest, root)
    builder.model["assets"].append({
        "id": asset_id,
        "kind": kind,
        "uri": asset_uri,
        "content_digest": digest,
        "metadata": {
            "clip_ids": clip_ids,
            "duration_seconds": duration,
            "m0_present": m0 is not None,
            "m2_frames": len((m2 or {}).get("frames") or []) if m2 else 0,
        },
        "provenance": [builder.provenance(
            digest, "m0", "library.tools.source_memory")],
    })

    _migrate_transcript(builder, digest, asset_id, clock_id, root)
    _migrate_identity(builder, digest, asset_id, clock_id, root)
    _migrate_scenes(builder, digest, asset_id, clock_id, root)
    _migrate_sound(builder, digest, asset_id, clock_id, root)
    _migrate_events(builder, digest, asset_id, clock_id, root)
    _migrate_relationships(builder, digest, asset_id, clock_id, root)
    _migrate_multicam(builder, digest, asset_id, clock_id, duration, root)


def _migrate_transcript(builder: _Builder, digest: str, asset_id: str,
                        clock_id: str, root: Path | None) -> None:
    doc, status = source_memory.read_m1(digest, root)
    if doc is None or status != "fresh":
        builder.notes.append(f"{digest[:12]}: M1 {status}; no speech "
                             f"observations")
        return
    # Lane B (cascade report 2.1): one Observation per utterance, with
    # the deterministic lane key `footage_model_link.utterance_obs_id`
    # both the footage index and timeline transcripts mint. Word-level
    # rows below keep their counter ids; the utterance row is what a
    # transcript segment's `obs_id` resolves to.
    try:
        from library.tools import footage_model_link as link
    except ImportError:  # pragma: no cover - same-tree import
        link = None
    for u_idx, utt in enumerate(doc.get("utterances") or []):
        if link is not None and isinstance(utt, dict):
            ticks = sec_to_ticks(utt.get("start"), utt.get("end"))
            obs_id = link.utterance_obs_id(digest, u_idx)
            if ticks is not None and not any(
                    isinstance(row, dict) and row.get("id") == obs_id
                    for row in builder.model["observations"]):
                words = utt.get("words") or []
                text = utt.get("text")
                if isinstance(text, str) and text.strip():
                    builder.model["observations"].append({
                        "id": obs_id,
                        "asset_id": asset_id,
                        "interval": {"clock_id": clock_id,
                                     "start": ticks[0], "end": ticks[1]},
                        "modality": "speech",
                        "payload": {"text": text,
                                    "utterance_index": u_idx,
                                    "word_count": len(words)},
                        "confidence": None,
                        "provenance_id": builder.provenance(
                            digest, "m1",
                            "library.tools.source_memory:M1"),
                    })
    for u_idx, utt in enumerate(doc.get("utterances") or []):
        for word in utt.get("words") or []:
            if not isinstance(word, dict):
                continue
            builder.observation(
                digest, "m1", asset_id, clock_id,
                word.get("start"), word.get("end"), "speech_word",
                {"text": word.get("text", word.get("word")),
                 "utterance_index": u_idx},
                word.get("confidence"),
                "library.tools.source_memory:M1")


def _identity_docs(digest: str, root: Path | None) -> tuple[dict | None, dict | None]:
    """(M3b identity, M1b speakers): the voices live in both slots from
    one measurement, so the migration reads M3b first and treats M1b as
    the same-measurement copy, never a second voice population."""
    identity = person_entity.read_identity(digest, root)
    speakers = source_memory.read_speakers(digest, root)
    return identity, speakers


def _migrate_identity(builder: _Builder, digest: str, asset_id: str,
                      clock_id: str, root: Path | None) -> None:
    identity, speakers = _identity_docs(digest, root)
    if identity is None and speakers is None:
        builder.notes.append(f"{digest[:12]}: M3b/M1b absent; no "
                             f"face or voice observations")
        return
    voices: list[dict] = []
    voice_lane = "m3b"
    if identity is not None:
        voices = [v for v in identity.get("voices") or []
                  if isinstance(v, dict)]
    if not voices and isinstance(speakers, dict):
        voices = [v for v in speakers.get("speakers") or []
                  if isinstance(v, dict)]
        voice_lane = "m1b"
        builder.notes.append(f"{digest[:12]}: voices read from the M1b "
                             f"copy; M3b carries no voice population")
    elif isinstance(speakers, dict):
        builder.notes.append(f"{digest[:12]}: M1b present as the "
                             f"same-measurement copy of M3b voices; "
                             f"migrated once")
    for voice in voices:
        track_id = voice.get("track_id")
        speaker_id = f"speaker-{digest}-{track_id}"
        if not any(e["id"] == speaker_id for e in builder.model["entities"]):
            builder.model["entities"].append({
                "id": speaker_id, "kind": "speaker", "label": None,
                "object_class_id": None,
            })
        for turn in voice.get("spans") or []:
            start, end = (turn if isinstance(turn, (list, tuple)) and len(turn) == 2
                          else (None, None))
            obs_id = builder.observation(
                digest, voice_lane, asset_id, clock_id, start, end,
                "voice_turn", {"voice_track": track_id},
                None, "library.tools.person_entity")
            if obs_id is not None:
                builder.link(speaker_id, obs_id, "diarization",
                             digest, voice_lane,
                             "library.tools.person_entity")


def _face_person(builder_persons: list[dict], digest: str,
                 track_id: str) -> str | None:
    for person in builder_persons:
        for track in person.get("face_tracks") or []:
            if (track.get("content_digest") == digest
                    and track.get("track_id") == track_id):
                return person["person_id"]
    return None


def _migrate_faces(builder: _Builder, digest: str, asset_id: str,
                   clock_id: str, roster: list[dict],
                   root: Path | None) -> None:
    """Face spans and per-track face masks; each span links to the roster
    person that owns its track, and each track carries one mask built
    from its own measured boxes (no M3 join needed: M3b spans ARE point
    samples with boxes)."""
    identity = person_entity.read_identity(digest, root)
    if identity is None:
        return
    for face in identity.get("faces") or []:
        if not isinstance(face, dict):
            continue
        track_id = face.get("track_id")
        person_id = _face_person(roster, digest, track_id)
        samples = []
        span_start: float | None = None
        span_end: float | None = None
        for span in face.get("spans") or []:
            if not isinstance(span, dict):
                continue
            obs_id = builder.observation(
                digest, "m3b", asset_id, clock_id,
                span.get("start"), span.get("end"), "face",
                {"face_track": track_id, "box": span.get("box")},
                span.get("det_score"), "library.tools.person_entity")
            if obs_id is not None and person_id is not None:
                builder.link(person_id, obs_id, "face_embedding_cluster",
                             digest, "m3b", "library.tools.person_entity")
            try:
                midpoint = (float(span["start"]) + float(span["end"])) / 2.0
            except (TypeError, ValueError, KeyError):
                continue
            samples.append({"t": midpoint, "box": span.get("box")})
            span_start = midpoint if span_start is None else min(span_start, midpoint)
            span_end = midpoint if span_end is None else max(span_end, midpoint)
        if not samples or span_start is None or span_end is None:
            continue
        ticks = sec_to_ticks(span_start, span_end if span_end > span_start else span_start + 0.001)
        if ticks is None:
            builder.dropped.append({"digest": digest, "lane": "m3b",
                                    "modality": "face_mask",
                                    "reason": "non-positive-interval",
                                    "track": track_id})
            continue
        if person_id is None:
            builder.notes.append(f"{digest[:12]}: face track {track_id} "
                                 f"has no roster person; mask withheld "
                                 f"rather than attached nowhere")
            continue
        builder.model["masks"].append({
            "id": f"mask-{digest}-{track_id}",
            "entity_id": person_id,
            "interval": {"clock_id": clock_id,
                         "start": ticks[0], "end": ticks[1]},
            "representation": {"kind": project_model_masks.FACE_BBOX_TRACK_KIND,
                               "face_track": track_id,
                               "samples": samples},
            "source_model": "insightface_buffalo_l+arcface_cluster",
            "provenance_id": builder.provenance(
                digest, "m3b", "library.tools.person_entity"),
        })


def _migrate_speech_face_links(builder: _Builder, digest: str,
                               roster: list[dict],
                               root: Path | None) -> None:
    """Within-source voice-to-person links: a voice turn reaches the
    roster person only through a measured same-source speech-face link.
    Cross-source voice identity is never minted (see module docstring)."""
    identity = person_entity.read_identity(digest, root)
    if identity is None:
        return
    face_to_person = {}
    for face in identity.get("faces") or []:
        if isinstance(face, dict) and face.get("track_id"):
            person_id = _face_person(roster, digest, face["track_id"])
            if person_id is not None:
                face_to_person[face["track_id"]] = person_id
    obs_by_voice: dict[str, list[str]] = {}
    voice_provs = {_prov_id(digest, "m3b"), _prov_id(digest, "m1b")}
    for obs in builder.model["observations"]:
        if (obs["modality"] == "voice_turn"
                and obs["provenance_id"] in voice_provs):
            obs_by_voice.setdefault(
                str(obs["payload"].get("voice_track")), []).append(obs["id"])
    for link in identity.get("speech_face_links") or []:
        if not isinstance(link, dict):
            continue
        person_id = face_to_person.get(link.get("face_track"))
        if person_id is None:
            continue
        for obs_id in obs_by_voice.get(str(link.get("voice_track")), []):
            builder.link(person_id, obs_id, "speech_face_cooccurrence",
                         digest, "m3b", "library.tools.person_entity")


def _migrate_scenes(builder: _Builder, digest: str, asset_id: str,
                    clock_id: str, root: Path | None) -> None:
    m4 = source_memory.read_scenes(digest, root)
    if m4 is None:
        builder.notes.append(f"{digest[:12]}: M4 absent; no scene "
                             f"observations")
        return
    for scene in m4.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        builder.observation(
            digest, "m4", asset_id, clock_id,
            scene.get("start"), scene.get("end"), "scene",
            {k: scene.get(k) for k in
             ("location", "type", "lighting", "notable_features")},
            None, "library.tools.analysis.vision_pipeline_v3")


def _migrate_sound(builder: _Builder, digest: str, asset_id: str,
                   clock_id: str, root: Path | None) -> None:
    m5 = source_memory.read_sound(digest, root)
    if m5 is None:
        builder.notes.append(f"{digest[:12]}: M5 absent; no sound "
                             f"observations")
        return
    for event in m5.get("sound_events") or []:
        if not isinstance(event, dict):
            continue
        builder.observation(
            digest, "m5", asset_id, clock_id,
            event.get("start"), event.get("end"), "sound_event",
            {"label": event.get("label")}, event.get("confidence"),
            "library.steps.step_1_04_temporal_index")


def _migrate_events(builder: _Builder, digest: str, asset_id: str,
                    clock_id: str, root: Path | None) -> None:
    m7 = event_spans.read_m7(digest, root)
    if m7 is None:
        builder.notes.append(f"{digest[:12]}: M7 absent; no event spans")
        builder.notes.append(f"{digest[:12]}: M8 verdicts unread without "
                             f"their M7 candidate spans")
        return
    try:
        verdicts = span_verification.read_verdicts(digest, root) or {}
    except (OSError, ValueError):
        verdicts = {}
    if not isinstance(verdicts, dict):
        verdicts = {}
    predicates = m7.get("predicates") or {}
    for predicate in ("speaking", "on_screen"):
        for span in (predicates.get(predicate) or {}).get("spans") or []:
            if not isinstance(span, dict):
                continue
            builder.observation(
                digest, "m7", asset_id, clock_id,
                span.get("start"), span.get("end"), "event_span",
                {"predicate": predicate,
                 "voice_track": span.get("voice_track"),
                 "face_track": span.get("face_track")},
                None, "library.tools.event_spans")
    for span in (m7.get("candidates") or {}).get(
            "hand_near_mouth", {}).get("spans") or []:
        if not isinstance(span, dict):
            continue
        matched = _verdicts_for_span(span, verdicts)
        builder.observation(
            digest, "m7", asset_id, clock_id,
            span.get("start"), span.get("end"), "event_candidate",
            {"predicate": "hand_near_mouth",
             "face_track": span.get("face_track"),
                 "verdicts": matched,
                 "verified": any(_is_affirmative(v.get("answer"))
                                 for v in matched)},
            None, "library.tools.event_spans")


def _is_affirmative(answer: Any) -> bool:
    """A yes verdict, whether the lane stored a boolean or a word."""
    return answer is True or str(answer).strip().lower() == "yes"


def _verdicts_for_span(span: dict, verdicts: dict) -> list[dict]:
    """M8 verdicts recorded for this candidate span's selected frames.

    Same matching rule ``world_model`` uses: the verdict's frames equal
    the span's selected frames at millisecond rounding. A verdict that
    matches no span stays out of the model and is reported in coverage
    by ``evidence_coverage``.
    """
    try:
        selected = span_verification.select_frames(span)
    except (KeyError, TypeError, ValueError):
        return []
    selected_times = [round(float(f["t"]), 3) for f in selected
                      if isinstance(f, dict) and "t" in f]
    matched = []
    for verdict in verdicts.values():
        if not isinstance(verdict, dict):
            continue
        frames = verdict.get("frames") or []
        if ([round(float(t), 3) for t in frames]
                == selected_times):
            matched.append({k: verdict.get(k) for k in
                            ("statement", "answer", "reason", "model")})
    return matched


def _migrate_relationships(builder: _Builder, digest: str, asset_id: str,
                           clock_id: str, root: Path | None) -> None:
    m10 = source_memory.read_relationships(digest, root)
    if m10 is None:
        builder.notes.append(f"{digest[:12]}: M10 absent; no relationship "
                             f"candidates")
        return
    for name in ("holding", "pointing"):
        for span in ((m10.get("relationships") or {}).get(name, {})
                     .get("spans") or []):
            if not isinstance(span, dict):
                continue
            builder.observation(
                digest, "m10", asset_id, clock_id,
                span.get("start"), span.get("end"),
                "relationship_candidate",
                {"relation": name, "owners": span.get("owners")},
                None, "library.tools.relationships")


def _migrate_multicam(builder: _Builder, digest: str, asset_id: str,
                      clock_id: str, duration: float | None,
                      root: Path | None) -> None:
    m6 = conversation_clock.read_clock(digest, root)
    if m6 is None:
        builder.notes.append(f"{digest[:12]}: M6 absent; source stands on "
                             f"its own clock")
        return
    if not duration:
        builder.notes.append(f"{digest[:12]}: M6 present but duration "
                             f"unknown; offset kept out of the model")
        return
    builder.observation(
        digest, "m6", asset_id, clock_id, 0.0, duration,
        "multicam_offset",
        {"group_id": m6.get("group_id"),
         "reference_digest": m6.get("reference_digest"),
         "offset_to_reference_seconds": m6.get(
             "offset_to_reference_seconds")},
        None, "library.tools.conversation_clock")


def _migrate_m3_frames(builder: _Builder, digest: str, asset_id: str,
                       clock_id: str, root: Path | None) -> None:
    """M3 frames ride the shared M2 sample: each frame's faces (box, lips)
    and hands become one observation at the frame's own time. Point
    samples take the module's stated neighbourhood interval."""
    m3 = person_measurements.read_m3(digest, root)
    if m3 is None:
        builder.notes.append(f"{digest[:12]}: M3 absent; no frame geometry")
        return
    for frame in m3.get("frames") or []:
        if not isinstance(frame, dict) or "t" not in frame:
            continue
        try:
            t = float(frame["t"])
        except (TypeError, ValueError):
            continue
        faces = frame.get("faces") or []
        for j, face in enumerate(faces):
            if not isinstance(face, dict):
                continue
            builder.observation(
                digest, "m3", asset_id, clock_id,
                max(0.0, t - M3_POINT_RADIUS_S), t + M3_POINT_RADIUS_S,
                "face_frame",
                {"face_index": j, "face": face,
                 "hands": frame.get("hands") or []},
                None, "library.tools.person_measurements")


def _subject_person_for_clip(roster: list[dict], digest: str,
                             sample_t: float) -> str | None:
    """The roster person owning a segmentation subject seed, or None.

    A face-seeded 1.06 track starts from the first frame's face box; it
    names its person only when exactly one roster person holds a face
    span covering that sample. Ambiguity (or nobody) is absence, never
    a majority vote.
    """
    owners = set()
    for person in roster:
        for span in person.get("face_spans") or []:
            if span.get("content_digest") != digest:
                continue
            entry = span.get("span") if isinstance(span.get("span"), dict) else span
            try:
                start, end = float(entry["start"]), float(entry["end"])
            except (TypeError, ValueError, KeyError):
                continue
            if start <= sample_t < end:
                owners.add(person["person_id"])
    if len(owners) == 1:
        return next(iter(owners))
    return None


def _migrate_segmentation(builder: _Builder, project_folder: str,
                          clip_id: str, digest: str, roster: list[dict],
                          root: Path | None) -> None:
    """1.06 tracked objects: known classes, per-clip instances, tracks
    and masks. RLE blobs stay in the step file by reference; the model
    carries the track geometry, not megabytes of base64."""
    del root
    seg = mask_seed.load_segmentation(project_folder, clip_id)
    if seg is None:
        builder.notes.append(f"{clip_id}: no 1.06 segmentation; no object "
                             f"tracks or subject masks")
        return
    sample_fps = seg.get("sample_fps") or 2.0
    try:
        sample_fps = float(sample_fps)
        if sample_fps <= 0:
            raise ValueError
    except (TypeError, ValueError):
        builder.dropped.append({"digest": digest, "lane": "segmentation",
                                "clip_id": clip_id,
                                "reason": "unusable-sample-fps",
                                "sample_fps": seg.get("sample_fps")})
        return
    try:
        from library.tools.project_layout import ProjectLayout
        seg_path = (ProjectLayout(project_folder)
                    .step_dir("object_segmentation")
                    / f"{clip_id}_segmentation.json")
        seg_ref = str(seg_path.relative_to(project_folder))
    except (ValueError, OSError):
        seg_ref = f"{clip_id}_segmentation.json"
    resolution = seg.get("resolution") or [0, 0]
    height, width = (list(resolution) + [0, 0])[:2]
    period = 1.0 / sample_fps
    for obj in seg.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        object_id = str(obj.get("object_id") or "obj")
        frames = sorted(f for f in obj.get("frames") or []
                        if isinstance(f, int))
        if not frames:
            builder.dropped.append({"digest": digest, "lane": "segmentation",
                                    "clip_id": clip_id, "object": object_id,
                                    "reason": "track-has-no-samples"})
            continue
        category = obj.get("category") or UNKNOWN_CATEGORY
        class_id: str | None = None
        if category != UNKNOWN_CATEGORY:
            class_id = f"class-{str(category).strip().lower().replace(' ', '_')}"
            if not any(c["id"] == class_id
                       for c in builder.model["object_classes"]):
                builder.model["object_classes"].append({
                    "id": class_id, "name": str(obj.get("label") or category),
                    "taxonomy": "sam2_seed_category",
                })
        else:
            builder.notes.append(f"{clip_id}/{object_id}: blob-seeded with "
                                 f"no semantic class; instance kept, class "
                                 f"withheld")
        entity_id = f"object-{clip_id}-{object_id}"
        builder.model["entities"].append({
            "id": entity_id, "kind": "object", "label": obj.get("label"),
            "object_class_id": class_id,
        })
        clock_id = _clock_id(digest)
        bboxes = obj.get("bboxes") or {}
        samples = []
        for frame_idx in frames:
            box = bboxes.get(frame_idx, bboxes.get(str(frame_idx)))
            if box is None:
                continue
            tick = round(frame_idx / sample_fps * TICKS_PER_SECOND)
            samples.append({"tick": tick,
                            "geometry": {"bbox_pixels": list(box)},
                            "confidence": None,
                            "coordinate_space":
                                f"source_pixels_{width}x{height}"})
        if not samples:
            builder.dropped.append({"digest": digest, "lane": "segmentation",
                                    "clip_id": clip_id, "object": object_id,
                                    "reason": "track-has-no-geometry"})
            builder.model["entities"].pop()
            continue
        builder.model["tracks"].append({
            "id": f"track-{clip_id}-{object_id}",
            "entity_id": entity_id,
            "clock_id": clock_id,
            "samples": samples,
            "provenance_id": builder.provenance(
                digest, "segmentation",
                "library.steps.step_1_06_object_segmentation"),
        })
        first_t = frames[0] / sample_fps
        last_t = frames[-1] / sample_fps + period
        ticks = sec_to_ticks(first_t, last_t)
        if ticks is None:
            builder.dropped.append({"digest": digest, "lane": "segmentation",
                                    "clip_id": clip_id, "object": object_id,
                                    "reason": "non-positive-interval"})
            continue
        mask_entity = entity_id
        if (obj.get("label") == FACE_SEED_LABEL
                and category == FACE_SEED_CATEGORY):
            owner = _subject_person_for_clip(roster, digest, first_t)
            if owner is not None:
                mask_entity = owner
            else:
                builder.notes.append(
                    f"{clip_id}/{object_id}: face-seeded subject with no "
                    f"unique roster owner at t=0; mask stays on the object "
                    f"instance")
        builder.model["masks"].append({
            "id": f"mask-{clip_id}-{object_id}",
            "entity_id": mask_entity,
            "interval": {"clock_id": clock_id,
                         "start": ticks[0], "end": ticks[1]},
            "representation": {
                "kind": project_model_masks.SAM2_RLE_TRACK_KIND,
                "segmentation_file": seg_ref,
                "object_id": object_id,
                "sample_fps": sample_fps,
                "resolution": [height, width],
                "sample_count": len(samples),
            },
            "source_model": "sam2",
            "provenance_id": builder.provenance(
                digest, "segmentation",
                "library.steps.step_1_06_object_segmentation"),
        })


def migrate_project_evidence(project_folder: str, *,
                             memory_root: str | Path | None = None,
                             catalog: list | None = None,
                             ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Migrate one project's measured evidence into a Project Model dict.

    Returns ``(model, report)``; the model is validated before return.
    Reads only: catalog, recorded fingerprints, memory lanes, the person
    roster derived from stored identity, and 1.06 segmentation files.

    ``catalog`` is the in-memory clip catalog when the caller just
    measured it (the scan link); None reads it off the project state
    as before.
    """
    root = Path(memory_root).expanduser() if memory_root else None
    slug = Path(project_folder).name or "project"
    builder = _Builder(f"project-{slug}")
    if catalog is None:
        catalog = source_memory.load_catalog(project_folder)
    if not isinstance(catalog, list):
        catalog = []
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    roster = person_entity.resolve_person_tracks(
        project_folder, root)["persons"]
    for person in roster:
        builder.model["entities"].append({
            "id": person["person_id"],
            "kind": "person",
            "label": person.get("name"),
            "object_class_id": None,
        })
    if not catalog:
        builder.notes.append("catalog empty or unreadable; model carries "
                             "the roster and nothing else")
    seen: dict[str, list[str]] = {}
    clip_digest: dict[str, str] = {}
    for clip in catalog:
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("clip_id") or "clip"
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            builder.notes.append(f"{clip_id}: no digest live or recorded; "
                                 f"nothing to migrate")
            continue
        clip_digest[clip_id] = digest
        seen.setdefault(digest, []).append(clip_id)
    for digest, clip_ids in seen.items():
        first = next(c for c in catalog
                     if isinstance(c, dict)
                     and source_memory.digest_for_clip(
                         project_folder, c, recorded)[0] == digest)
        uri = str(first.get("source_file") or first.get("path") or
                  first.get("file_path") or "")
        m0 = source_memory.read_m0(digest, root)
        duration = (m0 or {}).get("duration_seconds")
        _migrate_digest(builder, digest, uri, clip_ids, duration, root)
        _migrate_m3_frames(builder, digest, _asset_id(digest),
                           _clock_id(digest), root)
    for digest in seen:
        _migrate_faces(builder, digest, _asset_id(digest),
                       _clock_id(digest), roster, root)
    for digest in seen:
        _migrate_speech_face_links(builder, digest, roster, root)
    for clip_id, digest in clip_digest.items():
        _migrate_segmentation(builder, project_folder, clip_id, digest,
                              roster, root)
    validate_project_model(builder.model)
    report = {
        "project": str(project_folder),
        "digests": sorted(seen),
        "persons": [p["person_id"] for p in roster],
        "counts": {k: len(builder.model[k]) for k in
                   ("assets", "streams", "clocks", "entities",
                    "observations", "identity_links", "masks",
                    "object_classes", "tracks", "relations")},
        "dropped": builder.dropped,
        "notes": builder.notes,
    }
    return builder.model, report


def evidence_coverage(project_folder: str, model: dict[str, Any],
                      report: dict[str, Any], *,
                      memory_root: str | Path | None = None
                      ) -> dict[str, Any]:
    """Dual-read evidence parity: what the lanes hold versus what the
    model carries. Read-only; the legacy stores are never changed.

    Counts observations per (digest, lane) off provenance IDs and
    re-inventories lane presence, so a lane that landed after migration
    (or one the migration refused) shows up as drift, not silence.
    """
    root = Path(memory_root).expanduser() if memory_root else None
    counted: dict[str, dict[str, int]] = {}
    for obs in model.get("observations") or []:
        prov = str(obs.get("provenance_id") or "")
        if prov.startswith("prov-"):
            # Provenance ids are prov-{digest}-{lane}; digests carry
            # dashes, lane names do not, so the lane is the last segment.
            digest, _, lane = prov[len("prov-"):].rpartition("-")
            if digest and lane:
                counted.setdefault(digest, {}).setdefault(lane, 0)
                counted[digest][lane] += 1
    lanes: dict[str, dict[str, Any]] = {}
    for digest in report.get("digests") or []:
        m1, m1_status = source_memory.read_m1(digest, root)
        _identity, _speakers = _identity_docs(digest, root)
        presence = {
            "m0": source_memory.read_m0(digest, root) is not None,
            "m1": m1 is not None and m1_status == "fresh",
            "m1_status": m1_status,
            "m1b": _speakers is not None,
            "m2": source_memory.read_m2(digest, root) is not None,
            "m3": person_measurements.read_m3(digest, root) is not None,
            "m3b": _identity is not None,
            "m4": source_memory.read_scenes(digest, root) is not None,
            "m5": source_memory.read_sound(digest, root) is not None,
            "m6": None,
            "m7": None,
            "m8_verdicts": 0,
            "m9": source_memory.read_world_model(digest, root) is not None,
            "m10": source_memory.read_relationships(digest, root) is not None,
        }
        presence["m6"] = conversation_clock.read_clock(digest, root) is not None
        m7 = event_spans.read_m7(digest, root)
        presence["m7"] = m7 is not None
        try:
            verdicts = span_verification.read_verdicts(digest, root) or {}
            presence["m8_verdicts"] = len(verdicts) if isinstance(
                verdicts, dict) else 0
        except (OSError, ValueError):
            presence["m8_verdicts"] = 0
        lanes[digest] = {"present": presence,
                         "migrated_observations": counted.get(digest, {})}
    return {"project": str(project_folder),
            "model_id": model.get("id"),
            "schema_version": model.get("schema_version"),
            "counts": report.get("counts"),
            "lanes": lanes,
            "dropped": report.get("dropped") or [],
            "notes": report.get("notes") or [],
            "absent_identity": [
                ("cross-source voice links: withheld, "
                 "VOICE_MATCH_THRESHOLD unmeasured"),
                ("cross-clip object links: withheld, no re-identification "
                 "measurement"),
                ("M10 relations: candidates stay observations until an "
                 "object instance resolves the object end"),
            ]}


def resolve_person(model: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a measured person to observations, masks, regions, A-roll and
    rendered ranges, with provenance. Pure read over the model dict.

    ``regions`` are the person's region tracks (`geometry.kind ==
    "region"` samples, MU-09): the model half of `region_query`. A model
    migrated before region tracks (pre-MU-08) resolves to `regions: []`,
    never None - absence of measurement, not absence of the key.

    ``ref`` is an entity id or a declared label; anything else returns
    None, never a best-effort neighbour.
    """
    want = str(ref).strip()
    entity = None
    for candidate in model.get("entities") or []:
        if candidate.get("kind") != "person":
            continue
        if candidate.get("id") == want:
            entity = candidate
            break
    if entity is None and want:
        lowered = want.lower()
        for candidate in model.get("entities") or []:
            if (candidate.get("kind") == "person"
                    and isinstance(candidate.get("label"), str)
                    and candidate["label"].lower() == lowered):
                entity = candidate
                break
    if entity is None:
        return None
    entity_id = entity["id"]
    observations: dict[str, dict] = {}
    links = [link for link in model.get("identity_links") or []
             if link.get("entity_id") == entity_id]
    wanted_obs = {link.get("observation_id") for link in links}
    for obs in model.get("observations") or []:
        if obs.get("id") in wanted_obs:
            observations[obs["id"]] = obs
    masks = [mask for mask in model.get("masks") or []
             if mask.get("entity_id") == entity_id]
    regions = list(region_query.region_tracks_for(model, entity_id))
    clip_uses = [use for use in model.get("clip_uses") or []
                 if entity_id in (use.get("entity_links") or [])]
    rendered_ranges = []
    frame_maps = {fm.get("id"): fm for fm in model.get("frame_maps") or []}
    renders = model.get("renders") or []
    for use in clip_uses:
        for frame_map in frame_maps.values():
            for segment in frame_map.get("segments") or []:
                if segment.get("source_clip_use") != use.get("id"):
                    continue
                owners = [r for r in renders
                          if r.get("frame_map_id") == frame_map.get("id")]
                for render in owners:
                    rendered_ranges.append({
                        "clip_use_id": use.get("id"),
                        "render_id": render.get("id"),
                        "output_asset_id": render.get("output_asset_id"),
                        "input_revision": render.get("input_revision"),
                        "timeline_interval": segment.get("from"),
                        "render_interval": segment.get("to"),
                        "mapping_kind": segment.get("mapping_kind"),
                    })
    wanted_prov = set()
    for row in (list(observations.values()) + links + masks + regions
                + clip_uses):
        prov = row.get("provenance_id")
        if prov:
            wanted_prov.add(prov)
    provenance = [prov for prov in model.get("provenance") or []
                  if prov.get("id") in wanted_prov]
    return {"entity": entity, "identity_links": links,
            "observations": list(observations.values()),
            "masks": masks, "regions": regions, "clip_uses": clip_uses,
            "rendered_ranges": rendered_ranges,
            "provenance": provenance}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="project_model_evidence",
        description="Unit 3: migrate measured evidence and identity into "
                    "the canonical Project Model (read-only on lanes).")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="migrate a project's lanes")
    migrate.add_argument("project")
    migrate.add_argument("--memory-root", default=None)
    migrate.add_argument("--out", default=None,
                         help="write the model JSON here (default: stdout)")
    migrate.add_argument("--commit", action="store_true",
                         help="commit as a new store revision")
    migrate.add_argument("--expected-revision", type=int, default=0)
    migrate.add_argument("--reason", default="unit 3 evidence migration")

    coverage = sub.add_parser("coverage", help="dual-read evidence parity")
    coverage.add_argument("project")
    coverage.add_argument("--model", default=None,
                          help="model JSON file (default: read the store)")
    coverage.add_argument("--memory-root", default=None)

    resolve = sub.add_parser("resolve-person",
                             help="resolve a measured person with provenance")
    resolve.add_argument("project", help="project folder (store) or model file")
    resolve.add_argument("--ref", required=True)
    resolve.add_argument("--model", default=None)
    resolve.add_argument("--memory-root", default=None)

    args = parser.parse_args(argv)
    if args.command == "migrate":
        model, report = migrate_project_evidence(
            args.project, memory_root=args.memory_root)
        if args.commit:
            from library.tools.project_model_store import commit_project_model
            revision = commit_project_model(
                Path(args.project), model,
                expected_revision=args.expected_revision, reason=args.reason)
            report["committed_revision"] = revision
        payload = json.dumps({"report": report}, indent=2)
        if args.out:
            from library.tools.project_model import write_project_model
            write_project_model(Path(args.out), model)
            print(payload)
        else:
            print(json.dumps({"model": model, "report": report}, indent=2))
        return 0
    if args.command == "coverage":
        if args.model:
            from library.tools.project_model import read_project_model
            model = read_project_model(Path(args.model))
        else:
            from library.tools.project_model_store import load_project_model
            _revision, model = load_project_model(Path(args.project))
        _model, report = migrate_project_evidence(
            args.project, memory_root=args.memory_root)
        print(json.dumps(evidence_coverage(args.project, model, report,
                                           memory_root=args.memory_root),
                         indent=2))
        return 0
    resolved = None
    if args.model:
        from library.tools.project_model import read_project_model
        resolved = resolve_person(read_project_model(Path(args.model)),
                                  args.ref)
    else:
        candidate = Path(args.project)
        if candidate.is_file():
            from library.tools.project_model import read_project_model
            resolved = resolve_person(read_project_model(candidate), args.ref)
        else:
            from library.tools.project_model_store import load_project_model
            _revision, model = load_project_model(candidate)
            resolved = resolve_person(model, args.ref)
    if resolved is None:
        print(json.dumps({"ref": args.ref, "resolved": False}))
        return 1
    print(json.dumps(resolved, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
