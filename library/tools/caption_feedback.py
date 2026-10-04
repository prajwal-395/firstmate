"""Speech-grounded handling for timeline notes about caption wording.

The deterministic subtitle planner remains the owner of grouping and
timing. This module prepares a compact transcript/caption view for the
feedback-only model call, then records only corrections supported by the
same timed speech. A note whose requested words are missing from that
speech is reported for an upstream transcript reindex.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:['’][a-z0-9]+)?", re.IGNORECASE)
_QUOTED = re.compile(r'"([^"\n]{2,})"|“([^”\n]{2,})”|‘([^’\n]{2,})’')
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "can",
    "caption", "captions", "do", "for", "from", "here", "how", "i",
    "im", "in", "is", "it", "like", "needs", "of", "on", "or", "our",
    "says", "should", "so", "that", "the", "this", "to", "was", "were",
    "what", "where", "with", "year",
})


class CaptionFeedbackError(ValueError):
    """A model answer cannot be tied to a note and its spoken words."""


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold().replace("’", "'")
            for match in _TOKEN.finditer(text or "")]


def _contains_run(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[index:index + len(needle)] == needle
               for index in range(len(haystack) - len(needle) + 1))


def _quoted_phrases(text: str) -> list[list[str]]:
    return [tokens for match in _QUOTED.findall(text)
            if (tokens := _tokens(next(part for part in match if part)))]


def _speech_tokens(text: str) -> list[str]:
    from library.tools.caption_reading import apply_caption_reading_text

    return _tokens(apply_caption_reading_text(text))


def _block_evidence(block: dict, plan_entries: list[dict]) -> dict | None:
    words = block["word_timestamps"]
    if not words:
        return None
    timed_words = [{
        "word": word["word"],
        "source_start": word["source_start"],
        "source_end": word["source_end"],
    } for word in words]
    text = " ".join(word["word"] for word in timed_words)
    position = block["position"]
    entries = [entry for entry in plan_entries
               if entry.get("spine_block_position") == position]
    return {
        "clip_id": block["clip_id"],
        "source_start": block["source_start"],
        "source_end": block["source_end"],
        "spine_block_position": position,
        "spoken_text": text,
        "word_timestamps": timed_words,
        "caption_entries": [{
            "id": entry.get("entry_id", entry.get("id", "")),
            "text": entry["text"],
            "timeline_start": entry.get("timeline_start"),
            "timeline_end": entry.get("timeline_end"),
        } for entry in entries],
    }


def _note_matches(note: dict, evidence: list[dict]) -> list[dict]:
    note_text = note["typed"]
    quoted = _quoted_phrases(note_text)
    meaningful = {token for token in _tokens(note_text)
                  if token not in _STOP and len(token) > 1}
    ranked = []
    for row in evidence:
        speech = _speech_tokens(row["spoken_text"])
        captions = [_tokens(entry["text"])
                    for entry in row["caption_entries"]]
        score = len(meaningful.intersection(speech))
        score += 2 * sum(_contains_run(speech, phrase)
                         or any(_contains_run(caption, phrase)
                                for caption in captions)
                         for phrase in quoted)
        if any(token.isdigit() and token in speech for token in meaningful):
            score += 5
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0],
                                  pair[1]["source_start"],
                                  pair[1]["clip_id"]))
    return [row for _score, row in ranked[:3]]


def build_context(timeline_notes: dict, subtitle_plan: dict,
                  audio_spine: dict) -> dict:
    """Select the timed speech and current cards relevant to each note."""
    notes = timeline_notes.get("notes") or []
    plan_entries = subtitle_plan["subtitle_entries"]
    evidence = [row for block in audio_spine["structure"]
                if (row := _block_evidence(block, plan_entries)) is not None]
    return {
        "notes": [{
            "note_id": note["note_id"],
            "typed": note["typed"],
            "timeline": note.get("timeline", ""),
            "at_timecode": note.get("at_timecode", ""),
            "typed_operations": note.get("typed_operations", []),
            "matches": _note_matches(note, evidence),
        } for note in notes],
    }


def _valid_action(value) -> str:
    allowed = {"caption_fix", "already_correct",
               "upstream_transcript_needed", "outside_scope"}
    if value not in allowed:
        raise CaptionFeedbackError(
            f"caption feedback action {value!r} is not one of "
            f"{', '.join(sorted(allowed))}")
    return value


def _record_caption_fix(project_folder: str, note: dict, correction: dict):
    from library.tools import edit_spec

    spec = {
        "format": edit_spec.FORMAT,
        "request": note["typed"],
        "source_note_id": note["note_id"],
        "clauses": [{
            "id": "caption-feedback-1",
            "text": correction["reason"],
            "op": "caption_fix",
            "op_source": "model",
            "status": "resolved",
            "anchor": {"kind": "words",
                       "phrase": correction["anchor_phrase"]},
            "anchor_source": "model",
            "values": {"replacement": {
                "value": correction["replacement"],
                "unit": "caption text",
                "stated_by": "requester",
            }},
        }],
    }
    return edit_spec.record_spec(project_folder, spec)


def _supported(match: dict, phrase: str) -> bool:
    return _contains_run(_speech_tokens(match["spoken_text"]),
                         _tokens(phrase))


def resolve_feedback(data: dict) -> dict:
    """Validate feedback, record supported fixes, and update this plan."""
    from library.tools import captain_edits, nothing_to_decide

    context = data.get("caption_feedback_context") or {"notes": []}
    notes = context["notes"]
    plan = data["subtitle_plan"]
    if not notes:
        reason = data.get(nothing_to_decide.KEY) or "no caption feedback notes"
        return {
            "subtitle_plan": plan,
            "caption_feedback_report": {
                "status": "not_requested", "reason": reason, "notes": [],
            },
        }

    responses = data.get("caption_feedback")
    if not isinstance(responses, list):
        raise CaptionFeedbackError("caption_feedback must be a list")
    by_id = {}
    for response in responses:
        note_id = response.get("note_id")
        if note_id in by_id:
            raise CaptionFeedbackError(
                f"caption feedback answered note {note_id!r} more than once")
        by_id[note_id] = response
    note_ids = {note["note_id"] for note in notes}
    if set(by_id) != note_ids:
        missing = sorted(note_ids - set(by_id))
        extra = sorted(set(by_id) - note_ids)
        raise CaptionFeedbackError(
            f"caption feedback note coverage differs: missing={missing}, "
            f"unknown={extra}")

    project_folder = data.get("project_folder", "")
    report_rows = []
    prepared_fixes = []
    for note in notes:
        response = by_id[note["note_id"]]
        action = _valid_action(response.get("action"))
        reason = response.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CaptionFeedbackError(
                f"caption feedback for {note['note_id']} needs a reason")
        row = {"note_id": note["note_id"], "action": action,
               "reason": reason.strip()}
        if action == "already_correct":
            requested = response.get("requested_phrase")
            if not isinstance(requested, str) or not requested.strip():
                raise CaptionFeedbackError(
                    f"already_correct for {note['note_id']} needs a "
                    "requested_phrase to check")
            requested = requested.strip()
            matching_speech = any(_supported(match, requested)
                                  for match in note["matches"])
            matching_caption = any(
                _contains_run(_tokens(entry["text"]), _tokens(requested))
                for match in note["matches"]
                for entry in match["caption_entries"])
            if not matching_speech or not matching_caption:
                raise CaptionFeedbackError(
                    f"already_correct for {note['note_id']} is not present "
                    "in both the timed speech and current caption")
            report_rows.append({**row, "outcome": "already_correct",
                                "requested_phrase": requested})
            continue
        if action == "outside_scope":
            report_rows.append({**row, "outcome": "outside_scope"})
            continue
        if action == "upstream_transcript_needed":
            requested = response.get("requested_phrase")
            if not isinstance(requested, str) or not requested.strip():
                raise CaptionFeedbackError(
                    f"upstream_transcript_needed for {note['note_id']} "
                    "needs a requested_phrase to check")
            requested = requested.strip()
            if any(_supported(match, requested)
                   for match in note["matches"]):
                raise CaptionFeedbackError(
                    f"upstream_transcript_needed for {note['note_id']} names "
                    "a phrase already present in the timed speech")
            report_rows.append({**row,
                                "outcome": "upstream_transcript_needed",
                                "requested_phrase": requested})
            continue

        anchor = response.get("anchor_phrase")
        replacement = response.get("replacement")
        if not isinstance(anchor, str) or not anchor.strip():
            raise CaptionFeedbackError(
                f"caption fix for {note['note_id']} has no anchor_phrase")
        if not isinstance(replacement, str) or not replacement.strip():
            raise CaptionFeedbackError(
                f"caption fix for {note['note_id']} has no replacement")
        anchor, replacement = anchor.strip(), replacement.strip()
        matches = note["matches"]
        supported_matches = [match for match in matches
                             if _supported(match, anchor)
                             and _supported(match, replacement)]
        if not supported_matches:
            report_rows.append({
                **row,
                "outcome": "upstream_transcript_needed",
                "anchor_phrase": anchor,
                "replacement": replacement,
                "detail": ("the proposed anchor or replacement is absent "
                           "from the note's timed speech; reindex the source "
                           "transcript before changing caption wording"),
            })
            continue
        plan_matches = [entry for match in supported_matches
                        for entry in match["caption_entries"]
                        if _contains_run(_tokens(entry["text"]),
                                         _tokens(anchor))]
        if not plan_matches:
            already_drawn = any(
                _contains_run(_tokens(entry["text"]), _tokens(replacement))
                for match in supported_matches
                for entry in match["caption_entries"])
            report_rows.append({
                **row,
                "outcome": "already_correct" if already_drawn else "stale",
                "anchor_phrase": anchor,
                "replacement": replacement,
                "detail": ("the requested wording is already in the caption "
                           "plan" if already_drawn else
                           "the anchor is absent from the current caption plan"),
            })
            continue
        if not project_folder:
            raise CaptionFeedbackError(
                "a supported caption fix cannot be recorded without "
                "project_folder")
        correction = {"kind": "caption_fix",
                      "anchor_phrase": anchor,
                      "replacement": replacement,
                      "reason": reason.strip()}
        prepared_fixes.append((note, correction))
        report_rows.append({**row, "outcome": "prepared",
                            "anchor_phrase": anchor,
                            "replacement": replacement})

    applied = []
    stale = []
    if prepared_fixes:
        if not project_folder:
            raise CaptionFeedbackError(
                "a supported caption fix cannot be recorded without "
                "project_folder")
        for note, correction in prepared_fixes:
            _record_caption_fix(project_folder, note, correction)
        newly_recorded = [correction
                          for _note, correction in prepared_fixes]
        plan["subtitle_entries"], applied, stale = \
            captain_edits.apply_caption_fixes(
                plan["subtitle_entries"], newly_recorded)
        plan["total_subtitles"] = len(plan["subtitle_entries"])
        for report in report_rows:
            if report["outcome"] != "prepared":
                continue
            match = next((item for item in applied
                          if item["anchor_phrase"] == report["anchor_phrase"]
                          and item["replacement"] == report["replacement"]),
                         None)
            if match:
                report["outcome"] = "applied"
                report["cards_updated"] = match["cards_touched"]
            elif any(item["anchor_phrase"] == report["anchor_phrase"]
                     for item in stale):
                report["outcome"] = "stale"

    return {
        "subtitle_plan": plan,
        "caption_feedback_report": {
            "status": "reviewed",
            "notes": report_rows,
        },
    }
