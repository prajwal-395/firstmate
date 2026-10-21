"""The epistemic class of a claim: measured, inferred or interpreted.

A world model holds claims: a transcribed word, a detected face, a
scene description, a sound event, a speaking span, a model's verdict
about a statement. Until now the world model filed all of them the
same way, so a VLM's description of a room read exactly like the
device's own record of the frame rate - and a reader had no way to
tell which was which (punchlist VI-012: "separate measurements from
interpretations").

This module is the ONE enumeration of that distinction:

* **measured** - an instrument or the capture device read it. The
  clock's offset, the source's recorded metadata, a deterministic
  boundary between two shots.
* **inferred** - a model asserted it about the picture or the sound. A
  transcribed word, a detected face, a scene description, a sound event
  label, a model's verdict about a statement.
* **interpreted** - a judgement read it. A person's evaluation, a
  creative decision, an editorial reading of the footage.
* **unknown** - nobody declared how it was produced. The class stays
  unknown: it is never defaulted to a measurement.

**The class belongs to the producer, not to the reader.** Each producer
declares the registry type of the claim it writes
(``CLAIM_TYPES``); :data:`CLAIM_DECLARATIONS` below is the lookup the
world model reads, and ``tests/unit/picture/test_claim_provenance.py``
fails when the two disagree. The class itself is a CONSEQUENCE of the
registry type (``evidence_contract``), so there is one vocabulary and
not two.

**A model description is never promoted to a measurement without a
verifier.** :func:`may_file_as_measured` is the gate: a claim whose
producer was a model stays inferred unless a deterministic verifier
recorded a verification for it. A model's own confirmation of another
model's claim is recorded as a verification and does NOT raise the
class - a model agreeing with a model is still a model.

The census (:func:`census`, and the CLI) counts what a stored world
model claims: how many of its claims carry a class, and how many model
descriptions are filed as measurements. An unclassed claim is filed as
a measurement, because nothing in the record says otherwise - that is
the defect this module exists to name, not a claim about the data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from library.tools import evidence_contract

#: The classes a claim may carry. ``unknown`` is not one of them: it is
#: the absence of a class, and it is kept, never filled in.
CLAIM_CLASSES = ("measured", "inferred", "interpreted")

UNKNOWN_CLASS = "unknown"

#: The registry kind each class comes from. ``evaluation`` and
#: ``creative_judgment`` are HUMAN judgements - an interpretation of
#: what is there - and read as ``interpreted``. A model's own answer is
#: an inference, because the registry says a model-produced value is
#: always an inference (``evidence_contract``).
CLASS_FOR_KIND = {
    "measurement": "measured",
    "inference": "inferred",
    "evaluation": "interpreted",
    "creative_judgment": "interpreted",
}

#: The classes a MODEL (or a judgement) produced. A claim in one of
#: these is a description, not a reading of the signal.
MODEL_CLASSES = ("inferred", "interpreted")

#: The verifier kinds that may promote a description to a measurement.
#: A model is not one of them.
DETERMINISTIC_VERIFIERS = ("instrument", "recorded")

#: Every claim the world model holds: the lane that produced it, the
#: producer that declares its type, and the registry type it is filed
#: under. The producer's own ``CLAIM_TYPES`` is the authority; this is
#: the lookup, and a contract test keeps them equal.
CLAIM_DECLARATIONS: dict[str, dict[str, str]] = {
    "source_metadata": {
        "lane": "m0",
        "producer": "library.tools.source_memory",
        "measurement_type": "media.source_metadata",
    },
    "words": {
        "lane": "m1",
        "producer": "library.tools.source_transcript",
        "measurement_type": "speech.word",
    },
    "face_box": {
        "lane": "m3",
        "producer": "library.tools.person_measurements",
        "measurement_type": "person.face_box",
    },
    "identity": {
        "lane": "m3b",
        "producer": "library.tools.person_entity",
        "measurement_type": "object.track_sample",
    },
    "expression": {
        "lane": "m3c",
        "producer": "library.tools.expression_classifier",
        "measurement_type": "image.expression_frame",
    },
    "scene": {
        "lane": "m4",
        "producer": "library.tools.analysis.vision_pipeline_v3",
        "measurement_type": "video.scene_segment",
    },
    "scene_boundary": {
        "lane": "m4",
        "producer": "library.tools.analysis.vision_pipeline_v3",
        "measurement_type": "video.scene_boundary",
    },
    "sound_event": {
        "lane": "m5",
        "producer": "library.capabilities.temporal.index",
        "measurement_type": "audio.sound_event",
    },
    "multicam_offset": {
        "lane": "m6",
        "producer": "library.tools.conversation_clock",
        "measurement_type": "sync.multicam_offset",
    },
    "speaking_span": {
        "lane": "m7",
        "producer": "library.tools.event_spans",
        "measurement_type": "event.speaking_span",
    },
    "visible_span": {
        "lane": "m7",
        "producer": "library.tools.event_spans",
        "measurement_type": "event.visible_span",
    },
    "candidate_span": {
        "lane": "m7",
        "producer": "library.tools.event_spans",
        "measurement_type": "event.candidate_span",
    },
    "verdict": {
        "lane": "m8",
        "producer": "library.tools.span_verification",
        "measurement_type": "event.verdict",
    },
    "relationship": {
        "lane": "m10",
        "producer": "library.tools.relationships",
        "measurement_type": "relation.candidate_span",
    },
}

#: How a claim is told apart from its own kind, so the census counts
#: claims and not seconds. A claim with no fields here is identified by
#: where it was found.
IDENTITY_FIELDS = {
    "words": ("start", "end", "word"),
    "face_box": ("t", "track_id"),
    "identity": ("track_id",),
    "expression": ("t", "track_id"),
    "scene": ("start", "end"),
    "scene_boundary": ("method",),
    "sound_event": ("start", "end", "label"),
    "multicam_offset": ("offset_to_reference_seconds",),
    "speaking_span": ("start", "end", "voice_track"),
    "visible_span": ("start", "end", "face_track"),
    "candidate_span": ("start", "end"),
    "verdict": ("frames", "statement"),
    "relationship": ("name", "start", "end"),
}


class ClaimProvenanceError(ValueError):
    """A provenance claim that cannot be answered as asked."""


# ── The class of a claim ───────────────────────────────────────────


def class_for_kind(kind: str) -> str:
    """The class a registry kind carries."""
    return CLASS_FOR_KIND.get(str(kind), UNKNOWN_CLASS)


def class_for_type(measurement_type: str) -> str:
    """The class a registry type carries.

    A type the registry does not know has no class: it is unknown, and
    an unknown type is not a measurement.
    """
    try:
        entry = evidence_contract.registry_entry(measurement_type)
    except evidence_contract.EvidenceContractError:
        return UNKNOWN_CLASS
    return class_for_kind(entry["kind"].value)


def is_model_class(claim_class: str) -> bool:
    """Whether a class names a description rather than a reading."""
    return claim_class in MODEL_CLASSES


def declaration(claim_key: str) -> dict[str, Any] | None:
    """The declaration of one claim, with its class resolved.

    None when nothing declares the claim: the class stays unknown.
    """
    declared = CLAIM_DECLARATIONS.get(claim_key)
    if not declared:
        return None
    return {**declared, "class": class_for_type(declared["measurement_type"])}


def claim_class(claim_key: str) -> str:
    """The class of one claim, or ``unknown`` when nobody declared it."""
    declared = declaration(claim_key)
    return declared["class"] if declared else UNKNOWN_CLASS


def tag(claim_key: str, verification: Mapping[str, Any] | None = None,
        **extra: Any) -> dict[str, Any]:
    """The provenance block a claim carries in the world model.

    The class is the one the producer's declaration earns, and a
    description is promoted to a measurement ONLY by a deterministic
    verifier (see :func:`may_file_as_measured`).
    """
    declared = declaration(claim_key)
    if declared is None:
        block = {"class": UNKNOWN_CLASS, "claim": claim_key}
    else:
        block = {
            "class": file_class(declared["class"], verification),
            "claim": claim_key,
            "lane": declared["lane"],
            "producer": declared["producer"],
            "measurement_type": declared["measurement_type"],
        }
    if verification:
        block["verification"] = dict(verification)
    block.update(extra)
    return block


# ── The promotion gate ─────────────────────────────────────────────


def may_file_as_measured(declared_class: str,
                         verification: Mapping[str, Any] | None = None,
                         ) -> bool:
    """Whether a claim may be filed as a measurement.

    A claim an instrument read may. A description may NOT, unless a
    DETERMINISTIC verifier recorded a verification for it: a model's
    confirmation of another model's claim is a verification, and it
    does not make the claim a measurement.
    """
    if declared_class == "measured":
        return True
    if not verification:
        return False
    return str(verification.get("verifier_kind")) in DETERMINISTIC_VERIFIERS


def file_class(declared_class: str,
               verification: Mapping[str, Any] | None = None) -> str:
    """The class a claim is FILED under.

    The producer's class, unless a deterministic verifier promoted it.
    A description with no verifier keeps the class its producer earned.
    """
    if may_file_as_measured(declared_class, verification):
        return "measured"
    return declared_class


def verification_from_verdict(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The verification record a model verdict leaves behind."""
    return {
        "verifier": "library.tools.span_verification",
        "verifier_kind": "model",
        "answer": verdict.get("answer"),
        "statement": verdict.get("statement"),
        "model": verdict.get("model"),
    }


# ── The census ─────────────────────────────────────────────────────


def _identity(claim_key: str, claim: Any, fallback: str) -> str:
    """A stable identity for one claim, so seconds do not multiply it."""
    fields = IDENTITY_FIELDS.get(claim_key)
    if not fields or not isinstance(claim, Mapping):
        return f"{claim_key}:{fallback}"
    parts = []
    for field in fields:
        value = claim.get(field)
        if isinstance(value, (list, tuple)):
            value = tuple(value)
        parts.append(f"{field}={value!r}")
    return f"{claim_key}:" + "|".join(parts)


def _iter_claims(world_model: Mapping[str, Any]) -> Iterator[tuple[str, Any, Any]]:
    """Every claim a world model holds, as (claim key, claim, provenance).

    The walk is by SHAPE, so it reads a world model built before this
    module existed: those claims carry no provenance, and the census
    says so rather than guessing one.
    """
    timeline = world_model.get("timeline") or []
    for entry in timeline:
        if not isinstance(entry, Mapping):
            continue
        provenance = entry.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}

        speech = entry.get("speech") or {}
        for word in speech.get("words") or []:
            yield "words", word, provenance.get("words")
        for speaker in speech.get("speakers") or []:
            yield "speaking_span", speaker, provenance.get("speaking_span")

        for face in entry.get("faces") or []:
            yield "face_box", face, provenance.get("face_box")

        scene = entry.get("scene")
        if scene:
            yield "scene", scene, provenance.get("scene")
        cinematography = entry.get("cinematography")
        if cinematography:
            yield "scene_boundary", cinematography, provenance.get("scene_boundary")

        sound = entry.get("sound") or {}
        for event in sound.get("events") or []:
            yield "sound_event", event, provenance.get("sound_event")

        events = entry.get("events") or {}
        for face_track in events.get("on_screen") or []:
            yield "visible_span", {"face_track": face_track}, \
                provenance.get("visible_span")
        for span in events.get("speaking") or []:
            yield "speaking_span", span, provenance.get("speaking_span")
        for candidate in events.get("hand_near_mouth") or []:
            yield "candidate_span", candidate, provenance.get("candidate_span")
            for verdict in candidate.get("verdicts") or []:
                yield "verdict", verdict, provenance.get("verdict")

        relationships = entry.get("relationships") or {}
        for name, spans in relationships.items():
            for span in spans or []:
                yield "relationship", {**span, "name": name}, \
                    provenance.get("relationship")

    record_provenance = world_model.get("provenance")
    record_provenance = record_provenance if isinstance(
        record_provenance, Mapping) else {}

    entities = world_model.get("entities") or {}
    for kind in ("face_tracks", "voice_tracks"):
        for track in entities.get(kind) or []:
            yield "identity", track, record_provenance.get("identity")

    multicam = world_model.get("multicam")
    if multicam:
        yield "multicam_offset", multicam, record_provenance.get(
            "multicam_offset")


def enumerate_claims(world_model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every distinct claim a world model holds."""
    seen: set[str] = set()
    claims: list[dict[str, Any]] = []
    for index, (claim_key, claim, provenance) in enumerate(
            _iter_claims(world_model)):
        identity = _identity(claim_key, claim, str(index))
        if identity in seen:
            continue
        seen.add(identity)
        declared = declaration(claim_key)
        filed = None
        if isinstance(provenance, Mapping):
            filed = provenance.get("class")
        claims.append({
            "claim": claim_key,
            "identity": identity,
            "declared_class": (declared or {}).get("class", UNKNOWN_CLASS),
            "filed_class": filed,
        })
    return claims


def census(world_model: Mapping[str, Any]) -> dict[str, Any]:
    """What a world model claims, and how it files each claim.

    ``model_descriptions_filed_as_measurements`` is the defect count: a
    claim whose producer was a model and which is filed as a
    measurement - or which carries no class at all, because a claim
    with no class reads as a measurement to every reader.
    """
    claims = enumerate_claims(world_model)
    total = len(claims)
    by_class: dict[str, int] = {}
    for claim in claims:
        by_class[claim["filed_class"] or UNKNOWN_CLASS] = \
            by_class.get(claim["filed_class"] or UNKNOWN_CLASS, 0) + 1
    with_class = sum(1 for claim in claims if claim["filed_class"])
    defect = 0
    for claim in claims:
        if claim["declared_class"] == "measured":
            continue
        filed = claim["filed_class"]
        if filed is None or filed == "measured":
            defect += 1
    return {
        "claims": total,
        "with_a_class": with_class,
        "share_with_a_class": (with_class / total) if total else 0.0,
        "by_filed_class": by_class,
        "model_descriptions_filed_as_measurements": defect,
        "conflicts": _count_conflicts(world_model),
    }


def _count_conflicts(world_model: Mapping[str, Any]) -> int:
    """How many conflicting observations the world model records."""
    total = 0
    for entry in world_model.get("timeline") or []:
        if isinstance(entry, Mapping):
            total += len(entry.get("conflicts") or [])
    total += len(world_model.get("multicam", {}).get("conflicts") or []) \
        if isinstance(world_model.get("multicam"), Mapping) else 0
    return total


def render_report(report: Mapping[str, Any]) -> str:
    """The census as a table a reader can check."""
    lines = [
        f"claims: {report['claims']}",
        (f"claims carrying a class: {report['with_a_class']} "
         f"({report['share_with_a_class']:.1%})"),
        (f"model descriptions filed as measurements: "
         f"{report['model_descriptions_filed_as_measurements']}"),
        f"conflicting observations recorded: {report['conflicts']}",
        "",
        "| filed class | claims |",
        "|---|---:|",
    ]
    for name in sorted(report["by_filed_class"]):
        lines.append(f"| {name} | {report['by_filed_class'][name]} |")
    return "\n".join(lines)


def load_world_model(path: str | Path) -> dict[str, Any]:
    """Read a stored world model."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ClaimProvenanceError(f"{path} is not a world model")
    return dict(document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claim_provenance",
        description="Census the epistemic classes of a stored world model.")
    parser.add_argument("world_model",
                        help="a stored world_model.json")
    args = parser.parse_args(argv)
    model = load_world_model(args.world_model)
    report = census(model)
    print(render_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
