"""Played == captioned == spine, at word level.

`library/tools/subtitle_qa.py` checks overflow, overlap, duration, gaps
and read speed - all timing and geometry. No check in this pipeline read
whether the captioned words ARE the words that were played, so a reel
could lose a word, gain a word, or lose a whole caption, pass every
gate, and reach the captain. Measured 2026-09-19 on the field test
(`Podcast (field test)` / geo-podcast): Reel 12 captions "probably
probably" for one spoken "probably", Reel 29 plays ~2.3s of Craig with
no caption at all, Reel 29 captions "you're running a" where the reel
plays "If you're running a".

The three word sequences, and why three and not two
---------------------------------------------------
- **played** - what the reel's audio carries: transcript words
  intersected with the reel's PLACED audio spans (source file + source
  seconds), expressed in reel seconds. Placed spans, never the plan's
  keep ranges: the F11 rule (read the cards on the timeline, not the
  config) applies to the audio too - a re-cut reel plays what it
  places.
- **captioned** - what the subtitle assets render: the WORDS in the
  `<stem>_props.json` artefact each placed `sub_*` clip was rendered
  from, positioned by the clip's PLACED record position - never by the
  props' own `_timeline_start`, which goes stale the first time a
  re-plan reuses a render (measured on Reel 29: every card from 27s on
  carries a `_timeline_start` ~8-17s later than where it is placed,
  because the words were re-planned and the pixels reused).
- **spine** - what the reel's own proposal says belongs there: the same
  transcript words inside the proposal's `source_spans`, as a sequence.
  Played-vs-spine drift is the build's (retakes, re-cuts - another
  lane's), captioned-vs-played is this gate's.

The three disagreements have different causes and are reported
separately: a word played but not captioned, a word captioned but not
played, and a span with audio and no caption at all. A fourth finding,
`empty_window`, needs no transcript at all: a caption word the props
give no time on screen (end <= start) can never highlight, which the
artefact says about itself.

What this does NOT read
-----------------------
Per-word confidence scores. The default MFA path carries none
(`ALIGNMENT_SCORE_ABSENT_MFA`), so the diff works on the words
themselves. Comparison is on READ forms - the transcript words run
through the caption reading (`read_words_for_comparison`: the numeral
rule, the acronym set, the recorded spellings - the same procedure
the planner renders with) and then normalised (case and surrounding
punctuation folded - the same rule the renderer matches emphasis
with, `SubtitleOverlay normaliseWord`) - while every finding quotes
the RAW forms. A deliberate respelling ("three" reads "3", "jim and
i" reads "Gemini") never reads as a false positive; an unread one
("hello" captioned "goodbye") still fails.

A stretched word (longer than `max_word_seconds`) is the aligner
bridging silence, not speech - the same reading F5 applies
(`MAX_WORD_SECONDS` there) - and is excluded from the played measure
and REPORTED, never silently skipped.

Rows the transcript could not bind to any source (`source_file` None,
or words with no timings) cannot be mapped onto the reel and are
collected as `undetermined`: reported, never counted as played and
never counted as missing.

Transcription-level loss. Every leg of this diff - played, captioned
and spine - reads the transcript, so words the transcription never
produced are invisible to it BY CONSTRUCTION, and no timing hole is
left behind for it to trip on: the aligner stretches the surviving
words across the gap. Measured on Reel 12 (field test, 2026-09-19):
the large-v3 arm dropped "pull from there" and stuttered "probably"
into two in one 46-word row, MFA stretched "hallucinate" to 1.55s -
under `DEFAULT_MAX_WORD_SECONDS`, so the stretched-word exclusion
above never fired - and this gate reported CLEAN on audio speaking 9
words where the card captions 6. The "played" leg is the transcript
mapped through placed spans, not ground truth. A duration-anomaly
warning upstream of caption planning
(`library/tools/transcript_duration_anomaly.py`) flags the stretched
words before any pixel renders; the words never transcribed remain
outside what any played-vs-captioned comparison can see.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence

# A word the aligner stretched across silence is not speech. F5's own
# bound, kept as a parameter default rather than imported, so this
# module stays free of the verifier's Resolve-adjacent imports and the
# two readers of one number cannot drift silently: the verifier passes
# its MAX_WORD_SECONDS explicitly at the call site.
DEFAULT_MAX_WORD_SECONDS = 3.0

# How much two adjacent words of one transcript row may overlap before
# their timings stop being measurements. Genuine aligner output is
# ordered and non-overlapping (neighbours touch at worst); every
# measured violation on the field test is an exact 0.02s pile-up - 18
# pairs in 12 rows - where the aligner stamped several words onto one
# instant. 0.005s sits far above float dust and far below that.
DEGENERATE_OVERLAP_SECONDS = 0.005

# How far past a card's edge a played word may sit and still count as
# the neighbour card's spill rather than a missing/extra word. Card
# edges are frame-quantised on both sides (the plan rounds, the build
# rounds again), so a word straddling the boundary by a frame or two is
# placement arithmetic, not a lost word.
EDGE_SPILL_SECONDS = 0.15

# How much of a transcript word must lie inside a placed span to count
# as played. A word ending exactly where a cut begins still overlaps
# its span by float dust - measured 2026-09-19 on Reel 04: "because"
# ends at the 268.94s head trim to the millisecond and mapped as a 5ms
# played sliver at reel 0.00s, which the caption correctly drops and
# the diff then fails. A sliver narrower than half a frame (21ms at
# 23.976fps) is placement dust, never speech a card could carry: cards
# are frame-quantised, and the Reel-12 "That" precedent (50ms of a word
# surviving a cut counts as played) stays comfortably above this floor.
MIN_SPAN_OVERLAP_SECONDS = 0.02


def degenerate_indices(words: Sequence[dict]) -> set:
    """Indices of words whose timings contradict their neighbours.

    The words of one transcript row arrive in time order; both members
    of every adjacent pair overlapping by more than
    DEGENERATE_OVERLAP_SECONDS are pile-up, and neither member's
    position can be trusted for card assignment. The words still mean
    speech at that instant - only their placement is unusable.
    """
    bad: set = set()
    timed = []
    for index, word in enumerate(words):
        try:
            timed.append((index, float(word["start"]), float(word["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    for (ia, a0, a1), (ib, b0, b1) in zip(timed, timed[1:]):
        if min(a1, b1) - max(a0, b0) > DEGENERATE_OVERLAP_SECONDS:
            bad.add(ia)
            bad.add(ib)
    return bad


def normalize_word(word: str) -> str:
    """Fold case and surrounding punctuation, keep the token.

    The same rule the renderer matches emphasis words with
    (`SubtitleOverlay normaliseWord`: lowercase, strip non-letters and
    non-digits at the edges, keep internal apostrophes), so "AI",
    "ai" and "ai," compare equal while "100" and "hundred" do not.
    Empty string for tokens that are pure punctuation.
    """
    lowered = (word or "").lower()
    return re.sub(r"^[^\w']+|[^\w']+$", "", lowered, flags=re.UNICODE)


def _base(path: str) -> str:
    """Compare source files by basename.

    The transcript and the timeline snapshot are written on the same
    machine in one run, so both carry absolute paths; the basename is
    what identifies the media when a project moves between machines.
    The full paths travel in the output, so a collision would read as
    one rather than silently matching.
    """
    return os.path.basename(path or "")


def _word_source_time(segment: dict, word: dict) -> Optional[float]:
    """One transcript word's time in its SOURCE file's timebase.

    Word timings arrive in master-timeline time; the row's own
    `timeline_start`/`source_start` pair is the rebasing, the same
    arithmetic `to_source_time` applies on the transcribe path.
    None where the row or the word carries no timing to rebase from.
    """
    try:
        w_start = float(word["start"])
        seg_start = float(segment["timeline_start"])
        src_start = float(segment["source_start"])
    except (KeyError, TypeError, ValueError):
        return None
    return src_start + (w_start - seg_start)


def played_words_from_transcript(
    segments: Sequence[dict],
    placed_spans: Sequence[dict],
    max_word_seconds: float = DEFAULT_MAX_WORD_SECONDS,
) -> dict:
    """Transcript words the reel's placed spans actually play.

    `placed_spans` are the reel's own audio items as
    `{source_file, source_start, source_end, reel_start}` in seconds,
    where `reel_start` is the reel time `source_start` plays at. Words
    land in reel time through the span that contains their source time.

    Returns `{words, stretched, undetermined}`: `words` are
    `{word, norm, reel_start, reel_end, source_file, source_time,
    speaker}`;
    Returns `{words, stretched, undetermined, slivers, serialized}`:
    `words` are `{word, norm, reel_start, reel_end, source_file,
    source_time, speaker}`; `stretched` are words longer than `max_word_seconds`
    (aligner bridging silence - excluded and reported); `undetermined`
    are rows that could not be mapped at all (unbound, untimed -
    reported, never counted); `slivers` are words touching a placed
    span by less than MIN_SPAN_OVERLAP_SECONDS (placement dust -
    reported, never counted); `serialized` are same-file overlaps the
    transcript stamped onto one instant, clipped sequential and
    reported (one file is one mouth).
    """
    by_file: Dict[str, list] = {}
    for span in placed_spans or []:
        by_file.setdefault(_base(span.get("source_file")), []).append(span)

    words: list = []
    stretched: list = []
    undetermined: list = []
    slivers: list = []
    degenerate_rows: list = []
    for segment in segments or []:
        seg_file = segment.get("source_file")
        seg_words = segment.get("words") or []
        if not seg_file or segment.get("source_start") is None:
            if seg_words:
                undetermined.append({
                    "reason": "unbound",
                    "text": (segment.get("text") or "")[:120],
                    "timeline_start": segment.get("timeline_start"),
                    "timeline_end": segment.get("timeline_end"),
                    "word_count": len(seg_words),
                })
            continue
        spans = by_file.get(_base(seg_file), [])
        if not spans or not seg_words:
            continue
        # Words whose timings contradict their neighbours are pile-up:
        # they still mean speech at that instant (coverage counts
        # them), but no card assignment can be trusted with them.
        degenerate = degenerate_indices(seg_words)
        mapped = 0
        for index, word in enumerate(seg_words):
            raw = word.get("word", "")
            norm = normalize_word(raw)
            if not norm:
                continue
            try:
                w_start = float(word["start"])
                w_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                undetermined.append({
                    "reason": "untimed-word",
                    "text": raw[:60],
                    "timeline_start": segment.get("timeline_start"),
                })
                continue
            if w_end - w_start > max_word_seconds:
                stretched.append({
                    "word": raw,
                    "span_seconds": round(w_end - w_start, 3),
                    "timeline_start": segment.get("timeline_start"),
                })
                continue
            src = _word_source_time(segment, word)
            try:
                src_end = float(segment["source_start"]) + (
                    w_end - float(segment["timeline_start"]))
            except (KeyError, TypeError, ValueError):
                src_end = None
            if src is None or src_end is None:
                continue
            # Overlap, never start-containment: a word beginning a frame
            # before a placed span still plays (measured: Reel 12 opens
            # on "That", whose first 0.05s the reel cuts). The played
            # part is clipped to the span; a word inside a removed
            # take overlaps no span and stays out. The overlap must
            # clear MIN_SPAN_OVERLAP_SECONDS: a word ending exactly at
            # a cut still touches its span by float dust, and that
            # sliver is not speech (measured: Reel 04's "because" at
            # the 268.94s head trim).
            hit = None
            sliver = False
            for span in spans:
                overlap = (min(src_end, span["source_end"])
                           - max(src, span["source_start"]))
                if overlap <= 0:
                    continue
                if overlap > MIN_SPAN_OVERLAP_SECONDS:
                    hit = span
                    break
                sliver = True
            if hit is None:
                if sliver:
                    slivers.append({
                        "word": raw,
                        "overlap_seconds": round(overlap, 4),
                        "timeline_start": segment.get("timeline_start"),
                    })
                continue
            mapped += 1
            reel_start = hit["reel_start"] + max(
                0.0, src - hit["source_start"])
            reel_end = hit["reel_start"] + min(
                src_end, hit["source_end"]) - hit["source_start"]
            words.append({
                "word": raw,
                "norm": norm,
                "reel_start": reel_start,
                "reel_end": reel_end,
                "source_file": seg_file,
                "source_time": src,
                "degenerate": index in degenerate,
                # The row's speaker travels so the verifier can apply
                # the same display suppressions the caption planner
                # applies per block (F25): an anchored suppression
                # names its speaker, and a diff without it fails
                # captions that correctly hide a recorded token.
                "speaker": segment.get("speaker"),
            })
        # A pile-up row that plays nothing on this reel is not this
        # reel's problem: only rows contributing words here are named.
        if degenerate and mapped:
            degenerate_rows.append({
                "text": (segment.get("text") or "")[:120],
                "timeline_start": segment.get("timeline_start"),
                "timeline_end": segment.get("timeline_end"),
                "word_count": len(seg_words),
                "degenerate_count": len(degenerate),
            })
    words.sort(key=lambda w: (w["reel_start"], w["reel_end"]))
    serialized = _serialize_same_file_overlaps(words)
    return {"words": words, "stretched": stretched,
            "undetermined": undetermined,
            "slivers": slivers,
            "serialized": serialized,
            "degenerate_rows": degenerate_rows}


def _serialize_same_file_overlaps(words: Sequence[dict]) -> list:
    """Clip same-file overlaps to sequential, and say so.

    One source file is one mouth: two of its words stamped onto one
    instant is alignment slop, not talk-over (measured 2026-09-19 on
    Reel 04: the transcript runs Craig's "website." to 817.89s while
    starting his "Also" at 817.59s of the same LCATL0013 - the caption
    must serialize them onto one track whatever the transcript says,
    and grading the serialization against the overlap fails correct
    output). The earlier word ends where the later begins - the later
    word's start anchors its card, so it never moves. Different files
    are different mics and keep their overlaps: real talk-over still
    diffs as before.

    A clip that would invert the word (end at or before its start) is
    left alone: destroying a word is not serializing it. Every clip is
    reported - the played measure changed, so the report says where.
    """
    serialized: list = []
    by_file: Dict[str, list] = {}
    for word in words:
        # No source, no mouth: words that cannot be bound to one file
        # never serialize against each other.
        key = _base(word.get("source_file"))
        if not key:
            continue
        by_file.setdefault(key, []).append(word)
    for group in by_file.values():
        ordered = sorted(group,
                         key=lambda w: (w["reel_start"], w["reel_end"]))
        for earlier, later in zip(ordered, ordered[1:]):
            overlap = earlier["reel_end"] - later["reel_start"]
            if overlap <= DEGENERATE_OVERLAP_SECONDS:
                continue
            clipped = min(earlier["reel_end"], later["reel_start"])
            if clipped <= earlier["reel_start"] + DEGENERATE_OVERLAP_SECONDS:
                serialized.append({
                    "word": earlier["word"],
                    "reel_start": round(earlier["reel_start"], 3),
                    "reel_end": round(earlier["reel_end"], 3),
                    "overlaps": later["word"],
                    "unresolved": True,
                })
                continue
            serialized.append({
                "word": earlier["word"],
                "reel_start": round(earlier["reel_start"], 3),
                "was_end": round(earlier["reel_end"], 3),
                "now_end": round(clipped, 3),
                "overlaps": later["word"],
            })
            earlier["reel_end"] = clipped
    return serialized


def read_words_for_comparison(words: Sequence[dict],
                              corrections=None) -> list:
    """Played words through the caption reading the planner applied.

    The planner renders acronyms, numerals and recorded spellings
    (`library/tools/caption_reading.py`: "three" reads "3", "google"
    reads "Google"), so a verifier comparing RAW transcript words
    against READ captions fails correct output (measured 2026-09-19
    on Reel 04: '3' where the reel plays 'three'). Both sides of the
    diff must be in read space: this runs the SAME procedure the
    planner runs - `apply_caption_reading` with the project's recorded
    spellings - over the played words, keeping every entry's reel
    timings (a merged numeral spans its first word's start to its
    last word's end, exactly as the planner's does) and recomputing
    `norm` off the read form. Entries reading to pure punctuation are
    dropped, the way the played derivation drops them going in.
    """
    from library.tools import caption_reading as _reading

    played = list(words or [])
    if not played:
        return []
    projected = []
    for index, entry in enumerate(played):
        projected.append({
            "word": entry.get("word", ""),
            "start": entry.get("reel_start"),
            "end": entry.get("reel_end"),
            "_order": index,
        })
    read = _reading.apply_caption_reading(projected, corrections)
    orders = [entry.get("_order") for entry in read]
    out: list = []
    for position, entry in enumerate(read):
        first = entry.get("_order")
        if not isinstance(first, int):
            continue
        last = (orders[position + 1]
                if position + 1 < len(orders) else len(played))
        norm = normalize_word(str(entry.get("word", "")))
        if not norm:
            continue
        kept = dict(played[first])
        kept["word"] = entry.get("word")
        kept["norm"] = norm
        kept["reel_start"] = entry.get("start")
        kept["reel_end"] = entry.get("end")
        kept["degenerate"] = any(
            bool(source.get("degenerate"))
            for source in played[first:last])
        out.append(kept)
    return out


def _props_path(props_dir: str, clip_path: str) -> str:
    stem = os.path.basename(clip_path or "")
    if stem.endswith(".mov"):
        stem = stem[:-4]
    return os.path.join(props_dir, stem + "_props.json")


def captioned_words_from_placed_cards(
    cards: Sequence[dict],
    props_dir: str,
    fps: float,
) -> dict:
    """The words the placed caption clips render, in reel time.

    `cards` are the timeline's caption items as
    `{clip_path, reel_start_frame, source_in_frame}` - record position
    and left offset in frames. Words come from the props ARTEFACT each
    clip was rendered from (`<stem>_props.json`), positioned by the
    PLACED record frames: a word at render-relative frame `f` plays at
    `reel_start_frame + (f - source_in_frame)`.

    The props' own `_timeline_start` is NEVER read for placement: it
    goes stale the first time a re-plan reuses a render (same words,
    new time - the reuse key digests what draws, never placement), and
    reading it would grade the reel against a plan it no longer plays.

    Returns `{words, cards, unreadable}`: `words` are
    `{word, norm, reel_start, reel_end, card}`; `cards` are the placed
    spans `{card, reel_start, reel_end}`; `unreadable` are placed cards
    whose props are missing or carry no words - a check that cannot
    read the artefact says so rather than passing the card.
    """
    words: list = []
    spans: list = []
    unreadable: list = []
    for card in cards or []:
        clip_path = card.get("clip_path") or ""
        name = os.path.basename(clip_path)
        try:
            reel_start_f = float(card["reel_start_frame"])
            source_in = float(card["source_in_frame"])
        except (KeyError, TypeError, ValueError):
            unreadable.append({"card": name or "?", "reason": "no-record"})
            continue
        span_start = reel_start_f / fps
        try:
            with open(_props_path(props_dir, clip_path),
                      encoding="utf-8") as handle:
                props = json.load(handle)
        except (OSError, ValueError) as exc:
            unreadable.append({"card": name, "reason": f"no-props: {exc}"[:160]})
            continue
        card_words: list = []
        # Every norm the card DRAWS, timed or not: a pile-up stamp can
        # leave a rendered word with no karaoke window (measured
        # 2026-09-20 on Reel 27: the card reads "5-star reviews, AI
        # does see that." while its timed words start at "reviews," -
        # the transcript stamps 'five-star' at 20ms inside 'reviews,').
        # The coverage spill rule consults these so a degenerate-timing
        # word the viewer can read is timing, never a missing caption.
        text_norms: set = set()
        for sub in props.get("subtitles") or []:
            for token in str(sub.get("text") or "").split():
                normed = normalize_word(token)
                if normed:
                    text_norms.add(normed)
        for sub in props.get("subtitles") or []:
            for word in sub.get("words") or []:
                raw = word.get("word", "")
                norm = normalize_word(raw)
                if not norm:
                    continue
                try:
                    wf_start = float(word["startFrame"])
                    wf_end = float(word["endFrame"])
                except (KeyError, TypeError, ValueError):
                    continue
                card_words.append({
                    "word": raw,
                    "norm": norm,
                    "reel_start": (reel_start_f + (wf_start - source_in)) / fps,
                    "reel_end": (reel_start_f + (wf_end - source_in)) / fps,
                    "card": name,
                })
        if not card_words:
            unreadable.append({"card": name, "reason": "props-carry-no-words"})
            continue
        card_words.sort(key=lambda w: (w["reel_start"], w["reel_end"]))
        words.extend(card_words)
        try:
            reel_end_f = float(card["reel_end_frame"])
        except (KeyError, TypeError, ValueError):
            # Card words already carry reel SECONDS; back to frames.
            reel_end_f = max(w["reel_end"] for w in card_words) * fps
        # The card's recorded placement record
        # (`subtitle_segment_id.SEGMENT_BINDING_KEYS`), for the pin
        # owner (`caption_timing.matches`) to address it with: a pin
        # scopes the SPEECH a card captions, and the props artefact
        # behind the placed card is where that speech is recorded.
        # Absent on legacy renders - such a card matches nothing
        # (fail-closed), exactly as the build treats a segment whose
        # binding names no placement.
        binding = {
            "speaker": props.get("_speaker"),
            "source_clip_id": props.get("_source_clip_id"),
            "source_start": props.get("_source_start"),
            "source_end": props.get("_source_end"),
        }
        spans.append({"card": name, "reel_start": span_start,
                      "reel_end": reel_end_f / fps,
                      "text_norms": sorted(text_norms),
                      "binding": binding})
    words.sort(key=lambda w: (w["reel_start"], w["reel_end"]))
    spans.sort(key=lambda c: (c["reel_start"], c["reel_end"]))
    return {"words": words, "cards": spans, "unreadable": unreadable}


def spine_words_from_spans(
    segments: Sequence[dict],
    source_spans: Sequence[dict],
) -> dict:
    """The proposal's words, in proposal order.

    `source_spans` are the reel proposal's `{source_file, source_start,
    source_end}` in seconds. Transcript words whose source time falls
    inside them, span by span. Untimed by design - the spine says WHAT
    belongs there, and the reel says when - so the comparison against
    played is sequential, never positional.

    Returns `{words, undetermined}` with the same row-level honesty as
    the played derivation: a span the transcript cannot bind is said,
    not skipped.
    """
    by_file: Dict[str, list] = {}
    for segment in segments or []:
        if segment.get("source_file"):
            by_file.setdefault(_base(segment["source_file"]), []).append(segment)

    words: list = []
    undetermined: list = []
    for span in source_spans or []:
        segs = by_file.get(_base(span.get("source_file")), [])
        if not segs:
            undetermined.append({
                "reason": "no-transcript-for-span",
                "source_file": span.get("source_file"),
                "source_start": span.get("source_start"),
                "source_end": span.get("source_end"),
            })
            continue
        lo = float(span["source_start"])
        hi = float(span["source_end"])
        for segment in segs:
            if segment.get("source_start") is None:
                continue
            seg_base = float(segment["source_start"])
            try:
                seg_t0 = float(segment["timeline_start"])
            except (KeyError, TypeError, ValueError):
                continue
            for word in segment.get("words") or []:
                raw = word.get("word", "")
                norm = normalize_word(raw)
                if not norm:
                    continue
                try:
                    w0 = seg_base + (float(word["start"]) - seg_t0)
                    w1 = seg_base + (float(word["end"]) - seg_t0)
                except (KeyError, TypeError, ValueError):
                    continue
                # Overlap, like the played derivation: a word the span
                # edge clips still belongs to it.
                if w0 >= hi or w1 <= lo:
                    continue
                src = w0
                words.append({
                    "word": raw,
                    "norm": norm,
                    "source_file": segment.get("source_file"),
                    "source_time": src,
                })
    return {"words": words, "undetermined": undetermined}


def _midpoint(word: dict) -> float:
    return (word["reel_start"] + word["reel_end"]) / 2.0


def _covers(span_start: float, span_end: float, t: float) -> bool:
    return span_start <= t <= span_end


def check_word_coverage(
    played: Sequence[dict],
    captioned: Sequence[dict],
    cards: Sequence[dict],
    spine: Optional[Sequence[dict]] = None,
    edge_spill_seconds: float = EDGE_SPILL_SECONDS,
    suppressed: Optional[Sequence[dict]] = None,
    frame_seconds: float = 1001.0 / 24000.0,
) -> dict:
    """Diff played, captioned and (optionally) spine at word level.

    Three disagreement classes, reported separately because they have
    different causes:

    - `played_not_captioned` (error): maximal runs of played words no
      caption card covers - the missing-words and no-caption-at-all
      classes. One finding per run; the detail carries the words.
    - `captioned_not_played` (error): a placed card covering no played
      word at all (a caption over silence), or card words with no
      played counterpart beside them (words never said).
    - `word_mismatch` (error): a played word the covering card does not
      carry, or a card word the audio does not carry - dropped, added
      or substituted words inside an otherwise covered span. Raw forms
      quoted beside normalised ones.

    `spine` adds the third leg: played-vs-spine drift (the build cut or
    moved what the proposal planned) is `spine_drift` at info - worth a
    look, never a caption failure, because retake removal makes played
    a subset of spine BY DESIGN and failing that would fail correct
    output. `spine=None` skips the leg and SAYS so in the meta.

    Card edges are frame-quantised on both sides, so a played word
    within `edge_spill_seconds` of a card boundary counts as the
    neighbour card's spill rather than a missing or extra word.

    `suppressed` are played-word entries (the same objects as in
    `played`) an active recorded display suppression hides from
    captions (`transcript_corrections.suppressions`, the store the
    caption planner enforces per block in step 4.01). The planner
    drops them from what is drawn while the audio still plays them,
    so the identity diff skips them the way the planner did - while
    a card over only suppressed words is still a card over speech
    (the silence leg below reads unfiltered speech). The uncovered
    computation skips them too: demanding a card where the record
    says draw nothing would caption against the captain's recorded
    decision (measured 2026-09-20 on Reel 10, where the globally
    suppressed "um" strands in a card hole, lc-0049). Every skip is
    a warning finding naming the words and the suppression ids,
    never silent; without it the gate fails captions that obey a
    recorded correction (AGENTS.md 10.4).

    Returns `{findings, meta}`. `meta` carries the counts plus whether
    each leg ran - a decision to leave the spine out appears here, not
    in silence.
    """
    findings: list = []
    played = sorted(played or [],
                    key=lambda w: (w["reel_start"], w["reel_end"]))
    suppressed_ids = {id(word) for word in suppressed or []}
    captioned = sorted(captioned or [],
                       key=lambda w: (w["reel_start"], w["reel_end"]))
    cards = sorted(cards or [],
                   key=lambda c: (c["reel_start"], c["reel_end"]))

    # ── Empty karaoke windows, read off the artefact itself ──
    #
    # A caption word with no sweepable time on screen (end <= start)
    # is drawn UNSWEPT, never highlighted: the renderer draws every
    # entry in `words` and its accent phase needs a window with width
    # (`generate_remotion_props`), so a word the card starts after, or
    # a pile-up stamp of zero width, arrives as startFrame >= endFrame
    # and the sweep travels past it. No transcript needed - the props
    # contradict the sweep - so this fires even where the words match.
    # Measured: Reel 05's "10-man"/"50-person"/"shop," (pile-up stamps)
    # and Reel 12's "twenty" (12 -> 6: the word ends before its card
    # begins, so the clamp inverts it). A warning, never an error: the
    # word IS on screen while its card plays (omitting it deleted Reel
    # 05's numbers from the captions entirely), only the highlight
    # skips it - the same dust class as `untimed_drawn` below.
    for word in captioned:
        if word["reel_end"] <= word["reel_start"]:
            findings.append({
                "kind": "empty_window",
                "severity": "warning",
                "message": (
                    f"caption {word.get('card', '?')[:48]} draws "
                    f"{word['word']!r} with no highlight window "
                    f"(reel {word['reel_start']:.2f}-{word['reel_end']:.2f}s) "
                    f"- the word is on screen, karaoke skips it"),
                "detail": {
                    "card": word.get("card"),
                    "word": word["word"],
                    "reel_start": round(word["reel_start"], 3),
                    "reel_end": round(word["reel_end"], 3),
                },
            })

    # ── Which played words does any card cover ──
    def covering_card(t: float) -> Optional[dict]:
        for card in cards:
            if _covers(card["reel_start"], card["reel_end"], t):
                return card
        return None

    uncovered: list = []
    text_spilled: list = []
    card_norms: list = []
    for card in cards:
        card_norms.append((
            card,
            {w["norm"] for w in captioned
             if w.get("card") == card["card"]},
            set(card.get("text_norms") or [])))
    for word in played:
        if id(word) in suppressed_ids:
            # Recorded decision, not caption content: the planner hid
            # this token and the "suppressed" warning below names it
            # with its suppression id. Coverage demands a card over
            # speech the reel wants captioned; demanding one here
            # would draw what the record says not to.
            continue
        if covering_card(_midpoint(word)) is None:
            # A word straddling a card edge is placement arithmetic,
            # not a lost word - but only where the neighbour card
            # actually carries it. Proximity alone would wave through
            # a word the next card drops (measured: Reel 29's "If" at
            # 50.05s, one frame past the previous card's end, missing
            # from the card starting at 50.18s).
            #
            # A pile-up word (aligner timings contradicting their
            # neighbours - `degenerate_indices`) carries no position
            # to cover, so the timed norms cannot carry it even where
            # the card draws it: the RENDERED TEXT is consulted instead
            # (measured 2026-09-20 on Reel 27: 'five-star' stamped at
            # 20ms inside 'reviews,', drawn untimed on the card reading
            # "5-star reviews, AI does see that."). Timed words keep
            # the strict rule - a cleanly timed word the planner drew
            # but mistimed is a real defect, not dust.
            spill = False
            for card, norms, text_norms in card_norms:
                if word["norm"] in norms:
                    drawn = True
                elif (word.get("degenerate")
                        and word["norm"] in text_norms):
                    drawn = False
                else:
                    continue
                if (abs(word["reel_start"] - card["reel_end"])
                        <= edge_spill_seconds
                        or abs(word["reel_end"] - card["reel_start"])
                        <= edge_spill_seconds):
                    spill = True
                    if not drawn:
                        text_spilled.append((word, card))
                    break
            if not spill:
                uncovered.append(word)

    # Maximal runs of uncovered words: one finding per run, because a
    # run is one stretch of audio with nothing on screen. A run breaks
    # where a covered word intervenes - walking `played` in order, not
    # `uncovered`, so two gaps with captioned speech between them never
    # merge into one finding spanning the caption.
    uncovered_ids = {id(word) for word in uncovered}
    runs: list = []
    for pos, word in enumerate(played):
        if id(word) not in uncovered_ids:
            continue
        if runs and pos > 0 and played[pos - 1] is runs[-1][-1]:
            runs[-1].append(word)
        else:
            runs.append([word])
    for run in runs:
        start = run[0]["reel_start"]
        end = run[-1]["reel_end"]
        raws = [w["word"] for w in run]
        findings.append({
            "kind": "played_not_captioned",
            "severity": "error",
            "message": (
                f"{len(run)} played word(s) have no caption over them, "
                f"reel {start:.2f}-{end:.2f}s: "
                f"{' '.join(raws)[:160]}"),
            "detail": {
                "reel_start": round(start, 3),
                "reel_end": round(end, 3),
                "played": [
                    {"word": w["word"],
                     "reel_start": round(w["reel_start"], 3),
                     "reel_end": round(w["reel_end"], 3)}
                    for w in run],
            },
        })

    # A degenerate-timing word the adjacent card DRAWS but does not
    # time is placement dust the viewer never sees: the word is on
    # screen while it is spoken, a card edge away from its stamped
    # instant. Forgiven above, REPORTED here - one warning naming the
    # words and the cards, never silence.
    if text_spilled:
        seen: list = []
        seen_ids: set = set()
        for word, card in text_spilled:
            if id(word) in seen_ids:
                continue
            seen_ids.add(id(word))
            seen.append((word, card))
        findings.append({
            "kind": "degenerate_text_spill",
            "severity": "warning",
            "message": (
                f"{len(seen)} pile-up-timed played word(s) sit beside "
                f"(not under) the card drawing them, reel "
                f"{seen[0][0]['reel_start']:.2f}-"
                f"{seen[-1][0]['reel_end']:.2f}s: "
                f"{' '.join(w['word'] for w, _ in seen)[:160]}"),
            "detail": {
                "words": [
                    {"word": w["word"],
                     "reel_start": round(w["reel_start"], 3),
                     "reel_end": round(w["reel_end"], 3),
                     "card": c["card"]}
                    for w, c in seen],
            },
        })

    # ── Per-card alignment: identity inside covered spans ──
    #
    # Cards overlapping a degenerate played word (aligner pile-up -
    # positions untrustworthy) skip the identity diff: the card may be
    # exactly right while the transcript timings are not, and a diff
    # there reports the transcript's defect as the caption's. Coverage
    # above still holds those instants (speech with no card is real
    # whatever the timings say); the skip is REPORTED, never silent.
    degenerate_spans = [(w["reel_start"], w["reel_end"])
                        for w in played if w.get("degenerate")]
    skipped_cards: list = []
    untimed_drawn: list = []

    def overlaps_degenerate(card: dict) -> bool:
        return any(s0 < card["reel_end"] and s1 > card["reel_start"]
                   for s0, s1 in degenerate_spans)

    for index, card in enumerate(cards):
        if overlaps_degenerate(card):
            skipped_cards.append(card["card"])
            continue
        # Suppressed words sit out the identity diff only: the
        # planner hid them from this card by recorded correction, so
        # "missing" here is the plan, not a loss. Coverage above ran
        # on the unfiltered list, so nothing about their audibility
        # was waved through with them.
        in_card = [w for w in played
                   if id(w) not in suppressed_ids
                   and _covers(card["reel_start"], card["reel_end"],
                               _midpoint(w))]
        card_words = sorted(
            [w for w in captioned if w.get("card") == card["card"]],
            key=lambda w: (w["reel_start"], w["reel_end"]))
        if not in_card:
            # A card over silence - unless a played word merely
            # overlaps it (a long word the midpoints missed), which is
            # timing, not an unspoken caption.
            overlap = [w for w in played
                       if w["reel_end"] > card["reel_start"]
                       and w["reel_start"] < card["reel_end"]]
            if not overlap:
                findings.append({
                    "kind": "captioned_not_played",
                    "severity": "error",
                    "message": (
                        f"caption {card['card'][:48]} covers reel "
                        f"{card['reel_start']:.2f}-{card['reel_end']:.2f}s "
                        f"where nothing is played"),
                    "detail": {
                        "card": card["card"],
                        "reel_start": round(card["reel_start"], 3),
                        "reel_end": round(card["reel_end"], 3),
                        "captioned": [w["word"] for w in card_words],
                    },
                })
            continue
        if not card_words:
            continue
        matcher = difflib.SequenceMatcher(
            a=[w["norm"] for w in in_card],
            b=[w["norm"] for w in card_words],
            autojunk=False)
        neighbour_norms = set()
        for near in (cards[index - 1] if index > 0 else None,
                     cards[index + 1] if index + 1 < len(cards) else None):
            if near is None:
                continue
            neighbour_norms.update(
                w["norm"] for w in captioned
                if w.get("card") == near["card"])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            missing = in_card[i1:i2]
            extra = card_words[j1:j2]
            # Boundary spill: the "missing" word sits at the card edge
            # and the neighbour card carries it (or vice versa for an
            # "extra" word) - placement arithmetic, not a lost word.
            # Untimed-but-drawn: the planner skips a highlight window
            # with no width (`generate_remotion_props._clamp_word_window`
            # - a sub-frame stamp rounds to zero frames and no sweep can
            # cross it) while still drawing the word in the card text.
            # Measured 2026-09-20 on Reel 10: 'are' stamped at 30ms, the
            # card reads "so what's happening, what are you" but times
            # from "what" to "you". Forgiven with a warning naming the
            # word and the card - never silence - and ONLY below one
            # frame: a word long enough to sweep that the card does not
            # time is a real planner defect, never dust.
            card_text_norms = set(card.get("text_norms") or [])
            if tag == "delete":
                real = []
                for w in missing:
                    if (w["norm"] in neighbour_norms
                            and (abs(w["reel_start"] - card["reel_start"])
                                 <= edge_spill_seconds
                                 or abs(w["reel_end"] - card["reel_end"])
                                 <= edge_spill_seconds)):
                        continue
                    if (w["norm"] in card_text_norms
                            and 0 < w["reel_end"] - w["reel_start"]
                            < frame_seconds):
                        untimed_drawn.append((w, card))
                        continue
                    real.append(w)
                if real:
                    raws = [w["word"] for w in real]
                    findings.append({
                        "kind": "word_mismatch",
                        "severity": "error",
                        "message": (
                            f"caption {card['card'][:48]} drops "
                            f"{len(real)} played word(s) near reel "
                            f"{real[0]['reel_start']:.2f}s: "
                            f"{' '.join(raws)[:160]}"),
                        "detail": {
                            "card": card["card"],
                            "dropped": [
                                {"word": w["word"],
                                 "reel_start": round(w["reel_start"], 3),
                                 "reel_end": round(w["reel_end"], 3)}
                                for w in real],
                            "card_text": " ".join(
                                w["word"] for w in card_words)[:200],
                        },
                    })
            elif tag == "insert":
                real = [
                    w for w in extra
                    if w["norm"] not in
                    {p["norm"] for p in played
                     if abs(p["reel_start"] - card["reel_start"])
                     <= edge_spill_seconds
                     or abs(p["reel_end"] - card["reel_end"])
                     <= edge_spill_seconds}]
                if real:
                    raws = [w["word"] for w in real]
                    findings.append({
                        "kind": "word_mismatch",
                        "severity": "error",
                        "message": (
                            f"caption {card['card'][:48]} adds "
                            f"{len(real)} word(s) never played near reel "
                            f"{real[0]['reel_start']:.2f}s: "
                            f"{' '.join(raws)[:160]}"),
                        "detail": {
                            "card": card["card"],
                            "added": [
                                {"word": w["word"],
                                 "reel_start": round(w["reel_start"], 3),
                                 "reel_end": round(w["reel_end"], 3)}
                                for w in real],
                        },
                    })
            else:  # replace - a substitution inside a covered span
                findings.append({
                    "kind": "word_mismatch",
                    "severity": "error",
                    "message": (
                        f"caption {card['card'][:48]} says "
                        f"{' '.join(w['word'] for w in extra)[:120]!r} "
                        f"where the reel plays "
                        f"{' '.join(w['word'] for w in missing)[:120]!r} "
                        f"(reel {missing[0]['reel_start']:.2f}s)"),
                    "detail": {
                        "card": card["card"],
                        "played": [
                            {"word": w["word"],
                             "reel_start": round(w["reel_start"], 3)}
                            for w in missing],
                        "captioned": [
                            {"word": w["word"],
                             "reel_start": round(w["reel_start"], 3)}
                            for w in extra],
                    },
                })

    # ── Suppressed words, said not waved ──
    #
    # Entries the identity diff skipped above. Each names the recorded
    # Drawn but untimed: sub-frame played words the planner's karaoke
    # cannot sweep and the card text still shows. The viewer reads
    # them while they are spoken; the highlight skips them. Forgiven
    # above, REPORTED here - one warning naming the words and cards,
    # never silence.
    if untimed_drawn:
        seen: list = []
        seen_ids: set = set()
        for word, card in untimed_drawn:
            if id(word) in seen_ids:
                continue
            seen_ids.add(id(word))
            seen.append((word, card))
        findings.append({
            "kind": "untimed_drawn",
            "severity": "warning",
            "message": (
                f"{len(seen)} sub-frame played word(s) are drawn on "
                f"their card but carry no highlight window, reel "
                f"{seen[0][0]['reel_start']:.2f}-"
                f"{seen[-1][0]['reel_end']:.2f}s: "
                f"{' '.join(w['word'] for w, _ in seen)[:160]}"),
            "detail": {
                "words": [
                    {"word": w["word"],
                     "reel_start": round(w["reel_start"], 3),
                     "reel_end": round(w["reel_end"], 3),
                     "card": c["card"]}
                    for w, c in seen],
            },
        })

    # suppression that hid it, so a suppression that stopped matching
    # (or one recorded against the wrong anchor) reads as a warning
    # carrying its id rather than as silence.
    suppressed_words = [w for w in played if id(w) in suppressed_ids]
    if suppressed_words:
        raws = [w["word"] for w in suppressed_words]
        ids = sorted({str(w.get("suppression") or "?")
                      for w in suppressed_words})
        findings.append({
            "kind": "suppressed",
            "severity": "warning",
            "message": (
                f"{len(suppressed_words)} played word(s) hidden from "
                f"captions by recorded display suppression(s) "
                f"({', '.join(ids)}) - the audio plays them by design: "
                f"{' '.join(raws)[:160]}"),
            "detail": {
                "words": [
                    {"word": w["word"],
                     "reel_start": round(w["reel_start"], 3),
                     "reel_end": round(w["reel_end"], 3),
                     "suppression": str(w.get("suppression") or "")}
                    for w in suppressed_words],
            },
        })

    # ── The spine leg: what the proposal says belongs there ──
    meta = {
        "played_words": len(played),
        "captioned_words": len(captioned),
        "cards": len(cards),
        "suppressed_words": len(suppressed_words),
        # An empty spine is no spine: with no proposal spans every
        # played word would read as "added by the build".
        "spine_compared": bool(spine),
        "degenerate_words": sum(1 for w in played if w.get("degenerate")),
        "identity_skipped_cards": len(skipped_cards),
    }
    if skipped_cards:
        findings.append({
            "kind": "degenerate_timing",
            "severity": "warning",
            "message": (
                f"word identity not verified on {len(skipped_cards)} "
                f"card(s): transcript timings pile up there, so a diff "
                f"would report the transcript's defect as the caption's "
                f"(coverage above still holds)"),
            "detail": {"cards": [c[:64] for c in skipped_cards[:10]],
                       "total": len(skipped_cards)},
        })
    if spine:
        spine_norms = [w["norm"] for w in spine]
        played_norms = [w["norm"] for w in played]
        matcher = difflib.SequenceMatcher(a=spine_norms, b=played_norms,
                                          autojunk=False)
        dropped_by_build = 0
        added_by_build = 0
        examples: list = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag in ("delete", "replace"):
                dropped_by_build += i2 - i1
            if tag in ("insert", "replace"):
                added_by_build += j2 - j1
            if len(examples) < 5:
                examples.append({
                    "tag": tag,
                    "spine": " ".join(
                        w["word"] for w in spine[i1:i2])[:100],
                    "played": " ".join(
                        w["word"] for w in played[j1:j2])[:100],
                })
        meta.update({
            "spine_words": len(spine_norms),
            "spine_dropped_by_build": dropped_by_build,
            "spine_added_by_build": added_by_build,
        })
        if dropped_by_build or added_by_build:
            findings.append({
                "kind": "spine_drift",
                "severity": "info",
                "message": (
                    f"reel plays {added_by_build} word(s) outside the "
                    f"proposal and drops {dropped_by_build} it planned - "
                    f"the build's doing (retakes, re-cuts), not the "
                    f"captions'"),
                "detail": {
                    "dropped_by_build": dropped_by_build,
                    "added_by_build": added_by_build,
                    "examples": examples,
                },
            })
    return {"findings": findings, "meta": meta}


def transcript_currency(
    transcript: dict,
    master_fps: float,
    master_duration_seconds: float,
    master_picture_holes: Optional[Sequence[Sequence[float]]] = None,
) -> dict:
    """Whether the cached transcript describes THIS master timeline.

    The transcript file is written only by the transcribe pass, never
    by a reel build, so a re-cut master makes a re-cut reel look
    correct against old timings. The transcript's own `derived_from`
    (fps, duration, picture holes) is compared against the live
    master; any disagreement means the played-words basis is stale.

    Returns `{current, mismatches}` - mismatches name the field, the
    cached value and the live one. Duration agrees within 0.1s (four
    frames of head/tail silence either side is not a re-cut); holes
    agree rounded to the millisecond.
    """
    mismatches: list = []
    derived = (transcript or {}).get("derived_from") or {}

    cached_fps = derived.get("fps")
    if cached_fps is not None and abs(float(cached_fps) - master_fps) > 1e-9:
        mismatches.append({"field": "fps", "cached": cached_fps,
                           "live": master_fps})
    cached_dur = derived.get("duration_seconds")
    if cached_dur is not None and abs(float(cached_dur)
                                      - master_duration_seconds) > 0.1:
        mismatches.append({"field": "duration_seconds", "cached": cached_dur,
                           "live": master_duration_seconds})
    if master_picture_holes is not None:
        cached_holes = [[round(float(x), 3) for x in hole]
                        for hole in (derived.get("picture_holes") or [])]
        live_holes = [[round(float(x), 3) for x in hole]
                      for hole in master_picture_holes]
        if cached_holes != live_holes:
            mismatches.append({"field": "picture_holes",
                               "cached": cached_holes, "live": live_holes})
    return {"current": not mismatches, "mismatches": mismatches}


# ── Offline diagnosis: cached snapshots, no Resolve, no pipeline run ──

def _frames(value, fps: float) -> float:
    return float(value) / fps


def diagnose_reel(snapshot: dict,
                  transcript: dict,
                  props_dir: str,
                  source_spans: Optional[Sequence[dict]] = None,
                  max_word_seconds: float = DEFAULT_MAX_WORD_SECONDS,
                  ) -> dict:
    """Run the whole check off frozen artefacts.

    `snapshot` is a timeline snapshot in the review-JSON shape
    (`tracks[].clips[]` with `record_in`/`record_out`,
    `source_in`/`source_out` in frames, `file_path`, `name`) or the
    marker-feedback shape (`timeline_start`/`timeline_end`,
    `source_start`/`source_end`). Played words come from the reel's
    AUDIO items (falling back to V1/V2 picture where a snapshot has
    no audio); captioned words from the placed caption row ("Subtitles"
    / "Captions" by name, V4 by index); the spine from
    `source_spans` where given.
    """
    meta = snapshot.get("metadata", {}) or {}
    fps = float(meta.get("fps") or 24000 / 1001)
    reel_name = meta.get("name", "?")

    audio_spans: list = []
    picture_spans: list = []
    caption_cards: list = []
    for track in snapshot.get("tracks", []) or []:
        row = (track.get("name") or "").strip()
        for clip in track.get("clips", []) or []:
            path = clip.get("file_path") or ""
            rec_in = clip.get("record_in", clip.get("timeline_start"))
            rec_out = clip.get("record_out", clip.get("timeline_end"))
            src_in = clip.get("source_in", clip.get("source_start"))
            src_out = clip.get("source_out", clip.get("source_end"))
            if rec_in is None or rec_out is None:
                continue
            if track.get("type") == "audio":
                if src_in is not None and path:
                    audio_spans.append({
                        "source_file": path,
                        "source_start": _frames(src_in, fps),
                        "source_end": _frames(src_out, fps),
                        "reel_start": _frames(rec_in, fps),
                    })
            elif track.get("type") == "video" and track.get("index") in (1, 2):
                if src_in is not None and path and "freeze" not in (path or ""):
                    picture_spans.append({
                        "source_file": path,
                        "source_start": _frames(src_in, fps),
                        "source_end": _frames(src_out, fps),
                        "reel_start": _frames(rec_in, fps),
                    })
            is_caption_row = row in ("Subtitles", "Captions") or (
                not row and track.get("type") == "video"
                and track.get("index") == 4)
            if track.get("type") == "video" and is_caption_row:
                left = clip.get("left_offset", clip.get("source_in",
                                 clip.get("source_start", 0)))
                caption_cards.append({
                    "clip_path": path or clip.get("name", ""),
                    "reel_start_frame": rec_in,
                    "reel_end_frame": rec_out,
                    "source_in_frame": left or 0,
                })

    played_basis = audio_spans or picture_spans
    played = played_words_from_transcript(
        (transcript or {}).get("segments", []), played_basis,
        max_word_seconds=max_word_seconds)
    captioned = captioned_words_from_placed_cards(
        caption_cards, props_dir, fps)
    spine = (spine_words_from_spans((transcript or {}).get("segments", []),
                                    source_spans)
             if source_spans is not None else None)

    # Read space, like the gate: raw transcript against read captions
    # fails correct output (numerals, acronyms). No project here, so no
    # recorded spellings - the diagnose names that where it matters.
    read_played = read_words_for_comparison(played["words"])
    read_spine = (read_words_for_comparison(spine["words"])
                  if spine is not None else None)
    result = check_word_coverage(
        read_played, captioned["words"], captioned["cards"],
        spine=(read_spine if spine is not None else None))

    findings = list(result["findings"])
    for entry in captioned["unreadable"]:
        findings.append({
            "kind": "unreadable",
            "severity": "error",
            "message": (f"placed caption {entry['card'][:48]} cannot be "
                        f"verified: {entry['reason']}"),
            "detail": entry,
        })
    for entry in played["undetermined"]:
        findings.append({
            "kind": "undetermined",
            "severity": "warning",
            "message": (f"{entry.get('word_count', '?')} transcript word(s) "
                        f"cannot be mapped onto the reel "
                        f"({entry.get('reason')}): "
                        f"{entry.get('text', '')[:100]}"),
            "detail": entry,
        })
    if played["stretched"]:
        total = sum(s.get("span_seconds", 0) for s in played["stretched"])
        findings.append({
            "kind": "stretched",
            "severity": "warning",
            "message": (f"{len(played['stretched'])} aligner-stretched word(s) "
                        f"({total:.1f}s) excluded from the played measure"),
            "detail": {"words": played["stretched"][:10]},
        })
    if played["degenerate_rows"]:
        total = sum(r.get("degenerate_count", 0)
                    for r in played["degenerate_rows"])
        findings.append({
            "kind": "degenerate_rows",
            "severity": "warning",
            "message": (f"{total} word(s) in "
                        f"{len(played['degenerate_rows'])} transcript row(s) "
                        f"carry pile-up timings and place nowhere - "
                        f"identity skipped where they fall"),
            "detail": {"rows": played["degenerate_rows"][:10]},
        })
    if played.get("slivers"):
        findings.append({
            "kind": "slivers",
            "severity": "warning",
            "message": (f"{len(played['slivers'])} word(s) touch a placed "
                        f"span by less than a captionable span and are "
                        f"not counted as played"),
            "detail": {"words": played["slivers"][:10]},
        })
    if played.get("serialized"):
        findings.append({
            "kind": "serialized",
            "severity": "warning",
            "message": (f"{len(played['serialized'])} same-file transcript "
                        f"overlap(s) read sequential"),
            "detail": {"words": played["serialized"][:10]},
        })

    result["findings"] = findings
    result["reel"] = reel_name
    result["played_basis"] = "audio" if audio_spans else "picture"
    return result


def main(argv=None) -> int:
    """Offline diagnosis: `played == captioned == spine` for one reel."""
    parser = argparse.ArgumentParser(
        description="Word-level subtitle coverage for one built reel, "
                    "off cached snapshots (no Resolve, no pipeline run).")
    parser.add_argument("--snapshot", required=True,
                        help="reel timeline snapshot JSON (review shape)")
    parser.add_argument("--transcript", required=True,
                        help="cached timeline transcript JSON")
    parser.add_argument("--props-dir", required=True,
                        help="rendered subtitle artefacts (props + mov)")
    parser.add_argument("--proposal", default="",
                        help="reel proposals JSON (for the spine leg)")
    parser.add_argument("--reel-number", type=int, default=0,
                        help="reel number selecting the proposal moment")
    args = parser.parse_args(argv)

    with open(args.snapshot, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    with open(args.transcript, encoding="utf-8") as handle:
        transcript = json.load(handle)
    source_spans = None
    if args.proposal and args.reel_number:
        with open(args.proposal, encoding="utf-8") as handle:
            proposal = json.load(handle)
        moments = proposal.get("moments", proposal
                               if isinstance(proposal, list) else [])
        for moment in moments:
            if moment.get("number") == args.reel_number:
                source_spans = moment.get("source_spans")
                break

    result = diagnose_reel(snapshot, transcript, args.props_dir,
                           source_spans=source_spans)
    print(f"reel: {result['reel']} "
          f"(played basis: {result['played_basis']})")
    print(f"meta: {json.dumps(result['meta'])}")
    errors = [f for f in result["findings"] if f["severity"] == "error"]
    for finding in result["findings"]:
        print(f"[{finding['severity'].upper()}] "
              f"{finding['kind']}: {finding['message']}")
    print(f"{len(errors)} error(s), "
          f"{len(result['findings']) - len(errors)} other(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
