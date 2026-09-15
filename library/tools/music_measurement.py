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
comes out is numbers and one curve.  What each one IS, in what unit, is
stated in step 2.04's own ``handoff.md`` - never what to conclude from
it - and :data:`MEASURED_KEYS` is this module's inventory of them, which
the import-time guard below is asked of.

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


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**The bed's own measurements reach the mix, because a step that cannot see the music cannot act on any answer about it.**
Step 2.04 measures every candidate (§10.5); its post-bridge folds the CHOSEN track's SCALARS onto `music_selection.measurements` through `music_measurement.selection_measurements`, and step 5.02 reads them and records `bed` plus a per-window `bed_level_after_gain_lufs` - the bed's integrated loudness plus the clip gain, which is arithmetic and not a decision.
- **Only scalars travel.** `music_selection` is declared whole by `plan_transitions` and `mesh_spine`, so the envelope curve and the section table would land in two prompts (§10.1). `WITHHELD_FROM_THE_SELECTION` records both with the reason, and an unaccounted measurement key raises at import.
- **An unmeasured bed is an admitted absence**: `measured: false` with its reason, no level at all, and `bed_level_after_gain_lufs` None - never 0.
- **The separation a window will DELIVER is predicted, and the separation it OUGHT to deliver is not supplied.** `library/tools/speech_loudness.py` measures the speech with one ffmpeg `loudnorm` pass per block over the ranges `a_roll_assignments` names - 0.23 s a block, measured, so 1.2 s for 001's eight - and 5.02 records `speech_lufs` and `separation_delivered_db` per window. **Measure and expose; never choose.** `SEPARATION_TARGETS_DB` is still empty and the master loudness target is still the captain's, so nothing compares the delivered number with anything. A block whose speech could not be measured records the reason and `None`, never 0.
- Step 5.02 declares `audio_spine` and `music_selection` and nothing else. Re-declaring `creative_direction` or `enhancement_spec` needs a reader in the same commit.
- `tests/test_mix_reads_the_bed.py`.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**Every candidate is MEASURED, and nothing about it is classified.**
`library/tools/music_measurement.py` is that half: integrated loudness, loudness range, RMS spread, the envelope over the played window, true peak and the share of energy in the speech band.
- **What each measurement IS is defined in step 2.04's `handoff.md`**, where the model reads it - the unit and nothing about what to conclude. `MEASURED_KEYS` here is the inventory the import-time guard is asked of, not a second copy of the definitions: a key measured and neither travelling with the selection nor recorded as withheld fails at import.
- **A candidate the duration check already rejected is not opened**, and says so rather than leaving a blank column. That is mechanical - it cannot be selected either way.
- `DECLINED_MEASUREMENTS` records what was left out and why. Tempo and key used to be declined there (2.06 measures them after the choice); the captain's decision of 2026-09-07 reversed that, so they are measured per candidate at choice time instead. [why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)
- The bed's own level is what decides whether a planned `music_behavior` offset lands - see §10.4.
- The played window's envelope is the section starting at 0; `track_sections` is how every other span compares.
- **Where the candidates come from**: [`docs/MUSIC_SOURCING.md`](docs/MUSIC_SOURCING.md) §5.
- `tests/test_music_measurement.py`.
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

# Everything this module measures about one candidate, keyed the same way
# whether it is read here or downstream.  **This is the INVENTORY, not a
# legend**: what each key IS, in what unit, is stated in step 2.04's own
# `handoff.md` under "What was measured about each candidate", which is
# where the model reads it.  The definitions shipped beside the numbers as
# a `measurement_legend` dict only while that file was under the captain's
# freeze, lifted 2026-09-09.
#
# The inventory itself stays, because it is what the import-time guard
# below is asked of: a key measured here that neither travels with the
# chosen selection nor is recorded as deliberately withheld is a
# measurement nobody decided about, and that fails at import.
#
# Rhythm and harmony (the `tempo_*`, `musical_key`/`key_*` and `beat_grid`
# keys) are measured per candidate by the SAME functions step 2.06 uses -
# `analyze_tempo_beats` and `analyze_key` in
# `library/tools/analysis/music_pipeline.py` - called from
# :func:`measure_rhythm_candidates`, which the 2.04 bridge runs after the
# ffmpeg pass.  Captain's decision 2026-09-07, option (a): the choice was
# being made on loudness alone, with bpm and key correctly null because
# 2.06 measures them downstream of the choice.  The measurement moved to
# choice time; step 2.06 itself did not move - it still analyses the CHOSEN
# track.  Measured cost on the captain's machine: ~2.7s per 60s track via
# the librosa path (~5.3s per 90s), sequential, one whole-track decode
# each.  Key is null with a stated note wherever essentia is not
# installed - an absent measurement is stated, never defaulted.
MEASURED_KEYS = frozenset({
    "integrated_lufs",
    "loudness_range_lu",
    "true_peak_dbtp",
    "rms_spread_db",
    "window_spread_db",
    "window_envelope_dbfs",
    "window_seconds",
    "track_sections",
    "track_sections_note",
    "speech_band_ratio_db",
    "measured",
    "measurement_note",
    "tempo_bpm",
    "tempo_method",
    "tempo_beat_count",
    "tempo_downbeat_count",
    "tempo_stable",
    "tempo_note",
    "musical_key",
    "key_method",
    "key_strength",
    "key_note",
    "beat_grid",
})

# Measurements that were considered for this table and left out, with the
# reason.  A record, so the next person weighing one of these is arguing
# with a decision rather than rediscovering it.
#
# bpm and musical_key used to be declined here - librosa cost ~5s a track
# against 0.2s for an ffmpeg pass, and no consumer of the selection read a
# key.  Captain's decision 2026-09-07, option (a), reversed both: the
# choice was being made with no access to rhythm or harmony, so tempo, key
# and beat-grid are now measured per candidate at choice time by the same
# code 2.06 uses.  What remains below is still out, for the reasons given.
DECLINED_MEASUREMENTS = {
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
    # Rhythm and harmony travel the same way: scalars describing the bed
    # that will actually play, measured at choice time. The full grid
    # stays behind in WITHHELD_FROM_THE_SELECTION - it is a raw list, and
    # music_selection is declared whole by plan_transitions and mesh_spine.
    "tempo_bpm",
    "tempo_method",
    "tempo_beat_count",
    "tempo_downbeat_count",
    "tempo_stable",
    "tempo_note",
    "musical_key",
    "key_method",
    "key_strength",
    "key_note",
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
    "beat_grid": (
        "The full beats/downbeats arrays. Same raw-list hazard as the "
        "envelope and the section table, larger: hundreds of timestamps "
        "per candidate. Code reads it off the catalogue; prompts never "
        "see it - step 2.04's own manifest drops it, and the model's "
        "choice-time reading of rhythm is the tempo_* scalars, which do "
        "travel."
    ),
}

# A key that is in neither list is a measurement nobody decided about.
_UNACCOUNTED = (set(MEASURED_KEYS)
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


def bed_reading(music_selection: dict) -> dict:
    """What is known about the bed that will play, or a stated absence.

    Reads `music_selection.measurements`, which step 2.04's post-bridge
    folds on from the candidate it chose. An unmeasured bed reports
    `measured: False` and its reason - never a level of 0.

    This lived in step 5.02 and is here because two steps read it: 5.02
    writes the automation, and step 4.04's pre-bridge tells the sound
    planner what the bed under each block will be doing. Two copies of
    this arithmetic is how the two come to disagree.
    """
    selection = music_selection or {}
    measurements = selection.get("measurements") or {}
    reading = {
        "title": selection.get("title") or "",
        "audio_path": selection.get("audio_path") or "",
        "measured": bool(measurements.get("measured")),
        "measurement_note": measurements.get("measurement_note") or "",
    }
    if not reading["measured"]:
        if not measurements:
            reading["measurement_note"] = (
                "music_selection carries no `measurements` key. Step 2.04 "
                "writes one on every run; a selection recorded before it "
                "did has none, and re-running 2.04 is what supplies it."
            )
        return reading

    for key in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp",
                "rms_spread_db", "window_spread_db", "speech_band_ratio_db"):
        if key in measurements:
            reading[key] = measurements[key]
    return reading


def bed_level_after_gain(bed: dict, level_db):
    """Where the clip gain puts the bed, in LUFS. None when unmeasured.

    Arithmetic, not a decision: the bed's own integrated loudness plus
    the gain the plan applies to it.
    """
    integrated = (bed or {}).get("integrated_lufs")
    if not (bed or {}).get("measured") or not isinstance(
            integrated, (int, float)):
        return None
    if not isinstance(level_db, (int, float)):
        return None
    return round(float(integrated) + float(level_db), 2)


# ── What the bed is doing under one block ────────────────────────────
#
# The one SFX project 001 shipped plays at -14 dB at 2.398s, which is the
# exact frame the bed goes `prominent` (-6 dB, the loudest music in the
# video).  Step 4.04 is routed `timed_spine`, which carries
# `music_behavior` per block, so it COULD have seen that - but the word
# is buried in the spine and `volume_level` is a four-word ladder
# (subtle -18 / low -14 / medium -10 / prominent -6) with no relation to
# what the sound measures or to what is under it.  Nothing in the
# pipeline predicts whether a sound will be heard.
#
# So the bed's own level at the block a sound is placed on travels into
# that step's candidate table, as the `music_behavior` and `bed_under_it`
# columns.  **What those two columns ARE is stated in step 4.04's own
# `handoff.md`**, under "Context data available" and "What a level is
# measured against"; they shipped beside the table as a
# BED_UNDER_THE_BLOCK_LEGEND dict only while that file was under the
# captain's freeze, lifted 2026-09-09.
#
# **It states a level and never a target.**  What separation a sound
# should have over the bed is the same undeclared decision
# `music_behavior.SEPARATION_TARGETS_DB` is empty for, and an
# engine-supplied one would be a strength nobody chose arriving one
# level up (AGENTS.md 10.5).

def bed_under_block(block: dict, bed: dict) -> tuple:
    """`(behaviour, one sentence about the bed)` for one spine block.

    Raises on a behaviour outside the vocabulary, the same way every
    other reader of that word does.
    """
    from library.tools.music_behavior import (
        is_silent, music_level_db, resolve_music_behavior,
    )
    from library.tools.spine_contract import is_speech_block

    behaviour = resolve_music_behavior(
        (block or {}).get("music_behavior"),
        block_carries_speech=is_speech_block(block or {}))
    if is_silent(behaviour):
        return behaviour, "no music at all - a planned hole in the bed"
    gain = music_level_db(behaviour)
    after = bed_level_after_gain(bed or {}, gain)
    if after is None:
        note = (bed or {}).get("measurement_note") or "no measurement recorded"
        return behaviour, f"{gain} dB gain, bed level unmeasured ({note})"
    return behaviour, f"{gain} dB gain, bed at {after} LUFS"


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


def section_envelopes(audio_path: str,
                      sections: List[Dict[str, object]]
                      ) -> List[Dict[str, object]]:
    """The twelve-bucket envelope of each NAMED span of one file.

    Pass two of the two-pass shape (``library/tools/second_pass.py``).
    ``measure_track`` buckets the played window - seconds 0 to
    ``window_seconds`` - and nothing else, so the richest evidence in the
    step described only the head of the track, which is the answer the
    section feature exists to let the model move away from.  This
    measures the SHAPE of the spans the model says it is considering, and
    only those.

    One decode per file however many sections are named: the per-second
    RMS windows are read once and every section is bucketed out of them.
    A section that cannot be measured says so and carries no numbers -
    never a curve of zeroes (AGENTS.md 10.3).
    """
    try:
        windows = rms_windows(audio_path)
    except (OSError, subprocess.SubprocessError) as exc:
        return [{"source_in": s.get("source_in"),
                 "source_out": s.get("source_out"),
                 "measured": False,
                 "measurement_note": f"ffmpeg could not read the file: {exc}"}
                for s in sections]

    out: List[Dict[str, object]] = []
    for section in sections:
        start = int(round(float(section.get("source_in", 0.0))
                          / RMS_WINDOW_SECONDS))
        end = int(round(float(section.get("source_out", 0.0))
                        / RMS_WINDOW_SECONDS))
        start = max(0, min(start, len(windows)))
        end = max(start, min(end, len(windows)))
        span = windows[start:end]
        row: Dict[str, object] = {
            "source_in": round(float(section.get("source_in", 0.0)), 2),
            "source_out": round(float(section.get("source_out", 0.0)), 2),
        }
        if section.get("track"):
            row["track"] = section["track"]
        audible = [v for v in span if v > SILENCE_FLOOR_DBFS]
        if len(audible) < 2:
            row["measured"] = False
            row["measurement_note"] = (
                f"only {len(audible)} of {len(span)} one-second windows in "
                f"this span carry any level - nothing to shape")
            out.append(row)
            continue
        row["measured"] = True
        row["envelope_dbfs"] = _bucket_envelope(span)
        row["mean_dbfs"] = (lambda m: None if m is None else round(m, 1))(
            _power_mean_db(span))
        row["spread_db"] = _spread(span)
        out.append(row)
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


# ── Rhythm and harmony, at choice time ───────────────────────────────
#
# Step 2.04 chose the bed on loudness alone: bpm and key are in the
# model's output schema, but step 2.06 measures them DOWNSTREAM of the
# choice, so the model correctly answered null for both.  Captain's
# decision 2026-09-07, option (a): compute tempo, key and beat-grid PER
# CANDIDATE in the 2.04 bridge, with the SAME functions 2.06 runs -
# `analyze_tempo_beats` and `analyze_key` - so every candidate carries
# rhythm and harmony when the model sees it.  Step 2.06 does not move; it
# still analyses the CHOSEN track downstream.
#
# MEASUREMENTS, not preferences.  Nothing here thresholds, ranks or
# prefers: no BPM range, no key, no "danceable".  The columns describe the
# candidates; the model still decides (AGENTS.md 10.5).
#
# Cost is per candidate and paid in the bridge, after the ffmpeg pass:
# ~2.7s per 60s track via librosa on the captain's machine, one
# whole-track decode each.  Candidates the duration check already rejected
# are not opened - same mechanical scoping as `should_measure`, and the
# more so here, because a librosa pass over an hours-long compilation is
# the run crawling for an answer it cannot change.


def measure_rhythm_track(audio_path: str) -> Dict[str, object]:
    """Tempo, key and beat-grid for one candidate, by 2.06's own code.

    Calls `analyze_tempo_beats` and `analyze_key` from
    `library/tools/analysis/music_pipeline.py` - the functions step 2.06
    runs on the chosen track - and reduces them to scalar columns plus the
    full grid.  The scalars are what the model decides from; the grid is
    stored for code to read and never reaches a prompt (step 2.04's
    manifest drops it, AGENTS.md 10.1).

    Never raises: a tracker that cannot answer is a stated absence
    (AGENTS.md 10.3), never a zero.  A grid under
    `beat_grid.MIN_USABLE_BEATS` is noise rather than rhythm, so the tempo
    reads as absent with the counts still stated.
    """
    from library.tools.analysis.music_pipeline import (
        analyze_key,
        analyze_tempo_beats,
    )
    from library.tools.beat_grid import MIN_USABLE_BEATS

    out: Dict[str, object] = {}
    try:
        tempo = analyze_tempo_beats(audio_path or "")
    except Exception as exc:
        tempo = {"method": None, "bpm": None, "beats": [],
                 "downbeats": [], "tempo_stable": None,
                 "error": str(exc)}

    beats = tempo.get("beats") if isinstance(tempo, dict) else None
    downbeats = tempo.get("downbeats") if isinstance(tempo, dict) else None
    beats = [float(b) for b in beats] if isinstance(beats, list) else []
    downbeats = [float(d) for d in downbeats] \
        if isinstance(downbeats, list) else []
    method = (tempo or {}).get("method")
    stable = (tempo or {}).get("tempo_stable")

    out["tempo_method"] = method if isinstance(method, str) else None
    out["tempo_beat_count"] = len(beats)
    out["tempo_downbeat_count"] = len(downbeats)
    out["tempo_stable"] = stable if isinstance(stable, bool) else None
    out["beat_grid"] = {
        "beats": [round(b, 3) for b in beats],
        "downbeats": [round(d, 3) for d in downbeats],
    }

    raw_bpm = (tempo or {}).get("bpm")
    bpm = None
    try:
        bpm = float(raw_bpm)
    except (TypeError, ValueError):
        bpm = None
    if bpm is not None and not (bpm > 0):
        bpm = None
    if len(beats) < MIN_USABLE_BEATS:
        out["tempo_bpm"] = None
        cause = (tempo or {}).get("error") or (tempo or {}).get("note") or ""
        out["tempo_note"] = (
            f"no usable grid: {len(beats)} beats found, "
            f"fewer than the {MIN_USABLE_BEATS} a rhythm needs"
            + (f" ({cause})" if cause else "")
            + (f"; tracker {method}" if method else
               "; no tracker answered")
        )
    else:
        out["tempo_bpm"] = round(bpm, 1) if bpm is not None else None
        if bpm is None:
            out["tempo_note"] = (
                f"the tracker found {len(beats)} beats but no tempo; "
                f"tracker {method or 'unknown'}"
            )
        else:
            out["tempo_note"] = ""

    try:
        key = analyze_key(audio_path or "")
    except Exception as exc:
        key = {"method": None, "key": None, "scale": None,
               "error": str(exc)}

    label = (key or {}).get("key_label")
    if not label and (key or {}).get("key"):
        scale = (key or {}).get("scale") or ""
        label = f"{(key or {}).get('key')}" \
            f"{' ' + scale if scale else ''}".strip()
    out["musical_key"] = label if isinstance(label, str) and label else None
    key_method = (key or {}).get("method")
    out["key_method"] = key_method if isinstance(key_method, str) else None
    strength = (key or {}).get("strength")
    try:
        out["key_strength"] = round(float(strength), 3)
    except (TypeError, ValueError):
        out["key_strength"] = None
    if out["musical_key"] is None:
        cause = (key or {}).get("error") or (key or {}).get("note") or \
            "the extractor did not answer"
        out["key_note"] = f"no key detected: {cause}"
    else:
        out["key_note"] = ""

    return out


def measure_rhythm_candidates(candidates: List[dict]) -> List[dict]:
    """Every measured candidate, carrying rhythm and harmony.

    Runs after `measure_candidates`: only candidates the ffmpeg pass
    measured are opened - a candidate already out on duration carries its
    stated absence and no rhythm keys, the same way it carries no
    loudness keys.  Returns new dicts; the input list is not mutated.
    """
    out = []
    for candidate in candidates:
        enriched = dict(candidate)
        if candidate.get("measured") and \
                (candidate.get("audio_path") or "").strip():
            enriched.update(
                measure_rhythm_track(candidate["audio_path"]))
        out.append(enriched)
    return out
