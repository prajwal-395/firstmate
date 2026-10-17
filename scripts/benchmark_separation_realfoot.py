"""Real-footage separation comparison (no ground-truth stems).

Runs reuse scripts/benchmark_source_separation.py `run` (same weights,
same processing settings, same stem definitions). This module only adds
the no-truth comparison: waveform parity between baseline and candidate
stems, runtime/cost/memory rows, and ren_evaluation_report/1 eval JSONs
whose limitations state that quality judgments await blind agent labels.

Input spans are fixed WAV extracts from bounded material (podcast,
Apple 001); provenance (source file, offsets, resample settings,
sha256) is recorded in every report. Never iterate on held-out spans:
run once, report once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

STEMS = ("vocals", "drums", "bass", "other")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _channel_first(samples: np.ndarray, channels: int) -> np.ndarray:
    import soundfile as sf  # noqa: F401  (ensures dependency present)

    values = np.asarray(samples, dtype=np.float32)
    if values.ndim == 1:
        values = values[None, :]
    elif values.ndim == 2 and values.shape[0] != channels and values.shape[1] == channels:
        values = values.T
    if values.ndim != 2 or values.shape[0] != channels:
        raise ValueError(f"expected {channels} channel(s), got shape {values.shape}")
    return np.ascontiguousarray(values)


def read_stems(report: dict) -> dict[str, np.ndarray]:
    import soundfile as sf

    files = report["metrics"]["output_files"]
    out: dict[str, np.ndarray] = {}
    for name, path in files.items():
        samples, _rate = sf.read(path, dtype="float32", always_2d=True)
        out[name] = _channel_first(samples, 2)
    if set(out) != set(STEMS):
        raise ValueError(f"expected four Demucs stems, got {sorted(out)}")
    return out


def waveform_parity(before: dict[str, np.ndarray],
                    after: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    parity: dict[str, dict[str, float]] = {}
    for stem in STEMS:
        pre = before[stem].mean(axis=0).astype(np.float64)
        post = after[stem].mean(axis=0).astype(np.float64)
        difference = post - pre
        parity[stem] = {
            "difference_relative_l2": float(
                np.linalg.norm(difference) / max(np.linalg.norm(pre), 1e-12)),
            "difference_snr_db": float(10 * np.log10(
                (float(np.sum(pre ** 2)) + 1e-12)
                / (float(np.sum(difference ** 2)) + 1e-12))),
        }
    return parity


def accompaniment(stems: dict[str, np.ndarray]) -> np.ndarray:
    return sum(stems[name] for name in ("drums", "bass", "other"))


def runtime_metrics(raw: dict, role: str) -> dict[str, tuple[float, str, str]]:
    runtime = raw["metrics"]
    values: dict[str, tuple[float, str, str]] = {
        "model_load_seconds": (runtime["model_load_seconds"], "s", "latency"),
        "cold_inference_seconds": (runtime["cold_inference_seconds"], "s", "latency"),
        "warm_inference_seconds": (runtime["warm_inference_seconds_median"], "s", "latency"),
        "cold_input_decode_seconds": (runtime["cold_input_decode_seconds"], "s", "latency"),
        "output_serialize_seconds": (runtime["output_serialize_seconds"], "s", "latency"),
        "complete_separation_seconds": (runtime["complete_separation_seconds"], "s", "latency"),
        "peak_process_rss_bytes": (runtime["peak_rss_bytes"], "bytes", "memory"),
        "separation_cost_20s_input": (
            runtime["complete_separation_seconds"], "seconds/20-second-input", "cost"),
    }
    if "mlx_memory" in runtime:
        values["peak_mlx_memory_bytes"] = (
            runtime["mlx_memory"]["peak_bytes"], "bytes", "memory")
    return values


def as_report(raw: dict, version: dict, metric_values: dict,
              decision: str, reason: str, span: dict,
              evidence: list[dict]) -> dict:
    return {
        "format": "ren_evaluation_report/1",
        "revision": (f"{raw['identity']['implementation']} "
                     f"{raw['identity'].get('version', '')} / measured "
                     f"{datetime.now(UTC).date().isoformat()}"),
        "version": version,
        "modality": "audio.separate",
        "split": span["split"],
        "metrics": {
            name: {"value": float(value), "unit": unit, "dimension": dimension}
            for name, (value, unit, dimension) in metric_values.items()
        },
        "evidence": evidence,
        "selection_basis": ["calibration"] if span["split"] == "calibration" else [],
        "fixture": span["span_id"],
        "span": span,
        "adoption_decision": decision,
        "decision_reason": reason,
        "runtime_measurements": {
            key: value for key, value in raw["metrics"].items()
            if key not in {"output_path", "output_files"}},
    }


def compare(baseline_path: Path, candidate_path: Path, span_path: Path,
            output_path: Path, baseline_eval_path: Path,
            candidate_eval_path: Path, label_dir: Path) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    span = json.loads(span_path.read_text(encoding="utf-8"))

    before = read_stems(baseline)
    after = read_stems(candidate)
    parity = waveform_parity(before, after)

    # Listening renders for blind agent labels: vocals + accompaniment sum,
    # 16-bit stereo at the benchmark rate, rendered deterministically.
    import soundfile as sf

    label_dir.mkdir(parents=True, exist_ok=True)
    label_files: dict[str, dict[str, str]] = {}
    for tag, stems in (("baseline", before), ("candidate", after)):
        for kind, audio in (("vocals", stems["vocals"]),
                            ("accompaniment", accompaniment(stems))):
            out_path = label_dir / f"{span['span_id']}-{tag}-{kind}.wav"
            sf.write(out_path, np.asarray(audio).T, span["sample_rate_hz"],
                     subtype="PCM_16")
            label_files.setdefault(tag, {})[kind] = str(out_path)

    candidate_metrics = dict(runtime_metrics(candidate, "candidate"))
    for stem in STEMS:
        candidate_metrics[f"waveform_{stem}_rel_l2"] = (
            parity[stem]["difference_relative_l2"], "ratio", "quality")
    candidate_metrics["waveform_vocals_snr_db"] = (
        parity["vocals"]["difference_snr_db"], "dB", "quality")
    baseline_metrics = dict(runtime_metrics(baseline, "baseline"))

    evidence = [
        {"id": "benchmark-worker",
         "path": "scripts/benchmark_source_separation.py",
         "sha256": "see C6 comparison report for pinned worker hash"},
        {"id": "span-provenance", "path": span_path.name,
         "sha256": sha256(span_path)},
    ]
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
        "Real-footage runtime parity only; adoption awaits blind agent "
        "labels on these spans plus held-out reporting."
        if candidate_decision == "conditional fallback" else
        "This package uses the demucs-mlx implementation internally; retain "
        "as research-only unless its package-level workflow adds measurable "
        "value over the direct runtime. Quality awaits blind agent labels."
    )
    baseline_eval = as_report(
        baseline,
        {"id": "demucs-infer/htdemucs", "role": "baseline",
         "models": {"source": "htdemucs@955717e8", "runtime": "demucs-infer==4.2.2"}},
        baseline_metrics, "retain baseline",
        "Existing runtime is retained; real-footage quality awaits blind agent labels.",
        span, evidence)
    candidate_eval = as_report(
        candidate,
        {"id": f"{candidate_implementation}/htdemucs", "role": "candidate",
         "models": {"source": "htdemucs@955717e8", "runtime": candidate_runtime}},
        candidate_metrics, candidate_decision, candidate_reason, span, evidence)

    report = {
        "schema": "ren_separation_realfoot_comparison/1",
        "span": span,
        "split": span["split"],
        "weights": "htdemucs signature 955717e8, same official source weights",
        "processing": {"sample_rate_hz": 44100, "shifts": 0,
                       "overlap": 0.25, "stems": list(STEMS),
                       "weights_precision": "full precision on both; MLX attention fp32"},
        "baseline": baseline,
        "candidate": candidate,
        "waveform_parity": parity,
        "label_files": label_files,
        "scorecards": {"baseline": baseline_eval, "candidate": candidate_eval},
        "adoption_decision": candidate_decision,
        "decision_reason": candidate_reason,
        "limitations": [
            "Real footage has no isolated ground-truth stems, so no SI-SDR is reported here.",
            "Waveform parity measures runtime closeness, not separation quality.",
            "No blind agent labels are represented in this report yet.",
            "These are technical comparisons, not editorial-quality judgments.",
        ],
    }
    for raw in (baseline, candidate):
        raw["metrics"] = {
            key: value for key, value in raw["metrics"].items()
            if key not in {"output_path", "output_files"}}
    for path, payload in ((output_path, report),
                          (baseline_eval_path, baseline_eval),
                          (candidate_eval_path, candidate_eval)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"comparison": str(output_path),
                      "waveform_parity": parity,
                      "label_files": label_files}, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--span", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline-eval", type=Path, required=True)
    parser.add_argument("--candidate-eval", type=Path, required=True)
    parser.add_argument("--label-dir", type=Path, required=True)
    args = parser.parse_args()
    compare(args.baseline, args.candidate, args.span, args.report,
            args.baseline_eval, args.candidate_eval, args.label_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
