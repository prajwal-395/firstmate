"""A reel's own spine, in the reel's own time.

What this is, and what it deliberately is NOT
---------------------------------------------
This is a PRODUCER.  It makes an input the pipeline's steps already
consume - a spine, `{"structure": [block, ...]}` satisfying AGENTS.md
section 6 - and then it gets out of the way.  It plans nothing, styles
nothing and renders nothing.

That distinction is the captain's ruling of 2026-09-04, and it is the
reason `reel_subtitles.py` is deleted rather than extended:

    "the subtitles was meant to utilize the pipeline subtitles step and
     whatever funcitonality was put into that python file should have
     been augmented into the pipeline -- we are not tryig to create any
     standalone artifacts and scripts"

`reel_subtitles.py` grouped words into cards and attached a per-speaker
style.  Both of those are step 4.01's job and 4.01 already does them,
better: it groups by MEASURED PIXELS through `safe_area.fits_in_box`
(AGENTS.md 10.2), where the standalone module counted words.  So the
fold is not a copy - two of the three things it did were a second,
weaker implementation of work that already existed, and the third (the
closer seam) is the only behaviour that had to survive.

    "each reel does have an audio spine, its just the audio in the
     timeline itself"

That is what this builds.  Nineteen reels, nineteen spines, and every
step downstream runs against one exactly as it runs against a master -
no reel branch anywhere in a step.

The two clocks, and why this module exists at all
-------------------------------------------------
A reel is its keep ranges laid end to end, so **three different clocks
touch one word** and mixing any two silently produces captions that
drift:

    MASTER timeline second  the transcript's own clock
    REEL timeline second    what the viewer sees, via reel_build.reel_time
    SOURCE second           where it sits in the raw clip

The transcript's words carry bare `start`/`end`, which
`region.domain_of` refuses to guess at - the temporal index uses those
keys for SOURCE seconds and the subtitle plan uses them for TIMELINE
seconds, and on project 001's first word those differ by 0.836s.  So
every word here is put through `region.read_words(..., TIMELINE)` to
NAME its domain before anything reads it, and the spine's
`word_timestamps` come out in SOURCE, which is what section 6 requires.

A block's `timeline_start`/`timeline_end` are REEL seconds.  A word's
`source_start`/`source_end` are SOURCE seconds.  Those are different
clocks in one dict on purpose, and the contract says which is which.

Two mics, one sentence
----------------------
A two-camera shoot records the same words on both mics, so the
transcript carries the sentence TWICE - once on the speaker's own mic
and once, a fraction later and quieter, as bleed on the other's.  Left
alone that becomes two spine blocks claiming the same speech, and every
step downstream captions it twice with two different speakers.

Resolving it is the PRODUCER's job and not step 4.01's, because it is a
fact about the transcript rather than about captions: by the time 4.01
sees a spine the duplication is indistinguishable from two people
saying the same thing.  4.01 already handles what it CAN see - a card
running short, or two cards overlapping - and that division is
deliberate.  Each half resolves what it alone can detect.

The earlier card wins: the primary mic hears the words first and the
bleed arrives delayed.

**A row is not always bleed WHOLE.**  A speaker's turn ends and the
other's begins while the first mic is still recording, so a row often
carries its own speaker's sentence and then, at its TAIL (or at its
HEAD), a few words of the other person picked up as bleed.  Measured on
the captain's field test, 31 of 875 transcript rows carry another
speaker's words at the same instant, 20 of them on rows bound to a clip
and therefore reaching a block today.  `_drop_bleed` cannot see those:
it compares whole blocks, and a long row with a four-word bleed tail is
neither a subset of the short row nor half its vocabulary.

So the tail is CUT, and it is cut HERE - at the block boundary, which
is where a card's boundary is decided.  Step 4.01 groups WITHIN a block
and cannot see across one, so a block carrying two speakers is the only
way a card carrying two speakers can exist.  `_cut_cross_speaker_edges`
is that cut, and the rule it applies is exact rather than a threshold:
**two people cannot utter the same word at the same instant**, so a word
that a different speaker's block carries with the same text at an
overlapping reel second is one mic hearing the other.

Nothing is lost by the cut, and that is a property rather than a hope:
a word is only ever removed from a block while another surviving block
carries it at that same instant, `_cut_would_orphan` checks exactly
that, and a cut that would leave a word carried by nobody is CANCELLED.
The cost of picking the wrong side is which speaker's style the words
are drawn in, never the words.

What a reel cannot supply, said rather than invented
----------------------------------------------------
A reel has no `hook` block, no bookends and no music behaviour; it is
speech that was already cut.  Every block here is `speech`, and a step
that needs more than speech gets an honest absence rather than a
fabricated block (AGENTS.md 10.5).

`tests/test_reel_spine.py`.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from library.tools import region as region_mod
from library.tools.spine_contract import validate_spine_blocks

BLEED_WORD_OVERLAP = 0.5
"""How much of two blocks' wording must coincide to read as one sentence
on two mics rather than two people saying similar things."""

CAPTION_UNANCHORED_ROWS = True
"""Whether a row with no clip binding of its own may still be captioned.

The captain's ruling of 2026-09-06, on a measurement rather than a
preference. `timeline_transcript.attribute_to_clip` returns None for a
row that straddles a cut, and every such row was dropped here - so the
reel played the speech and nothing wrote it. Measured across the
captain's nineteen approved reels, 61 rows: **46 unique, 12 ambiguous,
3 bleed**, carrying 42.8 seconds of spoken words that play with no
caption over them. Ignoring them was not conservative, it was dropping
speech.

Flip this to False to restore the old behaviour in one place. The counts
this module returns are the other half of that: `unanchored_blocks`,
`unanchored_seconds`, `unanchored_ambiguous` and
`unanchored_bleed_dropped` reach the run output, so the change is
visible on one line rather than folded in silently.

**ON THE CAPTAIN'S OWN EPISODE IT PLACES NOTHING, and the reason is
`_place_unanchored`, not the footage.** Turning it on and measuring: 0
blocks added, 0 seconds, F5's coverage gap unmoved at 97.3s. Two of
this module's own choices make that unavoidable. `_clip_extents` reads
a clip's reach off the SPEECH of the rows bound to it, which understates
the clip by its silent head and tail; and `_place_unanchored` then
demands that ALL of a row's played words fall inside ONE such extent.
A row that straddles a cut has words on TWO clips by definition, so it
can never satisfy that, whatever the footage looks like.

**The claim this docstring used to carry - that 0.0 of those word
seconds fall on a clip of their own speaker, and that the row's middle
is WhisperX bridging its own silence with words whose timings belong to
the two ENDS - was measured through that same understating extent, and
it is wrong.** Measured 2026-09-06 against the real clip list: of the
977 words in the 61 rows, **958 sit squarely on a clip of their own
speaker**, and the bridged middle contains NO WORDS AT ALL. Row 206 of
the field test spans 615.5..636.2 and carries two words at 615.5 and
sixteen from 631.1, with nothing in the 14.4 seconds between.

So the repair really did belong in `timeline_transcript`, exactly where
the old note pointed - and it has landed there: a row that does not sit
inside one clip is now split at its own word boundaries, one bound
segment per clip, and reaches this module already anchored. What is
left for this path is the residue - the 19 words that sit on no clip at
all, nine of them single words the aligner stretched across a silence.
Those stay refused, and `unbindable_spans` now says where they are."""

UNANCHORED_UNIQUE_CEILING = 0.30
"""Below this similarity a row carries speech no anchored row carries.

The band boundaries are `reel_proposal.TAKE_SIMILARITY` above and this
below, and they are the SAME comparison the measurement used - not a
re-derivation with a different threshold, which would let a row be bleed
to one half of the codebase and unique to the other."""

ALIGNMENT_METHOD = "timeline_transcript"
"""How a reel block's words were aligned.

Not "whisperx": the words came from `timeline_transcript`, which
transcribed the built timeline's own audio and bound each segment back
to the clip it was cut from.  Naming the real producer is what lets a
reader tell a reel spine from a preflight one.
"""


class ReelSpineError(ValueError):
    """A reel that cannot be turned into a spine, and says why."""


def _touches(segment_start: float, segment_end: float,
             ranges: Sequence[tuple[float, float]]) -> bool:
    """Does this speech touch ANY range this reel plays?

    Any, never the envelope from the first range's start to the last
    range's end.  A reel's closer may come from EARLIER in the episode
    than its body (`reel_build.reel_ranges` spells the order), so on
    such a reel the envelope inverts - `start > end` - and silently
    drops every block.  That was a real defect in the standalone
    captioner and it is not being reproduced here.
    """
    return any(min(end, segment_end) > max(start, segment_start)
               for start, end in ranges)


def _clip_extents(segments: Sequence[dict]) -> dict:
    """Each clip's timeline reach and its timeline-to-source offset.

    Read off the rows that ARE bound, because they are the only ground
    truth on disk: the transcript carries no clip list. A clip's reach is
    the span of the speech attributed to it, which UNDERSTATES the clip -
    it says nothing about its silent head and tail. That is the safe
    direction: a row this cannot place is dropped exactly as before, so
    the failure mode is the old behaviour rather than an invented
    binding.

    Keyed by `(speaker, clip_id)`. This is a two-mic recording and each
    mic has its own clips, so a row must be placed on a clip that
    carried its own speaker.
    """
    out: dict = {}
    for segment in segments:
        clip_id = segment.get("resolve_item_id") or segment.get("source_file")
        start = segment.get("timeline_start")
        source_start = segment.get("source_start")
        if not clip_id or start is None or source_start is None:
            continue
        end = segment.get("timeline_end")
        end = float(end) if end is not None else float(start)
        key = (segment.get("speaker"), clip_id)
        offset = float(source_start) - float(start)
        seen = out.get(key)
        if seen is None:
            out[key] = {"clip_id": clip_id, "offset": offset,
                        "first": float(start), "last": end}
            continue
        # An offset that moves means these rows are not one clip after
        # all. Refuse the whole key rather than average two answers.
        if (seen["offset"] is not None
                and abs(seen["offset"] - offset) > 0.05):
            seen["offset"] = None
        seen["first"] = min(seen["first"], float(start))
        seen["last"] = max(seen["last"], end)
    return out


def _place_unanchored(segment: dict, first_word: float, last_word: float,
                      extents: dict):
    """The clip an unbound row's SURVIVING words sit on, or None.

    Same move as F5's clip: the ROW straddles a cut and has no single
    clip, but the part of it the reel actually plays often sits wholly
    inside one. Attributing that part is not guessing - it is asking the
    narrower question that has an answer.

    Returns `(clip_id, source_start)` or None. None keeps the row
    dropped, which is what every one of them was before.
    """
    speaker = segment.get("speaker")
    for (row_speaker, _), extent in extents.items():
        if row_speaker != speaker or extent["offset"] is None:
            continue
        if extent["first"] <= first_word and last_word <= extent["last"]:
            return extent["clip_id"], first_word + extent["offset"]
    return None


def _merge_spans(spans: Sequence[tuple[float, float]]) -> list:
    """Overlapping spans as one span each."""
    out: list = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def _subtract_spans(spans: Sequence, taken: Sequence) -> list:
    """`spans` minus `taken`, both already merged."""
    out: list = []
    for start, end in spans:
        cursor = start
        for a, b in taken:
            if b <= cursor or a >= end:
                continue
            if a > cursor:
                out.append([cursor, min(a, end)])
            cursor = max(cursor, b)
            if cursor >= end:
                break
        if cursor < end:
            out.append([cursor, end])
    return out


def _unbindable_spans(dropped: Sequence[tuple], blocks: Sequence[dict],
                      ranges) -> list:
    """Where this reel plays speech that NO block carries, in reel seconds.

    Measured three ways at once, because each is a way the count alone
    would lie:

    - **Words, not the row envelope.**  A row with no binding is usually
      Whisper joining two utterances across a silence, so its envelope
      is mostly nothing being said.  Reel 05's row 206 spans 20.7s and
      holds 6.0s of words.  Reporting the envelope would say the captain
      is missing three times what they are missing.
    - **Minus what the surviving blocks already cover.**  A dropped row
      whose seconds another block captions is not an uncaptioned second,
      and reporting it would be a check firing on correct output
      (AGENTS.md 10.4).
    - **In REEL seconds**, because that is where the captain is looking.
    """
    from library.tools.reel_build import reel_time

    covered = _merge_spans([(b["timeline_start"], b["timeline_end"])
                            for b in blocks])
    out: list = []
    for segment, inside in dropped:
        spans = []
        for word in inside:
            start = reel_time(float(word["start"]), ranges)
            end = reel_time(float(word["end"]), ranges, at_end=True)
            if start is None or end is None or end <= start:
                continue
            spans.append((float(start), float(end)))
        remaining = _subtract_spans(_merge_spans(spans), covered)
        if not remaining:
            continue
        out.append({
            "reel_start": round(remaining[0][0], 3),
            "reel_end": round(remaining[-1][1], 3),
            "seconds": round(sum(b - a for a, b in remaining), 3),
            "speaker": segment.get("speaker"),
            "master_start": round(float(inside[0]["start"]), 3),
            "master_end": round(float(inside[-1]["end"]), 3),
            "text": (segment.get("text") or "").strip()})
    return out


def _unanchored_band(segment: dict, anchored: Sequence[dict]) -> str:
    """`bleed`, `ambiguous` or `unique` for a row with no binding.

    THE SAME comparison the measurement used - `reel_proposal`'s own
    content words and its `TAKE_SIMILARITY` - against the anchored rows
    that overlap this one IN TIME. Bleed is the other microphone hearing
    the same words at the same moment, so it is simultaneous by
    definition; widening the window pools the vocabulary of everything
    nearby and inflates the overlap until nothing reads as unique.
    """
    from library.tools.reel_proposal import TAKE_SIMILARITY, _content_words

    words = _content_words(segment.get("text", ""))
    if not words:
        return "bleed"  # nothing to caption; treat as not worth adding
    start = float(segment.get("timeline_start", 0.0))
    end = float(segment.get("timeline_end", 0.0))
    near: set = set()
    for other in anchored:
        if (min(end, float(other.get("timeline_end", 0.0)))
                - max(start, float(other.get("timeline_start", 0.0))) > 0.001):
            near |= _content_words(other.get("text", ""))
    overlap = len(words & near) / len(words)
    if overlap >= TAKE_SIMILARITY:
        return "bleed"
    if overlap <= UNANCHORED_UNIQUE_CEILING:
        return "unique"
    return "ambiguous"


def spine_for_reel(moment, transcript: dict,
                   ranges: Sequence[tuple[float, float]] | None = None
                   ) -> dict:
    """The spine for one reel, in the reel's own time.

    `moment` is a reel proposal moment; `transcript` is the master
    timeline's transcript.  `ranges` defaults to
    `reel_build.reel_ranges(moment, transcript)` - pass it only when the
    caller already computed it, so one reel cannot be spined against one
    order and built against another.

    Returns `{"structure": [...]}`, validated against the spine
    contract before it is handed back: a spine that would fail
    downstream fails HERE, where the reel that produced it is still in
    hand.
    """
    from library.tools.reel_build import reel_ranges, reel_time

    if ranges is None:
        ranges = reel_ranges(moment, transcript)
    if not ranges:
        raise ReelSpineError(
            "this reel plays no ranges, so it has no spine and nothing "
            "downstream can run against it")

    segments = transcript.get("segments") or []
    if not segments:
        raise ReelSpineError(
            "the transcript carries no segments; a reel's spine is its "
            "own timeline audio, and there is none to read")

    blocks: list[dict] = []
    dropped_no_binding = 0
    anchored_rows = [s for s in segments
                     if (s.get("resolve_item_id") or s.get("source_file"))
                     and s.get("source_start") is not None]
    extents = _clip_extents(anchored_rows) if CAPTION_UNANCHORED_ROWS else {}
    unanchored_bands: dict = {"unique": 0, "ambiguous": 0, "bleed": 0}
    unanchored_seconds = 0.0
    dropped_rows: list[tuple] = []

    for segment in sorted(segments, key=lambda s: s.get("timeline_start", 0.0)):
        master_start = segment.get("timeline_start")
        master_end = segment.get("timeline_end")
        if master_start is None or master_end is None:
            continue
        if not _touches(master_start, master_end, ranges):
            continue

        # A segment that straddles a cut has no single source clip -
        # `timeline_transcript.attribute_to_clip` says so by leaving
        # these None rather than picking one.  A block with no clip_id
        # fails the contract, so it is DROPPED and counted, never
        # given an invented binding.
        clip_id = segment.get("resolve_item_id") or segment.get("source_file")
        source_start = segment.get("source_start")
        source_end = segment.get("source_end")
        band = ""
        if not clip_id or source_start is None or source_end is None:
            placed = None
            # The words this reel actually PLAYS. Computed either way,
            # because a dropped row's own span is what the reel is
            # missing and a count cannot say where it is.
            inside = [w for w in (segment.get("words") or [])
                      if w.get("start") is not None
                      and reel_time(float(w["start"]), ranges) is not None]
            if CAPTION_UNANCHORED_ROWS:
                # The ROW straddles a cut; the part this reel plays may
                # not. Ask the narrower question before dropping it.
                band = _unanchored_band(segment, anchored_rows) if inside else ""
                if band == "bleed":
                    # The MEASURED bleed rows only - the second mic
                    # carrying words an anchored row already carries.
                    # Captioning these is the one case that really would
                    # double the captions.
                    unanchored_bands["bleed"] += 1
                elif inside:
                    placed = _place_unanchored(
                        segment, float(inside[0]["start"]),
                        float(inside[-1]["end"]), extents)
            if placed is None:
                dropped_no_binding += 1
                # A `bleed` row's words ARE on screen, under the anchored
                # row on the other mic, so it is not a missing caption.
                if inside and band != "bleed":
                    dropped_rows.append((segment, inside))
                continue
            clip_id, source_start = placed
            source_end = source_start + (
                float(segment["timeline_end"])
                - float(segment["timeline_start"]))

        # Master seconds to REEL seconds.  `at_end=True` on the closing
        # edge: a range end lands exactly on a segment's `timeline_end`,
        # and read the half-open way the last word of the reel falls
        # outside every range and disappears.
        # CLIP to the words that survive, rather than dropping a segment
        # whole because one edge of it fell in a removed take.  A cut
        # rarely lands exactly on a segment boundary, and dropping the
        # whole segment loses speech the reel really plays - silently,
        # which is the worst way to lose it.
        kept = [w for w in (segment.get("words") or [])
                if w.get("start") is not None
                and reel_time(float(w["start"]), ranges) is not None]
        if not kept:
            # Its speech was CUT entirely - a dropped bad take takes its
            # words with it, which is correct and not a defect to report.
            continue
        if len(kept) != len(segment.get("words") or []):
            segment = dict(segment, words=kept)
            master_start = float(kept[0]["start"])
            master_end = float(kept[-1]["end"])
            source_start = float(source_start) + (
                master_start - float(segment["timeline_start"]))

        reel_start = reel_time(master_start, ranges)
        reel_end = reel_time(master_end, ranges, at_end=True)
        if reel_start is None or reel_end is None or reel_end <= reel_start:
            continue

        words = _source_words(segment, master_start, source_start)
        if not words:
            # The contract requires populated `word_timestamps` on a
            # speech block.  Speech with no per-word timing cannot be
            # captioned, so it is dropped rather than shipped hollow.
            dropped_no_binding += 1
            continue

        if band:
            unanchored_bands[band] += 1
            unanchored_seconds += float(reel_end) - float(reel_start)

        blocks.append({
            "position": len(blocks),
            "block_type": "speech",
            # NAMED, never folded in silently. A reader asking "where did
            # this card come from" gets an answer on the block itself,
            # and an `ambiguous` one can be found again.
            "from_unanchored_row": bool(band),
            "unanchored_band": band or None,
            "clip_id": clip_id,
            "source_start": float(source_start),
            "source_end": float(source_end),
            # REEL seconds. The other two clocks in this dict are
            # SOURCE; see the module docstring.
            "timeline_start": float(reel_start),
            "timeline_end": float(reel_end),
            "word_timestamps": words,
            "alignment_method": ALIGNMENT_METHOD,
            "speaker": segment.get("speaker"),
            "content": {"text": segment.get("text", "").strip()},
        })

    # ORDER BY THE REEL, not by the master.  A reel's closer may be cut
    # from EARLIER in the episode than its body but PLAYS LAST, so
    # sorting by master second puts the closer first and `position` -
    # which is block identity, and which 4.05 names its rendered
    # segments by - stops meaning play order.  The bleed sweep below
    # compares neighbours, so it needs this too.
    blocks.sort(key=lambda b: (b["timeline_start"], b["timeline_end"]))
    blocks, bled = _drop_bleed(blocks)
    # WHOLE-block bleed first, then the partial. The other order mangles
    # a near-duplicate pair: both rows carry the same sentence, so both
    # get an edge cut, and what `_drop_bleed` would have removed whole
    # survives as the few words in the middle that the two mics
    # transcribed differently.
    for position, block in enumerate(blocks):
        block["position"] = position
    blocks, cross_speaker = _cut_cross_speaker_edges(blocks)

    if not blocks:
        raise ReelSpineError(
            f"no speech survived onto this reel: {len(segments)} "
            f"transcript segment(s), {len(ranges)} range(s), "
            f"{dropped_no_binding} dropped for having no source binding "
            f"or no word timings. A reel with no spine cannot be "
            f"captioned by any step.")

    unbindable = _unbindable_spans(dropped_rows, blocks, ranges)

    # Fails here, with the reel in hand, rather than three steps later.
    validate_spine_blocks(blocks)
    for position, block in enumerate(blocks):
        block["position"] = position

    return {"structure": blocks,
            "dropped_segments": dropped_no_binding,
            "bleed_blocks_dropped": bled,
            # A card carrying two speakers is what the captain saw on
            # reel 05, and these are the three numbers that say whether
            # this reel had any: how many bled words were cut off a
            # block's edges, how many blocks were nothing BUT bleed, and
            # how many blocks carry a foreign run in the MIDDLE - a real
            # interruption, which is left alone rather than cut.
            "cross_speaker_words_cut": cross_speaker["words"],
            "cross_speaker_blocks_emptied": cross_speaker["blocks_emptied"],
            "cross_speaker_middle_runs": cross_speaker["middle_runs"],
            # Visible on one line, so the captain can judge the change by
            # looking at a number rather than at a diff.
            "unanchored_blocks": (unanchored_bands["unique"]
                                  + unanchored_bands["ambiguous"]),
            "unanchored_seconds": round(unanchored_seconds, 2),
            "unanchored_unique": unanchored_bands["unique"],
            "unanchored_ambiguous": unanchored_bands["ambiguous"],
            "unanchored_bleed_dropped": unanchored_bands["bleed"],
            # WHERE the reel plays speech that no block carries, in REEL
            # seconds, not just how many rows were dropped. A count says
            # a defect exists; a span says which nine seconds of reel 05
            # the captain is looking at.
            "unbindable_seconds": round(
                sum(u["seconds"] for u in unbindable), 2),
            "unbindable_spans": unbindable}


def _words_of(block: dict) -> set:
    """The block's spoken words, lowercased and stripped of punctuation."""
    return {_normalise(w["word"]) for w in block["word_timestamps"]} - {""}


def _is_bleed(earlier: dict, later: dict) -> bool:
    """Are these two blocks the SAME speech heard on two mics?

    Substantial word overlap, never timing alone: two people really can
    talk over each other, and an interruption is not a duplicate.  A
    subset counts because the bleed mic often catches only part of the
    sentence.
    """
    a, b = _words_of(earlier), _words_of(later)
    if not a or not b:
        return False
    return a.issubset(b) or b.issubset(a) or (
        len(a & b) / max(1, len(a | b)) > BLEED_WORD_OVERLAP)


def _normalise(text: str) -> str:
    """One word, lowercased and stripped of punctuation.

    The SAME normalisation `_words_of` uses, so a word that reads as a
    duplicate to the whole-block sweep reads as one here too. Two
    spellings of one rule is how a word becomes bleed to one half of a
    module and speech to the other.
    """
    return re.sub(r"[^a-z0-9']", "", text.lower())


def _word_reel_spans(block: dict) -> list[tuple[str, float, float]]:
    """This block's words as `(normalised, reel_start, reel_end)`.

    A block's `word_timestamps` are SOURCE seconds and SOURCE seconds are
    per CLIP, so two blocks' words cannot be compared in them - the same
    number means a different instant on a different clip. They are put
    into REEL seconds here with the block's own offset, which is exactly
    the arithmetic step 4.01 uses to place a card (`offset = seg_tl_start
    - v1_src_in`). Comparing in the clock the cards are planned in is
    what makes a collision found here a collision the viewer would see.
    """
    offset = block["timeline_start"] - block["source_start"]
    return [(_normalise(str(word["word"])),
             float(word["source_start"]) + offset,
             float(word["source_end"]) + offset)
            for word in block["word_timestamps"]]


def _owner_key(block: dict, index: int, spans: list, wholly_bleed: bool
               ) -> tuple:
    """Which copy of one word OWNS it, as a total order.

    Two things decide it, in this order.

    **A block that is nothing BUT another speaker's words owns none of
    them.**  A mic that caught a phrase and nothing else caught only
    bleed, and a block that carries its own speaker either side of the
    phrase did not.  This has to outrank the timing, because ASR word
    starts on two mics differ by hundredths of a second in either
    direction - measured on reel 05, Akshita's "so" beat Craig's by
    0.130s while his "ranking" beat hers by 0.010s.  Without it a
    wholly-bled block wins a word off a real one on a coin flip.

    Then `_drop_bleed`'s own rule, applied to a word rather than a whole
    block: the primary mic hears the words first and the bleed arrives
    delayed.  The block's start and its position follow so that two
    copies landing on one hundredth of a second still order - without a
    TOTAL order both sides of a collision can cut and the word is lost by
    both.
    """
    return (wholly_bleed, round(spans[index][1], 3),
            round(block["timeline_start"], 3), block["position"])


def _foreign_at(blocks: list[dict], spans: list, index: int) -> list:
    """For each word of block `index`, the copies a DIFFERENT speaker holds.

    A copy is the same normalised word at an OVERLAPPING reel second. Two
    people cannot utter one word at one instant, so a copy is one mic
    hearing the other - there is no threshold in that and none is wanted.
    Returns one list of `(block_index, word_index)` per word, empty where
    the word is the speaker's own.
    """
    block = blocks[index]
    out: list[list] = [[] for _ in spans[index]]
    for other_index, other in enumerate(blocks):
        if other_index == index or other.get("speaker") == block.get("speaker"):
            continue
        for word_index, (text, start, end) in enumerate(spans[index]):
            if not text:
                # A token that normalises to nothing - punctuation alone -
                # matches every other one, so it is evidence of nothing.
                continue
            for other_word, (other_text, other_start, other_end) in \
                    enumerate(spans[other_index]):
                if other_text != text:
                    continue
                if min(end, other_end) - max(start, other_start) > 0.0:
                    out[word_index].append((other_index, other_word))
                    break
    return out


def _edge_run(foreign: list) -> tuple[int, int]:
    """How many words at this block's HEAD and TAIL are another speaker's.

    HEAD and TAIL only. A run in the MIDDLE is speech the block's own
    speaker bracketed on both sides - two people talking over each other,
    which `_drop_bleed` already refuses to resolve and which cutting
    would turn into a block with a hole in it, joining two passages that
    were never adjacent. So the middle is left alone and reported.
    """
    total = len(foreign)
    head = 0
    while head < total and foreign[head]:
        head += 1
    if head == total:
        return total, 0
    tail = 0
    while tail < total - head and foreign[total - 1 - tail]:
        tail += 1
    return head, tail


def _loses_run(blocks: list, spans: list, foreign: list, wholly: list,
               index: int, head: int, tail: int) -> bool:
    """Does THIS block lose the edge run, or does it own it?

    The run is one utterance, so it is decided ONCE, on its FIRST word.
    Deciding word by word is what splits a phrase across two mics: on
    reel 05 a per-word rule would have handed "so" to Akshita and
    "ranking" to Craig, which is neither speaker's sentence.
    """
    total = len(spans[index])
    if head:
        first = 0
    elif tail:
        first = total - tail
    else:
        return False
    mine = _owner_key(blocks[index], first, spans[index], wholly[index])
    theirs = min(
        (_owner_key(blocks[other], word, spans[other], wholly[other])
         for other, word in foreign[index][first]),
        default=None)
    return theirs is not None and theirs < mine


def _cut_would_orphan(blocks: list, spans: list, cuts: dict,
                      index: int) -> bool:
    """Would this block's cut leave one of its words carried by nobody?

    The whole safety of the cut rests on the words surviving somewhere -
    they are removed because ANOTHER block carries them at that instant,
    so if that other block is losing them in the same pass the speech is
    gone.  Asked per block and answered against the cuts as a set, which
    is why the caller iterates to a fixed point rather than deciding each
    block on its own.
    """
    head, tail = cuts[index]
    total = len(spans[index])
    for word_index in list(range(head)) + list(range(total - tail, total)):
        text, start, end = spans[index][word_index]
        for other_index, other in enumerate(blocks):
            if other_index == index or \
                    other.get("speaker") == blocks[index].get("speaker"):
                continue
            other_head, other_tail = cuts.get(other_index, (0, 0))
            other_total = len(spans[other_index])
            for other_word in range(other_head, other_total - other_tail):
                other_text, other_start, other_end = spans[other_index][other_word]
                if other_text == text and \
                        min(end, other_end) - max(start, other_start) > 0.0:
                    break
            else:
                continue
            break
        else:
            return True
    return False


def _apply_cut(block: dict, head: int, tail: int) -> dict:
    """The block with its bled edges removed, re-timed to what is left.

    Every window the block declares is re-read off the SURVIVING words -
    its source span, its reel span and its text - because a block whose
    declared window still covers words it no longer carries is a block
    step 4.01 will filter against a range that is no longer true.
    """
    words = block["word_timestamps"][head:len(block["word_timestamps"]) - tail]
    offset = block["timeline_start"] - block["source_start"]
    kept = dict(block)
    kept["word_timestamps"] = words
    kept["source_start"] = float(words[0]["source_start"])
    kept["source_end"] = float(words[-1]["source_end"])
    kept["timeline_start"] = round(kept["source_start"] + offset, 6)
    kept["timeline_end"] = round(kept["source_end"] + offset, 6)
    kept["content"] = dict(block.get("content") or {},
                           text=" ".join(str(w["word"]) for w in words).strip())
    kept["cross_speaker_words_cut"] = head + tail
    return kept


def _cut_cross_speaker_edges(blocks: list[dict]) -> tuple[list[dict], dict]:
    """Cut, from each block's edges, the words another speaker was saying.

    This is where a CARD's boundary is decided: 4.01 groups within a
    block and never across one, so a block carrying one speaker is what
    makes a card carrying one speaker structurally true rather than
    usually true.

    Returns the surviving blocks and the counts, which the caller reports
    on the run - a cut nobody can see is a cut nobody can judge.
    """
    if len(blocks) < 2:
        return blocks, {"words": 0, "blocks_emptied": 0, "middle_runs": 0}

    spans = [_word_reel_spans(block) for block in blocks]
    foreign = [_foreign_at(blocks, spans, index)
               for index in range(len(blocks))]

    wholly = [bool(marks) and all(marks) for marks in foreign]

    middle_runs = 0
    cuts: dict = {}
    for index in range(len(blocks)):
        head, tail = _edge_run(foreign[index])
        if any(foreign[index][i]
               for i in range(head, len(foreign[index]) - tail)):
            middle_runs += 1
        head = head if _loses_run(blocks, spans, foreign, wholly,
                                  index, head, 0) else 0
        tail = tail if _loses_run(blocks, spans, foreign, wholly,
                                  index, 0, tail) else 0
        if head or tail:
            cuts[index] = (head, tail)

    # A block whose cut would orphan a word gives its cut up ENTIRELY,
    # and giving one up can make another's safe, so this settles rather
    # than deciding each block once. Cuts only ever shrink, so it ends.
    changed = True
    while changed:
        changed = False
        for index in list(cuts):
            if _cut_would_orphan(blocks, spans, cuts, index):
                del cuts[index]
                changed = True

    if not cuts:
        return blocks, {"words": 0, "blocks_emptied": 0,
                        "middle_runs": middle_runs}

    words_cut = 0
    emptied = 0
    kept_blocks: list[dict] = []
    for index, block in enumerate(blocks):
        head, tail = cuts.get(index, (0, 0))
        if not head and not tail:
            kept_blocks.append(block)
            continue
        words_cut += head + tail
        if head + tail >= len(block["word_timestamps"]):
            emptied += 1
            continue
        kept_blocks.append(_apply_cut(block, head, tail))

    kept_blocks.sort(key=lambda b: (b["timeline_start"], b["timeline_end"]))
    return kept_blocks, {"words": words_cut, "blocks_emptied": emptied,
                         "middle_runs": middle_runs}


def _drop_bleed(blocks: list[dict]) -> tuple[list[dict], int]:
    """Drop blocks that are another block's speech bleeding onto a second mic.

    Swept repeatedly, because dropping one block can expose an overlap
    between two that were not adjacent before.  Returns the surviving
    blocks, renumbered, and how many went.
    """
    dropped = 0
    changed = True
    while changed:
        changed = False
        kept: list[dict] = []
        for block in blocks:
            if not kept:
                kept.append(block)
                continue
            previous = kept[-1]
            if block["timeline_start"] >= previous["timeline_end"]:
                kept.append(block)
                continue
            if _is_bleed(previous, block):
                # The later one is the bleed.  The earlier block already
                # carries the mic the words were spoken into, which is
                # also the right speaker attribution.
                dropped += 1
                changed = True
                continue
            # A real interruption: two people talking over each other.
            # Both are kept - what to draw is 4.01's decision, and it can
            # see the overlap once the cards exist.
            kept.append(block)
        blocks = kept

    for position, block in enumerate(blocks):
        block["position"] = position
    return blocks, dropped


def _source_words(segment: dict, master_start: float,
                  source_start: float) -> list[dict]:
    """This segment's words, in SOURCE seconds, as section 6 requires.

    The transcript's words are bare `start`/`end` in MASTER timeline
    seconds.  They are NAMED as timeline through `region.read_words`
    before anything reads them - passing an undeclared word list on is
    the defect `region.py` exists for - and then shifted into source by
    this segment's own binding.

    The shift is per SEGMENT and not a constant: on project 001 the
    eight speech blocks had eight distinct offsets spread over 146.5
    seconds, and the sign was not constant either.
    """
    from library.tools.timeline_transcript import interpolate_untimed_words

    raw = list(segment.get("words") or [])
    if not raw:
        return []

    # Untimed words get timing from their neighbours rather than being
    # dropped - dropping them loses real speech from the caption.
    timed = interpolate_untimed_words(raw)
    named = region_mod.read_words(timed, region_mod.TIMELINE)

    offset = float(source_start) - float(master_start)
    out = []
    for word in named:
        text = str(word.get("word", ""))
        if not text.strip():
            continue
        out.append({
            "word": text,
            "source_start": round(float(word["timeline_start"]) + offset, 4),
            "source_end": round(float(word["timeline_end"]) + offset, 4),
        })

    # Proof rather than intention: if this ever hands back timeline
    # seconds under source key names, the collision region.py was built
    # to prevent has happened here.
    region_mod.assert_domain(out, region_mod.SOURCE, "reel_spine._source_words")
    return out
