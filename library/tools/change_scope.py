"""What a change actually touches: the items and the intervals, named.

PERF-003 asks for edit complexity proportional to the changed scope.  A
scope that is only implied cannot be measured, reused, or reported, so
this module makes it a value: the affected ITEMS (by identity) and the
affected INTERVALS (by record frame), together with what the change
REUSED rather than re-did.

The scope is computed from a diff, never from a plan.  A plan says what
should be true; a diff says what changed.  Only the diff can name the
items a change moved, and only the diff can say which tracks it left
alone.  Every quantity here is counted, never estimated: an unmeasured
quantity is ``None`` (unknown), never ``0`` (measured as nothing).

Two scopes are compared by ``compare``:

- the SCOPED strategy, which touches only the changed items;
- the BULK strategy, which re-derives everything.

Neither wins by assumption.  ``compare`` answers from the counts, and
answers "unmeasured" when the counts cannot say.  A caller that cannot
say which is cheaper has not measured the question, and saying so is the
honest answer (AGENTS.md 10.4, the reporting rules of the punch list:
"never combine denominators", "unmeasured is never zero").

This module owns no Resolve call and reads no file: it is pure
arithmetic over counts a caller already has.  The route that ACTS on a
scope is its own owner (``reel_incremental``); the module that says
WHETHER a scope is worth acting on is this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: A tracked item's identity: a stable key naming WHAT changed, not
#: where it was.  The key is a caller-chosen string (a segment id, a
#: clip's unique id, a caption's content digest) so the same scope can
#: name items from different rows.
ItemKey = str

#: An interval on the reel timeline: a row and the record frames it
#: covers.  ``(row_label, start_frame, end_frame)``; the end is
#: EXCLUSIVE, matching the record spans the placement code uses.
Interval = Tuple[str, int, int]


@dataclass
class Scope:
    """The affected items and intervals of one change.

    ``items`` maps an item key to the row it lives on.  ``intervals``
    are the record-frame ranges the change touches, by row label.
    ``reused`` counts what the change left alone, per kind: the assets
    whose bytes did not change, the tracks whose rows did not move, and
    the derived data (grades, decoded media) that was carried over
    rather than recomputed.

    A scope with no items and no intervals is a NO-OP scope: the change
    asked for nothing that the timeline does not already hold.  It is
    the strongest possible statement that a rebuild is wasted work.
    """

    items: Dict[ItemKey, str] = field(default_factory=dict)
    intervals: List[Interval] = field(default_factory=list)
    reused: Dict[str, int] = field(default_factory=dict)
    #: What the change was, in the caller's words.  Carried so a
    #: receipt can say what was scoped without the reader guessing.
    kind: str = ""
    #: The change's own words, when the caller has them.
    reason: str = ""

    # ── construction ───────────────────────────────────────────────

    def add_item(self, key: ItemKey, row: str = "") -> "Scope":
        """Name one affected item.  The same key twice is one item."""
        self.items[str(key)] = str(row or "")
        return self

    def add_interval(self, row: str, start: int, end: int) -> "Scope":
        """Name one affected interval.  An empty interval is ignored."""
        start, end = int(start), int(end)
        if end <= start:
            return self
        self.intervals.append((str(row), start, end))
        return self

    def note_reuse(self, kind: str, count: int) -> "Scope":
        """Record that ``count`` things of ``kind`` were carried over."""
        if count <= 0:
            return self
        self.reused[str(kind)] = self.reused.get(str(kind), 0) + int(count)
        return self

    # ── reading ────────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        """Whether the change touches nothing."""
        return not self.items and not self.intervals

    @property
    def affected_rows(self) -> Tuple[str, ...]:
        """The rows the change touches, in first-seen order."""
        rows: List[str] = []
        for row in self.items.values():
            if row and row not in rows:
                rows.append(row)
        for row, _start, _end in self.intervals:
            if row not in rows:
                rows.append(row)
        return tuple(rows)

    @property
    def affected_frames(self) -> int:
        """The record frames the change's intervals cover, summed.

        Intervals on DIFFERENT rows are counted separately: a picture
        row's frames and a caption row's frames are different frames of
        different pictures, and adding them would report a number
        larger than any real timeline.
        """
        by_row: Dict[str, int] = {}
        for row, start, end in self.intervals:
            by_row[row] = by_row.get(row, 0) + (end - start)
        return sum(by_row.values())

    @property
    def reused_total(self) -> int:
        """Everything the change carried over rather than redid."""
        return sum(self.reused.values())

    def as_dict(self) -> dict:
        """The scope as plain data, for a receipt or a ledger row."""
        return {
            "kind": self.kind,
            "reason": self.reason,
            "items": len(self.items),
            "intervals": len(self.intervals),
            "affected_rows": list(self.affected_rows),
            "affected_frames": self.affected_frames,
            "reused": dict(self.reused),
            "reused_total": self.reused_total,
        }

    def describe(self) -> str:
        """One line naming the scope, for a refusal or a receipt."""
        if self.is_empty:
            return "the change touches no item and no interval"
        parts = []
        if self.items:
            parts.append(f"{len(self.items)} item(s)")
        if self.intervals:
            parts.append(f"{len(self.intervals)} interval(s)")
        if self.reused:
            parts.append(
                f"{self.reused_total} reused")
        return ", ".join(parts)


def compare(scoped: Scope, bulk: Scope) -> dict:
    """Which strategy touches less, from counts a caller measured.

    ``scoped`` is the change applied to only what changed; ``bulk`` is
    the change applied by re-deriving everything.  Both are SCOPES, so
    the comparison is like for like: the same denominators, measured
    the same way.

    Two scopes are compared on each denominator SEPARATELY.  Items and
    frames are different units and are never added together - a sum of
    "one card and forty-eight frames" against "forty cards" compares
    nothing (the punch list's rule: never combine denominators).

    The answer is one of:

    - ``"scoped"`` - the scoped strategy touches less on every
      denominator both measured, and less on at least one;
    - ``"bulk"`` - the bulk strategy touches less the same way;
    - ``"mixed"`` - the strategies trade off: each touches less on a
      denominator the other touches more on.  Neither is uniformly
      cheaper, and the caller decides - the honest answer, not a
      manufactured winner;
    - ``"tie"`` - they touch the same;
    - ``"unmeasured"`` - a denominator is missing, so the comparison
      cannot be made.  Never a guess, and never zero standing in for
      unknown.
    """
    scoped_work = _work(scoped)
    bulk_work = _work(bulk)
    if scoped_work is None or bulk_work is None:
        return {
            "winner": "unmeasured",
            "reason": ("the work of one strategy was not measured, so "
                       "the comparison cannot be made"),
            "scoped_work": scoped_work,
            "bulk_work": bulk_work,
        }
    shared = sorted(set(scoped_work) & set(bulk_work))
    if not shared:
        return {
            "winner": "unmeasured",
            "reason": ("the two strategies were measured on different "
                       "denominators, so they cannot be compared"),
            "scoped_work": scoped_work,
            "bulk_work": bulk_work,
        }
    scoped_better = sum(
        1 for key in shared if scoped_work[key] < bulk_work[key])
    bulk_better = sum(
        1 for key in shared if bulk_work[key] < scoped_work[key])
    if scoped_better and not bulk_better:
        winner = "scoped"
    elif bulk_better and not scoped_better:
        winner = "bulk"
    elif scoped_better and bulk_better:
        winner = "mixed"
    else:
        winner = "tie"
    return {
        "winner": winner,
        "reason": _reason(winner, scoped_work, bulk_work, shared),
        "scoped_work": scoped_work,
        "bulk_work": bulk_work,
    }


def _work(scope: Scope) -> Optional[dict]:
    """The work a scope implies, per denominator, or ``None`` if unknown.

    A scope measures work by the items it touches and the frames it
    moves - two denominators, kept apart.  Where a scope carries no
    counts at all - the caller measured nothing - the work is UNKNOWN
    rather than zero, because "touches nothing" and "touches nothing
    because nobody looked" are different facts.
    """
    if scope is None:
        return None
    work = {"items": len(scope.items), "frames": scope.affected_frames}
    if scope.is_empty and not scope.reused:
        return None
    return work


def _reason(winner: str, scoped: dict, bulk: dict,
            shared: Sequence[str]) -> str:
    if winner == "scoped":
        return ("the scoped change touches less on every shared "
                "denominator (" + ", ".join(
                    f"{key} {scoped[key]} vs {bulk[key]}"
                    for key in shared) + ")")
    if winner == "bulk":
        return ("the bulk change touches less on every shared "
                "denominator (" + ", ".join(
                    f"{key} {bulk[key]} vs {scoped[key]}"
                    for key in shared) + ") - re-deriving everything is "
                "cheaper")
    if winner == "mixed":
        return ("the two strategies trade off across the shared "
                "denominators (" + ", ".join(
                    f"{key} scoped {scoped[key]} vs bulk {bulk[key]}"
                    for key in shared) + ") - neither is uniformly "
                "cheaper")
    return ("both strategies touch the same on every shared denominator (" +
            ", ".join(shared) + ") - neither is cheaper")


def merge(*scopes: Scope) -> Scope:
    """One scope carrying every scope's items, intervals and reuse."""
    out = Scope()
    for scope in scopes:
        if scope is None:
            continue
        out.items.update(scope.items)
        out.intervals.extend(scope.intervals)
        for kind, count in scope.reused.items():
            out.reused[kind] = out.reused.get(kind, 0) + count
        if not out.kind:
            out.kind = scope.kind
        if not out.reason:
            out.reason = scope.reason
    return out


__all__ = [
    "Interval",
    "ItemKey",
    "Scope",
    "compare",
    "merge",
]
