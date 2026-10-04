"""Carry disabled clip state from the retiring reel onto a rebuild.

Resolve stores `TimelineItem` enablement on the live timeline. Replacing that
timeline throws the declaration away unless the build carries it to a matching
staged item. Text-bearing Semantic graphics use element type, displayed copy,
and any attached asset or data for an exact match. If the element type changed,
one overlapping staged graphic with the same displayed copy is a safe fallback:
the staged item is disabled and the change is reported. Other Semantic
elements match on rendered visual properties and behavior data, without
planner rationale or color provenance. Other clips use their source path and
source-frame range.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path


class DisabledClipCarryRefused(RuntimeError):
    """A disabled source clip cannot be mapped safely onto the staging."""


_TIMING_FIELDS = frozenset({
    "timeline_start", "timeline_end", "timing_basis",
    "startFrame", "durationFrames", "endFrame",
    "timelineProgressStart", "timelineProgressEnd",
})
_DESCRIPTIVE_FIELDS = frozenset({"subject", "why", "colorBasis"})
_DATA_PROVENANCE_FIELDS = frozenset({"colour_basis", "color_basis"})


def _visual_data(element: dict):
    data = element.get("data", {})
    if not isinstance(data, dict):
        return data
    return {key: value for key, value in data.items()
            if key not in _DATA_PROVENANCE_FIELDS}


def semantic_graphic_identity(elements) -> list[dict]:
    """Stable content identity for a rendered Semantic segment.

    A text-bearing element is identified by its element type and ordered
    displayed copy. Its color, anchor, layer defaults, run formatting, and
    rationale are presentation details that can be re-resolved on a rerender.
    Timeline position is omitted so an opening shift does not change the key;
    occurrence order distinguishes repeated identical graphics. Explicit
    assets and behavior data remain part of the key. Descriptive rationale
    and color provenance do not: the rendered color itself is preserved,
    while planner wording and expression formatting can change between builds.
    Elements without text retain every other visual property, so a graphic
    with no copy is only carried when its rendered intent stays the same.
    """
    normalized = []
    for element in elements or ():
        if not isinstance(element, dict):
            return []
        runs = element.get("runs")
        element_type = element.get("element")
        if (isinstance(element_type, str) and element_type
                and isinstance(runs, list) and runs
                and all(isinstance(run, dict)
                        and isinstance(run.get("text"), str)
                        for run in runs)):
            copy = [run["text"] for run in runs]
            if any(copy):
                normalized.append({
                    "element": element_type,
                    "copy": copy,
                    "asset": element.get("asset", ""),
                    "data": _visual_data(element),
                })
                continue
        normalized.append({
            key: value for key, value in element.items()
            if key not in _TIMING_FIELDS | _DESCRIPTIVE_FIELDS
            and key != "data"
        })
        if "data" in element:
            normalized[-1]["data"] = _visual_data(element)
    return sorted(normalized, key=_canonical)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _segment_id_candidates(clip: dict) -> list[str]:
    candidates = []
    for value in (clip["source_file"], clip["name"]):
        stem = Path(str(value or "")).stem
        if stem and stem not in candidates:
            candidates.append(stem)
    return candidates


def _saved_semantic_identities(project_folder: str,
                               reel_name: str) -> dict[str, list[str]]:
    """Read stable identities, with a props-file bridge for older records."""
    from library.tools import reel_semantic_visual

    records = reel_semantic_visual.read_records(project_folder)
    record = reel_semantic_visual.record_for_reel(records, reel_name)
    if record is None:
        return {}

    identities: dict[str, list[str]] = defaultdict(list)
    for segment in record.get("segments", ()):
        segment_id = str(segment.get("segment_id") or "")
        if not segment_id:
            segment_id = Path(str(segment.get("overlay_path") or "")).stem
        if not segment_id:
            continue

        identity = segment.get("carry_identity")
        if isinstance(identity, list) and identity:
            normalized = semantic_graphic_identity(identity)
        else:
            path = Path(str(segment.get("overlay_path") or ""))
            props_path = path.with_name(f"{path.stem}_props.json")
            try:
                props = json.loads(props_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                props = {}
            normalized = semantic_graphic_identity(
                props.get("elements") if isinstance(props, dict) else None)
        if not normalized:
            continue
        identities[segment_id].append(_canonical(normalized))
    return dict(identities)


def _semantic_identity(clip: dict, identities_by_segment: dict,
                       final: str, row_label: str) -> str:
    for segment_id in _segment_id_candidates(clip):
        identities = identities_by_segment.get(segment_id)
        if identities:
            unique = sorted(set(identities))
            if len(unique) != 1:
                raise DisabledClipCarryRefused(
                    f"{final}: disabled Semantic item {clip['name']!r} on "
                    f"{row_label} at frame {clip['record_in']} maps to "
                    f"more than one recorded graphic intent.")
            return unique[0]
    raise DisabledClipCarryRefused(
        f"{final}: disabled Semantic item {clip['name']!r} on {row_label} "
        f"at frame {clip['record_in']} has no matching semantic-visual "
        f"record or rendered props. Promotion is refused rather than "
        f"re-enabling it.")


def add_semantic_graphic_identities(project_folder: str, reel_name: str,
                                    items: list[dict]) -> list[dict]:
    """Attach recorded source-graphic identities to Semantic timeline items.

    The identity comes from the reel's semantic-visual plan (or the
    render props that older plan records point to), never from the
    generated media filename or its content digest. Missing identity is
    left absent so callers can retain exact source matching; a changed
    render with no recorded source identity then fails to map safely. The
    plan is read once for the whole snapshot, not once per clip.
    """
    try:
        identities = _saved_semantic_identities(project_folder, reel_name)
    except Exception:  # noqa: BLE001 - an unavailable record is no identity
        return items
    if not identities:
        return items
    for item in items:
        row = str(item.get("track_name") or "")
        if (item.get("track_type") != "video"
                or "semantic" not in row.casefold()):
            continue
        source_identity = str(item.get("source_identity") or "")
        source_file = (source_identity[5:]
                       if source_identity.startswith("file:") else "")
        clip = {"name": item["name"], "source_file": source_file,
                "record_in": item.get("record_in")}
        row_label = f"video:{row} (V{item.get('track_index')} {row!r})"
        try:
            identity = _semantic_identity(
                clip, identities, reel_name, row_label)
        except DisabledClipCarryRefused:
            continue
        item["graphic_identity"] = identity
    return items


def _semantic_copy_details(identity: str):
    """Displayed-copy key and element types for a text-bearing identity."""
    try:
        elements = json.loads(identity)
    except (TypeError, ValueError):
        return None
    if not isinstance(elements, list) or not elements:
        return None
    copies, element_types = [], []
    for element in elements:
        if (not isinstance(element, dict)
                or not isinstance(element.get("element"), str)
                or not isinstance(element.get("copy"), list)
                or not all(isinstance(line, str)
                           for line in element["copy"])):
            return None
        copies.append(element["copy"])
        element_types.append(element["element"])
    return _canonical(sorted(copies, key=_canonical)), sorted(element_types)


def _semantic_items_by_copy(tracks: list[dict], identities_by_segment: dict,
                            final: str) -> dict[str, list[dict]]:
    """Index identifiable staged text graphics without their element type."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for track in tracks:
        if (track["type"] != "video"
                or "semantic" not in str(track["name"] or "").casefold()):
            continue
        label = f"{track['type']}:{track['name'] or track['index']} " \
            f"(V{track['index']} {track['name']!r})"
        for clip in track.get("clips", ()):
            try:
                identity = _semantic_identity(
                    clip, identities_by_segment, final, label)
            except DisabledClipCarryRefused:
                continue
            details = _semantic_copy_details(identity)
            if details is None:
                continue
            copy_key, element_types = details
            grouped[copy_key].append({
                "clip": clip,
                "track": track,
                "identity": identity,
                "element_types": element_types,
            })
    return dict(grouped)


def _source_identity(clip: dict, final: str, row_label: str) -> str:
    source = str(clip["source_file"] or "")
    source_in = clip["source_in_frame"]
    source_out = clip["source_out_frame"]
    if not source or source_in is None or source_out is None:
        raise DisabledClipCarryRefused(
            f"{final}: disabled item {clip['name']!r} on {row_label} at "
            f"frame {clip['record_in']} has unreadable source identity. "
            f"Promotion is refused rather than re-enabling it.")
    return _canonical({
        "track_type": clip["track_type"],
        "track_name": clip["track_name"],
        "source_file": os.path.normcase(os.path.normpath(source)),
        "source_in_frame": source_in,
        "source_out_frame": source_out,
    })


def _item_order(item: dict):
    return (item["clip"]["record_in"], item["clip"]["track_index"],
            item["clip"]["name"])


def _semantic_place_items(source: dict, source_track: dict,
                          staged_tracks: list[dict]) -> list[dict]:
    """Find staged Semantic graphics that play over the source's timeline."""
    source_start = source["record_in"]
    source_end = source["record_out"]
    row_name = str(source_track["name"] or "").casefold()
    found = []
    for track in staged_tracks:
        if (track["type"] != "video"
                or str(track["name"] or "").casefold() != row_name
                or "semantic" not in row_name):
            continue
        for clip in track["clips"]:
            if (clip["record_in"] < source_end
                    and source_start < clip["record_out"]):
                found.append({"clip": clip, "track": track})
    return sorted(found, key=_item_order)


def _record_unmatched_semantic(source_item: dict,
                               staged_tracks: list[dict],
                               final: str) -> dict:
    """Accept an unmatched disabled graphic only when it stays invisible."""
    source = source_item["clip"]
    source_track = source_item["track"]
    place_items = _semantic_place_items(source, source_track, staged_tracks)
    enabled = [item for item in place_items
               if item["clip"]["enabled"] is not False]
    row_label = (f"{source_track['type']}:{source_track['name'] or source_track['index']} "
                 f"(V{source_track['index']} {source_track['name']!r})")
    if enabled:
        replacement = enabled[0]["clip"]
        raise DisabledClipCarryRefused(
            f"{final}: disabled Semantic item {source['name']!r} on "
            f"{row_label} at frame {source['record_in']} has no matching "
            f"staged occurrence, and enabled item {replacement['name']!r} "
            f"on the staging Semantic row occupies its place at frame "
            f"{replacement['record_in']}. Promotion is refused rather than "
            f"showing a graphic the captain disabled.")

    return {
        "row": f"{source_track['type']}:{source_track['name'] or source_track['index']}",
        "source_item": source["name"],
        "source_frame": source["record_in"],
        "source_end": source["record_out"],
        "staged_items": [{
            "name": item["clip"]["name"],
            "start": item["clip"]["record_in"],
            "end": item["clip"]["record_out"],
            "enabled": item["clip"]["enabled"],
        } for item in place_items],
        "reason": ("staging has no enabled graphic at this place"
                   if place_items else "staging has no graphic at this place"),
    }


def _same_copy_type_change(source_item: dict, source_identity: str,
                           staged_tracks: list[dict], staged_by_copy: dict,
                           claimed_targets: set, final: str):
    """Find one overlapping staged text graphic with the same copy and new type."""
    details = _semantic_copy_details(source_identity)
    if details is None:
        return None
    copy_key, source_types = details
    source = source_item["clip"]
    source_track = source_item["track"]
    place_items = _semantic_place_items(source, source_track, staged_tracks)
    place_ids = {id(item["clip"]) for item in place_items}
    candidates = [item for item in staged_by_copy.get(copy_key, ())
                  if id(item["clip"]) in place_ids
                  and item["identity"] != source_identity
                  and item["element_types"] != source_types]
    if not candidates:
        return None
    row_label = (f"{source_track['type']}:{source_track['name'] or source_track['index']} "
                 f"(V{source_track['index']} {source_track['name']!r})")
    if len(candidates) != 1 or id(candidates[0]["clip"]) in claimed_targets:
        raise DisabledClipCarryRefused(
            f"{final}: disabled Semantic item {source['name']!r} on "
            f"{row_label} at frame {source['record_in']} has more than one "
            f"possible same-copy staged type-change match. Promotion is "
            f"refused rather than re-enabling it.")
    candidate = candidates[0]
    candidate_id = id(candidate["clip"])
    other_enabled = [item["clip"] for item in place_items
                     if item["clip"]["enabled"] is not False
                     and id(item["clip"]) != candidate_id]
    same_copy_ids = {
        id(item["clip"]) for item in staged_by_copy.get(copy_key, ())
        if id(item["clip"]) in place_ids
    }
    different_copy_enabled = [
        clip for clip in other_enabled if id(clip) not in same_copy_ids]
    if different_copy_enabled:
        replacement = different_copy_enabled[0]
        raise DisabledClipCarryRefused(
            f"{final}: disabled Semantic item {source['name']!r} on "
            f"{row_label} at frame {source['record_in']} has a same-copy "
            f"type-change match, but enabled item {replacement['name']!r} "
            f"with different copy occupies its place at frame "
            f"{replacement['record_in']}. Promotion is refused rather "
            f"than showing a graphic the captain disabled.")
    if other_enabled:
        replacement = other_enabled[0]
        raise DisabledClipCarryRefused(
            f"{final}: disabled Semantic item {source['name']!r} on "
            f"{row_label} at frame {source['record_in']} has more than one "
            f"enabled same-copy item at its place, including "
            f"{replacement['name']!r} at frame {replacement['record_in']}. "
            f"Promotion is refused because the carry is ambiguous.")
    return candidate


def _set_disabled(target: dict, source: dict, final: str, *, apply: bool):
    """Disable a staged live item and verify Resolve's result when applying."""
    staged_enabled_before = target["enabled"]
    if not apply:
        return staged_enabled_before, False
    try:
        written = target["_item"].SetClipEnabled(False)
        enabled = target["_item"].GetClipEnabled()
    except Exception as failed:
        raise DisabledClipCarryRefused(
            f"{final}: could not carry disabled state from "
            f"{source['name']!r} at frame {source['record_in']} onto staged "
            f"item {target['name']!r} at frame {target['record_in']} "
            f"({failed}); promotion is refused.") from failed
    if written is not True or enabled is not False:
        raise DisabledClipCarryRefused(
            f"{final}: Resolve did not verify disabled state on staged "
            f"item {target['name']!r} at frame {target['record_in']} for "
            f"the disabled source {source['name']!r} at frame "
            f"{source['record_in']} (SetClipEnabled returned {written!r}; "
            f"read-back was {enabled!r}); promotion is refused.")
    target["enabled"] = False
    return staged_enabled_before, enabled


def _attach_live_items(tracks, timeline, *, final: str):
    """Pair the reader's plain rows with their live Resolve handles."""
    from library.tools import reel_read

    try:
        live_rows = reel_read.live_items(timeline)
    except Exception as unreadable:
        raise DisabledClipCarryRefused(
            f"{final}: staged timeline items could not be enumerated "
            f"for an enabled-state write ({unreadable}); promotion is "
            f"refused.") from unreadable
    by_row = {(row["type"], row["index"]): row["items"]
              for row in live_rows}
    for track in tracks:
        key = (track["type"], track["index"])
        items = by_row.get(key)
        if items is None or len(items) != len(track["clips"]):
            raise DisabledClipCarryRefused(
                f"{final}: staged {track['type']} row "
                f"{track['name']!r} changed while its items were being "
                f"read; promotion is refused.")
        for clip, item in zip(track["clips"], items):
            clip["_item"] = item


def _items_by_identity(tracks, *, final: str, semantic_identities=None,
                       disabled_only=False) -> dict[str, list[dict]]:
    semantic_identities = semantic_identities or {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for track in tracks:
        semantic = (track["type"] == "video"
                    and "semantic" in
                    str(track["name"] or "").casefold())
        label = f"{track['type']}:{track['name'] or track['index']} " \
            f"(V{track['index']} {track['name']!r})"
        for clip in track.get("clips", ()):
            if disabled_only and clip["enabled"] is not False:
                continue
            if semantic:
                if disabled_only:
                    identity = _semantic_identity(
                        clip, semantic_identities, final, label)
                else:
                    try:
                        identity = _semantic_identity(
                            clip, semantic_identities, final, label)
                    except DisabledClipCarryRefused:
                        # An unidentifiable enabled item is not a carry
                        # candidate. A disabled one is refused above.
                        continue
            else:
                try:
                    identity = _source_identity(clip, final, label)
                except DisabledClipCarryRefused:
                    if disabled_only:
                        raise
                    continue
            grouped[identity].append({"clip": clip, "track": track})
    for items in grouped.values():
        items.sort(key=_item_order)
    return dict(grouped)


def carry_disabled_state(project_folder: str, final: str, staging: str,
                         retiring_timeline, staged_timeline, *,
                         apply: bool = True) -> dict:
    """Disable staging items that confidently match disabled live items.

    Semantic text graphics match exactly by element type, copy, and attached
    assets or data. One overlapping same-copy graphic with a changed element
    type is a fallback and is disabled with the change recorded. Occurrence
    order distinguishes repeated identical graphics. Other elements use their
    rendered visual properties and behavior data, and media clips use source
    identity. An unmatched disabled Semantic graphic is recorded when staging
    leaves its place invisible; an enabled different-copy graphic there refuses
    promotion.
    `apply=False` reads and reports the intended carry without changing staged
    enabled state, so callers can account for invisible removals before running
    other promotion guards.
    """
    from library.tools import reel_read

    try:
        old_tracks = reel_read.read_tracks(retiring_timeline)
        new_tracks = reel_read.read_tracks(staged_timeline)
    except reel_read.ReelReadError as unreadable:
        raise DisabledClipCarryRefused(
            f"{final}: disabled clip state could not be read from the "
            f"retiring or staged timeline ({unreadable}); promotion is "
            f"refused rather than re-enabling a clip.") from unreadable
    _attach_live_items(new_tracks, staged_timeline, final=final)

    old_semantic_record = _saved_semantic_identities(project_folder, final)
    new_semantic_record = _saved_semantic_identities(project_folder, staging)
    old_by_identity = _items_by_identity(
        old_tracks, final=final, semantic_identities=old_semantic_record)
    new_by_identity = _items_by_identity(
        new_tracks, final=final, semantic_identities=new_semantic_record)
    new_by_copy = _semantic_items_by_copy(
        new_tracks, new_semantic_record, final)
    disabled_by_identity = _items_by_identity(
        old_tracks, final=final, semantic_identities=old_semantic_record,
        disabled_only=True)

    carried = []
    unchanged_unmatched = []
    safe_replacements = []
    claimed_type_change_targets = set()
    for identity, old_disabled in disabled_by_identity.items():
        old_matches = old_by_identity.get(identity, [])
        new_matches = new_by_identity.get(identity, [])
        disabled_indexes = {
            index for index, item in enumerate(old_matches)
            if item["clip"]["enabled"] is False}
        unmatched_indexes = {
            index for index in disabled_indexes if index >= len(new_matches)}
        if (len(old_matches) != len(new_matches)
                and not unmatched_indexes):
            source = old_disabled[0]["clip"]
            track = old_disabled[0]["track"]
            row_label = f"{track['type']}:{track['name'] or track['index']} " \
                f"(V{track['index']} {track['name']!r})"
            raise DisabledClipCarryRefused(
                f"{final}: disabled item {source['name']!r} on "
                f"{row_label} at frame {source['record_in']} has "
                f"{len(old_matches)} matching source occurrence(s), but "
                f"the rebuilt staging {staging!r} has "
                f"{len(new_matches)}. The semantic content does not map "
                f"uniquely; promotion is refused rather than re-enabling "
                f"it.")

        for index in sorted(unmatched_indexes):
            source_item = old_matches[index]
            source_track = source_item["track"]
            if (source_track["type"] != "video"
                    or "semantic" not in str(
                        source_track["name"] or "").casefold()):
                source = source_item["clip"]
                row_label = (
                    f"{source_track['type']}:{source_track['name'] or source_track['index']} "
                    f"(V{source_track['index']} {source_track['name']!r})")
                raise DisabledClipCarryRefused(
                    f"{final}: disabled item {source['name']!r} on "
                    f"{row_label} at frame {source['record_in']} has "
                    f"{len(old_matches)} matching source occurrence(s), "
                    f"but the rebuilt staging {staging!r} has "
                    f"{len(new_matches)}. The source identity does not "
                    f"map uniquely; promotion is refused rather than "
                    f"re-enabling it.")
            changed_type_match = _same_copy_type_change(
                source_item, identity, new_tracks, new_by_copy,
                claimed_type_change_targets, final)
            if changed_type_match is None:
                unchanged_unmatched.append(_record_unmatched_semantic(
                    source_item, new_tracks, final))
                continue

            target = changed_type_match["clip"]
            source = source_item["clip"]
            staged_enabled_before, staged_enabled_after = _set_disabled(
                target, source, final, apply=apply)
            claimed_type_change_targets.add(id(target))
            _copy_key, source_types = _semantic_copy_details(identity)
            _copy_key, staged_types = _semantic_copy_details(
                changed_type_match["identity"])
            element_type_change = {
                "retired": source_types,
                "staged": staged_types,
            }
            carried.append({
                "row": (
                    f"{changed_type_match['track']['type']}:"
                    f"{changed_type_match['track']['name'] or changed_type_match['track']['index']}"),
                "source_item": source["name"],
                "source_frame": source["record_in"],
                "source_end": source["record_out"],
                "source_enabled": False,
                "staged_item": target["name"],
                "staged_frame": target["record_in"],
                "staged_enabled_before": staged_enabled_before,
                "staged_enabled_after": staged_enabled_after,
                "match_basis": "semantic graphic copy with element type change",
                "element_type_change": element_type_change,
            })
            safe_replacements.append({
                "row": (f"{source_track['type']}:"
                        f"{source_track['name'] or source_track['index']}"),
                "source_item": source["name"],
                "source_frame": source["record_in"],
                "source_end": source["record_out"],
                "replacement_item": target["name"],
                "replacement_frame": target["record_in"],
                "reason": "same copy carried disabled across an element type change",
            })

        for index in sorted(disabled_indexes):
            if index in unmatched_indexes:
                continue
            target = new_matches[index]["clip"]
            source = old_matches[index]["clip"]
            staged_enabled_before, enabled = _set_disabled(
                target, source, final, apply=apply)
            carried.append({
                "row": (
                    f"{new_matches[index]['track']['type']}:"
                    f"{new_matches[index]['track']['name'] or new_matches[index]['track']['index']}"),
                "source_item": old_matches[index]["clip"]["name"],
                "source_frame": old_matches[index]["clip"]["record_in"],
                "source_end": old_matches[index]["clip"]["record_out"],
                "source_enabled": False,
                "staged_item": target["name"],
                "staged_frame": target["record_in"],
                "staged_enabled_before": staged_enabled_before,
                "staged_enabled_after": enabled,
                "match_basis": (
                    "semantic graphic type and copy"
                    if (new_matches[index]["track"]["type"] == "video"
                        and "semantic" in str(
                            new_matches[index]["track"]["name"] or ""
                        ).casefold())
                    else "source media and source range"
                ),
            })
    return {"source_timeline": final, "staging_timeline": staging,
            "carried": carried,
            "unchanged_unmatched": unchanged_unmatched,
            "safe_replacements": safe_replacements}
