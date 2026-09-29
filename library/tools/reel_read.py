"""One way to read a reel: the whole truth about a timeline in a single call.

The incident behind this module: firstmate reported a reel as having no
markers when it had two, because the worker wrote a throwaway probe that
read `Timeline.GetMarkers()` only and trusted it. `marker_feedback`
already reads timeline markers, clip markers AND media-pool markers and
would have found both. The probe was easier to reach for than the tool,
because six overlapping ways to read one reel's state exist:

    marker_feedback.read_notes        all three marker levels
    marker_resolution                 also reads clip markers (to clear them)
    timeline_serializer               also reads clip markers (to dump state)
    timeline_ingest.snapshot_timeline clips with ground-truth ranges
    reel_replace_guard.snapshot_timeline
                                      clips with spans (a second function of
                                      the same name)
    resolve_project_sync.get_resolve_project_state
                                      project binding, no timeline content
    capture_timeline.py               a full item dump (script, run-on-import)
    measure_overlay_draw_positions.py where overlay ink lands (script,
                                      run-on-import - DELETED by PR 1005:
                                      it computed its answer FROM the
                                      draw-gain constant, so it could only
                                      restate it; the ink half lives on in
                                      `measure_ink` below)

Nothing answered "tell me everything true about this reel right now" in
one call. This module is that call: `read_reel` returns the timeline and
its bin, every clip on every track with ranges, source ranges and
transforms, every marker at every level, the Fusion elements present,
and where overlay ink actually lands. Where a caller needs a slice, it
takes a slice of this result - see `clips_of`, `markers_of`,
`fusion_of`, `rows_of`, `overlays_of`.

Consolidation, not a seventh reader: the marker half IS
`marker_feedback.read_notes` (called, not reimplemented); the clip
ranges use `timeline_ingest`'s frame semantics (FRAMES, never
`GetSourceStartTime()`); the per-item detail lives in `clip_detail`,
which `timeline_serializer` calls; `reel_replace_guard.snapshot_timeline`
delegates its row building to `rows_of`. A caller that needs a slice
takes a slice.

Cheap mode and full mode: the whole truth is cheap EXCEPT the pixels.
`mode="quick"` (the default) reads Resolve getters only. `mode="full"`
additionally opens overlay artefacts off disk and measures their ink
boxes. `reel_replace_guard` reads `quick`. Measure before deciding is
done: pixel decode is the only expensive part, so it is the only part
behind the flag.

What the ink measurement does NOT do: it never grades a placement
against an intent row, and there is no gain constant left to import -
PR 1005 removed `tight_box.draw_gain` and deleted
`measure_overlay_draw_positions.py` with it, because that script
computed its answer FROM the constant and could never contradict it. A
reader that derives its answer from the value it is meant to check is
not a measurement. This one reports the measured ink box in artefact
pixels plus the stored transform, and stops there. Grading belongs to
a sibling lane.

READ-ONLY. Every call here is a getter. This module changes no timeline
and makes no decision - it is not a rewrite of `reel_conformance_verifier`.

Resolve is a single instance and other lanes drive it: the CLI refuses
when a build holds the project (see `run_control.hold_requested`) and
never opens or creates a project - the project must already be open.

Rules relocated from AGENTS.md 15
---------------------------------
**To read a reel, call `library/tools/reel_read.py`. Do not write a new
probe.** `read_reel(timeline, ...)` answers the whole truth about one
reel in one call: clips, markers at every level, Fusion elements, bin,
and overlay ink. `GetMarkers` / `GetItemListInTrack` outside the reader
modules listed in `tests/test_reel_read.py` is a new probe by another
name. Where a caller needs a slice, it takes a slice of the one reader.
"""


from __future__ import annotations

from library.tools.resolve_lock import under_lease

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Optional

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import marker_feedback  # noqa: E402
from library.tools.timeline_ingest import (  # noqa: E402
    _item_transform,
    _pool_frames,
    exact_frame_rate,
)

MEDIA_TYPES = ("video", "audio")

QUICK = "quick"
FULL = "full"


class ReelReadError(RuntimeError):
    """A reel that could not be read honestly. Fail closed, like the guard."""


# ── The currency check: a transform off a non-current timeline is not one ──
#
# Measured 2026-09-15 on Resolve Studio 21.1.0.14 (the re-measurement
# lane): Resolve reports a timeline item's Pan/Tilt scaled by the
# resolution of the timeline that is CURRENT, not of the timeline the
# item is on - Pan by current_width/this_width, Tilt by
# current_height/this_height, per axis. Reading Reel 13 (1080x1920)
# through its handle while the 3840x2160 master was current returned
# Pan -85.33333 for the -24.0 the same item reads with Reel 13
# current (ratio exactly 3840/1080), and Tilt -1944.0 for -1728.0
# (ratio exactly 2160/1920). `docs/READING_A_TRANSFORM.md` carries the
# earlier three-way cross-current read that already showed the
# anisotropy. This is a strong candidate for - but not proven to
# close - the long-running unexplained 2x and 4x readings: a uniform
# halving on both axes would need a current timeline half the width
# AND half the height of the read one, and no such timeline exists in
# the projects measured, so the 4x cause is still formally
# unidentified. Say what the evidence supports and no more.
#
# The shape is REFUSE, not record-and-correct: a correction would bake
# the measured ratio into every consumer and would still read as a
# measurement wherever a consumer forgot to apply it - the same defect
# moved. Refusing collapses the conditions to one (the reel current),
# under which no consumer ever reasons about scale factors. The
# read-only sweeps that cannot move the cursor (the conformance
# verifier's F12, `resolve_axi --transforms`) are the cost, stated in
# the audit: their transform numbers are scaled whenever the read
# timeline is not current, and they must say so or self-read.


def _timeline_identity(timeline):
    """`(kind, value)`: `GetUniqueId` when served, else the exact name.

    Real Resolve timelines serve a UniqueId; stub timelines in tests
    may serve only a name. Identity is the id where it exists because
    two timelines can resemble in name and must never be substituted
    (AGENTS.md 5) - the name is only the fallback where no id answers.
    """
    try:
        unique = timeline.GetUniqueId()
    except Exception:
        unique = None
    if unique:
        return ("id", str(unique))
    try:
        name = timeline.GetName()
    except Exception:
        name = None
    return ("name", name)


def _timeline_size(timeline):
    """`(width, height)` from timeline settings, or `(None, None)`."""
    try:
        width = int(timeline.GetSetting("timelineResolutionWidth"))
        height = int(timeline.GetSetting("timelineResolutionHeight"))
    except (TypeError, ValueError):
        return (None, None)
    except Exception:
        return (None, None)
    return (width, height)


def assert_timeline_current(timeline, resolve_project) -> None:
    """Refuse unless `timeline` is `resolve_project`'s current one.

    A Pan/Tilt read through a non-current handle comes back scaled by
    the current timeline's dimensions over this one's (above), so it
    reads as a measurement and is not one. The remedy is a self-read:
    make the reel current - `resolve_lock.cursor_excursion`, which
    puts the cursor back through the guarded setter - then read.
    Raises `ReelReadError`, which names both timelines and both
    resolutions so the refusal itself shows the scale that would have
    been mistaken.
    """
    try:
        current = resolve_project.GetCurrentTimeline()
    except Exception as unreadable:
        raise ReelReadError(
            "the current timeline could not be read "
            f"({unreadable}); refusing rather than returning a "
            f"transform that may be scaled.") from unreadable
    if current is None:
        raise ReelReadError(
            "Resolve has no current timeline; a transform read with "
            "nothing current is not a measurement. Open the reel and "
            "re-run.")
    want, have = _timeline_identity(timeline), _timeline_identity(current)
    if want[1] is not None and want == have:
        return
    try:
        timeline_name = timeline.GetName()
    except Exception:
        timeline_name = "?"
    try:
        current_name = current.GetName()
    except Exception:
        current_name = "?"
    this_w, this_h = _timeline_size(timeline)
    cur_w, cur_h = _timeline_size(current)

    def _fmt(size):
        return f"{size[0]}x{size[1]}" if all(size) else "unknown size"

    raise ReelReadError(
        f"REFUSING: timeline {timeline_name!r} ({_fmt((this_w, this_h))}) "
        f"is not current - {current_name!r} ({_fmt((cur_w, cur_h))}) is. "
        f"Pan/Tilt read through a non-current handle come back scaled "
        f"by current_width/this_width on Pan and "
        f"current_height/this_height on Tilt, so the numbers would "
        f"read as a measurement and are not one. Make the reel current "
        f"(`resolve_lock.cursor_excursion`, which restores the cursor) "
        f"and re-read.")


# ── The per-item detail: the one place a clip is enumerated ──────────
#
# `timeline_serializer` calls this for every item instead of its own
# inline block, so there is one per-item reader. Identity fields are
# strict (a clip that cannot be read raises); enrichment is lenient (a
# Fusion/CDL call that declines reads as absent, never as empty proof).


def _safe(default, fn, *args):
    try:
        value = fn(*args)
    except Exception:
        return default
    return default if value is None else value


def _call(obj, name: str, default, *args):
    """`getattr` plus `_safe`: the attribute itself may be absent.

    `_safe` guards the CALL, but evaluating `item.GetUniqueId` to pass
    it in already raises `AttributeError` on an object that has no such
    method. On Resolve's own scripting proxies every lookup succeeds
    (AGENTS.md 5) and the call raises instead - also caught here.
    """
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    return _safe(default, fn, *args)


def clip_detail(item, track_type: str, track_index: int,
                track_name: str = "") -> dict:
    """Everything true about one timeline item, as plain data.

    Ranges are FRAMES in Resolve's own spaces: record (timeline) frames
    for placement, source frames for the range inside the file - the same
    distinction `timeline_ingest` documents. The transform is
    `GetProperty()` with no argument (AGENTS.md 5); anything that is not
    a dict reads as "Resolve did not say".
    """
    try:
        name = item.GetName()
        start = item.GetStart()
        end = item.GetEnd()
        duration = item.GetDuration()
    except Exception as unreadable:
        raise ReelReadError(
            f"a timeline item on {track_type}{track_index} could not be "
            f"read ({unreadable}); refusing rather than reading half "
            f"a reel.") from unreadable
    pool_item = _call(item, "GetMediaPoolItem", None)
    source_file = ""
    pool_frames = None
    pool_item_id = ""
    if pool_item is not None:
        source_file = (
            _call(pool_item, "GetClipProperty", "", "File Path")
            or _call(pool_item, "GetClipProperty", "", "Clip Path"))
        pool_frames = _pool_frames(pool_item)
        pool_item_id = _call(pool_item, "GetMediaId", "")
    return {
        "unique_id": _call(item, "GetUniqueId", ""),
        "name": name or "",
        "track_type": track_type,
        "track_index": track_index,
        "track_name": track_name,
        "record_in": start,
        "record_out": end,
        "duration": duration,
        "source_in_frame": _call(item, "GetSourceStartFrame", None),
        "source_out_frame": _call(item, "GetSourceEndFrame", None),
        "left_offset": _call(item, "GetLeftOffset", None),
        "right_offset": _call(item, "GetRightOffset", None),
        "source_file": source_file,
        "source_frames": pool_frames,
        "media_pool_item_id": pool_item_id,
        "clip_color": _call(item, "GetClipColor", ""),
        "flags": list(_call(item, "GetFlagList", []) or []),
        # Unknown stays unknown. A replacement snapshot must not turn a
        # failed enabled-state read into `True`, because that is exactly
        # the state a rebuild would silently restore.
        "enabled": _call(item, "GetClipEnabled", None),
        "transform": _item_transform(item),
        "fusion": {
            "comp_count": _call(item, "GetFusionCompCount", 0),
            "comp_names": list(
                _call(item, "GetFusionCompNameList", []) or []),
            # Where each comp's MediaIn reads from, and whether it
            # covers the frames this item PLAYS. A comp that does not
            # is why six of the captain's eight reels could not render
            # their own endings, and nothing could see it without
            # rendering - `library/tools/comp_media_window.py`.
            "media_windows": _comp_windows(item, duration),
        },
        "color": {
            "cdl": dict(_call(item, "GetCDL", {}) or {}),
            "color_group": _call(item, "GetColorGroup", ""),
        },
        "markers": [
            {
                "frame": key,
                "color": (marker.get("color") or ""),
                "name": (marker.get("name") or ""),
                "note": (marker.get("note") or ""),
                "duration": marker.get("duration", 0),
                "custom_data": (marker.get("customData") or ""),
            }
            for key, marker in
            (_call(item, "GetMarkers", {}) or {}).items()
        ],
    }


# ── The one enumeration ────────────────────────────────────────────
#
# Every clip on every track, read once, here. Both the full `read_reel`
# below and the replace guard's row slice go through this: one loop over
# Resolve items, not one per consumer. It needs only the row protocol
# (track counts/names, item lists, each item's name and span) and fails
# closed on an unreadable row or item - but it does NOT need timeline
# settings, markers or pool items, which is what the guard's diff reads
# and all it ever refused on. Routing the guard through the full read
# broke promotion on timelines that answer rows but nothing else
# (2026-09-12: five gate tests refused with "could not be read ...
# no attribute 'GetSetting'"), so the guard takes this slice instead.


def live_track_items(timeline, track_type: str, index: int,
                       *, resolve_project=None) -> list:
    """The LIVE handles on one row, in timeline order. A slice of the one read.

    The single-row half of `live_items` below: the `GetItemListInTrack`
    call itself lives here, so a caller that needs one row - the overlay
    sweep re-reading a placed clip's stored transform - takes this slice
    rather than opening its own probe (AGENTS.md 15). Raises
    `ReelReadError` when the row does not read.

    `resolve_project` is the currency proof: when given, the timeline
    must be the project's current one (`assert_timeline_current`),
    because the stored transforms on these handles read scaled when it
    is not. Left out, no check is made - the replace guard's rows
    (names and spans, which carry no transform) read this way, and so
    do the stub timelines in tests, which have no project.
    """
    if resolve_project is not None:
        assert_timeline_current(timeline, resolve_project)
    try:
        return list(timeline.GetItemListInTrack(track_type, index) or [])
    except Exception as unreadable:
        raise ReelReadError(
            f"the items of {track_type}{index} on "
            f"{_call(timeline, 'GetName', '?')!r} could not be read "
            f"({unreadable}); refusing rather than reading half "
            f"a reel.") from unreadable


def live_items(timeline, *, resolve_project=None) -> list:
    """Every row with its LIVE Resolve item handles, in track order.

    The one enumeration over the whole timeline: `read_tracks` below is
    this list projected into plain data, and a caller that must WRITE to
    an item - `composed_edit`, which deletes and re-places them - takes
    this slice rather than opening its own loop over the tracks
    (AGENTS.md 15: no new probe). The per-row read itself is
    `live_track_items` above, which single-row callers take directly.

    `resolve_project` is the currency proof, checked once here rather
    than per row. See `live_track_items` for when it is required and
    when it is left out.

    Returns `[{"type", "index", "name", "items": [handle, ...]}, ...]`.
    The handles are Resolve's own objects and are invalidated by any
    delete or place, so a caller re-reads after every mutation rather
    than holding one across it.
    """
    if resolve_project is not None:
        assert_timeline_current(timeline, resolve_project)
    rows = []
    try:
        for track_type in MEDIA_TYPES:
            count = timeline.GetTrackCount(track_type) or 0
            for index in range(1, count + 1):
                rows.append({
                    "type": track_type,
                    "index": index,
                    "name": timeline.GetTrackName(track_type, index) or "",
                    "items": live_track_items(timeline, track_type, index),
                })
    except ReelReadError:
        raise
    except Exception as unreadable:
        raise ReelReadError(
            f"the rows of {_call(timeline, 'GetName', '?')!r} could not "
            f"be read ({unreadable}); refusing rather than reading half "
            f"a reel.") from unreadable
    return rows


def read_tracks(timeline,
                speaker_map: Optional[Mapping[str, str]] = None,
                *, resolve_project=None) -> list:
    """Every track with every clip, as plain data. Read-only.

    Raises `ReelReadError` on ANY row/item read failure - a half-read
    timeline must refuse, never pass on the rows that happened to read -
    and when `resolve_project` is given and the timeline is not the
    project's current one (`assert_timeline_current`): the `transform`
    on every clip would read scaled. The replace guard calls this
    WITHOUT the project because its rows (names and spans) carry no
    transform and are current-independent; anyone comparing transforms
    across two reads passes the project on both, which refuses unless
    each timeline was current for its own read.
    """
    if resolve_project is not None:
        assert_timeline_current(timeline, resolve_project)
    speaker_map = dict(speaker_map or {})
    tracks = []
    try:
        for row in live_items(timeline):
            track_type, index = row["type"], row["index"]
            track_name = row["name"]
            speaker = speaker_map.get(track_name) or (track_name or None)
            clips = []
            for item in row["items"]:
                detail = clip_detail(item, track_type, index, track_name)
                detail["speaker"] = speaker
                clips.append(detail)
            tracks.append({
                "type": track_type,
                "index": index,
                "name": track_name,
                "speaker": speaker,
                "clips": clips,
            })
    except ReelReadError:
        raise
    except Exception as unreadable:
        raise ReelReadError(
            f"timeline {timeline.GetName()!r} could not be read "
            f"({unreadable}); refusing rather than reading half "
            f"a reel.") from unreadable
    return tracks


# ── The one read ─────────────────────────────────────────────────────


@under_lease("read a reel", exclusive=False)
def read_reel(timeline, project_name: str = "",
              speaker_map: Optional[Mapping[str, str]] = None,
              mode: str = QUICK, artefact_roots=(),
              project_folder=None, *, resolve_project) -> dict:
    """The whole truth about one reel, in one call. Read-only.

    `timeline` is the live Resolve timeline object, so this runs against
    whatever is open without changing the captain's session. `mode` is
    `"quick"` (Resolve getters only) or `"full"` (plus pixel-measured
    overlay ink off disk - the only expensive part).

    `resolve_project` is REQUIRED (keyword-only) and is the currency
    proof: the timeline must be the project's current one
    (`assert_timeline_current`), because Pan/Tilt read through a
    non-current handle come back scaled by the current timeline's
    dimensions and would read as a measurement that is not one. There
    is no opt-out on this function - the row-only slices
    (`read_tracks`, `live_items`) are the route for readers that need
    no transform. To read a reel that is not open, make it current
    first (`resolve_lock.cursor_excursion` restores the cursor) and
    pass the same project.

    Markers come from `marker_feedback.read_notes`: timeline, clip and
    media-pool levels with colour, name, note and attachment. That is
    the exact miss that started this task - a probe reading
    `Timeline.GetMarkers()` only reports no markers on a reel whose
    notes sit on its clips.
    """
    if mode not in (QUICK, FULL):
        raise ReelReadError(
            f"mode is {mode!r}; the vocabulary is {QUICK!r} and {FULL!r}.")
    assert_timeline_current(timeline, resolve_project)
    try:
        reported_fps = float(timeline.GetSetting("timelineFrameRate"))
    except (TypeError, ValueError):
        raise ReelReadError(
            "timelineFrameRate did not read back as a number; judging "
            "the call by what it returned (AGENTS.md 5).")
    if reported_fps <= 0:
        raise ReelReadError(f"timelineFrameRate is {reported_fps}.")
    fps = exact_frame_rate(reported_fps)

    try:
        width = int(timeline.GetSetting("timelineResolutionWidth"))
        height = int(timeline.GetSetting("timelineResolutionHeight"))
    except (TypeError, ValueError):
        width, height = 0, 0

    tracks = read_tracks(timeline, speaker_map)

    try:
        timeline_markers_raw = timeline.GetMarkers() or {}
    except Exception as unreadable:
        raise ReelReadError(
            f"timeline markers could not be read ({unreadable}).") \
            from unreadable

    notes = marker_feedback.read_notes(timeline, project_folder)

    pool_files = {}
    for track in tracks:
        for clip in track["clips"]:
            path = clip["source_file"] or clip["name"]
            if path and path not in pool_files:
                pool_files[path] = {
                    "file_path": clip["source_file"],
                    "source_frames": clip["source_frames"],
                }

    result = {
        "timeline": timeline.GetName(),
        "project": project_name,
        "fps": fps,
        "reported_fps": reported_fps,
        "width": width,
        "height": height,
        "start_frame": timeline.GetStartFrame(),
        "end_frame": timeline.GetEndFrame(),
        "mode": mode,
        "tracks": tracks,
        "markers": {
            "timeline": [
                {
                    "frame": key,
                    "color": (m.get("color") or ""),
                    "name": (m.get("name") or ""),
                    "note": (m.get("note") or ""),
                    "duration": m.get("duration", 0),
                    "custom_data": (m.get("customData") or ""),
                }
                for key, m in timeline_markers_raw.items()
            ],
            # Every level, with colour, name, note and attachment - the
            # canonical marker read. A caller that needs markers takes
            # this slice; it does not call GetMarkers itself.
            "notes": [asdict(n) for n in notes],
        },
        "bin": pool_files,
        "fusion": fusion_of_tracks(tracks),
        "overlays": [],
    }
    if mode == FULL:
        result["overlays"] = measure_ink(tracks, width, height,
                                         artefact_roots)
    return result


def _comp_windows(item, duration) -> list:
    """One row per comp: its media window and why it is uncovered, if it is.

    READ-ONLY, like everything else here: `comp_media_window` reads and
    judges; the repair lives in the comp pass that writes.
    """
    from library.tools import comp_media_window as _window

    rows = []
    try:
        count = int(_call(item, "GetFusionCompCount", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    for index in range(1, count + 1):
        window = _window.read_window(item, index)
        rows.append({
            "comp_index": index,
            "window": window,
            "uncovered_reason": _window.uncovered_reason(window, duration),
            # What the comp is made of, so a caller can tell a comp that
            # DRAWS something from Resolve's own empty one. Measured
            # 2026-09-12: a plain timeline item reports
            # `GetFusionCompCount() == 1` for a composition that is only
            # MediaIn -> MediaOut (plus AudioDisplay), and a caller that
            # read the count alone could not tell it from a built
            # treatment. `None` means the graph would not be read - an
            # absence, judged by the caller, never silently an empty one.
            "tools": _comp_tools(item, index),
        })
    return rows


#: Tools a Fusion composition carries when it draws NOTHING: the source,
#: the output, and the audio waveform Resolve attaches to both.
PASSTHROUGH_TOOLS = frozenset({"MediaIn", "MediaOut", "AudioDisplay"})


def _comp_tools(item, index: int):
    """Every tool's `TOOLS_RegID` in one comp, sorted, or None."""
    try:
        comp = item.GetFusionCompByIndex(index)
    except Exception:
        return None
    if comp is None or isinstance(comp, str):
        return None
    try:
        tools = comp.GetToolList(False) or {}
    except Exception:
        return None
    if not isinstance(tools, dict):
        return None
    out = []
    for _key, tool in sorted(tools.items(), key=lambda kv: str(kv[0])):
        try:
            out.append(tool.GetAttrs("TOOLS_RegID"))
        except Exception:
            return None
    return sorted(out)


def comp_draws_something(row) -> Optional[bool]:
    """Whether one `_comp_windows` row is more than a passthrough.

    `None` when the graph could not be read. A caller that must decide
    whether a treatment exists treats `None` as YES and says so: an
    unreadable graph is not evidence of an empty one.
    """
    tools = row.get("tools")
    if tools is None:
        return None
    return bool(set(tools) - PASSTHROUGH_TOOLS)


def fusion_of_tracks(tracks: list) -> list:
    """Every clip carrying Fusion comps. A slice of the one read."""
    out = []
    for track in tracks:
        for clip in track["clips"]:
            fusion = clip.get("fusion") or {}
            if fusion.get("comp_count"):
                out.append({
                    "clip": clip["name"],
                    "unique_id": clip["unique_id"],
                    "track_type": clip["track_type"],
                    "track_index": clip["track_index"],
                    "record_in": clip["record_in"],
                    "record_out": clip["record_out"],
                    "comp_count": fusion["comp_count"],
                    "comp_names": fusion["comp_names"],
                    "media_windows": fusion.get("media_windows", []),
                })
    return out


# ── Where overlay ink actually lands: measured pixels, never the gain ─


def _find_artefact(basename: str, source: str, roots) -> str:
    if source and Path(source).exists():
        return source
    for root in roots or ():
        hit = Path(root) / basename
        if hit.exists():
            return str(hit)
        try:
            for found in Path(root).rglob(basename):
                return str(found)
        except Exception:
            continue
    return ""


def _ink_box(path: str) -> dict:
    """The ink box of an artefact, measured from its own alpha.

    Opens the file and bounds the non-transparent pixels. What it does
    NOT do is scale, gain or grade the answer: measured pixels only, no
    intent row. The number that reaches the timeline is the stored
    transform beside this box, reported verbatim from `GetProperty()`.

    Stills decode through PIL; movie artefacts (`.mov`/`.mp4`) decode
    every frame through `tight_box.extract_frames` and union through
    `tight_box.ink_union_of_frames` (`_ink_box_movie`) - the same
    decoder the tight-box path already uses, not a second one. A file
    that cannot be decoded reads `measured: False` with the reason,
    never a default presented as measured.
    """
    if Path(path).suffix.lower() in _MOVIE_SUFFIXES:
        return _ink_box_movie(path)
    try:
        from PIL import Image
    except ImportError:
        return {"measured": False,
                "reason": "PIL is not installed; refusing to guess"}
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            alpha = rgba.getchannel("A")
            bbox = alpha.getbbox()
            if not bbox:
                return {"measured": False,
                        "reason": "fully transparent artefact"}
            return {"measured": True,
                    "canvas": [rgba.width, rgba.height],
                    "ink_box_xyxy": list(bbox)}
    except Exception as unreadable:
        return {"measured": False, "reason": str(unreadable)}


#: Suffixes that carry frames, not stills: PIL cannot open them (every
#: caption and motion-graphic artefact this engine writes is a
#: QuickTime movie), so they decode through the one ffmpeg frame
#: extractor rather than `Image.open`.
_MOVIE_SUFFIXES = (".mov", ".mp4")


def _ink_box_movie(path: str) -> dict:
    """The ink box of a movie artefact, measured off its decoded frames.

    The union of per-frame alpha bboxes across EVERY frame - a reveal
    that draws on frame 12 only is still ink the timeline shows, so a
    sampled frame would let drawn ink through as absent. Decode is
    `tight_box.extract_frames` and the union is
    `tight_box.ink_union_of_frames`: the pair the tight-box path
    already measures with (see `mg_tight_box.measure_mg_union` for the
    same composition), imported lazily so quick mode - and Resolve's
    scripting host - never loads that chain. An UNDECODABLE file reads
    `measured: False` with the reason, exactly as the still path does:
    absent evidence is not empty evidence.
    """
    try:
        from library.tools.tight_box import (
            TightBoxMismatch,
            extract_frames,
            ink_union_of_frames,
        )
    except ImportError as exc:
        return {"measured": False,
                "reason": f"cannot decode movie artefacts: {exc}"}
    try:
        from PIL import Image
    except ImportError:
        return {"measured": False,
                "reason": "PIL is not installed; refusing to guess"}
    try:
        with tempfile.TemporaryDirectory(prefix="reel-ink-") as work:
            frame_paths = extract_frames(path, work)
            union = ink_union_of_frames(frame_paths)
            if union is None:
                return {"measured": False,
                        "reason": "fully transparent artefact"}
            with Image.open(frame_paths[0]) as first:
                canvas = [first.width, first.height]
            return {"measured": True,
                    "canvas": canvas,
                    "ink_box_xyxy": [union.x0, union.y0,
                                     union.x1, union.y1],
                    "frames": len(frame_paths),
                    "inked_frames": union.inked_frames}
    except (TightBoxMismatch, OSError) as exc:
        return {"measured": False, "reason": str(exc)}


def measure_ink(tracks: list, width: int, height: int,
                artefact_roots=()) -> list:
    """One row per overlay clip: measured ink plus stored transform.

    Overlay rows are the tracks ABOVE the picture that carry artefacts
    (V4/V5-style rows); every video clip whose source file is an image
    or movie artefact is reported rather than filtered by index, so a
    renumbered track cannot hide one. The ink box is measured pixels;
    the transform is what Resolve stores. Nothing is derived from the
    draw-gain constant, which a sibling lane is removing.
    """
    rows = []
    for track in tracks:
        if track["type"] != "video":
            continue
        for clip in track["clips"]:
            source = clip["source_file"] or ""
            suffix = Path(source or clip["name"]).suffix.lower()
            if suffix not in (".png", ".mov", ".mp4", ".tif", ".tiff",
                              ".exr", ".psd"):
                continue
            path = _find_artefact(Path(source or clip["name"]).name,
                                  source, artefact_roots)
            if not path:
                rows.append({
                    "clip": clip["name"],
                    "track": track["name"],
                    "record_in": clip["record_in"],
                    "record_out": clip["record_out"],
                    "artefact": source,
                    "ink": {"measured": False,
                            "reason": "artefact not on disk"},
                    "transform": clip["transform"],
                })
                continue
            rows.append({
                "clip": clip["name"],
                "track": track["name"],
                "record_in": clip["record_in"],
                "record_out": clip["record_out"],
                "artefact": path,
                "ink": _ink_box(path),
                "transform": clip["transform"],
            })
    return rows


# ── Slices: where a caller that needs part takes part ────────────────


def clips_of(result: dict) -> list:
    """Every clip on every track, in track then timeline order."""
    out = []
    for track in result.get("tracks", []):
        out.extend(track.get("clips", []))
    return out


def markers_of(result: dict) -> list:
    """Every marker note at every level. The slice that replaces probes."""
    return result.get("markers", {}).get("notes", [])


def fusion_of(result: dict) -> list:
    """Every clip carrying Fusion comps."""
    return result.get("fusion", [])


def uncovered_comp_windows(result: dict) -> list:
    """Every comp whose MediaIn misses a frame its item PLAYS.

    The diagnosis slice for the defect `comp_media_window` measures: a
    reel carrying one of these renders until Resolve reaches the
    uncovered frame and then FAILS the whole job. Six of the captain's
    eight built reels answered this with a row on 2026-09-12.
    """
    out = []
    for clip in clips_of(result):
        for row in (clip.get("fusion") or {}).get("media_windows") or []:
            if not row.get("uncovered_reason"):
                continue
            out.append({
                "clip": clip["name"],
                "track_type": clip["track_type"],
                "track_index": clip["track_index"],
                "record_in": clip["record_in"],
                "record_out": clip["record_out"],
                "played_frames": clip["duration"],
                **row,
            })
    return out


def overlays_of(result: dict) -> list:
    """Every overlay row with its measured ink (full mode only)."""
    return result.get("overlays", [])


def rows_of(result: dict) -> dict:
    """The replace-guard shape: per row, items with name and span.

    `reel_replace_guard.snapshot_timeline` is this slice, so the guard
    and the reader cannot disagree on what a row holds.
    """
    from library.tools import reel_replace_guard as _guard

    rows = {}
    for track in result.get("tracks", []):
        key = _guard.row_key(track["type"], track["name"] or
                             f"#{track['index']}")
        items = [
            {"name": clip["name"], "start": clip["record_in"],
             "end": clip["record_out"], "duration": clip["duration"],
             # Some row producers project historical or derived reads
             # that predate enabled-state capture. Preserve that as
             # unknown; live replacement snapshots separately refuse it.
             "enabled": clip.get("enabled")}
            for clip in track.get("clips", [])
        ]
        rows[key] = {
            "media_type": track["type"],
            "index": track["index"],
            "name": track["name"],
            "items": items,
            "count": len(items),
            "frames": sum((entry["duration"] or 0) for entry in items),
        }
    return rows


# ── CLI: read-only, and never while a build holds the project ───────


def _hold_active(project_folder) -> bool:
    if not project_folder:
        return False
    try:
        from library.tools import run_control
        return bool(run_control.hold_requested(str(project_folder)))
    except Exception:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.reel_read",
        description="Read the whole truth about one Resolve timeline. "
                    "Read-only: it changes no timeline and makes no "
                    "decision.",
    )
    parser.add_argument("--timeline", required=True,
                        help="the timeline's EXACT name (AGENTS.md 5: "
                             "never a prefix)")
    parser.add_argument("--project", default="",
                        help="the Resolve project name, reported only")
    parser.add_argument("--mode", default=QUICK, choices=(QUICK, FULL),
                        help="quick reads Resolve getters only; full also "
                             "measures overlay ink off disk")
    parser.add_argument("--artefact-root", action="append", default=[],
                        help="search root for overlay artefacts "
                             "(repeatable, full mode only)")
    parser.add_argument("--project-folder", default=None,
                        help="pipeline project folder: refuses when a "
                             "build holds it, resolves attachments")
    parser.add_argument("--out", default=None,
                        help="write the JSON result here as well as stdout")
    args = parser.parse_args(argv)

    if _hold_active(args.project_folder):
        print("REFUSING: a build holds this project "
              "(pipeline.hold); a read taken mid-build is half a truth. "
              "Clear the hold or wait for the build, then re-run.",
              file=sys.stderr)
        return 4
    try:
        timeline, project = marker_feedback.current_timeline()
    except marker_feedback.ResolveUnavailable as exc:
        print(f"Cannot read: {exc}", file=sys.stderr)
        return 3
    if timeline.GetName() != args.timeline:
        print(f"REFUSING: the open timeline is {timeline.GetName()!r}, "
              f"not {args.timeline!r}. Exact names only (AGENTS.md 5); "
              f"open the reel and re-run.", file=sys.stderr)
        return 2
    try:
        result = read_reel(
            timeline, args.project or project.GetName(),
            mode=args.mode, artefact_roots=args.artefact_root,
            project_folder=args.project_folder,
            # The CLI reads through `current_timeline()` and refuses
            # unless the open timeline IS the named one, so this proof
            # always holds here - and a future caller that reaches for
            # a by-index handle gets the refusal instead of scaled
            # Pan/Tilt (`assert_timeline_current`).
            resolve_project=project)
    except ReelReadError as exc:
        print(f"Cannot read: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
