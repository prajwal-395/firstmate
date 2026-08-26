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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools.project_layout import Area, ProjectLayout


def profile_defect(profile: dict) -> str:
    """Why this prosody profile measures nothing, or "" if it does.

    A profile is a FILE ON DISK, and the failure path used to write one
    like any other result: `{"prosody": {"method": null, "error":
    "parselmouth not installed"}}`. Counting files is therefore not the
    same as counting measurements, and the difference is the whole of
    this defect. `speech_advanced_pipeline` no longer writes these, so
    this reads the ones a previous run already left behind - and it is
    also the check that keeps any future "soft failure" record from
    passing as data.
    """
    if not isinstance(profile, dict):
        return "not a JSON object"
    prosody = profile.get("prosody")
    if not isinstance(prosody, dict):
        return "no prosody block"
    if prosody.get("error"):
        return str(prosody["error"])
    if not prosody.get("method"):
        return "no analysis method recorded"
    if not prosody.get("pitch_stats") and not prosody.get("intensity_contour_50ms"):
        return "neither pitch nor intensity was measured"
    return ""


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
    
    if isinstance(temporal_index, list):
        for idx in temporal_index:
            clip_id = idx.get("clip_id", "")
            regions = idx.get("speech_regions", [])
            if clip_id and regions:
                speech_boundaries[clip_id] = [
                    {"start": r.get("start", 0), "end": r.get("end", 0)}
                    for r in regions
                ]
    elif isinstance(temporal_index, dict):
        # Primary path: read from per-clip JSON files on disk
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

        # Fallback: extract from full_indices in the pipeline state when
        # index_dir is unavailable or produced no results
        if not speech_boundaries:
            for idx in temporal_index.get("full_indices", []):
                clip_id = idx.get("clip_id", "")
                regions = idx.get("speech_regions", [])
                if clip_id and regions:
                    speech_boundaries[clip_id] = [
                        {"start": r.get("start", 0), "end": r.get("end", 0)}
                        for r in regions
                    ]

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
    for i, item in enumerate(raw_footage_files):
        path = item["path"] if isinstance(item, dict) else item
        if os.path.exists(path):
            clip_id = item.get("clip_id", f"clip_{i + 1:03d}") if isinstance(item, dict) else f"clip_{i + 1:03d}"
            audio_files.append({"path": path, "clip_id": clip_id})

    if not audio_files:
        json.dump({
            "prosody_analysis": {
                "available": False,
                "error": "No valid audio files found"
            }
        }, sys.stdout, indent=2)
        return

    # Output directory. See library/tools/project_layout.py - a step
    # names an area and gets a path; it does not compose one.
    output_dir = str(ProjectLayout(project_folder).write_dir(Area.PROSODY))

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
    for f in glob.glob(os.path.join(output_dir, "*_prosody.json")):
        clip_id = os.path.basename(f).replace("_prosody.json", "")
        existing.add(clip_id)

    missing = [af for af in audio_files if af["clip_id"] not in existing]
    print(f"Prosody Analysis: {len(audio_files)} clips, "
          f"{len(existing)} cached, {len(missing)} to analyze",
          file=sys.stderr)

    pipeline_failures = []
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
                [sys.executable, PROSODY_PIPELINE],
                input=json.dumps(input_data),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=600,  # 10 min max
            )
            # The pipeline reports per-clip failures on stdout and exits
            # non-zero. Read them: they carry the REASON, and a generic
            # "no profiles were produced" is exactly the uninformative
            # note this defect was made of.
            try:
                pipeline_failures = (
                    json.loads(result.stdout).get("failures", []) or [])
            except (json.JSONDecodeError, AttributeError):
                pipeline_failures = []
            if result.returncode != 0 and not pipeline_failures:
                pipeline_failures = [{
                    "clip_id": None,
                    "error": (f"prosody pipeline exited {result.returncode}: "
                              f"{result.stderr.strip()[-300:]}"),
                }]
            for failure in pipeline_failures:
                print(f"Prosody failed on {failure.get('clip_id')}: "
                      f"{failure.get('error')}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            pipeline_failures = [{"clip_id": None,
                                  "error": "prosody analysis timed out"}]
            print("Prosody analysis timed out", file=sys.stderr)
        except Exception as e:
            pipeline_failures = [{"clip_id": None,
                                  "error": f"prosody analysis error: {e}"}]
            print(f"Prosody analysis error: {e}", file=sys.stderr)

    # Collect all prosody profiles
    profiles = {}
    hollow = {}
    for f in sorted(glob.glob(os.path.join(output_dir, "*_prosody.json"))):
        clip_id = os.path.basename(f).replace("_prosody.json", "")
        try:
            with open(f) as fp:
                profile = json.load(fp)
        except (json.JSONDecodeError, IOError):
            continue
        problem = profile_defect(profile)
        if problem:
            hollow[clip_id] = problem
        else:
            profiles[clip_id] = profile

    print(f"Collected {len(profiles)} prosody profiles", file=sys.stderr)
    if hollow:
        print(f"Rejected {len(hollow)} profiles that measured nothing",
              file=sys.stderr)

    # `available` describes whether there is prosody data to use, not
    # whether the step reached its last line, and NOT whether a file
    # landed on disk. Project 001 collected seventeen files that each
    # said `{"method": null, "error": "parselmouth not installed"}`,
    # counted them as seventeen profiles, reported available=true in 0.1
    # seconds - and 4.2 KB of those identical error records were
    # serialised into the creative-direction prompt as if they were
    # measurements. `available: false` here is read by
    # run_pipeline.check_output_is_real as a failed step, which is the
    # point: a step that cannot do its job must not report success.
    for failure in pipeline_failures:
        hollow.setdefault(str(failure.get("clip_id")),
                          str(failure.get("error")))

    error = None
    if not profiles:
        if hollow:
            error = (
                f"Prosody measured nothing on any of {len(hollow)} clip(s). "
                + "; ".join(f"{cid}: {why}"
                            for cid, why in sorted(hollow.items())[:3])
                + ("; ..." if len(hollow) > 3 else "")
            )
        else:
            error = (
                f"Prosody pipeline produced no *_prosody.json profiles in "
                f"{output_dir}"
            )
    elif hollow:
        error = (
            f"Prosody measured nothing on {len(hollow)} of "
            f"{len(hollow) + len(profiles)} clip(s): "
            + ", ".join(sorted(hollow))
        )

    if error:
        print(f"ERROR: {error}", file=sys.stderr)

    json.dump({
        "prosody_analysis": {
            # A partial pass is a failure too: the consumers read a
            # per-clip mapping and a missing clip reads as silence.
            "available": bool(profiles) and not hollow,
            "profiles": profiles,
            "total_clips": len(profiles),
            "unmeasured_clips": sorted(hollow),
            "error": error,
        }
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
