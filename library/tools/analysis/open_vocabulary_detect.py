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

Heavy models load lazily and once per process; a backend whose
package or weights are missing REFUSES (`DetectorNotAvailable`) with
the reason, never an empty list - an empty list means "measured,
nothing there". Every detection is a normalised top-left
`[x0, y0, x1, y1]` box in 0..1, the space Gemma ground truth and the
1.06 seeder already speak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

DETECTORS = ("vision_foreground", "florence2", "owlv2", "grounding_dino")
"""Every open-vocabulary detector the bakeoff scores, in doc order."""

VISION_FOREGROUND = "vision_foreground"
FLORENCE2 = "florence2"
OWLV2 = "owlv2"
GROUNDING_DINO = "grounding_dino"

FLORENCE2_MODEL_ID = "microsoft/Florence-2-base"
OWLV2_MODEL_ID = "google/owlv2-base-patch16-ensemble"
GROUNDING_DINO_MODEL_ID = "IDEA-Research/grounding-dino-tiny"

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
    except Exception as exc:  # noqa: BLE001 - missing weights, no
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
        model = Owlv2ForObjectDetection.from_pretrained(OWLV2_MODEL_ID)
    except Exception as exc:  # noqa: BLE001 - see _load_florence2.
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


def _load_grounding_dino() -> dict[str, Any]:
    _require_transformers()
    try:
        from transformers import (AutoModelForZeroShotObjectDetection,
                                  AutoProcessor)
    except ImportError as exc:
        raise DetectorNotAvailable(
            "this transformers version has no zero-shot detection "
            "classes, so Grounding DINO cannot run here") from exc
    device = _torch_device()
    try:
        processor = AutoProcessor.from_pretrained(GROUNDING_DINO_MODEL_ID)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(
            GROUNDING_DINO_MODEL_ID)
    except Exception as exc:  # noqa: BLE001 - see _load_florence2.
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
    return _LOADED[name]


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
    from library.tools import vision_measure

    import os
    import tempfile

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


def _match_phrase(label: str, phrases: list[str]) -> str:
    """A detector's raw label back to the queried phrase: exact
    case-insensitive match first, then the queried phrase the label
    contains (or that contains the label). Unmatched labels keep their
    own text rather than being forced onto a phrase - a forced match
    would score recall for words nobody asked about."""
    lowered = label.strip().lower()
    for phrase in phrases:
        if phrase.lower() == lowered:
            return phrase
    for phrase in phrases:
        if lowered in phrase.lower() or phrase.lower() in lowered:
            return phrase
    return label


def _detect_owlv2(image_path: str, phrases: list[str],
                  score_threshold: float) -> list[Detection]:
    import torch

    loaded = load_detector(OWLV2)
    processor, model, device = (loaded["processor"], loaded["model"],
                                loaded["device"])
    image, (width, height) = _read_image(image_path)
    post = getattr(processor, "post_process_grounded_object_detection",
                   None)
    if post is None:
        raise DetectorNotAvailable(
            "this transformers version answers no "
            "post_process_grounded_object_detection on OWLv2, so its "
            "boxes cannot be read")
    inputs = processor(text=[phrases], images=image, return_tensors="pt")
    inputs = {k: (v.to(device) if hasattr(v, "to") else v)
              for k, v in inputs.items()}
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
            phrase = _match_phrase(str(texts[i]), phrases)
        else:
            try:
                phrase = phrases[int(label)]
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
    return _detect_grounding_dino(image_path, clean, score_threshold)
