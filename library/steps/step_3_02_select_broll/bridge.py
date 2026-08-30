#!/usr/bin/env python3
"""Step 3.02 pre-bridge: build the B-roll candidate table for the LLM.

The table lists what the vision analysis actually measured about each
clip: what it shows, how it is framed, how steady it is, who is in it and
which stretch of it may be cut from.

It used to be built per A-roll slot, alphabetically, truncated to 12 rows
of a 180-character single-frame caption. Every slot therefore received the
same 12 clips in the same order, four clips were never offered to any slot
at all, and the model - having no content to choose on - selected straight
down the list. One row per clip carries strictly more information in
fewer rows: the slots themselves, with their timings and visual notes,
already reach the model in `timed_spine` and `a_roll_assignments`.

The row order carries NO relevance ranking. A slot-relevance score would
need a matching rule this pipeline has never settled, and inventing one
would repeat the mistake in a subtler form; the table says so explicitly
so the model does not read list order as preference.

`footage_analysis_reference` is the second thing this bridge builds. The
step used to carry three views of one vision analysis - this table, the
`view:picture` action rows, and the raw `semantic_analysis_documents`
the other two are rendered from - at 60.7% of its whole context, of
which the raw structure alone was 40.5%. The structure is not deleted:
it is written to this step's own directory in full and the prompt gets
`brief_reference.build_reference`'s map instead, the same mechanism #295
built for the captain's brief and #299 applied to the SFX catalogue.
`library/tools/footage_reference.py` holds the measurement, the shape
and what each of the three views uniquely carries.

It also draws ONE FRAME STRIP PER CANDIDATE CUTAWAY WINDOW and puts a
reference to them in the prompt (`broll_window_frames`). The table above
is prose about what a clip DOES; a strip is a picture of what the window
LOOKS like, which is the half no step choosing a picture has ever been
shown. See `library/tools/window_frames.py`.
"""
import os
import sys
import json

from library.tools.brief_reference import build_reference
from library.tools.footage_reference import (
    DOCUMENT_NAME,
    REFERENCE_DOCUMENT_NAME,
    REFERENCE_WHY,
    footage_document,
)
from library.tools.pipeline_validation import require_keys
from library.tools.project_layout import Area, ProjectLayout, layout_for
from library.tools.semantic_index import build_semantic_lookup, clip_observations
from library.tools import window_frames as wf

# Upper bound on the candidate table. One row per clip, so this only binds
# on unusually large catalogs; whatever it drops is reported, never
# silently truncated.
MAX_CANDIDATE_CLIPS = 60

# Descriptions are time-bounded scene prose, not a caption. Long enough to
# carry every segment of a multi-scene clip.
MAX_DESCRIPTION_CHARS = 600

# Every other cell is a short label or list. Retired-schema documents put
# a whole model transcript in `analysis.objects`, so the cap is what keeps
# one bad legacy field from swamping the table.
MAX_CELL_CHARS = 200

CANDIDATE_HEADERS = [
    "clip_id", "duration_s", "used_as_aroll", "framing", "stability",
    "camera_move", "content_type", "usable_range", "subjects", "description",
]


def format_toon(headers, rows):
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out


def _cell(value, limit: int = MAX_CELL_CHARS) -> str:
    """A TOON cell: single-line, tab-free, length-capped."""
    text = " ".join(str(value or "").split())
    return text[:limit] if limit else text


def _slot_aroll_clip(slot: dict) -> str:
    """The clip this slot's A-roll already shows."""
    vsegs = slot.get("video_segments") or []
    if vsegs and vsegs[0].get("clip_id"):
        return vsegs[0]["clip_id"]
    return slot.get("source_clip_id") or slot.get("clip_id") or ""


def write_footage_reference(project_folder: str, documents,
                            catalog_entries) -> str:
    """Write the analysis out, and return the map that points at it.

    The path recorded is ABSOLUTE, because it is the path the model has
    to use and it runs from wherever the harness put it, not from the
    project folder. Same reasoning as the brief's and the SFX
    catalogue's, and the same function builds the map.
    """
    path = layout_for(project_folder).write_path(
        Area.FOOTAGE_ANALYSIS, DOCUMENT_NAME, step="select_broll")
    document = footage_document(documents, catalog_entries)
    path.write_text(document, encoding="utf-8")
    return build_reference(
        str(path.resolve()), document,
        document_name=REFERENCE_DOCUMENT_NAME,
        why_referenced=REFERENCE_WHY,
    )


def cutaway_slot_seconds(timed_spine: dict) -> list:
    """The lengths of the spine blocks a cutaway covers.

    A window's length IS the slot's length (`post_bridge` passes
    `block_duration` straight to `choose_window`), so these come off the
    spine rather than from a constant. Speech blocks are left out: a
    cutaway covers a non-speech block, which is the coverage requirement
    `compile_manifest._assert_timeline_fully_covered` enforces.
    """
    blocks = timed_spine.get("structure") or (
        timed_spine.get("audio_spine") or {}).get("structure") or []
    seconds = set()
    for block in blocks:
        if block.get("block_type") in ("speech", "hook"):
            continue
        start, end = block.get("timeline_start"), block.get("timeline_end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            length = round(float(end) - float(start), 3)
            if length > 0:
                seconds.add(length)
    return sorted(seconds)


def build_window_frames(data: dict, catalog_entries: list,
                        semantic: dict) -> str:
    """Draw a strip for every candidate cutaway window, and map them.

    Nothing is ranked, filtered or shortlisted - whatever selects a
    shortlist becomes the chooser (AGENTS.md 10.5). A window whose strip
    could not be drawn is NAMED, never dropped from the map.
    """
    project_folder = data.get("project_folder") or ""
    if not project_folder:
        print("  No project_folder: no window frames drawn", file=sys.stderr)
        return ""

    slots = cutaway_slot_seconds(data.get("timed_spine") or {})
    if not slots:
        print("  The spine has no non-speech block, so there is no cutaway "
              "slot and no window to draw", file=sys.stderr)
        return ""

    temporal_raw = data.get("temporal_event_indices") or []
    if isinstance(temporal_raw, dict):
        temporal_raw = temporal_raw.get("temporal_event_indices", [])
    index_lookup = {i.get("clip_id"): i for i in temporal_raw
                    if isinstance(i, dict)}

    directory = ProjectLayout(project_folder).write_dir(
        Area.WINDOW_FRAMES, step="select_broll")

    rows, missing, drawn = [], [], 0
    for clip in catalog_entries:
        clip_id = clip.get("clip_id")
        source_file = clip.get("source_file") or clip.get("path") or \
            clip.get("file_path")
        duration = clip.get("duration_seconds") or 0.0
        if not clip_id or not source_file or not os.path.exists(source_file):
            continue
        fps = clip.get("frame_rate") or 0.0
        anchors = wf.window_anchors(
            semantic.get(clip_id, {}), index_lookup.get(clip_id, {}),
            float(duration), slots,
        )
        for anchor in anchors:
            if drawn >= wf.MAX_STRIPS:
                missing.append(
                    f"{clip_id}@{anchor['video_in']:.3f}s (past the "
                    f"{wf.MAX_STRIPS}-strip bound)")
                continue
            times = wf.sample_times(
                anchor["video_in"], anchor["strip_end"], float(fps))
            # The name carries what was DRAWN, so a strip drawn under an
            # older sampling rule is a different file and is never read
            # as this one. See library/tools/window_frames.py.
            name = wf.strip_filename(clip_id, anchor["video_in"], times)
            drawn += 1
            if not wf.draw_strip(source_file, times, str(directory / name)):
                missing.append(f"{clip_id}@{anchor['video_in']:.3f}s")
                continue
            rows.append({
                "clip_id": clip_id,
                "video_in": anchor["video_in"],
                "strip_end": anchor["strip_end"],
                "frames": len(times),
                "file": name,
            })

    print(f"  {len(rows)} window frame strip(s) at {directory}"
          + (f"; {len(missing)} not drawn" if missing else ""),
          file=sys.stderr)
    if not rows:
        return ""
    return wf.build_block(str(directory), rows, missing)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    require_keys(data, ["clip_catalog", "a_roll_assignments"], "step_3_02_select_broll/bridge.py")

    aroll = data.get("a_roll_assignments", [])
    catalog_list = data.get("clip_catalog", [])

    if isinstance(catalog_list, list):
        catalog = {c.get("clip_id"): c for c in catalog_list}
        catalog_entries = catalog_list
    else:
        catalog = catalog_list
        catalog_entries = list(catalog_list.values())

    semantic = build_semantic_lookup(
        data.get("semantic_analysis_documents", {}), catalog_entries
    )

    slots = aroll if isinstance(aroll, list) else aroll.get(
        "a_roll_assignments", aroll.get("timeline_segments", []))

    # Clips used anywhere as A-roll: usable as B-roll elsewhere, but they
    # are the least interesting choice, so rank them last.
    aroll_clips = {_slot_aroll_clip(s) for s in slots}

    candidates_rows = []
    clips_without_description = []

    for clip_id, clip_info in catalog.items():
        doc = semantic.get(clip_id, {})
        observed = clip_observations(doc)
        desc = observed.get("description", "")
        if not desc:
            clips_without_description.append(clip_id)
            continue
        candidates_rows.append({
            "clip_id": clip_id,
            "duration_s": round(clip_info.get("duration_seconds", 0.0), 2),
            "used_as_aroll": "yes" if clip_id in aroll_clips else "no",
            "framing": _cell(observed.get("framing")),
            "stability": _cell(observed.get("stability")),
            "camera_move": _cell(observed.get("movement")),
            "content_type": _cell(observed.get("content_type")),
            "usable_range": _cell(observed.get("usable_ranges")),
            "subjects": _cell(observed.get("subjects")),
            "description": _cell(desc, MAX_DESCRIPTION_CHARS),
        })

    if slots and not candidates_rows:
        # An empty candidate table means the LLM has nothing to choose
        # from and the step can only produce filler. Say so and stop.
        missing = sorted(set(clips_without_description))
        print(json.dumps({
            "error": (
                f"No B-roll candidates for {len(slots)} A-roll slot(s): no "
                f"catalog clip has a usable semantic description. Clips "
                f"without one: {missing[:10]}"
            ),
            "step": "3.02_bridge",
        }))
        sys.exit(1)

    # Clips already carrying A-roll are listed last: they are the least
    # interesting cutaway, not a ranking of the rest.
    candidates_rows.sort(key=lambda r: (r["used_as_aroll"] == "yes", r["clip_id"]))

    if len(candidates_rows) > MAX_CANDIDATE_CLIPS:
        dropped = [r["clip_id"] for r in candidates_rows[MAX_CANDIDATE_CLIPS:]]
        print(
            f"  Candidate table capped at {MAX_CANDIDATE_CLIPS} clips; "
            f"not offered: {', '.join(dropped)}",
            file=sys.stderr,
        )
        candidates_rows = candidates_rows[:MAX_CANDIDATE_CLIPS]

    if clips_without_description:
        print(
            f"  {len(clips_without_description)} catalog clip(s) have no "
            f"semantic description and are not offered as B-roll: "
            f"{', '.join(sorted(set(clips_without_description)))}",
            file=sys.stderr,
        )

    candidates_toon = format_toon(CANDIDATE_HEADERS, candidates_rows)

    project_folder = data.get("project_folder") or ""
    if not project_folder:
        # There is nowhere to put the document, and carrying the raw
        # documents inline instead would be the degraded mode that ships
        # quietly. The runner broadcasts `project_folder` on every run.
        print(json.dumps({
            "error": (
                "No project_folder reached this bridge, so the vision "
                "pass's per-clip analysis has nowhere to be written and "
                "the prompt has no path to point at"
            ),
            "step": "3.02_bridge",
        }))
        sys.exit(1)

    out = {
        "broll_candidates_toon": candidates_toon,
        "footage_analysis_reference": write_footage_reference(
            project_folder,
            data.get("semantic_analysis_documents", {}),
            catalog_entries,
        ),
    }

    frames_block = build_window_frames(data, catalog_entries, semantic)
    if frames_block:
        out["broll_window_frames"] = frames_block

    print(json.dumps(out))


if __name__ == "__main__":
    main()
