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
import json
import os
import sys
from pathlib import Path

from library.tools import window_frames as wf
from library.tools.brief_reference import build_reference
from library.tools.cutaway_window import filter_broll_candidates
from library.tools.evaluation_policy import load_policy
from library.tools.footage_reference import (
    DOCUMENT_NAME,
    REFERENCE_DOCUMENT_NAME,
    REFERENCE_WHY,
    footage_document,
)
from library.tools import media_quality
from library.tools.pipeline_validation import require_keys
from library.tools.project_layout import Area, ProjectLayout, layout_for
from library.tools.semantic_index import build_semantic_lookup, clip_observations
from library.tools.transition_carriers import block_reaches_v1
from library.tools.vision_schema_adapter import adapt_semantic_document

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
    "tech_leg",
]

#: The block that tells the model what the technical column is and what
#: it is not. It is the ONLY place the rule is stated, so the numbers in
#: the table cannot be read as a preference.
TECHNICAL_LEG_BLOCK = """\
## Technical leg (measured, per candidate)

The `tech_leg` column carries the media-quality measurements over the
candidate's usable range: focus, blur, exposure, motion, clipping,
noise and usable speech. Each value is `value[measured/expected
samples, confidence]`.

- `samples/expected` is the COVERAGE: how many samples covered the
  interval out of how many it needs. A half-measured candidate says so.
- `confidence` is `full` (every sample the interval needs), `partial`
  (some of them), `single_sample` (one sample stands for it) or `none`.
- `-` means the lane that measures this indicator never ran. It is not a
  zero and not a bad value: nobody looked.
- Sharpness is a PROXY: it cannot separate defocus, motion blur and low
  texture. The speech figure is a frequency-coverage proxy, never
  intelligibility.

This column is ONE LEG of the choice, beside what the shot shows and
who is in it. It is not a ranking, and it does not make a candidate
better: the technically sharpest shot is not automatically the right
shot. Choose on the editorial grounds, and use these numbers to say
which candidate is technically preferable and why."""


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
    return slot.get("clip_id") or ""


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
    spine rather than from a constant. Blocks that already play their
    own picture on V1 are left out: a cutaway there is an interjection
    over A-roll, not the cover the block needs. A cutaway covers a
    block with no V1 picture - including a voiceover-sourced speech
    block, whose words play from the audio file - which is the coverage
    requirement `compile_manifest._assert_timeline_fully_covered`
    enforces. The line is `block_reaches_v1`, the one statement of V1
    membership, so the two cannot drift.
    """
    blocks = timed_spine.get("structure") or []
    seconds = set()
    for block in blocks:
        if block_reaches_v1(block):
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
        source_file = clip.get("source_file") or clip.get("path")
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


def build_search_hints(data: dict, catalog_entries: list) -> str:
    """Query the footage index with each must-cover slot's intent.

    Best-effort and backend-agnostic (`library/tools/search_in_planning.py`):
    when the index is missing or degraded the catalog-driven candidate
    table above stays the path and the hints say so aloud. Never fails
    the bridge - a search assist that could refuse the step would make
    the catalog path depend on the index existing.
    """
    from library.tools import search_in_planning as sip

    project_folder = data.get("project_folder") or ""
    timed_spine = data.get("timed_spine") or {}
    blocks = timed_spine.get("structure") or []
    slots = [b for b in blocks
             if isinstance(b, dict) and not block_reaches_v1(b)]
    if not project_folder or not slots:
        return sip.format_hints_block({})
    try:
        hints = sip.search_hints_for_blocks(
            project_folder, slots, catalog_entries)
    except Exception as exc:  # noqa: BLE001 - assist, never gate
        print(f"  Footage search hints unavailable: {exc}", file=sys.stderr)
        return sip.format_hints_block({})
    filled = sum(1 for h in hints.values() if h.get("status") == "filled")
    print(f"  Footage search hints: {filled}/{len(hints)} slot(s) vouched",
          file=sys.stderr)
    return sip.format_hints_block(hints)


def _temporal_lookup(data) -> dict:
    """The temporal index of every clip, keyed by clip id."""
    raw = data.get("temporal_event_indices") or []
    if isinstance(raw, dict):
        raw = raw.get("temporal_event_indices", [])
    return {row.get("clip_id"): row for row in raw if isinstance(row, dict)}


def _technical_interval(assessment, duration):
    """The interval a candidate's technical leg covers.

    The candidate's USABLE range is the relevant interval: it is the
    stretch the vision pass says may be cut from, so a measurement over
    the whole clip would average in the parts nobody may use. A clip
    with no measured usable range falls back to its own extent, and the
    leg says which interval it covered.
    """
    ranges = (assessment or {}).get("usable_ranges")
    if isinstance(ranges, list):
        for entry in ranges:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                start, end = float(entry[0]), float(entry[1])
            elif isinstance(entry, dict) and "start" in entry and "end" in entry:
                start, end = float(entry["start"]), float(entry["end"])
            else:
                continue
            if end > start >= 0:
                return start, end
    if duration and duration > 0:
        return 0.0, float(duration)
    return None


def build_technical_legs(data, catalog, documents, temporal_lookup):
    """The technical leg of every candidate, over its relevant interval.

    A leg is a MEASUREMENT over an interval, with its coverage and its
    confidence stated; it is never a score and never a preference (see
    `library/tools/media_quality.py`). A candidate nobody measured is
    reported as unmeasured, never as a clean one.

    A lane that cannot answer never fails the step: an absent
    measurement is the honest answer, and the step's own choice is not
    the measurement's to gate.
    """
    project_folder = data.get("project_folder") or ""
    legs = {}
    if not project_folder:
        return legs
    for clip_id, clip_info in catalog.items():
        doc = (documents or {}).get(clip_id) or {}
        assessment = adapt_semantic_document(doc).get("assessment") or {}
        duration = float(clip_info.get("duration_seconds") or 0.0)
        interval = _technical_interval(assessment, duration)
        if interval is None:
            continue
        try:
            leg = media_quality.technical_leg_for_clip(
                project_folder, clip_info,
                start_seconds=interval[0], end_seconds=interval[1],
                temporal_index=(temporal_lookup or {}).get(clip_id))
        except Exception as exc:  # noqa: BLE001 - a measurement, never a gate
            print(f"  Technical leg for {clip_id} unavailable: {exc}",
                  file=sys.stderr)
            continue
        legs[clip_id] = leg
    return legs


def main():
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
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

    slots = aroll if isinstance(aroll, list) else aroll.get("a_roll_assignments", [])

    # Voiceover-sourced speech blocks carry no A-roll - their picture is
    # this step's cover. A position claimed by BOTH a video assignment
    # and a voiceover assignment would play two pictures at once, so it
    # refuses here, where both assignments are in hand, rather than as
    # an overlap downstream.
    voiceover_positions = set()
    for entry in data.get("voiceover_assignments") or []:
        if isinstance(entry, dict) and entry.get("spine_block_position") is not None:
            voiceover_positions.add(str(entry["spine_block_position"]))
    aroll_positions = set()
    for slot in slots:
        if isinstance(slot, dict) and slot.get("spine_block_position") is not None:
            aroll_positions.add(str(slot["spine_block_position"]))
    doubly_claimed = sorted(voiceover_positions & aroll_positions)
    if doubly_claimed:
        print(json.dumps({
            "error": (
                f"Spine block(s) {doubly_claimed} carry both a video "
                f"assignment and a voiceover assignment - one block "
                f"cannot play footage and voiceover-over-B-roll at once"
            ),
            "step": "3.02_bridge",
        }))
        sys.exit(1)
    if voiceover_positions:
        print(
            f"  {len(voiceover_positions)} voiceover block(s) "
            f"({', '.join(sorted(voiceover_positions))}) need B-roll "
            f"cover - their words play from audio, not footage",
            file=sys.stderr,
        )

    # Clips used anywhere as A-roll: usable as B-roll elsewhere, but they
    # are the least interesting choice, so rank them last.
    aroll_clips = {_slot_aroll_clip(s) for s in slots}

    candidates_rows = []
    candidate_facets = {}
    clips_without_description = []

    temporal_lookup = _temporal_lookup(data)

    for clip_id, clip_info in catalog.items():
        doc = semantic.get(clip_id, {})
        observed = clip_observations(doc)
        desc = observed.get("description", "")
        if not desc:
            clips_without_description.append(clip_id)
            continue
        candidate_row = {
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
        }
        candidates_rows.append(candidate_row)
        candidate_facets[clip_id] = {
            "duration_s": candidate_row["duration_s"],
            "framing": observed.get("framing"),
            "stability": observed.get("stability"),
            # FI-15 names this measured camera facet `movement`.
            "movement": observed.get("movement"),
            "content_type": observed.get("content_type"),
        }
        adapted = adapt_semantic_document(doc)
        assessment = adapted.get("assessment") or {}
        if "primary_subject_visible" in assessment:
            candidate_facets[clip_id]["subject_visible"] = bool(
                assessment["primary_subject_visible"])

    # The technical leg of every candidate: the measured media quality
    # over the interval the candidate may be cut from. It is ONE LEG of
    # the choice and never a ranking (see TECHNICAL_LEG_BLOCK).
    technical_legs = build_technical_legs(
        data, catalog, semantic, temporal_lookup)
    for candidate_row in candidates_rows:
        leg = technical_legs.get(candidate_row["clip_id"])
        if not leg or not leg.get("measured_indicators"):
            candidate_row["tech_leg"] = "not measured"
            continue
        candidate_row["tech_leg"] = _cell(
            media_quality.render_leg_cell(leg), MAX_CELL_CHARS)

    # A policy is opt-in by exact reference from project_config. The Project
    # Model is the authority; never synthesize a default or pick a policy.
    project_config = data.get("project_config") or {}
    pipeline_config = project_config.get("pipeline") or {}
    policy_ref = pipeline_config.get("broll_evaluation_policy_ref")
    if policy_ref:
        from library.tools.project_model_store import load_project_model
        project_folder = data.get("project_folder") or ""
        if not project_folder:
            raise ValueError("B-roll policy selection requires project_folder")
        _revision, model = load_project_model(Path(project_folder))
        policy = load_policy(model, policy_ref)
    else:
        policy = None
    candidates_rows, broll_policy_report = filter_broll_candidates(
        candidates_rows, candidate_facets, policy)

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
        "broll_policy_filter_report": broll_policy_report,
        "broll_technical_leg": TECHNICAL_LEG_BLOCK,
        "footage_analysis_reference": write_footage_reference(
            project_folder,
            data.get("semantic_analysis_documents", {}),
            catalog_entries,
        ),
    }

    frames_block = build_window_frames(data, catalog_entries, semantic)
    if frames_block:
        out["broll_window_frames"] = frames_block

    out["broll_search_hints"] = build_search_hints(data, catalog_entries)

    print(json.dumps(out))


if __name__ == "__main__":
    main()
