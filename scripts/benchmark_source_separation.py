"""Reproducible C6 source-separation comparison workers.

Create a synthetic fixture with ``prepare``; run each backend in a separate
process so peak RSS belongs to that backend; then combine results with
``compare``. The command deliberately has no download path. Model setup and
weight conversion are explicit operator steps outside measured inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _channel_first(samples: np.ndarray, channels: int) -> np.ndarray:
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim == 1:
        values = values[None, :]
    elif values.ndim == 2 and values.shape[0] != channels and values.shape[1] == channels:
        values = values.T
    if values.ndim != 2 or values.shape[0] != channels:
        raise ValueError(f"expected {channels} channel(s), got shape {values.shape}")
    return np.ascontiguousarray(values)


def prepare(directory: Path) -> None:
    import soundfile as sf

    from library.tools.analysis.source_separation import synthetic_c6_fixture

    directory.mkdir(parents=True, exist_ok=True)
    fixture = synthetic_c6_fixture()
    rate = int(fixture["sample_rate"])
    mono = np.asarray(fixture["mixture"], dtype=np.float32)
    sf.write(directory / "mixture.wav", np.stack((mono, mono), axis=1), rate,
             subtype="FLOAT")
    np.savez_compressed(
        directory / "truth.npz",
        **{key: np.asarray(fixture[key], dtype=np.float32)
           for key in ("dialogue", "music", "ambience")},
    )
    print(json.dumps({"fixture": "C6 synthetic three-stem proxy",
                      "sample_rate": rate,
                      "duration_seconds": len(mono) / rate,
                      "input": str(directory / "mixture.wav"),
                      "truth": str(directory / "truth.npz")}, indent=2))


def _run_baseline(input_path: Path) -> tuple[dict[str, np.ndarray], dict]:
    import torch
    from demucs_infer.api import Separator

    started = time.perf_counter()
    checkpoint_dir = Path(torch.hub.get_dir()) / "checkpoints"
    separator = Separator(model="955717e8", repo=checkpoint_dir,
                          device="cpu", shifts=0, jobs=0, progress=False)
    load_seconds = time.perf_counter() - started
    return _measure_runs(
        input_path, lambda audio, rate: _baseline_separate(separator, audio, rate),
        {"implementation": "demucs-infer/htdemucs", "model": "htdemucs",
         "revision": "955717e8", "device": "cpu", "weights_precision": "fp32",
         "shifts": 0, "overlap": 0.25, "sample_rate_hz": 44100},
        load_seconds,
    )


def _baseline_separate(separator, audio: np.ndarray, rate: int) -> dict[str, np.ndarray]:
    import torch

    _original, stems = separator.separate_tensor(torch.from_numpy(audio), sr=rate)
    return {name: _channel_first(value.detach().cpu().numpy(), audio.shape[0])
            for name, value in stems.items()}


def _run_mlx(input_path: Path) -> tuple[dict[str, np.ndarray], dict]:
    import inspect
    from importlib.metadata import version

    import mlx.core as mx

    mx.reset_peak_memory()
    from demucs_mlx import Separator

    started = time.perf_counter()
    separator_options = {
        "model": "htdemucs", "shifts": 0, "overlap": 0.25,
        "batch_size": 2, "progress": False,
    }
    # demucs-mlx 1.1.0 predates the `seed` constructor option. Keep the
    # benchmark settings matched while passing only supported options.
    if "seed" in inspect.signature(Separator).parameters:
        separator_options["seed"] = 0
    separator = Separator(**separator_options)
    load_seconds = time.perf_counter() - started

    def separate(audio: np.ndarray, _rate: int) -> dict[str, np.ndarray]:
        _origin, stems = separator.separate_tensor(audio)
        return {name: _channel_first(value, audio.shape[0])
                for name, value in stems.items()}

    return _measure_runs(
        input_path, separate,
        {"implementation": "demucs-mlx", "model": "htdemucs",
         "revision": "955717e8", "device": "Apple GPU / MLX",
         "runtime": f"demucs-mlx=={version('demucs-mlx')}",
         "weights_precision": "fp32; attention fp32",
         "shifts": 0, "overlap": 0.25, "sample_rate_hz": 44100,
         "batch_size": 2},
        load_seconds,
        mlx_memory=lambda: {
            "peak_bytes": int(mx.get_peak_memory()),
            "active_bytes": int(mx.get_active_memory()),
            "cache_bytes": int(mx.get_cache_memory()),
        },
    )


def _run_mlx_audio_separator(input_path: Path) -> tuple[dict[str, np.ndarray], dict]:
    import mlx.core as mx
    import soundfile as sf
    from mlx_audio_separator import Separator

    scratch = input_path.parent / "mlx-audio-separator"
    os.environ["MLX_AUDIO_SEPARATOR_DEMUCS_CACHE_DIR"] = str(
        scratch / "demucs-cache")
    mx.reset_peak_memory()
    output_dir = scratch / "stems"
    model_dir = scratch / "models"
    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    package_started = time.perf_counter()
    separator = Separator(
        output_dir=str(output_dir), model_file_dir=str(model_dir),
        output_format="WAV", sample_rate=44100,
        demucs_params={"shifts": 0, "overlap": 0.25,
                       "segments_enabled": True, "batch_size": 2,
                       "seed": 0, "attention_precision": "fp32",
                       "compile": True},
    )
    init_seconds = time.perf_counter() - package_started
    load_started = time.perf_counter()
    separator.load_model("htdemucs.yaml")
    load_seconds = time.perf_counter() - load_started

    def separate_files():
        for old_path in output_dir.glob("*"):
            old_path.unlink()
        outputs = separator.separate(str(input_path))
        if separator.failed_files:
            raise RuntimeError(f"mlx-audio-separator failed: {separator.failed_files}")
        stems: dict[str, np.ndarray] = {}
        for output in outputs:
            lower = Path(output).stem.lower()
            name = next((stem for stem in ("vocals", "drums", "bass", "other")
                         if stem in lower), None)
            if name is None:
                raise ValueError(f"cannot identify stem in {output!r}")
            samples, _rate = sf.read(output, dtype="float32", always_2d=True)
            stems[name] = _channel_first(samples, 2)
        if set(stems) != {"vocals", "drums", "bass", "other"}:
            raise ValueError(f"expected four Demucs stems, got {sorted(stems)}")
        output_files = {}
        for output in outputs:
            lower = Path(output).stem.lower()
            name = next(stem for stem in ("vocals", "drums", "bass", "other")
                        if stem in lower)
            output_files[name] = str(Path(output))
        return stems, dict(separator.last_perf_metrics or {}), output_files

    _first, cold_details, _cold_files = separate_files()
    warm_results = [separate_files() for _ in range(4)]
    latest, latest_details, output_files = warm_results[-1]
    warm_inference = [details.get("inference_s", 0.0)
                      for _stems, details, _paths in warm_results]
    warm_total = [details.get("total_s", 0.0)
                  for _stems, details, _paths in warm_results]
    return latest, {
        "schema": "ren_separation_benchmark/1",
        "version": {"id": "mlx-audio-separator/htdemucs",
                    "role": "candidate",
                    "models": {"source": "htdemucs",
                               "signature": "955717e8",
                               "runtime": "mlx-audio-separator"}},
        "modality": "audio.separate",
        "split": "calibration",
        "identity": {
            "implementation": "mlx-audio-separator",
            "version": "0.1.20",
            "model": "htdemucs",
            "revision": "955717e8",
            "device": "Apple GPU / MLX",
            "weights_precision": "fp32; attention fp32",
            "shifts": 0, "overlap": 0.25, "sample_rate_hz": 44100,
            "batch_size": 2,
        },
        "metrics": {
            "model_init_seconds": init_seconds,
            "model_load_seconds": load_seconds,
            "cold_inference_seconds": cold_details.get("inference_s", 0.0),
            "warm_inference_seconds_median": statistics.median(warm_inference),
            "warm_inference_seconds_each": warm_inference,
            "cold_input_decode_seconds": cold_details.get("decode_s", 0.0),
            "output_serialize_seconds": statistics.median(
                [details.get("write_s", 0.0)
                 for _stems, details, _paths in warm_results]),
            "complete_separation_seconds": statistics.median(warm_total),
            "package_warm_details": latest_details,
            "peak_rss_bytes": _rss_bytes(),
            "mlx_memory": {
                "peak_bytes": int(mx.get_peak_memory()),
                "active_bytes": int(mx.get_active_memory()),
                "cache_bytes": int(mx.get_cache_memory()),
            },
            "output_files": output_files,
            "output_path": str(output_dir),
        },
    }


def _measure_runs(input_path: Path, separate, identity: dict,
                  load_seconds: float, mlx_memory=None
                  ) -> tuple[dict[str, np.ndarray], dict]:
    import soundfile as sf

    def once():
        decode_started = time.perf_counter()
        frames, rate = sf.read(input_path, dtype="float32", always_2d=True)
        audio = np.ascontiguousarray(frames.T)
        decode_seconds = time.perf_counter() - decode_started
        infer_started = time.perf_counter()
        outputs = separate(audio, int(rate))
        infer_seconds = time.perf_counter() - infer_started
        return outputs, decode_seconds, infer_seconds

    first, cold_decode, cold_infer = once()
    warm: list[float] = []
    latest = first
    for _ in range(2):
        latest, _decode_seconds, infer_seconds = once()
        warm.append(infer_seconds)

    # Keep the output serialization cost in the end-to-end figure.
    result_dir = input_path.parent / "results" / identity["implementation"]
    result_dir.mkdir(parents=True, exist_ok=True)

    save_started = time.perf_counter()
    output_files = {}
    for stem_name, samples in latest.items():
        out_path = result_dir / f"{stem_name}.wav"
        sf.write(out_path, np.asarray(samples).T, 44100, subtype="FLOAT")
        output_files[stem_name] = str(out_path)
    serialize_seconds = time.perf_counter() - save_started
    median_infer = statistics.median(warm)
    complete_seconds = cold_decode + median_infer + serialize_seconds
    metrics = {
        "model_load_seconds": load_seconds,
        "cold_inference_seconds": cold_infer,
        "warm_inference_seconds_median": median_infer,
        "warm_inference_seconds_each": warm,
        "cold_input_decode_seconds": cold_decode,
        "output_serialize_seconds": serialize_seconds,
        "complete_separation_seconds": complete_seconds,
        "peak_rss_bytes": _rss_bytes(),
        "output_serialize_format": "WAV FLOAT 32-bit, all four stems",
        "output_files": output_files,
        "output_path": str(result_dir),
    }
    if mlx_memory is not None:
        metrics["mlx_memory"] = mlx_memory()
    return latest, {
        "schema": "ren_separation_benchmark/1",
        "version": {"id": identity["implementation"], "role": "candidate"
                    if identity["device"] != "cpu" else "baseline",
                    "models": {"source": "htdemucs", "signature": "955717e8",
                               "runtime": identity["implementation"]}},
        "modality": "audio.separate",
        "split": "calibration",
        "identity": identity,
        "metrics": metrics,
    }


def run(backend: str, input_path: Path, output_path: Path) -> None:
    if backend == "demucs":
        _outputs, report = _run_baseline(input_path)
    elif backend == "mlx-audio-separator":
        _outputs, report = _run_mlx_audio_separator(input_path)
    else:
        _outputs, report = _run_mlx(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def compare(baseline_path: Path, candidate_path: Path, truth_path: Path,
            output_path: Path, baseline_report_path: Path,
            candidate_report_path: Path) -> None:
    from library.tools.analysis.source_separation import bleed_db, si_sdr

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    import soundfile as sf

    def read_stems(result: dict) -> dict[str, np.ndarray]:
        files = result["metrics"]["output_files"]
        return {name: _channel_first(
            sf.read(path, dtype="float32", always_2d=True)[0], 2)
                for name, path in files.items()}

    before = read_stems(baseline)
    after = read_stems(candidate)
    with np.load(truth_path) as truth:
        metrics = {}
        waveform_parity = {}
        for stem in ("vocals", "drums", "bass", "other"):
            before_stem = before[stem].mean(axis=0)
            after_stem = after[stem].mean(axis=0)
            difference = after_stem - before_stem
            waveform_parity[stem] = {
                "difference_relative_l2": float(
                    np.linalg.norm(difference) /
                    max(np.linalg.norm(before_stem), 1e-12)),
                "difference_snr_db": float(10 * np.log10(
                    (np.sum(before_stem ** 2) + 1e-12) /
                    (np.sum(difference ** 2) + 1e-12))),
            }
        for kind, reference_name, interfering in (
            ("vocals", "dialogue", ("music", "ambience")),
            ("accompaniment", "music", ("dialogue", "ambience")),
        ):
            reference = truth[reference_name]
            if kind == "vocals":
                pre_stem = before["vocals"]
                post_stem = after["vocals"]
            else:
                pre_stem = sum(before[name] for name in ("drums", "bass", "other"))
                post_stem = sum(after[name] for name in ("drums", "bass", "other"))
            pre = pre_stem.mean(axis=0)
            post = post_stem.mean(axis=0)
            undesired = {name: truth[name] for name in interfering}
            metrics[kind] = {
                "baseline_si_sdr_db": si_sdr(reference, pre),
                "candidate_si_sdr_db": si_sdr(reference, post),
                "candidate_minus_baseline_si_sdr_db": si_sdr(reference, post) - si_sdr(reference, pre),
                "baseline_bleed_db": bleed_db(pre, reference, undesired),
                "candidate_bleed_db": bleed_db(post, reference, undesired),
                "candidate_minus_baseline_bleed_db": bleed_db(post, reference, undesired) - bleed_db(pre, reference, undesired),
            }
        baseline_metrics = {
            "dialogue_si_sdr_db": (metrics["vocals"]["baseline_si_sdr_db"], "dB", "quality"),
            "dialogue_bleed_db": (metrics["vocals"]["baseline_bleed_db"], "dB", "quality"),
            "music_si_sdr_db": (metrics["accompaniment"]["baseline_si_sdr_db"], "dB", "quality"),
            "music_bleed_db": (metrics["accompaniment"]["baseline_bleed_db"], "dB", "quality"),
        }
        candidate_metrics = {
            "dialogue_si_sdr_db": (metrics["vocals"]["candidate_si_sdr_db"], "dB", "quality"),
            "dialogue_bleed_db": (metrics["vocals"]["candidate_bleed_db"], "dB", "quality"),
            "music_si_sdr_db": (metrics["accompaniment"]["candidate_si_sdr_db"], "dB", "quality"),
            "music_bleed_db": (metrics["accompaniment"]["candidate_bleed_db"], "dB", "quality"),
            "waveform_vocals_rel_l2": (waveform_parity["vocals"]["difference_relative_l2"], "ratio", "quality"),
            "waveform_vocals_snr_db": (waveform_parity["vocals"]["difference_snr_db"], "dB", "quality"),
            "waveform_drums_rel_l2": (waveform_parity["drums"]["difference_relative_l2"], "ratio", "quality"),
            "waveform_bass_rel_l2": (waveform_parity["bass"]["difference_relative_l2"], "ratio", "quality"),
            "waveform_other_rel_l2": (waveform_parity["other"]["difference_relative_l2"], "ratio", "quality"),
        }
        for values, result in ((baseline_metrics, baseline),
                               (candidate_metrics, candidate)):
            runtime = result["metrics"]
            values.update({
                "model_load_seconds": (runtime["model_load_seconds"], "s", "latency"),
                "cold_inference_seconds": (runtime["cold_inference_seconds"], "s", "latency"),
                "warm_inference_seconds": (runtime["warm_inference_seconds_median"], "s", "latency"),
                "cold_input_decode_seconds": (runtime["cold_input_decode_seconds"], "s", "latency"),
                "output_serialize_seconds": (runtime["output_serialize_seconds"], "s", "latency"),
                "complete_separation_seconds": (runtime["complete_separation_seconds"], "s", "latency"),
                "peak_process_rss_bytes": (runtime["peak_rss_bytes"], "bytes", "memory"),
                "separation_cost_6s_input": (runtime["complete_separation_seconds"], "seconds/6-second-input", "cost"),
            })
        if "mlx_memory" in candidate["metrics"]:
            candidate_metrics["peak_mlx_memory_bytes"] = (
                candidate["metrics"]["mlx_memory"]["peak_bytes"], "bytes", "memory")

        def sha256(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        evidence = [
            {"id": "benchmark-worker",
             "path": "../../scripts/benchmark_source_separation.py",
             "sha256": sha256(ROOT / "scripts/benchmark_source_separation.py")},
            {"id": "fixture-and-metrics",
             "path": "../../library/tools/analysis/source_separation.py",
             "sha256": sha256(ROOT / "library/tools/analysis/source_separation.py")},
            {"id": "prior-c6-baseline",
             "path": "2026-10-13-source-separation-c6.json",
             "sha256": sha256(ROOT / "docs/benchmarks/2026-10-13-source-separation-c6.json")},
        ]

        def as_report(raw, version, metric_values, decision, reason):
            report_metrics = {
                name: {"value": float(value), "unit": unit,
                       "dimension": dimension}
                for name, (value, unit, dimension) in metric_values.items()
            }

            return {
                "format": "ren_evaluation_report/1",
                "revision": (f"{raw['identity']['implementation']} "
                             f"{raw['identity'].get('version', '')} / "
                             "measured 2026-10-15"),
                "version": version,
                "modality": "audio.separate",
                "split": "calibration",
                "metrics": report_metrics,
                "evidence": evidence,
                "selection_basis": ["calibration"],
                "fixture": "C6 synthetic three-stem proxy",
                "adoption_decision": decision,
                "decision_reason": reason,
                "runtime_measurements": {
                    key: value for key, value in raw["metrics"].items()
                    if key not in {"output_path", "output_files"}},
            }

        baseline_eval = as_report(
            baseline,
            {"id": "demucs-infer/htdemucs", "role": "baseline",
             "models": {"source": "htdemucs@955717e8",
                        "runtime": "demucs-infer==4.2.2"}},
            baseline_metrics, "retain baseline",
            "Existing runtime is retained; its six-second proxy is a calibration baseline.")
        candidate_implementation = candidate["identity"]["implementation"]
    candidate_runtime = candidate["identity"].get("runtime")
    if candidate_runtime is None:
        candidate_runtime = ("demucs-mlx==1.4.8"
                             if candidate_implementation == "demucs-mlx"
                             else "mlx-audio-separator==0.1.20")
        candidate_decision = ("conditional fallback"
                              if candidate_implementation == "demucs-mlx"
                              else "research-only")
        candidate_reason = (
            "Faster on this synthetic proxy with close output parity; condition adoption on real-footage labels and memory qualification."
            if candidate_decision == "conditional fallback" else
            "This package uses the demucs-mlx implementation internally; retain as research-only unless its package-level workflow adds measurable value over the direct runtime."
        )
        candidate_eval = as_report(
            candidate,
            {"id": f"{candidate_implementation}/htdemucs", "role": "candidate",
             "models": {"source": "htdemucs@955717e8",
                        "runtime": candidate_runtime}},
            candidate_metrics, candidate_decision, candidate_reason)
        for raw in (baseline, candidate):
            raw["metrics"] = {
                key: value for key, value in raw["metrics"].items()
                if key not in {"output_path", "output_files"}}
        report = {
            "schema": "ren_separation_comparison/1",
            "fixture": "C6 synthetic three-stem proxy",
            "split": "calibration",
            "weights": "htdemucs signature 955717e8, same official source weights",
            "processing": {"sample_rate_hz": 44100, "shifts": 0,
                           "overlap": 0.25, "stems": ["vocals", "drums", "bass", "other"],
                           "weights_precision": "full precision on both; MLX attention fp32"},
            "baseline": baseline,
            "candidate": candidate,
            "quality": metrics,
            "waveform_parity": waveform_parity,
            "scorecards": {"baseline": baseline_eval,
                           "candidate": candidate_eval},
            "adoption_decision": candidate_decision,
            "decision_reason": candidate_reason,
            "limitations": [
                "Synthetic proxy is not speech; its harmonic dialogue signal is a constructed calibration target.",
                "The source separation C6 result is not an editorial-quality judgment.",
                "No held-out footage or agent labels are represented in this report.",
            ],
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_report_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    baseline_report_path.write_text(
        json.dumps(baseline_eval, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    candidate_report_path.write_text(
        json.dumps(candidate_eval, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("directory", type=Path)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--backend", choices=("demucs", "demucs-mlx",
                                                     "mlx-audio-separator"),
                            required=True)
    run_parser.add_argument("--input", type=Path, required=True)
    run_parser.add_argument("--report", type=Path, required=True)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--truth", type=Path, required=True)
    compare_parser.add_argument("--report", type=Path, required=True)
    compare_parser.add_argument("--baseline-eval", type=Path, required=True)
    compare_parser.add_argument("--candidate-eval", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.directory)
    elif args.command == "run":
        run(args.backend, args.input, args.report)
    else:
        compare(args.baseline, args.candidate, args.truth, args.report,
                args.baseline_eval, args.candidate_eval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
