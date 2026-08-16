#!/usr/bin/env python3
"""
Tests for composable effect blocks (Layer 2) and CompEngine (Layer 3).

Verifies:
1. Individual effect blocks produce valid nodes
2. Empty/neutral effects are correctly skipped
3. CompEngine chains effects and produces valid .comp output
4. from_params() backward compatibility with old generator signature
"""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
from fusion.effects import EffectBlock, _reset_counters, fx
from fusion.engine import CompEngine
from fusion.nodes import BezierSpline, FusionNode


class TestEffectBlocks(unittest.TestCase):
    """Test individual effect block functions."""

    def setUp(self):
        _reset_counters()

    def test_zoom_basic(self):
        block = fx.zoom(90, start=1.0, mid=1.04, end=1.03)
        self.assertTrue(len(block.nodes) > 0)
        self.assertIn("Transform", block.input_name)

        # Should have a Transform and a BezierSpline
        types = [type(n).__name__ for n in block.nodes]
        self.assertIn("FusionNode", types)
        self.assertIn("BezierSpline", types)

    def test_zoom_with_pan(self):
        block = fx.zoom(90, start=1.0, end=1.0, pan_end=(0.5, 0.49))
        self.assertTrue(len(block.nodes) > 0)

        # Find the transform and check it has Center
        transform = [n for n in block.nodes if isinstance(n, FusionNode)][0]
        self.assertIn("Center", transform.inputs)

    def test_zoom_neutral_skips(self):
        block = fx.zoom(90, start=1.0, mid=1.0, end=1.0)
        self.assertEqual(len(block.nodes), 0)
        self.assertEqual(block.input_name, "")

    def test_grade_basic(self):
        block = fx.grade(gain=1.05, contrast=0.04, saturation=1.15)
        self.assertEqual(len(block.nodes), 1)
        self.assertIn("BrightnessContrast", block.nodes[0].tool_type)

    def test_grade_neutral_skips(self):
        block = fx.grade(gain=1.0, contrast=0.0, saturation=1.0)
        self.assertEqual(len(block.nodes), 0)

    def test_glow_basic(self):
        block = fx.glow(gain=0.08)
        self.assertEqual(len(block.nodes), 1)
        self.assertIn("SoftGlow", block.nodes[0].tool_type)

    def test_glow_zero_skips(self):
        block = fx.glow(gain=0.0)
        self.assertEqual(len(block.nodes), 0)

    def test_grain(self):
        block = fx.grain(power=0.25, size=1.5)
        self.assertEqual(len(block.nodes), 1)
        self.assertEqual(block.nodes[0].tool_type, "FilmGrain")

    def test_defocus(self):
        block = fx.defocus(size=2.0)
        self.assertEqual(len(block.nodes), 1)
        self.assertEqual(block.nodes[0].tool_type, "Defocus")

    def test_vignette(self):
        block = fx.vignette(clip_dur=90)
        # Background + Ellipse + Merge
        self.assertEqual(len(block.nodes), 3)
        types = {n.tool_type for n in block.nodes if isinstance(n, FusionNode)}
        self.assertIn("Background", types)
        self.assertIn("EllipseMask", types)
        self.assertIn("Merge", types)

        # Input is Merge.Background (upstream feeds in here)
        self.assertEqual(block.input_key, "Background")

    def test_fade_in(self):
        block = fx.fade(90, fade_in=10)
        self.assertTrue(len(block.nodes) > 0)
        # Should have a BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        self.assertTrue(len(splines) > 0)

    def test_fade_out(self):
        block = fx.fade(90, fade_out=15)
        self.assertTrue(len(block.nodes) > 0)

    def test_fade_no_frames_skips(self):
        block = fx.fade(90)
        self.assertEqual(len(block.nodes), 0)

    def test_transition_tail_fade(self):
        block = fx.transition_tail(90, "fade_to_black", 12)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_tail_zoom_blur(self):
        block = fx.transition_tail(90, "zoom_blur", 10)
        self.assertTrue(len(block.nodes) > 0)
        # Should have Transform + DirectionalBlur
        types = {
            n.tool_type for n in block.nodes if isinstance(n, FusionNode)
        }
        self.assertIn("Transform", types)
        self.assertIn("DirectionalBlur", types)

    def test_transition_tail_defocus(self):
        block = fx.transition_tail(90, "defocus", 8)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_tail_flash(self):
        block = fx.transition_tail(90, "flash", 6)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_head_fade(self):
        block = fx.transition_head(90, "fade_to_black", 12)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_unknown_raises(self):
        with self.assertRaises(ValueError):
            fx.transition_tail(90, "nonexistent_type", 10)


class TestCompEngine(unittest.TestCase):
    """Test CompEngine composition and serialization."""

    def test_empty_comp(self):
        """Minimal comp with just MediaIn → MediaOut."""
        comp = CompEngine(clip_dur=30).serialize()
        self.assertIn("Composition {", comp)
        self.assertIn("MediaIn1 = MediaIn", comp)
        self.assertIn("MediaOut1 = MediaOut", comp)
        self.assertIn('SourceOp = "MediaIn1"', comp)

    def test_single_effect(self):
        comp = (CompEngine(clip_dur=90)
                .add(fx.grade(gain=1.05))
                .serialize())
        self.assertIn("BrightnessContrast", comp)
        self.assertIn("Gain = Input { Value = 1.05, },", comp)
        # BrightnessContrast should be wired to MediaIn1
        self.assertIn('SourceOp = "MediaIn1"', comp)

    def test_chained_effects(self):
        comp = (CompEngine(clip_dur=90)
                .add(fx.zoom(90, start=1.0, end=1.03))
                .add(fx.grade(gain=1.05))
                .add(fx.glow(gain=0.08))
                .serialize())
        # All three effect types present
        self.assertIn("Transform", comp)
        self.assertIn("BrightnessContrast", comp)
        self.assertIn("SoftGlow", comp)

    def test_skip_neutral_effects(self):
        comp = (CompEngine(clip_dur=90)
                .add(fx.zoom(90))  # neutral — skipped
                .add(fx.grade(gain=1.0))  # neutral — skipped
                .add(fx.glow(gain=0.08))  # active
                .serialize())
        # Only SoftGlow should be present (plus MediaIn/Out)
        self.assertNotIn("Transform", comp)
        self.assertNotIn("BrightnessContrast", comp)
        self.assertIn("SoftGlow", comp)
        # SoftGlow should wire directly to MediaIn1
        self.assertIn('SourceOp = "MediaIn1"', comp)


    def test_from_params_backward_compat(self):
        """from_params should accept the old flat param format."""
        comp = CompEngine.from_params(
            clip_dur=90,
            zoom_start=1.0,
            zoom_mid=1.04,
            zoom_end=1.03,
            pan_start=(0.5, 0.5),
            pan_end=(0.5, 0.49),
            grade_gain=1.05,
            grade_contrast=0.04,
            grade_saturation=1.15,
            glow_gain=0.08,
        )
        self.assertIn("Composition {", comp)
        self.assertIn("Transform", comp)
        self.assertIn("BrightnessContrast", comp)
        self.assertIn("SoftGlow", comp)

    def test_from_params_with_transitions(self):
        comp = CompEngine.from_params(
            clip_dur=90,
            tail_transition="fade_to_black",
            tail_transition_frames=12,
        )
        self.assertIn("Composition {", comp)
        # Should have a fade transition
        self.assertIn("BgTrans", comp)
        self.assertIn("MergeTrans", comp)

    def test_vignette_safety(self):
        """Vignette should always produce safe EllipseMask."""
        comp = (CompEngine(clip_dur=90)
                .add(fx.vignette(clip_dur=90))
                .serialize())
        # Must have Inverted, MaskWidth, MaskHeight, PixelAspect
        self.assertIn("Inverted", comp)
        self.assertIn("MaskWidth", comp)
        self.assertIn("MaskHeight", comp)
        self.assertIn("PixelAspect", comp)

    def test_directional_blur_safety_spline_fail(self):
        """A composition with a DirectionalBlur animated via spline peaking at 8.0 raises ValueError"""
        from fusion.nodes import FusionComp
        comp = FusionComp(duration=90)
        
        spline = BezierSpline("DirectionalBlur1Length")
        spline.add_key(0, 1.0).add_key(45, 8.0).add_key(90, 1.0)
        
        blur = FusionNode("DirectionalBlur1", "DirectionalBlur")
        blur.set_input("Length", spline)
        
        comp.add_node(spline).add_node(blur)
        with self.assertRaises(ValueError) as ctx:
            comp.serialize()
        self.assertIn("DirectionalBlur Length animated peak 8.0 > 5", str(ctx.exception))

    def test_directional_blur_safety_spline_pass(self):
        """A composition with a DirectionalBlur animated via spline peaking at 4.0 passes"""
        from fusion.nodes import FusionComp
        comp = FusionComp(duration=90)
        
        spline = BezierSpline("DirectionalBlur1Length")
        spline.add_key(0, 1.0).add_key(45, 4.0).add_key(90, 1.0)
        
        blur = FusionNode("DirectionalBlur1", "DirectionalBlur")
        blur.set_input("Length", spline)
        
        comp.add_node(spline).add_node(blur)
        comp.serialize()  # Should not raise

    def test_transform_size_safety_spline_fail(self):
        """A composition with a Transform.Size animated via spline peaking at 1.06 raises ValueError"""
        from fusion.nodes import FusionComp
        comp = FusionComp(duration=90)
        
        spline = BezierSpline("Transform1Size")
        spline.add_key(0, 1.0).add_key(45, 1.06).add_key(90, 1.0)
        
        transform = FusionNode("Transform1", "Transform")
        transform.set_input("Size", spline)
        
        comp.add_node(spline).add_node(transform)
        with self.assertRaises(ValueError) as ctx:
            comp.serialize()
        self.assertIn("Transform zoom (Size) animated peak 1.06 > 1.04", str(ctx.exception))

    def test_transform_size_safety_spline_pass(self):
        """A composition with a Transform.Size animated via spline peaking at 1.03 passes"""
        from fusion.nodes import FusionComp
        comp = FusionComp(duration=90)
        
        spline = BezierSpline("Transform1Size")
        spline.add_key(0, 1.0).add_key(45, 1.03).add_key(90, 1.0)
        
        transform = FusionNode("Transform1", "Transform")
        transform.set_input("Size", spline)
        
        comp.add_node(spline).add_node(transform)
        comp.serialize()  # Should not raise


if __name__ == "__main__":
    unittest.main()

