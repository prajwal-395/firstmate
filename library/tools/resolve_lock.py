"""Serialising placement into Resolve, and the rule that makes it necessary.

`assert_current_timeline` below is the enforcement; this is the reason,
moved here verbatim from `reel_subtitles.py` when that parallel module
was deleted. Losing the explanation with the module would leave a guard
nobody could justify, which is how a guard gets removed.

What guards placement, and what does not, measured 2026-09-12
-------------------------------------------------------------
`assert_current_timeline` is the only guard here, and it is wired: 22
call sites across `reel_build`, step 6.01's `resolve_build_timeline`,
`segment_renderer` and `execution/resolve_render`. It re-asserts and
reads back immediately before a write, so it closes the window between
`SetCurrentTimeline` and the append WITHIN one process.

It does NOT serialise two processes, and must not be read as though it
did: a second process re-asserts its own timeline just as happily, and
the interleaving is decided by whoever calls last. Only a lock could do
that, and the one that used to live here was removed - see the note
below - because nothing entered it.

Two measurements, from opposite sides, agreed on that:

* From outside, while another lane built into the captain's project:
  366 samples over twelve minutes, two seconds apart, found the lock
  UNHELD every time while that lane created a staging timeline,
  promoted it, and moved the current timeline six times.
* From inside: a process HELD the lock exclusively for two minutes
  while a sibling lane worked, and watched that lane move the current
  timeline three times regardless. `flock` behaved exactly as written.
  The acquisition cost 0.000s because there was no one to wait for.

So the lock was not merely untested. It was exercised and measured to
protect nothing, because the state it guarded is reached by a route
that does not pass through it. A mutual-exclusion primitive only
excludes parties that take it.

What contention there IS happens a layer down, in Resolve itself: its
scripting API serialises on the application, so a lane placing clips
stalls every other lane's getters for as long as the placement runs. A
read-only reader cannot take the placement lock to protect itself and
would not be helped by it - it does not place - so a reader racing a
writer is unmediated by design.

`docs/DUAL_WORKFLOW_SYNC_2026-09-12.md` is the measurement.
"""

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

class ResolveRaceError(RuntimeError):
    pass


# `resolve_placement_lock()` was here: a `@contextmanager` taking an
# exclusive `flock` on /tmp/resolve_placement.lock.  Nothing ever entered
# it - zero callers in library, tests, docs or scripts - so the module
# read as though placement into Resolve were serialised across processes
# when the only thing actually guarding it was `assert_current_timeline`
# below, which is a per-call check and not a lock.  A guard nobody enters
# is worse than no guard, because it reads as coverage (AGENTS.md 10.4).
# Removed 2026-09-12.  If cross-process serialisation is ever needed, note
# that Resolve is ONE shared instance and every lane drives the same app,
# so the lock would have to be agreed with the other lanes rather than
# taken unilaterally.

def assert_current_timeline(project, expected_timeline):
    """Verify the current timeline is the expected one immediately before writing."""
    project.SetCurrentTimeline(expected_timeline)
    current = project.GetCurrentTimeline()
    if not current or current.GetUniqueId() != expected_timeline.GetUniqueId():
        raise ResolveRaceError(f"Timeline race: expected {expected_timeline.GetName()}, but got {current.GetName() if current else 'None'}. Mutator changed it!")
