"""A retrieval false positive is a wrong EDIT, and it costs something.

Recall is the half of retrieval that gets measured, and the half that
gets reported.  The other half - the window the search returned that
holds nothing the editor asked for - is not free: it is a window an
editor might cut into the piece believing it is the moment they wanted.

The cost is not a number, because the cost depends on the edit.  A wrong
window that outranks every right one is a different failure from a wrong
window that trails them: the first is picked, the second is ignored.
A wrong window inside the right moment is worse than a wrong window far
away, because the editor cannot tell they are looking at the wrong part.

So this module reports a VECTOR, never a scalar:

* `wrong_seconds` - seconds of the wrong picture a selection would put
  into the cut;
* `missing_seconds` - seconds of the wanted moment the selection leaves
  out;
* `shadowing` - whether a wrong window outranked every right one;
* `false_positive_windows` / `false_positive_seconds` - the whole result,
  not just the selection.

**A single number would be a creative decision, and it is not this
module's to make.**  The weights belong to the edit policy, and the
policy is declared by whoever is making the edit.

**The selection is declared, not assumed.**  `selection_k` says how many
windows the editor takes.  It is an argument because "the editor takes
the first one" and "the editor takes five" are different edits, and the
consequence differs; the default is one because that is what an editor
does with a search result they trust.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

DEFAULT_SELECTION_K = 1
"""How many windows an editor takes from a ranked search result."""

SELECTION_NOTE = ("the downstream consequence is priced as if the editor "
                  "took the first window the search returned, because that "
                  "is the window they take")


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration(row: Mapping) -> float:
    start = _num(row.get("start"))
    end = _num(row.get("end"))
    if start is None or end is None:
        return 0.0
    return max(0.0, end - start)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def moment_seconds(moment: Mapping, tolerance_seconds: float) -> float:
    """How long one wanted moment lasts.

    A moment recorded on three cameras is ONE moment, not three, so the
    longest instance is its length.  Counting the cameras separately
    would make a well-covered moment look like a bigger miss.  The
    tolerance is a MATCHING allowance and does not lengthen the moment.
    """
    longest = 0.0
    for instance in moment.get("instances") or ():
        start = _num(instance.get("start"))
        end = _num(instance.get("end"))
        if start is None or end is None:
            continue
        longest = max(longest, max(0.0, end - start))
    return longest


def moment_covered(moment: Mapping, rows: Iterable[Mapping],
                   tolerance_seconds: float) -> float:
    """How much of one wanted moment the rows cover."""
    best = 0.0
    for instance in moment.get("instances") or ():
        asset = instance.get("asset")
        start = _num(instance.get("start"))
        end = _num(instance.get("end"))
        if start is None or end is None:
            continue
        span = (start - tolerance_seconds, end + tolerance_seconds)
        for row in rows or ():
            if row.get("asset") != asset:
                continue
            row_start = _num(row.get("start"))
            row_end = _num(row.get("end"))
            if row_start is None or row_end is None:
                continue
            best = max(best, _overlap(span, (row_start, row_end)))
    return best


def edit_consequence(moments: Sequence[Mapping], rows: Sequence[Mapping], *,
                     selection_k: int = DEFAULT_SELECTION_K,
                     tolerance_seconds: float = 1.0) -> dict:
    """What a wrong window costs in the edit it would cause.

    `rows` are the returned windows, each carrying `asset`, `start`,
    `end` and `hit` - whether it overlaps a wanted moment.  `moments`
    are the wanted moments.

    The result is a VECTOR.  There is no total, because weighting a
    wrong second against a missing second is the edit policy's decision
    and not this module's.
    """
    ranked = list(rows or ())
    selected = ranked[:max(0, selection_k)]
    wrong_selected = [row for row in selected if not row.get("hit")]
    right_selected = [row for row in selected if row.get("hit")]

    total_seconds = sum(moment_seconds(moment, tolerance_seconds)
                        for moment in moments or ())
    covered_seconds = sum(
        min(moment_seconds(moment, tolerance_seconds),
            moment_covered(moment, right_selected, tolerance_seconds))
        for moment in moments or ())
    missing_seconds = max(0.0, total_seconds - covered_seconds)

    wrong_rows = [row for row in ranked if not row.get("hit")]
    first_right = next((index for index, row in enumerate(ranked)
                        if row.get("hit")), None)
    first_wrong = next((index for index, row in enumerate(ranked)
                        if not row.get("hit")), None)
    shadowing = (first_wrong is not None
                 and (first_right is None or first_wrong < first_right))

    notes = []
    if not moments:
        notes.append("nothing was wanted here; every window is a false positive")
    if shadowing:
        notes.append("a wrong window outranks every right one, so the "
                     "first pick is the wrong one")
    if missing_seconds > 0 and moments:
        notes.append("the selection leaves part of the wanted moment out")
    if not wrong_rows:
        notes.append("every returned window holds something that was asked for")

    return {
        "selection_k": selection_k,
        "selection_note": SELECTION_NOTE,
        "selected": len(selected),
        "wrong_windows_selected": len(wrong_selected),
        "wrong_seconds_selected": round(
            sum(_duration(row) for row in wrong_selected), 3),
        "right_windows_selected": len(right_selected),
        "ground_truth_seconds": round(total_seconds, 3),
        "covered_seconds": round(covered_seconds, 3),
        "missing_seconds": round(missing_seconds, 3),
        "coverage_fraction": (round(covered_seconds / total_seconds, 4)
                              if total_seconds else None),
        "shadowing": shadowing,
        "false_positive_windows": len(wrong_rows),
        "false_positive_seconds": round(
            sum(_duration(row) for row in wrong_rows), 3),
        "notes": notes,
    }


def summarise(consequences: Iterable[Mapping]) -> dict:
    """The consequence vector, aggregated over a result set.

    Aggregated, never averaged into a score: the totals are what a
    summary can honestly carry, and the per-query rows stay for anyone
    who wants to see which query did the damage.
    """
    rows = [c for c in consequences or () if isinstance(c, Mapping)]
    if not rows:
        return {"queries": 0}
    wrong_seconds = sum(c.get("wrong_seconds_selected") or 0.0 for c in rows)
    missing_seconds = sum(c.get("missing_seconds") or 0.0 for c in rows)
    total = sum(c.get("ground_truth_seconds") or 0.0 for c in rows)
    return {
        "queries": len(rows),
        "wrong_seconds_selected": round(wrong_seconds, 3),
        "missing_seconds": round(missing_seconds, 3),
        "ground_truth_seconds": round(total, 3),
        "coverage_fraction": (round((total - missing_seconds) / total, 4)
                              if total else None),
        "shadowing_queries": sum(1 for c in rows if c.get("shadowing")),
        "false_positive_windows": sum(c.get("false_positive_windows") or 0
                                      for c in rows),
        "false_positive_seconds": round(
            sum(c.get("false_positive_seconds") or 0.0 for c in rows), 3),
    }
