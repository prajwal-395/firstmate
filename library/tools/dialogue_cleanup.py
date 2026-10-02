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
    played ranges (`speech_loudness.measure_range`), and
    `floor_unmeasured_reason` when the floor refuses - never a default
    level reported as measured.
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
