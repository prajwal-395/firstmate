"""What a bed sounds like, measured - so the choice is not made on filenames.

Step 2.04 picks the music that plays under the whole video.  Until now the
model was handed ``title, audio_path, duration_seconds, source,
duration_ok, duration_note`` and asked to score seven candidates against
the creative direction's emotional landscape.  There is nothing musical in
that list.  The run of record says so in its own words:

    "I am being asked to match an emotional landscape against seven
    filenames. ... on the filenames alone the 'rise' track reads as
    forbidden and Sickick reads as neutral - i.e. the context as supplied
    points at the wrong answer."

That run got the right answer only because the agent had a shell and
measured the files itself with ffmpeg.  A model without one cannot.  What
it measured, and what decided it, was four numbers:

======================  ==============  ====================
                        rise (chosen)   Sickick (rejected)
======================  ==============  ====================
integrated loudness     -13.9 LUFS      -15.1 LUFS
loudness range          6.0 LU          9.1 LU
RMS spread              5.7 dB          29.9 dB
envelope over 0-60s     flat at -16.3   -43 -> -13 dBFS
======================  ==============  ====================

A 29.9 dB swing under a voice that sometimes whispers has no single clip
gain that works.  That is the whole basis of the decision, and none of it
was in the context.  This module puts it there.

**It measures.  It does not classify.**  No mood, no genre, no "energy"
word, no ranking, no recommendation - those are the model's to write and
inventing one here is the taste-fabrication AGENTS.md 10.5 forbids.  What
comes out is numbers and one curve, with :data:`MEASUREMENT_LEGEND` saying
what each one IS rather than what to conclude from it.

What is measured, and why each one
----------------------------------
``integrated_lufs``
    Perceived loudness of the whole track (BS.1770, ffmpeg ``loudnorm``).
    The mix's ``music_behavior`` words are RELATIVE dB offsets applied to
    whatever the file already is, so the file's own level decides whether
    the planned offset lands.  AGENTS.md 10.4 records 001's music arriving
    8.6 dB hotter than its speech and the separation target becoming
    unreachable by any mix setting; this is that number, before the choice.

``loudness_range_lu``
    How much that perceived loudness varies across the track (LRA).  A bed
    is placed once, at one gain, and run to the end of the timeline.

``rms_spread_db``
    p95 minus p5 of per-second RMS windows over the whole track.  The
    ungated companion to LRA: LRA discards everything under its own
    relative gate, so a track that opens near silence and lands loud can
    read narrow.  This is the number the run of record used, and on 001's
    Sickick instrumental it reproduces it - 30.7 dB measured here against
    the 29.9 dB the agent got by hand.

``window_envelope_dbfs`` and ``window_spread_db``
    The same per-second RMS, over one ``target_duration_seconds`` span
    from the head of the file - the section that plays when the model
    declares none.  A viewer hears one span that long and no more, so this
    is the shape of what is heard, not of the track.  The curve is
    bucketed to :data:`ENVELOPE_BUCKETS` points so its size does not
    depend on the target duration; the spread is the same p95-p5 read, as
    one scannable column.  ``track_sections`` below is how every other
    span compares.

``true_peak_dbtp``
    Free from the same ``loudnorm`` JSON, no extra pass.  Headroom before
    the master limiter that step 6.02 measures and fails a build on.

``speech_band_ratio_db``
    Speech-band (:data:`SPEECH_BAND_HZ`, the telephony voice band) RMS
    minus full-band RMS: how much of the bed's energy sits where the voice
    does.  On 001 it separates the candidates by 5.8 dB - the piano is
    -2.8, the Sickick instrumental -8.6 - which is a real fact about
    whether a bed and a voice occupy the same place, and one no filename
    carries.

``track_sections``
    The same per-second RMS, reduced to one row per *playable section* of
    the track: successive non-overlapping spans of ``window_seconds``,
    plus the last span that still fits, each with its mean level and its
    spread.  A track is longer than the video and only part of it plays;
    which part is the model's decision
    (:mod:`library.tools.music_section`), and this is what it has to
    decide from.  The rows are a description of the track at the
    granularity of what plays - **not a menu**: a section may start
    anywhere, and nothing here says which one is best.

Nothing here is a threshold and nothing here is a verdict.  The one
judgement in the module is which candidates are worth opening at all, and
it is mechanical: see :func:`should_measure`.

Considered and declined
-----------------------
See :data:`DECLINED_MEASUREMENTS`.  Widening the table is not a way to make
the prompt better; every column is paid on every candidate, and the reason
there is room for these ones is that #295 stopped copying the channel brief
into the prompt.  Measured on 001's own snapshot, this step's context:

    b10833d  57,539 B  candidates 2,204 B   3.8%  (84.3% creative brief)
    #295     14,260 B  candidates 2,204 B  15.5%
    + this   16,747 B  candidates 4,691 B  28.0%
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from typing import Dict, List, Optional, Sequence

# The per-second RMS window the envelope and both spreads are read from.
# One second is short enough to show a 30-second fade-in and long enough
# that a single kick drum does not become a data point.
RMS_WINDOW_SECONDS = 1.0

# The envelope is reported at this many evenly spaced buckets over the
# played window, so its size in the prompt is fixed whatever the target
# duration is.  A mechanical bound on context size, not a musical choice.
ENVELOPE_BUCKETS = 12

# The telephony voice band.  A physical convention for where speech energy
# lives, not a judgement about music.
SPEECH_BAND_HZ = (300, 3400)

# Everything below this reads as digital silence rather than as a level,
# and is excluded from the percentile reads (and counted, so an excluded
# window is never silently dropped).
SILENCE_FLOOR_DBFS = -120.0

# Percentile pair for both spread reads.  max-minus-min is not usable:
# on 001's own files a fade-out tail and a run of digital silence put it
# at 67.4 dB and 99.9 dB on tracks whose real swing is 11.5 and 15.5.
SPREAD_PERCENTILES = (5.0, 95.0)

# Everything ffmpeg is asked to decode at, so a window is the same number
# of samples whatever the file's own rate is.
ANALYSIS_SAMPLE_RATE = 48000

FFMPEG_TIMEOUT_SECONDS = 900

# What each emitted key IS.  Shipped beside the numbers because step
# 2.04's handoff.md is under a captain freeze and cannot name them, and a
# column whose units are unstated is not a measurement anyone can use.
# Definitions only - what to conclude from a number is the model's call.
MEASUREMENT_LEGEND = {
    "integrated_lufs":
        "BS.1770 integrated loudness of the whole track, in LUFS "
        "(ffmpeg loudnorm). Lower is quieter.",
    "loudness_range_lu":
        "BS.1770 loudness range (LRA) of the whole track, in LU. It is "
        "gated: passages below its own relative threshold are excluded.",
    "true_peak_dbtp":
        "True peak of the whole track, in dBTP. 0.0 is full scale.",
    "rms_spread_db":
        "p95 minus p5 of per-second RMS windows across the whole track, "
        "in dB. Ungated, so unlike loudness_range_lu it counts near-silent "
        "passages.",
    "window_spread_db":
        "The same p95 minus p5 read, over the played window only.",
    "window_envelope_dbfs":
        "Per-second RMS over the played window - the first "
        "target_duration_seconds, which is all that plays because music is "
        "placed at source_in 0 - averaged into "
        f"{ENVELOPE_BUCKETS} equal buckets, in dBFS, in time order.",
    "window_seconds":
        "How long the played window is, in seconds. Each envelope bucket "
        "covers window_seconds / "
        f"{ENVELOPE_BUCKETS} of it.",
    "track_sections":
        "Every section of the track that is long enough to play under the "
        "whole edit: successive non-overlapping spans of window_seconds "
        "from 0, plus the last span that still fits. Each row is "
        "{start_seconds, end_seconds, mean_dbfs, spread_db}, in time "
        "order. A section may start anywhere, not only at a row boundary; "
        "these are a description of the track at the granularity of what "
        "plays, not a list of the options. window_envelope_dbfs is the "
        "shape of the section starting at 0.",
    "track_sections_note":
        "Why the section table is absent or short, when it is.",
    "speech_band_ratio_db":
        f"RMS in the {SPEECH_BAND_HZ[0]}-{SPEECH_BAND_HZ[1]} Hz speech "
        "band minus full-band RMS, in dB. How much of the track's energy "
        "sits in the same band as a voice.",
    "measured":
        "Whether the file was opened and measured. False means the "
        "numbers above are absent, never that they are zero.",
    "measurement_note":
        "Why a candidate was not measured, when measured is false.",
}

# Measurements that were considered for this table and left out, with the
# reason.  A record, so the next person weighing one of these is arguing
# with a decision rather than rediscovering it.
DECLINED_MEASUREMENTS = {
    "bpm": (
        "librosa beat tracking costs 5.1s per track here against 0.2s for "
        "an ffmpeg pass, pulls the ML stack into a bridge that needs only "
        "ffmpeg, and its own docstring in "
        "library/tools/analysis/music_pipeline.py warns of +/-2-5 BPM and "
        "frequent octave errors. Step 2.06 music_analysis measures tempo "
        "properly with madmom AFTER the choice, and the documented use of "
        "BPM is phase-4 beatmatching, which is downstream of it."
    ),
    "musical_key": (
        "Same cost and dependency as bpm, and no consumer of the selection "
        "reads a key. 2.06 is where it belongs."
    ),
    "genre, mood, energy, instrumentation": (
        "Taste, not measurement. AGENTS.md 10.5: the pipeline never "
        "invents a creative judgement on the model's behalf. A computed "
        "'uplifting' label would be exactly the fabrication this table "
        "exists to remove."
    ),
    "a per-second envelope at full resolution": (
        "AGENTS.md 10.1: no raw value list reaches a prompt. A 60-point "
        "curve per candidate is the same hazard as the beat grid that was "
        "removed from step 4.02. Bucketed to ENVELOPE_BUCKETS instead."
    ),
    "licence and rights status": (
        "Not derivable from the audio. The handoff asks the model to "
        "prefer royalty-free tracks; nothing in a waveform answers that, "
        "and asserting one would be a claim rather than a measurement."
    ),
}


# ── What travels onward with the track that was CHOSEN ──────────────
#
# Every number above is measured on every candidate so that step 2.04 can
# choose between them.  Once the choice is made, the same numbers are a
# description of the bed that will actually play, and the mix is the step
# that needs them: `music_behavior` words are RELATIVE dB applied to
# whatever the file already is, so the file's own level decides whether
# the planned offset lands (AGENTS.md 10.4, and the 8.6 dB it records).
#
# Only the SCALARS travel.  `music_selection` is declared whole by two
# steps that reach a prompt - `plan_transitions` and `mesh_spine` - so a
# curve or a table folded onto it lands in those prompts, and AGENTS.md
# 10.1 is explicit that no raw value list reaches one.  The two withheld
# keys are also the two that exist to decide WHICH section plays, which
# is a decision 2.04 has already taken by the time this runs.
SELECTION_MEASUREMENT_KEYS = (
    "measured",
    "measurement_note",
    "integrated_lufs",
    "loudness_range_lu",
    "true_peak_dbtp",
    "rms_spread_db",
    "window_seconds",
    "window_spread_db",
    "speech_band_ratio_db",
)

WITHHELD_FROM_THE_SELECTION = {
    "window_envelope_dbfs": (
        f"{ENVELOPE_BUCKETS} numbers. AGENTS.md 10.1: no raw value list "
        "reaches a prompt, and `music_selection` is declared whole by "
        "`plan_transitions` and `mesh_spine`. It is the shape of the "
        "section starting at 0, which is a thing to CHOOSE a section by; "
        "step 2.04 has chosen one by the time this travels."
    ),
    "track_sections": (
        "One row per playable span of the track. Same raw-list hazard, "
        "and the same reason: the table exists so the model can pick "
        "which section plays (library/tools/music_section.py), and the "
        "pick is already made."
    ),
    "track_sections_note": (
        "Says why the section table is absent or short; it travels with "
        "the table it qualifies, or with neither."
    ),
}

# A key that is in neither list is a measurement nobody decided about.
_UNACCOUNTED = (set(MEASUREMENT_LEGEND)
                - set(SELECTION_MEASUREMENT_KEYS)
                - set(WITHHELD_FROM_THE_SELECTION))
if _UNACCOUNTED:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "music_measurement: "
        + ", ".join(sorted(_UNACCOUNTED))
        + " is measured but neither travels with the chosen track nor is "
          "recorded as withheld. Add it to SELECTION_MEASUREMENT_KEYS or "
          "to WITHHELD_FROM_THE_SELECTION with the reason."
    )
del _UNACCOUNTED


def selection_measurements(selection: dict,
                           candidates: Sequence[dict]) -> Dict[str, object]:
    """What was measured about the track a selection chose.

    The chosen track is identified by ``audio_path`` - the same identity
    ``music_selection_contract.validate_selection`` checks catalogue
    membership on, so the two cannot disagree about which candidate was
    chosen.

    Returns ``{"measured": False, "measurement_note": ...}`` and no
    numbers whenever the measurements cannot be found, naming which of
    the reasons it was.  An absent measurement is STATED, never defaulted
    to a value (AGENTS.md 10.3): a bed whose level is unknown is not a
    bed at 0 LUFS.
    """
    audio_path = ((selection or {}).get("audio_path") or "").strip()
    if not audio_path:
        return {"measured": False,
                "measurement_note":
                    "the selection names no audio_path, so there is no file "
                    "to attribute a measurement to"}

    for candidate in candidates or []:
        if (candidate.get("audio_path") or "").strip() != audio_path:
            continue
        if not candidate.get("measured"):
            return {"measured": False,
                    "measurement_note": (candidate.get("measurement_note")
                                         or "the candidate was not measured")}
        return {key: candidate[key]
                for key in SELECTION_MEASUREMENT_KEYS
                if key in candidate}

    return {"measured": False,
            "measurement_note":
                f"{audio_path} is not one of the {len(candidates or [])} "
                f"measured candidates - a track fetched after the catalogue "
                f"was measured is not in it"}


def should_measure(candidate: dict) -> bool:
    """Whether opening this file can change the answer.

    A candidate the duration check already rejected cannot be chosen -
    ``music_selection_contract.validate_selection`` refuses a selection
    outside the duration bounds - so measuring it buys nothing and can
    cost a great deal.  On project 001 the two over-long candidates are
    3,914s and 11,386s; a ``loudnorm`` pass over them runs 83s and 242s,
    against 3.2s for a real track.  That is the whole of the scoping, and
    it is mechanical: it reads the flag the bridge already set, and states
    the omission rather than hiding it.
    """
    return bool(candidate.get("duration_ok"))


def _percentile(values: Sequence[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _spread(values: Sequence[float]) -> Optional[float]:
    """p95 - p5 over the windows that carry a level at all."""
    audible = [v for v in values if v > SILENCE_FLOOR_DBFS]
    if len(audible) < 2:
        return None
    low, high = SPREAD_PERCENTILES
    return round(_percentile(audible, high) - _percentile(audible, low), 2)


def _power_mean_db(values: Sequence[float]) -> Optional[float]:
    """Mean in the power domain, which is what averaging dB must do."""
    audible = [v for v in values if v > SILENCE_FLOOR_DBFS]
    if not audible:
        return None
    mean_power = sum(10.0 ** (v / 10.0) for v in audible) / len(audible)
    if mean_power <= 0:
        return None
    return 10.0 * math.log10(mean_power)


def _bucket_envelope(values: Sequence[float],
                     buckets: int = ENVELOPE_BUCKETS) -> List[Optional[float]]:
    """``values`` averaged into ``buckets`` equal spans, in time order."""
    if not values:
        return []
    out: List[Optional[float]] = []
    for i in range(buckets):
        start = (i * len(values)) // buckets
        end = max(start + 1, ((i + 1) * len(values)) // buckets)
        mean = _power_mean_db(values[start:end])
        out.append(None if mean is None else round(mean, 1))
    return out


def _run_ffmpeg(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=FFMPEG_TIMEOUT_SECONDS,
    )


def rms_windows(audio_path: str,
                window_seconds: float = RMS_WINDOW_SECONDS) -> List[float]:
    """Per-window RMS in dBFS, in time order, for the whole file.

    One decode.  ``asetnsamples`` fixes the frame length so ``astats``'
    per-frame reset is a fixed span of time rather than whatever the
    container happened to packetise.
    """
    samples = max(1, int(round(ANALYSIS_SAMPLE_RATE * window_seconds)))
    chain = (
        f"aresample={ANALYSIS_SAMPLE_RATE},"
        f"asetnsamples=n={samples}:p=0,"
        f"astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    result = _run_ffmpeg(["-i", audio_path, "-af", chain, "-f", "null", "-"])
    values: List[float] = []
    for line in (result.stdout or "").splitlines():
        if "RMS_level=" not in line:
            continue
        raw = line.split("=", 1)[1].strip()
        try:
            values.append(float(raw))
        except ValueError:
            # "-inf" and "nan" are how astats spells a silent window.
            values.append(float("-inf"))
    return values


def loudness(audio_path: str) -> Dict[str, float]:
    """Integrated loudness, loudness range and true peak, in one pass."""
    result = _run_ffmpeg(
        ["-i", audio_path, "-af", "loudnorm=print_format=json",
         "-f", "null", "-"])
    blob = ""
    depth = 0
    for line in (result.stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            depth = 1
            blob = "{"
            continue
        if depth:
            blob += stripped
            if stripped.startswith("}"):
                break
    if not blob:
        raise ValueError("ffmpeg loudnorm printed no JSON summary")
    data = json.loads(blob)
    return {
        "integrated_lufs": round(float(data["input_i"]), 2),
        "loudness_range_lu": round(float(data["input_lra"]), 2),
        "true_peak_dbtp": round(float(data["input_tp"]), 2),
    }


def _band_rms_db(audio_path: str, band_filters: str) -> Optional[float]:
    chain = f"aresample={ANALYSIS_SAMPLE_RATE}{band_filters}," \
            f"astats=measure_perchannel=none"
    result = _run_ffmpeg(["-i", audio_path, "-af", chain, "-f", "null", "-"])
    match = re.search(r"RMS level dB:\s*(-?[\d.]+|-?inf|nan)",
                      result.stderr or "")
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def speech_band_ratio_db(audio_path: str,
                         full_band_rms_db: Optional[float]) -> Optional[float]:
    """Speech-band RMS minus full-band RMS, in dB.

    ``full_band_rms_db`` is passed in rather than measured again: the
    per-window pass already carries it, and two reads of the same quantity
    that can disagree is a defect waiting to happen.
    """
    if full_band_rms_db is None:
        return None
    low, high = SPEECH_BAND_HZ
    band = _band_rms_db(audio_path, f",highpass=f={low},lowpass=f={high}")
    if band is None:
        return None
    return round(band - full_band_rms_db, 2)


def track_sections(values: Sequence[float],
                   window_seconds: float) -> List[Dict[str, object]]:
    """Every span of the track long enough to play under the whole edit.

    Successive non-overlapping spans of ``window_seconds`` from 0, plus
    the last span that still fits when the track does not divide evenly -
    so the tail of a track is described rather than falling off the end.
    Row count is bounded by the duration ceiling
    (``music_selection_contract.max_track_duration_seconds``), which is
    ten times the target.

    A DESCRIPTION, at the granularity of what plays.  It is not a menu and
    it is not ranked: a section may start anywhere, and which one is right
    is the model's call (``library/tools/music_section.py``).
    """
    span = max(1, int(round(window_seconds / RMS_WINDOW_SECONDS)))
    if len(values) < span:
        return []

    starts = list(range(0, len(values) - span + 1, span))
    last = len(values) - span
    if starts and starts[-1] != last:
        starts.append(last)

    rows: List[Dict[str, object]] = []
    for start in starts:
        window = values[start:start + span]
        mean = _power_mean_db(window)
        rows.append({
            "start_seconds": round(start * RMS_WINDOW_SECONDS, 2),
            "end_seconds": round((start + span) * RMS_WINDOW_SECONDS, 2),
            "mean_dbfs": None if mean is None else round(mean, 1),
            "spread_db": _spread(window),
        })
    return rows


def measure_track(audio_path: str, window_seconds: float) -> Dict[str, object]:
    """Every measurement for one candidate.

    ``window_seconds`` is the played window - the target duration of the
    edit.  Returns ``{"measured": False, "measurement_note": ...}`` and no
    numbers when the file cannot be measured: an absent measurement is
    stated, never defaulted to a value (AGENTS.md 10.3).
    """
    try:
        windows = rms_windows(audio_path)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"measured": False,
                "measurement_note": f"ffmpeg could not read the file: {exc}"}

    audible = [v for v in windows if v > SILENCE_FLOOR_DBFS]
    if len(audible) < 2:
        return {"measured": False,
                "measurement_note":
                    f"only {len(audible)} of {len(windows)} one-second "
                    f"windows carry any level - nothing to measure"}

    out: Dict[str, object] = {"measured": True, "measurement_note": ""}

    played = windows[:max(1, int(round(window_seconds / RMS_WINDOW_SECONDS)))]
    out["window_seconds"] = round(window_seconds, 3)
    out["window_envelope_dbfs"] = _bucket_envelope(played)
    out["window_spread_db"] = _spread(played)
    out["rms_spread_db"] = _spread(windows)

    sections = track_sections(windows, window_seconds)
    out["track_sections"] = sections
    out["track_sections_note"] = "" if sections else (
        f"the track is shorter than the {window_seconds:.0f}s the edit "
        f"runs, so no section of it can play under the whole video"
    )

    try:
        out.update(loudness(audio_path))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        out["measurement_note"] = (
            f"per-second levels measured; loudnorm did not answer: {exc}")

    out["speech_band_ratio_db"] = speech_band_ratio_db(
        audio_path, _power_mean_db(windows))

    return out


def measure_candidates(candidates: List[dict],
                       window_seconds: float) -> List[dict]:
    """Every candidate, measured where measuring it can change the answer.

    Returns new dicts; the input list is not mutated.  A candidate the
    duration check already rejected keeps ``measured: False`` and says so,
    because a blank column and a deliberate omission read the same and
    only one of them is honest.
    """
    out = []
    for candidate in candidates:
        enriched = dict(candidate)
        if should_measure(candidate):
            enriched.update(measure_track(
                candidate.get("audio_path", ""), window_seconds))
        else:
            enriched["measured"] = False
            enriched["measurement_note"] = (
                "not measured: this candidate is already out on duration "
                "and cannot be selected, so opening it would cost the run "
                "time it cannot change the answer with"
            )
        out.append(enriched)
    return out
