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
unit that can be exchanged.

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


class SpliceRefused(ValueError):
    """The splice would have changed something outside the region."""


def _by_position(entries: Sequence[dict]) -> dict:
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry["spine_block_position"], []).append(entry)
    return grouped


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
            "This splice would change how long the timeline is, and "
            "mesh_spine lays every block end to end - so every block after "
            "the change would move:\n  - "
            + "\n  - ".join(problems)
            + "\n\nA duration-preserving correction splices; a "
              "duration-changing one is a re-plan, and re-planning is "
              "`subtitles.plan` at project scope followed by the steps "
              "downstream of it."
        )


def splice_plan(stored_entries: Sequence[dict],
                fresh_entries: Sequence[dict],
                positions: Sequence) -> List[dict]:
    """Replace exactly `positions`' entries with `fresh_entries`.

    `positions` is passed explicitly rather than inferred from
    `fresh_entries`, and that is deliberate: a region can legitimately
    resolve to a block whose fresh plan is EMPTY - a re-index that found
    the passage was silence - and inferring the set from what came back
    would silently keep the old captions for exactly that case.

    Returns a new list in timeline order.  The input is not mutated.
    """
    targets = set(positions)

    stray = {e["spine_block_position"] for e in fresh_entries} - targets
    if stray:
        raise SpliceRefused(
            f"the fresh plan carries entries for block(s) "
            f"{sorted(stray, key=str)}, which are not in the region being "
            f"spliced ({sorted(targets, key=str)}). A splice may only write "
            f"the blocks it was asked for."
        )

    kept = [dict(e) for e in stored_entries
            if e["spine_block_position"] not in targets]
    merged = kept + [dict(e) for e in fresh_entries]
    merged.sort(key=lambda e: (e["timeline_start"], str(e["id"])))
    return merged


def outside_region(entries: Sequence[dict], positions: Sequence) -> List[dict]:
    """The entries a splice must leave byte-identical.

    The evidence half of this module.  A caller proves a splice was
    bounded by comparing this before and after; it is here rather than in
    a test so the demonstration and the tests measure the same thing.
    """
    targets = set(positions)
    return [e for e in entries if e["spine_block_position"] not in targets]


def splice_report(stored_entries: Sequence[dict],
                  merged_entries: Sequence[dict],
                  positions: Sequence) -> dict:
    """What the splice did, in numbers a reader can check.

    `outside_unchanged` is the claim that matters, and it is COMPUTED
    here rather than asserted by the caller - a splice that reports its
    own success without measuring it is the vacuous gate this repository
    keeps removing.
    """
    before = outside_region(stored_entries, positions)
    after = outside_region(merged_entries, positions)
    was = _by_position(stored_entries)
    now = _by_position(merged_entries)
    return {
        "positions": sorted(positions, key=str),
        "entries_before": len(stored_entries),
        "entries_after": len(merged_entries),
        "replaced": {str(p): {"before": len(was.get(p, [])),
                              "after": len(now.get(p, []))}
                     for p in sorted(positions, key=str)},
        "outside_count_before": len(before),
        "outside_count_after": len(after),
        "outside_unchanged": before == after,
    }
