"""A small plan change touches only what changed, in seconds.

A reel rebuild used to re-place the whole reel into a fresh staging
timeline for ANY change - a lower-third text fix re-placed sixty
footage clips to change one graphic. This module diffs the new plan
against what is already on the reel timeline and patches only the
changed items, re-rendering only changed graphics (render steps
content-address their outputs, so unchanged segments are byte-identical
and never re-rendered). The patched staging flows through the SAME
verify and promote gates as a full rebuild, and anything the diff
cannot prove safe refuses with `IncrementalRefused` - the caller falls
back to the full rebuild, which stays the default.

What v1 patches
----------------
Only the ADDITIVE overlay kinds whose placer is the shared
`place_overlay_segments` helper - explainer, semantic visuals and
speaker lower thirds. Changed bytes at an identical (row, record span)
are replaced in place; added spans are placed and removed spans are
deleted, through the same placer. Everything else must be
inventory-identical between the duplicate staging and the new plan,
else refuse:

- footage (picture + speech, span + source per row),
- cards, transitions, captions, TV frame (span + source; captions join
  link groups and the frame placer is not idempotent, so neither is
  patched in place),
- post header (refused when the new plan carries segments),
- freeze tail (refused when declared - its trace is placement-time
  evidence a patch cannot reproduce),
- edit-ledger `edit_program` rows (the algebra replay is not proven
  idempotent; every other row kind is re-applied by absolute setters,
  so a changed one converges rather than duplicating).

What the patch re-runs
----------------------
The passes that SET absolute values are re-run over the duplicate, so
a changed look, grade, retime, transform override or ledger row
converges to the new plan instead of going stale: ledger retimes, the
declared CDL/power-grade, the punch-in aim, recorded transform
overrides, the edit-ledger replay (minus `edit_program`), the motion
coverage proof and the link pass (the duplicate's link groups are
rebuilt - a duplicated timeline is not trusted to have carried them).
The frame pass is NOT re-run (it places); frame items are verified
identical and their cover zoom is compared, never re-placed.

The end state is proved, not asserted: the equivalence test
compares a patched staging against a fresh full rebuild of the same
plan, item by item (`tests/unit/reels/test_reel_incremental.py`).

`tests/unit/reels/test_footage_batch.py` covers the batch layer below.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence


class IncrementalRefused(Exception):
    """The diff is not applicable - fall back to the full rebuild."""


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
        return (int(item.GetStart()), int(item.GetEnd()))
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
            raise IncrementalRefused(
                f"{media_type}{index} would not list ({exc}) - refusing "
                f"to diff against an unreadable row") from exc
        rows = []
        for item in items:
            span = _live_span(item)
            if span is None:
                raise IncrementalRefused(
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
            duration = int(round(
                (place["source_out"] - place["source_in"]) * fps))
        start = int(place["snapped_record"])
        out.setdefault((track_type, int(row)), []).append(
            (start, start + duration, _norm_source(
                getattr(clip, "source_file", "") or "")))
    return {row: sorted(rows) for row, rows in out.items()}


def check_rows_identical(expected: Mapping[tuple, list],
                         live: Mapping[tuple, list], what: str) -> None:
    """Refuse unless the plan's inventory is exactly what is there."""
    if set(expected) != set(live):
        raise IncrementalRefused(
            f"{what} rows differ (plan {sorted(expected)} vs timeline "
            f"{sorted(live)}) - refusing")
    for row in expected:
        if list(expected[row]) != list(live[row]):
            raise IncrementalRefused(
                f"{what} on row {row} differs from the plan - refusing; "
                f"a full rebuild places it")


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
                      segments: Sequence[Mapping], fps: float) -> OverlayDiff:
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
        raise IncrementalRefused(
            f"{kind} rows would not list ({exc}) - refusing") from exc
    live_by_span: Dict[tuple, list] = {}
    for index, items in live_by_row.items():
        for item in items:
            span = _live_span(item)
            if span is None:
                raise IncrementalRefused(
                    f"{kind} holds an item whose span cannot be read - "
                    f"refusing")
            live_by_span.setdefault((int(index), span[0], span[1]),
                                    []).append(item)
    diff = OverlayDiff(kind=kind, rows=[int(r) for r in rows])
    claimed: set = set()
    for segment in segments or ():
        if not isinstance(segment, Mapping):
            raise IncrementalRefused(
                f"{kind} carries an unreadable segment - refusing")
        row = overlay_segment_row(segment, rows)
        span = helper_span_frames(segment, fps)
        key = (row, span[0], span[1])
        path = segment.get("overlay_path") or ""
        if not path or not os.path.isfile(path):
            raise IncrementalRefused(
                f"{kind} segment {segment.get('segment_id')!r} names "
                f"{path!r}, which is not a file - refusing; the full "
                f"build raises naming it")
        found = live_by_span.get(key, [])
        if not found:
            diff.added.append(dict(segment))
            continue
        if len(found) > 1:
            raise IncrementalRefused(
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
    from library.tools import edit_ledger as _ledger
    from library.tools.reel_build import _angle_key
    from library.tools.timeline_layout import (
        EXPLAINER, FRAME, MOTION_GRAPHICS, SEMANTIC, TRANSITIONS,
    )

    if freeze_tail is not None:
        raise IncrementalRefused(
            "a native ending hold is declared - its trace is "
            "placement-time evidence a patch cannot reproduce; refusing")
    if post_header is not None and getattr(post_header, "segments", None):
        raise IncrementalRefused(
            "a post header is declared - refusing; only the full build "
            "places it")
    if any((row.get("op") in (*_ledger.REPLAYED_OPS,
                              *_ledger.CARRIER_OPS, "edit_program"))
           for row in (ledger_rows or ())):
        raise IncrementalRefused(
            "edit-ledger rows replay onto the footage through the algebra "
            "- refusing; only the full build replays them")

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
                raise IncrementalRefused(
                    f"{media_type} rows would not count ({exc}) - "
                    f"refusing") from exc
            for index in range(1, count + 1):
                try:
                    live_rows[(media_type, index)] = (
                        timeline.GetTrackName(media_type, index) or "")
                except Exception as exc:
                    raise IncrementalRefused(
                        f"{media_type}{index} would not name itself "
                        f"({exc}) - refusing") from exc
    except IncrementalRefused:
        raise
    if planned_rows != live_rows:
        raise IncrementalRefused(
            f"timeline rows differ from the plan - refusing; rows are "
            f"the plan's own packing")

    # Footage: span + source per a-roll/speech row.
    aroll = [row.index for row in track_plan.aroll_rows()]
    speech = [row.index for row in track_plan.speech_rows()]
    check_rows_identical(
        footage_expected_rows(placements_list, video_row_by_angle,
                              speech_row_by_angle, fps, _angle_key),
        {**live_row_inventory(timeline, aroll, "video"),
         **live_row_inventory(timeline, speech, "audio")},
        "footage")

    # Cards, transitions, captions, frame: span + source, identical.
    _check_cards_identical(timeline, track_plan, cards)
    _check_transitions_identical(
        timeline, track_plan, transition_placements, TRANSITIONS)
    _check_captions_identical(
        timeline, track_plan, subtitle_segments, fps)
    _check_frame_identical(
        timeline, track_plan, frame_segments, fps, FRAME)

    plan = IncrementalPlan()
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
                                 fps)
        if diff.replace or diff.added or diff.removed:
            plan.touches_overlays = True
        plan.diffs.append(diff)
    return plan


def _card_span_frames(card, fps: float) -> tuple:
    start = int(getattr(card, "reel_start_frame", 0))
    frames = int(getattr(card, "source_frames", 0)
                 or getattr(card, "duration_frames", 0))
    return (start, start + frames)


def _check_cards_identical(timeline, track_plan, cards) -> None:
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
    if role is not None:
        for row in track_plan.rows_for_role(role):
            live.update(live_row_inventory(timeline, [int(row.index)],
                                           "video"))
    check_rows_identical(
        {row: sorted(rows) for row, rows in expected.items()}, live,
        "cards")


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


def _check_captions_identical(timeline, track_plan, segments,
                              fps: float) -> None:
    row = track_plan.caption_row()
    expected: Dict[tuple, list] = {}
    if segments:
        if row is None:
            raise IncrementalRefused(
                "captions are planned with no caption row - refusing")
        rows = []
        for segment in segments:
            span = caption_span_frames(segment, fps)
            path = segment.get("overlay_path") or ""
            frames = ((segment.get("frames") or {}).get("dir", "")
                      if segment.get("container") == "frames" else "")
            rows.append((span[0], span[1],
                         _norm_source(frames or path)))
        expected[("video", int(row.index))] = sorted(rows)
    live: Dict[tuple, list] = {}
    if row is not None:
        live.update(live_row_inventory(timeline, [int(row.index)],
                                       "video"))
    check_rows_identical(expected, live, "captions")


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
    """
    from library.tools import edit_patch
    from library.tools.reel_build import (
        PoolIndex, _assert_placing, place_overlay_segments,
    )

    doomed: list = []
    for diff in diffs:
        doomed.extend(diff.removed)
        doomed.extend(live for live, _segment in diff.replace)
    if doomed:
        _assert_placing(project, timeline)
        delete_ops = []
        for item in doomed:
            try:
                uid = item.GetUniqueId()
            except Exception as exc:
                raise IncrementalRefused(
                    f"an overlay item to remove would not report its "
                    f"id ({exc}) - refusing") from exc
            delete_ops.append({"op": "clip.delete", "unique_id": uid})
        import hashlib as _hashlib

        from library.tools.stable_json import dumps_stable
        material = dumps_stable(sorted(
            op["unique_id"] for op in delete_ops))
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline,
            # "Change one built reel's own timeline in place, instead
            # of rebuilding it" - the registered capability for
            # exactly this act (`reel.build` declares only
            # `clip.insert` and would refuse the deletes).
            capability="reel.touchup",
            operations=delete_ops,
            conflict_domains=("timeline_structure",),
            idempotency_key=(
                f"reel-incremental:{staging_name}:overlay-delete:"
                f"{_hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"),
            project_folder=project_folder,
            model_revision=model_revision)
        if receipt["status"] != "committed":
            raise IncrementalRefused(
                f"the overlay removals did not verify ({receipt}) - "
                f"falling back to the full rebuild")

    pool_index = PoolIndex(pool)
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
                raise IncrementalRefused(
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
                raise IncrementalRefused(
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

    The caller falls back to the full `build_reel_timeline` on any
    `IncrementalRefused`. The returned dict carries the same keys the
    rebuild loop reads off a full build's record, so verify and promote
    run unchanged. Must run under the same exclusive Resolve hold the
    full build takes (the duplicate, the deletes and the re-applied
    setters are all writes).
    """
    import sys as _sys
    import time as _time

    from library.tools.reel_build import ReelBuildError, _assert_placing
    from library.tools.timeline_layout import (
        EXPLAINER, FRAME, MOTION_GRAPHICS, SEMANTIC,
    )

    if placement_mode != "append":
        raise IncrementalRefused(
            f"placement mode is {placement_mode!r} - the incremental "
            f"path patches a live timeline, so only append applies")

    patch_started = _time.perf_counter()
    try:
        count = project.GetTimelineCount() or 0
    except Exception as exc:
        raise IncrementalRefused(
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
    try:
        staging = final_timeline.DuplicateTimeline(staging_name)
    except Exception as exc:
        raise IncrementalRefused(
            f"the final could not be duplicated ({exc}) - falling back "
            f"to the full rebuild") from exc
    if staging is None or staging.GetName() != staging_name:
        raise IncrementalRefused(
            "the staging copy did not land under its staging name - "
            "falling back to the full rebuild")
    _assert_placing(project, staging)

    # No drift: the live final must still be the build the record
    # describes, or the diff has no base.
    from library.tools import reel_rebuild_need as _need
    try:
        live_carried = _need.carried_digest_live(project, final_timeline)
    except Exception as exc:
        raise IncrementalRefused(
            f"the final timeline could not be read ({exc}) - refusing"
        ) from exc
    if not recorded_carried or live_carried != recorded_carried:
        raise IncrementalRefused(
            "the final drifted since its build (or its record is gone) "
            "- only the full rebuild restores it")

    track_plan = prepared.track_plan
    plan = decide_incremental(        timeline=staging, track_plan=track_plan, prepared=prepared,
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

    from library.tools import project_model_store as _model_store
    from pathlib import Path as _Path
    try:
        _model_revision, _ = _model_store.load_project_model(
            _Path(project_folder))
    except FileNotFoundError:
        _model_revision = None

    patch_out = apply_overlay_diffs(
        project=project, pool=project.GetMediaPool(), timeline=staging,
        staging_name=staging_name, fps=fps, width=width, height=height,
        track_plan=track_plan, diffs=plan.diffs,
        suppressions=suppressions, overlay_intent=overlay_intent,
        draw_gain=draw_gain, project_folder=project_folder,
        resolve_app=resolve_app, model_revision=_model_revision)

    from library.tools import edit_ledger as _ledger
    from library.tools.reel_build import (
        _angle_key, _apply_ledger_retimes, apply_transform_overrides,
    )
    from library.tools.reel_look import apply_grade

    build_record = dict(prepared.build_record)
    build_record["stream_enforcement"] = {
        "checked": 0, "deleted": [], "unverified": []}

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
        from library.tools import reel_look as _look
        from library.tools.reel_build import aim_picture_row
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
        build_record["motion_coverage"] = _look.verify_motion_screen_coverage(
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
        kinds=[("caption", [track_plan.caption_row().index]
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

    from library.tools.reel_graphics import assert_timeline_media_online
    from library.tools.reel_otio_placement import RecordingTimeline
    if resolve_app is not None and not isinstance(staging,
                                                  RecordingTimeline):
        try:
            assert_timeline_media_online(staging)
        except Exception as exc:
            from library.tools.reel_build import ReelBuildError
            raise ReelBuildError(f"{staging_name}: {exc}") from exc
    return build_record


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
            raise IncrementalRefused(
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
            raise IncrementalRefused(
                "a frame segment has no item after the patch - refusing")
        held = _held_transform(found, staging) or {}
        for key, value in want.items():
            try:
                same = abs(float(held.get(key)) - float(value)) < 1e-6
            except (TypeError, ValueError):
                same = held.get(key) == value
            if not same:
                raise IncrementalRefused(
                    f"the TV frame's {key} reads {held.get(key)!r}, not "
                    f"the planned {value!r} - refusing; only the full "
                    f"build re-places the frame")
