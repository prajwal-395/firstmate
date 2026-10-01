#!/usr/bin/env python3
"""
Advanced Speech Analysis — Prosody

Supplements WhisperX transcription (step 1.04) with prosodic features
that no other pipeline component captures:

- Pitch (F0) contour at 10ms resolution
- Speaking rate (from voicing regions)
- Voice quality (jitter, shimmer, HNR)
- Intensity (loudness) contour at 50ms resolution

Uses parselmouth (Python wrapper for Praat), the gold standard in
phonetics research. Signal processing, not a neural model — deterministic
and well-understood. ±1-2Hz pitch accuracy on clean speech.

Why this matters for editing:
- Pitch contour shows WHERE the speaker emphasizes, asks questions
  (rising intonation), or trails off (falling pitch)
- Speaking rate shows engagement/energy shifts
- Voice quality metrics (jitter, shimmer, HNR) are reported as numbers.
  No vocal-register label is assigned: on field recordings HNR measures
  ambient noise as much as the voice (issue #263).
- The LLM can interpret all of this directly — no need for a separate
  emotion classifier (Gemma4 vision + transcript + prosody already
  give richer emotional signal than any 4-class audio model)

Redundant components removed:
- Emotion detection: covered by Gemma4 vision (mood/body language/facial
  expressions), transcript content, and prosodic features themselves
- Speaker diarization: WhisperX (and its pyannote integration) left on
  2026-09-24, so nothing here diarizes. Per-ISO timelines separate
  voices by track in `timeline_transcript`; one-audio-path timelines
  with no declared roster diarize-then-transcribe through
  `library/tools/single_track_diarization.py`.
- Sound events: already in temporal index + vision pipeline

Input:  { "audio_files": [{"path": "...", "clip_id": "..."}], "output_dir": "..." }
Output: Per-clip JSON with prosody data


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**A file on disk is not a measurement.**
Judge a step by what it MEASURED.
`speech_advanced_pipeline` raises `ProsodyUnavailable` and writes nothing rather than recording an error as a result; step 1.05 rejects a hollow profile and reports `available: false`, which `check_output_is_real` reads as a failed step. [why](docs/RULE_EVIDENCE.md#hollow-prosody-files-cached)
[why](docs/RULE_EVIDENCE.md#hollow-prosody-files-cached)
"""
import json
import os
import sys
import time
import numpy as np
from pathlib import Path


class ProsodyUnavailable(RuntimeError):
    """No prosody could be measured for this clip.

    Raised rather than returned as `{"method": None, "error": ...}`. That
    dict was written to `<clip>_prosody.json` like any other result, so
    step 1.05 collected seventeen of them, counted seventeen profiles and
    reported `available: true` in 0.1 seconds - and 4.2 KB of identical
    "parselmouth not installed" records were serialised into the
    creative-direction prompt. A file on disk also caches: the next run
    saw the clip as already analysed and skipped it.
    """


# ── Per-word emphasis (fidelity rung 5b) ─────────────────────────────
#
# Clip-level pitch stats say the speaker varies; they do not say WHERE.
# A plan that punches in "on the most emphasized word" needs one number
# per word, measured from the Voz+MFA word timings step 1.04 already
# timed - not a second transcription, and not the model guessing from
# punctuation. Three unit-scaled terms, each relative to its own
# baseline so a loud fast high-pitched speaker does not read as
# emphasizing everything:
#
#   f0_term   = f0_rel_semitones / 3.0   (word median F0 vs the clip's
#               own voiced median; 3 semitones is a clear excursion)
#   loud_term = loud_rel_db / 4.0        (word median dB vs the phrase's
#               own word-span median; 4 dB is clearly louder)
#   dur_term  = log2(dur_ratio)          (actual vs expected from the
#               speaker's own seconds-per-letter; +1 is twice as slow)
#
#   emphasis = mean of the terms that could be measured.
#
# Median, not mean, inside each word span: one octave-error frame
# (Praat spikes to ~580 Hz on this footage) must not decide a word.
# A word with no voiced frames carries no f0 term, a word between two
# intensity samples carries no loud term - the score is the mean of
# what is there, and `terms` says how many answered. Duration always
# answers (it is the aligner's own stamps), so every timed word scores.

#: Divisors in WORD_EMPHASIS_FORMULA. Perceptual units, not fitted:
#: they hold across clips because each term is already relative.
F0_EMPHASIS_DIVISOR_ST = 3.0
LOUD_EMPHASIS_DIVISOR_DB = 4.0

WORD_EMPHASIS_FORMULA = (
    "emphasis = mean(f0_rel_semitones/3.0, loud_rel_db/4.0, "
    "log2(dur_ratio)) over the terms that measured"
)


def _median(values):
    """Median of a non-empty list, or None. No numpy: this runs in tests."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def _letters(word: str) -> int:
    return sum(1 for c in str(word or "") if c.isalnum())


def measure_word_prosody(speech_regions, pitch_samples,
                         intensity_samples) -> dict:
    """One emphasis row per timed word. Pure: no parselmouth, no disk.

    `speech_regions` are step 1.04's `{start, end, words: [{word,
    start, end}]}` (Voz+MFA stamps, audio clock == source clock).
    `pitch_samples` / `intensity_samples` are `(time, value-or-None)`
    lists at whatever resolution Praat produced. Returns
    `{"words", "baseline_f0_median_hz", "sec_per_letter", "formula"}`.
    A word that measures nothing still rows - with nulls - so the
    table and the transcript stay joinable word for word.
    """
    import math

    regions = speech_regions or []
    voiced = [f for _, f in pitch_samples or [] if f is not None]
    baseline = _median(voiced) if voiced else None

    total_letters = 0
    total_speech = 0.0
    for region in regions:
        for word in region.get("words", []) or []:
            total_letters += _letters(word.get("word"))
            try:
                total_speech += float(word.get("end", 0)) - float(
                    word.get("start", 0))
            except (TypeError, ValueError):
                pass
    sec_per_letter = (total_speech / total_letters
                      if total_letters > 0 and total_speech > 0 else None)

    rows = []
    for region in regions:
        region_words = region.get("words", []) or []
        # The phrase reference is the median intensity over this region's
        # own word spans - the same population the words are drawn
        # from. A mean over the whole region would drag silence and
        # pauses into the reference and read every word as loud
        # (measured +9.4 dB on 001 clip_017 before this).
        phrase_db = [
            db for (time, db) in intensity_samples or []
            if db is not None and any(
                w.get("start", 0) <= time < w.get("end", 0)
                for w in region_words
                if isinstance(w, dict))
        ]
        phrase_mean = (_median(phrase_db) if phrase_db else None)

        for word in region_words:
            if not isinstance(word, dict):
                continue
            text = word.get("word", "")
            try:
                start = float(word.get("start", 0))
                end = float(word.get("end", 0))
            except (TypeError, ValueError):
                continue
            f0_in = [f for (time, f) in pitch_samples or []
                     if f is not None and start <= time < end]
            db_in = [db for (time, db) in intensity_samples or []
                     if db is not None and start <= time < end]

            f0_st = loud_db = dur_ratio = None
            if f0_in and baseline:
                f0_st = round(12 * math.log2(_median(f0_in) / baseline),
                              2)
            if db_in and phrase_mean is not None:
                loud_db = round(_median(db_in) - phrase_mean, 2)
            letters = _letters(text)
            if letters and sec_per_letter:
                expected = letters * sec_per_letter
                if expected > 0 and end > start:
                    dur_ratio = round((end - start) / expected, 3)

            terms = []
            if f0_st is not None:
                terms.append(f0_st / F0_EMPHASIS_DIVISOR_ST)
            if loud_db is not None:
                terms.append(loud_db / LOUD_EMPHASIS_DIVISOR_DB)
            if dur_ratio is not None and dur_ratio > 0:
                terms.append(math.log2(dur_ratio))
            emphasis = (round(sum(terms) / len(terms), 3)
                        if terms else None)

            rows.append({
                "word": text,
                "start": round(start, 3),
                "end": round(end, 3),
                "f0_rel_semitones": f0_st,
                "loud_rel_db": loud_db,
                "dur_ratio": dur_ratio,
                "emphasis": emphasis,
                "terms": len(terms),
            })

    return {
        "words": rows,
        "baseline_f0_median_hz": (round(baseline, 1)
                                  if baseline else None),
        "sec_per_letter": (round(sec_per_letter, 4)
                           if sec_per_letter else None),
        "formula": WORD_EMPHASIS_FORMULA,
    }


def analyze_prosody(audio_path: str, speech_regions: list = None) -> dict:
    """Extract prosodic features using Praat via parselmouth.

    Accuracy: HIGH — Praat is the gold standard in phonetics research.
    F0 extraction via autocorrelation: ±1-2Hz on clean speech.
    Degrades with background noise, reverb, or very low-quality mics.
    All metrics are deterministic (signal processing, not a neural model).

    Features:
    - Pitch (F0) contour at 10ms resolution
    - Speaking rate (estimated from voicing regions)
    - Voice quality metrics (jitter, shimmer, HNR)
    - Intensity (loudness) contour at 50ms resolution
    """
    try:
        import parselmouth
        from parselmouth.praat import call

        print("  Analyzing prosody (parselmouth)...", file=sys.stderr)

        sound = parselmouth.Sound(audio_path)
        duration = sound.duration

        # 1. Pitch extraction (F0 contour)
        pitch = call(sound, "To Pitch", 0.0, 75, 600)

        pitch_values = []
        time_step = 0.01  # 10ms
        t = 0
        while t < duration:
            f0 = call(pitch, "Get value at time", t, "Hertz", "Linear")
            pitch_values.append({
                "time": round(t, 3),
                "f0_hz": round(float(f0), 1) if not np.isnan(f0) else None,
            })
            t += time_step

        # Summary statistics
        voiced_f0 = [p["f0_hz"] for p in pitch_values if p["f0_hz"] is not None]
        pitch_stats = {}
        if voiced_f0:
            pitch_stats = {
                "mean_f0_hz": round(float(np.mean(voiced_f0)), 1),
                "median_f0_hz": round(float(np.median(voiced_f0)), 1),
                "min_f0_hz": round(float(np.min(voiced_f0)), 1),
                "max_f0_hz": round(float(np.max(voiced_f0)), 1),
                "std_f0_hz": round(float(np.std(voiced_f0)), 1),
                "range_semitones": round(float(
                    12 * np.log2(max(voiced_f0) / min(voiced_f0))
                ), 1) if min(voiced_f0) > 0 else 0,
                "voicing_percentage": round(
                    len(voiced_f0) / len(pitch_values) * 100, 1
                ),
            }

        # Voice quality inputs: jitter, shimmer (from the point process)
        # and HNR (mean harmonics-to-noise ratio over the clip).
        point_process = call(sound, "To PointProcess (periodic, cc)", 75, 600)

        try:
            jitter = call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
            shimmer = call([sound, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)
        except Exception:
            jitter = None
            shimmer = None

        try:
            harmonicity = call(sound, "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0)
            hnr = call(harmonicity, "Get mean", 0, 0)
        except Exception:
            hnr = None

        # 2. Voice quality (jitter, shimmer, HNR) - MEASURED ONLY.
        # No vocal-register label is assigned (issue #263): on field
        # recordings HNR measures ambient noise as much as the voice.
        # Sixteen of seventeen clips on project 001 read "breathy"
        # under the old hnr > 20 / > 10 ladder - and so does audio with
        # no voice in it at all. A number a reader can judge beats a
        # word that means "outdoors", so HNR is reported and the label
        # is gone.
        def _measured(value, places):
            """A Praat return as a rounded float, or None when unmeasured.

            Explicit None/NaN checks: a measured 0.0 is a number, and
            `if value` would have discarded it as unmeasured.
            """
            if value is None:
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return round(number, places) if not np.isnan(number) else None

        voice_quality = {
            "jitter_local": _measured(jitter, 5),
            "shimmer_local": _measured(shimmer, 5),
            "hnr_db": _measured(hnr, 1),
        }

        # 3. Speaking rate (from speech regions if available)
        speaking_rate = None
        if speech_regions:
            total_speech_duration = sum(
                r.get("end", 0) - r.get("start", 0) for r in speech_regions
            )
            total_words = sum(
                len(r.get("words", [])) for r in speech_regions
            )
            if total_speech_duration > 0:
                speaking_rate = {
                    "words_per_minute": round(total_words / total_speech_duration * 60, 1),
                    "total_words": total_words,
                    "total_speech_seconds": round(total_speech_duration, 2),
                }

        # 4. Intensity (loudness) contour
        intensity = call(sound, "To Intensity", 100, 0)
        intensity_values = []
        t = 0
        while t < duration:
            try:
                db = call(intensity, "Get value at time", t, "cubic")
                intensity_values.append({
                    "time": round(t, 3),
                    "db": round(float(db), 1) if not np.isnan(db) else None,
                })
            except Exception:
                pass
            t += 0.05  # 50ms resolution for intensity

        result = {
            "method": "parselmouth-praat",
            "accuracy": "high — gold standard, ±1-2Hz F0",
            "pitch_stats": pitch_stats,
            "pitch_contour_10ms": pitch_values[:3000],  # Cap at 30s
            "voice_quality": voice_quality,
            "speaking_rate": speaking_rate,
            "intensity_contour_50ms": intensity_values[:1200],  # Cap at 60s
            "duration_s": round(duration, 2),
        }

        # Per-word emphasis from the Voz+MFA stamps, measured on the
        # FULL in-memory contours - the saved contours above are capped
        # at 30/60 s and a word past the cap would read as unvoiced
        # against them. A clip with no speech regions still profiles:
        # an empty table, not a missing one.
        word_rows = [
            (p["time"], p["f0_hz"]) for p in pitch_values
        ]
        word_db = [
            (v["time"], v["db"]) for v in intensity_values
        ]
        word_prosody = measure_word_prosody(
            speech_regions, word_rows, word_db)
        result["word_prosody"] = word_prosody["words"]
        result["word_prosody_formula"] = word_prosody["formula"]
        result["word_prosody_baseline"] = {
            "f0_median_hz": word_prosody["baseline_f0_median_hz"],
            "sec_per_letter": word_prosody["sec_per_letter"],
            "n_words": len(word_prosody["words"]),
            "n_scored": sum(
                1 for row in word_prosody["words"]
                if row.get("emphasis") is not None),
        }

        print(f"  prosody: F0 mean={pitch_stats.get('mean_f0_hz', '?')}Hz, "
              f"voicing={pitch_stats.get('voicing_percentage', '?')}%, "
              f"HNR={voice_quality.get('hnr_db', '?')}dB",
              file=sys.stderr)
        return result

    except ImportError as exc:
        raise ProsodyUnavailable(
            "praat-parselmouth is not installed, so no prosodic feature can "
            "be measured. It is declared in requirements.txt and as step "
            f"1.05's `env.parselmouth` requirement, which now refuses "
            f"before the run starts rather than here: pip install "
            f"praat-parselmouth ({exc})"
        ) from exc
    except Exception as e:
        print(f"  ERROR: prosody analysis failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        raise ProsodyUnavailable(f"prosody analysis failed: {e}") from e


# ── Main ────────────────────────────────────────────────────────────

def analyze_speech_advanced(
    audio_path: str,
    speech_regions: list = None,
    output_dir: str = None,
    clip_id: str = None,
) -> dict:
    """Run prosody analysis on an audio file.

    This supplements the temporal index (step 1.04) with prosodic
    features that no other pipeline component captures.
    """
    if clip_id is None:
        clip_id = Path(audio_path).stem

    if output_dir is None:
        output_dir = os.path.dirname(audio_path)
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Prosody Analysis: {clip_id}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    start_time = time.time()

    # Raises ProsodyUnavailable rather than returning an error record.
    # Nothing is written for a clip that could not be analysed: a profile
    # file IS the cache, so writing one would make the failure permanent
    # as well as silent.
    results = {
        "clip_id": clip_id,
        "audio_file": audio_path,
        "prosody": analyze_prosody(audio_path, speech_regions),
    }

    elapsed = time.time() - start_time
    results["analysis_time_s"] = round(elapsed, 1)

    output_path = os.path.join(output_dir, f"{clip_id}_prosody.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n  ✓ Prosody analysis complete in {elapsed:.1f}s", file=sys.stderr)
    print(f"  ✓ Saved to {output_path}", file=sys.stderr)

    return results


def main():
    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser(
            description="Prosody analysis (pitch, voice quality, speaking rate)"
        )
        parser.add_argument("audio_file", help="Path to audio file")
        parser.add_argument("--output-dir", help="Output directory")
        parser.add_argument("--clip-id", help="Clip identifier")
        parser.add_argument("--speech-regions",
                          help="Path to temporal index JSON (for speech regions)")
        args = parser.parse_args()

        speech_regions = None
        if args.speech_regions and os.path.exists(args.speech_regions):
            with open(args.speech_regions) as f:
                ti = json.load(f)
                speech_regions = ti.get("speech_regions", [])

        result = analyze_speech_advanced(
            args.audio_file,
            speech_regions=speech_regions,
            output_dir=args.output_dir,
            clip_id=args.clip_id,
        )
        json.dump(result, sys.stdout, indent=2)
    else:
        data = json.loads(sys.stdin.read())
        audio_files = data.get("audio_files", [])
        output_dir = data.get("output_dir")
        speech_boundaries = data.get("speech_boundaries", {})

        results = []
        failures = []
        for af in audio_files:
            clip_id = af.get("clip_id")
            regions = speech_boundaries.get(clip_id) if clip_id else None
            try:
                results.append(analyze_speech_advanced(
                    af["path"],
                    speech_regions=regions,
                    output_dir=output_dir,
                    clip_id=clip_id,
                ))
            except ProsodyUnavailable as exc:
                failures.append({"clip_id": clip_id,
                                 "error": str(exc)})
                print(f"  ✗ {clip_id}: {exc}", file=sys.stderr)

        json.dump({"results": results, "failures": failures},
                  sys.stdout, indent=2)
        if failures:
            # A caller that only reads the return code must still see it.
            sys.exit(1)


if __name__ == "__main__":
    main()
