"""The native renderer draws the project's own cards as it shipped them.

The recorded measurement over the project's 86 shipped motion-graphics
cards is in `tests/fixtures/native_render_parity/cards.json`: the card
the engine the gate selects draws today, against the artefact the
project actually shipped, at an ink IoU of 0.90.

This test is the live half of that record: it re-renders a sample of
the project's own cards with the native renderer and compares the INK
with the shipped artefact's ink mask, frame by frame, at the same
floor. The stored frames ARE the artefact's ink mask (alpha > 16) -
the exact quantity the comparison consumes.

It runs the native renderer, so it is marked `native_renderer` and runs
outside the correctness gates (`pyproject.toml`).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures"
           / "native_render_parity")
INK_FLOOR = 16
"""The alpha at which a pixel counts as ink (the measured metric's floor)."""
PARITY_FLOOR = 0.90
"""The ink IoU a native render must reach against the shipped artefact."""


def _ink(path: Path) -> np.ndarray:
    """One frame's ink mask."""
    with Image.open(path) as image:
        alpha = np.asarray(image.convert("RGBA"))[:, :, 3]
    return alpha > INK_FLOOR


def _stored_ink(path: Path) -> np.ndarray:
    """One stored artefact ink mask."""
    with Image.open(path) as image:
        return np.asarray(image.convert("L")) > 127


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    union = np.logical_or(left, right).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(left, right).sum()) / float(union)


def _fframes_available() -> bool:
    from library.tools import fframes_render

    try:
        usable, _ = fframes_render.fframes_available()
    except Exception:  # noqa: BLE001 - the binary may be absent entirely
        return False
    return bool(usable)


@pytest.mark.native_renderer
@pytest.mark.skipif(not _fframes_available(),
                    reason="FFrames binary not available")
def test_the_native_render_matches_the_shipped_artefact(tmp_path):
    """Each sampled card's native render reaches the parity floor.

    The floor is the same 0.90 the population was measured at, and the
    comparison is the same one: ink against the artefact the project
    shipped.
    """
    from library.tools import fframes_render

    fixture = json.loads((FIXTURE / "cards.json").read_text(encoding="utf-8"))
    samples = fixture["sample_cards"]
    assert samples, "the fixture records no sample cards"

    for card in samples:
        props = json.loads(
            (FIXTURE / "props" / f"{card}.json").read_text(encoding="utf-8"))
        rendered = fframes_render.render_one_card_frames(
            "MotionGraphics", props, str(tmp_path / card),
            str(tmp_path / "work"), "")
        frames = rendered["frames"]
        masks = sorted((FIXTURE / "frames").glob(f"{card}_*.png"))
        assert masks, f"{card} has no stored artefact frame"

        for mask_path in masks:
            number = int(mask_path.stem.rsplit("_", 1)[1])
            frame = _ink(Path(frames[number - 1]))
            score = _iou(frame, _stored_ink(mask_path))
            assert score >= PARITY_FLOOR, (
                f"{card} frame {number}: ink IoU {score:.3f} against the "
                f"shipped artefact is below {PARITY_FLOOR}")
