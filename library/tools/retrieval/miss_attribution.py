"""Which stage of retrieval LOST a wanted moment.

**A retrieval miss is not one thing, and "the search did not find it" says
none of them.**  A wanted moment can be lost at six different places, and
each of them is a different defect with a different owner:

* **extraction coverage** - the frame was never decoded, so no stage
  downstream could ever see it;
* **indexing** - it was decoded and never embedded;
* **candidate generation** - it is embedded and the query's candidate pool
  never saw it;
* **ranking** - it is in the pool and the ranking put it below the cut;
* **temporal localization** - the window that covers it is on the OTHER
  ANGLE of the same moment, so the wanted asset was never covered;
* **result presentation** - the window that covers it was cut before the
  editor saw it;
* **the retrieval floor** - it scored under the lane's cutoff, so the
  lane refused to stand behind it.

**This module is the ENUMERATION of those places.**  One vocabulary, one
ladder, one place that decides which stage a miss belongs to - so a
report cannot say "ranking" when it means "the frame was never decoded",
and two reports cannot mean different things by the same word.

**The credit rule is not a retrieval stage, and it is reported apart.**
A wanted moment can be TOUCHED by a returned window and still earn no
credit: the capped rule gives a REGION one credit and gives it to the
best-overlapping moment in it, and the tolerance test is applied on a
different boundary than the overlap test.  Those two outcomes are
`credit_accounting`, not a failure of the search, and a report that
called them a retrieval miss would send the reader to fix the wrong
thing.  The lane's own recall number does not separate them either,
which is why this module does.

**What this module does NOT do.**  It changes no policy: it reads what
the shipped lane did (the candidate pool, the windows it presented, the
policy it ran under, the index it read, the frames that were extracted)
and says where the wanted thing stopped.  It never re-ranks, never
re-samples, never re-merges.

**The `visual` family carries its own coarse diagnostics**
(`library.tools.visual_retrieval_benchmark._retrieval_diagnostics`).
That one attributes a QUERY and names four places; this one attributes a
WANTED ITEM and names the stages above.  The two vocabularies are
translated by `COARSE_TO_STAGE`, so a reader of one can read the other.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence

# ── The vocabulary ───────────────────────────────────────────────────

#: Every stage the attribution can report, in the order the pipeline runs
#: them.  The FIRST stage that could have lost the target is the one
#: reported, so a stage names a place a target can be lost.
STAGES = (
    # extraction coverage
    "never_sampled",
    "extraction_evidence_unavailable",
    "never_transcribed",
    # indexing
    "never_indexed",
    # candidate generation
    "not_in_candidate_pool",
    # the retrieval floor
    "removed_by_query_floor",
    "removed_by_window_floor",
    # ranking
    "ranked_too_low",
    # temporal localization
    "temporal_localization",
    # result presentation
    "not_presented",
    # the credit rule, not the retrieval
    "touched_within_tolerance",
    "point_window_not_credited",
    "credited_to_sibling_in_region",
    # found
    "credited",
)

#: Which of VI-001's six stages each stage belongs to.  The six are the
#: punchlist's own: extraction coverage, indexing, candidate generation,
#: ranking, temporal localization, result presentation - plus the floor,
#: which is a filter the lane applies rather than a stage of the
#: pipeline, and the credit rule, which is the metric's business.
PIPELINE_STAGE = {
    "never_sampled": "extraction_coverage",
    "extraction_evidence_unavailable": "extraction_coverage",
    "never_transcribed": "extraction_coverage",
    "never_indexed": "indexing",
    "not_in_candidate_pool": "candidate_generation",
    "removed_by_query_floor": "retrieval_floor",
    "removed_by_window_floor": "retrieval_floor",
    "ranked_too_low": "ranking",
    "temporal_localization": "temporal_localization",
    "not_presented": "result_presentation",
    "touched_within_tolerance": "credit_accounting",
    "point_window_not_credited": "credit_accounting",
    "credited_to_sibling_in_region": "credit_accounting",
    "credited": "found",
}

#: The stages that mean the SEARCH lost the target.  A report that names
#: one of these is naming a defect in a stage; a report that names
#: `credit_accounting` is naming the metric's own rule.
RETRIEVAL_FAILURE_STAGES = frozenset(
    stage for stage, where in PIPELINE_STAGE.items()
    if where not in ("found", "credit_accounting"))

#: The stages that mean the SEARCH found the target and the CREDIT rule
#: did not count it.
CREDIT_ACCOUNTING_STAGES = frozenset(
    stage for stage, where in PIPELINE_STAGE.items()
    if where == "credit_accounting")

#: The stages that mean the target was FOUND.
FOUND_STAGES = frozenset(
    stage for stage, where in PIPELINE_STAGE.items() if where == "found")

#: When a wanted item has several instances (the same moment on two
#: angles), the instance that got FURTHEST wins: a wanted thing found on
#: one angle WAS found, and reporting the other angle's failure would
#: blame a stage that did not lose it.
ADVANCEMENT = (
    "credited",
    "point_window_not_credited",
    "touched_within_tolerance",
    "credited_to_sibling_in_region",
    "temporal_localization",
    "not_presented",
    "ranked_too_low",
    "removed_by_window_floor",
    "removed_by_query_floor",
    "not_in_candidate_pool",
    "never_indexed",
    "never_sampled",
    "never_transcribed",
    "extraction_evidence_unavailable",
)

#: The `visual` family's coarse vocabulary, translated.  Its four places
#: are a coarser view of the same ladder; this table says which of the
#: fine stages each coarse name can mean, so a reader of one report can
#: read the other without a glossary.
COARSE_TO_STAGE = {
    "sampling_coverage": ("never_sampled", "never_transcribed",
                          "never_indexed"),
    "frame_ranking_or_candidate_pool": ("not_in_candidate_pool",
                                        "ranked_too_low"),
    "interval_ranking_or_merging": ("temporal_localization",
                                     "not_presented"),
    "sampling_coverage_unavailable": (),
    "retrieved": ("credited",),
}

# ── Small helpers ───────────────────────────────────────────────────


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _covers(times: Sequence[float], low: float, high: float) -> bool:
    """Is any of `times` inside [low, high]?  `times` is sorted."""
    if not times:
        return False
    index = bisect_left(times, low)
    return index < len(times) and times[index] <= high


def _covers_span(spans: Sequence[tuple[float, float]],
                  low: float, high: float) -> bool:
    """Does any of `spans` overlap [low, high]?"""
    for start, end in spans:
        if min(end, high) > max(start, low):
            return True
    return False


def _most_advanced(stages: Iterable[str]) -> str | None:
    """The stage that got furthest, or None when there was no stage."""
    best = None
    for stage in stages:
        if stage not in ADVANCEMENT:
            continue
        if best is None or (ADVANCEMENT.index(stage)
                            < ADVANCEMENT.index(best)):
            best = stage
    return best


def _instance_spans(moment: Mapping) -> list[tuple[str, float, float]]:
    """One wanted moment's spans, per asset."""
    out: list[tuple[str, float, float]] = []
    for instance in (moment.get("instances") or ()):
        asset = str(instance.get("asset") or "")
        start = _num(instance.get("start"))
        end = _num(instance.get("end"))
        if not asset or start is None or end is None:
            continue
        out.append((asset, start, end))
    return out


def _overlap(row_start: float, row_end: float,
             start: float, end: float) -> float:
    """How much a returned window and a wanted span overlap, in seconds."""
    return max(0.0, min(row_end, end) - max(row_start, start))


def _touching(row_start: float, row_end: float,
              start: float, end: float, tolerance: float) -> bool:
    """Does a returned window touch a wanted span, padding included?

    A window with NO LENGTH - one frame, which is what the frame lane
    returns when its merge produced a single sighting - counts as
    touching when it lies INSIDE the wanted span.  The harness's own
    `overlaps` says so, and a window that lands inside the thing the
    editor asked for is a hit, not a near miss.
    """
    if (row_start == row_end
            and start - tolerance <= row_start <= end + tolerance):
        return True
    return min(row_end, end + tolerance) > max(row_start, start - tolerance)


def _point_inside(row_start: float, row_end: float,
                  start: float, end: float) -> bool:
    """Is the window a single frame lying inside the wanted span?"""
    return row_start == row_end and start <= row_start <= end


def _covers_window(row_start: float, row_end: float,
                   low: float, high: float) -> bool:
    """Does the window cover any part of [low, high]?

    A window with no length covers the instant it stands on, so a single
    frame inside the span covers it.
    """
    if row_start == row_end:
        return low <= row_start <= high
    return min(row_end, high) > max(row_start, low)


# ── The visual lane ─────────────────────────────────────────────────


def attribute_visual(query: Mapping, *, pool: Sequence[Mapping] = (),
                      ranges: Sequence[Mapping] = (),
                      rows: Sequence[Mapping] = (),
                      policy: Mapping | None = None,
                      abstained: bool = False,
                      index_times: Mapping[str, Sequence[float]] | None = None,
                      sampled_times: Mapping[str, Sequence[float]] | None = None,
                      sampled_status: Mapping[str, str] | None = None,
                      clip_of_asset: Mapping[str, str] | None = None,
                      offsets: Mapping[str, float] | None = None,
                      groups: Mapping[str, str] | None = None,
                      credited: Iterable[int] = (),
                      tolerance: float = 1.0) -> dict:
    """Attribute every wanted moment of a visual query to a stage.

    The evidence is what the lane did, read back:

    * `pool` - the candidate frames the query ranked (`clip_id`, `t`,
      `score`), the pool the ranking was over;
    * `ranges` - the windows the lane PRESENTED (`clip_id`, `start`,
      `end`, `best_score`);
    * `rows` - the same windows named by ASSET (`asset`, `start`, `end`),
      which is how a wanted moment names them;
    * `policy` - the lane's own policy (`query_floor`, `window_floor`,
      `top_interval_limit`);
    * `index_times` - the times the frame INDEX holds, per clip;
    * `sampled_times` - the times the EXTRACTION produced, per clip: the
      M2 sample the index was built from, and `sampled_status` says
      whether that sample is readable at all.  This is the only evidence
      that separates "never decoded" from "never indexed" - and an
      unreadable sample is reported as unreadable, not as a gap.

    `credited` is the set of wanted moments the lane's own capped credit
    rule credited.
    """
    policy = dict(policy or {})
    index_times = index_times or {}
    sampled_times = sampled_times or {}
    sampled_status = sampled_status or {}
    clip_of_asset = clip_of_asset or {}
    offsets = offsets or {}
    groups = groups or {}
    credited = set(credited)
    query_floor = _num(policy.get("query_floor"))
    window_floor = _num(policy.get("window_floor"))
    moments = list(query.get("moments") or ())

    by_clip: dict[str, list[tuple[float, float]]] = {}
    for hit in pool or ():
        clip = hit.get("clip_id")
        t = _num(hit.get("t"))
        if clip is None or t is None:
            continue
        by_clip.setdefault(clip, []).append((t, _num(hit.get("score")) or 0.0))
    for hits in by_clip.values():
        hits.sort()

    windows_by_clip: dict[str, list[Mapping]] = {}
    for window in ranges or ():
        clip = window.get("clip_id")
        if clip is None:
            continue
        windows_by_clip.setdefault(clip, []).append(window)

    siblings: dict[str, list[str]] = {}
    for asset, group in groups.items():
        if group is None:
            continue
        for other, other_group in groups.items():
            if other != asset and other_group == group:
                siblings.setdefault(asset, []).append(other)

    per_moment: list[dict] = []
    for index, moment in enumerate(moments):
        stages: list[str] = []
        details: list[dict] = []
        for asset, start, end in _instance_spans(moment):
            stage, detail = _visual_instance_stage(
                asset, start, end,
                moment_index=index,
                credited=index in credited,
                rows=rows or (),
                by_clip=by_clip,
                windows_by_clip=windows_by_clip,
                index_times=index_times,
                sampled_times=sampled_times,
                sampled_status=sampled_status,
                clip_of_asset=clip_of_asset,
                siblings=siblings,
                offsets=offsets,
                query_floor=query_floor,
                window_floor=window_floor,
                abstained=bool(abstained),
                tolerance=tolerance)
            stages.append(stage)
            details.append({"asset": asset, "start": start, "end": end,
                            "stage": stage, "detail": detail})
        stage = _most_advanced(stages)
        per_moment.append({"moment": index, "stage": stage,
                           "instances": details})

    return _assemble(query, "visual", per_moment, moments, credited)


def _visual_instance_stage(asset: str, start: float, end: float, *,
                            moment_index: int, credited: bool,
                            rows: Sequence[Mapping],
                            by_clip: Mapping[str, list[tuple[float, float]]],
                            windows_by_clip: Mapping[str, list[Mapping]],
                            index_times: Mapping[str, Sequence[float]],
                            sampled_times: Mapping[str, Sequence[float]],
                            sampled_status: Mapping[str, str],
                            clip_of_asset: Mapping[str, str],
                            siblings: Mapping[str, list[str]],
                            offsets: Mapping[str, float],
                            query_floor: float | None,
                            window_floor: float | None,
                            abstained: bool,
                            tolerance: float) -> tuple[str, dict]:
    """The stage that lost ONE wanted span on ONE asset."""
    clip = clip_of_asset.get(asset)
    if clip is None:
        return "never_sampled", {"reason": "the asset is not in this project"}

    # Temporal localization, measured first so every report carries it:
    # is there a presented window on the OTHER ANGLE of this moment?
    sibling_window = None
    for sibling in siblings.get(asset, ()):
        sibling_clip = clip_of_asset.get(sibling)
        if sibling_clip is None:
            continue
        shift = (_num(offsets.get(sibling)) or 0.0) - (_num(offsets.get(asset))
                                                      or 0.0)
        sibling_span = (start + shift, end + shift)
        for window in windows_by_clip.get(sibling_clip, ()):
            if _covers_window(_num(window.get("start")) or 0.0,
                              _num(window.get("end")) or 0.0,
                              *sibling_span):
                sibling_window = sibling
                break
        if sibling_window:
            break

    # The credit rule first: a wanted moment the lane's own credit rule
    # credited was found, whatever the stages below say.
    raw = [row for row in rows
           if row.get("asset") == asset
           and _overlap(_num(row.get("start")) or 0.0,
                        _num(row.get("end")) or 0.0, start, end) > 0.0]
    points = [row for row in rows
              if row.get("asset") == asset
              and _point_inside(_num(row.get("start")) or 0.0,
                                _num(row.get("end")) or 0.0,
                                start, end)]
    touched = [row for row in rows
               if row.get("asset") == asset
               and _touching(_num(row.get("start")) or 0.0,
                             _num(row.get("end")) or 0.0,
                             start, end, tolerance)]
    if credited:
        return "credited", {"reason": "the capped credit rule credited it",
                            "windows": len(raw),
                            "localized_on_sibling": sibling_window}
    if raw:
        return "credited_to_sibling_in_region", {
            "reason": ("a returned window covers it, and the region's one "
                       "credit went to another moment in the same region"),
            "windows": len(raw),
            "localized_on_sibling": sibling_window}
    if points:
        return "point_window_not_credited", {
            "reason": ("a returned window of NO LENGTH lies inside it, and "
                       "the credit rule counts a window only when it "
                       "OVERLAPS the wanted span"),
            "windows": len(points),
            "localized_on_sibling": sibling_window}
    if touched:
        return "touched_within_tolerance", {
            "reason": ("a returned window touches it only inside the "
                       "overlap tolerance: the harness's hit test pads the "
                       "wanted span and calls it a hit, and the credit "
                       "rule counts a window only when it OVERLAPS the "
                       "span"),
            "windows": len(touched),
            "localized_on_sibling": sibling_window}

    # Indexing: the index does not hold it.  Was it ever decoded?
    indexed = index_times.get(clip) or ()
    if not _covers(indexed, start - tolerance, end + tolerance):
        status = sampled_status.get(clip)
        if status is not None and status != "fresh":
            return "extraction_evidence_unavailable", {
                "reason": ("the extracted sample for this source is not "
                           "readable, so whether the frame was decoded "
                           "cannot be said"),
                "sampling_status": status}
        sampled = sampled_times.get(clip) or ()
        if not _covers(sampled, start - tolerance, end + tolerance):
            return "never_sampled", {
                "reason": "no extracted frame covers this span",
                "extracted_frames_on_clip": len(sampled),
                "localized_on_sibling": sibling_window}
        return "never_indexed", {
            "reason": "an extracted frame covers it and the index does not",
            "indexed_frames_on_clip": len(indexed),
            "extracted_frames_on_clip": len(sampled_times.get(clip) or ()),
            "localized_on_sibling": sibling_window}

    # Candidate generation: indexed, and the pool never saw it.
    hits = [(t, score) for t, score in by_clip.get(clip, ())
            if start - tolerance <= t <= end + tolerance]
    if not hits:
        return "not_in_candidate_pool", {
            "reason": ("the index holds it and the CLIP ranking put it "
                       "below the candidate pool's cut"),
            "pool_frames_on_clip": len(by_clip.get(clip, ())),
            "localized_on_sibling": sibling_window}

    # The floor: the whole query was abstained, or the window that would
    # have carried it was dropped for scoring under the window floor.
    if abstained and query_floor is not None:
        return "removed_by_query_floor", {
            "reason": "the lane abstained: nothing in the pool cleared the floor",
            "query_floor": query_floor,
            "localized_on_sibling": sibling_window}
    if window_floor is not None and all(score < window_floor
                                        for _t, score in hits):
        return "removed_by_window_floor", {
            "reason": "every candidate in the span scored under the window floor",
            "window_floor": window_floor,
            "best_candidate_score": max(score for _t, score in hits),
            "localized_on_sibling": sibling_window}

    if sibling_window:
        return "temporal_localization", {
            "reason": ("the window that covers it is on the sibling "
                       "angle, not on this asset"),
            "sibling_asset": sibling_window}

    windows = windows_by_clip.get(clip, ())
    if not windows:
        return "not_presented", {
            "reason": ("the clip's window was cut before the editor saw "
                       "it"),
            "pool_frames_on_clip": len(by_clip.get(clip, ())),
            "localized_on_sibling": sibling_window}
    cut = max(_num(w.get("best_score")) or 0.0 for w in windows)
    best_candidate = max(score for _t, score in hits)
    if best_candidate < cut:
        return "ranked_too_low", {
            "reason": ("a better-scoring window on the same clip took the "
                       "slot"),
            "candidate_score": best_candidate,
            "presented_window_score": cut,
            "localized_on_sibling": sibling_window}
    return "not_presented", {
        "reason": "the candidate outranked the cut but no window carried it",
        "candidate_score": best_candidate,
        "presented_window_score": cut,
        "localized_on_sibling": sibling_window}


# ── The text lane ───────────────────────────────────────────────────


def attribute_text(query: Mapping, *, rows: Sequence[Mapping] = (),
                   weak: Sequence[Mapping] = (),
                   best_rejected: Mapping | None = None,
                   abstained: bool = False,
                   floor: float | None = None,
                   segment_spans: Mapping[str, Sequence[tuple[float, float]]]
                   | None = None,
                   clip_of_asset: Mapping[str, str] | None = None,
                   credited: Iterable[int] = (),
                   tolerance: float = 1.0) -> dict:
    """Attribute every wanted moment of a TEXT query to a stage.

    The text lane's evidence is what its report carries: the windows it
    returned, the WEAK band under the floor, and the `best_rejected`
    near miss the floor turned away.  A wanted moment the index holds and
    the floor did not reject is a RANKING miss: it was a candidate and
    the ranking did not put it in the top k.
    """
    clip_of_asset = clip_of_asset or {}
    segment_spans = segment_spans or {}
    credited = set(credited)
    moments = list(query.get("moments") or ())

    rejected = []
    for row in list(weak or ()) + ([best_rejected] if best_rejected else []):
        asset = row.get("asset")
        start = _num(row.get("start"))
        end = _num(row.get("end"))
        if asset is None or start is None or end is None:
            continue
        rejected.append((str(asset), start, end))

    per_moment: list[dict] = []
    for index, moment in enumerate(moments):
        stages: list[str] = []
        details: list[dict] = []
        for asset, start, end in _instance_spans(moment):
            stage, detail = _text_instance_stage(
                asset, start, end,
                credited=index in credited,
                rows=rows or (),
                rejected=rejected,
                abstained=bool(abstained),
                floor=floor,
                segment_spans=segment_spans,
                clip_of_asset=clip_of_asset,
                tolerance=tolerance)
            stages.append(stage)
            details.append({"asset": asset, "start": start, "end": end,
                            "stage": stage, "detail": detail})
        stage = _most_advanced(stages)
        per_moment.append({"moment": index, "stage": stage,
                           "instances": details})

    return _assemble(query, "text", per_moment, moments, credited)


def _text_instance_stage(asset: str, start: float, end: float, *,
                          credited: bool, rows: Sequence[Mapping],
                          rejected: Sequence[tuple[str, float, float]],
                          abstained: bool, floor: float | None,
                          segment_spans: Mapping[str, Sequence[tuple[float, float]]],
                          clip_of_asset: Mapping[str, str],
                          tolerance: float) -> tuple[str, dict]:
    """The stage that lost ONE wanted span of a text query."""
    raw = [row for row in rows
           if row.get("asset") == asset
           and _overlap(_num(row.get("start")) or 0.0,
                        _num(row.get("end")) or 0.0, start, end) > 0.0]
    points = [row for row in rows
              if row.get("asset") == asset
              and _point_inside(_num(row.get("start")) or 0.0,
                                _num(row.get("end")) or 0.0,
                                start, end)]
    touched = [row for row in rows
               if row.get("asset") == asset
               and _touching(_num(row.get("start")) or 0.0,
                             _num(row.get("end")) or 0.0,
                             start, end, tolerance)]
    if credited:
        return "credited", {"reason": "the capped credit rule credited it",
                            "windows": len(raw)}
    if raw:
        return "credited_to_sibling_in_region", {
            "reason": ("a returned window covers it, and the region's one "
                       "credit went to another moment in the same region"),
            "windows": len(raw)}
    if points:
        return "point_window_not_credited", {
            "reason": ("a returned window of NO LENGTH lies inside it, and "
                       "the credit rule counts a window only when it "
                       "OVERLAPS the wanted span"),
            "windows": len(points)}
    if touched:
        return "touched_within_tolerance", {
            "reason": ("a returned window touches it only inside the "
                       "overlap tolerance, so the credit rule did not "
                       "count it"),
            "windows": len(touched)}

    clip = clip_of_asset.get(asset)
    spans = list(segment_spans.get(clip, ())) if clip else []
    if not _covers_span(spans, start - tolerance, end + tolerance):
        return "never_transcribed", {
            "reason": ("no transcript segment in the index covers this "
                       "span"),
            "indexed_segments_on_clip": len(spans)}

    if abstained:
        return "removed_by_query_floor", {
            "reason": ("the lane abstained: nothing it considered cleared "
                       "the floor"),
            "floor": floor}

    for rejected_asset, rejected_start, rejected_end in rejected:
        if rejected_asset != asset:
            continue
        if _overlap(rejected_start, rejected_end, start, end) > 0.0:
            return "removed_by_query_floor", {
                "reason": ("the floor turned this candidate away and the "
                           "lane offered it as the near miss"),
                "floor": floor}

    return "ranked_too_low", {
        "reason": ("the transcript holds it and the ranking did not put it "
                   "in the top k"),
        "indexed_segments_on_clip": len(spans)}


# ── The report ──────────────────────────────────────────────────────


def _assemble(query: Mapping, lane: str, per_moment: list[dict],
              moments: Sequence[Mapping], credited: set[int]) -> dict:
    """One query's attribution: the stages, the count, and the verdict."""
    histogram: dict[str, int] = {}
    for entry in per_moment:
        stage = entry.get("stage") or "unattributed"
        histogram[stage] = histogram.get(stage, 0) + 1
    uncredited = [entry for entry in per_moment
                  if entry.get("stage") != "credited"]
    uncredited_histogram: dict[str, int] = {}
    for entry in uncredited:
        stage = entry.get("stage") or "unattributed"
        uncredited_histogram[stage] = uncredited_histogram.get(stage, 0) + 1
    failure_stage = _dominant(uncredited_histogram)
    failure_kind = None
    if failure_stage:
        failure_kind = ("retrieval"
                        if failure_stage in RETRIEVAL_FAILURE_STAGES
                        else "credit_accounting")
    sibling_localized = sum(
        1 for entry in per_moment
        for instance in (entry.get("instances") or ())
        if (instance.get("detail") or {}).get("localized_on_sibling"))
    return {
        "lane": lane,
        "query_id": query.get("id"),
        "category": query.get("category"),
        "outcome": "found" if not uncredited else "missed",
        "wanted_items": len(per_moment),
        "credited_items": len(per_moment) - len(uncredited),
        "instances_localized_on_sibling": sibling_localized,
        "stages": histogram,
        "uncredited_stages": uncredited_histogram,
        "failure_stage": failure_stage,
        "failure_pipeline_stage": (PIPELINE_STAGE.get(failure_stage)
                                    if failure_stage else None),
        "failure_kind": failure_kind,
        "is_retrieval_failure": bool(
            failure_stage and failure_stage in RETRIEVAL_FAILURE_STAGES),
        "per_moment": per_moment,
    }


def _dominant(histogram: Mapping[str, int]) -> str | None:
    """The stage that explains the most of the deficit.

    Ties go to the stage that is FURTHEST along the pipeline, because an
    upstream stage is the more fundamental cause: a target that was
    never decoded was never going to be ranked.
    """
    if not histogram:
        return None
    best = None
    for stage, count in histogram.items():
        if stage not in ADVANCEMENT:
            continue
        if best is None or count > histogram[best] or (
                count == histogram[best]
                and ADVANCEMENT.index(stage) < ADVANCEMENT.index(best)):
            best = stage
    return best


def attribute_unscored(query: Mapping, lane: str, reason: str) -> dict:
    """An entry the scorer does not score, and why.

    An UNLABELLED entry is not a miss and an absent entry is not a
    wanted thing, so neither can carry a stage.  Saying so is the
    honest report; a stage here would be invented.
    """
    return {
        "lane": lane,
        "query_id": query.get("id"),
        "category": query.get("category"),
        "outcome": reason,
        "wanted_items": 0,
        "credited_items": 0,
        "stages": {},
        "uncredited_stages": {},
        "failure_stage": None,
        "failure_pipeline_stage": None,
        "failure_kind": None,
        "is_retrieval_failure": False,
        "per_moment": [],
    }
