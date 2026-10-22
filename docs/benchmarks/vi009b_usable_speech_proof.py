#!/usr/bin/env python3
"""VI-009b proof: does the SELECTION carry a usable-speech reading?

PR 3140 put the technical leg in the candidate table, but its
``usable_speech`` indicator was absent on every candidate in a run that
never measured audio, because the audio lane (M11) was measured out of
band. This script measures that, on a Ren-owned scratch copy, with the
captain's podcast media read-only.

It builds the candidate table through the REAL step 3.02 bridge and
counts the candidates whose ``tech_leg`` cell carries a usable-speech
reading. The bridge to run is an argument, so the same scratch project
and the same footage can be read before and after the change:

    python3 docs/benchmarks/vi009b_usable_speech_proof.py --bridge <path>

The measurements land in the SCRATCH project's private source memory,
never in the machine store, and the captain's media is never written to.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import source_memory

MEDIA_ROOT = os.path.expanduser(
    "~/Documents/work_stuff/Lucie consulting/Social Media/podcast media")
SCRATCH_PROJECT = os.path.expanduser(
    "~/Documents/content_stuff/video_projects/fm-vi009b-scratch")

#: The podcast's own cameras. Every one of them recorded the same
#: conversation, so the table offers the planner several candidates for
#: one line - which is the comparison the usable-speech reading exists
#: for.
CAMERAS = ("LC4930.MXF", "LC4931.MXF", "LC4932.MXF", "LCATL0011.MXF")


def _v3_document(camera, path, duration):
    """A v3-shaped semantic document, so the candidate table has a row.

    The document is the shape the vision pass writes; the proof states
    it rather than running a vision pass over the captain's footage.
    """
    return {
        "clip_id": camera,
        "file_path": path,
        "duration_s": duration,
        "fps": 23.976,
        "resolution": [3840, 2160],
        "scene": [
            {"start": 0.0, "end": duration,
             "location": "A room with two people talking",
             "type": "indoor", "lighting": "Practical",
             "notable_features": ["A table", "Two chairs"]},
        ],
        "camera": [{"start": 0, "end": duration, "mode": "handheld",
                    "framing": "medium", "stability": "stable",
                    "movement": "stationary"}],
        "actions": [
            {"window": [0.0, min(duration, 10.0)],
             "actions": [
                 {"start": 0.0, "end": min(duration, 10.0),
                  "action": "The two people talk across the table.",
                  "speech_cue": None,
                  "body_language": "Neutral, attentive."},
             ]},
        ],
        "objects": [
            {"label": "a person in a dark shirt", "role": "primary_subject",
             "category": "person",
             "appearances": [[0.0, min(duration, 10.0)]],
             "readable_text": None},
        ],
        "assessment": {
            "content_type": "person_talking_to_camera",
            "camera_stability": "stable",
            "usable_ranges": [[0.0, duration]],
            "usable_ranges_method": "deterministic_v1",
            "primary_subject_visible": [[0.0, min(duration, 10.0)]],
        },
    }


def _duration(path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", check=False)
    try:
        return float(json.loads(probe.stdout or "{}")
                     ["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def run_bridge(bridge, catalog, documents):
    """The candidate table the planner reads, built by the real bridge."""
    payload = {
        "clip_catalog": catalog,
        "a_roll_assignments": [
            {"segment_id": "block_1", "spine_block_position": 1,
             "video_segments": [{"clip_id": catalog[0]["clip_id"]}]},
        ],
        "semantic_analysis_documents": documents,
        "timed_spine": {"structure": [
            {"position": 1, "block_type": "transition_slot",
             "timeline_start": 0.0, "timeline_end": 3.03}]},
        "project_folder": SCRATCH_PROJECT,
    }
    env = dict(os.environ)
    env["REN_EVENT_LOG"] = str(
        Path(SCRATCH_PROJECT) / "pipeline_output" / "logs" / "event_log.jsonl")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    proc = subprocess.run([sys.executable, str(bridge)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", env=env, check=False)
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        return None, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def count_readings(table):
    """The candidates that carry a usable-speech reading, and of how many."""
    lines = [line for line in table.splitlines() if line.strip()]
    header = lines[0]
    fields = header[header.index("{") + 1:header.index("}")].split(",")
    rows = [dict(zip(fields, line.split("\t"))) for line in lines[1:]]
    with_reading = [row for row in rows
                    if "usable_speech=" in row.get("tech_leg", "")
                    and "usable_speech=-" not in row.get("tech_leg", "")]
    return len(with_reading), len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default=None,
                        help="the step 3.02 bridge to run; the repository's "
                             "own bridge by default")
    parser.add_argument("--label", default="run")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    bridge = args.bridge or str(
        repo / "library" / "steps" / "step_3_02_select_broll" / "bridge.py")

    root = source_memory.project_memory_root(SCRATCH_PROJECT)
    print(f"Scratch project: {SCRATCH_PROJECT}")
    print(f"Measurements land in: {root}")
    print(f"Bridge: {bridge}  ({args.label})\n")

    catalog = []
    documents = []
    for camera in CAMERAS:
        path = os.path.join(MEDIA_ROOT, camera)
        duration = _duration(path)
        catalog.append({"clip_id": camera, "path": path,
                        "source_file": path,
                        "duration_seconds": duration})
        documents.append(_v3_document(camera, path, duration))

    result, stderr = run_bridge(bridge, catalog, documents)
    if not result:
        return 1
    for line in stderr.splitlines():
        if "Usable-speech readings" in line or "not reached" in line:
            print(line)

    table = result["broll_candidates_toon"]
    with_reading, offered = count_readings(table)
    print("\n--- The candidate table the planner reads")
    for line in table.splitlines():
        print(line)
    print()
    print(json.dumps({
        "label": args.label,
        "candidates_offered": offered,
        "candidates_with_a_usable_speech_reading": with_reading,
        "candidates_without": offered - with_reading,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
