#!/usr/bin/env python3
"""
Step 2.02 Bridge: Enrich Speech Sequence with Word Timestamps

Takes the speech_sequence (hook + body_sequence) and enriches each passage
with word-level timestamps from the temporal index (step 1.04 output).

Each passage must have start/end timestamps from the temporal index.
We extract all words in that time range directly — no text matching.

Classification: Deterministic / Data Transformation
Idempotent: Yes

Input:  {
    "speech_sequence": <from step 2.02>,
    "temporal_index_dir": <path to temporal_index/>
}
Output: {
    "speech_sequence": <enriched with word_timestamps, start_time, end_time>
}
"""
import json
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../tools")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from library.tools.project_layout import Area, ProjectLayout

# No engagement score is attached here any more.  `engagement_scorer` read
# `prosody_data.get("energy_rms", 0)` off step 1.05's whole output - a key
# nothing emits, on an object that is one record per RUN - so nine of
# project 001's eleven passages scored an identical 49.  The scorers are
# withdrawn, with the reason for each, in
# library/tools/passage_engagement.py.  This import was also wrapped in a
# try/except that set the name to None, so a scorer that failed to import
# left no trace at all: absence must be stated, not swallowed.


def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


class PassageAlignmentError(ValueError):
    """A passage could not be resolved to real word-level timings."""


# A passage whose aligned words share less than this fraction of its text
# is not the passage the LLM meant - refuse it rather than cut to it.
MIN_TEXT_OVERLAP = 0.5

# Two ranges on the same clip overlapping by more than this fraction of
# their union are the same moment.  The hook is allowed to tease a longer
# body passage (a small IoU); it is not allowed to BE one.
MAX_HOOK_BODY_IOU = 0.8

# Full-clip text alignment may anchor to a repeated phrase far from where
# the LLM said the passage was. Beyond this drift, re-align inside the
# hint window instead of trusting the distant match. This is a secondary
# aid only: a mis-anchor of less than MAX_HINT_DRIFT still looks perfectly
# valid to it, which is how the shipped export ended up replaying 0.741s
# of block 4 at the head of block 5. The structural check below is what
# actually stands between a mis-anchor and the timeline.
MAX_HINT_DRIFT = 2.0
HINT_WINDOW_SLACK = 1.0

# How far back of a candidate anchor the two-pointer starts, so a passage
# whose own first word was not transcribed can still align.  This is the
# literal that used to sit inline in _align_words_to_text.
ANCHOR_BACKUP_SECONDS = 2.0

# Two body passages cut from one clip must not claim overlapping source
# audio - the overlap is laid down twice, back to back. Sub-millisecond
# slop is float noise, not a repeat.
SOURCE_OVERLAP_EPSILON = 1e-3


def normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def collect_words_in_range(
    start: float,
    end: float,
    regions: list,
    passage_text: str = "",
    tolerance: float = 0.05,
) -> dict:
    """Extract word timestamps from temporal index, aligned to passage text.

    Strategy: collect ALL words from the clip, then use text alignment
    to find the matching sequence. The LLM's [start, end] range is used
    only as a proximity hint to disambiguate when the same text appears
    multiple times in the clip.

    Args:
        start: Approximate source start time (seconds) — used as hint
        end: Approximate source end time (seconds) — used as hint
        regions: speech_regions from the temporal index
        passage_text: The LLM's curated passage text to align against
        tolerance: Not used directly; kept for API compatibility

    Returns dict with: word_timestamps, start_time, end_time
    """
    # Collect ALL words from the clip (not just the time range)
    all_words = []
    for region in regions:
        for w in region.get("words", []):
            all_words.append({
                "word": w["word"],
                "start": w["start"],
                "end": w["end"],
            })

    alignment = {}

    if not all_words:
        return {"word_timestamps": [], "start_time": None,
                "end_time": None, "alignment": alignment}

    if passage_text:
        # Text alignment does the real work — finds the matching
        # subsequence regardless of where it falls in the clip.  Every
        # occurrence of the passage's first word is tried as an anchor;
        # see _align_words_to_text for why the time hint is only the
        # tie-break.
        words = _align_words_to_text(
            all_words, passage_text,
            hint_start=start, hint_end=end,
            notes=alignment,
        )
    else:
        # No passage text — fall back to time-range filter
        words = [
            w for w in all_words
            if w["end"] > start - tolerance
            and w["start"] < end + tolerance
        ]

    if not words:
        return {"word_timestamps": [], "start_time": None,
                "end_time": None, "alignment": alignment}

    # Emit the spine contract's word shape (source_start/source_end), not
    # the temporal index's internal start/end. Every consumer downstream
    # reads source_* because that is what the enclosing block uses.
    return {
        "word_timestamps": [
            {
                "word": w["word"],
                "source_start": w["start"],
                "source_end": w["end"],
            }
            for w in words
        ],
        "start_time": words[0]["start"],
        "end_time": words[-1]["end"],
        # What the anchor search measured and did.  Recorded on every
        # alignment, reported by enrich_passage; never a threshold.
        "alignment": alignment,
    }

def _hint_drift(enrichment: dict, start: float, end: float):
    """How far the aligned span sits from the LLM's hint, in seconds."""
    if enrichment["start_time"] is None:
        return None
    return max(
        abs(enrichment["start_time"] - start),
        abs(enrichment["end_time"] - end),
    )


def _clip_regions_to_window(regions: list, start: float, end: float) -> list:
    """Regions restricted to a window around the hint, with slack."""
    lo = start - HINT_WINDOW_SLACK
    hi = end + HINT_WINDOW_SLACK
    windowed = []
    for region in regions:
        words = [
            w for w in region.get("words", [])
            if w["end"] > lo and w["start"] < hi
        ]
        if words:
            windowed.append({**region, "words": words})
    return windowed


def _clip_regions_after(regions: list, floor: float) -> list:
    """Regions restricted to words starting at or after `floor`."""
    trimmed = []
    for region in regions:
        words = [
            w for w in region.get("words", [])
            if w["start"] >= floor - SOURCE_OVERLAP_EPSILON
        ]
        if words:
            trimmed.append({**region, "words": words})
    return trimmed


def _overlaps(enrichment: dict, claimed: tuple) -> bool:
    """Does an aligned span claim source audio a previous passage took?"""
    if enrichment["start_time"] is None or claimed is None:
        return False
    prior_start, prior_end = claimed
    return (
        enrichment["start_time"] < prior_end - SOURCE_OVERLAP_EPSILON
        and enrichment["end_time"] > prior_start + SOURCE_OVERLAP_EPSILON
    )


def _leading_gap(words: list):
    """Seconds of silence between a passage's first and second word.

    The mis-anchor's whole signature: the anchor is a word that really is
    in the passage, and then nothing that follows it belongs until the
    two-pointer picks the passage up again seconds later.
    """
    if len(words) < 2:
        return None
    return words[1]["start"] - words[0]["end"]


def _largest_gap(words: list):
    """The longest silence anywhere between consecutive aligned words."""
    if len(words) < 2:
        return None
    return max(words[i + 1]["start"] - words[i]["end"]
               for i in range(len(words) - 1))


def _voiced_fraction(words: list):
    """Share of the aligned span that the aligned words themselves cover.

    A measurement, never a threshold.  Reported so a reader can see what
    an alignment is made of; the anchor search below is what decides.
    """
    if len(words) < 2:
        return None
    span = words[-1]["end"] - words[0]["start"]
    if span <= 0:
        return None
    return sum(w["end"] - w["start"] for w in words) / span


def _anchor_backed_up(candidates: list, idx: int) -> int:
    """Back an anchor up by TIME, not by a fixed number of candidates.

    Backing up lets the two-pointer start before the anchor, so a passage
    whose own first word was never transcribed can still align.  Backing
    up by index instead would cross a long silence into an unrelated run
    of speech.
    """
    backup_time = candidates[idx]["start"] - ANCHOR_BACKUP_SECONDS
    while idx > 0 and candidates[idx - 1]["start"] >= backup_time:
        idx -= 1
    return idx


# Contraction expansion table: candidate spellings WhisperX produces that
# the passage text writes out in two words.
_EXPANDED_CONTRACTIONS = {
    "its": ("it", "is"),
    "im": ("i", "am"), "ive": ("i", "have"),
    "dont": ("do", "not"), "didnt": ("did", "not"),
    "cant": ("can", "not"), "wont": ("will", "not"),
    "isnt": ("is", "not"), "wasnt": ("was", "not"),
    "thats": ("that", "is"), "whats": ("what", "is"),
    "hes": ("he", "is"), "shes": ("she", "is"),
    "theyre": ("they", "are"), "were": ("we", "are"),
    "youre": ("you", "are"), "youve": ("you", "have"),
    "theres": ("there", "is"), "heres": ("here", "is"),
    "lets": ("let", "us"), "wouldnt": ("would", "not"),
    "couldnt": ("could", "not"), "shouldnt": ("should", "not"),
    "havent": ("have", "not"), "hasnt": ("has", "not"),
    "ill": ("i", "will"), "itll": ("it", "will"),
    "well": ("we", "will"), "theyll": ("they", "will"),
}


def _align_from(candidates: list, passage_words: list, c_idx: int) -> list:
    """Two-pointer alignment of `passage_words` starting at `c_idx`.

    Handles words the LLM included but WhisperX did not transcribe,
    contractions, and filler words in the candidate list.
    """
    aligned = []
    p_idx = 0

    while p_idx < len(passage_words) and c_idx < len(candidates):
        p_word = passage_words[p_idx]
        c_word = normalize(candidates[c_idx]["word"])

        if not c_word:
            c_idx += 1
            continue

        # Direct match
        if c_word == p_word:
            aligned.append(candidates[c_idx])
            p_idx += 1
            c_idx += 1
            continue

        # Contraction match: "it's" in candidate vs "it is" in passage
        if c_word in _EXPANDED_CONTRACTIONS and p_idx + 1 < len(passage_words):
            exp = _EXPANDED_CONTRACTIONS[c_word]
            if (p_word == exp[0] and
                    passage_words[p_idx + 1] == exp[1]):
                aligned.append(candidates[c_idx])
                p_idx += 2  # consumed 2 passage words
                c_idx += 1
                continue

        # Lookahead: maybe the current passage word wasn't transcribed
        # (e.g. "2026" missing from WhisperX). Try matching the NEXT
        # passage word against the current candidate.
        lookahead = min(3, len(passage_words) - p_idx)
        matched = False
        for skip in range(1, lookahead):
            if normalize(passage_words[p_idx + skip]) == c_word:
                p_idx += skip  # skip untranscribed passage words
                aligned.append(candidates[c_idx])
                p_idx += 1
                c_idx += 1
                matched = True
                break

        if matched:
            continue

        # No match at all — skip this candidate
        c_idx += 1

    return aligned


def _align_words_to_text(
    candidates: list,
    passage_text: str,
    hint_start: float = None,
    hint_end: float = None,
    notes: dict = None,
) -> list:
    """Align candidate word timestamps to the passage text.

    EVERY occurrence of the passage's first word is tried as an anchor,
    and the alignments are ranked - most passage words aligned first,
    then the SHORTEST span, then the smallest leading gap, and only then
    proximity to the LLM's time hint.  There is no threshold anywhere in
    that ordering: a silence survives exactly when no equally complete
    anchor removes it, which is the difference between a mis-anchor and
    a real pause.

    Anchoring on the occurrence NEAREST the hint is what put 6.901s of
    unintended, uncaptioned audio into 001's finished video: the passage
    opened on "i", the clip says "i" thirty-odd times, and the nearest
    one was seven seconds before the one the passage actually starts on.
    The correct anchor was FURTHER from the hint, so hint proximity can
    only ever be the tie-break it is here.  `source_start` is a lookup
    hint by contract (AGENTS.md section 6), not a timing.

    `notes`, when given, is filled with what the search measured and
    did: `leading_gap`, `largest_gap`, `voiced_fraction`,
    `anchors_considered`, `hint_nearest_start`,
    `hint_nearest_leading_gap`, `chosen_start` and `event`
    (`reanchored` | `held` | `ok`).  Nothing here decides on those
    numbers; they are recorded so a reader can see the alignment.

    Returns the aligned subset of candidates.
    """
    passage_words = normalize(passage_text).split()
    if not passage_words:
        return candidates

    first_passage_word = passage_words[0]
    occurrences = [
        i for i, c in enumerate(candidates)
        if normalize(c["word"]) == first_passage_word
    ]

    if hint_start is None or not occurrences:
        aligned = _align_from(candidates, passage_words, 0)
        _record_alignment(notes, aligned, len(occurrences), None)
        return aligned

    # Each occurrence is tried TWICE: from the occurrence itself, and
    # from ANCHOR_BACKUP_SECONDS before it.  The back-up alone is not
    # enough - it makes an anchor's result depend on the nearest earlier
    # occurrence, which on 001 collapsed the correct anchor for body_3
    # onto the "I" of "I think" 0.58s before it.
    start_indices = set(occurrences)
    start_indices.update(_anchor_backed_up(candidates, i) for i in occurrences)

    attempts = [
        _align_from(candidates, passage_words, idx)
        for idx in sorted(start_indices)
    ]
    attempts = [a for a in attempts if a]
    if not attempts:
        _record_alignment(notes, [], len(occurrences), None)
        return []

    def rank(aligned):
        # Completeness first, then the SPAN the alignment drags in, then
        # the leading gap, then the hint.
        #
        # Span before leading gap because bounding the head alone moves
        # the defect rather than removing it: an anchor whose second word
        # follows in 0.02s can still bridge five seconds of unrelated
        # speech at its third.  Span is the direct measure of how much
        # audio nobody chose an alignment carries, and it is arithmetic
        # over this passage's own words - not a band fitted to one
        # recording.
        gap = _leading_gap(aligned)
        return (
            -len(aligned),
            aligned[-1]["end"] - aligned[0]["start"],
            float("inf") if gap is None else gap,
            abs(aligned[0]["start"] - hint_start),
        )

    # What the rule that shipped - the occurrence nearest the hint -
    # would have produced, so the report can name what it moved from.
    nearest_idx = min(
        occurrences, key=lambda i: abs(candidates[i]["start"] - hint_start))
    hint_nearest = _align_from(
        candidates, passage_words, _anchor_backed_up(candidates, nearest_idx))

    aligned = min(attempts, key=rank)
    _record_alignment(notes, aligned, len(occurrences), hint_nearest)
    return aligned


def _record_alignment(notes, aligned, anchors_considered, hint_nearest):
    """Fill `notes` with what the anchor search measured and did."""
    if notes is None:
        return
    gap = _leading_gap(aligned)
    chosen_start = aligned[0]["start"] if aligned else None
    nearest_start = hint_nearest[0]["start"] if hint_nearest else None
    moved = (
        nearest_start is not None
        and chosen_start is not None
        and abs(chosen_start - nearest_start) > SOURCE_OVERLAP_EPSILON
    )
    largest = _largest_gap(aligned)
    if moved:
        event = "reanchored"
    elif (anchors_considered > 1 and gap is not None and gap > 0
            and largest is not None and gap >= largest):
        # The passage's LONGEST silence sits at its very head - the
        # mis-anchor's own signature - and yet no alternative anchor
        # aligns this passage as completely in a tighter span.  The
        # silence is the speaker's, not the aligner's.  Held, and said
        # so: this is where 001's body_0 dramatic pause lands - "and ...
        # i have an announcement to make" - and flattening it would break
        # a real creative decision.  The comparison is against this
        # passage's own gaps; there is no threshold.
        event = "held"
    else:
        event = "ok"
    notes.update({
        "leading_gap": gap,
        "largest_gap": largest,
        "voiced_fraction": _voiced_fraction(aligned),
        "anchors_considered": anchors_considered,
        "hint_nearest_start": nearest_start,
        "hint_nearest_leading_gap": _leading_gap(hint_nearest or []),
        "chosen_start": chosen_start,
        "event": event,
    })


def _report_anchor(label: str, clip_id: str, enrichment: dict,
                   report: list) -> None:
    """Say out loud what the anchor search did to this passage.

    A leading gap is either REMOVED by a better anchor or HELD because no
    equally complete anchor removes it, and both are said - one silently
    corrected mis-anchor is what put 6.901s of unintended audio into a
    59.437s video, and one silently swallowed pause would flatten a real
    dramatic beat.  The numbers are measurements of this passage, not a
    verdict against a constant.
    """
    alignment = enrichment.get("alignment") or {}
    if not alignment:
        return

    gap = alignment.get("leading_gap")
    voiced = alignment.get("voiced_fraction")
    entry = {
        "block": label,
        "clip_id": clip_id,
        "event": alignment.get("event", "ok"),
        "source_start": enrichment.get("start_time"),
        "source_end": enrichment.get("end_time"),
        "leading_gap_seconds": None if gap is None else round(gap, 3),
        "hint_nearest_leading_gap_seconds": (
            None if alignment.get("hint_nearest_leading_gap") is None
            else round(alignment["hint_nearest_leading_gap"], 3)),
        "largest_gap_seconds": (
            None if alignment.get("largest_gap") is None
            else round(alignment["largest_gap"], 3)),
        "voiced_fraction": None if voiced is None else round(voiced, 3),
        "anchors_considered": alignment.get("anchors_considered"),
        "hint_nearest_start": alignment.get("hint_nearest_start"),
        "chosen_start": alignment.get("chosen_start"),
    }
    report.append(entry)

    if entry["event"] == "ok":
        return

    gap_text = "n/a" if gap is None else f"{gap:.3f}s"
    voiced_text = "n/a" if voiced is None else f"{voiced * 100:.1f}%"
    if entry["event"] == "reanchored":
        nearest = alignment.get("hint_nearest_start")
        chosen = alignment.get("chosen_start")
        was = alignment.get("hint_nearest_leading_gap")
        was_text = "n/a" if was is None else f"{was:.3f}s"
        print(
            f"  {label}: RE-ANCHORED on {clip_id} from {nearest:.3f}s "
            f"(the occurrence nearest the LLM's hint, leading gap "
            f"{was_text}) to {chosen:.3f}s (leading gap {gap_text}) - one "
            f"of {alignment.get('anchors_considered')} occurrences of the "
            f"passage's first word; it aligns at least as many words in a "
            f"tighter span. The span is {voiced_text} voiced.",
            file=sys.stderr,
        )
        return

    print(
        f"  {label}: leading gap of {gap_text} HELD on {clip_id} - it is "
        f"this passage's longest silence, and none of "
        f"{alignment.get('anchors_considered')} anchors for the passage's "
        f"first word aligns it as completely in a tighter span, so the "
        f"silence is the speaker's. The span is {voiced_text} voiced.",
        file=sys.stderr,
    )


def enrich_speech_sequence(
    speech_sequence: dict,
    temporal_index_dir: str,
) -> dict:
    """Enrich all passages with word timestamps from temporal index.

    Uses timestamp-based lookup. Each passage must have start/end
    timestamps resolved from the temporal index.
    """

    # Cache loaded temporal indices
    ti_cache = {}

    def load_ti(clip_id):
        if clip_id not in ti_cache:
            path = os.path.join(temporal_index_dir, f"{clip_id}.json")
            if os.path.exists(path):
                with open(path) as f:
                    ti_cache[clip_id] = json.load(f)
            else:
                print(f"  WARNING: No temporal index for {clip_id}",
                      file=sys.stderr)
                ti_cache[clip_id] = {}
        return ti_cache[clip_id]

    # The source range each clip's most recent body passage claimed. Two
    # body passages cut from one clip may not claim the same audio: the
    # second would replay the tail of the first. The hook is deliberately
    # absent - it is allowed to tease a body passage, which
    # _drop_hook_duplicates bounds by IoU instead.
    claimed_by_clip = {}

    # What the anchor search did to every passage, in order.  This is the
    # durable half of "never correct silently": the stderr lines are read
    # by whoever is watching the run, this is read by whoever is not.
    alignment_report = []

    def enrich_passage(passage: dict, label: str,
                       prior_claim: tuple = None) -> dict:
        """Resolve a passage's real source timings from the temporal index.

        The LLM's `source_start`/`source_end` are a HINT only - they
        disambiguate which occurrence of the text to align against.  The
        returned times always come from WhisperX word timings, so an LLM
        that guessed a plausible-looking round-number range cannot leak
        fabricated timings into the timeline.

        Raises PassageAlignmentError when the passage cannot be aligned.
        Failing loudly here is the point: a passage with no word timings
        used to sail through as `word_timestamps: []`, which disabled
        beat-aligned cutting and left the back half of the video showing
        footage that did not match its transcript.
        """
        clip_id = passage.get("clip_id")
        if not clip_id:
            raise PassageAlignmentError(f"{label}: passage has no clip_id")

        if "source_start" not in passage or "source_end" not in passage:
            raise PassageAlignmentError(
                f"{label}: passage is missing source_start/source_end. "
                f"The speech_sequence contract requires both. "
                f"Got keys: {sorted(passage.keys())}"
            )
        try:
            start = float(passage["source_start"])
            end = float(passage["source_end"])
        except (TypeError, ValueError) as e:
            raise PassageAlignmentError(
                f"{label}: source_start/source_end are not numeric "
                f"({passage['source_start']!r}, {passage['source_end']!r})"
            ) from e

        passage_text = passage.get("text", "")
        if not passage_text.strip():
            raise PassageAlignmentError(f"{label}: passage has no text")

        ti = load_ti(clip_id)
        regions = ti.get("speech_regions", [])
        if not regions:
            raise PassageAlignmentError(
                f"{label}: no speech regions in the temporal index for "
                f"{clip_id} - cannot resolve real word timings"
            )

        enrichment = collect_words_in_range(
            start, end, regions, passage_text=passage_text,
        )

        # Text alignment searches the whole clip, so a passage whose
        # opening words recur can anchor to the wrong occurrence and come
        # back looking perfectly valid. If the aligned span wanders far
        # from the LLM's hint, re-align against the hint window only.
        drift = _hint_drift(enrichment, start, end)
        if drift is not None and drift > MAX_HINT_DRIFT:
            windowed = collect_words_in_range(
                start, end, _clip_regions_to_window(regions, start, end),
                passage_text=passage_text,
            )
            if windowed["word_timestamps"]:
                print(
                    f"  {label}: full-clip alignment drifted {drift:.2f}s "
                    f"from the hint; re-anchored inside the hint window",
                    file=sys.stderr,
                )
                enrichment = windowed

        # Structural check, not a threshold: a passage that begins inside
        # the range the previous passage on this clip already took is
        # mis-anchored by construction - the timeline would lay that audio
        # down twice, back to back. This is how block 5 came to start on
        # block 4's trailing "to post" while its own opening words went
        # unmatched, at 0.76s of drift - comfortably under MAX_HINT_DRIFT.
        # Re-anchor past the claimed range, or fail the passage loudly.
        if _overlaps(enrichment, prior_claim):
            overlap_start = enrichment["start_time"]
            reanchored = collect_words_in_range(
                start, end, _clip_regions_after(regions, prior_claim[1]),
                passage_text=passage_text,
            )
            if (reanchored["word_timestamps"]
                    and not _overlaps(reanchored, prior_claim)):
                print(
                    f"  {label}: alignment started at {overlap_start:.3f}s, "
                    f"inside the previous passage on {clip_id} "
                    f"({prior_claim[0]:.3f}-{prior_claim[1]:.3f}); "
                    f"re-anchored to {reanchored['start_time']:.3f}s",
                    file=sys.stderr,
                )
                enrichment = reanchored
            else:
                raise PassageAlignmentError(
                    f"{label}: aligned to {overlap_start:.3f}-"
                    f"{enrichment['end_time']:.3f}s in {clip_id}, which "
                    f"overlaps the previous passage on that clip "
                    f"({prior_claim[0]:.3f}-{prior_claim[1]:.3f}s), and no "
                    f"alignment after {prior_claim[1]:.3f}s matches the "
                    f"passage text {passage_text[:60]!r}. Two blocks cut "
                    f"from one clip cannot claim the same source audio."
                )

        words = enrichment["word_timestamps"]

        if not words:
            raise PassageAlignmentError(
                f"{label}: no words in {clip_id} matched the passage text "
                f"{passage_text[:60]!r} (LLM hint was "
                f"{start:.2f}-{end:.2f}s). The passage text must be "
                f"verbatim from the transcript."
            )

        # Faithfulness check: the aligned words must actually be this
        # passage, not a nearby run of words that happened to match.
        passage_words = set(normalize(passage_text).split())
        found_words = set(normalize(w["word"]) for w in words)
        ratio = len(passage_words & found_words) / max(len(passage_words), 1)
        if ratio < MIN_TEXT_OVERLAP:
            raise PassageAlignmentError(
                f"{label}: aligned words overlap the passage text by only "
                f"{ratio:.0%} (minimum {MIN_TEXT_OVERLAP:.0%}) in {clip_id} "
                f"at {enrichment['start_time']:.2f}-"
                f"{enrichment['end_time']:.2f}s"
            )

        hint_drift = max(
            abs(enrichment["start_time"] - start),
            abs(enrichment["end_time"] - end),
        )
        enrichment["alignment_method"] = "whisperx_word_alignment"
        # stderr, not stdout: stdout carries this bridge's JSON output.
        print(
            f"  {label}: {len(words)} words "
            f"({enrichment['start_time']:.3f}->{enrichment['end_time']:.3f}), "
            f"LLM hint was {start:.3f}->{end:.3f} "
            f"(drift {hint_drift:.2f}s)",
            file=sys.stderr,
        )
        _report_anchor(label, clip_id, enrichment, alignment_report)
        return enrichment

    def apply_enrichment(passage: dict, enrichment: dict) -> None:
        """Write resolved timings back onto a passage, in place."""
        passage["word_timestamps"] = enrichment["word_timestamps"]
        passage["alignment_method"] = enrichment["alignment_method"]
        # The aligned times replace the LLM's hint outright - they are the
        # only timings the pipeline is allowed to cut to.
        passage["source_start"] = enrichment["start_time"]
        passage["source_end"] = enrichment["end_time"]
        passage["start_time"] = enrichment["start_time"]
        passage["end_time"] = enrichment["end_time"]
        passage["duration_seconds"] = round(
            enrichment["end_time"] - enrichment["start_time"], 3
        )

    result = dict(speech_sequence)
    failures = []

    # Enrich hook segment
    hook = result.get("hook_segment")
    if hook:
        try:
            apply_enrichment(hook, enrich_passage(hook, "Hook"))
        except PassageAlignmentError as e:
            failures.append(str(e))

    # Enrich body passages
    body = result.get("body_sequence", [])
    aligned_body = []
    for passage in body:
        pos = passage.get("position", "?")
        clip_id = passage.get("clip_id")
        try:
            enrichment = enrich_passage(
                passage, f"Body[{pos}]", claimed_by_clip.get(clip_id),
            )
        except PassageAlignmentError as e:
            failures.append(str(e))
            continue
        apply_enrichment(passage, enrichment)
        claimed_by_clip[clip_id] = (
            enrichment["start_time"], enrichment["end_time"],
        )
        aligned_body.append(passage)

    if failures:
        raise PassageAlignmentError(
            f"{len(failures)} passage(s) could not be aligned to real word "
            f"timings:\n  - " + "\n  - ".join(failures)
        )

    result["body_sequence"] = _drop_hook_duplicates(hook, aligned_body, result)
    result["alignment_report"] = alignment_report
    return result


def _drop_hook_duplicates(hook: dict, body: list, result: dict) -> list:
    """Remove body passages that are the same moment as the hook.

    The hook is allowed to tease a longer body passage - that is a
    deliberate shortform technique.  It is not allowed to be byte-identical
    to one: that opens the video with a line and replays it verbatim a few
    seconds later.
    """
    if not hook or hook.get("source_start") is None:
        return body

    hook_clip = hook.get("clip_id")
    hook_start = hook["source_start"]
    hook_end = hook["source_end"]

    kept = []
    for passage in body:
        if passage.get("clip_id") != hook_clip:
            kept.append(passage)
            continue

        overlap = min(hook_end, passage["source_end"]) - max(
            hook_start, passage["source_start"]
        )
        union = max(hook_end, passage["source_end"]) - min(
            hook_start, passage["source_start"]
        )
        iou = overlap / union if union > 0 else 0.0

        if overlap > 0 and iou > MAX_HOOK_BODY_IOU:
            print(
                f"  Dropped Body[{passage.get('position')}]: same moment as "
                f"the hook ({hook_clip} {passage['source_start']:.3f}-"
                f"{passage['source_end']:.3f}, IoU {iou:.2f}) - the video "
                f"would open with this line and repeat it verbatim",
                file=sys.stderr,
            )
            result.setdefault("excluded_passages", []).append({
                "clip_id": hook_clip,
                "text": passage.get("text", ""),
                "source_start": passage["source_start"],
                "source_end": passage["source_end"],
                "reason_excluded": (
                    f"duplicate of hook_segment (IoU {iou:.2f})"
                ),
            })
            continue

        kept.append(passage)

    # Renumber so positions stay contiguous after a drop.
    for i, passage in enumerate(kept, start=1):
        passage["position"] = i
    return kept


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    # C2 fix: State provides temporal_index as an object with index_dir,
    # not temporal_index_dir at root level.
    _require_keys(data, ["speech_sequence", "temporal_index"], "Input data")
    if not isinstance(data["speech_sequence"], dict):
        raise ValueError("speech_sequence must be a dictionary")

    speech_sequence = data.get("speech_sequence")
    if not speech_sequence:
        print(json.dumps({"error": "Missing: speech_sequence",
                          "step": "2.02_bridge"}))
        sys.exit(1)

    temporal_index = data.get("temporal_index", {})
    ti_dir = ""
    if isinstance(temporal_index, dict):
        ti_dir = temporal_index.get("index_dir", "")
    elif isinstance(temporal_index, list) and len(temporal_index) > 0:
        first = temporal_index[0]
        if isinstance(first, dict) and "index_path" in first:
            ti_dir = os.path.dirname(first["index_path"])
            
    if not ti_dir or not os.path.isdir(ti_dir):
        # Fallback to pipeline_data.json like bridge.py
        project_dir = data.get("project_folder", "")
        if project_dir:
            layout = ProjectLayout(project_dir)
            state_file = str(layout.pipeline_data_path)
            if os.path.exists(state_file):
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                    ti_dir = state_data.get("step_outputs", {}).get("temporal_index", {}).get("index_dir", "")
            if not ti_dir or not os.path.isdir(ti_dir):
                ti_dir = str(layout.read_dir(Area.TEMPORAL_INDEX))
                    
    if not ti_dir or not os.path.isdir(ti_dir):
        print(json.dumps({"error": f"Invalid temporal_index.index_dir: {ti_dir}",
                          "step": "2.02_bridge"}))
        sys.exit(1)

    enriched = enrich_speech_sequence(speech_sequence, ti_dir)

    body = enriched.get("body_sequence", [])
    if len(body) < 8 or len(body) > 20:
        print(f"WARNING: Selected {len(body)} speech passages. Recommended is 10-15.", file=sys.stderr)
        
    total_duration = 0.0
    hook = enriched.get("hook_segment")
    if hook:
        start = hook.get("start_time")
        end = hook.get("end_time")
        if start is not None and end is not None:
            total_duration += (end - start)
            
    for passage in body:
        start = passage.get("start_time")
        end = passage.get("end_time")
        if start is not None and end is not None:
            total_duration += (end - start)
            
    from library.tools.duration_targets import (
        NO_TARGET_DECLARED, get_target_duration_zone)
    zone = get_target_duration_zone(data)

    if zone is None:
        # Stated, not measured against a made-up minute.
        print(f"NOTE: speech duration ({total_duration:.1f}s) not checked - "
              f"{NO_TARGET_DECLARED}.", file=sys.stderr)
    else:
        min_dur, target_dur, max_dur = zone
        if total_duration > max_dur or total_duration < min_dur:
            print(f"WARNING: Total speech duration ({total_duration:.1f}s) is out of bounds ({min_dur:.1f}-{max_dur:.1f}s).", file=sys.stderr)

    json.dump({
        "step": "2.02_bridge",
        "speech_sequence": enriched,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
