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

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v", ".webm"
}


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

    raw_footage_files = []
    skipped_files = []
    
    raw_dir = os.path.join(project_folder, "raw")
    if not os.path.exists(raw_dir) or not os.path.isdir(raw_dir):
        raise FileNotFoundError(f"Missing 'raw' subdirectory in project folder: {project_folder}")

    for root, _dirs, files in os.walk(raw_dir):
        for fname in sorted(files):  # sorted for deterministic output order
            filepath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()

            if ext not in SUPPORTED_VIDEO_EXTENSIONS:
                continue

            # Check individual file accessibility
            if not os.access(filepath, os.R_OK):
                skipped_files.append({
                    "path": filepath,
                    "reason": "permission denied"
                })
                print(
                    f"WARNING: Skipping file (permission denied): {filepath}",
                    file=sys.stderr,
                )
                continue

            # Check for zero-byte files (likely corrupt)
            size = os.path.getsize(filepath)
            if size == 0:
                skipped_files.append({
                    "path": filepath,
                    "reason": "zero-byte file (likely corrupt)"
                })
                print(
                    f"WARNING: Skipping zero-byte file: {filepath}",
                    file=sys.stderr,
                )
                continue

            raw_footage_files.append({
                "path": os.path.abspath(filepath),
                "filename": fname,
                "extension": ext,
                "size_bytes": size,
            })

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

    project_json_path = os.path.join(project_folder, "project.json")
    project_config = {
        "target_duration_seconds": 60,
        "style_preset": "shortform_vertical",
        "subtitle_style": "word_by_word"
    }
    if os.path.exists(project_json_path):
        try:
            with open(project_json_path, "r") as f:
                data = json.load(f)
            project_config["target_duration_seconds"] = data.get("target_duration_seconds", 60)
            project_config["style_preset"] = data.get("style_preset", "shortform_vertical")
            project_config["subtitle_style"] = data.get("subtitle_style", "word_by_word")
        except Exception as e:
            print(f"WARNING: Failed to read project.json: {e}", file=sys.stderr)

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
