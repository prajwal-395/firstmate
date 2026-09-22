"""One clamp for every forced-alignment word list. Spelled once.

The asymmetry this closes
-------------------------
`step_1_04_temporal_index` clamped wav2vec2's word boundaries and the
reels transcript path (`library/tools/timeline_transcript.py`) did not.
Same aligner, same failure mode, two different answers: nine stretched
words on the field-test transcript - 9 of 8,509, all backchannels -
each one bridging seconds of silence, each one widening its row's
envelope and shifting its midpoint into a clip gap, where the binding
reads it as unbound. That is also what forced `MAX_WORD_SECONDS` in
`reel_conformance_verifier` and what made caption coverage misreport.
Two copies of a clamp is how the asymmetry happens again, so there is
one implementation and both paths call it.

What it does, and what it refuses to
------------------------------------
Three passes over one segment's words, in order: a degenerate or
sub-frame span is given `MIN_WORD_SECONDS`, a span over
`MAX_WORD_SECONDS` is cut back to the median span of the words that are
not over it, and an overlap with the next word is trimmed. Words are
mutated in place and returned, so every key the caller carried (`timed`,
`alignment_score`, ...) survives; membership never changes, so
`transcript_fit.row_fit` - a count of timed words against text words -
reads identically before and after.

Only the END moves, never the start
------------------------------------
A stretched word is the aligner bridging TRAILING silence: the onset is
placed and the tail runs on. Whether the start can be trusted was
established without reading the word's own end (which would be
circular, the envelope being derived from it): on the field-test rows
the gap between the stretched word's start and the PREVIOUS word's end
- an independent measurement - sits in rhythm with the row's other
inter-word gaps (tens of milliseconds), and the start sits on a real
clip while the end sits in a gap. Had the speech been at the tail, the
gap before it would be the whole silence. So the clamp cuts the end
back to `start + median` and leaves the start where the aligner put it.

What this is not
----------------
`hybrid_transcription.clamp_heard_words` looks like this and is a
different stage: the TRANSCRIBER's words, clamped to build alignment
windows, before any aligner has run. It stays where it is, with its own
fallback semantics, and says so on its own docstring.
"""

from __future__ import annotations

MIN_WORD_SECONDS = 0.020
"""Roughly one frame at 30fps. A word shorter than this is a boundary."""

MAX_WORD_SECONDS = 2.0
"""An alignment failure threshold, not a claim about speech.

A single word held longer than this spans a silence, not a syllable.
The same number as `heard_speech.LONG_WORD_SECONDS`, by construction:
the same physical claim, measured on both instruments. It is restated
here rather than imported so this module - called from step and tool
contexts with heavy import graphs - depends on nothing but the
standard library.
"""

MEDIAN_FALLBACK_SECONDS = 0.3
"""The clamp length when no word on the segment is ordinary.

A single-word segment carrying one stretched word has no median to
read; 0.3s is the historical fallback `step_1_04` used there, kept so
both paths answer identically.
"""


def sanitize_word_boundaries(
    words: list,
    *,
    min_seconds: float = MIN_WORD_SECONDS,
    max_seconds: float = MAX_WORD_SECONDS,
    median_fallback: float = MEDIAN_FALLBACK_SECONDS,
) -> list:
    """Fix forced-alignment artifacts on one segment's words, in place.

    Must be called AFTER any onset snapping (which may shift starts).
    """
    if not words:
        return words

    # Pass 1: fix negative/zero durations
    for w in words:
        if w["end"] <= w["start"]:
            w["end"] = round(w["start"] + min_seconds, 3)
        elif w["end"] - w["start"] < min_seconds:
            w["end"] = round(w["start"] + min_seconds, 3)

    # Pass 2: clamp unreasonably long words
    durations = [
        w["end"] - w["start"] for w in words
        if min_seconds <= w["end"] - w["start"] <= max_seconds
    ]
    if durations:
        median_dur = sorted(durations)[len(durations) // 2]
    else:
        median_dur = median_fallback

    for w in words:
        if w["end"] - w["start"] > max_seconds:
            w["end"] = round(w["start"] + median_dur, 3)

    # Pass 3: clamp overlapping word boundaries
    for i in range(len(words) - 1):
        if words[i]["end"] > words[i + 1]["start"]:
            words[i]["end"] = words[i + 1]["start"]

    return words
