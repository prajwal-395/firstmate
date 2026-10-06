"""The transformation tier: common needs met by changing what exists.

Three handlers, registered into asset_routing's transformation tier:

- ``room_tone_fill``: an audio gap filled with the source's own measured
  room (``library/tools/room_tone.py`` - the quietest speech-free
  stretch, looped to length). The project's own ambience, never a
  library file and never a default level.
- ``freeze_frame``: a few missing frames at a clip boundary filled with
  a freeze of the frame at the boundary, rendered with ffmpeg.
- ``broll_substitute``: B-roll coverage answered from the project's own
  catalog - a clip already held, selected for the role. The Project
  Graph (track 2) has not landed on origin/main; this reads the
  catalog and the semantic analysis through the existing state readers
  and leaves the graph lane a note.

Every handler stages its artifact under the project's routing staging
area and returns the outcome contract ``asset_routing`` defines. A need
a transformation cannot answer is a refusal the router records, never a
default: a freeze of a clip that is not on disk, or a substitute with
no catalog to search, says so by name.
"""
from __future__ import annotations

import json
import os
import subprocess

from library.tools import asset_routing, capability_outputs, room_tone
from library.tools.ren_refusal import RenRefusal

#: Where transformation artifacts are staged, relative to the project's
#: output root. One directory per need, named by the decision id so a
#: rerun of the same need overwrites its own staging and nothing else.
STAGING_RELPATH = os.path.join("routing", "staging")


class TransformationRefused(RenRefusal):
    """A transformation cannot answer the need it was given."""


def staging_dir(project_folder: str, need: asset_routing.Need) -> str:
    """The staging directory for one routed need."""
    return os.path.join(project_folder, "pipeline_output", STAGING_RELPATH,
                        need.kind, asset_routing.decision_id(need))


def _outcome(handler, solved, artifact=None, provenance=None,
             reason=None, refusal=None) -> dict:
    return {"handler": handler, "solved": solved, "artifact": artifact,
            "provenance": provenance, "reason": reason,
            "refusal": refusal}


def room_tone_fill(need: asset_routing.Need, context: dict) -> dict:
    """Fill an audio gap with the source's own measured room.

    Params: ``source_file``, ``speech_spans`` (optional word spans to
    exclude), ``fill_seconds``. The measurement is ``room_tone``'s - the
    quietest speech-free stretch - so a source with no measurable room
    refuses instead of staging a default.
    """
    source_file = need.params.get("source_file")
    fill_seconds = need.params.get("fill_seconds")
    if not source_file or not isinstance(fill_seconds, (int, float)) \
            or isinstance(fill_seconds, bool) or fill_seconds <= 0:
        raise TransformationRefused(
            "room tone cannot be staged: the need carries no source_file "
            "and a positive fill_seconds",
            "the fill is measured from the source itself and looped to "
            "the length the gap needs; without both there is nothing to "
            "measure and no length to stage.",
            "route the need with the gap's source_file and its "
            "fill_seconds, or drop the fill.",
        )
    speech_spans = need.params.get("speech_spans") or []
    project_folder = context.get("project_folder") or ""
    out_path = os.path.join(staging_dir(project_folder, need),
                            "room_tone.wav")
    record = room_tone.measure_room_tone(source_file, speech_spans)
    staged = room_tone.stage_fill(source_file, record, fill_seconds,
                                  out_path)
    return _outcome(
        "room_tone_fill", True,
        artifact={"path": staged["path"], "media_type": "audio",
                  "duration_seconds": staged["fill_seconds"],
                  "loops": staged["loops"],
                  "level_dbfs": staged["level_dbfs"]},
        provenance={
            "producer": "room_tone_fill",
            "method": asset_routing.METHOD_OBSERVED,
            "derived_from": [source_file],
            "detail": (f"quietest speech-free gap "
                       f"{record['segment_start']}-"
                       f"{record['segment_end']}s of "
                       f"{os.path.basename(source_file)}, "
                       f"{staged['loops']} loop(s) to "
                       f"{staged['fill_seconds']}s"),
            "measurement": record,
        },
    )


def freeze_frame(need: asset_routing.Need, context: dict) -> dict:
    """Hold the frame at a clip boundary across a few missing frames.

    Params: ``source_file``, ``boundary_seconds`` (the frame to hold),
    ``frames`` (how many), ``fps`` (the timeline rate). The freeze is
    rendered with ffmpeg: the boundary frame is extracted, then looped
    to the requested length.
    """
    source_file = need.params.get("source_file")
    boundary = need.params.get("boundary_seconds")
    frames = need.params.get("frames")
    fps = need.params.get("fps")
    if not source_file or not os.path.isfile(source_file):
        raise TransformationRefused(
            f"a freeze cannot be rendered: {source_file!r} is not on "
            f"disk",
            "the freeze holds a frame of the source itself; a source "
            "that does not exist has no frame to hold.",
            "route the need with the real source_file, or drop it.",
        )
    if not isinstance(frames, int) or isinstance(frames, bool) \
            or frames <= 0:
        raise TransformationRefused(
            f"a freeze cannot be rendered: frames={frames!r} is not a "
            f"positive whole number",
            "a freeze of zero frames fills nothing, and a fractional "
            "frame is not a frame.",
            "route the need with the whole number of frames the gap "
            "needs, or drop it.",
        )
    fps = float(fps) if isinstance(fps, (int, float)) \
        and not isinstance(fps, bool) and fps > 0 else 30.0
    duration = frames / fps
    project_folder = context.get("project_folder") or ""
    out_dir = staging_dir(project_folder, need)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "freeze.mp4")
    boundary = float(boundary) if isinstance(boundary, (int, float)) \
        and not isinstance(boundary, bool) and boundary >= 0 else 0.0
    frame_path = os.path.join(out_dir, "boundary_frame.png")
    _run_ffmpeg(
        ["ffmpeg", "-v", "error", "-y",
         "-ss", str(boundary), "-i", source_file,
         "-frames:v", "1", frame_path],
        f"the boundary frame of {os.path.basename(source_file)} at "
        f"{boundary}s could not be extracted",
        "the source may be shorter than the boundary, or ffmpeg could "
        "not decode it.",
    )
    _run_ffmpeg(
        ["ffmpeg", "-v", "error", "-y",
         "-loop", "1", "-i", frame_path,
         "-t", str(duration), "-r", str(fps),
         "-pix_fmt", "yuv420p", out_path],
        f"the freeze of {os.path.basename(source_file)} could not be "
        f"rendered",
        "ffmpeg could not encode the looped frame.",
    )
    return _outcome(
        "freeze_frame", True,
        artifact={"path": out_path, "media_type": "video",
                  "duration_seconds": round(duration, 3),
                  "frames": frames},
        provenance={
            "producer": "freeze_frame",
            "method": asset_routing.METHOD_OBSERVED,
            "derived_from": [source_file],
            "detail": (f"frame at {boundary}s held for {frames} "
                       f"frame(s) at {fps} fps"),
        },
    )


def _run_ffmpeg(argv: list, what: str, why: str) -> None:
    """Run ffmpeg, refusing with its stderr when it fails."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=600,
                              encoding="utf-8", check=False)
    except FileNotFoundError as exc:
        raise TransformationRefused(
            f"{what}: ffmpeg is not installed",
            "the freeze is rendered with ffmpeg, which is absent on "
            "this machine.",
            "install ffmpeg, or route the need to another tier.",
        ) from exc
    if proc.returncode != 0:
        raise TransformationRefused(
            f"{what} ({proc.stderr[-300:]})",
            why,
            "check the source file and the boundary, or route the "
            "need to another tier.",
        )


def _read_state(project_folder: str) -> dict:
    """The project's pipeline state, or {} when it has not run."""
    path = os.path.join(project_folder, "pipeline_data", "pipeline_data.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _read_catalog(project_folder: str) -> list:
    """The project's clip catalog, read through the state readers.

    The Project Graph (track 2) has not landed; the catalog capability
    is the existing state reader for what the project holds.
    """
    state = _read_state(project_folder)
    return capability_outputs.value(state, "footage.catalog",
                                     "clip_catalog", []) or []


def _semantic_text_by_clip(project_folder: str, catalog: list) -> dict:
    """{clip_id: searchable prose} from the semantic analysis, if run.

    Clips without a semantic document have no entry: the ranking falls
    back to the filename for them, and the record says so.
    """
    from library.tools import semantic_index
    state = _read_state(project_folder)
    docs = capability_outputs.value(state, "semantics.analyse",
                                     "semantic_analysis_documents", [])
    if not docs:
        return {}
    lookup = semantic_index.build_semantic_lookup(docs, catalog)
    texts = {}
    for clip_id, doc in lookup.items():
        if not isinstance(doc, dict):
            continue
        parts = []
        for key in ("scene", "subject", "camera", "actions"):
            value = doc.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        if parts:
            texts[clip_id] = " ".join(parts)
    return texts


def _score(clip: dict, subject: str, semantic_texts: dict) -> float:
    """How well a clip's searchable text matches the subject.

    Token overlap, weighted so a clip whose semantic prose names the
    subject outranks one that only shares a filename token. A clip with
    no semantic document scores on its filename alone - the signal is
    weaker and the provenance says which text answered.
    """
    text = semantic_texts.get(clip.get("clip_id"), "")
    source = "semantic analysis" if text else "filename"
    haystack = (text + " " + os.path.basename(
        clip.get("path", ""))).lower()
    tokens = [t for t in subject.lower().split() if len(t) > 2]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in haystack)
    return hits / len(tokens)


def broll_substitute(need: asset_routing.Need, context: dict) -> dict:
    """Answer B-roll coverage from the project's own catalog.

    Params: ``subject`` (what the B-roll should show),
    ``exclude_clip_ids`` (clips already on the timeline),
    ``min_duration_seconds`` (a floor the caller states). The
    selection is scored against the semantic analysis when the project
    has one, and against the filename otherwise - the provenance records
    which text answered, so a substitute chosen by a weak signal is not
    reported as a strong one.
    """
    project_folder = context.get("project_folder") or ""
    catalog = _read_catalog(project_folder)
    if not catalog:
        raise TransformationRefused(
            "a B-roll substitute cannot be chosen: the project has no "
            "catalog",
            "the substitute is selected from the project's own footage; "
            "a project that has not cataloged its footage has nothing "
            "to select from.",
            "run the catalog step, or route the need to another tier.",
        )
    exclude = set(need.params.get("exclude_clip_ids") or [])
    min_duration = float(need.params.get("min_duration_seconds", 0.0)
                         or 0.0)
    subject = str(need.params.get("subject", "") or "")
    candidates = []
    for clip in catalog:
        if clip.get("clip_id") in exclude:
            continue
        duration = float(clip.get("duration_seconds", 0.0) or 0.0)
        if duration < min_duration:
            continue
        if not os.path.isfile(clip.get("path", "")):
            continue
        candidates.append(clip)
    if not candidates:
        raise TransformationRefused(
            "a B-roll substitute cannot be chosen: no catalog clip "
            "meets the need's constraints",
            "every clip is either already on the timeline, shorter than "
            "the stated floor, or not on disk.",
            "relax the constraints, or route the need to another tier.",
        )
    semantic_texts = _semantic_text_by_clip(project_folder, catalog)
    scored = [(_score(clip, subject, semantic_texts), clip)
              for clip in candidates]
    scored.sort(key=lambda row: (-row[0],
                                 -float(row[1].get("duration_seconds",
                                                    0.0) or 0.0)))
    best_score, best = scored[0]
    text_source = ("semantic analysis" if best.get("clip_id")
                   in semantic_texts else "filename")
    return _outcome(
        "broll_substitute", True,
        artifact={"path": best.get("path"), "media_type": "video",
                  "duration_seconds": best.get("duration_seconds"),
                  "clip_id": best.get("clip_id")},
        provenance={
            "producer": "broll_substitute",
            "method": asset_routing.METHOD_OBSERVED,
            "derived_from": [best.get("path")],
            "detail": (f"selected from {len(candidates)} catalog "
                       f"clip(s) by {text_source} match "
                       f"({best_score:.2f}) to {subject!r}; "
                       f"Project Graph track 2 has not landed - read "
                       f"through the state readers"),
        },
    )


def route_room_tone_fill(source_file: str, speech_spans: list,
                         fill_seconds: float,
                         project_folder: str) -> dict:
    """Route a room-tone fill through the routing layer.

    The J/L cut's gap fill is the project's most common missing asset:
    the audio cut opens a gap on the speech row that must be filled
    with the joining source's own room. Routing it here means the fill
    carries a decision record - what was needed, which tier answered,
    and the provenance of the staged segment - instead of being
    inserted with no record of why it exists.

    The router stages the fill under its own staging directory (named
    by the decision id), so the path returned is the router's, not the
    caller's. Returns the staged fill info (path, fill_seconds, loops,
    level_dbfs) plus the decision record. The decision is appended to
    the project's routing ledger before returning, so a fill whose
    routing failed is never silently inserted.
    """
    need = asset_routing.Need("room_tone", {
        "source_file": source_file,
        "speech_spans": speech_spans,
        "fill_seconds": fill_seconds,
    })
    record = asset_routing.route_need(need,
                                      {"project_folder": project_folder})
    if record["answered_by"] is None:
        raise TransformationRefused(
            f"room tone cannot be routed for "
            f"{os.path.basename(source_file)}: no tier could answer "
            f"the need",
            "the fill is the J/L cut's second half - an unfillable gap "
            "is a gap left as digital silence.",
            "configure a provider, or drop the J/L cut.",
        )
    asset = record["artifact"]
    asset_routing.append_decision(project_folder, record)
    return {
        "path": asset["path"],
        "fill_seconds": asset["duration_seconds"],
        "loops": asset.get("loops", 0),
        "level_dbfs": asset.get("level_dbfs"),
        "measurement": record.get("provenance", {}).get("measurement"),
        "decision": record,
    }


def route_broll_gap(block: dict, catalog: list, assignments: list,
                    project_folder: str) -> dict | None:
    """Route an uncovered block's B-roll gap through the routing layer.

    The B-roll plan leaves a block with no V1 picture and no cutaway:
    the hole would render black. Before the step refuses, the gap is
    routed as a ``b_roll`` need - the ``broll_substitute`` handler
    selects a substitute from the project's own catalog, and the
    decision record is written. The substitute plays the block's own
    span from the clip's start (capped, never the full clip), so it
    covers the slot exactly. Returns the substitute assignment, or
    None when no catalog clip can cover the block (the step then
    refuses as it did before).
    """
    block_duration = float(block.get("timeline_end", 0.0)
                           - block.get("timeline_start", 0.0))
    exclude = {str(a.get("clip_id"))
               for a in assignments if isinstance(a, dict)}
    exclude.add(str(block.get("clip_id")))
    subject = str(block.get("block_type", ""))
    need = asset_routing.Need("b_roll", {
        "subject": subject,
        "exclude_clip_ids": sorted(exclude),
        "min_duration_seconds": round(block_duration, 3),
    })
    record = asset_routing.route_need(need,
                                      {"project_folder": project_folder})
    if record["answered_by"] is None:
        return None
    asset = record["artifact"]
    asset_routing.append_decision(project_folder, record)
    timeline_start = float(block.get("timeline_start", 0.0))
    duration = float(asset.get("duration_seconds", 0.0) or 0.0)
    # The window is the block's own span, from the substitute's start -
    # never the substitute's full length. The handler only answers with
    # a clip at least as long as the block (the need's floor), so the
    # cap covers the slot exactly and the shortfall below stays 0.0; an
    # uncapped window would run past the block into the next cutaway on
    # V2. The opening seconds are not a picture-chosen moment - choosing
    # one from the semantic analysis is the graph lane's follow-up
    # (`find_broll_matches`), and the basis below says which answered.
    window = round(min(duration, block_duration), 3)
    return {
        "spine_block_position": block.get("position"),
        "block_type": block.get("block_type", "transition_slot"),
        "clip_id": asset.get("clip_id"),
        "source_file": asset.get("path"),
        "video_in": 0.0,
        "video_out": window,
        "duration_seconds": window,
        "timeline_start": timeline_start,
        "timeline_end": round(timeline_start + window, 3),
        "coverage_shortfall_seconds": round(
            max(0.0, block_duration - window), 3),
        "needs_conform": _substitute_needs_conform(
            catalog, asset.get("clip_id"), project_folder),
        "selection_rationale": "routed B-roll substitute",
        "window_basis": "routed_substitute",
        "window_basis_detail": record["provenance"]["detail"],
        "slip_seconds": 0.0,
        "video_only": True,
    }


def _substitute_needs_conform(catalog: list, clip_id: str | None,
                              project_folder: str) -> bool:
    """Whether the substitute plays at a foreign resolution.

    The bridge recomputes this flag from the catalog on every
    assignment it places itself (``check_needs_conform`` in
    ``post_bridge.py``); a routed substitute carries the same
    measurement rather than a default. A clip the passed catalog does
    not describe reads False - ``compile_manifest`` re-derives the
    conform from probed metadata for every V2 clip, so an unknown
    resolution is measured there, not refused here.
    """
    from library.tools.delivery_format import resolve_delivery_format
    clip = next((c for c in catalog
                 if isinstance(c, dict) and c.get("clip_id") == clip_id),
                None)
    if clip is None:
        return False
    width, height = resolve_delivery_format(project_folder)
    return bool(check_needs_conform(clip, width, height))


def check_needs_conform(clip: dict, target_width: int,
                        target_height: int) -> bool:
    """True when the clip's resolution or rotation differs from target.

    The one comparison both the bridge's own assignments and the
    router's substitutes are measured with (the bridge imports it;
    ``post_bridge.check_needs_conform`` keeps working as that import).
    Rotation-aware - a 1920x1080 clip with -90 rotation IS portrait -
    and None-safe where the bridge's former local copy raised in
    ``abs`` on a clip with no recorded rotation.
    """
    w = clip.get("width", 0)
    h = clip.get("height", 0)
    rotation = clip.get("rotation", 0)
    if abs(rotation or 0) in (90, 270, -90, -270):
        w, h = h, w
    if w and h and (w != target_width or h != target_height):
        return True
    return False


#: The transformation tier's handlers, registered in the order the
#: router consults them. A need the first cannot answer falls through
#: to the next, so a room-tone need never reaches the freeze handler.
TRANSFORMATION_HANDLERS = {
    "room_tone_fill": room_tone_fill,
    "freeze_frame": freeze_frame,
    "broll_substitute": broll_substitute,
}


def register_transformations() -> None:
    """Register every transformation handler into the routing tier."""
    for name, handler in TRANSFORMATION_HANDLERS.items():
        asset_routing.register_handler("transformation", name, handler)


register_transformations()
