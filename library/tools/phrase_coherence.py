"""Coherent speech selection: phrases, pauses, and question/answer units.

EDIT-004 uses speech boundaries to identify natural edit points: cuts sit
at phrase boundaries (pause centers), never inside words. EDIT-005 refuses
isolated words when a coherent phrase or answer is required. EDIT-006
identifies complete question/answer and narrative units so a cut keeps the
question with its answer.

Word rows follow the spine contract: `word`, `source_start`,
`source_end` read directly (AGENTS.md 6). The pause and gap bounds are
parameters, never hidden constants, so editorial preferences stay
configurable (EDIT-012) rather than hardcoded.

The 12-second question/answer gap bound is the bound PR 2603 measured
for complete dialogue exchanges; it is cited, not re-derived.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

# PR 2603's complete-exchange gap bound, cited by name. A question and an
# answer further apart than this are two units, not one.
QA_MAX_GAP_SECONDS = 12.0

_SENTENCE_ENDS = frozenset({".", "?", "!"})


def _word_text(row: Mapping[str, Any]) -> str:
    return str(row["word"])


def phrase_spans(words: Sequence[Mapping[str, Any]],
                 max_pause_seconds: float = 0.6) -> list[dict[str, Any]]:
    """Group word rows into coherent phrase spans.

    A new phrase starts after a pause longer than `max_pause_seconds` or
    after a sentence-final word. Each span carries `start_seconds`,
    `end_seconds`, `word_count` and the covered `word_indexes`. An empty
    input yields no spans, never one spanning nothing.
    """
    ordered = sorted(words, key=lambda row: float(row["source_start"]))
    spans: list[dict[str, Any]] = []
    current: list[int] = []
    for index, row in enumerate(ordered):
        if current:
            previous = ordered[current[-1]]
            gap = float(row["source_start"]) - float(previous["source_end"])
            previous_text = _word_text(previous).rstrip()
            ends_sentence = previous_text[-1:] in _SENTENCE_ENDS
            if gap > max_pause_seconds or ends_sentence:
                spans.append(_close_span(ordered, current))
                current = []
        current.append(index)
    if current:
        spans.append(_close_span(ordered, current))
    return spans


def _close_span(ordered: Sequence[Mapping[str, Any]],
                indexes: list[int]) -> dict[str, Any]:
    first = ordered[indexes[0]]
    last = ordered[indexes[-1]]
    return {"start_seconds": float(first["source_start"]),
            "end_seconds": float(last["source_end"]),
            "word_count": len(indexes),
            "word_indexes": list(indexes)}


def pause_center_cuts(words: Sequence[Mapping[str, Any]],
                      min_pause_seconds: float = 0.15) -> list[dict[str, Any]]:
    """Natural edit points at inter-word pause centers (EDIT-004).

    A cut is proposed at the center of every inter-word gap of at least
    `min_pause_seconds`, with the gap's `evidence` (the two bracketing
    words). Gaps below the bound yield no cut: abutting words are not a
    natural edit point, and the module says so by emitting nothing.
    """
    ordered = sorted(words, key=lambda row: float(row["source_start"]))
    cuts: list[dict[str, Any]] = []
    for left, right in zip(ordered, ordered[1:]):
        gap_start = float(left["source_end"])
        gap_end = float(right["source_start"])
        if gap_end - gap_start >= min_pause_seconds:
            cuts.append({
                "position_seconds": (gap_start + gap_end) / 2.0,
                "gap_seconds": gap_end - gap_start,
                "evidence": [
                    {"word": _word_text(left), "role": "before",
                     "source_start": float(left["source_start"]),
                     "source_end": float(left["source_end"])},
                    {"word": _word_text(right), "role": "after",
                     "source_start": float(right["source_start"]),
                     "source_end": float(right["source_end"])},
                ],
            })
    return cuts


def guard_coherent_span(start_seconds: float, end_seconds: float,
                        words: Sequence[Mapping[str, Any]],
                        max_pause_seconds: float = 0.6,
                        require: str = "phrase") -> dict[str, Any]:
    """Refuse a span that violates EDIT-005 (EDIT-005 guard).

    `require` is `phrase` (the span must cover at least one full phrase)
    or `answer` (at least one full phrase plus its question turn when
    `turns` context is supplied via `question_covered`). Checks, in
    order: empty/inverted span; mid-word edge (an edge landing strictly
    inside a word span); isolated word (fewer than two words covered
    when a phrase is required, or no full phrase covered). Returns
    `{status: ok|refused, reason, covered_words, phrases_covered}`.
    """
    if not end_seconds > start_seconds:
        return {"status": "refused", "reason": "empty_span",
                "covered_words": 0, "phrases_covered": 0}
    ordered = sorted(words, key=lambda row: float(row["source_start"]))
    for row in ordered:
        word_start = float(row["source_start"])
        word_end = float(row["source_end"])
        if word_start < start_seconds < word_end:
            return {"status": "refused", "reason": "mid_word_start",
                    "covered_words": 0, "phrases_covered": 0}
        if word_start < end_seconds < word_end:
            return {"status": "refused", "reason": "mid_word_end",
                    "covered_words": 0, "phrases_covered": 0}
    covered = [row for row in ordered
               if float(row["source_start"]) >= start_seconds
               and float(row["source_end"]) <= end_seconds]
    phrases = phrase_spans(covered, max_pause_seconds=max_pause_seconds)
    full = [span for span in phrases
            if span["start_seconds"] >= start_seconds
            and span["end_seconds"] <= end_seconds]
    if require == "phrase" and (len(covered) < 2 or not full):
        return {"status": "refused", "reason": "isolated_word",
                "covered_words": len(covered),
                "phrases_covered": len(full)}
    return {"status": "ok", "reason": "",
            "covered_words": len(covered), "phrases_covered": len(full)}


def question_answer_units(
        turns: Sequence[Mapping[str, Any]],
        words: Sequence[Mapping[str, Any]],
        max_gap_seconds: float = QA_MAX_GAP_SECONDS) -> list[dict[str, Any]]:
    """Complete question/answer units from speaker turns (EDIT-006).

    `turns` carry `speaker`, `start_seconds`, `end_seconds` (voice-turn
    evidence). A unit is a turn ending in a question followed within
    `max_gap_seconds` by a turn from a DIFFERENT speaker, with word
    evidence inside both turns. Units carry both spans, the speakers,
    the gap, and the word evidence ids when rows carry `evidence_id`.
    A question with no answering turn yields no unit, never a half unit.
    """
    ordered_turns = sorted(turns, key=lambda turn: float(turn["start_seconds"]))
    ordered_words = sorted(words, key=lambda row: float(row["source_start"])
                           ) if words else []
    units: list[dict[str, Any]] = []
    for position, question in enumerate(ordered_turns):
        question_text = " ".join(
            _word_text(row) for row in ordered_words
            if float(question["start_seconds"]) <= float(row["source_start"])
            and float(row["source_end"]) <= float(question["end_seconds"]))
        if not question_text.rstrip().endswith("?"):
            continue
        for answer in ordered_turns[position + 1:]:
            if answer.get("speaker") == question.get("speaker"):
                continue
            gap = (float(answer["start_seconds"])
                   - float(question["end_seconds"]))
            if gap < 0:
                continue
            if gap > max_gap_seconds:
                break
            answer_words = [
                row for row in ordered_words
                if float(answer["start_seconds"]) <= float(row["source_start"])
                and float(row["source_end"]) <= float(answer["end_seconds"])]
            question_words = [
                row for row in ordered_words
                if float(question["start_seconds"]) <= float(row["source_start"])
                and float(row["source_end"]) <= float(question["end_seconds"])]
            if not question_words or not answer_words:
                continue
            units.append({
                "question": {"speaker": question.get("speaker"),
                             "start_seconds": float(question["start_seconds"]),
                             "end_seconds": float(question["end_seconds"])},
                "answer": {"speaker": answer.get("speaker"),
                           "start_seconds": float(answer["start_seconds"]),
                           "end_seconds": float(answer["end_seconds"])},
                "gap_seconds": gap,
                "question_words": len(question_words),
                "answer_words": len(answer_words),
            })
            break
    return units
