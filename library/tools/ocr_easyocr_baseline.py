"""Run EasyOCR (the incumbent) over benchmark frames (GEO-015 baseline).

Reads a corpus JSON (`frames` with `id`, `split`, `image`) plus label
arrays, writes an `ocr_eval.evaluate` input document with predictions,
timing (cold/warm, decode/infer/serialize split) and memory. Apple
Vision runs separately via `docs/benchmarks/apple_vision_ocr.swift`;
PaddleOCR-VL-1.6 MLX via `docs/benchmarks/ocr_mlx_candidate.py`.

Usage:
    bin/vep -m library.tools.ocr_easyocr_baseline --corpus syn.json \\
        --images dir --out easyocr_run.json --revision <reader-rev>
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path


def run(corpus: dict, images: dict[str, Path], revision: str) -> dict:
    import easyocr

    started = time.perf_counter()
    reader = easyocr.Reader(["en"], download_enabled=False)
    load_seconds = time.perf_counter() - started
    frames = []
    infer_values: list[float] = []
    for row in corpus["frames"]:
        path = images.get(row["id"], Path(row.get("image", "")))
        decode_started = time.perf_counter()
        import cv2

        frame = cv2.imread(str(path))
        decode_seconds = time.perf_counter() - decode_started
        infer_started = time.perf_counter()
        raw = reader.readtext(frame) if frame is not None else []
        infer_seconds = time.perf_counter() - infer_started
        infer_values.append(infer_seconds)
        serialize_started = time.perf_counter()
        h, w = (frame.shape[:2] if frame is not None else (1, 1))
        predictions = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            quad, text, conf = item[0], item[1], item[2]
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            predictions.append({
                "text": str(text), "confidence": float(conf),
                "bbox": [x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h],
            })
        serialize_seconds = time.perf_counter() - serialize_started
        frames.append({
            "id": row["id"], "split": row.get("split", "calibration"),
            "labels": row.get("labels", []),
            "predictions": predictions,
            "inference_seconds": infer_seconds,
            "decode_seconds": decode_seconds,
            "serialization_seconds": serialize_seconds,
        })
    warm = (sum(infer_values[1:]) / max(1, len(infer_values) - 1)
            if infer_values else None)
    return {
        "candidate": "EasyOCR (incumbent)",
        "model_revision": revision,
        "precision": "fp32 (CPU/GPU per installed torch build)",
        "license_class": "Apache-2.0 (reader + detection/recognition weights)",
        "timing": {
            "cold_seconds": load_seconds,
            "warm_seconds": warm,
            "decode_seconds": sum(f["decode_seconds"] for f in frames) / max(1, len(frames)),
            "infer_seconds": sum(infer_values) / max(1, len(infer_values)),
            "serialize_seconds": sum(f["serialization_seconds"] for f in frames) / max(1, len(frames)),
        },
        "memory_peak_bytes": resource.getrusage(
            resource.RUSAGE_SELF).ru_maxrss,
        "frames": frames,
        "provenance": {"runner": "library/tools/ocr_easyocr_baseline.py"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--images", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--revision", default="easyocr-reader-en-v1")
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    images = {}
    if args.images:
        for row in corpus["frames"]:
            candidate = args.images / f"{row['id']}.png"
            if not candidate.exists():
                candidate = args.images / f"{row['id']}.jpg"
            images[row["id"]] = candidate if candidate.exists() else Path(
                row.get("image", ""))
    report = run(corpus, images, args.revision)
    args.out.write_text(json.dumps(report, indent=2) + "\n",
                        encoding="utf-8")
    from docs.benchmarks.ocr_eval import evaluate

    scored = evaluate(report)
    cal = scored["splits"]["calibration"]
    print(json.dumps({
        "frames": len(report["frames"]),
        "cold_s": round(report["timing"]["cold_seconds"], 2),
        "warm_s": round(report["timing"]["warm_seconds"] or 0, 3),
        "frame_exact": cal["frame_exact_text_accuracy"],
        "cer": cal["frame_character_error_rate"],
    }))


if __name__ == "__main__":
    main()
