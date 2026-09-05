#!/usr/bin/env python3
"""Step 4.06 post-bridge: render the planned motion-graphics layer.

Takes the model's `motion_graphics_plan` (see `handoff.md` and
`library/tools/motion_graphics_plan.py`), resolves it against the brand
template's palette when there is one, cuts it into non-overlapping
overlay segments and renders each to a ProRes 4444 clip with alpha.

**The layer is planned, not derived.**  This file used to resolve the
whole thing from `effect.motion_accents` and `effect.motion_progress_bar`,
so a project naming no brand template rendered eight fully transparent
segments and reported motion graphics as delivered.  The captain's
ruling of 2026-09-02 is that the gate was the bug.

**Every element carries its own timing.**  A segment's span comes from
the entries clustered into it, never from a spine block; several
elements can be on screen at once, on different rows, inside one
segment.

This step is also where the OTHER two Remotion-rendered things a brand
template may declare become files, because they need the same prepped
Remotion project and both have to exist before compile_manifest can
reference them:

* composition-mode bookends (``content.bookends``, V1) - see
  ``library/tools/bookend_render.py``;
* timed text moments (``effect.timed_text_overlay``, V6) - see
  ``library/tools/timed_text_overlay.py``.  A template that declares no
  moments renders none, and says so in the output rather than emitting
  ``available: false``, which the runner reads as a failed step.

Classification: Hybrid / Content generation
"""
import json
import os
import subprocess
import sys

from generate_motion_props import PLAN_KEY, generate_motion_props

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.delivery_format import resolve_delivery_format  # noqa: E402
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402


def _timed_text_output(segments: list, fps: int) -> dict:
    """The step's timed_text_overlay output for the segments it rendered.

    An undeclared slot carries NO `available` key on purpose:
    `check_output_is_real` in run_pipeline treats `available: false`
    anywhere in a step's output as a failed run, and "this template
    declares no timed text" is the normal case, not a failure.
    """
    if not segments:
        return {
            "declared": False,
            "segments": [],
            "reason": "brand template declares no effect.timed_text_overlay",
        }
    return {
        "declared": True,
        "available": True,
        "segments": segments,
        "format": "ProRes 4444",
        "has_alpha": True,
        "fps": fps,
        "total_segments": len(segments),
    }


def _nothing_to_draw_output(reason: str, basis: dict = None) -> dict:
    """The motion_graphics_overlay output when no segment would draw.

    Shaped like `_timed_text_output`'s undeclared case and for the same
    reason: no `available` key at all. `check_output_is_real` in
    run_pipeline reads `available: false` anywhere in a step's output as
    a failed run, and an empty layer is a legitimate answer.

    **It carries the basis.** An empty layer that says WHICH absence it
    is cannot be misread as a clean one: `no_elements_planned` is a
    decision the model took and `every_entry_dropped` is the absence of
    one surviving, and the dropped entries are named with their reasons.
    Same line `vfx_plan_basis` draws.
    """
    out = {
        "declared": False,
        "segments": [],
        "total_segments": 0,
        "reason": reason,
    }
    if basis is not None:
        out["planning_basis"] = basis
    return out


class MotionGraphicsRenderRefused(Exception):
    """The step cannot deliver overlays, and says so carrying its payload.

    The three refusal paths used to emit and then `sys.exit(1)` where
    they stood.  Both halves still happen in `main()`; a function that
    kills its caller's process cannot be called by one.
    """

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(
            payload.get("motion_graphics_overlay", {}).get("error", "refused"))


def render_motion_graphics(data: dict) -> dict:
    """Render the model's motion-graphics plan, its bookends and timed text.

    `data` is the merged dict the runner hands a post-bridge: the step's
    inputs, the pre-bridge's output, and the model's answer.  Raises
    `MotionGraphicsRenderRefused` where nothing can be delivered.
    """
    import sys
    audio_spine = data.get("audio_spine", {})
    project_folder = data.get("project_folder", "")
    # The model's answer. `enhancement_spec` and `creative_direction`
    # are declared and routed for the PROMPT, which is where they are
    # read now that this step has one - the runner projects them into
    # the context and the plan comes back here already decided.
    motion_graphics_plan = data.get(PLAN_KEY)

    # Find Remotion project (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    REMOTION_DIR = os.path.join(PILOT_ROOT, "remotion-subtitles")

    if not os.path.isdir(REMOTION_DIR):
        print(f"ERROR: Remotion project not found at {REMOTION_DIR}",
              file=sys.stderr)
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "error": "Remotion project not found at remotion-subtitles/"
            }
        })

    # Prep Remotion: link brand assets (logos, fonts) into Remotion's
    # public/brand/ directory so staticFile("brand/...") resolves at render
    # time.  There is no composition staging or Root.tsx generation -
    # compositions live in src/compositions/ and Root.tsx is committed.
    try:
        sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
        from tools.remotion_brand_linker import prep_remotion
        prep_result = prep_remotion(project_folder=project_folder)
        brand_info = prep_result.get("brand", {})
        if brand_info.get("linked"):
            print(f"  Linked {brand_info['count']} brand assets from {brand_info['source']}",
                  file=sys.stderr)
    except ImportError:
        pass

    # Output directory. No repo fallback: see step 4.05 and
    # library/tools/project_layout.py.
    layout = ProjectLayout(project_folder)
    mg_output_dir = str(layout.write_dir(
        Area.MOTION_GRAPHICS_SEGMENTS, step="render_motion_graphics"))

    # The timebase. Every planned span is expressed in timeline
    # seconds and converted here; nothing is derived from a block.
    fps = data.get("project_fps", 30)
    # The overlay is rendered AT THE DELIVERY FORMAT, so it composites
    # 1:1 onto the timeline. Reading a source-derived resolution here is
    # what put a vertical overlay on a landscape timeline as a lighter
    # band down the middle. See library/tools/delivery_format.py.
    width, height = resolve_delivery_format(project_folder)
    # Bookends first: an intro / outro / end card the brand template
    # declared reaches the spine in step 2.05, and has to be a file before
    # compile_manifest can put it on V1. A template that declares none -
    # which is every template unless someone opted in - renders none.
    # See library/tools/bookends.py.
    try:
        sys.path.insert(0, PILOT_ROOT)
        from library.tools.bookend_render import render_declared_bookends
        bookends_rendered = render_declared_bookends(
            audio_spine.get("structure", []), REMOTION_DIR,
            fps=fps, width=width, height=height)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": f"bookend render failed: {e}",
            }
        })
    if bookends_rendered:
        print(f"Bookends ready: {len(bookends_rendered)}", file=sys.stderr)

    # Timed text moments. Declare none and render none - the opt-in shape
    # of every effect slot. The spine is what a moment is timed FROM, and
    # what bounds a moment given in absolute frames; see
    # library/tools/timed_text_overlay.py.
    #
    # The PROJECT's own `effect.timed_text_overlay` wins over the brand
    # template's, because a card is series artwork and artwork is a
    # project asset (docs/ASSET_LIBRARY_PLAN.md section 3, ratified
    # 2026-08-20). resolve_declaration is where that precedence lives.
    structure = audio_spine.get("structure", [])
    try:
        from library.tools.timed_text_overlay import resolve_declaration
        from library.tools.timed_text_render import render_timed_text_segments
        timed_text_segments = render_timed_text_segments(
            resolve_declaration(data.get("brand_effect", {}), project_folder),
            REMOTION_DIR,
            str(layout.write_dir(
                Area.TIMED_TEXT_SEGMENTS, step="render_motion_graphics")),
            fps=fps, width=width, height=height,
            spine_structure=structure, project_folder=project_folder,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": f"timed text render failed: {e}",
            }
        })

    timed_text_overlay = _timed_text_output(timed_text_segments, fps)

    segments_plan, resolved = generate_motion_props(
        motion_graphics_plan,
        audio_spine,
        fps=fps, width=width, height=height,
        # The palette REFINES an entry's colour_role. A project that
        # names no template resolves nothing here and its plan states
        # its own colours; the layer is not reduced by the absence.
        brand_style=data.get("brand_style", {}),
        project_folder=project_folder,
        asked=motion_graphics_plan is not None,
    )
    basis = resolved.basis_record()

    for dropped in resolved.dropped:
        print(f"  dropped {dropped.element}: {dropped.reason}"
              + (f" - {dropped.detail}" if dropped.detail else ""),
              file=sys.stderr)

    if not segments_plan:
        print(f"No motion graphics to draw ({basis['basis']}): "
              f"{basis['what_the_basis_means']}", file=sys.stderr)
        return {
            "motion_graphics_overlay": _nothing_to_draw_output(
                basis["what_the_basis_means"], basis),
            "timed_text_overlay": timed_text_overlay,
        }

    print(f"Rendering {len(segments_plan)} motion graphics segments "
          f"({basis['resolved']} elements)...", file=sys.stderr)

    segments = []
    for i, planned in enumerate(segments_plan):
        props = planned["props"]
        segment_name = f"mg_{planned['index']:03d}"
        overlay_path = os.path.join(mg_output_dir, f"{segment_name}.mov")
        props_path = os.path.join(mg_output_dir, f"{segment_name}_props.json")

        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(props, f, indent=2)

        print(f"  [{i+1}/{len(segments_plan)}] {segment_name} "
              f"({', '.join(planned['elements'])}, "
              f"{planned['total_frames']}f, "
              f"tl:{planned['timeline_start']:.2f}-"
              f"{planned['timeline_end']:.2f}s)", file=sys.stderr)

        # Render via Remotion
        try:
            result = subprocess.run(
                ["npx", "remotion", "render",
                 "MotionGraphics",
                 overlay_path,
                 "--props", props_path,
                 "--codec", "prores",
                 "--prores-profile", "4444",
                 "--image-format", "png",
                 "--transparent",
                 ],
                cwd=REMOTION_DIR,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=120,
            )

            if result.returncode != 0:
                print(f"    WARN: Render failed: {result.stderr[:200]}",
                      file=sys.stderr)
                continue

            print(f"    OK: {overlay_path}", file=sys.stderr)

        except subprocess.TimeoutExpired:
            print(f"    WARN: Render timed out for {segment_name}",
                  file=sys.stderr)
            continue

        segments.append({
            "overlay_path": overlay_path,
            "timeline_start": planned["timeline_start"],
            "timeline_end": planned["timeline_end"],
            "total_frames": planned["total_frames"],
            "element_count": planned["element_count"],
            "elements": planned["elements"],
        })

    print(f"\nRendered {len(segments)}/{len(segments_plan)} motion graphics "
          f"segments", file=sys.stderr)

    if segments:
        try:
            sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
            from tools.qa.asset_qa import verify_alpha_channel
            if not verify_alpha_channel(segments[0]["overlay_path"]):
                print("WARNING: QA Check 2.1 Failed: First motion graphics "
                      "segment missing alpha or purely black", file=sys.stderr)
            else:
                print("QA Check 2.1 Passed: Motion graphics alpha verified",
                      file=sys.stderr)
        except Exception as e:
            print(f"WARNING: QA Check 2.1 execution failed: {e}",
                  file=sys.stderr)

    return {
        "motion_graphics_overlay": {
            "available": len(segments) > 0,
            "segments": segments,
            "format": "ProRes 4444",
            "has_alpha": True,
            "fps": fps,
            "total_segments": len(segments),
            "planning_basis": basis,
        },
        "timed_text_overlay": timed_text_overlay,
    }


def main():
    import sys
    try:
        result = render_motion_graphics(json.loads(sys.stdin.read()))
    except MotionGraphicsRenderRefused as refusal:
        json.dump(refusal.payload, sys.stdout, indent=2)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
