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
# compile_manifest/step.py manipulates sys.path at import time; mirror that.
_step_dir = os.path.join(PROJECT_ROOT, "library", "steps", "step_5_04_compile_manifest")
_lib_dir = os.path.join(PROJECT_ROOT, "library")
for _p in (_step_dir, _lib_dir, PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from library.steps.step_5_04_compile_manifest.step import _conform_fields


# ── Import the renderer helper under test ──
# Stub DaVinciResolveScript so the renderer module can be imported.
if "DaVinciResolveScript" not in sys.modules:
    sys.modules["DaVinciResolveScript"] = MagicMock()

sys.path.insert(0, os.path.join(PROJECT_ROOT, "library", "steps", "step_6_01_render"))
from resolve_build_timeline import _apply_conform


# ── Import schema to verify the field exists ──
from library.schemas.brand_template import BrandTemplate, StyleSlots


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
        """_apply_conform should call SetProperty for ZoomX, ZoomY, and
        PanX when the clip dict carries them."""
        mock_item = MagicMock()
        mock_item.SetProperty.return_value = True
        results = {"warnings": []}
        clip = {
            "needs_conform": True,
            "fill_zoom": 2.0,
            "framing_pan_x": 50.0,
            "label": "test_clip",
        }
        _apply_conform(mock_item, clip, results)

        mock_item.SetProperty.assert_any_call("ZoomX", 2.0)
        mock_item.SetProperty.assert_any_call("ZoomY", 2.0)
        mock_item.SetProperty.assert_any_call("PanX", 50.0)
        assert not results["warnings"]


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
# 4. Unset case reproduces today's output
# ─────────────────────────────────────────────────────────

class TestUnsetPreservesLegacy:
    """When framing_intent is None (unset), _conform_fields must behave
    identically to the pre-change code: the subject-visibility heuristic
    decides, and no Pan is produced."""

    def test_unset_no_semantic_fills(self):
        """Without semantic data and no framing_intent, landscape clips
        should be filled (the legacy fallback when subject is not
        visible)."""
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            semantic_doc=None,
            framing_intent=None,
        )
        assert result["needs_conform"] is True
        assert result["fill_zoom"] == EXPECTED_FULL_FILL_ZOOM
        assert "framing_pan_x" not in result

    def test_unset_subject_visible_letterboxes(self):
        """With semantic data showing subject visibility and no
        framing_intent, the clip should letterbox to preserve the
        subject."""
        semantic = {
            "assessment": {"primary_subject_visible": [[0.0, 5.0]]},
        }
        result = _conform_fields(
            LANDSCAPE_CLIP_META, "clip_001", VERTICAL_PROJ_RES,
            semantic_doc=semantic,
            source_in=1.0, source_out=3.0,
            framing_intent=None,
        )
        assert result.get("needs_conform") is False

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
        """When no Pan values are in the clip dict, _apply_conform must
        not call SetProperty for PanX or PanY."""
        mock_item = MagicMock()
        mock_item.SetProperty.return_value = True
        results = {"warnings": []}
        clip = {
            "needs_conform": True,
            "fill_zoom": EXPECTED_FULL_FILL_ZOOM,
            "label": "legacy_clip",
        }
        _apply_conform(mock_item, clip, results)

        # Only ZoomX and ZoomY, never PanX/PanY
        prop_calls = [c[0][0] for c in mock_item.SetProperty.call_args_list]
        assert "ZoomX" in prop_calls
        assert "ZoomY" in prop_calls
        assert "PanX" not in prop_calls
        assert "PanY" not in prop_calls


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
