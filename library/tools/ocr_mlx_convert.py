"""Convert VLM OCR runner JSON to ocr_eval input (GEO-016/017).

Handles the MLX runner (`docs/benchmarks/ocr_mlx_candidate.py`) and the
official-pipeline runner (`docs/benchmarks/ocr_paddle_pipeline.py`):
both emit `ren_ocr_candidate_run/1`. Boxed `predictions` pass through
when the runner carries them (pipeline route); text-only runs score
frame/numeric without claiming box accuracy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(mlx: dict, corpus: dict) -> dict:
    labels = {row["id"]: row for row in corpus["frames"]}
    frames = []
    for row in mlx.get("frames", []):
        key = Path(row["id"]).stem
        lab = labels.get(key, {})
        frames.append({
            "id": key,
            "split": lab.get("split", "calibration"),
            "labels": lab.get("labels", []),
            "predictions": [
                {"text": str(p.get("text", "")),
                 "confidence": p.get("confidence", 1.0),
                 **({"bbox": p["bbox"]} if "bbox" in p else {})}
                for p in row.get("predictions", [])],
            "ocr_text": row.get("ocr_text", ""),
            "inference_seconds": row.get("inference_seconds"),
            "peak_memory_gb": row.get("peak_memory_gb"),
        })
    return {
        "candidate": mlx.get("candidate", "PaddleOCR-VL-1.6 MLX"),
        "model_revision": mlx["model_revision"],
        "precision": mlx.get("precision", "4-bit MLX"),
        "license_class": mlx.get("license_class", "Apache-2.0"),
        "cold_load_seconds": mlx.get("cold_load_seconds"),
        "peak_rss_bytes": mlx.get("peak_rss_bytes"),
        "serialization_seconds": mlx.get("serialization_seconds"),
        "frames": frames,
        "provenance": {"runner": "docs/benchmarks/ocr_mlx_candidate.py"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlx", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = convert(
        json.loads(args.mlx.read_text(encoding="utf-8")),
        json.loads(args.corpus.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(report, indent=2) + "\n",
                        encoding="utf-8")
    from docs.benchmarks.ocr_eval import evaluate

    scored = evaluate(report)
    cal = scored["splits"]["calibration"]
    print(json.dumps({"frames": len(report["frames"]),
                      "frame_exact": cal["frame_exact_text_accuracy"],
                      "cer": cal["frame_character_error_rate"],
                      "numeric_doc": cal["numeric_exact_match_in_document"]}))


if __name__ == "__main__":
    main()
