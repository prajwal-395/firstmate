"""Run the frozen local MLX OCR candidate on a list of calibration images."""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

from mlx_vlm import generate, load
from mlx_vlm.prompt_utils import apply_chat_template


def run(model_path: Path, images: list[Path], revision: str,
        done_file: Path | None = None) -> dict:
    started = time.perf_counter()
    model, processor = load(str(model_path), trust_remote_code=True)
    load_seconds = time.perf_counter() - started
    done: dict[str, dict] = {}
    if done_file is not None and done_file.exists():
        for line in done_file.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done[row["id"]] = row
            except (ValueError, KeyError):
                continue
    frames = []
    for path in images:
        if path.name in done:
            frames.append(done[path.name])
            continue
        template = apply_chat_template(
            processor, model.config, prompt="OCR:", num_images=1)
        inference_started = time.perf_counter()
        result = generate(model, processor, template, image=str(path),
                          max_tokens=4096)
        inference_seconds = time.perf_counter() - inference_started
        frames.append({
            "id": Path(path).stem,
            "ocr_text": result.text,
            "inference_seconds": inference_seconds,
            "prompt_tokens": result.prompt_tokens,
            "generation_tokens": result.generation_tokens,
            "peak_memory_gb": result.peak_memory,
        })
        if done_file is not None:
            with done_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(frames[-1], ensure_ascii=False)
                             + "\n")
        print(json.dumps({"progress": path.name,
                          "inference_seconds": inference_seconds}),
              flush=True)
    serialization_started = time.perf_counter()
    report = {
        "schema": "ren_ocr_candidate_run/1",
        "candidate": "PaddleOCR-VL-1.6 MLX",
        "model_revision": revision,
        "precision": "bfloat16 (unquantized)",
        "license_class": "Apache-2.0, publisher model card; license-lane confirmation pending",
        "trust_remote_code": True,
        "cold_load_seconds": load_seconds,
        "warm_inference_seconds": [row["inference_seconds"]
                                    for row in frames[1:]],
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "frames": frames,
    }
    report["serialization_seconds"] = time.perf_counter() - serialization_started
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--done-file", type=Path, default=None,
                        help="JSONL of finished frames; skipped on resume")
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    report = run(args.model, args.images, args.revision,
                 done_file=args.done_file)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(json.dumps({"candidate": report["candidate"],
                      "frames": len(report["frames"]),
                      "cold_load_seconds": report["cold_load_seconds"],
                      "mean_warm_inference_seconds": (
                          sum(report["warm_inference_seconds"])
                          / max(1, len(report["warm_inference_seconds"]))),
                      "peak_rss_bytes": report["peak_rss_bytes"]}))


if __name__ == "__main__":
    main()
