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
from unittest.mock import MagicMock, call

# Ensure project root is on the path before any library imports.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
from library.schemas.brand_template import BrandTemplate, StyleSlots


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


# The Inspector Transform properties Resolve actually exposes on a video
# TimelineItem, read off `GetProperty()` on Resolve 21.0.0b.28. Note what
# is NOT here: `PanX` and `PanY`. Horizontal and vertical position are
# `Pan` and `Tilt`.
RESOLVE_VIDEO_ITEM_PROPERTIES = frozenset({
    "AnchorPointX", "AnchorPointY", "CompositeMode", "CropBottom",
    "CropLeft", "CropRetain", "CropRight", "CropSoftness", "CropTop",
    "Distortion", "DynamicZoomEase", "FlipX", "FlipY", "MotionEstimation",
    "Opacity", "Pan", "Pitch", "ResizeFilter", "RetimeProcess",
    "RotationAngle", "Scaling", "Tilt", "Yaw", "ZoomGang", "ZoomX", "ZoomY",
})


class FakeTimelineItem:
    """A TimelineItem that refuses unknown property names, as Resolve does.

    This exists because a bare `MagicMock` accepts every name and returns
    whatever `return_value` says. Under one, `_apply_conform` wrote `PanX`
    and `PanY` for the life of the feature: Resolve returned False, read
    back None, and the picture never moved, while the test suite was
    green. Resolve does not raise on a bad property name - it declines -
    so a fake that cannot decline cannot catch this.
    """

    def __init__(self, source_size=None):
        self.properties = {}
        self.refused = []
        self._source_size = source_size

    def GetMediaPoolItem(self):
        if self._source_size is None:
            return None
        size = self._source_size

        class _Pool:
            def GetClipProperty(self, name):
                return f"{size[0]}x{size[1]}" if name == "Resolution" else None

        return _Pool()

    def SetProperty(self, name, value):
        if name not in RESOLVE_VIDEO_ITEM_PROPERTIES:
            self.refused.append((name, value))
            return False
        self.properties[name] = value
        return True

    def GetProperty(self, name=None):
        if name is None:
            return dict(self.properties)
        return self.properties.get(name)

    def GetName(self):
        return "fake.mov"


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

    def test_framing_intent_full_produces_max_zoom(self):
        """framing_intent=1.0 should produce the full fill zoom."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=1.0,
        )
        assert result["needs_conform"] is True
        assert result["fill_zoom"] == EXPECTED_FULL_FILL_ZOOM

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
        item = FakeTimelineItem(source_size=(3840, 2160))
        results = {"warnings": []}
        clip = {
            "needs_conform": True,
            "fill_zoom": 2.0,
            "framing_pan_x": 50.0,
            "label": "test_clip",
        }
        _apply_conform(item, clip, results, frame_size=(1080, 1920))

        assert item.refused == [], f"Resolve would refuse {item.refused}"
        assert item.properties["ZoomX"] == 2.0
        assert item.properties["ZoomY"] == 2.0
        # 50 delivery pixels, in the Pan UNIT the one measured law gives
        # for 3840x2160 into 1080x1920: the fit is width-bound, so the
        # geometry factor is exactly 1 and the conversion IS the
        # measured draw gain - 50 px is Pan 25.  It is 25 because it
        # was CONVERTED, not because a pixel is a unit - see the Tilt
        # case below.  Driven against the law rather than restated, so
        # the next calibration moves this with the code.
        assert item.properties["Pan"] == pytest.approx(
            units_for_shift(50.0, 3840, 1080, _FIT_3840_TO_1080x1920),
            abs=0.01)
        assert item.properties["Pan"] == pytest.approx(25.0, abs=0.01)
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
        item = FakeTimelineItem(source_size=(3840, 2160))
        results = {"warnings": []}
        _apply_conform(item, {
            "needs_conform": True, "fill_zoom": 2.0,
            "framing_pan_y": -25.0, "label": "test_clip",
        }, results, frame_size=(1080, 1920))
        assert item.refused == []
        assert item.properties["Tilt"] == pytest.approx(
            units_for_shift(-25.0, 2160, 1920, _FIT_3840_TO_1080x1920),
            abs=0.01)
        assert item.properties["Tilt"] == pytest.approx(-39.506, abs=0.01)
        assert not results["warnings"]

    def test_a_pan_that_could_not_be_converted_says_so(self):
        """An unreadable source size does not silently guess a unit.

        The pixel value still goes through - refusing the pan would
        trade a mis-aimed picture for an unaimed one - but the run
        SAYS it was not converted.  Both wrong models of this transform
        survived review by being silent about which units they were in.
        """
        item = FakeTimelineItem()
        results = {"warnings": []}
        _apply_conform(item, {
            "needs_conform": True, "fill_zoom": 2.0,
            "framing_pan_x": 50.0, "label": "test_clip",
        }, results, frame_size=(1080, 1920))
        assert item.properties["Pan"] == 50.0
        assert any("unconverted pixels" in w for w in results["warnings"])

    def test_renderer_warns_when_resolve_refuses_a_property(self):
        """A refused SetProperty must be reported, not swallowed.

        Resolve returns False rather than raising, so a function that only
        catches exceptions reports success for a property that never
        landed - the same shape as the withdrawn Smart Reframe call.
        """
        class RefuseEverything(FakeTimelineItem):
            def SetProperty(self, name, value):
                self.refused.append((name, value))
                return False

        item = RefuseEverything()
        results = {"warnings": []}
        _apply_conform(item, {
            "needs_conform": True, "fill_zoom": 2.0, "label": "test_clip",
        }, results)
        assert results["warnings"], "a refused property must be recorded"
        assert "ZoomX" in results["warnings"][0]

    def test_pan_property_names_match_resolve(self):
        """The names are pinned, so a rename cannot pass silently."""
        import library.steps.step_6_01_render.resolve_build_timeline as rbt
        assert rbt._CONFORM_PAN_PROP in RESOLVE_VIDEO_ITEM_PROPERTIES
        assert rbt._CONFORM_TILT_PROP in RESOLVE_VIDEO_ITEM_PROPERTIES
        for prop in rbt._CONFORM_ZOOM_PROPS:
            assert prop in RESOLVE_VIDEO_ITEM_PROPERTIES
        assert "PanX" not in RESOLVE_VIDEO_ITEM_PROPERTIES
        assert "PanY" not in RESOLVE_VIDEO_ITEM_PROPERTIES


# ─────────────────────────────────────────────────────────
# 2. Template bias applies when clip does not set one
# ─────────────────────────────────────────────────────────

class TestTemplateBias:
    """When a clip has no explicit framing_intent, the template's
    default should be used as a fallback by the caller's resolution
    logic."""

    def test_template_framing_intent_applies_as_fallback(self):
        """Calling _conform_fields with a template's framing_intent
        value (simulating the _resolve_framing fallback) produces the
        expected partial zoom."""
        template_intent = 0.7
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=template_intent,
        )
        assert result["needs_conform"] is True
        expected_zoom = round(1.0 + (EXPECTED_FULL_FILL_ZOOM - 1.0) * 0.7, 4)
        assert result["fill_zoom"] == expected_zoom

    def test_brand_template_schema_has_framing_intent(self):
        """The BrandTemplate schema must expose framing_intent on the
        style slots so templates can set it."""
        slots = StyleSlots(framing_intent=0.5)
        assert slots.framing_intent == 0.5

    def test_brand_template_framing_intent_defaults_to_none(self):
        """An unset framing_intent should be None, meaning auto."""
        slots = StyleSlots()
        assert slots.framing_intent is None

    def test_brand_template_from_dict_reads_framing_intent(self):
        """BrandTemplate.from_dict should pick up framing_intent from
        the style dict."""
        data = {
            "series_id": "test",
            "style": {"framing_intent": 0.3},
        }
        tmpl = BrandTemplate.from_dict(data)
        assert tmpl.style.framing_intent == 0.3


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

    def test_clip_intent_partial_overrides_template_full(self):
        """A clip-level framing_intent=0.3 should win over a template
        value of 1.0 when passed directly."""
        result_clip = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=0.3,
        )
        result_template = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=1.0,
        )
        # The zoom at 0.3 must be less than at 1.0
        assert result_clip["fill_zoom"] < result_template["fill_zoom"]


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

    def test_unset_matches_the_declared_default(self):
        from library.tools.framing_intent import DEFAULT_FRAMING_INTENT
        unset = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=None,
        )
        declared = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=DEFAULT_FRAMING_INTENT,
        )
        assert unset == declared

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

    def test_unset_matching_aspect_no_conform(self):
        """A clip whose aspect ratio matches the target should never
        need conform, regardless of framing_intent."""
        matching_meta = {"clip_v": {"width": 1080, "height": 1920}}
        result = _conform_fields(
            matching_meta, "clip_v", VERTICAL_PROJ_RES,
            framing_intent=None,
        )
        assert result.get("needs_conform") is False

    def test_renderer_no_pan_when_unset(self):
        """With no pan in the clip dict, only zoom is touched, so a
        project that never set framing renders byte-identically."""
        item = FakeTimelineItem()
        results = {"warnings": []}
        clip = {
            "needs_conform": True,
            "fill_zoom": EXPECTED_FULL_FILL_ZOOM,
            "label": "legacy_clip",
        }
        _apply_conform(item, clip, results)

        assert set(item.properties) == {"ZoomX", "ZoomY"}
        assert item.refused == []
        assert not results["warnings"]


# ─────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────

class TestFramingEdgeCases:
    """Additional edge cases for robustness."""

    def test_framing_intent_clamped_above_one(self):
        """Values > 1.0 should be clamped to 1.0."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=1.5,
        )
        assert result["fill_zoom"] == EXPECTED_FULL_FILL_ZOOM

    def test_framing_intent_clamped_below_zero(self):
        """Values < 0.0 should be clamped to 0.0 (letterbox)."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=-0.5,
        )
        assert result.get("needs_conform") is False

    def test_framing_pan_clamped_to_range(self):
        """Pan values outside -1..1 should be clamped."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=1.0,
            framing_pan_x=2.0,  # > 1.0, should clamp
        )
        # The result should still have a valid pan, equivalent to pan=1.0
        result_at_one = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=1.0,
            framing_pan_x=1.0,
        )
        assert result["framing_pan_x"] == result_at_one["framing_pan_x"]

    def test_missing_clip_metadata_returns_empty(self):
        """If clip metadata is missing, should return empty dict."""
        result = _conform_fields(
            {}, "clip_999", VERTICAL_PROJ_RES,
            framing_intent=0.5,
        )
        assert result == {}

    def test_pan_zero_not_included(self):
        """framing_pan_x=0 should not add framing_pan_x to the result
        (centred is the default, no point setting it)."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            framing_intent=0.5,
            framing_pan_x=0.0,
        )
        assert "framing_pan_x" not in result

    def test_monotonic_zoom_across_continuum(self):
        """Zoom should increase monotonically as framing_intent goes
        from 0.0 to 1.0."""
        zooms = []
        for intent in [0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
            result = _conform_fields(
                LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
                framing_intent=intent,
            )
            zooms.append(result["fill_zoom"])
        for i in range(1, len(zooms)):
            assert zooms[i] >= zooms[i - 1], (
                f"Zoom should increase monotonically: {zooms}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
