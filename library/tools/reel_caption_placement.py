"""Place one reel's caption cards, exactly as the build places them.

A caption card is placed by the build's caption loop and, on the
scoped path, by the incremental patch.  Two places that place it
differently are two answers to one question, so the placement lives
HERE and both callers reach for it.

What is shared, and why each part matters:

- the RECORD SPAN is rounded per edge - ``[round(start), round(end))``
  - never ``round(start) + round(duration)``.  Abutting blocks share one
  edge in seconds and must share it in frames, or the spans overlap by a
  frame and Resolve trims one off the later item (reel 07 block 23,
  2026-09-08: planned 34, placed 33, the lone F2 of the rebuild).
- the SOURCE RANGE is the same duration counted from the content
  start, inside the render handles 4.05 leaves either side.
- the TIGHT BOX rides the placement its own record computed: the
  placer SETS it and then READS BACK what Resolve holds.  A sequence
  shares the mov's frame numbering, so the handle trim is the same
  arithmetic.
- the DRAW INTENT arms the pixel half: a declared pin first (the
  captain's place wins over the row), else the declared caption row
  for this reel.

This module is the ONE owner of that arithmetic.  ``reel_build`` calls
it for the full build; ``reel_incremental`` calls it for the scoped
patch.  A reader comparing the two paths compares the same function.
"""

from __future__ import annotations

import os
import sys
from typing import List, Mapping, Optional, Sequence, Tuple


def caption_span(segment: Mapping, fps: float) -> Tuple[int, int]:
    """The record span one caption segment occupies, per-edge rounded.

    ``(start, end)`` with the end EXCLUSIVE.
    """
    from library.tools.frame_utils import span_frames

    return span_frames(segment["timeline_start"], segment["timeline_end"], fps)


def caption_source_range(segment: Mapping, fps: float) -> Tuple[int, int]:
    """The source frames one caption segment reads: ``(in, out)``.

    The source window is the record span's own duration, counted from
    the content start.  The out frame is EXCLUSIVE, matching the
    convention the shared overlay placer uses.
    """
    start, end = caption_span(segment, fps)
    source_in = int(segment["source_in_frame"])
    return source_in, source_in + max(end - start, 1)


def caption_geometry(segment: Mapping, width: int, height: int) -> Optional[dict]:
    """The geometry descriptor one caption's tight box implies, or None.

    A caption with no tight canvas has no descriptor: it is a
    full-canvas segment, which needs no transform.
    """
    from library.tools.overlay_draw_intent import segment_canvas

    canvas = segment_canvas(segment)
    if canvas is None:
        return None
    from library.tools.layout_model import GeometryDescriptor

    box = (segment.get("tight_box") or {}).get("absolute_canvas_box")
    return GeometryDescriptor(
        frame=(int(width), int(height)),
        box=tuple(map(float, box or (0, 0, width, height))),
        canvas=tuple(map(float, canvas)),
    ).to_dict()


def place_caption_cards(
        *, pool, project, timeline, name: str, fps: float,
        width: int, height: int, segments: Sequence[Mapping],
        track_plan, project_folder: str = "",
        overlay_intent: Optional[Mapping] = None,
        applied_intent_keys: Optional[List[str]] = None,
        seen_intent_ids: Optional[List[str]] = None,
        draw_gain: float = 1.0, pool_index=None,
        suppressions: Sequence[Mapping] = (),
        suppressed_ids: Optional[List[str]] = None,
        sweep_records: Optional[List[dict]] = None,
        resolve_app=None) -> dict:
    """Place caption segments onto the caption row, and say what it did.

    Returns the placement outcome for the caller:

    ``{"placed": [...], "held_back": [...], "sweep_records": [...],
    "applied_intent": [...], "seen_intent_ids": [...]}``

    A segment that Resolve will not import or will not place is a
    REFUSAL raised here, named by segment id - the same refusal the
    build raises, so a scoped patch and a full build fail the same way
    on the same defect.
    """
    from library.tools import do_not_draw as _dnd
    from library.tools.overlay_draw_intent import (
        draw_intent_for_segment as _draw_intent_for_segment,
        segment_canvas as _segment_canvas,
    )
    from library.tools.overlay_placement import (
        place_overlay_segment,
        sequence_frame_paths,
    )
    from library.tools.reel_build import (
        ReelBuildError,
        _assert_placing,
        _held_transform,
        _native_overlay_position,
        import_pool_item,
        import_pool_sequence,
        overlay_import_bin,
    )
    from library.tools.reel_placed_assets import assert_placeable

    applied_intent = applied_intent_keys if applied_intent_keys is not None else []
    seen_ids = seen_intent_ids if seen_intent_ids is not None else []
    held_back = suppressed_ids if suppressed_ids is not None else []
    sweep_out = sweep_records if sweep_records is not None else []

    caption_row = track_plan.caption_row()
    placed_ids: List[str] = []
    for segment in (segments or []):
        held, why = _dnd.should_suppress(suppressions, name, segment or {})
        if held:
            print(f"  {name}: {why}", file=sys.stderr)
            held_back.append(segment.get("segment_id"))
            continue
        if why:
            print(f"  {name}: {why}", file=sys.stderr)

        frames_info = segment.get("frames") or {}
        frame_dir = (frames_info.get("dir", "")
                     if segment.get("container") == "frames" else "")
        # Captions render durable already (Area.SUBTITLE_SEGMENTS); the
        # assert pins it, so a future caller that reaches into scratch
        # fails here rather than on the captain's timeline.
        assert_placeable(segment.get("overlay_path") or "", project_folder)
        if frame_dir:
            assert_placeable(frame_dir, project_folder)
        if frame_dir:
            paths = sequence_frame_paths(frame_dir)
            found = import_pool_sequence(
                pool, paths, frame_dir, project_folder,
                dest=overlay_import_bin(project_folder, name,
                                        paths[0] if paths else ""),
                pool_index=pool_index)
            items = [found] if found is not None else []
        else:
            found = import_pool_item(
                pool, segment["overlay_path"],
                overlay_import_bin(project_folder, name,
                                   segment["overlay_path"]),
                pool_index=pool_index)
            items = [found] if found is not None else []
        if not items:
            failed_path = segment.get("overlay_path") or frame_dir
            raise ReelBuildError(
                f"{name}: Resolve would not import rendered caption "
                f"{failed_path!r}; refusing to omit a planned caption "
                f"segment")

        _assert_placing(project, timeline)
        record_start, record_end = caption_span(segment, fps)
        content_frames = max(record_end - record_start, 1)
        source_in, source_out = caption_source_range(segment, fps)
        seen_ids.append(segment.get("segment_id"))

        canvas = _segment_canvas(segment)
        caption_box = (segment.get("tight_box") or {}).get(
            "absolute_canvas_box")
        geometry = caption_geometry(segment, width, height)
        placed, note = place_overlay_segment(
            pool, timeline, items[0],
            track_index=caption_row.index,
            record_frame=record_start,
            source_in_frame=source_in,
            source_out_frame=source_out,
            placement=(segment.get("tight_box") or {}).get("placement"),
            label=segment.get("segment_id", "caption"),
            kind="caption",
            segment_id=segment.get("segment_id"),
            intent=overlay_intent,
            canvas=canvas,
            frame=(width, height),
            intent_matched=applied_intent,
            draw_gain=draw_gain,
            resolve_project=project,
            absolute_box=caption_box,
            geometry_descriptor=geometry,
            project_folder=project_folder,
            timeline_name=name,
            draw_intent=_draw_intent_for_segment(
                segment, kind="caption",
                segment_id=segment.get("segment_id"),
                placement_label=None,
                intent=overlay_intent, frame_wh=(width, height),
                project_folder=project_folder, reel_name=name,
                draw_gain=draw_gain))
        if not placed:
            raise ReelBuildError(
                f"{name}: Resolve would not place caption segment "
                f"{segment.get('segment_id')!r} at frame {record_start}: "
                f"{note or 'placement returned no item'}")
        if note:
            print(f"  caption {segment.get('segment_id')}: {note}",
                  file=sys.stderr)
        placed_ids.append(segment.get("segment_id"))
        if canvas is not None:
            # One post-build sweep record per tight caption: the values
            # half (`overlay_verify.sweep_reel_overlays`) reads it back
            # off the timeline after the build and judges it against
            # intent. Full-canvas captions need no transform and leave
            # no record.
            caption_item = None
            try:
                for candidate in (timeline.GetItemListInTrack(
                        "video", caption_row.index) or []):
                    if int(candidate.GetStart()) == record_start:
                        caption_item = candidate
                        break
            except Exception:  # noqa: BLE001 - placement remains recorded
                caption_item = None
            held_transform = (_held_transform(
                caption_item, timeline, project)
                if caption_item is not None else None)
            sweep_out.append({
                "label": segment.get("segment_id", "caption"),
                "kind": "caption",
                "segment_id": segment.get("segment_id"),
                # Captions carry no placing label (the label tier is
                # for reel graphics); the key stays so every sweep
                # record has one schema.
                "placement_label": None,
                "track_index": caption_row.index,
                "record_frame": record_start,
                "canvas_wh": canvas,
                "placement": (segment.get("tight_box") or {}).get(
                    "placement"),
                "overlay_path": segment.get("overlay_path") or "",
                "frames_dir": frame_dir,
                "timeline_start_frame": record_start,
                "duration_frames": content_frames,
                "absolute_box": (
                    caption_box
                    or [0, 0, int(width), int(height)]),
                "geometry": geometry,
                "source_start_frame": source_in,
                "transform": (dict(held_transform)
                              if isinstance(held_transform, Mapping) else None),
                "native_position": _native_overlay_position(
                    held_transform, canvas, (width, height), draw_gain),
            })
    return {"placed": placed_ids, "held_back": list(held_back),
            "sweep_records": sweep_out,
            "applied_intent": applied_intent,
            "seen_intent_ids": seen_ids}


__all__ = [
    "caption_geometry",
    "caption_source_range",
    "caption_span",
    "place_caption_cards",
]
