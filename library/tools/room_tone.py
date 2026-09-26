"""Room tone: the measured quiet of each source, kept as reusable audio.

Fidelity probe P3 (doc J-cuts with room tone, geo-podcast): the mix has
no room-tone or noise measurement, and the one J-cut that exists reaches
into unplayed source whose words no transcript covers. A J/L cut built
by trimming one side opens a gap on the speech row, and that gap must be
filled with the room of the source the ear is joining - never left as
digital silence.

This module measures that room, per source, from the footage itself:

- speech is excluded with the caller-supplied word spans in SOURCE
  seconds (the Voz+MFA word timing the spine contract carries - the same
  timings `sub_block_anchor` resolves words through);
- the remaining speech-free stretches ("gaps") are each measured for
  level, and the QUIETEST gap is the room tone: its level in dBFS, its
  spectrum in log bands, and its span;
- the span is staged to a reusable segment file (`stage_fill`), looped
  to the requested fill length when the gap is shorter than the lead or
  lag it must cover.

Nothing here invents signal. A source with no measurable speech-free
stretch refuses (`RoomToneRefused`, the `RenRefusal` shape) instead of
returning a default level - a default presented as measured is the
defect `tests/test_assessment_reports_no_default_as_measured.py` holds
for assessment fields, and it holds for this one too.

Decoding is stdlib `wave` for PCM WAV and ffmpeg otherwise (MXF camera
masters, MP4 proxies). The unit tests generate WAV fixtures with
`wave` itself, so they never need ffmpeg.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import wave

from library.tools.ren_refusal import RenRefusal


class RoomToneRefused(RenRefusal):
    """A source's room tone cannot be measured or staged."""


#: A gap shorter than this is not room tone, it is a breath between
#: words. The bound is about measurement validity, not taste: the
#: spectrum below needs enough samples to say anything, and a fill
#: looped from 80 ms of audio is a machine-gun loop, not a room.
MIN_GAP_SECONDS = 0.30

#: Spectrum bands, in Hz, as log-spaced edges from 20 Hz to 20 kHz.
#: Eight bands: the record says how the room sounds, coarsely, so a
#: fill from the wrong source reads as wrong on the record rather than
#: passing as measured.
SPECTRUM_EDGES_HZ = (20.0, 80.0, 250.0, 700.0, 2000.0, 5000.0,
                     10000.0, 15000.0, 20000.0)

#: Sample rate the measurement runs at. Sources are resampled to it on
#: decode so a 48 kHz master and a 44.1 kHz proxy report comparable
#: spectra.
MEASURE_SR = 48000


def _refuse(what: str, why: str, fix: str) -> RoomToneRefused:
    return RoomToneRefused(what=what, why=why, fix=fix)


def decode_mono(path: str, sample_rate: int = MEASURE_SR) -> tuple:
    """Decode `path` to mono float samples in [-1, 1] plus the rate.

    16- and 32-bit PCM WAV go through stdlib `wave` (no subprocess, exact
    samples); anything else, 24-bit WAV included, through ffmpeg. Refuses when the file is missing, has
    no audio, or decodes to nothing.
    """
    if not os.path.isfile(path):
        raise _refuse(
            f"room tone cannot be measured: {path!r} is not on disk",
            "the measurement reads the source file itself - a level "
            "invented without reading it would be a default reported "
            "as measured.",
            f"point room_tone at the real source file for this clip, "
            f"or drop the J/L cut that needs its room.",
        )
    if path.lower().endswith(".wav"):
        try:
            return _decode_wav(path, sample_rate)
        except wave.Error as exc:
            raise _refuse(
                f"room tone cannot be measured: {path!r} does not decode "
                f"as WAV ({exc})",
                "the file exists but carries no readable PCM audio.",
                "re-export the source as PCM WAV, or drop the J/L cut "
                "that needs its room.",
            )
    return _decode_ffmpeg(path, sample_rate)


def _decode_wav(path: str, sample_rate: int) -> tuple:
    with wave.open(path, "rb") as handle:
        n_channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        n_frames = handle.getnframes()
        raw = handle.readframes(n_frames)
    if n_frames <= 0:
        raise _refuse(
            f"room tone cannot be measured: {path!r} decodes to no audio",
            "the file exists but holds zero frames.",
            "point room_tone at the real source file for this clip, "
            "or drop the J/L cut that needs its room.",
        )
    if width == 2:
        ints = struct.unpack(f"<{n_frames * n_channels}h", raw)
        peak = 32768.0
    elif width == 4:
        ints = struct.unpack(f"<{n_frames * n_channels}i", raw)
        peak = 2147483648.0
    else:
        # 24-bit is what the plan-declared audio chain writes; a refusal
        # here was swallowed as "not measured" on every such stem.
        return _decode_ffmpeg(path, sample_rate)
    mono = []
    for i in range(n_frames):
        frame = ints[i * n_channels:(i + 1) * n_channels]
        mono.append(sum(frame) / (len(frame) * peak))
    if rate != sample_rate:
        mono = _resample(mono, rate, sample_rate)
    return mono, sample_rate


def _resample(samples: list, src_rate: int, dst_rate: int) -> list:
    """Linear resample. Measurement-grade, not delivery-grade."""
    if src_rate == dst_rate or not samples:
        return list(samples)
    ratio = dst_rate / src_rate
    out_len = max(1, int(round(len(samples) * ratio)))
    out = []
    for i in range(out_len):
        pos = i / ratio
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(samples) - 1)
        frac = pos - lo
        out.append(samples[lo] * (1.0 - frac) + samples[hi] * frac)
    return out


def _decode_ffmpeg(path: str, sample_rate: int) -> tuple:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
             "-ar", str(sample_rate), "-f", "f32le", "-acodec",
             "pcm_f32le", "-"],
            capture_output=True, timeout=600,
            encoding=None, check=False,
        )
    except FileNotFoundError as exc:
        raise _refuse(
            "room tone cannot be measured: ffmpeg is not installed",
            "non-WAV sources (MXF masters, MP4 proxies) decode through "
            "ffmpeg, which is absent on this machine.",
            "install ffmpeg, or stage the source as PCM WAV first.",
        ) from exc
    if proc.returncode != 0 or not proc.stdout:
        raise _refuse(
            f"room tone cannot be measured: {path!r} does not decode "
            f"({proc.stderr.decode('utf-8', 'replace')[-300:]})",
            "the file exists but ffmpeg read no audio stream from it.",
            "point room_tone at the real source file for this clip, "
            "or drop the J/L cut that needs its room.",
        )
    count = len(proc.stdout) // 4
    samples = list(struct.unpack(f"<{count}f", proc.stdout))
    return samples, sample_rate


def speech_free_gaps(duration: float, speech_spans: list,
                     min_gap: float = MIN_GAP_SECONDS) -> list:
    """Complement of the speech spans inside [0, duration), longest first.

    Spans are (start, end) in source seconds. Gaps shorter than
    `min_gap` are not room tone (see `MIN_GAP_SECONDS`) and are
    dropped. Returns [(start, end)] sorted longest first so the first
    measurable gap is the most loopable one.
    """
    if duration <= 0:
        return []
    bounds = []
    for span in speech_spans or []:
        try:
            start, end = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        start = max(0.0, start)
        end = min(float(duration), end)
        if end > start:
            bounds.append((start, end))
    bounds.sort()
    merged = []
    for start, end in bounds:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    gaps = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor >= min_gap:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if float(duration) - cursor >= min_gap:
        gaps.append((cursor, float(duration)))
    gaps.sort(key=lambda gap: (gap[1] - gap[0]), reverse=True)
    return gaps


def rms_dbfs(samples) -> float:
    """Level of the samples in dBFS. Digital silence reads -inf."""
    total = 0.0
    count = 0
    for value in samples:
        total += value * value
        count += 1
    if count == 0 or total <= 0.0:
        return float("-inf")
    return 10.0 * math.log10(total / count)


def spectrum_db(samples, sample_rate: int = MEASURE_SR) -> list:
    """Mean magnitude per log band in dB, same scale as `rms_dbfs`.

    Bands are `SPECTRUM_EDGES_HZ`; a band with no energy reads -inf,
    exactly like silence does in `rms_dbfs`.
    """
    import numpy as np

    window = np.asarray(list(samples), dtype=np.float64)
    n = window.shape[0]
    if n == 0:
        return [float("-inf")] * (len(SPECTRUM_EDGES_HZ) - 1)
    mags = np.abs(np.fft.rfft(window)) / n
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    bands = []
    for lo, hi in zip(SPECTRUM_EDGES_HZ[:-1], SPECTRUM_EDGES_HZ[1:]):
        cells = mags[(freqs >= lo) & (freqs < hi)]
        mean = float(cells.mean()) if cells.size else 0.0
        bands.append(20.0 * math.log10(mean) if mean > 0.0
                     else float("-inf"))
    return bands


def measure_room_tone(source_file: str, speech_spans: list,
                      min_gap: float = MIN_GAP_SECONDS) -> dict:
    """The quietest speech-free stretch of `source_file`, measured.

    Returns {"source_file", "sample_rate", "segment_start",
    "segment_end", "level_dbfs", "peak_dbfs", "spectrum_db",
    "spectrum_edges_hz", "gaps_considered", "method"}. Refuses when the
    file carries no audio or no speech-free stretch long enough to
    measure.
    """
    samples, rate = decode_mono(source_file)
    duration = len(samples) / rate
    gaps = speech_free_gaps(duration, speech_spans, min_gap=min_gap)
    if not gaps:
        raise _refuse(
            f"room tone cannot be measured: {os.path.basename(source_file)} "
            f"has no speech-free stretch of {min_gap:.2f}s or longer",
            "every measurable span of the source carries speech, so any "
            "'room tone' would be speech reported as room.",
            "pick a J/L join whose fill source rests between words, or "
            "drop the J/L cut.",
        )
    scored = []
    for start, end in gaps:
        lo = int(start * rate)
        hi = int(end * rate)
        piece = samples[lo:hi]
        scored.append((rms_dbfs(piece), start, end, piece))
    scored.sort(key=lambda row: row[0])
    # Digital silence is not room tone: a gap reading -inf would stage
    # a fill of pure silence, which is the defect this module exists
    # to remove. The quietest AUDIBLE gap wins; a source whose only
    # pauses are digital silence refuses below.
    audible = [row for row in scored if row[0] > float("-inf")]
    if not audible:
        raise _refuse(
            f"room tone cannot be measured: the pauses of "
            f"{os.path.basename(source_file)} are digital silence",
            "every speech-free stretch reads -inf dBFS, so any fill "
            "staged from them would be silence reported as room.",
            "pick a J/L join whose fill source rests in real room "
            "tone, or drop the J/L cut.",
        )
    level, start, end, piece = audible[0]
    peak = max((abs(v) for v in piece), default=0.0)
    # The spectrum is measured on a bounded window of the gap's middle
    # so a 40 s pause does not cost a 40 s DFT: room tone is stationary
    # by selection (it is the quietest stretch), and the record says
    # which window answered.
    window = piece[len(piece) // 4:len(piece) // 4
                   + min(len(piece), rate * 4)]
    record = {
        "source_file": source_file,
        "sample_rate": rate,
        "segment_start": round(start, 3),
        "segment_end": round(end, 3),
        # JSON-safe: a -inf reading (an empty spectrum band) is None
        # on the record, never a float the manifest cannot serialise.
        # `rms_dbfs` / `spectrum_db` keep their -inf in process.
        "level_dbfs": _db_or_none(round(level, 1)),
        "peak_dbfs": _db_or_none(
            round(20.0 * math.log10(peak), 1) if peak > 0.0
            else float("-inf")),
        "spectrum_db": [_db_or_none(round(b, 1)) for b in
                        spectrum_db(window, rate)],
        "spectrum_edges_hz": list(SPECTRUM_EDGES_HZ),
        "gaps_considered": len(gaps),
        "method": (f"quietest of {len(gaps)} speech-free gap(s) "
                   f"(Voz+MFA word spans excluded); spectrum on a 4 s "
                   f"window at the gap's middle"),
    }
    return record


def _db_or_none(value):
    """A dB reading the manifest can serialise: None for -inf."""
    if isinstance(value, float) and value == float("-inf"):
        return None
    return value


def stage_fill(source_file: str, record: dict, fill_seconds: float,
               out_path: str) -> dict:
    """Stage a reusable room-tone segment of `fill_seconds` at `out_path`.

    The measured gap is looped to length when it is shorter than the
    fill (room tone is stationary by selection - it is the quietest
    stretch - so looping extends the room instead of inventing one).
    Returns {"path", "fill_seconds", "loops", "level_dbfs"}. Refuses a
    non-positive fill length.
    """
    if not (isinstance(fill_seconds, (int, float))
            and not isinstance(fill_seconds, bool)
            and fill_seconds > 0):
        raise _refuse(
            f"room tone cannot be staged: fill length {fill_seconds!r} "
            f"is not a positive number of seconds",
            "a fill of no length is a gap left as digital silence.",
            "plan the J/L cut with a positive lead or lag, or drop it.",
        )
    samples, rate = decode_mono(source_file)
    lo = int(float(record["segment_start"]) * rate)
    hi = int(float(record["segment_end"]) * rate)
    piece = samples[lo:hi]
    if not piece:
        raise _refuse(
            f"room tone cannot be staged: the measured segment "
            f"{record['segment_start']}s-{record['segment_end']}s of "
            f"{os.path.basename(source_file)} reads empty",
            "the measurement and the file disagree - the source changed "
            "under the record.",
            "re-measure the room tone, then re-stage the fill.",
        )
    need = max(1, int(round(float(fill_seconds) * rate)))
    copies = max(1, math.ceil(need / len(piece)))
    loops = copies - 1
    # Crossfade loop joints over 5 ms so the loop does not click. A
    # joint inside stationary room tone is inaudible; a click from a
    # hard loop point would be a defect this module put there. Every
    # interior joint is crossfaded, not just the last one.
    fade = max(1, min(int(rate * 0.005), len(piece) // 2))
    out = list(piece[:need]) if copies == 1 else list(piece)
    for _ in range(loops):
        head = out[len(out) - fade:]
        tail = piece[:]
        joined = []
        for i in range(fade):
            alpha = (i + 1) / (fade + 1)
            joined.append(head[i] * (1.0 - alpha) + tail[i] * alpha)
        out = out[:len(out) - fade] + joined + tail[fade:]
        if len(out) >= need:
            break
    out = out[:need]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    _write_wav(out_path, out, rate)
    return {
        "path": out_path,
        "fill_seconds": round(len(out) / rate, 3),
        "loops": loops,
        "level_dbfs": _db_or_none(round(rms_dbfs(out), 1)),
    }


def _write_wav(path: str, samples: list, sample_rate: int) -> None:
    clamped = [max(-1.0, min(1.0, v)) for v in samples]
    packed = struct.pack(f"<{len(clamped)}h",
                         *(int(v * 32767.0) for v in clamped))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(packed)
