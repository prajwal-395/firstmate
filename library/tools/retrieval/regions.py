"""A wanted moment is a REGION, and ONE window earns ONE credit for it.

**The metric was gameable, and this is why.**

A visual query's ground truth is a sequence of labelled SAMPLES: every ten
seconds of the footage where the annotator saw the subject.  The scorer
counted a hit per SAMPLE, so a returned range that spanned twenty samples
earned twenty hits - one for every sample it happened to cover.  The
degenerate answer, "return the whole recording", therefore scored 0.33
recall and 0.50 precision on the held-out set: three times the incumbent's
recall, for 22 181 seconds of footage nobody asked for.  A metric that
pays for volume is a metric an optimisation loop will satisfy by making
the product worse.

**What is measured instead.**

* The wanted ground truth is grouped into **REGIONS**: maximal runs of
  labelled moments that touch or overlap, on any asset, in the raw
  timebase or in the reference timebase the M6 conversation clock gives.
  A region is one contiguous labelled thing - the thing an editor is
  asking for.
* **A window earns at most ONE credit per region.**  One window is one
  moment: it points at one wanted thing, however many samples of that
  thing it happens to span.  This is the cap.
* The old number is still reported, beside the new one, as
  `recall_uncredited`: a metric that changes its meaning silently is a
  metric nobody can compare against the artefacts that came before it.

**The extra seconds are charged.**  A window that covers the wanted thing
AND a hundred seconds of nothing else is not free: the seconds outside
the wanted spans are false-positive seconds, and when the editor takes
that window they are wrong seconds.  The charge lives in
`library.tools.retrieval.consequence`, because a wrong second is a
consequence and the consequence is where the cost of retrieval is priced.

The credit is capped per REGION and not per window, because a window may
legitimately cover two wanted things that happen to be next to each other
(two separate passages of speech, say).  Each is its own region, and each
is credited once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

#: Two labelled spans this close belong to the same wanted region.
CONTIGUITY_SECONDS = 1.0


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def instance_spans(moment: Mapping) -> list[tuple[str, float, float]]:
    """One labelled moment's spans, per asset."""
    out: list[tuple[str, float, float]] = []
    for instance in (moment.get("instances") or ()):
        asset = str(instance.get("asset") or "")
        start = _num(instance.get("start"))
        end = _num(instance.get("end"))
        if not asset or start is None or end is None or end <= start:
            continue
        out.append((asset, start, end))
    return out


def _touching(a_start: float, a_end: float,
              b_start: float, b_end: float, gap: float = 0.0) -> bool:
    """Do two spans touch or overlap, within `gap` seconds?"""
    return (a_start - gap <= b_end and b_start - gap <= a_end)


def _reference(asset: str, start: float, end: float,
               offsets: Mapping[str, float]) -> tuple[float, float]:
    """A span on the reference timebase the conversation clock defines."""
    offset = _num(offsets.get(asset))
    shift = offset if offset is not None else 0.0
    return (start - shift, end - shift)


def wanted_regions(moments: Sequence[Mapping],
                   offsets: Mapping[str, float] | None = None,
                   contiguity: float = CONTIGUITY_SECONDS
                   ) -> list[dict]:
    """The contiguous labelled regions the editor wants.

    Two labelled moments are in the SAME region when their spans touch or
    overlap on some asset - in the raw timebase OR in the reference
    timebase the M6 clock gives.  Both relations are used because the two
    kinds of label are marked in two different timebases: the frame labels
    mark both angles of a shot at the same raw timestamps, and the speech
    labels mark the same passage at the clock offset between the angles.
    Assuming either one alone would split a wanted thing in two.

    Each region carries the moments it holds and the spans it covers, so
    a reader can see what the region IS rather than only how many labels
    went into it.
    """
    offsets = offsets or {}
    spans: list[list[tuple[str, float, float]]] = [
        instance_spans(moment) for moment in moments]
    count = len(moments)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(count):
        for right in range(left + 1, count):
            if _linked(spans[left], spans[right], offsets, contiguity):
                union(left, right)

    grouped: dict[int, list[int]] = {}
    for index in range(count):
        grouped.setdefault(find(index), []).append(index)

    regions: list[dict] = []
    for members in grouped.values():
        members.sort()
        covered: dict[str, list[tuple[float, float]]] = {}
        for index in members:
            for asset, start, end in spans[index]:
                covered.setdefault(asset, []).append((start, end))
        regions.append({
            "id": len(regions),
            "moments": members,
            "spans": {asset: _merge_spans(intervals)
                      for asset, intervals in covered.items()},
        })
    regions.sort(key=lambda region: region["moments"][0])
    for position, region in enumerate(regions):
        region["id"] = position
    return regions


def _linked(left: Sequence[tuple[str, float, float]],
            right: Sequence[tuple[str, float, float]],
            offsets: Mapping[str, float],
            contiguity: float) -> bool:
    """Are two labelled moments the same wanted thing?"""
    for left_asset, left_start, left_end in left:
        for right_asset, right_start, right_end in right:
            if (_touching(left_start, left_end, right_start, right_end,
                          contiguity)
                    or _touching(*_reference(left_asset, left_start,
                                             left_end, offsets),
                                 *_reference(right_asset, right_start,
                                             right_end, offsets),
                                 contiguity)):
                return True
    return False


def _merge_spans(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping intervals into runs, in order."""
    ordered = sorted(intervals)
    runs: list[list[float]] = []
    for start, end in ordered:
        if runs and start <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    return [(run[0], run[1]) for run in runs]


def _merge(intervals: Iterable[tuple[float, float]]) -> tuple[float, float]:
    """The extent of a set of intervals, as one span."""
    runs = _merge_spans(intervals)
    if not runs:
        return (0.0, 0.0)
    return (runs[0][0], runs[-1][1])


def credit_windows(moments: Sequence[Mapping],
                   regions: Sequence[Mapping],
                   rows: Sequence[Mapping],
                   tolerance_seconds: float = 1.0) -> dict:
    """What the returned windows earn against the wanted regions.

    `rows` are the returned windows, each carrying `asset`, `start` and
    `end`.  A window CREDITS a moment when it overlaps that moment's
    span, and it credits AT MOST ONE moment per region: one window is
    one wanted thing, however many labelled samples of it the window
    happens to span.

    The result carries both numbers, so nothing is hidden by the change:

    * `credited` - the moments the capped rule credits;
    * `overlapped` - the moments any window touches, which is what the
      uncapped metric counted;
    * `excess_seconds` - the returned seconds that lie outside every
      wanted span: the footage nobody asked for.
    """
    region_of: dict[int, int] = {}
    for region in regions:
        for index in region.get("moments") or ():
            region_of[int(index)] = int(region.get("id", 0))
    wanted: dict[str, list[tuple[float, float]]] = {}
    for moment in moments:
        for asset, start, end in instance_spans(moment):
            wanted.setdefault(asset, []).append(
                (start - tolerance_seconds, end + tolerance_seconds))
    wanted = {asset: _merge_spans(spans) for asset, spans in wanted.items()}

    credited: set[int] = set()
    overlapped: set[int] = set()
    per_window: list[dict] = []
    excess_total = 0.0
    for row in rows or ():
        asset = str(row.get("asset") or "")
        start = _num(row.get("start"))
        end = _num(row.get("end"))
        if start is None or end is None or end <= start:
            continue
        best_by_region: dict[int, tuple[float, int]] = {}
        for index, moment in enumerate(moments):
            overlap = 0.0
            for moment_asset, moment_start, moment_end in instance_spans(moment):
                if moment_asset != asset:
                    continue
                if not _touching(start, end,
                                 moment_start - tolerance_seconds,
                                 moment_end + tolerance_seconds):
                    continue
                overlap = max(overlap, min(end, moment_end)
                              - max(start, moment_start))
            if overlap <= 0.0:
                continue
            overlapped.add(index)
            region_id = region_of.get(index, index)
            current = best_by_region.get(region_id)
            if current is None or overlap > current[0]:
                best_by_region[region_id] = (overlap, index)
        for region_id, (_overlap, index) in sorted(best_by_region.items()):
            credited.add(index)
        excess = 0.0
        if best_by_region:
            # A window that holds nothing the editor asked for is charged
            # in full as a false-positive window; only a window that DOES
            # hold something wanted carries an excess, and that excess is
            # the footage it brought along.
            excess = _excess_seconds(asset, start, end,
                                     wanted.get(asset) or ())
        excess_total += excess
        per_window.append({
            "asset": asset,
            "start": start,
            "end": end,
            "credited_moments": sorted(index for _o, index
                                       in best_by_region.values()),
            "overlapped_moments": sorted(
                index for index in overlapped
                if any(moment_asset == asset
                       for moment_asset, _s, _e in instance_spans(moments[index]))
                and _touches(start, end, moments[index], tolerance_seconds)),
            "excess_seconds": round(excess, 3),
        })
    return {
        "credited": sorted(credited),
        "overlapped": sorted(overlapped),
        "regions_found": sorted({region_of.get(index, index)
                                 for index in credited}),
        "excess_seconds": round(excess_total, 3),
        "per_window": per_window,
    }


def _touches(start: float, end: float, moment: Mapping,
              tolerance_seconds: float) -> bool:
    for asset, moment_start, moment_end in instance_spans(moment):
        if _touching(start, end, moment_start - tolerance_seconds,
                     moment_end + tolerance_seconds):
            return True
    return False


def _excess_seconds(asset: str, start: float, end: float,
                    wanted: Sequence[tuple[float, float]]) -> float:
    """The seconds of a window that lie outside every wanted span."""
    duration = max(0.0, end - start)
    inside = 0.0
    for span_start, span_end in wanted:
        inside += max(0.0, min(end, span_end) - max(start, span_start))
    return max(0.0, duration - min(duration, inside))


def wanted_spans(moments: Sequence[Mapping],
                 tolerance_seconds: float = 1.0
                 ) -> dict[str, list[tuple[float, float]]]:
    """Per asset, the spans the editor wanted, padded by the tolerance."""
    spans: dict[str, list[tuple[float, float]]] = {}
    for moment in moments or ():
        for asset, start, end in instance_spans(moment):
            spans.setdefault(asset, []).append(
                (start - tolerance_seconds, end + tolerance_seconds))
    return {asset: _merge_spans(intervals) for asset, intervals in spans.items()}
