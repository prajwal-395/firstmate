#!/usr/bin/env python3
"""Step 4.3 pre-bridge: the VFX candidate table the handoff tells the
model to read.

One row per SPINE BLOCK, because `target_block_position` is what the
step's `llm_outputs` schema asks the model to emit. A table keyed by
anything else names identifiers the answer cannot use.

This table used to be built from `data["a_roll_assignments"]` and it came
out hollow on every run - three failures in the same six lines:

  * `text` read `slot.get("text", "")` off a-roll assignment rows, which
    carry no `text` key, so every row was blank;
  * `vfx_suggested` was the literal string "No" on every row, described
    to the model as a pre-computed measurement but based on nothing;
  * `enhancement_spec` was pre-populated with a `color_wash` at
    intensity 0.5 on the first segment, unconditionally - a creative
    decision nobody made, presented to the model as a prior choice.

The table now carries the MEASUREMENTS the handoff says it is derived
from: per-block duration and camera movement, computed from `timed_spine`
and `semantic_analysis_documents`. `enhancement_spec` is not emitted -
the post-bridge writes it after the model answers.
"""
import os
import sys
import json

from library.tools.broll_coverage import (
    coverage_by_block, covering_assignment,
)
from library.tools.semantic_index import build_semantic_lookup
from library.tools.shot_colour import STILL_WIDTH, extract_still
from library.tools import still_vision as still_router
from library.tools.project_layout import Area, ProjectLayout
from library.tools.vfx_carriers import (
    picture_carriers,
)
from library.tools.vision_schema_adapter import (
    adapt_semantic_document,
    is_v3_profile,
)

#: What the still router is asked about each VFX still. Motion ONLY:
#: the designer already has the camera prose in `vfx_suggested` and
#: the colour numbers elsewhere, and a prompt that asks about
#: everything is answered about nothing. A still is one frame, so the
#: prompt says to admit where motion cannot be read off it.
VFX_STILL_PROMPT = (
    "Judge the MOTION in each still image, one line per file. "
    "Name the file, what moves in it (subject, camera, or nothing), "
    "and the direction of any motion you see (left, right, up, down, "
    "toward or away from the camera, or still). A still is one frame: "
    "where you cannot tell motion from a single frame, say so instead "
    "of guessing it. Do not judge exposure, colour or content.")

# How much of a block's line reaches the summary column. The full text is
# in `timed_spine`, which this step also routes; this table is an index
# into it, not a second copy.
TEXT_SUMMARY_CHARS = 80


def format_toon(headers, rows):
    if not rows:
        return f"[0]{{{','.join(headers)}}}\n"
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out


def _spine_blocks(data: dict) -> list:
    """The timeline spine, whichever of its two shapes arrives."""
    spine = data.get("timed_spine") or {}
    if not isinstance(spine, dict):
        return []
    blocks = spine.get("structure")
    if blocks is None:
        blocks = (spine.get("audio_spine") or {}).get("structure", [])
    return blocks if isinstance(blocks, list) else []


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path or ""))[0].lower()


def _build_clip_id_to_stem(data: dict) -> dict:
    """Map catalog clip_id to file stem from a_roll_assignments.

    The spine speaks catalog ids (``clip_006``) and semantic analysis
    documents speak file stems (``IMG_1806``). `a_roll_assignments`
    carries both - each entry's `video_segments` list has `clip_id`
    (catalog) and `source_file` (path) - so it is the join table.
    """
    mapping = {}
    aroll_raw = data.get("a_roll_assignments", [])
    if isinstance(aroll_raw, dict):
        aroll_raw = aroll_raw.get("a_roll_assignments") or []
    for entry in aroll_raw or []:
        if not isinstance(entry, dict):
            continue
        for seg in entry.get("video_segments") or []:
            if not isinstance(seg, dict):
                continue
            cid = seg.get("clip_id")
            sf = seg.get("source_file")
            if cid and sf:
                mapping[cid] = _stem(sf)
    return mapping


def _build_semantic_lookup(data: dict, clip_id_to_stem: dict) -> dict:
    """Map catalog clip_id to its semantic analysis document.

    The canonical join is `semantic_index.build_semantic_lookup`, which
    matches on the source path and reaches EVERY clip in the catalog
    (AGENTS.md 10.1).  The local join below reads `a_roll_assignments`
    and therefore only ever resolved the A-roll clips - so a B-roll
    cutaway's document was unreachable here and its camera came back as
    "no camera data".  The catalog is optional on this step, so the
    local join stays as the fallback and fills anything the canonical
    one missed.
    """
    catalog = data.get("clip_catalog")
    lookup = dict(build_semantic_lookup(
        data.get("semantic_analysis_documents"), catalog or []))

    docs_raw = data.get("semantic_analysis_documents", [])
    if not isinstance(docs_raw, list):
        return lookup

    # Key documents by every name they carry: file path stem, clip_id.
    by_stem = {}
    by_doc_id = {}
    for doc in docs_raw:
        if not isinstance(doc, dict):
            continue
        doc_id = doc.get("clip_id", "")
        if doc_id:
            by_doc_id[str(doc_id).lower()] = doc
        if doc.get("file_path"):
            by_stem[_stem(doc["file_path"])] = doc

    # Resolve each catalog clip_id the canonical join did not reach.
    for cid, stem in clip_id_to_stem.items():
        if cid in lookup:
            continue
        doc = by_stem.get(stem) or by_doc_id.get(stem)
        if doc:
            lookup[cid] = doc
    return lookup


def _camera_segments_in_range(doc: dict, source_start, source_end) -> list:
    """Camera segments that overlap the block's source range.

    Returns the subset of the document's camera[] array whose time range
    intersects [source_start, source_end]. When source bounds are None
    (non-speech blocks with no source clip), falls back to the whole clip.
    """
    adapted = adapt_semantic_document(doc) if is_v3_profile(doc) else doc
    camera = adapted.get("camera") or doc.get("camera") or []
    if not isinstance(camera, list):
        return []

    if source_start is None or source_end is None:
        return [s for s in camera if isinstance(s, dict)]

    overlapping = []
    for seg in camera:
        if not isinstance(seg, dict):
            continue
        seg_start = seg.get("start")
        seg_end = seg.get("end")
        if seg_start is None or seg_end is None:
            overlapping.append(seg)
            continue
        try:
            if float(seg_end) > float(source_start) and float(seg_start) < float(source_end):
                overlapping.append(seg)
        except (TypeError, ValueError):
            continue
    return overlapping


def _camera_description(doc: dict, source_start, source_end) -> str:
    """What the camera is doing in this block's source range.

    Returns a prose string like "stationary, steady" or
    "panning_left, handheld" - what the analyser measured, not a verdict.
    Returns "" when no camera data covers this range.
    """
    segs = _camera_segments_in_range(doc, source_start, source_end)
    if not segs:
        # Fall back to clip-level assessment.
        assessment = doc.get("assessment") or {}
        stability = assessment.get("camera_stability")
        if stability:
            return str(stability)
        return ""

    parts = []
    movements = []
    stabilities = []
    for seg in segs:
        m = seg.get("movement")
        if m and m not in movements:
            movements.append(str(m))
        s = seg.get("stability")
        if s and s not in stabilities:
            stabilities.append(str(s))

    if movements:
        parts.append(" -> ".join(movements))
    if stabilities:
        parts.append(" -> ".join(stabilities))
    return ", ".join(parts) if parts else ""


def _summary_text(block: dict) -> str:
    """What this segment says, in one line.

    The spoken line where there is one; otherwise the spine's own note
    for the beat, which is the only description a non-speech block has.
    """
    content = block.get("content")
    text = ""
    if isinstance(content, dict):
        text = content.get("text") or ""
    if not text:
        text = block.get("visual_note") or ""
    text = " ".join(str(text).split())
    if len(text) > TEXT_SUMMARY_CHARS:
        text = text[:TEXT_SUMMARY_CHARS - 3] + "..."
    return text


def _vfx_suggested(block: dict, camera_desc: str, clip_id=None,
                   from_broll: bool = False) -> str:
    """What the measurement says about this block, not a verdict.

    Reports block duration and camera movement - the two signals the
    handoff's criterion ("long AND static") is built from.

    A non-speech block names no `clip_id` on the spine, and this used to
    read `not measured (no source clip)` on every one of them - 5 of 13
    rows on 001, and the rows a cutaway effect would go on. The picture
    those blocks show is the cutaway `b_roll_assignments` names, in the
    same prompt, and a cutaway has a real camera description. The join
    turns a false statement of un-measurability into a measurement, and
    the cell says which clip it came from.
    See library/tools/broll_coverage.py.
    """
    if clip_id is None:
        clip_id = block.get("clip_id")
    if not clip_id:
        return "not measured (no source clip and no cutaway over it)"

    duration = None
    tl_start = block.get("timeline_start")
    tl_end = block.get("timeline_end")
    if tl_start is not None and tl_end is not None:
        try:
            duration = round(float(tl_end) - float(tl_start), 1)
        except (TypeError, ValueError):
            pass

    parts = []
    if duration is not None:
        parts.append(f"{duration}s")
    if camera_desc:
        parts.append(camera_desc)
    elif clip_id:
        parts.append("no camera data")
    if from_broll:
        parts.append(f"B-roll cutaway {clip_id}")
    return ", ".join(parts) if parts else "no data"


def build_vfx_candidates(data: dict) -> list:
    """One row per spine block, keyed by the position the answer names."""
    clip_id_to_stem = _build_clip_id_to_stem(data)
    semantic_lookup = _build_semantic_lookup(data, clip_id_to_stem)
    coverage = coverage_by_block(data.get("b_roll_assignments"))
    # Where each block's picture is, read off the same V1-membership rule
    # `compile_manifest` builds its V1 track from. An effect is a per-clip
    # Fusion comp and the renderer builds them on V1 and V2 alike, so a
    # cutaway block CAN carry one - but a block with no clip at all
    # cannot, and the plan used to be given no way to tell. This states
    # the fact per block; nothing is filtered or chosen for the model
    # (AGENTS.md 10.5). See library/tools/vfx_carriers.py.
    carriers = {
        str(row["position"]): row
        for row in picture_carriers(
            _spine_blocks(data), data.get("b_roll_assignments"))
    }
    rows = []
    for block in _spine_blocks(data):
        if not isinstance(block, dict):
            continue
        clip_id = block.get("clip_id")
        from_broll = False
        source_start = block.get("source_start")
        source_end = block.get("source_end")
        if not clip_id:
            entry = covering_assignment(block, coverage)
            if isinstance(entry, dict) and entry.get("clip_id"):
                clip_id = entry["clip_id"]
                from_broll = True
                # The cutaway's OWN source range, which is the range of
                # it that plays - not the spine block's, which is None.
                source_start = entry.get("video_in")
                source_end = entry.get("video_out")
        doc = semantic_lookup.get(clip_id, {}) if clip_id else {}
        camera_desc = _camera_description(doc, source_start, source_end)

        carrier = carriers.get(str(block.get("position")), {})
        rows.append({
            "segment_id": block.get("position"),
            "text": _summary_text(block),
            "vfx_suggested": _vfx_suggested(
                block, camera_desc, clip_id=clip_id, from_broll=from_broll),
            "picture_track": carrier.get("track", ""),
            "track_basis": carrier.get("basis", ""),
        })
    return rows


def _clip_sources(data: dict) -> dict:
    """Map clip_id to its source file, from the placed assignments.

    A-roll first (each entry's `video_segments` carry `clip_id` and
    `source_file`), then B-roll assignments (which carry both plus the
    played `video_in`/`video_out`). First placement wins: a clip used
    twice is still one file, and the still is of one of its sources.
    """
    sources: dict = {}
    aroll_raw = data.get("a_roll_assignments", [])
    if isinstance(aroll_raw, dict):
        aroll_raw = aroll_raw.get("a_roll_assignments") or []
    for entry in aroll_raw or []:
        if not isinstance(entry, dict):
            continue
        for seg in entry.get("video_segments") or []:
            if not isinstance(seg, dict):
                continue
            cid = seg.get("clip_id")
            if cid and cid not in sources and seg.get("source_file"):
                sources[cid] = seg["source_file"]
    broll_raw = data.get("b_roll_assignments", [])
    if isinstance(broll_raw, dict):
        broll_raw = broll_raw.get("b_roll_assignments") or []
    for entry in broll_raw or []:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("clip_id")
        if cid and cid not in sources and entry.get("source_file"):
            sources[cid] = entry["source_file"]
    return sources


def _motion_by_clip(data: dict) -> dict:
    """The routed temporal summaries keyed by clip id, or nothing.

    Tolerates the wrapped shape alongside the bare list, the same way
    the post-bridge does. Entries without peaks are kept - the caller
    tells "measured, no apex in this range" from "unmeasured" apart.
    """
    raw = data.get("temporal_event_indices", [])
    if isinstance(raw, dict):
        raw = raw.get("temporal_event_indices", [])
    by_clip: dict = {}
    for entry in raw or []:
        if isinstance(entry, dict) and entry.get("clip_id"):
            by_clip[str(entry["clip_id"])] = entry
    return by_clip


def _block_still_moment(block: dict, clip_id: str,
                        motion_by_clip: dict):
    """The source second the block's still is drawn at, and its basis.

    The block's first measured motion APEX inside its own source
    range - the moment an effect would punch - or the middle of the
    range where no apex was measured. Returns
    `(at_seconds, basis)` with basis one of `apex`, `middle` or
    `unranged` (no source range to draw from at all).
    """
    src_start = block.get("source_start")
    src_end = block.get("source_end")
    if (isinstance(src_start, bool) or isinstance(src_end, bool)
            or not isinstance(src_start, (int, float))
            or not isinstance(src_end, (int, float))
            or float(src_end) <= float(src_start)):
        return None, "unranged"
    summary = motion_by_clip.get(str(clip_id)) or {}
    apexes = sorted(
        float(p["time"]) for p in summary.get("motion_peaks", [])
        if isinstance(p, dict) and p.get("kind") == "apex"
        and isinstance(p.get("time"), (int, float))
        and not isinstance(p.get("time"), bool)
        and float(src_start) - 0.1 <= float(p["time"])
        <= float(src_end) + 0.1)
    if apexes:
        return round(apexes[0], 3), "apex"
    return round((float(src_start) + float(src_end)) / 2.0, 3), "middle"


def draw_vfx_stills(data: dict, project_folder: str) -> tuple:
    """One representative still per VFX-candidate block. Returns (block, paths).

    The still is drawn at the block's first measured motion apex where
    one exists, else the middle of the block's source range, at
    `STILL_WIDTH` wide, into the plan_vfx stills area - so the layout
    answers which step wrote it. A drawn still is reused; a block
    whose still could not be drawn is NAMED in the block, never
    quietly absent.
    """
    if not project_folder:
        print("  No project_folder: no VFX stills drawn",
              file=sys.stderr)
        return "", []
    directory = ProjectLayout(project_folder).write_dir(
        Area.VFX_STILLS, step="plan_vfx")

    sources = _clip_sources(data)
    motion_by_clip = _motion_by_clip(data)
    coverage = coverage_by_block(data.get("b_roll_assignments"))

    drawn, missing, paths = [], [], []
    for block in _spine_blocks(data):
        if not isinstance(block, dict):
            continue
        position = block.get("position")
        clip_id = block.get("clip_id")
        src_start = block.get("source_start")
        src_end = block.get("source_end")
        if not clip_id:
            entry = covering_assignment(block, coverage)
            if isinstance(entry, dict) and entry.get("clip_id"):
                clip_id = entry["clip_id"]
                src_start = entry.get("video_in")
                src_end = entry.get("video_out")
        ranged = dict(block)
        if src_start is not None:
            ranged["source_start"] = src_start
        if src_end is not None:
            ranged["source_end"] = src_end
        source = sources.get(clip_id, "") if clip_id else ""
        at_seconds, basis = _block_still_moment(
            ranged, clip_id, motion_by_clip)
        name = f"block_{position}__vfx.jpg"
        path = str(directory / name)
        if (not source or at_seconds is None or not (
                (os.path.exists(path) and os.path.getsize(path) > 0)
                or extract_still(source, path,
                                 at_seconds=at_seconds))):
            missing.append(
                f"{position} ("
                + ("no source clip" if not clip_id
                   else "no source file" if not source
                   else "no source range" if at_seconds is None
                   else "ffmpeg drew nothing")
                + ")")
            continue
        drawn.append({"position": position, "clip_id": clip_id,
                      "file": name, "basis": basis,
                      "at_seconds": at_seconds})
        paths.append(path)

    print(f"  {len(drawn)} VFX still(s) at {directory}"
          + (f"; {len(missing)} not drawn" if missing else ""),
          file=sys.stderr)
    if not drawn and not missing:
        return "", []
    lines = [
        (f"VFX stills - one representative frame per candidate block, "
         f"drawn at the block's first measured motion apex where one "
         f"exists (else the middle of its range), {STILL_WIDTH}px "
         f"wide, at:"),
        f"  directory: {directory}",
    ]
    for still in drawn:
        lines.append(
            f"  {still['position']} (clip {still['clip_id']}, "
            f"{still['basis']} {still['at_seconds']}s into the clip): "
            f"{still['file']}")
    for name in missing:
        lines.append(f"  NOT DRAWN: {name}")
    return "\n".join(lines), paths


def observe_vfx_stills(paths: list, project_folder: str) -> dict:
    """What the still router saw in the VFX stills.

    Asked through `library/tools/still_vision.py`: the driving LLM's
    own vision first, gemma4 where no host drives. The text is a second
    reading beside the deterministic numbers, not a replacement for
    them - and where nothing answered, the absence is STATED with the
    still paths left in `vfx_shot_stills` for the driver to open.
    """
    if not paths:
        return {"observed_by": "none",
                "reason": ("no stills were drawn, so there was nothing "
                           "to look at"),
                "text": ""}
    if not project_folder:
        return {"observed_by": "none",
                "reason": ("no project_folder: the still-vision request "
                           "has nowhere to live - open the stills in "
                           "`vfx_shot_stills` with your own vision"),
                "text": ""}
    try:
        text = still_router.inspect_stills(
            VFX_STILL_PROMPT, list(paths),
            project_folder=project_folder,
            step_id="step_4_03_plan_vfx",
            label="vfx stills")
    except Exception as exc:  # noqa: BLE001 - an absence with the reason
        return {"observed_by": "none",
                "reason": (f"{type(exc).__name__}: {exc} - open the "
                           f"stills in `vfx_shot_stills` with your own "
                           f"vision"),
                "text": ""}
    driving = still_router.resolve_harness()
    if driving is None:
        observed_by = "gemma4 fallback (no host drives this run)"
    else:
        observed_by = (f"host ({driving}) - the driving LLM's own vision")
    return {"observed_by": observed_by, "text": text}


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    rows = build_vfx_candidates(data)
    vfx_toon = format_toon(
        ["segment_id", "text", "vfx_suggested", "picture_track",
         "track_basis"],
        rows,
    )

    # The picture beside the measurements: one still per candidate
    # block, drawn at the block's first measured motion apex where one
    # exists (the moment an effect would punch), else the middle of
    # the block's range - and what the still router saw in them.
    project_folder = data.get("project_folder", "")
    stills_block, still_paths = draw_vfx_stills(
        data, project_folder)

    # No `enhancement_spec` stub. This bridge used to emit
    # `{"motion_graphics": [], "visual_effects": [{"effect_type":
    # "color_wash", "intensity": 0.5}]}` - a creative decision nobody made,
    # presented to the model as a prior choice. The post-bridge writes
    # the real `enhancement_spec` after the model answers.
    # `picture_track` and `track_basis` are DEFINED in this step's
    # handoff.md ("Context data available"), not shipped beside the table.
    # They travelled as a `vfx_candidates_legend` dict only while that
    # file was under the captain's freeze, lifted 2026-09-09.
    compressed = {
        "vfx_candidates_toon": vfx_toon,
        "vfx_shot_stills": stills_block,
        "still_motion_notes": observe_vfx_stills(
            still_paths, project_folder),
    }

    print(json.dumps(compressed))


if __name__ == "__main__":
    main()
