"""Convert Apple Vision runner JSON to ocr_eval input (GEO-015)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(apple: dict, corpus: dict) -> dict:
    labels = {row["id"]: row for row in corpus["frames"]}
    frames = []
    for row in apple.get("frames", []):
        key = Path(row["id"]).stem
        lab = labels.get(key, {})
        frames.append({
            "id": key,
            "split": lab.get("split", "calibration"),
            "labels": lab.get("labels", []),
            "predictions": [
                {"text": p["text"], "confidence": p.get("confidence", 1.0),
                 "bbox": p["bbox"]}
                for p in row.get("predictions", [])],
            "inference_seconds": row.get("latencySeconds"),
        })
    warm = [f["inference_seconds"] for f in frames[1:]
            if f.get("inference_seconds") is not None]
    return {
        "candidate": "Apple Vision OCR",
        "model_revision": apple.get("revision", "macOS-Vision"),
        "precision": apple.get("precision", "Apple Vision framework default"),
        "license_class": "OS-provided framework (no redistribution concern)",
        "timing": {
            "cold_seconds": apple.get("cold_inference_seconds"),
            "warm_seconds": (sum(warm) / len(warm) if warm else None),
            "decode_seconds": None,
            "infer_seconds": None,
            "serialize_seconds": None,
        },
        "memory_peak_bytes": None,
        "frames": frames,
        "provenance": {"runner": "docs/benchmarks/apple_vision_ocr.swift"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apple", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = convert(
        json.loads(args.apple.read_text(encoding="utf-8")),
        json.loads(args.corpus.read_text(encoding="utf-8")))
    args.out.write_text(json.dumps(report, indent=2) + "\n",
                        encoding="utf-8")
    from docs.benchmarks.ocr_eval import evaluate

    scored = evaluate(report)
    cal = scored["splits"]["calibration"]
    print(json.dumps({"frames": len(report["frames"]),
                      "frame_exact": cal["frame_exact_text_accuracy"],
                      "cer": cal["frame_character_error_rate"],
                      "det_f1": cal["detection_f1_iou50"]}))


if __name__ == "__main__":
    main()
