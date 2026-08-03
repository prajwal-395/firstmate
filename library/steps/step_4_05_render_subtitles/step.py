#!/usr/bin/env python3
"""
Step 4.05: Render Subtitles (Remotion)

Takes the subtitle plan from step 4.01 and renders it to a ProRes 4444
video overlay with alpha channel using Remotion.

Workflow:
  1. Generate Remotion input props from the subtitle plan
     (via generate_subtitle_props.py)
  2. Write props to remotion-subtitles/public/subtitleProps.json
  3. Run `npx remotion render` to produce the overlay
  4. Return the path to the rendered overlay for import into Resolve

The overlay is a transparent video (ProRes 4444 with alpha) that gets
composited on top of the A-roll in the final Resolve timeline.

Classification: Deterministic / Direct Action
Idempotent: Yes (same subtitle plan → same rendered overlay)
"""
import json
import os
import subprocess
import sys


def main():
    data = json.loads(sys.stdin.read())
    subtitle_plan = data.get("subtitle_plan", {})
    audio_spine = data.get("audio_spine", {})
    project_folder = data.get("project_folder", "")

    # Calculate total duration from audio spine
    total_duration = 0
    blocks = audio_spine.get("structure",
                audio_spine.get("blocks",
                    audio_spine.get("timed_spine", [])))
    if blocks:
        last_block = blocks[-1]
        total_duration = last_block.get("timeline_end",
                            last_block.get("end_time",
                                last_block.get("end", 0)))

    if total_duration == 0:
        print("WARNING: Could not determine total duration from audio spine",
              file=sys.stderr)

    # Find Remotion project and subtitle props tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    REMOTION_DIR = os.path.join(PILOT_ROOT, "remotion-subtitles")
    PROPS_GENERATOR = os.path.join(
        PILOT_ROOT, "library", "tools", "execution", "generate_subtitle_props.py")

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

    # Output path for the rendered overlay
    output_dir = os.path.join(project_folder, "pipeline_output")
    os.makedirs(output_dir, exist_ok=True)
    overlay_path = os.path.join(output_dir, "subtitle_overlay.mov")

    # Step 1: Generate Remotion input props
    props_path = os.path.join(REMOTION_DIR, "public", "subtitleProps.json")

    if os.path.exists(PROPS_GENERATOR):
        print("Generating Remotion subtitle props...", file=sys.stderr)
        try:
            result = subprocess.run(
                ["python3", PROPS_GENERATOR,
                 "--subtitle-plan", json.dumps(subtitle_plan),
                 "--output", props_path,
                 "--total-duration", str(total_duration)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                print(f"Props generator warning: {result.stderr[:200]}",
                      file=sys.stderr)
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"Props generator error: {e}", file=sys.stderr)
    else:
        # Fallback: write the subtitle plan directly as props
        print("generate_subtitle_props.py not found, writing plan directly",
              file=sys.stderr)
        with open(props_path, "w") as f:
            json.dump({
                "subtitles": subtitle_plan.get("subtitle_entries",
                                subtitle_plan.get("groups", [])),
                "totalDurationInFrames": int(total_duration * 30),
                "fps": 30,
            }, f, indent=2)

    # Step 2: Run Remotion render
    print(f"Rendering subtitle overlay via Remotion...", file=sys.stderr)
    fps = 30
    total_frames = int(total_duration * fps)

    try:
        result = subprocess.run(
            ["npx", "remotion", "render",
             "SubtitleOverlay",
             overlay_path,
             "--props", props_path,
             "--codec", "prores",
             "--prores-profile", "4444",
             "--image-format", "png",  # Required for alpha channel
             ],
            cwd=REMOTION_DIR,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
        )

        if result.returncode != 0:
            print(f"Remotion render failed: {result.stderr[:500]}",
                  file=sys.stderr)
            json.dump({
                "subtitle_overlay": {
                    "available": False,
                    "error": result.stderr[:200],
                    "props_path": props_path
                }
            }, sys.stdout, indent=2)
            sys.exit(1)

        print(f"Subtitle overlay rendered: {overlay_path}", file=sys.stderr)

    except subprocess.TimeoutExpired:
        print("Remotion render timed out after 10 minutes", file=sys.stderr)
        json.dump({
            "subtitle_overlay": {
                "available": False,
                "error": "Render timed out"
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    json.dump({
        "subtitle_overlay": {
            "available": True,
            "overlay_path": overlay_path,
            "props_path": props_path,
            "format": "ProRes 4444",
            "has_alpha": True,
            "fps": fps,
            "total_frames": total_frames,
        }
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
