"""The subtitle QA gate must look at frames that have captions on them.

A subtitle overlay is transparent between captions. The gate used to
sample two fixed instants - 0.5s and 1.5s into the segment - and on
project 001's `sub_block_10` both land in an ordinary pause: the first
caption is the single word "i", ending 0.70s in, and the next arrives at
1.58s. The vision model was shown two blank frames and answered, quite
correctly, that they were blank - failing a render whose subtitles were
fine. On an earlier run the same model PASSED the same two blank frames,
which is the worse half: the gate's verdict had nothing to do with the
subtitles either way.

So the frames are chosen by measuring the overlay's own alpha channel.
These tests build real overlays with ffmpeg and check both directions:
a segment with a gap is sampled where the ink is, and a segment with no
ink at all still fails.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "library"))

from tools.qa.subtitle_qa import (  # noqa: E402
    ALPHA_INK_THRESHOLD,
    check_caption_geometry,
    find_inked_timestamps,
    run_subtitle_qa,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _render_overlay(path: Path, draw_expr: str, seconds: float = 4.0) -> Path:
    """A 4s transparent 216x384 overlay, drawing a white box when `draw_expr`.

    Small frame and prores4444 so the alpha channel survives, which is
    what the probe reads.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"color=c=black@0.0:s=216x384:d={seconds}:r=30,format=yuva444p",
            "-vf",
            f"drawbox=x=40:y=300:w=136:h=40:color=white@1.0:t=fill:replace=1"
            f":enable='{draw_expr}'",
            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
            str(path),
        ],
        capture_output=True, check=True,
    )
    return path


def test_a_gap_is_not_sampled(tmp_path):
    """Ink only in the last second - the probe must find it there."""
    mov = _render_overlay(tmp_path / "gappy.mov", "between(t,3.0,4.0)")
    found = find_inked_timestamps(str(mov), 4.0, count=2)
    assert found, "the probe found no captions in a segment that has them"
    assert all(t >= 2.8 for t in found), (
        f"sampled {found}, but the only caption is after 3.0s - the gate "
        f"would be judging blank frames again"
    )


def test_the_old_fixed_timestamps_would_have_missed_it(tmp_path):
    """The regression, stated directly: 0.5s and 1.5s are both blank here."""
    mov = _render_overlay(tmp_path / "gappy.mov", "between(t,3.0,4.0)")
    from tools.qa.subtitle_qa import _probe_alpha

    assert _probe_alpha(str(mov), 0.5) < ALPHA_INK_THRESHOLD
    assert _probe_alpha(str(mov), 1.5) < ALPHA_INK_THRESHOLD
    assert _probe_alpha(str(mov), 3.5) >= ALPHA_INK_THRESHOLD


def test_an_empty_overlay_still_fails(tmp_path, monkeypatch):
    """The thing the gate exists to catch must still be caught.

    No ink anywhere is a blank overlay, not a pause, and it fails before
    the vision model is consulted at all.
    """
    mov = _render_overlay(tmp_path / "blank.mov", "between(t,99,100)")
    assert find_inked_timestamps(str(mov), 4.0, count=2) == []

    monkeypatch.delenv("SKIP_QA_CHECKS", raising=False)
    with pytest.raises(RuntimeError, match="draws nothing anywhere"):
        run_subtitle_qa(str(mov), str(tmp_path))


def test_a_fully_inked_overlay_is_sampled_anywhere(tmp_path):
    mov = _render_overlay(tmp_path / "solid.mov", "gte(t,0)")
    found = find_inked_timestamps(str(mov), 4.0, count=2)
    assert len(found) == 2


# ── The mechanical half is what decides ───────────────────────────────


def _frame(tmp_path: Path, box: tuple, size=(1080, 1920)) -> Path:
    """An RGBA frame with one opaque white box at `box` and nothing else."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle(box, fill=(255, 255, 255, 255))
    path = tmp_path / f"frame_{box[0]}_{box[1]}.png"
    im.save(path)
    return path


def test_a_normal_caption_passes_geometry(tmp_path):
    """The real thing the model called clipped and distorted.

    "casey neistat," measured at alpha bbox (288, 1694, 776, 1770) in a
    1080x1920 frame - 150 clear rows below the type.
    """
    assert check_caption_geometry(str(_frame(tmp_path, (288, 1694, 776, 1770)))) == []


def test_a_caption_clipped_at_the_bottom_fails(tmp_path):
    problems = check_caption_geometry(str(_frame(tmp_path, (288, 1860, 776, 1919))))
    assert any("bottom" in p for p in problems), problems


def test_a_caption_clipped_at_the_left_fails(tmp_path):
    problems = check_caption_geometry(str(_frame(tmp_path, (2, 1694, 776, 1770))))
    assert any("left" in p for p in problems), problems


def test_a_caption_in_the_upper_half_fails(tmp_path):
    problems = check_caption_geometry(str(_frame(tmp_path, (288, 200, 776, 280))))
    assert any("upper" in p for p in problems), problems


def test_a_blank_frame_fails_geometry(tmp_path):
    from PIL import Image

    path = tmp_path / "blank.png"
    Image.new("RGBA", (1080, 1920), (0, 0, 0, 0)).save(path)
    problems = check_caption_geometry(str(path))
    assert any("draws nothing" in p for p in problems), problems


def test_the_vision_verdict_does_not_block(tmp_path, monkeypatch):
    """A FAIL from the model is recorded, not enforced.

    Measured 2026-08-20: gemma-4-12b-it-4bit answered FAIL three times out
    of three on the clean "casey neistat," frame, claiming the letters
    were "overlapping and distorted". Blocking a render on that is not
    coverage.
    """
    import tools.qa.subtitle_qa as qa

    mov = _render_overlay(tmp_path / "solid.mov", "gte(t,0)")
    monkeypatch.setattr(
        qa, "check_caption_geometry", lambda _path: [])
    monkeypatch.setattr(
        qa, "_vision_observation",
        lambda _paths: "FAIL\nthe letters are overlapping and distorted")

    result = qa.run_subtitle_qa(str(mov), str(tmp_path))
    assert result["passed"] is True
    assert "overlapping" in result["vision_observation"]
