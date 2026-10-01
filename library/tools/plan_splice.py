"""Putting a region's re-planned entries back into a stored plan.

The one splice for every block-keyed plan.  Captions
(`subtitle_splice`) were first; the effect, sound and transition planners
follow, and each of their plans is the same shape - a list of entries,
each naming the spine block it sits on.  A second splice per planner
would be a second answer to "what may a region redo touch", so there is
one, keyed by whichever field the planner names its block with.

**A splice replaces WHOLE BLOCKS, never a time window.**  The caller
addresses the work with an interval; `region.resolve` turns it into the
blocks it touches, and those blocks' entries are replaced entirely.
Replacing by time instead would let a region that bisects a block keep
half its decisions from one plan and half from another, decided against
two different neighbourhoods.

**Entries outside the region are byte-identical afterwards, and the
report MEASURES that** rather than asserting it - `outside_unchanged` is
computed from the two plans, never handed in by the caller.

Pure: plan in, plan out, refusal in between.  Writing state is
`library/tools/state_splice.py`'s job alone.

`tests/test_plan_splice.py`.
"""

from __future__ import annotations

from typing import List, Sequence

from library.tools.ren_refusal import RenRefusal


class SpliceRefused(RenRefusal):
    """The splice would have changed something outside the region."""


def number_within_block(entries: Sequence[dict], key: str, id_key: str,
                        prefix: str) -> None:
    """Number entries WITHIN their block, in list order, in place.

    A splice is only provably bounded if ids are block-local: under a
    run-global counter, re-planning one region renumbers every entry
    after it, so a splice that left every out-of-region decision alone
    would still report them all as changed.  `vfx_7_002` is the second
    effect on block 7 however many any other block carries - the same
    fix 4.01 made for caption ids.
    """
    from library.tools.subtitle_segment_id import slug

    counters = {}
    for entry in entries:
        token = slug(entry[key], "noblock")
        counters[token] = counters.get(token, 0) + 1
        entry[id_key] = f"{prefix}_{token}_{counters[token]:03d}"


def _by_position(entries: Sequence[dict], key: str) -> dict:
    grouped = {}
    for entry in entries:
        grouped.setdefault(str(entry[key]), []).append(entry)
    return grouped


def splice_entries(stored_entries: Sequence[dict],
                   fresh_entries: Sequence[dict],
                   positions: Sequence,
                   key: str,
                   id_key: str,
                   start_key: str = "timeline_start") -> List[dict]:
    """Replace exactly `positions`' entries with `fresh_entries`.

    `key` is the field naming an entry's spine block
    (`spine_block_position` for captions, `target_block_position` for
    effects); `id_key` is the entry's own id, the tie-break when two
    entries start together; `start_key` is where an entry starts on the
    timeline (`timeline_in` for sounds).  Positions compare as strings, because a
    spine position is an int on one block and `"hook"` on another and the
    planners' entries carry whichever the model wrote.

    `positions` is explicit rather than inferred from `fresh_entries`: a
    region can legitimately re-plan to NOTHING - the editor decided the
    passage wants stillness - and inferring the set from what came back
    would silently keep the old entries for exactly that case.

    Returns a new list in timeline order.  The inputs are not mutated.
    """
    targets = {str(p) for p in positions}

    stray = {str(e[key]) for e in fresh_entries} - targets
    if stray:
        raise SpliceRefused(
            f"the fresh plan carries entries for block(s) {sorted(stray)}, "
            f"which are not in the region being spliced ({sorted(targets)})",
            "a splice may only write the blocks it was asked for - "
            "writing further would disturb decisions outside the region",
            "re-plan exactly the spliced region so the fresh entries "
            "cover its blocks and no others, then splice again")

    kept = [dict(e) for e in stored_entries if str(e[key]) not in targets]
    merged = kept + [dict(e) for e in fresh_entries]
    merged.sort(key=lambda e: (e[start_key], str(e[id_key])))
    return merged


def outside_region(entries: Sequence[dict], positions: Sequence,
                   key: str) -> List[dict]:
    """The entries a splice must leave byte-identical.

    Here rather than in a test so the report and the tests measure the
    same thing.
    """
    targets = {str(p) for p in positions}
    return [e for e in entries if str(e[key]) not in targets]


def splice_report(stored_entries: Sequence[dict],
                  merged_entries: Sequence[dict],
                  positions: Sequence,
                  key: str) -> dict:
    """What the splice did, in numbers a reader can check."""
    before = outside_region(stored_entries, positions, key)
    after = outside_region(merged_entries, positions, key)
    was = _by_position(stored_entries, key)
    now = _by_position(merged_entries, key)
    ordered = sorted(positions, key=str)
    return {
        "positions": ordered,
        "entries_before": len(stored_entries),
        "entries_after": len(merged_entries),
        "replaced": {str(p): {"before": len(was.get(str(p), [])),
                              "after": len(now.get(str(p), []))}
                     for p in ordered},
        "outside_count_before": len(before),
        "outside_count_after": len(after),
        "outside_unchanged": before == after,
    }
