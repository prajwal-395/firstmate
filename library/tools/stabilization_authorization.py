"""Typed user authorization for Resolve stabilization.

The planner may propose a ``stabilize`` effect, but neither that proposal
nor a stability measurement grants permission.  Only an explicit request
in a routed timeline note/edit request creates one of these records.  The
record travels with the plan and is copied onto every compiled directive.

This module is the first instance of the generalized effect authorization
pattern in ``effect_authorization.py``.  It delegates validation and scope
matching to that module while keeping the ``stabilization_authorization/1``
format for backward compatibility.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from library.tools.effect_authorization import (
    STABILIZATION_FORMAT as FORMAT,
    EffectAuthorizationError as StabilizationAuthorizationError,
    authorizations_from_timeline_notes as _effect_authorizations_from_notes,
    scope_matches_effect as _scope_matches_effect,
    validate_authorizations as _validate_authorizations,
)

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


def _note_text(note: dict) -> str:
    return str(note.get("typed") or note.get("text") or "").strip()


def _request_text(text: str) -> str:
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
            "family": "vfx",
            "authorization_id": note_id,
            "source": "edit_request" if _edit_request(note)
                     else "timeline_note",
            "instruction": instruction,
            "scope": _scope_for_note(note, clip_catalog or []),
        })
    return validate_authorizations(records)


def validate_authorizations(records) -> list[dict]:
    """Validate typed records read from a prior plan output."""
    return _validate_authorizations(records, family="vfx")


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
    return _scope_matches_effect(
        authorization, clip, family="vfx", clip_id=clip_id,
        semantic_document=semantic_document,
        source_frame_rate=source_frame_rate,
        timeline_frame_rate=timeline_frame_rate,
        timeline_name=timeline_name)
