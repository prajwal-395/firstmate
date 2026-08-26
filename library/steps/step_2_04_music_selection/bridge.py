#!/usr/bin/env python3
"""
Step 2.4: Music Selection - Pre-bridge

Catalogues the music that is actually available and hands the whole list
to the LLM. It picks NOTHING.

That is the change. This bridge used to select by sorting: it globbed
`project_folder/music/`, took `sorted(...)[0]`, and returned it as the
step's `music_selection`. On project 001 the alphabetically-first file is
a 3914-second "Inspirational Motivational Music Video", scoring a piece
whose creative direction says it must not be scored as triumphant - and
because the bridge supplied the step's only declared output, the LLM was
handed an empty output schema and was never asked. `PIPELINE_MUSIC_LIBRARY`
was never opened at all.

Captain's ruling 2026-08-20: fix the schema, consult the library, and keep
outside-the-library selection allowed. So this bridge lists every local
candidate from both the shared library and the project's own music folder,
measures each one, and states the target duration the choice has to serve.
The verdict happens in post_bridge.py, through
`library/tools/music_selection_contract.py`.

Input:  { "creative_direction": {...}, "project_folder": "..." }
Output: { "music_candidates": {
            "target_duration_seconds": 60.0,
            "max_track_duration_seconds": 600.0,
            "searched": [{ "source", "directory", "exists", "count" }],
            "candidates": [{ "title", "audio_path", "duration_seconds",
                             "source", "duration_ok", "duration_note" }],
        } }
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure this step's directory is on sys.path for local tool imports
STEP_DIR = Path(__file__).resolve().parent
if str(STEP_DIR) not in sys.path:
    sys.path.insert(0, str(STEP_DIR))

REPO_ROOT = STEP_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.music_selection_contract import (  # noqa: E402
    AUDIO_EXTENSIONS,
    DURATION_SLACK_SECONDS,
    catalogue_sources,
    max_track_duration_seconds,
)
from library.tools.project_layout import ProjectLayout  # noqa: E402

DEFAULT_TARGET_DURATION_SECONDS = 60.0


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                audio_path,
            ],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0.0


def _target_duration(inputs: dict) -> float:
    """How long the finished piece is meant to run.

    Read off the project's own `project.yaml`, the same way
    `resolve_delivery_format` reads the delivery format - a value threaded
    through the DAG is a value that gets renamed and defaulted away, and
    2.04 runs before the spine exists so there is no measured duration to
    read yet.
    """
    declared = inputs.get("target_duration_seconds")
    if isinstance(declared, (int, float)) and declared > 0:
        return float(declared)

    project_folder = inputs.get("project_folder", "")
    project_yaml = (
        str(ProjectLayout(project_folder).project_config_path)
        if project_folder else ""
    )
    if os.path.exists(project_yaml):
        try:
            import yaml
            with open(project_yaml, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            value = cfg.get("target_duration_seconds")
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        except Exception as exc:
            print(
                f"  WARNING: could not read target_duration_seconds from "
                f"{project_yaml}: {exc}",
                file=sys.stderr,
            )

    return DEFAULT_TARGET_DURATION_SECONDS


def catalogue_music(project_folder: str, target_duration: float) -> dict:
    """Every local track, from every source, measured and labelled."""
    ceiling = max_track_duration_seconds(target_duration)
    searched = []
    candidates = []

    # One source label can cover more than one directory: `project` is
    # both the captain's read-only music/ and the downloads area a
    # fetched track lands in. See library/tools/music_selection_contract.py.
    for source, directory in (
        (label, d)
        for label, dirs in catalogue_sources(project_folder).items()
        for d in dirs
    ):
        exists = bool(directory) and os.path.isdir(directory)
        found = 0
        if exists:
            for entry in sorted(os.listdir(directory)):
                if os.path.splitext(entry)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                full_path = os.path.join(directory, entry)
                if not os.path.isfile(full_path):
                    continue
                if os.path.getsize(full_path) <= 0:
                    continue

                duration = _get_audio_duration(full_path)
                if duration > ceiling:
                    note = (
                        f"TOO LONG: {duration / 60:.1f} min for a "
                        f"{target_duration:.0f}s edit - this is a "
                        f"compilation, not a track"
                    )
                    ok = False
                elif duration + DURATION_SLACK_SECONDS < target_duration:
                    note = (
                        f"TOO SHORT: {duration:.1f}s cannot cover a "
                        f"{target_duration:.0f}s edit"
                    )
                    ok = False
                else:
                    note = ""
                    ok = True

                candidates.append({
                    "title": os.path.splitext(entry)[0],
                    "audio_path": full_path,
                    "duration_seconds": round(duration, 3),
                    "source": source,
                    "duration_ok": ok,
                    "duration_note": note,
                })
                found += 1

        searched.append({
            "source": source,
            "directory": directory,
            "exists": exists,
            "count": found,
        })
        print(
            f"  {source}: {found} track(s) in {directory}"
            f"{'' if exists else ' (directory does not exist)'}",
            file=sys.stderr,
        )

    return {
        "target_duration_seconds": round(target_duration, 3),
        "max_track_duration_seconds": round(ceiling, 3),
        "searched": searched,
        "candidates": candidates,
    }


def run(inputs: dict) -> dict:
    project_folder = inputs.get("project_folder", "") or "./"
    target_duration = _target_duration(inputs)
    catalogue = catalogue_music(project_folder, target_duration)

    usable = [c for c in catalogue["candidates"] if c["duration_ok"]]
    print(
        f"  {len(catalogue['candidates'])} local track(s) catalogued, "
        f"{len(usable)} within duration sanity for a "
        f"{target_duration:.0f}s edit. Selecting from them - or from "
        f"outside - is the LLM's call.",
        file=sys.stderr,
    )

    return {"music_candidates": catalogue}


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    result = run(input_data)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
