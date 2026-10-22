"""Deterministic per-channel source audio quality measurements (FI-12).

One source is measured per invocation. Each decoded channel is written to
one temporary mono WAV at a time, keeping full-source memory bounded. The
BS.1770 loudness and true-peak values come from the same ``loudnorm``
measurement used by :mod:`library.tools.master_loudness`.

Measuring a source out of band
------------------------------
This lane was only ever called by hand, so a pipeline run that never
measured it left every audio reading absent - and the technical leg's
``usable_speech`` indicator absent with it, for every candidate. The
functions below bring the lane INTO a run: ``stored_record`` says what
this lane already holds for a source, ``ensure_measured`` gives one
source its reading (measured now, or the one it already carries), and
``ensure_readings`` does it for the sources of a set of candidates and
reports what happened to each.

A reading that could not be taken is reported with its reason and stays
absent. It is never a zero, and it never fails the run that asked.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from library.tools import footage_identity, source_memory
from library.tools.heavy_work_lock import heavy_work_lock
from library.tools.master_loudness import _loudnorm_measure

ANALYSIS_RATE_HZ = 48_000
INTERVAL_SECONDS = 1.0
SILENCE_THRESHOLD_DBFS = -60.0
SNR_PERCENTILES = (10.0, 95.0)
METHOD = "ffmpeg-pcm-s16le/48kHz; BS.1770 via master_loudness._loudnorm_measure; FI-12-v2"


def _run(args: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"required media tool not found: {args[0]}") from exc
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {(result.stderr or '')[-1000:]}")
    return result


def _probe(path: str) -> list[dict]:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index,channels,sample_rate",
        "-of", "json", path,
    ], timeout=120)
    streams = json.loads(result.stdout or "{}").get("streams", [])
    if not streams:
        raise ValueError(f"no audio streams in {path}")
    for stream in streams:
        if not isinstance(stream.get("channels"), int) or stream["channels"] < 1:
            raise ValueError(f"audio stream has no valid channel count: {stream}")
    return streams


def _dbfs(rms: float) -> float | None:
    if rms <= 0:
        return None
    return 20.0 * math.log10(rms)


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def _channel_pcm(path: str, audio_ordinal: int, channel: int,
                 output: Path) -> None:
    # Decode only this channel. The next channel starts after this temporary
    # WAV has been fully measured and removed.
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", path, "-map", f"0:a:{audio_ordinal}",
        "-af", f"pan=mono|c0=c{channel}", "-ar", str(ANALYSIS_RATE_HZ),
        "-ac", "1", "-c:a", "pcm_s16le", "-y", str(output),
    ])


def _measure_channel(wav_path: Path, stream_index: int, channel_index: int) -> dict:
    with wave.open(str(wav_path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("decoded channel is not mono 16-bit PCM")
        rate = wav.getframerate()
        if rate != ANALYSIS_RATE_HZ:
            raise ValueError(f"decoded sample rate is {rate}, expected {ANALYSIS_RATE_HZ}")
        frames_per_interval = round(rate * INTERVAL_SECONDS)
        interval_rows = []
        silence_intervals = []
        rms_windows = []
        sub_rms_db: list[float] = []
        clipped_samples = 0
        total_samples = 0
        elapsed = 0.0
        silence_start = None
        while True:
            raw = wav.readframes(frames_per_interval)
            if not raw:
                break
            samples_i16 = np.frombuffer(raw, dtype="<i2")
            samples = samples_i16.astype(np.float64) / 32768.0
            count = int(samples.size)
            start, end = elapsed, elapsed + count / rate
            elapsed = end
            total_samples += count
            clipped_samples += int(np.count_nonzero(
                np.abs(samples_i16.astype(np.int32)) >= 32767))
            rms = float(np.sqrt(np.mean(samples * samples))) if count else 0.0
            peak = float(np.max(np.abs(samples))) if count else 0.0
            rms_db = _dbfs(rms)
            peak_db = _dbfs(peak)
            rms_windows.append(rms_db if rms_db is not None else -math.inf)

            spectrum = np.abs(np.fft.rfft(samples * np.hanning(count))) if count else np.array([])
            frequencies = np.fft.rfftfreq(count, 1.0 / rate) if count else np.array([])
            # Power-weighted centroid: magnitude weighting biases a pure
            # tone sharp (measured +7.5 Hz on a 440 Hz fixture); power
            # lands on the peak bin.
            power = spectrum * spectrum
            power_sum = float(power.sum()) if power.size else 0.0
            centroid = (float(np.dot(frequencies, power) / power_sum)
                        if power_sum else None)
            # Keep tonal interference distinct from broadband noise. The
            # dominant-bin ratio is a screening proxy, not a psychoacoustic
            # hum verdict; the benchmark adapter combines it with mains
            # frequency bands and reports a review candidate.
            peak_bin = int(np.argmax(power)) if power.size else 0
            tonal_peak_hz = float(frequencies[peak_bin]) if power.size else None
            tonal_peak_fraction = (float(power[peak_bin] / power_sum)
                                   if power_sum else None)
            speech_band = (frequencies >= 300.0) & (frequencies <= 3400.0)
            speech_band_fraction = (float(power[speech_band].sum() / power_sum)
                                    if power_sum else None)
            positive = spectrum[spectrum > 0]
            flatness = (float(np.exp(np.mean(np.log(positive))) /
                              np.mean(positive)) if positive.size else None)
            interval_rows.append({
                "start_seconds": round(start, 6), "end_seconds": round(end, 6),
                "rms_dbfs": None if rms_db is None else round(rms_db, 3),
                "sample_peak_dbfs": None if peak_db is None else round(peak_db, 3),
                "spectral_centroid_hz": None if centroid is None else round(centroid, 2),
                "spectral_flatness": None if flatness is None else round(flatness, 6),
                "tonal_peak_hz": None if tonal_peak_hz is None else round(tonal_peak_hz, 2),
                "tonal_peak_power_fraction": (None if tonal_peak_fraction is None
                                              else round(tonal_peak_fraction, 6)),
                "speech_band_energy_fraction": (None if speech_band_fraction is None
                                                else round(speech_band_fraction, 6)),
            })

            # Silence is resolved at 100 ms, while retained measurement rows
            # are one second. This gives short gaps a bounded, explicit interval.
            # The same 100 ms parts feed the noise-floor percentiles: with
            # one-second windows a two-second fixture offers two points and
            # percentile interpolation never lands on either level.
            silence_frames = max(1, round(rate * 0.1))
            for offset in range(0, count, silence_frames):
                part = samples[offset:offset + silence_frames]
                part_rms = float(np.sqrt(np.mean(part * part))) if part.size else 0.0
                part_db = _dbfs(part_rms)
                if part_db is not None:
                    sub_rms_db.append(part_db)
                part_start = start + offset / rate
                silent = part_db is None or part_db <= SILENCE_THRESHOLD_DBFS
                if silent and silence_start is None:
                    silence_start = part_start
                elif not silent and silence_start is not None:
                    silence_intervals.append({"start_seconds": round(silence_start, 6),
                                              "end_seconds": round(part_start, 6)})
                    silence_start = None
        if silence_start is not None:
            silence_intervals.append({"start_seconds": round(silence_start, 6),
                                      "end_seconds": round(elapsed, 6)})

    loudness = _loudnorm_measure(str(wav_path))
    finite_rms = np.asarray([
        v for v in rms_windows
        if math.isfinite(v) and v > SILENCE_THRESHOLD_DBFS
    ], dtype=np.float64)
    finite_sub = np.asarray([
        v for v in sub_rms_db if v > SILENCE_THRESHOLD_DBFS
    ], dtype=np.float64)
    noise_floor = (float(np.percentile(finite_sub, SNR_PERCENTILES[0]))
                   if finite_sub.size else None)
    signal_level = (float(np.percentile(finite_sub, SNR_PERCENTILES[1]))
                    if finite_sub.size else None)
    snr = signal_level - noise_floor if signal_level is not None and noise_floor is not None else None
    finite_peak = [row["sample_peak_dbfs"] for row in interval_rows
                   if row["sample_peak_dbfs"] is not None]
    median_rms = float(np.median(finite_rms)) if finite_rms.size else None
    peak_dbfs = max(finite_peak) if finite_peak else None
    return {
        "stream_index": stream_index,
        "channel_index": channel_index,
        "sample_rate_hz": rate,
        "duration_seconds": round(elapsed, 6),
        "integrated_lufs": _finite(loudness["input_i"]),
        "true_peak_dbtp": _finite(loudness["input_tp"]),
        "loudness_range_lu": _finite(loudness["input_lra"]),
        "clipping": {
            "threshold": "absolute PCM sample >= 32767/32768 of full scale",
            "clipped_sample_count": clipped_samples,
            "sample_count": total_samples,
            "clipped_fraction": clipped_samples / total_samples if total_samples else 0.0,
        },
        "noise_floor_dbfs": None if noise_floor is None else round(noise_floor, 3),
        "snr_db": None if snr is None else round(snr, 3),
        "snr_method": "p95 minus p10 of non-silent 100 ms RMS levels; temporal contrast estimate",
        "intelligibility_proxies": {
            "speech_band_hz": [300, 3400],
            "speech_band_energy_fraction_by_interval": [
                row["speech_band_energy_fraction"] for row in interval_rows
            ],
            "interpretation": "frequency coverage proxy only; not intelligibility or word recognition",
        },
        "silence_threshold_dbfs": SILENCE_THRESHOLD_DBFS,
        "silence_intervals": silence_intervals,
        "dynamics": {
            "rms_p95_minus_p5_db": (round(float(np.percentile(finite_rms, 95) -
                                                     np.percentile(finite_rms, 5)), 3)
                                     if finite_rms.size else None),
            "crest_factor_db": (round(peak_dbfs - median_rms, 3)
                                 if peak_dbfs is not None and median_rms is not None else None),
        },
        "intervals": interval_rows,
    }


def measure_audio_quality(source_file: str, *, root: Path | None = None,
                          scratch_parent: str | None = None,
                          force: bool = False) -> dict:
    """Measure every decoded channel and persist M11 under the source digest.

    This performs a full source decode. Callers must hold the heavy-work
    lock, process sources serially, and provide only read-only source media.
    """
    source_file = os.path.abspath(source_file)
    fingerprint = footage_identity.fingerprint(source_file)
    digest = str(fingerprint["content_digest"])
    if not force:
        cached = source_memory.read_audio_quality(digest, root)
        if cached is not None and cached["method"] == METHOD:
            return {key: value for key, value in cached.items()
                    if key != "evidence_envelope"}
    streams = _probe(source_file)
    channels = []
    with tempfile.TemporaryDirectory(prefix="fi12-audio-",
                                     dir=scratch_parent) as scratch:
        temp = Path(scratch)
        for ordinal, stream in enumerate(streams):
            for channel_index in range(stream["channels"]):
                wav_path = temp / f"stream-{ordinal}-channel-{channel_index}.wav"
                _channel_pcm(source_file, ordinal, channel_index, wav_path)
                try:
                    channels.append(_measure_channel(
                        wav_path, int(stream["index"]), channel_index))
                finally:
                    wav_path.unlink(missing_ok=True)
    record = {
        "schema_version": 1,
        "content_digest": digest,
        "size_bytes": fingerprint["size_bytes"],
        "source_file": source_file,
        "method": METHOD,
        "channel_count": len(channels),
        "channels": channels,
    }
    source_memory.write_audio_quality(digest, record, root)
    return record


# ── Bringing the lane into a run ─────────────────────────────────────


def stored_record(source_file: str, *,
                  root: Path | None = None) -> dict | None:
    """The M11 reading this lane already holds for one source.

    The reading is filed under the source's own content digest, so the
    same file is the same reading. A record written by ANOTHER version of
    this lane is not this lane's reading: it is reported as absent, so
    the caller measures it again rather than reusing numbers this lane
    did not produce.
    """
    if not source_file:
        return None
    try:
        digest = str(
            footage_identity.fingerprint(source_file)["content_digest"])
    except (OSError, KeyError):
        return None
    record = source_memory.read_audio_quality(digest, root)
    if not isinstance(record, dict) or record.get("method") != METHOD:
        return None
    return record


def ensure_measured(source_file: str, *, root: Path | None = None,
                    force: bool = False) -> dict:
    """The M11 reading for one source, measured now when it has none.

    The report names what happened and never raises:

        {"status": "already_measured" | "measured" | "unmeasurable",
         "reason": str | None,
         "record": dict | None}

    A source with no audio stream, a missing file and a failed
    measurement are all reported as ``unmeasurable`` with the reason, and
    the reading stays absent: an absent measurement is a fact, not a
    zero, and the run that asked for it is not the measurement's to fail.
    """
    if not source_file or not os.path.exists(source_file):
        return {"status": "unmeasurable", "reason": "source_not_found",
                "record": None}
    if not force:
        stored = stored_record(source_file, root=root)
        if stored is not None:
            return {"status": "already_measured", "reason": None,
                    "record": stored}
    try:
        with heavy_work_lock("vep-audio-quality", "broll.resolve:audio"):
            record = measure_audio_quality(source_file, root=root, force=force)
    except ValueError as exc:
        # No audio stream in the file, or a channel count that cannot be
        # measured: there is no speech here to read.
        return {"status": "unmeasurable", "reason": str(exc), "record": None}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"status": "unmeasurable",
                "reason": f"measurement_failed: {exc}", "record": None}
    return {"status": "measured", "reason": None, "record": record}


def source_duration(source_file: str) -> float | None:
    """The source's duration in seconds, or None when it cannot be read.

    The lane decodes the WHOLE source, so a caller that bounds its work
    needs to know how long a source is before it starts.
    """
    if not source_file or not os.path.exists(source_file):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", source_file],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        duration = json.loads(result.stdout or "{}")["format"]["duration"]
    except (KeyError, TypeError, ValueError):
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def ensure_readings(clips: list, *, root: Path | None = None,
                    max_sources: int | None = None,
                    max_source_seconds: float | None = None,
                    budget_seconds: float | None = None) -> dict:
    """Make sure each clip's source carries an M11 reading.

    The candidates a selection offers are compared on more than their
    picture, and one of the indicators that comparison reads is the share
    of the speech band carrying energy. That reading is this lane's, so a
    run that never measured it left the indicator absent on every
    candidate.

    ``clips`` are catalog entries; each names its source by
    ``source_file`` or ``path``. Two clips behind one file are one source
    and are measured once. A source that already carries a reading is not
    measured again.

    The lane decodes a WHOLE source, so this call is bounded three ways
    and says which bound each source ran into:

    ``max_sources``
        how many sources ONE call measures;
    ``max_source_seconds``
        a source longer than this is not measured at all - a run cannot
        spend half an hour on one file;
    ``budget_seconds``
        the wall clock one call may spend measuring.

    The sources it measured are filed, so the next call continues with
    the ones it did not reach.

    The report names every source it was asked about:

        {"sources": int,
         "already_measured": [clip_id, ...],
         "measured": [clip_id, ...],
         "unmeasurable": {clip_id: reason, ...},
         "not_reached": {clip_id: reason, ...}}
    """
    report: dict = {"sources": 0, "already_measured": [], "measured": [],
                    "unmeasurable": {}, "not_reached": {}}
    seen: set[str] = set()
    measured_count = 0
    started = time.monotonic()
    for clip in clips or []:
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("clip_id") or ""
        path = clip.get("source_file") or clip.get("path") or ""
        if not path:
            report["unmeasurable"][clip_id] = "no_source_path"
            continue
        key = os.path.abspath(path)
        if key in seen:
            continue
        seen.add(key)
        report["sources"] += 1
        if stored_record(path, root=root) is not None:
            report["already_measured"].append(clip_id)
            continue
        if max_sources is not None and measured_count >= max_sources:
            report["not_reached"][clip_id] = (
                f"this call measured {max_sources} source(s) already; the "
                f"readings are stored for the next run")
            continue
        if max_source_seconds is not None:
            duration = clip.get("duration_seconds")
            if not isinstance(duration, (int, float)) or duration <= 0:
                duration = source_duration(path)
            if isinstance(duration, (int, float)) and duration > max_source_seconds:
                report["not_reached"][clip_id] = (
                    f"the source is {duration:.0f}s long and this call "
                    f"measures at most {max_source_seconds:.0f}s of source")
                continue
        if budget_seconds is not None and \
                time.monotonic() - started >= budget_seconds:
            report["not_reached"][clip_id] = (
                f"this call spent its {budget_seconds:.0f}s measuring "
                f"budget; the readings are stored for the next run")
            continue
        outcome = ensure_measured(path, root=root)
        measured_count += 1
        if outcome["status"] == "measured":
            report["measured"].append(clip_id)
        else:
            report["unmeasurable"][clip_id] = (
                outcome["reason"] or "unmeasurable")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_file")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--scratch-parent")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--problems", action="store_true",
                        help="include timestamped screening candidates and review actions")
    parser.add_argument("--speech-span", action="append", default=[], metavar="START:END",
                        help="source-time speech span in seconds; repeatable")
    parser.add_argument("--speech-segments-json", type=Path,
                        help="JSON array of source-time transcript segments for repeated-phrase screening")
    args = parser.parse_args(argv)
    speech_spans = []
    for value in args.speech_span:
        try:
            start, end = (float(part) for part in value.split(":"))
        except (TypeError, ValueError):
            parser.error(f"invalid --speech-span {value!r}; expected START:END seconds")
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            parser.error(f"invalid --speech-span {value!r}; expected 0 <= START < END")
        speech_spans.append((start, end))
    record = measure_audio_quality(args.source_file, root=args.root,
                                   scratch_parent=args.scratch_parent,
                                   force=args.force)
    output = record
    if args.problems:
        from library.tools.analysis.audio_defect_benchmark import (
            problem_report_from_audio_quality,
        )

        speech_segments = []
        if args.speech_segments_json:
            speech_segments = json.loads(
                args.speech_segments_json.read_text(encoding="utf-8"))
            if not isinstance(speech_segments, list):
                parser.error("--speech-segments-json must contain a JSON array")
            for row in speech_segments:
                try:
                    start, end = (float(row["start_seconds"]),
                                  float(row["end_seconds"]))
                except (KeyError, TypeError, ValueError):
                    parser.error("--speech-segments-json rows need numeric "
                                 "start_seconds and end_seconds")
                if (not isinstance(row.get("text"), str)
                        or not math.isfinite(start)
                        or not math.isfinite(end) or start < 0 or end <= start):
                    parser.error("--speech-segments-json rows need text and "
                                 "0 <= start_seconds < end_seconds")
        output = {"measurement": record,
                  "problem_report": problem_report_from_audio_quality(
                      record, speech_spans=speech_spans,
                      speech_segments=speech_segments)}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
