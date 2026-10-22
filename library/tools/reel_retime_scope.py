"""What a retime touches: the passage it speeds up, and the clock it moves.

A retime is a CLOCK change.  The passage plays at another rate, so it
occupies fewer (or more) record frames, and every placement after it
moves by the difference.  That is why the scoped route refused a
retime: it compared the plan's rows with the timeline's rows for
IDENTITY, and a retime is not identity - it is one passage plus a
shift.

This module makes the shift a VALUE, so the scoped route can act on it:

- ``passages_from_placements`` names the passages the plan retimes, in
  record frames, with the rate that produced them.
- ``diff_rows`` compares the plan's rows with the live rows and
  classifies every difference as either the RETIMED item (the passage
  itself: same start, a new length) or a MOVED item (the clock shifted
  it: the same length, a new start).  Anything else is not a retime
  this route can scope, and it says so.

The classification is PROVABLE, not heuristic:

- the shifted items all move by ONE number, the clock's own delta;
- that delta is the passage's own length change, so it is derived from
  the passage rather than guessed from the tail;
- the passage's new length is the old length divided by its rate (to
  the frame), so the rate explains the change instead of accompanying
  it;
- the shifted items all start at or after the passage's old end, so
  the clock moved them FORWARD, never backward over the passage.

Anything that fails one of those is refused with the reason.  A
refusal here is not a failure of the idea: it is the engine declining
to place a reel it cannot prove it placed correctly.

The scope is ONE clip.  A passage that covers more than one clip
splits the clips it crosses, and the row's item COUNT changes - a
different shape, and one this route does not claim.  It refuses, by
name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

#: A row's item, as the inventories carry it: `(start, end, source)`.
#: The end is EXCLUSIVE, matching the record spans the placement code
#: uses.
Item = Tuple[int, int, str]


class RetimeShapeError(Exception):
    """The change is not a retime shape this route can scope.

    The message names what did not fit, because a refusal that says
    only "no" leaves the reader to work out which of the shape's
    conditions failed.
    """


@dataclass(frozen=True)
class Passage:
    """One retimed passage, in the plan's own record frames."""

    rate: float
    record_start: int
    record_end: int
    source_in: float = 0.0
    source_out: float = 0.0

    @property
    def record_frames(self) -> int:
        return int(self.record_end) - int(self.record_start)

    @property
    def span(self) -> Tuple[int, int]:
        return (int(self.record_start), int(self.record_end))

    def as_dict(self) -> dict:
        return {
            "rate": float(self.rate),
            "record_start_frame": int(self.record_start),
            "record_end_frame": int(self.record_end),
            "record_frames": self.record_frames,
            "source_in": float(self.source_in),
            "source_out": float(self.source_out),
        }


@dataclass(frozen=True)
class ItemChange:
    """One item the retime changed, and how."""

    row: Tuple[str, int]
    old_span: Tuple[int, int]
    new_span: Tuple[int, int]
    kind: str  # "retimed" | "moved"

    @property
    def row_label(self) -> str:
        return f"{self.row[0]}{self.row[1]}"

    def as_dict(self) -> dict:
        return {
            "row": list(self.row),
            "kind": self.kind,
            "old_start_frame": int(self.old_span[0]),
            "old_end_frame": int(self.old_span[1]),
            "new_start_frame": int(self.new_span[0]),
            "new_end_frame": int(self.new_span[1]),
        }


@dataclass
class RetimeDiff:
    """The whole of a retime's effect: the passages, the shift, the items."""

    passages: Tuple[Passage, ...] = ()
    delta: int = 0
    changes: Tuple[ItemChange, ...] = ()
    reused: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def retimed(self) -> Tuple[ItemChange, ...]:
        return tuple(c for c in self.changes if c.kind == "retimed")

    @property
    def moved(self) -> Tuple[ItemChange, ...]:
        return tuple(c for c in self.changes if c.kind == "moved")

    @property
    def rows(self) -> Tuple[Tuple[str, int], ...]:
        seen: List[Tuple[str, int]] = []
        for change in self.changes:
            if change.row not in seen:
                seen.append(change.row)
        return tuple(seen)

    def as_dict(self) -> dict:
        return {
            "delta_frames": int(self.delta),
            "passages": [p.as_dict() for p in self.passages],
            "changed_items": len(self.changes),
            "retimed_items": len(self.retimed),
            "moved_items": len(self.moved),
            "reused_items": int(self.reused),
            "rows": [list(row) for row in self.rows],
            "items": [c.as_dict() for c in self.changes],
        }


def passages_from_placements(placements_list, fps: float) -> Tuple[Passage, ...]:
    """The passages the plan retimes, in record frames.

    A passage that spans a picture row and its speech row produces one
    placement on each, and the two are the SAME passage: they share a
    rate and a record span.  They are therefore one entry here, and a
    passage that covers more than one clip - which splits the clips it
    crosses and changes the row's item count - is refused here rather
    than scoped as if it were one clip.

    `fps` is accepted for symmetry with the rest of the placement code
    and is not needed: the placements already carry record FRAMES.
    """
    by_span: Dict[Tuple[float, int, int], Passage] = {}
    rates: Dict[float, set] = {}
    for place in placements_list or ():
        if not isinstance(place, Mapping) or "rate" not in place:
            continue
        rate = float(place.get("rate") or 1.0)
        start = int(place.get("snapped_record") or 0)
        frames = int(place.get("record_frames") or 0)
        if frames <= 0:
            raise RetimeShapeError(
                f"a retimed passage at reel frame {start} carries no "
                f"record length - refusing")
        key = (rate, start, start + frames)
        by_span.setdefault(key, Passage(
            rate=rate, record_start=start, record_end=start + frames,
            source_in=float(place.get("source_in") or 0.0),
            source_out=float(place.get("source_out") or 0.0)))
        rates.setdefault(rate, set()).add((start, start + frames))
    for rate, spans in rates.items():
        if len(spans) > 1:
            named = ", ".join(f"{s}-{e}" for s, e in sorted(spans))
            raise RetimeShapeError(
                f"the {rate * 100:g}% retime covers more than one clip "
                f"({named}) - a passage that crosses a clip splits it, "
                f"and this route scopes a retime of ONE clip")
    return tuple(sorted(by_span.values(), key=lambda p: p.record_start))


def diff_rows(*, expected: Mapping[Tuple[str, int], Sequence[Item]],
              live: Mapping[Tuple[str, int], Sequence[Item]],
              passages: Sequence[Passage], fps: float = 24.0) -> RetimeDiff:
    """What the retime changed between the live rows and the plan's.

    `expected` is the plan's inventory per row and `live` is what the
    timeline holds, both as `(start, end, source)` in play order.  The
    two are paired BY POSITION within a row: a retime moves items
    forward or backward, it never reorders them, and the pairing is
    checked - the media each pair carries must be the same file, or
    the change is not a retime.

    Raises `RetimeShapeError` when the difference is not a retime this
    route can scope, with the reason.
    """
    if not passages:
        raise RetimeShapeError(
            "the plan carries no retime passage, so a change in the "
            "footage rows is not a retime")
    if set(expected) != set(live):
        raise RetimeShapeError(
            f"the footage rows differ (plan {sorted(expected)} vs timeline "
            f"{sorted(live)}) - refusing")

    retimed_spans = {p.span for p in passages}
    raw: List[Tuple[Tuple[str, int], Item, Item, int, int]] = []
    reused = 0
    for row in expected:
        exp = list(expected[row] or ())
        liv = list(live.get(row) or ())
        if len(exp) != len(liv):
            raise RetimeShapeError(
                f"row {row[0]}{row[1]} holds {len(liv)} item(s) and the "
                f"plan places {len(exp)} - a retime that crosses a clip "
                f"splits it, and this route scopes a retime of ONE clip")
        for want, have in zip(exp, liv):
            if want[2] != have[2]:
                raise RetimeShapeError(
                    f"row {row[0]}{row[1]} carries different media than "
                    f"the plan places - refusing")
            d_start = int(want[0]) - int(have[0])
            d_end = int(want[1]) - int(have[1])
            if d_start == 0 and d_end == 0:
                reused += 1
                continue
            raw.append((row, have, want, d_start, d_end))

    if not raw:
        # The rows already carry the plan: the retime is in force and
        # the change is elsewhere.
        return RetimeDiff(passages=tuple(passages), delta=0, changes=(),
                           reused=reused)

    # The clock's shift, derived from the passage itself: the passage's
    # own length change.  Every retimed item must agree on it.
    deltas = set()
    retimed_old_end: Dict[Tuple[str, int], int] = {}
    for row, have, want, d_start, d_end in raw:
        if (int(want[0]), int(want[1])) in retimed_spans:
            if d_start != 0:
                raise RetimeShapeError(
                    f"the retimed passage on row {row[0]}{row[1]} starts "
                    f"at frame {want[0]} where the timeline holds "
                    f"{have[0]} - the passage does not begin where the "
                    f"clip does")
            deltas.add(d_end - d_start)
            retimed_old_end[row] = int(have[1])
    if not deltas:
        raise RetimeShapeError(
            "the footage rows differ from the plan and no passage "
            "explains the difference - refusing")
    if len(deltas) > 1:
        raise RetimeShapeError(
            "the retimed passage shortens by different amounts on "
            "different rows - refusing")
    delta = deltas.pop()

    changes: List[ItemChange] = []
    for row, have, want, d_start, d_end in raw:
        old_span = (int(have[0]), int(have[1]))
        new_span = (int(want[0]), int(want[1]))
        if new_span in retimed_spans:
            passage = _passage_of(passages, new_span)
            old_len = old_span[1] - old_span[0]
            new_len = new_span[1] - new_span[0]
            # The passage's new length is explained by its rate: the
            # plan plays `rate` times as much source in the frames it
            # occupies.  A passage whose length its own rate does not
            # explain is not a passage this route can place.
            master = int(round((passage.source_out - passage.source_in)
                                * fps))
            if master <= 0 or abs(new_len - master / passage.rate) > 1:
                raise RetimeShapeError(
                    f"the retimed passage on row {row[0]}{row[1]} plays "
                    f"{new_len} frame(s) of a {master}-frame passage, "
                    f"which its {passage.rate * 100:g}% rate does not "
                    f"explain - refusing")
            changes.append(ItemChange(row=row, old_span=old_span,
                                       new_span=new_span, kind="retimed"))
            continue
        if d_start == delta and d_end == delta:
            passage_end = retimed_old_end.get(row)
            if passage_end is not None and old_span[0] < passage_end:
                raise RetimeShapeError(
                    f"row {row[0]}{row[1]} moves an item from frame "
                    f"{old_span[0]}, which is inside the retimed "
                    f"passage - refusing")
            changes.append(ItemChange(row=row, old_span=old_span,
                                       new_span=new_span, kind="moved"))
            continue
        raise RetimeShapeError(
            f"row {row[0]}{row[1]} changes an item from "
            f"[{old_span[0]}, {old_span[1]}) to "
            f"[{new_span[0]}, {new_span[1]}), which is neither the "
            f"retimed passage nor the clock's shift of {delta} frame(s) "
            f"- refusing")

    covered = {c.new_span for c in changes if c.kind == "retimed"}
    for passage in passages:
        if passage.span not in covered:
            raise RetimeShapeError(
                f"the retimed passage at reel frames "
                f"{passage.record_start}-{passage.record_end} is on no "
                f"row the diff can see - refusing")

    return RetimeDiff(passages=tuple(passages), delta=delta,
                       changes=tuple(changes), reused=reused)


def _passage_of(passages: Sequence[Passage],
                span: Tuple[int, int]) -> Passage:
    """The passage occupying `span`."""
    for passage in passages:
        if passage.span == tuple(span):
            return passage
    raise RetimeShapeError(
        f"no passage occupies reel frames {span[0]}-{span[1]}")


__all__ = [
    "ItemChange",
    "Passage",
    "RetimeDiff",
    "RetimeShapeError",
    "diff_rows",
    "passages_from_placements",
]
