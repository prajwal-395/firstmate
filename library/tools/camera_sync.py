"""Which frame of one camera's file was recorded with which frame of another's.

A declared angle plan (`reel_angle_plan`) chooses a camera for a span of
the reel. The master only places a camera's picture where the edit used
it - on a podcast master that is while that person speaks - so showing
the listener, cutting ahead of the next speaker by a lead, or holding a
shot past the speaker change all need picture the master never placed.
The footage exists: both cameras rolled together. What is missing is the
offset between their files.

The master states it. Where the edit cuts from one camera to the other
with no gap, the two clips play the same instant on either side of the
cut, so the difference between their (source - timeline) offsets is the
recording offset between the two files. Measured 2026-09-25 on
geo-podcast: 63 of 68 speaker changes between LC4932.MXF and
LCATL0013.MXF agree on one offset (3.8 s); the rest are cuts where the
editor removed time, and they scatter.

So the offset is the value a strict majority of a file pair's cuts
agree on, within a frame. A pair whose cuts do not agree has NO sync -
never an average, never a guess - and the angle plan refuses by name
rather than show picture from the wrong second.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from itertools import pairwise

AGREEMENT_FRAMES = 1
"""Two cuts agree when their offsets differ by at most this many frames.
MECHANICAL: a cut position is itself rounded to a whole frame on each
side, so one frame of disagreement is quantisation, not a different
offset."""


def _frames(seconds: float, fps: float) -> int:
    return round(float(seconds) * fps)


def cut_votes(master_clips: Sequence, fps: float,
              angle_key=None) -> dict[tuple[str, str], list]:
    """Per (file A, file B): the offsets their gapless cuts imply.

    A vote `v` means frame `f` of B was recorded with frame `f - v` of A.
    Only picture clips on camera rows vote (`angle_key(clip)` non-empty),
    and only a cut between two different cameras: a same-camera cut says
    nothing about how two cameras line up.
    """
    if angle_key is None:
        from library.tools.reel_build import _angle_key as angle_key
    pictures = sorted(
        (clip for clip in master_clips or ()
         if getattr(clip, "track_type", "video") == "video"
         and angle_key(clip)),
        key=lambda clip: (clip.timeline_start, clip.timeline_end))
    votes: dict[tuple[str, str], list] = {}
    for before, after in pairwise(pictures):
        if angle_key(before) == angle_key(after):
            continue
        if before.source_file == after.source_file:
            continue
        gap = (_frames(after.timeline_start, fps)
               - _frames(before.timeline_end, fps))
        if abs(gap) > AGREEMENT_FRAMES:
            continue
        offset_before = (_frames(before.source_in, fps)
                         - _frames(before.timeline_start, fps))
        offset_after = (_frames(after.source_in, fps)
                        - _frames(after.timeline_start, fps))
        vote = offset_after - offset_before
        votes.setdefault((before.source_file, after.source_file),
                         []).append(vote)
        votes.setdefault((after.source_file, before.source_file),
                         []).append(-vote)
    return votes


def agreed_offset(votes: Sequence[int]):
    """The offset a strict majority agrees on within a frame, or None."""
    best = []
    for candidate in votes:
        cluster = [vote for vote in votes
                   if abs(vote - candidate) <= AGREEMENT_FRAMES]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < 2 or len(best) * 2 <= len(votes):
        return None
    ordered = sorted(best)
    return ordered[len(ordered) // 2]


def measure(master_clips: Sequence, fps: float, angle_key=None) -> dict:
    """`{"offsets": {(A, B): frames}, "pairs": [...]}` from the master.

    `offsets[(A, B)] = v` maps frame `f` of file A to frame `f + v` of
    file B. `pairs` records every file pair that voted - agreed or not,
    with its vote count - so a refusal can say what was measured.
    """
    offsets: dict[tuple[str, str], int] = {}
    pairs = []
    for (first, second), votes in sorted(
            cut_votes(master_clips, fps, angle_key).items()):
        agreed = agreed_offset(votes)
        pairs.append({"from": os.path.basename(first),
                      "to": os.path.basename(second),
                      "cuts": len(votes),
                      "offset_frames": agreed})
        if agreed is not None:
            offsets[(first, second)] = agreed
    return {"offsets": offsets, "pairs": pairs}
