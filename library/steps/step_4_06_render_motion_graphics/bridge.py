#!/usr/bin/env python3
"""Step 4.06 pre-bridge: what a model needs to plan the overlay layer.

Until now this step reached no prompt at all.  It resolved the whole
motion-graphics layer from two brand-template booleans, so a project
that named no template got nothing and nobody was ever asked.  The
captain's ruling of 2026-09-02 - *"the LLM was still meant to plan these
things ... the brand template is only a secondary"* - makes this the
planning surface it never had.

What it puts in front of the model:

* **the whole roster**, from `motion_graphics_vocabulary.roster_rows()`,
  with its legend and its axis table.  Nothing is shortlisted: eighteen
  entries fit, and whatever selects a shortlist becomes the chooser
  (AGENTS.md 10.5).  `reachable` is a column, so the model is told the
  truth about what the renderer can draw and still chooses.
* **the timeline as CONTEXT, not as a grid.**  One row per spine block
  with its span, what it is and what is said in it, so the model knows
  where the speech and the cutaways are.  The handoff says in as many
  words that a graphic need not start or end on one of these.
* **what the brand template declares**, or that it declares nothing.
  Stated either way, because a silent absence is what put eight
  fully-transparent renders on 001.

It states no count, no duration, no colour and no default.  A plan of
none and a plan of nine are both legal answers and this file expresses
no preference between them.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from library.tools import motion_graphics_plan as plan  # noqa: E402
from library.tools import motion_graphics_vocabulary as vocabulary  # noqa: E402
from library.tools.brand_palette import roles_from_palette  # noqa: E402
from library.tools.safe_area import resolve_safe_area  # noqa: E402

# How much of a block's line reaches the context table. The whole line is
# in `timed_spine`, which this step is not routed; this column exists so
# the model can tell one block from another, not so it can read the
# script from here.
TEXT_SUMMARY_CHARS = 80


def format_toon(headers, rows):
    if not rows:
        return f"[0]{{{','.join(headers)}}}\n"
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out


def _cell(value) -> str:
    """One line, TOON-safe. Tabs and newlines are the row separators."""
    return " ".join(str(value or "").split())


def _block_text(block: dict) -> str:
    content = block.get("content")
    text = ""
    if isinstance(content, dict):
        text = content.get("text") or ""
    elif isinstance(content, str):
        text = content
    text = _cell(text)
    if len(text) > TEXT_SUMMARY_CHARS:
        text = text[:TEXT_SUMMARY_CHARS - 1] + "…"
    return text


def timeline_rows(audio_spine: dict) -> list:
    """The spine as context. Not a grid the plan has to land on."""
    rows = []
    for block in audio_spine.get("structure", []):
        rows.append({
            "block_position": block.get("position", ""),
            "block_type": block.get("block_type", ""),
            "timeline_start": round(float(block.get("timeline_start", 0) or 0), 2),
            "timeline_end": round(float(block.get("timeline_end", 0) or 0), 2),
            "says": _block_text(block),
        })
    return rows


def brand_refinement(brand_style: dict, brand_effect: dict) -> dict:
    """What the template refines, said either way.

    A project with no template gets `declares_a_palette: false` and a
    note saying the plan states its own colours. It does NOT get a
    reduced set of elements, a substitute palette or a switched-off
    layer - that gate is the defect this step was rebuilt to remove.
    """
    roles = roles_from_palette((brand_style or {}).get("color_palette")) or {}
    return {
        "declares_a_palette": bool(roles),
        "palette_roles": roles,
        "how_a_colour_is_resolved": (
            "State `colour_role` and a declared palette resolves it. "
            "State `color` as a hex value and that is used instead. "
            "State neither and the entry is dropped - there is no house "
            "colour to fall back to."
        ),
        "template_effect_slots_declared": sorted(
            k for k, v in (brand_effect or {}).items() if v is not None),
        "what_a_template_absence_means": (
            "Nothing. A project that names no brand template plans the "
            "same layer; the template only saves the plan from having to "
            "state a colour itself."
        ),
    }


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:  # noqa: BLE001 - the runner reads stdout
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    audio_spine = data.get("audio_spine") or {}
    project_folder = data.get("project_folder", "")
    fps = data.get("project_fps", 30)

    rows = timeline_rows(audio_spine)
    duration = max((r["timeline_end"] for r in rows), default=0.0)

    frame_error = ""
    try:
        from library.tools.delivery_format import resolve_delivery_format
        width, height = resolve_delivery_format(project_folder)
        delivery_frame: dict = {"width": width, "height": height}
        safe = resolve_safe_area(
            project_folder or None, width=width, height=height).as_props()
    except Exception as exc:  # noqa: BLE001 - a bridge must not take the run down
        # No assumed frame: planning the layer against 1080x1920 while
        # the delivery is landscape is what drew project 001's overlays
        # as a lighter central band. An undeclared frame is SAID, and
        # the safe area - which is measured against a frame - is empty
        # rather than measured against a wrong one.
        delivery_frame = {"unknown": True}
        safe = {}
        frame_error = str(exc)

    compressed = {
        "motion_elements_toon": format_toon(
            list(vocabulary.ROSTER_LEGEND),
            [{k: _cell(v) for k, v in row.items()}
             for row in vocabulary.roster_rows()],
        ),
        "motion_axes_toon": format_toon(
            ["axis", "ranges_over", "positions", "continuous",
             "resolved_against"],
            [{k: _cell(v) for k, v in row.items()}
             for row in vocabulary.axis_rows()],
        ),
        "motion_elements_legend": dict(vocabulary.ROSTER_LEGEND),
        "timeline_context_toon": format_toon(
            ["block_position", "block_type", "timeline_start",
             "timeline_end", "says"],
            rows,
        ),
        "motion_graphics_frame": {
            "timeline_duration_seconds": round(duration, 3),
            "fps": fps,
            "delivery_frame": delivery_frame,
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
                "and never quietly rendered as nothing."
            ),
        },
        "brand_refinement": brand_refinement(
            data.get("brand_style") or {}, data.get("brand_effect") or {}),
    }
    if frame_error:
        compressed["motion_graphics_frame"]["frame_error"] = frame_error
        compressed["motion_graphics_frame"]["frame_unknown"] = (
            "The delivery format could not be resolved, so no frame is "
            "stated and no safe area is measured. Plan nothing that "
            "needs pixel positions until it is declared - see "
            "library/tools/delivery_format.py.")

    print(json.dumps(compressed))


if __name__ == "__main__":
    main()
