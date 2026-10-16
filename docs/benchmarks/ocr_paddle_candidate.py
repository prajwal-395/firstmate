"""Run PaddleOCR-VL-1.6 over benchmark images via transformers (GEO-016).

STATUS: blocked-with-evidence for this checkpoint (2026-10-16), kept as
the probe record. The installed transformers 5.19 registers paddleocr_vl
natively but rejects the checkpoint's vision rope layout; the
checkpoint's bundled remote modeling needs
`ROPE_INIT_FUNCTIONS['default']`, absent through transformers 5.20.0.
The faithful measured route is the official pipeline
(`ocr_paddle_pipeline.py`) and the MLX conversion
(`ocr_mlx_candidate.py`), both scored by
`library/tools/ocr_mlx_convert.py`. Revisit when transformers ships a
compatible rope init or the publisher updates the bundled modeling.

Publisher's intended route was: transformers>=5 with the repo's custom
modeling (`trust_remote_code=True`), "Spotting:" prompt, spotting pixel
budget from the model card. Records cold load, per-frame warm latency,
peak RSS and per-frame text, in the `ren_ocr_candidate_run/1` schema so
`library/tools/ocr_mlx_convert.py` scores it like the MLX candidate.

Usage:
    bin/vep -m docs.benchmarks.ocr_paddle_candidate \\
        --model <snapshot> --revision <sha> --out paddle_run.json img...

Device and dtype are recorded in the report; the runner takes what the
machine gives (MPS when available, else CPU) and SAYS which it took.
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path


def run(model_path: Path, images: list[Path], revision: str) -> dict:
    import sys
    import torch

    # Prefer the checkpoint's own modeling (trust_remote_code): the
    # transformers release in this environment now registers
    # paddleocr_vl natively, but its native vision config does not
    # accept this checkpoint's rope layout, while the publisher's
    # bundled modules are written for it. No environment change.
    # The bundled modules use relative imports, so load them as a
    # synthetic package rather than top-level modules.
    import importlib.util
    import types

    pkg = types.ModuleType("paddleocr_vl_remote")
    pkg.__path__ = [str(model_path)]
    sys.modules["paddleocr_vl_remote"] = pkg

    def _load_remote(name: str):
        spec = importlib.util.spec_from_file_location(
            f"paddleocr_vl_remote.{name}", model_path / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    config_mod = _load_remote("configuration_paddleocr_vl")
    model_mod = _load_remote("modeling_paddleocr_vl")
    proc_mod = _load_remote("processing_paddleocr_vl")
    PaddleOCRVLConfig = config_mod.PaddleOCRVLConfig
    PaddleOCRVLForConditionalGeneration = (
        model_mod.PaddleOCRVLForConditionalGeneration)
    PaddleOCRVLProcessor = proc_mod.PaddleOCRVLProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    started = time.perf_counter()
    processor = PaddleOCRVLProcessor.from_pretrained(str(model_path))
    config = PaddleOCRVLConfig.from_pretrained(str(model_path))
    model = PaddleOCRVLForConditionalGeneration.from_pretrained(
        str(model_path), config=config,
        torch_dtype=torch.float32 if device == "cpu" else torch.float16,
    ).to(device).eval()
    load_seconds = time.perf_counter() - started
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("PIL required: pip install pillow") from exc
    frames = []
    for path in images:
        image = Image.open(path).convert("RGB")
        inference_started = time.perf_counter()
        messages = [{"role": "user",
                     "content": [{"type": "image", "image": image},
                                 {"type": "text",
                                  "text": "Spotting:"}]}]
        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt")
        inputs = {k: v.to(device) if hasattr(v, "to") else v
                  for k, v in inputs.items()}
        with torch.no_grad():
            generated = model.generate(**inputs, max_new_tokens=1024)
        text = processor.decode(generated[0], skip_special_tokens=True)
        inference_seconds = time.perf_counter() - inference_started
        frames.append({
            "id": path.name,
            "ocr_text": text,
            "inference_seconds": inference_seconds,
        })
    serialization_started = time.perf_counter()
    report = {
        "schema": "ren_ocr_candidate_run/1",
        "candidate": "PaddleOCR-VL-1.6 (transformers)",
        "model_revision": revision,
        "precision": ("float16 on MPS" if device == "mps"
                      else "float32 on CPU"),
        "license_class": "Apache-2.0, publisher model card; "
                         "license-lane confirmation pending",
        "trust_remote_code": True,
        "device": device,
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
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    report = run(args.model, args.images, args.revision)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(json.dumps({"candidate": report["candidate"],
                      "frames": len(report["frames"]),
                      "device": report["device"],
                      "cold_load_seconds": report["cold_load_seconds"],
                      "mean_warm_inference_seconds": (
                          sum(report["warm_inference_seconds"])
                          / max(1, len(report["warm_inference_seconds"]))),
                      "peak_rss_bytes": report["peak_rss_bytes"]}))


if __name__ == "__main__":
    main()
