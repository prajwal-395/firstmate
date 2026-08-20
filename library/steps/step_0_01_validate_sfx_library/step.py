#!/usr/bin/env python3
"""
Step 0.01: Validate SFX Library

Pre-flight check that verifies the SFX library the pipeline will actually
read is loadable and points at files that exist. This does NOT re-run the
SFX profiling pipeline - that's a one-time setup step.

It validates through `library.tools.sfx_library.load_sfx_index`, which is
the only reader of this library in the pipeline. It used to assert a
DIFFERENT layout instead - `library_analysis.json` and
`library_semantic.json` inside `profiles/` - which the real library does
not have and no reader has ever wanted: `load_sfx_index` reads
`<library>/sfx_index.json` first and treats those two filenames as
metadata to SKIP. The shipped library at PIPELINE_SFX_LIBRARY therefore
failed a gate while being perfectly usable, and the only thing that passed
was a project-local `mock_sfx/` directory containing profiles and no audio
at all. A gate that fails the real asset and passes an empty mock is worse
than no gate.

What is checked, and why each one is a real failure:
  - the directory exists                  - nothing to read
  - the index loads to at least one entry - `load_sfx_index` returned []
  - at least one entry's file is on disk  - every placement would be silent
  - at least one SFX type is matchable    - `match_sfx_file` can serve no
                                            request the planner can make

Input:  { "sfx_library": "/path/to/sfx/library" }
Output: { "sfx_library_status": { "valid": true, ... } }
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from library.tools.sfx_library import (  # noqa: E402
    available_sfx_types,
    load_sfx_index,
)


def _fail(status: dict) -> None:
    json.dump({"sfx_library_status": status}, sys.stdout, indent=2)
    sys.exit(1)


def main():
    data = json.loads(sys.stdin.read())
    sfx_library = data.get("sfx_library", "")

    if not sfx_library:
        _fail({
            "valid": False,
            "error": "No sfx_library path provided",
            "fix": "Set PIPELINE_SFX_LIBRARY in .env, or sfx_library in project.yaml.",
        })

    if not os.path.isdir(sfx_library):
        _fail({
            "valid": False,
            "sfx_library_path": sfx_library,
            "error": f"SFX library directory not found: {sfx_library}",
        })

    entries = load_sfx_index(sfx_library)
    if not entries:
        _fail({
            "valid": False,
            "sfx_library_path": sfx_library,
            "error": ("load_sfx_index returned no entries: no readable "
                      "sfx_index.json and no profiles/*.json"),
            "fix": "Run: python library/tools/analysis/sfx_pipeline.py --sfx-dir <library>",
        })

    playable = [e for e in entries
                if e.get("path") and os.path.exists(e["path"])]
    types = available_sfx_types(entries)

    status = {
        "valid": bool(playable) and bool(types),
        "sfx_library_path": sfx_library,
        "entries": len(entries),
        "playable_entries": len(playable),
        "missing_files": len(entries) - len(playable),
        "available_types": types,
    }

    if not playable:
        status["error"] = ("no indexed entry points at a file that exists - "
                           "every SFX placement would be silent")
        status["fix"] = "Re-run the SFX profiler against the library's current location."
        _fail(status)

    if not types:
        status["error"] = ("no indexed entry matches any SFX type the "
                           "planner can request (see TYPE_KEYWORDS in "
                           "library/tools/sfx_library.py)")
        _fail(status)

    print(f"SFX Library Validation: {len(entries)} entries, "
          f"{len(playable)} playable, types={','.join(types)}, VALID",
          file=sys.stderr)

    json.dump({"sfx_library_status": status}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
