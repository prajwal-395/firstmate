#!/usr/bin/env python3
"""
Step 0.01: Validate SFX Library

Pre-flight check that verifies the SFX library's FAISS index and
semantic profiles exist and are searchable. This does NOT re-run
the SFX profiling pipeline — that's a one-time setup step.

If the index is missing, this step fails with a clear message
telling the user to run sfx_pipeline.py against their library.

Input:  { "sfx_library": "/path/to/sfx/library" }
Output: { "sfx_library_status": { "valid": true, ... } }
"""
import json
import os
import sys
import glob


def main():
    data = json.loads(sys.stdin.read())
    sfx_library = data.get("sfx_library", "")

    if not sfx_library:
        json.dump({
            "sfx_library_status": {
                "valid": False,
                "error": "No sfx_library path provided"
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    if not os.path.isdir(sfx_library):
        json.dump({
            "sfx_library_status": {
                "valid": False,
                "error": f"SFX library directory not found: {sfx_library}"
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    # Check for profiles directory
    profiles_dir = os.path.join(sfx_library, "profiles")
    if not os.path.isdir(profiles_dir):
        json.dump({
            "sfx_library_status": {
                "valid": False,
                "error": f"Profiles directory not found: {profiles_dir}",
                "fix": "Run: python library/tools/analysis/sfx_pipeline.py --sfx-dir <library>"
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    # Count profile files
    profiles = glob.glob(os.path.join(profiles_dir, "*.json"))
    if not profiles:
        json.dump({
            "sfx_library_status": {
                "valid": False,
                "error": "No profile .json files found in profiles/",
                "fix": "Run: python library/tools/analysis/sfx_pipeline.py --sfx-dir <library>"
            }
        }, sys.stdout, indent=2)
        sys.exit(1)

    # Check for FAISS index files
    index_files = {
        "library_analysis.json": "Librosa technical analysis index",
        "library_semantic.json": "Audio Flamingo semantic descriptions index",
    }
    missing_indexes = []
    for fname, desc in index_files.items():
        if not os.path.exists(os.path.join(profiles_dir, fname)):
            missing_indexes.append(f"{fname} ({desc})")

    # Also check for FAISS binary index if it exists
    faiss_index = os.path.join(profiles_dir, "sfx_index.faiss")
    has_faiss = os.path.exists(faiss_index)

    # Smoke test: try loading one profile
    smoke_test_ok = False
    smoke_test_error = None
    try:
        with open(profiles[0]) as f:
            profile = json.load(f)
            # Verify it has expected structure
            has_keys = any(k in profile for k in [
                "librosa_analysis", "semantic_description",
                "audio_features", "file_path"
            ])
            smoke_test_ok = has_keys
            if not has_keys:
                smoke_test_error = f"Profile {profiles[0]} missing expected keys"
    except (json.JSONDecodeError, IOError) as e:
        smoke_test_error = str(e)

    valid = len(missing_indexes) == 0 and smoke_test_ok

    status = {
        "valid": valid,
        "sfx_library_path": sfx_library,
        "profiles_dir": profiles_dir,
        "profiles_count": len(profiles),
        "has_faiss_index": has_faiss,
        "missing_indexes": missing_indexes,
        "smoke_test_ok": smoke_test_ok,
    }

    if smoke_test_error:
        status["smoke_test_error"] = smoke_test_error

    if not valid:
        status["fix"] = "Run: python library/tools/analysis/sfx_pipeline.py --sfx-dir <library>"

    print(f"SFX Library Validation: {len(profiles)} profiles, "
          f"FAISS={'✓' if has_faiss else '✗'}, "
          f"{'VALID' if valid else 'INVALID'}",
          file=sys.stderr)

    json.dump({"sfx_library_status": status}, sys.stdout, indent=2)

    if not valid:
        sys.exit(1)


if __name__ == "__main__":
    main()
