#!/usr/bin/env python3
"""
Render verification frames from the showcase timeline.
Captures a mid-point frame from each clip to confirm effects are visible.
"""

import sys
import os
import time
import subprocess

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

import DaVinciResolveScript as dvr


def main():
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        print("✗ Could not connect to Resolve")
        return

    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    print(f"Timeline: {timeline.GetName()}")

    v1_items = timeline.GetItemListInTrack("video", 1) or []
    print(f"V1 clips: {len(v1_items)}")

    out_dir = "/tmp/fusion_showcase_frames"
    os.makedirs(out_dir, exist_ok=True)

    # Switch to Deliver page
    resolve.OpenPage("deliver")
    time.sleep(1)

    for i, clip in enumerate(v1_items):
        clip_name = clip.GetName()
        clip_start = clip.GetStart()
        clip_dur = clip.GetDuration()
        mid_frame = clip_start + clip_dur // 2

        print(f"\n  [{i+1}] {clip_name}: rendering frame {mid_frame}")

        # Set render settings for a single frame
        project.SetRenderSettings({
            "TargetDir": out_dir,
            "CustomName": f"showcase_clip_{i+1}",
            "FormatWidth": 1080,
            "FormatHeight": 1920,
            "MarkIn": mid_frame,
            "MarkOut": mid_frame,
        })

        project.AddRenderJob()

    # Start rendering all jobs
    project.StartRendering()

    # Wait for completion
    print("\n  Rendering...")
    for _ in range(30):
        if not project.IsRenderingInProgress():
            break
        time.sleep(1)

    # Convert to PNG for viewing
    print(f"\n  Converting to PNG...")
    for i in range(len(v1_items)):
        mov = os.path.join(out_dir, f"showcase_clip_{i+1}.mov")
        png = os.path.join(out_dir, f"showcase_clip_{i+1}.png")
        if os.path.exists(mov):
            subprocess.run([
                "ffmpeg", "-y", "-i", mov, "-frames:v", "1", png
            ], capture_output=True)
            size = os.path.getsize(png) if os.path.exists(png) else 0
            status = "✓ real content" if size > 2000 else "✗ black/empty"
            print(f"    [{i+1}] {size:,} bytes — {status}")
        else:
            # Try .mp4 or other extensions
            found = [f for f in os.listdir(out_dir)
                     if f.startswith(f"showcase_clip_{i+1}")]
            if found:
                src = os.path.join(out_dir, found[0])
                subprocess.run([
                    "ffmpeg", "-y", "-i", src, "-frames:v", "1", png
                ], capture_output=True)
                size = os.path.getsize(png) if os.path.exists(png) else 0
                status = "✓ real content" if size > 2000 else "✗ black/empty"
                print(f"    [{i+1}] {found[0]}: {size:,} bytes — {status}")
            else:
                print(f"    [{i+1}] no output found")

    # Clean up render jobs
    project.DeleteAllRenderJobs()

    # Switch back to Edit page
    resolve.OpenPage("edit")

    print(f"\n  Frames saved to: {out_dir}/")
    print("  Done!")


if __name__ == "__main__":
    main()
