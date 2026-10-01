#!/usr/bin/env python3
"""
Step 1.04: Temporal Event Index

Deterministic pre-processing step that extracts timestamped events from
each video clip using signal-processing tools. It runs after step 1.03
because regional motion is restricted to Gemma's time-bounded action
labels; the other temporal measurements retain their existing methods.

Produces a per-clip JSON index containing:
  - scene_boundaries: visual cut/change points (ffmpeg scene detection)
  - speech_regions: start/end of speech with ASR transcript + word-level
    timestamps (the reel path's own transcription seam: the on-device
    transcriber's words through MFA forced alignment where its
    environment is present, wav2vec2 where MFA declines, full
    WhisperX where the hybrid cannot answer at all)
  - energy_curve: per-second RMS audio energy (librosa, 30Hz frame-aligned)
  - audio_events: classified audio events - measured PANNs
    non-speech labels plus measured energy quiet (the old
    zero-crossing `ambient_noise` guess is gone)
  - sound_events / sound_event_method: the PANNs event layer proper
    (AudioSet labels, spans, confidences) beside the method that says
    whether anything was measured - what event anchors resolve
    against (`library/tools/sound_events.py`)
  - motion_energy: per-second visual motion magnitude (frame differencing,
    30Hz frame-aligned)
  - regional_motion: 10Hz, 640x360 Farneback tracks and advisory crop /
    keep-clear evidence, measured only over time-bounded Gemma action spans

The index bridges the gap between semantic analysis (knows WHAT happens)
and timeline assembly (needs WHEN things happen).

Speech detection calls the SAME seam the reel builds transcribe through
(`library/tools/timeline_transcript.transcribe_audio`, adopted there in
PR 1180), rather than a copy of it - so the edit pipeline's words and
the reels' words are timed by the same instruments, and every index
says which one answered (`transcription_arm` / `transcription_aligner`
on the document, `method` on each region). A cached index that predates
the stamp is stamped as legacy WhisperX on read and served - only an
explicit `ren reindex` moves a project onto Voz.
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
    - the on-device transcriber (`da`) and MFA: they are the only
      transcription path since the whisperx fallback arms left on
      2026-09-24


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**Face frames are sampled at the CLIP'S OWN aspect, never a fixed shape.**
`face_sample_dimensions` reads the DISPLAY shape (rotation side data applied, because autorotate runs before the filter chain), bounds the SHORT side to `FACE_SAMPLE_SHORT_SIDE`, and raises rather than falling back to a shape.
Anything derived from the sample size - `frame_area`, the `face_center_x` divisor - must read that size, not a literal. [why - the numbers, and what the fix does not fix](docs/RULE_EVIDENCE.md#the-squashed-face-frame)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


# This step's stdout is its JSON result, so nothing else may write to
# it - whisperx attaches a stdout log handler and silently corrupted a
# completed 17-clip index. The guard and the full story live in
# library/tools/step_stdout.py; `main()` calls _claim_stdout() first.
from library.tools.step_stdout import claim_stdout as _claim_stdout, emit as _emit
from library.tools.project_layout import Area, ProjectLayout
from library.tools.subject_framing import load_face_cascade
from library.tools.word_boundaries import (
    sanitize_word_boundaries as _sanitize_word_boundaries,
)


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


# ── 2. Speech region detection (Voz + MFA) ──
#
# The transcription itself lives in
# `library/tools/timeline_transcript.transcribe_audio` - the seam the
# reel builds transcribe through, adopted there in PR 1180: the
# on-device transcriber's words (`da`) through MFA. The WhisperX
# fallback arms are gone with the whisperx pin since 2026-09-24, so a
# hybrid refusal on speech-bearing audio is a loud per-clip warning,
# not a second transcription. This step calls that seam rather than
# keeping its own transcriber (the old faster-whisper +
# wav2vec2 direct call is gone with `_get_whisperx_models`), so the
# edit pipeline's words and the reels' words are timed by the same
# instruments. What this step still owns is everything AFTER the
# words: onset snapping, the shared boundary hygiene, and the region
# shape downstream reads.
#
# `TRANSCRIBED_ALIGNERS` is the cache contract beside the language
# check in `_load_cached_index`: an index is only reused when a real
# instrument timed it. An index that predates the stamp was timed by
# the old WhisperX path - an instrument, not nothing - so it is
# STAMPED as legacy on read and served, never silently re-transcribed
# behind the project's back; `ren reindex` moves a project onto Voz
# when asked. An index written when nothing could transcribe
# (`"none"`) is re-indexed - words timed by nothing are not words.

#: Aligners whose stamp means an index was really timed. The legacy
#: WhisperX path stamped nothing, so a missing stamp reads as this
#: arm's wav2vec2 - the instrument that ran, written down at last.
TRANSCRIBED_ALIGNERS = ("mfa", "wav2vec2")

#: The stamp a pre-stamp index is given on read: the old path forced
#: every clip through faster-whisper plus wav2vec2, so this is what
#: timed it, and the detected language is what it was forced to.
LEGACY_ARM = "whisperx"
LEGACY_ALIGNER = "wav2vec2"

#: What an untranscribed clip records where the instrument would be.
#: Not an aligner name, and that is the whole point: a null there
#: would read as "timed by the default", which is how a machine that
#: could not transcribe would pin every later run to empty words.
UNTRANSCRIBED = "none"


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


# ── Speech regions ─────────────────────────────────────────────────
#
# Word-boundary hygiene lives in `library/tools/word_boundaries.py` and
# is SHARED with the reels transcript path
# (`timeline_transcript.segments_for_speaker`): one clamp, both paths.
# This step calls it as `_sanitize_word_boundaries`, imported under
# that historical name at the top of this file, so existing references
# keep reading.


def _method_name(arm: str, aligner: str, whisper_model_size: str) -> str:
    """The region `method` for the instrument that answered.

    The hybrid arm names its aligner (`hybrid-mfa`,
    `hybrid-wav2vec2`); the full-WhisperX fallback keeps the string
    every index before this change carried, so a reader can tell
    which past a region comes from.
    """
    from library.tools import hybrid_transcription

    if arm == hybrid_transcription.ARM_HYBRID:
        return f"hybrid-{aligner}"
    return f"whisperx-wav2vec2-{whisper_model_size}"


def _untranscribed(whisper_model_size: str, language: str) -> dict:
    """The account a clip nothing could transcribe carries."""
    return {
        "arm": UNTRANSCRIBED,
        "aligner": UNTRANSCRIBED,
        "detected_language": language,
        "method": f"untranscribed-{whisper_model_size}",
    }


def _transcription_from_record(record: dict, whisper_model_size: str,
                               language: str) -> dict:
    """Which instrument answered, in the shape the index stores."""
    from library.tools import hybrid_transcription

    arm = (record or {}).get("arm")
    aligner = (record or {}).get("aligner")
    if arm == UNTRANSCRIBED:
        # The seam heard nothing and said so: keep the "none" stamp
        # rather than letting the legacy defaults below rewrite it
        # into an instrument that never ran.
        return _untranscribed(whisper_model_size, language)
    arm = arm or hybrid_transcription.ARM_WHISPERX
    aligner = aligner or hybrid_transcription.ALIGNER_WAV2VEC2
    detected = (record or {}).get("language") or {}
    detected_language = (detected.get("language") if isinstance(
        detected, dict) else None) or language
    return {
        "arm": arm,
        "aligner": aligner,
        "detected_language": detected_language,
        "method": _method_name(arm, aligner, whisper_model_size),
    }


def _regions_from_segments(segments: list, onsets: list,
                           method: str) -> list:
    """Aligned segments into the region shape downstream reads.

    One place, both transcription paths (the single-clip seam and the
    batched run below): onset snapping, the shared boundary hygiene,
    and the region keys. `confidence` reads the transcriber's own
    segment confidence where one is carried and 0.0 elsewhere - no
    remaining arm publishes one, so 0.0 is what every region holds;
    the same default an absent key always carried, never a measurement.
    """
    regions: list = []
    for segment in segments or []:
        text = segment.get("text", "").strip()
        if not text:
            continue

        words = []
        for w in segment.get("words", []):
            # An aligner may fail for some words (numbers,
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

        # Sanitize aligner artifacts (overlaps, long words, zero-duration)
        words = _sanitize_word_boundaries(words)

        regions.append({
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": text.lower().strip(),
            "words": words,
            "confidence": round(
                segment.get("avg_logprob", 0.0), 3
            ) if "avg_logprob" in segment else 0.0,
            "method": method,
        })
    return regions


def detect_speech_regions(
    audio_path: str,
    output_dir: str,
    onsets: list = None,
    whisper_model_size: str = "large-v3",
    language: str = "en",
) -> tuple:
    """Detect speech regions through the reel path's transcription seam.

    The words come from `timeline_transcript.transcribe_audio` - the
    on-device transcriber through MFA. The WhisperX fallback arms left
    with the whisperx pin on 2026-09-24, so a hybrid refusal is either
    silence (returned empty and attributed) or a loud per-clip warning
    caught below - there is no second transcription. Everything after
    the words is this step's own: word start times are snapped to the
    nearest audio onset (from librosa onset detection) when within
    30ms, giving consonant attacks sub-frame precision, and the shared
    boundary hygiene is applied.

    Returns `(regions, transcription)`:

      * `regions` - the list of speech region dicts downstream reads:
        [{
            "start": 0.8, "end": 3.14,
            "text": "i can feel the silent judgment",
            "words": [{"word": "i", "start": 0.80, "end": 0.92}, ...],
            "confidence": 0.92,
            "method": "hybrid-mfa"
        }, ...]
        `confidence` is 0.0: no remaining arm publishes the
        transcriber's own segment confidence (the full-WhisperX fallback
        was the one that did) - the same default an absent key always
        carried, never a measurement.
      * `transcription` - which instrument answered:
        `{"arm", "aligner", "detected_language", "method"}`.
        A clip nothing could transcribe returns `[]` with the arm and
        aligner as `"none"` - an empty region list is not a
        measurement of silence, and the stamp is what keeps the next
        run from serving it as one.

    `language` is the project's `source.language`, kept as the cache
    key it always was (`transcription_language` on the index). The
    language the words were actually heard in is reported back as
    `detected_language`: the seam identifies per file, the way the
    reel path does, rather than forcing every clip through the
    declared one.
    """
    from library.tools import timeline_transcript

    regions: list = []
    transcription = _untranscribed(whisper_model_size, language)

    try:
        aligned, record = timeline_transcript.transcribe_audio(
            Path(audio_path), model_size=whisper_model_size,
            label=Path(audio_path).name)
    except ImportError as e:
        print(
            f"  WARNING: transcriber not available ({e}), "
            "skipping speech detection",
            file=sys.stderr,
        )
        return regions, transcription
    except Exception as e:
        print(
            f"  WARNING: speech detection failed: {e}",
            file=sys.stderr,
        )
        import traceback
        traceback.print_exc(file=sys.stderr)
        return regions, transcription

    transcription = _transcription_from_record(
        record, whisper_model_size, language)

    segments = (aligned or {}).get("segments") or []
    if not segments:
        print("          (no speech detected)", file=sys.stderr)
        return regions, transcription

    return (_regions_from_segments(segments, onsets,
                                   transcription["method"]),
            transcription)


# ── Batched transcription: one MFA run per language per step run ──
#
# MFA pays ~35 s fixed per `mfa align` invocation (model load) against
# ~5 ms per audio-second marginal. Per-clip hybrid calls pay the fixed
# cost on EVERY clip - almost an hour on a 100-clip project, on the
# machine that is already the limiter. So the run path below hears
# each clip once (the transcriber is 275-344x realtime and stays
# per-clip), concatenates the clip audios, and aligns each language's
# windows in ONE aligner call. A clip the transcriber cannot hear, a
# language MFA has no model for, and any batch the aligner declines
# all take the per-clip seam (`transcribe_audio`) - the same fallback
# the single path takes, never a degraded batch answer.
#
# The split-back is by construction: MFA's merge and whisperx's align
# both return one segment per input window in input order, so windows
# [i:j] of the batch are clip windows [i:j] and every time is moved
# back onto the clip's own clock by the concat offset.

def _concat_wavs(audio_paths: list, scratch_dir: str) -> str:
    """Many 16 kHz mono wavs as one, with each file's start offset.

    Returns `(concat_path, [(audio_path, offset_seconds)])`. A file
    whose params differ from the first is refused rather than
    resampled in silence - resampling inside a measurement is how a
    second clock gets in.
    """
    import wave

    params = None
    spans = []
    frames_all = []
    cursor = 0.0
    for audio_path in audio_paths:
        with wave.open(audio_path, "rb") as wav:
            these = (wav.getnchannels(), wav.getsampwidth(),
                     wav.getframerate())
            if params is None:
                params = these
                if params != (1, 2, 16000):
                    raise ValueError(
                        f"concat refuses {audio_path}: expected 16 kHz "
                        f"mono s16le, found {these}. The batch aligns "
                        f"one clock, not three.")
            elif these != params:
                raise ValueError(
                    f"concat refuses {audio_path}: params {these} "
                    f"against {params}. Batch members that fall out "
                    f"take the per-clip seam instead.")
            frames = wav.readframes(wav.getnframes())
        spans.append((audio_path, round(cursor, 3)))
        frames_all.append(frames)
        cursor += len(frames) / (params[0] * params[1] * params[2])
    out_path = os.path.join(scratch_dir, "ingest_batch.wav")
    with wave.open(out_path, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        for frames in frames_all:
            out.writeframes(frames)
    return out_path, spans


def transcribe_clips_batched(requests: list,
                             whisper_model_size: str = "large-v3",
                             language: str = "en") -> dict:
    """Hear every clip once, align each language's windows in one run.

    `requests` are `{"key", "audio_path"}` (plus per-request
    `"onsets"`); returns `{key: (regions, transcription)}` for every
    key it was given. The single-clip seam stays the fallback for any
    clip the batch cannot carry, and `detect_speech_regions` stays
    the single-shot entry - this is the run path's bulk door, not a
    second transcriber.
    """
    from library.tools import heard_speech, hybrid_transcription, mfa_align
    from library.tools import timeline_transcript
    from library.tools import shared_environment

    # Phase 1, still per clip: Voz writes the words first, then `da ear`
    # identifies language from the most continuous window of those words.
    # This prevents a silent or music lead-in from choosing the MFA model.
    # The windows are then offset into the concat.
    heard = []       # (key, audio_path, onsets, detected, windows)
    fallback_keys = []
    for request in requests or []:
        key = request["key"]
        try:
            spoken = heard_speech.transcribe(request["audio_path"])
        except heard_speech.TranscriberUnavailable as unheard:
            print(f"  {key}: transcriber unavailable ({unheard}); "
                  f"per-clip fallback", file=sys.stderr)
            fallback_keys.append(key)
            continue
        windows = hybrid_transcription.alignment_windows(spoken)
        if not windows:
            # No words: the seam would raise HEARD_NOTHING and take
            # the full fallback, so the batch sends it there directly.
            fallback_keys.append(key)
            continue
        try:
            detected = hybrid_transcription.identify_speech_language(
                request["audio_path"], windows).language
        except hybrid_transcription.FallbackRequired as declined:
            print(f"  {key}: speech language identification declined "
                  f"({declined.reason}); per-clip fallback",
                  file=sys.stderr)
            fallback_keys.append(key)
            continue
        heard.append((key, request["audio_path"],
                      request.get("onsets"), detected, windows))

    # Phase 2: one concat, one aligner run per batched language.
    results: dict = {}
    if heard:
        mfa_here, _ = shared_environment.mfa_available()
        batchable = [row for row in heard
                     if mfa_here and mfa_align.covers(row[3])]
        per_clip = [row for row in heard if row not in batchable]
        for row in per_clip:
            fallback_keys.append(row[0])
        if batchable:
            import tempfile
            with tempfile.TemporaryDirectory(
                    prefix="ingest_batch_") as scratch:
                paths = [row[1] for row in batchable]
                try:
                    concat, spans = _concat_wavs(paths, scratch)
                except ValueError as refused:
                    print(f"  batch concat refused ({refused}); "
                          f"per-clip fallback", file=sys.stderr)
                    fallback_keys.extend(row[0] for row in batchable)
                    batchable = []
                if batchable:
                    offsets = {path: offset for path, offset in spans}
                    by_language: dict = {}
                    for key, path, onsets, detected, windows in batchable:
                        offset = offsets[path]
                        shifted = []
                        for window in windows:
                            row = dict(window, start=window["start"] + offset,
                                       end=window["end"] + offset)
                            sources = row.get("source_words")
                            if isinstance(sources, list):
                                # The merge falls back to these spans for
                                # a window no pass can align, so they
                                # ride the concat clock like the window
                                # itself - unshifted, the split-back
                                # below would subtract the offset from
                                # the wrong clock.
                                row["source_words"] = [
                                    dict(span,
                                         start=span["start"] + offset,
                                         end=span["end"] + offset)
                                    if isinstance(span, dict)
                                    and isinstance(span.get("start"),
                                                   (int, float))
                                    and isinstance(span.get("end"),
                                                   (int, float))
                                    else span
                                    for span in sources
                                ]
                            shifted.append(row)
                        by_language.setdefault(detected, []).append(
                            (key, path, onsets, shifted))
                    # The reel path's own aligner (MFA, the only one
                    # since whisperx left) - the batch is timed by the
                    # same instrument the single path is.
                    aligner = timeline_transcript._aligner()
                    for group_language, group in by_language.items():
                        group_windows = [w for _, _, _, ws in group
                                         for w in ws]
                        try:
                            aligned = aligner.align(
                                group_windows, group_language, concat)
                            hybrid_transcription._check_alignment(aligned)
                        except hybrid_transcription.FallbackRequired as declined:
                            print(f"  batch aligner declined "
                                  f"({declined.reason}); per-clip "
                                  f"fallback", file=sys.stderr)
                            fallback_keys.extend(
                                key for key, _, _, _ in group)
                            continue
                        segments = aligned.get("segments") or []
                        cursor = 0
                        for key, path, onsets, ws in group:
                            own = segments[cursor:cursor + len(ws)]
                            cursor += len(ws)
                            offset = offsets[path]
                            moved = []
                            for segment in own:
                                row = dict(segment)
                                row["start"] = segment["start"] - offset
                                row["end"] = segment["end"] - offset
                                row["words"] = [
                                    dict(w, start=w["start"] - offset,
                                         end=w["end"] - offset)
                                    for w in segment.get("words") or []
                                    if isinstance(w, dict)
                                    and "start" in w and "end" in w]
                                moved.append(row)
                            transcription = _transcription_from_record(
                                {"arm": hybrid_transcription.ARM_HYBRID,
                                 "aligner": aligned.get(
                                     "aligner",
                                     hybrid_transcription.ALIGNER_WAV2VEC2),
                                 "language": {"language": group_language,
                                              "confidence": None}},
                                whisper_model_size, language)
                            results[key] = (
                                _regions_from_segments(
                                    moved, onsets,
                                    transcription["method"]),
                                transcription)

    # Phase 3: every clip the batch did not carry takes the seam.
    by_path = {request["key"]: request for request in requests or []}
    for key in fallback_keys:
        request = by_path[key]
        regions, transcription = detect_speech_regions(
            request["audio_path"],
            os.path.dirname(request["audio_path"]),
            onsets=request.get("onsets"),
            whisper_model_size=whisper_model_size,
            language=language)
        results[key] = (regions, transcription)
    return results


# ── Moving a project onto Voz: the explicit reindex ──────────────
#
# Pre-stamp indexes are served as legacy, never re-transcribed
# unasked. When the project is asked - `ren reindex` - the legacy
# files are invalidated (deleted; they are derived cache, recomputed
# on the next run) and reported by name, so the move states what it
# moved. Files already timed by a current instrument are untouched,
# and files this function does not recognise are left alone.

def invalidate_legacy_indexes(project_folder: str) -> dict:
    """Delete temporal-index files no current instrument timed.

    Returns `{"invalidated", "current", "unrecognized"}` (basenames).
    "Invalidated" is legacy (missing stamp, or the old WhisperX arm),
    untranscribed (`"none"`, retried on the next run) and corrupt
    (unreadable cache is recomputed, never served). Deleting is the
    whole move: the next run re-transcribes exactly these clips.
    """
    from library.tools.project_layout import Area, ProjectLayout

    report = {"invalidated": [], "current": [], "unrecognized": []}
    try:
        layout = ProjectLayout(project_folder)
        index_dir = str(layout.write_dir(
            Area.TEMPORAL_INDEX, step="temporal_index"))
    except (OSError, ValueError):
        return report
    if not os.path.isdir(index_dir):
        return report
    for name in sorted(os.listdir(index_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(index_dir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            os.remove(path)
            report["invalidated"].append(name)
            continue
        if not isinstance(document, dict) or "speech_regions" not in document:
            report["unrecognized"].append(name)
            continue
        aligner = document.get("transcription_aligner")
        arm = document.get("transcription_arm")
        if aligner in TRANSCRIBED_ALIGNERS and arm != LEGACY_ARM:
            report["current"].append(name)
            continue
        os.remove(path)
        report["invalidated"].append(name)
    return report


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


def measure_clip_sound_events(audio_path: str) -> tuple:
    """Measured non-speech sound events for one clip's index audio.

    Returns `(sound_events, method)`: the PANNs event list
    (`library/tools/analysis/sound_event_pipeline.py`, each
    `{"label", "start", "end", "confidence"}` in source seconds) and
    the producer method name - or `([], "unmeasured: <reason>")` where
    the checkpoint is not on this machine. An unmeasured clip refuses
    event anchors by name; it never serves heuristic guesses.
    """
    from library.tools.analysis import sound_event_pipeline as _sep

    try:
        measured = _sep.measure_sound_events(audio_path)
        return measured["events"], measured["method"]
    except _sep.SoundEventsUnavailable as e:
        print(f"  INFO: sound events unmeasured: {e}", file=sys.stderr)
        return [], f"unmeasured: {e}"
    except Exception as e:
        print(
            f"  WARNING: sound-event measurement failed: {e}",
            file=sys.stderr,
        )
        return [], f"unmeasured: {e}"


def _energy_silence_spans(audio_path: str,
                          speech_regions: list = None,
                          sample_rate_hz: int = 2) -> list:
    """Measured quiet spans: RMS under threshold, outside word spans.

    The one heuristic the old classifier carried that was a real
    signal - silence is low energy, honestly measured - kept as its
    own function with its own method tag. Word-overlapping quiet is
    filtered out exactly as before: a pause inside speech is the
    transcript's business, not silence.
    """
    import librosa
    y, sr = librosa.load(audio_path, sr=16000)
    hop = sr // sample_rate_hz
    rms = librosa.feature.rms(
        y=y, frame_length=hop * 2, hop_length=hop
    )[0]
    spans = []
    for i, val in enumerate(rms):
        if val < 0.005:
            spans.append({
                "time": round(i / sample_rate_hz, 2),
                "duration": round(1.0 / sample_rate_hz, 2),
                "class": "silence",
                "confidence": 0.8,
                "method": "energy",
            })
    events = _consolidate_events(spans)
    if speech_regions and events:
        word_spans = []
        for region in (speech_regions or []):
            for w in region.get("words", []):
                word_spans.append((w["start"], w["end"]))
        if word_spans:
            filtered = []
            for event in events:
                e_start = event["time"]
                e_end = e_start + event["duration"]
                overlaps_word = any(
                    min(e_end, we) - max(e_start, ws) > 0.05
                    for ws, we in word_spans
                )
                if overlaps_word:
                    continue
                filtered.append(event)
            events = filtered
    return events


def classify_audio_events(
    audio_path: str,
    speech_regions: list = None,
    min_confidence: float = 0.3,
    sample_rate_hz: int = 2,
    sound_measurement: tuple = None,
) -> list:
    """
    Classify audio events: measured PANNs labels plus measured quiet.

    The PANNs half names non-speech sounds (laughter, impacts, music)
    with timed spans in the model's own AudioSet vocabulary
    (`library/tools/analysis/sound_event_pipeline.py`); the energy
    half names quiet spans outside word time. The old zero-crossing
    `ambient_noise` guess is gone - nothing here thresholds a texture
    into a label. `min_confidence` and `sample_rate_hz` are kept so
    existing callers read unchanged; the producer owns its operating
    points. `sound_measurement` is the `(events, method)` pair a
    caller that already measured for its own document hands over, so
    one clip costs one inference; None measures here.

    Returns a list of audio event dicts:
        [{"time": 3.1, "duration": 0.8, "class": "Laughter",
          "confidence": 0.7, "method": "panns-..."}, ...]
    """
    _ = min_confidence
    events = []
    if sound_measurement is None:
        sound_measurement = measure_clip_sound_events(audio_path)
    sound_events, _method = sound_measurement
    for event in sound_events:
        events.append({
            "time": event["start"],
            "duration": round(event["end"] - event["start"], 3),
            "class": event["label"],
            "confidence": event["confidence"],
            "method": _method,
        })
    try:
        events.extend(
            _energy_silence_spans(audio_path, speech_regions,
                                  sample_rate_hz))
    except Exception as e:
        print(
            f"  WARNING: energy silence spans failed: {e}",
            file=sys.stderr,
        )
    events.sort(key=lambda e: (e["time"], e["duration"]))
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


# ── 8. Dense motion field (5Hz) ────────────────────────────────────────

#: Proxy size the flow is measured on. 160x90 keeps a minute of 5 Hz
#: pairs under a second of Farneback; the vectors are normalized by
#: `FLOW_NORM_PX` so no number names this size.
FLOW_PROXY_W = 160
FLOW_PROXY_H = 90

#: What a normalized magnitude of 1.0 means: 8 proxy-px of displacement
#: between two samples (0.2 s apart at 5 Hz). The same divisor the old
#: block matcher used, so magnitudes stay comparable across methods.
FLOW_NORM_PX = 8.0

#: Below this normalized magnitude a sample is stillness, not a direction.
FLOW_STATIC_MAG = 0.05

#: A zoom reads as radial flow about the frame centre. The clip-level
#: verdict says zoom only when the mean radial component clears this
#: floor AND carries at least `DIVERGENCE_ZOOM_RATIO` of the mean
#: magnitude - conservative on purpose, because a dolly past a textured
#: wall expands too, and the legend says so.
DIVERGENCE_ZOOM_MIN = 0.08
DIVERGENCE_ZOOM_RATIO = 0.5

#: Motion-peak detection on the magnitude curve. Apexes are scipy local
#: maxima with this prominence, at least `PEAK_MIN_SEPARATION_S` apart,
#: and at or above the onset threshold - a wobble below it is ripple,
#: not action. Onsets are rising-edge crossings of the onset threshold
#: with the same separation and `ONSET_HYSTERESIS` of re-arm: after an
#: onset fires, the curve must dip that far back under the threshold
#: before the next one can fire, so a curve hovering ON the threshold
#: reads as one sustained action rather than one onset per wobble.
#: An onset needs a below-threshold predecessor inside the clip - motion
#: already in progress on the first sample is never given a guessed
#: start, and a clip opening above the threshold arms only after a
#: real dip below the re-arm level.
APEX_PROMINENCE = 0.15
PEAK_MIN_SEPARATION_S = 0.5
ONSET_FLOOR = 0.20
ONSET_HYSTERESIS = 0.05


def _compass(dx: float, dy: float) -> str:
    """Eight-way direction of a normalized (dx, dy) vector.

    Screen coordinates: dx > 0 is rightward on screen, dy > 0 is
    downward. "up" therefore means dy < 0. Pure helper, no thresholds -
    the caller decides what counts as stillness.
    """
    import math

    names = ["right", "up-right", "up", "up-left",
             "left", "down-left", "down", "down-right"]
    angle = math.degrees(math.atan2(-dy, dx))
    return names[int(round(angle / 45.0)) % 8]


def _direction_of(med_x: float, med_y: float, mean_mag: float) -> str:
    """The direction label for one translation-plus-spread reading.

    `static` below the stillness floor; `mixed` where the field moves
    but has no dominant translation (a zoom's median is ~0 while its
    mean is not - compassing that noise would present a direction
    nothing decided); otherwise the median's eight-way compass.
    """
    import math

    if mean_mag < FLOW_STATIC_MAG:
        return "static"
    med_mag = math.sqrt(med_x ** 2 + med_y ** 2)
    if med_mag < 0.3 * mean_mag:
        return "mixed"
    return _compass(med_x, med_y)


def _extract_gray_proxy(
    video_path: str,
    sample_rate_hz: int,
) -> "object | None":
    """Low-resolution grayscale frames at the sample rate, or None.

    One ffmpeg pass at `FLOW_PROXY_W`x`FLOW_PROXY_H`; None when ffmpeg
    fails or yields nothing, so the caller reports unmeasured rather
    than measuring zero frames. The array shape is (n, H, W) uint8.
    """
    import numpy as np

    result = subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vf", (f"fps={sample_rate_hz},scale="
                    f"{FLOW_PROXY_W}:{FLOW_PROXY_H},format=gray"),
            "-f", "rawvideo", "-pix_fmt", "gray",
            "-v", "quiet",
            "-",
        ],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    frame_size = FLOW_PROXY_W * FLOW_PROXY_H
    raw = np.frombuffer(result.stdout, dtype=np.uint8)
    n_frames = len(raw) // frame_size
    if n_frames == 0:
        return None
    return raw[:n_frames * frame_size].reshape(
        n_frames, FLOW_PROXY_H, FLOW_PROXY_W)


def _block_match_shift(prev, curr) -> "tuple | None":
    """The content motion between two proxy frames, or None.

    Mean absolute difference over shifts of -8..8 px in steps of 2 -
    the estimator this step used before dense flow landed. Returns
    `(dx, dy)` in proxy px, content-motion sign (rightward content
    movement reads positive dx, matching the Farneback median): the
    search finds the shift that aligns curr back onto prev, which
    points the OTHER way, so it is negated before it leaves. Returns
    None when no shift candidate survives, so the pair contributes no
    vector rather than a zero one (AGENTS.md 10.3 - a default
    presented as a measurement).
    """
    import numpy as np

    height, width = prev.shape
    best_dx, best_dy = 0, 0
    best_score = float("inf")
    for dy in range(-8, 9, 2):
        for dx in range(-8, 9, 2):
            # Shift curr by (dx, dy) and compare to prev
            if dy >= 0:
                p_rows = slice(dy, None)
                c_rows = slice(None, height - dy if dy > 0 else None)
            else:
                p_rows = slice(None, height + dy)
                c_rows = slice(-dy, None)
            if dx >= 0:
                p_cols = slice(dx, None)
                c_cols = slice(None, width - dx if dx > 0 else None)
            else:
                p_cols = slice(None, width + dx)
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
    if best_score == float("inf"):
        return None
    # Content-motion sign: the search aligns curr back onto prev, so
    # its shift points against the movement. Negated, rightward
    # content motion reads positive dx - the same sign the Farneback
    # median carries, and the one the classifier's pan labels assume.
    return (-best_dx, -best_dy)


def _farneback_pair_stats(prev, curr) -> dict:
    """Dense Farneback field between two proxy frames, as sample stats.

    Returns the per-sample measurement WITHOUT its timestamp (the
    caller stamps it): normalized median translation (`dx`, `dy`),
    mean-field `magnitude`, eight-way `direction`, the global shift
    read as the camera hypothesis (`camera_tx`, `camera_ty`), the
    motion the global shift does NOT explain (`subject_energy`), and
    the mean radial component about the frame centre (`divergence`,
    positive = expansion: a zoom or dolly in). Raises where nothing
    about the pair was measured, so the pair stays unmeasured.

    `subject_energy` is a separation HYPOTHESIS, and the view legend
    says so: the median is read as camera, the rest as subject and
    shake - and a subject filling the frame reads as camera.
    """
    import numpy as np

    import cv2

    flow = cv2.calcOpticalFlowFarneback(
        prev, curr, None,
        pyr_scale=0.5, levels=2, winsize=9,
        iterations=2, poly_n=5, poly_sigma=1.1, flags=0,
    )
    fx = flow[..., 0]
    fy = flow[..., 1]
    mag = np.sqrt(fx ** 2 + fy ** 2)
    med_x = float(np.median(fx))
    med_y = float(np.median(fy))
    mean_mag = float(mag.mean())
    # What the global shift leaves behind: subject movement, shake,
    # parallax - anything that is not one translation.
    resid = np.sqrt((fx - med_x) ** 2 + (fy - med_y) ** 2)
    subject = float(resid.mean())
    # Radial component about the centre: expansion reads positive.
    height, width = prev.shape
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    xs = xs - (width - 1) / 2.0
    ys = ys - (height - 1) / 2.0
    radius = np.sqrt(xs ** 2 + ys ** 2)
    radius[radius == 0] = 1.0
    divergence = float(((fx * xs + fy * ys) / radius).mean())

    norm = FLOW_NORM_PX
    magnitude = mean_mag / norm
    med_nx, med_ny = med_x / norm, med_y / norm
    direction = _direction_of(med_nx, med_ny, magnitude)
    return {
        "dx": round(med_nx, 3),
        "dy": round(med_ny, 3),
        "magnitude": round(magnitude, 3),
        "direction": direction,
        "camera_tx": round(med_nx, 3),
        "camera_ty": round(med_ny, 3),
        "subject_energy": round(subject / norm, 3),
        "divergence": round(divergence / norm, 3),
        "method": "farneback",
    }


def detect_motion_peaks(
    magnitudes: list,
    sample_rate_hz: int,
) -> tuple:
    """Onsets and apexes of action on a magnitude-over-time curve.

    Returns `(peaks, onset_threshold)`: `peaks` are
    `{"time", "kind", "magnitude"}` sorted by time, `kind` one of
    `"onset"` (a rising-edge crossing of the threshold, re-armed only
    after a dip `ONSET_HYSTERESIS` back under it) or `"apex"` (a scipy
    local maximum at or above the threshold). `onset_threshold` is
    `max(ONSET_FLOOR, median + 0.75 * std)` of the curve - recorded
    because a peak without its threshold cannot be re-derived.

    Pure function of the curve: the unit tests drive it on synthetic
    magnitudes, and `compute_optical_flow_direction` is its only
    production caller.
    """
    import numpy as np

    peaks: list = []
    if not magnitudes:
        return peaks, 0.0
    curve = np.array([float(m) for m in magnitudes], dtype=float)
    median = float(np.median(curve))
    std = float(np.std(curve))
    threshold = round(max(ONSET_FLOOR, median + 0.75 * std), 3)

    separation = PEAK_MIN_SEPARATION_S
    rearm = threshold - ONSET_HYSTERESIS
    # A clip opening above the threshold is motion in progress, not an
    # onset about to happen: it arms only after a real dip.
    armed = bool(curve[0] < threshold)
    last_onset = float("-inf")
    for i, value in enumerate(curve):
        if i == 0:
            continue
        if armed and value >= threshold and curve[i - 1] < threshold:
            moment = round((i + 1) / sample_rate_hz, 3)
            if moment - last_onset >= separation - 1e-9:
                peaks.append({
                    "time": moment,
                    "kind": "onset",
                    "magnitude": round(float(value), 3),
                })
                last_onset = moment
                armed = False
        elif value < rearm:
            armed = True

    try:
        from scipy.signal import find_peaks

        hits, _ = find_peaks(
            curve, prominence=APEX_PROMINENCE,
            distance=max(1, int(round(sample_rate_hz
                                     * PEAK_MIN_SEPARATION_S))),
        )
        for hit in hits:
            if float(curve[int(hit)]) < threshold:
                continue
            peaks.append({
                "time": round((int(hit) + 1) / sample_rate_hz, 3),
                "kind": "apex",
                "magnitude": round(float(curve[int(hit)]), 3),
            })
    except ImportError:
        print(
            "  WARNING: scipy not available, motion apexes unmeasured",
            file=sys.stderr,
        )

    peaks.sort(key=lambda p: (p["time"], p["kind"]))
    return peaks, threshold


def compute_optical_flow_direction(
    video_path: str,
    sample_rate_hz: int = 5,
) -> dict:
    """Dense motion field at 5 Hz: direction, magnitude, peaks, zoom.

    Extracts low-resolution grayscale frames and measures a DENSE
    Farneback flow field per consecutive pair (OpenCV, on the
    `FLOW_PROXY_W`x`FLOW_PROXY_H` proxy). Where OpenCV is unavailable
    the pair falls back to the block-matching global shift
    (`_block_match_shift`) and says so per sample - a degraded but
    real measurement, never a silent substitution.

    Per sample (`values`):

      - `dx`, `dy` - the field's MEDIAN translation, normalized by
        `FLOW_NORM_PX`. The same keys the old block matcher wrote, so
        `decompose_camera_motion` keeps reading them unchanged.
      - `magnitude` - mean field magnitude, normalized the same way.
      - `direction` - the median's eight-way compass (`right`,
        `up-right`, `up`, ...; screen coordinates, dy > 0 is down),
        `static` below `FLOW_STATIC_MAG`, or `mixed` where the field
        moves with no dominant translation (a zoom's median is ~0
        while its mean is not - compassing that noise would present
        a direction nothing decided).
      - `camera_tx`, `camera_ty` - the global shift read as the CAMERA
        hypothesis: the median IS the translation one rigid move
        explains.
      - `subject_energy` - mean residual past the median: the motion NO
        rigid move explains (subject, shake, parallax). Farneback
        pairs only; absent elsewhere, never zero-filled.
      - `divergence` - mean radial component about the frame centre,
        positive = expansion (a zoom or dolly in). The MEASURED
        answer to the always-1.0 `zoom_factor` this step used to
        report: that constant is gone (see `decompose_camera_motion`
        and `tests/test_no_constant_zoom_factor.py`), and this is
        what replaced it - a number with a floor and a ratio, not a
        factor that could never move.
      - `method` - `farneback`, `block_match`, or `unmeasured`.

    Clip level:

      - `dominant_motion` - `static`, `pan_left`, `pan_right`,
        `tilt_up`, `tilt_down`, `zoom_in`, `zoom_out`, `handheld`,
        `mixed` or `unknown`. Zoom fires only when the mean
        divergence clears `DIVERGENCE_ZOOM_MIN` AND carries at least
        `DIVERGENCE_ZOOM_RATIO` of the mean magnitude.
      - `dominant_direction` - compass of the clip's mean vector.
      - `method` - `farneback` when every pair measured densely,
        `block_match` when none did, `farneback+block_match` between.
      - `motion_peaks` - `detect_motion_peaks` on the magnitude curve:
        `{"time", "kind" ("onset"|"apex"), "magnitude"}` sorted by
        time. Onsets are where action STARTS, apexes where it PEAKS -
        the two moments a cut or an effect anchors to.
      - `onset_threshold`, `apex_prominence`,
        `peak_min_separation_s` - the peak detector's own settings,
        recorded so a peak can be re-derived.

    A pair neither estimator measures contributes NO sample rather
    than a zero one; a clip with no samples reports `unknown`, never
    `static` (AGENTS.md 10.3).
    """
    try:
        import numpy as np

        frames = _extract_gray_proxy(video_path, sample_rate_hz)
        if frames is None:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [],
                "dominant_motion": "unknown",
                "dominant_direction": "unknown",
                "method": "unmeasured",
                "motion_peaks": [],
                "onset_threshold": 0.0,
                "apex_prominence": APEX_PROMINENCE,
                "peak_min_separation_s": PEAK_MIN_SEPARATION_S,
            }

        n_frames = frames.shape[0]
        if n_frames < 2:
            return {
                "sample_rate_hz": sample_rate_hz,
                "values": [{
                    "dx": 0.0, "dy": 0.0, "magnitude": 0.0,
                    "direction": "static",
                    "camera_tx": 0.0, "camera_ty": 0.0,
                    "method": "unmeasured",
                }],
                "dominant_motion": "static",
                "dominant_direction": "static",
                "method": "unmeasured",
                "motion_peaks": [],
                "onset_threshold": 0.0,
                "apex_prominence": APEX_PROMINENCE,
                "peak_min_separation_s": PEAK_MIN_SEPARATION_S,
            }

        samples = []
        for i in range(n_frames - 1):
            prev = frames[i]
            curr = frames[i + 1]
            moment = round((i + 1) / sample_rate_hz, 3)
            try:
                sample = _farneback_pair_stats(prev, curr)
            except Exception as dense_failed:
                # Dense flow owes no answer: the global shift below is a
                # degraded but real measurement of the same pair, and it
                # says which method answered. Only a pair NEITHER
                # measures stays out of the series.
                shift = _block_match_shift(prev, curr)
                if shift is None:
                    print(
                        "  WARNING: motion unmeasured for "
                        f"frame pair {i} ({dense_failed}); leaving it "
                        "out of the series",
                        file=sys.stderr,
                    )
                    continue
                magnitude = (float(np.sqrt(shift[0] ** 2
                                           + shift[1] ** 2))
                             / FLOW_NORM_PX)
                med_nx = float(shift[0]) / FLOW_NORM_PX
                med_ny = float(shift[1]) / FLOW_NORM_PX
                sample = {
                    "dx": round(med_nx, 3),
                    "dy": round(med_ny, 3),
                    "magnitude": round(magnitude, 3),
                    "direction": _direction_of(med_nx, med_ny, magnitude),
                    "camera_tx": round(med_nx, 3),
                    "camera_ty": round(med_ny, 3),
                    "method": "block_match",
                }
            sample["time"] = moment
            samples.append(sample)

        # Classify dominant motion pattern across all samples
        if not samples:
            # Every pair went unmeasured (or there was nothing to
            # compare): "unknown" is the admitted absence this
            # function already returns when ffmpeg or numpy fails.
            # "static" would claim a stillness nothing measured.
            dominant = "unknown"
            dominant_direction = "unknown"
            method = "unmeasured"
            peaks: list = []
            onset_threshold = 0.0
        else:
            mean_dx = float(np.mean([s["dx"] for s in samples]))
            mean_dy = float(np.mean([s["dy"] for s in samples]))
            mean_mag = float(np.mean([s["magnitude"] for s in samples]))
            divergences = [s["divergence"] for s in samples
                           if "divergence" in s]
            mean_div = (float(np.mean(divergences)) if divergences
                        else 0.0)

            if mean_mag < FLOW_STATIC_MAG:
                dominant = "static"
            elif (divergences
                    and abs(mean_div) > DIVERGENCE_ZOOM_MIN
                    and abs(mean_div) > DIVERGENCE_ZOOM_RATIO * mean_mag):
                # Measured radial expansion/contraction dominating the
                # translation: a zoom or a dolly, stated as one because
                # the field cannot tell them apart.
                dominant = ("zoom_in" if mean_div > 0 else "zoom_out")
            elif abs(mean_dx) > abs(mean_dy) * 1.5:
                dominant = "pan_right" if mean_dx > 0 else "pan_left"
            elif abs(mean_dy) > abs(mean_dx) * 1.5:
                dominant = "tilt_down" if mean_dy > 0 else "tilt_up"
            else:
                # Check magnitude consistency — consistent = pan, variable = handheld
                mag_std = float(np.std([s["magnitude"] for s in samples]))
                if mag_std > 0.15:
                    dominant = "handheld"
                else:
                    dominant = "mixed"
            dominant_direction = _direction_of(
                mean_dx, mean_dy, mean_mag)

            methods = {s.get("method") for s in samples}
            if methods == {"farneback"}:
                method = "farneback"
            elif methods == {"block_match"}:
                method = "block_match"
            else:
                method = "farneback+block_match"

            peaks, onset_threshold = detect_motion_peaks(
                [s["magnitude"] for s in samples], sample_rate_hz)

        return {
            "sample_rate_hz": sample_rate_hz,
            "values": samples,
            "dominant_motion": dominant,
            "dominant_direction": dominant_direction,
            "method": method,
            "motion_peaks": peaks,
            "onset_threshold": onset_threshold,
            "apex_prominence": APEX_PROMINENCE,
            "peak_min_separation_s": PEAK_MIN_SEPARATION_S,
        }

    except ImportError:
        print(
            "  WARNING: numpy not available for optical flow direction",
            file=sys.stderr,
        )
        return {"sample_rate_hz": sample_rate_hz, "values": [],
                "dominant_motion": "unknown",
                "dominant_direction": "unknown", "method": "unmeasured",
                "motion_peaks": [], "onset_threshold": 0.0,
                "apex_prominence": APEX_PROMINENCE,
                "peak_min_separation_s": PEAK_MIN_SEPARATION_S}
    except Exception as e:
        print(
            f"  WARNING: optical flow direction failed: {e}",
            file=sys.stderr,
        )
        return {"sample_rate_hz": sample_rate_hz, "values": [],
                "dominant_motion": "unknown",
                "dominant_direction": "unknown", "method": "unmeasured",
                "motion_peaks": [], "onset_threshold": 0.0,
                "apex_prominence": APEX_PROMINENCE,
                "peak_min_separation_s": PEAK_MIN_SEPARATION_S}


def motion_backfill_needed(index: dict) -> bool:
    """Whether a cached per-clip index predates dense motion.

    True when the document has no optical-flow measurement at all, when
    it was measured without a recorded method (the block-match era
    wrote none), when the method is not dense flow, or when the peak
    list is absent. Anything dense and peaked is current and stays
    cached - a backfill that rewrote those would re-measure every
    project on every run for nothing.
    """
    flow = (index or {}).get("optical_flow_direction")
    if not isinstance(flow, dict):
        return True
    if flow.get("method") != "farneback":
        return True
    return not isinstance(flow.get("motion_peaks"), list)


def backfill_motion_measurement(index: dict, video_path: str = None) -> bool:
    """Measure dense motion onto a cached index that predates it.

    Mutates `index` in place, replacing `optical_flow_direction` with
    a fresh `compute_optical_flow_direction` measurement. Returns
    whether anything changed. The footage is read from `video_path`
    (or the document's own `source_file`); when neither names a file
    on disk the document is left alone and False is returned - a
    stale measurement is served, never an invented one.

    A measurement that yields no samples (`unknown`) is NOT written:
    an upgrade that replaces one absence with another is churn, and
    the next run would retry it either way.
    """
    path = video_path or (index or {}).get("source_file")
    if not path or not os.path.isfile(path):
        print(
            f"  WARNING: motion backfill skipped for "
            f"{(index or {}).get('clip_id', '?')}: no footage file "
            f"at {path!r}",
            file=sys.stderr,
        )
        return False
    flow = compute_optical_flow_direction(str(path))
    if not flow.get("values"):
        return False
    index["optical_flow_direction"] = flow
    return True


def _panns_checkpoint_present() -> bool:
    """Whether the event checkpoint is on this machine right now."""
    try:
        from library.tools.shared_environment import panns_checkpoint
        path = panns_checkpoint()
        return path.is_file() and path.stat().st_size >= 3e8
    except Exception:
        return False


def sound_events_backfill_needed(index: dict) -> bool:
    """Whether a cached per-clip index predates the event measurement.

    True when the document has no event list, when its method is not
    the PANNs producer, or when it records an `unmeasured` absence
    whose reason may have cleared (the checkpoint is on this machine
    now). A measured clip stays cached, and a still-unmeasurable one
    is not rewritten - re-measuring either on every run would burn an
    inference per clip, or churn the file, for nothing.
    """
    from library.tools.analysis import sound_event_pipeline as _sep
    events = (index or {}).get("sound_events")
    method = (index or {}).get("sound_event_method")
    if not isinstance(events, list) or not isinstance(method, str):
        return True
    if method == _sep.METHOD:
        return False
    if method.startswith("unmeasured:"):
        return _panns_checkpoint_present()
    return True


def backfill_sound_events(index: dict, video_path: str = None,
                          layout=None) -> bool:
    """Measure sound events onto a cached index that predates them.

    Mutates `index` in place, setting `sound_events` and
    `sound_event_method` from a fresh `measure_clip_sound_events`
    pass over the clip's 16 kHz audio. Returns whether anything
    changed. The footage is read from `video_path` (or the document's
    own `source_file`); when neither names a file on disk the
    document is left alone and False is returned - a stale
    measurement is served, never an invented one.

    An `unmeasured` outcome IS written: unlike motion (whose absence
    is churn), it records that the checkpoint was missing at a named
    time, which is what makes the next run's refusal specific. The
    backfill predicate retries such a record only once the checkpoint
    is present, so a written `unmeasured` is not churned blindly.
    """
    path = video_path or (index or {}).get("source_file")
    if not path or not os.path.isfile(path):
        print(
            f"  WARNING: sound-event backfill skipped for "
            f"{(index or {}).get('clip_id', (index or {}).get('audio_id', '?'))}: "
            f"no footage file at {path!r}",
            file=sys.stderr,
        )
        return False
    try:
        if layout is not None:
            audio_dir = str(layout.write_dir(Area.AUDIO_CACHE,
                                             step="temporal_index"))
        else:
            audio_dir = tempfile.mkdtemp(prefix="r5e-backfill-")
        wav = extract_audio_16k(
            str(path), audio_dir,
            clip_id=(index or {}).get("clip_id",
                                      (index or {}).get("audio_id")))
        events, method = measure_clip_sound_events(wav)
    except Exception as e:
        print(
            f"  WARNING: sound-event backfill failed for "
            f"{(index or {}).get('clip_id', '?')}: {e}",
            file=sys.stderr,
        )
        return False
    index["sound_events"] = events
    index["sound_event_method"] = method
    # The legacy `audio_events` list predates the producer: refresh it
    # from the same pass so the two halves of the document agree.
    try:
        index["audio_events"] = classify_audio_events(
            wav, speech_regions=index.get("speech_regions"),
            sound_measurement=(events, method))
    except Exception as e:
        print(
            f"  WARNING: audio_events refresh failed for "
            f"{(index or {}).get('clip_id', '?')}: {e}",
            file=sys.stderr,
        )
    return True


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
      - residual:      leftover motion energy (subject movement, shake)

    There is deliberately NO zoom_factor here. A zoom estimate used to
    sit alongside these keys (`1.0 + max(0, mag - translation_mag) *
    0.3`), but the inputs are a single global translation vector per
    sample with magnitude = sqrt(dx^2 + dy^2), which is never larger
    than |dx| + |dy| - so the term was 0.0 on every sample and the
    factor read 1.0 always. A constant presented as a measurement.
    True zoom needs a center-weighted dense field (radial expansion
    toward the frame edges), which the block matcher above does not
    compute, so no zoom is reported rather than a wrong one.

    Returns:
        {
            "sample_rate_hz": 5,
            "values": [{"translation_x", "translation_y",
                         "residual"}, ...]
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

        # Leftover motion energy past the translation. (A zoom term used
        # to be derived here from mag - (|dx| + |dy|); that difference is
        # never positive for a translation vector, so it is gone - see
        # the docstring.)
        translation_mag = abs(dx) + abs(dy)
        residual = round(max(0.0, mag - translation_mag * 0.5), 3)

        decomposed.append({
            "translation_x": tx,
            "translation_y": ty,
            "residual": residual,
        })

    return {
        "sample_rate_hz": flow_direction.get("sample_rate_hz", 5),
        "values": decomposed,
    }


# ── 10. Face presence (5Hz) ───────────────────────────────────────────

# One loader, shared with `render_qa.measure_face_intact` - the render
# side asks the same availability question about the same cascade, and
# two copies of this guard is how one of them ends up not asking.
_load_face_cascade = load_face_cascade


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
            "face_width": [float|None, ...],     # box width as a fraction of
                                                 # the frame width; None where
                                                 # no face was located
            "face_boxes": [box|None, ...],       # the largest face as a
                                                 # normalized [x1, y1, x2, y2]
                                                 # box (each 0..1); None where
                                                 # no face was located
            "face_present_times": [float, ...],  # timestamps where value > 0.5
            "face_absent_times": [float, ...]    # timestamps where value < 0.5
        }

    `face_center_x` and `face_width` are parallel to `values` and the same
    length. They track the LARGEST face in the frame, the same face
    `values` scores, so the three never describe different people.

    **`face_width` is the measurement this function used to throw away.**
    The cascade returns (x, y, w, h) and only the centre was kept, so the
    pipeline could aim a crop at the subject but had no way to know
    whether the crop was WIDE ENOUGH for them. On project 001's A-roll
    the answer was no - a face box spanning 37.7% of the source width,
    into a fill crop that keeps 31.6% - and the conform cropped the
    speaker's face with nothing measuring it. See
    `library/tools/subject_framing.py`.

    Only the WIDTH is recorded, not the height. Landscape source in a
    portrait frame is a width-limited crop: the source's full height is
    always shown, so a face can only ever be lost off the sides. A
    measurement nothing can read is not worth a key.

    `face_boxes` carries the FULL rect the other two derive from, because
    SAM 2's box prompt needs y extents and the centre-plus-width pair
    cannot place the box vertically. The box travels NORMALIZED (each
    coordinate 0..1) so the seeder - which works in its own extraction
    frame's pixels - never names this function's sample size. See
    `library/tools/analysis/object_segmentation.normalize_face_box` and
    issue #268.
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
                "face_width": [],
                "face_boxes": [],
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
                "face_width": [],
                "face_boxes": [],
                "face_present_times": [],
                "face_absent_times": [],
            }

        frames = raw[:n_frames * frame_size].reshape(
            n_frames, sample_h, sample_w, 3)
        face_values = []
        face_center_x = []
        face_width = []
        face_boxes = []

        cascade = _load_face_cascade()
        if cascade is not None:
            import cv2
            # The full rect reaches the seeder; the import stays beside its
            # only use so this step never pays for the SAM module at import.
            from library.tools.analysis.object_segmentation import (
                normalize_face_box)

            for frame in frames:
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=3, minSize=(20, 20)
                )
                if len(faces) > 0:
                    # Score by face area relative to frame, and keep where
                    # that same largest face sits horizontally.
                    largest = max(faces, key=lambda f: f[2] * f[3])
                    fx, fy, fw, fh = largest
                    max_area = fw * fh
                    frame_area = sample_w * sample_h
                    presence = min(1.0, max_area / (frame_area * 0.15))
                    face_values.append(round(float(presence), 2))
                    # Normalise against the width actually sampled, which
                    # now varies per clip, so the value stays independent of
                    # both the sample size and the source resolution.
                    face_center_x.append(
                        round(float(fx + fw / 2.0) / sample_w, 4))
                    face_width.append(round(float(fw) / sample_w, 4))
                    # The whole rect, normalised against BOTH sample axes,
                    # is what the subject-masking seeder prompts SAM 2 with
                    # (issue #268). A rect the clamp rejects reads as no
                    # box, not as a squeezed one.
                    face_boxes.append(
                        normalize_face_box(
                            fx, fy, fw, fh, sample_w, sample_h))
                else:
                    face_values.append(0.0)
                    face_center_x.append(None)
                    face_width.append(None)
                    face_boxes.append(None)

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
                face_width.append(None)
                face_boxes.append(None)

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
            "face_width": face_width,
            "face_boxes": face_boxes,
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
            "face_width": [],
            "face_boxes": [],
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
            "face_width": [],
            "face_boxes": [],
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
    """Derive a binary speech activity curve from aligned word timestamps.

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


def _pretranscribe_misses(items: list, layout: ProjectLayout,
                          whisper_model_size: str,
                          language: str) -> dict:
    """Extraction + onsets + ONE batched transcription for cache misses.

    `items` are `(key, source_path, index_path, require_picture)`;
    returns `{key: (audio_path, onsets, regions, transcription)}` for
    the misses only. The per-clip indexers take these rows and skip
    their own extraction, onset and speech passes, so a run pays one
    MFA invocation per language no matter how many clips miss. The
    cache is read twice (here to filter, in the loop to serve) -
    reads are milliseconds, MFA runs are not.
    """
    audio_dir = str(layout.write_dir(Area.AUDIO_CACHE,
                                     step="temporal_index"))
    needs = []
    for key, source_path, index_path, require_picture in items:
        if not source_path or not os.path.isfile(source_path):
            continue
        if _load_cached_index(index_path, expected_language=language,
                              require_picture=require_picture) is not None:
            continue
        needs.append((key, source_path))
    if not needs:
        return {}
    prepared = []
    for key, source_path in needs:
        try:
            audio_path = extract_audio_16k(source_path, audio_dir,
                                           clip_id=key)
            onsets = detect_onsets(audio_path)
        except Exception as e:
            # One clip's extraction or onset pass must not fail the
            # step: the clip falls out of the batch and takes the
            # single-shot path in the loop, which reports per-clip.
            print(f"  WARNING: batch prep failed for {key} ({e}); "
                  f"single-shot fallback", file=sys.stderr)
            continue
        prepared.append({"key": key, "audio_path": audio_path,
                         "onsets": onsets})
    if not prepared:
        return {}
    print(f"    Transcribing {len(prepared)} clip(s) in one batch...",
          file=sys.stderr)
    try:
        batched = transcribe_clips_batched(
            prepared, whisper_model_size=whisper_model_size,
            language=language)
    except Exception as e:
        # The batch itself must not fail the step either: every clip
        # takes the single-shot seam, which is exactly what the step
        # did before batching existed.
        print(f"  WARNING: batched transcription failed ({e}); "
              f"single-shot fallback for {len(prepared)} clip(s)",
              file=sys.stderr)
        batched = {}
    out = {}
    for row in prepared:
        if row["key"] not in batched:
            regions, transcription = detect_speech_regions(
                row["audio_path"],
                os.path.dirname(row["audio_path"]),
                onsets=row["onsets"],
                whisper_model_size=whisper_model_size,
                language=language)
            batched[row["key"]] = (regions, transcription)
        regions, transcription = batched[row["key"]]
        out[row["key"]] = (row["audio_path"], row["onsets"],
                           regions, transcription)
    return out


def index_clip(
    video_path: str,
    clip_id: str,
    layout: ProjectLayout,
    whisper_model_size: str = "large-v3",
    language: str = "en",
    pretranscribed: dict = None,
) -> dict:
    """
    Build temporal event index for a single clip.

    Runs all sub-analyzers and produces a complete index document.
    `pretranscribed` is this clip's `_pretranscribe_misses` row
    `(audio_path, onsets, regions, transcription)` - the batched run
    path passes it so the clip skips its own extraction, onset and
    speech passes; anything else transcribes single-shot.
    """
    print(f"\n  Indexing {clip_id}: {Path(video_path).name}", file=sys.stderr)
    t0 = time.time()

    duration = get_duration(video_path)
    print(f"    Duration: {duration:.1f}s", file=sys.stderr)

    # Extract audio (shared by speech + energy + audio event analyzers)
    audio_dir = str(layout.write_dir(Area.AUDIO_CACHE, step="temporal_index"))
    if pretranscribed is not None:
        audio_path, onsets, speech, transcription = pretranscribed
    else:
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
    if pretranscribed is None:
        print("    [3/12] Onset detection...", file=sys.stderr)
        onsets = detect_onsets(audio_path)
        print(
            f"           {len(onsets)} onsets",
            file=sys.stderr,
        )
    else:
        print("    [3/12] Onset detection... (batched, reused)",
              file=sys.stderr)

    # 4. Speech regions (the reel path's seam: Voz + MFA, WhisperX fallback)
    # Onsets are passed in for word-boundary snapping
    if pretranscribed is None:
        print("    [4/12] Speech detection (Voz + MFA, WhisperX fallback)...",
              file=sys.stderr)
        speech, transcription = detect_speech_regions(
            audio_path, str(layout.read_dir(Area.OUTPUT_ROOT)),
            onsets=onsets,
            whisper_model_size=whisper_model_size,
            language=language,
        )
    else:
        print("    [4/12] Speech detection... (batched, reused)",
              file=sys.stderr)
    speech_dur = sum(r["end"] - r["start"] for r in speech)
    word_count = sum(len(r.get("words", [])) for r in speech)
    print(
        f"           {len(speech)} regions, {speech_dur:.1f}s speech, "
        f"{word_count} words ({transcription['aligner']} aligned, "
        f"{transcription['arm']} arm)",
        file=sys.stderr,
    )

    # 5. Audio events
    print("    [5/12] Audio events...", file=sys.stderr)
    sound_events, sound_event_method = measure_clip_sound_events(
        audio_path)
    audio_events = classify_audio_events(
        audio_path, speech_regions=speech,
        sound_measurement=(sound_events, sound_event_method))
    print(
        f"           {len(audio_events)} events "
        f"({len(sound_events)} measured non-speech, "
        f"{sound_event_method})",
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

    # 12. Speech activity curve (derived from aligned word timestamps)
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
        # What language this clip was transcribed in. A cached index
        # in another language is STALE (see `_load_cached_index`) -
        # without this the reuse below would serve English words for
        # a project that now declares Spanish.
        "transcription_language": language,
        # Which instrument timed these words, and which language it
        # heard. The reel path records the same account on its own
        # transcripts; a run that cannot tell MFA words from
        # wav2vec2 words cannot say what moved when the instrument
        # changes.
        "transcription_arm": transcription["arm"],
        "transcription_aligner": transcription["aligner"],
        "detected_language": transcription["detected_language"],

        # ── Original signal outputs ──────────────────────────────────
        "scene_boundaries": scenes,
        "speech_regions": speech,
        "energy_curve": energy,
        "audio_events": audio_events,
        # Measured non-speech sound events (PANNs AudioSet labels, in
        # source seconds) beside the method that produced them - the
        # layer event anchors and the soundevents view read. An
        # unmeasured clip records `sound_event_method: unmeasured:
        # <reason>` and an empty list, never guesses.
        "sound_events": sound_events,
        "sound_event_method": sound_event_method,
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
        # aligned word timestamps. No additional processing cost.
        # speech_ratio: fraction of clip duration with active speech.
        "speech_activity": speech_activity,
    }


def _load_cached_index(index_path: str, expected_language=None,
                       require_picture: bool = True):
    """An existing per-clip index, or None if there is nothing usable.

    A truncated or corrupt file reads as "no cache" and is recomputed - a
    half-written index is worse than none, because every downstream
    creative decision is made against it.

    A cache in another LANGUAGE reads as no cache too: the words on
    disk are in the wrong language for this run. Index documents
    written before `transcription_language` existed read as English,
    which is what every one of them was. Pass None to skip the check
    (callers that do not transcribe).

    An index reads as no cache when no real instrument timed it:
    `transcription_aligner` must name one of `TRANSCRIBED_ALIGNERS`,
    and an index written when nothing could transcribe (`"none"`)
    is re-indexed - words timed by nothing are not words.

    An index that PREDATES the stamp is stamped as legacy on read
    and served: it was timed by the old faster-whisper plus wav2vec2
    path, which is an instrument with a known ~40-55 ms late bias,
    not nothing. Re-transcribing whole projects unasked would move
    every downstream in-point with no record; `ren reindex` moves a
    project onto Voz when asked, stating what it invalidated.
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
    if not require_picture:
        # Audio-only indices carry no picture measurements (see
        # `index_audio_clip`) - requiring them would re-index every
        # voiceover and bed on every run.
        required = ("speech_regions", "energy_curve", "audio_events")
    missing = [k for k in required if k not in index]
    if missing:
        print(f"  WARNING: index {index_path} is missing {missing}; "
              f"re-indexing", file=sys.stderr)
        return None
    if index.get("transcription_aligner") not in TRANSCRIBED_ALIGNERS:
        if "transcription_aligner" not in index:
            # Legacy: timed by the old path before any stamp existed.
            # Stamped on read and served - the words are wav2vec2's,
            # with its known late bias, and the stamp says so from
            # here on. `ren reindex` moves them when asked.
            index["transcription_arm"] = LEGACY_ARM
            index["transcription_aligner"] = LEGACY_ALIGNER
            index["detected_language"] = (
                index.get("transcription_language") or "en")
            try:
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(index, f, indent=2)
            except OSError as e:
                print(f"  WARNING: index {index_path} could not be "
                      f"stamped ({e}); serving unstamped",
                      file=sys.stderr)
            print(f"  index {index_path} predates the instrument "
                  f"stamp; serving as legacy {LEGACY_ARM}-"
                  f"{LEGACY_ALIGNER} (`ren reindex` moves it)",
                  file=sys.stderr)
        else:
            print(f"  WARNING: index {index_path} was timed by "
                  f"{index.get('transcription_aligner')!r} - re-indexing, "
                  f"because only {list(TRANSCRIBED_ALIGNERS)} are served "
                  f"as current", file=sys.stderr)
            return None
    if expected_language is not None:
        cached_language = (index.get("transcription_language") or "en")
        if cached_language != expected_language:
            print(f"  WARNING: index {index_path} was transcribed as "
                  f"{cached_language}, this run wants "
                  f"{expected_language}; re-indexing", file=sys.stderr)
            return None
    return index


def index_audio_clip(
    audio_path: str,
    audio_id: str,
    layout: ProjectLayout,
    whisper_model_size: str = "large-v3",
    language: str = "en",
    pretranscribed: dict = None,
) -> dict:
    """The temporal index of a voiceover take or music bed: SOUND only.

    No scene detection, motion, flow, faces or colour - there is no
    picture to measure. What travels is what an audio file has:
    speech regions (a voiceover transcribed in the project's
    language; a music bed transcribes to nothing, which is the
    correct answer), the energy curve, onsets, audio events and the
    derived word timings. The document is cached beside the video
    indices under its own `audio_001` name.

    `pretranscribed` is this file's `_pretranscribe_misses` row -
    the batched run path passes it so the file skips its own onset
    and speech passes.
    """
    print(f"\n  Indexing {audio_id}: {Path(audio_path).name}",
          file=sys.stderr)
    t0 = time.time()

    duration = get_duration(audio_path)
    print(f"    Duration: {duration:.1f}s", file=sys.stderr)

    cache_dir = str(layout.write_dir(Area.AUDIO_CACHE,
                                     step="temporal_index"))
    if pretranscribed is not None:
        wav_path, onsets, speech, transcription = pretranscribed
    else:
        wav_path = extract_audio_16k(audio_path, cache_dir, clip_id=audio_id)

    print("    [1/5] Energy curve (30Hz)...", file=sys.stderr)
    energy = compute_energy_curve(wav_path)

    if pretranscribed is None:
        print("    [2/5] Onset detection...", file=sys.stderr)
        onsets = detect_onsets(wav_path)

        print("    [3/5] Speech detection (Voz + MFA, WhisperX fallback)...",
              file=sys.stderr)
        speech, transcription = detect_speech_regions(
            wav_path, str(layout.read_dir(Area.OUTPUT_ROOT)),
            onsets=onsets,
            whisper_model_size=whisper_model_size,
            language=language,
        )
    else:
        print("    [2/5] Onset detection... (batched, reused)",
              file=sys.stderr)
        print("    [3/5] Speech detection... (batched, reused)",
              file=sys.stderr)
    speech_dur = sum(r["end"] - r["start"] for r in speech)
    word_count = sum(len(r.get("words", [])) for r in speech)
    print(
        f"           {len(speech)} regions, {speech_dur:.1f}s speech, "
        f"{word_count} words ({transcription['aligner']} aligned, "
        f"{transcription['arm']} arm)",
        file=sys.stderr,
    )

    print("    [4/5] Audio events...", file=sys.stderr)
    sound_events, sound_event_method = measure_clip_sound_events(
        wav_path)
    audio_events = classify_audio_events(
        wav_path, speech_regions=speech,
        sound_measurement=(sound_events, sound_event_method))

    print("    [5/5] Word end times...", file=sys.stderr)
    word_ends = extract_word_end_times(speech)

    speech_activity = derive_speech_activity_curve(speech, duration)

    elapsed = time.time() - t0
    print(f"    Done ({elapsed:.1f}s)", file=sys.stderr)

    return {
        "audio_id": audio_id,
        "source_file": audio_path,
        "duration": round(duration, 3),
        "transcription_language": language,
        "transcription_arm": transcription["arm"],
        "transcription_aligner": transcription["aligner"],
        "detected_language": transcription["detected_language"],
        "speech_regions": speech,
        "energy_curve": energy,
        "audio_events": audio_events,
        "sound_events": sound_events,
        "sound_event_method": sound_event_method,
        "onset_times": onsets,
        "word_end_times": word_ends,
        "speech_activity": speech_activity,
    }


def _index_audio_files(
    audio_catalog: list,
    layout: ProjectLayout,
    whisper_model_size: str = "large-v3",
    language: str = "en",
) -> list:
    """Index every cataloged audio file, reusing whatever is on disk.

    One JSON file per audio id in the TEMPORAL_INDEX area, the same
    cache contract as video (`_load_cached_index`, including the
    language check). A file that fails to index is an error ENTRY,
    not an exception: one corrupt bed must not fail the step.
    """
    index_dir = str(layout.write_dir(Area.TEMPORAL_INDEX,
                                     step="temporal_index"))
    out = []
    total = len(audio_catalog)
    items = []
    for i, entry in enumerate(audio_catalog):
        if isinstance(entry, dict):
            filepath = entry.get("path", "")
            audio_id = entry.get("audio_id") or f"audio_{i + 1:03d}"
        else:
            filepath = str(entry)
            audio_id = f"audio_{i + 1:03d}"
        items.append((audio_id, filepath,
                      os.path.join(index_dir, f"{audio_id}.json"), False))
    pretranscribed = _pretranscribe_misses(
        items, layout, whisper_model_size, language)
    for i, entry in enumerate(audio_catalog):
        if isinstance(entry, dict):
            filepath = entry.get("path", "")
            filename = entry.get("filename", Path(filepath).name
                                 if filepath else "")
            audio_id = entry.get("audio_id") or f"audio_{i + 1:03d}"
        else:
            filepath = str(entry)
            filename = Path(filepath).name
            audio_id = f"audio_{i + 1:03d}"

        print(f"\n[{i + 1}/{total}] {filename}", file=sys.stderr)

        if not filepath or not os.path.isfile(filepath):
            print("  WARNING: file not found, skipping", file=sys.stderr)
            continue

        index_path = os.path.join(index_dir, f"{audio_id}.json")
        try:
            index = _load_cached_index(
                index_path, expected_language=language,
                require_picture=False)
            if index is None:
                index = index_audio_clip(
                    filepath, audio_id, layout, whisper_model_size,
                    language=language,
                    pretranscribed=pretranscribed.get(audio_id))
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(index, f, indent=2)
            else:
                print(f"  reusing {os.path.basename(index_path)}",
                      file=sys.stderr)
            out.append({
                "audio_id": audio_id,
                "index_path": index_path,
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
            })
        except Exception as e:
            print(f"  ERROR: indexing failed for {filename}: {e}",
                  file=sys.stderr)
            out.append({"audio_id": audio_id, "error": str(e)})
    return out


def build_temporal_index(
    raw_footage_files: list,
    layout: ProjectLayout,
    whisper_model_size: str = "large-v3",
    language: str = "en",
    audio_catalog: list = None,
    semantic_analysis_documents: list = None,
    clip_catalog: list = None,
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
    index_dir = str(layout.write_dir(Area.TEMPORAL_INDEX, step="temporal_index"))

    # The semantic step names files by source stem while the catalog owns
    # stable clip ids. Join them through the same path/stem adapter used by
    # other consumers, so a visual document can never attach to the wrong
    # temporal index by list position.
    from library.tools.semantic_index import build_semantic_lookup
    from library.tools.regional_motion import (
        build_analysis as build_regional_motion_analysis,
        compact_analysis as compact_regional_motion_analysis,
    )
    semantic_by_clip = build_semantic_lookup(
        semantic_analysis_documents or [], clip_catalog or [])

    results = []
    reused = 0
    total = len(raw_footage_files)

    items = []
    for i, file_info in enumerate(raw_footage_files):
        if isinstance(file_info, dict):
            filepath = file_info["path"]
            clip_id = file_info.get("clip_id", f"clip_{i + 1:03d}")
        else:
            filepath = file_info
            clip_id = f"clip_{i + 1:03d}"
        items.append((clip_id, filepath,
                      os.path.join(index_dir, f"{clip_id}.json"), True))
    pretranscribed = _pretranscribe_misses(
        items, layout, whisper_model_size, language)

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
            index = _load_cached_index(index_path, expected_language=language)
            if index is not None:
                reused += 1
                # A cached document predating dense motion is UPGRADED,
                # not re-transcribed: the backfill measures only the
                # flow and rewrites the same file, so old projects gain
                # peaks and directions for the cost of one ffmpeg pass.
                motion_due = motion_backfill_needed(index)
                if motion_due:
                    print(f"  backfilling dense motion for "
                          f"{os.path.basename(index_path)}",
                          file=sys.stderr)
                    if backfill_motion_measurement(index, filepath):
                        with open(index_path, "w", encoding="utf-8") as f:
                            json.dump(index, f, indent=2)
                    else:
                        print(f"  motion backfill yielded nothing; "
                              f"serving cached measurement",
                              file=sys.stderr)
                # A cached document predating sound events is UPGRADED
                # the same way: the backfill measures only the audio
                # and rewrites the same file, so old projects gain
                # event anchors for the cost of one ffmpeg pass plus
                # one inference - no re-transcription.
                sound_due = sound_events_backfill_needed(index)
                if sound_due:
                    print(f"  backfilling sound events for "
                          f"{os.path.basename(index_path)}",
                          file=sys.stderr)
                    if backfill_sound_events(index, filepath, layout):
                        with open(index_path, "w", encoding="utf-8") as f:
                            json.dump(index, f, indent=2)
                    else:
                        print(f"  sound-event backfill yielded nothing; "
                              f"serving cached measurement",
                              file=sys.stderr)
                if not motion_due and not sound_due:
                    print(f"  reusing {os.path.basename(index_path)}",
                          file=sys.stderr)
            else:
                index = index_clip(
                    filepath, clip_id, layout, whisper_model_size,
                    language=language,
                    pretranscribed=pretranscribed.get(clip_id),
                )
                # Write per-clip JSON
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(index, f, indent=2)

            # Regional flow is deliberately different from the clip-wide
            # curves above: only time-bounded Gemma actions select a span.
            # Existing face boxes and this step's existing scene boundaries
            # provide the fusion inputs; the cut detector itself is untouched.
            previous_regional = index.get("regional_motion")
            semantic_document = semantic_by_clip.get(clip_id)
            # A direct caller without semantic inputs cannot select a span,
            # so duration is immaterial in that case. Keep the promised
            # duration key strict whenever a document could select motion.
            regional_duration = (
                index["duration"] if semantic_document is not None else 0.0)
            regional = build_regional_motion_analysis(
                filepath,
                regional_duration,
                semantic_document,
                index.get("face_presence"),
                scene_boundaries=index["scene_boundaries"],
                cached=previous_regional,
            )
            if regional != previous_regional:
                index["regional_motion"] = regional
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(index, f, indent=2)
            print(
                f"  regional motion: {len(regional['spans'])} selected span(s), "
                f"{regional['measurement_status']}",
                file=sys.stderr,
            )


            # The motion half of the summary: what the cut and effect
            # planners address. Peaks ride here (times, kinds,
            # magnitudes) so sub-block anchors resolve without opening
            # the per-clip file; the per-sample series stays in that
            # file, named by `index_path` below.
            #
            # The sound half rides beside it: the clip's measured
            # non-speech events (labels, spans, confidences) so event
            # anchors resolve the same way, plus the method that says
            # whether anything was measured at all.
            flow = index.get("optical_flow_direction") or {}
            if not isinstance(flow, dict):
                flow = {}
            sound_events = index.get("sound_events")
            if not isinstance(sound_events, list):
                sound_events = []
            sound_event_method = index.get("sound_event_method")
            if not isinstance(sound_event_method, str):
                sound_event_method = (
                    "unmeasured: predates the event measurement")
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
                "dominant_motion": flow.get("dominant_motion", "unknown"),
                "dominant_direction": flow.get(
                    "dominant_direction", "unknown"),
                "motion_method": flow.get("method", "unmeasured"),
                "motion_peaks": [
                    p for p in flow.get("motion_peaks", [])
                    if isinstance(p, dict)
                ],
                "sound_event_method": sound_event_method,
                "sound_events": [
                    e for e in sound_events
                    if isinstance(e, dict)
                ],
                "regional_motion_status": regional["measurement_status"],
                "regional_motion_reason": regional.get("reason"),
                "regional_motion_spans": compact_regional_motion_analysis(
                    regional),
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
        "total_indexed": sum(1 for r in results if "error" not in r),
        "total_failed": sum(1 for r in results if "error" in r),
        "total_reused": reused,
        "index_dir": index_dir,
        "audio_indices": _index_audio_files(
            audio_catalog or [], layout, whisper_model_size,
            language=language),
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

    semantic_documents = input_data.get("semantic_analysis_documents")
    clip_catalog = input_data.get("clip_catalog")
    if not isinstance(semantic_documents, list):
        _emit({"error": "Missing required input: semantic_analysis_documents",
               "step": "1.04_temporal_index"})
        sys.exit(1)
    if not isinstance(clip_catalog, list):
        _emit({"error": "Missing required input: clip_catalog",
               "step": "1.04_temporal_index"})
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

    # The language this footage speaks. The project's `source.language`
    # (library/tools/footage_identity.declared_language); undeclared
    # reads as English, which is what every project transcribed before
    # the setting existed.
    from library.tools.footage_identity import declared_language
    language = declared_language(
        project_folder or str(Path(args.output_dir).resolve().parent))

    # ── Index, reusing whatever is already on disk ──
    result = build_temporal_index(
        raw_files, layout, args.whisper_model,
        language=language,
        audio_catalog=input_data.get("audio_catalog"),
        semantic_analysis_documents=semantic_documents,
        clip_catalog=clip_catalog,
    )
    result["source"] = "cache" if result["total_reused"] == len(raw_files) else "fresh"

    # Summary
    audio_done = len(result.get("audio_indices") or [])
    print(
        f"\n{'=' * 50}\n"
        f"Temporal Event Index Complete ({language})\n"
        f"  Indexed: {result['total_indexed']} clips\n"
        f"  Reused:  {result['total_reused']} clips\n"
        f"  Failed:  {result['total_failed']} clips\n"
        f"  Audio indexed: {audio_done} file(s)\n"
        f"  Output:  {result['index_dir']}\n"
        f"{'=' * 50}",
        file=sys.stderr,
    )

    # Output directly for DAG compatibility
    _emit(result)


if __name__ == "__main__":
    main()



# ── Region-scoped re-index, and putting it back ─────────────────────
#
# The captain's worked example, middle two moves: "go back to the raw
# footage and assets, re-index that specific region ... and splice that
# into where the audio transcription is stored."
#
# WHERE the transcription is stored is this step's own per-clip index -
# `{area:temporal_index}/<clip_id>.json`, whose `speech_regions[]` carry
# `{start, end, text, words[]}` in SOURCE seconds.  There is no separate
# transcript artifact on the DAG; this is it.
#
# Nothing here re-implements measurement.  `extract_span` already caches
# an extracted span keyed by the span ITSELF - source file, in, out - and
# was written for exactly this ask; `detect_speech_regions` already
# measures a wav.  The only new thing is the arithmetic of putting a
# span's results back on the clip's own clock.

def reindex_region(project_folder: str, clip_id: str, source_file: str,
                   source_start: float, source_end: float,
                   whisper_model_size: str = "large-v3",
                   pad_seconds: float = 0.5,
                   language: str = None) -> list:
    """Re-measure the speech in ONE span of one clip.

    Returns `speech_regions` in the CLIP'S OWN source seconds, not the
    span's - the span is an implementation detail of the measurement and
    every consumer downstream reads clip time.

    `pad_seconds` widens the extracted audio on both sides without
    widening what is returned.  The aligner reads against context, and a
    span cut exactly on a word boundary loses the consonant attack at
    each end; the padding is measured audio, and regions that fall
    entirely into it are dropped rather than reported, so the padding
    cannot smuggle a neighbour's words into the region.
    """
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.timeline_transcript import extract_span

    if source_end <= source_start:
        raise ValueError(
            f"reindex_region: {source_start}-{source_end} is not a span")

    layout = ProjectLayout(project_folder)
    cache_dir = Path(layout.write_dir(Area.AUDIO_CACHE, step="temporal_index"))

    if language is None:
        # The project's own declaration, else English - the same
        # resolution `main()` applies to whole clips.
        from library.tools.footage_identity import declared_language
        language = declared_language(project_folder)

    padded_start = max(0.0, source_start - pad_seconds)
    padded_end = source_end + pad_seconds
    span_wav = extract_span(source_file, padded_start, padded_end, cache_dir)

    with tempfile.TemporaryDirectory() as scratch:
        # The span's own instrument account is not returned: each
        # region already carries its `method`, and the caller splices
        # these into a clip-timed document with its own stamp.
        measured, _ = detect_speech_regions(
            audio_path=str(span_wav), output_dir=scratch, onsets=[],
            whisper_model_size=whisper_model_size, language=language)

    # Back onto the clip's clock, then bounded to what was ASKED for.
    regions = []
    for region in measured:
        start = region["start"] + padded_start
        end = region["end"] + padded_start
        if end <= source_start or start >= source_end:
            continue          # lives entirely in the padding
        moved = dict(region)
        moved["start"] = round(start, 3)
        moved["end"] = round(end, 3)
        moved["words"] = [
            {**w,
             "start": round(w["start"] + padded_start, 3),
             "end": round(w["end"] + padded_start, 3)}
            for w in (region.get("words") or [])
        ]
        moved["reindexed_span"] = [round(source_start, 3), round(source_end, 3)]
        regions.append(moved)
    return regions


def splice_region_index(index_doc: dict, fresh_regions: list,
                        source_start: float, source_end: float) -> dict:
    """Replace the speech in `[source_start, source_end)` with `fresh_regions`.

    Overlap, not containment, decides what goes: a region that straddles
    the edge of the span was partly re-measured, so keeping it would
    leave two descriptions of the same seconds - the old one and the new.

    Refuses a fresh region that lies outside the span it claims to be
    replacing.  Without that, a re-index whose padding leaked would
    silently overwrite a neighbour's words, and the neighbour was never
    re-measured.

    Returns a new document; the input is not mutated.
    """
    if source_end <= source_start:
        raise ValueError(
            f"splice_region_index: {source_start}-{source_end} is not a span")

    tolerance = 1e-6
    for region in fresh_regions:
        if (region["start"] < source_start - tolerance
                or region["end"] > source_end + tolerance):
            raise ValueError(
                f"splice_region_index: a re-measured region "
                f"{region['start']}-{region['end']}s lies outside the span "
                f"{source_start}-{source_end}s it replaces. The padding "
                f"leaked, and splicing it would overwrite speech that was "
                f"never re-measured.")

    kept = [r for r in (index_doc.get("speech_regions") or [])
            if r["end"] <= source_start + tolerance
            or r["start"] >= source_end - tolerance]
    merged = sorted(kept + [dict(r) for r in fresh_regions],
                    key=lambda r: (r["start"], r["end"]))

    out = dict(index_doc)
    out["speech_regions"] = merged
    # `word_end_times` is derived, and a consumer reading it beside a
    # spliced `speech_regions` would otherwise be reading two vintages.
    if "word_end_times" in out:
        out["word_end_times"] = [
            w["end"] for r in merged for w in (r.get("words") or [])]
    return out
