"""The dashboard's half of the footage index - one warm index per project.

The captain asked for the index in the browser so they can dig through
their own footage by hand.  **This is the PERSON half of that prototype,
and only the person half.**  `library/tools/analysis/footage_query.py`
is still forbidden to every step, DAG node and process manifest, and
`tests/test_footage_query_prototype.py` still enforces that; the
dashboard is the one caller that was carved out, because a reviewer
searching their own rushes is not the pipeline making a decision.

Three things live here that the prototype module has no business
knowing, because they are properties of a long-lived server rather than
of a search:

- **The index is held warm.**  Loading the embedding model is ~2 s and
  every query after it is ~2 ms - three orders of magnitude apart.  A
  server pays that once, in a background thread, and reports which of
  the four states it is in rather than looking hung.
- **The build is the reviewer's decision, never a side effect.**  It
  writes ~1 MB into `<project>/pipeline_output/scratch/footage_index`,
  and the UI states that path before the button is pressed.  Building on
  server start would write into the captain's project every time the
  dashboard opens; building on first query would do it at the moment
  they are least expecting a wait.
- **A hit is shaped for Resolve.**  The prototype answers in seconds;
  the captain acts in a timeline, so each hit also carries a non-drop
  source timecode computed at the clip's OWN frame rate, read off the
  catalog.  A clip whose frame rate the catalog does not know gets no
  timecode rather than a made-up one.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from pathlib import Path

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    FootageIndex,
    build_index,
    index_dir_for,
)
from library.tools.analysis.footage_segments import SEGMENT_KINDS, load_catalog

# Facets a reviewer can filter on from the browser, and the shape of each.
# A facet not in here has no control, and a control has no facet it does
# not name: the two lists were one list.
NAMED_FACETS = ("kind", "clip_id", "framing", "camera_mode", "stability",
                "movement", "scene_type", "content_type")
NUMERIC_FACETS = ("min_duration", "max_duration", "min_face_presence",
                  "max_face_presence", "min_motion", "max_motion")

# How many distinct labels the empty state names when it says what the
# footage DOES contain.  "No results" is correct and useless.
CORPUS_PREVIEW_LIMIT = 24

_lock = threading.Lock()
_indexes: dict = {}       # project_dir -> FootageIndex
_warm_state: dict = {}    # project_dir -> {"state", "backend", "started"}


# ─── The warm index ───────────────────────────────────────────────


def _key(project_dir) -> str:
    return str(Path(project_dir).resolve())


def get_index(project_dir) -> FootageIndex:
    """The one `FootageIndex` this server holds for a project."""
    key = _key(project_dir)
    with _lock:
        index = _indexes.get(key)
        if index is None:
            index = _indexes[key] = FootageIndex(key)
        return index


def forget(project_dir=None):
    """Drop cached state, so the next call reads the index off disk again.

    Called after a build and when the dashboard switches project: a
    `FootageIndex` caches its payload and its matrix the first time they
    are touched, and a rebuilt index behind a cached payload would answer
    with the segments it no longer has.
    """
    with _lock:
        if project_dir is None:
            _indexes.clear()
            _warm_state.clear()
            return
        key = _key(project_dir)
        _indexes.pop(key, None)
        _warm_state.pop(key, None)


def warm_state(project_dir) -> dict:
    """`cold`, `loading`, `ready` or `unavailable`, plus the backend name."""
    with _lock:
        return dict(_warm_state.get(_key(project_dir))
                    or {"state": "cold", "backend": None})


def start_warming(project_dir) -> dict:
    """Load the embedding model in the background, once per server.

    Returns immediately with the state so a page can say "loading the
    embedding model (~2 s, once)" instead of showing nothing while the
    first query blocks.
    """
    key = _key(project_dir)
    with _lock:
        current = _warm_state.get(key)
        if current and current["state"] in ("loading", "ready", "unavailable"):
            return dict(current)
        _warm_state[key] = {"state": "loading", "backend": None, "started": time.time()}

    def run():
        index = get_index(key)
        try:
            result = index.warm()
            state = "ready" if result["ready"] else "unavailable"
            backend = result["backend"]
        except Exception as exc:  # noqa: BLE001 - a failed load is a state, not a crash
            state, backend = "unavailable", f"{type(exc).__name__}: {exc}"
        with _lock:
            started = (_warm_state.get(key) or {}).get("started", time.time())
            _warm_state[key] = {"state": state, "backend": backend,
                                "started": started,
                                "seconds": round(time.time() - started, 2)}

    threading.Thread(target=run, name="footage-index-warm", daemon=True).start()
    return warm_state(project_dir)


# ─── Timecode a person can carry into Resolve ─────────────────────


def _clip_rates(project_dir) -> dict:
    """``{clip_id: fps}`` off the catalog, for the clips that declare one."""
    rates = {}
    for clip in load_catalog(project_dir) or []:
        if not isinstance(clip, dict):
            continue
        fps = clip.get("frame_rate", clip.get("fps"))
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            continue
        if fps > 0:
            rates[clip.get("clip_id", "")] = fps
    return rates


def source_timecode(seconds, fps) -> str:
    """`HH:MM:SS:FF` from the clip's start, non-drop, at the clip's own rate.

    Non-drop and from the clip's start because that is what a source
    timecode means for a file that starts at zero.  Nothing here invents
    a rate: a caller without one gets no timecode at all.
    """
    frames_per_second = round(fps)
    total_frames = round(float(seconds) * fps)
    ff = total_frames % frames_per_second
    total_seconds = total_frames // frames_per_second
    return (f"{total_seconds // 3600:02d}:{total_seconds // 60 % 60:02d}:"
            f"{total_seconds % 60:02d}:{ff:02d}")


def _with_timecode(hit: dict, rates: dict) -> dict:
    fps = rates.get(hit.get("clip_id"))
    if fps:
        hit["fps"] = fps
        hit["source_tc_in"] = source_timecode(hit["start"], fps)
        hit["source_tc_out"] = source_timecode(hit["end"], fps)
    return hit


# ─── What the footage contains, for an honest empty state ─────────


def corpus_preview(index: FootageIndex) -> dict:
    """The labels this footage really carries, most common first.

    An abstain that only says "no results" cannot be told from a broken
    search.  Naming what IS in the corpus turns it into an answer: the
    reviewer can see that there are caps and motorcycles and no cups.
    """
    objects, places = Counter(), Counter()
    for segment in index.segments:
        head = (segment.get("text") or "").split(".")[0].strip()
        if not head:
            continue
        if segment["kind"] == "object":
            objects[head.lower()] += 1
        elif segment["kind"] == "scene":
            places[head] += 1
    return {
        "objects": [w for w, _ in objects.most_common(CORPUS_PREVIEW_LIMIT)],
        "places": [w for w, _ in places.most_common(CORPUS_PREVIEW_LIMIT)],
    }


def facet_vocabulary(index: FootageIndex) -> dict:
    """Only the facet values this project's footage actually has.

    Offering "close-up / medium / wide" to a project whose vision pass
    only ever wrote "close-up" invites two empty searches to find that
    out.  The dropdowns are built from the index.
    """
    values = {name: set() for name in NAMED_FACETS}
    for segment in index.segments:
        values["kind"].add(segment["kind"])
        values["clip_id"].add(segment["clip_id"])
        for name, value in (segment.get("facets") or {}).items():
            if name in values and isinstance(value, str) and value:
                values[name].add(value)
    return {name: sorted(v) for name, v in values.items() if v}


# ─── The three things the browser asks for ────────────────────────


def status(project_dir, warm: bool = True) -> dict:
    """Everything the search view needs before a query is typed.

    Reports the state of the index rather than an index: not built, built
    and current, built and stale.  All three are legitimate and the page
    says which.
    """
    key = _key(project_dir)
    target = index_dir_for(key)
    out = {
        "project_dir": key,
        "index_dir": str(target),
        "index_file": str(target / footage_query.SEGMENTS_FILE),
        "exists": (target / footage_query.SEGMENTS_FILE).exists(),
        "floor": {"default": footage_query.DENSE_SCORE_FLOOR,
                  "weak": footage_query.DENSE_WEAK_FLOOR},
        "kinds": list(SEGMENT_KINDS),
        "embed": warm_state(key),
    }
    if not out["exists"]:
        out["hint"] = (
            "Building one reads the ingest output of steps 1.02-1.05 and "
            "writes about a megabyte into the project's scratch area, which "
            "no step reads and which is safe to delete at any moment. "
            "Nothing else in the project is touched."
        )
        return out

    index = get_index(key)
    try:
        out["summary"] = index.summary()
        out["staleness"] = index.staleness()
        out["facets"] = facet_vocabulary(index)
        out["numeric_facets"] = list(NUMERIC_FACETS)
        out["corpus"] = corpus_preview(index)
    except (OSError, ValueError, KeyError) as exc:
        out["exists"] = False
        out["error"] = f"index unreadable: {type(exc).__name__}: {exc}"
        return out

    if warm and out["embed"]["state"] == "cold":
        out["embed"] = start_warming(key)
    return out


def build(project_dir) -> dict:
    """Build the index into the project's scratch area.

    This is the ONE call that writes into the captain's project, it
    happens only when a reviewer presses the button, and the returned
    stats name the directory it wrote so the answer can be checked.
    """
    key = _key(project_dir)
    stats = build_index(key)
    forget(key)
    start_warming(key)
    return stats


def search(project_dir, query: str = "", top_k: int = 10, mode: str = "hybrid",
           floor=None, filters: dict | None = None) -> dict:
    """A query, or a filter-only selection when no query was typed.

    "Steady wide footage with nobody in frame" is a FILTER, not a search,
    and a reviewer who fills in the facets and leaves the box empty means
    exactly that.  Answering it with a ranking of nothing would be the
    wrong answer, so an empty query runs `filter` and says so.
    """
    key = _key(project_dir)
    index = get_index(key)
    rates = _clip_rates(key)
    clean = {k: v for k, v in (filters or {}).items() if v is not None and v != ""}

    started = time.perf_counter()
    if not (query or "").strip():
        rows = index.filter(**clean)
        report = {
            "query": "", "mode": "filter", "floor": None,
            "segment_count": len(index.segments),
            "considered": len(index.segments),
            "retained": len(rows),
            "results": [_with_timecode(r, rates) for r in rows[:top_k]],
            "weak": [], "abstained": not rows, "degraded": None, "error": None,
            "embed_backend": index.payload.get("embed_backend"),
            "truncated": max(0, len(rows) - top_k),
        }
        if clean:
            report["filters"] = clean
    else:
        report = index.search_report(query, top_k=top_k, mode=mode, floor=floor,
                                     filters=clean)
        report["results"] = [_with_timecode(r, rates) for r in report["results"]]
        report["weak"] = [_with_timecode(r, rates) for r in report["weak"]]

    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    report["embed"] = warm_state(key)
    return report


def detail(project_dir, segment_id: str) -> dict:
    """One segment's whole record, word timings included."""
    key = _key(project_dir)
    record = get_index(key).get_detail(segment_id)
    if "error" not in record:
        record = _with_timecode(dict(record), _clip_rates(key))
    return record
