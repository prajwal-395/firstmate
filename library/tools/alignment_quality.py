"""Does the recording agree with the boundary the aligner placed?

What this is
------------
A forced aligner places a word boundary because the TEXT says a word is
there. It never asks the audio whether anything was said at that
instant. On a noisy conversational recording the two answers come
apart: a boundary lands in a gap, a word lands on a chair squeak, and
the caption that follows is off for the rest of the sentence.

This module reads the recording the alignment claims to time and says,
per word, how the recording supports the placement. It is a
MEASUREMENT, and it is the same measurement for every input: the
file's own speech level, read off the file, with no level carried in
from another recording.

Three readings, and why they are three
--------------------------------------
``corroborated`` - the word's span carries signal close to the level
this file's own speech sits at.

``quiet`` - the span carries signal, but much quieter than this file's
speech. Whether a word was said there is NOT decided: a quiet word and
a gap in a noisy take look the same to an energy measurement, and this
module refuses to call either one.

``silent`` - the span carries essentially nothing. The recording does
not support a word there, and the placement is reported as
CONTRADICTED.

The three are measurements of the RECORDING. They are not verdicts on
the speech, and the module says so where the words are.

What it changes
---------------
A word the recording contradicts is moved onto the nearest span the
recording does support, when one is close enough to be the same word.
Every repair is recorded with the distance it moved, so a reader can
see how much of the transcript was moved and by how much. A word with
no supported span nearby is left alone and reported: a boundary moved
onto something hours away is a guess wearing a measurement's clothes.

What it does not do
-------------------
It does not decide that a word was not said, and it does not drop one.
A contradicted word is KEPT. Dropping it would lose speech the
transcriber heard, on the strength of an energy measurement that cannot
tell a quiet word from a missing one.

``unverified`` is the absence of an answer
------------------------------------------
A recording this module cannot read is not read. A recording whose
loudest and quietest parts sit within ``MIN_DYNAMIC_RANGE_DB`` of each
other cannot tell a word from room tone, and publishes nothing and
repairs nothing. ``unverified`` is NOT "no problems found": it is the
absence of an answer, and it travels as such.

The levels are the file's own
-----------------------------
``SPEECH_PERCENTILE`` and ``NOISE_PERCENTILE`` are read off this
recording, not fixed in dBFS: a studio clip and a phone recording in a
room differ by tens of decibels and both are ordinary material. The
floor is ``CORROBORATION_DB`` below the file's own speech level, so
the test is a RATIO against the recording rather than an absolute
level that only fits one kind of room.

``tests/unit/audio/test_alignment_quality.py``.
"""

from __future__ import annotations

import math
import wave
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

FRAME_SECONDS = 0.02
"""The analysis frame. One fifth of a typical syllable."""

FRAME_HOP_SECONDS = 0.01
"""Frame advance, so a boundary is located to ten milliseconds."""

NOISE_PERCENTILE = 10.0
"""The percentile of frame level read as this file's own room tone."""

SPEECH_PERCENTILE = 90.0
"""The percentile of frame level read as this file's own speech."""

MIN_DYNAMIC_RANGE_DB = 12.0
"""How far the file's speech must sit above its own noise floor before
this module will say anything about a word.

Twelve decibels is the point at which a frame is no longer
indistinguishable from the floor. A file flatter than that is reported
unverified, not reported clean."""

CORROBORATION_DB = 40.0
"""How far below this file's own speech level a span may sit and still
count as the recording carrying signal there."""

QUIET_DB = 60.0
"""How far below this file's own speech level a span may sit and still
count as carrying signal at all. Below this the recording carries
essentially nothing, and a placement there is contradicted."""

MAX_REPAIR_SEARCH_SECONDS = 0.5
"""How far a contradicted word may be moved onto a supported span. Past
this the word is left where the aligner put it and reported."""

MIN_WORD_SECONDS = 0.020
"""Roughly one frame at 30fps. A repair that would leave a word
shorter than this is not applied."""

METHOD = "alignment-energy-v1"
"""The method stamp. One name for this measurement."""

STATUS_MEASURED = "measured"
STATUS_UNVERIFIED = "unverified"

STATE_CORROBORATED = "corroborated"
STATE_QUIET = "quiet"
STATE_SILENT = "silent"

REASON_NO_AUDIO = "audio_unavailable"
REASON_FLAT_DYNAMIC_RANGE = "insufficient_dynamic_range"
REASON_NO_WORDS = "no_aligned_words"
REASON_AUDIO_UNREADABLE = "audio_unreadable"

DIGITAL_FLOOR_DB = -120.0
"""The level of a frame holding no signal at all."""


class AlignmentAudioUnavailable(RuntimeError):
    """The recording could not be read for this measurement.

    Raised with the reason, so a caller records "unverified" and
    carries on. Never raised as a substitute for a measurement that
    came out badly.
    """


def _percentile(values: Sequence[float], percentile: float) -> float:
    """The `percentile` of `values`, nearest-rank on a sorted copy."""
    if not values:
        raise ValueError("no values to take a percentile of")
    ordered = sorted(values)
    if percentile <= 0:
        return ordered[0]
    if percentile >= 100:
        return ordered[-1]
    rank = math.ceil(percentile / 100.0 * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def read_pcm(path: str) -> tuple[Any, int]:
    """The recording's samples as float32 in [-1, 1], and its rate.

    16-bit PCM WAV only, which is what the pipeline's canonical
    `program.16k.wav` is (`library/tools/source_primitives.py`). A
    multichannel file is averaged: the measurement is of the mixed
    signal, and an upmix artifact is not a second microphone.
    """
    import numpy as np

    try:
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2:
                raise AlignmentAudioUnavailable(
                    f"{Path(path).name} is not 16-bit PCM")
            rate = source.getframerate()
            channels = source.getnchannels()
            raw = source.readframes(source.getnframes())
    except AlignmentAudioUnavailable:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise AlignmentAudioUnavailable(
            f"{Path(path).name} is unreadable: {exc}") from exc
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    samples /= 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def envelope(samples: Any, rate: int) -> tuple[Any, Any, Any]:
    """The recording's level envelope: frame starts, ends and dBFS.

    Computed in blocks rather than frame by frame: an eighty-minute
    recording is 480,000 frames, and a Python loop over them costs more
    than the alignment this is checking.

    A frame with no samples in it is reported at the digital floor, so
    the envelope covers the whole recording and a word placed past the
    end of the audio is examined rather than skipped.
    """
    import numpy as np

    hop = max(1, round(FRAME_HOP_SECONDS * rate))
    span = max(1, round(FRAME_SECONDS * rate))
    blocks = max(1, span // hop)
    total = len(samples)
    block_count = total // hop
    block_sums = np.zeros(block_count + 1, dtype=np.float64)
    chunk_blocks = 1 << 14
    for begin in range(0, block_count, chunk_blocks):
        end = min(begin + chunk_blocks, block_count)
        chunk = np.asarray(samples[begin * hop:end * hop], dtype=np.float64)
        block_sums[begin:end] = (chunk * chunk).reshape(-1, hop).sum(axis=1)
    partial = np.asarray(samples[block_count * hop:], dtype=np.float64)
    partial_energy = float((partial * partial).sum())

    count = block_count + 1
    starts = np.arange(count, dtype=np.float64) * hop
    ends = np.minimum(starts + span, total)
    energies = np.zeros(count, dtype=np.float64)
    if block_count:
        energies[:block_count - blocks + 1] = np.convolve(
            block_sums[:block_count], np.ones(blocks), mode="valid")
    for index in range(max(0, block_count - blocks + 1), count):
        covered = min(blocks, block_count - index)
        energy = float(block_sums[index:index + covered].sum())
        if index + covered >= block_count:
            energy += partial_energy
        energies[index] = energy
    widths = np.maximum(ends - starts, 1.0)
    rms = np.sqrt(energies / widths)
    db = np.where(rms > 0.0, 20.0 * np.log10(np.maximum(rms, 1e-12)),
                  DIGITAL_FLOOR_DB)
    return starts / rate, ends / rate, db


def frame_levels(samples: Any, rate: int) -> list[dict]:
    """Every analysis frame's level, in dBFS, as one row per frame."""
    starts, ends, db = envelope(samples, rate)
    return [{"start": round(float(start), 6), "end": round(float(end), 6),
             "dbfs": round(float(level), 3)}
            for start, end, level in zip(starts, ends, db)]


def file_levels(levels: Sequence[float]) -> dict:
    """This recording's own levels: speech, noise, and the range.

    The speech level is the file's own speech percentile and the noise
    level its own noise percentile; neither is a constant carried in
    from another recording.
    """
    values = [float(level) for level in levels]
    noise = _percentile(values, NOISE_PERCENTILE)
    speech = _percentile(values, SPEECH_PERCENTILE)
    return {"noise_dbfs": round(noise, 3),
            "speech_dbfs": round(speech, 3),
            "dynamic_range_db": round(speech - noise, 3)}


def supported_regions(starts: Any, ends: Any, db: Any,
                      floor_db: float) -> list[tuple[float, float]]:
    """The runs of frames above `floor_db`, as `(start, end)` seconds."""
    import numpy as np

    above = db > floor_db
    if not above.any():
        return []
    padded = np.concatenate(([False], above, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    regions = []
    for begin, end in zip(edges[0::2], edges[1::2]):
        regions.append((float(starts[begin]), float(ends[end - 1])))
    return regions


def _peak_level(starts: Any, ends: Any, db: Any,
                start: float, end: float) -> float:
    """The loudest frame level inside `[start, end]`."""
    import numpy as np

    first = int(np.searchsorted(ends, start, side="left"))
    last = int(np.searchsorted(starts, end, side="right"))
    if first >= last:
        return DIGITAL_FLOOR_DB
    return float(db[first:last].max())


def _iter_words(aligned: dict) -> Iterable[dict]:
    """Every timed word in an aligned document, in order."""
    for segment in aligned.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words") or []:
            if isinstance(word, dict):
                yield word


def _word_span(word: dict) -> tuple[float, float] | None:
    try:
        start = float(word["start"])
        end = float(word["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
        return None
    return start, end


def _state_for(relative_db: float) -> str:
    """The reading for a span, relative to this file's own speech."""
    if relative_db >= -CORROBORATION_DB:
        return STATE_CORROBORATED
    if relative_db >= -QUIET_DB:
        return STATE_QUIET
    return STATE_SILENT


def _nearest_supported_span(regions: Sequence[tuple[float, float]],
                            start: float, end: float,
                            limit: float
                            ) -> tuple[tuple[float, float], float] | None:
    """The supported region nearest to `[start, end]`, and the distance.

    None when nothing supported is close enough to be the same word. The
    distance is what the search limit is measured against: how far the
    word would have to MOVE, not how far the region is from its edge.
    """
    best = None
    best_distance = None
    for region_start, region_end in regions:
        if region_start < end and region_end > start:
            return (region_start, region_end), 0.0
        distance = abs(region_start - start)
        if distance <= limit and (best_distance is None
                                  or distance < best_distance):
            best_distance = distance
            best = (region_start, region_end)
    if best is None:
        return None
    return best, float(best_distance)


def verify_alignment(aligned: dict, audio_path: str | None,
                     *, repair: bool = True) -> dict:
    """Check every aligned word against the recording, and repair what
    the recording contradicts.

    `aligned` is the aligner's document (`{"segments": [{"words": [...]}]}`);
    `audio_path` is the recording those words claim to be timed against.
    The document's word boundaries are MUTATED IN PLACE when a repair
    applies, so the caller keeps one set of timings.

    Returns the measurement record. `status` is `measured` when the
    recording answered and `unverified` when it could not; an
    unverified record carries a `reason` and reports no word counts.
    """
    words = [word for word in _iter_words(aligned)
             if _word_span(word) is not None]
    if not words:
        return {"status": STATUS_UNVERIFIED, "method": METHOD,
                "reason": REASON_NO_WORDS, "words": {"total": 0},
                "repairs": [], "not_applied": [], "contradicted": []}
    if not audio_path:
        return {"status": STATUS_UNVERIFIED, "method": METHOD,
                "reason": REASON_NO_AUDIO, "words": {"total": len(words)},
                "repairs": [], "not_applied": [], "contradicted": []}
    try:
        samples, rate = read_pcm(audio_path)
    except AlignmentAudioUnavailable as unavailable:
        return {"status": STATUS_UNVERIFIED, "method": METHOD,
                "reason": REASON_AUDIO_UNREADABLE,
                "detail": str(unavailable),
                "words": {"total": len(words)},
                "repairs": [], "not_applied": [], "contradicted": []}
    if len(samples) == 0:
        return {"status": STATUS_UNVERIFIED, "method": METHOD,
                "reason": REASON_AUDIO_UNREADABLE,
                "detail": "the recording decodes to no samples",
                "words": {"total": len(words)},
                "repairs": [], "not_applied": [], "contradicted": []}

    starts, ends, db = envelope(samples, rate)
    measured = file_levels(db)
    if measured["dynamic_range_db"] < MIN_DYNAMIC_RANGE_DB:
        return {"status": STATUS_UNVERIFIED, "method": METHOD,
                "reason": REASON_FLAT_DYNAMIC_RANGE,
                "detail": f"the recording's speech sits "
                          f"{measured['dynamic_range_db']:.1f} dB above its "
                          f"own noise floor, under the "
                          f"{MIN_DYNAMIC_RANGE_DB} dB this measurement needs "
                          f"to tell a word from room tone",
                "levels": measured,
                "words": {"total": len(words)},
                "repairs": [], "not_applied": [], "contradicted": []}

    speech_db = measured["speech_dbfs"]
    regions = supported_regions(starts, ends, db,
                                speech_db - CORROBORATION_DB)

    readings: list[dict] = []
    repairs: list[dict] = []
    not_applied: list[dict] = []
    contradicted: list[dict] = []
    counts = {STATE_CORROBORATED: 0, STATE_QUIET: 0, STATE_SILENT: 0}
    for word in words:
        span = _word_span(word)
        if span is None:
            continue
        start, end = span
        peak = _peak_level(starts, ends, db, start, end)
        relative = peak - speech_db
        state = _state_for(relative)
        counts[state] += 1
        reading = {
            "word": word.get("word") or word.get("text"),
            "start": round(start, 3), "end": round(end, 3),
            "peak_dbfs": round(peak, 3),
            "relative_db": round(relative, 3),
            "state": state,
        }
        if state == STATE_SILENT:
            contradicted.append({
                **reading,
                "seconds": round(end - start, 3),
                "reason": "the_recording_carries_no_signal_at_the_placed_span",
            })
            nearby = _nearest_supported_span(
                regions, start, end, MAX_REPAIR_SEARCH_SECONDS)
            if nearby is None:
                not_applied.append({
                    "word": reading["word"],
                    "from_start": round(start, 3), "from_end": round(end, 3),
                    "reason": "no_supported_span_within_the_search_limit",
                })
            else:
                (region_start, region_end), _distance = nearby
                # The word KEEPS ITS LENGTH and moves onto the span the
                # recording supports: a repair that also changed how
                # long the word is would be inventing a second thing.
                new_start = round(region_start, 3)
                new_end = round(min(region_start + (end - start),
                                     region_end), 3)
                if new_end - new_start < MIN_WORD_SECONDS:
                    not_applied.append({
                        "word": reading["word"],
                        "from_start": round(start, 3), "from_end": round(end, 3),
                        "reason": "the_supported_span_is_shorter_than_a_frame",
                    })
                else:
                    found = {
                        "word": reading["word"],
                        "from_start": round(start, 3), "from_end": round(end, 3),
                        "to_start": new_start, "to_end": new_end,
                        "shift_ms": round(abs(new_start - start) * 1000.0
                                          + abs(new_end - end) * 1000.0, 3),
                    }
                    if repair:
                        word["start"] = new_start
                        word["end"] = new_end
                        repairs.append(found)
                    else:
                        not_applied.append(found)
        readings.append(reading)

    shifts = sorted(row["shift_ms"] for row in repairs)
    return {
        "status": STATUS_MEASURED,
        "method": METHOD,
        "levels": measured,
        "audio": {"sample_rate_hz": rate,
                  "duration_seconds": round(len(samples) / rate, 3)},
        "words": {"total": len(words),
                  "corroborated": counts[STATE_CORROBORATED],
                  "quiet": counts[STATE_QUIET],
                  "silent": counts[STATE_SILENT],
                  "repaired": len(repairs)},
        "contradicted_seconds": round(
            sum(row["seconds"] for row in contradicted), 3),
        "repair_shift_ms": {
            "median": shifts[len(shifts) // 2] if shifts else None,
            "max": shifts[-1] if shifts else None,
        },
        "readings": readings,
        "repairs": repairs,
        "not_applied": not_applied,
        "contradicted": contradicted,
    }
