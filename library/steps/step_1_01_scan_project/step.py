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
    RAW_VIDEO_EXTENSIONS,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    enumerate_audio,
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

    # Voiceover and music audio, in their own numbering - see
    # `SUPPORTED_AUDIO_EXTENSIONS`. Absent on video-only projects,
    # which is the normal case and not an error.
    raw_audio_files, skipped_audio = enumerate_audio(project_folder)
    for entry in skipped_audio:
        print(f"WARNING: Skipping file ({entry['reason']}): {entry['path']}",
              file=sys.stderr)

    # --- Verification: at least one video file found ---
    if not raw_footage_files:
        raw_count = sum(
            1 for entry in skipped_files
            if entry.get("extension") in RAW_VIDEO_EXTENSIONS)
        raise ValueError(
            f"No video files found in {project_folder}. "
            f"Supported extensions: {sorted(SUPPORTED_VIDEO_EXTENSIONS)}"
            + (f" - {raw_count} RAW file(s) were seen but need "
               f"Resolve-side import ({sorted(RAW_VIDEO_EXTENSIONS)})"
               if raw_count else "")
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
    ProjectLayout(project_folder).ensure()

    # Lane B (cascade report 2.1): the scan writes Asset/Stream/Clock
    # through the Project Model revision API - the one writer PR 2271
    # built, no second store. Fail-soft by contract: a model link that
    # fails the scan is recorded in the step's own output, never raised
    # - the Resolve path is unchanged either way. The enumerated files
    # carry clip_id + path, which is all the migration needs to mint
    # content-derived ids; the catalog step stamps the revision.
    from library.tools import footage_model_link
    model_link = footage_model_link.link_scan(
        project_folder, catalog=raw_footage_files)
    model_revision = (model_link.get("revision")
                      if model_link.get("status") == "committed" else None)

    # What the project DECLARES, and nothing else.  This block used to
    # write `target_duration_seconds: 60`, `style_preset:
    # "shortform_vertical"` and `subtitle_style: "word_by_word"` for a
    # project that declared none of them, so a length nobody chose was
    # indistinguishable in state from one the captain typed.  One reader
    # now, shared with `load_pipeline_state`, so the two producers of
    # `project_config` cannot disagree.
    from library.tools.brand_registry import project_declared_config
    try:
        project_config = project_declared_config(project_folder)
    except Exception as e:
        print(f"WARNING: Failed to read project.yaml: {e}", file=sys.stderr)
        project_config = {}

    return {
        "raw_footage_files": raw_footage_files,
        "total_files": len(raw_footage_files),
        "raw_audio_files": raw_audio_files,
        "skipped_files": skipped_files,
        "project_config": project_config,
        # Lane B: which Project Model revision this scan's footage is
        # migrated at (None when the link was refused - the reason is
        # in `model_link`). The catalog step routes it onto every
        # entry it writes, so a reader of the catalog knows which
        # revision's assets/streams/clocks its rows resolve into.
        "model_revision": model_revision,
        "model_link": model_link,
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
