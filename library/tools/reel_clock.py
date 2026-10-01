"""The reel clock: how long a keep range PLAYS, and at what rate.

A reel is its keep ranges laid end to end on the master clock. Until a
range could play at a speed other than 100%, a range's length on the
reel WAS its length on the master, and every reader summed `end - start`
for itself. A durable retime (punch list item 10: "make this passage
110% speed" replayed through a rebuild) breaks that identity: a passage
at rate r covers `end - start` master seconds and PLAYS `(end - start)
/ r` reel seconds. So every reader that turns ranges into reel time
asks this module, and only this module knows the rate.

A retime is a PASSAGE inside a range, never a new range: the range
keeps its identity - its seams, its closer, its index - and carries a
piecewise rate map beside it (`RatedRange.pieces`). Splitting the range
at the passage's word edges instead would put a seam (a planned
transition, a closer boundary) where the edit made none.

A rated range is still a `(start, end)` pair - it unpacks, indexes and
compares like one, so membership tests and edge reads on the master
clock stay untouched. A plain tuple plays at 1.0, and at 1.0 every
answer here is bit-identical to the `end - start` arithmetic it
replaced: no retime declared, no change.

    played_seconds((10.0, 12.2))                          -> 2.2
    played_seconds(rated_range(10.0, 12.2, [(10.0, 12.2, 1.1)]))
                                                          -> 2.0
"""

from __future__ import annotations

from typing import Iterable, Sequence


def _check_rate(rate) -> float:
    rate = float(rate)
    if not rate > 0 or rate != rate or rate == float("inf"):
        raise ValueError(
            f"a keep range plays at a positive finite rate, not "
            f"{rate!r} (1.0 is sync; a freeze is not a rate)")
    return rate


class RatedRange(tuple):
    """A `(start, end)` master range with passages at other rates.

    `pieces` covers `[start, end)` exactly, in order, as
    `(piece_start, piece_end, rate)` - the sync stretches included at
    1.0, so a reader walks one list. A tuple subclass, so every
    master-clock reader (unpacking, `[0]`, `[1]`, overlap and
    membership tests) works unchanged; only a reader of REEL time asks
    `pieces`. CPython refuses a non-empty `__slots__` on a tuple
    subclass, so the map lives in the instance dict.
    """

    def __new__(cls, start: float, end: float, pieces):
        self = super().__new__(cls, (start, end))
        self.pieces = tuple((float(a), float(b), _check_rate(r))
                            for a, b, r in pieces)
        return self

    def __repr__(self) -> str:
        return f"RatedRange({self[0]!r}, {self[1]!r}, {self.pieces!r})"

    def __reduce__(self):
        return (RatedRange, (self[0], self[1], self.pieces))


def rated_range(start: float, end: float, passages: Sequence = ()):
    """`[start, end)` with each `(passage_start, passage_end, rate)`
    playing at its rate and the rest at sync. Passages are clipped to
    the range, must not overlap, and a passage at exactly 1.0 is no
    passage. Returns a plain tuple when nothing is retimed, so an
    unretimed reel carries no new type anywhere."""
    start, end = float(start), float(end)
    inside = []
    for a, b, rate in sorted(passages or (), key=lambda p: float(p[0])):
        rate = _check_rate(rate)
        a, b = max(float(a), start), min(float(b), end)
        if b <= a or rate == 1.0:
            continue
        if inside and a < inside[-1][1]:
            raise ValueError(
                f"two retimed passages overlap in range "
                f"{start:.3f}-{end:.3f}s: {inside[-1][0]:.3f}-"
                f"{inside[-1][1]:.3f}s and {a:.3f}-{b:.3f}s - one stretch "
                f"of speech plays at one speed")
        inside.append((a, b, rate))
    if not inside:
        return (start, end)
    pieces, cursor = [], start
    for a, b, rate in inside:
        if a > cursor:
            pieces.append((cursor, a, 1.0))
        pieces.append((a, b, rate))
        cursor = b
    if cursor < end:
        pieces.append((cursor, end, 1.0))
    return RatedRange(start, end, pieces)


def pieces(keep_range) -> tuple:
    """`(piece_start, piece_end, rate)` covering the range, in order.
    One sync piece for a plain range."""
    found = getattr(keep_range, "pieces", None)
    if found is not None:
        return found
    return ((keep_range[0], keep_range[1], 1.0),)


def is_retimed(ranges: Sequence) -> bool:
    """Whether any range plays any passage at a rate other than sync."""
    return any(getattr(r, "pieces", None) is not None for r in ranges or ())


def played_seconds(keep_range) -> float:
    """REEL seconds this range plays. Exactly `end - start` unretimed."""
    if getattr(keep_range, "pieces", None) is None:
        return float(keep_range[1]) - float(keep_range[0])
    return sum((b - a) / rate for a, b, rate in keep_range.pieces)


def _piece_frames(a: float, b: float, rate: float, fps: float) -> int:
    master_frames = int(round(b * fps)) - int(round(a * fps))
    if rate == 1.0:
        return master_frames
    return int(round(master_frames / rate))


def played_frames(keep_range, fps: float) -> int:
    """REEL frames this range plays, on the arithmetic `placements` uses
    per edge: `round(end*fps) - round(start*fps)` master frames per
    piece, divided by the piece's rate and rounded once."""
    return sum(_piece_frames(float(a), float(b), rate, fps)
               for a, b, rate in pieces(keep_range))


def total_played_seconds(ranges: Iterable) -> float:
    """The reel length of these ranges, in seconds."""
    return sum(played_seconds(r) for r in ranges)


def total_played_frames(ranges: Iterable, fps: float) -> int:
    """The reel length of these ranges, in frames."""
    return sum(played_frames(r, fps) for r in ranges)


def reel_offset(keep_range, master_time: float) -> float:
    """Reel seconds from the range's head to `master_time` inside it."""
    if getattr(keep_range, "pieces", None) is None:
        return master_time - keep_range[0]
    offset = 0.0
    for a, b, rate in keep_range.pieces:
        if master_time <= a:
            break
        offset += (min(master_time, b) - a) / rate
    return offset


def reel_position(offset: float, keep_range, master_time: float) -> float:
    """Reel seconds of `master_time` inside `keep_range`, the range
    starting `offset` reel seconds in. Unretimed this is exactly the
    left-to-right `offset + master_time - start` it replaced (float
    addition is not associative, so the order is kept, not tidied)."""
    if getattr(keep_range, "pieces", None) is None:
        return offset + master_time - keep_range[0]
    return offset + reel_offset(keep_range, master_time)


def reel_frame_offset(keep_range, master_time: float, fps: float) -> int:
    """Reel frames from the range's head to `master_time` inside it, on
    `played_frames`' per-edge arithmetic. Unretimed exactly
    `round(master_time*fps) - round(start*fps)`."""
    if getattr(keep_range, "pieces", None) is None:
        return (int(round(master_time * fps))
                - int(round(keep_range[0] * fps)))
    frames = 0
    for a, b, rate in keep_range.pieces:
        if master_time <= a:
            break
        frames += _piece_frames(a, min(master_time, b), rate, fps)
    return frames
