"""Point identical stills at one pool item, in place (captain 2026-10-07).

Tonight's `still-overlays` pass extracted its own still per overlay item,
so the TV frame - the same graphic on every reel - now exists as many
separate stills instead of one reused one. This verb collapses those
duplicates reel by reel without rebuilding: for each set of timeline
items drawing pixel-identical stills, every item but the canonical one
is re-placed onto the canonical pool item at its identical record span,
transforms and markers.

What qualifies, and what never does:

- Overlay rows only, and stills only: a row outside
  `still_overlays.OVERLAY_ROW_NAMES` is never walked, and only items
  whose pool item reads Type `Still` on a `.png` file are candidates -
  videos, image sequences and anything with audio are someone else's
  act.
- Ren-generated files only (`resolve_organization.is_generated`): a
  still the captain imported is his media, and re-pointing it would
  re-file his pool - refused by leaving it out of the plan entirely.
- Identical pixels (`still_content.hash_pixels`), identical spans:
  `still_content.plan_dedupe` refuses a group whose items sit at
  different spans, because a re-point keeps the record span and a
  still at the wrong span would move the cut.

Guards per reel: a `.drt` backup first (the standard recovery
archive), inventory before and after, and an inverse restore when the
after-state differs anywhere but the re-pointed items' pool file.
Hand edits, captions and markers are compared, never assumed. The
superseded duplicate pool items stay in the pool - `ren gc` collects
them into trash, which is the recoverable half that owns deletion.
"""

from __future__ import annotations

from library.tools import still_content
from library.tools.resolve_organization import is_generated
from library.tools.still_overlays import (
    OVERLAY_ROW_NAMES,
    inventory_row_key,
    inventory_snapshot,
)

STILL_EXTS = (".png",)


class DedupeRefused(still_content.DedupeRefused):
    """A reel cannot be deduped as planned; nothing was renamed."""


def _media_kind(pool_item) -> str:
    try:
        return pool_item.GetClipProperty("Type") or ""
    except Exception:  # noqa: BLE001 - unreadable is not a kind
        return ""


def _pool_file(pool_item):
    try:
        return pool_item.GetClipProperty("File Path")
    except Exception:  # noqa: BLE001
        return None


def collect_reel_stills(timeline, timeline_name: str,
                        project_folder: str) -> list:
    """Every dedupe candidate on one reel as plain dicts for the plan."""
    items = []
    try:
        video_tracks = timeline.GetTrackCount("video") or 0
    except Exception:  # noqa: BLE001
        return items
    for index in range(1, video_tracks + 1):
        try:
            row_name = timeline.GetTrackName("video", index)
        except Exception:  # noqa: BLE001
            continue
        if row_name not in OVERLAY_ROW_NAMES:
            continue
        try:
            row_items = timeline.GetItemListInTrack("video", index) or []
        except Exception:  # noqa: BLE001
            continue
        for item in row_items:
            try:
                mpi = item.GetMediaPoolItem()
            except Exception:  # noqa: BLE001
                continue
            if mpi is None or _media_kind(mpi) != "Still":
                continue
            file_path = _pool_file(mpi)
            if (not file_path
                    or not str(file_path).lower().endswith(STILL_EXTS)):
                continue
            if not is_generated(str(file_path), project_folder):
                continue
            try:
                digest = still_content.hash_pixels(str(file_path))
            except still_content.DedupeRefused:
                digest = ""
            try:
                markers = item.GetMarkers() or {}
            except Exception:  # noqa: BLE001
                markers = {}
            items.append({
                "timeline": timeline_name,
                "row": inventory_row_key("video", index, row_name or ""),
                "track_index": index,
                "item_uid": item.GetUniqueId(),
                "record": [int(item.GetStart()), int(item.GetEnd())],
                "duration": int(item.GetDuration()),
                "pool_uid": mpi.GetUniqueId(),
                "file_path": str(file_path),
                "digest": digest,
                "props": dict(item.GetProperty() or {}),
                "markers": {str(k): dict(v) for k, v in markers.items()
                            if isinstance(v, dict)},
            })
    return items


def _reel_row_names(timeline) -> list:
    """Every video row name on the reel, for the dry-run census."""
    names = []
    try:
        tracks = timeline.GetTrackCount("video") or 0
    except Exception:  # noqa: BLE001
        return names
    for index in range(1, tracks + 1):
        try:
            names.append(timeline.GetTrackName("video", index) or "")
        except Exception:  # noqa: BLE001
            names.append("")
    return names


def judge_dedupe(before: dict, after: dict, repoints: list) -> list:
    """Every difference between two inventories the repoints do not own.

    `before` / `after` map row key -> list of slim items (`name`,
    `record`, `mpi_path`, `pool_uid`, `props`, `markers`). A re-pointed
    item must read back at its identical span, transforms and markers,
    playing the canonical file; anything else must read identical.
    Returns the unexplained differences (empty is clean).
    """
    problems = []
    want = {(entry["row"], entry["record"][0]): entry for entry in repoints}
    for key, old_items in before.items():
        new_by_start = {}
        for item in after.get(key, []):
            new_by_start.setdefault(tuple(item["record"][:1]), []).append(
                item)
        for old_item in old_items:
            entry = want.get((key, old_item["record"][0]))
            found = new_by_start.get((old_item["record"][0],), [])
            if entry is None:
                if (len(found) != 1
                        or _sig(found[0]) != _sig(old_item)):
                    problems.append(f"{key}: moved without a re-point")
                continue
            if len(found) != 1:
                problems.append(
                    f"{key}: want one still for {old_item['name']!r}, "
                    f"found {len(found)}")
                continue
            new_item = found[0]
            if (new_item["record"] != old_item["record"]
                    or (new_item.get("props") or {})
                    != (old_item.get("props") or {})
                    or (new_item.get("markers") or {})
                    != (old_item.get("markers") or {})):
                problems.append(
                    f"{key}: {new_item['name']!r} moved - a re-point "
                    f"keeps span, transforms and markers")
            if new_item["mpi_path"] != entry["canonical_file"]:
                problems.append(
                    f"{key}: {new_item['name']!r} plays "
                    f"{new_item['mpi_path']!r}, want "
                    f"{entry['canonical_file']!r}")
    for key in after:
        if key not in before:
            problems.append(f"{key}: row appeared")
    return problems


def _sig(item: dict) -> tuple:
    return (item["name"], tuple(item["record"]), item["mpi_path"],
            item.get("pool_uid"),
            sorted((item.get("props") or {}).items()))


def _slim(item: dict) -> dict:
    return {"name": item["name"], "record": item["record"],
            "mpi_path": item["mpi_path"], "pool_uid": item.get("pool_uid"),
            "props": item.get("props") or {},
            "markers": item.get("clip_markers") or {}}


def _slim_rows(snapshot: dict) -> dict:
    return {key: [_slim(item) for item in items]
            for key, items in snapshot.get("rows", {}).items()}


def dedupe_reel_stills(project, timeline_name: str, project_folder: str,
                       *, dry_run=False) -> dict:
    """Collapse one reel's duplicate stills onto their canonical items.

    Plan everything first (any refusal stops the reel before any
    write), then backup (.drt recovery archive), apply, and diff the
    after-state against the plan: anything unexplained triggers the
    inverse restore. Returns the per-reel report: repointed / skipped /
    refused (+restored).
    """
    from library.tools.resolve_lock import (
        assert_current_timeline, resolve_lease)
    from library.tools.marker_feedback import connect_resolve
    from library.tools.versions.store import export_timeline_recovery_archive

    report: dict = {"reel": timeline_name, "repointed": [],
                    "skipped": [], "refused": None, "restored": False,
                    "stills": 0, "rows": []}
    with resolve_lease(f"dedupe-stills {timeline_name}",
                       exclusive=True, timeout=600):
        resolve = connect_resolve()
        manager = resolve.GetProjectManager()
        project = manager.GetCurrentProject()
        entry = project.GetCurrentTimeline()
        entry_id = (entry.GetUniqueId() if entry is not None else None)
        timeline = None
        for index in range(1, int(project.GetTimelineCount() or 0)
                           + 1):
            candidate = project.GetTimelineByIndex(index)
            if candidate and candidate.GetName() == timeline_name:
                timeline = candidate
                break
        if timeline is None:
            report["refused"] = f"no timeline {timeline_name!r}"
            return report
        try:
            fps = float(timeline.GetSetting("timelineFrameRate") or 0)
        except Exception:  # noqa: BLE001
            fps = 0.0
        if fps <= 0:
            report["refused"] = "unreadable timeline rate"
            return report
        candidates = collect_reel_stills(timeline, timeline_name,
                                         project_folder)
        report["stills"] = len(candidates)
        report["rows"] = _reel_row_names(timeline)
        try:
            plan = still_content.plan_dedupe(candidates)
        except still_content.DedupeRefused as exc:
            report["refused"] = str(exc)
            return report
        report["skipped"] = plan["skipped"]
        by_item = {(item["pool_uid"], item["record"][0]): item
                   for item in candidates}
        repoints = []
        for entry in plan["repoints"]:
            source = by_item.get((entry["pool_uid"],
                                  entry["record"][0]))
            if source is None:
                report["refused"] = (
                    f"plan names {entry['file_path']!r}@"
                    f"{entry['record']} the reel no longer holds - "
                    f"refusing a stale plan")
                return report
            repoints.append({**entry,
                             "row": source["row"],
                             "track_index": source["track_index"],
                             "item_uid": source["item_uid"]})
        if dry_run:
            report["planned"] = [
                f"{entry['timeline']} {entry['file_path']}@"
                f"{entry['record']} -> {entry['canonical_file']}"
                for entry in repoints]
            return report
        if not repoints:
            return report
        assert_current_timeline(project, timeline)
        before = inventory_snapshot(timeline)
        archive = export_timeline_recovery_archive(
            resolve, timeline, timeline_name, project_folder,
            label=f"dedupe-stills {timeline_name}")
        if not archive.get("archived"):
            report["refused"] = (
                f"no .drt backup ({archive.get('reason')}) - "
                f"refusing before any write")
            return report
        report["drt_backup"] = archive.get("path")
        pool = project.GetMediaPool()
        applied = []
        try:
            for entry in repoints:
                applied.append(_apply_repoint(
                    resolve, project, pool, timeline, fps, entry,
                    project_folder, timeline_name))
            after = inventory_snapshot(timeline)
            problems = judge_dedupe(
                _slim_rows(before), _slim_rows(after), repoints)
            before_marks = _mark_keys(before["timeline_markers"])
            after_marks = _mark_keys(after["timeline_markers"])
            if before_marks != after_marks:
                problems.append("timeline markers moved")
            if problems:
                _restore_repoints(
                    project, pool, timeline, fps, repoints, applied,
                    project_folder, timeline_name)
                report["restored"] = True
                report["refused"] = "; ".join(problems)
                return report
            report["repointed"] = [
                f"{entry['file_path']}@{entry['record']} -> "
                f"{entry['canonical_file']}"
                for entry in repoints]
        except DedupeRefused as exc:
            _restore_repoints(
                project, pool, timeline, fps, repoints, applied,
                project_folder, timeline_name)
            report["restored"] = True
            report["refused"] = str(exc)
            return report
        finally:
            current = project.GetCurrentTimeline()
            if (entry_id is not None and current is not None
                    and current.GetUniqueId() != entry_id):
                for index in range(
                        1, int(project.GetTimelineCount() or 0) + 1):
                    candidate = project.GetTimelineByIndex(index)
                    if (candidate is not None
                            and candidate.GetUniqueId() == entry_id):
                        assert_current_timeline(project, candidate)
                        break
            back = project.GetCurrentTimeline()
            report["cursor"] = back.GetName() if back else None
    return report


def _mark_keys(markers) -> set:
    out = set()
    for frame, marker in (markers or {}).items():
        if not isinstance(marker, dict):
            continue
        out.add((str(frame), marker.get("color"), marker.get("name"),
                 marker.get("note")))
    return out


def _pool_item_by_uid(pool, uid: str):
    stack = [pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        for clip in folder.GetClipList() or ():
            try:
                if clip.GetUniqueId() == uid:
                    return clip
            except Exception:  # noqa: BLE001
                continue
        stack.extend(folder.GetSubFolderList() or ())
    return None


def _items_with_uid(items, uid: str) -> list:
    out = []
    for item in items or ():
        try:
            if item.GetUniqueId() == uid:
                out.append(item)
        except Exception:  # noqa: BLE001
            continue
    return out


def _items_with_path_start(items, path: str, record_start: int) -> list:
    out = []
    for item in items or ():
        try:
            mpi = item.GetMediaPoolItem()
            hit = (mpi is not None
                   and (mpi.GetClipProperty("File Path") or "") == path
                   and item.GetStart() == record_start)
        except Exception:  # noqa: BLE001
            hit = False
        if hit:
            out.append(item)
    return out


def _apply_repoint(resolve, project, pool, timeline, fps: float,
                   entry: dict, project_folder: str,
                   timeline_name: str) -> dict:
    """Move one item onto the canonical pool item. Returns a receipt."""
    from library.tools.still_placement import place_still_exact
    from library.tools.transform_write_log import set_property

    canonical = _pool_item_by_uid(pool, entry["canonical_pool_uid"])
    if canonical is None:
        raise DedupeRefused(
            f"{timeline_name}: canonical still "
            f"{entry['canonical_file']!r} is gone from the pool mid-run")
    if (_pool_file(canonical) or "") != entry["canonical_file"]:
        raise DedupeRefused(
            f"{timeline_name}: canonical pool item now plays "
            f"{_pool_file(canonical)!r}, want "
            f"{entry['canonical_file']!r} - refusing a stale plan")
    if _media_kind(canonical) != "Still":
        raise DedupeRefused(
            f"{timeline_name}: canonical item is not a Still")
    old = None
    for item in timeline.GetItemListInTrack(
            "video", entry["track_index"]) or ():
        if item.GetUniqueId() == entry.get("item_uid"):
            old = item
            break
    if old is None:
        track_items = timeline.GetItemListInTrack(
            "video", entry["track_index"]) or ()
        narrowed = _items_with_path_start(
            track_items, entry["file_path"], entry["record"][0])
        if len(narrowed) != 1:
            raise DedupeRefused(
                f"{timeline_name}: duplicate item "
                f"{entry['file_path']!r}@{entry['record'][0]} is gone "
                f"mid-run")
        old = narrowed[0]
    try:
        props = dict(old.GetProperty() or {})
        markers = dict(old.GetMarkers() or {})
    except Exception as exc:  # noqa: BLE001
        raise DedupeRefused(
            f"{timeline_name}: duplicate item unreadable mid-run: "
            f"{exc}") from exc
    if not timeline.DeleteClips([old], False):
        raise DedupeRefused(
            f"{timeline_name}: Resolve would not delete the duplicate "
            f"item")
    want_len = entry["record"][1] - entry["record"][0]
    place_still_exact(
        resolve, project, pool, timeline, canonical, want_len,
        entry["record"][0], entry["track_index"], fps,
        timeline_name=timeline_name)
    placed = _items_with_path_start(
        timeline.GetItemListInTrack("video", entry["track_index"]),
        entry["canonical_file"], entry["record"][0])
    if len(placed) != 1:
        raise DedupeRefused(
            f"{timeline_name}: row holds {len(placed)} canonical still(s) "
            f"after placing - refusing")
    new = placed[0]
    current_props = dict(new.GetProperty() or {})
    for key, value in (entry.get("props") or props).items():
        if key in current_props and current_props[key] == value:
            continue
        if not set_property(
                new, key, value,
                item_identity={
                    "source_file": entry["canonical_file"],
                    "track": f"V{entry['track_index']}",
                    "record_frame": entry["record"][0],
                },
                project_folder=project_folder, project=None,
                timeline_name=timeline_name + " (still dedupe)"):
            raise DedupeRefused(
                f"{timeline_name}: refused {key}={value} on the "
                f"canonical still")
    try:
        new_src_off = int(new.GetLeftOffset())
    except Exception:  # noqa: BLE001
        raise DedupeRefused(
            f"{timeline_name}: the placed still has no source offset")
    for frame, marker in ((entry.get("markers") or markers) or {}).items():
        if not isinstance(marker, dict):
            continue
        try:
            record_frame = int(frame)
        except (TypeError, ValueError):
            continue
        source_frame = new_src_off + (record_frame - entry["record"][0])
        if not new.AddMarker(
                source_frame, marker.get("color") or "",
                marker.get("name") or "", marker.get("note") or "",
                int(marker.get("duration") or 1)):
            raise DedupeRefused(
                f"{timeline_name}: note {marker.get('name')!r} did not "
                f"carry onto the canonical still")
    back = _items_with_path_start(
        timeline.GetItemListInTrack("video", entry["track_index"]),
        entry["canonical_file"], entry["record"][0])
    if len(back) != 1:
        raise DedupeRefused(
            f"{timeline_name}: still unreadable after transforms")
    return {"repoint": entry["file_path"], "uid": back[0].GetUniqueId(),
            "record": [back[0].GetStart(), back[0].GetEnd()]}


def _restore_repoints(project, pool, timeline, fps, repoints, applied,
                      project_folder, timeline_name):
    """Reverse applied re-points: canonical stills out, originals back.

    Raises DedupeRefused when the reversal itself does not read back -
    a half-restored reel stops here with its .drt backup named, never
    silently. The duplicate pool items are never deleted here; `ren
    gc` owns that half.
    """
    from library.tools.transform_write_log import set_property
    from library.tools.still_placement import place_still_exact
    from library.tools.marker_feedback import connect_resolve

    resolve = connect_resolve()
    for entry in reversed(repoints):
        got = next((item for item in applied
                    if item.get("repoint") == entry["file_path"]
                    and item.get("record", [None])[0]
                    == entry["record"][0]), None)
        if got is None:
            continue
        original = _pool_item_by_uid(pool, entry["pool_uid"])
        if original is None:
            raise DedupeRefused(
                f"{timeline_name}: restore cannot find the duplicate "
                f"{entry['file_path']!r} pooled")
        doomed = _items_with_path_start(
            timeline.GetItemListInTrack("video", entry["track_index"]),
            entry["canonical_file"], entry["record"][0])
        if doomed and not timeline.DeleteClips(doomed, False):
            raise DedupeRefused(
                f"{timeline_name}: restore would not delete "
                f"{entry['canonical_file']!r}")
        want_len = entry["record"][1] - entry["record"][0]
        place_still_exact(
            resolve, project, pool, timeline, original, want_len,
            entry["record"][0], entry["track_index"], fps,
            timeline_name=timeline_name)
        placed = _items_with_path_start(
            timeline.GetItemListInTrack("video", entry["track_index"]),
            entry["file_path"], entry["record"][0])
        if len(placed) != 1:
            raise DedupeRefused(
                f"{timeline_name}: restore would not place "
                f"{entry['file_path']!r}")
        new = placed[0]
        current_props = dict(new.GetProperty() or {})
        for key, value in (entry.get("props") or {}).items():
            if key in current_props and current_props[key] == value:
                continue
            if not set_property(
                    new, key, value,
                    item_identity={
                        "source_file": entry["file_path"],
                        "track": f"V{entry['track_index']}",
                        "record_frame": entry["record"][0],
                    },
                    project_folder=project_folder, project=None,
                    timeline_name=timeline_name + " (still restore)"):
                raise DedupeRefused(
                    f"{timeline_name}: restore refused {key}={value}")
