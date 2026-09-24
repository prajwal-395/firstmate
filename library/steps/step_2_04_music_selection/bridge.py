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
longer only what is on disk.  Captain's ruling 2026-09-02: search should
run by default rather than waiting on a flag nobody sets.  When the
project declares no `pipeline.music_search`, this bridge derives a query
from creative_direction's `target_mood` and `narrative_theme` - the
model's own words from step 2.01.  A project can still set
`pipeline.music_search: false` to decline.  When search does not run,
the reason is stated LOUDLY rather than quietly presenting the on-disk
files as the whole menu.  `library/tools/music_search.py` is the whole
of it, including what a run costs.

And two of 001's four surviving candidates were the same recording, which
no filename said.  `library/tools/music_duplicates.py` establishes it from
the measurements that are already paid for, and MARKS them - the model was
told it had four things to choose between and it had three.

Input:  { "creative_direction": {...}, "project_folder": "..." }
Output: { "music_candidates": {
            "target_duration_seconds": 60.0,
            "max_track_duration_seconds": 600.0,
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
                               "tempo_bpm", "tempo_method",
                               "tempo_beat_count", "tempo_downbeat_count",
                               "tempo_downbeat_source",
                               "tempo_stable", "tempo_note",
                              "musical_key", "key_method", "key_strength",
                               "key_note", "beat_grid",
                               "song_structure", "song_structure_note",
                               "duplicate_of", "duplicate_deltas",
                               "source_url", "provenance" }],
         } }

 `beat_grid` ({beats, downbeats} in file seconds) is stored on the
 candidate for code to read and dropped from the model's prompt in this
 step's manifest - AGENTS.md 10.1 forbids raw value lists in prompts, and
 the tempo_* scalars are the choice-time reading of rhythm.
 `song_structure` is NOT dropped: a handful of labelled spans, not a raw
 value list, and it is the choice-time reading the section decision is
 made from.
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
    measure_candidates,
    measure_rhythm_candidates,
    measure_structure_candidates,
)
from library.tools.music_duplicates import (  # noqa: E402
    duplicate_groups,
    mark_duplicates,
    summarise as summarise_duplicates,
)
from library.tools.music_search import (  # noqa: E402
    MusicSearchError,
    provenance,
    resolve_declaration,
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

    Precedence: an explicit `target_duration_seconds` input, then the
    merged video preferences' SOFT `target_length_seconds`
    (``style.yaml`` locked value, then ``video.yaml`` - see
    ``library/tools/video_prefs.effective_target_length``), then the
    project's own `project.yaml` `target_duration_seconds`, then the
    60 s default. The preference is SOFT - a target the candidate
    verdicts are measured against, never a gate: a longer or shorter
    video with defensible quality is allowed, so this refuses nothing
    on length.

    Read off the project rather than threaded through the DAG - a value
    threaded through is a value that gets renamed and defaulted away,
    and 2.04 runs before the spine exists so there is no measured
    duration to read yet.
    """
    declared = inputs.get("target_duration_seconds")
    if isinstance(declared, (int, float)) and declared > 0:
        return float(declared)

    project_folder = inputs.get("project_folder", "")
    supplied = inputs.get("video_preferences")
    if project_folder or supplied is not None:
        try:
            from library.tools.video_prefs import effective_target_length
            soft = effective_target_length(
                project_folder or "", video_preferences=supplied)
            if soft is not None and soft > 0:
                return float(soft)
        except Exception as exc:
            print(
                "  WARNING: could not read video preferences "
                f"target_length_seconds: {exc}",
                file=sys.stderr,
            )

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


def catalogue_project_audio(inputs: dict, target_duration: float,
                            ceiling: float) -> list:
    """Catalogued voiceover/music files as choosing candidates.

    Step 1.02 catalogues what the project brings that carries no
    picture into `audio_catalog` (`audio_001` numbering), and step 1.04
    indexes each file's sound into `audio_indices`. A music bed the
    captain already holds is a track like any other: it joins the
    candidates under the `project` source label and is measured by the
    same pass below, so the model chooses from it rather than around
    it. Each row carries what step 1.04 MEASURED about speech in the
    file - a voiceover take with minutes of words is catalogued, not
    hidden, and the note says what it is so the choice is informed.

    `audio_catalog` arrives on the DAG edge when the run carried it;
    runs whose catalog predates it fall back to the state file, and a
    run with neither catalogues nothing here rather than refusing -
    an empty audio intake is the normal video-only project.
    """
    project_folder = inputs.get("project_folder", "") or ""
    audio_catalog = inputs.get("audio_catalog")
    # What step 1.04 measured about speech in each catalogued file, by
    # audio id - read independently of where the catalog itself came
    # from. An earlier shape read it only on the state-fallback path,
    # so a run carrying `audio_catalog` on its edge labelled every
    # voiceover take "no transcribed speech".
    speech_by_id = {}
    for entry in inputs.get("audio_indices") or []:
        if isinstance(entry, dict) and entry.get("audio_id"):
            speech_by_id[entry["audio_id"]] = entry
    if audio_catalog is None and project_folder:
        # A run whose catalog predates the `audio_catalog` edge still
        # has the answer in its state file; a run with neither
        # catalogues nothing here rather than refusing.
        try:
            from library.tools.project_layout import ProjectLayout
            state_file = str(ProjectLayout(
                project_folder).pipeline_data_path)
            if os.path.isfile(state_file):
                with open(state_file, encoding="utf-8") as handle:
                    state_data = json.load(handle) or {}
                outputs = state_data.get("step_outputs", {})
                audio_catalog = (outputs.get("catalog", {}).get(
                    "audio_catalog"))
                for entry in (outputs.get("temporal_index", {}).get(
                        "audio_indices") or []):
                    if (isinstance(entry, dict)
                            and entry.get("audio_id")):
                        speech_by_id[entry["audio_id"]] = entry
        except Exception as exc:
            print(f"  WARNING: could not read audio_catalog from "
                  f"state: {exc}", file=sys.stderr)
            audio_catalog = []
    if not isinstance(audio_catalog, list):
        audio_catalog = []

    candidates = []
    for entry in audio_catalog:
        if not isinstance(entry, dict):
            continue
        full_path = entry.get("path") or entry.get("source_file", "")
        if not full_path or not os.path.isfile(full_path):
            continue
        if os.path.getsize(full_path) <= 0:
            continue
        if (os.path.splitext(
                entry.get("filename") or full_path)[1].lower()
                not in AUDIO_EXTENSIONS):
            continue
        duration = entry.get("duration_seconds") or _get_audio_duration(
            full_path)
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0.0
        ok, note = _duration_verdict(duration, target_duration, ceiling)
        speech = speech_by_id.get(entry.get("audio_id", ""), {})
        words = speech.get("total_words", 0) or 0
        speech_note = (
            f"catalogued voiceover take ({words} transcribed words)"
            if words else "catalogued music bed (no transcribed speech)"
        )
        candidates.append({
            "title": os.path.splitext(
                entry.get("filename") or os.path.basename(full_path))[0],
            "audio_path": full_path,
            "duration_seconds": round(duration, 3),
            "source": "project",
            "duration_ok": ok,
            "duration_note": note,
            "catalog_note": (f"{speech_note}; held in the project's "
                             f"own audio intake as {entry.get('audio_id')}."),
        })
    if candidates:
        print(f"  project audio intake: {len(candidates)} catalogued "
              f"file(s) join the candidates", file=sys.stderr)
    return candidates


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
        "searched": searched,
        "candidates": candidates,
    }


def fetch_searched(project_folder: str, target_duration: float,
                   ceiling: float, already: list,
                   creative_direction: dict = None) -> tuple:
    """Search as the project declared it, and fetch what could be chosen.

    Returns `(new_candidates, report)`.  Every result is judged on the
    duration YouTube states BEFORE anything is downloaded - that read is
    free and it is the same read the local catalogue makes - so the bytes
    are only spent on tracks that could actually be selected.  What was
    rejected is reported, never silently dropped.

    When the project declares no ``pipeline.music_search``, queries are
    derived from ``creative_direction`` (default-on per the captain's
    ruling of 2026-09-02).  If derivation yields nothing, the report
    says so LOUDLY rather than quietly falling back to on-disk files.

    `already` is the local catalogue: a track fetched by an earlier run
    is already on disk under `pipeline_output`, so it is annotated in
    place rather than added twice.
    """
    from download_track import download_audio

    raw_declaration = search_declaration(project_folder)
    declaration = resolve_declaration(raw_declaration, creative_direction)
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
        # LOUD gap: say WHY search is not running.  A silent fallback to
        # on-disk files is the defect this change fixes.
        print(
            f"  *** MUSIC SEARCH DID NOT RUN: {declaration.reason}",
            file=sys.stderr,
        )
        report["cost"] = {"queries": 0, "results": 0, "search_seconds": 0.0,
                          "considered_after_duration": 0, "errors": []}
        return [], report

    # Log the resolved query so the run record shows what was searched.
    print(
        f"  search query(ies): {list(declaration.queries)}",
        file=sys.stderr,
    )

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
    creative_direction = inputs.get("creative_direction") or {}
    target_duration = _target_duration(inputs)
    catalogue = catalogue_music(project_folder, target_duration)
    local_count = len(catalogue["candidates"])

    # What the project itself brought: a catalogued music bed joins the
    # candidates under the `project` label and is measured by the same
    # pass, so the model chooses from it rather than around it.
    for record in catalogue_project_audio(
            inputs, target_duration,
            catalogue["max_track_duration_seconds"]):
        if record["audio_path"] not in {
                c.get("audio_path") for c in catalogue["candidates"]}:
            catalogue["candidates"].append(record)
    local_count = len(catalogue["candidates"])

    # Search is default-on (captain's ruling 2026-09-02).  When the
    # project declares no pipeline.music_search, queries are derived from
    # creative_direction.  A fetched track joins the same list and is
    # measured by the same pass, so it reaches the model in the same
    # columns a local one does.
    try:
        fetched, search_report = fetch_searched(
            project_folder, target_duration,
            catalogue["max_track_duration_seconds"],
            catalogue["candidates"],
            creative_direction=creative_direction)
    except MusicSearchError:
        # A malformed declaration is a mistake to report, not one to
        # search around.  It travels as the exception it already is:
        # `run()` is the name an operation points at, and a function
        # that writes stdout and kills the process cannot be called by
        # anything but a subprocess.  `main()` does both, below.
        raise
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

    # Rhythm and harmony, AT CHOICE TIME.  bpm and key are in the model's
    # output schema, but step 2.06 measures them downstream of the choice,
    # so until now the model correctly answered null for both and the
    # track was chosen with no access to its rhythm or its harmony.
    # Captain's decision 2026-09-07, option (a): run 2.06's own tempo and
    # key measurement per candidate here, in the bridge.  Step 2.06 does
    # not move - it still analyses the chosen track.  These are
    # MEASUREMENTS, not preferences: no threshold, no BPM range, no key
    # and no ranking on the new columns (AGENTS.md 10.5).  The full grids
    # are stored on the candidates for code to read and dropped from the
    # prompt in this step's manifest (AGENTS.md 10.1: no raw value list
    # reaches one); the tempo_* scalars are what the model decides from.
    rhythm_started = time.monotonic()
    measured = measure_rhythm_candidates(measured)
    rhythm_seconds = time.monotonic() - rhythm_started
    rhythmed = [e for e in measured if e.get("measured")]
    for entry in rhythmed:
        grid = entry.get("beat_grid") or {}
        print(
            f"    rhythm {entry['title'][:48]}: "
            f"{entry.get('tempo_bpm')} BPM "
            f"({entry.get('tempo_method') or 'no tracker'}, "
            f"{entry.get('tempo_beat_count')} beats), "
            f"key {entry.get('musical_key') or 'unmeasured'}",
            file=sys.stderr,
        )
    if rhythmed:
        print(
            f"  rhythm pass: {rhythm_seconds:.1f}s over "
            f"{len(rhythmed)} track(s) "
            f"({rhythm_seconds / len(rhythmed):.1f}s each)",
            file=sys.stderr,
        )

    # Song structure, AT CHOICE TIME, by 2.06's own segmenter.  The
    # section the bed starts on is the model's decision and the labels
    # it is decided from only exist downstream of it: step 2.06 runs
    # `analyze_structure` on the CHOSEN track, so the model has been
    # naming sections with no notion of where the chorus is.  The 2.06
    # output cannot reach this prompt - this step runs BEFORE it, and an
    # edge back would be a DAG cycle - so the measurement moves here the
    # way tempo and key did (captain's decision 2026-09-07, option (a)):
    # same function, per candidate, labels in file seconds, which is the
    # clock `section.source_in` and `splices` are named in.  MEASUREMENTS,
    # not preferences: no label is ranked, preferred or defaulted
    # (AGENTS.md 10.5).  Key rides nothing new - `musical_key`/`key_*`
    # already travel per candidate from the rhythm pass above.
    structure_started = time.monotonic()
    measured = measure_structure_candidates(measured)
    structure_seconds = time.monotonic() - structure_started
    for row in measured:
        if not row.get("measured"):
            continue
        labels = row.get("song_structure") or []
        kinds = ",".join(l.get("type", "?") for l in labels[:6])
        print(
            f"    structure {row['title'][:48]}: "
            f"{len(labels)} labelled span(s)"
            + (f" ({kinds})" if kinds else
               f" ({row.get('song_structure_note') or 'unlabelled'})"),
            file=sys.stderr,
        )
    if rhythmed:
        print(
            f"  structure pass: {structure_seconds:.1f}s over "
            f"{len(rhythmed)} track(s) "
            f"({structure_seconds / len(rhythmed):.1f}s each)",
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

    try:
        result = run(input_data)
    except MusicSearchError as exc:
        print(json.dumps({"error": str(exc), "step": "2.04_bridge"}))
        sys.exit(1)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
