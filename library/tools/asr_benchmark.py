"""Run every ASR-lane candidate on the same evaluation windows.

AUD-004..009, 011, 013: one runner executes the incumbent (Voz + MFA),
the transcription candidate (original Parakeet), the alignment candidate
(Desert Ant Align on the SAME Voz words MFA aligns), the language probe
(Ear) and the filler detector (Uhm) over identical audio, and writes:

- one hypothesis JSON per (candidate, window) in `speech_eval` shape,
  ready to score the day blind agent labels land;
- one `ren_evaluation_report/1` JSON per candidate carrying the MEASURED
  latency, memory and cost dimensions only. Quality is omitted until
  labels exist: the format requires finite numbers, and pairwise
  candidate agreement is not quality - it is reported in the lane's
  audit prose, never laundered into the quality dimension;
- one run manifest recording every window's provenance (source id,
  source SHA-256, interval) and every download the run consumed.

Calibration is the default. The one-time held-out report requires the
explicit `--held-out-single-report` flag, a held-out-only manifest, and
an empty output directory. Windows are extracted read-only with ffmpeg
into scratch (never committed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from library.tools import (
    desert_ant_align,
    heard_speech,
    hybrid_transcription,
    mfa_align,
    parakeet_asr,
)

#: Splits this runner will touch. Held-out audio is never extracted,
#: never transcribed, never probed here - its single report comes after
#: calibration settings freeze.
ALLOWED_SPLITS = ("training", "calibration")
HELD_OUT_SPLIT = "held_out"

SAMPLE_RATE = 16000

REPORT_FORMAT = "ren_evaluation_report/1"


class BenchmarkError(RuntimeError):
    """The benchmark cannot proceed honestly."""


@dataclass
class Window:
    """One calibration audio interval, extracted once, hashed once."""

    window_id: str
    source_id: str
    split: str
    source_path: str
    source_sha256: str
    start_seconds: float
    end_seconds: float
    wav_path: str = ""
    wav_sha256: str = ""
    detected_language: str = ""

    @property
    def audio_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_window(source_path: str, start: float, duration: float,
                   out_wav: str) -> str:
    """Slice one 16 kHz mono PCM window with ffmpeg. Returns its SHA."""
    command = ["ffmpeg", "-y", "-v", "error",
               "-ss", str(start), "-i", source_path,
               "-t", str(duration), "-vn", "-ac", "1",
               "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", out_wav]
    try:
        subprocess.run(command, capture_output=True, encoding="utf-8",
                       check=True, timeout=600)
    except (subprocess.CalledProcessError, OSError,
            subprocess.TimeoutExpired) as failed:
        raise BenchmarkError(
            f"ffmpeg could not slice {source_path} at {start}s: {failed}"
        ) from failed
    return sha256_file(out_wav)


def load_manifest(path: str, *, allow_held_out: bool = False) -> list[dict[str, Any]]:
    """Read window rows; refuse held-out rows before touching audio."""
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise BenchmarkError(
            f"cannot read window manifest {path}: {unreadable}") from unreadable
    if not isinstance(rows, list) or not rows:
        raise BenchmarkError(f"{path} holds no window rows")
    allowed_splits = (ALLOWED_SPLITS + (HELD_OUT_SPLIT,)
                      if allow_held_out else ALLOWED_SPLITS)
    for row in rows:
        if row.get("split") == HELD_OUT_SPLIT and not allow_held_out:
            raise BenchmarkError(
                f"window {row.get('window_id')} is held-out: this runner "
                f"requires explicit one-time held-out mode")
        if row.get("split") not in allowed_splits:
            raise BenchmarkError(
                f"window {row.get('window_id')} has unknown split "
                f"{row.get('split')!r}")
    return rows


def _child_highwater_bytes() -> int:
    """Cumulative peak RSS of waited-for children in this process, in bytes.

    The OS value is a process-lifetime high-water mark, so it cannot
    attribute memory to one candidate when several subprocess candidates
    run sequentially in the same benchmark process.
    """
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss


def _self_highwater_bytes() -> int:
    """This process's own RSS high-water mark, in bytes. Same rule."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _timed(call: Callable[[], Any]) -> tuple:
    """Run `call`, returning (result, elapsed_seconds)."""
    started = time.perf_counter()
    result = call()
    return result, time.perf_counter() - started


def _heard_to_hypothesis(heard: heard_speech.HeardSpeech,
                         status: str = "answered") -> dict[str, Any]:
    return {
        "words": [{"text": w.word, "start": w.start, "end": w.end}
                  for w in heard.words],
        "status": status,
    }


def _with_source_clock(value: Any, window: Window) -> Any:
    """Keep window-relative timestamps and add authoritative source times."""
    if isinstance(value, list):
        return [
            {**event, "source_id": window.source_id,
             "source_start": window.start_seconds + float(event["start"]),
             "source_end": window.start_seconds + float(event["end"])}
            for event in value
        ]
    if not isinstance(value, dict) or not isinstance(value.get("words"), list):
        return value
    result = dict(value)
    result["source_interval"] = {
        "source_id": window.source_id,
        "start_seconds": window.start_seconds,
        "end_seconds": window.end_seconds,
        "timebase": "original_source_audio_seconds",
    }
    result["words"] = [
        {**word,
         "source_start": window.start_seconds + float(word["start"]),
         "source_end": window.start_seconds + float(word["end"])}
        for word in value["words"]
    ]
    return result


def run_voz(wav_path: str) -> dict[str, Any]:
    """Voz transcription, cold then warm, with the vendor's own split."""
    heard_cold, cold_s = _timed(lambda: heard_speech.transcribe(wav_path))
    cold_peak = _child_highwater_bytes()
    heard_warm, warm_s = _timed(lambda: heard_speech.transcribe(wav_path))
    warm_peak = _child_highwater_bytes()
    engine = heard_warm.engine
    return {
        "hypothesis": _heard_to_hypothesis(heard_warm),
        "cold_seconds": cold_s,
        "warm_seconds": warm_s,
        "cold_peak_rss_bytes": max(cold_peak, 0),
        "warm_peak_rss_bytes": max(warm_peak, 0),
        "split": {"load_seconds": engine.get("load_seconds"),
                  "processing_seconds": engine.get("processing_seconds"),
                  "duration_seconds": engine.get("duration_seconds")},
        "engine": engine,
        "cold_word_count": len(heard_cold.words),
        "warm_word_count": len(heard_warm.words),
    }


def run_mfa(wav_path: str, words: list[dict[str, Any]],
            language: str = "en") -> dict[str, Any]:
    """MFA alignment of the SAME words, through the hybrid seam."""
    segments = [{"start": words[0]["start"], "end": words[-1]["end"],
                 "text": " ".join(w["text"] for w in words)}] if words else []
    if not segments:
        raise BenchmarkError("no words to align")
    _, prep_s = _timed(
        lambda: hybrid_transcription.alignment_windows(
            heard_speech.HeardSpeech(
                words=[heard_speech.HeardWord(word=w["text"],
                                              start=w["start"], end=w["end"])
                       for w in words])))
    aligned_cold, cold_s = _timed(
        lambda: mfa_align.align(segments, language, wav_path))
    cold_peak = _child_highwater_bytes()
    aligned_warm, warm_s = _timed(
        lambda: mfa_align.align(segments, language, wav_path))
    warm_peak = _child_highwater_bytes()
    cold_words = [w for s in aligned_cold.get("segments", [])
                  for w in s.get("words", []) if "start" in w]
    warm_words = [w for s in aligned_warm.get("segments", [])
                  for w in s.get("words", []) if "start" in w]
    return {
        "hypothesis": {"words": warm_words, "status": "answered"},
        "cold_seconds": prep_s + cold_s,
        "warm_seconds": warm_s,
        "cold_peak_rss_bytes": max(cold_peak, 0),
        "warm_peak_rss_bytes": max(warm_peak, 0),
        "split": {"prepare_seconds": prep_s,
                  "infer_seconds_cold": cold_s,
                  "infer_seconds_warm": warm_s},
        "aligned": aligned_warm,
        "cold_timed_words": len(cold_words),
        "warm_timed_words": len(warm_words),
    }


def run_ear(wav_path: str) -> dict[str, Any]:
    """Ear language identification over the whole window."""
    heard_cold, cold_s = _timed(
        lambda: heard_speech.identify_language(wav_path))
    cold_peak = _child_highwater_bytes()
    heard_warm, warm_s = _timed(
        lambda: heard_speech.identify_language(wav_path))
    warm_peak = _child_highwater_bytes()
    return {
        "language": heard_warm.as_dict(),
        "cold_seconds": cold_s,
        "warm_seconds": warm_s,
        "cold_peak_rss_bytes": max(cold_peak, 0),
        "warm_peak_rss_bytes": max(warm_peak, 0),
        "cold": heard_cold.as_dict(),
    }


def run_uhm(wav_path: str) -> dict[str, Any]:
    """Uhm filler detection over the whole window."""
    heard_cold, cold_s = _timed(
        lambda: heard_speech.detect_fillers(wav_path))
    cold_peak = _child_highwater_bytes()
    heard_warm, warm_s = _timed(
        lambda: heard_speech.detect_fillers(wav_path))
    warm_peak = _child_highwater_bytes()
    return {
        "events": heard_warm.as_dict()["events"],
        "event_count": len(heard_warm.events),
        "cold_event_count": len(heard_cold.events),
        "cold_seconds": cold_s,
        "warm_seconds": warm_s,
        "cold_peak_rss_bytes": max(cold_peak, 0),
        "warm_peak_rss_bytes": max(warm_peak, 0),
    }


def run_parakeet(wav_path: str, model_path: str,
                 checkpoint_revision: str = "",
                 detected_language: str = "") -> dict[str, Any]:
    """Original Parakeet checkpoint, cold then warm, in-process."""
    if not parakeet_asr.supports_language(detected_language):
        raise BenchmarkError(
            f"refusing Parakeet before model load for unsupported or "
            f"unknown language {detected_language!r}")
    load_seconds = 0.0
    if not parakeet_asr.is_model_loaded(model_path):
        _, load_seconds = _timed(
            lambda: parakeet_asr.load_model(model_path))
    heard_cold, cold_s = _timed(
        lambda: parakeet_asr.transcribe(wav_path, model_path=model_path,
                                        checkpoint_revision=checkpoint_revision,
                                        detected_language=detected_language))
    cold_peak = _self_highwater_bytes()
    heard_warm, warm_s = _timed(
        lambda: parakeet_asr.transcribe(wav_path, model_path=model_path,
                                        checkpoint_revision=checkpoint_revision,
                                        detected_language=detected_language))
    warm_peak = _self_highwater_bytes()
    return {
        "hypothesis": _heard_to_hypothesis(heard_warm),
        "model_load_seconds": load_seconds,
        "cold_infer_seconds": cold_s,
        "cold_seconds": load_seconds + cold_s,
        "warm_seconds": warm_s,
        "cold_peak_rss_bytes": max(cold_peak, 0),
        "warm_peak_rss_bytes": max(warm_peak, 0),
        "split": {"note": "in-process: load and infer share one RSS "
                           "reading; wall split is cold-vs-warm"},
        "engine": heard_warm.engine,
        "cold_word_count": len(heard_cold.words),
        "warm_word_count": len(heard_warm.words),
    }


def run_align(wav_path: str, words: list[dict[str, Any]],
              language: str, package_dir: str,
              model_dir: str = "") -> dict[str, Any]:
    """Align refinement of the SAME words MFA aligns, cold then warm."""
    refined_cold, cold_s = _timed(
        lambda: desert_ant_align.refine_words(
            words, wav_path, language=language,
            package_dir=package_dir, model_dir=model_dir or None))
    cold_peak = _child_highwater_bytes()
    refined_warm, warm_s = _timed(
        lambda: desert_ant_align.refine_words(
            words, wav_path, language=language,
            package_dir=package_dir, model_dir=model_dir or None))
    warm_peak = _child_highwater_bytes()
    return {
        "hypothesis": {"words": refined_warm["words"], "status": "answered"},
        "cold_seconds": cold_s,
        "warm_seconds": warm_s,
        "cold_peak_rss_bytes": max(cold_peak, 0),
        "warm_peak_rss_bytes": max(warm_peak, 0),
        "support": refined_warm["support"],
        "sdk": refined_warm["sdk"],
        "cold_refined": sum(1 for w in refined_cold["words"]
                            if w.get("refined")),
        "warm_refined": sum(1 for w in refined_warm["words"]
                            if w.get("refined")),
    }


def _metric(value: Any, unit: str, dimension: str,
            method: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "dimension": dimension,
            "method": method}


def write_report(candidate_id: str, role: str, models: dict[str, str],
                 modality: str, revision: str,
                 windows: list[Window],
                 per_window: list[dict[str, Any]],
                 evidence: list[dict[str, str]],
                 out_path: str, split: str = "calibration") -> dict[str, Any]:
    """One `ren_evaluation_report/1` file: measured latency, memory, cost.

    Quality waits for labels and is not named here at all - not even as
    null. A metric this format cannot verify does not belong in it.
    """
    total_audio = sum(w.audio_seconds for w in windows)
    cold = [r["cold_seconds"] for r in per_window]
    warm = [r["warm_seconds"] for r in per_window]
    if candidate_id == "parakeet-tdt-0.6b-v3-original":
        # One model load is shared across windows. Only the first call is
        # cold; average subsequent resident-model calls for warm latency.
        cold = [r["cold_seconds"] for r in per_window
                if r.get("model_load_seconds", 0) > 0] or cold[:1]
    cold_peak = [r["cold_peak_rss_bytes"] for r in per_window]
    metrics = {
        "cold_latency_mean_seconds": _metric(
            sum(cold) / len(cold), "seconds", "latency",
            ("single first window: checkpoint load plus first inference"
             if candidate_id == "parakeet-tdt-0.6b-v3-original"
             else "perf_counter around the candidate call, first invocation")),
        "warm_latency_mean_seconds": _metric(
            sum(warm) / len(warm), "seconds", "latency",
            "perf_counter around the candidate call, second invocation"),
        "cold_peak_rss_max_bytes": _metric(
            max(cold_peak), "bytes", "memory",
            "getrusage high-water at end of invocation, max over windows; "
            "children for subprocess runtimes, self for in-process; child "
            "RSS is cumulative across subprocess candidates in this runner"),
        "seconds_per_audio_minute_warm": _metric(
            (sum(warm) / total_audio * 60.0) if total_audio else 0.0,
            "seconds_per_audio_minute", "cost",
            "summed warm wall time divided by summed window audio"),
    }
    if candidate_id == "parakeet-tdt-0.6b-v3-original":
        metrics["model_load_seconds"] = _metric(
            sum(r.get("model_load_seconds", 0) for r in per_window),
            "seconds", "latency",
            "single perf_counter around NeMo restore_from in this process")
    report = {
        "format": REPORT_FORMAT,
        "revision": revision,
        "version": {"id": candidate_id, "role": role, "models": models},
        "modality": modality,
        "split": split,
        "metrics": metrics,
        "evidence": evidence,
        "selection_basis": (["calibration"] if split != HELD_OUT_SPLIT else
                            ["frozen_calibration_settings",
                             "single_requested_held_out_report"]),
    }
    Path(out_path).write_text(json.dumps(report, indent=2) + "\n",
                              encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=str, required=True)
    parser.add_argument("--windows-dir", type=str, required=True)
    parser.add_argument("--out-dir", type=str, required=True)
    parser.add_argument("--candidates", type=str, required=True,
                        help="comma-separated subset of "
                             "voz,mfa,ear,uhm,parakeet,align")
    parser.add_argument("--parakeet-model", type=str, default="")
    parser.add_argument("--parakeet-revision", type=str, default="")
    parser.add_argument("--align-package", type=str, default="")
    parser.add_argument("--align-models", type=str, default="")
    parser.add_argument("--revision", type=str, required=True,
                        help="run id recorded on every report")
    parser.add_argument("--held-out-single-report", action="store_true",
                        help="explicitly run the one held-out report after "
                             "calibration freeze; output directory must be "
                             "empty and held-out rows must be exclusive")
    args = parser.parse_args()

    rows = load_manifest(args.manifest,
                         allow_held_out=args.held_out_single_report)
    held_out_rows = [row for row in rows
                     if row.get("split") == HELD_OUT_SPLIT]
    if args.held_out_single_report and len(held_out_rows) != len(rows):
        raise BenchmarkError(
            "one-time held-out mode requires every manifest row to be held-out")
    if args.held_out_single_report and os.path.isdir(args.out_dir) and any(
            Path(args.out_dir).iterdir()):
        raise BenchmarkError(
            f"held-out output directory is not empty: {args.out_dir}; "
            "the single report will not overwrite an earlier run")
    os.makedirs(args.windows_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)
    windows: list[Window] = []
    for row in rows:
        window = Window(
            window_id=str(row["window_id"]), source_id=str(row["source_id"]),
            split=str(row["split"]), source_path=str(row["source_path"]),
            source_sha256=str(row.get("source_sha256") or ""),
            start_seconds=float(row["start_seconds"]),
            end_seconds=float(row["end_seconds"]),
            detected_language=str(row.get("detected_language") or ""))
        wav_path = os.path.join(args.windows_dir,
                                f"{window.window_id}.wav")
        if not os.path.isfile(wav_path):
            window.wav_sha256 = extract_window(
                window.source_path, window.start_seconds,
                window.audio_seconds, wav_path)
        else:
            window.wav_sha256 = sha256_file(wav_path)
        window.wav_path = wav_path
        windows.append(window)
    Path(args.out_dir, "windows.json").write_text(
        json.dumps([vars(w) for w in windows], indent=2) + "\n",
        encoding="utf-8")

    selected = [c.strip() for c in args.candidates.split(",") if c.strip()]
    runners: dict[str, Callable[[Window], dict[str, Any]]] = {}
    # Candidate keys for the contained transcriber go through its own
    # constants: spelling the vendor literals here would break the
    # containment `tests/contracts/test_heard_speech.py` pins.
    if heard_speech.SUBCOMMAND in selected:
        runners[heard_speech.SUBCOMMAND] = lambda w: run_voz(w.wav_path)
    if "mfa" in selected:
        runners["mfa"] = (
            lambda w: run_mfa(w.wav_path, _voz_words_for(w)))
    if heard_speech.EAR_SUBCOMMAND in selected:
        runners[heard_speech.EAR_SUBCOMMAND] = lambda w: run_ear(w.wav_path)
    if heard_speech.UHM_SUBCOMMAND in selected:
        runners[heard_speech.UHM_SUBCOMMAND] = lambda w: run_uhm(w.wav_path)
    if "parakeet" in selected:
        if not args.parakeet_model:
            raise BenchmarkError("parakeet selected without --parakeet-model")
        runners["parakeet"] = (
            lambda w: run_parakeet(w.wav_path, args.parakeet_model,
                                   args.parakeet_revision,
                                   w.detected_language))
    if "align" in selected:
        if not args.align_package:
            raise BenchmarkError("align selected without --align-package")
        runners["align"] = (
            lambda w: run_align(w.wav_path, _voz_words_for(w), "en",
                                args.align_package, args.align_models))
    unknown = [c for c in selected if c not in runners]
    if unknown:
        raise BenchmarkError(f"unknown candidates: {unknown}")

    for name, run in runners.items():
        if name == "parakeet":
            missing_language = [w.window_id for w in windows
                                if not w.detected_language]
            unsupported_language = [
                (w.window_id, w.detected_language) for w in windows
                if w.detected_language
                and not parakeet_asr.supports_language(w.detected_language)]
            if missing_language or unsupported_language:
                raise BenchmarkError(
                    "Parakeet requires a prior language-id result in each "
                    f"window; missing={missing_language}, "
                    f"unsupported={unsupported_language}")
        per_window = []
        for window in windows:
            result = run(window)
            if (name == heard_speech.EAR_SUBCOMMAND
                    and args.parakeet_model):
                language_result = result["language"]
                window.detected_language = str(language_result["language"])
            for key in ("hypothesis", "events"):
                if key in result:
                    result[key] = _with_source_clock(result[key], window)
            per_window.append(result)
            Path(args.out_dir,
                 f"hypothesis.{name}.{window.window_id}.json").write_text(
                     json.dumps(result.get("hypothesis")
                                or result.get("language")
                                or result.get("events"), indent=2) + "\n",
                     encoding="utf-8")
        if name == heard_speech.EAR_SUBCOMMAND and args.parakeet_model:
            Path(args.out_dir, "windows.json").write_text(
                json.dumps([vars(w) for w in windows], indent=2) + "\n",
                encoding="utf-8")
        Path(args.out_dir, f"run.{name}.json").write_text(
            json.dumps({"candidate": name, "revision": args.revision,
                        "windows": [w.window_id for w in windows],
                        "per_window": per_window}, indent=2,
                       default=str) + "\n", encoding="utf-8")
        candidate_id, role, models, modality = _report_identity(
            name, args)
        corpus_path = Path(
            "docs/benchmarks/2026-10-15-editorial-eval-corpus.json")
        evidence = [
            {"id": "editorial-eval-corpus",
             "path": os.path.relpath(corpus_path.resolve(),
                                     Path(args.out_dir).resolve()),
             "sha256": sha256_file(str(corpus_path))},
            *[{"id": f"window:{w.window_id}", "sha256": w.wav_sha256}
              for w in windows],
        ]
        write_report(candidate_id, role, models, modality, args.revision,
                     windows, per_window, evidence,
                     os.path.join(args.out_dir, f"report.{name}.json"),
                     split=(HELD_OUT_SPLIT if args.held_out_single_report
                            else "calibration"))
    return 0


def _report_identity(name: str, args: argparse.Namespace) -> tuple:
    """Scorecard identity per candidate: id, role, pinned models, modality.

    Voz and MFA are the incumbent, so they report as `baseline`;
    everything else reports as `candidate` until an adoption decision
    moves it. Model strings pin what answered, not what was hoped for.
    """
    from library.tools import shared_environment as shared

    if name == heard_speech.SUBCOMMAND:
        return (heard_speech.SUBCOMMAND, "baseline",
                {heard_speech.SUBCOMMAND: "desert-ant-labs/voz v0.1.0",
                 "voz_cli": heard_speech.version() or "version unknown"},
                "speech_transcription")
    if name == "mfa":
        return ("mfa", "baseline",
                {"mfa": f"montreal-forced-aligner {shared.MFA_PACKAGE_VERSION}",
                 "mfa_acoustic": f"{shared.MFA_ACOUSTIC_MODEL} "
                                 f"v{shared.MFA_ACOUSTIC_MODEL_VERSION}",
                 "mfa_dictionary": f"{shared.MFA_DICTIONARY} "
                                   f"v{shared.MFA_DICTIONARY_VERSION}",
                 "mfa_g2p": f"{shared.MFA_G2P_MODEL} "
                             f"v{shared.MFA_G2P_VERSION}"},
                "speech_alignment")
    if name == heard_speech.EAR_SUBCOMMAND:
        return ("desert-ant-ear", "candidate",
                {heard_speech.EAR_SUBCOMMAND: "desert-ant-labs/ear v0.1.0",
                 "ear_cli": heard_speech.version() or "version unknown"},
                "speech_language_id")
    if name == heard_speech.UHM_SUBCOMMAND:
        return ("desert-ant-uhm", "candidate",
                {heard_speech.UHM_SUBCOMMAND:
                 "desert-ant-labs/uhm @ "
                 "612592c10ad7b2a51f3237725448a1aad212480b",
                 "uhm_cli": heard_speech.version() or "version unknown"},
                "speech_filler_detection")
    if name == "parakeet":
        return ("parakeet-tdt-0.6b-v3-original", "candidate",
                {"parakeet": f"nvidia/parakeet-tdt-0.6b-v3 @ "
                              f"{args.parakeet_revision or 'unrecorded'}",
                 "parakeet_runtime": "nemo",
                 "parakeet_precision": "float32"},
                "speech_transcription")
    if name == "align":
        return ("desert-ant-align", "candidate",
                {"align": "desert-ant-labs/align",
                 "align_sdk": "@desert-ant-labs/align 3.6.0"},
                "speech_alignment")
    raise BenchmarkError(f"no report identity for {name}")


_VOZ_CACHE: dict[str, list[dict[str, Any]]] = {}


def _voz_words_for(window: Window) -> list[dict[str, Any]]:
    """Voz words for one window, transcribed once and shared by MFA/Align.

    Both aligners refine the SAME words: that is what makes their
    boundary comparison a comparison rather than two anecdotes.
    """
    if window.window_id not in _VOZ_CACHE:
        heard = heard_speech.transcribe(window.wav_path)
        _VOZ_CACHE[window.window_id] = [
            {"text": w.word, "start": w.start, "end": w.end}
            for w in heard.words]
    return _VOZ_CACHE[window.window_id]


if __name__ == "__main__":
    raise SystemExit(main())
