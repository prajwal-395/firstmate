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

The shape mirrors the pipeline's own agent file interface
(`run_pipeline`, `full_auto == "agent"`): this module builds the SAME
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

Placement: the plan's semantic row, whose index `SEMANTIC_TRACK` only
names for the full layout (V6 where picture is V1/V2, captions V3,
transitions V4 and the explainer V5). The placer
(`reel_build.build_reel_timeline`) reads the row off the track plan,
and the check (F22) reads the row's NAME - rows pack, so a reel with
no transitions row and no explainer row carries its semantic visuals
on V5, above the Subtitles row with or without the look.
`SEMANTIC_TRACK` stays as the legacy fallback for timelines built
before rows were named. The master's own V4 mapping is recorded rather
than followed because V4 is taken here.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import json
import os
import re
import sys
from dataclasses import field
from typing import Any, Dict, List, Optional, Sequence, Tuple

SEMANTIC_TRACK = 6
"""The reel video track semantic-visual segments are placed on - in
the FULL layout. Rows pack, so the plan's semantic row is what the
placer reads; this stays as the legacy fallback and the message
default where no placed item names a row."""

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
    # Recorded transcript corrections, as the model reads them: the
    # `says` column above already carries the corrected words (the
    # deterministic pass runs at the transcript root), and this names
    # the verdict behind them so MODEL-AUTHORED copy - `copy.display`,
    # `subject` - spells them the same way. Empty where the project
    # recorded none: an absence stated, not hidden.
    corrections_note = ""
    if project_folder:
        try:
            from library.tools import transcript_corrections as _tc
            corrections_note = _tc.render_for_model(project_folder)
        except Exception:
            corrections_note = ""
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
        "transcript_corrections": corrections_note,
    }


def _brand_slots(project_folder: str) -> Tuple[dict, dict]:
    """This project's brand `style` and `effect` slots, or `{}`s.

    `{}` where the project names no template (AGENTS.md 10.1: a project
    that names no brand template gets NOTHING) - and the plan then
    states its own colours, exactly as step 4.06's handoff instructs.

    Resolved through `resolve_template_reference`, the same function
    the pipeline runner uses to inject `brand_style`/`brand_effect`
    into step inputs - a project NAME resolves against the project's
    own `brand.json` first, so the reel's planner is told about the same
    palette the master's planner would be told about.
    """
    try:
        from library.tools.brand_registry import (
            project_template_name, query_slots, resolve_template_reference)
        template = resolve_template_reference(
            project_template_name(project_folder),
            project_folder=project_folder)
        return query_slots(template, "style") or {}, \
            query_slots(template, "effect") or {}
    except Exception:
        return {}, {}


def handoff_text() -> str:
    """The ask, in step 4.06's own words.

    Read from the handoff file rather than respelled, so the reel's
    planner and the master's planner can never drift into two asks.

    It is also where the roster's COLUMN DEFINITIONS live: they shipped
    beside the table as a `motion_elements_legend` dict while 4.06's
    `handoff.md` was under the captain's freeze, and moved into its prose
    when the freeze lifted.  Because this request carries that prose
    verbatim, the reel's planner is told what a column is by the same
    words the master's planner is.
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
        spine = spine_for_reel(moment, transcript, list(ranges),
                               project_folder=project_folder)
    except ReelSpineError as why:
        name = getattr(moment, "timeline_name", f"reel {reel_number}")
        print(f"  {name}: NO SEMANTIC REQUEST - {why}", file=sys.stderr)
        try:
            from library.tools import reel_phase_log as _phase_log
            _phase_log.log_wait(
                project_folder, reel_number, name,
                f"no semantic ask - {why}")
        except Exception:
            pass
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
            '"data": {"values": [3, 9]} on an element whose roster '
            'axes include data, asserted only from what the speech '
            'itself states, omitted otherwise - when the number is '
            'not in the speech choose an element that needs none, '
            '"asset": "a file in the project brand_assets/ on an '
            'element whose axes include asset, omitted otherwise", '
            '"color": "#RRGGBB", "why": "..."}]}. '
            'An empty list plans no visuals. Quote anchor_phrase ONLY '
            'from words the timeline_context_toon table shows. A data '
            'payload whose values are all equal, or data on an element '
            'that draws none, drops the entry by name.'),
        "project_folder": project_folder,
        "timestamp": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    out_dir = ProjectLayout(project_folder).write_dir(Area.LLM_REQUESTS)
    path = os.path.join(str(out_dir), request_stem(reel_number) + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2)
    # The ask, logged AT THE ASK: this file is rewritten on every build,
    # so its mtime is the last rewrite and not the ask - the phase log
    # carries its own timestamp instead (`reel_phase_log`).
    try:
        from library.tools import reel_phase_log as _phase_log
        _phase_log.log_event(
            project_folder, reel_number,
            getattr(moment, "timeline_name", ""), _phase_log.PLAN_ASKED,
            detail=f"semantic ask written: {request_stem(reel_number)}.json")
    except Exception:
        pass
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
        # The wait, by the waiter: the answer never arrived, so the reel
        # builds without visuals. Logged here, where both facts are in
        # hand (`reel_phase_log`).
        try:
            from library.tools import reel_phase_log as _phase_log
            _phase_log.log_wait(
                project_folder, reel_number, name,
                f"no model answer on file "
                f"({request_stem(reel_number)}.json) - "
                f"building without semantic visuals")
        except Exception:
            pass
        return [], _record(name, AWAITING_MODEL_ANSWER, [], resolved=None,
                           dropped=[])
    if not answer:
        print(f"  {name}: NO SEMANTIC VISUALS - the model planned none",
              file=sys.stderr)
        return [], _record(name, MODEL_PLANNED_NONE, [], resolved=None,
                           dropped=[])

    spine = None
    try:
        spine = spine_for_reel(moment, transcript, list(ranges),
                               project_folder=project_folder)
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
    from library.tools import reel_cta_treatment as cta_rx
    # The captain's declared CTA treatment (external/reel_cta.json),
    # normalised into the answer BEFORE resolve_plan times and validates
    # it - so a re-rendered card keeps the plan's own copy and timing
    # and only the treatment is unified. No declaration, no CTA, an
    # out-of-scope CTA, or no positionable card entry passes the answer
    # through untouched.
    answer, cta_report = cta_rx.apply(
        answer, moment, collect_word_windows(spine), project_folder)
    cta_rx.report(name, cta_report)
    from library.tools.brand_palette import roles_from_palette
    from library.tools.brand_registry import project_template_name
    safe_area = resolve_safe_area(
        project_folder or None, width=width, height=height).as_props()
    # A reel carrying a post header (library/tools/reel_post_header.py)
    # gives up its rows: a top-anchored visual sits under the header,
    # never on it.
    from library.tools.reel_post_header import header_floor
    floor = header_floor(project_folder or None, reel_number, width,
                         height, fps)
    if floor is not None and floor > safe_area["top"]:
        safe_area["top"] = floor
    try:
        palette_source = project_template_name(project_folder)
    except Exception:
        palette_source = ""
    resolved = mg.resolve_plan(
        answer, timeline_duration=reel_seconds, fps=fps,
        palette_roles=roles_from_palette(
            (brand_style or {}).get("color_palette")) or {},
        # Whose palette answered, so a resolved colour names its
        # source on its colorBasis - the same provenance step 4.06
        # records (library/tools/motion_graphics_plan.py).
        palette_source=palette_source,
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
        safe_area=safe_area, project_folder=project_folder or "")
    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.MOTION_GRAPHICS_SEGMENTS, step="render_motion_graphics"))
    render = operations.get("motion_graphics.render_segment")
    segments = []
    for index, planned in enumerate(segments_plan):
        # The project travels so the renderer reads the project's OWN
        # `motion_graphics_overlay_geometry` declaration
        # (`library/tools/overlay_mode.py`) - a project declaring tight
        # gets tight boxes here exactly as the master pass does, and a
        # project declaring nothing renders full canvas as before. The
        # geometry itself stays unresolved (None) so an explicit value
        # still wins and the declaration is read live, per render.
        rendered = render.run(
            planned, out_dir,
            segment_name=segment_name(name, index),
            progress=f"[{index + 1}/{len(segments_plan)}]",
            project_folder=project_folder)
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
            {"overlay_path": s.get("overlay_path"),
             "segment_id": s.get("segment_id"),
             "placement_label": s.get("placement_label"),
             "timeline_start": s["timeline_start"],
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
    final name. A record the previous build left under the final name
    is REPLACED, not kept beside the renamed one: two records for one
    reel leave `record_for_reel` reading the stale first, so the next
    verifier grades the promoted timeline against the absence.
    """
    if not mapping:
        return
    path = plans_path(project_folder)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle) or {}
    finals = set(mapping.values())
    stored["plans"] = [record for record in (stored.get("plans") or [])
                       if record.get("reel") not in finals]
    for record in stored["plans"]:
        if record.get("reel") in mapping:
            record["reel"] = mapping[record.get("reel")]
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


# ── The span's own ask: a picture reasoned from its speech ──────────
#
# Blocker 1 (`docs/SPAN_RENDERER_CAPABILITY.md` §3.1): no model plans a
# span. The request/answer/resolver above plans the V6 overlay layer;
# what follows plans the span's per-beat picture in the SAME three-part
# shape - a request built from measured evidence, a schema the answer
# must satisfy, and a resolver that refuses an answer it cannot bind to
# real word timings - because that is the shape this module already
# proves, not because the overlay machinery generalises. It does not:
# `motion_graphics_plan.resolve_plan` resolves element keys, anchors,
# colours and copy against the overlay roster, and a span beat is none
# of those. A beat is a NOUN illustrated, cued to words, leading them
# (`docs/ANIMATION_FIRST_REFERENCE.md` §1: every picture event
# illustrates a noun from the spoken line, arriving 67-839 ms before
# it). So the span gets the pattern, not the resolver, and its own
# reasons.
#
# Three known unknowns, answered where the code can answer them:
#
# - Measured word windows ARE reachable where a span is planned. The
#   request takes the same two inputs `_plan_span` already reads at plan
#   time (`ranges` and `transcript` in
#   `library/tools/full_frame_element.py`) and buckets the transcript's
#   timed words through `reel_build.reel_time`, the same arithmetic the
#   word-clock cues use. Nothing here needs a pipeline stage that runs
#   later.
# - Segments coincide with keep ranges today (one segment per range, by
#   index), so the evidence is one word list per range and the resolver
#   derives each segment's bounds from the ranges. If a sibling lane
#   makes segments subdividable, the evidence gains rows and the bounds
#   follow the subdivision; the anchor search and every refusal below
#   read per-segment words and bounds, so they are unchanged.
# - The resolved moments have no consumer yet: no renderer reads span
#   beats, so this section writes no PLACEMENT - but it writes a RECORD.
#   `write_span_records` files each reel's resolved plan under
#   `pipeline_output/review/span_visual_plans.json`, in the V6 record's
#   own convention, and the conformance verifier grades it (F23). A
#   record the verifier reads is not a declared output with no reader.
#
# What the planner decides is WHAT IS SHOWN, never the look: `shows`
# names the noun illustrated, in free text, and the schema carries no
# colour, no size, no font and no motion value. An entry carrying any
# key that names one is refused as `look_value_in_picture_plan` rather
# than read past. Taste stays with the model reasoning per project, per
# the captain's standing rule of 2026-09-08.
#
# What is NOT bounded here, on purpose: the lead. The reference
# measures 67-839 ms, but `ANIMATION_FIRST_REFERENCE.md` §11 says every
# magnitude there belongs to that piece and none may become a default.
# Bounding `lead_seconds` above by 839 ms would bake one piece into a
# gate that rejects correct output. The structural bounds are all that
# hold: a lead is a number at or above zero, and the beat it puts down
# lands inside its own segment.
#
# `tests/test_reel_span_visual.py`.

#: What the span answer is called. One spelling, here, the way
#: `motion_graphics_plan.PLAN_KEY` spells the overlay's.
SPAN_PLAN_KEY = "span_visual_plan"

SPAN_NOT_PLANNED = "span_not_planned"
SPAN_NO_EVENTS_PLANNED = "span_no_events_planned"
SPAN_EVERY_EVENT_DROPPED = "span_every_event_dropped"
SPAN_EVENTS_PLANNED = "span_events_planned"

SPAN_BASES = (SPAN_NOT_PLANNED, SPAN_NO_EVENTS_PLANNED,
              SPAN_EVERY_EVENT_DROPPED, SPAN_EVENTS_PLANNED)

SPAN_DROP_REASONS: Dict[str, str] = {
    "entry_is_not_a_mapping": (
        "The beat is not a mapping, so it names no segment, no noun "
        "and no anchor. Nothing is read off it."
    ),
    "unknown_segment": (
        "The beat names no segment of this span. Segments are the "
        "reel's keep ranges one by one, numbered from 1, and a beat "
        "for any other number has no seconds to land on."
    ),
    "look_value_in_picture_plan": (
        "The beat carries a key that names a look dimension - a "
        "colour, a size, a typeface, a motion character, an artwork "
        "file. The span planner decides what is SHOWN, never the "
        "look, so the entry is refused rather than read past."
    ),
    "no_subject_declared": (
        "The beat names no `shows`: a picture event with no noun to "
        "illustrate shows nothing, and an overlay that draws nothing "
        "is not rendered (AGENTS.md 10.2)."
    ),
    "no_anchor_declared": (
        "The beat names no `anchor_phrase`. A span beat is cued to "
        "its own words by SEARCH (AGENTS.md 6); explicit seconds "
        "alone would land near words instead of on them, so they are "
        "not a second timing - they are no timing."
    ),
    "conflicting_timing": (
        "The beat names both an anchor phrase and explicit timeline "
        "seconds - two timings. The engine does not pick one, "
        "because choosing would be choosing when the picture lands."
    ),
    "anchor_phrase_not_found": (
        "The beat's anchor phrase occurs nowhere in its own "
        "segment's measured words. The picture lands on its words "
        "or not at all."
    ),
    "anchor_word_untimed": (
        "The anchor phrase is said but its measured window is "
        "missing. A picture cued to an unmeasured word is cued to a "
        "guess."
    ),
    "no_word_timings_to_anchor_against": (
        "The beat anchors to words and its segment carries no "
        "measured word timings. Without a measurement there is no "
        "window to land on."
    ),
    "no_timing_declared": (
        "The beat's `lead_seconds` is not a number at or above "
        "zero. A lead is how far before its noun the picture "
        "arrives; a lag (below zero) or a non-number declares no "
        "timing."
    ),
    "beat_outside_segment": (
        "The beat lands outside its own segment: the anchor's start "
        "minus the lead is before the segment starts or past its "
        "end. Not clamped: moving a beat is choosing when the "
        "picture plays."
    ),
    "more_events_than_speech_supports": (
        "The segment's measured words are spent: it speaks fewer "
        "words than beats planned for it, or the beat re-claims a "
        "word occurrence an earlier beat already claimed. One "
        "picture event per spoken word at most - a beat with no word "
        "of its own decorates across the speech."
    ),
}

#: Keys that name a look dimension. A span beat carrying any of them is
#: refused as `look_value_in_picture_plan`: the planner decides what is
#: shown, and colour, size, typeface, motion and artwork stay with the
#: declaration and the renderer. Read from the key, never the value.
LOOK_KEYS = frozenset({
    "color", "colour", "font_size", "fontsize", "font_family",
    "fontfile", "font_file", "typeface", "type_role",
    "background", "entrance", "exit", "motion",
    "emphasis", "emphasis_colour", "emphasis_color",
    "hold_frames", "asset", "image", "image_width",
    "vignette", "texture",
})


class SpanPlanError(ValueError):
    """A span plan this section refuses outright, like `MotionPlanError`
    for the overlay layer: the plan is not a list of beats."""


def span_request_stem(reel_number: int) -> str:
    """The file stem this reel's span ask and answer share."""
    return f"reel_span_{int(reel_number):02d}"


def span_segment_words(ranges, transcript: dict) -> List[List[dict]]:
    """Each keep range's timed words, in REEL seconds.

    The same bucketing `_word_cues` in
    `library/tools/full_frame_element.py` paces the reveal off: the
    transcript's `timed` words mapped through `reel_build.reel_time`
    and filed under the range whose reel span contains them. One list
    per range, so segment `i` (1-based) reads `span_segment_words[i -
    1]`; each word is `{word, start, end}` in reel seconds, the shape
    `semantic_visual.find_phrase_window` searches.
    """
    from library.tools.reel_build import reel_time

    ranges = list(ranges or [])
    starts: List[float] = []
    cursor = 0.0
    for start, end in ranges:
        starts.append(cursor)
        cursor += max(0.0, float(end) - float(start))
    per: List[List[dict]] = [[] for _ in ranges]
    for segment in ((transcript or {}).get("segments") or []):
        for word in (segment.get("words") or []):
            if not word.get("timed"):
                continue
            text = str(word.get("word") or "")
            if not text:
                continue
            try:
                at = reel_time(float(word["start"]), ranges)
            except (TypeError, ValueError):
                continue
            if at is None:
                continue
            try:
                end_at = reel_time(float(word["end"]), ranges, at_end=True)
            except (TypeError, ValueError):
                continue
            if end_at is None:
                continue
            for index, (start, end) in enumerate(ranges):
                low, high = starts[index], starts[index] + max(
                    0.0, float(end) - float(start))
                if low - 1e-9 <= at < high - 1e-9:
                    per[index].append({
                        "word": text, "start": at, "end": end_at})
                    break
    for words in per:
        words.sort(key=lambda w: (w["start"], w["end"]))
    return per


SPAN_HANDOFF = """Plan this reel's full-frame span picture from its speech.

You are planning WHAT EACH BEAT SHOWS - the picture track of an
animation-first reel - not styling it. The reference this answers
(`docs/ANIMATION_FIRST_REFERENCE.md`) measures one rule: every picture
event illustrates a NOUN from the spoken line, and the picture arrives
BEFORE its noun is spoken.

For each keep range (one span segment each), read the segment's measured
words and decide what its beats SHOW: one beat per noun worth
illustrating, at most one beat per spoken word. Each beat names:

- `segment`: the 1-based segment this beat plays in.
- `shows`: free text naming the noun illustrated - "a mind", "an eye",
  "the entire field". This is the whole of the decision.
- `anchor_phrase`: words quoted EXACTLY from this segment's measured
  words - the noun's own window, never a neighbouring segment's.
- `lead_seconds`: how far BEFORE the anchor's start the picture lands.
  A number at or above zero. The beat lands inside its own segment.
- `why`: one line saying which spoken noun this illustrates.

Carry NO look values: no colour, no size, no font, no motion character,
no artwork file. An entry carrying any of those is refused outright -
taste is reasoned per project, never smuggled inside a picture plan.
An empty list plans no pictures."""

SPAN_EXPECTED_SCHEMA = (
    '{"span_visual_plan": ['
    '{"segment": 1, '
    '"shows": "free text naming the noun illustrated", '
    '"anchor_phrase": "words from THIS segment\'s measured words", '
    '"lead_seconds": 0.2, '
    '"why": "..."}]}. '
    'An empty list plans no pictures. Quote anchor_phrase ONLY from '
    'words the segment table shows, and plan at most one beat per '
    'spoken word. Carry no colour, size, font, motion or artwork key.')


def write_span_request(moment, transcript: dict, ranges, project_folder: str,
                       fps: float) -> str:
    """Ask the model to plan this reel's span picture. Pure: no Resolve.

    The V6 `write_request` shape scoped to the span: the reel's keep
    ranges and transcript become one measured word list per segment,
    and the request file carries the ask, that evidence, and the schema
    the answer must satisfy. Returns the request path, or `""` where
    the reel speaks no timed words: a reel with no word clock is
    REPORTED and planned without span pictures, the way a reel with no
    spine is built without V6 visuals.
    """
    from library.tools.project_layout import Area, ProjectLayout

    reel_number = int(moment.number)
    words = span_segment_words(ranges, transcript)
    if not any(words):
        name = getattr(moment, "timeline_name", f"reel {reel_number}")
        print(f"  {name}: NO SPAN REQUEST - no timed words in "
              f"{len(list(ranges or []))} keep range(s), so no beat has "
              f"a window to land on", file=sys.stderr)
        try:
            from library.tools import reel_phase_log as _phase_log
            _phase_log.log_wait(
                project_folder, reel_number, name,
                "no span ask - no timed words in "
                f"{len(list(ranges or []))} keep range(s)")
        except Exception:
            pass
        return ""
    bridge = _step_4_06_bridge()
    rows = []
    cursor = 0.0
    for position, ((start, end), segment_words) in enumerate(
            zip(list(ranges or []), words), start=1):
        reel_start = cursor
        cursor += max(0.0, float(end) - float(start))
        says = " ".join(
            f"{w['word']}[{w['start']:.3f}-{w['end']:.3f}]"
            for w in segment_words)
        rows.append({"segment": position,
                     "reel_start": round(reel_start, 3),
                     "reel_end": round(cursor, 3),
                     "says": _cell(says) or "(no timed words)"})
    context = {
        "span_frame": {
            "reel_number": reel_number,
            "reel_name": getattr(moment, "timeline_name", ""),
            "fps": fps,
            "rule": ("one picture event per noun, landing before it; "
                     "at most one beat per spoken word; the beat lands "
                     "inside its own segment"),
        },
        "span_segments_toon": bridge.format_toon(
            ["segment", "reel_start", "reel_end", "says"],
            [{k: _cell(v) for k, v in row.items()} for row in rows]),
    }
    try:
        from library.tools.toon_serializer import json_to_toon
        context_text = json_to_toon(context)
    except Exception:
        context_text = json.dumps(context, indent=1, default=str)
    request = {
        "step_id": "reel_span_visual",
        "reel_number": reel_number,
        "reel_name": getattr(moment, "timeline_name", ""),
        "prompt": SPAN_HANDOFF,
        "context": context_text,
        "expected_schema": SPAN_EXPECTED_SCHEMA,
        "project_folder": project_folder,
        "timestamp": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    out_dir = ProjectLayout(project_folder).write_dir(Area.LLM_REQUESTS)
    path = os.path.join(str(out_dir), span_request_stem(reel_number) + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2)
    # The ask, logged AT THE ASK - see `write_request` above.
    try:
        from library.tools import reel_phase_log as _phase_log
        _phase_log.log_event(
            project_folder, reel_number,
            getattr(moment, "timeline_name", ""), _phase_log.PLAN_ASKED,
            detail=f"span ask written: {span_request_stem(reel_number)}.json")
    except Exception:
        pass
    return path


def read_span_answer(project_folder: str, reel_number: int) -> Optional[list]:
    """The model's span answer for this reel, or None when unanswered.

    The V6 `read_answer` discipline exactly: `{"span_visual_plan":
    [...]}` or a bare list. Anything else - including a file that will
    not parse - reads as unanswered rather than as an empty plan: a
    malformed answer is not a decision for no pictures.
    """
    from library.tools.project_layout import Area, ProjectLayout
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.LLM_RESPONSES)),
        span_request_stem(reel_number) + ".json")
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
            payload.get(SPAN_PLAN_KEY), list):
        return payload[SPAN_PLAN_KEY]
    return None


@dataclasses.dataclass
class SpanDropped:
    """One span beat the resolver discarded, and why."""

    element: str
    reason: str
    detail: str = ""
    entry: Dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict:
        if self.reason not in SPAN_DROP_REASONS:
            raise SpanPlanError(
                f"{self.reason!r} is not a reason a beat may be dropped "
                f"for. SPAN_DROP_REASONS is the whole of it: "
                f"{sorted(SPAN_DROP_REASONS)}.")
        row = {"element": self.element, "reason": self.reason,
               "what_the_reason_means": SPAN_DROP_REASONS[self.reason]}
        if self.detail:
            row["detail"] = self.detail
        return row


@dataclasses.dataclass
class ResolvedSpanPlan:
    """What `resolve_span_plan` returns: the bindable beats and the basis."""

    moments: List[dict] = field(default_factory=list)
    basis: str = SPAN_NOT_PLANNED
    proposed: int = 0
    dropped: List["SpanDropped"] = field(default_factory=list)

    def basis_record(self) -> dict:
        """The account that travels onto the span plan's own output.

        Every casualty is named. An empty picture track that SAYS
        which absence it is cannot be misread as a clean one - the
        V6 `basis_record` shape exactly.
        """
        return {
            "basis": self.basis,
            "what_the_basis_means": {
                SPAN_NOT_PLANNED: "no span plan was asked for on this run",
                SPAN_NO_EVENTS_PLANNED: (
                    "the model was asked and planned no beat - a "
                    "decision, not an absence"),
                SPAN_EVERY_EVENT_DROPPED: (
                    "the model planned beats and every one of them "
                    "was refused - the absence of a decision "
                    "surviving, not a decision to show nothing"),
                SPAN_EVENTS_PLANNED: (
                    "the model planned beats bound to real word timings"),
            }[self.basis],
            "proposed": self.proposed,
            "resolved": len(self.moments),
            "dropped": [d.as_record() for d in self.dropped],
        }


def _span_number(raw) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return None
    return None


def _span_text(raw) -> str:
    return str(raw).strip() if isinstance(raw, (str, int, float)) else ""


def resolve_span_plan(plan: Any, *, segment_words: Sequence[Sequence[dict]],
                      ranges, asked: bool = True) -> ResolvedSpanPlan:
    """Turn the model's span plan into beats bound to word timings.

    `segment_words` is `span_segment_words` output - one measured word
    list per segment, in reel seconds - and `ranges` are the keep
    ranges those lists were bucketed from, read here only for each
    segment's reel bounds. Every beat is searched against its OWN
    segment's words through `semantic_visual.find_phrase_window`
    (AGENTS.md 6: anchored by SEARCH, never asserted), and whatever
    cannot be bound is dropped under a reason from
    `SPAN_DROP_REASONS` - never guessed, never moved, never clamped.
    """
    from library.tools import semantic_visual

    resolved = ResolvedSpanPlan()
    if not asked:
        return resolved
    if plan is None:
        resolved.basis = SPAN_NO_EVENTS_PLANNED
        return resolved
    if isinstance(plan, dict):
        plan = plan.get(SPAN_PLAN_KEY, [])
    if not isinstance(plan, list):
        raise SpanPlanError(
            f"{SPAN_PLAN_KEY} must be a list of beats, got "
            f"{type(plan).__name__}.")

    resolved.proposed = len(plan)
    bounds: List[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in list(ranges or []):
        low = cursor
        cursor += max(0.0, float(end) - float(start))
        bounds.append((low, cursor))

    def drop(entry, key, reason, detail=""):
        if reason not in SPAN_DROP_REASONS:
            raise SpanPlanError(
                f"{reason!r} is not a reason a beat may be dropped for. "
                f"SPAN_DROP_REASONS is the whole of it: "
                f"{sorted(SPAN_DROP_REASONS)}.")
        resolved.dropped.append(
            SpanDropped(element=key or "(unnamed)", reason=reason,
                        detail=detail,
                        entry=entry if isinstance(entry, dict) else {}))

    claimed: Dict[int, set] = {}
    counts: Dict[int, int] = {}

    for raw in plan:
        entry = raw if isinstance(raw, dict) else {}
        if not isinstance(raw, dict):
            drop(entry, "", "entry_is_not_a_mapping",
                 f"got {type(raw).__name__}")
            continue
        seg_number = _span_number(entry.get("segment"))
        seg_index = (int(seg_number) - 1 if seg_number is not None
                     and float(seg_number).is_integer() else None)
        if (seg_index is None or seg_index < 0
                or seg_index >= len(bounds)):
            drop(entry, _span_text(entry.get("shows")),
                 "unknown_segment",
                 f"segment={entry.get('segment')!r} names no segment of "
                 f"a {len(bounds)}-segment span")
            continue
        label = f"segment {seg_index + 1} beat"
        found_look = sorted(
            {k for k in entry if str(k).strip() in LOOK_KEYS})
        if found_look:
            drop(entry, _span_text(entry.get("shows")) or label,
                 "look_value_in_picture_plan",
                 f"carries look keys {found_look}; the span planner "
                 f"decides what is shown, never the look")
            continue
        shows = _span_text(entry.get("shows"))
        if not shows:
            drop(entry, label, "no_subject_declared",
                 "a beat with no `shows` illustrates nothing")
            continue
        phrase = _span_text(entry.get("anchor_phrase"))
        has_seconds = (entry.get("start_seconds") is not None
                       or entry.get("duration_seconds") is not None)
        if phrase and has_seconds:
            drop(entry, shows, "conflicting_timing",
                 f"anchor_phrase={phrase!r} beside "
                 f"start_seconds={entry.get('start_seconds')!r} "
                 f"duration_seconds={entry.get('duration_seconds')!r}")
            continue
        if not phrase:
            drop(entry, shows, "no_anchor_declared",
                 "explicit seconds alone land near words instead of on "
                 "them; a beat is cued to its own words or not at all")
            continue
        words = list((segment_words or [[]])[seg_index]
                     if seg_index < len(list(segment_words or [])) else [])
        try:
            anchor_start, anchor_end = semantic_visual.find_phrase_window(
                words, phrase)
        except semantic_visual.SemanticVisualError as anchor_err:
            drop(entry, shows, anchor_err.reason, anchor_err.detail)
            continue
        lead = _span_number(entry.get("lead_seconds", 0.0))
        if lead is None or lead < 0:
            drop(entry, shows, "no_timing_declared",
                 f"lead_seconds={entry.get('lead_seconds')!r} is not a "
                 f"number at or above zero")
            continue
        event = anchor_start - lead
        low, high = bounds[seg_index]
        if not (low - 1e-9 <= event <= high + 1e-9):
            drop(entry, shows, "beat_outside_segment",
                 f"beat {event:.3f}s sits outside segment {seg_index + 1} "
                 f"({low:.3f}-{high:.3f}s)")
            continue
        first_token = next(
            (t for t in (semantic_visual.normalize_word(p)
                         for p in phrase.split()) if t), "")
        occurrence = next(
            (i for i, w in enumerate(words)
             if w.get("start") == anchor_start
             and semantic_visual.normalize_word(w.get("word")) == first_token),
            None)
        if occurrence is None:
            occurrence = next(
                (i for i, w in enumerate(words)
                 if semantic_visual.normalize_word(w.get("word")) == first_token),
                None)
        seen = claimed.setdefault(seg_index, set())
        if counts.get(seg_index, 0) >= len(words) or (
                occurrence is not None and occurrence in seen):
            drop(entry, shows, "more_events_than_speech_supports",
                 f"segment {seg_index + 1} speaks {len(words)} word(s) "
                 f"and {'its beats already claim them all' if occurrence is None or occurrence not in seen else 'this word is already claimed by an earlier beat'}")
            continue
        if occurrence is not None:
            seen.add(occurrence)
        counts[seg_index] = counts.get(seg_index, 0) + 1
        resolved.moments.append({
            "segment": seg_index + 1,
            "shows": shows,
            "anchor_phrase": phrase,
            "lead_seconds": lead,
            "anchor_start": round(anchor_start, 3),
            "anchor_end": round(anchor_end, 3),
            "event_start": round(event, 3),
            # How this beat was timed: the measured word window an
            # anchor phrase searched for - the V6 `timing_basis`
            # spelling, because it is the same fact.
            "timing_basis": f"word_window:{phrase}",
            "why": _span_text(entry.get("why")),
        })

    if resolved.moments:
        resolved.basis = SPAN_EVENTS_PLANNED
    elif resolved.proposed:
        resolved.basis = SPAN_EVERY_EVENT_DROPPED
    else:
        resolved.basis = SPAN_NO_EVENTS_PLANNED
    return resolved


# ── The span's own record: the resolved plan, where the pipeline reads it ──
#
# The V6 `PLAN_FILENAME` convention exactly - same REVIEW area, same
# `{"format": ..., "plans": [...]}` envelope, same merge-per-reel write
# (a partial build must not delete the reels it did not touch), same
# read/rename/drop helpers for the staging round-trip - with a payload
# of its own. A span moment (segment, shows, anchor window, event_start)
# is not an overlay segment (timeline_start, total_frames, elements),
# so the per-reel rows differ while everything around them matches:
# the convention generalises, the shape does not.
#
# The verifier (F23) is the reader, so this is not a declared output
# with no reader. The placer is deliberately not one yet: moments carry
# `shows` as free-text provenance (the V6 `subject` discipline - travelled,
# never read), and rendering that as on-screen copy would make the engine
# draw model free text as artwork; segments need look values the planner
# refuses to emit, which must arrive by project declaration; and turning
# event instants into windows would choose coverage PR 776's tiling
# guarantees already own. A placer needs its own change carrying those
# three decisions - half of one here would mis-place silently.
#
# `tests/test_reel_span_record.py`.

SPAN_PLAN_FILENAME = "span_visual_plans.json"
"""Where the build RECORDS each reel's resolved span plan, per project.

Read back by the conformance check (F23) rather than re-derived, for
the reason `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` §4 gives: a
re-derived plan is only the build's plan while nothing changed in
between.
"""


def _span_record(reel_name: str, basis: str, entries: list, resolved=None,
                 dropped=(), moments=None) -> dict:
    """One reel's span-plan record, in the shape F23 grades."""
    assert basis in SPAN_BASES, f"{basis!r} is not a span basis: {SPAN_BASES}"
    if resolved is not None and not dropped:
        dropped = resolved.dropped
    record: Dict[str, Any] = {
        "reel": reel_name,
        "basis": basis,
        "entries": entries if isinstance(entries, list) else [],
        "dropped": [
            d.as_record() if isinstance(d, SpanDropped) else dict(d)
            for d in (dropped or [])],
        "moments": [dict(m) for m in (
            moments if moments is not None
            else (resolved.moments if resolved is not None else []))],
    }
    if resolved is not None:
        record["resolved"] = len(resolved.moments)
        record["proposed"] = resolved.proposed
    else:
        record["resolved"] = len(record["moments"])
        record["proposed"] = len(record["entries"])
    return record


def span_record_for_build(moment, transcript: dict, ranges, project_folder: str,
                           fps: float, timeline_name: str = "") -> dict:
    """Resolve one reel's span plan and return its record. Pure: no Resolve.

    The ask is written fresh on every build from the moment, the
    transcript and these same ranges (the V6 `write_request` discipline);
    the answer is read off the response file when a model has written
    one. No request - the reel speaks no timed words - and no answer
    both record SPAN_NOT_PLANNED: neither is a decision for no pictures.
    An answered-but-empty plan records SPAN_NO_EVENTS_PLANNED, which IS
    one, and F23 grades the two differently.
    """
    reel_number = int(moment.number)
    name = timeline_name or getattr(moment, "timeline_name", "")
    request_path = write_span_request(
        moment, transcript, ranges, project_folder, fps=fps)
    return resolve_span_record(
        moment, transcript, ranges, project_folder, fps=fps,
        timeline_name=name, asked=bool(request_path))


def resolve_span_record(moment, transcript: dict, ranges, project_folder: str,
                        fps: float, timeline_name: str = "",
                        *, asked: bool) -> dict:
    """Resolve one reel's span plan against an already-written ask.

    The second half of `span_record_for_build`, split out so a caller
    that wrote the ask itself - the pass-1 build's shared
    `reel_build.write_visual_asks`, or `reel.ask` - resolves without
    writing it twice. `asked` is whether the ask file was written: False
    is the reel-speaks-no-timed-words case, recorded as
    SPAN_NOT_PLANNED exactly as the combined form records it.
    """
    reel_number = int(moment.number)
    name = timeline_name or getattr(moment, "timeline_name", "")
    if not asked:
        return _span_record(
            name, SPAN_NOT_PLANNED, [],
            dropped=[SpanDropped(
                element="(unresolved)",
                reason="no_word_timings_to_anchor_against",
                detail=(f"no timed words in {len(list(ranges or []))} "
                        f"keep range(s), so no beat has a window"))])
    answer = read_span_answer(project_folder, reel_number)
    if answer is None:
        print(f"  {name}: NO SPAN PICTURES - no model answer on file "
              f"({span_request_stem(reel_number)}.json), recording "
              f"{SPAN_NOT_PLANNED}", file=sys.stderr)
        # The wait, by the waiter: asked, unanswered, recorded as such.
        try:
            from library.tools import reel_phase_log as _phase_log
            _phase_log.log_wait(
                project_folder, reel_number, name,
                f"no model answer on file "
                f"({span_request_stem(reel_number)}.json) - "
                f"recording {SPAN_NOT_PLANNED}")
        except Exception:
            pass
        return _span_record(name, SPAN_NOT_PLANNED, [])
    resolved = resolve_span_plan(
        answer, segment_words=span_segment_words(ranges, transcript),
        ranges=ranges, asked=True)
    for dropped in resolved.dropped:
        print(f"  {name}: span beat dropped ({dropped.reason}) "
              f"{dropped.element}: {dropped.detail}", file=sys.stderr)
    if not resolved.moments and resolved.proposed:
        print(f"  {name}: SPAN PLAN REFUSED - the model planned "
              f"{resolved.proposed} beat(s) and every one was refused",
              file=sys.stderr)
    return _span_record(name, resolved.basis, answer, resolved=resolved)


def span_plans_path(project_folder: str) -> str:
    """Where the merged per-reel span records live."""
    from library.tools.project_layout import Area, ProjectLayout
    return os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        SPAN_PLAN_FILENAME)


def write_span_records(project_folder: str, records: Sequence[dict]) -> str:
    """Merge this build's span records into the stored file, per reel.

    The V6 `write_records` merge exactly: every reel THIS build touched
    is replaced by what it resolved - or by its empty basis, when it
    resolved none - and every other reel's record is left exactly as it
    was. Overwriting the file would delete the record of the reels a
    partial (`only`) build did not touch, and F23 would then grade those
    timelines against an absence.
    """
    path = span_plans_path(project_folder)
    stored: Dict[str, Any] = {"format": "span_visual_plans/1",
                              "plans": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                stored = json.load(handle) or stored
        except (OSError, ValueError):
            stored = {"format": "span_visual_plans/1", "plans": []}
    touched = {str(r.get("reel")) for r in (records or ())}
    kept = [p for p in (stored.get("plans") or [])
            if str(p.get("reel")) not in touched]
    kept.extend(records or [])
    stored["plans"] = kept
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
    return path


def read_span_records(project_folder: str) -> dict:
    """What the build recorded, or `{}` when it recorded nothing."""
    path = span_plans_path(project_folder)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def span_record_for_reel(records: Optional[dict], reel_name: str) -> Optional[dict]:
    """The recorded span plan for one reel by NAME, or None.

    None means "this build recorded nothing for this reel", which is
    what a reel built before span planning existed looks like, and F23
    returns nothing rather than grading against an absence.
    """
    for record in ((records or {}).get("plans") or []):
        if str(record.get("reel")) == str(reel_name):
            return record
    return None


def rename_span_record_reels(project_folder: str, mapping: dict) -> None:
    """Rename `plans[].reel` fields, staging -> final.

    The staging half of promotion, mirroring `rename_record_reels`: the
    verifier graded the staging against this file, and after promotion
    the same resolutions live under the final name. A record the previous
    build left under the final name is REPLACED, not kept beside the
    renamed one: two records for one reel leave `span_record_for_reel`
    reading the stale first, so the next verifier grades the promoted
    timeline against the absence.
    """
    if not mapping:
        return
    path = span_plans_path(project_folder)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle) or {}
    finals = set(mapping.values())
    stored["plans"] = [record for record in (stored.get("plans") or [])
                       if record.get("reel") not in finals]
    for record in stored["plans"]:
        if record.get("reel") in mapping:
            record["reel"] = mapping[record.get("reel")]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)


def drop_span_record_reels(project_folder: str, names) -> None:
    """Remove span records for the named reels.

    The gate-fail half of a refused staging: no baseline may survive
    for a container that is about to be deleted, or F23 would grade the
    surviving approved timeline against a refused build's resolutions.
    """
    drop = set(names or ())
    if not drop:
        return
    path = span_plans_path(project_folder)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle) or {}
    stored["plans"] = [p for p in (stored.get("plans") or [])
                       if str(p.get("reel")) not in drop]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
