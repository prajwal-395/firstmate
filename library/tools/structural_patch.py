"""Dependency-aware planning for structural timeline edits.

This planner is deliberately read-only.  It maps the complete pre-edit
timeline onto the proposed placement, asks the existing cascade graph which
components a timing change reaches, and reports when the component manifest
cannot prove localized reuse.  The staged composed-edit path remains the
writer and verifier.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path


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


def _manifest_proves_components(manifest: Mapping | None,
                                required: set[str]) -> bool:
    """Require intact component and manifest fingerprints before reuse."""
    if (not isinstance(manifest, Mapping)
            or manifest.get("format") != "reel_component_manifest/1"):
        return False
    components = manifest.get("components")
    if not isinstance(components, Mapping) or not required.issubset(components):
        return False
    digests = {}
    for name in required:
        component = components[name]
        if (not isinstance(component, Mapping)
                or component.get("status") != "recorded"
                or not component.get("id")
                or not isinstance(component.get("dependencies"), Mapping)
                or not component.get("dependencies")
                or not isinstance(component.get("assets"), list)):
            return False
        body = {key: component[key]
                for key in ("id", "dependencies", "assets")}
        digest = hashlib.sha256(json.dumps(
            body, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if component.get("digest") != digest:
            return False
        digests[name] = digest
    manifest_digest = hashlib.sha256(json.dumps(
        digests, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return manifest.get("manifest_digest") == manifest_digest


def plan_structural_patch(
    tracks: Sequence[Mapping], changes: Sequence[object],
    insertions: Sequence[object] = (), *,
    deletions: Sequence[Mapping] = (),
    component_manifest: Mapping | None = None,
    sync_links: Mapping[str, str] | None = None,
    asset_root: str | None = None,
) -> dict:
    """Describe the smallest structurally affected interval and dependencies.

    ``changes`` and ``insertions`` use ``composed_edit`` records.  All
    timeline rows are considered, including audio and overlays.  A missing,
    malformed, or incomplete component manifest marks reuse unproven; callers
    must then use the full reconstruction path.
    """
    from library.tools import cascade_graph

    before_keys = _stable_keys(tracks)
    existing_item_count = sum(
        len(track.get("clips", ())) for track in tracks)
    changed_rows: set[str] = set()
    changed_frames: list[int] = []
    shifted_rows: set[str] = set()
    direct_targets: list[dict] = []
    dependent_targets: list[dict] = []
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
        placement = {
            "row": row,
            "item_index": int(change.item_index),
            "stable_id": before_keys.get((row, int(change.item_index))),
            "old_start": old,
            "new_start": new,
            "old_duration": int(change.previous_duration),
            "new_duration": int(change.duration),
        }
        placements.append(placement)
        direct_targets.append(dict(placement))
    for insertion in insertions:
        row = str(insertion.row)
        changed_rows.add(row)
        changed_frames.extend((int(insertion.record_frame),
                               int(insertion.record_frame) + int(insertion.duration)))
        shifted_rows.add(row)
        placement = {"row": row, "stable_id": None,
                           "new_start": int(insertion.record_frame),
                           "new_duration": int(insertion.duration),
                           "identity": "new-item"}
        placements.append(placement)
        direct_targets.append(dict(placement))

    for deletion in deletions:
        row = str(deletion["row"]).upper()
        changed_rows.add(row)
        start_frame = int(deletion["record_frame"])
        duration = int(deletion["duration"])
        changed_frames.extend((start_frame, start_frame + duration))
        placement = {
            "row": row,
            "stable_id": deletion.get("stable_id"),
            "old_start": start_frame,
            "old_duration": duration,
            "identity": "deleted-item",
        }
        placements.append(placement)
        direct_targets.append(dict(placement))

    if not placements:
        return {"status": "no_op", "affected_range": None,
                "changed_rows": [], "dependencies": [],
                "tracks_to_change": [],
                "direct_targets": [], "dependent_targets": [],
                "affected_by_track": {}, "stages": [],
                "stable_identity_proven": True,
                "sync_proven": True, "reuse_proven": True,
                "full_reconstruction_required": False}

    stable = (
        len(before_keys) == existing_item_count
        and all(item.get("stable_id") for item in placements
                if item.get("identity") != "new-item")
    )
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
                elif int(clip["record_in"]) >= ripple_cut:
                    dependent_targets.append({
                        "row": row, "item_index": index,
                        "stable_id": before_keys.get((row, index)),
                        "old_start": int(clip["record_in"]),
                        "old_end": int(clip["record_out"]),
                        "role": "ripple-dependent",
                    })
    timeline_ends = [int(clip["record_out"])
                     for track in tracks for clip in track.get("clips", ())]
    end = max(timeline_ends, default=max(changed_frames))
    start = min(changed_frames)
    affected_end = end if shifted_rows else max(
        int(item.get("new_start", item["old_start"]))
        + int(item.get("new_duration", item["old_duration"]))
        for item in placements)

    dependencies = []
    remapped_components: set[str] = set()
    for row in sorted(changed_rows):
        entity_id = f"structural:{row}:{start}"
        row_deletions = [deletion for deletion in deletions
                         if str(deletion["row"]).upper() == row]
        component = next((deletion.get("component")
                          for deletion in row_deletions
                          if deletion.get("component")), None)
        kind = {
            "captions": "caption_content",
            "lower_thirds": "lower_third_asset",
            "overlays": "overlay_plan",
            "audio": "audio_decision",
        }.get(component, "audio_decision" if row.startswith("A")
              else "placement_timing")
        requested_component = component
        dependency = cascade_graph.selective_invalidation(
            {"kind": kind, "entity_id": entity_id,
             "components": [requested_component] if requested_component else
             ["audio"] if kind == "audio_decision" else
             ["overlays"] if kind == "overlay_plan" else
             ["footage", "captions", "lower_thirds", "overlays"]})
        dependencies.append(dependency)
        remapped_components.update(dependency["remapped_components"])

    required = {"aroll", "captions", "motion_graphics", "audio",
                "fusion", "timeline"}
    components = ((component_manifest or {}).get("components")
                  if isinstance(component_manifest, Mapping) else None)
    manifest_complete = _manifest_proves_components(
        component_manifest, required)
    reasons = []
    if not stable:
        reasons.append(
            "one or more existing items lack stable source lineage or a changed item lacks its lineage")
    if unplanned_ripple_items:
        reasons.append("ripple omitted downstream items: " +
                       ", ".join(unplanned_ripple_items))
    if not manifest_complete:
        reasons.append("component manifest does not prove every dependent component")
    audio_rows = {
        _row(track) for track in tracks
        if str(track["type"]).lower().startswith("a") and track.get("clips")
    }
    sync_links = dict(sync_links or {})
    changed_video_rows = {row for row in changed_rows if row.startswith("V")}
    sync_proven = not audio_rows or all(
        sync_links.get(row) in audio_rows for row in changed_video_rows
    )
    if not sync_proven:
        reasons.append(
            "changed picture rows have no declared audio sync mapping in the composed-edit request")
    render_assets = []
    asset_identity_proven = True
    if manifest_complete:
        manifest_components = components
        if {"captions", "lower_thirds", "overlays"} & remapped_components:
            for name in ("captions", "motion_graphics"):
                component = manifest_components.get(name) or {}
                for asset in component.get("assets", ()):
                    if not isinstance(asset, Mapping) or not asset.get("sha256"):
                        asset_identity_proven = False
                        render_assets.append({
                            "component": name,
                            "path": (asset.get("path")
                                     if isinstance(asset, Mapping) else None),
                            "identity_verified": False,
                            "reason": "manifest asset has no content hash",
                        })
                        continue
                    entry = {
                            "component": name,
                            "sha256": asset["sha256"],
                            "bytes": asset.get("bytes"),
                            "path": asset.get("path"),
                            "identity_verified": False,
                    }
                    candidate = asset.get("path")
                    if asset_root and candidate:
                        root = Path(asset_root).resolve()
                        path = (root / str(candidate)).resolve()
                        try:
                            path.relative_to(root)
                            digest = hashlib.sha256(path.read_bytes()).hexdigest()
                            entry["identity_verified"] = (
                                digest == asset["sha256"]
                                and path.stat().st_size == asset.get("bytes")
                            )
                        except (OSError, ValueError, TypeError):
                            entry["identity_verified"] = False
                    asset_identity_proven &= entry["identity_verified"]
                    render_assets.append(entry)
    if render_assets and not asset_identity_proven:
        reasons.append(
            "one or more reusable rendered assets are missing or do not match their recorded content identity")
    affected_by_track: dict[str, dict[str, int]] = {}
    for item in placements:
        row = str(item["row"])
        start_frame = int(item.get("old_start", item.get("new_start", 0)))
        end_frame = start_frame + int(item.get("old_duration", item.get("new_duration", 0)))
        new_start = int(item.get("new_start", start_frame))
        new_end = new_start + int(item.get("new_duration", item.get("old_duration", 0)))
        bounds = affected_by_track.setdefault(
            row, {"start_frame": min(start_frame, new_start),
                  "end_frame": max(end_frame, new_end)})
        bounds["start_frame"] = min(bounds["start_frame"], start_frame, new_start)
        bounds["end_frame"] = max(bounds["end_frame"], end_frame, new_end)
    for item in dependent_targets:
        row = item["row"]
        bounds = affected_by_track.setdefault(
            row, {"start_frame": item["old_start"], "end_frame": item["old_end"]})
        bounds["start_frame"] = min(bounds["start_frame"], item["old_start"])
        bounds["end_frame"] = max(bounds["end_frame"], item["old_end"])
    if shifted_rows:
        for row in shifted_rows:
            bounds = affected_by_track.get(row)
            if bounds is not None:
                bounds["end_frame"] = max(bounds["end_frame"], end)
    safe = (stable and manifest_complete and sync_proven
            and not unplanned_ripple_items and asset_identity_proven)
    reason = "; ".join(reasons) if reasons else "all dependent components and identities are proven"
    return {
        "status": "planned",
        "operation": "structural",
        "affected_range": {"start_frame": start, "end_frame": affected_end},
        "changed_rows": sorted(changed_rows),
        "tracks_to_change": sorted(
            changed_rows | {item["row"] for item in dependent_targets}),
        "shifted_rows": sorted(shifted_rows),
        "placements": placements,
        "direct_targets": direct_targets,
        "dependent_targets": dependent_targets,
        "affected_by_track": affected_by_track,
        "dependencies": dependencies,
        "recomputed_source_to_timeline_mappings": placements,
        "remapped_components": sorted(remapped_components),
        "reused_render_assets": render_assets,
        "render_asset_identity_proven": asset_identity_proven,
        "stable_identity_proven": stable,
        "existing_item_identities_proven": len(before_keys) == existing_item_count,
        "ripple_complete": not unplanned_ripple_items,
        "sync_proven": sync_proven,
        "reuse_proven": safe,
        "full_reconstruction_required": not safe,
        "escalation_reason": reason if not safe else None,
        "reason": reason,
        "stages": (["validate-base-identities", "apply-direct-targets",
                    "apply-ripple-dependencies", "verify-sync-and-stable-identities",
                    "commit-staged-edit"] if safe else []),
        "preserved_components": sorted(
            set().union(*(set(d.get("reused_components") or ())
                          for d in dependencies)) if dependencies else set()),
    }


__all__ = ["plan_structural_patch"]
