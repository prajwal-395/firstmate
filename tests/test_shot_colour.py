"""Per-shot colour measurement: what each graded shot IS in RGB.

Step 5.01 saw mean luma only and could not see a cast. On geo-podcast
camera A reads about 15% warmer than camera B on the same dark neutral
set, and luma has no number for that. `library/tools/shot_colour.py`
measures per-channel means plus the R/B and G/B balance on a detected
neutral region, or says plainly that nothing measured.

Each test names the defect it stops: a zeros row reading as measured
black, a whole-frame mean wearing a neutral's name, a cast hiding
inside content.
"""
import shutil
import subprocess

import numpy as np
import pytest

from library.tools import shot_colour as sc

FFMPEG = shutil.which("ffmpeg")


def _swatch(rgb, width=64, height=48):
    return (np.ones((height, width, 3), dtype=np.float64)
            * np.array(rgb, dtype=np.float64))


def test_neutral_of_balanced_grey_is_one():
    """A true grey reads R/B 1.0 and G/B 1.0 - the reference everything
    else is a distance from."""
    out = sc.neutral_of(_swatch((120, 120, 120)))
    assert out["absent"] is False
    assert out["neutral_rb"] == pytest.approx(1.0)
    assert out["neutral_gb"] == pytest.approx(1.0)
    assert out["neutral_fraction"] == pytest.approx(1.0)


def test_neutral_of_warm_cast_reports_the_cast():
    """A near-grey surface with a slight warm push reads R/B above 1:
    the number the colourist was never shown. A STRONGER cast stops
    being a reference at all (next test) - the floor between them is
    the rule, not a judgement."""
    out = sc.neutral_of(_swatch((123, 120, 117)))
    assert out["absent"] is False
    assert out["neutral_rb"] == pytest.approx(round(123 / 117, 4))
    assert out["neutral_gb"] == pytest.approx(round(120 / 117, 4))


def test_strong_cast_is_content_not_reference():
    """A wall 20% warm is a warm wall, not a misbalanced camera: its
    spread clears the neutral rule, so correcting it to grey would
    destroy the content, not the cast."""
    out = sc.neutral_of(_swatch((132, 120, 110)))
    assert out["absent"] is True


def test_neutral_ignores_saturated_content():
    """A saturated red field is content with a hue, not a reference: no
    neutral clears the floor, and the absence SAYS so rather than
    borrowing the whole-frame mean."""
    out = sc.neutral_of(_swatch((200, 20, 20)))
    assert out["absent"] is True
    assert out["neutral_rb"] is None
    assert "near-neutral" in out["reason"]


def test_neutral_ignores_crushed_blacks_and_clipped_whites():
    """Noise votes warm down in the blacks and there is nothing to read
    in clipped whites - both sit outside the band."""
    pixels = np.concatenate([_swatch((4, 3, 5)).reshape(-1, 3),
                             _swatch((250, 250, 250)).reshape(-1, 3)])
    out = sc.neutral_of(pixels)
    assert out["absent"] is True


def test_neutral_separates_wall_from_face():
    """The geo-podcast shape: a neutral wall behind warm content. The
    wall's balance survives the face beside it."""
    wall = _swatch((53, 52, 53), width=64, height=36)
    face = (np.ones((12, 64, 3)) * np.array([150, 100, 80])).astype(float)
    pixels = np.concatenate([wall.reshape(-1, 3),
                             face.reshape(-1, 3)])
    out = sc.neutral_of(pixels)
    assert out["absent"] is False
    assert out["neutral_rb"] == pytest.approx(1.0, abs=0.02)
    assert out["neutral_fraction"] == pytest.approx(0.75, abs=0.01)


def test_unmeasured_is_absent_never_zeros():
    """A missing file reports the absence. A zeros row would read as
    measured black, which balances to nothing."""
    out = sc.measure_shot_colour("/no/such/file.mov")
    assert out["mean_rgb"] is None
    assert out["neutral_rb"] is None
    assert out["colour_method"] == sc.COLOUR_UNMEASURED
    assert "not on disk" in out["colour_unmeasured_because"]


def test_empty_source_is_absent():
    out = sc.measure_shot_colour("")
    assert out["mean_rgb"] is None
    assert out["colour_method"] == sc.COLOUR_UNMEASURED


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_measured_video_reports_means_and_neutral(tmp_path):
    """End to end off a generated file: a warm grey field measures warm
    on both the whole frame and the neutral, with the method stated."""
    src = str(tmp_path / "warm.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x787673:s=160x120:d=12",
         "-pix_fmt", "yuv420p", src],
        check=True)
    out = sc.measure_shot_colour(src)
    assert out["colour_method"] == sc.COLOUR_METHOD
    assert out["colour_samples"] >= 1
    assert out["mean_rgb"][0] > out["mean_rgb"][2]
    assert out["rb_all"] == pytest.approx(
        round(out["mean_rgb"][0] / out["mean_rgb"][2], 4))
    assert out["neutral_rb"] is not None
    assert out["neutral_rb"] > 1.0
    assert "colour_unmeasured_because" not in out


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_extract_still_draws_inside_the_sampled_span(tmp_path):
    """The still the colourist sees is of the seconds the numbers
    describe - and a still that was not drawn is False, never a
    zero-byte file."""
    src = str(tmp_path / "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=s=160x120:d=12",
         "-pix_fmt", "yuv420p", src],
        check=True)
    still = str(tmp_path / "still.jpg")
    assert sc.extract_still(src, still) is True
    assert tmp_path.joinpath("still.jpg").stat().st_size > 0
    assert sc.extract_still(str(tmp_path / "missing.mp4"),
                            str(tmp_path / "nope.jpg")) is False
    assert not (tmp_path / "nope.jpg").exists()
