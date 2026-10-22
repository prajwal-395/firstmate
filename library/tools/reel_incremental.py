"""Apply changed overlays to a duplicate staging, then check the whole plan.

Unchanged footage, cards, captions, frame, post header and native ending
hold are proved against the plan. A card may share a motion-graphics row
with lower thirds without becoming part of their replacement diff. The
hold's exact source frame, record span and zero speed must agree; its
freshly treated live tail supplies its inherited transform and grade.

Changed overlay removals and placements are one provisional write pass,
without per-batch Project Model transactions. One final row inventory is
shared by deterministic checks. A refusal deletes only this call's own
staging and restores the final cursor before the caller falls back.
Replayed edit-ledger programs still refuse because their carriers cannot
safely be duplicated. Fake-Resolve item-by-item parity is tested in
`tests/unit/reels/test_reel_incremental.py`.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from library.tools.ren_refusal import RenRefusal

#: The closed category set of an incremental refusal. An unrecognised
#: category is a defect, not a licence to fall back.
INCREMENTAL_REFUSAL_CATEGORIES = ("unsupported", "stale", "conflict", "broken")

#: How the host may proceed, per category. ``auto_recoverable`` permits
#: only the named safe recovery, ``needs_question`` asks the smallest
#: blocking question, ``blocked`` states what must be resolved first.
INCREMENTAL_REFUSAL_RECOVERY_CLASSES = {
    "unsupported": "auto_recoverable",
    "stale": "needs_question",
    "conflict": "needs_question",
    "broken": "blocked",
}

#: The next action, per category. The category says what kind of refusal
#: this is; the fix says the exact next step.
INCREMENTAL_REFUSAL_FIXES = {
    "unsupported": (
        "This plan is not incrementally applicable. Route it to the "
        "full rebuild, which places the reel from the plan; the router "
        "decides whether that fallback is permitted."),
    "stale": (
        "The live reel drifted from the plan it was built from. "
        "Rebuild the reel from the current plan, or resolve the drift "
        "first; do not patch over a stale base."),
    "conflict": (
        "The live timeline disagrees with the plan. Preserve the live "
        "timeline and resolve the difference before editing it."),
    "broken": (
        "The live timeline could not be read. Re-read it and retry; do "
        "not rebuild over state nobody could read."),
}


class IncrementalRefused(RenRefusal):
    """The incremental backend cannot apply this plan safely.

    AXI-42: a typed refusal, not a bare exception. It carries the
    category (which decides whether a rebuild may follow), the next
    action and the recovery class, so this path refuses in the same
    shape as every other refusal in Ren. The category set is CLOSED: an
    unrecognised category is a defect, not a licence to fall back.
    """

    CODE = "REN_INCREMENTAL_REFUSED"
    USER_MESSAGE = ("I couldn't patch this reel in place, so nothing on "
                    "the timeline changed. The reason and the next step "
                    "are named below.")

    CATEGORIES = INCREMENTAL_REFUSAL_CATEGORIES
    RECOVERY_CLASSES = INCREMENTAL_REFUSAL_RECOVERY_CLASSES
    FIXES = INCREMENTAL_REFUSAL_FIXES

    category = "unsupported"

    def __init__(self, why: str, *, what: str = "", fix: str = "",
                 category: str = "", recovery_class: str = "") -> None:
        resolved = category or type(self).category
        if resolved not in self.CATEGORIES:
            raise ValueError(
                f"unknown incremental refusal category: {resolved!r}; one of "
                f"{list(self.CATEGORIES)}")
        self.category = resolved
        super().__init__(
            what or "the incremental route cannot apply this plan safely",
            why,
            fix or self.FIXES[resolved],
            recovery_class=(recovery_class
                            or self.RECOVERY_CLASSES[resolved]),
        )


class IncrementalStale(IncrementalRefused):
    """The recorded base no longer matches the live timeline."""
    category = "stale"
    CODE = "REN_INCREMENTAL_STALE"


class IncrementalConflict(IncrementalRefused):
    """A live edit conflicts with the planned incremental diff."""
    category = "conflict"
    CODE = "REN_INCREMENTAL_CONFLICT"


class IncrementalBroken(IncrementalRefused):
    """Live state could not be read or the staging operation malfunctioned."""
    category = "broken"
    CODE = "REN_INCREMENTAL_BROKEN"


class _RowInventory:
    """One row enumeration shared by the decision's deterministic checks.

    VC-12: the enumeration is implemented in ``transaction_orchestration``
    (shared with the post-patch re-check); this subclasses it so existing
    imports and ``isinstance`` checks keep working.
    """

    def __init__(self, timeline):
        from library.tools import transaction_orchestration as _orch

        if isinstance(timeline, _orch.SharedRowInventory):
            self.rows = dict(timeline.rows)
            self.names = dict(timeline.names)
            self.counts = dict(timeline.counts)
            return
        wrapped = _orch.SharedRowInventory(timeline)
        self.rows = wrapped.rows
        self.names = wrapped.names
        self.counts = wrapped.counts

    def GetTrackCount(self, kind):
        return self.counts[kind]

    def GetTrackName(self, kind, index):
        return self.names[kind, index]

    def GetItemListInTrack(self, kind, index):
        return self.rows.get((kind, index), ())


# ── inventories: plain data, no Resolve writes ──────────────────────

def _norm_source(path: str) -> str:
    """A pool path with its image-sequence bracket range stripped.

    A sequence reports ONE `File Path` with a bracket range; the
    directory is the identity (the same reading `PoolIndex` takes).
    """
    text = str(path or "")
    if "[" in text:
        text = os.path.normpath(os.path.dirname(text.split("[")[0]))
    return os.path.normpath(text) if text else ""


def _live_source(item) -> str:
    try:
        pool_item = item.GetMediaPoolItem()
    except Exception:
        return ""
    if pool_item is None:
        return ""
    try:
        return _norm_source(pool_item.GetClipProperty("File Path") or "")
    except Exception:
        return ""


def _live_span(item):
    try:
        from library.tools.resolve_temporal import record_span
        span = record_span(item.GetStart(), item.GetEnd())
        return (span.start, span.end)
    except Exception:
        return None


def live_row_inventory(timeline, indices: Sequence[int],
                       media_type: str) -> Dict[tuple, list]:
    """{(media, index): sorted [(start, end, norm source)]} off the timeline."""
    out: Dict[tuple, list] = {}
    for index in indices:
        try:
            items = timeline.GetItemListInTrack(media_type, int(index)) or []
        except Exception as exc:
            raise IncrementalBroken(
                f"{media_type}{index} would not list ({exc}) - refusing "
                f"to diff against an unreadable row") from exc
        rows = []
        for item in items:
            span = _live_span(item)
            if span is None:
                raise IncrementalBroken(
                    f"{media_type}{index} holds an item whose span cannot "
                    f"be read - refusing to diff against it")
            rows.append((span[0], span[1], _live_source(item)))
        out[(media_type, int(index))] = sorted(rows)
    return out


def footage_expected_rows(placements_list, video_row_by_angle,
                          speech_row_by_angle, fps: float,
                          angle_of_key) -> Dict[tuple, list]:
    """The same inventory the footage pass would place, per (media, row).

    The record duration repeats the placement's own arithmetic
    (`record_frames` for a ledger retime, else the rounded source
    duration at the reel rate), so a retime that moves record spans
    reads as different here and refuses - correctly, since only
    span-identical footage is patchable.
    """
    out: Dict[tuple, list] = {}
    for place in placements_list or ():
        clip = place["clip"]
        track_type = getattr(clip, "track_type", "video")
        key = angle_of_key(clip)
        if track_type == "video":
            row = video_row_by_angle.get(key)
        else:
            row = speech_row_by_angle.get(key)
        if row is None:
            raise IncrementalRefused(
                f"a {track_type} placement has no "
                f"planned row for angle {key!r} - refusing")
        if "rate" in place:
            duration = int(place["record_frames"])
        else:
            from library.tools.resolve_temporal import record_duration_for_seconds
            duration = record_duration_for_seconds(
                place["source_in"], place["source_out"], fps)
        start = int(place["snapped_record"])
        out.setdefault((track_type, int(row)), []).append(
            (start, start + duration, _norm_source(
                getattr(clip, "source_file", "") or "")))
    return {row: sorted(rows) for row, rows in out.items()}


def retime_scope(placements_list, fps: float) -> Optional[dict]:
    """What a retime in this plan moved, named.

    A retime is a CLOCK change: the passage plays at another rate, so
    the reel is shorter or longer and every placement after it moves.
    Measured on the fake Resolve, 2026-10-21: a passage retimed to
    110% occupied record frames 0-87 (its plan's `record_frames`)
    instead of 0-96, and the placement after it moved from frame 96 to
    frame 87.  So a retime is NOT a span-local change, and the scope
    of one is the passage plus the interval the clock moved.

    The scoped route SCOPES a retime of one clip: it re-places the
    passage and the items the clock moved, and reuses everything else
    (`library/tools/reel_retime_scope.py`).  This function is the
    refusal's own vocabulary - it names WHICH passage and HOW FAR the
    clock change reaches, for the retimes the scoped route cannot
    scope and for the record of the ones it did.

    [why](docs/RULE_EVIDENCE.md#the-caption-change-that-rebuilt-the-whole-reel)

    `None` when the plan carries no retime.
    """
    retimed = [place for place in (placements_list or ())
               if isinstance(place, Mapping) and "rate" in place]
    if not retimed:
        return None
    passages = []
    for place in retimed:
        start = int(place.get("snapped_record") or 0)
        frames = int(place.get("record_frames") or 0)
        passages.append({
            "rate": float(place.get("rate") or 1.0),
            "record_start_frame": start,
            "record_end_frame": start + frames,
            "source_in": place.get("source_in"),
            "source_out": place.get("source_out"),
        })
    passages.sort(key=lambda row: row["record_start_frame"])
    first = passages[0]["record_start_frame"]
    last = max(int(place.get("snapped_record") or 0)
               for place in (placements_list or []))
    return {
        "kind": "retime",
        "passages": passages,
        # The clock change reaches every placement after the first
        # retimed passage: that is the interval the retime moved.
        "moved_from_frame": first,
        "moved_to_frame": last,
        "reason": ("a retime changes the reel's clock, so every "
                   "placement after it moves - the scoped path "
                   "re-places the passage and the items the clock "
                   "moved, and a full rebuild places the reel when it "
                   "cannot scope that"),
    }


def check_rows_identical(expected: Mapping[tuple, list],
                         live: Mapping[tuple, list], what: str,
                         scope: Optional[Mapping] = None) -> None:
    """Refuse unless the plan's inventory is exactly what is there.

    `scope` is the change's own extent when the change is a retime:
    the refusal then says WHICH passage and HOW FAR the clock change
    reaches, instead of leaving the reader to work it out from the row
    dump.
    """
    if set(expected) != set(live):
        raise IncrementalConflict(
            f"{what} rows differ (plan {sorted(expected)} vs timeline "
            f"{sorted(live)}) - refusing")
    for row in expected:
        if list(expected[row]) != list(live[row]):
            if scope is not None and scope.get("kind") == "retime":
                raise IncrementalConflict(
                    f"{what} on row {row} differs from the plan - "
                    f"refusing; {_retime_reason(scope)}")
            raise IncrementalConflict(
                f"{what} on row {row} differs from the plan - refusing; "
                f"a full rebuild places it")


def _retime_reason(scope: Mapping) -> str:
    """One sentence naming the retime's passages and its reach."""
    passages = scope.get("passages") or ()
    named = ", ".join(
        f"{float(row.get('rate') or 1.0) * 100:g}% at reel frame "
        f"{row.get('record_start_frame')}"
        for row in passages) or "a retime"
    return (f"a retime ({named}) changes the reel's clock, so every "
            f"placement after frame {scope.get('moved_from_frame')} "
            f"moves; the scoped path re-places the passage and the items "
            f"the clock moved, and a full rebuild places the reel when "
            f"it cannot scope that")


def diff_footage(expected: Mapping[tuple, list],
                 live: Mapping[tuple, list],
                 placements_list, fps: float):
    """What the plan's retime changed, between the live rows and its own.

    A plan with no retime must leave the footage rows exactly as they
    are: the rows are compared for IDENTITY, as they always were.

    A plan WITH a retime is a clock change, and a clock change has a
    shape: the passage plays at another rate, and every placement
    after it moves by the difference.  `reel_retime_scope.diff_rows`
    proves that shape - the shift is the passage's own length change,
    the rate explains the new length, and the moved items all start
    after the passage's old end - and returns the items to re-place.

    A difference that is NOT that shape refuses, by name: the scoped
    route places a reel it can prove it placed, and a full rebuild
    places the rest.
    """
    from library.tools.reel_retime_scope import (
        RetimeShapeError,
        diff_rows,
        passages_from_placements,
    )

    try:
        passages = passages_from_placements(placements_list, fps)
    except RetimeShapeError as exc:
        raise IncrementalRefused(str(exc), category="unsupported") from exc
    if not passages:
        check_rows_identical(expected, live, "footage")
        return None
    try:
        return diff_rows(expected=expected, live=live, passages=passages,
                         fps=fps)
    except RetimeShapeError as exc:
        scope = retime_scope(placements_list, fps)
        raise IncrementalRefused(
            f"the footage rows differ from the plan in a way this "
            f"retime does not explain - refusing; {exc}; "
            f"{_retime_reason(scope)}",
            category="unsupported") from exc


# ── overlay spans and sources, per kind ─────────────────────────────

def helper_span_frames(segment: Mapping, fps: float) -> tuple:
    """The record span `place_overlay_segments` places a still/video at."""
    start = int(round(float(segment["timeline_start"]) * fps))
    return (start, start + int(segment["total_frames"]))


def caption_span_frames(segment: Mapping, fps: float) -> tuple:
    """The record span the caption loop places, per-edge rounded."""
    from library.tools.frame_utils import span_frames
    return span_frames(segment["timeline_start"], segment["timeline_end"],
                       fps)


def _kind_rows(track_plan, role: str) -> List[int]:
    return [int(row.index) for row in track_plan.rows_for_role(role)]


def overlay_segment_row(segment: Mapping, rows: Sequence[int]) -> int:
    """The row the shared placer gives this segment, by its lane."""
    from library.tools.reel_build import ReelBuildError

    lane = int(segment.get("lane", 0) or 0)
    if lane >= len(rows):
        raise IncrementalRefused(
            f"overlay segment {segment.get('segment_id')!r} names lane "
            f"{lane} of {len(rows)} row(s) - refusing")
    return int(rows[lane])


def segment_source_file(segment: Mapping) -> str:
    return _norm_source(segment.get("overlay_path") or "")


def _file_digest(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        return f"missing:{os.path.basename(str(path))}"


@dataclass
class OverlayDiff:
    """What one patchable kind needs: replace in place, add, remove."""
    kind: str
    rows: List[int] = field(default_factory=list)
    replace: List[tuple] = field(default_factory=list)
    added: List[dict] = field(default_factory=list)
    removed: List[object] = field(default_factory=list)


def diff_overlay_kind(timeline, kind: str, rows: Sequence[int],
                      segments: Sequence[Mapping], fps: float, *, exclude_ids=()) -> OverlayDiff:
    """Match the kind's live items to its new segments by (row, span).

    Same span, different bytes: replace. A live span the plan no longer
    names: remove. A planned span with nothing on it: add. A segment
    whose file is missing refuses - the full build would raise naming
    it, and a patch must not proceed past that.
    """
    try:
        live_by_row: Dict[int, list] = {}
        for index in rows:
            live_by_row[int(index)] = list(
                timeline.GetItemListInTrack("video", int(index)) or [])
    except Exception as exc:
        raise IncrementalBroken(
            f"{kind} rows would not list ({exc}) - refusing") from exc
    live_by_span: Dict[tuple, list] = {}
    for index, items in live_by_row.items():
        for item in items:
            if str(item.GetUniqueId()) in exclude_ids:
                continue
            span = _live_span(item)
            if span is None:
                raise IncrementalBroken(
                    f"{kind} holds an item whose span cannot be read - "
                    f"refusing")
            live_by_span.setdefault((int(index), span[0], span[1]),
                                    []).append(item)
    diff = OverlayDiff(kind=kind, rows=[int(r) for r in rows])
    claimed: set = set()
    for segment in segments or ():
        if not isinstance(segment, Mapping):
            raise IncrementalBroken(
                f"{kind} carries an unreadable segment - refusing")
        row = overlay_segment_row(segment, rows)
        span = helper_span_frames(segment, fps)
        key = (row, span[0], span[1])
        path = segment.get("overlay_path") or ""
        if not path or not os.path.isfile(path):
            raise IncrementalBroken(
                f"{kind} segment {segment.get('segment_id')!r} names "
                f"{path!r}, which is not a file - refusing; the full "
                f"build raises naming it")
        found = live_by_span.get(key, [])
        if not found:
            diff.added.append(dict(segment))
            continue
        if len(found) > 1:
            raise IncrementalConflict(
                f"{kind} has {len(found)} items at {key} - refusing an "
                f"ambiguous match")
        live = found[0]
        claimed.add(key)
        if _file_digest(path) != _file_digest(
                _live_raw_path(live)):
            diff.replace.append((live, dict(segment)))

    for key, items in live_by_span.items():
        if key not in claimed:
            diff.removed.extend(items)
    return diff


def _live_raw_path(item) -> str:
    """The live item's pool path WITH its bracket range, for digesting."""
    try:
        pool_item = item.GetMediaPoolItem()
    except Exception:
        return ""
    if pool_item is None:
        return ""
    try:
        return pool_item.GetClipProperty("File Path") or ""
    except Exception:
        return ""


# ── the decision: patch or rebuild ──────────────────────────────────

@dataclass
class IncrementalPlan:
    """The applicable patch: per-kind overlay diffs plus re-run flags."""
    diffs: List[OverlayDiff] = field(default_factory=list)
    touches_overlays: bool = False
    #: The retime the plan carries, as a scope: the passage it speeds
    #: up and the items the clock moved.  `None` where the plan
    #: carries no retime; an EMPTY scope where the timeline already
    #: holds the plan's retime.
    footage: Optional[object] = None


def _check_incremental_eligibility(freeze_tail, post_header, ledger_rows):
    """Refuse unsupported plans before creating a staging timeline."""
    from library.tools import edit_ledger as _ledger

    if any((row.get("op") in (*_ledger.REPLAYED_OPS,
                              *_ledger.CARRIER_OPS, "edit_program"))
           for row in (ledger_rows or ())):
        raise IncrementalRefused(
            "edit-ledger rows replay onto the footage through the algebra "
            "- refusing; only the full build replays them")


def _scope_cost_decision(scope, *, project_folder: str = "") -> dict:
    """Is the scoped patch cheaper than rebuilding the reel?

    PERF-003 asks the engine to DETECT when the small-delta strategy is
    actually more expensive than bulk reconstruction.  Two measurements
    are compared, and neither is ever a stand-in for the other:

    - the SCOPE: how many items the patch touches and how many frames
      it moves, from `change_scope`;
    - the COST: what this project has MEASURED for a targeted edit and
      for a full rebuild, from its own performance ledger.

    Where the project has measured nothing, the answer is
    ``"unmeasured"`` and the scoped path continues: an unknown cost is
    not a reason to do the expensive thing.  Where the ledger has
    measured a full rebuild as cheaper than a targeted edit, the answer
    is ``"bulk"`` and the caller refuses the scoped route, which is
    the one refusal category a rebuild may follow.
    """
    from library.tools.change_scope import compare

    measured = None
    if project_folder:
        from library.tools.reel_build import measured_strategy_costs
        measured = measured_strategy_costs(project_folder)

    if not isinstance(measured, Mapping):
        return {
            "winner": "unmeasured",
            "reason": ("this project has measured neither a targeted "
                       "edit nor a full rebuild, so the two cannot be "
                       "compared - the scoped path continues, because an "
                       "unknown cost is not a reason to do the "
                       "expensive thing"),
            "scope": scope.as_dict() if hasattr(scope, "as_dict") else {},
            "measured_costs": "unmeasured",
        }

    targeted = ((measured.get("targeted") or {}).get("total_s_p50"))
    rebuild = ((measured.get("full_rebuild") or {}).get("total_s_p50"))
    if not isinstance(targeted, (int, float)) or not isinstance(
            rebuild, (int, float)):
        return {
            "winner": "unmeasured",
            "reason": ("this project has not measured both a targeted "
                       "edit and a full rebuild, so the two cannot be "
                       "compared"),
            "scope": scope.as_dict() if hasattr(scope, "as_dict") else {},
            "measured_costs": measured,
        }

    if rebuild < targeted:
        return {
            "winner": "bulk",
            "reason": (f"this project measured a full rebuild at "
                       f"{rebuild:g}s against a targeted edit at "
                       f"{targeted:g}s, so rebuilding is cheaper"),
            "scope": scope.as_dict() if hasattr(scope, "as_dict") else {},
            "measured_costs": measured,
        }
    return {
        "winner": "scoped",
        "reason": (f"this project measured a targeted edit at "
                   f"{targeted:g}s against a full rebuild at "
                   f"{rebuild:g}s, so the scoped path is cheaper"),
        "scope": scope.as_dict() if hasattr(scope, "as_dict") else {},
        "measured_costs": measured,
    }


def _scope_of(plan, prepared, track_plan, fps: float):
    """What the patch touched, as an explicit scope.

    The scope names the ITEMS the patch replaced and the INTERVALS it
    moved, and counts what it reused.  It is computed from the diff the
    patch already made, so it cannot disagree with what was done.

    Items are named by what identifies them: a caption card by its
    segment id, an overlay segment by its own.  Intervals are the
    record spans the patch placed at, per row.

    A RETIME names its own items: the passage it re-placed and the
    items the clock moved.  Both are items the patch touched, and the
    intervals it moved are the frames between where an item was and
    where it now sits.
    """
    from library.tools.change_scope import Scope
    from library.tools.reel_build import _angle_key

    scope = Scope(kind="incremental")
    rows_by_index = {
        int(row.index): row.name
        for row in (list(track_plan.video_tracks)
                    + list(track_plan.audio_tracks))}

    for diff in plan.diffs:
        for live, segment in diff.replace:
            key = str(segment.get("segment_id") or segment.get("overlay_path")
                       or diff.kind)
            span = (caption_span_frames(segment, fps)
                    if diff.kind == "caption"
                    else helper_span_frames(segment, fps))
            row_name = rows_by_index.get(
                int(diff.rows[0]) if diff.rows else -1, "")
            scope.add_item(key, row=row_name)
            scope.add_interval(row_name, span[0], span[1])
        for segment in diff.added:
            key = str(segment.get("segment_id") or segment.get("overlay_path")
                       or diff.kind)
            span = (caption_span_frames(segment, fps)
                    if diff.kind == "caption"
                    else helper_span_frames(segment, fps))
            row_name = rows_by_index.get(
                int(diff.rows[0]) if diff.rows else -1, "")
            scope.add_item(key, row=row_name)
            scope.add_interval(row_name, span[0], span[1])
        for live in diff.removed:
            span = _live_span(live)
            row_name = rows_by_index.get(
                int(diff.rows[0]) if diff.rows else -1, "")
            scope.add_item(str(diff.kind), row=row_name)
            if span is not None:
                scope.add_interval(row_name, span[0], span[1])

    # What the patch did NOT touch: the caption cards and the overlay
    # segments it left alone, and the footage rows it carried over.
    caption_row = track_plan.caption_row()
    if caption_row is not None:
        scope.note_reuse(
            "caption_cards",
            len(prepared.subtitle_segments or ())
            - sum(1 for diff in plan.diffs if diff.kind == "caption"
                  for _live, _segment in diff.replace))
    scope.note_reuse(
        "footage_rows",
        len({(place["clip"].track_type,
              prepared.video_row_by_angle.get(_angle_key(place["clip"]))
              or prepared.speech_row_by_angle.get(_angle_key(place["clip"])))
             for place in prepared.placements_list}))

    # The retime's own items and intervals: the passage it re-placed and
    # the frames the clock moved.
    footage = getattr(plan, "footage", None)
    if footage is not None and not footage.is_empty:
        for change in footage.changes:
            row_name = rows_by_index.get(int(change.row[1]), "")
            key = (f"{change.row[0]}{change.row[1]}@"
                   f"{change.new_span[0]}")
            scope.add_item(key, row=row_name)
            scope.add_interval(row_name, change.old_span[0],
                               change.old_span[1])
            scope.add_interval(row_name, change.new_span[0],
                               change.new_span[1])
        scope.note_reuse("footage_items", int(footage.reused))

    # WHY the scope is what it is: the kinds the patch actually changed.
    changed = sorted({diff.kind for diff in plan.diffs
                      if diff.replace or diff.added or diff.removed})
    if footage is not None and not footage.is_empty:
        changed = sorted(set(changed) | {"retime"})
    scope.reason = (
        f"{', '.join(changed)} changed" if changed
        else "nothing changed - the patch carried the reel over")
    return scope


def decide_incremental(*, timeline, track_plan, prepared,
                       placements_list, video_row_by_angle,
                       speech_row_by_angle, fps: float,
                       cards, transition_placements, subtitle_segments,
                       frame_segments, post_header, freeze_tail,
                       explainer_segments, semantic_segments,
                       lower_third_segments, ledger_rows) -> IncrementalPlan:
    """Patch only the changed overlay bytes, or refuse.

    Every inventory the patch does not own must be identical between
    the duplicate staging and the new plan; anything else refuses with
    the reason, and the caller falls back to the full rebuild. Only the
    three shared-placer overlay kinds (explainer, semantic visuals,
    speaker lower thirds) are patchable, and only by byte change at an
    identical (row, span) - additions and removals ride the same
    delete/place machinery, so they are applicable too.
    """
    from library.tools.reel_build import _angle_key
    from library.tools.timeline_layout import (
        EXPLAINER, FRAME, MOTION_GRAPHICS, POST_HEADER, SEMANTIC, TRANSITIONS,
    )

    _check_incremental_eligibility(freeze_tail, post_header, ledger_rows)
    if not isinstance(timeline, _RowInventory):
        timeline = _RowInventory(timeline)

    # The rows themselves: every video and audio row, by index and
    # name. An extra row, a missing row or a renamed row refuses - the
    # duplicate must be the plan's own layout, not merely a superset.
    planned_rows = {(spec.media_type, int(spec.index)): (spec.name or "")
                    for spec in (track_plan.video_tracks
                                 + track_plan.audio_tracks)}
    try:
        live_rows: dict = {}
        for media_type in ("video", "audio"):
            try:
                count = timeline.GetTrackCount(media_type) or 0
            except Exception as exc:
                raise IncrementalBroken(
                    f"{media_type} rows would not count ({exc}) - "
                    f"refusing") from exc
            for index in range(1, count + 1):
                try:
                    live_rows[(media_type, index)] = (
                        timeline.GetTrackName(media_type, index) or "")
                except Exception as exc:
                    raise IncrementalBroken(
                        f"{media_type}{index} would not name itself "
                        f"({exc}) - refusing") from exc
    except IncrementalRefused:
        raise
    if planned_rows != live_rows:
        raise IncrementalConflict(
            f"timeline rows differ from the plan - refusing; rows are "
            f"the plan's own packing")

    # Footage: span + source per a-roll/speech row.
    aroll = [row.index for row in track_plan.aroll_rows()]
    speech = [row.index for row in track_plan.speech_rows()]
    # A retime is a CLOCK change, and a clock change is a SCOPE: the
    # passage plays at another rate and every placement after it moves
    # by the difference.  The diff names the passage and the items the
    # clock moved, so the route re-places exactly those and reuses the
    # rest - instead of refusing a change it can name.
    expected_footage = footage_expected_rows(
        placements_list, video_row_by_angle, speech_row_by_angle, fps,
        _angle_key)
    live_footage = {**live_row_inventory(timeline, aroll, "video"),
                    **live_row_inventory(timeline, speech, "audio")}
    footage_diff = diff_footage(expected_footage, live_footage,
                                placements_list, fps)

    # Cards, transitions, frame: span + source, identical.
    fixed_card_ids = _check_cards_identical(timeline, track_plan, cards)
    _check_transitions_identical(
        timeline, track_plan, transition_placements, TRANSITIONS)
    _check_frame_identical(
        timeline, track_plan, frame_segments, fps, FRAME)

    _check_frame_identical(timeline, track_plan,
                           getattr(post_header, "segments", ()) or (), fps, POST_HEADER)
    if freeze_tail is not None:
        _check_native_hold(timeline, freeze_tail, video_row_by_angle)

    plan = IncrementalPlan()
    plan.footage = footage_diff
    # Captions are DIFFED, not compared: a caption card is content-named
    # (PR 3048), so a card whose words changed is a different file at
    # the same span.  Every other card on the row is reused.
    #
    # A retime moves the clock the captions sit on, so a caption after
    # the passage moves with it.  That is a PLACEMENT change, and it is
    # scoped the same way the retime is: the cards that moved are
    # re-placed, the cards that did not are reused.  It is allowed only
    # where the change IS a retime - a caption that moved on its own
    # still refuses, because a card that moved for no stated reason is
    # a plan this route cannot prove.
    caption_diff = diff_caption_cards(
        timeline, track_plan, subtitle_segments, fps,
        moved_ok=bool(footage_diff is not None
                      and not footage_diff.is_empty))
    if (caption_diff.replace or caption_diff.added
            or caption_diff.removed):
        plan.touches_overlays = True
    plan.diffs.append(caption_diff)

    for kind, role, segments in (
            ("explainer", EXPLAINER, explainer_segments),
            ("semantic visual", SEMANTIC, semantic_segments),
            ("speaker lower third", MOTION_GRAPHICS,
             lower_third_segments)):
        rows = _kind_rows(track_plan, role)
        if not rows and not segments:
            continue
        if not rows:
            raise IncrementalRefused(
                f"{kind} segments are planned with no {role} row - "
                f"refusing")
        diff = diff_overlay_kind(timeline, kind, rows, segments or (),
                                 fps, exclude_ids=fixed_card_ids)
        if diff.replace or diff.added or diff.removed:
            plan.touches_overlays = True
        plan.diffs.append(diff)
    return plan


def _card_span_frames(card, fps: float) -> tuple:
    start = int(getattr(card, "reel_start_frame", 0))
    frames = int(getattr(card, "source_frames", 0)
                 or getattr(card, "duration_frames", 0))
    return (start, start + frames)


def _check_cards_identical(timeline, track_plan, cards) -> frozenset:
    from library.tools.timeline_layout import (
        card_spans_for_role, lane_of_span,
    )

    head_tail = [card for card in (cards or ())
                 if getattr(card, "placement", "") in ("head", "tail")]
    role = getattr(track_plan, "material", {}).get("card_role")
    expected: Dict[tuple, list] = {}
    if head_tail:
        if role is None:
            raise IncrementalRefused(
                "head/tail cards are planned with no card role - refusing")
        rows = track_plan.rows_for_role(role)
        spans = card_spans_for_role(track_plan.material, role)
        for position, card in enumerate(head_tail):
            lane = lane_of_span(spans, len(spans) - len(head_tail)
                                + position)
            if lane >= len(rows):
                raise IncrementalRefused(
                    "a card's lane is outside its rows - refusing")
            span = _card_span_frames(card, 0.0)
            expected.setdefault(("video", int(rows[lane].index)), []).append(
                (span[0], span[1], _norm_source(
                    getattr(card, "rendered_path", "") or "")))
    live: Dict[tuple, list] = {}
    fixed_ids = set()
    # Cards can share the motion-graphics role with lower thirds. Own
    # only their declared slots; the overlay diff owns the remainder.
    slots = {(kind, index, start, end) for (kind, index), rows in expected.items()
             for start, end, _ in rows}
    if role is not None:
        for row in track_plan.rows_for_role(role):
            for item in timeline.GetItemListInTrack("video", int(row.index)) or ():
                span = _live_span(item)
                if span is not None and ("video", int(row.index), *span) in slots:
                    live.setdefault(("video", int(row.index)), []).append(
                        (*span, _live_source(item)))
                    fixed_ids.add(str(item.GetUniqueId()))
    check_rows_identical(
        {row: sorted(rows) for row, rows in expected.items()},
        {row: sorted(rows) for row, rows in live.items()}, "cards")
    return frozenset(fixed_ids)


def _check_native_hold(timeline, freeze, video_row_by_angle):
    from library.tools.reel_build import _freeze_tail_matches
    row = video_row_by_angle.get(str(freeze.track_index))
    if row is None or len(_freeze_tail_matches(
            timeline, row, freeze.reel_start_frame, freeze.end_frame,
            freeze.held_from, freeze.held_source_frame, require_hold=True)) != 1:
        raise IncrementalConflict("native ending hold differs from the plan")
    return {"called": False, "reused": True, "landed": True,
            "append_verified": True, "verified": True,
            "expected": {"source_file": freeze.held_from,
                         "source_start_frame": freeze.held_source_frame,
                         "track_index": row, "start_frame": freeze.reel_start_frame,
                         "end_frame": freeze.end_frame},
            "append_attempts": [], "repair": {"attempted": False, "status": "not_needed"},
            "post_placement": {"readback_matches": 1, "verified": True}}


def _check_transitions_identical(timeline, track_plan, placements,
                                 role: str) -> None:
    rows = _kind_rows(track_plan, role)
    expected: Dict[tuple, list] = {}
    for placement in placements or ():
        record = (placement.as_dict() if hasattr(placement, "as_dict")
                  else dict(placement))
        row = record.get("track_index")
        if row is None:
            raise IncrementalRefused(
                "a transition element names no row - refusing")
        start = int(record["record_frame"])
        expected.setdefault(("video", int(row)), []).append(
            (start, start + int(record["duration_frames"]),
             _norm_source(record.get("element_path") or "")))
    live: Dict[tuple, list] = {}
    for index in rows:
        live.update(live_row_inventory(timeline, [index], "video"))
    check_rows_identical(
        {row: sorted(rows) for row, rows in expected.items()}, live,
        "transitions")


def caption_source_of(segment: Mapping) -> str:
    """The artefact a caption segment names: a rendered file or a frame dir.

    A caption renders either as one movie or as a frame sequence, and
    the SEQUENCE is the artefact - a directory, not a file.  Reading
    the wrong one would compare a directory against a file and call
    every caption changed.
    """
    path = segment.get("overlay_path") or ""
    frames = ((segment.get("frames") or {}).get("dir", "")
              if segment.get("container") == "frames" else "")
    return _norm_source(frames or path)


def diff_caption_cards(timeline, track_plan, segments,
                        fps: float, *, moved_ok: bool = False) -> "OverlayDiff":
    """Match the caption row's live cards to the plan's, by (row, span).

    A caption card is CONTENT-NAMED (PR 3048: the name carries a
    digest of the drawing inputs), so a card whose words changed is a
    DIFFERENT FILE at the SAME SPAN.  That is the one caption change
    this route patches: same span, different bytes -> replace that one
    card; every other card on the row is REUSED, not re-imported and
    re-placed.

    A caption whose SPAN moved is a PLACEMENT change, not a byte
    change, and this diff refuses it rather than guessing.  The full
    rebuild places it correctly; a patch that deleted one card and
    added another could leave two cards overlapping on one row, and a
    caption row that overlaps is unreadable.  So: added, removed and
    moved captions REFUSE, exactly as they did before.

    `moved_ok` is the ONE exception, and it is the retime's: a retime
    moves the clock the captions sit on, so every caption after the
    passage moves with it.  The cards are then paired BY ORDER - the
    row's cards and the plan's segments, both in play order - and a
    card whose span changed is re-placed at the plan's span while every
    card that did not move is reused.  The delete of every moved card
    happens before any of them is placed, so the row never carries two
    cards over the same frames.
    """
    row = track_plan.caption_row()
    diff = OverlayDiff(kind="caption")
    if not segments:
        if row is not None:
            try:
                live = list(timeline.GetItemListInTrack(
                    "video", int(row.index)) or [])
            except Exception as exc:
                raise IncrementalBroken(
                    f"caption row would not list ({exc}) - refusing") from exc
            if live:
                raise IncrementalRefused(
                    "the plan has no captions and the caption row holds "
                    f"{len(live)} card(s) - refusing; a full rebuild "
                    "places the caption row")
        return diff
    if row is None:
        raise IncrementalRefused(
            "captions are planned with no caption row - refusing")
    try:
        live_items = list(timeline.GetItemListInTrack(
            "video", int(row.index)) or [])
    except Exception as exc:
        raise IncrementalBroken(
            f"caption row would not list ({exc}) - refusing") from exc

    if moved_ok:
        return _diff_caption_cards_in_order(
            live_items, segments, fps, int(row.index))

    live_by_span: Dict[tuple, list] = {}
    for item in live_items:
        span = _live_span(item)
        if span is None:
            raise IncrementalBroken(
                "caption row holds an item whose span cannot be read - "
                "refusing")
        live_by_span.setdefault(
            (int(row.index), span[0], span[1]), []).append(item)

    claimed: set = set()
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise IncrementalBroken(
                "caption row carries an unreadable segment - refusing")
        span = caption_span_frames(segment, fps)
        key = (int(row.index), span[0], span[1])
        source = caption_source_of(segment)
        if not source:
            raise IncrementalBroken(
                f"caption segment {segment.get('segment_id')!r} names no "
                "artefact - refusing")
        found = live_by_span.get(key, [])
        if not found:
            raise IncrementalRefused(
                f"caption segment {segment.get('segment_id')!r} has no "
                f"live card at {key[1]}-{key[2]} - refusing; a caption "
                "that moved is a placement change, and a full rebuild "
                "places the caption row")
        if len(found) > 1:
            raise IncrementalConflict(
                f"caption row has {len(found)} cards at {key} - refusing "
                "an ambiguous match")
        live = found[0]
        claimed.add(key)
        if _file_digest(source) != _file_digest(_live_raw_path(live)):
            diff.replace.append((live, dict(segment)))

    unclaimed = [key for key in live_by_span if key not in claimed]
    if unclaimed:
        raise IncrementalRefused(
            f"{len(unclaimed)} live caption card(s) have no planned card "
            "at the same span - refusing; a caption that moved or was "
            "removed is a placement change, and a full rebuild places the "
            "caption row")
    diff.rows = [int(row.index)]
    return diff


def _diff_caption_cards_in_order(live_items, segments, fps: float,
                                  row_index: int) -> "OverlayDiff":
    """The caption diff for a change that MOVED the clock under the row.

    The row's cards and the plan's segments are paired BY ORDER: both
    are in play order, a clock change moves cards forward without
    reordering them, and the count must agree - a card that appeared or
    disappeared is a different change and is refused here.
    """
    diff = OverlayDiff(kind="caption")
    live_spans = []
    for item in live_items:
        span = _live_span(item)
        if span is None:
            raise IncrementalBroken(
                "caption row holds an item whose span cannot be read - "
                "refusing")
        live_spans.append((span, item))
    live_spans.sort(key=lambda pair: pair[0])
    planned = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise IncrementalBroken(
                "caption row carries an unreadable segment - refusing")
        if not caption_source_of(segment):
            raise IncrementalBroken(
                f"caption segment {segment.get('segment_id')!r} names no "
                "artefact - refusing")
        planned.append((caption_span_frames(segment, fps), segment))
    planned.sort(key=lambda pair: pair[0])
    if len(planned) != len(live_spans):
        raise IncrementalRefused(
            f"the caption row holds {len(live_spans)} card(s) and the plan "
            f"places {len(planned)} - a change that adds or drops a "
            f"caption is not scoped here; a full rebuild places the "
            f"caption row")
    for (live_span, live), (want_span, segment) in zip(live_spans, planned):
        if tuple(live_span) == tuple(want_span):
            if _file_digest(caption_source_of(segment)) != _file_digest(
                    _live_raw_path(live)):
                diff.replace.append((live, dict(segment)))
            continue
        diff.removed.append(live)
        diff.added.append(dict(segment))
    diff.rows = [int(row_index)]
    return diff


def _check_frame_identical(timeline, track_plan, segments, fps: float,
                           role: str) -> None:
    rows = _kind_rows(track_plan, role)
    expected: Dict[tuple, list] = {}
    for segment in segments or ():
        if not rows:
            raise IncrementalRefused(
                "frame segments are planned with no frame row - refusing")
        span = helper_span_frames(segment, fps)
        expected.setdefault(("video", int(rows[0])), []).append(
            (span[0], span[1], segment_source_file(segment)))
    live: Dict[tuple, list] = {}
    for index in rows:
        live.update(live_row_inventory(timeline, [index], "video"))
    check_rows_identical(
        {row: sorted(rows) for row, rows in expected.items()}, live,
        "TV frame")


# ── the patch: delete, place, re-apply setters ──────────────────────

def _kind_check(kind: str) -> str:
    return {"explainer": "F21", "semantic visual": "F22",
            "speaker lower third": "F21"}[kind]


def _placements_by_row(placements_list, video_row_by_angle,
                       speech_row_by_angle, angle_of_key) -> dict:
    """The plan's placements, keyed by the row and the frame they land on.

    The key is `(media type, row index, record frame)`: the row the
    placement owns and the frame it starts at.  Two placements never
    share a row and a start frame, so the key names one placement.
    """
    out: dict = {}
    for place in placements_list or ():
        clip = place.get("clip") if isinstance(place, Mapping) else None
        if clip is None:
            continue
        track_type = getattr(clip, "track_type", "video")
        key = angle_of_key(clip)
        row = (video_row_by_angle.get(key) if track_type == "video"
               else speech_row_by_angle.get(key))
        if row is None:
            continue
        out[(str(track_type), int(row),
             int(place.get("snapped_record") or 0))] = place
    return out


def _live_item_at(timeline, row, span) -> Optional[object]:
    """The live item on `row` occupying `span`, or None."""
    media_type, index = row
    try:
        items = timeline.GetItemListInTrack(media_type, int(index)) or []
    except Exception as exc:
        raise IncrementalBroken(
            f"{media_type}{index} would not list ({exc}) - refusing") from exc
    found = [item for item in items
             if _live_span(item) == (int(span[0]), int(span[1]))]
    if len(found) != 1:
        raise IncrementalBroken(
            f"{media_type}{index} holds {len(found)} item(s) at "
            f"[{span[0]}, {span[1]}) - refusing to re-place an item "
            f"this route cannot name")
    return found[0]


def _live_source_start(item) -> Optional[int]:
    """The source frame the live item reads from, or None if unreadable.

    An item that cannot say where it reads from is NOT proof of
    anything: the check that uses this treats an unreadable answer as
    no answer, and the patch proceeds on the plan's own arithmetic.
    """
    try:
        return int(item.GetSourceStartFrame())
    except Exception:
        return None


def apply_footage_retime(*, project, pool, timeline, staging_name: str,
                          fps: float, diff, prepared, track_plan,
                          video_row_by_angle, speech_row_by_angle,
                          pool_index=None) -> dict:
    """Re-place the items a retime changed: the passage, and the clock's tail.

    The passage is re-placed at its new length, over the source the plan
    names, and the items the clock moved are re-placed at their new
    start over the source they already carry.  Everything else on the
    timeline is left exactly as the duplicate carries it.

    The placement goes through the SAME machinery the full build uses -
    `commit_footage_placements`, with the same op identity, the same
    batching and the same speech-row sweep - so the scoped route and
    the rebuild cannot drift on the arithmetic.  The pool items are
    REUSED: a retime changes no media, so nothing is imported again.

    Returns the record of what it did: the items it replaced, the items
    it moved, and the items it left alone.
    """
    from library.tools.reel_build import (
        PoolIndex,
        _angle_key,
        _assert_placing,
        _resolve_source_frame_span,
        _retimed_span,
        commit_footage_placements,
        pool_item_for,
    )

    if diff is None or diff.is_empty:
        return {"replaced": [], "moved": [], "reused": 0}
    if pool_index is None:
        pool_index = PoolIndex(pool, authoritative=True)
    placements = _placements_by_row(
        prepared.placements_list, video_row_by_angle, speech_row_by_angle,
        _angle_key)
    resolved_channels = getattr(prepared, "resolved_channels", {}) or {}

    doomed: list = []
    wanted: list = []
    for change in diff.changes:
        place = placements.get(
            (change.row[0], int(change.row[1]), int(change.new_span[0])))
        if place is None:
            raise IncrementalBroken(
                f"the plan places nothing at {change.row[0]}"
                f"{change.row[1]} frame {change.new_span[0]}, where the "
                f"retime moves an item - refusing")
        live = _live_item_at(timeline, change.row, change.old_span)
        doomed.append(live)
        wanted.append((change, place, live))

    if doomed:
        _assert_placing(project, timeline)
        # One delete pass: the retimed passage and every item the clock
        # moved leave the staging together, so the row is free for the
        # re-placement and no card is ever left overlapping another.
        timeline.DeleteClips(doomed, False)

    video_ops: list = []
    audio_pairs: list = []
    pool_items: dict = {}
    for change, place, live in wanted:
        clip = place["clip"]
        angle = _angle_key(clip)
        dest_row = (video_row_by_angle.get(angle)
                    if clip.track_type == "video"
                    else speech_row_by_angle.get(angle))
        if dest_row is None:
            raise IncrementalBroken(
                f"a {clip.track_type} placement has no planned row for "
                f"angle {angle!r} - refusing")
        pool_item = pool_item_for(pool, clip.source_file,
                                  pool_index=pool_index)
        if not pool_item:
            raise IncrementalBroken(
                f"the source for {clip.source_file!r} is not in the media "
                f"pool - refusing")
        pool_fps = float(pool_item.GetClipProperty("FPS") or fps)
        appended_out = place["source_out"]
        if "rate" in place:
            # A retimed passage: the record frames it plays are appended
            # as source, and the speed pass makes those record frames
            # play the passage.
            appended_out = (place["source_in"]
                            + _retimed_span(place, staging_name)[1] / fps)
        source_in, source_out = _resolve_source_frame_span(
            place["source_in"], appended_out, pool_fps)
        # The live item must be the one the plan describes: it plays the
        # SAME source, from the same frame.  A live item that plays
        # other source is not the passage the retime is about, and
        # re-placing it would place material nobody measured.
        live_source = _live_source_start(live)
        if live_source is not None and live_source != source_in:
            raise IncrementalBroken(
                f"the item on {change.row[0]}{change.row[1]} at "
                f"[{change.old_span[0]}, {change.old_span[1]}) reads "
                f"source frame {live_source} where the plan places "
                f"{source_in} - refusing")
        if "rate" in place:
            record_duration = int(place["record_frames"])
        else:
            record_duration = int(round(
                (place["source_out"] - place["source_in"]) * fps))
        pool_items[str(pool_item.GetUniqueId())] = pool_item
        op = {
            "op": "clip.insert",
            "unique_id": (
                f"footage:{staging_name}:{clip.track_type}"
                f":{clip.source_file}:{place['snapped_record']}"),
            "track_type": clip.track_type,
            "track_index": dest_row,
            "record_in": int(place["snapped_record"]),
            "record_out": int(place["snapped_record"]) + record_duration,
            "media_pool_item": str(pool_item.GetUniqueId()),
            "source_in": source_in,
            "source_out": source_out,
        }
        if clip.track_type == "video":
            video_ops.append(op)
        else:
            audio_pairs.append((op, {
                "target_row": dest_row,
                "expected_channel": resolved_channels.get(angle, 1),
                "label": (f"A{dest_row} "
                          f"{clip.source_file.rsplit('/', 1)[-1]} "
                          f"@{place['snapped_record']}"),
                "record_in": int(place["snapped_record"]),
                "source_file": clip.source_file,
            }))

    if video_ops or audio_pairs:
        def append_planned_footage(operations):
            pool.AppendToTimeline([{
                "mediaPoolItem": pool_items[op["media_pool_item"]],
                "startFrame": op["source_in"], "endFrame": op["source_out"],
                "mediaType": 1 if op["track_type"] == "video" else 2,
                "trackIndex": op["track_index"],
                "recordFrame": op["record_in"],
            } for op in operations])
        _assert_placing(project, timeline)
        commit_footage_placements(
            project, timeline, track_plan, staging_name, video_ops,
            audio_pairs, append_ops=append_planned_footage)

    return {
        "replaced": [c.as_dict() for c in diff.retimed],
        "moved": [c.as_dict() for c in diff.moved],
        "reused": int(diff.reused),
        "delta_frames": int(diff.delta),
        "passages": [p.as_dict() for p in diff.passages],
    }


def apply_overlay_diffs(*, project, pool, timeline, staging_name: str,
                        fps: float, width: int, height: int,
                        track_plan, diffs: Sequence[OverlayDiff],
                        suppressions, overlay_intent, draw_gain: float,
                        project_folder: str, resolve_app,
                        model_revision=None) -> dict:
    """Delete removed/replaced overlay items, place the new segments.

    Deletions commit in ONE `clip.delete` EditPatch (verified); the
    changed segments then go through the SAME `place_overlay_segments`
    helper the full build uses, with the same intent, suppression and
    sweep-record plumbing. Returns {"sweep_records", "held_back",
    "applied_intent", "seen_ids", "seen_labels"} for the record.

    A CAPTION card is placed by `reel_caption_placement` instead: the
    caption row has its own span arithmetic (per-edge rounded), its own
    tight-box geometry and its own sweep record, and the full build
    places it through that owner.  Calling the same owner here is what
    makes a patched card land exactly where a full build puts it.
    """
    from library.tools.reel_build import (
        PoolIndex, _assert_placing, place_overlay_segments,
    )

    doomed: list = []
    for diff in diffs:
        doomed.extend(diff.removed)
        doomed.extend(live for live, _segment in diff.replace)
    if doomed:
        _assert_placing(project, timeline)
        # Provisional result: the final inventory proves every removal
        # and replacement together. No per-batch model replay or judge.
        timeline.DeleteClips(doomed, False)

    pool_index = PoolIndex(pool, authoritative=True)
    sweep_records: list = []
    held_back: list = []
    seen_ids: list = []
    seen_labels: list = []
    applied_intent: list = []
    for diff in diffs:
        segments = ([dict(segment) for _live, segment in diff.replace]
                    + [dict(segment) for segment in diff.added])
        if not segments:
            continue
        _assert_placing(project, timeline)
        if diff.kind == "caption":
            from library.tools.reel_caption_placement import (
                place_caption_cards,
            )
            outcome = place_caption_cards(
                pool=pool, project=project, timeline=timeline,
                name=staging_name, fps=fps, width=width, height=height,
                segments=segments, track_plan=track_plan,
                project_folder=project_folder,
                overlay_intent=overlay_intent,
                applied_intent_keys=applied_intent,
                seen_intent_ids=seen_ids,
                draw_gain=draw_gain, pool_index=pool_index,
                suppressions=suppressions, suppressed_ids=held_back,
                sweep_records=sweep_records, resolve_app=resolve_app)
            continue
        held_back.extend(place_overlay_segments(
            pool, project, timeline, staging_name, fps, segments,
            list(diff.rows), kind=diff.kind, check=_kind_check(diff.kind),
            project_folder=project_folder,
            overlay_intent=overlay_intent, frame=(width, height),
            seen_ids=seen_ids,
            do_not_draw=suppressions,
            intent_applied=applied_intent,
            seen_labels=seen_labels,
            sweep_out=sweep_records,
            draw_gain=draw_gain,
            resolve_app=resolve_app,
            pool_index=pool_index))
    return {"sweep_records": sweep_records, "held_back": held_back,
            "applied_intent": applied_intent, "seen_ids": seen_ids,
            "seen_labels": seen_labels}


def _suppression_ids(segments_by_kind, suppressions,
                     staging_name: str) -> tuple:
    """Every segment id this plan holds back, plus the unmatched report."""
    import sys as _sys

    from library.tools import do_not_draw as _dnd

    suppressed: list = []
    for segments in segments_by_kind:
        for segment in segments or ():
            held_back, why = _dnd.should_suppress(
                suppressions, staging_name, segment or {})
            if held_back:
                print(f"  {staging_name}: {why}", file=_sys.stderr)
                from library.tools.reel_build import _overlay_segment_id
                suppressed.append(_overlay_segment_id(segment or {}))
    unmatched = _dnd.report_unmatched(
        suppressions, staging_name,
        [segment for segments in segments_by_kind
         for segment in (segments or ())],
        source=f"do_not_draw.json ({staging_name})")
    return suppressed, unmatched


def _reconstruct_sweep_records(*, timeline, project, track_plan,
                               kinds, width: int, height: int,
                               draw_gain: float, fps: float,
                               skip_keys: frozenset = frozenset()) -> list:
    """Sweep-shaped records for every tight overlay on the staging.

    The deferred overlay sweep judges what these records name, and the
    model writer reads them as what the staging placed - so they must
    cover every tight overlay, patched or not. Each record is built
    from the new plan's segment (identical-by-inventory to what placed
    the live item) plus the live item's held transform, read back the
    way the placer reads it. Segments the patch already recorded
    (`skip_keys`, as (row, record frame)) are not rebuilt.
    """
    from library.tools.overlay_draw_intent import (
        segment_canvas as _segment_canvas,
    )
    from library.tools.reel_build import (
        _held_transform, _native_overlay_position, _overlay_segment_id,
    )
    from collections.abc import Mapping as _Mapping

    records: list = []
    for kind, rows, segments, span_of in kinds:
        for segment in segments or ():
            if not isinstance(segment, _Mapping):
                continue
            canvas = _segment_canvas(segment)
            if canvas is None:
                continue
            row = overlay_segment_row(segment, rows)
            span = span_of(segment, fps)
            if (int(row), int(span[0])) in skip_keys:
                continue
            found = None
            try:
                items = (timeline.GetItemListInTrack("video", row) or [])
            except Exception as exc:
                raise IncrementalBroken(
                    f"{kind} row {row} would not list ({exc}) - "
                    f"refusing") from exc
            for item in items:
                item_span = _live_span(item)
                if (item_span is not None
                        and (int(item_span[0]), int(item_span[1]))
                        == (int(span[0]), int(span[1]))):
                    found = item
                    break
            if found is None:
                raise IncrementalBroken(
                    f"{kind} segment {segment.get('segment_id')!r} at "
                    f"{span} has no item on V{row} after the patch - "
                    f"refusing")
            held = _held_transform(found, timeline, project)
            records.append({
                "label": (segment.get("segment_id")
                          or _overlay_segment_id(segment)),
                "kind": kind,
                "segment_id": segment.get("segment_id"),
                "placement_label": segment.get("placement_label"),
                "track_index": row,
                "record_frame": span[0],
                "canvas_wh": canvas,
                "placement": (segment.get("tight_box") or {}).get(
                    "placement"),
                "overlay_path": segment.get("overlay_path") or "",
                "frames_dir": "",
                "timeline_start_frame": span[0],
                "duration_frames": span[1] - span[0],
                "source_start_frame": int(
                    segment.get("source_in_frame") or 0),
                "transform": (dict(held)
                              if isinstance(held, _Mapping) else None),
                "native_position": _native_overlay_position(
                    held, canvas, (width, height), draw_gain),
            })
    return records


def build_staged_incremental(
        *, project, staging_name: str, final_name: str,
        recorded_carried: Optional[str], prepared,
        overlay_placements, explainer_segments, semantic_segments,
        lower_third_segments, look, grade_cdl, power_grade, motion,
        overlay_intent, suppressions, fps: float,
        width: int, height: int, project_folder: str, draw_gain: float,
        resolve_app, single_reel_scope: bool,
        placement_mode: str, transcript: dict) -> dict:
    """Duplicate the final, patch only what changed, return a build record.

    Unsupported plans may be routed to the full `build_reel_timeline`
    with the refusal reason recorded. Stale, conflicting or broken live
    state is raised to the caller and never rebuilt over. The returned dict carries the same keys the
    rebuild loop reads off a full build's record, so verify and promote
    run unchanged. Must run under the same exclusive Resolve hold the
    full build takes (the duplicate, the deletes and the re-applied
    setters are all writes).
    """
    import sys as _sys
    import time as _time

    from library.tools.reel_build import ReelBuildError, _assert_placing
    from library.tools.timeline_layout import (
        EXPLAINER, FRAME, MOTION_GRAPHICS, POST_HEADER, SEMANTIC,
    )

    if placement_mode != "append":
        raise IncrementalRefused(
            f"placement mode is {placement_mode!r} - the incremental "
            f"path patches a live timeline, so only append applies")

    _check_incremental_eligibility(
        prepared.freeze_tail, prepared.post_header, prepared.ledger_rows)

    patch_started = _time.perf_counter()
    try:
        count = project.GetTimelineCount() or 0
    except Exception as exc:
        raise IncrementalBroken(
            f"the project would not count its timelines ({exc}) - "
            f"falling back to the full rebuild") from exc
    final_timeline = None
    for index in range(1, count + 1):
        try:
            candidate = project.GetTimelineByIndex(index)
        except Exception:
            continue
        if candidate is not None and candidate.GetName() == final_name:
            final_timeline = candidate
            break
    if final_timeline is None:
        raise IncrementalRefused(
            "no approved timeline under the final name yet - only the "
            "full build can stage a first reel")

    # No drift: the live final must still be the build the record
    # describes, or the diff has no base.
    from library.tools import reel_rebuild_need as _need
    try:
        live_carried = _need.carried_digest_live(project, final_timeline)
    except Exception as exc:
        raise IncrementalBroken(
            f"the final timeline could not be read ({exc}) - refusing"
        ) from exc
    if not recorded_carried:
        # NO build record for this final: the reel was not built by this
        # project (an imported reel, or one whose record was pruned).
        # The incremental diff has no base, so this is a BACKEND-
        # SELECTION refusal and the full build places the reel - which
        # is what this same message has always said. It was raised as
        # `IncrementalStale`, which the build does NOT fall through
        # from, so a reel this project never built could not be
        # rebuilt at all. Measured 2026-10-20 on the Ren Scratch -
        # breaktest project: an imported reel refused every build with
        # this message and never reached the full rebuild it names.
        raise IncrementalRefused(
            "the final drifted since its build (or its record is gone) "
            "- only the full rebuild restores it")
    if live_carried != recorded_carried:
        raise IncrementalStale(
            "the final drifted since its build - only the full rebuild "
            "restores it")

    # VC-09 / AXI-35: the strategy is selected before the candidate
    # exists. The stale/conflict checks above are the eligibility
    # evidence; the staging duplicate below is already backend-specific
    # work. The route is asked of the ONE router entry point, with the
    # eligibility result as its evidence, so this arm and the rebuild
    # arm are decided by the same rule.
    from library.tools.execution_router import (
        STRATEGY_INCREMENTAL_ID,
        route_compiled_edit,
    )
    execution_routing = route_compiled_edit(
        {"operations": [{"op": STRATEGY_INCREMENTAL_ID}]},
        context={
            "incremental_eligibility": True,
            "reason": ("incremental eligibility checks passed for this "
                       "staged diff"),
        })

    from library.tools.composed_edit import prepare_candidate
    try:
        staging = prepare_candidate(final_timeline, staging_name)
    except Exception as exc:
        raise IncrementalBroken(
            f"the final could not be duplicated as a verified candidate "
            f"({exc}) - falling back to the full rebuild") from exc
    _assert_placing(project, staging)
    from library.tools.composed_edit import CandidateLifecycle
    candidate_lifecycle = CandidateLifecycle.create(
        final_timeline, staging, final_name)
    candidate_lifecycle.mark_applying()



    try:
        track_plan = prepared.track_plan
        plan = decide_incremental(
            timeline=staging, track_plan=track_plan, prepared=prepared,
            placements_list=prepared.placements_list,
            video_row_by_angle=prepared.video_row_by_angle,
            speech_row_by_angle=prepared.speech_row_by_angle, fps=fps,
            cards=prepared.cards, transition_placements=overlay_placements,
            subtitle_segments=prepared.subtitle_segments,
            frame_segments=prepared.frame_segments,
            post_header=prepared.post_header, freeze_tail=prepared.freeze_tail,
            explainer_segments=explainer_segments,
            semantic_segments=semantic_segments,
            lower_third_segments=lower_third_segments,
            ledger_rows=prepared.ledger_rows)

        # Is the scoped patch actually CHEAPER than rebuilding the reel?
        # A small-delta strategy that costs more than a bulk rebuild is
        # not a small-delta strategy, and the caller must be told which
        # one it is running.  The comparison is made on THIS project's
        # own measured costs; where nothing was measured the answer is
        # "unmeasured" and the scoped path continues, because an
        # unknown cost is not a reason to do the expensive thing.
        _scope = _scope_of(plan, prepared, track_plan, fps)
        _cost = _scope_cost_decision(
            _scope, project_folder=project_folder)
        if _cost.get("winner") == "bulk":
            raise IncrementalRefused(
                "the scoped patch is measurably more expensive than "
                f"rebuilding the reel ({_cost.get('reason')}) - "
                "refusing; a full rebuild places it",
                category="unsupported")

        # The retime's own re-placement: the passage and the items the
        # clock moved.  It runs BEFORE the overlays, because the overlay
        # diff was computed against the plan's spans and the clock is
        # what moved them.
        _footage_record = apply_footage_retime(
            project=project, pool=project.GetMediaPool(), timeline=staging,
            staging_name=staging_name, fps=fps, diff=plan.footage,
            prepared=prepared, track_plan=track_plan,
            video_row_by_angle=prepared.video_row_by_angle,
            speech_row_by_angle=prepared.speech_row_by_angle)

        patch_out = apply_overlay_diffs(
            project=project, pool=project.GetMediaPool(), timeline=staging,
            staging_name=staging_name, fps=fps, width=width, height=height,
            track_plan=track_plan, diffs=plan.diffs,
            suppressions=suppressions, overlay_intent=overlay_intent,
            draw_gain=draw_gain, project_folder=project_folder,
            resolve_app=resolve_app)

        from library.tools import edit_ledger as _ledger
        from library.tools.reel_build import (
            _angle_key, _apply_ledger_retimes, apply_transform_overrides,
        )

        build_record = dict(prepared.build_record)
        build_record["stream_enforcement"] = {
            "checked": 0, "deleted": [], "unverified": []}
        # WHAT THE RETIME TOUCHED: the passage it re-placed, the items
        # the clock moved, and what it left alone.  A scope nobody can
        # read is a scope nobody can measure.
        build_record["footage_retime"] = _footage_record

        # Ledger retimes: absolute speeds, re-applied (a no-op where the
        # plan is unchanged, the update where it changed).
        build_record["ledger_retimes"] = _apply_ledger_retimes(
            staging, prepared.placements_list, fps,
            sorted(set(prepared.video_row_by_angle.values())),
            sorted(set(prepared.speech_row_by_angle.values())), staging_name)

        # The declared grade: the same setter the full build runs.
        footage_sources = {
            getattr(place["clip"], "source_file", "")
            for place in prepared.placements_list
            if getattr(place["clip"], "track_type", "video") == "video"}
        if grade_cdl or power_grade:
            from library.tools import reel_look as _grade
            cdl_record = _grade.apply_grade(
                staging, track_plan, grade_cdl, power_grade=power_grade,
                footage_sources={s for s in footage_sources if s})
            build_record["cdl"] = cdl_record
        else:
            build_record["cdl"] = {"applied": [], "skipped": [],
                                   "warnings": [], "route": "none",
                                   "verified": False,
                                   "basis": "no look declared - nothing graded"}

        # The punch-in aim, per shot, exactly as the full build aims it.
        screen_window = None
        if look is not None:
            from library.tools.reel_build import aim_picture_row, verify_motion_screen_coverage
            from library.tools.tv_frame import screen_window_rect
            screen_window = screen_window_rect(look, width, height)
            aimed = 0
            for aroll_row in track_plan.aroll_rows():
                row_items = (staging.GetItemListInTrack(
                    "video", aroll_row.index) or [])
                row_places = [
                    place for place in prepared.placements_list
                    if getattr(place["clip"], "track_type", "video") == "video"
                    and prepared.video_row_by_angle.get(
                        _angle_key(place["clip"])) == aroll_row.index]
                row_places.sort(key=lambda place: place["snapped_record"])
                aimed += aim_picture_row(
                    staging_name, look, screen_window, width, height,
                    row_items, row_places, project_folder=project_folder,
                    measure=prepared.subject_measure,
                    draw_gain=draw_gain, timeline=staging,
                    resolve_project=project)
            build_record["motion_coverage"] = verify_motion_screen_coverage(
                staging_name, track_plan, prepared.placements_list,
                prepared.video_row_by_angle, staging, motion or [], width,
                height, screen_window, draw_gain,
                resolve_project=project)
        else:
            build_record["motion_coverage"] = []

        # Recorded transform holds, then the edit-ledger replay (safe rows
        # only - `edit_program` refused above).
        held = apply_transform_overrides(
            staging_name, track_plan, prepared.video_row_by_angle,
            prepared.placements_list, staging, transcript, project_folder,
            width, height, look=look,
            screen_window=screen_window,
            draw_gain=draw_gain,
            report_sibling_stale=not single_reel_scope,
            resolve_project=project)
        if held:
            print(f"  {staging_name}: {held} recorded transform hold(s) in "
                  f"force", file=_sys.stderr)
        if any(row.get("op") in (*_ledger.REPLAYED_OPS, *_ledger.CARRIER_OPS)
               for row in (prepared.ledger_rows or ())):
            _video_places = [
                place for place in prepared.placements_list
                if getattr(place["clip"], "track_type", "video") == "video"]
            _position = {id(place): index
                         for index, place in enumerate(_video_places)}
            _span_items: dict = {}
            for _aroll_row in track_plan.aroll_rows():
                _row_items = (staging.GetItemListInTrack(
                    "video", _aroll_row.index) or [])
                _row_places = [
                    place for place in prepared.placements_list
                    if getattr(place["clip"], "track_type", "video") == "video"
                    and prepared.video_row_by_angle.get(
                        _angle_key(place["clip"])) == _aroll_row.index]
                _row_places.sort(key=lambda place: place["snapped_record"])
                for _index, _item in enumerate(_row_items):
                    if _index >= len(_row_places):
                        break
                    _span_items[_position.get(
                        id(_row_places[_index]), -1)] = _item
            _replay = _ledger.replay_on_timeline(
                staging_name, prepared.ledger_rows, _video_places,
                transcript, staging,
                item_for_span=_span_items.get, reel_name=staging_name,
                project=project)
            build_record["edit_ledger"] = {
                "applied": _replay["applied"],
                "planned": _replay["planned"],
                "unreplayable": _replay["unreplayable"],
            }

        if prepared.freeze_tail is not None:
            from library.tools.reel_build import _inherit_freeze_treatment
            build_record["freeze_tail"] = _inherit_freeze_treatment(
                staging_name, staging, track_plan, prepared.video_row_by_angle,
                prepared.freeze_tail, project_folder)
            build_record["freeze_placement"] = _check_native_hold(
                staging, prepared.freeze_tail, prepared.video_row_by_angle)

        # The TV frame is placed, never re-placed: verify its cover zoom
        # instead, and refuse rather than ship a stale one.
        _check_frame_zoom(staging, track_plan, prepared.frame_segments, look,
                          fps, width, height, draw_gain, staging_name)

        # The link pass rebuilds the duplicate's groups (a copy is not
        # trusted to have carried them); on identical items it reforms the
        # identical groups.
        from library.tools.reel_build import link_reel_groups
        link_record = link_reel_groups(
            staging, track_plan, offset_links=prepared.offset_links)
        build_record["link_groups"] = link_record["link_groups"]
        build_record["caption_links"] = link_record["caption_links"]
        build_record["link_warnings"] = link_record["warnings"]

        # Sweep records for EVERY tight overlay on the staging (patched or
        # not - the sweep judges what the staging holds, and the model
        # writer reads the same records as what was placed).
        # Sweep records for every tight overlay on the staging: the
        # placer already recorded the patched ones, so the reconstruction
        # below covers only the untouched remainder (matched by row and
        # record frame, the same identity the deferred sweep reads by).
        patched_keys = {(record.get("track_index"),
                         record.get("record_frame"))
                        for record in patch_out["sweep_records"]}
        sweep_records = _reconstruct_sweep_records(
            timeline=staging, project=project, track_plan=track_plan,
            kinds=[("post header", [int(row.index) for row in track_plan.rows_for_role(POST_HEADER)],
                    prepared.post_header.segments, _helper_span),
                   ("caption", [track_plan.caption_row().index]
                    if track_plan.caption_row() is not None else [],
                    prepared.subtitle_segments, _caption_span),
                   ("explainer",
                    [int(row.index)
                     for row in track_plan.rows_for_role(EXPLAINER)],
                    explainer_segments, _helper_span),
                   ("semantic visual",
                    [int(row.index)
                     for row in track_plan.rows_for_role(SEMANTIC)],
                    semantic_segments, _helper_span),
                   ("speaker lower third",
                    [int(row.index)
                     for row in track_plan.rows_for_role(MOTION_GRAPHICS)],
                    lower_third_segments, _helper_span),
                   ("TV frame",
                    [int(row.index)
                     for row in track_plan.rows_for_role(FRAME)],
                    prepared.frame_segments, _helper_span)],
            width=width, height=height, draw_gain=draw_gain, fps=fps,
            skip_keys=frozenset(patched_keys))
        sweep_records.extend(patch_out["sweep_records"])

        from library.tools.overlay_verify import read_reel_overlays
        from library.tools.timeline_layout import A_ROLL, B_ROLL
        if sweep_records:
            print(f"── Overlay sweep ({len(sweep_records)} tight "
                  f"overlay(s)) ──", file=_sys.stderr)
            sweep_reads = read_reel_overlays(
                staging, sweep_records, resolve_project=project,
                picture_tracks=[row.index for row in track_plan.video_tracks
                                if row.role in (A_ROLL, B_ROLL)])
            sweep_judgement = {"intent": overlay_intent,
                               "full_wh": (width, height),
                               "draw_gain": draw_gain,
                               "project_folder": project_folder, "fps": fps}
            build_record["overlay_sweep"] = None
            build_record["overlay_sweep_reads"] = (sweep_reads,
                                                   sweep_judgement)
        else:
            build_record["overlay_sweep"] = {
                "passed": True, "checked": 0,
                "detail": "no tight overlays placed on this reel",
            }

        # Suppressions: complete (pure), intent: what the patch touched.
        segments_by_kind = [prepared.subtitle_segments, explainer_segments,
                            semantic_segments, lower_third_segments,
                            prepared.frame_segments]
        suppressed, unmatched_dnd = _suppression_ids(
            segments_by_kind, suppressions, staging_name)
        build_record["suppressed_overlays"] = suppressed
        build_record["unmatched_do_not_draw"] = unmatched_dnd
        if overlay_intent:
            from library.tools.overlay_intent import (
                report_unmatched as report_unmatched_intent,
            )
            build_record["unmatched_overlay_intent"] = report_unmatched_intent(
                overlay_intent, patch_out["seen_ids"],
                source=f"overlay_intent.json ({staging_name})",
                seen_labels=patch_out["seen_labels"])
            build_record["applied_overlay_intent"] = sorted(
                set(patch_out["applied_intent"]))
        else:
            build_record["applied_overlay_intent"] = []
        build_record["post_header"] = prepared.post_header.as_dict()

        import dataclasses as _dataclasses

        from library.tools.timeline_layout import TRANSITIONS as _TRANSITIONS
        _transitions_row = track_plan.row_for_role(_TRANSITIONS)
        _transitions_index = (_transitions_row.index
                              if _transitions_row is not None else None)
        build_record["transition_placements"] = [
            (placement.as_dict() if hasattr(placement, "as_dict")
             else dict(placement))
            for placement in [
                _dataclasses.replace(placement, track_index=_transitions_index)
                if (getattr(placement, "track_index", None)
                        != _transitions_index
                        and _transitions_index is not None)
                else placement
                for placement in (overlay_placements or [])]]

        from library.tools.reel_build import serializable_placements
        build_record["fps"] = float(fps)
        build_record["placements"] = serializable_placements(
            prepared.placements_list, fps=float(fps))
        build_record["overlay_placements"] = [dict(row)
                                              for row in sweep_records]
        build_record["placement_profile"] = {
            "incremental_patch_s": round(_time.perf_counter()
                                         - patch_started, 4),
        }
        # WHAT THE CHANGE TOUCHED, explicitly: the items it replaced and
        # the intervals it moved, and what it reused.  A scope nobody can
        # read is a scope nobody can measure, and the point of the scoped
        # route is that the work is proportional to the change.
        build_record["change_scope"] = _scope_of(
            plan, prepared, track_plan, fps).as_dict()
        # A retime in the plan is a CLOCK change, and the scope of one
        # is the interval it moved.  It is recorded even when the
        # scoped path refused it, because a refusal that names the
        # interval is the difference between "this reel must be built
        # again" and "these frames moved".
        _retime = retime_scope(prepared.placements_list, fps)
        if _retime is not None:
            build_record["change_scope"]["retime"] = _retime

        final_inventory = _RowInventory(staging)
        final_plan = decide_incremental(
            timeline=final_inventory, track_plan=track_plan, prepared=prepared,
            placements_list=prepared.placements_list,
            video_row_by_angle=prepared.video_row_by_angle,
            speech_row_by_angle=prepared.speech_row_by_angle, fps=fps,
            cards=prepared.cards, transition_placements=overlay_placements,
            subtitle_segments=prepared.subtitle_segments, frame_segments=prepared.frame_segments,
            post_header=prepared.post_header, freeze_tail=prepared.freeze_tail,
            explainer_segments=explainer_segments, semantic_segments=semantic_segments,
            lower_third_segments=lower_third_segments, ledger_rows=prepared.ledger_rows)
        if final_plan.touches_overlays:
            raise IncrementalBroken(
                "incremental placement inventory still differs from the plan")
        build_record["placement_check"] = {"passed": True, "repair_attempted": False}
        # Incremental and manual edits publish compatible transaction
        # records. This is candidate verification only; the build owner
        # promotes the candidate through its existing guarded path.
        from library.tools.composed_edit import operation_description
        requested_ops = []
        for diff in plan.diffs:
            if diff.removed or diff.replace:
                requested_ops.append(operation_description(
                    {"op": "overlay.remove"}))
            if diff.added or diff.replace:
                requested_ops.append(operation_description(
                    {"op": "overlay.add"}))
        candidate_lifecycle.operations = requested_ops
        candidate_lifecycle.mark_applied()
        candidate_lifecycle.mark_verified(
            modified=True, preserved=True,
            evidence={"placement_check": build_record["placement_check"],
                      "final_overlay_diff_empty": True,
                      "preserved_inventories": [
                          "footage", "cards", "transitions", "captions",
                          "frame", "freeze tail"]})
        build_record["transaction"] = {
            **candidate_lifecycle.as_dict(),
            "backend": "reel_incremental",
            "status": "verified",
            "base_timeline": final_name,
            "verification": {
                "status": "verified",
                "preserved_items": "placement and overlay inventories",
                "placement_check": build_record["placement_check"],
            },
            "promotion": {"status": "awaiting_promotion"},
        }
        # VC-12: the routing decision is attached through the shared
        # orchestration (same dict the router returns, same key).
        from library.tools import transaction_orchestration as _orch

        build_record.update(_orch.attach_routing(
            {}, execution_routing, key="execution_routing"))

        from library.tools.reel_graphics import assert_timeline_media_online
        from library.tools.reel_otio_placement import RecordingTimeline
        if resolve_app is not None and not isinstance(staging,
                                                      RecordingTimeline):
            try:
                assert_timeline_media_online(final_inventory)
            except Exception as exc:
                from library.tools.reel_build import ReelBuildError
                raise ReelBuildError(f"{staging_name}: {exc}") from exc
        return build_record
    except IncrementalRefused:
        from library.tools.resolve_lock import assert_current_timeline
        assert_current_timeline(project, final_timeline)
        if not project.GetMediaPool().DeleteTimelines([staging]):
            raise ReelBuildError("incremental staging cleanup failed")
        raise


def _caption_span(segment, fps: float) -> tuple:
    return caption_span_frames(segment, fps)


def _helper_span(segment, fps: float) -> tuple:
    return helper_span_frames(segment, fps)


def _check_frame_zoom(staging, track_plan, frame_segments, look,
                      fps: float, width: int, height: int,
                      draw_gain: float, staging_name: str) -> None:
    """The frame is placed, never re-placed: prove its cover zoom."""
    if not frame_segments:
        return
    if look is None:
        return
    from library.tools.reel_build import _held_transform
    from library.tools.reel_look import frame_properties
    from library.tools.timeline_layout import FRAME

    rows = [int(row.index) for row in track_plan.rows_for_role(FRAME)]
    if not rows:
        raise IncrementalRefused(
            "frame segments are planned with no frame row - refusing")
    want = frame_properties(look, width, height, draw_gain=draw_gain)
    for segment in frame_segments:
        row = overlay_segment_row(segment, rows)
        span = helper_span_frames(segment, fps)
        try:
            items = (staging.GetItemListInTrack("video", row) or [])
        except Exception as exc:
            raise IncrementalBroken(
                f"frame row {row} would not list ({exc}) - refusing"
            ) from exc
        found = None
        for item in items:
            item_span = _live_span(item)
            if (item_span is not None
                    and (int(item_span[0]), int(item_span[1]))
                    == (int(span[0]), int(span[1]))):
                found = item
                break
        if found is None:
            raise IncrementalBroken(
                "a frame segment has no item after the patch - refusing")
        held = _held_transform(found, staging) or {}
        for key, value in want.items():
            try:
                same = abs(float(held.get(key)) - float(value)) < 1e-6
            except (TypeError, ValueError):
                same = held.get(key) == value
            if not same:
                raise IncrementalConflict(
                    f"the TV frame's {key} reads {held.get(key)!r}, not "
                    f"the planned {value!r} - refusing; only the full "
                    f"build re-places the frame")
