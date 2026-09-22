"""What a built reel SAYS, against what it was planned to say.

The gap this closes
-------------------
`render_qa` measures the render for black frames, loudness, freezes and
letterbox. `manifest_validator` checks the plan against itself.
`reel_conformance_verifier` grades format, item count, picture holes and
caption timing. `render_watch` shows a model the PICTURE.

**Nothing compared what the render is HEARD to say against what the plan
says it says**, and that gap has exactly one shape: an edit whose audio
and whose word-timed decisions - captions, karaoke highlights, take
boundaries - disagree, in a file that has already shipped.

Reel 26 of the captain's `geo-podcast` is the known-answer case and it
is pinned in `tests/test_reel_hearing.py`. One WhisperX row carried
twelve words in 920 milliseconds with `words: []`, and the delivered mp4
therefore contains six words with no caption at all, a caption card a
full second late and a karaoke highlight on the wrong word. Every one of
those is invisible to every check above and all three fall out of this
one deterministically.

Why it is affordable now, and not before
----------------------------------------
Hearing a 45.9-second reel costs **3.5 seconds** of wall clock through
`library/tools/heard_speech.py`. The same pass through this project's
own WhisperX rate is 33-47 minutes for a 31-reel episode, which is
precisely why nobody built it. The arithmetic, not a preference, is what
changed.

Two halves, and the first needs no model
----------------------------------------
**Deterministic**: transcribe the render, resolve the plan's own words
through the timeline transcript, align, diff, measure drift, measure
caption coverage. That half found all three Reel 26 consequences on its
own and it is what this module is.

**Judgement**: which of the divergences matter. That is a model's job,
it is roughly 1,600 tokens, and it is an ADDITION - `library/skills/
hear_the_reel/` hands a model this module's output and asks. Nothing
here calls a model and nothing here needs one.

It REPORTS. It gates nothing.
-----------------------------
No build reads this record, no step declares it, and `passed: false` on
a row here fails nothing. That is deliberate and it is the conservative
direction: a new gate that blocks builds is the hard-to-reverse move,
and the right order is to prove this on real episodes and then ask the
captain to promote it. `tests/test_reel_hearing.py` pins that no gate
reads the record.

`passed` is still the check's own honest verdict rather than a constant
true, because a row that says "clean" when it measured a defect is the
gate-that-cannot-fail turned inside out (AGENTS.md 10.4). The rows carry
real verdicts and nothing acts on them; which of those two facts changes
is the captain's call, and it is one boolean in
`library/tools/reel_hearing.GATES`.

Normalisation, and why it is not optional
-----------------------------------------
The planned side has already had this project's filed spelling
corrections applied to it (`transcript_corrections.apply_to_document`
rewrote `lucy` to `Lucie` 28 times on the run of record). The heard side
has not. Diffing them raw reports a false `Lucie` -> `Lucy` on EVERY
reel this project builds, which is a tool crying wolf on its first real
run. So the heard side goes through `transcript_corrections.apply_
spelling` with the same store before anything is compared, and the
recorded Reel 26 divergence count drops from one substitution to zero.

Nothing else is forgiven. Case, punctuation and curly apostrophes are
folded, because those are spellings of the same word; `gonna` against
`going to`, a spelled-out number against a digit and a filler word one
transcriber caught are all REPORTED. A normaliser that silently forgives
a real divergence is worse than a noisy one, because the noise is
readable and the silence is not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

GATES = False
"""Whether a finding here may fail anything. One boolean, and it is the
captain's to flip. See the module docstring."""

RECORD_SUFFIX = ".hearing.json"
"""Where the record lands: beside the render, next to `.deliver.json`."""


# ── The measured bounds, and which of them are DIALS ─────────────────
#
# MECHANICAL values, not creative floors (AGENTS.md 10.5): each is a
# property of the measuring instruments, and each was measured on this
# project's own footage before it was written down.
#
# These three are the DEFAULTS, and `library/tools/hearing_settings.py`
# is where a project or a run may move one and where the split between
# a measured property and a real dial is drawn.  It also states why
# `decided_value`'s ladder does not reach any of them.  Every number
# below is still spelled exactly once - `hearing_settings.DIALS` reads
# these as its own defaults, so there is no second copy to drift.

DRIFT_NOISE_FLOOR_SECONDS = 0.25
"""Below this, a drift is the two transcribers disagreeing.

Measured 2026-09-16 over 6,983 words of this project's own audio: the
on-device transcriber's word starts sit **94.1 ms mean absolute** from
WhisperX's forced-alignment boundaries with a **134.0 ms** standard
deviation, and the bias is small against that spread (-35 ms), so
subtracting a constant removes about a quarter of it and leaves the
rest. A quarter of a second is a little over one standard deviation
above the mean disagreement: under it there is nothing an edit could be
blamed for. The Reel 26 defect is **1.01 seconds**, four times this.
"""

DRIFT_RUN_MIN_WORDS = 3
"""How many consecutive words must drift the same way to be a finding.

A single word past the floor is one transcriber hearing an onset
differently. A RUN of consecutive words all late, or all early, is the
picture of a passage sitting somewhere the plan does not think it is -
which is what a misplaced caption, a mis-anchored passage or a
hallucinated transcript row all look like from here. Reel 26's run is
nine words long.
"""

CAPTION_HALF_COVERED = 0.5
"""A word this much covered by a caption card is captioned enough.

Below it the viewer sees the card arrive or leave mid-word. Zero
coverage - no card on screen at all while the word is spoken - is
counted and reported separately, because it is a different defect from a
card that is merely late.
"""

CAPTION_PAIRING_TOLERANCE_SECONDS = 0.5
"""How far a caption card may sit from the reel-time window of the
source span its own filename declares before the pairing is reported.

A tolerance, not a dial (`library/tools/hearing_settings.py`): the
comparison's own noise is frame quantization plus the millisecond
rounding in the filename, and the measured-good reel sits an order of
magnitude inside this - 13 of 13 cards within one 23.976fps frame of
their declared span, worst 43 ms, mean 15 ms (PR 1178, which measured
this check and did not build it). Any genuine mispairing - the wrong
file in a slot, or the right file at the wrong time - displaces the
card by a card length or more, which is seconds. Half a second is
twelve frames: ten times the worst good measurement and far below any
real defect.
"""

PAIRING_WORD_EDGE_SECONDS = 0.005
"""How far outside a card's declared source span a word's source time
may fall and still count as the card's own speech.

The span token in the filename rounds to milliseconds and word
boundaries are floats, so a word starting exactly on the span's edge
can read a hair outside it. Five milliseconds admits float dust and
nothing else: a word genuinely outside the span belongs to another
card, and widening this would smear the expected window toward it.
"""


# ── Tokens ───────────────────────────────────────────────────────────

_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'"})
_NOT_A_WORD = re.compile(r"[^a-z0-9']+")


def normalise(word: str) -> str:
    """One word, folded to what two transcribers can be asked to agree on.

    Case, surrounding punctuation and the apostrophe's several code
    points. Nothing else - see the module docstring on why forgiving
    more would make this quieter and less true.
    """
    folded = unicodedata.normalize("NFKC", str(word or "")).translate(
        _APOSTROPHES).lower()
    return _NOT_A_WORD.sub("", folded)


@dataclass(frozen=True)
class Word:
    """One word, placed in REEL time."""

    word: str
    token: str
    start: float
    end: float
    speaker: Optional[str] = None


@dataclass(frozen=True)
class Span:
    """One planned stretch of speech audio, in reel time and source time."""

    file_path: str
    track: str
    reel_in: float
    reel_out: float
    source_in: float
    source_out: float


# ── The plan side ────────────────────────────────────────────────────

def _fps(timeline: Dict[str, Any]) -> float:
    """The timeline's own frame rate.

    Indexed, never defaulted: every reel-time number below is this
    division, and a guessed rate silently scales every finding
    (AGENTS.md 10.1).
    """
    rate = float(((timeline.get("metadata") or {}).get("fps")) or 0.0)
    if rate <= 0:
        raise ValueError(
            "the serialized timeline records no frame rate, so no clip "
            "can be placed in reel time. metadata.fps is written by "
            "library/tools/timeline_serializer.py.")
    return rate


def speech_spans(timeline: Dict[str, Any],
                 transcript: Dict[str, Any]) -> List[Span]:
    """The audio clips carrying SPEECH, in reel time.

    A speech clip is one whose source file the timeline transcript has
    words for - MEASURED, never read off a track name. Music and SFX
    rows sit on audio tracks too and a name-matching rule would have to
    be kept in step with `timeline_layout`'s naming; a file the
    transcriber never heard speech in cannot be the speech row.
    """
    fps = _fps(timeline)
    heard_in = {segment.get("source_file")
                for segment in (transcript.get("segments") or [])
                if segment.get("source_file")}
    spans: List[Span] = []
    for track in timeline.get("tracks") or []:
        if track.get("type") != "audio":
            continue
        for clip in track.get("clips") or []:
            path = clip.get("file_path")
            if path not in heard_in:
                continue
            played = clip["record_out"] - clip["record_in"]
            spans.append(Span(
                file_path=path,
                track=str(track.get("name") or ""),
                reel_in=clip["record_in"] / fps,
                reel_out=clip["record_out"] / fps,
                source_in=clip["source_in"] / fps,
                source_out=(clip["source_in"] + played) / fps,
            ))
    spans.sort(key=lambda s: s.reel_in)
    return spans


#: How far before a span's own start a word may begin and still be
#: counted as inside it: a word the cut starts a hair inside is a word
#: the reel plays, and dropping it would report the reel as saying a
#: word less than it does.
#:
#: Half a frame at 24fps, and deliberately sub-frame. The comment here
#: used to call 20ms "one 24fps frame", which it is not - a frame at
#: 23.976 is 41.7ms. The VALUE is unchanged and only the description of
#: it is corrected: widening the window to a full frame would admit
#: words the neighbouring clip plays, and a tolerance is not a dial
#: (`library/tools/hearing_settings.py`).
SPAN_LEAD_TOLERANCE_SECONDS = 0.02


def _sourced_words(timeline: Dict[str, Any],
                    transcript: Dict[str, Any],
                    spans: Sequence[Span]
                    ) -> List[Tuple[str, float, float, float, str, str,
                                    Optional[str]]]:
    """Every timed word, in BOTH timebases at once.

    One row per word: `(source_file, source_time, reel_start, reel_end,
    word, token, speaker)`. `planned_words` and the caption-pairing
    check walk the same clips and rows through this one function, so
    the two can never disagree about where a word plays - a second
    spelling of the walk is how a check passes a word the plan does
    not carry.

    Keys are indexed rather than `.get`-defaulted: the timeline
    transcript's contract promises every one of them, and a rename must
    fail loudly (AGENTS.md 10.1).
    """
    segments = transcript.get("segments") or []
    out: List[Tuple[str, float, float, float, str, str, Optional[str]]] = []
    for span in spans:
        for segment in segments:
            if segment.get("source_file") != span.file_path:
                continue
            # Every segment carries both timebases, so the offset is
            # exact per row rather than assumed constant.
            offset = segment["source_start"] - segment["timeline_start"]
            for row in segment.get("words") or []:
                start = row["start"] + offset
                end = row["end"] + offset
                if start < span.source_in - SPAN_LEAD_TOLERANCE_SECONDS:
                    continue
                if start > span.source_out:
                    continue
                token = normalise(row["word"])
                if not token:
                    continue
                out.append((span.file_path, start,
                            start - span.source_in + span.reel_in,
                            end - span.source_in + span.reel_in,
                            row["word"], token,
                            segment.get("speaker")))
    return out


def planned_words(timeline: Dict[str, Any],
                   transcript: Dict[str, Any]) -> Tuple[List[Word], List[Span]]:
    """What the PLAN says this reel says, in reel time.

    The plan's words are the timeline transcript's words - the same
    document every timing decision in this reel was made from - windowed
    to each placed clip's own source range and mapped onto reel time by
    that clip's placement. So this is not a second opinion about the
    speech: it is the pipeline's own opinion, moved into the frame the
    render can be heard in.
    """
    spans = speech_spans(timeline, transcript)
    words = [Word(word=word, token=token, start=reel_start, end=reel_end,
                  speaker=speaker)
             for _, _, reel_start, reel_end, word, token, speaker
             in _sourced_words(timeline, transcript, spans)]
    words.sort(key=lambda w: (w.start, w.end))
    return words, spans


def unfitted_rows_played(timeline: Dict[str, Any],
                         transcript: Dict[str, Any],
                         spans: Optional[Sequence[Span]] = None
                         ) -> List[Dict[str, Any]]:
    """Transcript rows this reel plays whose text did not fit its audio.

    The measurement is `library/tools/transcript_fit.py`'s and is not
    repeated here: a row is unfitted when fewer of its words carry
    timings than its text has words, which is a count against a count
    with no threshold in it.

    This used to be `wordless_rows` and reported only the rows that lost
    EVERY word, as a note beside the findings. It is a FINDING now, and
    it catches the rows that lost only PART of themselves too - both
    halves of the class the hybrid-alignment report measured and named
    as already-detected-and-unread.
    """
    from library.tools import transcript_fit

    spans = speech_spans(timeline, transcript) if spans is None else spans
    return transcript_fit.rows_played(timeline, transcript, spans)


# ── The caption side ─────────────────────────────────────────────────

#: A rendered caption filename, split into what it declares. Speaker
#: and clip slugs never contain `_` and the span is `nospan` or
#: `<ms>-<ms>`, so a `sub_` id that `is_segment_id` accepts is exactly
#: these five parts - the same shape
#: `library/tools/subtitle_segment_id.py` documents, parsed here rather
#: than there because the pairing check needs the DECLARED span and the
#: naming module's contract is naming, not parsing.
_CARD_ID_RE = re.compile(
    r"^sub_([^_]+)_([^_]+)_(\d+-\d+|nospan)_([0-9a-f]+)$")


def caption_cards(timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every rendered caption card on the timeline, and what its own
    filename declares.

    A card is identified by its FILENAME, through the naming contract
    `library/tools/subtitle_segment_id.py` owns - not by which track it
    sits on. A card placed on the wrong row is still a card the viewer
    reads, and both coverage and pairing are questions about the
    picture.

    `source_start`/`source_end` are the declared source span in
    seconds, or None when the filename declares `nospan`: a file that
    says nothing about its speech cannot be paired with any, and the
    pairing check records that openly rather than guessing.
    """
    from library.tools import subtitle_segment_id

    fps = _fps(timeline)
    cards: List[Dict[str, Any]] = []
    for track in timeline.get("tracks") or []:
        if track.get("type") != "video":
            continue
        for clip in track.get("clips") or []:
            name = clip.get("file_path") or clip.get("name") or ""
            stem = os.path.splitext(os.path.basename(str(name)))[0]
            if not subtitle_segment_id.is_segment_id(stem):
                continue
            match = _CARD_ID_RE.match(stem)
            declared: Tuple[Optional[float], Optional[float]] = (None, None)
            speaker, slug = None, None
            if match is not None:
                speaker, slug = match.group(1), match.group(2)
                if match.group(3) != "nospan":
                    start_ms, end_ms = match.group(3).split("-")
                    declared = (int(start_ms) / 1000.0,
                                int(end_ms) / 1000.0)
            cards.append({
                "clip_name": str(name),
                "stem": stem,
                "speaker": speaker,
                "clip": slug,
                "source_start": declared[0],
                "source_end": declared[1],
                "reel_in": clip["record_in"] / fps,
                "reel_out": clip["record_out"] / fps,
            })
    cards.sort(key=lambda c: (c["reel_in"], c["reel_out"]))
    return cards


def caption_windows(timeline: Dict[str, Any]) -> List[Tuple[float, float]]:
    """When a rendered caption card is on screen, in reel time.

    The card's own windows, without what each card declares - which is
    what the coverage check reads. The pairing check reads
    `caption_cards` instead.
    """
    return [(card["reel_in"], card["reel_out"])
            for card in caption_cards(timeline)]


def pairing_rows(timeline: Dict[str, Any],
                 transcript: Dict[str, Any],
                 spans: Optional[Sequence[Span]] = None
                 ) -> Dict[str, Any]:
    """One row per caption card: what its file declares, and where that
    speech actually plays on this reel.

    The declared source span comes out of the card's own FILENAME - the
    provenance stem `subtitle_segment_id` roots every render in - and
    the reel-time window it should sit over is measured, never assumed:
    the transcript segments overlapping that span say which source file
    it is, and the placed audio clips say when that file plays. No
    render, no audio, no model: arithmetic over a filename and a clip
    placement, which is what closes the "wrong-but-plausible pairing is
    invisible" the naming module's own docstring names.

    Three ways a card fails, each a different wrong pairing:

    - `span_unplayed`: the declared span overlaps no transcript segment
      at all - the file claims speech this reel's transcript never
      plays.
    - `speaker_mismatch`: segments overlap the span but none speaks as
      the card's own speaker slug - one speaker's caption over another
      speaker's audio.
    - `displaced`: the card sits further than
      `CAPTION_PAIRING_TOLERANCE_SECONDS` from the reel-time window of
      its own declared words - the right file at the wrong time, or
      the wrong file in the slot.

    A card the check cannot establish - a `nospan` filename, or a
    declared span under which the transcript carries no timed words -
    is recorded in `unestablished`, never as a finding. The second is
    the transcript-row-fit cause wearing another hat, and
    `transcript_row_fit` already owns it: reporting it here too would
    be one defect with two owners. An absent verdict is not a verdict
    of fine, but neither is an unmeasurable one a verdict of guilt.
    """
    from library.tools import subtitle_segment_id

    if spans is None:
        spans = speech_spans(timeline, transcript)
    cards = caption_cards(timeline)
    segments = transcript.get("segments") or []
    sourced = _sourced_words(timeline, transcript, spans)

    mispaired: List[Dict[str, Any]] = []
    unestablished: List[Dict[str, Any]] = []
    edge_offsets: List[float] = []
    for card in cards:
        source_start, source_end = card["source_start"], card["source_end"]
        if source_start is None or source_end is None:
            unestablished.append({
                "card": card["stem"],
                "reason": "its filename declares no source span "
                          "(nospan), so there is no speech to pair it "
                          "with",
            })
            continue
        overlapping = [segment for segment in segments
                       if source_start < segment["source_end"]
                       and source_end > segment["source_start"]]
        if not overlapping:
            mispaired.append({
                "card": card["stem"],
                "kind": "span_unplayed",
                "reel_start": round(card["reel_in"], 3),
                "reel_end": round(card["reel_out"], 3),
                "declared_span": [source_start, source_end],
                "declared_speaker": card["speaker"],
                "expected_start": None,
                "expected_end": None,
                "offset_seconds": None,
                "detail": (
                    f"{card['stem']!r} declares source "
                    f"{source_start:.3f}-{source_end:.3f}s and no "
                    f"transcript segment plays that span - the file "
                    f"claims speech this reel never carries"),
            })
            continue
        speakers = {subtitle_segment_id.slug(segment.get("speaker"),
                                             "nospeaker")
                    for segment in overlapping}
        if card["speaker"] not in speakers:
            mispaired.append({
                "card": card["stem"],
                "kind": "speaker_mismatch",
                "reel_start": round(card["reel_in"], 3),
                "reel_end": round(card["reel_out"], 3),
                "declared_span": [source_start, source_end],
                "declared_speaker": card["speaker"],
                "expected_start": None,
                "expected_end": None,
                "offset_seconds": None,
                "detail": (
                    f"{card['stem']!r} speaks as {card['speaker']!r} "
                    f"over a span the transcript gives to "
                    f"{sorted(speakers)} - one speaker's caption over "
                    f"another speaker's audio"),
            })
            continue
        files = {segment.get("source_file") for segment in overlapping}
        lo, hi = None, None
        for source_file, source_time, reel_start, reel_end, *_ in sourced:
            if source_file not in files:
                continue
            if not (source_start - PAIRING_WORD_EDGE_SECONDS
                    <= source_time
                    <= source_end + PAIRING_WORD_EDGE_SECONDS):
                continue
            lo = reel_start if lo is None else min(lo, reel_start)
            hi = reel_end if hi is None else max(hi, reel_end)
        if lo is None:
            unestablished.append({
                "card": card["stem"],
                "reason": ("its declared span "
                           f"{source_start:.3f}-{source_end:.3f}s "
                           "carries no timed word on this reel - the "
                           "transcript rows there lost their timings, "
                           "which transcript_row_fit already reports"),
            })
            continue
        edge_offsets.append(max(abs(card["reel_in"] - lo),
                                abs(card["reel_out"] - hi)))
        gap = max(0.0, lo - card["reel_out"], card["reel_in"] - hi)
        if gap > CAPTION_PAIRING_TOLERANCE_SECONDS:
            mispaired.append({
                "card": card["stem"],
                "kind": "displaced",
                "reel_start": round(card["reel_in"], 3),
                "reel_end": round(card["reel_out"], 3),
                "declared_span": [source_start, source_end],
                "declared_speaker": card["speaker"],
                "expected_start": round(lo, 3),
                "expected_end": round(hi, 3),
                "offset_seconds": round(gap, 3),
                "detail": (
                    f"{card['stem']!r} sits at "
                    f"{card['reel_in']:.2f}-{card['reel_out']:.2f}s "
                    f"but its declared span plays at "
                    f"{lo:.2f}-{hi:.2f}s - "
                    f"{gap:.2f}s away"),
            })
    mispaired.sort(key=lambda r: (r["reel_start"], r["card"]))
    return {
        "measured": True,
        "caption_cards": len(cards),
        "established_cards": len(edge_offsets),
        "mispaired": mispaired,
        "unestablished": unestablished,
        "max_edge_offset_seconds": (round(max(edge_offsets), 3)
                                    if edge_offsets else None),
        "mean_edge_offset_seconds": (round(sum(edge_offsets)
                                           / len(edge_offsets), 3)
                                     if edge_offsets else None),
    }


def covered_fraction(start: float, end: float,
                     windows: Sequence[Tuple[float, float]]) -> float:
    """How much of `[start, end]` a caption card is on screen for."""
    width = end - start
    if width <= 0:
        return 1.0 if any(lo <= start < hi for lo, hi in windows) else 0.0
    inside = 0.0
    for lo, hi in windows:
        overlap = min(end, hi) - max(start, lo)
        if overlap > 0:
            inside += overlap
    return min(inside / width, 1.0)


# ── Alignment ────────────────────────────────────────────────────────

EQUAL, SUBSTITUTED, NOT_HEARD, EXTRA = "eq", "sub", "not_heard", "extra"


def align(planned: Sequence[str], heard: Sequence[str]) -> List[tuple]:
    """Levenshtein over normalised tokens, as an edit script.

    Rows are `(kind, planned_index, heard_index)`; the unused index is
    None. Plain dynamic programming rather than a library, because the
    whole point of the deterministic half is that it needs nothing
    installed to run.
    """
    n, m = len(planned), len(heard)
    grid = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        grid[i][0] = i
    for j in range(m + 1):
        grid[0][j] = j
    for i in range(1, n + 1):
        left = planned[i - 1]
        row, prev = grid[i], grid[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1,
                         prev[j - 1] + (0 if left == heard[j - 1] else 1))
    ops: List[tuple] = []
    i, j = n, m
    while i > 0 or j > 0:
        same = 0 if (i and j and planned[i - 1] == heard[j - 1]) else 1
        if i and j and grid[i][j] == grid[i - 1][j - 1] + same:
            ops.append((EQUAL if not same else SUBSTITUTED, i - 1, j - 1))
            i -= 1
            j -= 1
        elif i and grid[i][j] == grid[i - 1][j] + 1:
            ops.append((NOT_HEARD, i - 1, None))
            i -= 1
        else:
            ops.append((EXTRA, None, j - 1))
            j -= 1
    ops.reverse()
    return ops


def drift_runs(pairs: Sequence[Tuple[int, int, float]],
               planned: Sequence[Word],
               heard: Sequence[Word],
               floor: float = DRIFT_NOISE_FLOOR_SECONDS,
               minimum: int = DRIFT_RUN_MIN_WORDS) -> List[Dict[str, Any]]:
    """Maximal runs of consecutive words drifting the same way past `floor`.

    `pairs` are `(planned_index, heard_index, drift_seconds)` for matched
    words, in reel order. A run ends at the first word inside the floor
    OR at the first sign change: an edit that is late and then early is
    two things happening, not one.
    """
    runs: List[List[Tuple[int, int, float]]] = []
    current: List[Tuple[int, int, float]] = []
    for pair in pairs:
        drift = pair[2]
        if abs(drift) <= floor:
            if len(current) >= minimum:
                runs.append(current)
            current = []
            continue
        if current and (drift > 0) != (current[-1][2] > 0):
            if len(current) >= minimum:
                runs.append(current)
            current = []
        current.append(pair)
    if len(current) >= minimum:
        runs.append(current)

    out: List[Dict[str, Any]] = []
    for run in runs:
        drifts = [d for _, _, d in run]
        median = statistics.median(drifts)
        out.append({
            "words": len(run),
            "planned_start": round(planned[run[0][0]].start, 3),
            "planned_end": round(planned[run[-1][0]].end, 3),
            "heard_start": round(heard[run[0][1]].start, 3),
            "median_drift_seconds": round(median, 3),
            "max_drift_seconds": round(max(drifts, key=abs), 3),
            "direction": "late" if median > 0 else "early",
            "text": " ".join(planned[i].word for i, _, _ in run),
        })
    return out


# ── One finding ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One measured row, in `qa_report.json`'s own shape.

    Same six fields as `render_qa.RenderQAResult`, so
    `library/tools/qa_findings.read_qa_report` reads these without a
    second vocabulary and `FINDING_READERS` claims them the same way.
    """

    metric: str
    passed: bool
    value: Any
    threshold: Any
    severity: str
    detail: str

    def as_row(self) -> Dict[str, Any]:
        return {"metric": self.metric, "passed": self.passed,
                "value": self.value, "threshold": self.threshold,
                "severity": self.severity, "detail": self.detail}


SCRIPT_METRIC = "heard_script_divergence"
DRIFT_METRIC = "heard_timing_drift"
COVERAGE_METRIC = "heard_caption_coverage"
FIT_METRIC = "transcript_row_fit"
PAIRING_METRIC = "heard_caption_pairing"

METRICS = (SCRIPT_METRIC, DRIFT_METRIC, COVERAGE_METRIC, FIT_METRIC,
           PAIRING_METRIC)
"""Every metric this producer can emit. Complete, and each has a row in
`qa_findings.FINDING_READERS`.

`FIT_METRIC` is the odd one and says so: the other three measure the
RENDER against the plan, and this one measures the plan's own transcript
against the audio it claims to be in. It is emitted here because a
reader looking at an uncaptioned passage needs its cause in the same
record, and it is the only finding this producer makes that a reel could
have had BEFORE it was rendered
(`library/tools/transcript_fit.py`)."""


class NothingWasHeard(RuntimeError):
    """This reel cannot be heard against its plan, and the reason is named."""


# ── The pass ─────────────────────────────────────────────────────────

@dataclass
class Hearing:
    """One reel, heard against its plan."""

    timeline_name: str = ""
    timeline_path: str = ""
    video_path: str = ""
    fps: float = 0.0
    planned: List[Word] = field(default_factory=list)
    heard: List[Word] = field(default_factory=list)
    spans: List[Span] = field(default_factory=list)
    operations: List[tuple] = field(default_factory=list)
    divergences: List[Dict[str, Any]] = field(default_factory=list)
    drift: Dict[str, Any] = field(default_factory=dict)
    runs: List[Dict[str, Any]] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    engine: Dict[str, Any] = field(default_factory=dict)
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    corrections_applied: List[Dict[str, Any]] = field(default_factory=list)
    unfitted_transcript_rows: List[Dict[str, Any]] = field(
        default_factory=list)
    transcript_fit: Dict[str, Any] = field(default_factory=dict)
    """What the WHOLE transcript looks like, not just this reel's rows.

    The population is a property of the transcript: rows exist whether
    or not any reel plays one. Carried on every hearing so a reader
    learns the episode-wide count from the reel they already ran,
    without hearing thirty more."""
    pairing: Dict[str, Any] = field(default_factory=dict)
    """What each caption card's own filename declares, and where that
    speech actually plays. `pairing_rows` owns the shape; unmeasured
    until `hear` runs it, like `coverage`."""
    settings: Any = None
    """The `hearing_settings.HearingSettings` this hearing ran with."""
    skipped: List[Dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timeline_name": self.timeline_name,
            "timeline_path": self.timeline_path,
            "video_path": self.video_path,
            "gates": GATES,
            "fps": self.fps,
            "engine": self.engine,
            "normalisation": {
                "spelling_corrections_applied_to_the_heard_side":
                    self.corrections_applied,
                "folded": "case, surrounding punctuation, apostrophe forms",
                "not_folded": ("contractions, spelled-out numbers, compound "
                               "splits, filler words - all reported"),
            },
            "planned_word_count": len(self.planned),
            "heard_word_count": len(self.heard),
            "planned_script": " ".join(w.word for w in self.planned),
            "heard_script": " ".join(w.word for w in self.heard),
            "spans": [{"track": s.track,
                       "file_path": s.file_path,
                       "reel_in": round(s.reel_in, 3),
                       "reel_out": round(s.reel_out, 3),
                       "source_in": round(s.source_in, 3),
                       "source_out": round(s.source_out, 3)}
                      for s in self.spans],
            "divergences": self.divergences,
            "drift": self.drift,
            "drift_runs": self.runs,
            "caption_coverage": self.coverage,
            "caption_pairing": self.pairing,
            "transcriber_anomalies": self.anomalies,
            "unfitted_transcript_rows": self.unfitted_transcript_rows,
            "transcript_fit": self.transcript_fit,
            "settings": (self.settings.as_dict() if self.settings is not None
                         else {}),
            "skipped": self.skipped,
            "findings": [f.as_row() for f in self.findings],
        }


def _script_finding(hearing: Hearing) -> Finding:
    kinds = {SUBSTITUTED: 0, NOT_HEARD: 0, EXTRA: 0}
    for row in hearing.divergences:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    total = sum(kinds.values())
    matched = sum(1 for kind, _, _ in hearing.operations if kind == EQUAL)
    detail = (f"{matched} of {len(hearing.planned)} planned words are "
              f"heard in the render; {kinds[SUBSTITUTED]} substituted, "
              f"{kinds[NOT_HEARD]} planned and not heard, {kinds[EXTRA]} "
              f"heard and not planned")
    if hearing.divergences:
        first = hearing.divergences[0]
        detail += f" - first at {first['reel_seconds']:.2f}s: {first['detail']}"
    return Finding(
        metric=SCRIPT_METRIC, passed=total == 0, value=total, threshold=0,
        severity="warning" if total else "info", detail=detail)


def _drift_finding(hearing: Hearing) -> Finding:
    runs = hearing.runs
    floor = hearing.settings.drift_noise_floor_seconds
    minimum = hearing.settings.drift_run_min_words
    if not hearing.drift.get("matched_words"):
        return Finding(
            metric=DRIFT_METRIC, passed=True, value=0, threshold=0,
            severity="info",
            detail="no word matched between plan and render, so no drift "
                   "could be measured")
    detail = (f"{hearing.drift['matched_words']} matched words: median "
              f"{hearing.drift['median_seconds'] * 1000:+.0f}ms, mean "
              f"absolute {hearing.drift['mean_absolute_seconds'] * 1000:.0f}ms"
              f", worst {hearing.drift['max_absolute_seconds'] * 1000:.0f}ms")
    if runs:
        worst = max(runs, key=lambda r: abs(r["median_drift_seconds"]))
        detail += (f"; {len(runs)} run(s) of {minimum}+ "
                   f"consecutive words past the "
                   f"{floor * 1000:.0f}ms transcriber "
                   f"noise floor - worst is {worst['words']} words "
                   f"{worst['direction']} by "
                   f"{worst['median_drift_seconds'] * 1000:+.0f}ms at "
                   f"{worst['planned_start']:.2f}s: {worst['text']!r}")
    else:
        detail += (f"; no run of {minimum}+ consecutive words "
                   f"past the {floor * 1000:.0f}ms "
                   f"noise floor")
    return Finding(
        metric=DRIFT_METRIC, passed=not runs, value=len(runs), threshold=0,
        severity="error" if runs else "info", detail=detail)


def _coverage_finding(hearing: Hearing) -> Optional[Finding]:
    coverage = hearing.coverage
    if not coverage.get("measured"):
        return None
    zero = coverage["uncaptioned_words"]
    partial = coverage["half_captioned_words"]
    detail = (f"{len(zero)} of {len(hearing.heard)} heard words have NO "
              f"caption on screen while they are spoken, "
              f"{len(partial)} are under "
              f"{hearing.settings.caption_coverage_floor:.0%} covered, "
              f"across "
              f"{coverage['caption_cards']} cards")
    if zero:
        detail += (f" - {zero[0]['reel_start']:.2f}-{zero[-1]['reel_end']:.2f}s"
                   f": {' '.join(w['word'] for w in zero)!r}")
    # A word only half covered is a card the viewer watches arrive or
    # leave mid-word, which is a divergence of its own - so it fails the
    # row's own verdict rather than passing with a loud severity nobody
    # would ever see. A finding that reads CLEAN while carrying a
    # warning is exactly what `qa_findings.REPORT_ONLY_METRICS` exists
    # to keep rare, and this is not one of those two.
    return Finding(
        metric=COVERAGE_METRIC, passed=not (zero or partial),
        value=[len(zero), len(partial)], threshold=0,
        severity="error" if zero else ("warning" if partial else "info"),
        detail=detail)


def _fit_finding(hearing: Hearing) -> Finding:
    """The rows this reel plays whose text did not fit its audio.

    A finding rather than a note, which is the change: the row that cost
    Reel 26 its captions was already being found and was reported as
    context nobody had to act on. `qa_findings` owns who acts on it and
    `temporal_index` is the owner, because the transcript is where it
    was made.

    The severity is `error` when any row lost EVERY word, because a row
    with no timings generates no caption card at all and the viewer sees
    nothing; `warning` when only parts of rows were lost, because the
    surviving words still place a card and what is missing is a word
    inside it.
    """
    rows = hearing.unfitted_transcript_rows
    whole = [r for r in rows
             if r["kind"] == "whole_row_lost"]
    episode = hearing.transcript_fit
    if not rows:
        detail = ("every transcript row this reel plays carries a word "
                  "timing for every word of its text")
        if episode:
            detail += (f"; {episode['unfitted_rows']} row(s) of the "
                       f"episode's {episode['rows']} do not, and this reel "
                       f"plays none of them")
        return Finding(metric=FIT_METRIC, passed=True, value=0, threshold=0,
                       severity="info", detail=detail)
    untimed = sum(r["untimed_words"] for r in rows)
    detail = (f"{len(rows)} transcript row(s) this reel plays carry text "
              f"with no word timing under it - {untimed} word(s), "
              f"{len(whole)} row(s) lost entirely. Nothing word-timed can "
              f"be placed over them, so they generate no caption card")
    worst = max(rows, key=lambda r: r["untimed_words"])
    detail += (f" - worst at {worst['reel_start']:.2f}s: "
               f"{worst['untimed_words']} of {worst['text_words']} words "
               f"in {worst['span_seconds']:.2f}s "
               f"({worst['implied_words_per_second']} words per second): "
               f"{worst['text'][:70]!r}")
    if episode:
        reference = episode["fastest_fitted_words_per_second"]
        detail += (f". Episode-wide: {episode['unfitted_rows']} of "
                   f"{episode['rows']} rows, against a fastest FITTED row "
                   f"of {reference} words per second")
    return Finding(
        metric=FIT_METRIC, passed=False, value=len(rows), threshold=0,
        severity="error" if whole else "warning", detail=detail)


def _pairing_finding(hearing: Hearing) -> Optional[Finding]:
    """The caption cards paired with the wrong source span.

    The one check in this pass that needs no transcription at all: a
    card's own filename declares whose speech and which source span it
    captions (`subtitle_segment_id`), and the timeline says where the
    card was placed. A wrong-but-plausible pairing - the wrong file in
    a slot, or the right file at the wrong time - is invisible to every
    other check here, because the words on screen are real words and
    the timing can be perfect while they belong to another passage.

    `error` when any card is mispaired, because a viewer reading one
    speaker's caption over another speaker's audio is misinformed, not
    inconvenienced. Cards the check cannot establish (`nospan`
    filenames, or spans under which the transcript carries no timed
    word) never fail it: the second already has an owner in
    `transcript_row_fit`, and an unmeasurable card is not a guilty one.
    """
    pairing = hearing.pairing
    if not pairing.get("measured"):
        return None
    mispaired = pairing["mispaired"]
    cards = pairing["caption_cards"]
    if not mispaired:
        detail = (f"{pairing['established_cards']} of {cards} caption "
                  f"cards sit over the source span their own filename "
                  f"declares")
        if pairing["max_edge_offset_seconds"] is not None:
            detail += (f" - worst edge "
                       f"{pairing['max_edge_offset_seconds'] * 1000:.0f}ms, "
                       f"mean "
                       f"{pairing['mean_edge_offset_seconds'] * 1000:.0f}ms")
        if pairing["unestablished"]:
            detail += (f"; {len(pairing['unestablished'])} card(s) declare "
                       f"nothing to pair - "
                       + ", ".join(row["card"]
                                   for row in pairing["unestablished"]))
        return Finding(metric=PAIRING_METRIC, passed=True, value=0,
                       threshold=0, severity="info", detail=detail)
    kinds: Dict[str, int] = {}
    for row in mispaired:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    detail = (f"{len(mispaired)} of {cards} caption cards are paired "
              f"with speech their filename does not declare "
              f"({', '.join(f'{count} {kind}' for kind, count in sorted(kinds.items()))})")
    first = mispaired[0]
    detail += f" - first at {first['reel_start']:.2f}s: {first['detail']}"
    return Finding(
        metric=PAIRING_METRIC, passed=False, value=len(mispaired),
        threshold=0, severity="error", detail=detail)


def hear(timeline: Dict[str, Any],
         transcript: Dict[str, Any],
         spoken,
         project_folder: str = "",
         timeline_path: str = "",
         video_path: str = "",
         settings=None) -> Hearing:
    """Compare one render's speech against one plan's. No model, no I/O.

    `spoken` is a `heard_speech.HeardSpeech` - ALREADY transcribed, so
    this function is pure and a recorded transcription replays through it
    exactly. That is what lets the known-answer case be a test rather
    than a run. `project_folder` is read for two things: the filed
    spelling corrections that the planned side already has applied and
    the heard side does not, and - when `settings` is not passed - the
    project's own `pipeline.reel_hearing` declaration.

    `settings` is a `hearing_settings.HearingSettings`. Resolved here
    when absent so every caller gets the project's declaration without
    having to remember to ask for it, and so a hearing can never run
    with values nothing recorded.
    """
    from library.tools import hearing_settings, transcript_corrections, \
        transcript_fit

    if settings is None:
        settings = hearing_settings.resolve(project_folder or None)

    hearing = Hearing(
        settings=settings,
        timeline_name=str((timeline.get("metadata") or {}).get("name") or ""),
        timeline_path=timeline_path,
        video_path=video_path,
        fps=_fps(timeline),
        engine=dict(spoken.engine),
        anomalies=list(spoken.anomalies),
    )

    hearing.planned, hearing.spans = planned_words(timeline, transcript)
    if not hearing.planned:
        raise NothingWasHeard(
            f"the plan places no transcribed speech on "
            f"{hearing.timeline_name or 'this timeline'}: no audio clip on "
            f"it names a source file the timeline transcript has words for. "
            f"There is nothing to hear this render against.")

    corrections = (transcript_corrections.spelling_corrections(project_folder)
                   if project_folder else [])
    hearing.corrections_applied = [
        {"id": c["id"], "heard": c["heard"], "correct": c["correct"]}
        for c in corrections]
    for raw in spoken.words:
        spelled = (transcript_corrections.apply_spelling(
            raw.word, corrections)[0] if corrections else raw.word)
        token = normalise(spelled)
        if not token:
            continue
        hearing.heard.append(Word(word=spelled, token=token,
                                  start=raw.start, end=raw.end))

    hearing.operations = align([w.token for w in hearing.planned],
                               [w.token for w in hearing.heard])

    rows: List[Dict[str, Any]] = []
    pairs: List[Tuple[int, int, float]] = []
    for kind, i, j in hearing.operations:
        if kind == EQUAL:
            pairs.append((i, j, hearing.heard[j].start
                          - hearing.planned[i].start))
        elif kind == SUBSTITUTED:
            rows.append({"kind": kind,
                         "reel_seconds": round(hearing.planned[i].start, 3),
                         "planned": hearing.planned[i].word,
                         "heard": hearing.heard[j].word,
                         "detail": f"planned {hearing.planned[i].word!r}, "
                                   f"heard {hearing.heard[j].word!r}"})
        elif kind == NOT_HEARD:
            rows.append({"kind": kind,
                         "reel_seconds": round(hearing.planned[i].start, 3),
                         "planned": hearing.planned[i].word, "heard": None,
                         "detail": f"planned {hearing.planned[i].word!r} is "
                                   f"not heard in the render"})
        else:
            rows.append({"kind": kind,
                         "reel_seconds": round(hearing.heard[j].start, 3),
                         "planned": None, "heard": hearing.heard[j].word,
                         "detail": f"heard {hearing.heard[j].word!r}, which "
                                   f"the plan does not carry"})
    hearing.divergences = sorted(rows, key=lambda r: r["reel_seconds"])

    if pairs:
        drifts = [d for _, _, d in pairs]
        absolute = [abs(d) for d in drifts]
        hearing.drift = {
            "matched_words": len(drifts),
            "median_seconds": round(statistics.median(drifts), 4),
            "mean_seconds": round(statistics.fmean(drifts), 4),
            "mean_absolute_seconds": round(statistics.fmean(absolute), 4),
            "max_absolute_seconds": round(max(absolute), 4),
            "noise_floor_seconds": settings.drift_noise_floor_seconds,
            "past_the_floor": sum(
                1 for a in absolute
                if a > settings.drift_noise_floor_seconds),
            "run_min_words": settings.drift_run_min_words,
        }
        hearing.runs = drift_runs(
            pairs, hearing.planned, hearing.heard,
            floor=settings.drift_noise_floor_seconds,
            minimum=settings.drift_run_min_words)
    else:
        hearing.drift = {"matched_words": 0}

    windows = caption_windows(timeline) if settings.runs(
        COVERAGE_METRIC) else []
    if not settings.runs(COVERAGE_METRIC):
        hearing.coverage = {"measured": False, "caption_cards": 0,
                            "uncaptioned_words": [],
                            "half_captioned_words": []}
        hearing.skipped.append({
            "check": COVERAGE_METRIC,
            "reason": "this hearing was asked not to make this check "
                      "(hearing_settings.checks_declined). It is skipped "
                      "openly rather than left out: a report missing a row "
                      "reads as a clean one."})
    elif windows:
        zero, partial = [], []
        for word in hearing.heard:
            share = covered_fraction(word.start, word.end, windows)
            row = {"word": word.word, "reel_start": round(word.start, 3),
                   "reel_end": round(word.end, 3),
                   "covered": round(share, 3)}
            if share <= 0.0:
                zero.append(row)
            elif share < settings.caption_coverage_floor:
                partial.append(row)
        hearing.coverage = {
            "measured": True, "caption_cards": len(windows),
            "uncaptioned_words": zero, "half_captioned_words": partial,
        }
    else:
        hearing.coverage = {"measured": False, "caption_cards": 0,
                            "uncaptioned_words": [],
                            "half_captioned_words": []}
        hearing.skipped.append({
            "check": COVERAGE_METRIC,
            "reason": "this timeline carries no rendered caption segment, "
                      "so there is no caption timing to measure. A reel "
                      "that declares no captions is not a reel with late "
                      "ones."})

    hearing.unfitted_transcript_rows = unfitted_rows_played(
        timeline, transcript, hearing.spans)
    hearing.transcript_fit = transcript_fit.scan(transcript)
    # The whole-document rows are already in each finding's detail and
    # in the reel's own list; carrying them a third time would be the
    # summary and the structure it was rendered from (AGENTS.md 10.1).
    hearing.transcript_fit.pop("rows_detail", None)

    if not settings.runs(PAIRING_METRIC):
        hearing.pairing = {"measured": False, "caption_cards": 0,
                           "established_cards": 0, "mispaired": [],
                           "unestablished": [],
                           "max_edge_offset_seconds": None,
                           "mean_edge_offset_seconds": None}
    elif not caption_cards(timeline):
        hearing.pairing = {"measured": False, "caption_cards": 0,
                           "established_cards": 0, "mispaired": [],
                           "unestablished": [],
                           "max_edge_offset_seconds": None,
                           "mean_edge_offset_seconds": None}
        hearing.skipped.append({
            "check": PAIRING_METRIC,
            "reason": "this timeline carries no rendered caption segment, "
                      "so there is no pairing to check. A reel that "
                      "declares no captions is not a reel with wrong ones."})
    else:
        hearing.pairing = pairing_rows(timeline, transcript, hearing.spans)

    builders = ((SCRIPT_METRIC, _script_finding),
                (DRIFT_METRIC, _drift_finding),
                (COVERAGE_METRIC, _coverage_finding),
                (FIT_METRIC, _fit_finding),
                (PAIRING_METRIC, _pairing_finding))
    hearing.findings = []
    for metric, build in builders:
        if not settings.runs(metric):
            if metric != COVERAGE_METRIC:  # already recorded above
                hearing.skipped.append({
                    "check": metric,
                    "reason": "this hearing was asked not to make this "
                              "check (hearing_settings.checks_declined). "
                              "It is skipped openly rather than left out: "
                              "a report missing a row reads as a clean "
                              "one."})
            continue
        finding = build(hearing)
        if finding is not None:
            hearing.findings.append(finding)
    return hearing


# ── Where the record goes, and who is told ───────────────────────────

def record_path(video_path: str) -> str:
    """Beside the render, next to `deliver-reel`'s own sidecar."""
    return os.path.splitext(os.path.abspath(video_path))[0] + RECORD_SUFFIX


def write_record(hearing: Hearing) -> str:
    """The whole hearing, on disk beside what it heard."""
    path = record_path(hearing.video_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(hearing.as_dict(), handle, indent=2, default=str)
    return path


def hearing_id(hearing: Hearing) -> str:
    """This render, heard against this plan - stable across re-runs.

    Used as the run id when a finding is announced, so hearing the same
    unchanged render twice does not tell the captain twice. A render
    that was rebuilt has a different size and mtime and is a different
    hearing.
    """
    try:
        stat = os.stat(hearing.video_path)
        stamp = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        stamp = "absent"
    digest = hashlib.sha256(
        f"{hearing.video_path}|{stamp}|{len(hearing.planned)}".encode()
    ).hexdigest()[:12]
    return f"hearing:{digest}"


def announce(project_folder: str, hearing: Hearing) -> List[Any]:
    """Raise each non-clean finding on the project's own hook layer.

    `qa_finding_raised` is the condition `library/tools/hooks.py` already
    declares for exactly this - "the render QA measured something it is
    asked to report" - and a `steer` hook on it is how a finding reaches
    the captain's review channel as an anchored note. Nothing fires
    unless the project DECLARES a hook, `dispatch` never raises for a
    hook that failed, and this gates nothing either way.
    """
    from library.tools import hooks

    fired: List[Any] = []
    run_id = hearing_id(hearing)
    for finding in hearing.findings:
        if finding.passed:
            continue
        try:
            fired.extend(hooks.dispatch(
                project_folder, "qa_finding_raised",
                {"metric": finding.metric, "detail": finding.detail,
                 "timeline": hearing.timeline_name}, run_id))
        except hooks.HookError:
            # A hook layer that cannot read its own declaration is a
            # declaration problem and refuses at load. Reporting must
            # not fail on it.
            continue
    return fired


def read_findings(hearing: Hearing):
    """The findings, through the ONE reader (`qa_findings`).

    Not a private opinion about which measurements matter: the same
    classification, the same severities and the same
    `FINDING_READERS` table the run summary and step 3.03 read
    (AGENTS.md 10.4).
    """
    from library.tools import qa_findings

    return qa_findings.read_qa_report(
        [f.as_row() for f in hearing.findings],
        qa_findings.SOURCE_FILE,
        f"hearing {hearing.timeline_name or hearing.video_path}")


def summary_lines(hearing: Hearing) -> List[str]:
    """What a reader is told, as lines."""
    from library.tools import qa_findings

    read = read_findings(hearing)
    lines = [
        f"Heard {hearing.timeline_name or os.path.basename(hearing.video_path)}",
        f"  render:   {hearing.video_path}",
        f"  plan:     {hearing.timeline_path}",
        f"  heard by: {hearing.engine.get('transcriber', '?')} "
        f"{hearing.engine.get('version', '')}".rstrip(),
        (f"  words:    {len(hearing.planned)} planned, "
         f"{len(hearing.heard)} heard"),
        "  gates:    no - this is a report, not a gate",
    ]
    if hearing.corrections_applied:
        lines.append(
            "  spelling: the heard side was corrected through "
            + ", ".join(f"{c['heard']}->{c['correct']}"
                        for c in hearing.corrections_applied))
    if hearing.settings is not None:
        from library.tools import hearing_settings

        lines.extend(hearing_settings.warning_lines(hearing.settings))
    for finding in read.reportable:
        icon = "✗" if finding.verdict == qa_findings.FAILING else "!"
        owner = f" [{finding.owner}]" if finding.owner else ""
        lines.append(f"  {icon} {finding.severity:<7} "
                     f"{finding.metric}{owner}: {finding.detail}")
    if not read.reportable:
        lines.append("  ✓ the render says what the plan says, when the "
                     "plan says it")
    for row in hearing.skipped:
        lines.append(f"  - skipped {row['check']}: {row['reason']}")
    if hearing.unfitted_transcript_rows:
        lines.append(
            f"  rows:   {len(hearing.unfitted_transcript_rows)} transcript "
            f"row(s) this reel plays carry text with NO word timing under "
            f"it - nothing word-timed can be placed over them:")
    for row in hearing.unfitted_transcript_rows:
        lines.append(f"        {row['reel_start']:.2f}s "
                     f"({row['untimed_words']} of {row['text_words']} "
                     f"words untimed in {row['span_seconds']:.2f}s = "
                     f"{row['implied_words_per_second']} w/s): "
                     f"{row['text'][:80]!r}")
    if hearing.transcript_fit:
        episode = hearing.transcript_fit
        lines.append(
            f"  note: the whole transcript carries "
            f"{episode['unfitted_rows']} unfitted row(s) of "
            f"{episode['rows']} - "
            f"`python3 -m library.tools.transcript_fit <project>` lists "
            f"them without hearing another reel")
        if episode["interpolated_words"]:
            lines.append(
                f"        and {episode['interpolated_words']} word(s) the "
                f"aligner PLACED without aligning - numerals, not a defect")
    if hearing.anomalies:
        lines.append(
            f"  note: {len(hearing.anomalies)} transcriber anomal(ies) - "
            f"read a divergence near one as the transcriber's, not the "
            f"edit's")
    return lines
