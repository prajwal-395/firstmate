#!/usr/bin/env python3
"""
Step 4.06: Render Motion Graphics (Remotion)

Takes the enhancement spec from step 4.03 and creative direction, then
renders per-spine-block motion graphics overlays to ProRes 4444 videos
with alpha channel using Remotion.

Each spine block gets its own rendered overlay clip, which the Resolve
builder places at the correct timeline position on V4.

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
    }
}
"""
import json
import os
import subprocess
import sys

from generate_motion_props import generate_motion_props


def main():
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

    # Prep Remotion: stage compositions from shared assets, link brand assets, generate Root.tsx
    try:
        sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
        from tools.remotion_brand_linker import prep_remotion
        prep_result = prep_remotion(project_folder=project_folder)
        comp_info = prep_result.get("compositions", {})
        if comp_info.get("staged"):
            print(f"  Staged {comp_info['count']} compositions from {comp_info['source']}",
                  file=sys.stderr)
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
    width = data.get("project_resolution", [1080, 1920])[0]
    height = data.get("project_resolution", [1080, 1920])[1]
    props_list = generate_motion_props(
        enhancement_spec, creative_direction, audio_spine, fps=fps, width=width, height=height
    )

    if not props_list:
        print("WARNING: No motion graphics blocks to render", file=sys.stderr)
        json.dump({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "reason": "No motion graphics blocks found in audio spine"
            }
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

        segment_name = f"mg_block_{block_pos:02d}"
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
                 ],
                cwd=REMOTION_DIR,
                capture_output=True,
                text=True,
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

    json.dump({
        "motion_graphics_overlay": {
            "available": len(segments) > 0,
            "segments": segments,
            "format": "ProRes 4444",
            "has_alpha": True,
            "fps": fps,
            "total_segments": len(segments),
        }
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
