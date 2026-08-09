#!/usr/bin/env python3
"""
Step 1.05: Prosody Analysis

Supplements WhisperX transcription (step 1.04) with prosodic features
that the LLM can use for creative decisions:

  - Pitch (F0) contour → emphasis, questions, trailing off
  - Speaking rate → engagement/energy shifts
  - Voice quality (jitter, shimmer, HNR) → emotional register
  - Intensity contour → loudness dynamics

Delegates to library/tools/analysis/speech_advanced_pipeline.py.

Classification: Deterministic / Data Transformation
Idempotent: Yes (same audio → same prosody)
"""
import json
import os
import subprocess
import sys
import glob


def main():
    data = json.loads(sys.stdin.read())
    raw_footage_files = data.get("raw_footage_files", [])
    project_folder = data.get("project_folder", "")

    # C8 fix: Extract temporal boundaries from the temporal_index input.
    # The manifest declares temporal_index as a required input - use it to
    # focus prosody analysis on voiced segments only (avoids wasting compute
    # on silence and improves pitch/rate accuracy).
    temporal_index = data.get("temporal_index", data)
    speech_boundaries = {}
    if isinstance(temporal_index, dict):
        index_dir = temporal_index.get("index_dir", "") or data.get("index_dir", "")
        if index_dir and os.path.isdir(index_dir):
            for ti_file in glob.glob(os.path.join(index_dir, "*.json")):
                clip_id = os.path.splitext(os.path.basename(ti_file))[0]
                try:
                    with open(ti_file) as f:
                        ti_data = json.load(f)
                    regions = ti_data.get("speech_regions", [])
                    if regions:
                        speech_boundaries[clip_id] = [
                            {"start": r.get("start", 0), "end": r.get("end", 0)}
                            for r in regions
                        ]
                except (json.JSONDecodeError, IOError):
                    pass

    if not raw_footage_files:
        json.dump({
            "prosody_analysis": {
                "available": False,
                "error": "No raw footage files provided"
            }
        }, sys.stdout, indent=2)
        return

    # Resolve audio files from footage
    audio_files = []
    for item in raw_footage_files:
        path = item["path"] if isinstance(item, dict) else item
        if os.path.exists(path):
            clip_id = os.path.splitext(os.path.basename(path))[0]
            audio_files.append({"path": path, "clip_id": clip_id})

    if not audio_files:
        json.dump({
            "prosody_analysis": {
                "available": False,
                "error": "No valid audio files found"
            }
        }, sys.stdout, indent=2)
        return

    # Output directory
    output_dir = os.path.join(project_folder, "pipeline_output", "prosody")
    os.makedirs(output_dir, exist_ok=True)

    # Path to the prosody tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    PROSODY_PIPELINE = os.path.join(
        PILOT_ROOT, "library", "tools", "analysis",
        "speech_advanced_pipeline.py")

    if not os.path.exists(PROSODY_PIPELINE):
        print(f"ERROR: Prosody pipeline not found at {PROSODY_PIPELINE}",
              file=sys.stderr)
        json.dump({
            "prosody_analysis": {
                "available": False,
                "error": "speech_advanced_pipeline.py not found"
            }
        }, sys.stdout, indent=2)
        return

    # Check which clips already have prosody data
    existing = set()
    for f in glob.glob(os.path.join(output_dir, "prosody_*.json")):
        clip_id = os.path.basename(f).replace("prosody_", "").replace(".json", "")
        existing.add(clip_id)

    missing = [af for af in audio_files if af["clip_id"] not in existing]
    print(f"Prosody Analysis: {len(audio_files)} clips, "
          f"{len(existing)} cached, {len(missing)} to analyze",
          file=sys.stderr)

    if missing:
        # Write input JSON for the pipeline
        input_data = {
            "audio_files": missing,
            "output_dir": output_dir,
            # C8 fix: Pass speech region boundaries from temporal_index so the
            # prosody pipeline can focus on voiced segments.
            "speech_boundaries": speech_boundaries,
        }

        try:
            result = subprocess.run(
                ["python3", PROSODY_PIPELINE],
                input=json.dumps(input_data),
                capture_output=True,
                text=True,
                timeout=600,  # 10 min max
            )
            if result.returncode != 0:
                print(f"Prosody pipeline warning: {result.stderr[:300]}",
                      file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("Prosody analysis timed out", file=sys.stderr)
        except Exception as e:
            print(f"Prosody analysis error: {e}", file=sys.stderr)

    # Collect all prosody profiles
    profiles = {}
    for f in sorted(glob.glob(os.path.join(output_dir, "prosody_*.json"))):
        clip_id = os.path.basename(f).replace("prosody_", "").replace(".json", "")
        try:
            with open(f) as fp:
                profiles[clip_id] = json.load(fp)
        except (json.JSONDecodeError, IOError):
            pass

    print(f"Collected {len(profiles)} prosody profiles", file=sys.stderr)

    json.dump({
        "prosody_analysis": {
            "available": True,
            "profiles": profiles,
            "total_clips": len(profiles)
        }
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
