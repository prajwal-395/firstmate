"""Text-to-region grounding adapters for the OBJ-026..035 evaluation lane.

A candidate model answers a frozen calibration query with text; this module
prepares the image it sees, parses its answer strictly, and freezes a
prediction record that later scoring (library/tools/spatial_grounding.py)
compares against blind agent labels. Malformed answers are recorded as
malformed, never repaired: inventing coordinates for a model would score
the adapter, not the candidate.

Image preparation uses ffmpeg only (no Pillow dependency): the prepared
image is a pure aspect-preserving downscale, and the exact resize is
carried as a spatial_grounding.ImageTransform so every prediction maps
back to full-resolution source pixels (OBJ-032).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from library.tools.spatial_grounding import ImageTransform

COORDINATE_PROMPT = (
    "Locate the described object in this image.\n"
    "Query: {phrase}\n"
    "Reply with exactly one JSON object and nothing else: "
    '{"visible": true/false, '
    '"box_1000": [x1, y1, x2, y2], "point_1000": [x, y]}. '
    "Coordinates are integers in 0..1000 relative to the image you see "
    "(0,0 is the top-left corner, 1000,1000 the bottom-right). "
    "box_1000 is the tight box around the described instance; point_1000 "
    "is a point inside that same instance. "
    "If the described thing is not visible, reply with visible false and "
    "omit box_1000 and point_1000."
)

PROMPT_SHA256 = hashlib.sha256(COORDINATE_PROMPT.encode("utf-8")).hexdigest()

MOLMOPOINT_MODEL = "mlx-community/MolmoPoint-8B-4bit"
MOLMOPOINT_REVISION = "9bab196f867ceedecdb1eab071395bfedcd62538"
FLORENCE2_MODEL = "microsoft/Florence-2-large"
FLORENCE2_REVISION = "21a599d414c4d928c9032694c424fb94458e3594"
FLORENCE2_DEVICE = "mps"
QWEN25VL_MODEL = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"
QWEN25VL_REVISION = "46d4cf06a06ffc1a766c214174f9cbed2f45bcab"

_JSON_OBJECT = re.compile(r"\{[^{}]*\}")


def render_prompt(phrase: str) -> str:
    """Fill the frozen coordinate template. Plain substitution, on purpose:
    str.format would try to interpret the JSON braces the model must echo."""
    return COORDINATE_PROMPT.replace("{phrase}", phrase)


class GroundingAdapterError(ValueError):
    """A grounding prediction cannot be produced or parsed."""


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        raise GroundingAdapterError(
            f"{cmd[0]} failed: {(proc.stderr or proc.stdout).strip()[-500:]}")


def probe_dims(png_path: str) -> tuple[int, int]:
    """Image dimensions via ffprobe (no image decode dependency)."""
    proc = subprocess.run(
        ["ffprobe", "-hide_banner", "-loglevel", "error",
         "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", png_path],
        capture_output=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        raise GroundingAdapterError(f"ffprobe failed on {png_path}")
    width, height = (int(v) for v in proc.stdout.strip().split(","))
    if width <= 0 or height <= 0:
        raise GroundingAdapterError(f"ffprobe returned no size for {png_path}")
    return width, height


def prepare_image(source_png: str, prepared_png: str, max_side: int = 768) -> ImageTransform:
    """Downscale for model input, returning the exact source mapping."""
    if max_side <= 0:
        raise GroundingAdapterError("max_side must be positive")
    source_w, source_h = probe_dims(source_png)
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
          "-i", source_png, "-vf",
          f"scale=w='if(gt(iw,ih),{max_side},-2)':h='if(gt(iw,ih),-2,{max_side})'",
          prepared_png])
    prepared_w, prepared_h = probe_dims(prepared_png)
    return ImageTransform.resize(source_w, source_h, prepared_w, prepared_h)


def extract_frozen_frame(media_path: str, timestamp: float, output_png: str) -> str:
    """Extract one frozen frame using the evaluation corpus's ffmpeg recipe."""
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
          "-ss", str(timestamp), "-i", media_path, "-frames:v", "1",
          output_png])
    return output_png


def parse_coordinate_json(raw_text: str) -> dict[str, Any]:
    """Strictly parse one coordinate answer; malformed input raises."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise GroundingAdapterError("empty model answer")
    stripped = raw_text.strip()
    match = _JSON_OBJECT.fullmatch(stripped)
    if match is None:
        raise GroundingAdapterError("no JSON object in model answer")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise GroundingAdapterError(f"answer JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("visible"), bool):
        raise GroundingAdapterError("answer needs a boolean visible field")
    if not payload["visible"]:
        return {"visible": False}
    point = payload.get("point_1000")
    box = payload.get("box_1000")
    if (not isinstance(point, list) or len(point) != 2
            or (box is not None and (not isinstance(box, list) or len(box) != 4))):
        raise GroundingAdapterError(
            "visible answer needs point_1000[2] and optional box_1000[4]")
    coords = []
    for value in (list(box) if box is not None else []) + list(point):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GroundingAdapterError("coordinates must be numbers")
        if not 0 <= value <= 1000:
            raise GroundingAdapterError("coordinates must be within 0..1000")
        coords.append(value)
    parsed = {"visible": True, "point_1000": coords[-2:]}
    if box is not None:
        x1, y1, x2, y2 = coords[:4]
        if x2 <= x1 or y2 <= y1:
            raise GroundingAdapterError("box_1000 must have positive area")
        parsed["box_1000"] = [x1, y1, x2, y2]
    if "all_points_1000" in payload:
        parsed["all_points_1000"] = payload["all_points_1000"]
    if "all_boxes_1000" in payload:
        boxes = payload["all_boxes_1000"]
        if not isinstance(boxes, list):
            raise GroundingAdapterError("all_boxes_1000 must be a list")
        parsed_boxes = []
        for item in boxes:
            if (not isinstance(item, list) or len(item) != 4
                    or any(isinstance(value, bool)
                           or not isinstance(value, (int, float))
                           or not 0 <= value <= 1000 for value in item)
                    or item[2] <= item[0] or item[3] <= item[1]):
                raise GroundingAdapterError(
                    "all_boxes_1000 entries must be positive-area boxes in 0..1000")
            parsed_boxes.append(item)
        parsed["all_boxes_1000"] = parsed_boxes
    return parsed


def coordinates_to_source(parsed: dict[str, Any], transform: ImageTransform
                          ) -> dict[str, Any]:
    """Map 0..1000 model coordinates through prepared pixels to source pixels.

    The model's normalized coordinate space is the complete prepared image.
    Keeping this conversion here ensures scoring and tracker seeding use the
    same recorded transform, including letterbox offsets when present.
    """
    if not parsed["visible"]:
        return {"visible": False}
    point_1000 = parsed["point_1000"]
    prepared_point = (point_1000[0] * transform.prepared_width / 1000,
                      point_1000[1] * transform.prepared_height / 1000)
    try:
        source_point = transform.prepared_point_to_source(prepared_point)
        result = {"visible": True,
                  "prepared_point": list(prepared_point),
                  "source_point": list(source_point)}
        if "box_1000" in parsed:
            box_1000 = parsed["box_1000"]
            prepared_box = tuple(value * size / 1000 for value, size in zip(
                box_1000, (transform.prepared_width, transform.prepared_height,
                           transform.prepared_width, transform.prepared_height)))
            source_box = transform.prepared_box_to_source(prepared_box)
            result.update({"prepared_box": list(prepared_box),
                           "source_box": list(source_box),
                           "normalized_source_box": transform.normalized_source_box(prepared_box)})
        if "all_boxes_1000" in parsed:
            prepared_boxes = [tuple(value * size / 1000 for value, size in zip(
                box, (transform.prepared_width, transform.prepared_height,
                      transform.prepared_width, transform.prepared_height)))
                for box in parsed["all_boxes_1000"]]
            result["all_source_boxes"] = [
                list(transform.prepared_box_to_source(box)) for box in prepared_boxes]
    except ValueError as exc:
        raise GroundingAdapterError(f"coordinates map outside source frame: {exc}") from exc
    return result


def gemma_text_answer(image_path: str, phrase: str) -> str:
    """Incumbent textual-coordinate generation via the resident Gemma path."""
    from library.tools.vision_model import VisionModel

    return VisionModel().analyze_image(
        image_path, render_prompt(phrase), max_tokens=300)


@lru_cache(maxsize=1)
def _load_molmopoint():
    """Load the frozen MLX point-grounding candidate once per process."""
    from mlx_vlm import load

    return load(MOLMOPOINT_MODEL, revision=MOLMOPOINT_REVISION)


def molmopoint_answer(image_path: str, phrase: str) -> str:
    """Return point coordinates from MolmoPoint in the prepared-image space."""
    from mlx_vlm import generate
    from mlx_vlm.models.molmo_point.point_utils import extract_points_from_text
    from mlx_vlm.prompt_utils import apply_chat_template

    model, processor = _load_molmopoint()
    prompt = apply_chat_template(
        processor, model.config, f"Point to {phrase}", num_images=1)
    result = generate(model, processor, prompt, [image_path],
                      max_tokens=50, temperature=0.0)
    metadata = getattr(processor, "_pointing_metadata", None)
    if not metadata:
        raise GroundingAdapterError("MolmoPoint returned no pointing metadata")
    points = extract_points_from_text(
        result.text, metadata,
        no_more_points_class=model.config.no_more_points_class,
        patch_location=model.config.patch_location)
    if not points:
        return json.dumps({"visible": False})
    width, height = metadata["image_sizes"][0]
    normalized = [[x * 1000 / width, y * 1000 / height]
                  for _, _, x, y in points]
    return json.dumps({"visible": True, "point_1000": normalized[0],
                       "all_points_1000": normalized})


@lru_cache(maxsize=1)
def _load_florence2():
    """Load Florence-2-large once per process at its published full precision."""
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, AutoProcessor
    from transformers.configuration_utils import PretrainedConfig
    from transformers.tokenization_utils_base import PreTrainedTokenizerBase

    # This pinned Florence remote config still reads the legacy field from
    # Transformers' PretrainedConfig during construction. Transformers 5
    # moved it to GenerationConfig, so supply the documented default in this
    # isolated candidate process for backward compatibility.
    if not hasattr(PretrainedConfig, "forced_bos_token_id"):
        PretrainedConfig.forced_bos_token_id = None
    if not hasattr(PreTrainedTokenizerBase, "additional_special_tokens"):
        PreTrainedTokenizerBase.additional_special_tokens = property(
            lambda tokenizer: tokenizer.extra_special_tokens)
    # Device is fixed in the frozen candidate settings and recorded in output.
    device = FLORENCE2_DEVICE
    processor = AutoProcessor.from_pretrained(
        FLORENCE2_MODEL, revision=FLORENCE2_REVISION, trust_remote_code=True)
    config = AutoConfig.from_pretrained(
        FLORENCE2_MODEL, revision=FLORENCE2_REVISION, trust_remote_code=True)
    # Transformers 5 changed PretrainedConfig's default for this field. The
    # checkpoint stores one shared vocabulary matrix and omits the three tied
    # aliases, so an implicit false default leaves the language head random.
    config.tie_word_embeddings = True
    config.text_config.tie_word_embeddings = True
    # The pinned Florence remote code uses the Transformers 4 list form for
    # `_tied_weights_keys`; Transformers 5 expects a target-to-source mapping.
    # The checkpoint stores the vocabulary matrix once as `shared.weight`.
    import sys

    from transformers.dynamic_module_utils import get_class_from_dynamic_module
    remote_model_class = get_class_from_dynamic_module(
        config.auto_map["AutoModelForCausalLM"], FLORENCE2_MODEL,
        revision=FLORENCE2_REVISION)
    remote_module = sys.modules[remote_model_class.__module__]
    tied_weight_maps = {
        "Florence2LanguageModel": {
            "encoder.embed_tokens.weight": "shared.weight",
            "decoder.embed_tokens.weight": "shared.weight",
        },
        "Florence2LanguageForConditionalGeneration": {
            "model.encoder.embed_tokens.weight": "model.shared.weight",
            "model.decoder.embed_tokens.weight": "model.shared.weight",
            "lm_head.weight": "model.shared.weight",
        },
        "Florence2ForConditionalGeneration": {
            "language_model.model.encoder.embed_tokens.weight":
                "language_model.model.shared.weight",
            "language_model.model.decoder.embed_tokens.weight":
                "language_model.model.shared.weight",
            "language_model.lm_head.weight":
                "language_model.model.shared.weight",
        },
    }
    for name, mapping in tied_weight_maps.items():
        model_class = getattr(remote_module, name, None)
        if model_class is not None:
            model_class._tied_weights_keys = mapping
    # Transformers 5 defaults custom models to SDPA, whose init-time capability
    # probe expects a class attribute removed from this published Florence code.
    # Its eager attention implementation is available and compatible.
    config._attn_implementation = "eager"
    model = AutoModelForCausalLM.from_pretrained(
        FLORENCE2_MODEL, revision=FLORENCE2_REVISION, trust_remote_code=True,
        config=config)
    # The cache contains one shared vocabulary matrix. Transformers 5 ignores
    # the legacy tied-key list while loading custom remote code, so explicitly
    # restore Florence's documented shared-embedding topology from that loaded
    # tensor before the first forward pass.
    language_model = model.language_model
    shared_embeddings = language_model.model.shared
    language_model.model.encoder.embed_tokens = shared_embeddings
    language_model.model.decoder.embed_tokens = shared_embeddings
    language_model.lm_head.weight = shared_embeddings.weight
    model = model.to(device=device, dtype=torch.float32).eval()
    # Transformers 5 wraps encoder-decoder KV tuples in Cache objects, while
    # this pinned Florence code indexes the legacy tuple in generation helpers.
    from types import MethodType

    from transformers.cache_utils import EncoderDecoderCache

    def to_legacy_call(args, kwargs):
        args = list(args)
        cache = kwargs.get("past_key_values")
        if cache is None and len(args) > 1:
            cache = args[1]
        if isinstance(cache, EncoderDecoderCache):
            legacy = tuple(tuple(layer) for layer in cache)
            if not legacy or not legacy[0] or legacy[0][0] is None:
                legacy = None
            if "past_key_values" in kwargs:
                kwargs["past_key_values"] = legacy
            elif len(args) > 1:
                args[1] = legacy
        return tuple(args), kwargs

    original_prepare = model.language_model.prepare_inputs_for_generation
    def prepare(self, *args, **kwargs):
        args, kwargs = to_legacy_call(args, kwargs)
        return original_prepare(*args, **kwargs)
    model.language_model.prepare_inputs_for_generation = MethodType(
        prepare, model.language_model)

    for module in (model, model.language_model):
        original_forward = module.forward
        def forward(self, *args, _original=original_forward, **kwargs):
            args, kwargs = to_legacy_call(args, kwargs)
            outputs = _original(*args, **kwargs)
            # Keep Transformers' generation loop on its Cache API (MPS
            # stopping criteria inspect Cache.is_croppable), then convert back
            # only at Florence's tuple-indexing compatibility seam.
            cache = getattr(outputs, "past_key_values", None)
            if isinstance(cache, tuple):
                outputs.past_key_values = EncoderDecoderCache(cache)
            return outputs
        module.forward = MethodType(forward, module)
    return model, processor, device


def florence2_answer(image_path: str, phrase: str) -> str:
    """Ground a phrase to boxes; expose the first box center as its point."""
    import torch
    from PIL import Image

    # This task accepts a referring phrase directly. Caption-to-phrase
    # grounding expects a caption containing the target phrase and often
    # returns no regions for standalone referring expressions.
    task = "<OPEN_VOCABULARY_DETECTION>"
    model, processor, device = _load_florence2()
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    side = max(image.size)
    left = (side - image.width) // 2
    top = (side - image.height) // 2
    square = Image.new("RGB", (side, side), (122, 116, 104))
    square.paste(image, (left, top))
    inputs = processor(text=task + phrase, images=square, return_tensors="pt")
    input_ids = inputs["input_ids"].to(device)
    pixel_values = inputs["pixel_values"].to(device=device, dtype=torch.float32)
    with torch.inference_mode():
        generated = model.generate(input_ids=input_ids, pixel_values=pixel_values,
                                   max_new_tokens=128, num_beams=1,
                                   use_cache=False)
    text = processor.batch_decode(generated, skip_special_tokens=False)[0]
    result = processor.post_process_generation(
        text, task=task, image_size=(side, side))[task]
    boxes = result["bboxes"]
    if not boxes:
        return json.dumps({"visible": False})
    normalized_boxes = []
    for box in boxes:
        x1, y1, x2, y2 = map(float, box)
        x1, x2 = (max(0.0, min(image.width, x - left)) for x in (x1, x2))
        y1, y2 = (max(0.0, min(image.height, y - top)) for y in (y1, y2))
        normalized_boxes.append([x1 * 1000 / image.width,
                                 y1 * 1000 / image.height,
                                 x2 * 1000 / image.width,
                                 y2 * 1000 / image.height])
    x1, y1, x2, y2 = normalized_boxes[0]
    return json.dumps({"visible": True, "box_1000": normalized_boxes[0],
                       "point_1000": [(x1 + x2) / 2, (y1 + y2) / 2],
                       "all_boxes_1000": normalized_boxes,
                       "florence_labels": result.get(
                           "labels", result.get("bboxes_labels", []))})


@lru_cache(maxsize=1)
def _load_qwen25vl():
    """Load the frozen Qwen2.5-VL 3B MLX 4-bit candidate once."""
    from mlx_vlm import load

    return load(QWEN25VL_MODEL, revision=QWEN25VL_REVISION)


def qwen25vl_answer(image_path: str, phrase: str) -> str:
    """Ask Qwen2.5-VL for the same strict coordinate JSON as Gemma."""
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    model, processor = _load_qwen25vl()
    prompt = apply_chat_template(
        processor, model.config, render_prompt(phrase), num_images=1)
    result = generate(model, processor, prompt, image=[image_path],
                      max_tokens=200, temperature=0.0, verbose=False)
    return result.text if hasattr(result, "text") else str(result)


def _peak_rss_mb() -> float:
    scale = 1024 * 1024 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale


def predict_query(query: dict[str, Any], frame: dict[str, Any],
                  prepared_png: str, transform: ImageTransform,
                  text_answer: Callable[[str, str], str]) -> dict[str, Any]:
    """Run one frozen query and return its frozen prediction record."""
    record: dict[str, Any] = {
        "query_id": query["query_id"],
        "frame_id": frame["frame_id"],
        "phrase": query["phrase"],
        "prompt_sha256": PROMPT_SHA256,
        "prepared": {"width": transform.prepared_width,
                     "height": transform.prepared_height,
                     "scale_x": transform.scale_x,
                     "scale_y": transform.scale_y,
                     "offset_x": transform.offset_x,
                     "offset_y": transform.offset_y,
                     "source_width": transform.source_width,
                     "source_height": transform.source_height},
    }
    timers: dict[str, float] = {}
    started = time.perf_counter()
    try:
        raw = text_answer(prepared_png, query["phrase"])
    except Exception as exc:  # noqa: BLE001 - inference failure is data
        timers["infer_s"] = time.perf_counter() - started
        record["malformed"] = {"reason": f"inference failed: {exc}"}
        record["timings_s"] = timers
        return record
    timers["infer_s"] = time.perf_counter() - started
    serialize_started = time.perf_counter()
    record["raw_answer"] = raw[:2000]
    try:
        parsed = parse_coordinate_json(raw)
        record["parsed"] = parsed
        record["geometry"] = coordinates_to_source(parsed, transform)
    except GroundingAdapterError as exc:
        record["malformed"] = {"reason": str(exc)}
    timers["serialize_s"] = time.perf_counter() - serialize_started
    record["timings_s"] = timers
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--media-root", type=Path,
                        help="root for media paths frozen in the calibration manifest")
    parser.add_argument("--model", default="gemma-4-12b-it-4bit")
    parser.add_argument("--model-revision", default="73bcf09092aa277861d5a191b989b666f7f32e8f")
    parser.add_argument("--precision", default="mlx-4bit")
    parser.add_argument("--backend", choices=("gemma", "molmopoint", "florence2", "qwen25vl"), default="gemma")
    parser.add_argument("--max-side", type=int, default=768)
    parser.add_argument("--frame-png", action="append", default=[],
                        help="frame_id=/path/to/frame.png; repeatable (test seam)")
    args = parser.parse_args()

    overrides = dict(item.split("=", 1) for item in args.frame_png)

    if args.backend == "molmopoint":
        args.model = MOLMOPOINT_MODEL
        args.model_revision = MOLMOPOINT_REVISION
        text_answer = molmopoint_answer
    elif args.backend == "florence2":
        args.model = FLORENCE2_MODEL
        args.model_revision = FLORENCE2_REVISION
        args.precision = "full-fp32"
        text_answer = florence2_answer
    elif args.backend == "qwen25vl":
        args.model = QWEN25VL_MODEL
        args.model_revision = QWEN25VL_REVISION
        args.precision = "mlx-4bit"
        text_answer = qwen25vl_answer
    else:
        text_answer = gemma_text_answer

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    frames = {f["frame_id"]: f for f in manifest["frames"]}
    args.prepared_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    infer_times: list[float] = []
    rss_before = _peak_rss_mb()
    wall_started = time.perf_counter()
    for query in manifest["queries"]:
        frame = frames[query["frame_id"]]
        source_png = overrides.get(frame["frame_id"])
        if source_png is None:
            if args.media_root is None:
                raise GroundingAdapterError(
                    f"no --frame-png for {frame['frame_id']}; provide --media-root "
                    "to resolve the manifest's frozen media path")
            source_png = str(args.prepared_dir / f"{frame['frame_id']}.source.png")
            extract_frozen_frame(
                str(args.media_root / frame["media"]), frame["t_seconds"], source_png)
            digest = hashlib.sha256(Path(source_png).read_bytes()).hexdigest()
            if digest != frame["frame_sha256"]:
                raise GroundingAdapterError(
                    f"{frame['frame_id']}: extracted frame hash differs from frozen manifest")
        decode_started = time.perf_counter()
        prepared_png = str(args.prepared_dir / f"{frame['frame_id']}.png")
        transform = prepare_image(source_png, prepared_png,
                                  args.max_side)
        w, h = probe_dims(source_png)
        if (w, h) != (frame["width"], frame["height"]):
            raise GroundingAdapterError(
                f"{frame['frame_id']}: re-extracted frame is {w}x{h}, "
                f"manifest froze {frame['width']}x{frame['height']}")
        decode_s = time.perf_counter() - decode_started
        record = predict_query(query, frame, prepared_png, transform, text_answer)
        record["timings_s"]["decode_s"] = decode_s
        record.update({"model": args.model, "model_revision": args.model_revision,
                       "precision": args.precision})
        if args.backend == "florence2":
            record["device"] = FLORENCE2_DEVICE
        elif args.backend == "qwen25vl":
            record["device"] = "mlx"
        records.append(record)
        infer_times.append(record["timings_s"].get("infer_s", 0.0))
    wall_s = time.perf_counter() - wall_started
    payload = {
        "format": "ren_spatial_grounding_predictions/1",
        "manifest": str(args.manifest),
        "split": manifest["split"],
        "model": args.model,
        "model_revision": args.model_revision,
        "precision": args.precision,
        "device": (FLORENCE2_DEVICE if args.backend == "florence2"
                   else "mlx" if args.backend == "qwen25vl" else None),
        "resource": {
            "wall_s": wall_s,
            "cold_infer_s": infer_times[0] if infer_times else None,
            "warm_infer_s_mean": (sum(infer_times[1:]) / len(infer_times[1:])
                                  if len(infer_times) > 1 else None),
            "peak_rss_mb": _peak_rss_mb(),
            "peak_rss_mb_before": rss_before,
        },
        "predictions": records,
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    malformed = sum(1 for r in records if "malformed" in r)
    print(f"froze {len(records)} predictions "
          f"({malformed} malformed) -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
