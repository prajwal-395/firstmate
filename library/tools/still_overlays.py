"""Freeze static overlay videos into stills, in place (captain 2026-10-06).

A feedback note on Reel 13 observed that static graphics (post headers,
TV frames) ship as rendered videos of a hardcoded length instead of
still overlays that can cover whatever duration is needed - and that
this holds for all 31 reels. This verb applies that fix reel by reel
without rebuilding: for each overlay item whose media is a static
video, it extracts a still from the approved file itself, replaces the
item at identical transforms and duration (extended to the reel end
only where the original was cut short), and carries clip markers onto
the replacement.

What qualifies, and what never does:

- Overlay rows only: video rows named like the engine's overlay rows
  (`Frame`, `Post Header`, `Semantic`, `Motion Graphics`, `Explainer`,
  `Timed Text`). Camera rows, B-roll, transitions, captions/subtitles
  (image sequences) and guides are never candidates - a static camera
  shot is picture, not an overlay, and a transition element may carry
  audio the still would drop.
- One video stream and no audio stream (ffprobe): an item with an
  audio stream is refused, never muted into a still.
- Static proven by frame compare (first/middle/last frame of the
  placed span byte-identical); anything that animates is left alone.
- No Fusion comps on the item: a graded/composited overlay is craft,
  not a mechanical swap - refused with the reason.
- Disabled items are left alone and reported.

Guards per reel: a `.drt` backup first (the standard recovery
archive), inventory before and after, and an inverse restore when the
after-state differs anywhere but the swapped items. Hand edits,
captions and markers are compared, never assumed.

One pool item per distinct image: the extracted frame is hashed by
pixels (`still_content.hash_pixels`) and the reel's
`still_digest_index` is consulted before importing - the second
identical overlay reuses the first one's pool item at its own span,
and a fresh still lands under a content-addressed name
(`still_<hash>.png`) so it is found next time. A restore never
deletes a reused pool item other swaps still play.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from library.tools import still_content
from library.tools.resolve_organization import is_generated

#: Video rows that carry graphic overlays (never footage, transitions,
#: captions or guides - see module docstring).
OVERLAY_ROW_NAMES = frozenset({
    "Frame", "Post Header", "Semantic", "Motion Graphics", "Explainer",
    "Timed Text",
})

#: Overlay media the swap handles: one video file. Image sequences
#: (captions), stills (already done) and anything else never qualify.
VIDEO_EXTS = (".mov", ".mp4", ".m4v")


class StillOverlayError(RuntimeError):
    """A reel cannot be swapped as planned; nothing was renamed."""


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise StillOverlayError(
            "ffmpeg is not on PATH - the static proof needs it, and an "
            "unproven overlay is left alone, never swapped on a guess")
    return path


def _probe_streams(path: str) -> tuple[int, int]:
    """(video streams, audio streams) of `path`; raises when unreadable."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type", "-of", "json", path],
        capture_output=True, encoding="utf-8", check=False).stdout
    try:
        streams = json.loads(out).get("streams") or []
    except ValueError as exc:
        raise StillOverlayError(
            f"ffprobe could not read {path!r} - leaving it alone") from exc
    kinds = [stream.get("codec_type") for stream in streams]
    if not kinds:
        raise StillOverlayError(
            f"ffprobe found no streams in {path!r} - leaving it alone")
    return (sum(1 for kind in kinds if kind == "video"),
            sum(1 for kind in kinds if kind == "audio"))


def _grab_frame(path: str, seconds: float, dest: str) -> None:
    done = subprocess.run(
        [_ffmpeg(), "-v", "error", "-y", "-ss", str(seconds),
         "-i", path, "-frames:v", "1", dest],
        capture_output=True, encoding="utf-8", check=False)
    if done.returncode != 0 or not os.path.isfile(dest):
        raise StillOverlayError(
            f"could not extract a frame from {path!r} - leaving it alone")


def is_static_video(path: str, duration_seconds: float) -> bool:
    """True when three frames across `duration_seconds` are byte-identical.

    Raises `StillOverlayError` when the file cannot be probed or read -
    an unproven overlay is refused, never swapped on a guess.
    """
    _ffmpeg()
    video, audio = _probe_streams(path)
    if video != 1 or audio != 0:
        raise StillOverlayError(
            f"{path!r} holds {video} video and {audio} audio streams - "
            f"only one silent video stream swaps to a still")
    if duration_seconds <= 0:
        raise StillOverlayError(f"no duration to prove static for {path!r}")
    with tempfile.TemporaryDirectory(prefix="still-proof-") as scratch:
        frames = []
        for fraction in (0.05, 0.5, 0.95):
            dest = os.path.join(scratch, f"{fraction}.png")
            _grab_frame(path, duration_seconds * fraction, dest)
            with open(dest, "rb") as handle:
                frames.append(handle.read())
    return frames[0] == frames[1] == frames[2]


def _media_kind(pool_item) -> str:
    try:
        return pool_item.GetClipProperty("Type") or ""
    except Exception:  # noqa: BLE001 - unreadable is not a kind
        return ""


def plan_swaps(items: list, row_name: str, track_index: int,
               timeline_start: int, timeline_end: int,
               fps: float) -> tuple[list, list, list]:
    """Which overlay items swap, skip, or refuse - no Resolve writes.

    `items` are live timeline items with their pool items attached as
    `(item, pool_item, file_path)` triples. Returns `(swaps, skipped,
    refused)`: a swap names the item, its span and whether it extends
    to the reel end; a skip names an animated or already-still item; a
    refusal names why (with the still-proof error for unreadable
    media). Anything the plan cannot classify is a refusal, never a
    silent skip.
    """
    swaps, skipped, refused = [], [], []
    if row_name not in OVERLAY_ROW_NAMES:
        return swaps, skipped, refused
    for item, pool_item, file_path in items:
        label = f"{row_name} {item.GetName()}@{item.GetStart()}"
        try:
            enabled = item.GetClipEnabled()
        except Exception:  # noqa: BLE001
            enabled = None
        if enabled is not True:
            skipped.append({"item": label, "why": "not enabled"})
            continue
        if _media_kind(pool_item) == "Still":
            skipped.append({"item": label, "why": "already a still"})
            continue
        if not file_path or not os.path.isfile(file_path) or not str(
                file_path).lower().endswith(VIDEO_EXTS):
            skipped.append({"item": label,
                            "why": "not a single video file"})
            continue
        try:
            comps = item.GetFusionCompCount()
        except Exception:  # noqa: BLE001
            comps = -1
        if comps:
            refused.append({"item": label,
                            "why": "carries Fusion comps - craft, not a "
                                   "mechanical swap"})
            continue
        try:
            video, audio = _probe_streams(file_path)
        except StillOverlayError as exc:
            refused.append({"item": label, "why": str(exc)})
            continue
        if audio != 0:
            skipped.append({"item": label,
                            "why": f"carries audio ({audio} audio "
                                   f"stream(s)) - a still would mute it"})
            continue
        if video != 1:
            refused.append({"item": label,
                            "why": f"holds {video} video streams - only "
                                   f"one silent video stream swaps"})
            continue
        start, end = int(item.GetStart()), int(item.GetEnd())
        duration_s = (end - start) / float(fps) if fps > 0 else 0
        try:
            static = is_static_video(file_path, duration_s)
        except StillOverlayError as exc:
            refused.append({"item": label, "why": str(exc)})
            continue
        if not static:
            skipped.append({"item": label, "why": "animates"})
            continue
        spans = [it for (it, _p, _f) in items]
        last_on_row = all(other.GetEnd() <= end for other in spans)
        extends = (start == timeline_start and end < timeline_end
                   and last_on_row)
        swaps.append({
            "label": label,
            "row": row_name,
            "track_index": track_index,
            "item_uid": item.GetUniqueId(),
            "record": [start, end],
            "source_in": int(item.GetLeftOffset()),
            "file_path": file_path,
            "props": dict(item.GetProperty() or {}),
            "markers": dict(item.GetMarkers() or {}),
            "extends": extends,
            "want_end": timeline_end if extends else end,
            "still_path": None,
        })
    return swaps, skipped, refused


def judge_replacement(before: dict, after: dict, swaps: list) -> list:
    """Every difference between two inventories the swaps do not own.

    `before` / `after` map row key -> list of `{name, record, src_off,
    mpi_path, enabled, props, markers}`. Items pair by record start:
    a swapped item must read as its still (the `still_path` the apply
    phase filled in) at its span - extended to the reel end where the
    swap says so - with its markers preserved; anything else must read
    identical. Returns the unexplained differences (empty is clean).
    """
    problems = []
    for key, old_items in before.items():
        new_by_start = {}
        for item in after.get(key, []):
            new_by_start.setdefault(item["record"][0], []).append(item)
        for old_item in old_items:
            Blues = new_by_start.get(old_item["record"][0], [])
            swap = next(
                (entry for entry in swaps
                 if entry["row"] == key and entry["record"][0]
                 == old_item["record"][0]), None)
            if swap is None:
                if len(Blues) != 1 or _sig(Blues) != _sig([old_item]):
                    problems.append(f"{key}: moved without a swap")
                continue
            if len(Blues) != 1:
                problems.append(
                    f"{key}: want one still for {old_item['name']!r}, "
                    f"found {len(Blues)}")
                continue
            new_item = Blues[0]
            want_end = (swap["want_end"] if swap.get("extends")
                        else old_item["record"][1])
            if new_item["record"] != [old_item["record"][0], want_end]:
                problems.append(
                    f"{key}: {new_item['name']!r} spans "
                    f"{new_item['record']}, want "
                    f"[{old_item['record'][0]}, {want_end}]")
            if (new_item["mpi_path"] or "") != (swap.get("still_path")
                                                or ""):
                problems.append(
                    f"{key}: {new_item['name']!r} plays "
                    f"{new_item['mpi_path']!r}, want the frozen still")
            if (new_item.get("props") or {}) != (swap.get("props") or {}):
                problems.append(
                    f"{key}: {new_item['name']!r} transform differs from "
                    f"the replaced overlay")
            for marker in old_item.get("markers", {}).values():
                found = [mark for mark in
                         new_item.get("markers", {}).values()
                         if (mark.get("color"), mark.get("name"),
                             mark.get("note")) == (
                                 marker.get("color"), marker.get("name"),
                                 marker.get("note"))]
                if not found:
                    problems.append(
                        f"{key}: note {marker.get('name')!r} did not "
                        f"survive on {new_item['name']!r}")
    for key in after:
        if key not in before:
            problems.append(f"{key}: row appeared")
    return problems


def _sig(items: list) -> list:
    return [(item["name"], item["record"], item["src_off"],
             item["mpi_path"], item["enabled"],
             sorted((item.get("props") or {}).items()))
            for item in items]




def inventory_row_key(kind: str, index: int, name: str) -> str:
    """The inventory's key for one track - the ONLY spelling the judge
    matches swaps against. `plan_swaps` emits the bare row name for
    human labels, so the call site stamps each swap with this key
    before judging; a swap carrying any other spelling reads as
    "moved without a swap" and refuses (Reel 01, 2026-10-07)."""
    return f"{kind[0].upper()}{index}:{name}"


def key_swaps_for_judge(swaps: list, kind: str, track_index: int,
                       row_name: str) -> list:
    """Stamp planned swaps with the inventory row key the judge matches.

    `plan_swaps` emits the bare row name (human labels); the judge
    compares against inventory keys, so every swap must carry
    `inventory_row_key` before judging - an unstamped swap refuses a
    correct replacement as "moved without a swap".
    """
    key = inventory_row_key(kind, track_index, row_name)
    for swap in swaps:
        swap["row"] = key
    return swaps


def inventory_snapshot(timeline) -> dict:
    """The reel's rows, end and markers as plain data for the diff."""
    rows = {}
    for kind in ("video", "audio"):
        try:
            count = timeline.GetTrackCount(kind) or 0
        except Exception:  # noqa: BLE001
            continue
        for index in range(1, count + 1):
            try:
                name = timeline.GetTrackName(kind, index)
            except Exception:  # noqa: BLE001
                name = ""
            items = []
            for item in timeline.GetItemListInTrack(kind, index) or []:
                mpi = item.GetMediaPoolItem()
                try:
                    clip_markers = item.GetMarkers() or {}
                except Exception:  # noqa: BLE001
                    clip_markers = {}
                try:
                    comps = item.GetFusionCompCount()
                except Exception:  # noqa: BLE001
                    comps = -1
                items.append({
                    "uid": item.GetUniqueId(),
                    "name": item.GetName(),
                    "record": [item.GetStart(), item.GetEnd()],
                    "src_off": item.GetLeftOffset(),
                    "duration": item.GetDuration(),
                    "enabled": item.GetClipEnabled(),
                    "props": dict(item.GetProperty() or {}),
                    "fusion_comps": comps,
                    "mpi_path": (mpi.GetClipProperty("File Path")
                                 if mpi is not None else None),
                    "pool_uid": (mpi.GetUniqueId() if mpi is not None
                                 else None),
                    "clip_markers": clip_markers,
                })
            rows[f"{inventory_row_key(kind, index, name)}"] = items
    return {"rows": rows, "end": [timeline.GetStartFrame(),
                                  timeline.GetEndFrame()],
            "timeline_markers": timeline.GetMarkers() or {}}


def _pool_file(pool_item):
    try:
        return pool_item.GetClipProperty("File Path")
    except Exception:  # noqa: BLE001 - unreadable is not a path
        return None


def still_digest_index(pool, project_folder: str) -> dict:
    """One pool item per distinct still image, keyed by pixel hash.

    Built once per reel before any swap lands: every pooled Still on a
    Ren-generated `.png` is hashed once (`still_content.hash_pixels`,
    which hashes decoded pixels, not file bytes, so two exports of the
    same frame merge). Captain-imported stills are never indexed - a
    swap re-points onto pool media, and his media is not ours to
    re-point onto. `_apply_swap` consults this before importing, so
    the second identical overlay reuses the first one's pool item
    instead of importing another.
    """
    index: dict = {}
    hashed: dict = {}
    stack = [pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        try:
            clips = folder.GetClipList() or ()
            subs = folder.GetSubFolderList() or ()
        except Exception:  # noqa: BLE001 - unreadable is not indexed
            continue
        for clip in clips:
            if _media_kind(clip) != "Still":
                continue
            path = _pool_file(clip) or ""
            if (not path.lower().endswith(".png")
                    or not is_generated(path, project_folder)):
                continue
            if path in hashed:
                digest = hashed[path]
            else:
                try:
                    digest = still_content.hash_pixels(path)
                except still_content.DedupeRefused:
                    continue
                hashed[path] = digest
            index.setdefault(digest, clip)
    return index


def _find_folder(pool, folder, path):
    for clip in folder.GetClipList() or ():
        try:
            if (clip.GetClipProperty("File Path") or "") == path:
                return folder
        except Exception:  # noqa: BLE001
            continue
    for sub in folder.GetSubFolderList() or ():
        found = _find_folder(pool, sub, path)
        if found is not None:
            return found
    return None


def swap_reel_overlays(project, timeline_name: str, project_folder: str,
                       *, dry_run=False) -> dict:
    """Freeze one reel's static overlays in place. See module docstring.

    Plan everything first (any refusal stops the reel before any
    write), then backup (.drt recovery archive), apply, and diff the
    after-state against the plan: anything unexplained triggers the
    inverse restore. Returns the per-reel report: swapped / skipped /
    refused (+restored).
    """
    from library.tools.resolve_lock import (
        assert_current_timeline, resolve_lease)
    from library.tools.marker_feedback import connect_resolve
    from library.tools.versions.store import export_timeline_recovery_archive

    report: dict = {"reel": timeline_name, "swapped": [], "skipped": [],
                    "refused": None, "restored": False}
    with resolve_lease(f"still-overlays {timeline_name}",
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
        start_frame = timeline.GetStartFrame()
        timeline_end = timeline.GetEndFrame()
        pool = project.GetMediaPool()
        plans, skipped, refused = [], [], []
        for index in range(1, (timeline.GetTrackCount("video") or 0)
                           + 1):
            try:
                row_name = timeline.GetTrackName("video", index)
            except Exception:  # noqa: BLE001
                continue
            if row_name not in OVERLAY_ROW_NAMES:
                continue
            triples = []
            for item in timeline.GetItemListInTrack(
                    "video", index) or []:
                mpi = item.GetMediaPoolItem()
                triples.append((
                    item, mpi,
                    mpi.GetClipProperty("File Path")
                    if mpi is not None else None))
            swaps, skipped_rows, refused_rows = plan_swaps(
                triples, row_name, index, start_frame, timeline_end,
                fps)
            key_swaps_for_judge(swaps, "video", index, row_name)
            plans.extend(swaps)
            skipped.extend({**entry, "row": row_name}
                           for entry in skipped_rows)
            refused.extend({**entry, "row": row_name}
                           for entry in refused_rows)
        report["skipped"] = skipped
        if refused:
            report["refused"] = "; ".join(
                f"{entry['row']} {entry['item']}: {entry['why']}"
                for entry in refused)
            return report
        if dry_run:
            report["planned"] = [swap["label"] for swap in plans]
            return report
        assert_current_timeline(project, timeline)
        before = inventory_snapshot(timeline)
        archive = export_timeline_recovery_archive(
            resolve, timeline, timeline_name, project_folder,
            label=f"still-overlays {timeline_name}")
        if not archive.get("archived"):
            report["refused"] = (
                f"no .drt backup ({archive.get('reason')}) - "
                f"refusing before any write")
            return report
        report["drt_backup"] = archive.get("path")
        reuse = still_digest_index(pool, project_folder)
        applied = []
        try:
            for swap in plans:
                applied.append(_apply_swap(
                    resolve, project, pool, timeline, fps, swap,
                    project_folder, timeline_name, reuse))
            after = inventory_snapshot(timeline)
            for swap, got in zip(plans, applied):
                swap["still_path"] = got["still_path"]
            problems = judge_replacement(
                _slim_rows(before), _slim_rows(after), plans)
            before_marks = _mark_keys(before["timeline_markers"])
            after_marks = _mark_keys(after["timeline_markers"])
            if before_marks != after_marks:
                problems.append("timeline markers moved")
            if problems:
                _restore_swaps(project, pool, timeline, fps, plans,
                               applied, project_folder, timeline_name)
                report["restored"] = True
                report["refused"] = "; ".join(problems)
                return report
            report["swapped"] = [
                f"{swap['label']} -> "
                f"{swap['still_path'].rsplit('/', 1)[-1]}"
                f"@{swap['record'][0]}..{swap['want_end']}"
                f"{' (reused)' if got.get('reused') else ''}"
                for swap, got in zip(plans, applied)]
        except StillOverlayError as exc:
            _restore_swaps(project, pool, timeline, fps, plans,
                           applied, project_folder, timeline_name)
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


def _slim_rows(snapshot: dict) -> dict:
    return {key: [_slim_row(item) for item in items]
            for key, items in snapshot.get("rows", {}).items()}


def _slim_row(item: dict) -> dict:
    return {"name": item["name"], "record": item["record"],
            "src_off": item["src_off"], "mpi_path": item["mpi_path"],
            "enabled": item["enabled"], "props": item["props"],
            "markers": item.get("clip_markers", {})}


def _mark_keys(markers) -> set:
    out = set()
    for frame, marker in (markers or {}).items():
        if not isinstance(marker, dict):
            continue
        out.add((str(frame), marker.get("color"), marker.get("name"),
                 marker.get("note")))
    return out


def _items_with_path(items, path: str, record_start=None) -> list:
    """Timeline items playing `path`, narrowed to `record_start` when
    given. Two swaps from one source file share the still path (Reel 07
    carries one tv_frame across three Frame items), so a path-only
    lookup reads its siblings back and refuses a correct placement -
    the record start is part of the identity."""
    out = []
    for item in items or ():
        try:
            mpi = item.GetMediaPoolItem()
            hit = (mpi is not None
                   and (mpi.GetClipProperty("File Path") or "") == path)
        except Exception:  # noqa: BLE001 - unreadable is not a match
            hit = False
        if not hit:
            continue
        if record_start is not None:
            try:
                if item.GetStart() != record_start:
                    continue
            except Exception:  # noqa: BLE001 - unreadable is not a match
                continue
        out.append(item)
    return out


def _apply_swap(resolve, project, pool, timeline, fps: float, swap: dict,
                project_folder: str, timeline_name: str,
                reuse: dict | None = None) -> dict:
    """Replace one overlay item with its frozen still. Returns a receipt.

    `reuse` is the reel's `still_digest_index`: when the extracted
    frame's pixels already have a pool item - the TV frame on an
    earlier reel, or an earlier swap on this one - the swap re-points
    at that item instead of importing another still.
    """
    from library.tools.still_placement import place_still_exact
    from library.tools.transform_write_log import set_property

    reuse = reuse if reuse is not None else {}
    duration_s = ((swap["record"][1] - swap["record"][0]) / float(fps)
                  if fps > 0 else 0)
    with tempfile.TemporaryDirectory(prefix="still-swap-") as scratch:
        still = os.path.join(
            scratch,
            os.path.basename(swap["file_path"]).rsplit(".", 1)[0]
            + "_still.png")
        _grab_frame(swap["file_path"], max(0.0, duration_s / 2.0), still)
        try:
            digest = still_content.hash_pixels(still)
        except still_content.DedupeRefused as exc:
            raise StillOverlayError(
                f"{swap['label']}: cannot hash the frozen still - "
                f"{exc}") from exc
        hit = reuse.get(digest)
        if (hit is not None and _media_kind(hit) == "Still"
                and os.path.isfile(_pool_file(hit) or "")):
            still_item = hit
            final_path = _pool_file(hit) or ""
            reused = True
        else:
            home = _find_folder(pool, pool.GetRootFolder(),
                                swap["file_path"])
            if home is None:
                raise StillOverlayError(
                    f"{swap['label']}: pool folder not found - refusing")
            previous = pool.GetCurrentFolder()
            try:
                pool.SetCurrentFolder(home)
                imported = pool.ImportMedia([still])
            finally:
                if previous is not None:
                    pool.SetCurrentFolder(previous)
            if not imported:
                raise StillOverlayError(
                    f"{swap['label']}: Resolve would not import the still")
            still_item = imported[0]
            if _media_kind(still_item) != "Still":
                raise StillOverlayError(
                    f"{swap['label']}: imported file is not a Still")
            final_path = os.path.join(
                os.path.dirname(swap["file_path"]),
                still_content.content_still_filename(digest))
            if os.path.isfile(final_path):
                try:
                    parked = still_content.hash_pixels(final_path)
                except still_content.DedupeRefused as exc:
                    raise StillOverlayError(
                        f"{swap['label']}: {final_path!r} is already here "
                        f"and unreadable - refusing to overwrite it: "
                        f"{exc}") from exc
                if parked != digest:
                    raise StillOverlayError(
                        f"{swap['label']}: {final_path!r} is already here "
                        f"with different pixels - refusing to overwrite it")
            else:
                shutil.copyfile(still, final_path)
            if not still_item.ReplaceClip(final_path):
                raise StillOverlayError(
                    f"{swap['label']}: still would not re-point beside "
                    f"its source")
            reuse[digest] = still_item
            reused = False
        old = None
        for item in timeline.GetItemListInTrack(
                "video", swap["track_index"]) or ():
            if item.GetUniqueId() == swap["item_uid"]:
                old = item
                break
        if old is None:
            raise StillOverlayError(
                f"{swap['label']}: overlay item is gone mid-run")
        if not timeline.DeleteClips([old], False):
            raise StillOverlayError(
                f"{swap['label']}: Resolve would not delete the video item")
        want_len = swap["want_end"] - swap["record"][0]
        place_still_exact(
            resolve, project, pool, timeline, still_item, want_len,
            swap["record"][0], swap["track_index"], fps,
            timeline_name=timeline_name)
        placed = _items_with_path(
            timeline.GetItemListInTrack("video", swap["track_index"]),
            final_path, swap["record"][0])
        if len(placed) != 1:
            raise StillOverlayError(
                f"{swap['label']}: row holds {len(placed)} still(s) "
                f"after placing - refusing")
        new = placed[0]
        current_props = dict(new.GetProperty() or {})
        for key, value in (swap["props"] or {}).items():
            if key in current_props and current_props[key] == value:
                continue
            if not set_property(
                    new, key, value,
                    item_identity={
                        "source_file": final_path,
                        "track": f"V{swap['track_index']}",
                        "record_frame": swap["record"][0],
                    },
                    project_folder=project_folder, project=None,
                    timeline_name=timeline_name + " (still overlay)"):
                raise StillOverlayError(
                    f"{swap['label']}: refused {key}={value} on the still")
        try:
            new_src_off = int(new.GetLeftOffset())
        except Exception:  # noqa: BLE001
            raise StillOverlayError(
                f"{swap['label']}: the placed still has no source offset")
        for frame, marker in (swap["markers"] or {}).items():
            if not isinstance(marker, dict):
                continue
            try:
                record_frame = int(frame)
            except (TypeError, ValueError):
                continue
            source_frame = (new_src_off
                            + (record_frame - swap["record"][0]))
            if not new.AddMarker(
                    source_frame, marker.get("color") or "",
                    marker.get("name") or "", marker.get("note") or "",
                    int(marker.get("duration") or 1)):
                raise StillOverlayError(
                    f"{swap['label']}: note {marker.get('name')!r} did "
                    f"not carry onto the still")
        back = _items_with_path(
            timeline.GetItemListInTrack("video", swap["track_index"]),
            final_path, swap["record"][0])
        if len(back) != 1:
            raise StillOverlayError(
                f"{swap['label']}: still unreadable after transforms")
        return {"swap": swap["label"], "uid": back[0].GetUniqueId(),
                "still_path": final_path, "reused": reused,
                "record": [back[0].GetStart(), back[0].GetEnd()]}


def _restore_swaps(project, pool, timeline, fps, plans, applied,
                   project_folder, timeline_name):
    """Reverse applied swaps: stills out, videos back with notes.

    Raises StillOverlayError when the reversal itself does not read
    back - a half-restored reel stops here with its .drt backup named,
    never silently.
    """
    from library.tools.transform_write_log import set_property

    for swap in reversed(plans):
        got = next((entry for entry in applied
                    if entry.get("swap") == swap["label"]), None)
        if got is None:
            continue
        still_path = got.get("still_path") or ""
        pooled = None
        for folder_items in ([pool.GetRootFolder()],):
            stack = list(folder_items)
            while stack and pooled is None:
                folder = stack.pop()
                for clip in folder.GetClipList() or ():
                    try:
                        if (clip.GetClipProperty("File Path") or ""
                                == still_path):
                            pooled = clip
                            break
                    except Exception:  # noqa: BLE001
                        continue
                stack.extend(folder.GetSubFolderList() or ())
        doomed = _items_with_path(
            timeline.GetItemListInTrack("video", swap["track_index"]),
            still_path, swap["record"][0])
        if doomed and not timeline.DeleteClips(doomed, False):
            raise StillOverlayError(
                f"{timeline_name}: restore would not delete "
                f"{still_path!r}")
        from library.tools.reel_build import pool_item_for
        mov = pool_item_for(pool, swap["file_path"])
        if mov is None:
            raise StillOverlayError(
                f"{timeline_name}: restore cannot find "
                f"{swap['file_path']!r} pooled")
        placed = pool.AppendToTimeline([{
            "mediaPoolItem": mov,
            "startFrame": swap["source_in"],
            "endFrame": (swap["source_in"] + swap["record"][1]
                         - swap["record"][0]),
            "mediaType": 1,
            "trackIndex": swap["track_index"],
            "recordFrame": swap["record"][0],
        }])
        if not placed:
            raise StillOverlayError(
                f"{timeline_name}: restore would not place "
                f"{swap['file_path']!r}")
        new = placed[0]
        current_props = dict(new.GetProperty() or {})
        for key, value in (swap["props"] or {}).items():
            if key in current_props and current_props[key] == value:
                continue
            if not set_property(
                    new, key, value,
                    item_identity={
                        "source_file": swap["file_path"],
                        "track": f"V{swap['track_index']}",
                        "record_frame": swap["record"][0],
                    },
                    project_folder=project_folder, project=None,
                    timeline_name=timeline_name + " (still restore)"):
                raise StillOverlayError(
                    f"{timeline_name}: restore refused {key}={value}")
        try:
            new_src_off = int(new.GetLeftOffset())
        except Exception:  # noqa: BLE001
            raise StillOverlayError(
                f"{timeline_name}: restored item has no source offset")
        for frame, marker in (swap["markers"] or {}).items():
            if not isinstance(marker, dict):
                continue
            try:
                record_frame = int(frame)
            except (TypeError, ValueError):
                continue
            if not new.AddMarker(
                    new_src_off + (record_frame - swap["record"][0]),
                    marker.get("color") or "", marker.get("name") or "",
                    marker.get("note") or "",
                    int(marker.get("duration") or 1)):
                raise StillOverlayError(
                    f"{timeline_name}: restore lost note "
                    f"{marker.get('name')!r}")
        if pooled is not None and not got.get("reused"):
            if not _still_played_anywhere(project, timeline, still_path,
                                          swap):
                pool.DeleteClips([pooled])


def _still_played_anywhere(project, timeline, still_path: str,
                           exempt: dict) -> bool:
    """True when any item outside the exempt swap's span plays the still.

    A reused still is one pool item several swaps share, so the
    restore of one swap must not delete the pool item the others
    still play. The exempt swap's own item is already deleted by the
    time this runs; any other hit keeps the pool item pooled (it is
    unreferenced pool debris `ren gc` owns, not this restore).
    """
    try:
        tracks = timeline.GetTrackCount("video") or 0
    except Exception:  # noqa: BLE001
        return True
    for index in range(1, tracks + 1):
        try:
            items = timeline.GetItemListInTrack("video", index) or ()
        except Exception:  # noqa: BLE001
            continue
        for item in items:
            try:
                mpi = item.GetMediaPoolItem()
                hit = (mpi is not None
                       and (mpi.GetClipProperty("File Path") or "")
                       == still_path)
            except Exception:  # noqa: BLE001
                continue
            if not hit:
                continue
            try:
                start = item.GetStart()
            except Exception:  # noqa: BLE001
                return True
            if (index == exempt.get("track_index")
                    and start == (exempt.get("record") or [None])[0]):
                continue
            return True
    return False
