"""Execute a complete recorded reel plan through Resolve's append API.

The recorder already serves the OTIO backend. This backend uses the same
plan when a native append is needed, without manufacturing an EditPatch
transaction, Project Model load, and three timeline reads for each batch.
Only audio spill requires an intermediate, targeted speech-row read: an
MXF append can put its second channel on a later placement's destination.
"""
from __future__ import annotations

import os
from collections import Counter

from library.tools.resolve_temporal import (
    append_duration_fields, append_fields, played_source_span,
    read_playback_rate, record_duration_for_source, record_span,
    source_span as normalized_source_span,
)


def supports(prepared, transitions):
    return (not transitions
            and not any("rate" in row for row in prepared.placements_list)
            and not any(
        segment.get("container") == "frames"
        for segment in (prepared.subtitle_segments or ())))


def append_recorded(project, recorder, name, track_plan, fps, width, height,
                    project_folder, resolve_app):
    from library.tools.reel_build import (
        _create_sized_reel_timeline, commit_footage_placements,
        import_pool_item, pool_item_for,
    )
    from library.tools.still_placement import place_still_exact

    pool = project.GetMediaPool()
    timeline = _create_sized_reel_timeline(
        project, pool, name, track_plan, width, height, fps=fps)
    index = recorder.pool_index
    sources = {}
    for spec in recorder.specs:
        if spec.path in sources:
            continue
        item = pool_item_for(pool, spec.path, pool_index=index)
        if item is None or spec.path in recorder.bins:
            item = import_pool_item(
                pool, spec.path, recorder.bins.get(spec.path),
                pool_index=index)
        if item is None:
            from library.tools.reel_build import ReelBuildError
            raise ReelBuildError(
                f"{name}: Resolve would not import planned source "
                f"{spec.path!r}; refusing to omit a planned item")
        sources[spec.path] = item

    by_id = {str(item.GetUniqueId()): item for item in sources.values()}
    video, audio, stills = [], [], []
    expected = []
    for number, spec in enumerate(recorder.specs):
        item = sources[spec.path]
        still = item.GetClipProperty("Type") == "Still"
        source_fps = float(item.GetClipProperty("FPS") or fps)
        frames = record_duration_for_source(
            record_span(spec.source_in, spec.source_out),
            source_fps=source_fps, timeline_fps=fps, still=still)
        op = {
            "op": "clip.insert", "unique_id": f"planned:{number}",
            "track_type": "video" if spec.media_type == 1 else "audio",
            "track_index": spec.track, "record_in": spec.record,
            "record_out": spec.record + frames,
            "source_in": spec.source_in, "source_out": spec.source_out,
            "media_pool_item": str(item.GetUniqueId()),
        }
        expected.append({**op, "source_file": spec.path, "still": still,
                         "properties": dict(spec.properties)})
        if still:
            stills.append((spec, item, frames))
        elif spec.media_type == 1:
            video.append(op)
        else:
            audio.append((op, {
                "target_row": spec.track, "expected_channel": spec.channel,
                "label": f"A{spec.track} {os.path.basename(spec.path)} @{spec.record}",
                "record_in": spec.record, "source_file": spec.path,
            }))

    def append(operations):
        infos = [{"mediaPoolItem": by_id[op["media_pool_item"]],
                  **append_fields(op["source_in"], op["source_out"],
                                  op["record_in"], op["track_index"],
                                  1 if op["track_type"] == "video" else 2)}
                 for op in operations]
        # No whole-timeline read here. A truthy return is provisional;
        # the final inventory below is the verdict, including false-success.
        pool.AppendToTimeline(infos)

    record = commit_footage_placements(
        project, timeline, track_plan, name, video, audio, append_ops=append)
    for spec, item, frames in stills:
        place_still_exact(
            resolve_app, project, pool, timeline, item, frames,
            spec.record, spec.track, fps, timeline_name=name)
    return timeline, record, expected


def read_inventory(timeline, selected_rows=None, *, handles=None, check_media=False, row_inventory=None):
    """One inexpensive final pass, with no color/Fusion/model work."""
    from library.tools.reel_build import _linked_ids
    from library.tools.timeline_ingest import exact_frame_rate
    from library.tools.reel_ending import is_native_hold
    timeline_fps = exact_frame_rate(float(timeline.GetSetting("timelineFrameRate")))
    source_rates = {}
    source_errors = {}
    rows = []
    for kind in ("video", "audio"):
        for index in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
            if selected_rows is not None and (kind, index) not in selected_rows:
                continue
            items = timeline.GetItemListInTrack(kind, index) or ()
            if row_inventory is not None:
                row_inventory.append((kind, index, len(items)))
            for item in items:
                source = item.GetMediaPoolItem()
                path = source.GetClipProperty("File Path") if source else ""
                if check_media and path not in source_errors:
                    errors = []
                    if source:
                        try:
                            offline = source.GetClipProperty("Offline")
                        except Exception:
                            offline = None
                        if str(offline or "").strip().lower() in ("1", "true", "yes", "offline"):
                            errors.append(f"{path} reports Offline={offline}")
                        if path and not os.path.isfile(path):
                            errors.append(f"media is missing at {path}")
                    source_errors[path] = errors
                uid = str(item.GetUniqueId())
                if handles is not None:
                    handles[uid] = item
                if path not in source_rates:
                    source_rates[path] = exact_frame_rate(float(
                        source.GetClipProperty("FPS") or timeline_fps)) if source else timeline_fps
                raw_in, raw_out = item.GetSourceStartFrame(), item.GetSourceEndFrame()
                # Resolve's raw source-out report is not the played edge
                # (timeline_ingest documents the measured disagreement).
                # Native append offsets retain the requested file window;
                # derive its played end from the measured timeline clock.
                left = item.GetLeftOffset()
                source_in = int(left) if left is not None else raw_in
                record = record_span(item.GetStart(), item.GetEnd())
                held = kind == "video" and is_native_hold(item)
                if held:
                    source_in = raw_in
                playback_rate = read_playback_rate(
                    item, zero_speed_hold=held)
                played = played_source_span(
                    source_in, record, source_fps=source_rates[path],
                    timeline_fps=timeline_fps,
                    still=bool(source and source.GetClipProperty("Type") == "Still"),
                    zero_speed_hold=held, playback_rate=playback_rate)
                rows.append({
                    "track_type": kind, "track_index": index,
                    "source_file": path,
                    "source_in": source_in,
                    "source_out": played.end,
                    "raw_source_in": raw_in, "raw_source_out": raw_out,
                    "record_in": record.start, "record_out": record.end,
                    "unique_id": uid,
                    "media_errors": source_errors.get(path, []),
                    "transform": item.GetProperty(),
                    "linked_ids": _linked_ids(item),
                })
    return rows


def compare_inventory(expected, actual, deleted_tracks=(), link_groups=()):
    """Exact multisets preserve duplicates; indexes make the check linear."""
    def signature(row, planned=False):
        index = int(row["track_index"])
        if planned:
            index -= sum(1 for deleted in deleted_tracks
                         if deleted["media_type"] == row["track_type"]
                         and deleted["index"] < row["track_index"])
        # Native stills have an NLE-defined source window; their planned
        # timeline span and file identity remain exact.
        source = (None, None) if row.get("still") else (
            int(row["source_in"]), int(row["source_out"]))
        return (row["track_type"], index,
                os.path.realpath(row["source_file"]), *source,
                int(row["record_in"]), int(row["record_out"]))

    still_keys = {(row["track_type"], signature(row, True)[1],
                   os.path.realpath(row["source_file"]), row["record_in"])
                  for row in expected if row.get("still")}
    observed = []
    for row in actual:
        key = (row["track_type"], row["track_index"],
               os.path.realpath(row["source_file"]), row["record_in"])
        observed.append(signature({**row, "still": key in still_keys}))
    wanted = Counter(signature(row, True) for row in expected)
    got = Counter(observed)
    by_id = {row["unique_id"]: row for row in actual}
    by_signature = {}
    for row in actual:
        key = (row["track_type"], row["track_index"],
               os.path.realpath(row["source_file"]), row["record_in"])
        by_signature.setdefault(key, []).append(row)
    property_mismatches = []
    for row in expected:
        index = signature(row, True)[1]
        matches = by_signature.get((row["track_type"], index,
                                    os.path.realpath(row["source_file"]),
                                    row["record_in"]), ())
        if len(matches) != 1:
            continue
        held = matches[0].get("transform") or {}
        for prop, value in row.get("properties", {}).items():
            try:
                agrees = abs(float(held[prop]) - float(value)) <= 0.001
            except (KeyError, TypeError, ValueError):
                agrees = False
            if not agrees:
                property_mismatches.append((row["track_type"], index, row["record_in"], prop,
                                            value, held.get(prop)))
    broken_links = []
    for members in link_groups:
        ids = set(members)
        for uid in ids:
            if uid not in by_id or not ids - {uid} <= set(by_id[uid]["linked_ids"]):
                broken_links.append(uid)
    media_errors = sorted({error for row in actual for error in row.get("media_errors", ())})
    return {"passed": wanted == got and not broken_links and not property_mismatches and not media_errors,
            "media_errors": media_errors,
            "expected_items": sum(wanted.values()), "actual_items": sum(got.values()),
            "missing": list((wanted - got).elements()),
            "unexpected": list((got - wanted).elements()),
            "broken_links": broken_links, "property_mismatches": property_mismatches}


def verify_or_repair(project, timeline, expected, *, deleted_tracks=(),
                     link_groups=(), resolve_app=None, fps=24,
                     project_folder="", prune_empty_rows=False):

    """Read once; only missing items and broken links get another call.

    Unexpected media is a refusal, not permission to delete anything.
    A repair is bounded to one attempt and a targeted row re-read. The
    staging remains unpromotable if Resolve still disagrees.
    """
    handles, rows = {}, []
    actual = read_inventory(timeline, handles=handles, check_media=resolve_app is not None,
                            row_inventory=rows)
    if prune_empty_rows:
        # Empty planned rows are derived from this same inventory.
        # Keep a row whose append is missing so targeted repair can
        # fill it; never delete a row carrying unexpected media.
        occupied = {(row["track_type"], row["track_index"]) for row in expected}
        for kind, index, count in reversed(rows):
            if count or (kind, index) in occupied:
                continue
            if not timeline.DeleteTrack(kind, index):
                raise RuntimeError(f"empty {kind}{index} declined deletion")
            deleted_tracks.append({"media_type": kind, "index": index, "name": "unoccupied"})
        for row in actual:
            row["track_index"] -= sum(1 for deleted in deleted_tracks
                                      if deleted["media_type"] == row["track_type"]
                                      and deleted["index"] < row["track_index"])
    result = compare_inventory(expected, actual, deleted_tracks, link_groups)
    result["repair_attempted"] = False
    if result["passed"] or result["unexpected"] or result["media_errors"]:
        return result, actual
    missing_slots = {(row[0], row[1], row[-2]) for row in result["missing"]}
    if any(row.get("requires_full_replay") and
           (row["track_type"], row["track_index"], row["record_in"]) in missing_slots
           for row in expected):
        result["repair_refused"] = "missing picture needs its full planned treatment replay"
        return result, actual
    from library.tools import edit_algebra
    from library.tools.still_placement import place_still_exact

    result["repair_attempted"] = True
    pool = project.GetMediaPool()
    changed_rows = set()
    missing = Counter(tuple(row) for row in result["missing"])
    with edit_algebra.insertion_sources():
        for row in expected:
            index = row["track_index"] - sum(
                1 for deleted in deleted_tracks
                if deleted["media_type"] == row["track_type"]
                and deleted["index"] < row["track_index"])
            source_span = (None, None) if row.get("still") else (
                row["source_in"], row["source_out"])
            key = (row["track_type"], index,
                   os.path.realpath(row["source_file"]), *source_span,
                   row["record_in"], row["record_out"])
            if not missing[key]:
                continue
            missing[key] -= 1
            item = edit_algebra._find_pool_item_by_id(pool, row["media_pool_item"])
            if item is None:
                continue
            if row.get("still"):
                still = place_still_exact(resolve_app, project, pool, timeline, item,
                                          record_span(row["record_in"],
                                                      row["record_out"]).duration,
                                          row["record_in"], index, fps,
                                          timeline_name=timeline.GetName())
                placed = [still["item"]]
            else:
                placed = pool.AppendToTimeline([{
                    "mediaPoolItem": item,
                    **append_duration_fields(
                        row["source_in"],
                        normalized_source_span(row["source_in"],
                                               row["source_out"]).duration,
                        row["record_in"], index,
                        1 if row["track_type"] == "video" else 2),
                }])
            from library.tools.transform_write_log import set_property
            for placed_item in placed or ():
                for prop, value in row.get("properties", {}).items():
                    set_property(placed_item, prop, value,
                                 item_identity={"resolve_unique_id": str(placed_item.GetUniqueId())},
                                 project_folder=project_folder or None,
                                 timeline_name=timeline.GetName())
            changed_rows.add((row["track_type"], index))
    if result["property_mismatches"]:
        from library.tools.transform_write_log import set_property
        wanted_properties = {}
        for kind, index, start, prop, value, _ in result["property_mismatches"]:
            wanted_properties.setdefault((kind, index, start), {})[prop] = value
        for row in actual:
            slot = (row["track_type"], row["track_index"], row["record_in"])
            for prop, value in wanted_properties.get(slot, {}).items():
                set_property(handles[row["unique_id"]], prop, value,
                             item_identity={"resolve_unique_id": row["unique_id"]},
                             project_folder=project_folder or None,
                             timeline_name=timeline.GetName())
                changed_rows.add(slot[:2])
    if result["broken_links"]:
        broken = set(result["broken_links"])
        for group in link_groups:
            if broken.intersection(group) and all(uid in handles for uid in group):
                timeline.SetClipsLinked([handles[uid] for uid in group], True)
                changed_rows.update((row["track_type"], row["track_index"])
                                    for row in actual if row["unique_id"] in group)
    reread = read_inventory(timeline, changed_rows, check_media=resolve_app is not None)
    actual = [row for row in actual if
              (row["track_type"], row["track_index"]) not in changed_rows] + reread
    result = compare_inventory(expected, actual, deleted_tracks, link_groups)
    result["repair_attempted"] = True
    return result, actual
