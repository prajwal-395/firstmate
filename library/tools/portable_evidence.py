"""INFRA-001: the portable multimodal evidence model - ONE record shape.

Ren stores evidence in many shapes. A Project Model observation carries
its evidence contract inside its payload; an event carries a certainty
and a list of the observations it rests on; a relation carries a
subject, a predicate and an object; a track carries samples; a mask
carries a representation. Each shape says something true, and a reader
who wants to know *where a claim came from* has to learn all of them.

This module is the ONE portable record every piece of evidence can be
lifted onto. It does not add a second vocabulary: the source reference
is the universal one (``library/tools/source_reference.py``, PR 3116),
the epistemic class is the one the claim producers declare
(``library/tools/claim_provenance.py``, PR 3137), and the coverage
kinds are the contract's (``library/tools/evidence_contract.py``). What
it adds is the part that had no home: a record says what KIND of thing
it is, which MODALITIES it is anchored into, and whether it is a LOSSY
description of the media rather than a reading of it.

**The record.** One shape, four kinds - ``interval`` (a span of a
source), ``entity`` (a thing that persists), ``event`` (something that
happened) and ``relationship`` (how two things stand to each other) -
and four modalities - ``video``, ``audio``, ``text`` and ``derived``.

**The three attributes every record carries.** A portable record names
the source it was read from, the epistemic class of the claim, and how
much of the source was actually covered. A record missing one of them
is not portable: it cannot be checked, and it cannot be carried to
another machine with its meaning intact.

**Immutable media, separately.** The portable document has two
sections. ``sources`` is the immutable original media - the asset, its
streams, the clock that counts its elements - keyed by the content
digest, so the same bytes have the same name on every machine. ``records``
is the derived measurement, and every record names the source it came
from. A record never names another record as its source: grounding a
claim in a description of a frame is grounding it in a caption.

**Recovering the exact original frames and audio samples.** A record
carries source references over the SOURCE's own clock, and the document
carries that clock's rate. A reference that cannot be placed on its
clock - a span longer than the source it claims to be in - is REFUSED
rather than emitted: a wrong reference is worse than a missing one,
because a wrong one is believed.

**Semantic observations are explicitly lossy.** A transcription, a
scene description, a label: these describe the media, they are not the
media. A lossy record says so, and a lossy record may never be filed as
a measurement - that is the rule that keeps a model's description from
reading like an instrument's reading.

**Versioning and migration.** A record carries its version. A record
older than this reader is MIGRATED (the pre-contract reference
spellings become the modern ones; the contract nested in a payload is
lifted to the top level), and a record NEWER than this reader is
REFUSED, never half-read.

    python3 -m library.tools.portable_evidence census --model MODEL.json
    python3 -m library.tools.portable_evidence migrate --model MODEL.json \\
        --out portable.json [--reel reel-26] [--producer m1]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import (
    claim_provenance,
    epistemic_vocabulary,
    evidence_contract,
    source_reference,
)

SCHEMA = "ren.portable-evidence"
SCHEMA_VERSION = 1

#: The kinds of thing a portable record can be. The list IS the
#: contract: a kind that is not here has no reading.
RECORD_KINDS = ("interval", "entity", "event", "relationship")

#: The modalities a record can be anchored into. A record names the
#: media it was read from; ``derived`` is for a record computed over
#: other records rather than read off the media.
MODALITIES = ("video", "audio", "text", "derived")

#: The epistemic classes, the same vocabulary the claim producers
#: declare. ``unknown`` is the ABSENCE of a class: it is kept, never
#: filled in with a measurement.
PROVENANCE_CLASSES = ("measured", "inferred", "interpreted", "unknown")

#: The coverage kinds, the contract's own.
COVERAGE_KINDS = ("complete", "sampled", "inferred", "unavailable")

#: The stored observation modality -> the portable modality. A
#: transcription is a reading of WORDS, so it is a text record that
#: points at audio; a face box is a reading of the PICTURE.
MODALITY_OF_STORED = {
    "speech": "text",
    "speech_word": "text",
    "speech_pause": "text",
    "speech_rate": "text",
    "speech_completeness": "text",
    "speech_disfluency": "text",
    "speech_repetition": "text",
    "speech_take_link": "text",
    "voice_turn": "text",
    "cross_modal_report": "text",
    "face": "video",
    "region_frame": "video",
    "region_track": "video",
    "image_measurement": "video",
    "scene": "video",
    "scene_boundary": "video",
    "cinematography_interval": "video",
    "sound_event": "audio",
    "source_metadata": "derived",
    "multicam_offset": "derived",
}

#: The modality a record was READ from, given the modality it is in.
#: A transcription is a text record that was read from the audio; a
#: face box is a video record read from the picture.
SOURCE_MODALITY = {
    "text": "audio",
    "video": "video",
    "audio": "audio",
}

#: The payload keys that carry a DESCRIPTION of the media rather than a
#: measurement of it. A record holding one of these is lossy: it
#: describes the picture or the sound, and it is not the picture.
DESCRIPTIVE_KEYS = (
    "text", "label", "location", "type", "lighting", "notable_features",
    "claim", "result", "description", "reason", "statement", "words",
)

#: The registry units whose value IS a description. A record whose value
#: is words or labels is a summary of the media, and it is lossy
#: whatever else it carries.
DESCRIPTIVE_UNITS = ("text", "labels", "regions")

#: The producers this module has been given. One at a time, on purpose:
#: a producer is migrated when its records can be lifted onto the
#: portable shape without losing a value, and the census names the ones
#: that are still pending.
MIGRATED_PRODUCERS = ("m1",)


class PortableEvidenceError(ValueError):
    """A record that is not a portable evidence record, or cannot be one.

    Carries the reason so a refusal names what was wrong rather than
    only that something was.
    """


# ── The record ─────────────────────────────────────────────────────


def build_record(*, id: str, kind: str, modality: str, asset_id: str,
                 stream_id: str, clock_id: str,
                 interval: Mapping[str, Any] | None = None,
                 source_references: Sequence[Mapping[str, Any]] = (),
                 modality_refs: Sequence[Mapping[str, Any]] = (),
                 provenance: Mapping[str, Any] | None = None,
                 coverage: Mapping[str, Any] | None = None,
                 lossy: bool = False,
                 semantic: Mapping[str, Any] | None = None,
                 value: Any = None,
                 entities: Sequence[str] = (),
                 relation: Mapping[str, Any] | None = None,
                 migrated_from: str | None = None) -> dict[str, Any]:
    """Build one portable evidence record.

    The record is validated before it is returned: a builder that can
    produce an invalid record is a builder that eventually does.
    """
    record: dict[str, Any] = {
        "evidence_model": SCHEMA,
        "version": SCHEMA_VERSION,
        "id": id,
        "kind": kind,
        "modality": modality,
        "asset_id": asset_id,
        "stream_id": stream_id,
        "clock_id": clock_id,
        "interval": dict(interval) if interval else None,
        "source_references": [dict(reference)
                              for reference in source_references],
        "modality_refs": [dict(ref) for ref in modality_refs],
        "provenance": dict(provenance) if provenance else None,
        "coverage": dict(coverage) if coverage else None,
        "lossy": bool(lossy),
        "semantic": dict(semantic) if semantic is not None else None,
        "value": value,
        "entities": list(entities),
        "relation": dict(relation) if relation else None,
        "migration": {"from_version": SCHEMA_VERSION if migrated_from is None
                      else 0, "migrated_from": migrated_from},
    }
    return parse_record(record)


def parse_record(record: Any) -> dict[str, Any]:
    """Check one portable record, or refuse it by name.

    The checks are the contract:

    * the version is not newer than this reader knows;
    * the kind and the modality are in the vocabularies;
    * the record names the immutable original media;
    * at least one source reference NAMES THE SOURCE - a record that
      points at nothing is a record about nothing;
    * the provenance names a class in the vocabulary, a producer and the
      registry type the claim was filed under;
    * the coverage names a kind, and an unavailable coverage carries
      neither a value nor samples;
    * a LOSSY record is never filed as a measurement.
    """
    if not isinstance(record, Mapping):
        raise PortableEvidenceError("a portable record is a mapping")
    version = record.get("version")
    if not isinstance(version, int):
        raise PortableEvidenceError("a portable record carries its version")
    if version > SCHEMA_VERSION:
        raise PortableEvidenceError(
            f"record stamped v{version}; this reader knows "
            f"v{SCHEMA_VERSION}")
    if record.get("evidence_model") not in (None, SCHEMA):
        raise PortableEvidenceError(
            f"record names model {record.get('evidence_model')!r}, "
            f"not {SCHEMA!r}")
    kind = record.get("kind")
    if kind not in RECORD_KINDS:
        raise PortableEvidenceError(
            f"record kind {kind!r} is not one of {RECORD_KINDS}")
    modality = record.get("modality")
    if modality not in MODALITIES:
        raise PortableEvidenceError(
            f"record modality {modality!r} is not one of {MODALITIES}")
    for key in ("id", "asset_id", "stream_id", "clock_id"):
        if not record.get(key):
            raise PortableEvidenceError(
                f"a portable record names its {key}")
    references = record.get("source_references")
    if not isinstance(references, list) or not references:
        raise PortableEvidenceError(
            "a portable record carries at least one source reference")
    names_source = False
    for reference in references:
        try:
            source_reference.parse(reference)
        except source_reference.SourceReferenceError as exc:
            raise PortableEvidenceError(
                f"source reference is not one: {exc}") from exc
        if source_reference.names_source(reference):
            names_source = True
    if not names_source:
        raise PortableEvidenceError(
            "a portable record carries a reference that NAMES THE SOURCE; "
            "a reference that names only the file is not a place in it")
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        raise PortableEvidenceError("a portable record carries its provenance")
    klass = provenance.get("class")
    if klass not in PROVENANCE_CLASSES:
        raise PortableEvidenceError(
            f"provenance class {klass!r} is not one of {PROVENANCE_CLASSES}")
    if not provenance.get("producer"):
        raise PortableEvidenceError("provenance names its producer")
    if not provenance.get("measurement_type"):
        raise PortableEvidenceError(
            "provenance names the registry type its claim was filed under")
    coverage = record.get("coverage")
    if not isinstance(coverage, Mapping):
        raise PortableEvidenceError("a portable record carries its coverage")
    if coverage.get("kind") not in COVERAGE_KINDS:
        raise PortableEvidenceError(
            f"coverage kind {coverage.get('kind')!r} is not one of "
            f"{COVERAGE_KINDS}")
    samples = coverage.get("samples")
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 0:
        raise PortableEvidenceError("coverage carries a whole sample count")
    if coverage.get("kind") == "unavailable" and (samples or
                                                  record.get("value") is not None):
        raise PortableEvidenceError(
            "unavailable coverage carries no samples and no value")
    lossy = bool(record.get("lossy"))
    semantic = record.get("semantic")
    if semantic is not None and not lossy:
        raise PortableEvidenceError(
            "a record carrying a description is lossy, and says so")
    if lossy and klass == "measured":
        raise PortableEvidenceError(
            "a lossy description is not a measurement: a model said so, "
            "and no instrument read it")
    if kind in ("interval", "event"):
        interval = record.get("interval")
        if not isinstance(interval, Mapping) or not interval.get("clock_id"):
            raise PortableEvidenceError(
                f"a {kind} record carries the interval it happened over")
    if kind == "relationship":
        relation = record.get("relation")
        if (not isinstance(relation, Mapping)
                or not relation.get("subject")
                or not relation.get("predicate")
                or not relation.get("object")):
            raise PortableEvidenceError(
                "a relationship record names its subject, its predicate "
                "and its object")
    return dict(record)


# ── The model index ────────────────────────────────────────────────


class ModelIndex:
    """The parts of a Project Model a portable record is read against.

    The assets, the streams, the clocks and the provenance rows: enough
    to name the immutable original media, to place a span on its clock,
    and to say who produced a claim.
    """

    def __init__(self, model: Mapping[str, Any]) -> None:
        self.model = model
        self.assets = {row["id"]: row for row in model.get("assets") or []
                       if isinstance(row, Mapping) and row.get("id")}
        self.streams = {row["id"]: row for row in model.get("streams") or []
                        if isinstance(row, Mapping) and row.get("id")}
        self.clocks = {row["id"]: row for row in model.get("clocks") or []
                       if isinstance(row, Mapping) and row.get("id")}
        self.provenance = {row["id"]: row
                           for row in model.get("provenance") or []
                           if isinstance(row, Mapping) and row.get("id")}
        self._streams_by_clock: dict[str, list[dict[str, Any]]] = {}
        for stream in self.streams.values():
            clock_id = stream.get("clock_id")
            if clock_id:
                self._streams_by_clock.setdefault(clock_id, []).append(stream)

    def clock(self, clock_id: str) -> dict[str, Any] | None:
        return self.clocks.get(clock_id)

    def stream_for(self, asset_id: str, clock_id: str) -> dict[str, Any] | None:
        """The stream of an asset that the given clock counts.

        A source asset carries a video stream and several audio
        streams; a clock names the stream it belongs to, and that is
        the one a reference into the source must use.
        """
        for stream in self._streams_by_clock.get(clock_id, []):
            if stream.get("asset_id") == asset_id:
                return stream
        for stream in self._streams_by_clock.get(clock_id, []):
            return stream
        for stream in self.streams.values():
            if stream.get("asset_id") == asset_id:
                return stream
        return None

    def streams_of_modality(self, asset_id: str, clock_id: str,
                            modality: str) -> list[dict[str, Any]]:
        """Every stream of one asset, on one clock, of one modality.

        A source carries one video stream and several audio channels;
        a reference into the source names ONE of them, and the count
        of the others is what tells a reader that the choice was not
        forced.
        """
        found = []
        for stream in self._streams_by_clock.get(clock_id, []):
            if stream.get("asset_id") != asset_id:
                continue
            if self.modality_of_stream(stream) != modality:
                continue
            found.append(stream)
        found.sort(key=lambda row: row.get("stream_index") or 0)
        return found

    def modality_of_stream(self, stream: Mapping[str, Any] | None
                           ) -> str:
        """The portable modality a stream belongs to."""
        if not stream:
            return "derived"
        codec_type = str((stream.get("metadata") or {}).get("codec_type")
                         or "")
        if codec_type == "video":
            return "video"
        if codec_type == "audio":
            return "audio"
        return "derived"

    def duration_ticks(self, asset_id: str, clock_id: str) -> int | None:
        """How many ticks of this clock the source itself holds.

        The asset's measured duration, on the clock's own rate. A span
        longer than this is not a span of the source, and a reference to
        it cannot be recovered.
        """
        asset = self.assets.get(asset_id) or {}
        duration = (asset.get("metadata") or {}).get("duration_seconds")
        clock = self.clock(clock_id) or {}
        rate = clock.get("ticks_per_second")
        if not isinstance(duration, (int, float)) or not isinstance(rate, int):
            return None
        if duration <= 0 or rate <= 0:
            return None
        return int(duration * rate)

    def provenance_row(self, provenance_id: str | None
                       ) -> dict[str, Any] | None:
        if not provenance_id:
            return None
        return self.provenance.get(provenance_id)


def lane_of(provenance_id: str | None) -> str:
    """The producer lane a provenance id names.

    ``prov-<digest>-m1`` is the M1 transcript lane; ``prov-reel-build``
    is the reel build. The lane is the unit a producer is migrated in.
    """
    if not isinstance(provenance_id, str) or not provenance_id:
        return "unknown"
    if provenance_id == "prov-reel-build":
        return "reel-build"
    tail = provenance_id.rsplit("-", 1)[-1]
    return tail or "unknown"


# ── Lifting a stored record onto the portable shape ────────────────


def _migrate_references(references: Any, clock_id: str
                         ) -> tuple[list[dict[str, Any]], list[str]]:
    """The source references of a stored record, in the modern spelling.

    The pre-contract spellings are READ and never written, so a stored
    ``source_frame`` becomes the modern ``element`` here. A reference
    that cannot be upgraded is dropped and named: it is not silently
    carried forward as if it were a modern reference.
    """
    modern: list[dict[str, Any]] = []
    dropped: list[str] = []
    if not isinstance(references, Sequence) or isinstance(references, (str, bytes)):
        return modern, dropped
    for reference in references:
        if not isinstance(reference, Mapping):
            dropped.append("not a mapping")
            continue
        kind = reference.get("kind")
        if kind in source_reference.LEGACY_REFERENCE_KINDS:
            upgraded = source_reference.upgrade_legacy(reference, clock_id)
            if upgraded is None:
                dropped.append(f"legacy reference {kind!r} has no modern "
                               f"spelling without a clock")
                continue
            modern.append(upgraded)
            continue
        try:
            modern.append(source_reference.parse(reference))
        except source_reference.SourceReferenceError as exc:
            dropped.append(str(exc))
    return modern, dropped


def _is_lossy(payload: Mapping[str, Any], unit: str | None) -> bool:
    """Whether a record DESCRIBES the media rather than measuring it.

    A transcription, a scene description, a label: the record is a
    summary of the media and not the media. A number is not lossy - it
    is a reading of the media, and the reading is exact enough to
    compare. A rate computed over a transcript is a reading; the words
    it was computed from are the description.
    """
    if unit in DESCRIPTIVE_UNITS:
        return True
    for key in DESCRIPTIVE_KEYS:
        if key in payload:
            return True
    return False


def _semantic_payload(payload: Mapping[str, Any], unit: str | None,
                      value: Any) -> dict[str, Any] | None:
    """The descriptive part of a record, or None when it has none.

    A description carried under a descriptive key is the description.
    A record whose registry unit IS a description keeps its value as
    the description: that value is the words or the labels.
    """
    described = {key: payload[key] for key in DESCRIPTIVE_KEYS
                 if key in payload}
    if described:
        return described
    if unit in DESCRIPTIVE_UNITS and isinstance(value, Mapping):
        return dict(value)
    return None


def _declared_class(measurement_type: str) -> str:
    """The epistemic class the PRODUCER declared for a registry type.

    The class is a consequence of the registry type (PR 3137): a type a
    model produced is an inference, whatever the stored record says.
    A type the registry does not know stays UNKNOWN - it is never
    defaulted to a measurement.
    """
    declared = claim_provenance.class_for_type(measurement_type)
    if declared in PROVENANCE_CLASSES:
        return declared
    return claim_provenance.UNKNOWN_CLASS


def _filed_class(contract: Mapping[str, Any]) -> str | None:
    """The class a stored record was FILED under, if it says one.

    Kept beside the declared class so the disagreement is visible: a
    record that filed a model's description as a measurement is a
    defect somebody has to see, and the portable record carries the
    evidence of it.
    """
    filed = contract.get("certainty")
    if isinstance(filed, str) and filed in PROVENANCE_CLASSES:
        return filed
    return None


def portable_record_from_observation(observation: Mapping[str, Any],
                                     index: ModelIndex,
                                     ) -> tuple[dict[str, Any] | None, str | None]:
    """Lift one stored observation onto the portable record shape.

    Returns ``(record, None)`` or ``(None, reason)``. A record that
    cannot be lifted is REFUSED, never approximated: a portable record
    that points at the wrong place in the source is worse than one that
    is absent, because the wrong one is believed.
    """
    contract = epistemic_vocabulary.contract_of(observation)
    if not isinstance(contract, Mapping):
        return None, "the record carries no evidence contract"
    measurement_type = contract.get("measurement_type")
    if not isinstance(measurement_type, str) or not measurement_type:
        return None, "the contract names no registry type"
    asset_id = observation.get("asset_id")
    interval = observation.get("interval")
    if not isinstance(asset_id, str) or not asset_id:
        return None, "the record names no asset"
    if not isinstance(interval, Mapping):
        return None, "the record carries no interval"
    clock_id = interval.get("clock_id")
    if not isinstance(clock_id, str) or not clock_id:
        return None, "the record's interval names no clock"
    start, end = interval.get("start"), interval.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None, "the record's interval is not counted in whole ticks"
    if end <= start:
        return None, "the record's interval is empty"
    stream = index.stream_for(asset_id, clock_id)
    if stream is None:
        return None, "the record names an asset with no stream on its clock"
    # The span has to FIT the source it claims to be in. A span longer
    # than the source is a span on another grid, and a reference to it
    # would recover the wrong frames.
    limit = index.duration_ticks(asset_id, clock_id)
    if limit is not None and end > limit:
        return None, (f"the record's interval ends at tick {end}, past the "
                      f"source's own {limit} ticks on this clock: the span "
                      f"is not a span of this source")
    modality = MODALITY_OF_STORED.get(str(observation.get("modality")),
                                      "derived")
    if modality == "derived":
        modality = index.modality_of_stream(stream)
    source_modality = SOURCE_MODALITY.get(modality)
    if source_modality is None:
        source_modality = index.modality_of_stream(stream)
    # The reference names the stream of the modality the record was
    # READ from: a transcription is a reading of the audio, and a face
    # box is a reading of the picture. A source with several streams
    # of one modality (four audio channels, say) is a choice, and the
    # count of the alternatives travels with the record.
    modality_streams = index.streams_of_modality(asset_id, clock_id,
                                                 source_modality)
    if not modality_streams:
        modality_streams = [stream]
    reference_stream = modality_streams[0]
    stream_id = str(reference_stream.get("id"))
    references, dropped = _migrate_references(contract.get("references"),
                                              clock_id)
    if not references:
        references = [source_reference.interval(stream_id, clock_id,
                                                start, end)]
    if not any(source_reference.names_source(reference)
               for reference in references):
        return None, "the record's references name no source"

    modality_refs = []
    for reference in references:
        reference_stream_id = reference.get("stream_id")
        referenced = index.streams.get(reference_stream_id) \
            if reference_stream_id else None
        anchor = {
            "modality": index.modality_of_stream(referenced)
            if referenced else source_modality,
            "reference": dict(reference),
        }
        alternatives = index.streams_of_modality(
            asset_id, clock_id, anchor["modality"])
        if len(alternatives) > 1:
            anchor["streams_of_this_modality"] = len(alternatives)
        modality_refs.append(anchor)

    payload = observation.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    unit = None
    try:
        unit = evidence_contract.registry_entry(measurement_type)["unit"]
    except evidence_contract.EvidenceContractError:
        unit = None
    value = contract.get("value")
    semantic = _semantic_payload(payload, unit, value)
    lossy = _is_lossy(payload, unit)
    provenance_row = index.provenance_row(observation.get("provenance_id"))
    provenance = {
        "class": _declared_class(measurement_type),
        "filed_class": _filed_class(contract),
        "producer": (provenance_row or {}).get("producer")
        or contract.get("producer") or "unknown",
        "producer_version": (provenance_row or {}).get("producer_version")
        or contract.get("producer_version"),
        "method": contract.get("method"),
        "measurement_type": measurement_type,
        "provenance_id": observation.get("provenance_id"),
    }
    coverage = {
        "kind": contract.get("coverage") or "unavailable",
        "samples": contract.get("coverage_samples") or 0,
        "scope": contract.get("coverage_scope"),
        "status": contract.get("coverage_status"),
        "freshness": contract.get("freshness"),
    }
    if coverage["kind"] not in COVERAGE_KINDS:
        coverage["kind"] = "unavailable"
    record = build_record(
        id=str(observation.get("id") or ""),
        kind="interval",
        modality=modality,
        asset_id=asset_id,
        stream_id=stream_id,
        clock_id=clock_id,
        interval={"clock_id": clock_id, "start": start, "end": end},
        source_references=references,
        modality_refs=modality_refs,
        provenance=provenance,
        coverage=coverage,
        lossy=lossy,
        semantic=semantic or None,
        value=value,
        migrated_from=str(observation.get("id") or ""),
    )
    if dropped:
        record["migration"]["dropped_references"] = dropped
    return record, None


# ── The census ─────────────────────────────────────────────────────


def _stored_rows(observations: Sequence[Mapping[str, Any]],
                 index: ModelIndex) -> list[dict[str, Any]]:
    """One row per stored observation, with the three attributes read.

    The attributes are read the way the portable model reads them, so
    the before and the after are measured with the same ruler. The
    class the record was FILED under is kept beside the class its
    producer DECLARED: the disagreement between them is the defect
    this model exists to end.
    """
    rows: list[dict[str, Any]] = []
    for observation in observations:
        contract = epistemic_vocabulary.contract_of(observation) or {}
        references, _dropped = _migrate_references(
            contract.get("references"),
            str((observation.get("interval") or {}).get("clock_id") or ""))
        has_reference = any(source_reference.names_source(reference)
                            for reference in references)
        measurement_type = contract.get("measurement_type")
        declared = _declared_class(measurement_type) \
            if isinstance(measurement_type, str) else claim_provenance.UNKNOWN_CLASS
        filed = _filed_class(contract)
        coverage = contract.get("coverage")
        payload = observation.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        unit = None
        if isinstance(measurement_type, str):
            try:
                unit = evidence_contract.registry_entry(measurement_type)["unit"]
            except evidence_contract.EvidenceContractError:
                unit = None
        rows.append({
            "shape": tuple(sorted(observation)),
            "payload_shape": tuple(sorted(payload)),
            "source_reference": has_reference,
            "provenance_class": filed,
            "declared_class": declared,
            "filed_class": filed,
            "coverage": coverage if coverage in COVERAGE_KINDS else None,
            "lossy": _is_lossy(payload, unit),
            "producer": lane_of(observation.get("provenance_id")),
            "kind": "interval",
        })
    return rows


def _portable_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per portable record, with the three attributes read."""
    rows: list[dict[str, Any]] = []
    for record in records:
        provenance = record.get("provenance") or {}
        coverage = record.get("coverage") or {}
        declared = provenance.get("class")
        filed = provenance.get("filed_class")
        rows.append({
            "shape": tuple(sorted(record)),
            "payload_shape": (),
            "source_reference": any(
                source_reference.names_source(reference)
                for reference in record.get("source_references") or []),
            "provenance_class": declared
            if declared in PROVENANCE_CLASSES else None,
            "declared_class": declared,
            "filed_class": filed,
            "coverage": coverage.get("kind")
            if coverage.get("kind") in COVERAGE_KINDS else None,
            "lossy": bool(record.get("lossy")),
            "producer": lane_of((provenance or {}).get("provenance_id")),
            "kind": record.get("kind"),
        })
    return rows


def census_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count the shapes and the three attributes of a set of rows.

    ``distinct_shapes`` is the number of record shapes the rows come
    in; ``with_all_three`` is the share of rows that carry a source
    reference, a provenance class AND a coverage - the three attributes
    a portable record must carry. ``class_defects`` counts the rows
    whose class disagrees with the class their producer declared: a
    model's description filed as a measurement.
    """
    total = len(rows)
    shapes: dict[str, int] = {}
    payload_shapes: dict[str, int] = {}
    record_shapes: set[tuple[str, str]] = set()
    by_producer: dict[str, dict[str, int]] = {}
    by_kind: dict[str, int] = {}
    with_reference = with_class = with_coverage = with_all = 0
    class_defects = 0
    filing_disagreements = 0
    lossy = 0
    for row in rows:
        shape = "|".join(row.get("shape") or ())
        shapes[shape] = shapes.get(shape, 0) + 1
        payload_shape = "|".join(row.get("payload_shape") or ())
        if payload_shape:
            payload_shapes[payload_shape] = payload_shapes.get(payload_shape, 0) + 1
        record_shapes.add((shape, payload_shape))
        producer = str(row.get("producer") or "unknown")
        bucket = by_producer.setdefault(producer, {
            "rows": 0, "with_source_reference": 0, "with_provenance_class": 0,
            "with_coverage": 0, "with_all_three": 0, "class_defects": 0})
        bucket["rows"] += 1
        kind = str(row.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        reference = bool(row.get("source_reference"))
        klass = row.get("provenance_class")
        coverage = row.get("coverage")
        if reference:
            with_reference += 1
            bucket["with_source_reference"] += 1
        if klass:
            with_class += 1
            bucket["with_provenance_class"] += 1
        if coverage:
            with_coverage += 1
            bucket["with_coverage"] += 1
        if reference and klass and coverage:
            with_all += 1
            bucket["with_all_three"] += 1
        declared = row.get("declared_class")
        carried = row.get("provenance_class")
        if declared and declared != claim_provenance.UNKNOWN_CLASS \
                and carried and carried != declared:
            class_defects += 1
            bucket["class_defects"] += 1
        filed = row.get("filed_class")
        if declared and declared != claim_provenance.UNKNOWN_CLASS \
                and filed and filed != declared:
            filing_disagreements += 1
        if row.get("lossy"):
            lossy += 1
    return {
        "records": total,
        "distinct_shapes": len(shapes),
        "distinct_payload_shapes": len(payload_shapes),
        "distinct_record_shapes": len(record_shapes),
        "with_source_reference": with_reference,
        "with_provenance_class": with_class,
        "with_coverage": with_coverage,
        "with_all_three": with_all,
        "share_with_all_three": (with_all / total) if total else 0.0,
        "class_defects": class_defects,
        "filing_disagreements": filing_disagreements,
        "lossy": lossy,
        "by_producer": by_producer,
        "by_kind": by_kind,
        "shapes": shapes,
    }


# ── The scope of a reel ────────────────────────────────────────────


def reel_assets(model: Mapping[str, Any], reel: str) -> list[str]:
    """The assets one reel draws its records from.

    A reel's clip uses name the assets it cut from; those assets' rows
    are the reel's records. A reel that names no clip use has no scope,
    and the census says so rather than counting the whole project.
    """
    assets: list[str] = []
    for use in model.get("clip_uses") or []:
        if not isinstance(use, Mapping):
            continue
        if reel not in str(use.get("id") or ""):
            continue
        asset_id = use.get("asset_id")
        if isinstance(asset_id, str) and asset_id and asset_id not in assets:
            assets.append(asset_id)
    return assets


def scope_observations(model: Mapping[str, Any], assets: Sequence[str]
                       ) -> list[dict[str, Any]]:
    """The observations of the given assets."""
    wanted = set(assets)
    return [row for row in model.get("observations") or []
            if isinstance(row, Mapping) and row.get("asset_id") in wanted]


def scope_clocks(model: Mapping[str, Any], assets: Sequence[str]
                 ) -> set[str]:
    """The clocks the given assets' records are counted on."""
    wanted = set(assets)
    index = ModelIndex(model)
    clocks: set[str] = set()
    for stream in index.streams.values():
        if stream.get("asset_id") in wanted and stream.get("clock_id"):
            clocks.add(str(stream["clock_id"]))
    return clocks


#: The collections that hold stored evidence besides the observations.
#: Each one is a shape of its own, and the census counts them so the
#: number of shapes in production is the whole number.
EVIDENCE_COLLECTIONS = ("events", "relations", "tracks", "masks",
                         "signals", "identity_links")


def other_evidence_shapes(model: Mapping[str, Any],
                           assets: Sequence[str]) -> dict[str, Any]:
    """The shapes of the stored evidence that is not an observation.

    A reel's events, relations, tracks, masks, signals and identity
    links are stored evidence too, and each collection has a shape of
    its own. The count is reported with the collection it belongs to,
    so the denominators are never combined.
    """
    clocks = scope_clocks(model, assets)
    wanted = set(assets)
    report: dict[str, Any] = {}
    for collection in EVIDENCE_COLLECTIONS:
        rows = [row for row in model.get(collection) or []
                if isinstance(row, Mapping)]
        if collection == "signals":
            rows = [row for row in rows if row.get("asset_id") in wanted]
        elif collection == "identity_links":
            rows = [row for row in rows
                    if str(row.get("provenance_id") or "").endswith("-m1")
                    or str(row.get("provenance_id") or "") == "prov-reel-build"]
        else:
            rows = [row for row in rows
                    if str((row.get("interval") or {}).get("clock_id")
                           or row.get("clock_id") or "") in clocks]
        shapes: dict[str, int] = {}
        for row in rows:
            shape = "|".join(sorted(row))
            shapes[shape] = shapes.get(shape, 0) + 1
        report[collection] = {"rows": len(rows),
                              "distinct_shapes": len(shapes),
                              "shapes": shapes}
    return report


# ── The portable document ──────────────────────────────────────────


def portable_sources(model: Mapping[str, Any], index: ModelIndex,
                     assets: Sequence[str]) -> list[dict[str, Any]]:
    """The immutable original media the records were read from.

    Keyed by the content digest, so the same bytes carry the same name
    on another machine. No path is written: the document is portable
    because it names the media, not where this machine keeps it.
    """
    wanted = set(assets)
    sources: list[dict[str, Any]] = []
    for asset_id in sorted(wanted):
        asset = index.assets.get(asset_id) or {}
        streams = []
        for stream in index.streams.values():
            if stream.get("asset_id") != asset_id:
                continue
            clock = index.clock(str(stream.get("clock_id") or "")) or {}
            streams.append({
                "stream_id": stream.get("id"),
                "modality": index.modality_of_stream(stream),
                "stream_index": stream.get("stream_index"),
                "clock_id": stream.get("clock_id"),
                "ticks_per_second": clock.get("ticks_per_second"),
                "frame_rate": clock.get("frame_rate"),
                "metadata": stream.get("metadata") or {},
            })
        streams.sort(key=lambda row: (row.get("modality") or "",
                                       row.get("stream_index") or 0))
        sources.append({
            "asset_id": asset_id,
            "content_digest": asset.get("content_digest"),
            "kind": asset.get("kind"),
            "duration_seconds": (asset.get("metadata") or {}).get(
                "duration_seconds"),
            "streams": streams,
        })
    return sources


def build_portable_document(model: Mapping[str, Any],
                             records: Sequence[Mapping[str, Any]],
                             index: ModelIndex,
                             assets: Sequence[str]) -> dict[str, Any]:
    """The portable document: the immutable media, and the records."""
    document = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_from": {
            "model_id": model.get("id"),
            "schema_version": model.get("schema_version"),
        },
        "sources": portable_sources(model, index, assets),
        "records": [dict(record) for record in records],
    }
    assert_portable(document)
    return document


def assert_portable(document: Any, where: str = "$") -> None:
    """Refuse a document that carries a path from this machine.

    A portable document names the media and the bytes, never where this
    machine keeps them: the content digest is the name, and it means
    the same thing everywhere.
    """
    if isinstance(document, Mapping):
        for key, value in document.items():
            assert_portable(value, f"{where}.{key}")
    elif isinstance(document, (list, tuple)):
        for index, value in enumerate(document):
            assert_portable(value, f"{where}[{index}]")
    elif isinstance(document, str):
        for marker in ("/Users/", "/home/", str(Path.home())):
            if marker and marker != "/" and marker in document:
                raise PortableEvidenceError(
                    f"{where} carries a local path ({marker!r}); a portable "
                    f"document names the media, not where this machine "
                    f"keeps it")


# ── The CLI ────────────────────────────────────────────────────────


def load_model(path: str | Path) -> dict[str, Any]:
    """Read a stored Project Model (a snapshot, a checkpoint, or a model)."""
    return epistemic_vocabulary.load_model(path)


def migrate_observations(model: Mapping[str, Any],
                         observations: Sequence[Mapping[str, Any]],
                         ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Lift the given observations onto the portable shape.

    Returns the portable records and the refusals, one per record that
    could not be lifted. A refusal is named, never dropped: a record
    that cannot be portable is a defect somebody has to see.
    """
    index = ModelIndex(model)
    records: list[dict[str, Any]] = []
    refusals: list[dict[str, str]] = []
    for observation in observations:
        record, reason = portable_record_from_observation(observation, index)
        if record is None:
            refusals.append({
                "id": str(observation.get("id") or ""),
                "reason": reason or "refused",
            })
            continue
        records.append(record)
    return records, refusals


def _census_report(model: Mapping[str, Any], reel: str | None,
                   producers: Sequence[str] | None) -> dict[str, Any]:
    assets = reel_assets(model, reel) if reel else []
    observations = scope_observations(model, assets) if reel else [
        row for row in model.get("observations") or []
        if isinstance(row, Mapping)]
    index = ModelIndex(model)
    stored = _stored_rows(observations, index)
    producers = tuple(producers) if producers else MIGRATED_PRODUCERS
    selected = [row for row in observations
                if lane_of(row.get("provenance_id")) in producers]
    stored_migrated = [row for row in stored
                       if row["producer"] in producers]
    records, refusals = migrate_observations(model, selected)
    portable = _portable_rows(records)
    before = census_rows(stored)
    before_migrated = census_rows(stored_migrated)
    after = census_rows(portable)
    return {
        "reel": reel,
        "assets": assets,
        "migrated_producers": list(producers),
        "before": before,
        "before_migrated_producers": before_migrated,
        "after": after,
        "other_evidence": other_evidence_shapes(model, assets),
        "refusals": refusals,
        "scope": {
            "observations_in_scope": len(observations),
            "observations_of_migrated_producers": len(selected),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="portable_evidence",
        description="The portable multimodal evidence model: lift stored "
                    "evidence onto one record shape, and measure it.")
    sub = parser.add_subparsers(dest="command", required=True)

    census = sub.add_parser("census", help="measure the stored records")
    census.add_argument("--model", required=True,
                        help="a stored Project Model (snapshot or model)")
    census.add_argument("--reel", default=None,
                        help="the reel whose records to measure")
    census.add_argument("--producer", action="append", default=None,
                        help="the producer lane to migrate (repeatable)")

    migrate = sub.add_parser("migrate", help="write the portable document")
    migrate.add_argument("--model", required=True)
    migrate.add_argument("--out", required=True)
    migrate.add_argument("--reel", default=None)
    migrate.add_argument("--producer", action="append", default=None)

    args = parser.parse_args(argv)
    model = load_model(args.model)
    producers = tuple(args.producer) if args.producer else MIGRATED_PRODUCERS
    report = _census_report(model, args.reel, producers)
    if args.command == "census":
        print(json.dumps(report, indent=2, default=str))
        return 0
    assets = report["assets"]
    observations = scope_observations(model, assets) if args.reel else [
        row for row in model.get("observations") or []
        if isinstance(row, Mapping)]
    selected = [row for row in observations
                if lane_of(row.get("provenance_id")) in producers]
    records, refusals = migrate_observations(model, selected)
    document = build_portable_document(model, records, ModelIndex(model),
                                       assets)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=1, default=str),
                      encoding="utf-8")
    print(json.dumps({
        "out": str(target),
        "records": len(records),
        "refusals": len(refusals),
        "census": report["after"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
