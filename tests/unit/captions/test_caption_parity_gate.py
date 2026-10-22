"""The caption parity gate: a re-render may not change the picture that shipped.

THE DEFECT THIS NAMES.  The caption render's reuse key names the
engine, so switching engines re-renders every card OVER the artefact
the project shipped - the filename carries the drawing digest, not the
engine.  Nothing compared the two pictures, so the engine that drew the
new card could differ from the engine that drew the old one, and the
delivered video changed without a word in the run's record.

THE GATE.  The native renderer draws to a candidate, the candidate is
compared against the shipped artefact by INK (the alpha plane, mean
per-frame IoU, floor 0.90), and the card is published only if the two
agree to the floor.  A card below the floor is REFUSED: the shipped
artefact stays and the new pixels are discarded.

These tests build their artefacts with ffmpeg (a white box, drawn
where the test says), so the measure is exercised on real pixels rather
than on a mock of the measure.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools import caption_parity_gate as gate


def _ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


requires_ffmpeg = pytest.mark.skipif(not _ffmpeg(),
                                     reason="ffmpeg is not on PATH")


def _draw(path: Path, box: tuple[int, int, int, int], size=(64, 64),
          frames: int = 2) -> str:
    """Write a caption artefact: a white box on a transparent canvas.

    ``box`` is ``(x, y, width, height)`` of the ink. The canvas is
    transparent everywhere else, so the alpha plane IS the ink - which
    is the plane the gate measures.
    """
    import numpy as np

    x, y, w, h = box
    width, height = size
    canvas = np.zeros((frames, height, width, 4), dtype=np.uint8)
    canvas[:, y:y + h, x:x + w] = (255, 255, 255, 255)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
         "-s", f"{width}x{height}", "-r", "25", "-i", "-",
         "-c:v", "qtrle", "-pix_fmt", "argb", str(path)],
        input=canvas.tobytes(), check=True, capture_output=True)
    return str(path)


class _Engine:
    """A renderer that draws one fixed box, whatever it is asked for."""

    def __init__(self, box):
        self.box = box
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append(overlay_path)
        _draw(Path(overlay_path), self.box)
        return True, ""

    def close(self):
        pass


@requires_ffmpeg
def test_a_card_that_draws_a_different_picture_is_refused(tmp_path):
    """The defect: the native render would replace the shipped picture.

    The candidate draws its box somewhere else entirely, so the two
    pictures share no ink. The gate must refuse the card and leave the
    artefact that shipped exactly where it was.
    """
    shipped = tmp_path / "card.mov"
    _draw(shipped, (0, 0, 16, 16))
    reference = tmp_path / "reference.mov"
    shutil.copy2(shipped, reference)

    engine = gate.gated(_Engine((40, 40, 16, 16)))
    ok, error = engine.render(str(tmp_path / "props.json"), str(shipped))

    assert ok, error
    kept = gate.compare_artifacts(str(shipped), str(reference))
    assert kept.mean_iou == 1.0, \
        "the gate published a card that changed the picture that shipped"
    assert len(engine.refusals) == 1
    refusal = engine.refusals[0]
    assert refusal.result.mean_iou < gate.PARITY_FLOOR
    assert refusal.result.frames == 2
    assert "shipped artefact stays" in refusal.reason


@requires_ffmpeg
def test_a_card_that_draws_the_same_picture_is_published(tmp_path):
    """A card within the floor is published: the gate is not a block."""
    shipped = tmp_path / "card.mov"
    _draw(shipped, (8, 8, 24, 24))

    engine = gate.gated(_Engine((8, 8, 24, 24)))
    ok, error = engine.render(str(tmp_path / "props.json"), str(shipped))

    assert ok, error
    assert not engine.refusals
    assert shipped.is_file()


@requires_ffmpeg
def test_a_card_with_nothing_shipped_is_published(tmp_path):
    """Nothing to compare against: the card is published as drawn."""
    target = tmp_path / "card.mov"
    engine = gate.gated(_Engine((8, 8, 24, 24)))
    ok, error = engine.render(str(tmp_path / "props.json"), str(target))

    assert ok, error
    assert target.is_file()
    assert not engine.refusals


@requires_ffmpeg
def test_the_measure_is_the_mean_ink_iou_over_the_frames(tmp_path):
    """The measure is the one the motion-graphics parity used.

    Mean per-frame ink IoU at alpha > 16, and the floor is 0.90 - the
    same floor the project's own shipped motion-graphics cards are held
    to, so the two measurements are comparable.
    """
    same = tmp_path / "a.mov"
    other = tmp_path / "b.mov"
    _draw(same, (8, 8, 24, 24))
    _draw(other, (12, 8, 24, 24))

    result = gate.compare_artifacts(str(same), str(other))
    assert result.frames == 2
    assert 0.0 < result.mean_iou < 1.0
    assert result.min_iou <= result.mean_iou
    assert not result.within_floor
    assert result.as_record()["floor"] == 0.90

    identical = gate.compare_artifacts(str(same), str(same))
    assert identical.mean_iou == 1.0
    assert identical.within_floor


@requires_ffmpeg
def test_an_unreadable_artefact_is_not_a_pass(tmp_path):
    """Absent evidence is not a pass: the measure raises, it does not guess."""
    shipped = tmp_path / "card.mov"
    _draw(shipped, (8, 8, 24, 24))
    broken = tmp_path / "broken.mov"
    broken.write_bytes(b"not a movie")

    with pytest.raises(gate.ParityUnreadable):
        gate.compare_artifacts(str(broken), str(shipped))
    assert gate.measured_against(str(broken), str(shipped)) is None


@requires_ffmpeg
def test_the_step_records_a_refusal_and_keeps_the_shipped_card(tmp_path):
    """The refusal is in the segment's own record, and the card stays.

    The step publishes what the engine drew; a card the gate refused is
    not published, and the record says so - a reader who finds the
    shipped card in place must be able to see that the native render
    was refused, not assume it was drawn.
    """
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.unit.captions.test_overlay_carriage import _props

    first = render_one_segment(_props(), str(tmp_path), "tl",
                                remotion_dir="/none",
                                renderer=gate.gated(_Engine((8, 8, 24, 24))),
                                overlay_geometry="full")
    assert first["provenance"] == RENDERED
    assert first["parity_gate"] == []

    second = render_one_segment(_props(), str(tmp_path), "tl",
                                remotion_dir="/none",
                                renderer=gate.gated(_Engine((40, 40, 24, 24))),
                                overlay_geometry="full")
    assert second["provenance"] == RENDERED
    assert second["parity_gate"], \
        "the refusal the gate recorded is not in the segment's record"
    refusal = second["parity_gate"][0]
    assert refusal["within_floor"] is False
    assert refusal["mean_iou"] < gate.PARITY_FLOOR


def test_the_native_caption_engine_is_behind_the_gate(monkeypatch):
    """The wiring: the engine the caption layer draws with is gated.

    The native renderer is the default graphics engine, and the caption
    render's reuse key names the engine - so switching engines
    re-renders every card over the artefact the project shipped.  The
    gate is what stands between that and the delivered video.
    """
    from library.steps.step_4_05_render_subtitles import step as caption_step

    monkeypatch.setattr(caption_step._engines, "is_fframes",
                        lambda project_folder=None: True)
    engine = caption_step._default_unit_engine(
        str(caption_step.REMOTION_DIR), "video", "")
    assert isinstance(engine, gate.GatedRenderer), (
        "the native caption engine is not behind the parity gate")


@requires_ffmpeg
def test_the_gate_never_destroys_the_artefact_that_shipped(tmp_path):
    """The shipped artefact is not at risk, not even for a second.

    The candidate is drawn to its own path and only replaces the
    shipped file once the comparison has passed.
    """
    shipped = tmp_path / "card.mov"
    _draw(shipped, (8, 8, 24, 24))
    reference = tmp_path / "reference.mov"
    shutil.copy2(shipped, reference)

    engine = gate.gated(_Engine((8, 8, 24, 24)))
    engine.render(str(tmp_path / "props.json"), str(shipped))

    leftovers = [name for name in os.listdir(tmp_path)
                 if ".parity_candidate" in name]
    assert not leftovers, f"the gate left {leftovers} behind"
    kept = gate.compare_artifacts(str(shipped), str(reference))
    assert kept.mean_iou == 1.0, \
        "the gate published a card that changed the picture that shipped"
