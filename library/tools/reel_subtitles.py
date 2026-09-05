"""Captions for a reel, styled per speaker, timed in the reel's own time.

Why this exists
---------------
`subtitle_style.py` resolves a per-speaker look and
`subtitle_segment_id.py` names a rendered overlay, but nothing ever
rendered a caption onto a reel - the captain opened sixteen finished
timelines and found no subtitles on any of them. This is the missing
middle.

Two rules it exists to hold
---------------------------
**A caption is timed in REEL time, not master time.** A reel is its keep
ranges laid end to end, so a caption timed against the master drifts by
the length of every bad take removed before it. `reel_build.reel_time`
is the same arithmetic the picture went through, and captions go through
it too or they desynchronise from the speech they caption.

**A caption carries its SPEAKER's style.** The captain asked for "the
same subtitle styling as the scripts you found", and those scripts give
each speaker their own accent colour - that styling IS the diarization
signal. The values are the project's (AGENTS.md 14); this module only
routes each caption to the right one.

Placing onto a named timeline
-----------------------------
**`MediaPool.AppendToTimeline` appends to the project's CURRENT timeline.
A timeline handle is not a destination.** `SetCurrentTimeline` first, or
the clips land wherever the project happens to be pointing.

Measured 2026-09-04, and the shape of the mistake is worth keeping: all
579 captions for sixteen reels were appended while Reel 01 was current,
so Reel 01 collected what it could and the other fifteen ended up with an
empty V3 - while every call returned True and the run reported
"placed 26/26" sixteen times. `AddTrack` and `SetTrackName` DO act on the
handle, which is what makes it look like the handle is the destination.

The reel builder appeared to work only because `CreateEmptyTimeline`
makes its result current, so each reel happened to be current when its
own clips were appended. That is luck, not correctness, and it stops
being luck the moment anything is placed onto a timeline that already
exists.

Word grouping follows the captain's own script: 3-6 words a card,
breaking at sentence and clause punctuation, with untimed words
interpolated rather than dropped.

`tests/test_reel_subtitles.py`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

PLACEMENT_REQUIRES_CURRENT = (
    "MediaPool.AppendToTimeline appends to the project's CURRENT "
    "timeline. Call project.SetCurrentTimeline(timeline) before placing, "
    "and read back what landed. AddTrack and SetTrackName DO act on the "
    "handle you pass, which is what makes a timeline handle look like a "
    "destination when it is not."
)

MIN_WORDS, MAX_WORDS = 3, 6
MAX_GAP_SECONDS = 1.5
"""The captain's `place_subtitles.py` values, kept."""

SEAM_TOLERANCE = 0.02
"""How far a seam may sit from a word boundary and still be that
boundary. Half a frame at 24fps.

A seam is a sum of range lengths and a word time comes from the
transcript, so the two agree to about a part in 1e15 and not exactly:
measured, a word ended at 2.8000000000000003 where the seam read 2.8,
and an exact comparison declined to flush. A card spanning the seam is
the body's last words joined to the closer's first, which is the one
thing the flush exists to prevent, so the comparison is made at the
resolution the picture actually cuts at."""


def _boundary(word: str, kind: str) -> bool:
    return bool(re.search(r"[.!?]$" if kind == "sentence" else r"[,;:\-]$",
                          word.strip()))


def caption_groups(words: Sequence[dict],
                   seams: Sequence[float] = ()) -> List[dict]:
    """Words grouped into caption cards, the captain's script's way.

    `seams` are reel seconds a card may not span - in practice the one
    second at which a reel jumps to its closer, from
    `reel_build.closer_seam`. Empty by default, which is every reel that
    has no closer.
    """
    groups: List[dict] = []
    buffer: List[dict] = []

    def flush():
        nonlocal buffer
        if buffer:
            groups.append({
                "text": " ".join(w["word"] for w in buffer).strip(),
                "start": buffer[0]["start"], "end": buffer[-1]["end"],
                "words": list(buffer),
            })
            buffer = []

    for word in words:
        if buffer and (word["start"] - buffer[-1]["end"]) > MAX_GAP_SECONDS:
            flush()
        elif buffer and any(
                buffer[-1]["end"] <= seam + SEAM_TOLERANCE
                and word["start"] >= seam - SEAM_TOLERANCE
                for seam in seams):
            flush()
        buffer.append(word)
        if len(buffer) >= MAX_WORDS:
            flush()
        elif (_boundary(word["word"], "sentence")
              and len(buffer) >= MIN_WORDS):
            flush()
        elif _boundary(word["word"], "clause") and len(buffer) >= MIN_WORDS:
            flush()
    flush()
    return groups


def reel_captions(transcript: dict, ranges: Sequence[Tuple[float, float]],
                  styles: Dict[Optional[str], dict], fps: float,
                  width: int, height: int,
                  closer_seam: Optional[float] = None) -> List[dict]:
    """Every caption for one reel: props to render, and where it goes.

    A caption whose speech was CUT returns nothing - `reel_time` gives
    None for a master second inside a removed take, so a dropped bad take
    takes its captions with it.

    **A range may sit anywhere on the master, in any order.** The last
    range is a reel's closing CTA and can come from earlier in the
    episode than its body (`reel_build.reel_ranges`), so a segment is
    kept when it touches ANY range rather than when it falls inside the
    envelope from the first range's start to the last range's end. That
    envelope was the old test, and on a reel whose CTA precedes its body
    it inverts - `start > end` - and silently drops every caption on the
    reel. `reel_time` was already order-following and needed no change.

    **A card does not straddle the CLOSER's seam, and nothing else is a
    seam.** `closer_seam` is the reel second the closer starts at, from
    `reel_build.closer_seam`, and is None on a reel that has no closer -
    which is every reel built before one could exist. The seams a
    bad-take cut leaves are deliberately NOT flush points: those join
    speech the editor made contiguous on purpose, so a card reading
    across one is a sentence as spoken. A closer is a different passage
    of the episode, so a card joining the body's last words to its first
    would be a sentence nobody said.

    A word's END is read with `at_end=True`. A range end lands exactly on
    a segment's `timeline_end`, which is exactly its last word's end, so
    read the half-open way the closing word of the reel - and of the
    closer - falls outside every range and is silently dropped.
    """
    from library.tools.reel_build import reel_time
    from library.tools.reel_proposal import bound_segments
    from library.tools.timeline_transcript import interpolate_untimed_words

    by_speaker: Dict[Optional[str], List[dict]] = {}
    segments = transcript.get("segments") or []
    for segment in sorted(segments,
                          key=lambda s: float(s["timeline_start"])):
        if not any(float(segment["timeline_end"]) > range_start
                   and float(segment["timeline_start"]) < range_end
                   for range_start, range_end in ranges):
            continue
        words = segment.get("words") or []
        timed = interpolate_untimed_words(words) if words else []
        for word in timed:
            at = reel_time(float(word["start"]), ranges)
            out = reel_time(float(word["end"]), ranges, at_end=True)
            if at is None or out is None or out <= at:
                continue
            by_speaker.setdefault(segment.get("speaker"), []).append(
                {"word": word["word"], "start": at, "end": out})

    captions: List[dict] = []
    for speaker, words in by_speaker.items():
        style = styles.get(speaker) or styles.get(None) or {}
        seams = () if closer_seam is None else (closer_seam,)
        for group in caption_groups(sorted(words, key=lambda w: w["start"]),
                                    seams):
            frames = max(int(round((group["end"] - group["start"]) * fps)), 2)
            captions.append({
                "speaker": speaker,
                "reel_start": group["start"],
                "reel_end": group["end"],
                "text": group["text"],
                "frames": frames,
                "props": {
                    "subtitles": [{
                        "text": group["text"], "startFrame": 0,
                        "endFrame": frames, "emphasisWords": [],
                        "words": [{
                            "word": w["word"],
                            "startFrame": max(int(round(
                                (w["start"] - group["start"]) * fps)), 0),
                            "endFrame": max(int(round(
                                (w["end"] - group["start"]) * fps)), 1),
                        } for w in group["words"]],
                    }],
                    "fps": fps, "width": width, "height": height,
                    "durationInFrames": frames, "style": style,
                    "safeArea": style.get("safeArea") or {},
                    "captionMaxWidth": style.get("captionMaxWidth", width),

                },
            })
    captions.sort(key=lambda c: c["reel_start"])

    # ── F7: enforce minimum caption duration ──
    # Never DROP a short card - that re-creates uncaptioned speech.
    # Extend its end to the minimum instead.
    min_dur = 0.5
    min_frames = max(int(round(min_dur * fps)), 2)
    for c in captions:
        if c["frames"] < min_frames:
            c["reel_end"] = c["reel_start"] + min_dur
            c["frames"] = min_frames
            c["props"]["durationInFrames"] = min_frames
            c["props"]["subtitles"][0]["endFrame"] = min_frames

    # ── F6: deconflict overlapping cards ──
    # Full-pass: repeatedly sweep until no overlaps remain, so three-way
    # overlaps and cards exposed by earlier resolution are caught.
    def _normalise(text):
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    def _is_bleed(a, b):
        """Two cards are mic bleed when their word sets substantially overlap."""
        wa, wb = _normalise(a["text"]), _normalise(b["text"])
        if not wa or not wb:
            return False
        return wa.issubset(wb) or wb.issubset(wa) or (
            len(wa & wb) / max(1, len(wa | wb)) > 0.5)

    changed = True
    while changed:
        changed = False
        result = []
        for c in captions:
            if not result:
                result.append(c)
                continue
            prev = result[-1]
            if c["reel_start"] >= prev["reel_end"]:
                # No overlap
                result.append(c)
                continue

            # Overlap detected
            if _is_bleed(prev, c):
                # Mic bleed: the primary mic picks up speech FIRST; bleed
                # arrives on the other mic with a slight delay.  The earlier
                # card's speaker field is the correct attribution because
                # that is the mic the words were spoken into.  If both start
                # at the same time, keep the earlier card (already prev).
                # Either way the later card is dropped - it is the bleed.
                if c["reel_start"] < prev["reel_start"]:
                    result[-1] = c
                # else: prev started first or tied, keep prev
                changed = True
            else:
                # Interruption: trim the later card's start past the overlap
                c["reel_start"] = prev["reel_end"]
                dur = c["reel_end"] - c["reel_start"]
                if dur < min_dur:
                    # Extend to minimum rather than dropping
                    c["reel_end"] = c["reel_start"] + min_dur
                    dur = min_dur
                c["frames"] = max(int(round(dur * fps)), min_frames)
                c["props"]["durationInFrames"] = c["frames"]
                c["props"]["subtitles"][0]["endFrame"] = c["frames"]
                result.append(c)
                changed = True
        captions = result

    return captions
