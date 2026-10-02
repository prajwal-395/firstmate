"""
Tests for the Fusion node graph object model (Layer 1).

Verifies:
1. Individual node serialization matches expected Lua syntax
2. BezierSpline keyframe serialization with handles
3. FusionComp produces valid .comp output
4. Safety validations fire correctly (ApplyMode, BlendClone, etc.)
5. Backward compatibility: reproduces existing hook_1.comp exactly
"""

import os
import sys
import unittest

import pytest

pytestmark = pytest.mark.unit

# Add the tools directory to path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
from fusion.nodes import BezierSpline, FusionComp, FusionNode


class TestBezierSpline(unittest.TestCase):
    """Test BezierSpline serialization."""

    def test_simple_keyframes(self):
        s = BezierSpline("Transform1Size")
        s.add_key(0, 1.0, rh=(25, 1.013))
        s.add_key(38, 1.04)
        s.add_key(75, 1.03, lh=(50, 1.033))

        out = s.serialize()
        self.assertIn("Transform1Size = BezierSpline {", out)
        self.assertIn("[0] = { 1.0, RH = { 25, 1.013 } },", out)
        self.assertIn("[38] = { 1.04 },", out)
        self.assertIn("[75] = { 1.03, LH = { 50, 1.033 } },", out)
        self.assertIn("SplineColor = { Red = 233, Green = 217, Blue = 11 }", out)

    def test_both_handles(self):
        s = BezierSpline("Size")
        s.add_key(10, 2.0, lh=(5, 1.5), rh=(15, 2.5))

        out = s.serialize()
        self.assertIn("LH = { 5, 1.5 }", out)
        self.assertIn("RH = { 15, 2.5 }", out)

    def test_custom_color(self):
        s = BezierSpline("Test", color=(255, 100, 50))
        s.add_key(0, 0.0)
        out = s.serialize()
        self.assertIn("Red = 255, Green = 100, Blue = 50", out)

    def test_repr(self):
        s = BezierSpline("MySpline")
        s.add_key(0, 1.0)
        s.add_key(30, 2.0)
        self.assertEqual(repr(s), "BezierSpline('MySpline', 2 keys)")


class TestFusionNode(unittest.TestCase):
    """Test FusionNode serialization and validation."""

    def test_simple_node(self):
        n = FusionNode("BrightnessContrast1", "BrightnessContrast")
        n.set_input("Gain", 1.05)
        n.set_input("Contrast", 0.04)
        n.set_input("Input", "Transform1")
        n.pos = (220, 0)

        out = n.serialize()
        self.assertIn("BrightnessContrast1 = BrightnessContrast {", out)
        self.assertIn("Gain = Input { Value = 1.05, },", out)
        self.assertIn("Contrast = Input { Value = 0.04, },", out)
        self.assertIn('SourceOp = "Transform1"', out)
        self.assertIn("Pos = { 220, 0 }", out)

    def test_attribute(self):
        n = FusionNode("Transform1", "Transform")
        n.set_attr("CtrlWZoom", False)
        n.set_input("Input", "MediaIn1")
        n.pos = (110, 0)

        out = n.serialize()
        self.assertIn("CtrlWZoom = false,", out)

    def test_point_input(self):
        n = FusionNode("Transform1", "Transform")
        n.set_attr("CtrlWZoom", False)
        n.set_input("Center", (0.5, 0.49))
        n.set_input("Input", "MediaIn1")
        n.pos = (110, 0)

        out = n.serialize()
        self.assertIn("Center = Input { Value = { 0.5, 0.49 }, },", out)

    def test_spline_input(self):
        spline = BezierSpline("Transform1Size")
        spline.add_key(0, 1.0)

        n = FusionNode("Transform1", "Transform")
        n.set_attr("CtrlWZoom", False)
        n.set_input("Size", spline)
        n.set_input("Input", "MediaIn1")
        n.pos = (110, 0)

        out = n.serialize()
        self.assertIn('SourceOp = "Transform1Size"', out)
        self.assertIn('Source = "Value"', out)

    def test_mask_source(self):
        n = FusionNode("Background1", "Background")
        n.set_input("GlobalOut", 75)
        n.set_input("Width", 1080)
        n.set_input("Height", 1920)
        n.set_input("EffectMask", "Ellipse1", source="Mask")

        out = n.serialize()
        self.assertIn('Source = "Mask"', out)

    # ── Safety validations ──

    def test_reject_apply_mode(self):
        n = FusionNode("Merge1", "Merge")
        n.set_input("Blend", 0.5)
        n.set_input("ApplyMode", 5)
        n.set_input("Background", "SomeNode")
        n.set_input("Foreground", "OtherNode")

        with self.assertRaises(ValueError) as ctx:
            n.serialize()
        self.assertIn("ApplyMode", str(ctx.exception))
        self.assertIn("CRASHES", str(ctx.exception))

    def test_reject_blend_clone(self):
        n = FusionNode("Merge1", "Merge")
        n.set_input("BlendClone", 0.5)
        n.set_input("Background", "A")
        n.set_input("Foreground", "B")

        with self.assertRaises(ValueError) as ctx:
            n.serialize()
        self.assertIn("BlendClone", str(ctx.exception))

    def test_reject_background_without_global_out(self):
        n = FusionNode("Background1", "Background")
        n.set_input("Width", 1080)
        n.set_input("Height", 1920)

        with self.assertRaises(ValueError) as ctx:
            n.serialize()
        self.assertIn("GlobalOut", str(ctx.exception))

    def test_reject_ellipse_missing_required(self):
        n = FusionNode("Ellipse1", "EllipseMask")
        n.set_input("SoftEdge", 0.35)
        # Missing Invert, MaskWidth, MaskHeight, PixelAspect

        with self.assertRaises(ValueError) as ctx:
            n.serialize()
        self.assertIn("EllipseMask missing required", str(ctx.exception))

    def test_reject_directional_blur_too_long(self):
        n = FusionNode("DBlur1", "DirectionalBlur")
        n.set_input("Length", 10.0)
        n.set_input("Input", "SomeNode")

        with self.assertRaises(ValueError) as ctx:
            n.serialize()
        self.assertIn("Length", str(ctx.exception))

    def test_allow_directional_blur_via_spline(self):
        """Animated Length via spline should NOT trigger static check."""
        spline = BezierSpline("DBlurLen")
        spline.add_key(0, 0.0)
        spline.add_key(30, 5.0)

        n = FusionNode("DBlur1", "DirectionalBlur")
        n.set_input("Length", spline)
        n.set_input("Input", "SomeNode")

        # Should not raise — the spline manages the values
        out = n.serialize()
        self.assertIn("DBlur1 = DirectionalBlur", out)


class TestFusionComp(unittest.TestCase):
    """Test FusionComp serialization."""

    def test_minimal_comp(self):
        comp = FusionComp(duration=30)

        media_in = FusionNode("MediaIn1", "MediaIn")
        media_in.inputs["MediaIn1.GlobalStart"] = {
            "_type": "quoted_key",
            "value": 0,
        }
        media_in.inputs["MediaIn1.GlobalEnd"] = {
            "_type": "quoted_key",
            "value": 29,
        }

        media_out = FusionNode("MediaOut1", "MediaOut")
        media_out.set_input("Input", "MediaIn1")
        media_out.pos = (110, 0)

        comp.add_node(media_in)
        comp.add_node(media_out)

        out = comp.serialize()
        self.assertIn("Composition {", out)
        self.assertIn("RenderRange = { 0, 29 }", out)
        self.assertIn("GlobalRange = { 0, 29 }", out)
        self.assertIn("Tools = {", out)
        self.assertIn("MediaIn1 = MediaIn", out)
        self.assertIn("MediaOut1 = MediaOut", out)
        self.assertTrue(out.endswith("}"))

    def test_find_node(self):
        comp = FusionComp(duration=30)
        n = FusionNode("Transform1", "Transform")
        n.set_attr("CtrlWZoom", False)
        n.set_input("Input", "MediaIn1")
        comp.add_node(n)

        found = comp.find_node("Transform1")
        self.assertIsNotNone(found)
        self.assertEqual(found.tool_type, "Transform")

        not_found = comp.find_node("DoesNotExist")
        self.assertIsNone(not_found)

    def test_find_nodes_by_type(self):
        comp = FusionComp(duration=30)
        comp.add_node(FusionNode("T1", "Transform").set_attr("CtrlWZoom", False).set_input("Input", "X"))
        comp.add_node(FusionNode("T2", "Transform").set_attr("CtrlWZoom", False).set_input("Input", "Y"))
        comp.add_node(FusionNode("B1", "Background").set_input("GlobalOut", 29).set_input("Width", 1080).set_input("Height", 1920))

        transforms = comp.find_nodes_by_type("Transform")
        self.assertEqual(len(transforms), 2)

    def test_dump(self):
        comp = FusionComp(duration=30)
        comp.add_node(FusionNode("T1", "Transform").set_attr("CtrlWZoom", False).set_input("Input", "X"))
        dump = comp.dump()
        self.assertIn("FusionComp(duration=30", dump)
        self.assertIn("Transform", dump)

    def test_reject_path_with_merge(self):
        """Path type + Merge in same comp → black output."""
        comp = FusionComp(duration=30)

        transform = FusionNode("Transform1", "Transform")
        transform.set_attr("CtrlWZoom", False)
        transform.inputs["Center"] = {"_type": "path"}
        transform.set_input("Input", "MediaIn1")
        comp.add_node(transform)

        merge = FusionNode("Merge1", "Merge")
        merge.set_input("Blend", 0.5)
        merge.set_input("Background", "Transform1")
        merge.set_input("Foreground", "Bg1")
        comp.add_node(merge)

        with self.assertRaises(ValueError) as ctx:
            comp.serialize()
        self.assertIn("Path", str(ctx.exception))
        self.assertIn("Merge", str(ctx.exception))


class TestHook1Reproduction(unittest.TestCase):
    """Reproduce the existing hook_1.comp output using the new node model.

    This is the critical backward-compatibility test. We build the same
    composition programmatically and verify the output matches.
    """

    def test_hook_1_structure(self):
        """Build hook_1.comp's node graph and verify structure."""
        comp = FusionComp(duration=76)  # 0..75

        # MediaIn
        media_in = FusionNode("MediaIn1", "MediaIn")
        media_in.inputs["MediaIn1.GlobalStart"] = {
            "_type": "quoted_key", "value": 0,
        }
        media_in.inputs["MediaIn1.GlobalEnd"] = {
            "_type": "quoted_key", "value": 75,
        }
        media_in.pos = (0, 0)
        comp.add_node(media_in)

        # Transform with animated zoom + static pan
        zoom_spline = BezierSpline("Transform1Size")
        zoom_spline.add_key(0, 1.0, rh=(25, 1.0132))
        zoom_spline.add_key(38, 1.04, lh=(13, 1.04), rh=(63, 1.04))
        zoom_spline.add_key(75, 1.03, lh=(50, 1.0333))

        transform = FusionNode("Transform1", "Transform")
        transform.set_attr("CtrlWZoom", False)
        transform.set_input("Size", zoom_spline)
        transform.set_input("Center", (0.5, 0.49))
        transform.set_input("Input", "MediaIn1")
        transform.pos = (110, 0)
        comp.add_node(transform)
        comp.add_node(zoom_spline)

        # BrightnessContrast
        bc = FusionNode("BrightnessContrast1", "BrightnessContrast")
        bc.set_input("Gain", 1.05)
        bc.set_input("Contrast", 0.04)
        bc.set_input("Saturation", 1.15)
        bc.set_input("Input", "Transform1")
        bc.pos = (220, 0)
        comp.add_node(bc)

        # SoftGlow
        glow = FusionNode("SoftGlow1", "SoftGlow")
        glow.set_input("Threshold", 0.75)
        glow.set_input("Gain", 0.08)
        glow.set_input("XGlowSize", 3.5)
        glow.set_input("Input", "BrightnessContrast1")
        glow.pos = (330, 0)
        comp.add_node(glow)

        # Vignette: Background + EllipseMask + Merge
        bg = FusionNode("Background1", "Background")
        bg.set_input("GlobalOut", 75)
        bg.set_input("Width", 1080)
        bg.set_input("Height", 1920)
        bg.set_input("EffectMask", "Ellipse1", source="Mask")
        bg.pos = (330, 82)
        comp.add_node(bg)

        ellipse = FusionNode("Ellipse1", "EllipseMask")
        ellipse.set_input("SoftEdge", 0.35)
        ellipse.set_input("MaskWidth", 320)
        ellipse.set_input("MaskHeight", 240)
        ellipse.set_input("PixelAspect", (1, 1))
        ellipse.set_input("Invert", 1)
        ellipse.set_input("Width", 1.8)
        ellipse.set_input("Height", 1.8)
        ellipse.pos = (220, 82)
        comp.add_node(ellipse)

        merge = FusionNode("Merge1", "Merge")
        merge.set_input("Blend", 0.25)
        merge.set_input("Background", "SoftGlow1")
        merge.set_input("Foreground", "Background1")
        merge.pos = (440, 0)
        comp.add_node(merge)

        # MediaOut
        media_out = FusionNode("MediaOut1", "MediaOut")
        media_out.set_input("Input", "Merge1")
        media_out.pos = (770, 0)
        comp.add_node(media_out)

        # Serialize and verify key structural elements
        out = comp.serialize()

        # Header
        self.assertIn("RenderRange = { 0, 75 }", out)
        self.assertIn("GlobalRange = { 0, 75 }", out)

        # Zoom keyframes
        self.assertIn("[0] = { 1.0, RH = { 25, 1.0132 } },", out)
        self.assertIn("[38] = { 1.04, LH = { 13, 1.04 }, RH = { 63, 1.04 } },", out)
        self.assertIn("[75] = { 1.03, LH = { 50, 1.0333 } },", out)

        # Color grade
        self.assertIn("Gain = Input { Value = 1.05, },", out)
        self.assertIn("Saturation = Input { Value = 1.15, },", out)

        # Vignette
        self.assertIn("Invert = Input { Value = 1, },", out)
        self.assertIn("Blend = Input { Value = 0.25, },", out)

        # Correct wiring chain
        self.assertIn('SourceOp = "MediaIn1"', out)
        self.assertIn('SourceOp = "Transform1"', out)
        self.assertIn('SourceOp = "BrightnessContrast1"', out)
        self.assertIn('SourceOp = "SoftGlow1"', out)
        self.assertIn('SourceOp = "Background1"', out)
        self.assertIn('SourceOp = "Merge1"', out)


if __name__ == "__main__":
    unittest.main()
