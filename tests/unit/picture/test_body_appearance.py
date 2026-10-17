"""Unit tests for library/tools/body_appearance.py.

Pure synthetic numpy crops only: NO footage, NO model, NO downloads.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from library.tools.body_appearance import (
    STUDY_DIM,
    describe_frame_boxes,
    describe_person_crop,
    match,
)

RED = (0, 0, 255)  # BGR solid red
GREEN = (0, 255, 0)  # BGR solid green
BLUE = (255, 0, 0)  # BGR solid blue


def thirds_crop(top, mid, bot, h=90, w=60):
    """Synthetic person crop with one solid BGR color per horizontal third."""
    assert h % 3 == 0
    crop = np.zeros((h, w, 3), dtype=np.uint8)
    crop[0 : h // 3] = top
    crop[h // 3 : 2 * h // 3] = mid
    crop[2 * h // 3 : h] = bot
    return crop


def test_study_dim_is_384():
    assert STUDY_DIM == 384
    assert len(describe_person_crop(thirds_crop(RED, GREEN, BLUE))) == STUDY_DIM


def test_identical_crops_match_one():
    a = describe_person_crop(thirds_crop(RED, GREEN, BLUE))
    b = describe_person_crop(thirds_crop(RED, GREEN, BLUE).copy())
    assert match(a, b) == pytest.approx(1.0)


def test_red_vs_blue_torso_matches_low():
    red = describe_person_crop(thirds_crop(RED, RED, RED))
    blue = describe_person_crop(thirds_crop(BLUE, BLUE, BLUE))
    identical = match(red, describe_person_crop(thirds_crop(RED, RED, RED)))
    assert match(red, blue) < 0.9
    assert match(red, blue) < identical


def test_thirds_ordering_matters():
    base = describe_person_crop(thirds_crop(RED, GREEN, BLUE))
    same = describe_person_crop(thirds_crop(RED, GREEN, BLUE))
    swapped = describe_person_crop(thirds_crop(RED, BLUE, GREEN))
    assert match(base, swapped) < match(base, same)


def test_descriptor_is_l2_unit():
    vector = describe_person_crop(thirds_crop(RED, GREEN, BLUE))
    assert math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)


def test_match_rejects_shape_mismatch():
    with pytest.raises(ValueError):
        match((1.0, 2.0), (1.0, 2.0, 3.0))


@pytest.mark.parametrize(
    "bad",
    [
        None,
        np.zeros((0, 10, 3), dtype=np.uint8),
        np.zeros((10, 0, 3), dtype=np.uint8),
        np.zeros((10, 10), dtype=np.uint8),
    ],
)
def test_degenerate_crop_raises(bad):
    with pytest.raises(ValueError):
        describe_person_crop(bad)


def test_frame_boxes_clip_and_report_skipped():
    frame = thirds_crop(RED, GREEN, BLUE, h=90, w=60)
    boxes = [
        [0.0, 0.0, 1.0, 1.0],  # valid full frame
        [-0.5, -0.5, 1.5, 1.5],  # out of range, clips to full frame
        [0.5, 0.5, 0.5, 0.5],  # zero area, degenerate
        [0.8, 0.8, 0.2, 0.2],  # inverted, degenerate after clipping
    ]
    descriptors, skipped = describe_frame_boxes(frame, boxes)
    assert len(descriptors) == 2
    assert skipped == [2, 3]
    assert match(descriptors[0], descriptors[1]) == pytest.approx(1.0)


def test_scale_invariance_same_pattern():
    small = describe_person_crop(thirds_crop(RED, GREEN, BLUE, h=30, w=20))
    large = describe_person_crop(thirds_crop(RED, GREEN, BLUE, h=120, w=80))
    assert match(small, large) >= 0.99
