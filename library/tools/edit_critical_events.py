"""Edit-critical event types and their fidelity requirements.

EDIT-001 defines the event vocabulary: the ten event types from punchlist
section 14.1 that receive high-priority analysis when relevant. EDIT-002
states what "measured well enough to cut on" means for each type: a
temporal tolerance, the evidence that must exist, and how the event is
verified. Anything below its fidelity requirement is reported as
uncertain, never silently treated as a cut point.

Every requirement names a READER: the measurement or query that can
verify an event of that type. A type with no verifiable evidence cannot
ground a candidate edit (see edit_candidates.py).
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping

# EDIT-001 - the ten edit-critical event types (punchlist 14.1), in the
# order the punchlist names them. The id is the identity; the label is
# the punchlist's own wording.
EDIT_CRITICAL_EVENT_TYPES: tuple[tuple[str, str], ...] = (
    ("speech_start_end", "Speech starts and ends."),
    ("word_phrase_boundary", "Word and phrase boundaries."),
    ("speaker_change", "Speaker changes."),
    ("action_state_change", "Actions and state changes."),
    ("object_interaction", "Object interactions."),
    ("scene_transition", "Scene transitions."),
    ("onscreen_text_change", "Important on-screen text changes."),
    ("audio_event", "Relevant audio events."),
    ("technical_defect", "Technical defects."),
    ("retake_repeat", "Retakes and repeated content."),
)

_EVENT_IDS = frozenset(event_id for event_id, _ in EDIT_CRITICAL_EVENT_TYPES)


# EDIT-002 - fidelity requirements. Each entry states:
# - temporal_tolerance_seconds: how precisely the event's boundary must
#   be located before a cut may sit on it. Values are mechanical (a
#   frame multiple at common delivery rates), not taste.
# - required_evidence: measurement or signal names that must be present.
#   Names follow evidence_contract measurement types and the signal
#   kinds edit_point_planner reads where one exists.
# - verification: how a claimed event is checked (exact key reads).
# - abstain_when: the named condition under which the event is reported
#   as uncertain rather than used.
_FIDELITY_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "speech_start_end": {
        "temporal_tolerance_seconds": 0.04,
        "required_evidence": ("speech.word",),
        "verification": "word rows with source_start/source_end bracketing "
                        "the boundary; no cut inside a word span",
        "abstain_when": "no word evidence covering the boundary",
    },
    "word_phrase_boundary": {
        "temporal_tolerance_seconds": 0.12,
        "required_evidence": ("speech.word",),
        "verification": "inter-word pause located from adjacent word "
                        "source_start/source_end; cut at pause center",
        "abstain_when": "words abut with no measurable pause",
    },
    "speaker_change": {
        "temporal_tolerance_seconds": 0.12,
        "required_evidence": ("person.voice_turn", "speech.word"),
        "verification": "voice-turn edge corroborated by attributed word "
                        "evidence on both sides",
        "abstain_when": "turn edge has no word evidence on either side",
    },
    "action_state_change": {
        "temporal_tolerance_seconds": 0.20,
        "required_evidence": ("event_span",),
        "verification": "verified action event span only; unverified "
                        "candidates never verify (evidence_query rule)",
        "abstain_when": "only unverified candidates cover the change",
    },
    "object_interaction": {
        "temporal_tolerance_seconds": 0.20,
        "required_evidence": ("object.detection", "object.visibility"),
        "verification": "detection/visibility rows bracketing the contact "
                        "moment on the same clock",
        "abstain_when": "no detection rows within tolerance of the moment",
    },
    "scene_transition": {
        "temporal_tolerance_seconds": 0.04,
        "required_evidence": ("video.scene_boundary",),
        "verification": "measured scene-boundary tick; one frame tolerance",
        "abstain_when": "no scene-boundary signal analyzed for the clock",
    },
    "onscreen_text_change": {
        "temporal_tolerance_seconds": 0.50,
        "required_evidence": ("ocr.text_span",),
        "verification": "OCR text spans differing across the change with "
                        "readable_text present",
        "abstain_when": "no OCR spans cover the change",
    },
    "audio_event": {
        "temporal_tolerance_seconds": 0.10,
        "required_evidence": ("audio.sound_event",),
        "verification": "measured sound-event interval overlapping the "
                        "claimed moment",
        "abstain_when": "no sound-event measurement covers the moment",
    },
    "technical_defect": {
        "temporal_tolerance_seconds": 0.60,
        "required_evidence": ("picture.soft_range", "camera.stability"),
        "verification": "soft-picture run or instability verdict overlapping "
                        "the interval (shot_reject combines them)",
        "abstain_when": "neither picture nor stability measured the interval",
    },
    "retake_repeat": {
        "temporal_tolerance_seconds": 0.50,
        "required_evidence": ("speech.word", "audio.sound_event"),
        "verification": "repeated word n-grams across takes plus take "
                        "identity; never filename similarity alone",
        "abstain_when": "takes share no measured word evidence",
    },
}


def event_types() -> tuple[str, ...]:
    """The EDIT-001 event-type ids, in punchlist order."""
    return tuple(event_id for event_id, _ in EDIT_CRITICAL_EVENT_TYPES)


def event_label(event_type: str) -> str:
    """The punchlist's own wording for an event type.

    Raises KeyError naming the unknown type: a rename fails loudly
    (AGENTS.md 10.1), it never returns a default.
    """
    for event_id, label in EDIT_CRITICAL_EVENT_TYPES:
        if event_id == event_type:
            return label
    raise KeyError(f"unknown edit-critical event type: {event_type!r}")


def fidelity_requirement(event_type: str) -> dict[str, Any]:
    """The EDIT-002 fidelity requirement for an event type.

    Raises KeyError on an unknown type. The returned mapping is a copy;
    the registry itself is not mutable through it.
    """
    try:
        requirement = _FIDELITY_REQUIREMENTS[event_type]
    except KeyError:
        raise KeyError(
            f"no fidelity requirement for {event_type!r}") from None
    return {
        "temporal_tolerance_seconds":
            requirement["temporal_tolerance_seconds"],
        "required_evidence": tuple(requirement["required_evidence"]),
        "verification": requirement["verification"],
        "abstain_when": requirement["abstain_when"],
    }


def verify_event_fidelity(event: Mapping[str, Any],
                          available_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Check one claimed event against its fidelity requirement.

    `event` carries `event_type`, `boundary_seconds` (or `start_seconds`/
    `end_seconds` for a span) and `evidence` (a mapping of evidence name
    to the rows supporting this event). `available_evidence` maps
    evidence name to a bool (or a non-empty row collection) saying the
    measurement exists for the relevant clock.

    Returns `{status, event_type, reasons[]}` where status is `verified`
    or `uncertain`. Uncertain names the missing evidence or the
    tolerance breach; it never invents a boundary.
    """
    reasons: list[str] = []
    event_type = event.get("event_type")
    if event_type not in _EVENT_IDS:
        return {"status": "uncertain", "event_type": event_type,
                "reasons": [f"not an edit-critical event type: "
                            f"{event_type!r}"]}
    requirement = _FIDELITY_REQUIREMENTS[event_type]
    provided = event.get("evidence") or {}
    for name in requirement["required_evidence"]:
        rows = provided.get(name, available_evidence.get(name))
        if isinstance(rows, bool):
            present = rows
        else:
            present = bool(rows)
        if not present:
            reasons.append(f"missing required evidence: {name}")
    tolerance = float(requirement["temporal_tolerance_seconds"])
    claimed_precision = event.get("boundary_precision_seconds")
    if claimed_precision is not None:
        try:
            precision = float(claimed_precision)
        except (TypeError, ValueError):
            reasons.append("boundary_precision_seconds is not a number")
            precision = None
        if precision is not None and precision > tolerance:
            reasons.append(
                f"boundary precision {precision}s exceeds tolerance "
                f"{tolerance}s")
    if reasons:
        return {"status": "uncertain", "event_type": event_type,
                "reasons": reasons}
    return {"status": "verified", "event_type": event_type, "reasons": []}
