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
  3. Emits `music_selection` with `audio_path` at the top level, which is
     the key every downstream consumer reads (`music_analysis`,
     `mesh_spine`, `plan_transitions`, `compile_manifest`).

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

from library.tools.music_selection_contract import (  # noqa: E402
    validate_selection,
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
    """Download an external choice into the project's music folder."""
    from download_track import download_audio

    output_dir = os.path.join(project_folder, "music")
    os.makedirs(output_dir, exist_ok=True)
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
    if errors:
        raise ValueError(
            "The music selection is rejected:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return selection


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

    try:
        resolved = resolve_selection(
            selection, candidates, target_duration, project_folder)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "step": "2.04_post_bridge"}))
        sys.exit(1)

    resolved.setdefault("source_url", "")
    resolved.setdefault("bpm", None)
    resolved.setdefault("key", None)
    resolved["target_duration_seconds"] = target_duration
    resolved["catalogue_size"] = len(candidates)

    json.dump({"music_selection": resolved}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
