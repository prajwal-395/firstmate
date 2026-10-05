"""Typed user authorization for effect families.

Every effect family (audio, color, graphics, VFX) follows the stabilization
model: measurement detects, recommendation may propose, only explicit editor
instruction authorizes.  The planner may propose an effect, but neither
that proposal nor a measurement grants permission.  Only an explicit request
in a routed timeline note/edit request creates one of these records.

The record travels with the plan and is copied onto every compiled directive.

Three tiers:
  measured     - the measurement that detected the condition
  recommended  - the planner's proposal (in the plan, not the record)
  authorized   - the user's explicit request (the record)

The stabilization module is the first instance of this pattern.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

FAMILIES = ("audio", "color", "graphics", "vfx")

FORMATS = {
    "audio": "audio_effect_authorization/1",
    "color": "color_effect_authorization/1",
    "graphics": "graphics_effect_authorization/1",
    "vfx": "vfx_effect_authorization/1",
}

STABILIZATION_FORMAT = "stabilization_authorization/1"

SCOPE_KINDS = {"whole_video", "shaky_footage", "noisy_footage", "clip", "span"}
SOURCES = {"timeline_note", "edit_request"}

RESTRICTED_EFFECTS = {
    "vfx": {"stabilize"},
    "audio": {"voice_isolation"},
    "color": set(),
    "graphics": set(),
}

MEASUREMENT_SCOPE_KINDS = {
    "vfx": "shaky_footage",
    "audio": "noisy_footage",
}

_ACTION_CLAUSE_BREAK = re.compile(
    r"[.!?;,\n]+|\b(?:and\s+then|then|and)\b", re.IGNORECASE)

_ACTION_PATTERNS = {
    "vfx": re.compile(
        r"^(?:please\s+)?(?:stabili[sz]e\b|"
        r"(?:apply|add|use|enable|turn\s+on|perform)\s+"
        r"(?:the\s+)?stabili[sz]ation\b|"
        r"(?:can|could|would)\s+(?:you|we)\s+(?:please\s+)?"
        r"stabili[sz]e\b|"
        r"(?:i|we)\s+(?:want|need)\s+(?:you\s+to\s+)?"
        r"stabili[sz]e\b|"
        r"(?:i|we)\s+would\s+like\s+(?:you\s+to\s+)?"
        r"stabili[sz]e\b)",
        re.IGNORECASE,
    ),
    "audio": re.compile(
        r"^(?:please\s+)?(?:isolate\b|"
        r"(?:apply|add|use|enable|turn\s+on|perform)\s+"
        r"(?:the\s+)?voice\s+isolation\b|"
        r"(?:can|could|would)\s+(?:you|we)\s+(?:please\s+)?"
        r"isolate\b|"
        r"(?:i|we)\s+(?:want|need)\s+(?:you\s+to\s+)?"
        r"isolate\b|"
        r"(?:i|we)\s+would\s+like\s+(?:you\s+to\s+)?"
        r"isolate\b)",
        re.IGNORECASE,
    ),
}

_NEGATION_PATTERNS = {
    "vfx": re.compile(
        r"\b(?:do\s+not|don't|dont|never|avoid|skip)\s+"
        r"(?:apply\s+|add\s+|use\s+|enable\s+|turn\s+on\s+)?"
        r"(?:the\s+)?stabili[sz](?:e|ation)\b",
        re.IGNORECASE,
    ),
    "audio": re.compile(
        r"\b(?:do\s+not|don't|dont|never|avoid|skip)\s+"
        r"(?:apply\s+|add\s+|use\s+|enable\s+|turn\s+on\s+)?"
        r"(?:the\s+)?voice\s+isolation\b",
        re.IGNORECASE,
    ),
}

_WHOLE_VIDEO = re.compile(
    r"\b(?:whole|entire|all|every)\s+(?:of\s+)?(?:the\s+)?"
    r"(?:video|edit|timeline|all\s+clips?)\b|"
    r"\ball\s+(?:of\s+)?(?:the\s+)?clips?\b|"
    r"\b(?:the|this)\s+video\b(?!\s+clip)",
    re.IGNORECASE,
)
_SHAKY_SCOPE = re.compile(
    r"\b(?:shaky|unstable|unsteady|jittery|handheld)\s+"
    r"(?:footage|clips?|shots?|video)\b|"
    r"\b(?:footage|clips?|shots?)\s+(?:that\s+is\s+)?"
    r"(?:shaky|unstable|unsteady|jittery|handheld)\b",
    re.IGNORECASE,
)
_NOISY_SCOPE = re.compile(
    r"\b(?:noisy|loud|noisy)\s+"
    r"(?:footage|clips?|shots?|video)\b|"
    r"\b(?:footage|clips?|shots?)\s+(?:that\s+is\s+)?"
    r"(?:noisy|loud)\b",
    re.IGNORECASE,
)
_REGION = re.compile(r"^\s*(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)\s*$")
_SPECIFIC_SELECTOR = re.compile(
    r"\b(?:this|that|these|those|attached|selected|current)\s+"
    r"(?:[\w-]+\s+){0,2}(?:clip|shot|span|range)\b",
    re.IGNORECASE,
)


class EffectAuthorizationError(ValueError):
    """An effect authorization request or planned treatment has no valid scope."""


def _note_text(note: dict) -> str:
    return str(note.get("typed") or note.get("text") or "").strip()


def _request_text(text: str) -> str:
    if "\n\n" in text:
        return text.split("\n\n", 1)[1].strip()
    return text.strip()


def _explicit_request(text: str, family: str) -> bool:
    pattern = _ACTION_PATTERNS.get(family)
    if pattern is None:
        return False
    negation = _NEGATION_PATTERNS.get(family)
    if negation and negation.search(text):
        return False
    return any(pattern.match(clause.strip())
               for clause in _ACTION_CLAUSE_BREAK.split(text))


def _edit_request(note: dict, family: str) -> bool:
    for operation in note.get("typed_operations") or []:
        if not isinstance(operation, dict):
            continue
        if family == "vfx":
            if operation.get("owner") == "plan_vfx" or operation.get("op") in (
                    "visual_effect", "subject_effect"):
                return True
        elif family == "audio":
            if operation.get("owner") == "audio_mix" or operation.get("op") in (
                    "voice_isolation", "audio_effect"):
                return True
    return False


def _catalog_mentions(text: str, clip_catalog) -> list[dict]:
    matches = {}
    for clip in clip_catalog or []:
        if not isinstance(clip, dict):
            continue
        aliases = [clip.get("clip_id"), clip.get("filename"),
                   Path(str(clip.get("path") or "")).name]
        for alias in aliases:
            alias = str(alias or "").strip()
            if not alias:
                continue
            pattern = (r"(?<![A-Za-z0-9_])" + re.escape(alias) +
                       r"(?![A-Za-z0-9_])")
            if re.search(pattern, text, re.IGNORECASE):
                key = str(clip.get("clip_id") or alias).casefold()
                matches[key] = clip
                break
    return list(matches.values())


def _parse_region(value: str) -> tuple[float, float] | None:
    match = _REGION.match(str(value or ""))
    if not match:
        return None
    start, end = map(float, match.groups())
    if end <= start:
        return None
    return start, end


def _clip_scope(note: dict, clip_catalog) -> dict | None:
    attached = note.get("attached_to") == "clip"
    clip_name = str(note.get("clip") or "").strip()
    source_file = str(note.get("clip_source_file") or "").strip()
    text = _request_text(_note_text(note))
    mentioned = _catalog_mentions(text, clip_catalog)

    if attached:
        if mentioned:
            attached_matches = [
                c for c in mentioned
                if clip_name and str(c.get("filename") or "").casefold()
                == Path(clip_name).name.casefold()
                or (source_file and c.get("path")
                    and _same_file(str(c["path"]), source_file))
            ]
            if not attached_matches:
                raise EffectAuthorizationError(
                    f"timeline note {note.get('note_id')!r} names a clip "
                    "that conflicts with the clip it is attached to")
            mentioned = attached_matches
        scope = {"kind": "clip", "clip_name": clip_name}
        if note.get("timeline"):
            scope["timeline"] = str(note["timeline"])
        scope["attached_placement"] = True
        if source_file:
            scope["source_file"] = source_file
        frames = _parse_region(note.get("clip_source_frames", ""))
        if frames:
            scope["source_start_frame"], scope["source_end_frame"] = frames
        fps = 0.0
        for clip in clip_catalog or []:
            filename = str(clip.get("filename") or "").casefold()
            clip_path = str(clip.get("path") or "").casefold()
            if ((clip_name and filename == Path(clip_name).name.casefold())
                    or (source_file and clip_path == source_file.casefold())):
                fps = float(clip.get("frame_rate") or clip.get("fps") or 0)
                if clip.get("clip_id"):
                    scope["clip_id"] = str(clip["clip_id"])
                break
        if fps > 0:
            scope["source_frame_rate"] = fps
        return scope if clip_name or source_file or scope.get("clip_id") else None

    if len(mentioned) == 1:
        clip = mentioned[0]
        scope = {"kind": "clip", "clip_name": str(
            clip.get("filename") or clip.get("clip_id") or "")}
        if note.get("timeline"):
            scope["timeline"] = str(note["timeline"])
        if clip.get("clip_id"):
            scope["clip_id"] = str(clip["clip_id"])
        if clip.get("path"):
            scope["source_file"] = str(clip["path"])
        return scope
    if len(mentioned) > 1:
        raise EffectAuthorizationError(
            f"timeline note {note.get('note_id')!r} names more than one "
            "clip; effect authorization scope is ambiguous")
    return None


def _scope_for_note(note: dict, clip_catalog, family: str) -> dict:
    text = _request_text(_note_text(note))
    if _WHOLE_VIDEO.search(text):
        scope = {"kind": "whole_video"}
        if note.get("timeline"):
            scope["timeline"] = str(note["timeline"])
        return scope

    clip = _clip_scope(note, clip_catalog)
    specifically_named = bool(_SPECIFIC_SELECTOR.search(text)
                              or _catalog_mentions(text, clip_catalog or []))
    if clip and specifically_named:
        return clip

    measurement_kind = MEASUREMENT_SCOPE_KINDS.get(family)
    if measurement_kind:
        if family == "vfx" and _SHAKY_SCOPE.search(text):
            scope = {"kind": measurement_kind}
            if note.get("timeline"):
                scope["timeline"] = str(note["timeline"])
            return scope
        if family == "audio" and _NOISY_SCOPE.search(text):
            scope = {"kind": measurement_kind}
            if note.get("timeline"):
                scope["timeline"] = str(note["timeline"])
            return scope

    if clip:
        return clip

    region = _parse_region(note.get("at_region", ""))
    if region and note.get("on_timeline"):
        return {"kind": "span",
                "timeline": str(note["on_timeline"]),
                "start_seconds": region[0], "end_seconds": region[1]}
    raise EffectAuthorizationError(
        f"timeline note {note.get('note_id')!r} explicitly requests "
        f"an effect but does not name a video, clip, or placed timeline "
        f"span")


def authorizations_from_timeline_notes(timeline_notes, clip_catalog=None,
                                       family=None):
    """Build typed records only from explicit user effect requests.

    If family is given, only records for that family are built.
    Otherwise, records for all families are built.
    """
    if not isinstance(timeline_notes, dict):
        return []
    notes = timeline_notes.get("notes") or []
    if not isinstance(notes, list):
        raise EffectAuthorizationError(
            "timeline_notes.notes must be a list")
    families = (family,) if family else FAMILIES
    records = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        instruction = _note_text(note)
        for fam in families:
            if not _explicit_request(_request_text(instruction), fam):
                continue
            note_id = str(note.get("note_id") or "").strip()
            if not note_id:
                raise EffectAuthorizationError(
                    f"an explicit {fam} effect instruction has no note_id")
            records.append({
                "format": FORMATS.get(fam, f"{fam}_effect_authorization/1"),
                "family": fam,
                "authorization_id": note_id,
                "source": "edit_request" if _edit_request(note, fam)
                         else "timeline_note",
                "instruction": instruction,
                "scope": _scope_for_note(note, clip_catalog or [], fam),
            })
    return validate_authorizations(records)


def validate_authorizations(records, family=None):
    """Validate typed records read from a prior plan output.

    If family is given, only records for that family are validated.
    Otherwise, records for all families are validated.
    """
    if records is None:
        return []
    if not isinstance(records, list):
        raise EffectAuthorizationError(
            "effect_authorizations must be a list")
    validated = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise EffectAuthorizationError(
                f"effect authorization {index} must be an object")
        record_family = record.get("family")
        if family and record_family != family:
            continue
        if record_family and record_family not in FAMILIES:
            raise EffectAuthorizationError(
                f"effect authorization {index} has unsupported family "
                f"{record_family!r}")
        expected_format = FORMATS.get(record_family or "", "")
        if record.get("format") not in (expected_format, STABILIZATION_FORMAT):
            raise EffectAuthorizationError(
                f"effect authorization {index} has no supported "
                f"format {expected_format!r}")
        authorization_id = str(record.get("authorization_id") or "").strip()
        instruction = str(record.get("instruction") or "").strip()
        source = record.get("source")
        scope = record.get("scope")
        if not authorization_id or not instruction:
            raise EffectAuthorizationError(
                f"effect authorization {index} needs an id and the "
                "original instruction")
        if source not in SOURCES:
            raise EffectAuthorizationError(
                f"effect authorization {authorization_id!r} has "
                f"unsupported source {source!r}")
        if not _explicit_request(_request_text(instruction),
                                 record_family or ""):
            raise EffectAuthorizationError(
                f"effect authorization {authorization_id!r} does "
                "not contain an explicit user request")
        if not isinstance(scope, dict) or scope.get("kind") not in SCOPE_KINDS:
            raise EffectAuthorizationError(
                f"effect authorization {authorization_id!r} has "
                "no supported scope")
        kind = scope["kind"]
        if kind == "clip" and not any(scope.get(k) for k in (
                "clip_id", "source_file", "clip_name")):
            raise EffectAuthorizationError(
                f"clip-scoped authorization {authorization_id!r} has no "
                "clip identity")
        if kind == "span":
            try:
                start = float(scope["start_seconds"])
                end = float(scope["end_seconds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise EffectAuthorizationError(
                    f"span authorization {authorization_id!r} needs "
                    "start_seconds and end_seconds") from exc
            if end <= start:
                raise EffectAuthorizationError(
                    f"span authorization {authorization_id!r} has an "
                    "empty or reversed range")
        validated.append(record)
    return validated


def _same_file(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right))


def _measured_shaky(semantic_document) -> bool:
    if not isinstance(semantic_document, dict):
        return False
    analysis = semantic_document.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
    assessment = (semantic_document.get("assessment")
                  or analysis.get("assessment") or {})
    values = [assessment.get("camera_stability", "")]
    camera = semantic_document.get("camera") or analysis.get("camera") or []
    if isinstance(camera, list):
        values.extend(row.get("stability", "") for row in camera
                      if isinstance(row, dict))
    return any(re.search(r"\b(?:unstable|shaky|unsteady|jittery)\b",
                         str(value), re.IGNORECASE)
               for value in values)


def _measured_noisy(semantic_document) -> bool:
    if not isinstance(semantic_document, dict):
        return False
    analysis = semantic_document.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
    assessment = (semantic_document.get("assessment")
                  or analysis.get("assessment") or {})
    values = [assessment.get("audio_quality", ""),
              assessment.get("noise_floor", "")]
    audio = semantic_document.get("audio") or analysis.get("audio") or []
    if isinstance(audio, list):
        values.extend(row.get("noise", "") for row in audio
                      if isinstance(row, dict))
    return any(re.search(r"\b(?:noisy|loud|noisy)\b",
                         str(value), re.IGNORECASE)
               for value in values)


def scope_matches_effect(authorization: dict, effect: dict, *, family="",
                         clip_id="", semantic_document=None,
                         source_frame_rate=0.0, timeline_frame_rate=30.0,
                         timeline_name="") -> bool:
    """Whether a planned effect lies inside this authorization."""
    scope = authorization["scope"]
    kind = scope["kind"]
    authorized_timeline = str(scope.get("timeline") or "")
    if (authorized_timeline
            and authorized_timeline != "(the master timeline)"
            and (not timeline_name or authorized_timeline.casefold()
                 != timeline_name.casefold())):
        return False
    if kind == "whole_video":
        return True
    measurement_kind = MEASUREMENT_SCOPE_KINDS.get(family or
                                                   authorization.get("family",
                                                                      ""))
    if measurement_kind and kind == measurement_kind:
        if family == "vfx" or authorization.get("family") == "vfx":
            return _measured_shaky(semantic_document)
        if family == "audio" or authorization.get("family") == "audio":
            return _measured_noisy(semantic_document)
        return False
    if kind == "clip":
        if scope.get("clip_id") and clip_id:
            identity_matches = str(scope["clip_id"]).casefold() == str(
                clip_id).casefold()
        else:
            identity_matches = _same_file(
                str(scope.get("source_file") or ""),
                str(effect.get("source_file") or ""))
        if not identity_matches:
            return False
        start_frame = scope.get("source_start_frame")
        end_frame = scope.get("source_end_frame")
        source_fps = float(scope.get("source_frame_rate")
                           or source_frame_rate or 0)
        if start_frame is not None and end_frame is not None:
            if source_fps <= 0:
                return False
            start = float(start_frame) / source_fps
            end = float(end_frame) / source_fps
            tolerance = 1.0 / source_fps
            return (float(effect.get("source_in", 0)) >= start - tolerance
                    and float(effect.get("source_out", 0)) <= end + tolerance)
        return True
    if kind == "span":
        start = float(scope["start_seconds"])
        end = float(scope["end_seconds"])
        tolerance = 0.5 / float(timeline_frame_rate or 30.0)
        return (float(effect.get("timeline_in", 0)) >= start - tolerance
                and float(effect.get("timeline_out", 0)) <= end + tolerance)
    return False


def refuse_unauthorized_effect(effect: dict, *, family: str = "",
                              authorization_id: str = "", reason: str = ""):
    """Refuse an unauthorized effect by name."""
    effect_name = effect.get("effect_type") or effect.get("sfx_id") or \
        effect.get("label") or "unknown"
    if not reason:
        if not authorization_id:
            reason = "there is no explicit user timeline note or edit request"
        else:
            reason = "no user authorization covers that clip/span"
    if family == "vfx":
        message = (
            f"Stabilization authorization refused: model-authored stabilize "
            f"entry for block {effect.get('target_block_position')!r} "
            f"is unauthorized because {reason}. "
            "Authorize stabilization explicitly in a timeline note or edit "
            "request and name its scope."
        )
    else:
        message = (
            f"Effect authorization refused: {family} effect "
            f"{effect_name!r} is unauthorized because {reason}. "
            f"Authorize the effect explicitly in a timeline note or edit "
            f"request and name its scope."
        )
    print(message, file=sys.stderr)
    raise ValueError(message)


def gate_effect_authorizations(effect_plans: dict, authorizations: list,
                               *, clip_catalog=None, semantic_lookup=None,
                               clip_metadata=None, timeline_name="",
                               timeline_frame_rate=30.0):
    """Gate function that checks all effects in plans against authorizations.

    VFX stabilize effects are REFUSED without explicit authorization (the
    stabilization model).  Other families (audio, color, graphics) are
    record-and-report: an unauthorized effect is reported but not refused,
    because current planners (e.g. audio_mix step 5.02) produce these
    effects from model decisions, not editor instructions.

    effect_plans: dict mapping family -> list of effects
    authorizations: list of authorization records

    Returns: dict mapping family -> list of authorized effects
    """
    authorized_by_family = {}
    for family, effects in effect_plans.items():
        restricted = RESTRICTED_EFFECTS.get(family, set())
        if not restricted:
            authorized_by_family[family] = list(effects)
            continue
        authorized_effects = []
        for effect in effects:
            effect_type = effect.get("effect_type") or effect.get("sfx_id")
            if effect_type not in restricted:
                authorized_effects.append(effect)
                continue
            clip_id = effect.get("clip_id", "")
            semantic_doc = None
            if semantic_lookup and clip_id:
                semantic_doc = semantic_lookup.get(clip_id)
            source_fps = 0.0
            if clip_metadata and clip_id:
                source_fps = float(clip_metadata.get(clip_id, {}).get(
                    "frame_rate") or clip_metadata.get(clip_id, {}).get(
                    "fps") or 0)
            matched = False
            for authorization in authorizations:
                if authorization.get("family", family) != family:
                    continue
                if scope_matches_effect(
                        authorization, effect, family=family,
                        clip_id=clip_id, semantic_document=semantic_doc,
                        source_frame_rate=source_fps,
                        timeline_frame_rate=timeline_frame_rate,
                        timeline_name=timeline_name):
                    matched = True
                    break
            if not matched:
                if family == "vfx":
                    refuse_unauthorized_effect(effect, family=family)
                else:
                    effect_name = (effect.get("effect_type")
                                   or effect.get("sfx_id")
                                   or effect.get("label") or "unknown")
                    print(
                        f"  Note: {family} effect {effect_name!r} has no "
                        f"explicit user authorization; applying anyway "
                        f"(record-and-report for non-stabilization families).",
                        file=sys.stderr,
                    )
            authorized_effects.append(effect)
        authorized_by_family[family] = authorized_effects
    return authorized_by_family
