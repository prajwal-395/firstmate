"""Semantic visuals for reels: the MODEL plans them, the ENGINE times them.

Step 4.06 plans the motion-graphics layer for the MASTER video, and its
handoff already asks the model for entries cued to what is being said -
`subject` (free-text reasoning) plus `anchor_phrase` (words from the
speech), timed by search over measured word timings
(`library/tools/semantic_visual.py`). What never happened is that ask
being made for a REEL: `rebuild_reels_in_project` places picture,
captions, transitions, cards and the deterministic explainer, and no
planning step ever reasons over a reel's speech. That is the gap this
module closes, without adding any taste to the engine.

The shape mirrors the pipeline's own agy file interface
(`run_pipeline`, `full_auto == "agy"`): this module builds the SAME
context step 4.06's bridge builds - the whole roster, the timeline as
context, what the brand template refines - scoped to the reel's own
spine, and writes it as a request file. A MODEL answers with a
`motion_graphics_plan`; the engine then validates it through step
4.06's OWN resolver (`motion_graphics_plan.resolve_plan`, with its
named drops), renders it through step 4.06's OWN renderer
(`motion_graphics.render_segment`), and records the plan for the
conformance verifier to grade against (F22).

Nothing here decides WHAT the visual is. There is no keyword table, no
subject list, no default element: an unanswered request builds the reel
without semantic visuals and SAYS so (`awaiting_model_answer`), the
same way an undeclared explainer builds without one. The model's
`subject` travels as provenance and is never read
(`semantic_visual.entry_subject`).

Placement: `SEMANTIC_TRACK` (V6), named once here so the placer
(`reel_build.build_reel_timeline`) and the check (F22) cannot disagree
about it. On the reels path V1/V2 carry picture, V3 captions, V4
transitions and V5 the explainer; the master's own V4 mapping is
recorded rather than followed because V4 is taken here.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

SEMANTIC_TRACK = 6
"""The reel video track semantic-visual segments are placed on."""

SEMANTIC_TRACK_NAME = "Semantic"
"""What Resolve calls the track, the way V3 is already named "Captions"."""

RENDER_PREFIX = "vox"
"""What a rendered segment is called: `vox_<reel-slug>_<index>.mov`.

AGENTS.md 5: prefix an overlay filename with its context. A reel's
segments must not overwrite another reel's, which is the same defect
`subtitle_segment_id` exists to stop.
"""

PLAN_FILENAME = "semantic_visual_plans.json"
"""Where the build RECORDS what it placed, per project.

Read back by the conformance check (F22) rather than re-derived, for
the reason `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` §4 gives: a
re-derived plan is only the build's plan while nothing changed in
between.
"""

AWAITING_MODEL_ANSWER = "awaiting_model_answer"
MODEL_PLANNED_NONE = "model_planned_none"
EVERY_ENTRY_DROPPED = "every_entry_dropped"
NOTHING_RENDERED = "nothing_rendered"
PLANNED = "planned"

BASES = (AWAITING_MODEL_ANSWER, MODEL_PLANNED_NONE, EVERY_ENTRY_DROPPED,
         NOTHING_RENDERED, PLANNED)
"""Why a reel has the semantic visuals it has, including none."""


def _step_4_06_bridge():
    """Step 4.06's pre-bridge module, the pipeline's own planning surface.

    Imported by path rather than by package name: it is a step script
    (`bridge.py`), and importing it as top-level `bridge` would collide
    with every other step's script of the same name. This module calls
    its context builders with a REEL spine where the master path passes
    the master spine - the functions read blocks, not products, so the
    ask is identical and scoped.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "steps",
                        "step_4_06_render_motion_graphics", "bridge.py")
    spec = importlib.util.spec_from_file_location("step_4_06_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request_stem(reel_number: int) -> str:
    """The file stem this reel's ask and answer share, in their areas."""
    return f"reel_semantic_{int(reel_number):02d}"


def segment_name(reel_name: str, index: int) -> str:
    """The render name, and the only place it is spelled."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(reel_name).lower()).strip("_")
    return f"{RENDER_PREFIX}_{slug}_{index:02d}"


def _cell(value) -> str:
    """One line, TOON-safe. The same one-liner step 4.06's bridge uses
    (`bridge._cell` there); spelled here so this module does not reach
    into another module's private."""
    return " ".join(str(value or "").split())


def bridge_context(reel_spine: dict, project_folder: str, fps: float) -> dict:
    """The model context for one reel, built by step 4.06's own bridge.

    `reel_spine` is `reel_spine.spine_for_reel` output - the reel's own
    audio in reel time, with word timings. Every table below is what the
    master path shows its planner; `timeline_context_toon` is the reel's
    blocks instead of the master's, so an `anchor_phrase` the model
    quotes is words this reel actually says.
    """
    from library.tools import motion_graphics_plan as plan
    from library.tools import motion_graphics_vocabulary as vocabulary
    from library.tools.brand_palette import roles_from_palette
    from library.tools.delivery_format import resolve_delivery_format
    from library.tools.safe_area import resolve_safe_area

    bridge = _step_4_06_bridge()
    rows = bridge.timeline_rows(reel_spine)
    duration = max((r["timeline_end"] for r in rows), default=0.0)
    try:
        width, height = resolve_delivery_format(project_folder)
    except Exception:
        width, height = 1080, 1920
    safe = resolve_safe_area(
        project_folder or None, width=width, height=height).as_props()
    brand_style, brand_effect = _brand_slots(project_folder)
    return {
        "motion_elements_toon": bridge.format_toon(
            list(vocabulary.ROSTER_LEGEND),
            [{k: _cell(v) for k, v in row.items()}
             for row in vocabulary.roster_rows()]),
        "motion_axes_toon": bridge.format_toon(
            ["axis", "ranges_over", "positions", "continuous",
             "resolved_against"],
            [{k: _cell(v) for k, v in row.items()}
             for row in vocabulary.axis_rows()]),
        "motion_elements_legend": dict(vocabulary.ROSTER_LEGEND),
        "timeline_context_toon": bridge.format_toon(
            ["block_position", "block_type", "timeline_start",
             "timeline_end", "says"], rows),
        "motion_graphics_frame": {
            "timeline_duration_seconds": round(duration, 3),
            "fps": fps,
            "delivery_frame": {"width": width, "height": height},
            "safe_area_px": safe,
            "anchors": list(plan.ANCHORS),
            "anchor_needing_a_measurement": plan.ANCHOR_NEEDS_MEASUREMENT,
            "entrance_and_exit_characters": list(plan.MOTION_CHARACTERS),
            "colour_roles": list(plan.COLOUR_ROLES),
            "type_roles": list(plan.TYPE_ROLES),
            "elements_the_renderer_draws_today": sorted(plan.DRAWABLE),
            "what_happens_to_the_others": (
                "An entry naming an element the renderer cannot draw is "
                "dropped by name, with the reason recorded on the step's "
                "output. It is never swapped for a neighbouring element "
                "and never quietly rendered as nothing."),
        },
        "brand_refinement": bridge.brand_refinement(brand_style,
                                                    brand_effect),
    }


def _brand_slots(project_folder: str) -> Tuple[dict, dict]:
    """This project's brand `style` and `effect` slots, or `{}`s.

    `{}` where the project names no template (AGENTS.md 10.1: a project
    that names no brand template gets NOTHING) - and the plan then
    states its own colours, exactly as step 4.06's handoff instructs.

    Resolved through `resolve_template_reference`, the same function
    the pipeline runner uses to inject `brand_style`/`brand_effect`
    into step inputs - a project NAME resolves against
    `library/templates/`, so the reel's planner is told about the same
    palette the master's planner would be told about.
    """
    try:
        from library.tools.brand_registry import (
            project_template_name, query_slots, resolve_template_reference)
        template = resolve_template_reference(
            project_template_name(project_folder))
        return query_slots(template, "style") or {}, \
            query_slots(template, "effect") or {}
    except Exception:
        return {}, {}


def handoff_text() -> str:
    """The ask, in step 4.06's own words.

    Read from the handoff file rather than respelled, so the reel's
    planner and the master's planner can never drift into two asks.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "steps",
                        "step_4_06_render_motion_graphics", "handoff.md")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_request(moment, transcript: dict, ranges, project_folder: str,
                  fps: float) -> str:
    """Ask the model to plan this reel's semantic visuals. Pure: no Resolve.

    Builds the reel spine, scopes step 4.06's bridge context to it, and
    writes the request file the answering model reads. Returns the
    request path, or `""` where the reel cannot be spined: a reel with
    no spine is REPORTED and built without visuals, not allowed to
    abort the other reels (`reel_subtitle_segments` keeps the same
    discipline for captions).
    """
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_spine import ReelSpineError, spine_for_reel

    reel_number = int(moment.number)
    try:
        spine = spine_for_reel(moment, transcript, list(ranges))
    except ReelSpineError as why:
        import sys
        name = getattr(moment, "timeline_name", f"reel {reel_number}")
        print(f"  {name}: NO SEMANTIC REQUEST - {why}", file=sys.stderr)
        return ""
    context = bridge_context(spine, project_folder, fps)
    try:
        from library.tools.toon_serializer import json_to_toon
        context_text = json_to_toon(context)
    except Exception:
        context_text = json.dumps(context, indent=1, default=str)
    request = {
        "step_id": "reel_semantic_visual",
        "reel_number": reel_number,
        "reel_name": getattr(moment, "timeline_name", ""),
        "prompt": handoff_text(),
        "context": context_text,
        "expected_schema": (
            '{"motion_graphics_plan": ['
            '{"element": "a key from motion_elements_toon", '
            '"subject": "free text, what this span is about", '
            '"anchor_phrase": "words from timeline_context_toon, '
            'INSTEAD of start_seconds/duration_seconds", '
            '"hold_seconds": 2.0, '
            '"anchor": "one of motion_graphics_frame.anchors", '
            '"row": 0, "copy": {"display": "..."}, '
            '"color": "#RRGGBB", "why": "..."}]}. '
            'An empty list plans no visuals. Quote anchor_phrase ONLY '
            'from words the timeline_context_toon table shows.'),
        "project_folder": project_folder,
        "timestamp": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    out_dir = ProjectLayout(project_folder).write_dir(Area.LLM_REQUESTS)
    path = os.path.join(str(out_dir), request_stem(reel_number) + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2)
    return path


def read_answer(project_folder: str, reel_number: int) -> Optional[list]:
    """The model's answer for this reel, or None when unanswered.

    Accepts `{"motion_graphics_plan": [...]}` or a bare list. Anything
    else - including a file that will not parse - reads as unanswered
    rather than as an empty plan: a malformed answer is not a decision
    for no visuals, and `awaiting_model_answer` says exactly that.
    """
    from library.tools.project_layout import Area, ProjectLayout
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.LLM_RESPONSES)),
        request_stem(reel_number) + ".json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(
            payload.get("motion_graphics_plan"), list):
        return payload["motion_graphics_plan"]
    return None


def build_for_reel(moment, transcript: dict, ranges, project_folder: str,
                   fps: float, width: int, height: int,
                   timeline_name: str = "") -> Tuple[list, dict]:
    """Resolve, render and record one reel's semantic visuals.

    Returns `(segments, record)`. `segments` are rendered overlay dicts
    ready for `build_reel_timeline`'s semantic track; `record` is what
    `semantic_visual_plans.json` carries for the verifier (F22) to grade
    against. No answer on file builds nothing and records
    `awaiting_model_answer` - a reel the model never planned is not a
    reel that failed to render.
    """
    import sys

    from library.tools import motion_graphics_plan as mg
    from library.tools import operations
    from library.tools.caption_band import captioned_spans, occupied_bands
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_spine import ReelSpineError, spine_for_reel
    from library.tools.safe_area import resolve_safe_area
    from library.tools.semantic_visual import collect_word_windows

    reel_number = int(moment.number)
    name = timeline_name or getattr(moment, "timeline_name", "")
    answer = read_answer(project_folder, reel_number)
    if answer is None:
        print(f"  {name}: NO SEMANTIC VISUALS - no model answer on file "
              f"({request_stem(reel_number)}.json), building without",
              file=sys.stderr)
        return [], _record(name, AWAITING_MODEL_ANSWER, [], resolved=None,
                           dropped=[])
    if not answer:
        print(f"  {name}: NO SEMANTIC VISUALS - the model planned none",
              file=sys.stderr)
        return [], _record(name, MODEL_PLANNED_NONE, [], resolved=None,
                           dropped=[])

    spine = None
    try:
        spine = spine_for_reel(moment, transcript, list(ranges))
    except ReelSpineError as why:
        print(f"  {name}: NO SEMANTIC VISUALS - {why}", file=sys.stderr)
        return [], {"reel": name, "basis": EVERY_ENTRY_DROPPED,
                    "entries": answer if isinstance(answer, list) else [],
                    "dropped": [{"element": "(unresolved)",
                                 "reason": "no_word_timings_to_anchor_against",
                                 "detail": str(why)}],
                    "segments": []}
    reel_seconds = sum(max(0.0, end - start) for start, end in (ranges or []))
    brand_style, brand_effect = _brand_slots(project_folder)
    from library.tools.brand_palette import roles_from_palette
    safe_area = resolve_safe_area(
        project_folder or None, width=width, height=height).as_props()
    resolved = mg.resolve_plan(
        answer, timeline_duration=reel_seconds, fps=fps,
        palette_roles=roles_from_palette(
            (brand_style or {}).get("color_palette")) or {},
        asked=True,
        caption_bands=occupied_bands(
            brand_effect=brand_effect, brand_style=brand_style,
            project_folder=project_folder or None),
        captioned_spans=captioned_spans(spine),
        word_windows=collect_word_windows(spine),
        resolve_asset=None,
    )
    for dropped in resolved.dropped:
        print(f"  {name}: semantic entry dropped ({dropped.reason}) "
              f"{dropped.element}: {dropped.detail}", file=sys.stderr)
    if not resolved.moments:
        return [], _record(name, EVERY_ENTRY_DROPPED, answer,
                           resolved=resolved, dropped=resolved.dropped)
    segments_plan = mg.plan_segments(
        resolved.moments, fps=fps, width=width, height=height,
        safe_area=safe_area)
    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.MOTION_GRAPHICS_SEGMENTS, step="render_motion_graphics"))
    render = operations.get("motion_graphics.render_segment")
    segments = []
    for index, planned in enumerate(segments_plan):
        rendered = render.run(
            planned, out_dir,
            segment_name=segment_name(name, index),
            progress=f"[{index + 1}/{len(segments_plan)}]")
        if rendered is None:
            continue
        segments.append(rendered)
    if not segments:
        return [], _record(name, NOTHING_RENDERED, answer,
                           resolved=resolved, dropped=resolved.dropped)
    return segments, _record(name, PLANNED, answer, resolved=resolved,
                             dropped=resolved.dropped, segments=segments)


def _record(reel_name: str, basis: str, entries: list, resolved=None,
            dropped=(), segments=None) -> dict:
    """One reel's semantic-visual record, in the shape F22 grades."""
    assert basis in BASES, f"{basis!r} is not a recorded basis: {BASES}"
    record: Dict[str, Any] = {
        "reel": reel_name,
        "basis": basis,
        "entries": entries if isinstance(entries, list) else [],
        "dropped": [
            {"element": d.element, "reason": d.reason, "detail": d.detail}
            for d in (dropped or [])],
        "segments": [
            {"timeline_start": s["timeline_start"],
             "timeline_end": s["timeline_end"],
             "total_frames": s["total_frames"],
             "elements": list(s.get("elements") or [])}
            for s in (segments or [])],
    }
    if resolved is not None:
        record["resolved"] = len(resolved.moments)
        record["proposed"] = resolved.proposed
    return record


def plans_path(project_folder: str) -> str:
    """Where the merged per-reel records live."""
    from library.tools.project_layout import Area, ProjectLayout
    return os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        PLAN_FILENAME)


def write_records(project_folder: str, records: Sequence[dict]) -> str:
    """Merge this build's records into the stored file, per reel.

    Every reel THIS build touched is replaced by what it placed - or by
    its empty basis, when it placed none - and every other reel's record
    is left exactly as it was. Overwriting the file would delete the
    record of the reels a partial (`only`) build did not touch, and F22
    would then grade those timelines against an absence.
    """
    path = plans_path(project_folder)
    stored: Dict[str, Any] = {"format": "semantic_visual_plans/1",
                              "plans": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                stored = json.load(handle) or stored
        except (OSError, ValueError):
            stored = {"format": "semantic_visual_plans/1", "plans": []}
    touched = {str(r.get("reel")) for r in (records or ())}
    kept = [p for p in (stored.get("plans") or [])
            if str(p.get("reel")) not in touched]
    kept.extend(records or [])
    stored["plans"] = kept
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
    return path


def read_records(project_folder: str) -> dict:
    """What the build recorded, or `{}` when it recorded nothing."""
    path = plans_path(project_folder)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def record_for_reel(records: Optional[dict], reel_name: str) -> Optional[dict]:
    """The recorded plan for one reel by NAME, or None.

    None means "this build recorded nothing for this reel", which is
    what a reel built before semantic visuals existed looks like, and
    F22 returns nothing rather than grading against an absence.
    """
    for record in ((records or {}).get("plans") or []):
        if str(record.get("reel")) == str(reel_name):
            return record
    return None


def rename_record_reels(project_folder: str, mapping: dict) -> None:
    """Rename `plans[].reel` fields, staging -> final.

    The staging half of promotion, mirroring `explainer_plan` and the
    transition-overlay records: the verifier graded the staging against
    this file, and after promotion the same placements live under the
    final name.
    """
    if not mapping:
        return
    path = plans_path(project_folder)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle) or {}
    for record in (stored.get("plans") or []):
        if record.get("reel") in mapping:
            record["reel"] = mapping[record["reel"]]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)


def drop_record_reels(project_folder: str, names) -> None:
    """Remove records for the named reels.

    The gate-fail half of a refused staging: no baseline may survive
    for a container that is about to be deleted, or F22 would grade the
    surviving approved timeline against a refused build's placements.
    """
    drop = set(names or ())
    if not drop:
        return
    path = plans_path(project_folder)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle) or {}
    stored["plans"] = [p for p in (stored.get("plans") or [])
                       if str(p.get("reel")) not in drop]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
