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
  - the CATALOGUE has at least one row    - `load_sfx_catalog` is what the
                                            planner chooses from, and an
                                            empty one leaves it nothing to
                                            choose

There used to be a fourth check, "at least one SFX type is matchable",
and it is gone with the thing it checked: `match_sfx_file` counted
keyword hits to turn one of eight abstract type names into a file, and
the planner now names a real file out of the catalogue instead.

Input:  { "sfx_library": "/path/to/sfx/library" }
Output: { "sfx_library_status": { "valid": true, ... } }
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from library.tools.sfx_library import (  # noqa: E402
    load_sfx_catalog,
    load_sfx_index,
)


class SfxLibraryInvalid(Exception):
    """The library cannot serve a run, and says so carrying its status.

    This was `_fail()`: a module-level function that wrote to stdout and
    then killed the process.  Both halves still happen - `main()` emits
    the same payload and still exits 1 - but at the process boundary, so
    a caller that is not a process gets an exception it can catch.
    """

    def __init__(self, status: dict):
        self.status = status
        super().__init__(status.get("error", "sfx library invalid"))


def _fail(status: dict) -> None:
    raise SfxLibraryInvalid(status)


def validate_sfx_library(inputs: dict) -> dict:
    """Check the SFX library can actually serve a run.

    Returns the status dict.  Raises `SfxLibraryInvalid` carrying the
    same status where the library cannot: no path, no directory, no
    readable index, nothing playable, or an empty catalogue.

    Takes the step's INPUTS rather than the resolved path, and reads
    `sfx_library` off them here, because this is the refusal that makes
    the manifest's `required: true` true.  `input_contract.read_step_code`
    finds it by binding the declared key to a name and then seeing a
    guard on that name exit; lifting the read into `main()` and passing
    a bare string moved the binding out of the guard's reach, and the
    survey reported `sfx_library` as declared-required-but-unenforced.
    See library/tools/input_contract.py.
    """
    sfx_library = inputs.get("sfx_library", "")

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

    # Compute and cache transient offsets
    index_updated = False
    for entry in playable:
        if "transient_offset_sec" not in entry:
            try:
                import librosa
                import numpy as np
                y, sr = librosa.load(entry["path"], sr=None)
                peak = np.max(np.abs(y))
                if peak == 0:
                    entry["transient_offset_sec"] = None
                else:
                    thresh = peak * (10 ** (-6 / 20))
                    above = np.where(np.abs(y) > thresh)[0]
                    prop = len(above) / len(y) if len(y) > 0 else 0
                    if prop >= 0.1:
                        entry["transient_offset_sec"] = None
                    else:
                        peak_time = float(np.argmax(np.abs(y)) / sr)
                        entry["transient_offset_sec"] = round(peak_time, 3)
            except Exception as e:
                print(f"Warning: could not analyze transient for {entry['path']}: {e}", file=sys.stderr)
                entry["transient_offset_sec"] = "unknown"
            index_updated = True

    if index_updated:
        index_path = os.path.join(sfx_library, "sfx_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "w") as f:
                    json.dump(entries, f, indent=2)
            except Exception as e:
                print(f"Warning: could not update sfx_index.json with transients: {e}", file=sys.stderr)

    # The catalogue is what step 4.04 puts in front of the model, so it
    # is what "usable" means. It is built from this same library path and
    # counts only entries whose file is on disk.
    catalog = load_sfx_catalog(sfx_library)
    described = sum(1 for e in catalog if e.get("description"))

    status = {
        "valid": bool(playable) and bool(catalog),
        "sfx_library_path": sfx_library,
        "entries": len(entries),
        "playable_entries": len(playable),
        "missing_files": len(entries) - len(playable),
        "catalog_entries": len(catalog),
        "catalog_entries_described": described,
    }

    if not playable:
        status["error"] = ("no indexed entry points at a file that exists - "
                           "every SFX placement would be silent")
        status["fix"] = "Re-run the SFX profiler against the library's current location."
        _fail(status)

    if not catalog:
        status["error"] = ("the SFX catalogue is empty - step 4.04 would "
                           "have nothing to offer the model (see "
                           "load_sfx_catalog in library/tools/sfx_library.py)")
        _fail(status)

    print(f"SFX Library Validation: {len(entries)} entries, "
          f"{len(playable)} playable, {len(catalog)} in the catalogue, "
          f"{described} described, VALID",
          file=sys.stderr)

    return status


def main():
    data = json.loads(sys.stdin.read())
    try:
        status = validate_sfx_library(data)
    except SfxLibraryInvalid as invalid:
        json.dump({"sfx_library_status": invalid.status}, sys.stdout, indent=2)
        sys.exit(1)

    json.dump({"sfx_library_status": status}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
