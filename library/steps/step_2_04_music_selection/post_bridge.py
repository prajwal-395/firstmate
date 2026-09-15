#!/usr/bin/env python3
"""
Step 2.4: Music Selection - Post-bridge

The verdict on the LLM's choice, and the only place one is passed.

Captain's ruling 2026-08-20 asked that a selection contradicting the
stated creative direction be **rejectable on the recorded reasoning**
rather than only on taste. So this bridge:

  1. Validates the selection against
     `library/tools/music_selection_contract.py` - source, catalogue
     membership, duration sanity, and a justification that names the
     registers the direction forbids.
  2. Fetches an `external` choice that names a URL but no local file, and
     re-measures the real duration off the downloaded file rather than
     trusting the number the model stated.
  3. Checks the SECTION the model chose - which part of the track plays -
     against the track's real length, through
     `library/tools/music_section.py`. It validates; it never picks one.
  4. Emits `music_selection` with `audio_path` at the top level, which is
     the key every downstream consumer reads (`music_analysis`,
     `mesh_spine`, `plan_transitions`, `plan_sfx`, `compile_manifest`).

A failure here exits non-zero with the reasons, so the run stops rather
than scoring the piece with something nobody would have chosen.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

STEP_DIR = Path(__file__).resolve().parent
if str(STEP_DIR) not in sys.path:
    sys.path.insert(0, str(STEP_DIR))

REPO_ROOT = STEP_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.second_pass import (
    SecondPassError,
    is_final_pass,
    read_shortlist,
)
from library.tools.second_pass import request as second_pass_request
from library.tools.music_section import (  # noqa: E402
    describe as describe_section,
    read_section,
    validate_section,
)
from library.tools.music_measurement import (  # noqa: E402
    section_envelopes,
    selection_measurements,
)
from library.tools.music_selection_contract import (  # noqa: E402
    CATALOGUE_SOURCES,
    resolve_audio_path,
    validate_selection,
    acquired_media_dir,
)


def _probe_duration(audio_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", audio_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0.0


def _fetch_external(selection: dict, project_folder: str) -> dict:
    """Download an external choice into the project's downloads area.

    NOT into `music/`.  That directory is the captain's own material and
    the pipeline does not write to it - a fetched track is something the
    pipeline produced, so it lives in the output tree with everything
    else the pipeline produced.  It stays catalogued as a `project`
    candidate either way; see `catalogue_sources`.
    """
    from download_track import download_audio

    output_dir = acquired_media_dir(project_folder)
    print(
        f"  Fetching external track: {selection.get('source_url')}",
        file=sys.stderr,
    )
    track_info = download_audio(selection["source_url"], output_dir)
    selection = dict(selection)
    selection["audio_path"] = track_info.get("audio_path", "")
    if track_info.get("title"):
        selection["title"] = track_info["title"]
    return selection


def resolve_selection(
    selection: dict,
    candidates: list,
    target_duration: float,
    project_folder: str,
) -> dict:
    """Validate, fetch if needed, re-measure, validate again."""
    if (selection.get("source") or "").lower() == "external" \
            and not (selection.get("audio_path") or "").strip() \
            and (selection.get("source_url") or "").strip():
        selection = _fetch_external(selection, project_folder)

    # The model names the track; the script supplies the path.  A local
    # selection that names a title but no path is resolved against the
    # catalogue here, exactly - before the verdict below, which still
    # demands an exact catalogue member.  A selection that already names
    # a path is left alone for the verdict to judge as before.
    if (selection.get("source") or "").strip().lower() in CATALOGUE_SOURCES \
            and not (selection.get("audio_path") or "").strip():
        path, resolution_errors = resolve_audio_path(
            selection.get("title"), selection.get("source"), candidates)
        if resolution_errors:
            raise ValueError(
                "The music selection names no resolvable track:\n"
                + "\n".join(f"  - {e}" for e in resolution_errors)
            )
        selection = dict(selection)
        selection["audio_path"] = path
        print(f"  Resolved {selection.get('title')!r} to {path}",
              file=sys.stderr)

    # Never trust a stated duration when the file is on disk. The stated
    # number is what a model wrote; the measured one is what will play.
    audio_path = (selection.get("audio_path") or "").strip()
    if audio_path and os.path.exists(audio_path):
        measured = _probe_duration(audio_path)
        if measured > 0:
            stated = selection.get("duration_seconds")
            if isinstance(stated, (int, float)) and abs(measured - stated) > 1.0:
                print(
                    f"  Stated duration {stated}s does not match the file "
                    f"({measured:.1f}s) - using the measured value.",
                    file=sys.stderr,
                )
            selection = dict(selection)
            selection["duration_seconds"] = round(measured, 3)

    errors = validate_selection(selection, candidates, target_duration)
    # Which part of the track plays is the model's decision (the frozen
    # handoff has asked for splices all along and nothing read them).
    # This checks it can be played; it does not choose one, and a
    # selection that declares none plays from the head of the file.
    errors += validate_section(
        selection, selection.get("duration_seconds") or 0.0, target_duration)
    if errors:
        raise ValueError(
            "The music selection is rejected:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    print("  " + describe_section(read_section(selection)), file=sys.stderr)

    # The bed's own measurements travel with the bed.  Every candidate was
    # measured so this step could choose between them; the chosen one's
    # numbers are a description of what will actually play, and step 5.02
    # needs them - a `music_behavior` word is a RELATIVE dB applied to
    # whatever the file already is, so the file's own level decides
    # whether the planned offset lands.  Scalars only; see
    # `music_measurement.WITHHELD_FROM_THE_SELECTION`.
    selection = dict(selection)
    selection["measurements"] = selection_measurements(selection, candidates)
    measured = selection["measurements"]
    if measured.get("measured"):
        print(f"  Bed measured: {measured.get('integrated_lufs')} LUFS "
              f"integrated, speech-band ratio "
              f"{measured.get('speech_band_ratio_db')} dB.", file=sys.stderr)
    else:
        print(f"  Bed NOT measured: {measured.get('measurement_note')}",
              file=sys.stderr)
    return selection


def _shortlist_track(row: dict, selection: dict, candidates: list) -> str:
    """The file a shortlisted section names, matched EXACTLY.

    A section naming no track is the selection's own track, which is the
    single-track case.  No nearest match: choosing a different track is a
    decision (AGENTS.md 10.5).
    """
    reference = (row.get("track") or "").strip()
    if not reference:
        return (selection.get("audio_path") or "").strip()
    for candidate in candidates:
        if candidate.get("audio_path") == reference:
            return reference
        if (candidate.get("title") or "") == reference:
            return (candidate.get("audio_path") or "").strip()
    return reference


def _measure_shortlist(shortlist: list, selection: dict,
                       candidates: list) -> list:
    """The envelope of every shortlisted section, grouped by file.

    One decode per FILE however many sections of it are named - the
    per-second windows are read once and each section is bucketed out of
    them (`music_measurement.section_envelopes`).
    """
    if not shortlist:
        return []
    by_path = {}
    for row in shortlist:
        path = _shortlist_track(row, selection, candidates)
        by_path.setdefault(path, []).append(row)
    out = []
    for path, rows in by_path.items():
        if not path or not os.path.exists(path):
            for row in rows:
                out.append({**row, "measured": False,
                            "measurement_note":
                                f"no readable file for track {path!r}"})
            continue
        for measured in section_envelopes(path, rows):
            out.append({**measured, "audio_path": path})
    return out


def _shortlist_block(measurements: list) -> str:
    """The measurements of the shortlisted sections, as numbers.

    What each key IS is stated in step 2.04's own `handoff.md`, under
    "The second pass", which the model has already read when this block
    reaches it.  The definitions travelled beside the numbers as a
    `section_measurements_legend` only while that file was under the
    captain's freeze, lifted 2026-09-09.
    """
    return json.dumps({
        "section_measurements": measurements,
    }, indent=2)


def main():
    data = json.loads(sys.stdin.read())

    catalogue = data.get("music_candidates") or {}
    candidates = catalogue.get("candidates", [])
    target_duration = catalogue.get("target_duration_seconds") or 60.0
    project_folder = data.get("project_folder", "") or "./"

    selection = data.get("music_selection")
    if not isinstance(selection, dict) and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            selection = parsed.get("music_selection", parsed)
        except Exception:
            selection = None

    if not isinstance(selection, dict) or not selection:
        print(json.dumps({
            "error": (
                "music_selection is missing. The step must choose a track: "
                "one of the catalogued candidates (source 'library' or "
                "'project'), or a track from outside the library (source "
                "'external' with a source_url)."
            ),
            "step": "2.04_post_bridge",
        }))
        sys.exit(1)

    # Some catalogued selection shapes nest the track under `tracks`.
    if "audio_path" not in selection and isinstance(
            selection.get("tracks"), list) and selection["tracks"]:
        merged = dict(selection)
        merged.update(selection["tracks"][0])
        selection = merged

    # ── Pass one: measure the SHAPE of the sections the model named ──
    # `measure_track` buckets the envelope of seconds 0 to the length of
    # the edit and nothing else, so the richest evidence in this step
    # described only the head of every track - the answer the section
    # feature exists to let the model move AWAY from. The model names the
    # sections it is CONSIDERING - several of them, across several tracks
    # if the bed is to be spliced - and this measures those and asks
    # again. Captain's choice, 2026-09-02: "Two-pass: summaries first,
    # envelope for the section the model names."
    # See library/tools/second_pass.py.
    try:
        shortlist = read_shortlist(selection)
    except SecondPassError as exc:
        print(json.dumps({"error": str(exc), "step": "2.04_post_bridge"}))
        sys.exit(1)
    measurements = _measure_shortlist(shortlist, selection, candidates)
    if shortlist and not is_final_pass(data):
        json.dump(second_pass_request(
            "You named sections you are considering and only their mean "
            "level and spread were measured. Here is the SHAPE of each "
            "one.",
            _shortlist_block(measurements),
        ), sys.stdout, indent=2)
        return

    try:
        resolved = resolve_selection(
            selection, candidates, target_duration, project_folder)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "step": "2.04_post_bridge"}))
        sys.exit(1)

    # The evidence the choice was made from travels with the choice.
    if shortlist:
        resolved["section_measurements"] = measurements

    resolved.setdefault("source_url", "")
    resolved.setdefault("bpm", None)
    resolved.setdefault("key", None)
    resolved["target_duration_seconds"] = target_duration
    resolved["catalogue_size"] = len(candidates)

    # Where the track came from travels with the choice. It is RECORDED,
    # and it gates nothing: the captain took licensing off the table on
    # 2026-08-28 ("just assume for everything that you already have a
    # licence"), so no step refuses a track on rights and there is no
    # rights model to build one out of.
    chosen = next(
        (c for c in candidates
         if c.get("audio_path") and c.get("audio_path") == resolved.get("audio_path")),
        None,
    )
    if chosen and chosen.get("provenance"):
        resolved.setdefault("provenance", chosen["provenance"])
    elif (resolved.get("source") or "").lower() == "external":
        resolved.setdefault("provenance", {
            "found_by": "named_by_the_model",
            "source_url": resolved.get("source_url", ""),
            "licence_gates": (
                "nothing - recorded as provenance only, per the captain's "
                "ruling of 2026-08-28"
            ),
        })

    json.dump({"music_selection": resolved}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
