"""Dependency-aware planning for structural timeline edits.

This planner is deliberately read-only.  It maps the complete pre-edit
timeline onto the proposed placement, asks the existing cascade graph which
components a timing change reaches, and reports when the component manifest
cannot prove localized reuse.  The staged composed-edit path remains the
writer and verifier.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence


def _row(track: Mapping) -> str:
    return f"{str(track['type']).upper()[0]}{int(track['index'])}"


def _stable_keys(tracks: Sequence[Mapping]) -> dict[tuple[str, int], str]:
    """Stable placement lineage, independent of Resolve's re-created id.

    Duplicate source windows are disambiguated by their occurrence order in
    the full timeline.  Incomplete or ambiguous source identity is omitted,
    which prevents a caller from claiming identity preservation.
    """
    clips = []
    for track in tracks:
        row = _row(track)
        for index, clip in enumerate(track.get("clips", ())):
            source = str(clip.get("media_pool_item_id") or
                         clip.get("source_file") or "")
            source_in = clip.get("source_in_frame")
            source_out = clip.get("source_out_frame")
            if not source or source_in is None or source_out is None:
                continue
            signature = (row, source, int(source_in), int(source_out))
            clips.append((int(clip["record_in"]), signature, row, index))
    counts: Counter = Counter()
    keys = {}
    for _position, signature, row, index in sorted(clips):
        occurrence = counts[signature]
        counts[signature] += 1
        keys[(row, index)] = (
            f"{signature[0]}:{signature[1]}:{signature[2]}:"
            f"{signature[3]}:{occurrence}")
    return keys


def plan_structural_patch(
    tracks: Sequence[Mapping], changes: Sequence[object],
    insertions: Sequence[object] = (), *,
    deletions: Sequence[Mapping] = (),
    component_manifest: Mapping | None = None,
) -> dict:
    """Describe the smallest structurally affected interval and dependencies.

    ``changes`` and ``insertions`` use ``composed_edit`` records.  All
    timeline rows are considered, including audio and overlays.  A missing,
    malformed, or incomplete component manifest marks reuse unproven; callers
    must then use the full reconstruction path.
    """
    from library.tools import cascade_graph

    before_keys = _stable_keys(tracks)
    changed_rows: set[str] = set()
    changed_frames: list[int] = []
    shifted_rows: set[str] = set()
    ripple_cut: int | None = None
    placements = []
    for change in changes:
        row = str(change.row)
        changed_rows.add(row)
        old = int(change.previous_record)
        new = int(change.record_frame)
        changed_frames.extend((old, new))
        if old != new or bool(change.played_length_changes):
            shifted_rows.add(row)
        if str(change.how).lower() == "shift":
            ripple_cut = (old if ripple_cut is None else min(ripple_cut, old))
        placements.append({
            "row": row,
            "item_index": int(change.item_index),
            "stable_id": before_keys.get((row, int(change.item_index))),
            "old_start": old,
            "new_start": new,
            "old_duration": int(change.previous_duration),
            "new_duration": int(change.duration),
        })
    for insertion in insertions:
        row = str(insertion.row)
        changed_rows.add(row)
        changed_frames.extend((int(insertion.record_frame),
                               int(insertion.record_frame) + int(insertion.duration)))
        shifted_rows.add(row)
        placements.append({"row": row, "stable_id": None,
                           "new_start": int(insertion.record_frame),
                           "new_duration": int(insertion.duration),
                           "identity": "new-item"})

    for deletion in deletions:
        row = str(deletion["row"]).upper()
        changed_rows.add(row)
        start_frame = int(deletion["record_frame"])
        duration = int(deletion["duration"])
        changed_frames.extend((start_frame, start_frame + duration))
        placements.append({
            "row": row,
            "stable_id": deletion.get("stable_id"),
            "old_start": start_frame,
            "old_duration": duration,
            "identity": "deleted-item",
        })

    if not placements:
        return {"status": "no_op", "affected_range": None,
                "changed_rows": [], "dependencies": [],
                "stable_identity_proven": True,
                "sync_proven": True, "reuse_proven": True,
                "full_reconstruction_required": False}

    stable = all(item.get("stable_id") for item in placements
                 if item.get("identity") != "new-item")
    moved_items = {(str(change.row), int(change.item_index))
                   for change in changes}
    unplanned_ripple_items = []
    if ripple_cut is not None:
        for track in tracks:
            row = _row(track)
            for index, clip in enumerate(track.get("clips", ())):
                if int(clip["record_in"]) >= ripple_cut and (
                        row, index) not in moved_items:
                    unplanned_ripple_items.append(
                        f"{row}[{index}]@{clip['record_in']}")
    end = max(int(clip["record_out"])
              for track in tracks for clip in track.get("clips", ()))
    start = min(changed_frames)
    affected_end = end if shifted_rows else max(
        int(item["new_start"]) + int(item["new_duration"])
        for item in placements)

    dependencies = []
    remapped_components: set[str] = set()
    for row in sorted(changed_rows):
        entity_id = f"structural:{row}:{start}"
        has_deletion = any(str(deletion["row"]).upper() == row
                           for deletion in deletions)
        kind = ("overlay_plan" if has_deletion and row.startswith("V")
                else "audio_decision" if row.startswith("A")
                else "placement_timing")
        dependency = cascade_graph.selective_invalidation(
            {"kind": kind, "entity_id": entity_id,
             "components": ["audio"] if kind == "audio_decision" else
             ["overlays"] if kind == "overlay_plan" else
             ["footage", "captions", "lower_thirds", "overlays"]})
        dependencies.append(dependency)
        remapped_components.update(dependency["remapped_components"])

    required = {"aroll", "captions", "motion_graphics", "audio",
                "fusion", "timeline"}
    components = ((component_manifest or {}).get("components")
                  if isinstance(component_manifest, Mapping) else None)
    manifest_complete = (isinstance(components, Mapping) and
                         required.issubset(components) and
                         all(isinstance(components[name], Mapping) and
                             components[name].get("digest") and
                             components[name].get("status") == "recorded"
                             for name in required))
    reasons = []
    if not stable:
        reasons.append("one or more existing items lack stable source lineage")
    if unplanned_ripple_items:
        reasons.append("ripple omitted downstream items: " +
                       ", ".join(unplanned_ripple_items))
    if not manifest_complete:
        reasons.append("component manifest does not prove every dependent component")
    sync_proven = not any(
        str(track["type"]).lower().startswith("a") and track.get("clips")
        for track in tracks)
    if not sync_proven:
        reasons.append(
            "audio item linkage is not represented in the timeline read, so sync cannot be proven")
    render_assets = []
    if manifest_complete:
        manifest_components = components
        if {"captions", "lower_thirds", "overlays"} & remapped_components:
            for name in ("captions", "motion_graphics"):
                component = manifest_components.get(name) or {}
                for asset in component.get("assets", ()):
                    if isinstance(asset, Mapping) and asset.get("sha256"):
                        render_assets.append({
                            "component": name,
                            "sha256": asset["sha256"],
                            "bytes": asset.get("bytes"),
                        })
    return {
        "status": "planned",
        "operation": "structural",
        "affected_range": {"start_frame": start, "end_frame": affected_end},
        "changed_rows": sorted(changed_rows),
        "shifted_rows": sorted(shifted_rows),
        "placements": placements,
        "dependencies": dependencies,
        "recomputed_source_to_timeline_mappings": placements,
        "remapped_components": sorted(remapped_components),
        "reused_render_assets": render_assets,
        "stable_identity_proven": stable,
        "ripple_complete": not unplanned_ripple_items,
        "sync_proven": sync_proven,
        "reuse_proven": (stable and manifest_complete and sync_proven
                         and not unplanned_ripple_items),
        "full_reconstruction_required": not (
            stable and manifest_complete and sync_proven
            and not unplanned_ripple_items),
        "reason": "; ".join(reasons) if reasons else "all dependent components and identities are proven",
    }


__all__ = ["plan_structural_patch"]
