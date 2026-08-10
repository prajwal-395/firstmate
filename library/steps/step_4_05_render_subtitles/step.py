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

from generate_remotion_props import generate_subtitle_props_per_block


def main():
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
        json.dump({
            "subtitle_overlay": {
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
    sub_output_dir = os.path.join(output_dir, "subtitle_segments")
    os.makedirs(sub_output_dir, exist_ok=True)

    # Generate per-block props
    fps = data.get("project_fps", 30)
    width = data.get("project_resolution", [1080, 1920])[0]
    height = data.get("project_resolution", [1080, 1920])[1]
    props_list = generate_subtitle_props_per_block(subtitle_plan, fps=fps, width=width, height=height, audio_spine=audio_spine)

    if not props_list:
        print("WARNING: No subtitle blocks to render", file=sys.stderr)
        json.dump({
            "subtitle_overlay": {
                "available": False,
                "segments": [],
                "reason": "No subtitle entries found in subtitle plan"
            }
        }, sys.stdout, indent=2)
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
                 ],
                cwd=REMOTION_DIR,
                capture_output=True,
                text=True,
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
            "total_frames": total_frames,
        })

    print(f"\nRendered {len(segments)}/{len(props_list)} subtitle segments",
          file=sys.stderr)

    failure_rate = (len(props_list) - len(segments)) / len(props_list) if len(props_list) > 0 else 0
    if failure_rate > 0.1:
        error_msg = f"More than 10% of subtitle renders failed ({len(props_list) - len(segments)} out of {len(props_list)})."
        print(f"ERROR: {error_msg}", file=sys.stderr)
        json.dump({
            "subtitle_overlay": {
                "available": False,
                "error": error_msg
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    json.dump({
        "subtitle_overlay": {
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
