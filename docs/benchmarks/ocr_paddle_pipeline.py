"""Run PaddleOCR-VL-1.6 via the official pipeline (GEO-016).

Publisher's recommended route: `PaddleOCRVL(pipeline_version="v1.6")`.
Runs in the isolated /tmp/ocr-paddle-env venv (paddlepaddle + paddleocr),
never in the shared ML venv (ED-024). Emits `ren_ocr_candidate_run/1`
with per-frame `ocr_text` plus the pipeline's raw boxes where the
schema carries them, scored by `library/tools/ocr_mlx_convert.py`.

Usage (isolated env):
    /tmp/ocr-paddle-env/bin/python docs/benchmarks/ocr_paddle_pipeline.py \\
        --out paddle_pipe.json img...
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path


def _sidecar_json(path: Path, work_dir: Path):
    """The save_to_json sidecar `{stem}_res.json`, when the runner wrote it."""
    candidate = work_dir / f"{path.stem}_res.json"
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        return None
    return None


def _texts_from_data(data: dict) -> list[str]:
    texts = []
    for row in data.get("parsing_res_list", []) or []:
        content = row.get("block_content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts


def _predictions_from_data(data: dict, width: int, height: int) -> list[dict]:
    predictions = []
    for row in data.get("parsing_res_list", []) or []:
        content = row.get("block_content")
        bbox = row.get("block_bbox")
        if not (isinstance(content, str) and content.strip()):
            continue
        if (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                and width > 0 and height > 0):
            x0, y0, x1, y1 = (float(v) for v in bbox)
            predictions.append({
                "text": content,
                "confidence": 1.0,
                "bbox": [x0 / width, y0 / height,
                         (x1 - x0) / width, (y1 - y0) / height],
                "block_label": row.get("block_label"),
            })
        else:
            predictions.append({"text": content, "confidence": 1.0})
    return predictions


def run(images: list[Path], done_file: Path | None = None) -> dict:
    from paddleocr import PaddleOCRVL

    done: dict[str, dict] = {}
    if done_file is not None and done_file.exists():
        for line in done_file.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done[row["id"]] = row
            except (ValueError, KeyError):
                continue
    started = time.perf_counter()
    pipeline = PaddleOCRVL(pipeline_version="v1.6")
    load_seconds = time.perf_counter() - started
    work_dir = Path("/tmp/ocr-paddle-sidecars")
    work_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for path in images:
        if path.name in done:
            frames.append(done[path.name])
            continue
        inference_started = time.perf_counter()
        # Fast text path: the VLM directly on the image (spotting),
        # without the document-parsing stages (orientation, unwarping,
        # layout, chart, seal). Text-only output scores frame/numeric
        # without claiming box accuracy; the full parsing route is a
        # separate, slower candidate for chart/table stills.
        output = pipeline.predict(
            str(path),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_layout_detection=False,
            use_chart_recognition=False,
            use_seal_recognition=False,
            prompt_label="ocr",
        )
        inference_seconds = time.perf_counter() - inference_started
        for res in output:
            try:
                res.save_to_json(save_path=str(work_dir))
            except Exception:
                pass
        data = _sidecar_json(path, work_dir) or {}
        try:
            from PIL import Image as _Image
            with _Image.open(path) as handle:
                width, height = handle.size
        except Exception:
            width = height = 0
        texts = _texts_from_data(data)
        predictions = _predictions_from_data(data, width, height)
        if not predictions:
            predictions = [{"text": text, "confidence": 1.0}
                           for text in texts]
        frames.append({
            "id": path.name,
            "ocr_text": "\n".join(texts),
            "predictions": predictions,
            "inference_seconds": inference_seconds,
        })
        # Flush per frame: a killed run resumes with --done-file
        # instead of losing every finished image.
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
        "candidate": "PaddleOCR-VL-1.6 (official pipeline)",
        "model_revision": "PaddlePaddle/PaddleOCR-VL-1.6@"
                          "676e2aad67375fec9c7dcc9740babec7a0d0d110",
        "precision": "paddlepaddle 3.3.1 CPU (macOS arm64)",
        "license_class": "Apache-2.0, publisher model card; "
                         "license-lane confirmation pending",
        "trust_remote_code": False,
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
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--done-file", type=Path, default=None,
                        help="JSONL of finished frames; skipped on resume")
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    report = run(args.images, done_file=args.done_file)
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
