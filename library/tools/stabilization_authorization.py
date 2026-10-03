"""Typed user authorization for Resolve stabilization.

The planner may propose a ``stabilize`` effect, but neither that proposal
nor a stability measurement grants permission.  Only an explicit request
in a routed timeline note/edit request creates one of these records.  The
record travels with the plan and is copied onto every compiled directive.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

FORMAT = "stabilization_authorization/1"
SCOPE_KINDS = {"whole_video", "shaky_footage", "clip", "span"}
SOURCES = {"timeline_note", "edit_request"}

_STABILIZE_ACTION = re.compile(
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
)
_ACTION_CLAUSE_BREAK = re.compile(
    r"[.!?;,\n]+|\b(?:and\s+then|then|and)\b", re.IGNORECASE)
_STABILIZE_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|skip)\s+"
    r"(?:apply\s+|add\s+|use\s+|enable\s+|turn\s+on\s+)?"
    r"(?:the\s+)?stabili[sz](?:e|ation)\b",
    re.IGNORECASE,
)
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
_REGION = re.compile(r"^\s*(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)\s*$")
_SPECIFIC_SELECTOR = re.compile(
    r"\b(?:this|that|these|those|attached|selected|current)\s+"
    r"(?:[\w-]+\s+){0,2}(?:clip|shot|span|range)\b",
    re.IGNORECASE,
)


class StabilizationAuthorizationError(ValueError):
    """A stabilization request or planned treatment has no valid scope."""


def _note_text(note: dict) -> str:
    return str(note.get("typed") or note.get("text") or "").strip()


def _request_text(text: str) -> str:
    """Drop the marker title; only the user's note body grants permission."""
    if "\n\n" in text:
        return text.split("\n\n", 1)[1].strip()
    return text.strip()


def _explicit_request(text: str) -> bool:
    if _STABILIZE_NEGATION.search(text):
        return False
    return any(_STABILIZE_ACTION.match(clause.strip())
               for clause in _ACTION_CLAUSE_BREAK.split(text))


def _edit_request(note: dict) -> bool:
    for operation in note.get("typed_operations") or []:
        if not isinstance(operation, dict):
            continue
        if operation.get("owner") == "plan_vfx" or operation.get("op") in (
                "visual_effect", "subject_effect"):
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
                raise StabilizationAuthorizationError(
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
        raise StabilizationAuthorizationError(
            f"timeline note {note.get('note_id')!r} names more than one "
            "clip; stabilization scope is ambiguous")
    return None


def _scope_for_note(note: dict, clip_catalog) -> dict:
    text = _request_text(_note_text(note))
    if _WHOLE_VIDEO.search(text):
        scope = {"kind": "whole_video"}
        if note.get("timeline"):
            scope["timeline"] = str(note["timeline"])
        return scope

    # An attached item, explicit filename or demonstrative ("this clip")
    # is narrower than nearby descriptive words such as "shaky".
    clip = _clip_scope(note, clip_catalog)
    specifically_named = bool(_SPECIFIC_SELECTOR.search(text)
                              or _catalog_mentions(text, clip_catalog or []))
    if clip and specifically_named:
        return clip
    if _SHAKY_SCOPE.search(text):
        scope = {"kind": "shaky_footage"}
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
    raise StabilizationAuthorizationError(
        f"timeline note {note.get('note_id')!r} explicitly requests "
        "stabilization but does not name a video, clip, or placed timeline "
        "span")


def authorizations_from_timeline_notes(timeline_notes, clip_catalog=None):
    """Build typed records only from explicit user stabilization requests."""
    if not isinstance(timeline_notes, dict):
        return []
    notes = timeline_notes.get("notes") or []
    if not isinstance(notes, list):
        raise StabilizationAuthorizationError(
            "timeline_notes.notes must be a list")
    records = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        instruction = _note_text(note)
        if not _explicit_request(_request_text(instruction)):
            continue
        note_id = str(note.get("note_id") or "").strip()
        if not note_id:
            raise StabilizationAuthorizationError(
                "an explicit stabilization instruction has no note_id")
        records.append({
            "format": FORMAT,
            "authorization_id": note_id,
            "source": "edit_request" if _edit_request(note)
                     else "timeline_note",
            "instruction": instruction,
            "scope": _scope_for_note(note, clip_catalog or []),
        })
    return validate_authorizations(records)


def validate_authorizations(records) -> list[dict]:
    """Validate typed records read from a prior plan output."""
    if records is None:
        return []
    if not isinstance(records, list):
        raise StabilizationAuthorizationError(
            "stabilization_authorizations must be a list")
    validated = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise StabilizationAuthorizationError(
                f"stabilization authorization {index} must be an object")
        if record.get("format") != FORMAT:
            raise StabilizationAuthorizationError(
                f"stabilization authorization {index} has no supported "
                f"format {FORMAT!r}")
        authorization_id = str(record.get("authorization_id") or "").strip()
        instruction = str(record.get("instruction") or "").strip()
        source = record.get("source")
        scope = record.get("scope")
        if not authorization_id or not instruction:
            raise StabilizationAuthorizationError(
                f"stabilization authorization {index} needs an id and the "
                "original instruction")
        if source not in SOURCES:
            raise StabilizationAuthorizationError(
                f"stabilization authorization {authorization_id!r} has "
                f"unsupported source {source!r}")
        if not _explicit_request(_request_text(instruction)):
            raise StabilizationAuthorizationError(
                f"stabilization authorization {authorization_id!r} does "
                "not contain an explicit user request")
        if not isinstance(scope, dict) or scope.get("kind") not in SCOPE_KINDS:
            raise StabilizationAuthorizationError(
                f"stabilization authorization {authorization_id!r} has "
                "no supported scope")
        kind = scope["kind"]
        if kind == "clip" and not any(scope.get(k) for k in (
                "clip_id", "source_file", "clip_name")):
            raise StabilizationAuthorizationError(
                f"clip-scoped authorization {authorization_id!r} has no "
                "clip identity")
        if kind == "span":
            try:
                start = float(scope["start_seconds"])
                end = float(scope["end_seconds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise StabilizationAuthorizationError(
                    f"span authorization {authorization_id!r} needs "
                    "start_seconds and end_seconds") from exc
            if end <= start:
                raise StabilizationAuthorizationError(
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


def scope_matches_clip(authorization: dict, clip: dict, *, clip_id="",
                       semantic_document=None, source_frame_rate=0.0,
                       timeline_frame_rate=30.0,
                       timeline_name="") -> bool:
    """Whether a placed manifest item lies wholly inside this authorization."""
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
    if kind == "shaky_footage":
        return _measured_shaky(semantic_document)
    if kind == "clip":
        if scope.get("clip_id") and clip_id:
            identity_matches = str(scope["clip_id"]).casefold() == str(
                clip_id).casefold()
        else:
            identity_matches = _same_file(
                str(scope.get("source_file") or ""),
                str(clip.get("source_file") or ""))
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
            return (float(clip.get("source_in", 0)) >= start - tolerance
                    and float(clip.get("source_out", 0)) <= end + tolerance)
        return True
    if kind == "span":
        start = float(scope["start_seconds"])
        end = float(scope["end_seconds"])
        tolerance = 0.5 / float(timeline_frame_rate or 30.0)
        return (float(clip.get("timeline_in", 0)) >= start - tolerance
                and float(clip.get("timeline_out", 0)) <= end + tolerance)
    return False
