#!/usr/bin/env python3
"""
Step 4.05: Render Subtitles (Remotion)

Takes the subtitle plan from step 4.01 and renders it to per-spine-block
ProRes 4444 video overlays with alpha channel using Remotion.

Each spine block with subtitles gets its own rendered overlay clip. The
Resolve builder (step 6.01) places each segment at its timeline position
on V3.

Workflow:
  1. Generate per-block Remotion input props from the subtitle plan
     (via generate_remotion_props.py)
  2. Write props to per-block JSON files
  3. Run `npx remotion render` for each block
  4. Return the list of rendered overlay paths for the manifest

Classification: Deterministic / Direct Action
Idempotent: Yes (same subtitle plan -> same rendered overlays)

Input:  {
    "subtitle_plan": { subtitle_entries: [...] },
    "audio_spine": { structure: [...] }
}
Output: {
    "subtitle_overlay": {
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
    }
}
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from generate_remotion_props import generate_subtitle_props_per_block

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools.step_stdout import claim_stdout, emit
from library.tools.delivery_format import resolve_delivery_format
from library.tools.project_layout import Area, ProjectLayout


def main():
    # First, before any dependency can grab it: vision_model printed
    # its model-loading line into the middle of this step's result.
    # See library/tools/step_stdout.py.
    claim_stdout()
    data = json.loads(sys.stdin.read())
    subtitle_plan = data.get("subtitle_plan", {})
    audio_spine = data.get("audio_spine", {})
    project_folder = data.get("project_folder", "")

    # Find Remotion project (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    REMOTION_DIR = os.path.join(PILOT_ROOT, "remotion-subtitles")

    if not os.path.isdir(REMOTION_DIR):
        print(f"ERROR: Remotion project not found at {REMOTION_DIR}",
              file=sys.stderr)
        emit({
            "subtitle_overlay": {
                "available": False,
                "error": "Remotion project not found at remotion-subtitles/"
            }
        })
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

    # Output directory.
    #
    # There is no repo fallback any more. Without a project_folder this
    # step wrote its rendered overlays into <repo>/pipeline_output/ -
    # inside the checkout, and inside a disposable worktree whenever the
    # run happened in one. The layout owner raises instead, so a run with
    # no project says so rather than banking work somewhere nothing will
    # look for it. See library/tools/project_layout.py.
    layout = ProjectLayout(project_folder)
    sub_output_dir = str(
        layout.write_dir(Area.SUBTITLE_SEGMENTS, step="render_subtitles"))

    # Generate per-block props
    fps = data.get("project_fps", 30)
    # The overlay is rendered AT THE DELIVERY FORMAT, so it composites
    # 1:1 onto the timeline. Reading a source-derived resolution here is
    # what put a vertical overlay on a landscape timeline as a lighter
    # band down the middle. See library/tools/delivery_format.py.
    width, height = resolve_delivery_format(project_folder)
    props_list = generate_subtitle_props_per_block(subtitle_plan, fps=fps, width=width, height=height, audio_spine=audio_spine)

    if not props_list:
        print("WARNING: No subtitle blocks to render", file=sys.stderr)
        emit({
            "subtitle_overlay": {
                "available": False,
                "segments": [],
                "reason": "No subtitle entries found in subtitle plan"
            }
        })
        return

    print(f"Rendering {len(props_list)} subtitle segments...",
          file=sys.stderr)

    segments = []
    for i, props in enumerate(props_list):
        block_pos = props.get("_block_position")
        tl_start = props.get("_timeline_start")
        tl_end = props.get("_timeline_end")
        total_frames = props["durationInFrames"]
        num_subs = len(props.get("subtitles", []))

        # Generate output path
        # block_pos may be a string like "body_1" or "hook"
        safe_pos = str(block_pos).replace(" ", "_")
        segment_name = f"sub_block_{safe_pos}"
        overlay_path = os.path.join(sub_output_dir, f"{segment_name}.mov")
        props_path = os.path.join(sub_output_dir, f"{segment_name}_props.json")

        # Write props file
        with open(props_path, "w") as f:
            json.dump(props, f, indent=2)

        print(f"  [{i+1}/{len(props_list)}] {segment_name} "
              f"({num_subs} subs, {total_frames}f, "
              f"tl:{tl_start:.1f}-{tl_end:.1f}s)", file=sys.stderr)

        # Render via Remotion
        try:
            result = subprocess.run(
                ["npx", "remotion", "render",
                 "SubtitleOverlay",
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
                timeout=180,  # 3 min per segment
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
            # The rendered clip carries animation handles either side of
            # the content; these trim them off at placement time so blocks
            # sit on their true bounds and never overlap.
            "source_in_frame": props["_source_in_frame"],
            "source_out_frame": props["_source_out_frame"],
            "total_frames": props["_source_out_frame"] - props["_source_in_frame"],
            "rendered_frames": total_frames,
        })

    print(f"\nRendered {len(segments)}/{len(props_list)} subtitle segments",
          file=sys.stderr)

    if segments:
        try:
            sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
            from tools.qa.subtitle_qa import run_subtitle_qa
            run_subtitle_qa(segments[0]["overlay_path"], project_folder)
        except Exception as e:
            error_msg = f"Subtitle QA Validation Failed: {str(e)}"
            print(f"ERROR: {error_msg}", file=sys.stderr)
            # Don't fail the step if it's just QA that failed, unless it's a critical error
            emit({
                "subtitle_overlay": {
                    "available": False,
                    "error": error_msg
                }
            })
            sys.exit(1)

    failure_rate = (len(props_list) - len(segments)) / len(props_list) if len(props_list) > 0 else 0
    if failure_rate > 0.1:
        error_msg = f"More than 10% of subtitle renders failed ({len(props_list) - len(segments)} out of {len(props_list)})."
        print(f"ERROR: {error_msg}", file=sys.stderr)
        emit({
            "subtitle_overlay": {
                "available": False,
                "error": error_msg
            }
        })
        sys.exit(1)

    emit({
        "subtitle_overlay": {
            "available": len(segments) > 0,
            "segments": segments,
            "format": "ProRes 4444",
            "has_alpha": True,
            "fps": fps,
            "total_segments": len(segments),
        }
    })


if __name__ == "__main__":
    main()
