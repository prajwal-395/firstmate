"""Turn measured word prosody into reviewable emphasis edit candidates.

This adapter reads the existing transcript word spans and step 1.05
``word_prosody`` rows. It does not infer words or alter transcript timing.
Its output is a ranked set of acoustic candidates for an editor to inspect,
not an emotional label or an automatic cut instruction.
"""
from __future__ import annotations


def emphasis_edit_candidates(timed_words: list, prosody_profile: dict,
                             *, minimum_emphasis: float = 0.5) -> list[dict]:
    """Return high-emphasis transcript words with their original source spans.

    Certainty is a bounded evidence-coverage value: the number of available
    acoustic terms (pitch, intensity, duration) divided by the three terms
    the measurement formula can use. It is deliberately not a probability.
    Candidates are ranked by measured emphasis, with stronger multi-signal
    support as the tie-breaker.
    """
    if not isinstance(timed_words, list) or not isinstance(prosody_profile, dict):
        return []
    prosody = prosody_profile.get("prosody") or {}
    rows = prosody.get("word_prosody") or []
    if not isinstance(rows, list):
        return []

    # Match repeated spellings in spoken order, just as the emphasis view
    # does. The profile's timestamps remain the source of evidence; the
    # transcript's timing remains the source of the returned edit span.
    by_word: dict[str, list[dict]] = {}
    for row in rows:
        if isinstance(row, dict):
            key = _normalise(row.get("word"))
            if key:
                by_word.setdefault(key, []).append(row)
    for matches in by_word.values():
        matches.sort(key=lambda row: float(row.get("start", 0)))

    seen: dict[str, int] = {}
    candidates = []
    for word in timed_words:
        if not isinstance(word, dict):
            continue
        key = _normalise(word.get("word"))
        index = seen.get(key, 0)
        seen[key] = index + 1
        matches = by_word.get(key, [])
        if not key or index >= len(matches):
            continue
        measured = matches[index]
        emphasis = measured.get("emphasis")
        if isinstance(emphasis, bool) or not isinstance(emphasis, (int, float)):
            continue
        if emphasis < minimum_emphasis:
            continue
        try:
            start = float(word["source_start"])
            end = float(word["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        terms = measured.get("terms", 0)
        terms = terms if isinstance(terms, int) and not isinstance(terms, bool) else 0
        candidates.append({
            "word": str(word["word"]),
            "source_start": start,
            "source_end": end,
            "emphasis": float(emphasis),
            "certainty": round(max(0, min(terms, 3)) / 3, 3),
            "evidence": {
                "pitch_semitones": measured.get("f0_rel_semitones"),
                "intensity_db": measured.get("loud_rel_db"),
                "duration_ratio": measured.get("dur_ratio"),
                "measured_terms": terms,
            },
            "interpretation": "acoustic emphasis candidate; editorial review required",
        })
    return sorted(candidates, key=lambda item: (-item["emphasis"], -item["certainty"], item["source_start"]))


def _normalise(value) -> str:
    return str(value or "").strip(" \t\n\r…—–!?,.;:'\"()[]{}").lower()
