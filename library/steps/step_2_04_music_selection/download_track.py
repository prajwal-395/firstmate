#!/usr/bin/env python3
"""
Step 2.4 Tool: Download YouTube Track and Analyze

Downloads a YouTube video as audio (WAV), then analyzes it for BPM and
musical key.

Usage (stdin/stdout JSON):
    echo '{"url": "https://youtube.com/watch?v=...", "output_dir": "./music"}' | python3 download_track.py

Requires:
    - yt-dlp (pip install yt-dlp)
    - ffmpeg (brew install ffmpeg) — used by yt-dlp for audio extraction
    - librosa (pip install librosa) — for BPM detection (optional, falls back to estimate)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

STEP_DIR = Path(__file__).resolve().parent
REPO_ROOT = STEP_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.music_search import yt_dlp_command  # noqa: E402


def download_audio(url: str, output_dir: str, analyze: bool = True) -> dict:
    """
    Download audio from a YouTube URL as WAV.
    Returns metadata about the downloaded file.

    `analyze=False` skips the librosa BPM and key passes. Step 2.04's
    bridge fetches searched candidates only to MEASURE them, and the
    rhythm pass (`music_measurement.measure_rhythm_candidates`, running
    2.06's own tempo and key measurement per candidate at choice time
    since the captain's decision of 2026-09-07) is what supplies tempo,
    key and beat-grid there - measuring twice would pay the librosa pass
    twice. The default is unchanged, so the named-URL path this has
    always served behaves exactly as before.
    """
    os.makedirs(output_dir, exist_ok=True)

    # First, get metadata to determine filename
    try:
        meta_result = subprocess.run(
            [
                *yt_dlp_command(),
                "--dump-json",
                "--no-download",
                url,
            ],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        if meta_result.returncode == 0:
            meta = json.loads(meta_result.stdout)
            title = meta.get("title", "unknown_track")
            # Sanitize title for filename
            safe_title = "".join(
                c if c.isalnum() or c in " -_" else "_"
                for c in title
            ).strip()[:80]
        else:
            safe_title = "downloaded_track"
            meta = {}
    except Exception:
        safe_title = "downloaded_track"
        meta = {}

    output_template = os.path.join(output_dir, f"{safe_title}.%(ext)s")
    wav_path = os.path.join(output_dir, f"{safe_title}.wav")

    # Download and convert to WAV
    try:
        result = subprocess.run(
            [
                *yt_dlp_command(),
                "-x",                          # Extract audio
                "--audio-format", "wav",        # Convert to WAV
                "--audio-quality", "0",         # Best quality
                "-o", output_template,          # Output path
                "--no-playlist",                # Single video only
                "--no-overwrites",              # Don't re-download
                url,
            ],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp not found. It is in requirements.txt; install it into "
            "the interpreter the run uses."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Download timed out for {url}")

    if result.returncode != 0:
        from library.tools.music_search import yt_dlp_version
        # The whole of stderr, not 300 characters of it: a stale yt-dlp
        # prints an update warning first and its real error - "HTTP Error
        # 403: Forbidden" - was landing past the truncation.
        raise RuntimeError(
            f"Download failed for {url} (yt-dlp {yt_dlp_version()}): "
            f"{(result.stderr or '').strip()}"
        )

    # Find the output file (yt-dlp may use a slightly different name)
    if not os.path.exists(wav_path):
        # Search for any wav file in the output directory
        for f in os.listdir(output_dir):
            if f.endswith(".wav") and safe_title[:20] in f:
                wav_path = os.path.join(output_dir, f)
                break

    if not os.path.exists(wav_path):
        raise RuntimeError(
            f"Download completed but WAV file not found at {wav_path}"
        )

    # Get duration using ffprobe
    duration = get_audio_duration(wav_path)

    bpm = analyze_bpm(wav_path) if analyze else None
    key = analyze_key(wav_path) if analyze else None

    return {
        "audio_path": os.path.abspath(wav_path),
        "title": meta.get("title", safe_title),
        "source_url": url,
        "channel": meta.get("channel", meta.get("uploader", "Unknown")),
        # Recorded as provenance and read by nothing. The captain took
        # licensing off the table on 2026-08-28; see
        # library/tools/music_search.py.
        "licence": meta.get("license") or "",
        "duration_seconds": duration,
        "bpm": bpm,
        "key": key,
    }


def get_audio_duration(filepath: str) -> float:
    """Get audio duration using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                filepath,
            ],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return round(
                float(data.get("format", {}).get("duration", 0)), 3
            )
    except Exception:
        pass
    return 0.0


def analyze_bpm(filepath: str) -> int | None:
    """
    Detect BPM using librosa. Falls back to None if unavailable.
    """
    try:
        import librosa
        y, sr = librosa.load(filepath, sr=22050, duration=60)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        # librosa may return an array; extract scalar
        if hasattr(tempo, '__len__'):
            tempo = tempo[0]
        return int(round(float(tempo)))
    except ImportError:
        print(
            "WARNING: librosa not available for BPM detection. "
            "Install with: pip install librosa",
            file=sys.stderr,
        )
        return None
    except Exception as e:
        print(
            f"WARNING: BPM detection failed: {e}",
            file=sys.stderr,
        )
        return None


def analyze_key(filepath: str) -> str | None:
    """
    Detect musical key using librosa. Falls back to None if unavailable.
    """
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(filepath, sr=22050, duration=60)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)

        keys = [
            "C", "C#", "D", "D#", "E", "F",
            "F#", "G", "G#", "A", "A#", "B"
        ]
        key_idx = int(np.argmax(chroma_mean))
        return keys[key_idx]
    except ImportError:
        return None
    except Exception:
        return None


def main():
    input_data = json.loads(sys.stdin.read())
    url = input_data.get("url")
    output_dir = input_data.get("output_dir", "./music")

    if not url:
        print(json.dumps({
            "error": "Missing required input: url",
            "tool": "download_track"
        }))
        sys.exit(1)

    try:
        result = download_audio(url, output_dir)
    except RuntimeError as e:
        print(json.dumps({
            "error": str(e),
            "tool": "download_track"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
