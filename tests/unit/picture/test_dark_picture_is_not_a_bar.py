"""A dark picture is not a black bar, and a composition is not a conform.

A bar carries no light at all (`BAR_ROW_MAX_LUMA`), so a flat graded
shadow is picture; a picture inset in black on all four sides is a
composition, never a conform; a near-black fade carries no geometry.
Measurements behind each bound:
`docs/RULE_EVIDENCE.md#a-dark-picture-is-not-a-black-bar`.
"""

import numpy as np
import pytest

from library.tools import render_qa
from library.tools.render_qa import (
    LIT_LUMA_THRESHOLD,
    _bar_rows,
    measure_frame_occupancy,
)

W, H = 1080, 1920

# The reference's own numbers at 1121.0s: a row mean of 8.56 to 9.33 and
# a within-row standard deviation of 0.88 to 1.97.
SHADOW_MEAN = 9.0
SHADOW_ROWS = 640


def _shadow_rows(rows: int, width: int = W):
    """Dark picture that is FLAT enough to have fooled the variance half."""
    y, x = np.mgrid[0:rows, 0:width].astype(np.float32)
    # +/- one luma level of structure, which is all an 8-bit shadow has.
    return SHADOW_MEAN + np.sin(x / 7.0) + np.sin(y / 11.0) * 0.4


def _dark_topped_picture():
    """A full-bleed frame whose top is a graded shadow. All picture."""
    luma = np.full((H, W), 120.0, dtype=np.float32)
    luma[:SHADOW_ROWS] = _shadow_rows(SHADOW_ROWS)
    return np.round(luma).astype(np.uint8)[None, :, :]


def _flat_black_bars(picture_rows: int, level: float = 0.0):
    """A conform letterbox: black bars, picture spanning the full width."""
    luma = np.full((H, W), level, dtype=np.float32)
    top = (H - picture_rows) // 2
    luma[top:top + picture_rows] = 120.0 + np.sin(
        np.mgrid[0:picture_rows, 0:W][1] / 7.0) * 20.0
    return np.round(luma).astype(np.uint8)[None, :, :]


def _inset_in_black(picture_rows: int, picture_cols: int):
    """A composition: a picture inset in black on all four sides."""
    luma = np.zeros((H, W), dtype=np.float32)
    top = (H - picture_rows) // 2
    left = (W - picture_cols) // 2
    luma[top:top + picture_rows, left:left + picture_cols] = (
        120.0 + np.sin(np.mgrid[0:picture_rows, 0:picture_cols][1] / 7.0) * 20.0)
    return np.round(luma).astype(np.uint8)[None, :, :]


def _measure(frames, spans=None, **kwargs):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_qa, "_probe_video_size", lambda p: (W, H))
        mp.setattr(render_qa, "_stream_raw_frames",
                   lambda *a, **k: iter(frames))
        return measure_frame_occupancy("master.mp4", framing_spans=spans,
                                       **kwargs)


# ── 1. a graded shadow is picture ──


def test_darkness_alone_would_still_eat_the_shadow():
    """Why the level bound and not a wider variance bound.

    Run the same rows under the threshold the walk used to take and they
    are consumed; under the level a bar really measures they are not.
    """
    shadow = _shadow_rows(SHADOW_ROWS)
    means, stds = shadow.mean(axis=1), shadow.std(axis=1)
    assert _bar_rows(means, stds, max_luma=LIT_LUMA_THRESHOLD) == SHADOW_ROWS
    assert _bar_rows(means, stds) == 0


# ── the bar itself is unchanged ──





# ── 2. a composition is not a conform ──

def test_a_picture_inset_in_black_on_four_sides_is_counted_out():
    lit = np.full((1, H, W), 140, dtype=np.uint8)
    result = _measure([lit] * 8 + [_inset_in_black(900, 600)] * 4)
    assert result.passed, result.detail
    assert result.value["inset_frames_skipped"] == 4
    assert result.value["frames_sampled"] == 8
    assert result.value["spread"] == 0.0
    assert "composition and not a conform" in result.detail




# ── a frame with no picture at all ──

def test_a_frame_that_is_black_but_not_zero_carries_no_geometry():
    """The black test is the pipeline's own, not the bar walk by proxy.

    A fade held at luma 2 is black to any viewer and to `blackdetect`,
    and the level bound means the bar walk no longer eats it - so the
    black predicate has to be asked directly or the fade reads as a full
    frame of picture and moves the spread.
    """
    lit = np.full((1, H, W), 140, dtype=np.uint8)
    nearly_black = np.full((1, H, W), 2, dtype=np.uint8)
    result = _measure([lit] * 8 + [nearly_black] * 3)
    assert result.passed, result.detail
    assert result.value["black_frames_skipped"] == 3
    assert result.value["frames_sampled"] == 8
