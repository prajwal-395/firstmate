"""Run one sound-event candidate over the synthetic calibration fixtures.

AUD-052 (PANNs baseline reproduction) and the AUD-053/059/060
candidate comparisons share this runner: it synthesizes the fixtures
(`library/tools/analysis/sound_event_fixtures.py`), measures the named
candidate's proposals plus its resource split (cold/warm latency,
peak RSS, decode/infer/postprocess), scores with
`library/tools/analysis/sound_event_benchmark.py`, and emits a
`ren_evaluation_report/1` JSON that
`library/tools/evaluation_scorecard.py` aggregates unchanged.

Usage (foreground, under the narrowest heavy-work profile that fits -
`semantics.analyse:model_memory` for PANNs-class CPU inference)::

    bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-sound \\
        --profile semantics.analyse:model_memory -- \\
        docs/benchmarks/sound_event_bench.py --candidate panns \\
            --fixtures-dir /tmp/fm-vep-mu-sound/sound-eval \\
            --out-dir docs/benchmarks

The runner never touches a project, Resolve, or shared model caches:
candidate downloads live under the fixtures dir and are deleted after
the benchmark unless the adoption decision keeps them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.analysis import sound_event_benchmark as seb  # noqa: E402
from library.tools.analysis import sound_event_fixtures as fix  # noqa: E402

VENV_PYTHON = Path.home() / ".local/share/vep/venv-py312/bin/python3"

CANDIDATES = ("panns", "flexsed")

#: The fixed FlexSED query set: every fixture reference label plus
#: every hard-negative label, so the open-vocabulary probe is asked
#: for what is present AND what must be refused.
FLEXSED_QUERIES = ("Door slam", "Music", "Glass break", "Dog bark",
                   "Boing", "Laughter", "Gunshot", "Cough", "Vehicle",
                   "Sneeze")
#: Operating point, stated not tuned: the paper's median filter (7)
#: and a 0.5 posterior threshold; runs under 0.2 s drop and gaps
#: under 0.25 s merge (the PANNs pipeline's own merge/drop shape).
FLEXSED_THRESHOLD = 0.5
FLEXSED_MEDIAN_WINDOW = 7
FLEXSED_MIN_DURATION = 0.2
FLEXSED_MERGE_GAP = 0.25
FLEXSED_FRAME_RATE = 25.0


def _peak_rss_gb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e9  # bytes on darwin


def _cold_start_panns(clip_wav: str, checkpoint: str) -> float:
    """Fresh-interpreter load plus one inference, in seconds."""
    program = (
        "import time; "
        "t0 = time.perf_counter(); "
        "from library.tools.analysis import sound_event_pipeline as sep; "
        f"sep.measure_sound_events({clip_wav!r}, checkpoint_path={checkpoint!r}); "
        "print(time.perf_counter() - t0)"
    )
    proc = subprocess.run(
        [str(VENV_PYTHON), "-c", program],
        capture_output=True, check=True, encoding="utf-8", timeout=1200,
        cwd=str(REPO_ROOT),
    )
    return float(proc.stdout.strip().splitlines()[-1])


def propose_panns(fixtures_dir: Path) -> tuple[dict, dict]:
    """PANNs proposals per fixture plus the measured resource split."""
    from library.tools.analysis import sound_event_pipeline as sep

    checkpoint = sep.default_checkpoint()
    clips = sorted(fixtures_dir.glob("*.wav"))
    first = str(clips[0])
    cold_seconds = _cold_start_panns(first, checkpoint)

    timings_total = {"decode_seconds": 0.0, "infer_seconds": 0.0,
                     "postprocess_seconds": 0.0}
    proposals: dict[str, list[dict]] = {}
    wall_start = time.perf_counter()
    for clip in clips:
        timings: dict = {}
        measured = sep.measure_sound_events(str(clip), _timings=timings)
        for key in timings_total:
            timings_total[key] += timings[key]
        rows = []
        for index, event in enumerate(measured["events"]):
            rows.append({
                "proposal_id": f"{clip.stem}-p{index}",
                "label": event["label"],
                "start": event["start"],
                "end": event["end"],
                "confidence": event["confidence"],
                "proposal_method": "panns-cnn14-decisionlevelmax@thr0.25",
                "model_id": "panns.cnn14_decision_max",
            })
        proposals[clip.stem] = rows
    warm_seconds = time.perf_counter() - wall_start
    n_clips = len(clips)
    audio_seconds = sum(
        float(s["duration_seconds"]) for s in
        json.loads((fixtures_dir / "references.json").read_text(
            encoding="utf-8"))["sources"])
    resources = {
        "cold_start_seconds": cold_seconds,
        "warm_end_to_end_seconds": warm_seconds,
        "warm_seconds_per_clip": warm_seconds / n_clips,
        "throughput_audio_per_wall": audio_seconds / warm_seconds,
        "decode_seconds_per_clip": timings_total["decode_seconds"] / n_clips,
        "infer_seconds_per_clip": timings_total["infer_seconds"] / n_clips,
        "postprocess_seconds_per_clip":
            timings_total["postprocess_seconds"] / n_clips,
        "peak_rss_gb": _peak_rss_gb(),
        "checkpoint": checkpoint,
        "precision": "fp32",
        "device": "cpu",
        "license": "CC-BY-4.0",
    }
    return proposals, resources


def _flexsed_runs(column, frame_rate: float) -> list[tuple[float, float, float]]:
    """Threshold runs as (start, end, peak) seconds, paper's filter first."""
    import numpy as np

    window = FLEXSED_MEDIAN_WINDOW
    padded = np.pad(column, (window // 2, window // 2), mode="edge")
    smooth = np.median(
        [padded[offset:offset + len(column)] for offset in range(window)],
        axis=0,
    )
    mask = smooth >= FLEXSED_THRESHOLD
    runs, start = [], None
    for frame, on in enumerate(mask):
        if on and start is None:
            start = frame
        elif not on and start is not None:
            runs.append((start, frame))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    merged: list[list[int]] = []
    for run_start, run_end in runs:
        if (merged and (run_start - merged[-1][1]) / frame_rate
                <= FLEXSED_MERGE_GAP):
            merged[-1][1] = run_end
        else:
            merged.append([run_start, run_end])
    out = []
    for run_start, run_end in merged:
        if (run_end - run_start) / frame_rate < FLEXSED_MIN_DURATION:
            continue
        peak = float(smooth[run_start:run_end].max())
        out.append((run_start / frame_rate, run_end / frame_rate, peak))
    return out


def _load_flexsed(code_dir: Path, checkpoint: Path):
    import sys as _sys

    _sys.path.insert(0, str(code_dir))
    from api import FlexSED

    return FlexSED(device="cpu", ckpt_path=str(checkpoint),
                   config_path=str(code_dir / "src" / "configs" / "model.yml"))


def propose_flexsed(fixtures_dir: Path, code_dir: Path,
                    checkpoint: Path) -> tuple[dict, dict]:
    """FlexSED open-vocabulary proposals over the fixed query set.

    The checkpoint + code live under the lane's scratch dir (deleted
    after the benchmark unless adopted). The CLAP text encoder reads
    the machine's shared HF cache; nothing is written to it.
    """
    import numpy as np
    import soundfile as sf
    import torch

    wall_start = time.perf_counter()
    model = _load_flexsed(code_dir, checkpoint)
    load_seconds = time.perf_counter() - wall_start
    clips = sorted(fixtures_dir.glob("*.wav"))
    proposals: dict[str, list[dict]] = {}
    infer_seconds = 0.0
    for clip in clips:
        tick = time.perf_counter()
        with torch.no_grad():
            posteriors = model.run_inference(
                str(clip), list(FLEXSED_QUERIES)).squeeze(1).detach().numpy()
        infer_seconds += time.perf_counter() - tick
        n_frames = posteriors.shape[1]
        duration = len(sf.read(str(clip))[0]) / 16000.0
        frame_rate = n_frames / duration
        rows = []
        for index, query in enumerate(FLEXSED_QUERIES):
            for span, end, peak in _flexsed_runs(
                    np.asarray(posteriors[index], dtype=np.float64),
                    frame_rate):
                rows.append({
                    "proposal_id": f"{clip.stem}-fx{len(rows)}",
                    "label": query,
                    "start": round(span, 3),
                    "end": round(end, 3),
                    "confidence": round(peak, 3),
                    "proposal_method": (
                        "flexsed-ovsed@thr0.5_med7_mindur0.2_merge0.25"),
                    "model_id": "flexsed.ovsed",
                })
        rows.sort(key=lambda r: (r["start"], r["end"]))
        proposals[clip.stem] = rows
    warm_seconds = time.perf_counter() - wall_start
    n_clips = len(clips)
    audio_seconds = sum(
        float(s["duration_seconds"]) for s in
        json.loads((fixtures_dir / "references.json").read_text(
            encoding="utf-8"))["sources"])
    resources = {
        "cold_start_seconds": load_seconds,
        "warm_end_to_end_seconds": warm_seconds,
        "warm_seconds_per_clip": warm_seconds / n_clips,
        "throughput_audio_per_wall": audio_seconds / warm_seconds,
        "decode_seconds_per_clip": None,
        "infer_seconds_per_clip": infer_seconds / n_clips,
        "postprocess_seconds_per_clip": None,
        "peak_rss_gb": _peak_rss_gb(),
        "checkpoint": str(checkpoint),
        "precision": "fp32",
        "device": "cpu",
        "license": "UNVERIFIED (quarantine)",
        "model_id": "flexsed.ovsed",
        "version_id": "flexsed-ovsed-fp32-cpu",
        "role": "candidate",
    }
    return proposals, resources


def _metric(value, unit, dimension):
    return {"value": value, "unit": unit, "dimension": dimension}


def assemble_report(candidate: str, scored: dict, resources: dict,
                    input_path: Path, revision: str) -> dict:
    """A strict `ren_evaluation_report/1` from scored output + resources."""
    overall = scored["overall_at_0_5"]
    boundary = scored["overall_boundary_errors"]
    metrics: dict[str, dict] = {}

    def put(name, value, unit, dimension):
        if value is not None:
            metrics[name] = _metric(value, unit, dimension)

    put("event_f1_at_iou_0_5", overall["f1"], "F1 at temporal IoU >= 0.5",
        "quality")
    put("event_recall_at_iou_0_5", overall["recall"],
        "recall at temporal IoU >= 0.5", "quality")
    put("event_precision_at_iou_0_5", overall["precision"],
        "precision at temporal IoU >= 0.5", "quality")
    put("onset_mean_abs_error_s", boundary["onset_absolute"]["mean"],
        "seconds", "quality")
    put("onset_p95_abs_error_s", boundary["onset_absolute"]["p95"],
        "seconds", "quality")
    put("offset_mean_abs_error_s", boundary["offset_absolute"]["mean"],
        "seconds", "quality")
    put("offset_p95_abs_error_s", boundary["offset_absolute"]["p95"],
        "seconds", "quality")
    rare_recalls = [s["rare_recall_at_0_5"] for s in scored["sources"]
                    if s["rare_recall_at_0_5"] is not None]
    put("rare_recall_at_0_5",
        (sum(rare_recalls) / len(rare_recalls)) if rare_recalls else None,
        "mean recall over rare/nonstandard classes", "quality")
    rejections = [s["hard_negatives"]["rejection_rate"]
                  for s in scored["sources"]
                  if s["hard_negatives"]["rejection_rate"] is not None]
    put("hard_negative_rejection_rate",
        (sum(rejections) / len(rejections)) if rejections else None,
        "fraction of hard negatives with no same-label overlap", "quality")
    put("cold_start_seconds", resources["cold_start_seconds"],
        "seconds, fresh interpreter, load plus one inference", "latency")
    put("warm_seconds_per_clip", resources["warm_seconds_per_clip"],
        "seconds per 6-8 s fixture clip", "latency")
    put("throughput_audio_per_wall",
        resources["throughput_audio_per_wall"],
        "fixture audio seconds per wall second", "latency")
    put("decode_seconds_per_clip", resources["decode_seconds_per_clip"],
        "seconds", "latency")
    put("infer_seconds_per_clip", resources["infer_seconds_per_clip"],
        "seconds", "latency")
    put("postprocess_seconds_per_clip",
        resources["postprocess_seconds_per_clip"], "seconds", "latency")
    put("serialize_seconds_per_clip",
        resources["serialize_seconds_per_clip"], "seconds", "latency")
    put("peak_rss_gb", resources["peak_rss_gb"], "GB process peak RSS",
        "memory")
    put("measured_run_cost_wall_seconds",
        resources["cold_start_seconds"] + resources["warm_end_to_end_seconds"]
        + resources["serialize_seconds_per_clip"]
        * resources["n_clips"],
        "seconds cold plus warm", "cost")

    digest = hashlib.sha256()
    with input_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    version_models = {
        "detection": resources.get("model_id", candidate),
        "checkpoint": Path(resources["checkpoint"]).name,
        "precision": resources["precision"],
        "device": resources["device"],
        "license": resources["license"],
    }
    return {
        "format": "ren_evaluation_report/1",
        "revision": revision,
        "version": {"id": resources.get("version_id", candidate),
                    "role": resources.get("role", "candidate"),
                    "models": version_models},
        "modality": "sound_event_detection",
        "split": "calibration",
        "metrics": metrics,
        "evidence": [{"id": "sound-event-fixture-proposals",
                      "path": input_path.name,
                      "sha256": digest.hexdigest()}],
        "selection_basis": ["calibration"],
    }


def run(candidate: str, fixtures_dir: Path, out_dir: Path,
        revision: str, tag: str, code_dir: Path | None = None,
        checkpoint: Path | None = None) -> dict[str, Path]:
    """Synthesize, measure, score, and write the input + report pair."""
    fixtures_dir = Path(fixtures_dir).resolve()
    out_dir = Path(out_dir).resolve()
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    references = fix.synthesize(fixtures_dir)
    if candidate == "panns":
        proposals, resources = propose_panns(fixtures_dir)
        resources.update({
            "model_id": "panns.cnn14_decision_max",
            "version_id": "panns-cnn14-fp32-cpu",
            "role": "baseline",
        })
    elif candidate == "flexsed":
        if code_dir is None or checkpoint is None:
            raise ValueError("flexsed needs --code-dir and --checkpoint")
        proposals, resources = propose_flexsed(fixtures_dir, code_dir,
                                               checkpoint)
    else:  # pragma: no cover - CANDIDATES guards the CLI
        raise ValueError(f"unknown candidate {candidate!r}")
    for source in references["sources"]:
        source["proposals"] = proposals[source["source_id"]]
        source["proposal_generation_seconds"] = resources[
            "warm_seconds_per_clip"]
    del references["generator"]
    del references["seed"]
    del references["sample_rate"]
    input_path = out_dir / f"2026-10-16-sound-event-{tag}.input.json"
    serialize_start = time.perf_counter()
    input_path.write_text(json.dumps(references, indent=2, sort_keys=True)
                          + "\n", encoding="utf-8")
    resources["serialize_seconds_per_clip"] = (
        time.perf_counter() - serialize_start
    ) / len(references["sources"])
    resources["n_clips"] = len(references["sources"])
    scored = seb.score_document(references)
    scored_path = out_dir / f"2026-10-16-sound-event-{tag}.scored.json"
    scored_path.write_text(json.dumps(scored, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
    report = assemble_report(candidate, scored, resources, input_path,
                             revision)
    report_path = out_dir / f"2026-10-16-sound-event-{tag}.report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
    summary = {
        "candidate": candidate,
        "overall_f1": scored["overall_at_0_5"]["f1"],
        "overall_recall": scored["overall_at_0_5"]["recall"],
        "onset_p95": scored["overall_boundary_errors"][
            "onset_absolute"]["p95"],
        "cold_s": round(resources["cold_start_seconds"], 1),
        "warm_s_per_clip": round(resources["warm_seconds_per_clip"], 2),
        "peak_rss_gb": round(resources["peak_rss_gb"], 2),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return {"input": input_path, "scored": scored_path, "report": report_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=CANDIDATES, default="panns")
    parser.add_argument("--fixtures-dir", type=Path, required=True,
                        help="scratch dir for synthesized WAVs (outside repo)")
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="where the input/scored/report JSON lands")
    parser.add_argument("--revision", default="unrecorded",
                        help="git sha the measurement ran at")
    parser.add_argument("--tag", default="panns-baseline",
                        help="filename tag for this candidate run")
    parser.add_argument("--code-dir", type=Path, default=None,
                        help="flexsed: checked-out vendor code dir")
    parser.add_argument("--checkpoint", type=Path, default=None,
                        help="flexsed: vendor checkpoint path")
    args = parser.parse_args(argv)
    run(args.candidate, args.fixtures_dir, args.out_dir, args.revision,
        args.tag, code_dir=args.code_dir, checkpoint=args.checkpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
