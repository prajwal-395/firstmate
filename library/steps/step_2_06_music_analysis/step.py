#!/usr/bin/env python3
"""
Step 2.06: Music Analysis

Per-project step that analyzes the selected music track to produce:
  - Beat grid (beat timestamps, bar boundaries)
  - BPM (tempo)
  - Key detection
  - Song structure (intro, verse, chorus, etc.)

Delegates to library/tools/analysis/music_pipeline.py for the actual
analysis, wrapping it in the step interface (JSON stdin → JSON stdout).

Consumed by step 4.04 (plan_sfx), which snaps SFX to bar boundaries
(post_bridge.py:344).

The DAG also wires this output into 2.05 (mesh_spine) and 4.02
(plan_transitions), and neither reads it. plan_transitions synthesises its
own uniform grid from BPM instead (bridge.py:24-33), so its cuts snap to a
grid starting at t=0 rather than to the track's actual beats. Do not add
"consumed by" lines here for edges that exist only in the DAG; see
docs/PIPELINE_PLAN.md.

Classification: Deterministic / Data Transformation
Idempotent: Yes (same track → same analysis)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools.project_layout import Area, ProjectLayout


def main():
    data = json.loads(sys.stdin.read())
    music_selection = data.get("music_selection", {})
    project_folder = data.get("project_folder", "")

    # Extract track path from music selection
    track_path = music_selection.get("track_path", "")
    if not track_path:
        for key in ("audio_path", "file_path", "path"):
            track_path = music_selection.get(key, "")
            if track_path:
                break
    
    if not track_path and "tracks" in music_selection and isinstance(music_selection["tracks"], list) and len(music_selection["tracks"]) > 0:
        first_track = music_selection["tracks"][0]
        track_path = first_track.get("audio_path", first_track.get("track_path", ""))

    if not track_path or not os.path.exists(track_path):
        print(f"ERROR: Music track not found: {track_path}", file=sys.stderr)
        # Return empty analysis rather than failing — music analysis
        # is optional (SFX placement works without beat grid)
        json.dump({
            "music_analysis": {
                "available": False,
                "error": f"Track not found: {track_path}"
            }
        }, sys.stdout, indent=2)
        return

    # Output directory for analysis results.
    # See library/tools/project_layout.py.
    output_dir = str(ProjectLayout(project_folder).write_dir(
        Area.MUSIC_ANALYSIS, step="music_analysis"))

    # Path to the music pipeline tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    MUSIC_PIPELINE = os.path.join(
        PILOT_ROOT, "library", "tools", "analysis", "music_pipeline.py")

    if not os.path.exists(MUSIC_PIPELINE):
        print(f"ERROR: Music pipeline not found at {MUSIC_PIPELINE}",
              file=sys.stderr)
        json.dump({
            "music_analysis": {
                "available": False,
                "error": f"music_pipeline.py not found"
            }
        }, sys.stdout, indent=2)
        return

    # Check if analysis already exists for this track
    analysis_path = os.path.join(output_dir, "music_analysis.json")
    if os.path.exists(analysis_path):
        try:
            with open(analysis_path) as f:
                existing = json.load(f)
            # Verify it's for the same track
            if existing.get("source_file") == track_path:
                print(f"Music analysis already exists for {os.path.basename(track_path)}, "
                      f"reusing cached result", file=sys.stderr)
                existing["available"] = True
                json.dump({"music_analysis": existing}, sys.stdout, indent=2)
                return
        except (json.JSONDecodeError, IOError):
            pass  # Re-run analysis

    print(f"Analyzing music track: {os.path.basename(track_path)}",
          file=sys.stderr)

    try:
        result = subprocess.run(
            [sys.executable, MUSIC_PIPELINE, track_path,
             "--output-dir", output_dir],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=300,  # 5 min max
        )

        if result.returncode != 0:
            # Report the exception, not the first 200 characters of a
            # traceback. Truncating from the top yielded
            # "Traceback (most recent call last):\n  File ..." and hid the
            # actual cause (a missing dependency) for an entire run.
            stderr = result.stderr.strip()
            cause = stderr.splitlines()[-1] if stderr else "no stderr"
            print(f"Music pipeline failed: {cause}", file=sys.stderr)
            print(stderr[-2000:], file=sys.stderr)
            json.dump({
                "music_analysis": {
                    "available": False,
                    "error": cause,
                    "traceback_tail": stderr[-2000:],
                }
            }, sys.stdout, indent=2)
            return

        # Load the generated analysis
        if os.path.exists(analysis_path):
            with open(analysis_path) as f:
                analysis = json.load(f)
            analysis["available"] = True
            # BPM lives under `tempo`, not at the top level - this line
            # read analysis['bpm'] and printed "BPM=?" on every run.
            _tempo = analysis.get("tempo") or {}
            _key = analysis.get("key") or {}
            print(f"Music analysis complete: "
                  f"BPM={_tempo.get('bpm', '?')}, "
                  f"beats={len(_tempo.get('beats') or [])}, "
                  f"downbeats={len(_tempo.get('downbeats') or [])}, "
                  f"Key={_key.get('key', '?')}",
                  file=sys.stderr)
            json.dump({"music_analysis": analysis}, sys.stdout, indent=2)
        else:
            # Pipeline ran but didn't produce expected output
            json.dump({
                "music_analysis": {
                    "available": False,
                    "error": "Pipeline completed but music_analysis.json not found"
                }
            }, sys.stdout, indent=2)

    except subprocess.TimeoutExpired:
        print("Music analysis timed out after 5 minutes", file=sys.stderr)
        json.dump({
            "music_analysis": {
                "available": False,
                "error": "Analysis timed out"
            }
        }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
