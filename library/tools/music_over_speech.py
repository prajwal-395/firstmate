"""A named span where the music is deliberately allowed over the speech.

The speech-above-bed gate (`render_qa.measure_speech_above_bed`) fails a
render whose bed sits under the voice by less than the separation the plan
declared.  Sometimes the captain WANTS the bed over the voice - a comic
beat, a deliberate swell - and the gate must not fail that span.

The override is an explicit instruction in a timeline note or edit
request naming the span, the same path every other override in this
pipeline uses (`effect_authorization.py`): the note is parsed into a typed
record with a scope, the record travels with the plan on the assembly
manifest, and the consumer matches it against its own targets.  A record
matching no window is reported on the result, never silently dropped, and
an overridden window carries its override's id in the receipt beside its
measurement.

The record is a CHECK override, not an effect authorization: it does not
permit a mix decision, it exempts one named span from a gate.  The mix
still delivers the plan's separation everywhere, including the overridden
span - the captain's "music over the speech here" is delivered by the
plan's own behaviour, and this record only stops the gate failing it.
"""

from __future__ import annotations

import re

FORMAT = "music_over_speech_override/1"

SOURCES = {"timeline_note", "edit_request"}


class MusicOverSpeechError(ValueError):
    """A music-over-speech override request has no valid scope."""


_ACTION_CLAUSE_BREAK = re.compile(
    r"[.!?;,\n]+|\b(?:and\s+then|then|and)\b", re.IGNORECASE)

# The captain's own words: "if a user wants to play music of the speech in
# a certain area -- maybe for comedic affect or something".  The pattern
# names the two things that must be present - the bed and the voice - and
# the relation between them, so a note about something else entirely does
# not become an override.  An optional verb sits between the bed and the
# relation because the captain's phrasing is "let the music SIT over the
# speech".
_MUSIC_OVER_SPEECH = re.compile(
    r"^(?:please\s+)?"
    r"(?:(?:let|allow|have|keep|put|turn\s+up|raise|crank\s+up)\s+)?"
    r"(?:(?:the\s+)?(?:music|bed|track|score|instrumental)\s+"
    r"(?:(?:sit|be|play|stay|swell)\s+)?"
    r"(?:over|above|on\s+top\s+of|on)\s+"
    r"(?:the\s+)?(?:speech|voice|dialogue|vocals?|talking)\b"
    r"|\bmusic\s+over\s+(?:the\s+)?speech\b"
    r"|\b(?:comic|comedic)\s+effect\b)",
    re.IGNORECASE,
)

_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|stop)\s+"
    r"(?:let\s+|allow\s+|have\s+|keep\s+|put\s+)?"
    r"(?:(?:the\s+)?(?:music|bed|track|score)\s+)?over\b",
    re.IGNORECASE,
)

_WHOLE_VIDEO = re.compile(
    r"\b(?:whole|entire|all|every)\s+(?:of\s+)?(?:the\s+)?"
    r"(?:video|edit|timeline|all\s+clips?)\b|"
    r"\ball\s+(?:of\s+)?(?:the\s+)?clips?\b|"
    r"\b(?:the|this)\s+video\b(?!\s+clip)",
    re.IGNORECASE,
)

_REGION = re.compile(r"^\s*(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)\s*$")


def _note_text(note: dict) -> str:
    return str(note.get("typed") or note.get("text") or "").strip()


def _request_text(text: str) -> str:
    if "\n\n" in text:
        return text.split("\n\n", 1)[1].strip()
    return text.strip()


def _explicit_request(text: str) -> bool:
    if _NEGATION.search(text):
        return False
    return any(_MUSIC_OVER_SPEECH.match(clause.strip())
               for clause in _ACTION_CLAUSE_BREAK.split(text))


def _edit_request(note: dict) -> bool:
    for operation in note.get("typed_operations") or []:
        if not isinstance(operation, dict):
            continue
        if operation.get("owner") == "audio_mix" or operation.get("op") in (
                "music_over_speech", "audio_effect"):
            return True
    return False


def _parse_region(value: str):
    match = _REGION.match(str(value or ""))
    if not match:
        return None
    start, end = map(float, match.groups())
    if end <= start:
        return None
    return start, end


def _scope_for_note(note: dict) -> dict:
    text = _request_text(_note_text(note))
    if _WHOLE_VIDEO.search(text):
        scope = {"kind": "whole_video"}
        if note.get("timeline"):
            scope["timeline"] = str(note["timeline"])
        return scope
    region = _parse_region(note.get("at_region", ""))
    if region and note.get("on_timeline"):
        return {"kind": "span",
                "timeline": str(note["on_timeline"]),
                "start_seconds": region[0], "end_seconds": region[1]}
    raise MusicOverSpeechError(
        f"timeline note {note.get('note_id')!r} explicitly wants the music "
        "over the speech but names neither a whole video nor a placed "
        "timeline span")


def overrides_from_timeline_notes(timeline_notes) -> list:
    """Build typed records only from explicit music-over-speech requests.

    A note that does not explicitly ask for the bed over the voice builds
    nothing - the same stabilization model every effect family follows,
    where a measurement or a proposal grants no permission.
    """
    if not isinstance(timeline_notes, dict):
        return []
    notes = timeline_notes.get("notes") or []
    if not isinstance(notes, list):
        raise MusicOverSpeechError("timeline_notes.notes must be a list")
    records = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        instruction = _note_text(note)
        if not _explicit_request(_request_text(instruction)):
            continue
        note_id = str(note.get("note_id") or "").strip()
        if not note_id:
            raise MusicOverSpeechError(
                "an explicit music-over-speech instruction has no note_id")
        records.append({
            "format": FORMAT,
            "override_id": note_id,
            "source": "edit_request" if _edit_request(note)
                     else "timeline_note",
            "instruction": instruction,
            "scope": _scope_for_note(note),
        })
    return validate_overrides(records)


def validate_overrides(records) -> list:
    """Validate typed records read from a prior plan output."""
    if records is None:
        return []
    if not isinstance(records, list):
        raise MusicOverSpeechError(
            "music_over_speech_overrides must be a list")
    validated = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise MusicOverSpeechError(
                f"music-over-speech override {index} must be an object")
        if record.get("format") != FORMAT:
            raise MusicOverSpeechError(
                f"music-over-speech override {index} has no supported "
                f"format {FORMAT!r}")
        override_id = str(record.get("override_id") or "").strip()
        instruction = str(record.get("instruction") or "").strip()
        source = record.get("source")
        scope = record.get("scope")
        if not override_id or not instruction:
            raise MusicOverSpeechError(
                f"music-over-speech override {index} needs an id and the "
                "original instruction")
        if source not in SOURCES:
            raise MusicOverSpeechError(
                f"music-over-speech override {override_id!r} has unsupported "
                f"source {source!r}")
        if not _explicit_request(_request_text(instruction)):
            raise MusicOverSpeechError(
                f"music-over-speech override {override_id!r} does not contain "
                "an explicit user request")
        if not isinstance(scope, dict) or scope.get("kind") not in (
                "whole_video", "span"):
            raise MusicOverSpeechError(
                f"music-over-speech override {override_id!r} has no supported "
                "scope")
        if scope["kind"] == "span":
            try:
                start = float(scope["start_seconds"])
                end = float(scope["end_seconds"])
            except (KeyError, TypeError, ValueError) as exc:
                raise MusicOverSpeechError(
                    f"span override {override_id!r} needs start_seconds and "
                    "end_seconds") from exc
            if end <= start:
                raise MusicOverSpeechError(
                    f"span override {override_id!r} has an empty or reversed "
                    "range")
        validated.append(record)
    return validated


def span_is_overridden(overrides, start: float, end: float) -> str:
    """The id of the override covering [start, end], or "" when none does.

    A window is overridden when an override's span OVERLAPS it at all: the
    captain named a stretch they want the bed over the voice, and a gate
    failing a window that even partly sits inside that stretch would fail
    something they meant to exempt.  `whole_video` overrides every window.
    """
    for record in overrides or []:
        if not isinstance(record, dict):
            continue
        scope = record.get("scope") or {}
        kind = scope.get("kind")
        if kind == "whole_video":
            return str(record.get("override_id") or "")
        if kind == "span":
            try:
                s = float(scope["start_seconds"])
                e = float(scope["end_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            if start < e and end > s:
                return str(record.get("override_id") or "")
    return ""
