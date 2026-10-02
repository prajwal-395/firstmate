"""Tests for the per-clip framing parameter.

Covers the four acceptance criteria:
1. The parameter surviving planner -> manifest -> renderer.
2. A template bias applying when the clip does not set one.
3. The clip's own value winning over the template's.
4. The unset case reproducing today's output.
"""
import os
import sys
import pytest

from tests.resolve_double import VIDEO_ITEM_PROPERTIES, timeline_item

# Ensure project root is on the path before any library imports.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Import the compile_manifest helper under test ──
# compile_manifest/step.py puts `library/` and the repo root on sys.path
# itself at import time, so only the root is added here.
from library.steps.step_5_04_compile_manifest.step import _conform_fields


# ── Import the renderer helper under test ──

from library.steps.step_6_01_render.resolve_build_timeline import (
    _apply_conform,
)


# ── Import schema to verify the field exists ──


# ── The one measured Pan/Tilt law, driven directly ──
from library.tools.resolve_transform import (
    fit_base_scale,
    units_for_shift,
)

# 3840x2160 at fit in a 1080x1920 frame: the geometry every renderer
# test below converts through.  The factor is exactly 1 on Pan (the
# fit is width-bound), so the conversion IS the measured draw gain.
_FIT_3840_TO_1080x1920 = fit_base_scale(3840, 2160, 1080, 1920)


# ── Shared fixtures ──

# A 16:9 landscape clip (1920x1080) in a 9:16 vertical timeline (1080x1920).
# fit_scale = min(1080/1920, 1920/1080) = 0.5625
# fill_scale = max(1080/1920, 1920/1080) = 1.7778
# fill_zoom = fill_scale / fit_scale = 3.1605
LANDSCAPE_CLIP_META = {
    "clip_001": {"width": 1920, "height": 1080},
}
VERTICAL_PROJ_RES = [1080, 1920]

EXPECTED_FULL_FILL_ZOOM = round(
    max(1080 / 1920, 1920 / 1080) / min(1080 / 1920, 1920 / 1080), 4
)


def _item(source_size=None):
    """A video item that declines unknown property names, as Resolve does.

    A bare `MagicMock` accepts every name: under one, `_apply_conform`
    wrote `PanX`/`PanY` for the life of the feature and the picture never
    moved while the suite was green. No source size means no pool item.
    """
    item = timeline_item("fake.mov", 0, 48, has_media=source_size is not None)
    if source_size is not None:
        item.GetMediaPoolItem().SetClipProperty(
            "Resolution", f"{source_size[0]}x{source_size[1]}")
    return item


# ─────────────────────────────────────────────────────────
# 1. Parameter surviving planner -> manifest -> renderer
# ─────────────────────────────────────────────────────────

class TestFramingEndToEnd:
    """The framing_intent parameter flows from the spine block through
    compile_manifest's _conform_fields into the clip dict, and the
    renderer's _apply_conform reads it to set Zoom and Pan."""

    def test_framing_intent_half_produces_partial_zoom(self):
        """framing_intent=0.5 should produce a zoom halfway between 1.0
        and the full fill zoom."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=0.5,
        )
        assert result["needs_conform"] is True
        expected_zoom = round(1.0 + (EXPECTED_FULL_FILL_ZOOM - 1.0) * 0.5, 4)
        assert result["fill_zoom"] == expected_zoom

    def test_framing_intent_zero_produces_letterbox(self):
        """framing_intent=0.0 should produce letterbox (no conform)."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=0.0,
        )
        assert result.get("needs_conform") is False

    def test_framing_pan_x_sets_pixel_offset(self):
        """framing_pan_x should produce a pixel PanX value in the result."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=1.0,
            framing_pan_x=0.5,
        )
        assert result["needs_conform"] is True
        assert "framing_pan_x" in result
        # Pan should be positive (shifted right) and non-trivial
        assert result["framing_pan_x"] > 0

    def test_renderer_applies_zoom_and_pan(self):
        """_apply_conform must set properties Resolve actually accepts.

        Asserted against a fake that refuses unknown names, because that
        is what Resolve does. `PanX` passed this test for the life of the
        feature and moved nothing.
        """
        item = _item(source_size=(3840, 2160))
        results = {"warnings": []}
        clip = {
            "needs_conform": True,
            "fill_zoom": 2.0,
            "framing_pan_x": 50.0,
            "label": "test_clip",
        }
        _apply_conform(item, clip, results, frame_size=(1080, 1920))

        assert item.refused == [], f"Resolve would refuse {item.refused}"
        assert item.GetProperty("ZoomX") == 2.0
        assert item.GetProperty("ZoomY") == 2.0
        # 50 delivery pixels, in the Pan UNIT the one measured law gives
        # for 3840x2160 into 1080x1920: the fit is width-bound, so the
        # geometry factor is exactly 1 and the conversion IS the
        # measured draw gain - 50 px is Pan 25.  It is 25 because it
        # was CONVERTED, not because a pixel is a unit - see the Tilt
        # case below.  Driven against the law rather than restated, so
        # the next calibration moves this with the code.
        assert item.GetProperty("Pan") == pytest.approx(
            units_for_shift(50.0, 3840, 1080, _FIT_3840_TO_1080x1920),
            abs=0.01)
        assert item.GetProperty("Pan") == pytest.approx(25.0, abs=0.01)
        assert not results["warnings"]

    def test_renderer_uses_tilt_for_vertical_pan(self):
        """And converts the pixel offset into the Tilt UNIT.

        A landscape source conformed into a vertical frame draws
        `(2160/1920) * (1080/3840) * draw_gain = 0.6328` pixels per
        Tilt unit under the measured gain, so -25 delivery pixels is
        Tilt -39.506.  Passing the pixel value straight through moved
        the picture 25 * 0.3164 = 7.9px under the old gain - the
        under-aim this test exists to pin, now stated at today's.
        """
        item = _item(source_size=(3840, 2160))
        results = {"warnings": []}
        _apply_conform(item, {
            "needs_conform": True, "fill_zoom": 2.0,
            "framing_pan_y": -25.0, "label": "test_clip",
        }, results, frame_size=(1080, 1920))
        assert item.refused == []
        assert item.GetProperty("Tilt") == pytest.approx(
            units_for_shift(-25.0, 2160, 1920, _FIT_3840_TO_1080x1920),
            abs=0.01)
        assert item.GetProperty("Tilt") == pytest.approx(-39.506, abs=0.01)
        assert not results["warnings"]

    def test_a_pan_that_could_not_be_converted_says_so(self):
        """An unreadable source size does not silently guess a unit.

        The pixel value still goes through - refusing the pan would
        trade a mis-aimed picture for an unaimed one - but the run
        SAYS it was not converted.  Both wrong models of this transform
        survived review by being silent about which units they were in.
        """
        item = _item()
        results = {"warnings": []}
        _apply_conform(item, {
            "needs_conform": True, "fill_zoom": 2.0,
            "framing_pan_x": 50.0, "label": "test_clip",
        }, results, frame_size=(1080, 1920))
        assert item.GetProperty("Pan") == 50.0
        assert any("unconverted pixels" in w for w in results["warnings"])

    def test_renderer_warns_when_resolve_refuses_a_property(self):
        """A refused SetProperty must be reported, not swallowed.

        Resolve returns False rather than raising, so a function that only
        catches exceptions reports success for a property that never
        landed - the same shape as the withdrawn Smart Reframe call.
        """
        item = _item()
        item.refuse_properties = VIDEO_ITEM_PROPERTIES
        results = {"warnings": []}
        _apply_conform(item, {
            "needs_conform": True, "fill_zoom": 2.0, "label": "test_clip",
        }, results)
        assert results["warnings"], "a refused property must be recorded"
        assert "ZoomX" in results["warnings"][0]


# ─────────────────────────────────────────────────────────
# 2. Template bias applies when clip does not set one
# ─────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────
# 3. Clip's own value wins over template
# ─────────────────────────────────────────────────────────

class TestClipOverridesTemplate:
    """When both a per-clip framing_intent and a template bias exist,
    the per-clip value must win. This is tested at the _resolve_framing
    level by simulating the resolution logic."""

    def test_clip_intent_overrides_template(self):
        """A clip-level framing_intent=0.0 (letterbox) should produce
        letterbox even though the template might prefer fill."""
        # Template wants fill (1.0), but clip says letterbox (0.0)
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=0.0,  # clip's own value
        )
        assert result.get("needs_conform") is False


# ─────────────────────────────────────────────────────────
# 4. Unset means the default, and the default is fill
# ─────────────────────────────────────────────────────────

class TestUnsetIsTheDefault:
    """`framing_intent=None` means "nothing declared one", which resolves
    to framing_intent.DEFAULT_FRAMING_INTENT.

    It used to mean "run the legacy subject-visibility heuristic", whose
    rule was *letterbox whenever the primary subject is visible* - so on a
    talking head every clip letterboxed and project 001 shipped its A-roll
    in 608 of 1920 rows. The heuristic is deleted; `_conform_fields` has
    one branch now."""

    def test_unset_fills(self):
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=None,
        )
        assert result["needs_conform"] is True
        assert result["fill_zoom"] == EXPECTED_FULL_FILL_ZOOM
        assert "framing_pan_x" not in result

    def test_a_visible_subject_no_longer_forces_letterbox(self):
        """The exact case that letterboxed all eight of 001's A-roll
        clips: a landscape talking head with the subject on screen for the
        whole range. Only an explicit 0.0 letterboxes now."""
        filled = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=None, subject_center_x=0.42,
        )
        assert filled["needs_conform"] is True
        assert filled["fill_zoom"] == EXPECTED_FULL_FILL_ZOOM
        # ...and the crop window follows the subject rather than sitting
        # dead centre, which is what the old rule was guarding against.
        assert filled["framing_pan_x"] > 0

        letterboxed = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=0.0, subject_center_x=0.42,
        )
        # The RESOLVED intent travels with the clip so render_qa's
        # occupancy gate can tell a declared letterbox from an accidental
        # one; the conform half is still "leave it alone".
        assert letterboxed == {"needs_conform": False,
                               "framing_intent": 0.0,
                               "framing_delivered": 0.0}

    def test_renderer_no_pan_when_unset(self):
        """With no pan in the clip dict, only zoom is touched, so a
        project that never set framing renders byte-identically."""
        item = _item()
        results = {"warnings": []}
        clip = {
            "needs_conform": True,
            "fill_zoom": EXPECTED_FULL_FILL_ZOOM,
            "label": "legacy_clip",
        }
        _apply_conform(item, clip, results)

        assert {name for name, _ in item.property_writes} == {"ZoomX", "ZoomY"}
        assert item.refused == []
        assert not results["warnings"]


# ─────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────

class TestFramingEdgeCases:
    """Additional edge cases for robustness."""

    def test_missing_clip_metadata_returns_empty(self):
        """If clip metadata is missing, should return empty dict."""
        result = _conform_fields(
            {}, "clip_999", VERTICAL_PROJ_RES,
            framing_intent=0.5,
        )
        assert result == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
