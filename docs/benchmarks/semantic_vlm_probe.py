"""Run a small, calibration-only semantic VLM comparison on Apple 001.

The probe reads source clips and external blind labels only. It never writes
to a project or media source. Candidate weights and generated scratch are
deleted when the run exits; pass --out outside the repository for its JSON.

Run under Ren's model-memory lock:
  bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-vlm \
    --profile semantics.analyse:model_memory -- \
    bin/vep docs/benchmarks/semantic_vlm_probe.py --out /tmp/vlm.json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import resource
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APPLE_RAW = Path(
    "/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001/raw"
)
LABELS = ROOT / "docs/benchmarks/evaluator-only/2026-10-15-agent-action-consensus.json"
ROUND2_CONSENSUS = ROOT / "docs/benchmarks/2026-10-16-vlm-round2-consensus.json"
# Pre-downloaded candidate weights live OUTSIDE the repo (ephemeral scratch,
# deleted after the benchmark unless a candidate is adopted). Point
# VEP_MU_VLM_WEIGHTS at the dir holding one subdir per model slug; any
# candidate without a local dir is downloaded on demand into run scratch
# and removed afterwards.
WEIGHTS_ROOT = Path(os.environ.get("VEP_MU_VLM_WEIGHTS", "/tmp/vep-mu-vlm-weights"))


def _local(slug: str) -> str | None:
    path = WEIGHTS_ROOT / slug
    return str(path) if path.is_dir() else None
# SEM-008 (Molmo2-4B) is deferred, not absent by oversight: the license lane
# records it unresolved over third-party training-dataset terms, so no
# Molmo2 weights are downloaded or benchmarked by this probe.
MODEL_SPECS = [
    {
        "id": "mlx-community/gemma-4-12b-it-4bit",
        "role": "baseline",
        "precision": "MLX 4-bit",
        "revision": "73bcf09092aa277861d5a191b989b666f7f32e8f",
        "local": str(Path.home() / ".cache/huggingface/hub/models--mlx-community--gemma-4-12b-it-4bit/snapshots/73bcf09092aa277861d5a191b989b666f7f32e8f"),
        "license_class": "conditional Gemma Terms (registry record)",
        "license_record": "library/model_registry/registry.json",
    },
    {
        "id": "mlx-community/Qwen3.5-4B-4bit",
        "role": "candidate",
        "precision": "MLX 4-bit",
        "revision": "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c",
        "local": _local("Qwen3.5-4B-4bit"),
        "license_class": "conditional Apache (conversion artifact needs review)",
        "license_record": "vep-mu-license qwen35_4b.json",
    },
    {
        "id": "smdesai/SmolVLM2-2.2B-Instruct-4bit",
        "role": "candidate",
        "precision": "MLX 4-bit",
        "revision": "0a4ab666f99095e6c6bd0e1e49aca8822fc46df2",
        "local": _local("SmolVLM2-2.2B-Instruct-4bit"),
        "license_class": "conditional Apache",
        "license_record": "vep-mu-license smolvlm2.json",
    },
    {
        "id": "mlx-community/SmolVLM2-500M-Video-Instruct-mlx",
        "role": "candidate",
        "precision": "MLX BF16/full precision",
        "revision": "fa57db46815177fbdfd65cc85a2b3416a8332268",
        "local": _local("SmolVLM2-500M-Video-Instruct-mlx"),
        "license_class": "conditional Apache",
        "license_record": "vep-mu-license smolvlm2.json",
    },
    {
        "id": "mlx-community/SmolVLM2-256M-Video-Instruct-mlx",
        "role": "candidate",
        "precision": "MLX BF16/full precision",
        "revision": "79901655c1a3d7ed6646c91325e3fde47e40e611",
        "local": _local("SmolVLM2-256M-Video-Instruct-mlx"),
        "license_class": "conditional Apache",
        "license_record": "vep-mu-license smolvlm2.json",
    },
]
SAMPLES = [
    "IMG_1806", "IMG_1807", "IMG_1808", "IMG_1809", "IMG_1810",
    "IMG_1811", "IMG_1814", "IMG_1815", "IMG_1816",
]
PROMPT = """You are describing sampled frames from a source video interval.
The images are ordered and correspond to these source times in seconds:
{times}.
Report only directly visible evidence. Do not infer emotions, causes, or
identity. Return exactly one JSON object with this schema and no other text:
{{"scene_class":"vehicle|indoor|outdoor|mixed|unknown","objects":[{{"label":"string","start_seconds":0.0,"end_seconds":1.0}}],"actions":[{{"label":"string","start_seconds":0.0,"end_seconds":1.0}}],"camera_motion":[{{"label":"string","start_seconds":0.0,"end_seconds":1.0}}]}}
Use source-clock seconds, keep all times inside [{start}, {end}), and use
empty arrays when an item is not visible. The intervals may be approximate;
only assert an action if the sampled images support it."""


def _norm(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words}


def _similarity(left: str, right: str) -> float:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    return float(result.stdout.strip())


def _extract_frames(path: Path, duration: float, target: Path) -> tuple[list[str], list[float], float]:
    import cv2

    started = time.perf_counter()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open calibration source: {path}")
    end = min(duration, 10.0)
    offsets = [0.25] if end <= 0.5 else [
        round(float(x), 3) for x in __import__("numpy").linspace(
            0.25, end - 0.25, num=min(5, max(2, int(end))))
    ]
    images = []
    for index, seconds in enumerate(offsets):
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        file = target / f"{path.stem}-{index}.jpg"
        if not cv2.imwrite(str(file), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise RuntimeError(f"cannot write scratch frame: {file}")
        images.append(str(file))
    cap.release()
    if len(images) < 2:
        raise RuntimeError(f"fewer than two frames decoded from {path}")
    return images, offsets[:len(images)], time.perf_counter() - started


def _references(all_labels: list[dict[str, Any]], take: str, kind: str,
                start: float, end: float) -> list[dict[str, Any]]:
    selected = []
    for labels in all_labels:
        for row in labels["labels"]:
            if row.get("take") != take or row.get("kind") != kind:
                continue
            if not row.get("agreement"):
                continue
            if kind == "object":
                label = row["name"]
            else:
                label = row["verb"]
            if row["end"] > start and row["start"] < end:
                selected.append({"label": label, "start": row["start"],
                                 "end": row["end"],
                                 "agreement_iou": row["agreement"].get("iou")})
    return selected


def _score_outputs(predictions: list[dict[str, Any]], references: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    for pi, pred in enumerate(predictions):
        for ri, ref in enumerate(references):
            sim = _similarity(pred["label"], ref["label"])
            ps, pe = pred.get("start_seconds"), pred.get("end_seconds")
            rs, re_ = ref["start"], ref["end"]
            if ps is None or pe is None or pe <= ps:
                iou = 0.0
                start_error = end_error = None
            else:
                overlap = max(0.0, min(pe, re_) - max(ps, rs))
                iou = overlap / max(1e-9, (pe - ps) + (re_ - rs) - overlap)
                start_error, end_error = abs(ps - rs), abs(pe - re_)
            if sim >= 0.4:
                pairs.append((sim * iou if kind_is_temporal(pred) else sim,
                              sim, iou, start_error, end_error, pi, ri))
    pairs.sort(reverse=True)
    used_p, used_r, matched = set(), set(), []
    for _, sim, iou, se, ee, pi, ri in pairs:
        if pi in used_p or ri in used_r:
            continue
        used_p.add(pi); used_r.add(ri)
        matched.append({"similarity": round(sim, 4), "interval_iou": round(iou, 4),
                        "start_error_s": se, "end_error_s": ee,
                        "prediction": predictions[pi], "reference": references[ri]})
    return {
        "precision": len(used_p) / len(predictions) if predictions else (1.0 if not references else 0.0),
        "recall": len(used_r) / len(references) if references else None,
        "hallucinated_claims": len(predictions) - len(used_p),
        "missed_observations": len(references) - len(used_r),
        "matches": matched,
    }


def kind_is_temporal(pred: dict[str, Any]) -> bool:
    return pred.get("kind") == "action"


def _parse(text: str, start: float, end: float) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    fenced = False
    body = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", body, re.DOTALL)
    if fence:
        fenced = True
        body = fence.group(1).strip()
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None, {"json": False, "schema": False, "timestamps": False,
                      "fenced": fenced}
    schema = (
        isinstance(obj, dict)
        and obj.get("scene_class") in {"vehicle", "indoor", "outdoor", "mixed", "unknown"}
        and all(isinstance(obj.get(k), list) for k in ("objects", "actions", "camera_motion"))
        and all(isinstance(row, dict) and isinstance(row.get("label"), str)
                and isinstance(row.get("start_seconds"), (int, float))
                and isinstance(row.get("end_seconds"), (int, float))
                for k in ("objects", "actions", "camera_motion") for row in obj[k])
    )
    timestamps = schema and all(
        start <= row["start_seconds"] < row["end_seconds"] <= end
        for k in ("objects", "actions", "camera_motion") for row in obj[k]
    )
    return obj, {"json": True, "schema": bool(schema),
                 "timestamps": bool(timestamps), "fenced": fenced}


def _infer_sample(spec: dict[str, Any], model: Any, processor: Any,
                  sample: dict[str, Any], generate: Any,
                  apply_chat_template: Any) -> dict[str, Any]:
    prompt = PROMPT.format(
        times=", ".join(f"{t:.3f}" for t in sample["frame_times"]),
        start=sample["start"], end=sample["end"])
    serialize_started = time.perf_counter()
    formatted = apply_chat_template(
        processor, model.config, prompt, num_images=len(sample["images"]))
    serialize_s = time.perf_counter() - serialize_started
    infer_started = time.perf_counter()
    result = generate(model, processor, formatted,
                      image=sample["images"], max_tokens=384,
                      temperature=0.0, verbose=False)
    infer_s = time.perf_counter() - infer_started
    raw = getattr(result, "text", str(result))
    parsed, checks = _parse(raw, sample["start"], sample["end"])
    if not isinstance(parsed, dict):
        parsed = {"objects": [], "actions": [], "camera_motion": [],
                  "scene_class": "unknown"}
    pred_objects = [{**row, "kind": "object"} for row in parsed["objects"]]
    pred_actions = [{**row, "kind": "action"} for row in parsed["actions"]]
    obj_score = _score_outputs(pred_objects, sample["objects"])
    act_score = _score_outputs(pred_actions, sample["actions"])
    return {
        "source_id": sample["take"], "split": "calibration",
        "window": [sample["start"], sample["end"]],
        "prompt_serialization_s": serialize_s, "inference_s": infer_s,
        "input_frames": len(sample["images"]), "parse": checks,
        "scene_class": parsed.get("scene_class"),
        "object_metrics": obj_score, "action_metrics": act_score,
        "raw_output": raw,
        "tokens": {"input": getattr(result, "prompt_tokens", None),
                   "output": getattr(result, "generation_tokens", None)},
    }


def run_model(spec: dict[str, Any], samples: list[dict[str, Any]],
              scratch: Path, delete_after: bool,
              decode_s: dict[str, float]) -> dict[str, Any]:
    import mlx.core as mx
    from huggingface_hub import model_info, snapshot_download
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template

    info = model_info(spec["id"], revision=spec["revision"], files_metadata=True)
    model_bytes = sum(file.size or 0 for file in info.siblings)
    download_path = None
    if spec.get("local") is None:
        download_path = scratch / ("weights-" + re.sub(r"[^a-zA-Z0-9]+", "-", spec["id"]))
        download_path.mkdir(parents=True, exist_ok=True)
        model_path = snapshot_download(
            repo_id=spec["id"], revision=spec["revision"],
            local_dir=str(download_path))
        print(f"DOWNLOADED model={spec['id']} revision={spec['revision']} "
              f"size_bytes={model_bytes} path={model_path}", flush=True)
    else:
        model_path = spec["local"]
        if not Path(model_path).is_dir():
            raise RuntimeError(f"incumbent weights missing: {model_path}")
    mx.reset_peak_memory()
    load_started = time.perf_counter()
    print(f"LOADING model={spec['id']} path={model_path}", flush=True)
    model, processor = load(model_path)
    cold_load_s = time.perf_counter() - load_started
    print(f"LOADED model={spec['id']} cold_load_s={cold_load_s:.1f}", flush=True)
    rows = []
    for sample in samples:
        print(f"SAMPLE model={spec['id']} take={sample['take']}", flush=True)
        try:
            rows.append(_infer_sample(
                spec, model, processor, sample, generate,
                apply_chat_template))
        except Exception as exc:
            print(f"SAMPLE_ERROR model={spec['id']} take={sample['take']} "
                  f"error={type(exc).__name__}: {exc}", flush=True)
            rows.append({
                "source_id": sample["take"], "split": "calibration",
                "window": [sample["start"], sample["end"]],
                "error": f"{type(exc).__name__}: {exc}",
                "parse": {"json": False, "schema": False, "timestamps": False,
                          "fenced": False},
                "scene_class": None,
                "object_metrics": {"precision": None, "recall": None,
                                   "hallucinated_claims": 0,
                                   "missed_observations": 0, "matches": []},
                "action_metrics": {"precision": None, "recall": None,
                                   "hallucinated_claims": 0,
                                   "missed_observations": 0, "matches": []},
                "raw_output": None, "tokens": {"input": None, "output": None},
            })
    active_memory = int(mx.get_peak_memory())
    max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    del model, processor
    gc.collect()
    mx.clear_cache()
    out = {
        **{k: v for k, v in spec.items() if k != "local"},
        "weights_path": spec.get("local"),
        "artifact_bytes": model_bytes, "download_path": str(download_path) if download_path else None,
        "published_license": info.card_data.license if info.card_data else None,
        "downloaded_bytes": model_bytes if download_path else 0,
        "cold_load_s": cold_load_s,
        "warm_latency_s": [row["inference_s"] for row in rows
                           if "inference_s" in row],
        "peak_mlx_memory_bytes": active_memory, "peak_rss_bytes": max_rss,
        "samples": rows,
        "decode_seconds_by_source": decode_s,
    }
    if delete_after and download_path:
        shutil.rmtree(download_path)
        out["download_removed"] = True
    else:
        out["download_removed"] = False
    return out


def build_samples(all_labels: list[dict[str, Any]], takes: list[str], scratch: Path) -> tuple[list[dict[str, Any]], dict[str, float]]:
    samples = []
    decode_s = {}
    for take in takes:
        path = APPLE_RAW / f"{take}.MOV"
        duration = _duration(path)
        start, end = 0.0, min(10.0, duration)
        frames, times, elapsed = _extract_frames(path, duration, scratch / "frames")
        decode_s[take] = elapsed
        samples.append({
            "take": take, "start": start, "end": end,
            "images": frames, "frame_times": times,
            "objects": _references(all_labels, take, "object", start, end),
            "actions": _references(all_labels, take, "action", start, end),
        })
    return samples, decode_s


def aggregate(result: dict[str, Any]) -> None:
    samples = result["samples"]
    result["error_samples"] = sum(1 for row in samples if "error" in row)
    for field in ("json", "schema", "timestamps", "fenced"):
        result[field + "_valid_count"] = sum(1 for row in samples if row["parse"].get(field))
        total = len(samples)
        result[field + "_valid_rate"] = result[field + "_valid_count"] / total
    for kind in ("object", "action"):
        key = kind + "_metrics"
        predictions = sum(s[key]["hallucinated_claims"] + len(s[key]["matches"]) for s in samples)
        references = sum(s[key]["missed_observations"] + len(s[key]["matches"]) for s in samples)
        hits = sum(len(s[key]["matches"]) for s in samples)
        result[key] = {
            "precision": (hits / predictions if predictions
                          else (1.0 if not references else 0.0)),
            "recall": hits / references if references else None,
            "hallucinated_claims": predictions - hits,
            "missed_observations": references - hits,
        }
    latencies = result["warm_latency_s"]
    result["warm_latency_mean_s"] = sum(latencies) / len(latencies) if latencies else None
    result["decode_mean_s_per_sample"] = (
        sum(result["decode_seconds_by_source"].values()) / len(samples))
    ser = [row["prompt_serialization_s"] for row in samples
           if "prompt_serialization_s" in row]
    result["serialize_mean_s_per_sample"] = sum(ser) / len(ser) if ser else None
    result["end_to_end_cost_s_per_sample"] = (
        result["decode_mean_s_per_sample"] + result["cold_load_s"] / len(samples)
        + (result["warm_latency_mean_s"] or 0.0)
        + (result["serialize_mean_s_per_sample"] or 0.0))
    result["measurement_scope"] = (
        f"calibration only; {len(samples)} Apple 001 takes "
        f"({','.join(row['source_id'] for row in samples)}); "
        "0-10s or clip duration")


def _write_output(args: argparse.Namespace, results: list[dict[str, Any]],
                  decode_s: dict[str, float], takes: list[str],
                  complete: bool) -> None:
    output = {
        "schema": "semantic-vlm-probe-v1", "split": "calibration",
        "takes": takes, "complete": complete,
        "label_type": "blind agent consensus; not human/gold labels",
        "label_sources": [str(LABELS), str(ROUND2_CONSENSUS)],
        "label_revision": "evaluator-only consensus plus round-2 agreed pairs plus tiebreak-confirmed claims; unresolved disputes and tiebreak-only rows are not scored",
        "decode_seconds_by_source": decode_s,
        "environment": {
            "python": os.sys.version,
            "mlx_version": __import__("importlib.metadata", fromlist=["version"]).version("mlx"),
            "mlx_vlm_version": __import__("importlib.metadata", fromlist=["version"]).version("mlx-vlm"),
        },
        "candidates": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"WROTE {args.out} complete={complete}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--only", action="append", help="exact model id; repeatable")
    parser.add_argument("--spec-json", action="append", default=[],
                        help="adhoc candidate spec as a JSON object (e.g. a "
                        "precision variant for a quality-gap rerun); "
                        "repeatable; recorded verbatim in output")
    parser.add_argument("--takes", action="append",
                        help="limit samples to these take stems (e.g. IMG_1808); "
                        "repeatable; recorded in output so a filtered run is "
                        "never mistaken for a full run")
    parser.add_argument("--keep-downloads", action="store_true")
    args = parser.parse_args()
    takes = args.takes or SAMPLES
    unknown = [t for t in takes if t not in SAMPLES]
    if unknown:
        raise SystemExit(f"unknown takes: {unknown}; known: {SAMPLES}")
    with tempfile.TemporaryDirectory(prefix="vep-mu-vlm-") as scratch_raw:
        scratch = Path(scratch_raw)
        (scratch / "frames").mkdir()
        all_labels = [
            json.loads(LABELS.read_text(encoding="utf-8")),
            json.loads(ROUND2_CONSENSUS.read_text(encoding="utf-8")),
        ]
        samples, decode_s = build_samples(all_labels, takes, scratch)
        specs = [s for s in MODEL_SPECS if not args.only or s["id"] in args.only]
        for raw_spec in args.spec_json:
            extra = json.loads(raw_spec)
            if not args.only or extra["id"] in args.only:
                specs.append(extra)
        results = []
        try:
            for spec in specs:
                result = run_model(spec, samples, scratch,
                                   delete_after=not args.keep_downloads,
                                   decode_s=decode_s)
                aggregate(result)
                results.append(result)
                warm = result["warm_latency_mean_s"]
                print(f"FINISHED model={spec['id']} cold={result['cold_load_s']:.2f}s "
                      f"warm_mean={'n/a' if warm is None else f'{warm:.2f}s'} "
                      f"errors={result['error_samples']} "
                      f"schema={result['schema_valid_rate']:.3f} "
                      f"obj_pr={result['object_metrics']['precision']:.3f} "
                      f"obj_rc={result['object_metrics']['recall']}", flush=True)
                _write_output(args, results, decode_s, takes, complete=False)
        finally:
            for child in scratch.glob("weights-*"):
                if not args.keep_downloads:
                    shutil.rmtree(child, ignore_errors=True)
        _write_output(args, results, decode_s, takes, complete=True)


if __name__ == "__main__":
    main()
