#!/usr/bin/env python3
"""
Step 1.1: Scan Project Folder

Scans the designated project folder and enumerates all video files,
including files in subdirectories. Produces a structured inventory of
all raw footage available for this project.

Classification: Deterministic / Information Retrieval
Archetype: Information Retrieval
Idempotent: Yes

Input:  { "project_folder": "<absolute path>" }
Output: {
    "raw_footage_files": [{ path, filename, extension, size_bytes }, ...],
    "total_files": integer,
    "skipped_files": [{ path, reason }, ...]
}
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools.footage_identity import (
    SUPPORTED_VIDEO_EXTENSIONS,
    enumerate_footage,
)
from library.tools.project_layout import ProjectLayout


def scan_project_folder(project_folder: str) -> dict:
    """
    Recursively scan project_folder for video files.
    Returns a dict with 'raw_footage_files' list.
    """
    # --- Precondition: folder exists and is accessible ---
    if not os.path.exists(project_folder):
        raise FileNotFoundError(
            f"Project folder does not exist: {project_folder}"
        )
    if not os.path.isdir(project_folder):
        raise NotADirectoryError(
            f"Path is not a directory: {project_folder}"
        )
    if not os.access(project_folder, os.R_OK):
        raise PermissionError(
            f"Cannot read project folder: {project_folder}"
        )

    # Enumeration and clip_id assignment live in
    # library/tools/footage_identity.py, because the runner's
    # source-identity check has to number clips exactly the way this scan
    # does or it cannot tell whether clip_007's cached analysis is still
    # about the same file.
    raw_footage_files, skipped_files = enumerate_footage(project_folder)

    for entry in skipped_files:
        print(f"WARNING: Skipping file ({entry['reason']}): {entry['path']}",
              file=sys.stderr)

    # --- Verification: at least one video file found ---
    if not raw_footage_files:
        raise ValueError(
            f"No video files found in {project_folder}. "
            f"Supported extensions: {sorted(SUPPORTED_VIDEO_EXTENSIONS)}"
        )

    # --- Verification: every entry has a valid, accessible file path ---
    for entry in raw_footage_files:
        assert os.path.isfile(entry["path"]), \
            f"Invalid file path: {entry['path']}"

    # The first step of every run is where the folder gets its shape.
    # `ensure` creates the output side of the layout and refreshes
    # README-LAYOUT.md, so a project the captain opens in six months
    # explains itself without anyone reading code. Input directories are
    # deliberately not created. See library/tools/project_layout.py.
    layout = ProjectLayout(project_folder).ensure()

    project_yaml_path = str(layout.project_config_path)
    project_config = {
        "target_duration_seconds": 60,
        "style_preset": "shortform_vertical",
        "subtitle_style": "word_by_word"
    }
    if os.path.exists(project_yaml_path):
        import yaml
        try:
            with open(project_yaml_path, "r") as f:
                data = yaml.safe_load(f) or {}
            project_config["target_duration_seconds"] = data.get("target_duration_seconds", 60)
            project_config["style_preset"] = data.get("style_preset", "shortform_vertical")
            project_config["subtitle_style"] = data.get("subtitle_style", "word_by_word")
        except Exception as e:
            print(f"WARNING: Failed to read project.yaml: {e}", file=sys.stderr)

    return {
        "raw_footage_files": raw_footage_files,
        "total_files": len(raw_footage_files),
        "skipped_files": skipped_files,
        "project_config": project_config,
    }


def main():
    input_data = json.loads(sys.stdin.read())
    project_folder = input_data.get("project_folder")

    if not project_folder:
        print(json.dumps({
            "error": "Missing required input: project_folder",
            "step": "1.1_scan_project_folder"
        }))
        sys.exit(1)

    try:
        result = scan_project_folder(project_folder)
    except (FileNotFoundError, NotADirectoryError,
            PermissionError, ValueError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "1.1_scan_project_folder"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
