"""Serialising placement into Resolve, and the rule that makes it necessary.

`assert_current_timeline` below is the enforcement; this is the reason,
moved here verbatim from `reel_subtitles.py` when that parallel module
was deleted. Losing the explanation with the module would leave a guard
nobody could justify, which is how a guard gets removed.
"""
import os
import time
import fcntl
from contextlib import contextmanager

PLACEMENT_REQUIRES_CURRENT = (
    "MediaPool.AppendToTimeline appends to the project's CURRENT "
    "timeline. Call project.SetCurrentTimeline(timeline) before placing, "
    "and read back what landed. AddTrack and SetTrackName DO act on the "
    "handle you pass, which is what makes a timeline handle look like a "
    "destination when it is not."
)
"""Why a timeline handle is not a destination.

Measured 2026-09-04: all 579 captions for sixteen reels were appended
while Reel 01 was current, so Reel 01 collected what it could and the
other fifteen ended up with an empty V3 - while every call returned True
and the run reported "placed 26/26" sixteen times.

The reel builder appeared to work only because `CreateEmptyTimeline`
makes its result current, so each reel happened to be current when its
own clips were appended. That is luck, not correctness, and it stops
being luck the moment anything is placed onto a timeline that already
exists.
"""

LOCK_FILE = "/tmp/resolve_placement.lock"

class ResolveRaceError(RuntimeError):
    pass

@contextmanager
def resolve_placement_lock():
    """Acquire a system-wide lock for Resolve placement operations."""
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def assert_current_timeline(project, expected_timeline):
    """Verify the current timeline is the expected one immediately before writing."""
    project.SetCurrentTimeline(expected_timeline)
    current = project.GetCurrentTimeline()
    if not current or current.GetUniqueId() != expected_timeline.GetUniqueId():
        raise ResolveRaceError(f"Timeline race: expected {expected_timeline.GetName()}, but got {current.GetName() if current else 'None'}. Mutator changed it!")
