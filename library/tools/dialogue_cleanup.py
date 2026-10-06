"""Dialogue cleanup: plan-requested noise treatment for speech, never a default.

A cleanup is PLAN-REQUESTED or it does not happen.  There is no engine
default, no threshold that turns it on, and no fallback tool.

What this module owns (one enumeration, like every vocabulary here):

- `TOOLS`: `voice_isolation` (Resolve's own per-track Voice Isolation,
  `Timeline.SetVoiceIsolationState`, Studio), `deepfilternet` (a
  processed stem placed through the OTIO route) and `audio_ops` (a
  plan-declared EQ / de-ess / dereverb chain, delivered as the same kind
  of stem).  Anything else is refused by name in
  `validate_cleanup_request`, never dropped.
- `CLEANUP_ENTRY_KEYS`: the entry keys step 5.02 reads.  An unknown key,
  an unknown tool, an amount outside Resolve's scale, a backwards span
  or an entry with no `why` raises
  `DialogueCleanupRefused`, so the model re-plans through the
  post-bridge retry path instead of the mix carrying a key nobody reads.
- Amounts are Resolve's own scale, `VOICE_ISOLATION_MIN` ..
  `VOICE_ISOLATION_MAX` (0..100); the engine offers no scale of its own
  (AGENTS.md 10.5).
- Availability probes with the reason attached (`deepfilternet_probe`,
  `voice_isolation_note`, `audio_operations_note`), so the plan context
  states what the build can actually do.
- `measure_source` and `spans_by_source`: the noise record for each
  played source, absences stated.
- `validate_leveler_properties` and `apply_dialogue_leveler`: the
  per-clip Dialogue Leveler through `TimelineItem.SetProperties`,
  judged by `GetProperties` re-read with the same discipline as
  Voice Isolation. Applied directly, never a cleanup-plan tool (the
  plan half is a later slice); the six keys are Resolve's own, and an
  `EQBand1Frequency` refuses as an unknown key - that refusal IS the
  recorded measurement that track EQ has no scripting API.
- `source_spectra` and `plan_dialogue_eq`: the Fairlight chain's
  measurement half - per-source speech/floor spectra on the analyzer's
  own bands, and the dialogue-bus EQ (high-pass + one peaking cut)
  derived from them with every number's source recorded. Spectra never
  enter pipeline state or a prompt; only the derived bands travel, in
  the intent post_bridge validates.
- `run_deepfilternet`: source range in, stem file out, with wall time
  and before/after measurements on the record; `stage_audio_chain`
  (`stage_deepfilternet` is its compatibility name) stages every
  requested stem at `STEM_SAMPLE_RATE`.
- `apply_voice_isolation`: the track call with Get read-back, the same
  discipline as the `audio isolate` resolve-axi verb.  The per-clip
  variant stays out: the probe ranks the track call first.
- `rewrite_clip_media_to_stem`: the OTIO half of a stem - the clip's
  media reference becomes the stem file, which IS the played range, so
  the source start resets to zero.  Pure function.

DeepFilterNet is NOT in `requirements.txt`: it pins `numpy<2.0` and
ships no cp312 macOS-arm64 wheel, so pinning it would break the shared
ML venv.  The engine invokes it opportunistically - `df` import, else
the `deep-filter` binary at the shared-environment location
(`<vep_home>/bin/deep-filter`, `PIPELINE_DEEPFILTER_BINARY` to name
another, `scripts/install_deepfilternet.sh` to fill it), else the same
binary on PATH - and REFUSES BY NAME when none answers.

`tests/unit/audio/test_audio_mix.py`, `tests/unit/audio/test_audio_mix.py`,
`tests/unit/context/test_plan_values.py`.

The local measurements of both tools on the captain's dialogue (noise
floor, speech level and WER per clip and per Voice Isolation amount)
and the dependency conditioning: docs/evidence/dialogue_cleanup.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from library.tools.ren_refusal import RenRefusal


class DialogueCleanupRefused(RenRefusal):
    """A dialogue cleanup cannot be planned, staged, or applied."""


#: The only cleanup tools. `voice_isolation` is Resolve's own
#: per-track Voice Isolation (`Timeline.SetVoiceIsolationState`,
#: amount 0..100, Studio-only since 21.1). `deepfilternet` is a
#: processed stem the build places natively through the OTIO route.
#: `audio_ops` is a plan-declared chain of EQ, de-ess and dereverb
#: operations, delivered as the same kind of stem.
TOOLS = ("voice_isolation", "deepfilternet", "audio_ops")

#: Resolve's own Voice Isolation scale, read off the 21.1 stub and the
#: rung-3a probe (`SetVoiceIsolationState(1, {isEnabled, amount: 60})`
#: returning True and reading back). The engine offers no scale of its
#: own: a bound narrower than Resolve's would be taste (AGENTS.md 10.5).
VOICE_ISOLATION_MIN = 0
VOICE_ISOLATION_MAX = 100

#: The entry keys step 5.02 reads. Anything else is REFUSED, never
#: dropped - an unread key is how a probe's SFX `at_word` landed 3.06 s
#: early on the block start.
CLEANUP_ENTRY_KEYS = frozenset({
    "source",
    "tool",
    "amount",
    "span_start",
    "span_end",
    "operations",
    "why",
})

#: Measure-grade sample rate for stems. DeepFilterNet is full-band at
#: 48 kHz; the stem stays there so the OTIO route places exactly what
#: was measured.
STEM_SAMPLE_RATE = 48000


def _refuse(what: str, why: str, fix: str) -> DialogueCleanupRefused:
    return DialogueCleanupRefused(what=what, why=why, fix=fix)


# ── Availability ─────────────────────────────────────────────────────

def _deepfilter_binary_path() -> str:
    """The `deep-filter` executable the binary method runs, or "".

    One resolution, shared by the probe and the run: the
    shared-environment location first (`PIPELINE_DEEPFILTER_BINARY`
    outright, else `<vep_home>/bin/deep-filter` - see
    `shared_environment.deepfilter_binary`), then PATH as the legacy
    rung. Two different answers here is how a probe's "available"
    becomes a run's "left PATH mid-run".
    """
    try:
        from library.tools import shared_environment as _se
    except ImportError:  # imported as `tools.dialogue_cleanup` from inside library/
        try:
            from tools import shared_environment as _se
        except ImportError:
            _se = None
    if _se is not None:
        try:
            candidate = _se.deepfilter_binary()
        except Exception:
            candidate = None
        if (candidate is not None and candidate.is_file()
                and os.access(candidate, os.X_OK)):
            return str(candidate)
    return shutil.which("deep-filter") or ""


def deepfilternet_probe() -> dict:
    """Whether this interpreter can run DeepFilterNet, and how.

    Returns `{"available", "method", "reason"}`. `method` is `python`
    (the `df` package imports), `binary` (the `deep-filter` Rust binary
    at the shared-environment location or on PATH), or "" when neither
    answers. Unavailability is a stated fact with the measured block,
    not an exception: the plan context carries it so the model asks
    for what the build can do.
    """
    try:
        import importlib.util as _ilu

        if _ilu.find_spec("df") is not None:
            return {"available": True, "method": "python",
                    "reason": "the df package imports in this interpreter"}
    except (ImportError, ValueError, AttributeError):
        pass
    binary = _deepfilter_binary_path()
    if binary:
        return {"available": True, "method": "binary",
                "reason": f"the deep-filter binary answers at {binary}"}
    return {
        "available": False,
        "method": "",
        "reason": (
            "no deep-filter binary answers here: deepfilternet pins "
            "numpy<2 against this stack's numpy 2.x and deepfilterlib "
            "0.5.6 ships no cp312 macOS-arm64 wheel, so it is not in "
            "requirements.txt. Install deepfilternet==0.5.6 with "
            "deepfilterlib==0.5.6 on Python 3.11 (torch 2.8/torchaudio "
            "2.8) for the python method, or run "
            "scripts/install_deepfilternet.sh for the prebuilt binary "
            "(<vep_home>/bin/deep-filter, or PIPELINE_DEEPFILTER_BINARY "
            "to name another)."
        ),
    }


def voice_isolation_note() -> dict:
    """What the plan context says about Voice Isolation availability.

    The call needs a live Resolve Studio timeline, which no plan-time
    probe can see - so this states the precondition instead of a
    verdict. The build refuses by name when Resolve answers otherwise.
    """
    return {
        "available": "at build",
        "method": "resolve",
        "reason": (
            "Timeline.SetVoiceIsolationState with Get read-back, applied "
            "to the speech track at build time (rung 3a read back "
            "amount 60). Scripting is Studio-only since Resolve 21.1; "
            "the build refuses by name when Resolve is absent or "
            "declines."
        ),
    }


def audio_operations_note() -> dict:
    """What the plan-declared EQ/de-ess/dereverb chain can deliver."""
    from library.tools.audio_effects import OPERATION_KEYS

    ffmpeg = shutil.which("ffmpeg")
    return {
        "available": bool(ffmpeg),
        "method": "ffmpeg plus offline WPE",
        "operations": sorted(OPERATION_KEYS),
        "reason": (f"ffmpeg answers at {ffmpeg}; dereverb uses the local "
                   "NumPy/SciPy WPE implementation"
                   if ffmpeg else "ffmpeg is not available on PATH"),
    }


# ── Plan validation ──────────────────────────────────────────────────

def validate_cleanup_request(entry: dict) -> dict:
    """A plan entry normalised, or refused by name.

    Returns `{"source", "tool", "amount", "span_start", "span_end",
    "why"}` plus `operations` when declared. `amount`/`span_*` are None
    when not given. Refuses an unknown tool, a Voice Isolation amount outside Resolve's own
    0..100, a span that is not a positive range, and an entry with no
    `why` or no `source` - a cleanup nobody justified is exactly what
    "never an engine default" exists to remove.
    """
    if not isinstance(entry, dict):
        raise _refuse(
            "dialogue cleanup cannot be planned: an entry is not an object",
            "the plan list carries a row the step cannot read field by "
            "field.",
            "re-plan the cleanup list as objects with keys "
            + ", ".join(sorted(CLEANUP_ENTRY_KEYS)) + ".",
        )
    source = entry.get("source")
    if not (isinstance(source, str) and source.strip()):
        raise _refuse(
            "dialogue cleanup cannot be planned: an entry names no source",
            "a cleanup with no source would apply to whatever the build "
            "guessed, which is an engine default by another name.",
            "name the source file (basename matches) each entry cleans, "
            "or drop the entry.",
        )
    tool = entry.get("tool")
    if tool not in TOOLS:
        raise _refuse(
            f"dialogue cleanup cannot be planned: {tool!r} is not a "
            f"cleanup tool",
            "the build applies voice_isolation through Resolve, "
            "deepfilternet as a staged stem, and audio_ops as a declared "
            "EQ/de-ess/dereverb stem - another name would reach no code "
            "and ship as an uncleaned source reported clean.",
            f"re-plan with one of: {', '.join(TOOLS)}.",
        )
    amount = entry.get("amount")
    if tool == "voice_isolation":
        if amount is None:
            raise _refuse(
                f"dialogue cleanup cannot be planned: voice_isolation on "
                f"{source!r} names no amount",
                "Resolve's Voice Isolation takes 0..100 and the engine "
                "holds no default amount - a substituted one would be "
                "taste nobody chose (AGENTS.md 10.5).",
                "name the amount (0..100) with the measured floor that "
                "made it that strong, or drop the entry.",
            )
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise _refuse(
                f"dialogue cleanup cannot be planned: amount {amount!r} "
                f"is not a number",
                "the amount reaches Timeline.SetVoiceIsolationState, "
                "which takes 0..100.",
                "re-plan the amount as an integer 0..100.",
            ) from None
        if not VOICE_ISOLATION_MIN <= amount <= VOICE_ISOLATION_MAX:
            raise _refuse(
                f"dialogue cleanup cannot be planned: amount {amount} is "
                f"outside Resolve's 0..100",
                "the amount is Resolve's own scale, passed through - "
                "outside it nothing answers.",
                "re-plan the amount inside 0..100.",
            )
    elif amount is not None:
        raise _refuse(
            f"dialogue cleanup cannot be planned: {tool} on "
            f"{source!r} carries an amount",
            "this tool has no amount dial. An amount beside it would "
            "reach no code.",
            "drop the amount key, or switch the tool to voice_isolation.",
        )
    operations = None
    if "operations" in entry:
        try:
            from library.tools.audio_effects import (
                AudioOperationError,
                validate_operations,
            )
            operations = validate_operations(entry["operations"])
        except AudioOperationError as exc:
            raise _refuse(
                f"dialogue cleanup cannot be planned: operations on "
                f"{source!r} are invalid",
                str(exc),
                "re-plan with declared high_pass, equalizer, de_ess, or "
                "dereverb operations and their required measurements.",
            ) from exc
        if not operations:
            raise _refuse(
                f"dialogue cleanup cannot be planned: operations on "
                f"{source!r} are empty",
                "an empty chain would be an unread plan field and could "
                "claim treatment was delivered without changing the audio.",
                "remove operations or name at least one real operation.",
            )
    if tool == "audio_ops" and not operations:
        raise _refuse(
            f"dialogue cleanup cannot be planned: audio_ops on "
            f"{source!r} names no operations",
            "an empty chain would place an unchanged stem and claim the "
            "requested treatment was delivered.",
            "name at least one plan-declared audio operation, or choose "
            "a cleanup tool that carries out the requested treatment.",
        )
    span_start, span_end = entry.get("span_start"), entry.get("span_end")
    if (span_start is None) != (span_end is None):
        raise _refuse(
            f"dialogue cleanup cannot be planned: {source!r} carries half "
            f"a span",
            "a span with one end would clean from (or to) wherever the "
            "build guessed.",
            "give both span_start and span_end in source seconds, or "
            "neither (the whole played range).",
        )
    if span_start is not None:
        try:
            span_start, span_end = float(span_start), float(span_end)
        except (TypeError, ValueError):
            raise _refuse(
                f"dialogue cleanup cannot be planned: span "
                f"{span_start!r}-{span_end!r} is not seconds",
                "spans are source seconds, the unit every word timing "
                "already carries.",
                "re-plan the span as two numbers in source seconds.",
            ) from None
        if not span_end > span_start:
            raise _refuse(
                f"dialogue cleanup cannot be planned: span "
                f"{span_start}-{span_end} runs backwards",
                "a non-positive span stages an empty stem, which is a "
                "gap reported as cleaned.",
                "re-plan with span_end past span_start, in source "
                "seconds.",
            )
    why = entry.get("why")
    if not (isinstance(why, str) and why.strip()):
        raise _refuse(
            f"dialogue cleanup cannot be planned: the entry on {source!r} "
            f"carries no why",
            "a cleanup nobody justified is an engine default with a "
            "plan-shaped excuse - 5.01's rule, unchanged.",
            "state what in the measured floor made this tool (and this "
            "amount) the answer, or drop the entry.",
        )
    normalized = {
        "source": source.strip(),
        "tool": tool,
        "amount": amount,
        "span_start": span_start,
        "span_end": span_end,
        "why": why.strip(),
    }
    if operations is not None:
        normalized["operations"] = operations
    return normalized


# ── Source measurement (the plan context) ────────────────────────────

def spans_by_source(structure: list, a_roll_assignments) -> tuple:
    """Played ranges and word spans per source file, in source seconds.

    Joins the audio spine blocks (`clip_id` + `word_timestamps`, read
    directly per the spine contract) to the played ranges
    (`video_segments` source_file/video_in/video_out) through the
    segment `clip_id`. Returns `({source: [(in, out)]},
    {source: [(word_start, word_end)]})` with word spans clipped to
    what the edit really plays - timings from unplayed takes must not
    exclude the room the edit rests on. Both the 5.02 bridge and
    compile_manifest read this, so the two halves cannot join
    differently and disagree.
    """
    words_by_clip: dict = {}
    for block in structure or []:
        if not isinstance(block, dict):
            continue
        content = block.get("content")
        if not isinstance(content, dict):
            content = {}
        clip_id = block.get("clip_id") or content.get("clip_id")
        stamps = block.get("word_timestamps") or content.get("word_timestamps")
        if not clip_id or not isinstance(stamps, list):
            continue
        spans = []
        for stamp in stamps:
            if not isinstance(stamp, dict):
                continue
            try:
                start, end = (float(stamp["source_start"]),
                              float(stamp["source_end"]))
            except (KeyError, TypeError, ValueError):
                continue
            if end > start:
                spans.append((start, end))
        if spans:
            words_by_clip.setdefault(clip_id, []).extend(spans)

    played: dict = {}
    clips: dict = {}
    for entry in a_roll_assignments or []:
        if not isinstance(entry, dict):
            continue
        for segment in entry.get("video_segments") or []:
            if not isinstance(segment, dict):
                continue
            source = segment.get("source_file")
            try:
                start, end = (float(segment["video_in"]),
                              float(segment["video_out"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not source or not end > start:
                continue
            played.setdefault(source, []).append((start, end))
            if segment.get("clip_id"):
                clips.setdefault(source, set()).add(segment["clip_id"])

    spans: dict = {}
    for source, ranges in played.items():
        out = []
        for clip_id in clips.get(source, ()):
            for start, end in words_by_clip.get(clip_id, []):
                if any(start < r_end and end > r_start
                       for r_start, r_end in ranges):
                    out.append((start, end))
        spans[source] = sorted(out)
    return played, spans

def measure_source(source_file: str, played_ranges: list,
                   speech_spans: list) -> dict:
    """The noise record for one played source, absences stated.

    `played_ranges` are (in, out) source seconds the edit plays;
    `speech_spans` are word timings in the same unit (the spine
    contract's `source_start`/`source_end`, read directly). Returns the
    floor (`room_tone.measure_room_tone`), the speech level over the
    played ranges (`speech_loudness.measure_range`), the room-tone
    dropouts (`room_tone.find_dropouts` - digital-silence spans inside
    the played ranges, the stretches where the room the source carries
    drops out), and `floor_unmeasured_reason` when the floor refuses -
    never a default level reported as measured. The floor and the
    dropouts are independent measurements: a source can hold digital-
    silence spans in its played ranges whether or not it has audible
    room to fill them with. The FILL needs both - `plan_room_tone_fills`
    plans nothing for a source with dropouts but no floor, because
    filling from silence is the defect `room_tone` exists to remove.
    """
    from library.tools import room_tone as _room
    from library.tools import speech_loudness as _loud

    record = {"source_file": source_file,
              "played_ranges": [list(r) for r in played_ranges or []]}
    try:
        floor = _room.measure_room_tone(source_file, speech_spans or [])
        record["floor"] = {
            "level_dbfs": floor.get("level_dbfs"),
            "peak_dbfs": floor.get("peak_dbfs"),
            "segment_start": floor.get("segment_start"),
            "segment_end": floor.get("segment_end"),
            "gaps_considered": floor.get("gaps_considered"),
        }
        record["floor_unmeasured_reason"] = ""
    except Exception as exc:
        record["floor"] = {}
        record["floor_unmeasured_reason"] = str(exc)[:300]
    try:
        record["dropouts"] = _room.find_dropouts(
            source_file, played_ranges or [])
    except Exception as exc:
        record["dropouts"] = []
        record["dropouts_unmeasured_reason"] = str(exc)[:300]
    speech = None
    for start, end in played_ranges or []:
        reading = _loud.measure_range(source_file, start, end)
        if reading.get("measured") and (
                speech is None or reading["integrated_lufs"]
                > speech["integrated_lufs"]):
            speech = reading
    record["speech"] = speech or {"measured": False,
                                   "reason": "no played range measured"}
    return record


# ── Fairlight chain planning ─────────────────────────────────────────

def source_spectra(source_file: str, played_ranges: list,
                   speech_spans: list, floor_segment) -> dict:
    """Speech and floor spectra for one played source, absences stated.

    One decode (`room_tone.decode_mono`, resampled to the measurement
    rate), sliced two ways: the speech spectrum over the played word
    spans (each span clipped to the ranges the edit really plays - room
    between words must not dilute the speech), the floor spectrum over
    the measured floor segment `measure_room_tone` answered. Both go
    through `room_tone.spectrum_db`, so the bands and edges are the
    analyzer's own (`SPECTRUM_EDGES_HZ`) - no second spectrum
    implementation. Bands keep their -inf in process (a band with no
    energy reads -inf, exactly like silence does in `rms_dbfs`); -inf
    never enters pipeline state, only the bands the planner derives.

    `floor_segment` is the `(segment_start, segment_end)` the floor
    record measured, or None when the floor refused. Either absence is
    stated on the record: no floor, or no played speech, means no
    chain can be derived for the source - the caller plans nothing for
    it and says so, the way `plan_room_tone_fills` plans nothing for a
    source with dropouts but no floor. A file that does not decode
    raises the decode refusal; the caller records it per source and
    never sinks the run on one unreadable file.

    Both spectra are measured over EQUAL-length windows (each side
    truncated to the shorter): the analyzer's band means average over
    their bins, so a pure tone reads hotter in the shorter window and
    unequal windows would report a phantom excess. The truncation is
    the comparison's own requirement, stated on the record as
    `window_seconds` - never silent resampling of the evidence.
    """
    try:
        from library.tools import room_tone as _room
    except ImportError:
        from tools import room_tone as _room

    samples, rate = _room.decode_mono(source_file)
    edges = list(_room.SPECTRUM_EDGES_HZ)
    empty = [float("-inf")] * (len(edges) - 1)

    def _slice(start: float, end: float) -> list:
        lo = max(0, int(start * rate))
        hi = min(len(samples), int(end * rate))
        return samples[lo:hi] if hi > lo else []

    ranges = [(float(s), float(e)) for s, e in played_ranges or []
              if float(e) > float(s)]
    speech_samples: list = []
    for start, end in speech_spans or []:
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not end > start:
            continue
        for range_start, range_end in ranges:
            lo, hi = max(start, range_start), min(end, range_end)
            if hi > lo:
                speech_samples.extend(_slice(lo, hi))

    if floor_segment is not None:
        try:
            floor_start, floor_end = float(floor_segment[0]), float(
                floor_segment[1])
        except (TypeError, ValueError, IndexError):
            floor_start, floor_end = 0.0, 0.0
        floor_samples = (_slice(floor_start, floor_end)
                         if floor_end > floor_start else [])
    else:
        floor_samples = []

    record: dict = {
        "source_file": source_file,
        "sample_rate": rate,
        "spectrum_edges_hz": edges,
        "speech_seconds": round(len(speech_samples) / rate, 3)
        if rate else 0.0,
        "floor_seconds": round(len(floor_samples) / rate, 3)
        if rate else 0.0,
        "absences": [],
    }
    window = min(len(speech_samples), len(floor_samples))
    if window == 0:
        record["speech_bands_db"] = list(empty)
        record["floor_bands_db"] = list(empty)
        record["window_seconds"] = 0.0
        if not speech_samples:
            record["absences"].append(
                "no played word span carries speech on this source, so "
                "no speech spectrum can be measured")
        if not floor_samples:
            record["absences"].append(
                "the floor refused on this source (or its segment "
                "covers no samples), so nothing can say which bands "
                "carry speech above the room")
        return record
    record["window_seconds"] = round(window / rate, 3)
    record["speech_bands_db"] = _room.spectrum_db(speech_samples[:window],
                                                  rate)
    record["floor_bands_db"] = _room.spectrum_db(floor_samples[:window],
                                                 rate)
    return record


def plan_dialogue_eq(measured: list, edges: list) -> dict:
    """The dialogue-bus EQ this material's own spectra ask for, derived.

    `measured` is one `source_spectra` record per played dialogue
    source plus its bus weight (`weight_seconds` - the played speech
    seconds the source contributes to the dialogue bus); `edges` are
    the analyzer's band edges both spectra share. Every number derives
    from these spectra; every number's source is recorded beside it in
    `basis`, the way `_speech_reference` records the basis its level
    was read on. At most two bands: a high-pass where the bus carries
    no speech above its own room, and one peaking cut on the worst
    measured hump. Anything that cannot be derived is absent and said:

    - excess is speech minus floor per band, averaged across the
      sources that carry both, weighted by played speech seconds. A
      band no source measures is -inf, never zero-filled.
    - the high-pass is the top edge of the lowest contiguous run of
      bands at or below the MEDIAN band excess, starting at the bottom
      and never reaching the peak band. The median is the material's
      own spectral center - no invented dB threshold: bands at or
      below it carry no more speech-above-room than the typical band,
      and a word's own transient lift sits at or under the median with
      them instead of defeating the comparison. No run means the
      speech starts above the center: no high-pass, stated. The peak
      band stops the run, so a flat spectrum (whose peak is the bottom
      band) plans no high-pass, stated.
    - the peaking band is the band of maximum excess over the median of
      the finite band excesses - the worst hump relative to the
      source's own spectrum. Its gain is minus that above-median
      excess (the hump cut back to the material's own median, never a
      chosen amount), its frequency the band's geometric center, its Q
      the center over the width of the contiguous above-median run
      around it (the hump's measured width - the definition of Q, not
      a choice). A flat spectrum plans no cut, stated.
    - each band is validated through `audio_chain.validate_eq_band`
      before it joins the plan. A band the intent schema refuses (a
      cut deeper than Resolve's scale, a Q outside it) is DROPPED with
      the reason - never clamped into something the measurement did
      not say.

    Returns `{"bands", "basis", "dropped"}`. `bands` are plain dicts
    ready for `audio_chain.validate_intent`; they carry no spectra,
    only the derived controls plus their basis strings.
    """
    try:
        from library.tools import audio_chain as _chain
    except ImportError:
        from tools import audio_chain as _chain

    basis: dict = {}
    dropped: list = []
    if not measured:
        return {"bands": [], "basis": basis, "dropped": dropped,
                "why_empty": "no played dialogue source was measured"}
    band_count = len(edges) - 1
    for row in measured:
        if (len(row.get("speech_bands_db") or []) != band_count
                or len(row.get("floor_bands_db") or []) != band_count):
            raise ValueError(
                "plan_dialogue_eq needs spectra on the analyzer's own "
                f"{band_count} bands for every source")

    answered = [r.get("source_file", "?") for r in measured]
    basis["sources"] = (
        f"{len(measured)} played dialogue source(s) answered: "
        + ", ".join(answered))

    excess: list = []
    for band in range(band_count):
        total = 0.0
        weight = 0.0
        for row in measured:
            speech = row["speech_bands_db"][band]
            floor = row["floor_bands_db"][band]
            if speech == float("-inf") or floor == float("-inf"):
                continue
            w = float(row.get("weight_seconds") or 0.0)
            if w <= 0.0:
                continue
            total += w * (speech - floor)
            weight += w
        excess.append(total / weight if weight > 0.0 else float("-inf"))

    finite = [e for e in excess if e != float("-inf")]
    if not finite:
        return {"bands": [], "basis": basis, "dropped": dropped,
                "why_empty": "no band carries speech above any measured "
                             "floor on this material"}

    ordered = sorted(finite)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (
        ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2.0
    peak = max(range(band_count),
               key=lambda b: excess[b]
               if excess[b] != float("-inf") else float("-inf"))
    peak_excess = excess[peak]
    peak_above = peak_excess - median
    peak_lo, peak_hi = edges[peak], edges[peak + 1]

    bands: list = []

    # The peaking cut first: the high-pass guard reads its band.
    if peak_above <= 0.0:
        basis["peaking"] = (
            f"no peaking cut: the strongest band ({peak_lo:g}-"
            f"{peak_hi:g} Hz, excess {peak_excess:.1f} dB) sits at or "
            f"below the median band excess ({median:.1f} dB), so the "
            f"spectrum is flat and there is no hump to cut")
    else:
        run_lo, run_hi = peak, peak
        while (run_lo - 1 >= 0
               and excess[run_lo - 1] != float("-inf")
               and excess[run_lo - 1] - median > 0.0):
            run_lo -= 1
        while (run_hi + 1 < band_count
               and excess[run_hi + 1] != float("-inf")
               and excess[run_hi + 1] - median > 0.0):
            run_hi += 1
        center = (peak_lo * peak_hi) ** 0.5
        width = edges[run_hi + 1] - edges[run_lo]
        candidate = {
            "type": "peaking",
            "frequency_hz": round(center, 1),
            "gain_db": round(-peak_above, 1),
            "q": round(center / width, 2) if width > 0.0 else 0.0,
        }
        peak_basis = (
            f"peaking cut {candidate['gain_db']:.1f} dB at "
            f"{candidate['frequency_hz']:.1f} Hz: the "
            f"{peak_lo:g}-{peak_hi:g} Hz band exceeds this "
            f"material's own median band excess by "
            f"{peak_above:.1f} dB; Q {candidate['q']:.2f} is the "
            f"band center over the width of the measured "
            f"above-median run ({edges[run_lo]:g}-"
            f"{edges[run_hi + 1]:g} Hz)")
        try:
            _chain.validate_eq_band(candidate)
        except Exception as exc:
            dropped.append({"band": candidate, "reason": str(exc)[:300]})
            basis["peaking"] = (
                f"no peaking cut: the derived band {candidate} is "
                f"outside the intent scale and is dropped, never "
                f"clamped - {str(exc)[:200]}")
        else:
            bands.append(candidate)
            basis["peaking"] = peak_basis

    # The high-pass: the bottom run at or below the material's own
    # median band excess, stopping at the peak band.
    run = 0
    while (run < peak and excess[run] != float("-inf")
           and excess[run] <= median):
        run += 1
    if run == 0:
        basis["high_pass"] = (
            f"no high-pass: the bottom band ({edges[0]:g}-"
            f"{edges[1]:g} Hz) carries speech at or above the "
            f"material's own median band excess ({median:.1f} dB), so "
            f"the speech starts above the spectral center and "
            f"filtering the bottom would cut it")
    else:
        cutoff = edges[run]
        candidate = {
            "type": "high_pass",
            "frequency_hz": cutoff,
            "gain_db": 0.0,
            "q": 0.71,
        }
        hp_basis = (
            f"high-pass at {cutoff:g} Hz: the bands below sit at or "
            f"below this material's own median band excess "
            f"({median:.1f} dB) - they carry no more speech above "
            f"the room than the typical band, and the run stops at "
            f"the speech hump's own band ({peak_lo:g}-"
            f"{peak_hi:g} Hz); {cutoff:g} Hz is the analyzer's own "
            f"band edge, not a chosen number. Gain 0.0 dB and Q "
            f"0.71 are the high-pass's own mechanical identity (no "
            f"boost, Butterworth) - the Fairlight control they map "
            f"to carries no gain or Q")
        try:
            _chain.validate_eq_band(candidate)
        except Exception as exc:
            dropped.append({"band": candidate,
                            "reason": str(exc)[:300]})
            basis["high_pass"] = (
                f"no high-pass: the derived band {candidate} is "
                f"outside the intent scale and is dropped, never "
                f"clamped - {str(exc)[:200]}")
        else:
            bands.insert(0, candidate)
            basis["high_pass"] = hp_basis

    result: dict = {"bands": bands, "basis": basis, "dropped": dropped}
    if not bands:
        result["why_empty"] = ("neither a below-room run nor an "
                                "above-median hump survived validation - "
                                "see basis")
    return result


# ── DeepFilterNet staging ────────────────────────────────────────────

def run_deepfilternet(source_wav: str, out_wav: str, *,
                      speech_spans: list | None = None) -> dict:
    """Clean `source_wav` into `out_wav`. Returns the staged record.

    Tries the `df` package, then the `deep-filter` binary, and refuses
    by name when neither answers (see `deepfilternet_probe` for the
    measured install block). The record carries wall time and the
    before/after floor and speech level - the proof the render is
    judged against. A floor that refuses on either side is stated, not
    defaulted.
    """
    if not os.path.isfile(source_wav):
        raise _refuse(
            f"dialogue cleanup cannot be staged: {source_wav!r} is not on "
            f"disk",
            "the stem is processed from the source file itself - a stem "
            "built without reading it would be invented signal.",
            "point the cleanup request at the real source file, or drop "
            "the entry.",
        )
    probe = deepfilternet_probe()
    if not probe["available"]:
        raise _refuse(
            "dialogue cleanup cannot be staged: DeepFilterNet answers "
            "nowhere here",
            probe["reason"],
            "request voice_isolation instead (Resolve applies it at "
            "build), or install DeepFilterNet and re-run compile.",
        )
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    started = time.time()
    if probe["method"] == "python":
        _enhance_python(source_wav, out_wav)
    else:
        _enhance_binary(source_wav, out_wav)
    wall = round(time.time() - started, 2)
    if not os.path.isfile(out_wav):
        raise _refuse(
            f"dialogue cleanup cannot be staged: DeepFilterNet ran but "
            f"{out_wav!r} is not on disk",
            "a run that reports success with no file is the fake-success "
            "stub rung 1 removed.",
            "re-run compile; if it repeats, the DeepFilterNet install "
            "is broken - reinstall it.",
        )
    return {
        "stem_file": out_wav,
        "method": probe["method"],
        "wall_seconds": wall,
        "floor_before_dbfs": _floor_or_none(source_wav, speech_spans),
        "floor_after_dbfs": _floor_or_none(out_wav, speech_spans),
        "speech_before_lufs": _speech_or_none(source_wav),
        "speech_after_lufs": _speech_or_none(out_wav),
    }


def _enhance_python(source_wav: str, out_wav: str) -> None:
    from df import enhance as _df_enhance
    from df import init_df as _df_init
    from df.io import load_audio as _df_load
    from df.io import save_audio as _df_save

    model, state, _ = _df_init()
    audio, _ = _df_load(source_wav, sr=state.sr())
    cleaned = _df_enhance(model, state, audio)
    _df_save(out_wav, cleaned, state.sr())


def _enhance_binary(source_wav: str, out_wav: str) -> None:
    binary = _deepfilter_binary_path()
    if not binary:
        raise _refuse(
            "dialogue cleanup cannot be staged: the deep-filter binary "
            "is unreachable mid-run",
            "the probe saw it and the run does not - the environment "
            "moved under the build.",
            "run scripts/install_deepfilternet.sh (or set "
            "PIPELINE_DEEPFILTER_BINARY) and re-run compile.",
        )
    # The binary writes the stem under the INPUT's basename inside the
    # output directory (measured 2026-09-24: `probe.wav -o out` leaves
    # `out/probe.wav`), never under the name it was asked for - so it
    # runs into a scratch directory and the stem is moved into place.
    # Running it straight into the stem's directory would overwrite a
    # range file that shares the input's name.
    import tempfile

    work_dir = tempfile.mkdtemp(prefix="deepfilter_",
                                dir=os.path.dirname(os.path.abspath(out_wav)))
    try:
        proc = subprocess.run(
            [binary, source_wav, "-o", work_dir],
            capture_output=True, encoding="utf-8", timeout=900, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _refuse(
            "dialogue cleanup cannot be staged: the deep-filter binary "
            f"did not run ({exc})",
            "the stem is processed audio, not a planned level - without "
            "the run there is no stem.",
            "repair the deep-filter install and re-run compile, or "
            "re-plan with voice_isolation.",
        ) from exc
    produced = os.path.join(work_dir, os.path.basename(source_wav))
    if proc.returncode != 0 or not os.path.isfile(produced):
        raise _refuse(
            "dialogue cleanup cannot be staged: the deep-filter binary "
            f"answered {proc.returncode}",
            (proc.stderr or "")[-300:] or "no diagnostic on stderr.",
            "re-run compile; if it repeats, re-plan with "
            "voice_isolation.",
        )
    try:
        os.replace(produced, out_wav)
    finally:
        try:
            os.rmdir(work_dir)
        except OSError:
            pass


def _floor_or_none(path: str, spans: list | None):
    from library.tools import room_tone as _room

    try:
        return (_room.measure_room_tone(path, spans or [])
                .get("level_dbfs"))
    except Exception:
        return None


def _speech_or_none(path: str):
    from library.tools import room_tone as _room
    from library.tools import speech_loudness as _loud

    try:
        duration = len(_room.decode_mono(path)[0]) / _room.MEASURE_SR
    except Exception:
        return None
    reading = _loud.measure_range(path, 0, duration)
    return reading.get("integrated_lufs") if reading.get("measured") else None


# ── Compile-time staging ───────────────────────────────────────────

def stage_deepfilternet(request: dict, clips: list, spans: list,
                        out_dir: str) -> list:
    """Compatibility name for staging the requested dialogue chain."""
    return stage_audio_chain(request, clips, spans, out_dir)


def stage_audio_chain(request: dict, clips: list, spans: list,
                      out_dir: str) -> list:
    """Stage plan-declared cleanup and audio-operation stems.

    `request` is a validated entry (`validate_cleanup_request` runs
    again here - the plan crossed a step boundary since 5.02, and a
    second validation is cheaper than a stem for a request nobody can
    read). `clips` are the manifest V1 clips cut from the requested
    source, each carrying `source_in`/`source_out` plus its timeline
    frames; `spans` are that source's word timings in source seconds.

    A request span intersects each played range (a range outside the
    span is dropped, a straddling one is clipped - never extended).
    Each surviving range is extracted to 48 kHz mono, cleaned, and
    recorded with its timeline frames so the OTIO route can swap it in
    and the render can be judged: floor before/after, speech
    before/after, wall time. A source with no played range refuses by
    name, and so does a span that intersects nothing.
    """
    row = validate_cleanup_request(request)
    stem_dir = os.path.join(out_dir, "dialogue_cleanup")
    os.makedirs(stem_dir, exist_ok=True)
    span = ((row["span_start"], row["span_end"])
            if row["span_start"] is not None else None)

    ranges = []
    for clip in clips or []:
        try:
            start, end = (float(clip.get("source_in", 0.0)),
                          float(clip.get("source_out", 0.0)))
        except (TypeError, ValueError):
            continue
        if not end > start:
            continue
        if span is not None:
            start, end = max(start, span[0]), min(end, span[1])
            if not end > start:
                continue
        ranges.append((start, end, clip))
    if not ranges:
        if span is not None:
            raise _refuse(
                f"dialogue cleanup cannot be staged: span "
                f"{span[0]}-{span[1]}s of {row['source']!r} intersects "
                f"nothing the edit plays",
                "a span outside the played ranges stages an empty stem, "
                "which is a gap reported as cleaned.",
                "re-plan the span inside a played range, or drop the "
                "span and clean the whole played range.",
            )
        raise _refuse(
            f"dialogue cleanup cannot be staged: {row['source']!r} "
            f"reaches no played clip",
            "the request names a source the edit does not play - the "
            "stem would land nowhere.",
            "re-plan the source against cleanup_context.sources, or "
            "drop the entry.",
        )

    stems = []
    for index, (start, end, clip) in enumerate(ranges):
        label = str(clip.get("label") or os.path.basename(
            row["source"])) + f"_clean{index}"
        source_file = clip.get("source_file") or ""
        if not (isinstance(source_file, str) and source_file
                and os.path.isfile(source_file)):
            raise _refuse(
                f"dialogue cleanup cannot be staged: the played clip "
                f"{label!r} names no source file on disk",
                "the stem is processed from the source file itself - a "
                "basename the plan carried cannot be decoded.",
                "restore the source file, or drop the cleanup request.",
            )
        range_wav = os.path.join(stem_dir, f"{label}_range.wav")
        stem_wav = os.path.join(stem_dir, f"{label}.wav")
        _extract_range(source_file, start, end, range_wav)
        local_spans = [(s - start, e - start) for s, e in spans or []
                       if e > start and s < end]
        started = time.time()
        operations = row.get("operations", [])
        if row["tool"] == "deepfilternet":
            deepfilter_path = (
                os.path.join(stem_dir, f"{label}_deepfilter.wav")
                if operations else stem_wav)
            staged = run_deepfilternet(
                range_wav, deepfilter_path, speech_spans=local_spans)
            if operations:
                from library.tools.audio_effects import apply_operations
                try:
                    effect_result = apply_operations(
                        deepfilter_path, stem_wav, operations)
                except Exception as exc:
                    raise _refuse(
                        f"dialogue audio operations could not be staged "
                        f"for {label}",
                        str(exc)[:500],
                        "correct the plan-declared operation parameters "
                        "or restore the ffmpeg/WPE processing path.",
                    ) from exc
                staged.update({
                    "stem_file": stem_wav,
                    "output_path": stem_wav,
                    "operations": effect_result["operations"],
                    "floor_after_dbfs": _floor_or_none(
                        stem_wav, local_spans),
                    "speech_after_lufs": _speech_or_none(stem_wav),
                    "wall_seconds": round(
                        staged["wall_seconds"] + time.time() - started, 2),
                })
        else:
            from library.tools import audio_effects
            try:
                effect_result = audio_effects.apply_operations(
                    range_wav, stem_wav, operations)
            except Exception as exc:
                raise _refuse(
                    f"dialogue audio operations could not be staged for "
                    f"{label}",
                    str(exc)[:500],
                    "correct the plan-declared operation parameters or "
                    "restore the ffmpeg/WPE processing path.",
                ) from exc
            staged = {
                "stem_file": stem_wav,
                "method": "plan_declared_audio_operations",
                "wall_seconds": round(time.time() - started, 2),
                "floor_before_dbfs": _floor_or_none(range_wav, local_spans),
                "floor_after_dbfs": _floor_or_none(stem_wav, local_spans),
                "speech_before_lufs": _speech_or_none(range_wav),
                "speech_after_lufs": _speech_or_none(stem_wav),
                "operations": effect_result["operations"],
            }
        staged.update({
            "label": label,
            "source_file": clip.get("source_file") or row["source"],
            "request_source": row["source"],
            "source_in": round(start, 3),
            "source_out": round(end, 3),
            "timeline_in_frame": clip.get("timeline_in_frame"),
            "timeline_out_frame": clip.get("timeline_out_frame"),
            "why": row["why"],
            "tool": row["tool"],
        })
        # Measured above as mono; delivered in the source's layout.
        staged["channels"] = _match_source_channels(
            staged["stem_file"], source_file)
        stems.append(staged)
    return stems


def _source_channels(source_file: str) -> int:
    """Channel count of the source's first audio stream."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels", "-of", "csv=p=0",
         source_file],
        capture_output=True, encoding="utf-8", timeout=60, check=False,
    )
    try:
        return int(proc.stdout.strip().splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise _refuse(
            f"dialogue cleanup cannot be staged: the channel layout of "
            f"{os.path.basename(source_file)} does not read",
            (proc.stderr or "")[-300:] or "ffprobe printed no channel count.",
            "restore the source file, or drop the cleanup request.",
        ) from exc


def _match_source_channels(stem_wav: str, source_file: str) -> int:
    """Rewrite a mono stem in the source's channel count, each at unity.

    The stem replaces the source clip's media on a track laid out for the
    source. A mono stem there plays in the FIRST channel only: every
    cleaned segment of the K2 evals (and main's rung-5d DeepFilterNet
    stems) delivered its dialogue about 15 dB down in the right ear.
    """
    channels = _source_channels(source_file)
    if channels <= 1:
        return 1
    pan = "|".join(f"c{index}=c0" for index in range(channels))
    widened = stem_wav + ".channels.wav"
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", stem_wav,
         "-af", f"pan={channels}c|{pan}", "-c:a", "pcm_s24le", "-y",
         widened],
        capture_output=True, encoding="utf-8", timeout=900, check=False,
    )
    if proc.returncode != 0 or not os.path.isfile(widened):
        raise _refuse(
            f"dialogue cleanup cannot be staged: the stem "
            f"{os.path.basename(stem_wav)} could not be laid out as "
            f"{channels} channels",
            (proc.stderr or "")[-300:] or "no diagnostic on stderr.",
            "restore ffmpeg, or drop the cleanup request.",
        )
    os.replace(widened, stem_wav)
    return channels


def _extract_range(source_file: str, start: float, end: float,
                   out_wav: str) -> None:
    """One played range to 48 kHz mono WAV, or a named refusal."""
    if not os.path.isfile(source_file):
        raise _refuse(
            f"dialogue cleanup cannot be staged: {source_file!r} is not "
            f"on disk",
            "the stem is processed from the source file itself.",
            "restore the source file, or drop the cleanup request.",
        )
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin",
             "-ss", f"{max(0.0, start):.3f}",
             "-t", f"{end - start:.3f}",
             "-i", source_file, "-ac", "1", "-ar",
             str(STEM_SAMPLE_RATE), "-c:a", "pcm_s16le", "-y", out_wav],
            capture_output=True, encoding="utf-8", timeout=900, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _refuse(
            f"dialogue cleanup cannot be staged: ffmpeg did not run "
            f"({exc})",
            "the range extraction is the stem's first half - without it "
            "there is nothing to clean.",
            "install ffmpeg and re-run compile, or drop the cleanup "
            "request.",
        ) from exc
    if proc.returncode != 0 or not os.path.isfile(out_wav):
        raise _refuse(
            f"dialogue cleanup cannot be staged: ffmpeg could not read "
            f"{start:.2f}-{end:.2f}s of {os.path.basename(source_file)}",
            (proc.stderr or "")[-300:] or "no diagnostic on stderr.",
            "restore the source file, or drop the cleanup request.",
        )


# ── Voice Isolation application ──────────────────────────────────────

def apply_voice_isolation(timeline, track: int, amount: int) -> dict:
    """Set Voice Isolation on one audio track, judged by re-read.

    The same discipline as the `audio isolate` resolve-axi verb: the
    write is claimed only when `GetVoiceIsolationState` re-reads
    `isEnabled True` at the requested amount. A True that isolated
    nothing fails naming both. `timeline` is duck-typed so fakes judge
    the discipline in tests.
    """
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise _refuse(
            f"dialogue cleanup cannot be applied: amount {amount!r} is "
            f"not a number",
            "the amount reaches Timeline.SetVoiceIsolationState, which "
            "takes 0..100.",
            "re-plan the amount as an integer 0..100.",
        ) from None
    if not VOICE_ISOLATION_MIN <= amount <= VOICE_ISOLATION_MAX:
        raise _refuse(
            f"dialogue cleanup cannot be applied: amount {amount} is "
            f"outside Resolve's 0..100",
            "the amount is Resolve's own scale, passed through.",
            "re-plan the amount inside 0..100.",
        )
    try:
        count = timeline.GetTrackCount("audio") or 0
    except Exception as exc:
        raise _refuse(
            "dialogue cleanup cannot be applied: the timeline would not "
            f"report its audio tracks ({exc})",
            "without a track count the build cannot say the track names "
            "anything.",
            "verify the timeline in Resolve and re-run the build.",
        ) from exc
    if track < 1 or track > count:
        raise _refuse(
            f"dialogue cleanup cannot be applied: audio track {track} "
            f"names nothing (the timeline holds {count})",
            "an isolation setting on a missing track would ship as "
            "cleaned dialogue that was never touched.",
            "re-plan against the speech track the build really placed.",
        )
    state = {"isEnabled": True, "amount": amount}
    try:
        wrote = bool(timeline.SetVoiceIsolationState(track, dict(state)))
    except Exception as exc:
        raise _refuse(
            f"dialogue cleanup cannot be applied: SetVoiceIsolationState "
            f"raised ({exc})",
            "the call declined instead of answering True.",
            "verify Voice Isolation in Resolve Studio (scripting is "
            "Studio-only since 21.1) and re-run the build.",
        ) from exc
    if not wrote:
        raise _refuse(
            f"dialogue cleanup cannot be applied: "
            f"SetVoiceIsolationState(audio{track}) answered False",
            "False is Resolve declining the write - claiming it would "
            "be the fake success rung 1 removed.",
            "verify Voice Isolation in Resolve Studio and re-run the "
            "build, or re-plan with deepfilternet.",
        )
    try:
        back = timeline.GetVoiceIsolationState(track) or {}
    except Exception as exc:
        raise _refuse(
            "dialogue cleanup cannot be applied: the isolation re-read "
            f"raised ({exc})",
            "a write without a read-back is an unjudged placement.",
            "verify by hand in Resolve and re-run the build.",
        ) from exc
    if (not back.get("isEnabled")
            or back.get("amount") != amount):
        raise _refuse(
            f"dialogue cleanup cannot be applied: "
            f"SetVoiceIsolationState reports True and re-reads {back!r} "
            f"for {state!r}",
            "True that isolated nothing is the defect this read-back "
            "exists to catch.",
            "verify Voice Isolation in Resolve Studio and re-run the "
            "build, or re-plan with deepfilternet.",
        )
    return {"track": track, "amount": amount, "verified": "re-reads equal"}


# ── Dialogue Leveler application ─────────────────────────────────────

#: The TimelineItemProperties keys the Dialogue Leveler answers, read
#: off the installed scripting stub (DaVinciResolveScript.pyi, class
#: TimelineItemProperties). Per-clip dynamics through
#: `TimelineItem.SetProperties`, judged by `GetProperties` re-read.
#: An older Resolve whose properties lack these keys refuses by name
#: below - the missing keys are the measurement, never an assumed
#: default. What the stub does NOT list among the Audio keys is the
#: measured gap this lane records: no per-band EQ key exists
#: (AudioVolume/Pan/Pitch/VoiceIsolation/DialogueLeveler only), so
#: track EQ stays UI-only and out of this module.
LEVELER_PROPERTY_KEYS = (
    "AudioDialogueLevelerEnabled",
    "AudioDialogueLevelerMode",
    "AudioDialogueLevelerReduceLoudDialogue",
    "AudioDialogueLevelerLiftSoftDialogue",
    "AudioDialogueLevelerBackgroundReduction",
    "AudioDialogueLevelerOutputGain",
)

#: The output gain's own scale, read off the stub (0.0 to 6.0 dB).
LEVELER_OUTPUT_GAIN_MIN = 0.0
LEVELER_OUTPUT_GAIN_MAX = 6.0


def _refuse_leveler(what: str, why: str, fix: str) -> DialogueCleanupRefused:
    return DialogueCleanupRefused(what=what, why=why, fix=fix)


def validate_leveler_properties(raw: dict) -> dict:
    """The six leveler controls, checked against Resolve's own scale.

    Every key required - a leveler with an undeclared mode or gain
    would land at whatever the panel last held, which is state, not a
    plan. The mode is Resolve's own float enum (read live off the
    `DIALOGUE_LEVELER_MODE_*` constants, never retyped here); the
    three shaping switches are booleans; the output gain is the
    stub's 0.0..6.0 dB. Unknown keys refuse - an `EQBand1Frequency`
    is how a track-EQ hope would smuggle itself onto a clip that
    cannot carry one.
    """
    if not isinstance(raw, dict):
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: leveler properties "
            "are not an object",
            "the six controls cannot be read from this plan.",
            "plan the six leveler keys as an object.",
        )
    unknown = sorted(set(raw) - set(LEVELER_PROPERTY_KEYS))
    if unknown:
        raise _refuse_leveler(
            f"dialogue cleanup cannot be applied: unknown leveler "
            f"keys {unknown}",
            "a clip carries no control by that name - an EQ band key "
            "here is the track-EQ gap wearing a plan's clothes.",
            "drop the unknown keys or plan them where they belong.",
        )
    missing = [key for key in LEVELER_PROPERTY_KEYS if key not in raw]
    if missing:
        raise _refuse_leveler(
            f"dialogue cleanup cannot be applied: leveler keys missing "
            f"{missing}",
            "an undeclared control would land at the panel's leftover "
            "state.",
            "declare all six leveler keys explicitly.",
        )
    for key in ("AudioDialogueLevelerEnabled",
                "AudioDialogueLevelerReduceLoudDialogue",
                "AudioDialogueLevelerLiftSoftDialogue",
                "AudioDialogueLevelerBackgroundReduction"):
        if not isinstance(raw[key], bool):
            raise _refuse_leveler(
                f"dialogue cleanup cannot be applied: {key} "
                f"{raw[key]!r} is not a boolean",
                "the shaping switches take true/false.",
                f"re-plan {key} as a boolean.",
            )
    mode = raw["AudioDialogueLevelerMode"]
    if isinstance(mode, bool) or not isinstance(mode, (int, float)):
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: leveler mode "
            f"{mode!r} is not a number",
            "the mode is Resolve's own float enum.",
            "re-plan the mode from the DIALOGUE_LEVELER_MODE_* "
            "constants.",
        )
    gain = raw["AudioDialogueLevelerOutputGain"]
    if (isinstance(gain, bool) or not isinstance(gain, (int, float))
            or not LEVELER_OUTPUT_GAIN_MIN <= float(gain)
            <= LEVELER_OUTPUT_GAIN_MAX):
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: output gain "
            f"{gain!r} is outside Resolve's "
            f"{LEVELER_OUTPUT_GAIN_MIN:g}..{LEVELER_OUTPUT_GAIN_MAX:g} dB",
            "the gain is Resolve's own scale, passed through.",
            "re-plan the output gain inside 0.0..6.0 dB.",
        )
    return {"AudioDialogueLevelerEnabled": bool(raw[
        "AudioDialogueLevelerEnabled"]),
        "AudioDialogueLevelerMode": float(mode),
        "AudioDialogueLevelerReduceLoudDialogue": bool(raw[
            "AudioDialogueLevelerReduceLoudDialogue"]),
        "AudioDialogueLevelerLiftSoftDialogue": bool(raw[
            "AudioDialogueLevelerLiftSoftDialogue"]),
        "AudioDialogueLevelerBackgroundReduction": bool(raw[
            "AudioDialogueLevelerBackgroundReduction"]),
        "AudioDialogueLevelerOutputGain": float(gain)}


def apply_dialogue_leveler(timeline_item, properties: dict) -> dict:
    """Set the Dialogue Leveler on one timeline clip, judged by re-read.

    The same discipline as `apply_voice_isolation`: the write is
    claimed only when `GetProperties` re-reads every set key equal
    (booleans exact, numbers within 1e-6 - the re-read crosses into
    Resolve and back). A True that leveled nothing fails naming both.
    `timeline_item` is duck-typed so fakes judge the discipline in
    tests.
    """
    checked = validate_leveler_properties(properties)
    try:
        current = timeline_item.GetProperties() or {}
    except Exception as exc:
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: the clip would not "
            f"report its properties ({exc})",
            "without a property read the build cannot say the clip "
            "can carry a leveler.",
            "verify the timeline clip in Resolve and re-run the build.",
        ) from exc
    absent = [key for key in LEVELER_PROPERTY_KEYS if key not in current]
    if absent:
        raise _refuse_leveler(
            f"dialogue cleanup cannot be applied: this Resolve reports "
            f"no {absent}",
            "a clip without leveler properties is an older Resolve - "
            "claiming the set would be the fake success rung 1 removed.",
            "re-plan with voice_isolation or deepfilternet, or run a "
            "Resolve whose scripting exposes the leveler.",
        )
    try:
        wrote = bool(timeline_item.SetProperties(dict(checked)))
    except Exception as exc:
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: SetProperties raised "
            f"({exc})",
            "the call declined instead of answering True.",
            "verify the timeline clip in Resolve and re-run the build.",
        ) from exc
    if not wrote:
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: SetProperties "
            "answered False",
            "False is Resolve declining the write - claiming it would "
            "be the fake success rung 1 removed.",
            "verify the timeline clip in Resolve and re-run the build.",
        )
    try:
        back = timeline_item.GetProperties() or {}
    except Exception as exc:
        raise _refuse_leveler(
            "dialogue cleanup cannot be applied: the leveler re-read "
            f"raised ({exc})",
            "a write without a read-back is an unjudged placement.",
            "verify by hand in Resolve and re-run the build.",
        ) from exc
    mismatched = []
    for key in LEVELER_PROPERTY_KEYS:
        want = checked[key]
        got = back.get(key)
        if isinstance(want, bool):
            # Resolve may re-read a boolean as 0/1 - compare by value,
            # never by identity.
            if not isinstance(got, (bool, int)) or bool(got) != want:
                mismatched.append(key)
        elif (not isinstance(got, (int, float))
              or abs(float(got) - want) > 1e-6):
            mismatched.append(key)
    if mismatched:
        raise _refuse_leveler(
            f"dialogue cleanup cannot be applied: SetProperties "
            f"reports True and re-reads {back!r} for {checked!r}",
            "True that leveled nothing is the defect this read-back "
            "exists to catch.",
            "verify by hand in Resolve and re-run the build.",
        )
    return {"verified": "re-reads equal",
            "properties": dict(checked)}


# ── The OTIO half of a stem ──────────────────────────────────────────

def rewrite_clip_media_to_stem(clip: dict, stem_file: str) -> dict:
    """Point an OTIO audio clip at its cleaned stem. Returns the clip.

    The stem IS the played range (staged from source_in..source_out at
    compile time), so the clip's source start resets to zero while its
    duration - what the timeline plays - is untouched. Refuses a stem
    that is not on disk: the import answers None for a whole timeline
    over one missing file and says nothing about which.
    """
    if not (isinstance(stem_file, str) and stem_file
            and os.path.exists(stem_file)):
        raise _refuse(
            f"dialogue cleanup cannot be placed: stem {stem_file!r} is "
            f"not on disk",
            "Resolve's OTIO import returns None for a whole timeline "
            "when one referenced file is missing, naming nothing.",
            "re-run compile to re-stage the stem, or drop the cleanup "
            "request.",
        )
    references = clip.get("media_references") or {}
    key = clip.get("active_media_reference_key", "")
    reference = references.get(key)
    if not isinstance(reference, dict):
        raise _refuse(
            "dialogue cleanup cannot be placed: the clip names no active "
            "media reference",
            "without the reference there is no URL to rewrite - the "
            "stem would land nowhere.",
            "drop the cleanup request: this clip's media cannot be "
            "swapped.",
        )
    reference["target_url"] = "file://" + os.path.abspath(stem_file)
    source_range = clip.get("source_range") or {}
    start = source_range.get("start_time")
    if isinstance(start, dict) and "value" in start:
        start["value"] = 0
    return clip
