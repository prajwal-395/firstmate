"""Read the TEXT a delivered frame carries, through Apple Vision.

One purpose: judge a caption on the delivered file's pixels. The
delivered file is the artefact the captain receives; a caption that is
correct in the plan and absent from the export is a defect no plan-side
check can see.

Why a compiled Swift helper, and why this shape
------------------------------------------------
`library/tools/vision_helper.swift` already established the pattern in
this repo: a compiled helper, one `--list` protocol, one batch per
call, a binary cached by source hash in the system temp, and a NAMED
absence (`VisionUnavailable`) when the machine cannot run it. This
module reuses that machinery rather than writing a second compiler
cache and a second batch runner - `vision_measure.ensure_helper` and
`vision_measure.measure_frames` are the two calls, pointed at this
module's own source.

LANGUAGE CORRECTION IS OFF (in the helper). Vision's corrector rewrites
what it read into what it thinks was meant; a caption check compares the
delivered pixels against the intended WORDS, so a corrector would turn a
delivered typo into a passing check.

What it returns
---------------
`read_texts(paths)` returns, per image, the list of text observations
Vision found: `{"text", "confidence", "bbox"}`, where `bbox` is
`[x, y, width, height]` in NORMALISED coordinates with a top-left
origin. A frame with no text returns an empty list - an empty list is
a measurement ("nothing was read here"), never an error.

The absence is a first-class answer: `CaptionOcrUnavailable` carries the
reason, and the caller reports it. An unmeasured caption is not a
passing caption.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Sequence

from library.tools import vision_measure

SWIFT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "caption_ocr_helper.swift")

HELPER_NAME = "caption_ocr_helper"


class CaptionOcrUnavailable(Exception):
    """The OCR helper cannot run here; the message is the reason."""


def helper_available() -> tuple:
    """Whether the OCR helper can run on this machine, and why not.

    Returns `(True, None)` or `(False, reason)`.
    """
    return vision_measure.helper_available(SWIFT_SOURCE, HELPER_NAME)


def ensure_helper() -> tuple:
    """A runnable helper binary path, compiling on first use.

    Returns `(path, None)` or `(None, reason)`. Never raises: a failed
    compile is an absence, not a failed run.
    """
    return vision_measure.ensure_helper(SWIFT_SOURCE, HELPER_NAME)


def read_texts(image_paths: Sequence[str]) -> List[List[Dict[str, Any]]]:
    """The text observations in each image, in input order.

    Raises `CaptionOcrUnavailable` when the helper cannot run or does
    not answer one doc per image. The caller converts that into a
    recorded absence.
    """
    paths = [str(path) for path in image_paths]
    if not paths:
        return []
    binary, reason = ensure_helper()
    if not binary:
        raise CaptionOcrUnavailable(reason or "unavailable: no helper")
    try:
        docs = vision_measure.measure_frames(paths, binary)
    except vision_measure.VisionUnavailable as exc:
        raise CaptionOcrUnavailable(str(exc)) from None
    out: List[List[Dict[str, Any]]] = []
    for doc in docs:
        texts = []
        for observation in (doc or {}).get("texts") or []:
            bbox = observation.get("bbox") or []
            if len(bbox) != 4:
                continue
            texts.append({
                "text": str(observation.get("text") or ""),
                "confidence": float(observation.get("confidence") or 0.0),
                "bbox": [float(value) for value in bbox],
            })
        out.append(texts)
    return out


__all__ = [
    "CaptionOcrUnavailable",
    "ensure_helper",
    "helper_available",
    "read_texts",
]
