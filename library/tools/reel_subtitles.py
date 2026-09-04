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


def _boundary(word: str, kind: str) -> bool:
    return bool(re.search(r"[.!?]$" if kind == "sentence" else r"[,;:\-]$",
                          word.strip()))


def caption_groups(words: Sequence[dict]) -> List[dict]:
    """Words grouped into caption cards, the captain's script's way."""
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
                  width: int, height: int) -> List[dict]:
    """Every caption for one reel: props to render, and where it goes.

    A caption whose speech was CUT returns nothing - `reel_time` gives
    None for a master second inside a removed take, so a dropped bad take
    takes its captions with it.
    """
    from library.tools.reel_build import reel_time
    from library.tools.reel_proposal import bound_segments
    from library.tools.timeline_transcript import interpolate_untimed_words

    by_speaker: Dict[Optional[str], List[dict]] = {}
    for segment in sorted(bound_segments(transcript),
                          key=lambda s: s["timeline_start"]):
        start, end = ranges[0][0], ranges[-1][1]
        if segment["timeline_end"] <= start or segment["timeline_start"] >= end:
            continue
        words = segment.get("words") or []
        timed = interpolate_untimed_words(words) if words else []
        for word in timed:
            at = reel_time(float(word["start"]), ranges)
            out = reel_time(float(word["end"]), ranges)
            if at is None or out is None or out <= at:
                continue
            by_speaker.setdefault(segment.get("speaker"), []).append(
                {"word": word["word"], "start": at, "end": out})

    captions: List[dict] = []
    for speaker, words in by_speaker.items():
        style = styles.get(speaker) or styles.get(None) or {}
        for group in caption_groups(sorted(words, key=lambda w: w["start"])):
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
                },
            })
    captions.sort(key=lambda c: c["reel_start"])
    return captions
