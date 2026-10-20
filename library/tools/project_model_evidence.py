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
  face-embedding clusters, face-only across sources. Voice tracks become
  per-source Speaker entities and reach a Person through a within-source
  measured speech-face link, or through a cross-source voice match at the
  measured ``VOICE_MATCH_THRESHOLD`` to a voice already linked to that
  person (``person_entity.join_voices_across_sources``) - never by
  merging two persons.
- Object instances stay per-clip: MU-15's existing SAM 2 track samples
  support continuity only within that track. The held-out benchmark at
  ``docs/benchmarks/object-continuity-benchmark.md`` measures cross-clip
  re-identification; this migration still mints no cross-clip object link,
  because a score alone does not authorize one. First measured report is
  ``docs/benchmarks/2026-10-14-object-continuity-benchmark.json``
  (held-out LC4930 + LCATL0011, 12 stills, Apple Vision person-instance
  producer at 0.107 s/frame: person precision/recall 0.643, IDF1 0.143
  with 6 identity switches from per-frame track IDs, 2 of 2 cross-clip
  entities abstained, 4 state transitions unscored, laptop recall 0.0
  from the person-only producer).
- A face-seeded (1.06) subject mask reaches the roster Person only when
  exactly one roster person holds a face span covering the track's first
  sample; otherwise it stays on its per-clip object entity and the
  ambiguity is reported. Blob-seeded (category ``unknown``) objects get
  no ``ObjectClass``: an ``auto_object_N`` label is not a semantic class.
  A detector-seeded (MU-15) object mints one with taxonomy
  ``open-vocabulary`` from its seed category; face/blob seeds keep the
  legacy ``sam2_seed_category``.
- An M3d person-region track reaches the roster Person holding its
  majority face track (``IdentityLink`` method ``face_in_region``,
  confidence the track's face-track share). A track with no majority
  face track stays on an explicit ``unassigned-{digest}-{track}``
  entity with its named reason and NO identity link: ambiguity
  (two faces in one mask) or absence (a limb whose owner is out of
  frame) is reported, never guessed.
- M3f region stats (subject against outside, per sample) become
  ``region_stats`` observations that name their region track and carry
  no identity link of their own: the track's ``face_in_region`` link is
  the only road from a reading to a person.
- M3g depth (median relative depth per region, per sample) becomes
  ``depth`` observations on the same road: depth never names a person.
- Confidences are measured, never defaulted: the ArcFace cosine of a
  face track against its person's centroid (``face_embedding_cluster``),
  the diarization silhouette of the chosen k (``diarization``), the
  containment share (``face_in_region``). Where a lane stored no score,
  the link carries None.
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
import copy
import hashlib
import json
import math
from dataclasses import replace
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

try:
    from library.tools import (
        conversation_clock,
        depth,
        event_spans,
        evidence_contract,
        mask_seed,
        pause_class,
        person_entity,
        person_measurements,
        person_regions,
        project_model_masks,
        region_query,
        region_stats,
        source_memory,
        span_verification,
        speech_take,
    )
    from library.tools.project_model import SCHEMA_VERSION, validate_project_model
    from library.tools.evidence_lifecycle import (
        normalize_envelope_preprocessing, typed_confidence,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools import (
        conversation_clock,
        depth,
        event_spans,
        evidence_contract,
        mask_seed,
        pause_class,
        person_entity,
        person_measurements,
        person_regions,
        project_model_masks,
        region_query,
        region_stats,
        source_memory,
        span_verification,
        speech_take,
    )
    from tools.project_model import SCHEMA_VERSION, validate_project_model
    from tools.evidence_lifecycle import (
        normalize_envelope_preprocessing, typed_confidence,
    )

TICKS_PER_SECOND = 1000
"""Millisecond source clocks: lanes measure float seconds, so integer
millisecond ticks preserve every measured span without a frame-rate
assumption no lane recorded."""

M3_POINT_RADIUS_S = 0.1
"""Half-width for an M3 frame point sample, the same convention
``person_entity`` uses for face observations (``FACE_SPAN_RADIUS_S``):
a sample is evidence about its neighbourhood, not an invented track."""

M7_METHOD = "event-spans-v1 over M3+M3b"
"""The migration's name for the M7 derivation: a pure join over M3
faces and M3b tracks that stamps no instrument method of its own, so
native M7 records key freshness on slot recency (the events slot must
be no older than the persons/identity slots it was read from) rather
than on a method string. A record naming any other method was not
built by this derivation and is refused."""

M7_PREDICATE_TYPES = {"speaking": "event.speaking_span",
                      "on_screen": "event.visible_span"}
"""M7 predicate spans migrate as measurements of their own kind: a
speaking span is a diarized turn attributed to a face, an on-screen
span is a visible face track. Anything else stays a legacy-only
observation rather than being filed under the wrong kind."""

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


def _embedding_cosine(a: Any, b: Any) -> float | None:
    """Cosine similarity of two embedding vectors, or None when either
    is not a usable vector. Pure arithmetic (no numpy): the migration
    must run wherever the model is read, not only where the ML stack
    is installed."""
    try:
        xs = [float(v) for v in a]
        ys = [float(v) for v in b]
    except (TypeError, ValueError):
        return None
    if len(xs) != len(ys) or not xs:
        return None
    dot = sum(x * y for x, y in zip(xs, ys))
    nx = math.sqrt(sum(x * x for x in xs))
    ny = math.sqrt(sum(y * y for y in ys))
    if nx <= 0.0 or ny <= 0.0 or not math.isfinite(dot):
        return None
    return dot / (nx * ny)


def _asset_id(digest: str) -> str:
    return f"asset-{digest}"


def _clock_id(digest: str) -> str:
    return f"clock-{digest}"


def _stream_id(digest: str, stream_index: int = 0) -> str:
    return f"stream-{digest}" if stream_index == 0 else f"stream-{digest}-{stream_index}"


def _memory_freshness(doc: dict, digest: str) -> str:
    """Freshness of M4/M5 records, whose producers have no input ledger."""
    if doc.get("content_digest") != digest:
        return "stale"
    source_file = doc.get("source_file")
    if not isinstance(source_file, str) or not source_file:
        return "unknown"
    path = Path(source_file)
    if not path.is_file():
        return "unknown"
    try:
        from library.tools import footage_identity
        live_digest = footage_identity.fingerprint(str(path))["content_digest"]
    except OSError:
        return "stale"
    return "fresh" if live_digest == digest else "stale"


def _prov_id(digest: str, lane: str) -> str:
    return f"prov-{digest}-{lane}"


class _Builder:
    """Accumulates one Project Model document plus its migration report."""

    def __init__(self, project_id: str,
                 memory_root: Path | None = None) -> None:
        self.memory_root = memory_root
        self.model: dict[str, Any] = {
            "id": project_id,
            "schema_version": SCHEMA_VERSION,
            "timebase_policy": "integer_ticks",
            "clocks": [],
            "time_transforms": [],
            "assets": [],
            "streams": [],
            "entities": [],
            "observations": [],
            "signals": [],
            "events": [],
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
            "timeline_states": [],
        }
        self.dropped: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self._obs_counter: dict[str, int] = {}
        self._seen_prov: set[str] = set()
        self._memory_envelopes: dict[tuple[str, str], dict | None] = {}
        self._indexed_observations = None
        self._observation_count = 0
        self._observation_by_id = {}

    def memory_envelope(self, digest: str, lane: str) -> dict | None:
        key = (digest, lane)
        if key not in self._memory_envelopes:
            self._memory_envelopes[key] = source_memory.read_evidence_envelope(
                digest, lane, self.memory_root)
        return self._memory_envelopes[key]

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
                     producer: str,
                     evidence: evidence_contract.EvidenceRecord | None = None,
                     observation_id: str | None = None,
                     ) -> str | None:
        ticks = sec_to_ticks(start, end)
        if ticks is None:
            self.dropped.append({"digest": digest, "lane": lane,
                                 "modality": modality,
                                 "reason": "non-positive-interval",
                                 "start": start, "end": end})
            return None
        # FI-03 contract audit: a mapped modality files a measurement,
        # never an evaluation. Unmapped modalities pass through; the
        # check raises only on a genuine type violation.
        evidence_contract.audit_legacy_observation(modality, payload)
        envelope = self.memory_envelope(digest, lane)
        if envelope is not None:
            payload = {**payload, "source_memory_envelope": envelope}
        if evidence is not None:
            native = evidence_contract.to_rust_evidence(evidence)
            has_basis = (envelope is not None
                         or lane not in source_memory.SLOT_BY_LANE)
            if envelope is None:
                if lane not in source_memory.SLOT_BY_LANE:
                    envelope = {
                        "source_stream_id": _stream_id(digest),
                        "sampling_method": evidence.method,
                        "sample_resolution": evidence.sample_resolution,
                        "coverage_intervals": [{
                            "clock_id": clock_id,
                            "start": ticks[0], "end": ticks[1]}],
                        "coverage_status": evidence.coverage.value,
                        "verified_status": ("verified" if payload.get("verified")
                                            else "unverified"),
                        "producer": producer,
                        "producer_version": evidence.producer_version or "1",
                    }
                else:
                    envelope = {
                        "source_stream_id": None,
                        "sampling_method": None,
                        "sample_resolution": None,
                        "coverage_intervals": [],
                        "coverage_status": "unknown",
                        "verified_status": "unknown",
                        "producer": None,
                        "producer_version": None,
                    }
            # Only the spans overlapping this record ride its native
            # contract; the full lane basis stays in the payload's
            # source_memory_envelope. Trimming loses no absence claim:
            # evidence_query only trusts coverage beyond the record's own
            # window when the record itself already spans the scope.
            temporal_coverage = []
            for covered in envelope["coverage_intervals"]:
                if (isinstance(covered, dict) and covered.get("clock_id")
                        and isinstance(covered.get("start"), int)
                        and isinstance(covered.get("end"), int)):
                    if (covered.get("clock_id") == clock_id
                            and covered["start"] < ticks[1]
                            and covered["end"] > ticks[0]):
                        temporal_coverage.append(covered)
                elif (isinstance(covered, dict)
                      and isinstance(covered.get("start_s"), (int, float))
                      and isinstance(covered.get("end_s"), (int, float))):
                    covered_ticks = sec_to_ticks(covered["start_s"], covered["end_s"])
                    if (covered_ticks and covered_ticks[0] < ticks[1]
                            and covered_ticks[1] > ticks[0]):
                        temporal_coverage.append({
                            "clock_id": clock_id, "start": covered_ticks[0],
                            "end": covered_ticks[1]})
            native.update({
                "source_stream_id": envelope["source_stream_id"],
                "sampling_method": envelope["sampling_method"],
                "sample_resolution": (envelope["sample_resolution"]
                                      or evidence.sample_resolution)
                                      if has_basis else None,
                "temporal_coverage": temporal_coverage,
                "coverage_status": envelope["coverage_status"],
                "verified_status": envelope["verified_status"],
                "producer": envelope["producer"],
                "producer_version": envelope["producer_version"],
            })
            # Preserve the recorded analysis basis without inventing
            # preprocessing parameters absent from a legacy envelope.
            derivation_basis = dict(envelope)
            derivation_basis.update({
                "producer": (derivation_basis.get("producer")
                             or native.get("producer") or producer),
                "producer_version": (derivation_basis.get("producer_version")
                                     or native.get("producer_version")
                                     or (evidence.producer_version
                                         if evidence is not None else None)
                                     or "unknown"),
                "sampling_method": (derivation_basis.get("sampling_method")
                                    or native.get("sampling_method")
                                    or native.get("method")),
                "sample_resolution": (derivation_basis.get("sample_resolution")
                                      or native.get("sample_resolution")),
                "coverage_intervals": (derivation_basis.get("coverage_intervals")
                                       or native.get("temporal_coverage") or []),
                "coverage_status": (derivation_basis.get("coverage_status")
                                    or native.get("coverage_status", "unknown")),
                "source_digest": digest,
            })
            if (derivation_basis["producer"]
                    and derivation_basis["producer_version"]):
                native["derivation"] = normalize_envelope_preprocessing(
                    derivation_basis)
            payload = {**payload, "evidence_contract": native}
        key = f"{digest}-{lane}"
        self._obs_counter[key] = self._obs_counter.get(key, 0) + 1
        obs_id = observation_id or f"obs-{key}-{self._obs_counter[key]:04d}"
        observation = {
            "id": obs_id,
            "asset_id": asset_id,
            "interval": {"clock_id": clock_id,
                         "start": ticks[0], "end": ticks[1]},
            "modality": modality,
            "payload": payload,
            "confidence": _confidence(confidence),
            "confidence_detail": (typed_confidence(
                _confidence(confidence), "raw_score", producer)
                if _confidence(confidence) is not None else None),
            "provenance_id": self.provenance(digest, lane, producer),
        }
        self.model["observations"].append(observation)
        return obs_id

    def link(self, entity_id: str, observation_id: str, method: str,
             digest: str, lane: str, producer: str,
             confidence: Any = None, status: str = "verified", *,
             supporting_observation_ids: list[str] | None = None,
             contradicting_observation_ids: list[str] | None = None,
             scope: dict[str, Any] | None = None,
             uncertainty: float | None = None,
             track_id: str | None = None) -> str:
        link_id = f"link-{entity_id}-{observation_id}-{method}"
        observations = self.model["observations"]
        if observations is not self._indexed_observations or len(observations) < self._observation_count:
            self._indexed_observations = observations
            self._observation_count = 0
            self._observation_by_id = {}
        # A migration appends stable observation identities. Index only
        # the new suffix, including observations supplied by other lane
        # adapters, and preserve the old first-match duplicate semantics.
        for row in observations[self._observation_count:]:
            self._observation_by_id.setdefault(row["id"], row)
        self._observation_count = len(observations)
        observation = self._observation_by_id.get(observation_id)
        if observation is None:
            raise ValueError(f"identity link anchor observation missing: {observation_id}")
        interval = observation["interval"]
        link = {
            "id": link_id,
            "entity_id": entity_id,
            "observation_id": observation_id,
            "confidence": _confidence(confidence),
            "confidence_detail": (typed_confidence(
                _confidence(confidence), "raw_score", producer)
                if _confidence(confidence) is not None else None),
            "method": method,
            "status": status,
            "provenance_id": self.provenance(digest, lane, producer),
            "supporting_observation_ids": (supporting_observation_ids
                                           if supporting_observation_ids is not None
                                           else [observation_id]),
            "scope": scope or {"clock_id": interval["clock_id"],
                                "start": interval["start"],
                                "end": interval["end"]},
            "uncertainty": uncertainty,
        }
        if track_id is not None:
            link["track_id"] = track_id
        if contradicting_observation_ids:
            link["contradicting_observation_ids"] = contradicting_observation_ids
        self.model["identity_links"].append(link)
        return link_id


def _migrate_digest(builder: _Builder, digest: str, asset_uri: str,
                    clip_ids: list[str], duration: float | None,
                    root: Path | None, roster: list[dict]) -> None:
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
    m0 = source_memory.read_m0(digest, root)
    m0_freshness = "unknown"
    if m0 is not None and m0.get("content_digest") != digest:
        builder.notes.append(f"{digest[:12]}: M0 digest mismatch; refused")
        m0 = None
        m0_freshness = "stale"
    elif m0 is not None and asset_uri and Path(asset_uri).is_file():
        m0_freshness = ("fresh" if source_memory.is_fresh(m0, asset_uri)
                        else "stale")
        if m0_freshness == "stale":
            builder.notes.append(f"{digest[:12]}: M0 stale against source; refused")
            m0 = None
    if m0 is not None:
        video_streams = m0.get("video_streams") or []
        kind = "video" if video_streams else "audio"
        duration = m0.get("duration_seconds") or duration
        source_streams = m0.get("streams") or video_streams + (m0.get("audio_streams") or [])
        if not source_streams:
            source_streams = [{"index": 0, "codec_type": kind,
                               "legacy_metadata_unavailable": True}]
    else:
        kind = "other"
        source_streams = [{"index": 0, "metadata_status": "unavailable"}]
        builder.notes.append(f"{digest[:12]}: M0 absent; asset kept for "
                             f"catalog parity with kind other")
    for source_stream in source_streams:
        stream_index = int(source_stream.get("index", 0))
        capture_provenance = source_stream.get("capture_metadata_provenance")
        stream_record = {
            "id": _stream_id(digest, stream_index),
            "asset_id": asset_id,
            "stream_index": stream_index,
            "clock_id": clock_id,
            "metadata": source_stream,
            "source_digest": digest,
            "coverage": "analyzed" if m0 is not None else "unavailable",
            "producer_version": "ffprobe",
            "freshness": "fresh" if m0 is not None else "unknown",
        }
        if capture_provenance:
            stream_record["provenance_id"] = builder.provenance(
                digest, "m0", capture_provenance.get("producer", "ffprobe"))
        builder.model["streams"].append(stream_record)
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
            "packet_table": (m0 or {}).get("packet_table", []),
            "frame_table": (m0 or {}).get("frame_table", []),
            "timestamp_discontinuities": (m0 or {}).get(
                "timestamp_discontinuities", []),
            "file_integrity": (m0 or {}).get("file_integrity", {
                "status": "unavailable"}),
        },
        "provenance": [builder.provenance(
            digest, "m0", "library.tools.source_memory")],
    })
    _migrate_source_metadata(builder, digest, asset_id, clock_id, m0,
                             m0_freshness)

    _migrate_transcript(builder, digest, asset_id, clock_id, root)
    _migrate_speech_take(builder, digest, asset_id, clock_id, root)
    _migrate_identity(builder, digest, asset_id, clock_id, root)
    _migrate_expressions(builder, digest, asset_id, clock_id, root)
    _migrate_scenes(builder, digest, asset_id, clock_id, duration, root)
    _migrate_sound(builder, digest, asset_id, clock_id, duration, root)
    _migrate_events(builder, digest, asset_id, clock_id, duration, root,
                    roster)
    _migrate_relationships(builder, digest, asset_id, clock_id, duration,
                           root, roster)
    _migrate_multicam(builder, digest, asset_id, clock_id, duration, root)
    _migrate_saliency(builder, digest, asset_id, clock_id, root)
    _materialize_source_signals(builder, digest, asset_id, clock_id, root)


def _migrate_source_metadata(builder: _Builder, digest: str, asset_id: str,
                             clock_id: str, m0: dict | None,
                             freshness: str) -> None:
    if m0 is None:
        builder.notes.append(f"{digest[:12]}: M0 absent; no source metadata")
        return
    duration = m0.get("duration_seconds")
    ticks = sec_to_ticks(0.0, duration) if duration is not None else None
    if ticks is None:
        builder.notes.append(
            f"{digest[:12]}: M0 duration unavailable; source metadata has no interval")
        return
    streams = (m0.get("video_streams") or []) + (m0.get("audio_streams") or [])
    native = evidence_contract.new_measurement(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=ticks[0], end_tick=ticks[1],
        # Keep the native value independent from the legacy payload: the
        # evidence contract is embedded into that payload just below.
        measurement_type="media.source_metadata", value=copy.deepcopy(m0),
        method="library.tools.source_primitives",
        provenance_id=_prov_id(digest, "m0"),
        coverage=evidence_contract.CoverageKind.COMPLETE,
        coverage_samples=len(streams), coverage_sample_rate_hz=0.0)
    native = replace(native, freshness=freshness)
    builder.observation(
        digest, "m0", asset_id, clock_id, 0.0, duration,
        "source_metadata", m0, None,
        "library.tools.source_primitives", evidence=native)


def _migrate_expressions(builder: _Builder, digest: str, asset_id: str,
                         clock_id: str, root: Path | None) -> None:
    from library.tools import expression_classifier, footage_analysis

    doc = expression_classifier.read_m3c(digest, root)
    if doc is None:
        builder.notes.append(f"{digest[:12]}: M3c absent or unreadable")
        return
    lane = next(item for item in footage_analysis.LANES
                if item.name == "expressions")
    if not footage_analysis.slot_is_fresh(lane, digest, root):
        builder.notes.append(
            f"{digest[:12]}: M3c stale; method or M2/M3 input changed, refused")
        return
    from library.tools.person_measurements import read_m3
    m3 = read_m3(digest, root)
    m3_frames = {round(float(frame["t"]), 3): frame
                 for frame in (m3 or {}).get("frames") or []
                 if isinstance(frame, dict) and "t" in frame}
    method = doc["instrument"]["method"]
    for index, frame in enumerate(doc["frames"]):
        timestamp = float(frame["t"])
        if not math.isfinite(timestamp) or timestamp < 0:
            continue
        sample_start = math.floor(timestamp * TICKS_PER_SECOND)
        bounds = (sample_start, sample_start + 1)
        faces = frame["faces"]
        source_frame = m3_frames.get(round(timestamp, 3))
        source_faces = (source_frame["faces"] if source_frame else None)
        if faces:
            coverage = (evidence_contract.CoverageKind.COMPLETE
                        if source_faces is not None
                        and len(faces) >= len(source_faces)
                        else evidence_contract.CoverageKind.SAMPLED)
            record = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                measurement_type="image.expression_frame", value=faces,
                method=method, provenance_id=_prov_id(digest, "m3c"),
                coverage=coverage, coverage_samples=len(faces),
                coverage_sample_rate_hz=0.0)
        elif source_faces == []:
            record = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                measurement_type="image.expression_frame", value=[],
                method=method, provenance_id=_prov_id(digest, "m3c"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=0, coverage_sample_rate_hz=0.0)
        else:
            record = evidence_contract.EvidenceRecord(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                target_entity_id=None, target_region=None,
                evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                measurement_type="image.expression_frame", value=None,
                unit="labels", method=method, sample_resolution=None,
                coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                coverage_samples=0, coverage_sample_rate_hz=0.0,
                provenance_id=_prov_id(digest, "m3c"),
                processing_status=evidence_contract.ProcessingStatus.INCONCLUSIVE)
        record = replace(
            record, freshness="fresh",
            references=({"kind": "source_frame",
                         "stream_id": _stream_id(digest),
                         "frame_index": index},))
        builder.observation(
            digest, "m3c", asset_id, clock_id,
            bounds[0] / TICKS_PER_SECOND,
            bounds[1] / TICKS_PER_SECOND, "expression_frame",
            {"faces": faces, "sample_index": index}, None,
            "library.tools.expression_classifier", evidence=record)


def _migrate_saliency(builder: _Builder, digest: str, asset_id: str,
                      clock_id: str, root: Path | None) -> None:
    """Migrate only current M3e samples; stale method/input records refuse."""
    from library.tools import attention_saliency, footage_analysis

    doc = attention_saliency.read_m3e(digest, root)
    if doc is None:
        builder.notes.append(f"{digest[:12]}: M3e absent or unreadable")
        return
    lane = next(item for item in footage_analysis.LANES
                if item.name == "saliency")
    if not footage_analysis.slot_is_fresh(lane, digest, root):
        builder.notes.append(
            f"{digest[:12]}: M3e stale; method or M2 input changed, refused")
        return
    instrument = doc.get("instrument")
    if not isinstance(instrument, dict) or not instrument:
        builder.notes.append(f"{digest[:12]}: M3e has no method; refused")
        return
    method = instrument["method"]
    samples = doc["samples"]
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or "t" not in sample:
            builder.dropped.append({"digest": digest, "lane": "m3e",
                                    "reason": "invalid-sample",
                                    "sample_index": index})
            continue
        try:
            timestamp = float(sample["t"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(timestamp) or timestamp < 0:
            continue
        sample_start = math.floor(timestamp * TICKS_PER_SECOND)
        bounds = (sample_start, sample_start + 1)
        boxes = sample["boxes"]
        if boxes is None:
            record = evidence_contract.EvidenceRecord(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                target_entity_id=None, target_region=None,
                evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                measurement_type="image.salient_boxes", value=None,
                unit="normalized", method=method, sample_resolution=None,
                coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                coverage_samples=0, coverage_sample_rate_hz=0.0,
                provenance_id=_prov_id(digest, "m3e"),
                processing_status=evidence_contract.ProcessingStatus.UNAVAILABLE,
                references=({"kind": "source_frame",
                             "stream_id": _stream_id(digest),
                             "frame_index": index},),
                freshness="fresh")
            payload_boxes = None
        else:
            record = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                measurement_type="image.salient_boxes", value=boxes,
                method=method, provenance_id=_prov_id(digest, "m3e"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            record = replace(
                record, freshness="fresh",
                references=({"kind": "source_frame",
                             "stream_id": _stream_id(digest),
                             "frame_index": index},))
            payload_boxes = boxes
        builder.observation(
            digest, "m3e", asset_id, clock_id,
            bounds[0] / TICKS_PER_SECOND,
            bounds[1] / TICKS_PER_SECOND, "saliency",
            {"boxes": payload_boxes, "sample_index": index}, None,
            "library.tools.attention_saliency", evidence=record)


def _migrate_transcript(builder: _Builder, digest: str, asset_id: str,
                        clock_id: str, root: Path | None) -> None:
    doc, status = source_memory.read_m1(digest, root)
    if doc is None or status != "fresh":
        builder.notes.append(f"{digest[:12]}: M1 {status}; no speech "
                             f"observations")
        return
    instrument = doc.get("instrument")
    if not isinstance(instrument, dict) or not instrument:
        builder.notes.append(f"{digest[:12]}: M1 has no method; refused")
        return
    method = json.dumps(instrument, sort_keys=True, separators=(",", ":"))
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
                    payload = {"text": text, "utterance_index": u_idx,
                               "word_count": len(words)}
                    native = evidence_contract.new_measurement(
                        asset_id=asset_id, clock_id=clock_id,
                        start_tick=ticks[0], end_tick=ticks[1],
                        measurement_type="speech.utterance", value=payload,
                        method=method,
                        provenance_id=_prov_id(digest, "m1"),
                        coverage=evidence_contract.CoverageKind.COMPLETE,
                        coverage_samples=len(words),
                        coverage_sample_rate_hz=0.0)
                    builder.model["observations"].append({
                        "id": obs_id,
                        "asset_id": asset_id,
                        "interval": {"clock_id": clock_id,
                                     "start": ticks[0], "end": ticks[1]},
                        "modality": "speech",
                        "payload": {**payload,
                                    "evidence_contract":
                                        evidence_contract.to_rust_evidence(
                                            native)},
                        "confidence": None,
                        "provenance_id": builder.provenance(
                            digest, "m1",
                            "library.tools.source_memory:M1"),
                    })
    for u_idx, utt in enumerate(doc.get("utterances") or []):
        for word in utt.get("words") or []:
            if not isinstance(word, dict):
                continue
            ticks = sec_to_ticks(word.get("start"), word.get("end"))
            if ticks is None:
                builder.observation(
                    digest, "m1", asset_id, clock_id,
                    word.get("start"), word.get("end"), "speech_word",
                    {"text": word.get("text", word.get("word")),
                     "utterance_index": u_idx}, word.get("confidence"),
                    "library.tools.source_memory:M1")
                continue
            native = evidence_contract.migrate_m1_word(
                word, asset_id, clock_id, _prov_id(digest, "m1"), method,
                utterance_index=u_idx)
            builder.observation(
                digest, "m1", asset_id, clock_id,
                word.get("start"), word.get("end"), "speech_word",
                {"text": word.get("text", word.get("word")),
                 "utterance_index": u_idx},
                word.get("confidence"),
                "library.tools.source_memory:M1", evidence=native)


SPEECH_TAKE_METHOD = "speech-take-v1 over M1 transcript words"
"""Method stamp for FI-13 derived speech/take records: a deterministic
computation over M1 words, never a new instrument. The transcript run
it read travels on the shared ``prov-{digest}-m1`` provenance; a stale
M1 refuses the whole derivation rather than re-stamping old words."""


def _speech_take_native(builder: _Builder, digest: str, asset_id: str,
                        clock_id: str, start: float, end: float,
                        modality: str, measurement_type: str, value: Any,
                        samples: int) -> evidence_contract.EvidenceRecord | None:
    """One FI-13 native record, or None (with a dropped row) when the
    span cannot sit on the clock. Callers that only emit positive spans
    still route through here so the refusal path is one place."""
    ticks = sec_to_ticks(start, end)
    if ticks is None:
        builder.dropped.append({"digest": digest, "lane": "m1",
                                "modality": modality,
                                "reason": "non-positive-interval",
                                "start": start, "end": end})
        return None
    record = evidence_contract.new_measurement(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=ticks[0], end_tick=ticks[1],
        measurement_type=measurement_type, value=value,
        method=SPEECH_TAKE_METHOD, provenance_id=_prov_id(digest, "m1"),
        coverage=evidence_contract.CoverageKind.COMPLETE,
        coverage_samples=samples, coverage_sample_rate_hz=0.0)
    return replace(record, freshness="fresh")


def _measured_sound_events(digest: str,
                            root: Path | None) -> list[dict] | None:
    """This source's measured non-speech events, or None.

    None is "not measured": the M5 slot is absent, stale, or recorded
    as an unmeasured refusal. The pause reading treats the difference
    between "measured, and there was nothing there" and "nobody
    looked" as the difference between an answer and no answer.
    """
    from library.tools.analysis import sound_event_pipeline

    m5 = source_memory.read_sound(digest, root)
    if not isinstance(m5, dict):
        return None
    method = m5.get("method")
    if (method != sound_event_pipeline.METHOD
            or m5.get("status") != "measured"
            or _memory_freshness(m5, digest) == "stale"):
        return None
    events = m5.get("sound_events")
    return events if isinstance(events, list) else None


def _measured_speakers(digest: str, utterances: list[dict],
                       root: Path | None) -> dict | None:
    """This source's speaker attribution per utterance, or None.

    The M1b slot carries the diarization lane's voice tracks. None when
    the lane never ran, or when no track touches an utterance: that is
    "nobody separated the voices", not "one voice".
    """
    doc = source_memory.read_speakers(digest, root)
    if not isinstance(doc, dict):
        return None
    tracks = doc.get("speakers")
    if not isinstance(tracks, list):
        return None
    return pause_class.speakers_from_voice_tracks(utterances, tracks)


def _migrate_speech_take(builder: _Builder, digest: str, asset_id: str,
                         clock_id: str, root: Path | None) -> None:
    """FI-13 per-source speech/take records: rate, pause, disfluency,
    repetition and completeness over every M1 utterance, plus
    same-source take links. Both instances of every take stay as full
    utterance observations (migrated by ``_migrate_transcript``); the
    links only point at them.

    A stale or method-less M1 refuses the derivation: derived numbers
    off a superseded transcript would disagree with the words beside
    them. Dropped utterances land in the report, never in silence.
    """
    doc, status = source_memory.read_m1(digest, root)
    if doc is None or status != "fresh":
        builder.notes.append(f"{digest[:12]}: M1 {status}; no speech/take "
                             f"records")
        return
    instrument = doc.get("instrument")
    if not isinstance(instrument, dict) or not instrument:
        builder.notes.append(f"{digest[:12]}: M1 has no method; speech/take "
                             f"records refused")
        return
    utterances, dropped = speech_take.utterance_spans(doc)
    for entry in dropped:
        builder.dropped.append({"digest": digest, "lane": "m1",
                                "modality": "speech_take",
                                **entry})
    for utterance in utterances:
        start, end = utterance["start"], utterance["end"]
        rate = speech_take.rate_of(utterance)
        native = _speech_take_native(
            builder, digest, asset_id, clock_id, start, end,
            "speech_rate", "speech.rate", rate, rate["word_count"])
        if native is not None:
            builder.observation(
                digest, "m1", asset_id, clock_id, start, end,
                "speech_rate", dict(rate), None,
                "library.tools.speech_take", evidence=native)
        completeness = speech_take.completeness_of(utterance)
        native = _speech_take_native(
            builder, digest, asset_id, clock_id, start, end,
            "speech_completeness", "speech.completeness",
            dict(completeness), 1)
        if native is not None:
            builder.observation(
                digest, "m1", asset_id, clock_id, start, end,
                "speech_completeness", dict(completeness), None,
                "library.tools.speech_take", evidence=native)
        disfluencies = speech_take.disfluencies_in(utterance)
        native = _speech_take_native(
            builder, digest, asset_id, clock_id, start, end,
            "speech_disfluency", "speech.disfluency",
            list(disfluencies), len(disfluencies))
        if native is not None:
            builder.observation(
                digest, "m1", asset_id, clock_id, start, end,
                "speech_disfluency", {"disfluencies": disfluencies,
                                      "utterance_index":
                                      utterance["utterance_index"]},
                None, "library.tools.speech_take", evidence=native)
        repeat = speech_take.repetition_in(utterance)
        native = _speech_take_native(
            builder, digest, asset_id, clock_id, start, end,
            "speech_repetition", "speech.repetition",
            copy.deepcopy(repeat), 1 if repeat is not None else 0)
        if native is not None:
            builder.observation(
                digest, "m1", asset_id, clock_id, start, end,
                "speech_repetition", {"repeat": repeat,
                                      "utterance_index":
                                      utterance["utterance_index"]},
                None, "library.tools.speech_take", evidence=native)
    pauses = speech_take.pauses_between(utterances)
    pause_readings = pause_class.classify_pauses(
        pauses, utterances=utterances,
        sound_events=_measured_sound_events(digest, root),
        speakers=_measured_speakers(digest, utterances, root))
    for pause, reading in zip(pauses, pause_readings):
        value = dict(pause)
        value["reading"] = reading
        native = _speech_take_native(
            builder, digest, asset_id, clock_id, pause["start"],
            pause["end"], "speech_pause", "speech.pause", value, 1)
        if native is not None:
            builder.observation(
                digest, "m1", asset_id, clock_id, pause["start"],
                pause["end"], "speech_pause", value, None,
                "library.tools.speech_take", evidence=native)
    for link in speech_take.same_source_links(digest, utterances):
        anchor = link["utterance_a"]
        native = _speech_take_native(
            builder, digest, asset_id, clock_id, anchor["start_s"],
            anchor["end_s"], "speech_take_link", "speech.take_link",
            copy.deepcopy(link), 1)
        if native is not None:
            builder.observation(
                digest, "m1", asset_id, clock_id, anchor["start_s"],
                anchor["end_s"], "speech_take_link", copy.deepcopy(link),
                None, "library.tools.speech_take", evidence=native)


def _migrate_take_links(builder: _Builder, digests: list[str],
                        root: Path | None) -> None:
    """FI-13 cross-source take links: identical/rephrased/repeated
    utterances across digests, anchored on the lesser (digest, index).

    A digest without a fresh M1 contributes nothing and is noted; a
    pair with no shared M6 group still links (text needs no clock)
    with an explicit unmeasured uncertainty basis. Both instances stay
    untouched: the link cites utterance indexes, never winning spans.
    """
    by_digest: dict[str, list[dict]] = {}
    clocks: dict[str, dict | None] = {}
    for digest in digests:
        doc, status = source_memory.read_m1(digest, root)
        if doc is None or status != "fresh":
            builder.notes.append(f"{digest[:12]}: M1 {status}; no "
                                 f"cross-source take links")
            continue
        utterances, _ = speech_take.utterance_spans(doc)
        by_digest[digest] = utterances
        clocks[digest] = conversation_clock.read_clock(digest, root)
    for link in speech_take.cross_source_links(by_digest, clocks):
        anchor = link["utterance_a"]
        digest = anchor["digest"]
        native = _speech_take_native(
            builder, digest, _asset_id(digest), _clock_id(digest),
            anchor["start_s"], anchor["end_s"], "speech_take_link",
            "speech.take_link", copy.deepcopy(link), 1)
        if native is not None:
            builder.observation(
                digest, "m1", _asset_id(digest), _clock_id(digest),
                anchor["start_s"], anchor["end_s"], "speech_take_link",
                copy.deepcopy(link), None, "library.tools.speech_take",
                evidence=native)


def _identity_docs(digest: str, root: Path | None) -> tuple[dict | None, dict | None]:
    """(M3b identity, M1b speakers): the voices live in both slots from
    one measurement, so the migration reads M3b first and treats M1b as
    the same-measurement copy, never a second voice population."""
    identity = person_entity.read_identity(digest, root)
    speakers = source_memory.read_speakers(digest, root)
    return identity, speakers


def _migrate_identity(builder: _Builder, digest: str, asset_id: str,
                      clock_id: str, root: Path | None) -> None:
    from library.tools import footage_analysis

    identity, speakers = _identity_docs(digest, root)
    if identity is None and speakers is None:
        builder.notes.append(f"{digest[:12]}: M3b/M1b absent; no "
                             f"face or voice observations")
        return
    lane = next(item for item in footage_analysis.LANES
                if item.name == "identity")
    identity_fresh = (identity is not None
                      and footage_analysis.slot_is_fresh(lane, digest, root))
    if identity is not None and not identity_fresh:
        builder.notes.append(f"{digest[:12]}: M3b stale against M2 input; "
                             "native evidence refused")
    instrument = ((identity or {}).get("instrument")
                  or (speakers or {}).get("instrument") or {})
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
                # Lane 3: a diarized voice track holds for its source
                # asset until linked to a roster person.
                "identity_scope": "asset",
            })
        for turn in voice.get("spans") or []:
            start, end = (turn if isinstance(turn, (list, tuple)) and len(turn) == 2
                          else (None, None))
            method = instrument.get("voice")
            ticks = sec_to_ticks(start, end)
            if ticks is None:
                builder.observation(
                    digest, voice_lane, asset_id, clock_id, start, end,
                    "voice_turn", {"voice_track": track_id}, None,
                    "library.tools.person_entity")
                continue
            native = (evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="person.voice_turn",
                value={"voice_track": track_id}, method=str(method),
                provenance_id=_prov_id(digest, voice_lane),
                coverage=evidence_contract.CoverageKind.SAMPLED,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
                       if identity_fresh and method else None)
            if native is not None:
                native = replace(native, freshness="fresh")
            obs_id = builder.observation(
                digest, voice_lane, asset_id, clock_id, start, end,
                "voice_turn", {"voice_track": track_id},
                None, "library.tools.person_entity", evidence=native)
            if obs_id is not None:
                builder.link(speaker_id, obs_id, "diarization",
                             digest, voice_lane,
                             "library.tools.person_entity",
                             confidence=_confidence(
                                 voice.get("silhouette")))


def _face_person(builder_persons: list[dict], digest: str,
                 track_id: str) -> str | None:
    for person in builder_persons:
        for track in person.get("face_tracks") or []:
            if (track.get("content_digest") == digest
                    and track.get("track_id") == track_id):
                return person["person_id"]
    return None


def _face_cluster_confidence(roster: list[dict], digest: str,
                             track_id: str,
                             track_embedding: Any) -> float | None:
    """The ArcFace cosine of one face track against its roster person's
    centroid: the clustering's own score for the link. None when the
    track has no roster person or either embedding is unusable - an
    absent score stays absent, never a default."""
    person_id = _face_person(roster, digest, track_id)
    if person_id is None:
        return None
    person = next((p for p in roster if p.get("person_id") == person_id),
                  None)
    if person is None:
        return None
    return _confidence(_embedding_cosine(track_embedding,
                                         person.get("face_embedding")))


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
    from library.tools import footage_analysis
    lane = next(item for item in footage_analysis.LANES
                if item.name == "identity")
    identity_fresh = footage_analysis.slot_is_fresh(lane, digest, root)
    if not identity_fresh:
        builder.notes.append(f"{digest[:12]}: M3b stale against M2 input; "
                             "native face evidence refused")
    instrument = identity["instrument"]
    method = instrument.get("face")
    resolution = instrument.get("frame_pixels")
    native_face_ready = (bool(method) and isinstance(resolution, list)
                         and len(resolution) == 2)
    if identity_fresh and not native_face_ready:
        builder.notes.append(f"{digest[:12]}: M3b face lacks method or "
                             "resolution; native evidence refused")
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
            try:
                native = (evidence_contract.migrate_m3_face_span(
                    span, track_id, asset_id, clock_id,
                    _prov_id(digest, "m3b"), str(method), resolution)
                          if identity_fresh and native_face_ready else None)
                if native is not None:
                    native = replace(native, freshness="fresh")
            except (ValueError, TypeError) as exc:
                builder.dropped.append({"digest": digest, "lane": "m3b",
                                        "track": track_id,
                                        "reason": f"invalid-face-evidence: {exc}"})
                continue
            obs_id = builder.observation(
                digest, "m3b", asset_id, clock_id,
                span.get("start"), span.get("end"), "face",
                {"face_track": track_id, "box": span.get("box")},
                span.get("det_score"), "library.tools.person_entity",
                evidence=native)
            if obs_id is not None and person_id is not None:
                builder.link(person_id, obs_id, "face_embedding_cluster",
                             digest, "m3b", "library.tools.person_entity",
                             confidence=_face_cluster_confidence(
                                 roster, digest, track_id,
                                 face.get("embedding")))
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


def _migrate_voice_matches(builder: _Builder, roster: list[dict]) -> None:
    """Cross-source voice-to-person links: a voice track no face in its
    own source claims, matched at the measured threshold to a voice this
    person's face IS linked to (`resolve_person_tracks` decided it)."""
    for person in roster:
        for match in person.get("voice_matches") or []:
            digest = match["content_digest"]
            voice_provs = {_prov_id(digest, "m3b"), _prov_id(digest, "m1b")}
            for obs in builder.model["observations"]:
                if (obs["modality"] == "voice_turn"
                        and obs["provenance_id"] in voice_provs
                        and str(obs["payload"].get("voice_track"))
                        == match["voice_track"]):
                    builder.link(person["person_id"], obs["id"],
                                 "cross_source_voice_match", digest, "m3b",
                                 "library.tools.person_entity")


def _migrate_voice_match_conflicts(builder: _Builder,
                                   conflicts: list[dict],
                                   root: Path | None) -> None:
    """Keep M3b's cross-source voice conflicts as candidate identity
    links, with both matching voice observations as support and the
    competing person's face observations as contradictions.

    These links never merge people. They preserve the measured voice
    similarity for review while making the face-backed disagreement
    explicit in the canonical evidence graph.
    """
    voice_obs: dict[tuple[str, str], list[str]] = {}
    digest_by_asset = {asset["id"]: asset["id"].removeprefix("asset-")
                       for asset in builder.model["assets"]}
    for observation in builder.model["observations"]:
        if observation["modality"] != "voice_turn":
            continue
        digest = digest_by_asset[observation["asset_id"]]
        track_id = str(observation["payload"].get("voice_track"))
        voice_obs.setdefault((digest, track_id), []).append(observation["id"])

    owner_by_voice: dict[tuple[str, str], set[str]] = {}
    face_obs_by_person: dict[str, list[str]] = {}
    observations = {row["id"]: row for row in builder.model["observations"]}
    for link in builder.model["identity_links"]:
        observation = observations[link["observation_id"]]
        if link["method"] == "speech_face_cooccurrence":
            digest = digest_by_asset[observation["asset_id"]]
            track_id = str(observation["payload"].get("voice_track"))
            owner_by_voice.setdefault((digest, track_id), set()).add(
                link["entity_id"])
        elif link["method"] == "face_embedding_cluster":
            face_obs_by_person.setdefault(link["entity_id"], []).append(
                observation["id"])

    # The M3b roster reports conflicts only when a voice group also has
    # an unlinked track. Reuse its measured matcher to retain conflicts
    # where every matched voice is already attached to a face-backed
    # person as well.
    voices_by_digest: dict[str, list[dict]] = {}
    for asset in builder.model["assets"]:
        digest = digest_by_asset[asset["id"]]
        identity = person_entity.read_identity(digest, root)
        if not isinstance(identity, dict):
            continue
        eligible = []
        for voice in identity.get("voices") or []:
            if not isinstance(voice, dict):
                continue
            spans = voice.get("spans") or []
            if sum(float(end) - float(start) for start, end in spans) \
                    >= person_entity.VOICE_MATCH_MIN_SECONDS:
                eligible.append(voice)
        if eligible:
            voices_by_digest[digest] = eligible
    matches = person_entity.match_voices_across_sources(
        voices_by_digest, person_entity.VOICE_MATCH_THRESHOLD)
    for match in matches:
        left_key = (match["source_a"], match["voice_a"])
        right_key = (match["source_b"], match["voice_b"])
        left_owners = owner_by_voice.get(left_key, set())
        right_owners = owner_by_voice.get(right_key, set())
        if left_owners and right_owners and left_owners != right_owners:
            conflicts.append({
                "voice_tracks": [
                    {"content_digest": left_key[0],
                     "voice_track": left_key[1]},
                    {"content_digest": right_key[0],
                     "voice_track": right_key[1]}],
                "persons": sorted(left_owners | right_owners),
            })

    for conflict in conflicts:
        tracks = [track for track in conflict.get("voice_tracks", [])
                  if isinstance(track, dict)]
        for left_index, left in enumerate(tracks):
            left_key = (str(left.get("content_digest")),
                        str(left.get("voice_track")))
            left_obs = voice_obs.get(left_key, [])
            for right in tracks[left_index + 1:]:
                right_key = (str(right.get("content_digest")),
                             str(right.get("voice_track")))
                right_obs = voice_obs.get(right_key, [])
                for left_owner in owner_by_voice.get(left_key, set()):
                    for right_owner in owner_by_voice.get(right_key, set()):
                        if left_owner == right_owner:
                            continue
                        for entity_id, candidate_obs, matched_obs, \
                                contradicting_obs, digest in (
                                (left_owner, right_obs, left_obs,
                                 face_obs_by_person.get(right_owner, []),
                                 right_key[0]),
                                (right_owner, left_obs, right_obs,
                                 face_obs_by_person.get(left_owner, []),
                                 left_key[0])):
                            if not candidate_obs or not matched_obs:
                                continue
                            builder.link(
                                entity_id, candidate_obs[0],
                                "cross_source_voice_match_candidate",
                                digest, "m3b",
                                "library.tools.person_entity",
                                status="candidate",
                                supporting_observation_ids=(
                                    matched_obs + candidate_obs),
                                contradicting_observation_ids=(
                                    contradicting_obs))


def _migrate_scenes(builder: _Builder, digest: str, asset_id: str,
                    clock_id: str, duration: float | None,
                    root: Path | None) -> None:
    m4 = source_memory.read_scenes(digest, root)
    if m4 is None:
        builder.notes.append(f"{digest[:12]}: M4 absent; no scene "
                             f"observations")
        return
    from library.tools import source_memory as sm
    instrument = m4.get("instrument") or {}
    method = instrument.get("scene_pass")
    boundary_method = instrument.get("boundary_method")
    method_fresh = (method == sm.M4_SCENE_PASS_METHOD
                    and m4.get("status") == "measured")
    freshness = _memory_freshness(m4, digest)
    if not method_fresh:
        builder.notes.append(f"{digest[:12]}: M4 stale method or status; "
                             "native evidence refused")
    elif freshness == "stale":
        builder.notes.append(f"{digest[:12]}: M4 stale against source; native evidence refused")
    if (method_fresh and boundary_method != sm.M4_BOUNDARY_METHOD
            and m4.get("scene_boundaries")):
        builder.notes.append(f"{digest[:12]}: M4 boundary method absent or stale; "
                             "boundary evidence refused")
    scenes = m4.get("scenes") or []
    window_coverage = [row for row in m4.get("window_coverage", [])
                       if isinstance(row, dict)
                       and row.get("status") == "complete"
                       and row.get("source_digest") == digest
                       and isinstance(row.get("producer_version"), str)]
    window_versions = {row["producer_version"] for row in window_coverage}
    if len(window_versions) == 1 and method_fresh and freshness != "stale":
        provenance_id = builder.provenance(
            digest, "m4", "library.tools.analysis.vision_pipeline_v3")
        for provenance in builder.model["provenance"]:
            if provenance["id"] == provenance_id:
                provenance["producer_version"] = next(iter(window_versions))
    for window in window_coverage:
        start, end = float(window["start_s"]), float(window["end_s"])
        scoped_scenes = [scene for scene in scenes
                         if isinstance(scene, dict)
                         and float(scene.get("start", 0)) < end
                         and float(scene.get("end", 0)) > start]
        if scoped_scenes:
            continue
        ticks = sec_to_ticks(start, end)
        if ticks is None:
            continue
        version = window["producer_version"]
        native = evidence_contract.new_measurement(
            asset_id=asset_id, clock_id=clock_id,
            start_tick=ticks[0], end_tick=ticks[1],
            measurement_type="video.scene_segment", value=[], method=method,
            provenance_id=_prov_id(digest, "m4"),
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=0, coverage_sample_rate_hz=0.0)
        native = replace(native, freshness=freshness,
                          producer_version=version)
        stable_id = (f"obs-{digest}-m4-window-"
                     f"{ticks[0]}-{ticks[1]}-empty")
        empty_payload = {"scenes": []}
        if (window.get("model_provenance") is not None
                and window.get("producer_project_id")
                and window.get("producer_project_root")):
            empty_payload["model_provenance"] = window["model_provenance"]
            empty_payload["model_provenance_source"] = {
                "project_id": window["producer_project_id"],
                "project_root": window["producer_project_root"],
            }
        builder.observation(
            digest, "m4", asset_id, clock_id, start, end, "scene",
            empty_payload, None,
            "library.tools.analysis.vision_pipeline_v3", evidence=native,
            observation_id=stable_id)
    if (not scenes and not window_coverage and duration and method_fresh
            and freshness != "stale"):
        ticks = sec_to_ticks(0.0, duration)
        if ticks is not None:
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="video.scene_segment", value=[],
                method=method, provenance_id=_prov_id(digest, "m4"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=0, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness)
            builder.observation(
                digest, "m4", asset_id, clock_id, 0.0, duration,
                "scene", {"scenes": [],
                          "scene_boundaries": copy.deepcopy(
                              m4.get("scene_boundaries") or [])}, None,
                "library.tools.analysis.vision_pipeline_v3", evidence=native)
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        native = None
        start, end = scene.get("start"), scene.get("end")
        ticks = sec_to_ticks(start, end)
        if method_fresh and freshness != "stale" and ticks is not None:
            scene_version = next((window["producer_version"]
                                  for window in window_coverage
                                  if float(scene.get("start", 0)) < float(window["end_s"])
                                  and float(scene.get("end", 0)) > float(window["start_s"])),
                                 None)
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="video.scene_segment",
                value=copy.deepcopy(scene), method=method,
                provenance_id=_prov_id(digest, "m4"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness,
                             producer_version=scene_version)
        stable_id = None
        model_window = next((window for window in window_coverage
                             if float(scene.get("start", 0)) < float(window["end_s"])
                             and float(scene.get("end", 0)) > float(window["start_s"])),
                            None)
        model_record = (model_window.get("model_provenance")
                        if model_window
                        and model_window.get("producer_project_id")
                        and model_window.get("producer_project_root")
                        else None)
        if ticks is not None:
            stable_id = (f"obs-{digest}-m4-scene-"
                         f"{ticks[0]}-{ticks[1]}-"
                         f"{hashlib.sha256(json.dumps(scene, sort_keys=True).encode()).hexdigest()[:12]}")
        scene_payload = {k: scene.get(k) for k in
                         ("location", "type", "lighting", "notable_features")}
        if model_record is not None:
            scene_payload["model_provenance"] = model_record
            scene_payload["model_provenance_source"] = {
                    "project_id": model_window["producer_project_id"],
                    "project_root": model_window["producer_project_root"],
            }
        builder.observation(
            digest, "m4", asset_id, clock_id,
            start, end, "scene",
            scene_payload,
            None, "library.tools.analysis.vision_pipeline_v3",
            evidence=native, observation_id=stable_id)
    if (method_fresh and boundary_method == sm.M4_BOUNDARY_METHOD
            and freshness != "stale"):
        boundaries = m4.get("scene_boundaries") or []
        if not boundaries and duration:
            ticks = sec_to_ticks(0.0, duration)
            if ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type="video.scene_boundary", value=[],
                    method=str(boundary_method),
                    provenance_id=_prov_id(digest, "m4"),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness=freshness)
                builder.observation(
                    digest, "m4", asset_id, clock_id, 0.0, duration,
                    "scene_boundary", {"boundaries": []}, None,
                    "library.tools.analysis.vision_pipeline_v3",
                    evidence=native)
        for boundary in boundaries:
            if not isinstance(boundary, dict):
                continue
            try:
                at = float(boundary.get("time", boundary.get("timestamp")))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(at) or at < 0:
                continue
            start_tick = math.floor(at * TICKS_PER_SECOND)
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=start_tick, end_tick=start_tick + 1,
                measurement_type="video.scene_boundary",
                value=copy.deepcopy(boundary), method=str(boundary_method),
                provenance_id=_prov_id(digest, "m4"),
                coverage=evidence_contract.CoverageKind.SAMPLED,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness)
            builder.observation(
                digest, "m4", asset_id, clock_id, at,
                at + (1 / TICKS_PER_SECOND), "scene_boundary",
                {"time": at}, None,
                "library.tools.analysis.vision_pipeline_v3",
                evidence=native)

    # VI-09's measured hierarchy is carried by M4 and projected as
    # interval observations. Its profile interpretation cites those
    # observations; source measurement IDs remain in the payload and are
    # not promoted to Project Model evidence IDs without a native record.
    hierarchy = m4.get("scene_cinematography")
    if (method_fresh and freshness != "stale"
            and isinstance(hierarchy, dict)
            and hierarchy.get("status") == "measured"
            and hierarchy.get("method") == "scene-cinematography-measurements-v1"):
        hierarchy_items = {
            collection: hierarchy[collection]
            for collection in ("scenes", "shots", "subshots", "sequences")
        }
        event_ids = {
            (collection, item["id"]):
                f"event-cinema-{digest}-{collection}-{item['id']}"
            for collection, items in hierarchy_items.items()
            for item in items
        }
        scene_by_id = {item["id"]: item for item in hierarchy_items["scenes"]}
        shot_by_id = {item["id"]: item for item in hierarchy_items["shots"]}
        duration = float(hierarchy.get("duration", 0.0))

        def hierarchy_parents(collection: str, item: dict) -> list[str]:
            if collection == "scenes":
                return [event_ids[("sequences", sequence["id"])]
                        for sequence in hierarchy_items["sequences"]
                        if item["id"] in sequence.get("scene_ids", [])]
            if collection == "shots":
                return [event_ids[("scenes", scene_id)]
                        for scene_id, scene in scene_by_id.items()
                        if scene["start"] <= item["start"]
                        and scene["end"] >= item["end"]]
            if collection == "subshots":
                shot_id = item.get("parent_shot_id")
                return ([event_ids[("shots", shot_id)]]
                        if shot_id in shot_by_id else [])
            return []

        def boundary_record(status: str) -> dict:
            return {"status": status, "uncertainty_ticks": None}

        for collection in ("sequences", "scenes", "shots", "subshots"):
            for item in hierarchy_items[collection]:
                cited_ids = set(item.get("boundary_basis_ids", []))
                cited_ids.update(item.get("basis_measurement_ids", []))
                for attribute in item.get("attributes", {}).values():
                    cited_ids.update(attribute.get("evidence_ids", []))
                measurement_catalog = {measurement["id"]: measurement
                                       for measurement in hierarchy["measurement_catalog"]}
                observation_id = builder.observation(
                    digest, "vi09", asset_id, clock_id,
                    item["start"], item["end"], "cinematography_interval",
                    {"level": collection[:-1], "record": copy.deepcopy(item),
                     "source_measurements": [copy.deepcopy(measurement_catalog[evidence_id])
                                             for evidence_id in sorted(cited_ids)
                                             if evidence_id in measurement_catalog]},
                    None, "library.tools.scene_cinematography")
                if observation_id is not None:
                    builder.model["observations"][-1]["certainty"] = item["certainty"]
                    interval_ticks = sec_to_ticks(item["start"], item["end"])
                    if interval_ticks is not None:
                        measured_starts = {
                            round(float(candidate["start"]), 6)
                            for candidate in hierarchy_items[collection]
                            if candidate.get("boundary_basis_ids")
                        }
                        start_status = (
                            "exact" if float(item["start"]) == 0.0 else
                            "estimated" if round(float(item["start"]), 6) in measured_starts
                            else "open")
                        end_status = (
                            "exact" if float(item["end"]) == duration else
                            "estimated" if round(float(item["end"]), 6) in measured_starts
                            else "open")
                        builder.model["events"].append({
                            "id": event_ids[(collection, item["id"])],
                            "interval": {"clock_id": clock_id,
                                         "start": interval_ticks[0],
                                         "end": interval_ticks[1]},
                            "kind": f"cinematography_{collection[:-1]}",
                            "parent_event_ids": hierarchy_parents(collection, item),
                            "participant_ids": [],
                            "status": "verified",
                            "certainty": item["certainty"],
                            "evidence_ids": [observation_id],
                            "confidence": None,
                            "start_boundary": boundary_record(start_status),
                            "end_boundary": boundary_record(end_status),
                        })
                if observation_id is not None and collection in ("shots", "sequences"):
                    kind = ("cinematography_profile" if collection == "shots"
                            else "same_scene_context_sequence_hypothesis")
                    objective = (
                        "describe evidence-linked measured visual attributes"
                        if collection == "shots" else
                        "group adjacent M4 scenes with matching location and type")
                    builder.model.setdefault("interpretations", []).append({
                        "id": f"interpretation-{observation_id}",
                        "kind": kind,
                        "author": "library.tools.scene_cinematography",
                        "objective": objective,
                        "policy_id": None,
                        "evidence_ids": [observation_id],
                        "scope": {"asset_id": asset_id,
                                  "interval": {"clock_id": clock_id,
                                               "start": sec_to_ticks(item["start"], item["end"])[0],
                                               "end": sec_to_ticks(item["start"], item["end"])[1]}},
                        "uncertainty": None,
                        "certainty": "interpreted",
                    })


def _materialize_source_signals(builder: _Builder, digest: str,
                                asset_id: str, clock_id: str,
                                root: Path | None) -> None:
    """Expose cut-relevant producer measurements as typed model signals.

    Signal rows retain the producer's observation id (where one exists),
    provenance and source sample references. They are projections of the
    existing measurement lanes, not a second analysis pass.
    """
    model = builder.model
    signal_ids = {signal["id"] for signal in model["signals"]}

    def append_signal(signal_id: str, measurement_type: str, unit: str,
                      start: int, end: int, samples: list[dict],
                      provenance_id: str, *, samples_ref: str | None = None,
                      source_signal_id: str | None = None,
                      estimate_status: str | None = None) -> None:
        if start >= end or signal_id in signal_ids:
            return
        signal_ids.add(signal_id)
        model["signals"].append({
            "id": signal_id,
            "asset_id": asset_id,
            "interval": {"clock_id": clock_id, "start": start, "end": end},
            "measurement_type": measurement_type,
            "unit": unit,
            "samples": samples,
            "sample_clock_id": clock_id,
            "samples_ref": samples_ref,
            "source_signal_id": source_signal_id,
            "estimate_status": estimate_status,
            "provenance_id": provenance_id,
            "processing_status": "analyzed",
            "sampling_method": "source_interval_projection",
            "sample_resolution": None,
            "coverage_intervals": [{"clock_id": clock_id,
                                    "start": start, "end": end}],
        })

    source_observations = [
        row for row in model["observations"]
        if row["asset_id"] == asset_id
        and row["provenance_id"].startswith(f"prov-{digest}-")
    ]
    # M1's word intervals are the exact source boundaries used by speech
    # planning. Their Project Model observation IDs are the measurement IDs.
    for observation in source_observations:
        if observation["modality"] == "speech_word":
            append_signal(
                observation["id"], "speech", "word",
                observation["interval"]["start"],
                observation["interval"]["end"], [],
                observation["provenance_id"])
        elif observation["modality"] == "scene_boundary":
            append_signal(
                observation["id"], "scene.boundary", "boundary",
                observation["interval"]["start"],
                observation["interval"]["end"], [],
                observation["provenance_id"])
        elif (observation["modality"] == "event_candidate"
              and observation["payload"].get("predicate") in {
                  "hand_raise", "nod", "object_pickup"}):
            append_signal(
                observation["id"], "gesture.event", "event",
                observation["interval"]["start"],
                observation["interval"]["end"], [],
                observation["provenance_id"])

    # MFA can attach a phone tier to a transcript region. Keep each phone's
    # source timing and the parent word observation that anchors that region.
    m1, m1_status = source_memory.read_m1(digest, root)
    if m1 is not None and m1_status == "fresh":
        word_rows = [row for row in source_observations
                     if row["modality"] == "speech_word"]
        phone_index = 0
        for utterance_index, utterance in enumerate(m1.get("utterances") or []):
            if not isinstance(utterance, dict):
                continue
            words = utterance.get("words") or []
            phones = list(utterance.get("phonemes") or [])
            for word in words:
                if isinstance(word, dict):
                    phones.extend(word.get("phonemes") or [])
            for phone in phones:
                if not isinstance(phone, dict):
                    continue
                ticks = sec_to_ticks(phone.get("start"), phone.get("end"))
                if ticks is None:
                    continue
                phone_index += 1
                parent = next((row for row in word_rows
                               if row["payload"].get("utterance_index")
                               == utterance_index
                               and row["interval"]["start"] <= ticks[0]
                               and ticks[1] <= row["interval"]["end"]), None)
                samples = [{"tick": ticks[0],
                            "value": phone.get("phone", phone.get("phoneme")),
                            "source_observation_id": (parent["id"]
                                                       if parent else None)}]
                append_signal(
                    f"signal-{digest}-phoneme-{phone_index:06d}",
                    "speech.phoneme", "phoneme", ticks[0], ticks[1],
                    samples, _prov_id(digest, "m1"))

    # M5 carries step 2.06's point beats and structural section intervals.
    # Only accept a measured, digest-matched music lane.
    m5 = source_memory.read_sound(digest, root)
    if (isinstance(m5, dict) and m5.get("content_digest") == digest
            and m5.get("music_status") == "measured"
            and m5.get("music_method")
            and _memory_freshness(m5, digest) != "stale"):
        duration = model["assets"][-1]["metadata"].get("duration_seconds")
        bounds = sec_to_ticks(0.0, duration) if duration else None
        for index, row in enumerate(m5.get("music_interval_evidence") or []):
            if not isinstance(row, dict) or row.get("certainty_state") != "measured":
                continue
            kind = row.get("kind")
            if kind not in {"beat", "downbeat", "music_section"}:
                continue
            start_s, end_s = row.get("start_seconds"), row.get("end_seconds")
            if kind in {"beat", "downbeat"}:
                try:
                    tick = math.floor(float(start_s) * TICKS_PER_SECOND)
                except (TypeError, ValueError):
                    continue
                start_tick, end_tick = tick, tick + 1
                measurement_type = "music.beat"
                samples = [{"tick": tick, "value": kind,
                            "source_measurement_id":
                                f"music_interval_evidence:{index}"}]
            else:
                ticks = sec_to_ticks(start_s, end_s)
                if ticks is None:
                    continue
                start_tick, end_tick = ticks
                measurement_type = "music.section"
                samples = [{"tick": start_tick,
                            "value": row.get("label"),
                            "source_measurement_id":
                                f"music_interval_evidence:{index}"}]
            if bounds is not None and end_tick > bounds[1]:
                continue
            signal_id = f"signal-{digest}-music-{index:06d}"
            append_signal(signal_id, measurement_type,
                          "beat" if measurement_type == "music.beat" else "section",
                          start_tick, end_tick, samples,
                          _prov_id(digest, "m5"))

    # VI-09 preserves each measured camera-motion sample and its source
    # measurement ID. A zero-motion sample is the producer's static class,
    # using the same 0.01 boundary as scene_cinematography._movement.
    m4 = source_memory.read_scenes(digest, root)
    hierarchy = (m4 or {}).get("scene_cinematography") or {}
    if (isinstance(hierarchy, dict)
            and hierarchy.get("status") == "measured"
            and hierarchy.get("method") == "scene-cinematography-measurements-v1"
            and _memory_freshness(m4, digest) != "stale"):
        camera_samples = []
        for measurement in hierarchy.get("measurement_catalog") or []:
            if not isinstance(measurement, dict) or measurement.get("lane") != "camera_motion":
                continue
            try:
                tick = math.floor(float(measurement["time"]) * TICKS_PER_SECOND)
            except (KeyError, TypeError, ValueError):
                continue
            value = measurement.get("value")
            if isinstance(value, dict):
                components = [value.get(key, 0.0)
                              for key in ("translation_x", "translation_y", "residual")]
                try:
                    settled = max(abs(float(component)) for component in components) < 0.01
                except (TypeError, ValueError):
                    continue
            else:
                settled = value == "static"
            camera_samples.append({"tick": tick,
                                   "value": "settled" if settled else "moving",
                                   "source_measurement_id": measurement["id"]})
        if camera_samples:
            duration = model["assets"][-1]["metadata"].get("duration_seconds")
            bounds = sec_to_ticks(0.0, duration) if duration else None
            if bounds is not None:
                camera_samples = [sample for sample in camera_samples
                                  if bounds[0] <= sample["tick"] < bounds[1]]
                append_signal(
                    f"signal-{digest}-camera-settle", "camera.settle",
                    "settled_state", bounds[0], bounds[1], camera_samples,
                    _prov_id(digest, "m4"))


def _migrate_music_signals(builder: _Builder, project_folder: str,
                           root: Path | None) -> None:
    """Migrate fresh step 2.06 beat and section measurements.

    The music track is a source asset in its own right. Its M5 interval
    record is keyed by that track's digest, so it gets its own source clock
    instead of being placed on an unrelated camera clock.
    """
    from library.tools.project_layout import Area, ProjectLayout

    analysis_path = ProjectLayout(project_folder).read_path(
        Area.MUSIC_ANALYSIS, "music_analysis.json")
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(analysis, dict) or not analysis.get("available"):
        return
    track_path = analysis.get("file")
    if not isinstance(track_path, str) or not Path(track_path).is_file():
        return
    try:
        from library.tools import footage_identity
        digest = footage_identity.fingerprint(track_path)["content_digest"]
    except (ImportError, OSError, KeyError, ValueError):
        return
    if not analysis.get("method_hash"):
        return
    music = source_memory.read_sound(digest, root)
    if (not isinstance(music, dict) or music.get("content_digest") != digest
            or music.get("music_status") != "measured"
            or not music.get("music_method")
            or _memory_freshness(music, digest) != "fresh"
            or Path(music.get("source_file", "")).resolve()
            != Path(track_path).resolve()):
        return

    asset_id, clock_id, stream_id = (
        _asset_id(digest), _clock_id(digest), _stream_id(digest))
    duration = analysis.get("duration_s")
    ticks = sec_to_ticks(0.0, duration) if duration else None
    if ticks is None:
        return
    provenance_id = builder.provenance(
        digest, "m5_music", "library.steps.step_2_06_music_analysis",
        track_path)
    if asset_id not in {asset["id"] for asset in builder.model["assets"]}:
        builder.model["clocks"].append({
            "id": clock_id,
            "domain": {"kind": "source", "asset_id": asset_id,
                       "stream_id": stream_id},
            "ticks_per_second": TICKS_PER_SECOND,
            "frame_rate": None,
        })
        builder.model["assets"].append({
            "id": asset_id, "kind": "audio", "uri": track_path,
            "content_digest": digest,
            "metadata": {"duration_seconds": duration,
                         "music_method": music["music_method"]},
            "provenance": [provenance_id],
        })
        builder.model["streams"].append({
            "id": stream_id, "asset_id": asset_id, "stream_index": 0,
            "clock_id": clock_id, "metadata": {"codec_type": "audio"},
            "source_digest": digest, "coverage": "analyzed",
            "producer_version": "step_2_06_music_analysis",
            "freshness": "fresh", "provenance_id": provenance_id,
        })

    for index, interval in enumerate(music.get("music_interval_evidence") or []):
        if not isinstance(interval, dict) or interval.get("certainty_state") != "measured":
            continue
        kind = interval.get("kind")
        start_s, end_s = interval.get("start_seconds"), interval.get("end_seconds")
        if kind in {"beat", "downbeat"}:
            try:
                tick = math.floor(float(start_s) * TICKS_PER_SECOND)
            except (TypeError, ValueError):
                continue
            start_tick, end_tick = tick, tick + 1
            measurement_type = "music.beat"
            samples = [{"tick": tick, "value": kind,
                        "source_measurement_id":
                            f"music_interval_evidence:{index}"}]
            unit = "beat"
        elif kind == "music_section":
            point = sec_to_ticks(start_s, end_s)
            if point is None:
                continue
            start_tick, end_tick = point
            measurement_type = "music.section"
            samples = [{"tick": start_tick,
                        "value": interval.get("label"),
                        "source_measurement_id":
                            f"music_interval_evidence:{index}"}]
            unit = "section"
        else:
            continue
        if end_tick > ticks[1]:
            continue
        signal_id = f"signal-{digest}-music-{index:06d}"
        if signal_id in {signal["id"] for signal in builder.model["signals"]}:
            continue
        builder.model["signals"].append({
            "id": signal_id,
            "asset_id": asset_id,
            "interval": {"clock_id": clock_id,
                         "start": start_tick, "end": end_tick},
            "measurement_type": measurement_type,
            "unit": unit,
            "samples": samples,
            "sample_clock_id": clock_id,
            "samples_ref": None,
            "source_signal_id": None,
            "estimate_status": None,
            "provenance_id": provenance_id,
            "processing_status": "analyzed",
        })


def _migrate_sound(builder: _Builder, digest: str, asset_id: str,
                   clock_id: str, duration: float | None,
                   root: Path | None) -> None:
    m5 = source_memory.read_sound(digest, root)
    if m5 is None:
        builder.notes.append(f"{digest[:12]}: M5 absent; no sound "
                             f"observations")
        return
    from library.tools.analysis import sound_event_pipeline
    method = m5.get("method")
    method_fresh = (method == sound_event_pipeline.METHOD
                    and m5.get("status") == "measured")
    is_unmeasured = (isinstance(method, str)
                     and method.startswith("unmeasured:")
                     and m5.get("status") == "unmeasured")
    freshness = _memory_freshness(m5, digest)
    if not method_fresh and not is_unmeasured:
        builder.notes.append(f"{digest[:12]}: M5 stale method; native evidence refused")
    elif freshness == "stale":
        builder.notes.append(f"{digest[:12]}: M5 stale against source; native evidence refused")
    if duration and (method_fresh or is_unmeasured) and freshness != "stale":
        ticks = sec_to_ticks(0.0, duration)
        if ticks is not None and is_unmeasured:
            native = evidence_contract.EvidenceRecord(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                target_entity_id=None, target_region=None,
                evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                measurement_type="audio.sound_event", value=None,
                unit="record", method=str(method), sample_resolution=None,
                coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                coverage_samples=0, coverage_sample_rate_hz=0.0,
                provenance_id=_prov_id(digest, "m5"),
                processing_status=evidence_contract.ProcessingStatus.UNAVAILABLE,
                freshness=freshness)
            builder.observation(
                digest, "m5", asset_id, clock_id, 0.0, duration,
                "sound_event", {"events": None, "status": "unmeasured"},
                None, "library.steps.step_1_04_temporal_index",
                evidence=native)
        elif ticks is not None and not m5.get("sound_events"):
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="audio.sound_event", value=[],
                method=str(method), provenance_id=_prov_id(digest, "m5"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=0, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness)
            builder.observation(
                digest, "m5", asset_id, clock_id, 0.0, duration,
                "sound_event", {"events": []}, None,
                "library.steps.step_1_04_temporal_index", evidence=native)
    for event in m5.get("sound_events") or []:
        if not isinstance(event, dict):
            continue
        native = None
        ticks = sec_to_ticks(event.get("start"), event.get("end"))
        if method_fresh and freshness != "stale" and ticks is not None:
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="audio.sound_event",
                value=copy.deepcopy(event), method=str(method),
                provenance_id=_prov_id(digest, "m5"),
                coverage=evidence_contract.CoverageKind.SAMPLED,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness)
        builder.observation(
            digest, "m5", asset_id, clock_id,
            event.get("start"), event.get("end"), "sound_event",
            {"label": event.get("label")}, event.get("confidence"),
            "library.steps.step_1_04_temporal_index", evidence=native)


def _m7_slot_fresh(digest: str, root: Path | None) -> bool:
    """Whether the M7 events slot is current for ``digest``.

    M7 stamps no instrument method (it is a pure join), so recency is
    the freshness signal: the events slot must exist for this digest
    and be no older than the persons/identity slots it was read from.
    """
    from library.tools import footage_analysis

    lane = next(item for item in footage_analysis.LANES
                if item.name == "events")
    try:
        return footage_analysis.slot_is_fresh(lane, digest, root)
    except OSError:
        return False


def _migrate_events(builder: _Builder, digest: str, asset_id: str,
                    clock_id: str, duration: float | None,
                    root: Path | None, roster: list[dict]) -> None:
    m7 = event_spans.read_m7(digest, root)
    if m7 is None:
        builder.notes.append(f"{digest[:12]}: M7 absent; no event spans")
        builder.notes.append(f"{digest[:12]}: M8 verdicts unread without "
                             f"their M7 candidate spans")
        return
    # read_m7 already refuses a record naming another digest; what
    # remains is checked against the live source and its inputs.
    memory_freshness = _memory_freshness(m7, digest)
    if memory_freshness == "stale":
        builder.notes.append(f"{digest[:12]}: M7 stale against source; "
                             f"native evidence refused")
        return
    slot_fresh = _m7_slot_fresh(digest, root)
    if not slot_fresh:
        builder.notes.append(f"{digest[:12]}: M7 stale against its M3/M3b "
                             f"inputs; native evidence refused")
        return
    freshness = "fresh" if memory_freshness == "fresh" else "unknown"
    try:
        verdicts = span_verification.read_verdicts(digest, root) or {}
    except (OSError, ValueError):
        verdicts = {}
    if not isinstance(verdicts, dict):
        verdicts = {}
    predicates = m7.get("predicates") or {}
    for predicate in ("speaking", "on_screen"):
        spans = (predicates.get(predicate) or {}).get("spans") or []
        if not spans and duration:
            ticks = sec_to_ticks(0.0, duration)
            if ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type=M7_PREDICATE_TYPES[predicate],
                    value=[], method=M7_METHOD,
                    provenance_id=_prov_id(digest, "m7"),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness=freshness)
                builder.observation(
                    digest, "m7", asset_id, clock_id, 0.0, duration,
                    "event_span", {"predicate": predicate,
                                   "voice_track": None,
                                   "face_track": None, "spans": []},
                    None, "library.tools.event_spans", evidence=native)
        for span in spans:
            if not isinstance(span, dict):
                continue
            native = None
            ticks = sec_to_ticks(span.get("start"), span.get("end"))
            if ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type=M7_PREDICATE_TYPES[predicate],
                    value={"predicate": predicate,
                           "voice_track": span.get("voice_track"),
                           "face_track": span.get("face_track")},
                    method=M7_METHOD, provenance_id=_prov_id(digest, "m7"),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=1, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness=freshness)
            builder.observation(
                digest, "m7", asset_id, clock_id,
                span.get("start"), span.get("end"), "event_span",
                {"predicate": predicate,
                 "voice_track": span.get("voice_track"),
                 "face_track": span.get("face_track")},
                None, "library.tools.event_spans", evidence=native)
    candidates = m7.get("candidates") or {}
    candidate_spans: list[dict] = []
    for kind, body in candidates.items():
        if not isinstance(body, dict):
            continue
        for span in body.get("spans") or []:
            if isinstance(span, dict):
                candidate_spans.append((kind, span))
    if not candidate_spans and duration:
        ticks = sec_to_ticks(0.0, duration)
        if ticks is not None:
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="event.candidate_span",
                value=[], method=M7_METHOD,
                provenance_id=_prov_id(digest, "m7"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=0, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness)
            builder.observation(
                digest, "m7", asset_id, clock_id, 0.0, duration,
                "event_candidate", {"predicate": None,
                                    "face_track": None,
                                    "verdicts": [], "verified": False,
                                    "spans": []},
                None, "library.tools.event_spans", evidence=native)
    for kind, span in candidate_spans:
        matched = _verdicts_for_span(span, verdicts)
        selected_frames = span_verification.select_frames(span)
        verified = (len({round(float(frame["t"]), 3)
                         for frame in selected_frames if "t" in frame}) >= 2
                    and any(_is_affirmative(v.get("answer")) for v in matched))
        native = None
        ticks = sec_to_ticks(span.get("start"), span.get("end"))
        if ticks is not None:
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=ticks[0], end_tick=ticks[1],
                measurement_type="event.candidate_span",
                value={"predicate": kind,
                       "face_track": span.get("face_track"),
                       "verdicts": copy.deepcopy(matched),
                       "verified": verified,
                       "method": M7_METHOD,
                       "verification_method": (
                           "span-verification-prompt-v"
                           f"{span_verification.PROMPT_VERSION}")},
                method=M7_METHOD, provenance_id=_prov_id(digest, "m7"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            native = replace(native, freshness=freshness)
        observation_id = builder.observation(
            digest, "m7", asset_id, clock_id,
            span.get("start"), span.get("end"), "event_candidate",
            {"predicate": kind,
             "face_track": span.get("face_track"),
                 "verdicts": matched,
                 "verified": verified},
            None, "library.tools.event_spans", evidence=native)
        # M7 candidates are first-class events, but remain candidates unless
        # a matched M8 verdict explicitly confirms them. The observation is
        # the evidence link; an unmatched verdict cannot promote an event.
        if observation_id is not None:
            face_track = span.get("face_track")
            participant = (_face_person(roster, digest, str(face_track))
                           if face_track else None)
            builder.model["events"].append({
                "id": f"event-{observation_id}",
                "interval": {"clock_id": clock_id,
                             "start": math.floor(float(span["start"]) * TICKS_PER_SECOND),
                             "end": math.ceil(float(span["end"]) * TICKS_PER_SECOND)},
                "kind": str(kind),
                "participant_ids": [participant] if participant else [],
                "status": "verified" if verified else "candidate",
                "certainty": "inferred",
                "evidence_ids": [observation_id],
                "confidence": None,
            })
    unmatched = _unmatched_verdicts(candidate_spans, verdicts)
    if unmatched:
        builder.notes.append(
            f"{digest[:12]}: M8 {len(unmatched)} verdict(s) match no M7 "
            f"candidate span; kept out of the model")


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


def _unmatched_verdicts(candidate_spans: list[tuple[str, dict]],
                        verdicts: dict) -> list[dict]:
    """M8 verdicts matching no candidate span's selected frames.

    Same millisecond frame-equality rule ``world_model`` and
    ``_verdicts_for_span`` use. Unmatched verdicts stay out of the
    model; the caller reports their count so a verification is never
    silently dropped.
    """
    if not isinstance(verdicts, dict) or not verdicts:
        return []
    selected_sets = []
    for _kind, span in candidate_spans:
        try:
            selected = span_verification.select_frames(span)
        except (KeyError, TypeError, ValueError):
            continue
        selected_sets.append(
            [round(float(f["t"]), 3) for f in selected
             if isinstance(f, dict) and "t" in f])
    unmatched = []
    for verdict in verdicts.values():
        if not isinstance(verdict, dict):
            continue
        frames = verdict.get("frames") or []
        try:
            key = [round(float(t), 3) for t in frames]
        except (TypeError, ValueError):
            continue
        if key not in selected_sets:
            unmatched.append(verdict)
    return unmatched


def _migrate_relationships(builder: _Builder, digest: str, asset_id: str,
                           clock_id: str, duration: float | None,
                           root: Path | None, roster: list[dict]) -> None:
    from library.tools import footage_analysis
    from library.tools.relationships import M10_METHOD

    m10 = source_memory.read_relationships(digest, root)
    if m10 is None:
        builder.notes.append(f"{digest[:12]}: M10 absent; no relationship "
                             f"candidates")
        return
    method = (m10.get("instrument") or {}).get("method")
    if method != M10_METHOD:
        builder.notes.append(f"{digest[:12]}: M10 stale method; native "
                             f"evidence refused")
        return
    if m10.get("content_digest") != digest:
        builder.notes.append(f"{digest[:12]}: M10 stale against source; "
                             f"native evidence refused")
        return
    memory_freshness = _memory_freshness(m10, digest)
    if memory_freshness == "stale":
        builder.notes.append(f"{digest[:12]}: M10 stale against source; "
                             f"native evidence refused")
        return
    lane = next(item for item in footage_analysis.LANES
                if item.name == "relationships")
    try:
        slot_fresh = footage_analysis.slot_is_fresh(lane, digest, root)
    except OSError:
        slot_fresh = False
    if not slot_fresh:
        builder.notes.append(f"{digest[:12]}: M10 stale against its "
                             f"M3/M3b/M3d inputs; native evidence refused")
        return
    freshness = "fresh" if memory_freshness == "fresh" else "unknown"
    for name in ("holding", "pointing", "looking_at"):
        spans = ((m10.get("relationships") or {}).get(name, {})
                     .get("spans") or [])
        if not spans and duration:
            ticks = sec_to_ticks(0.0, duration)
            if ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type="relation.candidate_span",
                    value=[], method=M10_METHOD,
                    provenance_id=_prov_id(digest, "m10"),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness=freshness)
                builder.observation(
                    digest, "m10", asset_id, clock_id, 0.0, duration,
                    "relationship_candidate",
                    {"relation": name, "owners": [], "spans": []},
                    None, "library.tools.relationships", evidence=native)
        for span in spans:
            if not isinstance(span, dict):
                continue
            native = None
            ticks = sec_to_ticks(span.get("start"), span.get("end"))
            if ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type="relation.candidate_span",
                    value={"relation": name,
                           "owners": copy.deepcopy(span.get("owners"))},
                    method=M10_METHOD,
                    provenance_id=_prov_id(digest, "m10"),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=1, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness=freshness)
            observation_id = builder.observation(
                digest, "m10", asset_id, clock_id,
                span.get("start"), span.get("end"),
                "relationship_candidate",
                {"relation": name, "owners": span.get("owners")},
                None, "library.tools.relationships", evidence=native)
            owners = span.get("owners") or []
            participant_ids = [
                person_id for owner in owners
                if isinstance(owner, str)
                and (person_id := _face_person(roster, digest, owner)) is not None
            ]
            # M10's owner list is only a relation when it identifies two
            # distinct people. Never invent an object target from a hand or
            # a single owner; retain those measurements as candidates above.
            if (observation_id is not None and len(participant_ids) == 2
                    and ticks is not None):
                builder.model["relations"].append({
                    "id": f"relation-{observation_id}",
                    "subject_id": participant_ids[0],
                    "predicate": ("two_hand_contact"
                                  if name == "holding"
                                  and participant_ids[0] == participant_ids[1]
                                  else str(name)),
                    "object_id": participant_ids[1],
                    "interval": {"clock_id": clock_id,
                                 "start": ticks[0], "end": ticks[1]},
                    "confidence": None,
                    "provenance_id": _prov_id(digest, "m10"),
                    "evidence_ids": [observation_id],
                    "status": "candidate",
                    "certainty": "inferred",
                    # Lane 3: owner co-presence is spatial contact
                    # evidence, so the edge is physical and stays a
                    # candidate until verified.
                    "relation_class": "physical",
                })


def _migrate_multicam(builder: _Builder, digest: str, asset_id: str,
                      clock_id: str, duration: float | None,
                      root: Path | None) -> None:
    m6 = conversation_clock.read_clock(digest, root)
    if m6 is None:
        builder.notes.append(f"{digest[:12]}: M6 absent; source stands on "
                             f"its own clock")
        return
    if m6.get("content_digest") != digest:
        builder.notes.append(f"{digest[:12]}: M6 stale against source; "
                             f"native evidence refused")
        return
    ngram_size = m6.get("ngram_size")
    if ngram_size != conversation_clock.NGRAM_SIZE:
        builder.notes.append(f"{digest[:12]}: M6 stale n-gram method; "
                             f"native evidence refused")
        return
    method = f"conversation-clock-ngram-v{ngram_size}"
    memory_freshness = _memory_freshness(m6, digest)
    if memory_freshness == "stale":
        builder.notes.append(f"{digest[:12]}: M6 stale against source; "
                             f"native evidence refused")
        return
    if not duration:
        builder.notes.append(f"{digest[:12]}: M6 present but duration "
                             f"unknown; offset kept out of the model")
        return
    freshness = "fresh" if memory_freshness == "fresh" else "unknown"
    ticks = sec_to_ticks(0.0, duration)
    native = None
    if ticks is not None:
        direct = m6.get("direct_measurement")
        native = evidence_contract.new_measurement(
            asset_id=asset_id, clock_id=clock_id,
            start_tick=ticks[0], end_tick=ticks[1],
            measurement_type="sync.multicam_offset",
            value={"group_id": m6.get("group_id"),
                   "reference_digest": m6.get("reference_digest"),
                   "offset_to_reference_seconds": m6.get(
                       "offset_to_reference_seconds"),
                   "direct_measurement": copy.deepcopy(direct)},
            method=method, provenance_id=_prov_id(digest, "m6"),
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=1, coverage_sample_rate_hz=0.0)
        native = replace(native, freshness=freshness)
    builder.observation(
        digest, "m6", asset_id, clock_id, 0.0, duration,
        "multicam_offset",
        {"group_id": m6.get("group_id"),
         "reference_digest": m6.get("reference_digest"),
         "offset_to_reference_seconds": m6.get(
             "offset_to_reference_seconds")},
        None, "library.tools.conversation_clock", evidence=native)


def _materialize_multicam_transforms(builder: _Builder,
                                     digests: list[str],
                                     durations: dict[str, float | None],
                                     root: Path | None) -> None:
    """Project directly measured M6 offsets as bounded source-clock maps.

    Transitive M6 offsets and direct offsets without a measured spread do
    not have an error bound, so they remain observations and cannot drive
    synchronized joins.
    """
    clock_ids = {clock["id"] for clock in builder.model["clocks"]}
    for digest in digests:
        m6 = conversation_clock.read_clock(digest, root)
        if not isinstance(m6, dict) or m6.get("content_digest") != digest:
            continue
        reference = m6.get("reference_digest")
        if (not isinstance(reference, str) or reference == digest
                or _clock_id(reference) not in clock_ids):
            continue
        direct = m6.get("direct_measurement")
        if not isinstance(direct, dict):
            continue
        try:
            offset = float(m6["offset_to_reference_seconds"])
            p5, p95 = float(direct["p5_seconds"]), float(direct["p95_seconds"])
            drift = abs(float(direct.get("drift_seconds") or 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if (not all(math.isfinite(value) for value in (offset, p5, p95, drift))
                or p95 < p5 or direct.get("drift_exceeds_tolerance") is True):
            continue
        duration = durations.get(digest)
        reference_duration = durations.get(reference)
        try:
            duration = float(duration)
            reference_duration = float(reference_duration)
        except (TypeError, ValueError):
            continue
        if (not math.isfinite(duration)
                or not math.isfinite(reference_duration)):
            continue
        # Clip the source domain to the part that maps inside the reference
        # source's recorded duration. M6's source times are millisecond ticks.
        offset_ticks = round(offset * TICKS_PER_SECOND)
        src_start = max(0, -offset_ticks)
        src_end = min(round(duration * TICKS_PER_SECOND),
                      round(reference_duration * TICKS_PER_SECOND) - offset_ticks)
        if src_end <= src_start:
            continue
        dst_start, dst_end = src_start + offset_ticks, src_end + offset_ticks
        uncertainty = int((
            (Decimal(str(p95)) - Decimal(str(p5))
             + Decimal(str(drift)))
            * Decimal(TICKS_PER_SECOND) / 2
        ).to_integral_value(rounding=ROUND_CEILING))
        builder.model["time_transforms"].append({
            "id": f"transform-{digest}-to-{reference}",
            "from_clock": _clock_id(digest),
            "to_clock": _clock_id(reference),
            "segments": [{
                "from": {"clock_id": _clock_id(digest),
                         "start": src_start, "end": src_end},
                "to": {"clock_id": _clock_id(reference),
                       "start": dst_start, "end": dst_end},
                "slope": {"numerator": 1, "denominator": 1},
                "offset_ticks": offset_ticks,
                "uncertainty_ticks": uncertainty,
                "invertible": True,
            }],
            "provenance_id": _prov_id(digest, "m6"),
        })


def _migrate_m3_frames(builder: _Builder, digest: str, asset_id: str,
                       clock_id: str, root: Path | None) -> None:
    """M3 frames ride M2 samples with one native record per sampled frame."""
    from library.tools import footage_analysis

    m3 = person_measurements.read_m3(digest, root)
    if m3 is None:
        builder.notes.append(f"{digest[:12]}: M3 absent; no frame geometry")
        return
    lane = next(item for item in footage_analysis.LANES
                if item.name == "persons")
    if not footage_analysis.slot_is_fresh(lane, digest, root):
        builder.notes.append(f"{digest[:12]}: M3 stale; method or M2 input changed, refused")
        return
    from library.tools.person_measurements import M3_METHOD
    m2 = source_memory.read_m2(digest, root)
    resolution = None
    if m2 and isinstance(m2.get("width"), int) and isinstance(
            m2.get("height"), int):
        resolution = [m2["width"], m2["height"]]
    for index, frame in enumerate(m3.get("frames") or []):
        if not isinstance(frame, dict) or "t" not in frame:
            continue
        try:
            t = float(frame["t"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t) or t < 0:
            continue
        sample_start = math.floor(t * TICKS_PER_SECOND)
        bounds = (sample_start, sample_start + 1)
        faces = [face for face in (frame.get("faces") or [])
                 if isinstance(face, dict)]
        rows = list(enumerate(faces)) if faces else [(None, None)]
        for face_index, face in rows:
            payload = {"face_index": face_index, "face": face,
                       "hands": frame.get("hands") or []}
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                measurement_type="person.face_frame", value=payload,
                method=M3_METHOD, provenance_id=_prov_id(digest, "m3"),
                sample_resolution=resolution,
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            native = replace(
                native, freshness="fresh",
                references=({"kind": "source_frame",
                             "stream_id": _stream_id(digest),
                             "frame_index": index},))
            builder.observation(
                digest, "m3", asset_id, clock_id,
                bounds[0] / TICKS_PER_SECOND,
                bounds[1] / TICKS_PER_SECOND, "face_frame", payload, None,
                "library.tools.person_measurements", evidence=native)


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


def _object_track_method(width: int, height: int) -> str:
    """The SAM 2 video-track method stamp: family plus the pixels
    measured, the contract's `@resolution` vocabulary. The same
    instrument at two geometries is two methods of one family."""
    return f"sam2-video-track-v1@{width}x{height}"


def _normalised_object_box(box: Any, width: int, height: int,
                           ) -> list[float] | None:
    """Stored `bbox_pixels` (x, y, w, h) as a normalised top-left box,
    or None when it names no area. A partially out-of-frame box is
    clamped; a degenerate one is not a measurement."""
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or width <= 0 or height <= 0:
        return None
    x0 = min(max(x / width, 0.0), 1.0)
    y0 = min(max(y / height, 0.0), 1.0)
    x1 = min(max((x + w) / width, 0.0), 1.0)
    y1 = min(max((y + h) / height, 0.0), 1.0)
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _object_sample_ticks(sample_time: float, period: float,
                         ) -> tuple[int, int] | None:
    """Half-open millisecond ticks for one track sample, or None when
    the span is unusable. The same floor/ceil rule the observation
    itself is filed under, so evidence never straddles its own row."""
    try:
        start_f, end_f = float(sample_time), float(sample_time + period)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        return None
    start_tick = math.floor(start_f * TICKS_PER_SECOND)
    end_tick = math.ceil(end_f * TICKS_PER_SECOND)
    if end_tick <= start_tick or start_tick < 0:
        return None
    return (start_tick, end_tick)


def _migrate_object_detection(builder: _Builder, digest: str,
                               clock_id: str, clip_id: str, obj: dict,
                               object_id: str, entity_id: str,
                               category: str, first_t: float,
                               period: float) -> None:
    """The open-vocabulary answer a detector-seeded track grew from.

    One `object_detection` observation per detector-seeded object,
    carrying the detector slate name, the Gemma phrase and category,
    and the detector's own score - or null where the seeding run
    recorded none. Face- and blob-seeded objects emit nothing here:
    no detector answered for them, and saying otherwise would invent
    model evidence. The seeding box itself is not stored in the
    segmentation file, so none is claimed; box evidence lives in the
    track samples."""
    seed_name = str(obj.get("seed") or "")
    if not seed_name.startswith("detector:"):
        return
    detector = seed_name.split(":", 1)[1].strip()
    if not detector:
        builder.notes.append(f"{clip_id}/{object_id}: detector seed "
                             f"names no detector; detection withheld")
        return
    sample_ticks = _object_sample_ticks(first_t, period)
    evidence = None
    if sample_ticks is not None:
        evidence = evidence_contract.new_measurement(
            asset_id=_asset_id(digest), clock_id=clock_id,
            start_tick=sample_ticks[0], end_tick=sample_ticks[1],
            measurement_type="object.detection",
            value={"track_id": f"track-{clip_id}-{object_id}",
                   "object_id": object_id, "detector": detector,
                   "label": obj.get("label"), "category": category,
                   "score": _confidence(obj.get("score"))},
            method=f"{detector}-open-vocabulary-detection",
            provenance_id=_prov_id(digest, "segmentation"),
            target_entity_id=entity_id,
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=1,
            coverage_sample_rate_hz=0.0)
    builder.observation(
        digest, "segmentation", _asset_id(digest), clock_id,
        first_t, first_t + period, "object_detection",
        {"track_id": f"track-{clip_id}-{object_id}",
         "object_id": object_id, "detector": detector,
         "label": obj.get("label"), "category": category,
         "score": _confidence(obj.get("score"))},
        _confidence(obj.get("score")),
        "library.steps.step_1_06_object_segmentation",
        evidence=evidence)


def _migrate_object_appearance(builder: _Builder, digest: str,
                               clock_id: str, clip_id: str, obj: dict,
                               object_id: str, entity_id: str,
                               frames: list[int], bboxes: dict,
                               sample_fps: float, frame_width: int,
                               frame_height: int, first_t: float,
                               last_t: float,
                               track_method: str) -> None:
    """Shape read off one object's stored track geometry (VI-05).

    Aspect range/mean plus box and mask area shares, all from the
    bboxes the segmentation file already stores - no frame is decoded
    and no model runs. Colour and texture need source pixels and are
    named as unmeasured rather than defaulted: no record under
    `object.appearance` carries them."""
    aspects: list[float] = []
    area_ratios: list[float] = []
    frame_area = frame_width * frame_height
    for frame_idx in frames:
        box = bboxes.get(frame_idx, bboxes.get(str(frame_idx)))
        try:
            _x, _y, w, h = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0 or frame_area <= 0:
            continue
        aspects.append(w / h)
        area_ratios.append((w * h) / frame_area)
    if not aspects:
        builder.dropped.append({"digest": digest, "lane": "segmentation",
                                "clip_id": clip_id, "object": object_id,
                                "reason": "appearance-has-no-geometry"})
        return
    try:
        mask_share = float(obj.get("avg_area_ratio"))
        if not math.isfinite(mask_share) or mask_share < 0:
            raise ValueError
    except (TypeError, ValueError):
        mask_share = None
    value = {"track_id": f"track-{clip_id}-{object_id}",
             "object_id": object_id, "samples": len(aspects),
             "aspect_min": round(min(aspects), 4),
             "aspect_max": round(max(aspects), 4),
             "aspect_mean": round(sum(aspects) / len(aspects), 4),
             "bbox_area_ratio_mean": round(
                 sum(area_ratios) / len(area_ratios), 4),
             "mask_area_ratio_mean": mask_share,
             "unmeasured": ["colour", "texture"]}
    ticks = sec_to_ticks(first_t, last_t)
    evidence = None
    if ticks is not None:
        evidence = evidence_contract.new_measurement(
            asset_id=_asset_id(digest), clock_id=clock_id,
            start_tick=ticks[0], end_tick=ticks[1],
            measurement_type="object.appearance", value=value,
            method=track_method,
            provenance_id=_prov_id(digest, "segmentation"),
            sample_resolution=[frame_width, frame_height],
            target_entity_id=entity_id,
            coverage=evidence_contract.CoverageKind.SAMPLED,
            coverage_samples=len(aspects),
            coverage_sample_rate_hz=sample_fps)
    builder.observation(
        digest, "segmentation", _asset_id(digest), clock_id,
        first_t, last_t, "object_appearance", value, None,
        "library.steps.step_1_06_object_segmentation",
        evidence=evidence)


def _migrate_object_visibility(builder: _Builder, digest: str,
                               clock_id: str, clip_id: str,
                               object_id: str, entity_id: str,
                               frames: list[int], sample_fps: float,
                               first_t: float, last_t: float) -> None:
    """One object's visible samples plus its explicit coverage gaps.

    SAM 2 propagation skips frames where the mask is empty, so a
    frame index absent between the first and last sample is
    unmeasured coverage - never an occlusion verdict and never
    filled by a guess. The gaps are recorded on the observation and
    in the migration notes; a track with none says it is continuous,
    so silence never reads as coverage."""
    present = set(frames)
    gaps: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(frames[0], frames[-1] + 1):
        if index not in present:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            gaps.append((run_start, index - 1))
            run_start = None
    if run_start is not None:
        gaps.append((run_start, frames[-1]))
    gap_rows = []
    for start_frame, end_frame in gaps:
        start_tick = math.floor(start_frame / sample_fps
                                * TICKS_PER_SECOND)
        end_tick = math.ceil((end_frame + 1) / sample_fps
                             * TICKS_PER_SECOND)
        gap_rows.append({"start_tick": start_tick, "end_tick": end_tick,
                         "missing_frames": list(
                             range(start_frame, end_frame + 1))})
    value = {"track_id": f"track-{clip_id}-{object_id}",
             "object_id": object_id,
             "visible_ticks": [round(index / sample_fps * TICKS_PER_SECOND)
                               for index in frames],
             "gaps": gap_rows}
    ticks = sec_to_ticks(first_t, last_t)
    evidence = None
    if ticks is not None:
        evidence = evidence_contract.new_measurement(
            asset_id=_asset_id(digest), clock_id=clock_id,
            start_tick=ticks[0], end_tick=ticks[1],
            measurement_type="object.visibility", value=value,
            method="sam2-video-track-v1",
            provenance_id=_prov_id(digest, "segmentation"),
            target_entity_id=entity_id,
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=len(frames),
            coverage_sample_rate_hz=0.0)
    builder.observation(
        digest, "segmentation", _asset_id(digest), clock_id,
        first_t, last_t, "object_visibility", value, None,
        "library.steps.step_1_06_object_segmentation",
        evidence=evidence)
    if gap_rows:
        ranges = ", ".join(
            f"frames {row['missing_frames'][0]}.."
            f"{row['missing_frames'][-1]} "
            f"(ticks {row['start_tick']}..{row['end_tick']})"
            for row in gap_rows)
        builder.notes.append(
            f"{clip_id}/{object_id}: {len(frames)} visible samples with "
            f"{len(gap_rows)} coverage gap(s): {ranges}; gaps are "
            f"unmeasured, never occlusions")
    else:
        builder.notes.append(
            f"{clip_id}/{object_id}: continuous coverage over "
            f"{len(frames)} samples; no gaps")


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
            seed = str(obj.get("seed") or "")
            taxonomy = ("open-vocabulary" if seed.startswith(
                "detector:") else "sam2_seed_category")
            if not any(c["id"] == class_id
                       for c in builder.model["object_classes"]):
                builder.model["object_classes"].append({
                    "id": class_id, "name": str(obj.get("label") or category),
                    "taxonomy": taxonomy,
                })
        else:
            builder.notes.append(f"{clip_id}/{object_id}: blob-seeded with "
                                 f"no semantic class; instance kept, class "
                                 f"withheld")
        entity_id = f"object-{clip_id}-{object_id}"
        builder.model["entities"].append({
            "id": entity_id, "kind": "object", "label": obj.get("label"),
            "object_class_id": class_id,
            # Lane 3: the 1.06 tracker names instances per clip; the
            # entity holds for that asset until a re-identification
            # link promotes it.
            "identity_scope": "asset",
        })
        clock_id = _clock_id(digest)
        bboxes = obj.get("bboxes") or {}
        try:
            frame_width, frame_height = int(width), int(height)
        except (TypeError, ValueError):
            frame_width, frame_height = (0, 0)
        measured_dims = frame_width > 0 and frame_height > 0
        track_method = (_object_track_method(frame_width, frame_height)
                        if measured_dims else "")
        samples = []
        sample_observation_ids = []
        for frame_idx in frames:
            box = bboxes.get(frame_idx, bboxes.get(str(frame_idx)))
            if box is None:
                continue
            sample_time = frame_idx / sample_fps
            tick = round(sample_time * TICKS_PER_SECOND)
            samples.append({"tick": tick,
                            "geometry": {"bbox_pixels": list(box)},
                            "confidence": None,
                            "coordinate_space":
                                f"source_pixels_{width}x{height}"})
            evidence = None
            if measured_dims:
                sample_ticks = _object_sample_ticks(sample_time, period)
                norm_box = _normalised_object_box(box, frame_width,
                                                  frame_height)
                if sample_ticks is not None and norm_box is not None:
                    evidence = evidence_contract.new_measurement(
                        asset_id=_asset_id(digest), clock_id=clock_id,
                        start_tick=sample_ticks[0],
                        end_tick=sample_ticks[1],
                        measurement_type="object.track_sample",
                        value={"track_id": f"track-{clip_id}-{object_id}",
                               "object_id": object_id, "box": norm_box,
                               "coordinate_space":
                                   f"source_pixels_{width}x{height}",
                               "seed": obj.get("seed"),
                               "category": category},
                        method=track_method,
                        provenance_id=_prov_id(digest, "segmentation"),
                        sample_resolution=[frame_width, frame_height],
                        target_entity_id=entity_id,
                        coverage=evidence_contract.CoverageKind.SAMPLED,
                        coverage_samples=1,
                        coverage_sample_rate_hz=sample_fps)
            sample_observation_id = builder.observation(
                digest, "segmentation", _asset_id(digest), clock_id,
                sample_time, sample_time + period,
                "object_track_sample",
                {"track_id": f"track-{clip_id}-{object_id}",
                 "object_id": object_id, "bbox_pixels": list(box),
                 "coordinate_space": f"source_pixels_{width}x{height}",
                 "seed": obj.get("seed"), "category": category},
                None, "library.steps.step_1_06_object_segmentation",
                evidence=evidence)
            if sample_observation_id is not None:
                sample_observation_ids.append(sample_observation_id)
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
            # Lane 3: the tracker's clip-local name, distinct from the
            # durable entity id above.
            "local_track_id": f"{clip_id}/{object_id}",
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
        if sample_observation_ids:
            builder.link(
                entity_id, sample_observation_ids[0], "sam2_track_continuity",
                digest, "segmentation",
                "library.steps.step_1_06_object_segmentation",
                supporting_observation_ids=sample_observation_ids,
                scope={"clock_id": clock_id,
                       "start": ticks[0], "end": ticks[1]},
                track_id=f"track-{clip_id}-{object_id}")
        _migrate_object_detection(builder, digest, clock_id, clip_id, obj,
                                  object_id, entity_id, category,
                                  first_t, period)
        if measured_dims:
            _migrate_object_appearance(
                builder, digest, clock_id, clip_id, obj, object_id,
                entity_id, frames, bboxes, sample_fps, frame_width,
                frame_height, first_t, last_t, track_method)
        # A frame index alone is not evidence that the migrated track has
        # geometry there. Segmentation artifacts can list a propagated mask
        # frame without a bbox; the sample loop above skips that frame. Keep
        # visibility aligned with the observations the model can actually
        # query, and let the missing index become an explicit coverage gap.
        sampled_frames = [frame_idx for frame_idx in frames
                          if bboxes.get(frame_idx,
                                        bboxes.get(str(frame_idx))) is not None]
        if sampled_frames:
            _migrate_object_visibility(builder, digest, clock_id, clip_id,
                                       object_id, entity_id, sampled_frames,
                                       sample_fps, first_t, last_t)
        else:
            builder.notes.append(
                f"{clip_id}/{object_id}: no track samples have usable "
                "bounding boxes; visibility is unmeasured")
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


def _derive_track_motion_events(builder: _Builder) -> None:
    """Turn consecutive VI-05 box measurements into candidate state changes.

    Only contiguous samples are compared. Every event cites both visual
    samples and remains a candidate because sparse tracks cannot establish
    a complete action.
    """
    by_track: dict[str, list[dict]] = {}
    for item in builder.model["observations"]:
        if item["modality"] != "object_track_sample":
            continue
        track_id = item["payload"].get("track_id")
        if track_id:
            by_track.setdefault(str(track_id), []).append(item)
    entity_by_track = {track["id"]: track["entity_id"]
                       for track in builder.model["tracks"]}
    for track_id, samples in by_track.items():
        samples.sort(key=lambda item: item["interval"]["start"])
        entity_id = entity_by_track.get(track_id)
        if entity_id is None:
            continue
        for before, after in pairwise(samples):
            before_interval, after_interval = before["interval"], after["interval"]
            before_box = before["payload"].get("bbox_pixels")
            after_box = after["payload"].get("bbox_pixels")
            if (before_interval["clock_id"] != after_interval["clock_id"]
                    or before_interval["end"] != after_interval["start"]
                    or before_box is None or after_box is None
                    or before_box == after_box):
                continue
            evidence_ids = [before["id"], after["id"]]
            builder.model["events"].append({
                "id": f"event-motion-{before['id']}-{after['id']}",
                "interval": {"clock_id": before_interval["clock_id"],
                             "start": before_interval["start"],
                             "end": after_interval["end"]},
                "kind": "object_position_change",
                "participant_ids": [entity_id],
                "status": "candidate",
                "certainty": "measured",
                "evidence_ids": evidence_ids,
                "confidence": None,
                "state_changes": [{
                    "entity_id": entity_id,
                    "property": "bbox_pixels",
                    "from": before_box,
                    "to": after_box,
                    "evidence_ids": evidence_ids,
                }],
            })


REGION_TRACK_PRODUCER = "library.tools.person_regions"
REGION_SOURCE_MODEL = "apple_vision_person_instance_mask"
REGION_OBSERVATION_MODALITY = "region_track"
FACE_IN_REGION_METHOD = "face_in_region"
STALE_REGION_FACE_TRACK_REASON = "face_track_without_roster_person"


def _migrate_regions(builder: _Builder, digest: str, asset_id: str,
                     clock_id: str, roster: list[dict],
                     root: Path | None) -> None:
    """M3d person-region tracks: region geometry, region masks and
    face-in-region identity links.

    One ``tracks[]`` entry per M3d region track, with one sample per M2
    frame the instance was measured on (``geometry.kind == "region"``:
    bbox, centroid, interior point and area, plus the sidecar index a
    renderer decodes - the inline summary answers geometry without
    reading mask bytes). One ``masks[]`` entry per track with the
    registry's ``region_rle`` kind (a reference to the content-addressed
    sidecar, never the bytes). A track whose face track holds most
    samples links to its roster person with method ``face_in_region``
    and the track's face-track share as confidence; a track with no
    roster owner stays on an explicit ``unassigned-{digest}-{track}``
    entity with its named reason and NO identity link.
    """
    doc = person_regions.read_m3d(digest, root)
    if doc is None:
        builder.notes.append(f"{digest[:12]}: M3d absent; no region tracks")
        return
    from library.tools import footage_analysis
    lane = next(item for item in footage_analysis.LANES
                if item.name == "regions")
    instrument = doc.get("instrument") or {}
    method = instrument.get("method")
    if method is not None and method != lane.method:
        builder.notes.append(f"{digest[:12]}: M3d stale method; refused")
        return
    fresh = (method == lane.method
             and footage_analysis.slot_is_fresh(lane, digest, root))
    if not fresh:
        builder.notes.append(f"{digest[:12]}: M3d stale against method or "
                             "inputs; native evidence refused")
    for index, sample in enumerate(doc["samples"]):
        timestamp = float(sample["t"])
        if not math.isfinite(timestamp) or timestamp < 0:
            continue
        sample_start = math.floor(timestamp * TICKS_PER_SECOND)
        bounds = (sample_start, sample_start + 1)
        instances = sample["instances"]
        native = None
        if fresh and method and instances is None:
            native = evidence_contract.EvidenceRecord(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                target_entity_id=None, target_region=None,
                evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                measurement_type="person.region_frame", value=None,
                unit="regions", method=method, sample_resolution=None,
                coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                coverage_samples=0, coverage_sample_rate_hz=0.0,
                provenance_id=_prov_id(digest, "m3d"),
                processing_status=evidence_contract.ProcessingStatus.UNAVAILABLE)
        elif fresh and method:
            native = evidence_contract.new_measurement(
                asset_id=asset_id, clock_id=clock_id,
                start_tick=bounds[0], end_tick=bounds[1],
                measurement_type="person.region_frame", value=instances,
                method=method, provenance_id=_prov_id(digest, "m3d"),
                coverage=evidence_contract.CoverageKind.COMPLETE,
                coverage_samples=len(instances),
                coverage_sample_rate_hz=0.0)
        if native is not None:
            native = replace(
                native, freshness="fresh",
                references=({"kind": "source_frame",
                             "stream_id": _stream_id(digest),
                             "frame_index": index},))
        builder.observation(
            digest, "m3d", asset_id, clock_id,
            bounds[0] / TICKS_PER_SECOND,
            bounds[1] / TICKS_PER_SECOND, "region_frame",
            {"instances": instances, "sample_index": index}, None,
            REGION_TRACK_PRODUCER, evidence=native)
    sidecar = doc.get("sidecar") or {}
    grid = doc.get("grid") or []
    try:
        grid_pair = [int(grid[0]), int(grid[1])]
    except (TypeError, ValueError, IndexError):
        grid_pair = []
    if len(grid_pair) != 2 or min(grid_pair) <= 0:
        builder.dropped.append({"digest": digest, "lane": "m3d",
                                "reason": "unusable-grid",
                                "grid": doc.get("grid")})
        return
    if not isinstance(sidecar.get("file"), str) or not sidecar.get("file"):
        builder.dropped.append({"digest": digest, "lane": "m3d",
                                "reason": "sidecar-unresolved"})
        return
    by_track: dict[str, list[tuple[float, dict]]] = {}
    for sample in doc.get("samples") or []:
        if not isinstance(sample, dict) or "t" not in sample:
            continue
        try:
            t = float(sample["t"])
        except (TypeError, ValueError):
            continue
        for inst in sample.get("instances") or []:
            if not isinstance(inst, dict) or not inst.get("track"):
                continue
            by_track.setdefault(str(inst["track"]), []).append((t, inst))
    for track in doc.get("tracks") or []:
        if not isinstance(track, dict) or not track.get("track_id"):
            continue
        track_id = str(track["track_id"])
        members = sorted(by_track.get(track_id, []), key=lambda m: m[0])
        if not members:
            builder.dropped.append({"digest": digest, "lane": "m3d",
                                    "track": track_id,
                                    "reason": "track-has-no-samples"})
            continue
        samples = []
        valid = True
        for t, inst in members:
            summary = {k: inst.get(k) for k in
                       ("bbox", "centroid", "interior_point", "area",
                        "mask")}
            if (not isinstance(summary["bbox"], list)
                    or not isinstance(summary["centroid"], list)
                    or not isinstance(summary["interior_point"], list)
                    or not isinstance(summary["area"], (int, float))
                    or not isinstance(summary["mask"], int)):
                valid = False
                break
            samples.append({
                "tick": round(t * TICKS_PER_SECOND),
                "geometry": {
                    "kind": "region",
                    "bbox": summary["bbox"],
                    "centroid": summary["centroid"],
                    "interior_point": summary["interior_point"],
                    "area": summary["area"],
                    "mask_ref": {"mask_index": summary["mask"]},
                },
                "confidence": _confidence(inst.get("containment")),
                "coordinate_space": "source-normalised-tl",
            })
        if not valid:
            builder.dropped.append({"digest": digest, "lane": "m3d",
                                    "track": track_id,
                                    "reason": "track-has-no-geometry"})
            continue
        owner_face = track.get("face_track")
        person_id = (_face_person(roster, digest, owner_face)
                     if isinstance(owner_face, str) and owner_face else None)
        entity_id = person_id or f"unassigned-{digest}-{track_id}"
        if person_id is None:
            reason = (track.get("unassigned")
                      or STALE_REGION_FACE_TRACK_REASON)
            if not any(e["id"] == entity_id
                       for e in builder.model["entities"]):
                builder.model["entities"].append({
                    "id": entity_id, "kind": "object", "label": None,
                    "object_class_id": None,
                    # Lane 3: a region track with no roster owner is
                    # an asset-local instance, never a durable person.
                    "identity_scope": "asset",
                })
            builder.notes.append(
                f"{digest[:12]}/{track_id}: region track has no roster "
                f"owner ({reason}); mask stays on {entity_id} with no "
                f"identity link")
        else:
            reason = None
        builder.model["tracks"].append({
            "id": f"track-region-{digest}-{track_id}",
            "entity_id": entity_id,
            "clock_id": clock_id,
            # Lane 3: the M3d region tracker's own name for this
            # instance, distinct from the entity it is assigned to.
            "local_track_id": track_id,
            "samples": samples,
            "provenance_id": builder.provenance(
                digest, "m3d", REGION_TRACK_PRODUCER),
        })
        first_t = members[0][0]
        last_t = members[-1][0]
        ticks = sec_to_ticks(first_t,
                             last_t if last_t > first_t else first_t + 0.001)
        if ticks is None:  # unreachable: last_t >= first_t >= 0 by read_m3d
            builder.dropped.append({"digest": digest, "lane": "m3d",
                                    "track": track_id,
                                    "reason": "non-positive-interval"})
            builder.model["tracks"].pop()
            continue
        payload: dict[str, Any] = {
            "track_id": track_id,
            "face_track": owner_face,
            "face_track_fraction": track.get("face_track_fraction"),
            "sample_count": len(members),
            "grid": grid_pair,
        }
        if reason is not None:
            payload["unassigned"] = reason
        obs_id = builder.observation(
            digest, "m3d", asset_id, clock_id, first_t,
            last_t if last_t > first_t else first_t + 0.001,
            REGION_OBSERVATION_MODALITY, payload,
            track.get("face_track_fraction") if person_id else None,
            REGION_TRACK_PRODUCER)
        if obs_id is not None and person_id is not None:
            builder.link(person_id, obs_id, FACE_IN_REGION_METHOD,
                         digest, "m3d", REGION_TRACK_PRODUCER,
                         confidence=track.get("face_track_fraction"))
        builder.model["masks"].append({
            "id": f"mask-region-{digest}-{track_id}",
            "entity_id": entity_id,
            "interval": {"clock_id": clock_id,
                         "start": ticks[0], "end": ticks[1]},
            "representation": {
                "kind": project_model_masks.REGION_RLE_KIND,
                "content_digest": digest,
                "sidecar_file": sidecar.get("file"),
                "sidecar_sha256": sidecar.get("sha256"),
                "codec": sidecar.get("codec",
                                     project_model_masks.REGION_RUNS_CODEC),
                "grid": grid_pair,
                "track_id": track_id,
                "sample_ticks": [s["tick"] for s in samples],
                "mask_indices": [s["geometry"]["mask_ref"]["mask_index"]
                                 for s in samples],
            },
            "source_model": REGION_SOURCE_MODEL,
            "provenance_id": builder.provenance(
                digest, "m3d", REGION_TRACK_PRODUCER),
        })


REGION_STATS_PRODUCER = "library.tools.region_stats"
REGION_STATS_MODALITY = "region_stats"
IMAGE_MEASUREMENT_MODALITY = "image_measurement"


def _migrate_region_measurements(builder: _Builder, digest: str,
                                 asset_id: str, clock_id: str,
                                 sample: dict, doc: dict) -> None:
    """Expose M3f's exact raster measurements as typed, queryable evidence."""
    grid = [int(v) for v in doc["grid"]]
    method = f"{region_stats.M3F_METHOD}@{min(grid)}px"
    instrument = doc["instrument"]
    m3d_digest = instrument["m3d_sidecar_sha256"]
    targets = [("whole_frame", None, sample["whole_frame"])]
    if sample.get("outside") is not None:
        targets.append(("outside_regions", None, sample["outside"]))
    if sample.get("regions") is not None:
        targets.extend((f"region:{entry['track']}",
                        f"track-region-{digest}-{entry['track']}",
                        entry["inside"]) for entry in sample["regions"])
    metrics = (("luma_p50", "image.luma.rec709_p50"),
               ("rgb_mean", "image.color.rgb_mean"),
               ("sharpness", "image.sharpness.laplacian_variance"))
    for target, entity_id, reading in targets:
        for field, measurement_type in metrics:
            value = reading[field]
            if value is None:
                continue
            measured_cells = (reading["sharpness_cells"]
                              if field == "sharpness" else reading["cells"])
            coverage_fraction = measured_cells / (grid[0] * grid[1])
            mask_digest = reading["mask_sha256"]
            start = max(0.0, float(sample["t"])-M3_POINT_RADIUS_S)
            end = float(sample["t"])+M3_POINT_RADIUS_S
            ticks = sec_to_ticks(start, end)
            if ticks is None:
                continue
            record = evidence_contract.new_measurement(
                asset_id, clock_id, ticks[0], ticks[1], measurement_type,
                {"value": value, "cells": measured_cells,
                 "coverage_fraction": coverage_fraction,
                 "mask_sha256": mask_digest,
                 "m3d_sidecar_sha256": m3d_digest},
                method, _prov_id(digest, "m3f"), sample_resolution=grid,
                target_entity_id=entity_id, target_region=target,
                coverage=evidence_contract.CoverageKind.SAMPLED,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            record = replace(
                record,
                source_reference=(f"m2-sample:{sample['t']};"
                                  f"mask-sha256:{mask_digest};"
                                  f"m3d-sidecar-sha256:{m3d_digest}"))
            builder.observation(
                digest, "m3f", asset_id, clock_id, start, end,
                IMAGE_MEASUREMENT_MODALITY,
                {"metric": measurement_type, "value": value,
                 "target": target, "cells": measured_cells,
                 "coverage_fraction": coverage_fraction,
                 "mask_sha256": mask_digest,
                 "m3d_sidecar_sha256": m3d_digest}, None,
                REGION_STATS_PRODUCER, evidence=record)


def _migrate_region_stats(builder: _Builder, digest: str, asset_id: str,
                          clock_id: str, root: Path | None) -> None:
    """M3f: one ``Observation(modality="region_stats")`` per region per
    measured sample - luma median, mean RGB, skin vector and Laplacian
    sharpness inside the region against outside every region, at the
    sample's own time with the M3 point-sample neighbourhood. The payload
    names the region track (``track-region-{digest}-{track}``), whose
    own observation carries the identity link: a reading is about a
    region, and a region reaches a person only through ``face_in_region``.
    """
    doc = region_stats.read_m3f(digest, root)
    if doc is None:
        builder.notes.append(f"{digest[:12]}: M3f absent; no region stats")
        return
    from library.tools import footage_analysis
    lane = next(item for item in footage_analysis.LANES
                if item.name == "region_stats")
    m3d = person_regions.read_m3d(digest, root)
    method = (doc.get("instrument") or {}).get("method")
    fresh = (doc.get("status") == region_stats.M3F_STATUS_MEASURED
             and method == region_stats.M3F_METHOD and m3d is not None
             and footage_analysis.slot_is_fresh(lane, digest, root)
             and region_stats._current(doc, m3d))
    if method != region_stats.M3F_METHOD:
        builder.notes.append(f"{digest[:12]}: M3f stale method; native evidence refused")
    elif not fresh:
        builder.notes.append(f"{digest[:12]}: M3f stale against M2/M3d; native evidence refused")
    grid = doc["grid"]
    for index, sample in enumerate(doc.get("samples") or []):
        if not isinstance(sample, dict):
            continue
        try:
            t = float(sample["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if sample.get("whole_frame") is not None:
            _migrate_region_measurements(builder, digest, asset_id,
                                         clock_id, sample, doc)
        regions = sample.get("regions")
        start, end = max(0.0, t - M3_POINT_RADIUS_S), t + M3_POINT_RADIUS_S
        ticks = sec_to_ticks(start, end)
        if regions is None:
            native = None
            if fresh and ticks is not None:
                native = evidence_contract.EvidenceRecord(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    target_entity_id=None, target_region=None,
                    evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                    measurement_type="image.region_stats", value=None,
                    unit="record", method=str(method),
                    sample_resolution=list(grid),
                    coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0,
                    provenance_id=_prov_id(digest, "m3f"),
                    processing_status=evidence_contract.ProcessingStatus.UNAVAILABLE,
                    freshness="fresh",
                    references=({"kind": "source_sample",
                                 "stream_id": _stream_id(digest),
                                 "sample_index": index},))
            builder.observation(
                digest, "m3f", asset_id, clock_id, start, end,
                "region_stats_frame",
                {"sample_index": index,
                 "unmeasured_because": sample.get("unmeasured_because")},
                None, REGION_STATS_PRODUCER, evidence=native)
            continue
        if not regions:
            native = None
            if fresh and ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type="image.region_stats", value=[],
                    method=str(method), provenance_id=_prov_id(digest, "m3f"),
                    sample_resolution=list(grid),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness="fresh",
                                 references=({"kind": "source_sample",
                                              "stream_id": _stream_id(digest),
                                              "sample_index": index},))
            builder.observation(
                digest, "m3f", asset_id, clock_id, start, end,
                "region_stats_frame", {"sample_index": index, "regions": []},
                None, REGION_STATS_PRODUCER, evidence=native)
            continue
        for region in sample["regions"]:
            if not isinstance(region, dict) or not region.get("track"):
                continue
            native = None
            if fresh and ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type="image.region_stats",
                    value=copy.deepcopy({"inside": region["inside"],
                                         "outside": sample.get("outside"),
                                         "outside_is": doc.get("outside"),
                                         "grid": grid}),
                    method=str(method), provenance_id=_prov_id(digest, "m3f"),
                    sample_resolution=list(grid),
                    target_region=str(region["track"]),
                    coverage=evidence_contract.CoverageKind.SAMPLED,
                    coverage_samples=1, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness="fresh",
                                 references=({"kind": "source_sample",
                                              "stream_id": _stream_id(digest),
                                              "sample_index": index},))
            builder.observation(
                digest, "m3f", asset_id, clock_id,
                start, end,
                REGION_STATS_MODALITY,
                {"region_track": str(region["track"]),
                 "track_id": f"track-region-{digest}-{region['track']}",
                 "mask_index": region.get("mask"),
                 "inside": region.get("inside"),
                 "outside": sample.get("outside"),
                 "outside_is": doc.get("outside")},
                None, REGION_STATS_PRODUCER, evidence=native)


def _migrate_image_defects(builder: _Builder, digest: str, asset_id: str,
                           clock_id: str, root: Path | None) -> None:
    """VI-03: sampled image defects as typed, source-frame evidence."""
    doc = source_memory.read_image_defects(digest, root)
    if doc is None:
        return
    sampling = doc["sampling"]
    rate = float(sampling["rate_hz"])
    count = int(sampling["sample_count"])
    if rate <= 0 or count <= 0:
        builder.notes.append(f"{digest[:12]}: VI-03 invalid sampling coverage")
        return
    signals = doc["signals"]
    provenance_id = _prov_id(digest, "vi03")
    size = [int(v) for v in sampling["sample_size"]]

    def add(index: int, measurement_type: str, value: Any, *,
            span_samples: int = 1, reference_index: int | None = None) -> None:
        start = index / rate
        covered_end = float(sampling["interval_s"][1])
        if start >= covered_end:
            return
        end = min(covered_end, (index + span_samples) / rate)
        ticks = sec_to_ticks(start, end)
        if ticks is None:
            return
        evidence = evidence_contract.new_measurement(
            asset_id, clock_id, ticks[0], ticks[1], measurement_type, value,
            doc["method"], provenance_id, sample_resolution=size,
            coverage=evidence_contract.CoverageKind.SAMPLED,
            coverage_samples=1, coverage_sample_rate_hz=rate)
        evidence = replace(evidence, freshness="fresh",
                           references=({"kind": "source_sample",
                                        "stream_id": _stream_id(digest),
                                        "sample_index": (index if reference_index is None
                                                         else reference_index)},))
        builder.observation(
            digest, "vi03", asset_id, clock_id, start, end,
            IMAGE_MEASUREMENT_MODALITY,
            {"metric": measurement_type, "value": value,
             "sample_index": index, "sharpness_cause_claimed": False},
            None, "library.tools.analysis.image_defects", evidence=evidence)

    for field, measurement_type in (
            ("black_frame_sample_indices", "image.frame.black"),
            ("frozen_frame_sample_indices", "image.frame.frozen"),
            ("flicker_sample_indices", "image.flicker.alternation")):
        indices = set(signals.get(field) or [])
        for index in range(count):
            add(index, measurement_type, index in indices)
    for field, measurement_type, span_samples in (
            ("temporal_noise", "image.temporal_noise.residual", 2),
            ("compression_blockiness", "image.compression.blockiness_proxy", 1),
            ("sharpness_proxy_laplacian_variance",
             "image.sharpness.laplacian_variance", 1),
            ("clipping_underexposed_fraction",
             "image.exposure.underexposed_fraction", 1),
            ("clipping_overexposed_fraction",
             "image.exposure.overexposed_fraction", 1)):
        block = signals.get(field)
        if block is None:
            continue
        values = block["values"] if isinstance(block, dict) else block
        for index, value in enumerate(values):
            add(index, measurement_type, value, span_samples=span_samples,
                reference_index=index + 1 if span_samples == 2 else index)


DEPTH_PRODUCER = "library.tools.depth"
DEPTH_MODALITY = "depth"


def _migrate_depth(builder: _Builder, digest: str, asset_id: str,
                   clock_id: str, root: Path | None) -> None:
    """M3g: one ``Observation(modality="depth")`` per region per
    measured sample - the median relative depth inside the region's
    mask, on the source's own normalisation. The payload names the
    region track (``track-region-{digest}-{track}``), whose own
    observation carries the identity link: a reading is about a
    region, and a region reaches a person only through
    ``face_in_region`` - the same road the ``region_stats`` readings
    take, so depth never invents its own person.
    """
    doc = depth.read_m3g(digest, root)
    if doc is None:
        builder.notes.append(f"{digest[:12]}: M3g absent; no region depth")
        return
    from library.tools import footage_analysis
    lane = next(item for item in footage_analysis.LANES
                if item.name == "depth")
    m3d = person_regions.read_m3d(digest, root)
    method = (doc.get("instrument") or {}).get("method")
    grid = doc["grid"]
    fresh = (doc.get("status") == depth.M3G_STATUS_MEASURED
             and method == depth.M3G_METHOD and m3d is not None
             and (doc["instrument"].get("m3d_sidecar_sha256")
                  == (m3d.get("sidecar") or {}).get("sha256"))
             and footage_analysis.slot_is_fresh(lane, digest, root))
    if method != depth.M3G_METHOD:
        builder.notes.append(f"{digest[:12]}: M3g stale method; native evidence refused")
    elif not fresh:
        builder.notes.append(f"{digest[:12]}: M3g stale against M2/M3d; native evidence refused")
    for index, sample in enumerate(doc.get("samples") or []):
        if not isinstance(sample, dict):
            continue
        try:
            t = float(sample["t"])
        except (KeyError, TypeError, ValueError):
            continue
        regions = sample.get("regions")
        start, end = max(0.0, t - M3_POINT_RADIUS_S), t + M3_POINT_RADIUS_S
        ticks = sec_to_ticks(start, end)
        if regions is None:
            native = None
            if fresh and ticks is not None:
                native = evidence_contract.EvidenceRecord(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    target_entity_id=None, target_region=None,
                    evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                    measurement_type="person.region_depth_frame", value=None,
                    unit="regions", method=str(method),
                    sample_resolution=list(grid),
                    coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0,
                    provenance_id=_prov_id(digest, "m3g"),
                    processing_status=evidence_contract.ProcessingStatus.UNAVAILABLE,
                    freshness="fresh",
                    references=({"kind": "source_sample",
                                 "stream_id": _stream_id(digest),
                                 "sample_index": index},))
            builder.observation(
                digest, "m3g", asset_id, clock_id, start, end, "depth_frame",
                {"sample_index": index,
                 "unmeasured_because": sample.get("unmeasured_because")},
                None, DEPTH_PRODUCER, evidence=native)
            continue
        if not regions:
            native = None
            if fresh and ticks is not None:
                native = evidence_contract.new_measurement(
                    asset_id=asset_id, clock_id=clock_id,
                    start_tick=ticks[0], end_tick=ticks[1],
                    measurement_type="person.region_depth_frame", value=[],
                    method=str(method), provenance_id=_prov_id(digest, "m3g"),
                    sample_resolution=list(grid),
                    coverage=evidence_contract.CoverageKind.COMPLETE,
                    coverage_samples=0, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness="fresh",
                                 references=({"kind": "source_sample",
                                              "stream_id": _stream_id(digest),
                                              "sample_index": index},))
            builder.observation(
                digest, "m3g", asset_id, clock_id, start, end,
                "depth_frame", {"sample_index": index, "regions": []},
                None, DEPTH_PRODUCER, evidence=native)
            continue
        for region in regions:
            if not isinstance(region, dict) or not region.get("track"):
                continue
            median = region.get("median_depth")
            native = None
            if fresh and ticks is not None:
                if median is None:
                    native = evidence_contract.EvidenceRecord(
                        asset_id=asset_id, clock_id=clock_id,
                        start_tick=ticks[0], end_tick=ticks[1],
                        target_entity_id=None,
                        target_region=str(region["track"]),
                        evidence_type=evidence_contract.EvidenceType.MEASUREMENT,
                        measurement_type="image.relative_depth", value=None,
                        unit="normalized", method=str(method),
                        sample_resolution=list(grid),
                        coverage=evidence_contract.CoverageKind.UNAVAILABLE,
                        coverage_samples=0, coverage_sample_rate_hz=0.0,
                        provenance_id=_prov_id(digest, "m3g"),
                        processing_status=evidence_contract.ProcessingStatus.UNAVAILABLE,
                        freshness="fresh",
                        references=({"kind": "source_sample",
                                     "stream_id": _stream_id(digest),
                                     "sample_index": index},))
                else:
                    native = evidence_contract.new_measurement(
                        asset_id=asset_id, clock_id=clock_id,
                        start_tick=ticks[0], end_tick=ticks[1],
                        measurement_type="image.relative_depth",
                        value=median, method=str(method),
                        provenance_id=_prov_id(digest, "m3g"),
                        sample_resolution=list(grid),
                        target_region=str(region["track"]),
                        coverage=evidence_contract.CoverageKind.SAMPLED,
                        coverage_samples=1, coverage_sample_rate_hz=0.0)
                native = replace(native, freshness="fresh",
                                 references=({"kind": "source_sample",
                                              "stream_id": _stream_id(digest),
                                              "sample_index": index},))
            builder.observation(
                digest, "m3g", asset_id, clock_id,
                start, end,
                DEPTH_MODALITY,
                {"region_track": str(region["track"]),
                 "track_id": f"track-region-{digest}-{region['track']}",
                 "mask_index": region.get("mask"),
                 "median_depth": median,
                 "direction": doc.get("direction"),
                 "depth_kind": doc.get("depth_kind", "relative"),
                 "unit": doc.get("unit", "normalized"),
                 "calibration_id": None,
                 "depth_percentiles": ((doc.get("instrument") or {})
                                       .get("depth_percentiles"))},
                None, DEPTH_PRODUCER, evidence=native)


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
    builder = _Builder(f"project-{slug}", root)
    if catalog is None:
        catalog = source_memory.load_catalog(project_folder)
    if not isinstance(catalog, list):
        catalog = []
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    roster_result = person_entity.resolve_person_tracks(project_folder, root)
    roster = roster_result["persons"]
    for person in roster:
        builder.model["entities"].append({
            "id": person["person_id"],
            "kind": "person",
            "label": person.get("name"),
            "object_class_id": None,
            # Lane 3: resolve_person_tracks clusters face embeddings
            # across sources, so the person holds project-wide.
            "identity_scope": "project",
        })
    if not catalog:
        builder.notes.append("catalog empty or unreadable; model carries "
                             "the roster and nothing else")
    seen: dict[str, list[str]] = {}
    clip_digest: dict[str, str] = {}
    durations: dict[str, float | None] = {}
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
        durations[digest] = duration
        _migrate_digest(builder, digest, uri, clip_ids, duration, root,
                        roster)
        _migrate_m3_frames(builder, digest, _asset_id(digest),
                           _clock_id(digest), root)
    _materialize_multicam_transforms(builder, sorted(seen), durations, root)
    for digest in seen:
        _migrate_faces(builder, digest, _asset_id(digest),
                       _clock_id(digest), roster, root)
    for digest in seen:
        _migrate_regions(builder, digest, _asset_id(digest),
                         _clock_id(digest), roster, root)
        _migrate_region_stats(builder, digest, _asset_id(digest),
                              _clock_id(digest), root)
        _migrate_image_defects(builder, digest, _asset_id(digest),
                               _clock_id(digest), root)
        _migrate_depth(builder, digest, _asset_id(digest),
                       _clock_id(digest), root)
    for digest in seen:
        _migrate_speech_face_links(builder, digest, roster, root)
    _migrate_take_links(builder, sorted(seen), root)
    _migrate_voice_matches(builder, roster)
    _migrate_voice_match_conflicts(
        builder, roster_result.get("voice_match_conflicts") or [], root)
    for clip_id, digest in clip_digest.items():
        _migrate_segmentation(builder, project_folder, clip_id, digest,
                              roster, root)
        try:
            from library.tools.ocr_project_model import migrate_ocr_artifact
        except ImportError:
            from tools.ocr_project_model import migrate_ocr_artifact
        migrate_ocr_artifact(builder, project_folder, clip_id, digest)
    # Typed temporal action hypotheses are derived only after both M3 hand
    # samples and M3d/MU-15 object tracks have reached the Project Model.
    # They cite those measurements and stay candidates pending L3 review.
    from library.tools.temporal_action_candidates import candidates_from_model
    for action_rule in ("drink_from_cup", "pick_up_put_down", "pick_up"):
        builder.model["events"].extend(
            candidates_from_model(builder.model, rule=action_rule))
    _migrate_music_signals(builder, project_folder, root)
    _derive_track_motion_events(builder)
    # Lane 3: persistent identity and physical relations are derived
    # only after every digest's tracks, faces and hands have reached
    # the model. Re-association stays candidate without verification
    # evidence; relation edges cite the observations they rest on.
    from library.tools.identity_reassociation import reassociate_tracks
    reassociated = reassociate_tracks(builder.model)
    builder.model["identity_links"].extend(reassociated["links"])
    for skipped in reassociated["skipped"]:
        builder.notes.append(
            f"re-identification skipped {skipped['earlier']} -> "
            f"{skipped['later']}: {skipped['reason']}")
    from library.tools.relation_classes import (
        derive_physical_relations,
        derive_temporal_relations,
    )
    builder.model["relations"].extend(
        derive_temporal_relations(builder.model))
    builder.model["relations"].extend(
        derive_physical_relations(builder.model))
    from library.tools import cross_modal_grounding
    cross_modal_grounding.apply_project_model(builder.model)
    validate_project_model(builder.model)
    report = {
        "project": str(project_folder),
        "digests": sorted(seen),
        "persons": [p["person_id"] for p in roster],
        "counts": {k: len(builder.model[k]) for k in
                   ("assets", "streams", "clocks", "entities",
                    "observations", "signals", "identity_links", "masks",
                    "object_classes", "tracks", "events", "relations")},
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
    native_counted: dict[str, dict[str, int]] = {}
    for obs in model.get("observations") or []:
        prov = str(obs.get("provenance_id") or "")
        if prov.startswith("prov-"):
            # Provenance ids are prov-{digest}-{lane}; digests carry
            # dashes, lane names do not, so the lane is the last segment.
            digest, _, lane = prov[len("prov-"):].rpartition("-")
            if digest and lane:
                counted.setdefault(digest, {}).setdefault(lane, 0)
                counted[digest][lane] += 1
                if (obs.get("payload") or {}).get("evidence_contract"):
                    native_counted.setdefault(digest, {}).setdefault(lane, 0)
                    native_counted[digest][lane] += 1
    lanes: dict[str, dict[str, Any]] = {}
    for digest in report.get("digests") or []:
        m1, m1_status = source_memory.read_m1(digest, root)
        _identity, _speakers = _identity_docs(digest, root)
        from library.tools import (
            attention_saliency,
            expression_classifier,
            footage_analysis,
        )
        m3_doc = person_measurements.read_m3(digest, root)
        m3_lane = next(item for item in footage_analysis.LANES
                       if item.name == "persons")
        m3_freshness = ("fresh" if m3_doc is not None and
                        footage_analysis.slot_is_fresh(m3_lane, digest, root)
                        else "stale" if m3_doc is not None else "unknown")
        presence = {
            "m0": source_memory.read_m0(digest, root) is not None,
            "m1": m1 is not None and m1_status == "fresh",
            "m1_status": m1_status,
            "m1b": _speakers is not None,
            "m2": source_memory.read_m2(digest, root) is not None,
            "m3": m3_doc is not None,
            "m3c": expression_classifier.read_m3c(digest, root) is not None,
            "m3b": _identity is not None,
            "m3e": None,
            "m3d": person_regions.read_m3d(digest, root) is not None,
            "m3f": region_stats.read_m3f(digest, root) is not None,
            "m3g": depth.read_m3g(digest, root) is not None,
            "vi03": source_memory.read_image_defects(digest, root) is not None,
            "m4": source_memory.read_scenes(digest, root) is not None,
            "m5": source_memory.read_sound(digest, root) is not None,
            "m6": None,
            "m7": None,
            "m8_verdicts": 0,
            "m9": source_memory.read_world_model(digest, root) is not None,
            "m10": source_memory.read_relationships(digest, root) is not None,
        }
        presence["m6"] = conversation_clock.read_clock(digest, root) is not None
        saliency = attention_saliency.read_m3e(digest, root)
        saliency_lane = next(item for item in footage_analysis.LANES
                             if item.name == "saliency")
        presence["m3e"] = saliency is not None
        freshness = ("fresh" if saliency is not None and
                     footage_analysis.slot_is_fresh(saliency_lane, digest,
                                                    root)
                     else "stale" if saliency is not None else "unknown")
        m0_doc = source_memory.read_m0(digest, root)
        m0_path = ((m0_doc or {}).get("observed_paths") or [None])[0]
        m0_freshness = ("fresh" if m0_path and Path(m0_path).is_file()
                        and source_memory.is_fresh(m0_doc, m0_path)
                        else "stale" if m0_path and Path(m0_path).is_file()
                        else "unknown")
        identity_lane = next(item for item in footage_analysis.LANES
                             if item.name == "identity")
        identity_freshness = (
            "fresh" if _identity is not None and
            footage_analysis.slot_is_fresh(identity_lane, digest, root)
            else "stale" if _identity is not None else "unknown")
        m3c_doc = expression_classifier.read_m3c(digest, root)
        expression_lane = next(item for item in footage_analysis.LANES
                               if item.name == "expressions")
        m3c_freshness = (
            "fresh" if m3c_doc is not None and
            footage_analysis.slot_is_fresh(expression_lane, digest, root)
            else "stale" if m3c_doc is not None else "unknown")
        m3d_doc = person_regions.read_m3d(digest, root)
        region_lane = next(item for item in footage_analysis.LANES
                           if item.name == "regions")
        m3d_freshness = (
            "fresh" if m3d_doc is not None and
            footage_analysis.slot_is_fresh(region_lane, digest, root)
            else "stale" if m3d_doc is not None else "unknown")
        region_stats_doc = region_stats.read_m3f(digest, root)
        stats_lane = next(item for item in footage_analysis.LANES
                          if item.name == "region_stats")
        m3f_freshness = (
            "fresh" if region_stats_doc is not None and m3d_doc is not None
            and region_stats_doc.get("status") == region_stats.M3F_STATUS_MEASURED
            and footage_analysis.slot_is_fresh(stats_lane, digest, root)
            and region_stats._current(region_stats_doc, m3d_doc)
            else "stale" if region_stats_doc is not None else "unknown")
        depth_doc = depth.read_m3g(digest, root)
        depth_lane = next(item for item in footage_analysis.LANES
                          if item.name == "depth")
        m3g_freshness = (
            "fresh" if depth_doc is not None and m3d_doc is not None
            and depth_doc.get("status") == depth.M3G_STATUS_MEASURED
            and (depth_doc.get("instrument") or {}).get(
                "m3d_sidecar_sha256") == (m3d_doc.get("sidecar") or {}).get(
                    "sha256")
            and footage_analysis.slot_is_fresh(depth_lane, digest, root)
            else "stale" if depth_doc is not None else "unknown")
        m4_doc = source_memory.read_scenes(digest, root)
        m4_instrument = (m4_doc or {}).get("instrument") or {}
        m4_method = m4_instrument.get("scene_pass")
        m4_freshness = (
            _memory_freshness(m4_doc, digest)
            if m4_doc is not None and m4_doc.get("status") == "measured" and
            m4_method == source_memory.M4_SCENE_PASS_METHOD
            and m4_instrument.get("boundary_method")
            == source_memory.M4_BOUNDARY_METHOD
            else "stale" if m4_doc is not None else "unknown")
        m5_doc = source_memory.read_sound(digest, root)
        if m5_doc is None:
            m5_freshness = "unknown"
        else:
            from library.tools.analysis import sound_event_pipeline
            m5_method = m5_doc.get("method")
            if ((m5_method == sound_event_pipeline.METHOD
                 and m5_doc.get("status") == "measured")
                    or (isinstance(m5_method, str)
                        and m5_method.startswith("unmeasured:")
                        and m5_doc.get("status") == "unmeasured")):
                m5_freshness = _memory_freshness(
                m5_doc, digest)
            else:
                m5_freshness = "stale"
        m7 = event_spans.read_m7(digest, root)
        presence["m7"] = m7 is not None
        try:
            verdicts = span_verification.read_verdicts(digest, root) or {}
            presence["m8_verdicts"] = len(verdicts) if isinstance(
                verdicts, dict) else 0
        except (OSError, ValueError):
            verdicts = {}
            presence["m8_verdicts"] = 0
        if not isinstance(verdicts, dict):
            verdicts = {}
            presence["m8_verdicts"] = 0
        candidate_spans: list[tuple[str, dict]] = []
        for _kind, _body in ((m7 or {}).get("candidates") or {}).items():
            if not isinstance(_body, dict):
                continue
            for _span in _body.get("spans") or []:
                if isinstance(_span, dict):
                    candidate_spans.append((_kind, _span))
        presence["m8_unmatched_verdicts"] = len(
            _unmatched_verdicts(candidate_spans, verdicts))
        events_lane = next(item for item in footage_analysis.LANES
                           if item.name == "events")
        try:
            m7_slot_fresh = (m7 is not None
                             and footage_analysis.slot_is_fresh(
                                 events_lane, digest, root))
        except OSError:
            m7_slot_fresh = False
        if m7 is None:
            m7_freshness = "unknown"
        elif (m7.get("content_digest") != digest
                or _memory_freshness(m7, digest) == "stale"
                or not m7_slot_fresh):
            m7_freshness = "stale"
        else:
            m7_freshness = _memory_freshness(m7, digest)
            if m7_freshness != "fresh":
                m7_freshness = "unknown"
        if not verdicts:
            m8_freshness = "unknown"
        elif m7 is None or m7_freshness == "stale":
            m8_freshness = "stale"
        else:
            m8_freshness = m7_freshness
        m6_doc = conversation_clock.read_clock(digest, root)
        if m6_doc is None:
            m6_freshness = "unknown"
        elif (m6_doc.get("content_digest") != digest
                or m6_doc.get("ngram_size") != conversation_clock.NGRAM_SIZE
                or _memory_freshness(m6_doc, digest) == "stale"):
            m6_freshness = "stale"
        else:
            m6_freshness = _memory_freshness(m6_doc, digest)
            if m6_freshness != "fresh":
                m6_freshness = "unknown"
        m10_doc = source_memory.read_relationships(digest, root)
        relationships_lane = next(
            item for item in footage_analysis.LANES
            if item.name == "relationships")
        try:
            m10_slot_fresh = (
                m10_doc is not None
                and footage_analysis.slot_is_fresh(
                    relationships_lane, digest, root))
        except OSError:
            m10_slot_fresh = False
        if m10_doc is None:
            m10_freshness = "unknown"
        else:
            from library.tools.relationships import M10_METHOD as _M10M
            if ((m10_doc.get("instrument") or {}).get("method") != _M10M
                    or m10_doc.get("content_digest") != digest
                    or _memory_freshness(m10_doc, digest) == "stale"
                    or not m10_slot_fresh):
                m10_freshness = "stale"
            else:
                m10_freshness = _memory_freshness(m10_doc, digest)
                if m10_freshness != "fresh":
                    m10_freshness = "unknown"
        migrated = counted.get(digest, {})
        native = native_counted.get(digest, {})
        m1_freshness = ("fresh" if m1 is not None and m1_status == "fresh"
                        else "stale" if m1_status == "stale" else "unknown")
        lanes[digest] = {
            "present": presence,
            "freshness": {"m0": m0_freshness, "m1": m1_freshness,
                          "m3": m3_freshness, "m3b": identity_freshness,
                          "m3c": m3c_freshness, "m3d": m3d_freshness,
                          "m3e": freshness, "m3f": m3f_freshness,
                          "m3g": m3g_freshness, "m4": m4_freshness,
                          "m5": m5_freshness, "m6": m6_freshness,
                          "m7": m7_freshness, "m8": m8_freshness,
                          "m10": m10_freshness},
            "processing_status": {"m1": m1_status},
            "migrated_observations": migrated,
            "native_migrated_observations": native,
            "legacy_only_migrated_observations": {
                lane: total - native.get(lane, 0)
                for lane, total in migrated.items()
                if total > native.get(lane, 0)},
        }
    return {"project": str(project_folder),
            "model_id": model.get("id"),
            "schema_version": model.get("schema_version"),
            "counts": report.get("counts"),
            "lanes": lanes,
            "dropped": report.get("dropped") or [],
            "notes": report.get("notes") or [],
            "absent_identity": [
                ("cross-clip object links: withheld pending a linking "
                 "producer; first held-out measurement abstains on 2 of 2 "
                 "cross-clip entities "
                 "(2026-10-14-object-continuity-benchmark.json)"),
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
    None, never a best-effort neighbour. Only this person's tracks are
    returned - never another person's, never an unassigned track.
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
            from library.tools.project_model_store import commit_evidence_import
            revision = commit_evidence_import(
                Path(args.project), model,
                expected_revision=args.expected_revision,
                author="capability:project_model_evidence", reason=args.reason)
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
