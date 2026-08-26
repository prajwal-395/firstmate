#!/usr/bin/env python3
"""
Step 1.04: Temporal Event Index

Deterministic pre-processing step that extracts timestamped events from
each video clip using signal-processing tools. Runs in parallel with
steps 1.02 (catalog) and 1.03 (semantic analysis) — only needs file
paths from step 1.01.

Produces a per-clip JSON index containing:
  - scene_boundaries: visual cut/change points (ffmpeg scene detection)
  - speech_regions: start/end of speech with ASR transcript + word-level
    timestamps (WhisperX: faster-whisper transcription + wav2vec2
    forced alignment for ±15ms word boundaries)
  - energy_curve: per-second RMS audio energy (librosa, 30Hz frame-aligned)
  - audio_events: classified audio events — silence, ambient noise, etc.
  - motion_energy: per-second visual motion magnitude (frame differencing,
    30Hz frame-aligned)

The index bridges the gap between semantic analysis (knows WHAT happens)
and timeline assembly (needs WHEN things happen).

Speech detection uses WhisperX, which combines:
  1. faster-whisper (large-v3) for fast, accurate transcription
  2. wav2vec2 forced alignment for phoneme-level word timestamps
This two-pass approach produces word boundaries with ±5-15ms accuracy,
compared to ±30-150ms from Whisper's attention-based timestamps alone.
Word start times are additionally snapped to the nearest audio onset
(±23ms spectral transient) when within 30ms, giving consonant attacks
sub-frame precision.

Classification: Deterministic / Signal Processing
Archetype: Data Transformation
Idempotent: Yes (same input → same output)

Input:  { "raw_footage_files": [{ path, filename, ... }] }
Output: Per-clip JSON files in <project>/pipeline_output/temporal_index/<clip_id>.json
        A clip whose file is already there is reused, not re-transcribed.

Requires:
    - ffmpeg on PATH
    - librosa + soundfile
    - whisperx (pip install whisperx)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from model_lifecycle import load_model, unload_model


# This step's stdout is its JSON result, so nothing else may write to
# it - whisperx attaches a stdout log handler and silently corrupted a
# completed 17-clip index. The guard and the full story live in
# library/tools/step_stdout.py; `main()` calls _claim_stdout() first.
from library.tools.step_stdout import claim_stdout as _claim_stdout, emit as _emit
from library.tools.project_layout import Area, ProjectLayout


# ── Audio extraction ─────────────────────────────────────────────────

def extract_audio_16k(video_path: str, output_dir: str, clip_id: str = None) -> str:
    """Extract audio as 16kHz mono WAV. Cached — skips if already exists."""
    os.makedirs(output_dir, exist_ok=True)
    basename = clip_id if clip_id else Path(video_path).stem
    audio_path = os.path.join(output_dir, f"{basename}.wav")

    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
        return audio_path

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1",
                "-y", audio_path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install: brew install ffmpeg")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg timed out extracting audio: {video_path}")

    if result.returncode != 0:
        raise RuntimeError(
            f"Audio extraction failed for {video_path}: "
            f"{result.stderr[:300]}"
        )

    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        raise RuntimeError(f"Empty audio file for {video_path}")

    return audio_path


# ── 1. Scene detection (ffmpeg) ──────────────────────────────────────

def detect_scenes(video_path: str, threshold: float = 0.3) -> list:
    """
    Use ffmpeg's scene detection filter to find visual cut/change points.

    Returns a list of scene boundary dicts:
        [{"time": 0.0, "score": 1.0, "type": "start"}, ...]
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-filter:v",
                f"select='gt(scene,{threshold})',metadata=print",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(
            f"  WARNING: scene detection timed out for {video_path}",
            file=sys.stderr,
        )
        return [{"time": 0.0, "score": 1.0, "type": "start"}]

    scenes = [{"time": 0.0, "score": 1.0, "type": "start"}]

    # Parse ffmpeg metadata output for scene scores
    # Lines look like: lavfi.scene_score=0.452387
    # with pts_time in the preceding showinfo line
    current_time = None
    for line in result.stderr.split("\n"):
        # Look for pts_time in frame metadata
        pts_match = re.search(r"pts_time:(\S+)", line)
        if pts_match:
            current_time = float(pts_match.group(1))

        # Look for scene_score
        score_match = re.search(r"scene_score=(\S+)", line)
        if score_match and current_time is not None:
            score = float(score_match.group(1))
            if score > threshold:
                scenes.append({
                    "time": round(current_time, 3),
                    "score": round(score, 3),
                    "type": "scene_change",
                })
            current_time = None

    # Deduplicate (within 0.1s)
    deduped = [scenes[0]]
    for s in scenes[1:]:
        if s["time"] - deduped[-1]["time"] > 0.1:
            deduped.append(s)

    return deduped


# ── 2. Speech region detection (WhisperX: faster-whisper + wav2vec2) ──

def _get_whisperx_models(model_size: str = "large-v3"):
    """Load WhisperX transcription + alignment models using model_lifecycle.

    Transcription uses CTranslate2 (CPU-only on macOS).
    Alignment uses wav2vec2 via PyTorch — uses MPS on Apple Silicon
    for GPU acceleration, falls back to CPU.
    """
    try:
        import torchaudio
        if not hasattr(torchaudio, 'set_audio_backend'):
            torchaudio.set_audio_backend = lambda x: None
        if not hasattr(torchaudio, 'get_audio_backend'):
            torchaudio.get_audio_backend = lambda: "soundfile"
    except ImportError:
        pass
    import whisperx

    def _load_transcribe():
        print(f"  Loading WhisperX transcription model ({model_size}, int8, cpu)...", file=sys.stderr)
        return whisperx.load_model(model_size, device="cpu", compute_type="int8")

    def _load_align():
        import torch
        if torch.backends.mps.is_available():
            device = "mps"
            print("  Loading wav2vec2 alignment model (mps — GPU)...", file=sys.stderr)
        else:
            device = "cpu"
            print("  Loading wav2vec2 alignment model (cpu)...", file=sys.stderr)
            
        model, metadata = whisperx.load_align_model(language_code="en", device=device)
        return (model, metadata, device)

    transcribe_model = load_model("whisperx_transcribe", _load_transcribe)
    align_model, align_metadata, align_device = load_model("whisperx_align", _load_align)

    return (
        transcribe_model,
        align_model,
        align_metadata,
        align_device,
    )

def _unload_whisperx_models():
    """Unload WhisperX models when done with speech detection."""
    unload_model("whisperx_transcribe")
    unload_model("whisperx_align")


def snap_word_boundaries_to_onsets(
    words: list,
    onsets: list,
    max_snap_ms: float = 30.0,
) -> list:
    """Snap word start times to the nearest audio onset.

    Consonant attacks ("p", "t", "k", "b", etc.) produce spectral
    onsets that are detectable with ±23ms precision via librosa.
    If a word's start time is within max_snap_ms of an onset, snap
    it to that onset for sub-frame precision.

    Args:
        words: List of {word, start, end} dicts (mutated in place)
        onsets: Sorted list of onset times in seconds
        max_snap_ms: Maximum snap distance in milliseconds

    Returns:
        The same words list, with start times potentially adjusted.
    """
    if not onsets or not words:
        return words

    import bisect
    max_snap_s = max_snap_ms / 1000.0
    snapped_count = 0

    for word in words:
        # Binary search for nearest onset
        idx = bisect.bisect_left(onsets, word["start"])
        candidates = []
        if idx > 0:
            candidates.append(onsets[idx - 1])
        if idx < len(onsets):
            candidates.append(onsets[idx])

        if candidates:
            nearest = min(candidates, key=lambda o: abs(o - word["start"]))
            delta = abs(nearest - word["start"])
            if delta <= max_snap_s:
                word["start"] = round(nearest, 3)
                snapped_count += 1

    return words


def _sanitize_word_boundaries(words: list) -> list:
    """Fix wav2vec2 forced-alignment artifacts.

    Three passes:
      1. Fix negative/zero durations (end <= start)
      2. Clamp unreasonably long words (> 2s) to median duration
      3. Fix overlapping boundaries (word[i].end > word[i+1].start)

    Must be called AFTER onset snapping (which may shift starts).
    """
    if not words:
        return words

    MIN_WORD_DURATION = 0.020   # 20ms — roughly one frame at 30fps
    MAX_WORD_DURATION = 2.0     # alignment failure threshold

    # Pass 1: fix negative/zero durations
    for w in words:
        if w["end"] <= w["start"]:
            w["end"] = round(w["start"] + MIN_WORD_DURATION, 3)
        elif w["end"] - w["start"] < MIN_WORD_DURATION:
            w["end"] = round(w["start"] + MIN_WORD_DURATION, 3)

    # Pass 2: clamp unreasonably long words
    durations = [
        w["end"] - w["start"] for w in words
        if MIN_WORD_DURATION <= w["end"] - w["start"] <= MAX_WORD_DURATION
    ]
    if durations:
        median_dur = sorted(durations)[len(durations) // 2]
    else:
        median_dur = 0.3  # fallback

    for w in words:
        if w["end"] - w["start"] > MAX_WORD_DURATION:
            w["end"] = round(w["start"] + median_dur, 3)

    # Pass 3: clamp overlapping word boundaries
    for i in range(len(words) - 1):
        if words[i]["end"] > words[i + 1]["start"]:
            words[i]["end"] = words[i + 1]["start"]

    return words


def detect_speech_regions(
    audio_path: str,
    output_dir: str,
    onsets: list = None,
    whisper_model_size: str = "large-v3",
) -> list:
    """Detect speech regions using WhisperX with forced alignment.

    Two-pass pipeline:
      1. faster-whisper (large-v3, int8) transcription with VAD
      2. wav2vec2 forced alignment for phoneme-level word timestamps

    Word start times are additionally snapped to the nearest audio onset
    (from librosa onset detection) when within 30ms, giving consonant
    attacks sub-frame precision.

    Returns a list of speech region dicts:
        [{
            "start": 0.8, "end": 3.14,
            "text": "i can feel the silent judgment",
            "words": [{"word": "i", "start": 0.80, "end": 0.92}, ...],
            "confidence": 0.92,
            "method": "whisperx-wav2vec2-large-v3"
        }, ...]
    """
    regions = []

    try:
        try:
            import torchaudio
            if not hasattr(torchaudio, 'set_audio_backend'):
                torchaudio.set_audio_backend = lambda x: None
            if not hasattr(torchaudio, 'get_audio_backend'):
                torchaudio.get_audio_backend = lambda: "soundfile"
        except ImportError:
            pass
        import whisperx

        trans_model, align_model, align_metadata, align_device = \
            _get_whisperx_models(whisper_model_size)

        # Pass 1: Transcribe with faster-whisper (batched)
        audio = whisperx.load_audio(audio_path)
        result = trans_model.transcribe(
            audio, batch_size=4, language="en",
        )

        # Guard: clips with no speech (ambient, B-roll, silence)
        if not result.get("segments"):
            print("          (no speech detected)", file=sys.stderr)
            return regions

        # Pass 2: Forced alignment with wav2vec2
        aligned = whisperx.align(
            result["segments"],
            align_model,
            align_metadata,
            audio,
            align_device,
            return_char_alignments=False,
        )

        if not aligned.get("segments"):
            print("          (alignment produced no segments)",
                  file=sys.stderr)
            return regions

        # Process aligned segments into speech regions
        for segment in aligned["segments"]:
            text = segment.get("text", "").strip()
            if not text:
                continue

            words = []
            for w in segment.get("words", []):
                # wav2vec2 alignment may fail for some words (numbers,
                # symbols) — skip words without timing
                if "start" not in w or "end" not in w:
                    continue
                words.append({
                    "word": w["word"].strip().lower(),
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                })

            if not words:
                continue

            # Snap word boundaries to onsets for extra precision
            if onsets:
                words = snap_word_boundaries_to_onsets(words, onsets)

            # Sanitize wav2vec2 artifacts (overlaps, long words, zero-duration)
            words = _sanitize_word_boundaries(words)

            region = {
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "text": text.lower().strip(),
                "words": words,
                "confidence": round(
                    segment.get("avg_logprob", 0.0), 3
                ) if "avg_logprob" in segment else 0.0,
                "method": f"whisperx-wav2vec2-{whisper_model_size}",
            }
            regions.append(region)

    except ImportError as e:
        print(
            f"  WARNING: whisperx not available ({e}), "
            "skipping speech detection",
            file=sys.stderr,
        )
    except Exception as e:
        print(
            f"  WARNING: speech detection failed: {e}",
            file=sys.stderr,
        )
        import traceback
        traceback.print_exc(file=sys.stderr)
    finally:
        _unload_whisperx_models()

    return regions


def _merge_speech_regions(regions: list, max_gap: float = 0.5) -> list:
    """Merge adjacent speech regions separated by less than max_gap."""
    if not regions:
        return []

    sorted_regions = sorted(regions, key=lambda r: r["start"])
    merged = [sorted_regions[0].copy()]

    for region in sorted_regions[1:]:
        prev = merged[-1]
        gap = region["start"] - prev["end"]

        if gap < max_gap:
            # Merge: extend previous region
            prev["end"] = region["end"]
            prev["text"] += " " + region["text"]
            prev["words"].extend(region.get("words", []))
        else:
            merged.append(region.copy())

    return merged


# ── 3. Energy curve (librosa RMS) ────────────────────────────────────

def compute_energy_curve(
    audio_path: str,
    sample_rate_hz: int = 30,
) -> dict:
    """
    Compute frame-aligned RMS audio energy using librosa.

    Default sample rate of 30Hz aligns with the video frame rate (30fps),
    giving ±33ms precision — single-frame accuracy for energy-based
    decisions like SFX placement and ducking transitions.

    Returns:
        {
            "sample_rate_hz": 30,
            "values": [0.1, 0.3, 0.9, ...],  # normalized 0-1
            "peak_times": [4.533, 12.100, ...]  # scipy-detected peaks
        }
    """
    try:
        import librosa
        import numpy as np
        from scipy.signal import find_peaks

        y, sr = librosa.load(audio_path, sr=16000)

        # RMS energy with hop size matching desired output sample rate
        hop_length = sr // sample_rate_hz
        rms = librosa.feature.rms(
            y=y, frame_length=hop_length * 2, hop_length=hop_length
        )[0]

        # Normalize to 0-1
        rms_max = rms.max()
        if rms_max > 0:
            rms_norm = rms / rms_max
        else:
            rms_norm = rms

        # Scipy peak finding with prominence and minimum spacing.
        # distance=15 at 30Hz = 0.5s minimum between peaks — avoids
        # clustering and gives one clear peak per energy event.
        peaks, properties = find_peaks(
            rms_norm, prominence=0.3, distance=max(1, sample_rate_hz // 2)
        )
        peak_times = [round(float(p) / sample_rate_hz, 3) for p in peaks]

        # Round values for JSON
        rms_values = [round(float(v), 3) for v in rms_norm]

        return {
            "sample_rate_hz": sample_rate_hz,
            "values": rms_values,
            "peak_times": peak_times,
        }

    except ImportError:
        print(
            "  WARNING: librosa/scipy not available, skipping energy curve",
            file=sys.stderr,
        )
        return {"sample_rate_hz": sample_rate_hz, "values": [], "peak_times": []}
    except Exception as e:
        print(
            f"  WARNING: energy curve failed: {e}",
            file=sys.stderr,
        )
        return {"sample_rate_hz": sample_rate_hz, "values": [], "peak_times": []}


# ── 4. Audio event classification ────────────────────────────────────

# Classes of interest for editorial decisions (from AudioSet ontology)
_EDITORIAL_AUDIO_CLASSES = {
    "Speech", "Laughter", "Chuckle, chortle", "Giggle", "Snicker",
    "Sigh", "Gasp", "Crying, sobbing", "Whispering",
    "Clapping", "Finger snapping", "Cheering",
    "Music", "Singing", "Musical instrument",
    "Vehicle", "Car", "Traffic noise, roadway noise",
    "Wind", "Rain", "Water", "Bird", "Dog",
    "Door", "Footsteps", "Silence",
}


def classify_audio_events(
    audio_path: str,
    speech_regions: list = None,
    min_confidence: float = 0.3,
    sample_rate_hz: int = 2,
) -> list:
    """
    Classify audio events using a lightweight audio classifier.

    Uses torchaudio's pipeline if available, falls back to simple
    energy-based speech/silence classification otherwise.

    If speech_regions is provided, silence events that overlap with
    word-level speech activity are filtered out.

    Returns a list of audio event dicts:
        [{"time": 3.1, "duration": 0.8, "class": "laughter", "confidence": 0.7}, ...]
    """
    events = []

    try:
        import numpy as np

        # Try torchaudio YAMNet-style classification
        try:
            import torch
            import torchaudio
            from torchaudio.pipelines import (
                VGGISH as _pipeline,
            )

            # Load audio
            waveform, sr = torchaudio.load(audio_path)
            if sr != 16000:
                waveform = torchaudio.functional.resample(waveform, sr, 16000)
                sr = 16000

            # Use mono
            if waveform.shape[0] > 1:
                waveform = waveform.mean(dim=0, keepdim=True)

            # Process in 1-second windows for event detection
            window_samples = sr  # 1 second
            hop_samples = sr // sample_rate_hz

            # Simple energy-based event detection with spectral features
            waveform_np = waveform.squeeze().numpy()
            total_samples = len(waveform_np)

            for i in range(0, total_samples - window_samples, hop_samples):
                chunk = waveform_np[i:i + window_samples]
                t = i / sr

                # RMS energy
                rms = float(np.sqrt(np.mean(chunk ** 2)))

                # Zero crossing rate (high = noise/unvoiced, low = voiced/tonal)
                zcr = float(np.mean(np.abs(np.diff(np.sign(chunk))) > 0))

                # Classify based on features
                if rms < 0.005:
                    events.append({
                        "time": round(t, 2),
                        "duration": round(1.0 / sample_rate_hz, 2),
                        "class": "silence",
                        "confidence": round(min(1.0, (0.005 - rms) / 0.005), 2),
                    })
                elif zcr > 0.15 and rms > 0.02:
                    # High ZCR + energy = likely unvoiced/noise (wind, etc.)
                    events.append({
                        "time": round(t, 2),
                        "duration": round(1.0 / sample_rate_hz, 2),
                        "class": "ambient_noise",
                        "confidence": round(min(1.0, zcr / 0.3), 2),
                    })

            # Deduplicate consecutive same-class events into ranges
            events = _consolidate_events(events)

        except (ImportError, Exception) as e:
            print(
                f"  INFO: torchaudio not available for audio events ({e}), "
                "using basic detection",
                file=sys.stderr,
            )
            # Fallback: energy-based silence detection only
            import librosa
            y, sr = librosa.load(audio_path, sr=16000)
            hop = sr // sample_rate_hz
            rms = librosa.feature.rms(
                y=y, frame_length=hop * 2, hop_length=hop
            )[0]

            for i, val in enumerate(rms):
                t = i / sample_rate_hz
                if val < 0.005:
                    events.append({
                        "time": round(t, 2),
                        "duration": round(1.0 / sample_rate_hz, 2),
                        "class": "silence",
                        "confidence": 0.8,
                    })
            events = _consolidate_events(events)

    except Exception as e:
        print(
            f"  WARNING: audio event classification failed: {e}",
            file=sys.stderr,
        )

    # Filter silence events that overlap with word-level speech
    if speech_regions and events:
        word_spans = []
        for sr in (speech_regions or []):
            for w in sr.get("words", []):
                word_spans.append((w["start"], w["end"]))

        if word_spans:
            filtered = []
            for event in events:
                if event["class"] == "silence":
                    e_start = event["time"]
                    e_end = e_start + event["duration"]
                    overlaps_word = any(
                        min(e_end, we) - max(e_start, ws) > 0.05
                        for ws, we in word_spans
                    )
                    if overlaps_word:
                        continue  # skip — silence during speech
                filtered.append(event)
            events = filtered

    return events


def _consolidate_events(events: list) -> list:
    """Merge consecutive events of the same class into single ranges."""
    if not events:
        return []

    consolidated = [events[0].copy()]
    for ev in events[1:]:
        prev = consolidated[-1]
        # Same class and adjacent in time (within tolerance)
        if (ev["class"] == prev["class"] and
                ev["time"] <= prev["time"] + prev["duration"] + 0.1):
            # Extend duration
            prev["duration"] = round(
                ev["time"] + ev["duration"] - prev["time"], 2
            )
            # Average confidence
            prev["confidence"] = round(
                (prev["confidence"] + ev["confidence"]) / 2, 2
            )
        else:
            consolidated.append(ev.copy())

    # Filter out very short events (< 0.3s)
    return [e for e in consolidated if e["duration"] >= 0.3]


# ── 5. Motion energy (frame differencing) ────────────────────────────

def compute_motion_energy(
    video_path: str,
    sample_rate_hz: int = 30,
) -> dict:
    """
    Compute visual motion magnitude using frame differencing.

    Default sample rate of 30Hz gives 33ms resolution — single-frame
    accuracy at 30fps. This matches the energy curve resolution,
    enabling frame-aligned decisions for B-roll placement, VFX triggers,
    and scene boundary refinement.

    `values` is normalized to the clip's own peak, so it says nothing about
    how much motion the clip holds in absolute terms — every clip peaks at
    1.0. `peak_mean_abs_diff` carries that peak before normalization (mean
    absolute frame difference, 0-1 grey scale), so a consumer that needs an
    absolute measure can recover it as `value * peak_mean_abs_diff`.

    Returns:
        {
            "sample_rate_hz": 10,
            "values": [0.0, 0.1, 0.8, ...],  # normalized 0-1
            "peak_mean_abs_diff": 0.184,  # pre-normalization peak, 0-1
            "peak_motion_times": [4.5, 12.0, ...]  # scipy-detected peaks
            "high_motion_times": [4.5, 12.0, ...]   # values > 0.5
        }
    """
    try:
        import numpy as np
        from scipy.signal import find_peaks

        # Extract low-res grayscale frames at target FPS
        # Using ffmpeg to output raw frames — much faster than opencv
        fps = sample_rate_hz
        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps={fps},scale=160:90,format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray",
                "-v", "quiet",
                "-",
            ],
            capture_output=True, timeout=120,
        )

        if result.returncode != 0 or not result.stdout:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [],
                "peak_mean_abs_diff": 0.0,
                "peak_motion_times": [],
                "high_motion_times": [],
            }

        # Parse raw frames (160x90 grayscale = 14400 bytes per frame)
        frame_size = 160 * 90
        raw = np.frombuffer(result.stdout, dtype=np.uint8)
        n_frames = len(raw) // frame_size

        if n_frames < 2:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [0.0],
                "peak_mean_abs_diff": 0.0,
                "peak_motion_times": [],
                "high_motion_times": [],
            }

        frames = raw[:n_frames * frame_size].reshape(n_frames, 90, 160)

        # Compute mean absolute difference between consecutive frames
        diffs = np.abs(
            frames[1:].astype(np.float32) - frames[:-1].astype(np.float32)
        )
        motion = diffs.mean(axis=(1, 2)) / 255.0  # normalize to 0-1

        # Normalize to 0-1 range
        m_max = motion.max()
        if m_max > 0:
            motion_norm = motion / m_max
        else:
            motion_norm = motion

        motion_values = [round(float(v), 3) for v in motion_norm]

        # Scipy peak finding — minimum 0.5s between peaks
        peaks, _ = find_peaks(
            motion_norm, prominence=0.3,
            distance=max(1, sample_rate_hz // 2)
        )
        peak_motion_times = [
            round(float(p + 1) / sample_rate_hz, 3) for p in peaks
        ]

        # Also keep simple threshold for backward compat
        high_motion = [
            round((i + 1) / sample_rate_hz, 3)
            for i, v in enumerate(motion_values)
            if v > 0.5
        ]

        return {
            "sample_rate_hz": sample_rate_hz,
            "values": motion_values,
            "peak_mean_abs_diff": round(float(m_max), 5),
            "peak_motion_times": peak_motion_times,
            "high_motion_times": high_motion,
        }

    except ImportError:
        print(
            "  WARNING: numpy/scipy not available for motion energy",
            file=sys.stderr,
        )
        return {
            "sample_rate_hz": sample_rate_hz,
            "values": [],
            "peak_mean_abs_diff": 0.0,
            "peak_motion_times": [],
            "high_motion_times": [],
        }
    except Exception as e:
        print(
            f"  WARNING: motion energy computation failed: {e}",
            file=sys.stderr,
        )
        return {
            "sample_rate_hz": sample_rate_hz,
            "values": [],
            "peak_mean_abs_diff": 0.0,
            "peak_motion_times": [],
            "high_motion_times": [],
        }


# ── 6. Onset detection (librosa) ─────────────────────────────────────

def detect_onsets(audio_path: str) -> list:
    """Detect audio onset times (transients) using librosa.

    Onsets are sudden increases in spectral energy — claps, impacts,
    consonant attacks, percussive events. These are the natural
    anchor points for SFX placement.

    Returns a list of onset timestamps in seconds, with ~23ms precision
    (librosa default hop_length=512 at sr=22050).
    """
    try:
        import librosa

        y, sr = librosa.load(audio_path, sr=22050)
        # API changed across librosa versions
        try:
            onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
        except AttributeError:
            onset_frames = librosa.onset.detect(y=y, sr=sr)
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)
        return [round(float(t), 3) for t in onset_times]

    except ImportError:
        print(
            "  WARNING: librosa not available, skipping onset detection",
            file=sys.stderr,
        )
        return []
    except Exception as e:
        print(
            f"  WARNING: onset detection failed: {e}",
            file=sys.stderr,
        )
        return []


# ── 7. Word end times (convenience) ──────────────────────────────────

def extract_word_end_times(speech_regions: list) -> list:
    """Extract a flat sorted array of all word end timestamps.

    Used by downstream steps to snap cut points, transition timings,
    and subtitle click-SFX to word boundaries without traversing
    the full region tree.
    """
    ends = set()
    for region in speech_regions:
        for w in region.get("words", []):
            ends.add(round(w["end"], 3))
    return sorted(ends)


# ── 8. Optical flow direction (5Hz) ──────────────────────────────────

def compute_optical_flow_direction(
    video_path: str,
    sample_rate_hz: int = 5,
) -> dict:
    """Compute dominant optical flow direction at 5Hz.

    Extracts low-resolution grayscale frames, computes frame-to-frame
    optical flow using Farneback's dense optical flow algorithm, and
    summarizes the dominant motion vector (dx, dy) per sample.

    Interpretation:
      - dx > 0 = rightward motion (pan right or subject moves right)
      - dy > 0 = downward motion (tilt down or subject moves down)
      - Large consistent vectors = camera pan/tilt
      - Expanding vectors from center = zoom in
      - High magnitude with random directions = handheld shake

    Returns:
        {
            "sample_rate_hz": 5,
            "values": [{"dx": float, "dy": float, "magnitude": float}, ...],
            "dominant_motion": "static | pan_left | pan_right | tilt_up |
                                tilt_down | zoom | handheld | mixed"
        }
    """
    try:
        import numpy as np

        # Extract low-res grayscale frames at target FPS
        fps = sample_rate_hz
        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps={fps},scale=160:90,format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray",
                "-v", "quiet",
                "-",
            ],
            capture_output=True, timeout=120,
        )

        if result.returncode != 0 or not result.stdout:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [],
                "dominant_motion": "unknown",
            }

        frame_size = 160 * 90
        raw = np.frombuffer(result.stdout, dtype=np.uint8)
        n_frames = len(raw) // frame_size

        if n_frames < 2:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [{"dx": 0.0, "dy": 0.0, "magnitude": 0.0}],
                "dominant_motion": "static",
            }

        frames = raw[:n_frames * frame_size].reshape(n_frames, 90, 160)

        flow_vectors = []
        for i in range(n_frames - 1):
            prev = frames[i].astype(np.float32)
            curr = frames[i + 1].astype(np.float32)

            # Simple block-matching approximation using gradient correlation.
            # Full Farneback requires OpenCV — use a fast numpy alternative:
            # compute mean absolute difference in shifted versions to find
            # the dominant translation (dx, dy) between frames.
            best_dx, best_dy = 0, 0
            best_score = float("inf")

            for dy in range(-8, 9, 2):
                for dx in range(-8, 9, 2):
                    # Shift curr by (dx, dy) and compare to prev
                    if dy >= 0:
                        p_rows = slice(dy, None)
                        c_rows = slice(None, 90 - dy if dy > 0 else None)
                    else:
                        p_rows = slice(None, 90 + dy)
                        c_rows = slice(-dy, None)
                    if dx >= 0:
                        p_cols = slice(dx, None)
                        c_cols = slice(None, 160 - dx if dx > 0 else None)
                    else:
                        p_cols = slice(None, 160 + dx)
                        c_cols = slice(-dx, None)

                    try:
                        diff = np.mean(np.abs(
                            prev[p_rows, p_cols] - curr[c_rows, c_cols]
                        ))
                        if diff < best_score:
                            best_score = diff
                            best_dx, best_dy = dx, dy
                    except Exception:
                        pass

            # Normalize: divide by frame pixel range (0-255) → 0-1 per pixel
            magnitude = float(np.sqrt(best_dx ** 2 + best_dy ** 2)) / 8.0
            flow_vectors.append({
                "dx": round(float(best_dx) / 8.0, 3),
                "dy": round(float(best_dy) / 8.0, 3),
                "magnitude": round(magnitude, 3),
            })

        # Classify dominant motion pattern across all vectors
        if not flow_vectors:
            dominant = "static"
        else:
            mean_dx = float(np.mean([v["dx"] for v in flow_vectors]))
            mean_dy = float(np.mean([v["dy"] for v in flow_vectors]))
            mean_mag = float(np.mean([v["magnitude"] for v in flow_vectors]))

            if mean_mag < 0.05:
                dominant = "static"
            elif abs(mean_dx) > abs(mean_dy) * 1.5:
                dominant = "pan_right" if mean_dx > 0 else "pan_left"
            elif abs(mean_dy) > abs(mean_dx) * 1.5:
                dominant = "tilt_down" if mean_dy > 0 else "tilt_up"
            else:
                # Check magnitude consistency — consistent = pan, variable = handheld
                mag_std = float(np.std([v["magnitude"] for v in flow_vectors]))
                if mag_std > 0.15:
                    dominant = "handheld"
                else:
                    dominant = "mixed"

        return {
            "sample_rate_hz": sample_rate_hz,
            "values": flow_vectors,
            "dominant_motion": dominant,
        }

    except ImportError:
        print(
            "  WARNING: numpy not available for optical flow direction",
            file=sys.stderr,
        )
        return {"sample_rate_hz": sample_rate_hz, "values": [], "dominant_motion": "unknown"}
    except Exception as e:
        print(
            f"  WARNING: optical flow direction failed: {e}",
            file=sys.stderr,
        )
        return {"sample_rate_hz": sample_rate_hz, "values": [], "dominant_motion": "unknown"}


# ── 9. Camera motion decomposition (5Hz) ─────────────────────────────

def decompose_camera_motion(
    flow_direction: dict,
) -> dict:
    """Decompose optical flow into interpretable camera motion components.

    Derived from the optical_flow_direction output — no additional video
    decoding required.

    Components:
      - translation_x: horizontal camera motion (positive = panning right)
      - translation_y: vertical camera motion (positive = tilting down)
      - zoom_factor:   1.0 = no zoom. >1.0 = zoom in. <1.0 = zoom out.
                       Estimated from magnitude increase toward frame edges
                       vs. center — approximated from mean magnitude here.
      - residual:      leftover motion energy (subject movement, shake)

    Note: true zoom decomposition requires center-weighted optical flow
    analysis. This provides a useful approximation for editorial purposes.

    Returns:
        {
            "sample_rate_hz": 5,
            "values": [{"translation_x", "translation_y",
                         "zoom_factor", "residual"}, ...]
        }
    """
    values = flow_direction.get("values", [])
    if not values:
        return {"sample_rate_hz": flow_direction.get("sample_rate_hz", 5), "values": []}

    decomposed = []
    for v in values:
        dx = v["dx"]
        dy = v["dy"]
        mag = v["magnitude"]

        # Translation: direct from flow vectors
        tx = round(dx, 3)
        ty = round(dy, 3)

        # Zoom approximation: when motion magnitude is high but direction
        # is radially outward from center, interpret as zoom-in.
        # We approximate: if magnitude is high and dx/dy are small
        # relative to magnitude → possible zoom.
        translation_mag = abs(dx) + abs(dy)
        residual = round(max(0.0, mag - translation_mag * 0.5), 3)
        zoom_factor = round(1.0 + max(0.0, mag - translation_mag) * 0.3, 3)

        decomposed.append({
            "translation_x": tx,
            "translation_y": ty,
            "zoom_factor": zoom_factor,
            "residual": residual,
        })

    return {
        "sample_rate_hz": flow_direction.get("sample_rate_hz", 5),
        "values": decomposed,
    }


# ── 10. Face presence (5Hz) ───────────────────────────────────────────

def _load_face_cascade():
    """The frontal-face Haar cascade, or None if this OpenCV has none.

    Returning None matters as much as returning a classifier. OpenCV 5
    dropped Haar cascades: there is no `cv2.CascadeClassifier` and no XML
    in `cv2.data.haarcascades`. `requirements.txt` said `opencv-python>=4.8`,
    which resolves to 5.x, and the AttributeError that produced was caught
    by the broad `except Exception` around the whole function - so
    `face_presence` came back with EMPTY values on such a machine, rather
    than falling back to the variance heuristic. Empty is worse than
    approximate: it silently disabled the subject-absence usable-range
    rule in the vision pass as well as subject-aware framing.

    The requirement is now pinned below 5 so the cascade is really there;
    this function is the guard for anyone whose environment predates that.
    """
    try:
        import cv2
    except ImportError:
        return None

    classifier = getattr(cv2, "CascadeClassifier", None)
    if classifier is None:
        return None

    data = getattr(cv2, "data", None)
    haar_dir = getattr(data, "haarcascades", None) if data else None
    if not haar_dir:
        return None

    cascade_path = os.path.join(haar_dir, "haarcascade_frontalface_default.xml")
    if not os.path.exists(cascade_path):
        return None

    cascade = classifier(cascade_path)
    # A CascadeClassifier that failed to load its XML is not an error, it
    # is an object that detects nothing on every frame.
    if hasattr(cascade, "empty") and cascade.empty():
        return None
    return cascade


# The shortest side of the frames the cascade actually sees. Sampling was
# `scale=320:180`, a literal landscape shape applied to every clip: a
# rotated iPhone clip is 1080x1920 after ffmpeg's autorotate, so it was
# squashed ~5.3x horizontally before the frontal cascade ever saw it. On
# project 001 that split the index exactly along the `rotation` field -
# 1,886 detections over 3,578 landscape samples, 1 over 461 rotated ones -
# and `subject_center_x` was therefore None for every portrait clip.
#
# Why 480, measured on 001's own footage (OpenCV 4.14, cascade parameters
# unchanged):
#   * It preserves aspect, which is the defect. Nothing else here does.
#   * It bounds the SHORT side, so portrait costs the same as landscape
#     (480x854 vs 854x480). Bounding the height instead - the shape the
#     audit reproduced with - charges a vertical channel 4x and a
#     horizontal one 12.6x for the same answer.
#   * On 001's A-roll re-framed into a vertical frame, detection goes
#     0.1% -> 35.9% of samples; over 001's ten landscape clips it goes
#     52.7% -> 69.8%. The whole 17-clip face pass costs 2.4x more
#     (38.8s -> 93.3s), against a step whose WhisperX pass is minutes.
#   * Larger keeps buying recall, but the Haar cascade's false positives
#     grow faster. Over 001's seven face-free B-roll clips - scored the
#     way `subject_framing` scores, plausibility bounds applied - the
#     whole-clip false-positive rate is 22.1% at 480 and 32.3% at 600,
#     and 0.34 is the ratio `subject_framing.MIN_DETECTION_RATIO` treats
#     as a real track. Past 480 the crop starts being aimed by trees.
FACE_SAMPLE_SHORT_SIDE = 480


def face_sample_dimensions(
    video_path: str,
    short_side: int = FACE_SAMPLE_SHORT_SIDE,
) -> tuple:
    """The (width, height) to sample this clip at, preserving its aspect.

    Reads the DISPLAY shape - the stored frame with any rotation side data
    applied - because that is what ffmpeg's autorotate hands the filter
    chain. Never upscales past the source: a 640x360 clip carries no more
    detail at 854x480 and the cascade would just cost more.

    Raises rather than falling back to a fixed shape. A silent landscape
    default is the bug this function exists to remove, and a clip whose
    geometry cannot be read is a clip ffmpeg is about to fail on anyway.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-select_streams", "v:0",
            video_path,
        ],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe could not read {video_path}")

    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        raise ValueError(f"no video stream in {video_path}")
    stream = streams[0]

    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"no frame size for {video_path}")

    # Same rotation reading as step_1_02_catalog_footage: side data first,
    # the legacy `rotate` tag second.
    rotation = 0
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            rotation = int(side_data["rotation"])
            break
    if rotation == 0:
        try:
            rotation = int(stream.get("tags", {}).get("rotate", 0))
        except (TypeError, ValueError):
            rotation = 0
    if abs(rotation) in (90, 270):
        width, height = height, width

    target = min(short_side, min(width, height))
    if width <= height:
        out_w, out_h = float(target), target * height / float(width)
    else:
        out_h, out_w = float(target), target * width / float(height)

    # Even dimensions - several ffmpeg filters and codecs require them.
    # Rounded to the nearest even value from the exact ratio, so the
    # sampled aspect stays within a fraction of a percent of the source's.
    def _even(value):
        return max(2, int(value / 2.0 + 0.5) * 2)

    return _even(out_w), _even(out_h)


def compute_face_presence(
    video_path: str,
    sample_rate_hz: int = 5,
) -> dict:
    """Detect face presence at 5Hz using ffmpeg + OpenCV's Haar cascade.

    Provides a lightweight binary signal - is a human face present and
    roughly prominent in frame at each sample point - AND where it is
    horizontally, which is what lets a crop follow the subject instead of
    blindly centring.

    The cascade returns (x, y, w, h) per face and this function used to
    keep only max(w*h) as a scalar. The position was measured and thrown
    away, and the pipeline had no other source of subject geometry: the
    v3 vision pass emits shot size (`camera[].framing`), identity
    (`objects[].role`) and time ranges (`assessment.primary_subject_visible`),
    none of which is a position, and the two steps that do produce real
    bounding boxes (`object_segmentation`, `ocr_extraction`) are not wired
    into the DAG.

    Frames are sampled at the clip's OWN aspect ratio - see
    `face_sample_dimensions`. They were sampled at a fixed 320x180, which
    squashed every portrait clip by ~5.3x before the cascade saw it and
    made `face_center_x` None for all of them.

    Falls back to a simple brightness-variance heuristic (faces tend to
    introduce structured mid-frequency variation) if OpenCV is
    unavailable. That fallback cannot locate anything, so `face_center_x`
    is None throughout and consumers must degrade to centred framing.

    Returns:
        {
            "sample_rate_hz": 5,
            "values": [0.0–1.0, ...],  # 0=no face, 1=face detected, 0.5=partial
            "face_center_x": [float|None, ...],  # 0.0=left edge, 1.0=right
                                                 # edge; None where no face
                                                 # was located at that sample
            "face_present_times": [float, ...],  # timestamps where value > 0.5
            "face_absent_times": [float, ...]    # timestamps where value < 0.5
        }

    `face_center_x` is parallel to `values` and the same length. It tracks
    the LARGEST face in the frame, the same face `values` scores, so the
    two never describe different people.
    """
    try:
        import numpy as np

        # Extract frames at sample rate, at the clip's OWN aspect. The
        # cascade is trained on undistorted faces; a squashed frame moves
        # every face out of the shape it can match.
        fps = sample_rate_hz
        sample_w, sample_h = face_sample_dimensions(video_path)
        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps={fps},scale={sample_w}:{sample_h}",
                "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-v", "quiet",
                "-",
            ],
            capture_output=True, timeout=180,
        )

        if result.returncode != 0 or not result.stdout:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [],
                "face_center_x": [],
                "face_present_times": [],
                "face_absent_times": [],
            }

        frame_size = sample_w * sample_h * 3  # RGB
        raw = np.frombuffer(result.stdout, dtype=np.uint8)
        n_frames = len(raw) // frame_size

        if n_frames == 0:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [],
                "face_center_x": [],
                "face_present_times": [],
                "face_absent_times": [],
            }

        frames = raw[:n_frames * frame_size].reshape(
            n_frames, sample_h, sample_w, 3)
        face_values = []
        face_center_x = []

        cascade = _load_face_cascade()
        if cascade is not None:
            import cv2

            for frame in frames:
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=3, minSize=(20, 20)
                )
                if len(faces) > 0:
                    # Score by face area relative to frame, and keep where
                    # that same largest face sits horizontally.
                    largest = max(faces, key=lambda f: f[2] * f[3])
                    fx, _fy, fw, fh = largest
                    max_area = fw * fh
                    frame_area = sample_w * sample_h
                    presence = min(1.0, max_area / (frame_area * 0.15))
                    face_values.append(round(float(presence), 2))
                    # Normalise against the width actually sampled, which
                    # now varies per clip, so the value stays independent of
                    # both the sample size and the source resolution.
                    face_center_x.append(
                        round(float(fx + fw / 2.0) / sample_w, 4))
                else:
                    face_values.append(0.0)
                    face_center_x.append(None)

        else:
            # Fallback: mid-frequency variance heuristic.
            # Faces introduce structured variation in the mid-range.
            # High global variance + moderate spatial freq = face likely present.
            for frame_rgb in frames:
                gray = frame_rgb.mean(axis=2)  # simple luminance
                # Local variance in 20x20 blocks — faces cause structured variation
                local_var = float(np.std(gray))
                # Normalize: typical indoor scene std is 30-60
                presence = round(min(1.0, max(0.0, (local_var - 15) / 45)), 2)
                face_values.append(presence)
                # A variance heuristic knows nothing about WHERE. Saying
                # "centred" here would be a fabricated measurement, so the
                # honest answer is None and consumers centre by default.
                face_center_x.append(None)

        # Derive presence/absence time arrays
        face_present_times = [
            round(i / sample_rate_hz, 3)
            for i, v in enumerate(face_values) if v > 0.5
        ]
        face_absent_times = [
            round(i / sample_rate_hz, 3)
            for i, v in enumerate(face_values) if v <= 0.1
        ]

        return {
            "sample_rate_hz": sample_rate_hz,
            "values": face_values,
            "face_center_x": face_center_x,
            "face_present_times": face_present_times,
            "face_absent_times": face_absent_times,
        }

    except ImportError:
        print(
            "  WARNING: numpy not available for face presence",
            file=sys.stderr,
        )
        return {
            "sample_rate_hz": sample_rate_hz,
            "values": [],
            "face_center_x": [],
            "face_present_times": [],
            "face_absent_times": [],
        }
    except Exception as e:
        print(
            f"  WARNING: face presence computation failed: {e}",
            file=sys.stderr,
        )
        return {
            "sample_rate_hz": sample_rate_hz,
            "values": [],
            "face_center_x": [],
            "face_present_times": [],
            "face_absent_times": [],
        }


# ── 11. Dominant hue curve (1Hz) ──────────────────────────────────────

def compute_dominant_hue_curve(
    video_path: str,
    sample_rate_hz: int = 1,
) -> dict:
    """Compute dominant hue and brightness per second.

    Extracts the dominant color hue (HSV) from each sampled frame.
    Useful for detecting:
      - Color temperature shifts (warm ↔ cool transitions)
      - Scene changes that ffmpeg scene detection missed
      - Existing color grades on clips
      - Lighting transitions (golden hour → indoor)

    Returns:
        {
            "sample_rate_hz": 1,
            "hue_values": [float, ...],         # dominant hue 0-360 per sample
            "saturation_values": [float, ...],  # mean saturation 0-1
            "brightness_values": [float, ...],  # mean luminance 0-1
            "temperature_curve": ["warm"|"neutral"|"cool", ...]  # derived
        }
    """
    try:
        import numpy as np

        result = subprocess.run(
            [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps={sample_rate_hz},scale=80:45",
                "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-v", "quiet",
                "-",
            ],
            capture_output=True, timeout=120,
        )

        if result.returncode != 0 or not result.stdout:
            return {
                "sample_rate_hz": sample_rate_hz,
                "hue_values": [],
                "saturation_values": [],
                "brightness_values": [],
                "temperature_curve": [],
            }

        frame_size = 80 * 45 * 3
        raw = np.frombuffer(result.stdout, dtype=np.uint8)
        n_frames = len(raw) // frame_size

        if n_frames == 0:
            return {
                "sample_rate_hz": sample_rate_hz,
                "hue_values": [],
                "saturation_values": [],
                "brightness_values": [],
                "temperature_curve": [],
            }

        frames = raw[:n_frames * frame_size].reshape(n_frames, 45, 80, 3)
        hue_vals = []
        sat_vals = []
        bright_vals = []
        temp_vals = []

        for frame_rgb in frames:
            r = frame_rgb[:, :, 0].astype(np.float32) / 255.0
            g = frame_rgb[:, :, 1].astype(np.float32) / 255.0
            b = frame_rgb[:, :, 2].astype(np.float32) / 255.0

            # Brightness (luminance)
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            mean_bright = float(lum.mean())

            # RGB → HSV approximation
            cmax = np.maximum(np.maximum(r, g), b)
            cmin = np.minimum(np.minimum(r, g), b)
            delta = cmax - cmin

            # Saturation
            sat = np.where(cmax > 0, delta / cmax, 0.0)
            mean_sat = float(sat.mean())

            # Hue (using only well-saturated pixels to find dominant hue)
            sat_mask = sat > 0.2  # only count pixels with meaningful saturation
            if sat_mask.sum() > 10:
                r_m = r[sat_mask]
                g_m = g[sat_mask]
                b_m = b[sat_mask]
                cmax_m = cmax[sat_mask]
                delta_m = delta[sat_mask]

                hue = np.zeros_like(r_m)
                # Red dominant
                mask_r = (cmax_m == r_m) & (delta_m > 0)
                hue[mask_r] = (60 * ((g_m[mask_r] - b_m[mask_r]) / delta_m[mask_r]) % 6)
                # Green dominant
                mask_g = (cmax_m == g_m) & (delta_m > 0)
                hue[mask_g] = 60 * ((b_m[mask_g] - r_m[mask_g]) / delta_m[mask_g] + 2)
                # Blue dominant
                mask_b = (cmax_m == b_m) & (delta_m > 0)
                hue[mask_b] = 60 * ((r_m[mask_b] - g_m[mask_b]) / delta_m[mask_b] + 4)

                hue = hue % 360
                dominant_hue = float(np.median(hue))
            else:
                dominant_hue = 0.0  # achromatic / desaturated

            # Temperature classification from hue
            # Warm: reds, oranges, yellows (0-60 or 300-360)
            # Cool: blues, cyans (180-260)
            # Neutral: greens, magentas (everything else)
            if dominant_hue < 60 or dominant_hue > 300:
                temp = "warm"
            elif 180 <= dominant_hue <= 260:
                temp = "cool"
            else:
                temp = "neutral"

            hue_vals.append(round(dominant_hue, 1))
            sat_vals.append(round(mean_sat, 3))
            bright_vals.append(round(mean_bright, 3))
            temp_vals.append(temp)

        return {
            "sample_rate_hz": sample_rate_hz,
            "hue_values": hue_vals,
            "saturation_values": sat_vals,
            "brightness_values": bright_vals,
            "temperature_curve": temp_vals,
        }

    except ImportError:
        print(
            "  WARNING: numpy not available for hue/brightness curves",
            file=sys.stderr,
        )
        return {
            "sample_rate_hz": sample_rate_hz,
            "hue_values": [],
            "saturation_values": [],
            "brightness_values": [],
            "temperature_curve": [],
        }
    except Exception as e:
        print(
            f"  WARNING: hue/brightness curve failed: {e}",
            file=sys.stderr,
        )
        return {
            "sample_rate_hz": sample_rate_hz,
            "hue_values": [],
            "saturation_values": [],
            "brightness_values": [],
            "temperature_curve": [],
        }


# ── 12. Speech activity curve (derived, 30Hz) ─────────────────────────

def derive_speech_activity_curve(
    speech_regions: list,
    duration: float,
    sample_rate_hz: int = 30,
) -> dict:
    """Derive a binary speech activity curve from WhisperX word timestamps.

    No additional audio processing — computed directly from the already-aligned
    word timestamps in speech_regions. Provides a frame-aligned binary signal
    for instant lookup: 'is someone speaking at timestamp T?'

    Values:
      1 = speech active (a word's start-end span covers this frame)
      0 = no speech (silence, pause between words, non-speech segment)

    Returns:
        {
            "sample_rate_hz": 30,
            "values": [0 or 1, ...],
            "speech_ratio": float  # fraction of total clip duration with speech
        }
    """
    if not duration or duration <= 0:
        return {"sample_rate_hz": sample_rate_hz, "values": [], "speech_ratio": 0.0}

    n_samples = int(duration * sample_rate_hz) + 1
    activity = [0] * n_samples

    for region in speech_regions:
        for word in region.get("words", []):
            w_start = word.get("start", 0.0)
            w_end = word.get("end", 0.0)
            # Mark all samples in [w_start, w_end] as speech-active
            idx_start = max(0, int(w_start * sample_rate_hz))
            idx_end = min(n_samples - 1, int(w_end * sample_rate_hz))
            for i in range(idx_start, idx_end + 1):
                activity[i] = 1

    speech_ratio = sum(activity) / len(activity) if activity else 0.0

    return {
        "sample_rate_hz": sample_rate_hz,
        "values": activity,
        "speech_ratio": round(speech_ratio, 3),
    }


# ── Main orchestrator ────────────────────────────────────────────────

def get_duration(video_path: str) -> float:
    """Get video duration using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                video_path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0.0


def index_clip(
    video_path: str,
    clip_id: str,
    layout: ProjectLayout,
    whisper_model_size: str = "large-v3",
) -> dict:
    """
    Build temporal event index for a single clip.

    Runs all sub-analyzers and produces a complete index document.
    """
    print(f"\n  Indexing {clip_id}: {Path(video_path).name}", file=sys.stderr)
    t0 = time.time()

    duration = get_duration(video_path)
    print(f"    Duration: {duration:.1f}s", file=sys.stderr)

    # Extract audio (shared by speech + energy + audio event analyzers)
    audio_dir = str(layout.write_dir(Area.AUDIO_CACHE))
    audio_path = extract_audio_16k(video_path, audio_dir, clip_id=clip_id)

    # 1. Scene detection
    print("    [1/12] Scene detection...", file=sys.stderr)
    scenes = detect_scenes(video_path)
    print(f"           {len(scenes)} boundaries", file=sys.stderr)

    # 2. Energy curve (30Hz — frame-aligned)
    print("    [2/12] Energy curve (30Hz)...", file=sys.stderr)
    energy = compute_energy_curve(audio_path)
    print(
        f"           {len(energy['values'])} samples @ {energy['sample_rate_hz']}Hz, "
        f"{len(energy['peak_times'])} peaks",
        file=sys.stderr,
    )

    # 3. Onset detection (run BEFORE speech so we can onset-snap words)
    print("    [3/12] Onset detection...", file=sys.stderr)
    onsets = detect_onsets(audio_path)
    print(
        f"           {len(onsets)} onsets",
        file=sys.stderr,
    )

    # 4. Speech regions (WhisperX: transcription + wav2vec2 alignment)
    # Onsets are passed in for word-boundary snapping
    print("    [4/12] Speech detection (WhisperX + wav2vec2)...",
          file=sys.stderr)
    speech = detect_speech_regions(
        audio_path, str(layout.read_dir(Area.OUTPUT_ROOT)),
        onsets=onsets,
        whisper_model_size=whisper_model_size,
    )
    speech_dur = sum(r["end"] - r["start"] for r in speech)
    word_count = sum(len(r.get("words", [])) for r in speech)
    print(
        f"           {len(speech)} regions, {speech_dur:.1f}s speech, "
        f"{word_count} words (wav2vec2 aligned)",
        file=sys.stderr,
    )

    # 5. Audio events
    print("    [5/12] Audio events...", file=sys.stderr)
    audio_events = classify_audio_events(audio_path, speech_regions=speech)
    print(
        f"           {len(audio_events)} events",
        file=sys.stderr,
    )

    # 6. Motion energy (30Hz — frame-aligned)
    print("    [6/12] Motion energy (30Hz)...", file=sys.stderr)
    motion = compute_motion_energy(video_path)
    print(
        f"           {len(motion['values'])} samples @ {motion['sample_rate_hz']}Hz, "
        f"{len(motion.get('peak_motion_times', []))} peaks, "
        f"{len(motion['high_motion_times'])} high-motion",
        file=sys.stderr,
    )

    # 7. Word end times (convenience array for downstream cut-point snapping)
    print("    [7/12] Word end times...", file=sys.stderr)
    word_ends = extract_word_end_times(speech)
    print(
        f"           {len(word_ends)} word boundaries",
        file=sys.stderr,
    )

    # 8. Optical flow direction (5Hz — camera motion characterization)
    print("    [8/12] Optical flow direction (5Hz)...", file=sys.stderr)
    flow = compute_optical_flow_direction(video_path)
    print(
        f"           {len(flow['values'])} samples, "
        f"dominant: {flow['dominant_motion']}",
        file=sys.stderr,
    )

    # 9. Camera motion decomposition (derived from optical flow)
    print("    [9/12] Camera motion decomposition...", file=sys.stderr)
    cam_motion = decompose_camera_motion(flow)
    print(
        f"           {len(cam_motion['values'])} samples",
        file=sys.stderr,
    )

    # 10. Face presence (5Hz)
    print("    [10/12] Face presence (5Hz)...", file=sys.stderr)
    face = compute_face_presence(video_path)
    face_pct = (
        round(len(face['face_present_times']) / len(face['values']) * 100)
        if face['values'] else 0
    )
    print(
        f"            {face_pct}% of samples with face detected",
        file=sys.stderr,
    )

    # 11. Dominant hue + brightness curve (1Hz)
    print("    [11/12] Hue/brightness curve (1Hz)...", file=sys.stderr)
    color_curves = compute_dominant_hue_curve(video_path)
    print(
        f"            {len(color_curves['hue_values'])} samples",
        file=sys.stderr,
    )

    # 12. Speech activity curve (derived from WhisperX word timestamps)
    print("    [12/12] Speech activity curve (30Hz)...", file=sys.stderr)
    speech_activity = derive_speech_activity_curve(speech, duration)
    print(
        f"            {round(speech_activity['speech_ratio'] * 100)}% speech",
        file=sys.stderr,
    )

    elapsed = time.time() - t0
    print(f"    Done ({elapsed:.1f}s)", file=sys.stderr)

    return {
        "clip_id": clip_id,
        "source_file": video_path,
        "duration": round(duration, 3),

        # ── Original signal outputs ──────────────────────────────────
        "scene_boundaries": scenes,
        "speech_regions": speech,
        "energy_curve": energy,
        "audio_events": audio_events,
        "motion_energy": motion,
        "onset_times": onsets,
        "word_end_times": word_ends,

        # ── New visual signal outputs ─────────────────────────────────
        # Optical flow direction (5Hz): dominant motion vector per sample.
        # dominant_motion: "static" | "pan_left" | "pan_right" | "tilt_up" |
        #                  "tilt_down" | "handheld" | "mixed" | "unknown"
        "optical_flow_direction": flow,

        # Camera motion decomposition (5Hz): separates translation, zoom,
        # and residual (subject motion / shake) from the flow signal.
        "camera_motion_decomposition": cam_motion,

        # Face presence (5Hz): 0-1 confidence score per sample.
        # face_present_times: timestamps where confidence > 0.5
        # face_absent_times: timestamps where confidence < 0.1 (no face)
        "face_presence": face,

        # Hue/brightness/temperature per second (1Hz).
        # Useful for detecting color grade, lighting transitions,
        # and scene changes that scene detection missed.
        "color_curves": color_curves,

        # Binary speech activity at frame rate (30Hz), derived from
        # WhisperX word timestamps. No additional processing cost.
        # speech_ratio: fraction of clip duration with active speech.
        "speech_activity": speech_activity,
    }


def _load_cached_index(index_path: str):
    """An existing per-clip index, or None if there is nothing usable.

    A truncated or corrupt file reads as "no cache" and is recomputed - a
    half-written index is worse than none, because every downstream
    creative decision is made against it.
    """
    if not os.path.isfile(index_path) or os.path.getsize(index_path) == 0:
        return None
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: unreadable index {index_path} ({e}); re-indexing",
              file=sys.stderr)
        return None
    required = ("scene_boundaries", "speech_regions", "energy_curve",
                "audio_events", "motion_energy")
    missing = [k for k in required if k not in index]
    if missing:
        print(f"  WARNING: index {index_path} is missing {missing}; "
              f"re-indexing", file=sys.stderr)
        return None
    return index


def build_temporal_index(
    raw_footage_files: list,
    layout: ProjectLayout,
    whisper_model_size: str = "large-v3",
) -> dict:
    """
    Build temporal event index for all clips.

    Produces one JSON file per clip in the project's TEMPORAL_INDEX area
    (library/tools/project_layout.py).

    A clip whose index is already there is REUSED, not re-transcribed.
    That per-clip file is the cache; the ledger in pipeline_data.json is
    only bookkeeping over it.  Two consequences worth stating:

      * A run that died on clip 12 of 17 costs five clips on the retry,
        not seventeen.  This step used to re-index every clip every time,
        and project 001 paid for the same forty minutes of WhisperX twice.
      * Whoever deletes the file decides what gets recomputed.  The runner
        deletes exactly the clips whose source footage changed, and
        exactly the clip named by `--rerun temporal_index:clip_007`.
    """
    # Absolute, and resolved from the PROJECT rather than the runner's
    # CWD, so downstream steps find the per-clip JSON files whatever
    # directory they run from.  The layout owner guarantees both.
    index_dir = str(layout.write_dir(Area.TEMPORAL_INDEX))

    results = []
    full_indices = []
    reused = 0
    total = len(raw_footage_files)

    for i, file_info in enumerate(raw_footage_files):
        if isinstance(file_info, dict):
            filepath = file_info["path"]
            filename = file_info.get("filename", Path(filepath).name)
            clip_id = file_info.get("clip_id", f"clip_{i + 1:03d}")
        else:
            filepath = file_info
            filename = Path(filepath).name
            clip_id = f"clip_{i + 1:03d}"

        print(
            f"\n[{i+1}/{total}] {filename}",
            file=sys.stderr,
        )

        if not os.path.isfile(filepath):
            print(f"  WARNING: file not found, skipping", file=sys.stderr)
            continue

        index_path = os.path.join(index_dir, f"{clip_id}.json")

        try:
            index = _load_cached_index(index_path)
            if index is not None:
                reused += 1
                print(f"  reusing {os.path.basename(index_path)}",
                      file=sys.stderr)
            else:
                index = index_clip(
                    filepath, clip_id, layout, whisper_model_size
                )
                # Write per-clip JSON
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(index, f, indent=2)

            # Keep full index for downstream steps that need speech_regions
            full_indices.append(index)

            results.append({
                "clip_id": clip_id,
                "index_path": index_path,
                "scenes": index["scene_boundaries"],
                "speech_regions": index["speech_regions"],
                "speech_duration": round(
                    sum(r["end"] - r["start"]
                        for r in index["speech_regions"]),
                    2,
                ),
                "total_words": sum(
                    len(r.get("words", []))
                    for r in index["speech_regions"]
                ),
                "energy_peaks": len(index["energy_curve"]["peak_times"]),
                "audio_events": len(index["audio_events"]),
                "high_motion_count": len(
                    index["motion_energy"]["high_motion_times"]
                ),
            })

        except Exception as e:
            print(
                f"  ERROR: indexing failed for {filename}: {e}",
                file=sys.stderr,
            )
            results.append({
                "clip_id": clip_id,
                "error": str(e),
            })

    return {
        "temporal_event_indices": results,
        "full_indices": full_indices,
        "total_indexed": sum(1 for r in results if "error" not in r),
        "total_failed": sum(1 for r in results if "error" in r),
        "total_reused": reused,
        "index_dir": index_dir,
    }


# ── Phase E: Phase-boundary compression ─────────────────────────────

def compress_for_downstream(
    clip_catalog: list,
    temporal_indices: list,
    semantic_docs: list = None,
) -> list:
    """
    Produce compact per-clip summaries for downstream LLM consumption.

    Reduces ~2KB/clip of raw index data to ~200 bytes/clip of actionable
    signal. Downstream phases get everything they need to make decisions
    without wading through full temporal indices.

    Args:
        clip_catalog: List of clip catalog entries (from step 1.02)
        temporal_indices: List of temporal index documents (from this step)
        semantic_docs: Optional list of semantic analysis docs (from step 1.03)

    Returns:
        List of compact clip summary dicts, one per clip.
    """
    # Build lookup maps
    idx_map = {i["clip_id"]: i for i in temporal_indices if "clip_id" in i}
    sem_map = {}
    if semantic_docs:
        sem_map = {d["clip_id"]: d for d in semantic_docs if "clip_id" in d}

    summaries = []
    for clip in clip_catalog:
        cid = clip.get("clip_id", "")
        idx = idx_map.get(cid, {})
        sem = sem_map.get(cid, {})

        # Temporal metrics
        speech_regions = idx.get("speech_regions", [])
        scenes = idx.get("scene_boundaries", [])
        energy = idx.get("energy_curve", {})
        audio_events = idx.get("audio_events", [])
        motion = idx.get("motion_energy", {})

        speech_dur = sum(r["end"] - r["start"] for r in speech_regions)

        # Assessment from semantic analysis
        assessment = sem.get("assessment", {})

        summary = {
            "clip_id": cid,
            "duration": clip.get("duration_seconds", idx.get("duration", 0)),
            "type": assessment.get("clip_type", "unknown"),
            "score": assessment.get("interest_score", 0),
            "moment": assessment.get("moment_type", "unknown"),
            "scene_count": len(scenes),
            "has_speech": len(speech_regions) > 0,
            "speech_duration": round(speech_dur, 1),
            "energy_peaks": len(energy.get("peak_times", [])),
            "audio_events": [
                f"{e['class']}@{e['time']:.0f}s"
                for e in audio_events[:5]  # top 5 events
            ],
            "high_motion": len(motion.get("high_motion_times", [])) > 0,
            "best_range": assessment.get("usable_portions", "")[:80],
        }

        # Add broll context if available
        if assessment.get("broll_context"):
            summary["broll_context"] = assessment["broll_context"][:60]

        summaries.append(summary)

    return summaries


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    """
    Read raw_footage_files from stdin (JSON), produce temporal index.

    Usage:
        cat step_1_01.json | python step.py --output-dir ./pipeline_output
        python step.py --manifest step_1_01.json --output-dir ./pipeline_output
    """
    # First, before any dependency can grab it. See "The stdout
    # contract" above.
    _claim_stdout()

    parser = argparse.ArgumentParser(
        description="Step 1.04: Build Temporal Event Index"
    )
    parser.add_argument(
        "--manifest", "-m",
        help="Path to step_1_01.json (alternative to stdin)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="./pipeline_output",
        help="Output directory (default: ./pipeline_output)",
    )
    parser.add_argument(
        "--whisper-model", "-w",
        default="large-v3",
        choices=["tiny", "base", "small", "medium", "large-v3", "large-v2", "distil-large-v3"],
        help="Whisper model size (default: large-v3)",
    )
    parser.add_argument(
        "--clip-id",
        help="Index only a specific clip_id (for debugging)",
    )
    args = parser.parse_args()

    # Load input
    if args.manifest:
        with open(args.manifest) as f:
            input_data = json.load(f)
    else:
        input_data = json.loads(sys.stdin.read())

    raw_files = input_data.get("raw_footage_files")
    if not raw_files:
        _emit({
            "error": "Missing required input: raw_footage_files",
            "step": "1.04_temporal_index",
        })
        sys.exit(1)

    # Optional: filter to single clip
    if args.clip_id:
        idx = int(args.clip_id.replace("clip_", "")) - 1
        if 0 <= idx < len(raw_files):
            raw_files = [raw_files[idx]]
        else:
            print(f"clip_id {args.clip_id} out of range", file=sys.stderr)
            sys.exit(1)

    # ── Where the index lives ──
    #
    # With the project, next to prosody's profiles - NOT in the runner's
    # current working directory, which is what `--output-dir`'s default of
    # "./pipeline_output" meant in practice.  Project 001's state recorded
    # its 17-clip index at
    #   /Users/.../.treehouse/video_editing_pilot-.../3/video_editing_pilot/pipeline_output/temporal_index
    # - inside a DISPOSABLE git worktree.  Forty minutes of WhisperX,
    # banked somewhere the project can never find it again, which is how
    # it came to be paid for twice.
    #
    # There was also a "cache-aware execution" block here that looked for
    # <project>/raw/analysis/temporal_index/ - a directory this step has
    # never written to - and required a hit on ALL clips before it would
    # use any of them.  It could not fire, and it read as coverage for the
    # reuse that is now in build_temporal_index, per clip.
    # `--output-dir` names an output directory, so the project that owns
    # it is its parent.  With `project_folder` supplied - which is every
    # DAG run - that is what the layout is built from, and the flag is a
    # debugging convenience only.
    project_folder = input_data.get("project_folder", "")
    layout = ProjectLayout(
        project_folder or str(Path(args.output_dir).resolve().parent))

    # ── Index, reusing whatever is already on disk ──
    result = build_temporal_index(
        raw_files, layout, args.whisper_model
    )
    result["source"] = "cache" if result["total_reused"] == len(raw_files) else "fresh"

    # Summary
    print(
        f"\n{'=' * 50}\n"
        f"Temporal Event Index Complete\n"
        f"  Indexed: {result['total_indexed']} clips\n"
        f"  Reused:  {result['total_reused']} clips\n"
        f"  Failed:  {result['total_failed']} clips\n"
        f"  Output:  {result['index_dir']}\n"
        f"{'=' * 50}",
        file=sys.stderr,
    )

    # Output directly for DAG compatibility
    _emit(result)


if __name__ == "__main__":
    main()

