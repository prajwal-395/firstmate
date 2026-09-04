"""Cutting an approved reel onto its own timeline, bad takes removed.

The captain, approving the second batch: *"the only thing i would say is
to remove the bad takes out so that the timelines of the reels are the
finished cut"*.

What is CUT and what is only FLAGGED
------------------------------------
A retake is easy to see and hard to prove.  Measured over the sixteen
approved reels, every loose rule removed real content:

- **containment alone** drops "make sure you're writing about that"
  against "Make sure you're writing why you're better than a competitor"
  at 1.00, because a short phrase is wholly inside a longer sentence,
- **without a same-speaker test** it drops Craig's actual question
  against Akshita's answer echoing it - and worse, mic bleed puts her
  words on his track, so the two sides can BOTH read as Craig,
- **without a duration test** it drops a 4.3s line to keep a 0.5s
  fragment of the same sentence.

So the cut rule is deliberately narrow, and everything it is not sure
about is REPORTED rather than removed:

    same speaker, both sides at least MIN_TAKE_SECONDS long,
    durations within DURATION_RATIO of each other,
    containment >= CUT_CONTAINMENT and Jaccard >= CUT_JACCARD,
    the second beginning within CUT_WINDOW_SECONDS of the first ending.

`redundant_takes` returns those.  `suspected_takes` returns the near
misses, which become MARKERS on the built timeline rather than edits -
the captain reviews in Resolve through
`library/tools/marker_feedback.py`, so a suspect belongs where they are
already looking.  A wrong cut is content they have to notice is missing;
a marker is one keystroke to act on.

**The LATER take is kept.** A retake exists because the first attempt was
flubbed - reel 02's first is "stuffed all their keywords with H1 tags",
which is backwards, and its retake says it correctly. That is a
JUDGEMENT, so it is recorded per cut and reversible rather than silent.

Sync
----
A reel is built from KEEP RANGES over the master's own timebase, not by
copying clips and closing gaps per track.  Both picture tracks are cut
against the same ranges and laid down at the same running offset, so
removing a take cannot slide one speaker against the other.

`tests/test_reel_build.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

CUT_CONTAINMENT = 0.75
CUT_JACCARD = 0.55
CUT_WINDOW_SECONDS = 30.0
MIN_TAKE_SECONDS = 1.5
DURATION_RATIO = 2.0
"""A cut needs all of these. Each one is here because dropping it removed
real content from the sixteen approved reels - see the module docstring."""

SUSPECT_CONTAINMENT = 0.60
"""Below the cut bar and above this, a marker is written instead."""

def required_tracks(clips) -> dict:
    """How many video AND AUDIO tracks a reel needs.

    **A reel needs one AUDIO track per picture track, and a new timeline
    has exactly ONE.** This is not symmetry for its own sake: measured
    2026-09-04, the first sixteen reels were built with two video tracks
    and one audio track, so Akshita's V1 clips carried their sound to A1
    and Craig's V2 clips had nowhere to put theirs. Resolve placed his
    PICTURE and discarded his AUDIO, returning True the whole way. Every
    reel reported success and played with one speaker silent - on a
    format whose entire unit is a two-speaker conversation.

    The tell is exact and worth knowing: A1's item count equalled V1's in
    all sixteen, and the V2 items contributed no audio at all.

    AGENTS.md 5 already warned about the reverse - placing V1 clips while
    extra tracks exist floods the timeline - so the rule is that track
    counts are DECLARED from the material, never left at the default.
    """
    indexes = sorted({c.track_index for c in clips})
    return {"video": max(indexes) if indexes else 1,
            "audio": max(indexes) if indexes else 1}


def audio_layout(master_clips) -> Dict[int, set]:
    """Which SOURCE FILES belong on each audio track, from the master.

    **Every source here carries FOUR audio channels**, and a linked
    append brings all of them - Resolve then spreads them across whatever
    audio tracks exist. On a two-track reel that put Akshita on A1 AND
    A2, and Craig on A2 underneath her.

    The captain's master already answers the question: A1 holds only
    their three files and A2 only Craig's four. So the layout is READ off
    the master rather than assumed, and `strays` names anything that
    landed outside it.

    This is the second half of the missing-audio defect. Adding the
    audio track stopped Craig being dropped; without this, he is present
    but mixed under a duplicate of Akshita.
    """
    out: Dict[int, set] = {}
    for clip in master_clips:
        out.setdefault(clip.track_index, set()).add(clip.source_file)
    return out


def strays(timeline, layout: Dict[int, set]) -> List:
    """Audio items sitting on a track their source does not belong to."""
    out = []
    for index, allowed in layout.items():
        for item in (timeline.GetItemListInTrack("audio", index) or []):
            pool_item = item.GetMediaPoolItem()
            path = pool_item.GetClipProperty("File Path") if pool_item else None
            if path and path not in allowed:
                out.append(item)
    return out


REEL_RESOLUTION = (1080, 1920)
"""Set EXPLICITLY on every reel timeline.

Measured in phase one: the PROJECT's own resolution is 3840x2160 and only
the existing timelines override it, so a timeline created through the API
inherits the horizontal UHD default. That is a silent wrong answer rather
than an error, and sixteen of them would be sixteen rebuilds."""


class ReelBuildError(RuntimeError):
    """A reel could not be built safely."""


@dataclass(frozen=True)
class Cut:
    """One take removed, and the one kept in its place."""

    dropped_start: float
    dropped_end: float
    dropped_text: str
    kept_start: float
    kept_end: float
    kept_text: str
    speaker: Optional[str]
    containment: float
    jaccard: float

    def as_dict(self) -> dict:
        return {
            "dropped_start": round(self.dropped_start, 2),
            "dropped_end": round(self.dropped_end, 2),
            "dropped_text": self.dropped_text,
            "kept_start": round(self.kept_start, 2),
            "kept_end": round(self.kept_end, 2),
            "kept_text": self.kept_text,
            "speaker": self.speaker,
            "containment": round(self.containment, 3),
            "jaccard": round(self.jaccard, 3),
            "kept": "the later take - a retake exists because the first "
                    "was flubbed",
        }


def _pair_scores(a: dict, b: dict) -> Tuple[float, float]:
    from library.tools.reel_proposal import _content_words

    wa, wb = _content_words(a.get("text", "")), _content_words(b.get("text", ""))
    if not wa or not wb:
        return 0.0, 0.0
    shared = len(wa & wb)
    return shared / min(len(wa), len(wb)), shared / len(wa | wb)


def _segments_in(start: float, end: float, transcript: dict) -> List[dict]:
    from library.tools.reel_proposal import bound_segments

    return sorted(
        (s for s in bound_segments(transcript)
         if s["timeline_end"] > start and s["timeline_start"] < end),
        key=lambda s: s["timeline_start"])


def _scan(start: float, end: float, transcript: dict,
          containment_floor: float, jaccard_floor: float,
          enforce_shape: bool) -> List[Cut]:
    inside = _segments_in(start, end, transcript)
    used, found = set(), []
    for index, first in enumerate(inside):
        if id(first) in used:
            continue
        for second in inside[index + 1:]:
            if id(second) in used or first.get("speaker") != second.get("speaker"):
                continue
            if second["timeline_start"] - first["timeline_end"] > CUT_WINDOW_SECONDS:
                break
            da = first["timeline_end"] - first["timeline_start"]
            db = second["timeline_end"] - second["timeline_start"]
            if enforce_shape:
                if da < MIN_TAKE_SECONDS or db < MIN_TAKE_SECONDS:
                    continue
                ratio = max(da, db) / min(da, db)
                if ratio > DURATION_RATIO:
                    continue
            containment, jaccard = _pair_scores(first, second)
            if containment >= containment_floor and jaccard >= jaccard_floor:
                used.add(id(first))
                used.add(id(second))
                found.append(Cut(
                    dropped_start=float(first["timeline_start"]),
                    dropped_end=float(first["timeline_end"]),
                    dropped_text=(first.get("text") or "").strip(),
                    kept_start=float(second["timeline_start"]),
                    kept_end=float(second["timeline_end"]),
                    kept_text=(second.get("text") or "").strip(),
                    speaker=first.get("speaker"),
                    containment=containment, jaccard=jaccard))
                break
    return found


def redundant_takes(start: float, end: float, transcript: dict) -> List[Cut]:
    """Takes confident enough to REMOVE."""
    return _scan(start, end, transcript, CUT_CONTAINMENT, CUT_JACCARD, True)


def suspected_takes(start: float, end: float, transcript: dict) -> List[Cut]:
    """Near misses. These become MARKERS, never edits."""
    confident = {(c.dropped_start, c.kept_start)
                 for c in redundant_takes(start, end, transcript)}
    loose = _scan(start, end, transcript, SUSPECT_CONTAINMENT, 0.0, False)
    return [c for c in loose if (c.dropped_start, c.kept_start) not in confident]


def keep_ranges(start: float, end: float,
                cuts: Sequence[Cut]) -> List[Tuple[float, float]]:
    """The reel's span with each dropped take taken out of it.

    Ranges are over the MASTER's timebase and are applied to both picture
    tracks identically, which is what keeps the two speakers in sync.
    """
    ranges = [(start, end)]
    for cut in sorted(cuts, key=lambda c: c.dropped_start):
        out: List[Tuple[float, float]] = []
        for a, b in ranges:
            if cut.dropped_end <= a or cut.dropped_start >= b:
                out.append((a, b))
                continue
            if a < cut.dropped_start:
                out.append((a, cut.dropped_start))
            if cut.dropped_end < b:
                out.append((cut.dropped_end, b))
        ranges = out
    return [(a, b) for a, b in ranges if b - a > 0.04]


def reel_time(master_time: float,
              ranges: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Where a MASTER second lands on the reel, or None if it was cut.

    A reel is its keep ranges laid end to end, so a caption timed against
    the master has to come through the same arithmetic the picture did -
    otherwise removing a bad take slides every caption after it out of
    sync with the speech it belongs to.
    """
    cursor = 0.0
    for range_start, range_end in ranges:
        if range_start <= master_time < range_end:
            return cursor + (master_time - range_start)
        cursor += range_end - range_start
    return None


def placements(ranges: Sequence[Tuple[float, float]],
               clips: Sequence) -> List[dict]:
    """Where each master clip lands on the reel, in seconds.

    One entry per (keep range, overlapping clip). `record` is the running
    offset on the REEL, so the ranges close up and both tracks move
    together.
    """
    out: List[dict] = []
    cursor = 0.0
    for range_start, range_end in ranges:
        for clip in clips:
            overlap_start = max(clip.timeline_start, range_start)
            overlap_end = min(clip.timeline_end, range_end)
            if overlap_end - overlap_start <= 0.04:
                continue
            into_clip = overlap_start - clip.timeline_start
            out.append({
                "clip": clip,
                "source_in": clip.source_in + into_clip,
                "source_out": clip.source_in + into_clip + (overlap_end - overlap_start),
                "record": cursor + (overlap_start - range_start),
                "track_index": clip.track_index,
                "speaker": clip.speaker,
            })
        cursor += range_end - range_start
    return out
