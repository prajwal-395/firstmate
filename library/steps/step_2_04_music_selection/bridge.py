#!/usr/bin/env python3
"""
Step 2.4: Music Selection — Bridge

Pre-computes music context for the LLM handoff.

Workflow:
  1. Check for local music files in project_folder/music/
  2. If no local music, search YouTube based on creative_direction mood
  3. Download the best match
  4. Return the track info as bridge output

The bridge output feeds into handoff.md where the LLM identifies
specific splices, evaluates mood alignment, and produces the full
music_selection document.

Input:  { "creative_direction": {...}, "project_folder": "..." }
Output: {
    "music_selection": {
        "audio_path": "...",
        "title": "...",
        "source_url": "...",
        "duration_seconds": 120.0,
        "bpm": 120,
        "key": "C"
    }
}
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure this step's directory is on sys.path for local tool imports
STEP_DIR = Path(__file__).resolve().parent
if str(STEP_DIR) not in sys.path:
    sys.path.insert(0, str(STEP_DIR))


def _find_local_music(project_folder: str) -> list:
    """Scan project_folder/music/ for existing audio files."""
    music_dir = os.path.join(project_folder, "music")
    if not os.path.isdir(music_dir):
        return []

    audio_exts = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
    found = []
    for entry in os.listdir(music_dir):
        ext = os.path.splitext(entry)[1].lower()
        if ext in audio_exts:
            full_path = os.path.join(music_dir, entry)
            if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                found.append(full_path)
    return sorted(found)


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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0.0


def run(inputs: dict) -> dict:
    creative_direction = inputs.get("creative_direction", {})
    mood = creative_direction.get("target_mood", "motivational")
    energy = creative_direction.get("target_energy", "medium")

    project_folder = inputs.get("project_folder", "./")
    output_dir = os.path.join(project_folder, "music")

    # 1. Check for local music files first
    local_files = _find_local_music(project_folder)
    if local_files:
        audio_path = local_files[0]
        duration = _get_audio_duration(audio_path)
        print(
            f"  Found local music: {os.path.basename(audio_path)} "
            f"({duration:.1f}s)",
            file=sys.stderr,
        )
        return {
            "music_selection": {
                "audio_path": audio_path,
                "title": os.path.splitext(os.path.basename(audio_path))[0],
                "source_url": "",
                "duration_seconds": duration,
                "bpm": None,
                "key": None,
                "source": "local",
            }
        }

    # 2. No local music - search YouTube
    print(
        f"  No local music found, searching YouTube for: "
        f"{mood} {energy}",
        file=sys.stderr,
    )

    try:
        from search_youtube import search_youtube
        from download_track import download_audio

        query = f"{mood} {energy} background music no copyright"
        search_results = search_youtube(query, max_results=3)

        if not search_results.get("results"):
            raise ValueError("YouTube search returned no results")

        best_match = search_results["results"][0]
        url = best_match["url"]
        print(
            f"  Selected: {best_match.get('title', 'unknown')} ({url})",
            file=sys.stderr,
        )

        track_info = download_audio(url, output_dir)
        track_info["source"] = "youtube"
        return {
            "music_selection": track_info
        }

    except Exception as e:
        # 3. Fallback only if BOTH local check and YouTube fail
        print(
            f"  WARNING: Music selection failed: {e}",
            file=sys.stderr,
        )
        return {
            "music_selection": {
                "audio_path": "",
                "title": f"Fallback track for {mood}",
                "source_url": "",
                "duration_seconds": 0,
                "bpm": None,
                "key": None,
                "error": str(e),
                "source": "fallback",
            }
        }


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    result = run(input_data)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
