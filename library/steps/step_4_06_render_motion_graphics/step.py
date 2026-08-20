#!/usr/bin/env python3
"""
Step 4.06: Render Motion Graphics (Remotion)

Takes the enhancement spec from step 4.03 and creative direction, then
renders per-spine-block motion graphics overlays to ProRes 4444 videos
with alpha channel using Remotion.

Each spine block gets its own rendered overlay clip, which the Resolve
builder places at the correct timeline position on V4.

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

Classification: Deterministic / Direct Action
Idempotent: Yes (same inputs -> same rendered overlays)

Input:  {
    "enhancement_spec": { ... },
    "audio_spine": { structure: [...] },
    "creative_direction": { ... }
}
Output: {
    "motion_graphics_overlay": {
        "available": bool,
        "segments": [
            {
                "overlay_path": str,
                "timeline_start": float,
                "timeline_end": float,
                "block_position": int,
                "total_frames": int
            }
        ],
        "format": "ProRes 4444",
        "has_alpha": true,
        "fps": 30
    },
    "timed_text_overlay": {
        "declared": bool,
        "available": bool,     # only when declared
        "segments": [
            {
                "overlay_path": str,
                "timeline_start": float,
                "timeline_end": float,
                "total_frames": int,
                "moment_count": int
            }
        ]
    }
}
"""
import json
import os
import subprocess
import sys

from generate_motion_props import generate_motion_props

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.delivery_format import resolve_delivery_format  # noqa: E402
from library.tools.timed_text_render import (  # noqa: E402
    TIMED_TEXT_RENDER_DIRNAME,
)


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


def main():
    import sys
    data = json.loads(sys.stdin.read())
    enhancement_spec = data.get("enhancement_spec", {})
    audio_spine = data.get("audio_spine", {})
    creative_direction = data.get("creative_direction", {})
    project_folder = data.get("project_folder", "")

    # Find Remotion project (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    REMOTION_DIR = os.path.join(PILOT_ROOT, "remotion-subtitles")

    if not os.path.isdir(REMOTION_DIR):
        print(f"ERROR: Remotion project not found at {REMOTION_DIR}",
              file=sys.stderr)
        json.dump({
            "motion_graphics_overlay": {
                "available": False,
                "error": "Remotion project not found at remotion-subtitles/"
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

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

    # Output directory
    output_dir = os.path.join(project_folder, "pipeline_output") if project_folder else os.path.join(PILOT_ROOT, "pipeline_output")
    os.makedirs(output_dir, exist_ok=True)
    mg_output_dir = os.path.join(output_dir, "motion_graphics_segments")
    os.makedirs(mg_output_dir, exist_ok=True)

    # Generate per-block props
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
        json.dump({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": f"bookend render failed: {e}",
            }
        }, sys.stdout, indent=2)
        sys.exit(1)
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
            os.path.join(output_dir, TIMED_TEXT_RENDER_DIRNAME),
            fps=fps, width=width, height=height,
            spine_structure=structure,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        json.dump({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": f"timed text render failed: {e}",
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    timed_text_overlay = _timed_text_output(timed_text_segments, fps)

    props_list = generate_motion_props(
        enhancement_spec, creative_direction, audio_spine,
        fps=fps, width=width, height=height,
        # Brand slots, injected by the pipeline runner for any step whose
        # manifest declares them. They drive the accent colour and whether
        # the corner accents and progress bar are drawn at all (P3.1).
        brand_style=data.get("brand_style", {}),
        brand_effect=data.get("brand_effect", {}),
    )

    if not props_list:
        print("WARNING: No motion graphics blocks to render", file=sys.stderr)
        json.dump({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "reason": "No motion graphics blocks found in audio spine"
            },
            "timed_text_overlay": timed_text_overlay,
        }, sys.stdout, indent=2)
        return

    print(f"Rendering {len(props_list)} motion graphics segments...",
          file=sys.stderr)

    segments = []
    for i, props in enumerate(props_list):
        block_pos = props.get("_block_position")
        tl_start = props.get("_timeline_start")
        tl_end = props.get("_timeline_end")
        block_type = props.get("_block_type")
        total_frames = props["durationInFrames"]
        safe_pos = str(block_pos).replace(" ", "_")
        segment_name = f"mg_block_{safe_pos}"
        overlay_path = os.path.join(mg_output_dir, f"{segment_name}.mov")
        props_path = os.path.join(mg_output_dir, f"{segment_name}_props.json")

        # Write props file
        with open(props_path, "w") as f:
            json.dump(props, f, indent=2)

        print(f"  [{i+1}/{len(props_list)}] {segment_name} "
              f"({block_type}, {total_frames}f, "
              f"tl:{tl_start:.1f}-{tl_end:.1f}s)", file=sys.stderr)

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
            "timeline_start": tl_start,
            "timeline_end": tl_end,
            "block_position": block_pos,
            "total_frames": total_frames,
        })

    print(f"\nRendered {len(segments)}/{len(props_list)} motion graphics segments",
          file=sys.stderr)

    if segments:
        try:
            sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
            from tools.qa.asset_qa import verify_alpha_channel
            if not verify_alpha_channel(segments[0]["overlay_path"]):
                print("WARNING: QA Check 2.1 Failed: First motion graphics segment missing alpha or purely black", file=sys.stderr)
            else:
                print("QA Check 2.1 Passed: Motion graphics alpha verified", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: QA Check 2.1 execution failed: {e}", file=sys.stderr)

    json.dump({
        "motion_graphics_overlay": {
            "available": len(segments) > 0,
            "segments": segments,
            "format": "ProRes 4444",
            "has_alpha": True,
            "fps": fps,
            "total_segments": len(segments),
        },
        "timed_text_overlay": timed_text_overlay,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
