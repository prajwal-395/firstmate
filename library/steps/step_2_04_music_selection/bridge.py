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

Captain's question 2026-08-28: *"why is it that we are not ... stuck
choosing from 7 filenames?"*  Because a duration was the only thing this
bridge opened the files for.  The run of record recorded the consequence -
"on the filenames alone the 'rise' track reads as forbidden and Sickick
reads as neutral - i.e. the context as supplied points at the wrong
answer" - and got it right only by shelling out to ffmpeg itself.  So the
catalogue now carries what that agent had to generate: integrated
loudness, loudness range, RMS spread, the envelope over the part that
actually plays, true peak and the share of energy sitting in the speech
band.  `library/tools/music_measurement.py` is the whole of it, and it
measures without classifying - no mood, no genre, no ranking.

Captain's ruling 2026-08-28: *"flush out the search functionality ...
just download what you need from youtube"*.  So the catalogue is no
longer only what is on disk.  When the project declares
`pipeline.music_search`, this bridge searches, drops what cannot cover
the edit on the free metadata, fetches the survivors, and MEASURES them -
so a searched candidate reaches the model in the same columns a local one
does.  A candidate the model cannot see measured is the defect #296 just
fixed and this must not reintroduce it by another door.  Search is off
unless the project asks; `library/tools/music_search.py` is the whole of
it, including what a run costs.

And two of 001's four surviving candidates were the same recording, which
no filename said.  `library/tools/music_duplicates.py` establishes it from
the measurements that are already paid for, and MARKS them - the model was
told it had four things to choose between and it had three.

Input:  { "creative_direction": {...}, "project_folder": "..." }
Output: { "music_candidates": {
            "target_duration_seconds": 60.0,
            "max_track_duration_seconds": 600.0,
            "measurement_legend": { "<key>": "what it is" },
            "search": { "requested", "declaration", "cost", "rejected" },
            "duplicate_groups": [{ "representative", "also", ... }],
            "searched": [{ "source", "directory", "exists", "count" }],
            "candidates": [{ "title", "audio_path", "duration_seconds",
                             "source", "duration_ok", "duration_note",
                             "measured", "measurement_note",
                             "integrated_lufs", "loudness_range_lu",
                             "true_peak_dbtp", "rms_spread_db",
                             "window_seconds", "window_spread_db",
                             "window_envelope_dbfs", "track_sections",
                             "speech_band_ratio_db",
                             "duplicate_of", "duplicate_deltas",
                             "source_url", "provenance" }],
        } }
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure this step's directory is on sys.path for local tool imports
STEP_DIR = Path(__file__).resolve().parent
if str(STEP_DIR) not in sys.path:
    sys.path.insert(0, str(STEP_DIR))

REPO_ROOT = STEP_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.music_measurement import (  # noqa: E402
    MEASUREMENT_LEGEND,
    measure_candidates,
)
from library.tools.music_duplicates import (  # noqa: E402
    duplicate_groups,
    mark_duplicates,
    summarise as summarise_duplicates,
)
from library.tools.music_search import (  # noqa: E402
    MusicSearchError,
    provenance,
    search_all,
    search_declaration,
    within_duration,
)
from library.tools.music_selection_contract import (  # noqa: E402
    AUDIO_EXTENSIONS,
    DURATION_SLACK_SECONDS,
    acquired_media_dir,
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


def _duration_verdict(duration: float, target_duration: float,
                      ceiling: float) -> tuple:
    """`(duration_ok, duration_note)` - the one reading, for every source."""
    if duration > ceiling:
        return False, (
            f"TOO LONG: {duration / 60:.1f} min for a "
            f"{target_duration:.0f}s edit - this is a compilation, not a "
            f"track"
        )
    if duration + DURATION_SLACK_SECONDS < target_duration:
        return False, (
            f"TOO SHORT: {duration:.1f}s cannot cover a "
            f"{target_duration:.0f}s edit"
        )
    return True, ""


def catalogue_music(project_folder: str, target_duration: float) -> dict:
    """Every local track, from every source, labelled. Not yet measured."""
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
                ok, note = _duration_verdict(
                    duration, target_duration, ceiling)

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
        # Shipped with the numbers because step 2.04's handoff.md is under
        # a captain freeze and cannot name the new columns. A definition,
        # never a conclusion.
        "measurement_legend": dict(MEASUREMENT_LEGEND),
        "searched": searched,
        "candidates": candidates,
    }


def fetch_searched(project_folder: str, target_duration: float,
                   ceiling: float, already: list) -> tuple:
    """Search as the project declared it, and fetch what could be chosen.

    Returns `(new_candidates, report)`.  Every result is judged on the
    duration YouTube states BEFORE anything is downloaded - that read is
    free and it is the same read the local catalogue makes - so the bytes
    are only spent on tracks that could actually be selected.  What was
    rejected is reported, never silently dropped.

    `already` is the local catalogue: a track fetched by an earlier run
    is already on disk under `pipeline_output`, so it is annotated in
    place rather than added twice.
    """
    from download_track import download_audio

    declaration = search_declaration(project_folder)
    report = {
        "requested": declaration.requested,
        "declaration": declaration.describe(),
        "reason": declaration.reason,
        "rejected_on_duration": [],
        "fetched": 0,
        "fetch_seconds": 0.0,
        "fetch_errors": [],
    }
    print(f"  {declaration.describe()}", file=sys.stderr)
    if not declaration.requested:
        report["cost"] = {"queries": 0, "results": 0, "search_seconds": 0.0,
                          "considered_after_duration": 0, "errors": []}
        return [], report

    results, cost = search_all(declaration)
    report["cost"] = cost.as_dict()
    print(
        f"    {cost.results} result(s) from {cost.queries} query(ies) in "
        f"{cost.search_seconds:.1f}s",
        file=sys.stderr,
    )
    for error in cost.errors:
        print(f"    SEARCH: {error}", file=sys.stderr)

    keepers = []
    for result in results:
        ok, note = within_duration(
            result, target_duration, ceiling, DURATION_SLACK_SECONDS)
        if ok:
            keepers.append(result)
        else:
            report["rejected_on_duration"].append({
                "title": result.get("title", ""),
                "source_url": result.get("source_url", ""),
                "duration_seconds": result.get("duration_seconds"),
                "duration_note": note,
            })
    report["cost"]["considered_after_duration"] = len(keepers)
    print(
        f"    {len(keepers)} of {len(results)} could cover a "
        f"{target_duration:.0f}s edit; fetching at most "
        f"{declaration.fetch_limit}",
        file=sys.stderr,
    )

    by_path = {c.get("audio_path"): c for c in already}
    output_dir = acquired_media_dir(project_folder)
    fetched = []
    started = time.monotonic()
    for result in keepers[:declaration.fetch_limit]:
        try:
            track = download_audio(
                result["source_url"], output_dir, analyze=False)
        except Exception as exc:
            report["fetch_errors"].append(
                f"{result.get('title', '')}: {exc}")
            print(f"    FETCH FAILED: {result.get('title','')}: {exc}",
                  file=sys.stderr)
            continue

        path = track.get("audio_path", "")
        duration = _get_audio_duration(path) if path else 0.0
        ok, note = _duration_verdict(duration, target_duration, ceiling)
        result["licence"] = track.get("licence") or ""
        record = by_path.get(path)
        if record is None:
            record = {
                "title": track.get("title") or result.get("title", ""),
                "audio_path": path,
                "duration_seconds": round(duration, 3),
                # It is on disk in this project now, so it is catalogued
                # the same way anything else in the project is - the
                # source labels are part of the contract with the model.
                "source": "project",
                "duration_ok": ok,
                "duration_note": note,
            }
            fetched.append(record)
        record["source_url"] = result.get("source_url", "")
        record["provenance"] = provenance(result, declaration)
        report["fetched"] += 1
        print(f"    fetched {record['title'][:56]} ({duration:.1f}s)",
              file=sys.stderr)

    report["fetch_seconds"] = round(time.monotonic() - started, 2)
    return fetched, report


def run(inputs: dict) -> dict:
    project_folder = inputs.get("project_folder", "") or "./"
    target_duration = _target_duration(inputs)
    catalogue = catalogue_music(project_folder, target_duration)
    local_count = len(catalogue["candidates"])

    # Search, if the project asked for one. A fetched track joins the same
    # list and is measured by the same pass, so it reaches the model in
    # the same columns a local one does.
    try:
        fetched, search_report = fetch_searched(
            project_folder, target_duration,
            catalogue["max_track_duration_seconds"],
            catalogue["candidates"])
    except MusicSearchError as exc:
        # A malformed declaration is a mistake to report, not one to
        # search around.
        print(json.dumps({"error": str(exc), "step": "2.04_bridge"}))
        sys.exit(1)
    catalogue["candidates"].extend(fetched)
    catalogue["search"] = search_report

    measured = measure_candidates(catalogue["candidates"], target_duration)
    for entry in measured:
        if entry.get("measured"):
            print(
                f"    measured {entry['title'][:48]}: "
                f"{entry.get('integrated_lufs')} LUFS, "
                f"LRA {entry.get('loudness_range_lu')} LU, "
                f"spread {entry.get('rms_spread_db')} dB, "
                f"first {target_duration:.0f}s "
                f"{entry.get('window_spread_db')} dB",
                file=sys.stderr,
            )

    # Two rows that are one recording is a third of 001's choice set. The
    # test is the measurements above, never the filename.
    measured = mark_duplicates(measured)
    catalogue["candidates"] = measured
    catalogue["duplicate_groups"] = duplicate_groups(measured)
    duplicate_line = summarise_duplicates(measured)
    if duplicate_line:
        print(f"  same recording twice: {duplicate_line}", file=sys.stderr)

    usable = [c for c in measured if c["duration_ok"]]
    distinct = [c for c in usable if not c.get("duplicate_of")]
    print(
        f"  {len(measured)} track(s) catalogued ({local_count} local, "
        f"{len(fetched)} searched), {len(usable)} within duration sanity "
        f"for a {target_duration:.0f}s edit, {len(distinct)} of them "
        f"distinct recordings. Selecting from them - or from outside - is "
        f"the LLM's call.",
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
