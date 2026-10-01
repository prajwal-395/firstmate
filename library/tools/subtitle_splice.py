"""Putting a region's captions back, and proving nothing else moved.

The captain's worked example ends with the hard half: *"splice the
refreshed subtitles back in."*  Planning a region was never the problem -
4.01 is block-local and a region-scoped plan reproduces its blocks byte
for byte.  Getting the result back into the stored plan without
disturbing the rest of the timeline is.

Two rules, and the first one is the whole safety argument.

**A splice that changes a block's DURATION is refused.**
`step_2_05_mesh_spine/post_bridge.py` recomputes every block's
`timeline_start` from a cumulative cursor over block durations, so a
fragment that is even slightly longer than the one it replaces silently
moves every block after it.  Measured on project 001: a change to block
10 would re-time 5.5s of a 56.6s timeline; a change to the hook would
re-time all of it.  The refusal names the block and both durations,
because "the splice failed" is not actionable and "block 10: 6.82s ->
7.32s" is.

That boundary is the difference between a cheap correction and a full
re-plan, and this module will not quietly do the second while being
asked for the first.

**A splice replaces WHOLE BLOCKS, never a time window.**
The unit is `spine_block_position` and not an interval, even though the
caller addresses the work with an interval.  A region is resolved to the
blocks it touches (`region.resolve`), and then those blocks' entries are
replaced entirely.  Replacing by time instead would let a region that
bisects a block leave half its captions from one plan and half from
another, with the grouping decided against two different neighbourhoods -
cards that overlap, or a gap where the two halves disagree about where a
sentence broke.  Grouping is a block-wide decision, so blocks are the
unit that can be exchanged.  The exchange itself is
`library/tools/plan_splice.py`, shared with every block-keyed planner;
this module adds what is captions' own, the duration refusal.

What this module does NOT do
----------------------------
It does not write state.  `library/tools/state_splice.py` owns that, and
owns it alone.  This module is pure: plan in, plan out, refusal in
between.  Keeping the arithmetic separable from the write is what lets
the tests drive every refusal without a project on disk.

`tests/test_subtitle_splice.py`.
"""

from __future__ import annotations

from typing import List, Sequence

from library.tools import plan_splice
from library.tools.plan_splice import SpliceRefused

KEY = "spine_block_position"
"""The field a caption entry names its spine block with."""

__all__ = ["SpliceRefused", "assert_durations_preserved", "splice_plan",
           "outside_region", "splice_report"]


def assert_durations_preserved(stored_structure: Sequence[dict],
                               fresh_structure: Sequence[dict]) -> None:
    """Refuse a fragment that would re-time the timeline.

    Compared per block by position rather than pairwise by index, so a
    fragment that has DROPPED or ADDED a block is caught as well as one
    that stretched a block it kept.  A count change preserves total
    duration while renumbering every position downstream, and position is
    the join key for 4.01, 4.05 and 5.04 alike.
    """
    stored = {b["position"]: b for b in stored_structure}
    fresh = {b["position"]: b for b in fresh_structure}

    problems = []
    for position, block in fresh.items():
        was = stored.get(position)
        if was is None:
            problems.append(
                f"block {position}: not in the stored spine, so the splice "
                f"would ADD a block and renumber everything after it")
            continue
        before = was.get("duration_seconds")
        after = block.get("duration_seconds")
        if before is None or after is None:
            problems.append(
                f"block {position}: duration_seconds is missing on "
                f"{'the stored' if before is None else 'the fresh'} block, "
                f"so it cannot be shown to be unchanged")
            continue
        if abs(float(before) - float(after)) > 1e-6:
            problems.append(
                f"block {position}: {before}s -> {after}s")

    if problems:
        raise SpliceRefused(
            "this splice would change how long the timeline is",
            "mesh_spine lays every block end to end - so every block "
            "after the change would move:\n  - "
            + "\n  - ".join(problems),
            "a duration-changing correction is a re-plan, not a splice: "
            "re-run the subtitles plan at project scope, then the steps "
            "downstream of it")


def splice_plan(stored_entries: Sequence[dict],
                fresh_entries: Sequence[dict],
                positions: Sequence) -> List[dict]:
    """Replace exactly `positions`' captions with `fresh_entries`.

    `plan_splice.splice_entries` keyed on the caption's block; see there
    for why `positions` is explicit.
    """
    return plan_splice.splice_entries(stored_entries, fresh_entries,
                                      positions, KEY, "id")


def outside_region(entries: Sequence[dict], positions: Sequence) -> List[dict]:
    """The captions a splice must leave byte-identical."""
    return plan_splice.outside_region(entries, positions, KEY)


def splice_report(stored_entries: Sequence[dict],
                  merged_entries: Sequence[dict],
                  positions: Sequence) -> dict:
    """What the splice did; `outside_unchanged` is MEASURED."""
    return plan_splice.splice_report(stored_entries, merged_entries,
                                     positions, KEY)
