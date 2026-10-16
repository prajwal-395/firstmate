"""Combined dialogue + visible-text queries (GEO-027).

Joins what was SAID (transcript words with source intervals) against
what was SHOWN (OCR tracks with appearance intervals and normalized
boxes) so a question like "when does he say X while Y is on screen"
resolves to one interval with both halves cited.

Both inputs are RECOGNIZED strings with provenance, never model
interpretations (GEO-026): transcript rows carry their source clip and
word times; OCR tracks carry their artifact path, track id and sample
ticks. The join reports overlap; it never invents a paraphrase.
"""
from __future__ import annotations


def _normalize(value: str) -> str:
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"\w+", folded, flags=re.UNICODE))


def transcript_hits(transcript_segments: list, phrase: str) -> list[dict]:
    """Segments whose text contains every token of `phrase` (order-free)."""
    tokens = _normalize(phrase).split()
    hits = []
    for seg in transcript_segments or []:
        hay = _normalize(seg.get("text", ""))
        words = set(hay.split())
        if tokens and all(tok in words or any(
                tok in w or w in tok for w in words if len(w) >= 4)
                for tok in tokens):
            hits.append(seg)
    return hits


def ocr_hits(ocr_tracks: list, phrase: str) -> list[dict]:
    """Tracks whose recognized text contains the phrase tokens."""
    tokens = _normalize(phrase).split()
    hits = []
    for track in ocr_tracks or []:
        hay = _normalize(track.get("text", ""))
        words = set(hay.split())
        if tokens and all(tok in words or any(
                tok in w or w in tok for w in words if len(w) >= 4)
                for tok in tokens):
            hits.append(track)
    return hits


def _span(row: dict) -> tuple[float, float] | None:
    for start_key, end_key in (("start_seconds", "end_seconds"),
                               ("start", "end")):
        if start_key in row and end_key in row:
            try:
                return float(row[start_key]), float(row[end_key])
            except (TypeError, ValueError):
                return None
    appearances = row.get("appearances") or row.get(
        "appearance_intervals") or []
    if appearances:
        first, last = appearances[0], appearances[-1]
        try:
            if isinstance(first, dict):
                return (float(first["start_seconds"]),
                        float(last["end_seconds"]))
            return float(first[0]), float(last[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
    return None


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def combined_query(transcript_segments: list, ocr_tracks: list,
                   said: str, shown: str,
                   require_overlap: bool = True) -> list[dict]:
    """Join dialogue hits against on-screen-text hits.

    Returns one row per (speech, text-track) pair, ordered by overlap
    seconds then by speech start. Each row cites both provenances:
    the transcript segment id and the OCR track id + artifact ref when
    present. With `require_overlap=False`, pairs from the same clip
    without a shared second are still reported with overlap 0.
    """
    speech_rows = transcript_hits(transcript_segments, said)
    text_rows = ocr_hits(ocr_tracks, shown)
    joined = []
    for speech in speech_rows:
        speech_span = _span(speech)
        for track in text_rows:
            text_span = _span(track)
            same_clip = (speech.get("clip_id") == track.get("clip_id")
                         or "clip_id" not in speech
                         or "clip_id" not in track)
            if speech_span is None or text_span is None:
                seconds = 0.0
                if require_overlap:
                    continue
            else:
                seconds = overlap(speech_span, text_span)
                if require_overlap and seconds <= 0:
                    continue
            if not same_clip and require_overlap:
                continue
            joined.append({
                "said": speech.get("text", ""),
                "shown": track.get("text", ""),
                "clip_id": speech.get("clip_id", track.get("clip_id")),
                "speech_span": list(speech_span) if speech_span else None,
                "text_span": list(text_span) if text_span else None,
                "overlap_seconds": seconds,
                "speech_provenance": {
                    "segment_id": speech.get("segment_id",
                                             speech.get("id")),
                    "clip_id": speech.get("clip_id"),
                },
                "text_provenance": {
                    "track_id": track.get("track_id"),
                    "artifact_ref": track.get("artifact_ref"),
                    "bbox_normalized": track.get("bbox_normalized",
                                                track.get("bbox")),
                },
            })
    joined.sort(key=lambda row: (-row["overlap_seconds"],
                                 (row["speech_span"] or [0])[0]))
    return joined
