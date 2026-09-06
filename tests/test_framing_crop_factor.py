"""Tests for framing_crop_factor: the additional zoom beyond fill.

``tests/test_framing_crop_factor.py``.
"""

from __future__ import annotations

import pytest

from library.tools.framing_intent import (
    DEFAULT_CROP_FACTOR,
    validate_crop_factor,
    resolve_crop_factor,
)
from library.steps.step_5_04_compile_manifest.step import _conform_fields


# ── Shared fixtures ──────────────────────────────────────────────────

LANDSCAPE = {"clip_001": {"width": 1920, "height": 1080}}
LANDSCAPE_4K = {"clip_001": {"width": 3840, "height": 2160}}
VERTICAL = (1080, 1920)  # 9:16 delivery


def _conform(clip_meta=None, proj_res=None, **kwargs):
    """Shorthand for _conform_fields with defaults."""
    return _conform_fields(
        clip_meta or LANDSCAPE, "clip_001",
        proj_res or VERTICAL, **kwargs)


# ── validate_crop_factor ─────────────────────────────────────────────

class TestValidateCropFactor:

    def test_default_is_valid(self):
        assert validate_crop_factor(1.0, "test") == 1.0

    def test_max_is_valid(self):
        assert validate_crop_factor(2.0, "test") == 2.0

    def test_below_minimum_raises(self):
        with pytest.raises(ValueError, match="must be between"):
            validate_crop_factor(0.5, "test")

    def test_above_maximum_raises(self):
        with pytest.raises(ValueError, match="must be between"):
            validate_crop_factor(2.5, "test")

    def test_string_raises(self):
        with pytest.raises(TypeError, match="must be a number"):
            validate_crop_factor("tight", "test")

    def test_boolean_raises(self):
        with pytest.raises(TypeError, match="must be a number"):
            validate_crop_factor(True, "test")

    def test_1_3_is_valid(self):
        """A typical podcast tightening factor."""
        assert validate_crop_factor(1.3, "test") == 1.3


# ── resolve_crop_factor ──────────────────────────────────────────────

class TestResolveCropFactor:

    def test_default_when_nothing_declared(self):
        assert resolve_crop_factor() == DEFAULT_CROP_FACTOR

    def test_block_level_wins(self):
        assert resolve_crop_factor(block_crop_factor=1.4) == 1.4

    def test_block_level_raises_on_invalid(self):
        with pytest.raises(ValueError):
            resolve_crop_factor(block_crop_factor=0.5)


# ── _conform_fields with crop factor ─────────────────────────────────

class TestConformFieldsCropFactor:

    def test_no_crop_factor_gives_standard_fill(self):
        """Without a crop factor, fill zoom is the standard ratio."""
        result = _conform(framing_intent=1.0)
        assert result.get("needs_conform") is True
        # Standard landscape 1920x1080 into 1080x1920:
        # fit_scale = min(1080/1920, 1920/1080) = 0.5625
        # fill_scale = max(1080/1920, 1920/1080) = 1.7778
        # max_zoom = fill_scale / fit_scale = 3.1605
        # zoom at intent 1.0 = 1.0 + (3.1605 - 1.0) * 1.0 = 3.1605
        expected_max_zoom = round(
            max(1080 / 1920, 1920 / 1080) /
            min(1080 / 1920, 1920 / 1080), 4)
        assert result["fill_zoom"] == expected_max_zoom
        assert "framing_crop_factor" not in result

    def test_crop_factor_1_0_same_as_none(self):
        """A crop factor of 1.0 is equivalent to no crop factor."""
        result_none = _conform(framing_intent=1.0)
        result_1_0 = _conform(framing_intent=1.0, framing_crop_factor=1.0)
        assert result_none["fill_zoom"] == result_1_0["fill_zoom"]
        assert "framing_crop_factor" not in result_1_0

    def test_crop_factor_increases_zoom(self):
        """A crop factor > 1.0 zooms in tighter."""
        result_base = _conform(framing_intent=1.0)
        result_tight = _conform(framing_intent=1.0, framing_crop_factor=1.3)
        assert result_tight["fill_zoom"] > result_base["fill_zoom"]
        # Zoom should be base_zoom * 1.3
        expected = round(result_base["fill_zoom"] * 1.3, 4)
        assert result_tight["fill_zoom"] == expected
        assert result_tight["framing_crop_factor"] == 1.3

    def test_crop_factor_with_4k_landscape(self):
        """4K landscape (3840x2160) into 9:16 with crop factor."""
        result_base = _conform(clip_meta=LANDSCAPE_4K, framing_intent=1.0)
        result_tight = _conform(clip_meta=LANDSCAPE_4K, framing_intent=1.0,
                                framing_crop_factor=1.3)
        assert result_tight["fill_zoom"] > result_base["fill_zoom"]
        assert result_tight["framing_crop_factor"] == 1.3

    def test_crop_factor_with_letterbox_intent_is_noop(self):
        """A crop factor has no effect when framing_intent is 0.0
        (full letterbox)."""
        result = _conform(framing_intent=0.0, framing_crop_factor=1.3)
        assert result.get("needs_conform") is False
        assert "framing_crop_factor" not in result

    def test_crop_factor_recorded_on_clip(self):
        """The crop factor is recorded in the clip dict so downstream
        knows what was applied."""
        result = _conform(framing_intent=1.0, framing_crop_factor=1.5)
        assert result["framing_crop_factor"] == 1.5

    def test_crop_factor_still_triggers_backdrop(self):
        """A crop factor that would cut a wide subject still triggers
        the backdrop route. The subject-safety check fires AFTER the
        crop factor is applied."""
        # A subject occupying 80% of the source width:
        # At standard fill (3.16x), the visible column is 1/3.16 = 0.316
        # of the source width. 0.80 * (1 + 2*0.15) = 1.04 > 0.316, so
        # backdrop fires even without crop factor.
        result = _conform(framing_intent=1.0, framing_crop_factor=1.3,
                          subject_width=0.8)
        # Should have backdrop route
        assert "framing_backdrop" in result

    def test_crop_factor_clamped_to_max(self):
        """A crop factor above MAX_CROP_FACTOR is clamped."""
        result = _conform(framing_intent=1.0, framing_crop_factor=3.0)
        # Should be clamped to 2.0 in _conform_fields
        result_at_max = _conform(framing_intent=1.0, framing_crop_factor=2.0)
        assert result["fill_zoom"] == result_at_max["fill_zoom"]

    def test_crop_factor_clamped_below_1(self):
        """A crop factor below 1.0 is clamped to 1.0."""
        result_base = _conform(framing_intent=1.0)
        result_below = _conform(framing_intent=1.0, framing_crop_factor=0.8)
        assert result_below["fill_zoom"] == result_base["fill_zoom"]

    def test_partial_intent_with_crop_factor(self):
        """Crop factor works with partial framing intent."""
        result = _conform(framing_intent=0.5, framing_crop_factor=1.3)
        assert result.get("needs_conform") is True
        # Verify it's tighter than without crop factor
        result_base = _conform(framing_intent=0.5)
        assert result["fill_zoom"] > result_base["fill_zoom"]
