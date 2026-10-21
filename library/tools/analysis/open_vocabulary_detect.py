"""MU-15: open-vocabulary object detection - the seed SAM 2 tracks grow from.

SAM 2 tracks whatever it is seeded with, and the 1.06 step only ever
seeded faces (or arbitrary blobs before that): "mask out the logo" had
no box to start from, so C-11's `mask.create` had no producer for a
named object. This module is the naming half: given an image and Gemma's
object phrases ("laptop", "coffee mug"), each detector answers boxes.

Backends (the plan's bakeoff slate, MU-15 step one):

- `vision_foreground`: the class-agnostic baseline - Apple Vision's
  `VNGenerateForegroundInstanceMaskRequest` through
  `object_foreground_helper.swift` beside this file. It names no
  phrase, so its detections carry `phrase=""`: the bakeoff scores it
  by whether ANY instance covers each ground-truth box.
- `florence2`: `microsoft/Florence-2-base`, `<OPEN_VOCABULARY_DETECTION>`.
- `owlv2`: `google/owlv2-base-patch16-ensemble`.
- `grounding_dino`: `IDEA-Research/grounding-dino-tiny`.
- `rf_detr_nano` / `rf_detr_small`: fixed-vocabulary COCO detectors
  (`Roboflow/rf-detr-nano`, `Roboflow/rf-detr-small`, Apache-2.0). They
  predict COCO classes, not the queried phrases, so the adapter maps
  each COCO label back to a queried phrase with the same fuzzy match
  the Florence-2 path uses, and drops predictions no phrase names.
  Dropped out-of-query predictions are NOT detections of the query:
  the bakeoff scores what the detector answered, and the scorecard
  states the mapping rule beside the numbers.

Heavy models load lazily and once per process; a backend whose
package or weights are missing REFUSES (`DetectorNotAvailable`) with
the reason, never an empty list - an empty list means "measured,
nothing there". Every detection is a normalised top-left
`[x0, y0, x1, y1]` box in 0..1, the space Gemma ground truth and the
1.06 seeder already speak.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools.analyzer_providers import (
    AnalyzerTier,
    CapabilityPlans,
    CapabilityTiers,
    ExecutionPlan,
    PromotionTrigger,
    run_ordered_tiers,
    select_execution_plan,
)

DETECTORS = ("vision_foreground", "florence2", "owlv2", "grounding_dino",
               "rf_detr_nano", "rf_detr_small")
"""Every open-vocabulary detector the bakeoff scores, in doc order."""

VISION_FOREGROUND = "vision_foreground"
FLORENCE2 = "florence2"
OWLV2 = "owlv2"
GROUNDING_DINO = "grounding_dino"
RFDETR_NANO = "rf_detr_nano"
RFDETR_SMALL = "rf_detr_small"

FLORENCE2_MODEL_ID = "microsoft/Florence-2-base"
OWLV2_MODEL_ID = "google/owlv2-base-patch16-ensemble"
OWLV2_QUERY_BATCH_SIZE = 4
OWLV2_MAX_TEXT_LENGTH = 16
OWLV2_PADDING_QUERY = "__owlv2_padding__"
OWLV2_INPUT_SIZE = 960
GROUNDING_DINO_MODEL_ID = "IDEA-Research/grounding-dino-tiny"
RFDETR_NANO_MODEL_ID = "Roboflow/rf-detr-nano"
RFDETR_SMALL_MODEL_ID = "Roboflow/rf-detr-small"
# Publisher-published checkpoint revisions (the hub commit each scorecard
# ran against), recorded from the publisher, never hashed locally.
RFDETR_NANO_REVISION = "9f201577d9415f0378084748d27e68586e6b3600"
RFDETR_SMALL_REVISION = "3bdc465063270f99769da5a1b4c00c68bd2d439d"

FLORENCE2_OVD_TASK = "<OPEN_VOCABULARY_DETECTION>"

DEFAULT_SCORE_THRESHOLD = 0.1
"""The floor a scored box must clear to be returned. Stated, not
tuned: the bakeoff records it beside every recall number, so a reader
can re-score the same boxes at any other floor."""

BAKEOFF_IOU_THRESHOLD = 0.5
"""A ground-truth box is recalled when any predicted box for its phrase
overlaps it at IoU >= this. The detection literature's default, kept
so the numbers compare with what a reader expects."""


class DetectorNotAvailable(RuntimeError):
    """A detector backend cannot run on this machine (no package, no
    weights, no API in this transformers version). Raised, never an
    empty detection list: callers (and the bakeoff doc) must tell
    "cannot run" from "ran, nothing there"."""


@dataclass
class Detection:
    """One detected object: whose phrase, where, how confidently."""

    phrase: str
    box: list[float]
    score: float
    detector: str = ""
    extra: dict = field(default_factory=dict)
    tier_id: str = ""
    promoted_because: str | None = None


@dataclass(frozen=True)
class TieredDetectionResult:
    plan: ExecutionPlan
    detections: tuple[Detection, ...]
    status: str
    promoted_because: str | None


def check_detector(name: str) -> str:
    """The detector `name` names, refused when it is not on the slate."""
    if name not in DETECTORS:
        raise ValueError(
            f"unknown open-vocabulary detector {name!r}; one of "
            f"{list(DETECTORS)}")
    return name


def check_phrases(phrases: Sequence[str]) -> list[str]:
    """Phrases as a clean non-empty list. An empty phrase list is
    refused: a detector asked about nothing answers nothing, and that
    empty answer would read as a recall of zero."""
    cleaned = [str(p).strip() for p in (phrases or [])]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        raise ValueError("an open-vocabulary query needs at least one "
                         "phrase - Gemma's object names, not an empty list")
    return cleaned


def normalize_box(box: Sequence[float]) -> list[float] | None:
    """A [x0, y0, x1, y1] box clamped to the unit square, or None when
    it names no area. A partially out-of-frame detection is clamped (an
    object at the frame edge is still an object); an inverted or empty
    one is not a detection."""
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x0, y0 = min(max(x0, 0.0), 1.0), min(max(y0, 0.0), 1.0)
    x1, y1 = min(max(x1, 0.0), 1.0), min(max(y1, 0.0), 1.0)
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two normalised boxes. A degenerate
    box overlaps nothing: it is not a detection, so it scores zero."""
    try:
        ax0, ay0, ax1, ay1 = (float(v) for v in a)
        bx0, by0, bx1, by1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return 0.0
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


# ── Backend loading (lazy: no model cost at import) ──

_LOADED: dict[str, dict[str, Any]] = {}


def _torch_device() -> str:
    """MPS where this Mac has it, else CPU. Recorded beside every
    bakeoff number, so a recall is never compared across devices."""
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _require_transformers():
    try:
        import transformers  # noqa: F401
    except ImportError as exc:
        raise DetectorNotAvailable(
            "transformers is not installed in this interpreter, so no "
            "HF-backed detector can run here") from exc


def _load_florence2() -> dict[str, Any]:
    _require_transformers()
    from transformers import AutoModelForCausalLM, AutoProcessor

    device = _torch_device()
    try:
        processor = AutoProcessor.from_pretrained(
            FLORENCE2_MODEL_ID, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            FLORENCE2_MODEL_ID, trust_remote_code=True)
    except Exception as exc:
        # network, gated repo: all read as cannot-run with the reason.
        raise DetectorNotAvailable(
            f"Florence-2 weights {FLORENCE2_MODEL_ID} did not load: "
            f"{type(exc).__name__}: {exc}") from exc
    if device != "cpu":
        try:
            model = model.to(device)
        except Exception:  # noqa: BLE001 - MPS placement is an
            # optimisation, never a verdict: fall back to CPU loudly.
            device = "cpu"
    return {"processor": processor, "model": model, "device": device,
            "model_id": FLORENCE2_MODEL_ID}


def _load_owlv2() -> dict[str, Any]:
    _require_transformers()
    try:
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
    except ImportError as exc:
        raise DetectorNotAvailable(
            "this transformers version has no Owlv2 classes, so OWLv2 "
            "cannot run here") from exc
    device = _torch_device()
    try:
        processor = Owlv2Processor.from_pretrained(OWLV2_MODEL_ID)
        _set_owlv2_input_size(processor)
        model = Owlv2ForObjectDetection.from_pretrained(OWLV2_MODEL_ID)
    except Exception as exc:
        raise DetectorNotAvailable(
            f"OWLv2 weights {OWLV2_MODEL_ID} did not load: "
            f"{type(exc).__name__}: {exc}") from exc
    if device != "cpu":
        try:
            model = model.to(device)
        except Exception:  # noqa: BLE001 - see _load_florence2.
            device = "cpu"
    return {"processor": processor, "model": model, "device": device,
            "model_id": OWLV2_MODEL_ID}


def _set_owlv2_input_size(processor: Any) -> None:
    """Keep the processor on the model's published 960 px input canvas."""
    image_processor = processor.image_processor
    size = image_processor.size
    if isinstance(size, dict):
        image_processor.size = {**size, "height": OWLV2_INPUT_SIZE,
                                "width": OWLV2_INPUT_SIZE}
    else:
        size.height = OWLV2_INPUT_SIZE
        size.width = OWLV2_INPUT_SIZE


def _load_grounding_dino() -> dict[str, Any]:
    _require_transformers()
    try:
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    except ImportError as exc:
        raise DetectorNotAvailable(
            "this transformers version has no zero-shot detection "
            "classes, so Grounding DINO cannot run here") from exc
    device = _torch_device()
    try:
        processor = AutoProcessor.from_pretrained(GROUNDING_DINO_MODEL_ID)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(
            GROUNDING_DINO_MODEL_ID)
    except Exception as exc:
        raise DetectorNotAvailable(
            f"Grounding DINO weights {GROUNDING_DINO_MODEL_ID} did not "
            f"load: {type(exc).__name__}: {exc}") from exc
    if device != "cpu":
        try:
            model = model.to(device)
        except Exception:  # noqa: BLE001 - see _load_florence2.
            device = "cpu"
    return {"processor": processor, "model": model, "device": device,
            "model_id": GROUNDING_DINO_MODEL_ID}


def _load_rf_detr(model_id: str, revision: str) -> dict[str, Any]:
    _require_transformers()
    try:
        from transformers import RfDetrForObjectDetection, RfDetrImageProcessor
    except ImportError as exc:
        raise DetectorNotAvailable(
            "this transformers version has no RF-DETR classes, so "
            f"{model_id} cannot run here") from exc
    device = _torch_device()
    try:
        processor = RfDetrImageProcessor.from_pretrained(
            model_id, revision=revision)
        model = RfDetrForObjectDetection.from_pretrained(
            model_id, revision=revision)
    except Exception as exc:
        raise DetectorNotAvailable(
            f"RF-DETR weights {model_id}@{revision} did not load: "
            f"{type(exc).__name__}: {exc}") from exc
    if device != "cpu":
        try:
            model = model.to(device)
        except Exception:  # noqa: BLE001 - see _load_florence2.
            device = "cpu"
    return {"processor": processor, "model": model, "device": device,
            "model_id": model_id, "revision": revision}


def load_detector(name: str) -> dict[str, Any]:
    """A detector's loaded backend, cached per process. The Vision
    baseline answers `{"device": "ane"}`; HF backends answer their
    processor, model, device and id. Missing weights refuse - never an
    unloaded backend wearing a loaded one's name."""
    check_detector(name)
    if name not in _LOADED:
        if name == VISION_FOREGROUND:
            _LOADED[name] = {"device": "ane", "model_id": "apple-vision"}
        elif name == FLORENCE2:
            _LOADED[name] = _load_florence2()
        elif name == OWLV2:
            _LOADED[name] = _load_owlv2()
        elif name == GROUNDING_DINO:
            _LOADED[name] = _load_grounding_dino()
        elif name == RFDETR_NANO:
            _LOADED[name] = _load_rf_detr(
                RFDETR_NANO_MODEL_ID, RFDETR_NANO_REVISION)
        elif name == RFDETR_SMALL:
            _LOADED[name] = _load_rf_detr(
                RFDETR_SMALL_MODEL_ID, RFDETR_SMALL_REVISION)
    return _LOADED[name]


def published_revision(loaded: dict[str, Any]) -> str | None:
    """Return the hub-published checkpoint revision reported by Transformers.

    A local file digest would identify cached bytes, not the publisher's
    revision, so absent metadata stays unknown instead of being guessed.
    """
    for value in (loaded.get("model"), loaded.get("processor")):
        config = getattr(value, "config", None)
        revision = getattr(config, "_commit_hash", None)
        if isinstance(revision, str) and revision.strip():
            return revision.strip()
    return None


# ── Per-image detection ──


def _read_image(image_path: str):
    try:
        from PIL import Image
    except ImportError as exc:
        raise DetectorNotAvailable(
            "Pillow is not installed, so no image can be read for "
            "detection") from exc
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"no frame at {image_path}")
    image = Image.open(path).convert("RGB")
    return image, image.size


def _detect_vision_foreground(image_path: str) -> list[Detection]:
    """The class-agnostic baseline: every Vision foreground instance as
    a phraseless detection. One helper process per call - the bakeoff
    batches through `measure_frames` instead; this is the single-image
    entry the winner's seeding path shares."""
    import os
    import tempfile

    from library.tools import vision_measure

    helper, reason = vision_measure.ensure_helper(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "object_foreground_helper.swift"),
        "object_foreground_helper")
    if helper is None:
        raise DetectorNotAvailable(
            f"the Vision foreground helper did not build: {reason}")
    with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                     delete=False) as handle:
        handle.write(image_path + "\n")
        list_path = handle.name
    try:
        docs = vision_measure.measure_frames([image_path], helper)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
    if not docs or docs[0].get("error"):
        raise DetectorNotAvailable(
            f"Vision foreground failed on {image_path}: "
            f"{(docs[0].get('error') if docs else 'no document')}")
    out = []
    for inst in docs[0].get("instances") or []:
        box = normalize_box(inst.get("box") or [])
        if box is None:
            continue
        out.append(Detection(phrase="", box=box, score=1.0,
                             detector=VISION_FOREGROUND))
    return out


def _detect_florence2(image_path: str, phrases: list[str],
                      score_threshold: float) -> list[Detection]:
    import torch

    loaded = load_detector(FLORENCE2)
    processor, model, device = (loaded["processor"], loaded["model"],
                                loaded["device"])
    image, _ = _read_image(image_path)
    prompt = FLORENCE2_OVD_TASK + " " + " ".join(phrases)
    inputs = processor(text=prompt, images=image, return_tensors="pt")
    inputs = {k: (v.to(device) if hasattr(v, "to") else v)
              for k, v in inputs.items()}
    post = getattr(processor, "post_process_generation", None)
    if post is None:
        raise DetectorNotAvailable(
            "this transformers version answers no "
            "post_process_generation, so Florence-2 boxes cannot be read")
    with torch.no_grad():
        generated = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024, num_beams=3)
    try:
        decoded = processor.batch_decode(generated,
                                         skip_special_tokens=False)[0]
    except (AttributeError, IndexError, TypeError) as exc:
        raise DetectorNotAvailable(
            "Florence-2 generations cannot be decoded by this "
            f"transformers version: {exc}") from exc
    parsed = post(decoded, task=FLORENCE2_OVD_TASK,
                  image_size=(image.size[1], image.size[0]))
    # The response is keyed by task ({task: {...}}); the OVD answer
    # carries "bboxes"/"bboxes_labels" (0-1000 quantized). Older shapes
    # ("boxes"/"labels") are read too, so a version skew refuses
    # nothing it could have read.
    answer = parsed.get(FLORENCE2_OVD_TASK, parsed)
    boxes = answer.get("bboxes", answer.get("boxes", []))
    labels = answer.get("bboxes_labels", answer.get("labels", []))
    out = []
    for box, label in zip(boxes, labels):
        try:
            x0, y0, x1, y1 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        norm = normalize_box([x0 / 1000.0, y0 / 1000.0,
                              x1 / 1000.0, y1 / 1000.0])
        if norm is None:
            continue
        phrase = _match_phrase(str(label), phrases)
        out.append(Detection(phrase=phrase, box=norm, score=1.0,
                             detector=FLORENCE2))
    return [d for d in out if d.score >= score_threshold]


def _stem(word: str) -> str:
    """A crude stem, enough to see that "potted" and "pot" are one
    word. It is used only to decide whether two COMPOUND names are the
    same noun said differently."""
    stemmed = word.strip().lower()
    for suffix in ("ted", "ed", "es", "s"):
        if len(stemmed) > len(suffix) + 2 and stemmed.endswith(suffix):
            stemmed = stemmed[:-len(suffix)]
            break
    return stemmed


def _match_phrase(label: str, phrases: list[str]) -> str:
    """A detector's raw label back to the queried phrase: exact
    case-insensitive match first, then the queried phrase the label
    contains (or that contains the label). Unmatched labels keep their
    own text rather than being forced onto a phrase - a forced match
    would score recall for words nobody asked about.

    Two COMPOUND names that are the same nouns in another order are one
    noun: COCO says "potted plant" where the query said "plant pot",
    and that is a TRUE detection of the queried object. Before this
    rule the adapter dropped it for a naming reason and the fixed
    vocabulary path measured 0 plant-pot coverage on real footage
    (D-01, 2026-10-21). Both sides must be compounds, so a one-word
    label still never answers a longer phrase: COCO's "car" is not a
    "car door handle", and "traffic light" is not a "light switch"."""
    lowered = label.strip().lower()
    for phrase in phrases:
        phrase_lower = phrase.lower()
        if phrase_lower == lowered:
            return phrase
        if phrase_lower in lowered:
            return phrase
        # A label may be LONGER than the query ("cell phone" answers
        # "phone"), but a ONE-WORD label is not a longer query: COCO's
        # "car" is not a "car door handle", and counting it as one
        # makes the fixed-vocabulary path report detections it did not
        # make (D-01, 2026-10-21).
        if lowered in phrase_lower and (
                len(lowered.split()) >= 2 or len(phrase_lower.split()) == 1):
            return phrase
    label_words = lowered.split()
    if len(label_words) >= 2:
        label_stems = {_stem(word) for word in label_words}
        for phrase in phrases:
            phrase_words = phrase.lower().split()
            if len(phrase_words) < 2:
                continue
            if label_stems == {_stem(word) for word in phrase_words}:
                return phrase
    return label


def _detect_owlv2(image_path: str, phrases: list[str],
                  score_threshold: float) -> list[Detection]:
    import gc

    import torch

    loaded = load_detector(OWLV2)
    processor, model, device = (loaded["processor"], loaded["model"],
                                loaded["device"])
    try:
        # Keep all per-image tensors in a short-lived frame. OWLv2 returns
        # device tensors from both the model and post-processor; returning
        # only Python-valued Detection records lets those references die
        # before the MPS allocator cache is released.
        return _owlv2_infer(processor, model, device, image_path, phrases,
                            score_threshold)
    finally:
        if device == "mps":
            gc.collect()
            torch.mps.empty_cache()


def _owlv2_infer(processor: Any, model: Any, device: str, image_path: str,
                 phrases: list[str], score_threshold: float
                 ) -> list[Detection]:
    import gc

    import torch

    image, (width, height) = _read_image(image_path)
    if max(width, height) > OWLV2_INPUT_SIZE:
        # Owlv2ImageProcessor pads to square before resizing. Thumbnail first
        # so a 4K source does not allocate a 4K-square tensor just to become
        # the same 960 px canvas.
        image.thumbnail((OWLV2_INPUT_SIZE, OWLV2_INPUT_SIZE))
    out: list[Detection] = []
    for start in range(0, len(phrases), OWLV2_QUERY_BATCH_SIZE):
        active_phrases = phrases[start:start + OWLV2_QUERY_BATCH_SIZE]
        phrase_batch = active_phrases + [
            OWLV2_PADDING_QUERY
        ] * (OWLV2_QUERY_BATCH_SIZE - len(active_phrases))
        out.extend(_owlv2_infer_phrase_batch(
            processor, model, device, image, width, height, phrase_batch,
            len(active_phrases), score_threshold))
        if device == "mps":
            torch.mps.synchronize()
            gc.collect()
            torch.mps.empty_cache()
    return out


def _owlv2_infer_phrase_batch(processor: Any, model: Any, device: str,
                              image: Any, width: int, height: int,
                              phrases: list[str], active_phrase_count: int,
                              score_threshold: float
                              ) -> list[Detection]:
    import torch

    post = getattr(processor, "post_process_grounded_object_detection",
                   None)
    if post is None:
        raise DetectorNotAvailable(
            "this transformers version answers no "
            "post_process_grounded_object_detection on OWLv2, so its "
            "boxes cannot be read")
    inputs = processor(text=[phrases], images=image, return_tensors="pt",
                       padding="max_length", max_length=OWLV2_MAX_TEXT_LENGTH)
    inputs = {k: (v.to(device) if hasattr(v, "to") else v)
              for k, v in inputs.items()}
    inputs["interpolate_pos_encoding"] = OWLV2_INPUT_SIZE != 960
    with torch.no_grad():
        outputs = model(**inputs)
    target = (height, width)
    results = post(outputs, target_sizes=[target], threshold=0.0,
                   text_labels=[phrases])[0]
    texts = results.get("text_labels") or []
    out = []
    for i, (box, score, label) in enumerate(zip(
            results["boxes"], results["scores"], results["labels"])):
        score_f = float(score)
        if score_f < score_threshold:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in box.detach().cpu().tolist())
        except (TypeError, ValueError):
            continue
        norm = normalize_box([x0 / width, y0 / height,
                              x1 / width, y1 / height])
        if norm is None:
            continue
        if i < len(texts) and texts[i]:
            text_label = str(texts[i])
            if text_label == OWLV2_PADDING_QUERY:
                continue
            phrase = _match_phrase(text_label, phrases[:active_phrase_count])
        else:
            try:
                label_index = int(label)
                if label_index >= active_phrase_count:
                    continue
                phrase = phrases[label_index]
            except (TypeError, ValueError, IndexError):
                phrase = str(label)
        out.append(Detection(phrase=phrase, box=norm, score=round(score_f, 3),
                             detector=OWLV2))
    return out


def _detect_grounding_dino(image_path: str, phrases: list[str],
                           score_threshold: float) -> list[Detection]:
    import torch

    loaded = load_detector(GROUNDING_DINO)
    processor, model, device = (loaded["processor"], loaded["model"],
                                loaded["device"])
    post = getattr(processor, "post_process_grounded_object_detection",
                   None)
    if post is None:
        raise DetectorNotAvailable(
            "this transformers version answers no "
            "post_process_grounded_object_detection, so Grounding DINO "
            "boxes cannot be read")
    image, (width, height) = _read_image(image_path)
    inputs = processor(images=image, text=[phrases], return_tensors="pt")
    inputs = {k: (v.to(device) if hasattr(v, "to") else v)
              for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    target = (height, width)
    results = post(outputs, target_sizes=[target],
                   threshold=0.0, text_threshold=0.0,
                   text_labels=[phrases])[0]
    out = []
    for box, score, label in zip(results["boxes"], results["scores"],
                                 results["labels"]):
        score_f = float(score)
        if score_f < score_threshold:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in box.detach().cpu().tolist())
        except (TypeError, ValueError):
            continue
        norm = normalize_box([x0 / width, y0 / height,
                              x1 / width, y1 / height])
        if norm is None:
            continue
        phrase = _match_phrase(str(label), phrases)
        out.append(Detection(phrase=phrase, box=norm, score=round(score_f, 3),
                             detector=GROUNDING_DINO))
    return out


def _detect_rf_detr(detector: str, image_path: str, phrases: list[str],
                    score_threshold: float) -> list[Detection]:
    """Fixed-vocabulary COCO detection mapped onto the queried phrases.

    RF-DETR names COCO classes ("cell phone", "cup"), never the query,
    so each prediction is mapped with `_match_phrase` and predictions
    no queried phrase names are dropped: they are true COCO detections
    but not answers to this query. `extra["coco_label"]` keeps the raw
    class so a scorecard can audit the mapping; `extra` never reaches
    the bakeoff's scored boxes."""
    import torch

    loaded = load_detector(detector)
    processor, model, device = (loaded["processor"], loaded["model"],
                                loaded["device"])
    post = getattr(processor, "post_process_object_detection", None)
    if post is None:
        raise DetectorNotAvailable(
            "this transformers version answers no "
            "post_process_object_detection on RF-DETR, so its boxes "
            "cannot be read")
    image, (width, height) = _read_image(image_path)
    inputs = processor(images=image, return_tensors="pt")
    inputs = {k: (v.to(device) if hasattr(v, "to") else v)
              for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    results = post(outputs, target_sizes=[(height, width)],
                   threshold=0.0)[0]
    id2label = getattr(getattr(loaded.get("model"), "config", None),
                       "id2label", None) or {}
    out = []
    for box, score, raw_label in zip(results["boxes"], results["scores"],
                                     results.get("labels", [])):
        score_f = float(score)
        if score_f < score_threshold:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in box.detach().cpu().tolist())
        except (TypeError, ValueError):
            continue
        norm = normalize_box([x0 / width, y0 / height,
                              x1 / width, y1 / height])
        if norm is None:
            continue
        try:
            # Post-processing answers class indices (tensors or ints).
            coco_label = str(id2label.get(int(raw_label), raw_label))
        except (TypeError, ValueError):
            coco_label = str(raw_label)
        phrase = _match_phrase(coco_label, phrases)
        if phrase not in phrases:
            # No queried phrase names this COCO class: a true detection
            # of something unasked, not an answer. Dropped, counted
            # nowhere; the scorecard states this rule.
            continue
        out.append(Detection(phrase=phrase, box=norm, score=round(score_f, 3),
                             detector=detector,
                             extra={"coco_label": coco_label}))
    return out


def detect_image(detector: str, image_path: str, phrases: Sequence[str],
                 *, score_threshold: float = DEFAULT_SCORE_THRESHOLD,
                 ) -> list[Detection]:
    """Every box one detector answers on one image for `phrases`.

    The Vision baseline ignores phrases (it names none) but still
    requires them non-empty: the caller's query is what the bakeoff
    scores, and a phraseless call has no score.
    """
    check_detector(detector)
    clean = check_phrases(phrases)
    if detector == VISION_FOREGROUND:
        return _detect_vision_foreground(image_path)
    if detector == FLORENCE2:
        return _detect_florence2(image_path, clean, score_threshold)
    if detector == OWLV2:
        return _detect_owlv2(image_path, clean, score_threshold)
    if detector in (RFDETR_NANO, RFDETR_SMALL):
        return _detect_rf_detr(detector, image_path, clean, score_threshold)
    return _detect_grounding_dino(image_path, clean, score_threshold)


# Measurements on the 40-frame, 148-box bakeoff set. The phrases came from
# Gemma outputs, so they are useful operational evidence, not an unbiased
# held-out object query evaluation.
OBJECT_DETECTION_TIERS = CapabilityTiers(
    capability="object_detection",
    tiers=(
        AnalyzerTier("fast", VISION_FOREGROUND, "named_object_box_recall",
                     0.243, 0.1631349667, 26.734375,
                     frozenset({PromotionTrigger.NAMED_OBJECT,
                                PromotionTrigger.EDIT_CRITICAL,
                                PromotionTrigger.LOW_CONFIDENCE,
                                PromotionTrigger.AMBIGUOUS})),
        AnalyzerTier("high_fidelity", OWLV2, "named_object_box_recall",
                     132 / 148, 146.80397528898902 / 40, 1174.5625),
    ),
)

OBJECT_DETECTION_PLANS = CapabilityPlans(
    capability="object_detection",
    plans=(
        ExecutionPlan(
            "vision_nominations", "object_detection", ("fast",),
            "named_object_box_recall", 36 / 148, 6.525398666999536 / 40,
            26.734375, candidate_retention=36 / 148, coverage=36 / 148,
            false_negative_rate=112 / 148, inputs=("frame", "object_query"),
            output_evidence_types=("object.foreground_candidate",),
            dependencies=("Apple Vision foreground instances",),
        ),
        ExecutionPlan(
            "owlv2_named_queries", "object_detection", ("high_fidelity",),
            "named_object_box_recall", 132 / 148, 145.55284595899866 / 40,
            1174.5625, candidate_retention=132 / 148, coverage=132 / 148,
            false_negative_rate=16 / 148, inputs=("frame", "object_query"),
            output_evidence_types=("object.detection",),
            dependencies=("OWLv2",),
        ),
        ExecutionPlan(
            "vision_then_owlv2", "object_detection", ("fast", "high_fidelity"),
            "named_object_box_recall", 132 / 148, 155.1594626659935 / 40,
            1194.015625, candidate_retention=36 / 148, coverage=132 / 148,
            false_negative_rate=16 / 148, inputs=("frame", "object_query"),
            output_evidence_types=("object.foreground_candidate",
                                   "object.detection"),
            dependencies=("Apple Vision foreground instances", "OWLv2"),
        ),
    ),
)


def detect_image_tiered(
    image_path: str,
    phrases: Sequence[str],
    *,
    named_object_requested: bool = False,
    edit_critical: bool = False,
    low_confidence: bool = False,
    ambiguous: bool = False,
    minimum_fidelity: float,
    required_output_evidence_types: frozenset[str] = frozenset({
        "object.detection"}),
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> TieredDetectionResult:
    """Select a measured plan; cheap misses remain unanalyzed, never absent.

    Callers handling timelines use query-directed acquisition to invoke this
    image-scoped operation only for frames in the task's named windows.
    """
    clean = check_phrases(phrases)
    triggers = set()
    if named_object_requested:
        triggers.add(PromotionTrigger.NAMED_OBJECT)
    if edit_critical:
        triggers.add(PromotionTrigger.EDIT_CRITICAL)
    if low_confidence:
        triggers.add(PromotionTrigger.LOW_CONFIDENCE)
    if ambiguous:
        triggers.add(PromotionTrigger.AMBIGUOUS)

    selected_plan = select_execution_plan(
        OBJECT_DETECTION_PLANS,
        fidelity_metric="named_object_box_recall",
        minimum_fidelity=minimum_fidelity,
        required_output_evidence_types=required_output_evidence_types,
    )
    should_promote = "high_fidelity" in selected_plan.tier_ids
    combined_plan = ("fast" in selected_plan.tier_ids and should_promote)
    promotion_reason = (
        ",".join(sorted(trigger.value for trigger in triggers))
        if triggers else f"quality_requirement>={minimum_fidelity:g}"
    ) if combined_plan else None
    tier_elapsed: dict[str, float] = {}

    def run_tier(tier: AnalyzerTier) -> list[Detection]:
        started = time.perf_counter()
        detections = detect_image(tier.provider_id, image_path, clean,
                                  score_threshold=score_threshold)
        tier_elapsed[tier.tier_id] = time.perf_counter() - started
        return detections

    raw_rows = run_ordered_tiers(
        CapabilityTiers(
            "object_detection",
            tuple(tier for tier in OBJECT_DETECTION_TIERS.tiers
                  if tier.tier_id in selected_plan.tier_ids),
        ),
        run_tier,
        triggers=(frozenset({PromotionTrigger.NAMED_OBJECT})
                  if combined_plan else frozenset()),
    )
    detections: list[Detection] = []
    for row in raw_rows:
        detections.extend(
            Detection(d.phrase, d.box, d.score, d.detector,
                      {**d.extra,
                       "tier_elapsed_seconds": tier_elapsed[row.tier_id]},
                      row.tier_id,
                      promotion_reason if row.tier_id == "high_fidelity"
                      else None)
            for d in row.value
        )
    query_candidates = [
        detection for detection in detections
        if detection.tier_id == ("high_fidelity" if should_promote else "fast")
        and (not should_promote or
             detection.phrase.strip().lower() in
             {phrase.lower() for phrase in clean})
    ]
    status = ("candidate" if query_candidates else
              "inconclusive" if should_promote else "not_yet_analysed")
    return TieredDetectionResult(
        plan=selected_plan, detections=tuple(detections), status=status,
        promoted_because=promotion_reason if should_promote else None,
    )


def acquire_query_detections(
    answer: dict,
    query_plan: dict,
    *,
    source_digest: str,
    duration_seconds: float,
    ambiguity_windows: Sequence[dict],
    cached,
    producer,
    max_cost_seconds: float,
    cost_budget_seconds: float,
    window_seconds: float,
):
    """Promote object detection only in query-directed, budgeted windows.

    ``producer`` receives one bounded interval and is expected to call
    :func:`detect_image_tiered` for its sampled frames. The acquisition
    layer checks cache coverage, cost, memory ceiling and model version.
    """
    from library.tools.query_directed_acquisition import (
        execute_query_acquisition,
        plan_query_acquisition,
    )

    scope = query_plan.get("scope") or {}
    if ((scope.get("start_s") is None or scope.get("end_s") is None)
            and not ambiguity_windows):
        return {"schema_version": 1, "action": "no_run", "steps": [],
                "reason": "object detection promotion requires a named query window"}

    bounded_plan = plan_query_acquisition(
        answer,
        {**query_plan,
         "required_lanes": ["object_detection"]},
        source_digest=source_digest,
        duration_seconds=duration_seconds,
        resolvable_ambiguities=[
            {**window, "lane": "object_detection"}
            for window in ambiguity_windows
        ],
        available_lanes=("object_detection",),
        producer_budgets={"object_detection": cost_budget_seconds},
        window_seconds_by_lane={"object_detection": window_seconds},
        producer_cost_per_window_seconds={
            "object_detection": cost_budget_seconds,
        },
        max_cost_seconds=max_cost_seconds,
    )
    return execute_query_acquisition(
        bounded_plan,
        cached=cached,
        producers={"object_detection": producer},
        max_cost_seconds=max_cost_seconds,
    )
