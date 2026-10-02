from types import SimpleNamespace

import numpy as np

from prototypes.depth_blur.build_depth_blur import (
    even,
    foreground_alpha,
    normalize_depth,
)


def test_normalize_depth_uses_one_pooled_2nd_to_98th_percentile_range():
    depths = np.arange(100, dtype=np.float32).reshape(2, 5, 10)

    normalized, low, high = normalize_depth(depths)

    assert low == np.percentile(depths, 2)
    assert high == np.percentile(depths, 98)
    assert normalized.shape == depths.shape
    assert normalized.min() == 0.0
    assert normalized.max() == 1.0


def test_foreground_alpha_softens_only_the_relative_depth_transition():
    args = SimpleNamespace(
        foreground_center=0.5,
        foreground_feather=0.4,
        mask_softness_px=0.0,
    )
    normalized_depth = np.array([[0.3, 0.5, 0.7]], dtype=np.float32)

    alpha = foreground_alpha(normalized_depth, args, (3, 1))

    np.testing.assert_allclose(alpha.ravel(), [0.0, 0.5, 1.0], atol=1e-6)


def test_even_resolution_helper_reduces_odd_dimension():
    assert even(960) == 960
    assert even(539) == 538
