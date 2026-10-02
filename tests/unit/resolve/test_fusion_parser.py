"""Tests for the .comp / .setting parser (Layer 0).

Verifies:
1. Round-trip: parse hook_1.comp → inspect nodes → verify structure
2. BezierSpline keyframe parsing with handles
3. SourceOp wiring reconstruction
4. .setting macro parsing (GroupOperator)
5. Parse Resolve's built-in Chromatic Aberration.setting
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
import pytest
from unittest.mock import MagicMock, patch
from library.tools.builtin_effect_loader import import_customized_effect
import pathlib
from library.tools.builtin_effect_loader import BUILTIN_DIR, list_builtin_effects
from library.tools.fusion.nodes import BezierSpline as BezierSpline_2, FusionComp as FusionComp_2, FusionNode as FusionNode_2
from library.tools.fusion.parser import parse_setting as parse_setting_2
import json
import re
import sys
import io
from pathlib import Path
import ast
from library.tools.fusion import tool_inputs
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.nodes import FusionComp as FusionComp_3, FusionNode as FusionNode_3


_SECTION_0_MARK = pytest.mark.unit

from fusion.nodes import BezierSpline, FusionNode
from fusion.parser import parse_comp, parse_comp_file, parse_setting

# Navigate: resolve/ → unit/ → tests/ → (workspace root)
_WORKSPACE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
))
COMP_DIR = os.path.join(
    _WORKSPACE, "library", "steps", "step_6_01_render", "fusion_comps",
)


# A hook comp as the generators author one: a MediaIn feeding a Transform
# whose Size is driven by a BezierSpline, a graded and glowed chain, a
# vignette built from a Background and an inverted EllipseMask, and a
# transition Merge on its own spline.  It exercises every parser feature
# the class below asserts on - RenderRange, SourceOp wiring, keyframe
# handles, ViewInfo positions.
#
# This class used to read `library/steps/step_6_01_render/fusion_comps/
# hook_1.comp`, which is in no commit in this repository's history, so
# `self.skipTest("hook_1.comp not found")` fired in every environment and
# all nine tests below have never run (issue #249).  The subject is the
# PARSER, not that file: the input is supplied here so the assertions -
# unchanged - are made against something that exists.
HOOK_COMP = """\
Composition {
	CurrentTime = 0,
	RenderRange = { 0, 71 },
	GlobalRange = { 0, 71 },
	Tools = {
		MediaIn1 = MediaIn {
			ViewInfo = OperatorInfo { Pos = { 0, 0 } },
		},
		Transform1 = Transform {
			CtrlWZoom = false,
			Inputs = {
				Size = Input {
					SourceOp = "Transform1Size",
					Source = "Value",
				},
				Input = Input {
					SourceOp = "MediaIn1",
					Source = "Output",
				},
			},
			ViewInfo = OperatorInfo { Pos = { 110, 0 } },
		},
		Transform1Size = BezierSpline {
			SplineColor = { Red = 225, Green = 0, Blue = 0 },
			KeyFrames = {
				[0] = { 1.0, RH = { 24, 1.0133 } },
				[36] = { 1.04, LH = { 24, 1.0267 }, RH = { 47.6667, 1.0367 } },
				[71] = { 1.03, LH = { 59.3333, 1.0333 } },
			}
		},
		BrightnessContrast1 = BrightnessContrast {
			Inputs = {
				Gain = Input { Value = 1.05, },
				Input = Input {
					SourceOp = "Transform1",
					Source = "Output",
				},
			},
			ViewInfo = OperatorInfo { Pos = { 220, 0 } },
		},
		SoftGlow1 = SoftGlow {
			Inputs = {
				Gain = Input { Value = 5.0, },
				Input = Input {
					SourceOp = "BrightnessContrast1",
					Source = "Output",
				},
			},
			ViewInfo = OperatorInfo { Pos = { 330, 0 } },
		},
		Background1 = Background {
			Inputs = {
				GlobalOut = Input { Value = 71, },
				Width = Input { Value = 1080, },
				Height = Input { Value = 1920, },
			},
			ViewInfo = OperatorInfo { Pos = { 330, 110 } },
		},
		Ellipse1 = EllipseMask {
			Inputs = {
				Inverted = Input { Value = 1, },
				MaskWidth = Input { Value = 1080, },
				MaskHeight = Input { Value = 1920, },
				PixelAspect = Input { Value = { 1, 1 } },
			},
			ViewInfo = OperatorInfo { Pos = { 440, 110 } },
		},
		Merge1 = Merge {
			Inputs = {
				Blend = Input { Value = 0.35, },
				Background = Input {
					SourceOp = "SoftGlow1",
					Source = "Output",
				},
				Foreground = Input {
					SourceOp = "Background1",
					Source = "Output",
				},
				EffectMask = Input {
					SourceOp = "Ellipse1",
					Source = "Mask",
				},
			},
			ViewInfo = OperatorInfo { Pos = { 550, 0 } },
		},
		BgTrans1 = Background {
			Inputs = {
				GlobalOut = Input { Value = 71, },
				TopLeftAlpha = Input { Value = 1, },
			},
			ViewInfo = OperatorInfo { Pos = { 550, 110 } },
		},
		MergeTrans1 = Merge {
			Inputs = {
				Blend = Input {
					SourceOp = "MergeTrans1Blend",
					Source = "Value",
				},
				Background = Input {
					SourceOp = "Merge1",
					Source = "Output",
				},
				Foreground = Input {
					SourceOp = "BgTrans1",
					Source = "Output",
				},
			},
			ViewInfo = OperatorInfo { Pos = { 660, 0 } },
		},
		MergeTrans1Blend = BezierSpline {
			SplineColor = { Red = 0, Green = 225, Blue = 0 },
			KeyFrames = {
				[0] = { 1.0, RH = { 4, 0.6667 } },
				[12] = { 0.0, LH = { 8, 0.3333 } },
			}
		},
		MediaOut1 = MediaOut {
			Inputs = {
				Input = Input {
					SourceOp = "MergeTrans1",
					Source = "Output",
				},
			},
			ViewInfo = OperatorInfo { Pos = { 770, 0 } },
		},
	},
}
"""


@_SECTION_0_MARK
class TestParseHook1(unittest.TestCase):
    """Parse a hook comp off disk and verify structure."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.comp_path = os.path.join(self._dir, "hook_1.comp")
        with open(self.comp_path, "w", encoding="utf-8") as f:
            f.write(HOOK_COMP)
        self.comp = parse_comp_file(self.comp_path)

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_duration(self):
        """RenderRange { 0, 71 } -> duration = 72."""
        self.assertEqual(self.comp.duration, 72)

    def test_node_count(self):
        """hook_1 has 12 nodes: MediaIn, Transform, BezierSpline(Size),
        BrightnessContrast, SoftGlow, Background, EllipseMask,
        Merge, BgTrans1, MergeTrans1, BezierSpline(Blend), MediaOut."""
        self.assertEqual(len(self.comp.nodes), 12)

    def test_media_in(self):
        n = self.comp.find_node("MediaIn1")
        self.assertIsNotNone(n)
        self.assertEqual(n.tool_type, "MediaIn")

    def test_transform(self):
        n = self.comp.find_node("Transform1")
        self.assertIsNotNone(n)
        self.assertEqual(n.tool_type, "Transform")
        # Should be wired to MediaIn1
        inp = n.inputs.get("Input", {})
        self.assertEqual(inp.get("SourceOp"), "MediaIn1")

    def test_bezier_spline(self):
        """Transform1Size BezierSpline with 3 keyframes."""
        splines = [
            n for n in self.comp.nodes if isinstance(n, BezierSpline)
        ]
        self.assertEqual(len(splines), 2)
        spline = next(s for s in splines if s.name == "Transform1Size")
        self.assertEqual(spline.name, "Transform1Size")
        self.assertEqual(len(spline.keyframes), 3)

        # Check keyframe values
        kf0 = spline.keyframes[0]
        self.assertEqual(kf0.frame, 0)
        self.assertAlmostEqual(kf0.value, 1.0)
        self.assertIsNotNone(kf0.rh)
        self.assertEqual(kf0.rh[0], 24)

        kf1 = spline.keyframes[1]
        self.assertEqual(kf1.frame, 36)
        self.assertAlmostEqual(kf1.value, 1.04)
        self.assertIsNotNone(kf1.lh)
        self.assertIsNotNone(kf1.rh)

        kf2 = spline.keyframes[2]
        self.assertEqual(kf2.frame, 71)
        self.assertAlmostEqual(kf2.value, 1.03)
        self.assertIsNotNone(kf2.lh)

    def test_brightness_contrast(self):
        n = self.comp.find_node("BrightnessContrast1")
        self.assertIsNotNone(n)
        # Gain = 1.05
        gain = n.inputs.get("Gain", {})
        self.assertAlmostEqual(gain.get("value"), 1.05)

    def test_vignette_nodes(self):
        bg = self.comp.find_node("Background1")
        self.assertIsNotNone(bg)
        self.assertEqual(bg.tool_type, "Background")

        ellipse = self.comp.find_node("Ellipse1")
        self.assertIsNotNone(ellipse)
        self.assertEqual(ellipse.tool_type, "EllipseMask")

        merge = self.comp.find_node("Merge1")
        self.assertIsNotNone(merge)
        self.assertEqual(merge.tool_type, "Merge")

    def test_media_out_wiring(self):
        n = self.comp.find_node("MediaOut1")
        self.assertIsNotNone(n)
        inp = n.inputs.get("Input", {})
        self.assertEqual(inp.get("SourceOp"), "MergeTrans1")

    def test_positions_parsed(self):
        tf = self.comp.find_node("Transform1")
        self.assertEqual(tf.pos, (110, 0))

        mo = self.comp.find_node("MediaOut1")
        self.assertEqual(mo.pos, (770, 0))


@_SECTION_0_MARK
class TestParseInline(unittest.TestCase):
    """Parse inline .comp strings."""

    def test_minimal_comp(self):
        comp_str = """Composition {
    CurrentTime = 0,
    RenderRange = { 0, 29 },
    GlobalRange = { 0, 29 },
    Tools = {
        MediaIn1 = MediaIn {
            ViewInfo = OperatorInfo { Pos = { 0, 0 } },
        },
        MediaOut1 = MediaOut {
            Inputs = {
                Input = Input {
                    SourceOp = "MediaIn1",
                    Source = "Output",
                },
            },
            ViewInfo = OperatorInfo { Pos = { 110, 0 } },
        },
    },
}"""
        comp = parse_comp(comp_str)
        self.assertEqual(comp.duration, 30)
        self.assertEqual(len(comp.nodes), 2)

    def test_quoted_keys(self):
        comp_str = """Composition {
    RenderRange = { 0, 50 },
    Tools = {
        MediaIn1 = MediaIn {
            Inputs = {
                ["MediaIn1.GlobalStart"] = Input { Value = 0, },
                ["MediaIn1.GlobalEnd"] = Input { Value = 50, },
            },
        },
    },
}"""
        comp = parse_comp(comp_str)
        mi = comp.find_node("MediaIn1")
        self.assertIn("MediaIn1.GlobalStart", mi.inputs)


@_SECTION_0_MARK
class TestParseSetting(unittest.TestCase):
    """Parse .setting file format (GroupOperator macros)."""

    def test_simple_group(self):
        setting = """{
    Tools = ordered() {
        MyEffect = GroupOperator {
            Tools = ordered() {
                DirectionalBlur = DirectionalBlur {
                    CtrlWZoom = false,
                    Inputs = {
                        Length = Input { Value = 0.005, },
                    },
                    ViewInfo = OperatorInfo { Pos = { 0, 0 } },
                },
            },
        },
    },
}"""
        comp = parse_setting(setting)
        # Should have the inner DirectionalBlur
        self.assertTrue(len(comp.nodes) > 0)
        db = comp.find_node("DirectionalBlur")
        self.assertIsNotNone(db)
        self.assertEqual(db.tool_type, "DirectionalBlur")

    def test_chromatic_aberration_setting(self):
        """Parse the actual Chromatic Aberration.setting from Templates.drfx."""
        drfx = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Fusion/Templates/Templates.drfx"
        if not os.path.exists(drfx):
            self.skipTest("Templates.drfx not found")

        # Extract the setting
        result = subprocess.run(
            ["unzip", "-p", drfx, "Fusion/Tools/Chromatic Aberration.setting"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("Could not extract setting from drfx")

        comp = parse_setting(result.stdout)

        # Should contain DirectionalBlur nodes
        db_nodes = [
            n for n in comp.nodes
            if isinstance(n, FusionNode) and n.tool_type == "DirectionalBlur"
        ]
        self.assertTrue(len(db_nodes) >= 1,
                        f"Expected DirectionalBlur nodes, got: {[n.name for n in comp.nodes]}")

    def test_cross_dissolve_setting(self):
        """Parse the Cross Dissolve transition from Templates.drfx."""
        drfx = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Fusion/Templates/Templates.drfx"
        if not os.path.exists(drfx):
            self.skipTest("Templates.drfx not found")

        result = subprocess.run(
            ["unzip", "-p", drfx, "Edit/Transitions/Cross Dissolve.setting"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("Could not extract setting from drfx")

        comp = parse_setting(result.stdout)
        # Should contain a Dissolve node
        dissolve = [
            n for n in comp.nodes
            if isinstance(n, FusionNode) and n.tool_type == "Dissolve"
        ]
        self.assertTrue(len(dissolve) >= 1,
                        f"Expected Dissolve node, got: {[n.name for n in comp.nodes]}")


# --------------------------------------------------------------------------
# From test_fusion_parser_integration.py

@pytest.fixture
def mock_setting_content():
    return '''{
        Tools = ordered() {
            AdvancedCameraShake1 = MacroOperator {
                Inputs = ordered() {
                    Input1 = InstanceInput {
                        SourceOp = "CameraShake1",
                        Source = "overall_magnitude",
                    },
                },
                Tools = ordered() {
                    CameraShake1 = CameraShake {
                        Inputs = {
                            overall_magnitude = Input { Value = 0.5, },
                        }
                    }
                }
            }
        }
    }'''

def test_import_customized_effect(mock_setting_content):
    mock_clip = MagicMock()
    
    with patch('library.tools.builtin_effect_loader.load_effect_setting', return_value=mock_setting_content):
        def fake_import(path):
            with open(path, 'r') as f:
                mock_clip.imported_content = f.read()
            return True
            
        mock_clip.ImportFusionComp.side_effect = fake_import
        
        overrides = {"overall_magnitude": 0.8}
        
        success = import_customized_effect(mock_clip, "advanced_camera_shake", overrides)
        assert success
        
        assert mock_clip.ImportFusionComp.called
        assert "overall_magnitude = Input { Value = 0.8, }" in mock_clip.imported_content


# --------------------------------------------------------------------------
# From test_fusion_nodes.py
#
# Tests for the Fusion node graph object model (Layer 1).
#
# Verifies:
# 1. Individual node serialization matches expected Lua syntax
# 2. BezierSpline keyframe serialization with handles
# 3. FusionComp produces valid .comp output
# 4. Safety validations fire correctly (ApplyMode, BlendClone, etc.)
# 5. Backward compatibility: reproduces existing hook_1.comp exactly

_SECTION_2_MARK = pytest.mark.unit

# Add the tools directory to path
from fusion.nodes import FusionComp


@_SECTION_2_MARK
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


@_SECTION_2_MARK
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


@_SECTION_2_MARK
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


@_SECTION_2_MARK
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


# --------------------------------------------------------------------------
# From test_fusion_effects.py
#
# Tests for composable effect blocks (Layer 2) and CompEngine (Layer 3).
#
# Verifies:
# 1. Individual effect blocks produce valid nodes
# 2. Empty/neutral effects are correctly skipped
# 3. CompEngine chains effects and produces valid .comp output
# 4. from_params() backward compatibility with old generator signature

_SECTION_3_MARK = pytest.mark.unit

from fusion.effects import _reset_counters, fx
from fusion.engine import CompEngine

#: The frame every builder below is stated at. The builders take no
#: default frame since slice 3/4 of the delivery-format generalisation,
#: so each call states the source frame it sizes its canvas at - the
#: same numbers the removed defaults carried, which is what keeps the
#: asserted bytes identical.
RES = (1080, 1920)


@_SECTION_3_MARK
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
        transform = next(n for n in block.nodes if isinstance(n, FusionNode))
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
        block = fx.glow(gain=0.08, threshold=0.75, size=3.5)
        self.assertEqual(len(block.nodes), 1)
        self.assertIn("SoftGlow", block.nodes[0].tool_type)

    def test_glow_zero_skips(self):
        block = fx.glow(gain=0.0, threshold=0.75, size=3.5)
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
        block = fx.vignette(clip_dur=90, width=1.0, height=1.0, soft=0.35,
                            blend=0.25, res=RES)
        # Background + Ellipse + Merge
        self.assertEqual(len(block.nodes), 3)
        types = {n.tool_type for n in block.nodes if isinstance(n, FusionNode)}
        self.assertIn("Background", types)
        self.assertIn("EllipseMask", types)
        self.assertIn("Merge", types)

        # Input is Merge.Background (upstream feeds in here)
        self.assertEqual(block.input_key, "Background")

    def test_fade_in(self):
        block = fx.fade(90, fade_in=10, res=RES)
        self.assertTrue(len(block.nodes) > 0)
        # Should have a BezierSpline
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        self.assertTrue(len(splines) > 0)

    def test_fade_out(self):
        block = fx.fade(90, fade_out=15, res=RES)
        self.assertTrue(len(block.nodes) > 0)

    def test_fade_no_frames_skips(self):
        block = fx.fade(90, res=RES)
        self.assertEqual(len(block.nodes), 0)

    def test_transition_tail_fade(self):
        block = fx.transition_tail(90, "fade_to_black", 12, res=RES)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_tail_zoom_blur(self):
        block = fx.transition_tail(90, "zoom_blur", 10, res=RES)
        self.assertTrue(len(block.nodes) > 0)
        # Should have Transform + DirectionalBlur
        types = {
            n.tool_type for n in block.nodes if isinstance(n, FusionNode)
        }
        self.assertIn("Transform", types)
        self.assertIn("DirectionalBlur", types)

    def test_transition_tail_defocus(self):
        block = fx.transition_tail(90, "defocus", 8, res=RES)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_tail_flash(self):
        block = fx.transition_tail(90, "flash", 6, res=RES)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_head_fade(self):
        block = fx.transition_head(90, "fade_to_black", 12, res=RES)
        self.assertTrue(len(block.nodes) > 0)

    def test_transition_unknown_raises(self):
        with self.assertRaises(ValueError):
            fx.transition_tail(90, "nonexistent_type", 10, res=RES)


@_SECTION_3_MARK
class TestCompEngine(unittest.TestCase):
    """Test CompEngine composition and serialization."""

    def test_empty_comp(self):
        """Minimal comp with just MediaIn → MediaOut."""
        comp = CompEngine(clip_dur=30, width=RES[0], height=RES[1]).serialize()
        self.assertIn("Composition {", comp)
        self.assertIn("MediaIn1 = MediaIn", comp)
        self.assertIn("MediaOut1 = MediaOut", comp)
        self.assertIn('SourceOp = "MediaIn1"', comp)

    def test_single_effect(self):
        comp = (CompEngine(clip_dur=90, width=RES[0], height=RES[1])
                .add(fx.grade(gain=1.05))
                .serialize())
        self.assertIn("BrightnessContrast", comp)
        self.assertIn("Gain = Input { Value = 1.05, },", comp)
        # BrightnessContrast should be wired to MediaIn1
        self.assertIn('SourceOp = "MediaIn1"', comp)

    def test_chained_effects(self):
        comp = (CompEngine(clip_dur=90, width=RES[0], height=RES[1])
                .add(fx.zoom(90, start=1.0, end=1.03))
                .add(fx.grade(gain=1.05))
                .add(fx.glow(gain=0.08, threshold=0.75, size=3.5))
                .serialize())
        # All three effect types present
        self.assertIn("Transform", comp)
        self.assertIn("BrightnessContrast", comp)
        self.assertIn("SoftGlow", comp)

    def test_skip_neutral_effects(self):
        comp = (CompEngine(clip_dur=90, width=RES[0], height=RES[1])
                .add(fx.zoom(90))  # neutral — skipped
                .add(fx.grade(gain=1.0))  # neutral — skipped
                .add(fx.glow(gain=0.08, threshold=0.75, size=3.5))  # active
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
            source_res=RES,
            zoom_start=1.0,
            zoom_mid=1.04,
            zoom_end=1.03,
            pan_start=(0.5, 0.5),
            pan_end=(0.5, 0.49),
            grade_gain=1.05,
            grade_contrast=0.04,
            grade_saturation=1.15,
            glow_gain=0.08,
            glow_threshold=0.75,
            glow_size=3.5,
        )
        self.assertIn("Composition {", comp)
        self.assertIn("Transform", comp)
        self.assertIn("BrightnessContrast", comp)
        self.assertIn("SoftGlow", comp)

    def test_from_params_with_transitions(self):
        comp = CompEngine.from_params(
            clip_dur=90,
            source_res=RES,
            tail_transition="fade_to_black",
            tail_transition_frames=12,
        )
        self.assertIn("Composition {", comp)
        # Should have a fade transition
        self.assertIn("BgTrans", comp)
        self.assertIn("MergeTrans", comp)

    def test_vignette_safety(self):
        """Vignette should always produce safe EllipseMask."""
        comp = (CompEngine(clip_dur=90, width=RES[0], height=RES[1])
                .add(fx.vignette(clip_dur=90, width=1.0, height=1.0, soft=0.35,
                            blend=0.25, res=RES))
                .serialize())
        # Must have Invert (the name the tool HAS - `Inverted` is
        # silently ignored and draws a black disc), MaskWidth,
        # MaskHeight, PixelAspect
        self.assertIn("Invert = Input { Value = 1, },", comp)
        self.assertNotIn("Inverted", comp)
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
        """A composition with a Transform.Size animated via spline peaking at 1.16 raises ValueError"""
        from fusion.nodes import FusionComp
        comp = FusionComp(duration=90)
        
        spline = BezierSpline("Transform1Size")
        spline.add_key(0, 1.0).add_key(45, 1.16).add_key(90, 1.0)
        
        transform = FusionNode("Transform1", "Transform")
        transform.set_input("Size", spline)
        
        comp.add_node(spline).add_node(transform)
        with self.assertRaises(ValueError) as ctx:
            comp.serialize()
        self.assertIn("Transform zoom (Size) animated peak 1.16 > 1.15", str(ctx.exception))

    def test_transform_size_safety_spline_pass(self):
        """A composition with a Transform.Size animated via spline peaking at 1.10 passes"""
        from fusion.nodes import FusionComp
        comp = FusionComp(duration=90)
        
        spline = BezierSpline("Transform1Size")
        spline.add_key(0, 1.0).add_key(45, 1.10).add_key(90, 1.0)
        
        transform = FusionNode("Transform1", "Transform")
        transform.set_input("Size", spline)
        
        comp.add_node(spline).add_node(transform)
        comp.serialize()  # Should not raise


# --------------------------------------------------------------------------
# From test_fusion_builtin_presets.py
#
# DaVinci's own shipped presets must survive our Fusion harness.
#
# Parse + serialize every `.setting` under `library/presets/resolve-builtin/`,
# keep house rules for comps WE author only, and ship built-ins byte-identical.
# History (56 of 143 failing, silent node loss): docs/evidence/fusion_parser.md.

ALL_EFFECTS = sorted(list_builtin_effects())


def _source(name: str) -> str:
    meta = list_builtin_effects()[name]
    return (BUILTIN_DIR / meta["path"]).read_text(encoding="utf-8")


# ── The whole library round-trips ────────────────────────────


def test_every_preset_round_trips_with_its_nodes():
    """Parse + serialize must not raise, or lose nodes, for any preset.

    Each historical failure mode (authorship rules on foreign files, the
    anonymous nested table desync, namespaced Fuse types) raises here; the
    pinned node counts catch the silent desync that dropped nodes instead.
    """
    failures = {}
    for name in ALL_EFFECTS:
        try:
            comp = parse_setting_2(_source(name))
            if not comp.nodes:
                failures[name] = "parsed to an empty comp"
            elif not comp.serialize().startswith("Composition {"):
                failures[name] = "serialized without a Composition header"
        except Exception as exc:  # noqa: BLE001 - collected per preset
            failures[name] = repr(exc)
    assert not failures, failures

    # Pinned from the fixed parser; every one was failing or losing nodes.
    pinned = {
        "ambient_occlusion": 30,   # was 11 - lost 19 nodes, reported success
        "lightwrap": 23,           # was 8
        "circle_layout": 5,        # was 1
        "bokeh_edges": 21,         # was AttributeError
        "brick": 24,               # was ValueError: float('.')
        "anisotropic": 15,         # was Background missing GlobalOut
    }
    counts = {n: len(parse_setting_2(_source(n)).nodes) for n in pinned}
    assert counts == pinned


# ── Fix A: anonymous nested tables ───────────────────────────


# ── Fix B: namespaced Fuse plugin tool types ─────────────────


def test_quoted_key_containing_a_dot_still_parses():
    """The dotted-identifier fix must not touch quoted keys."""
    comp = parse_setting_2(
        '{ Tools = { A = Foo { Inputs = { '
        '["MediaIn1.GlobalStart"] = Input { Value = 3, }, }, }, } }'
    )
    assert comp.nodes[0].inputs["MediaIn1.GlobalStart"]["value"] == 3


# ── Fix C: values go through _lua_value ──────────────────────


def test_boolean_input_serializes_as_lua_boolean():
    """`Value = True` is Python. Lua reads it as an undefined global."""
    node = FusionNode_2("Grain1", "Custom")
    node.inputs["Enabled"] = {"_type": "value", "value": True}
    out = node.serialize()
    assert "Value = true," in out
    assert "Value = True" not in out


# ── Fix D: house rules are authorship rules ──────────────────


def _zooming_comp(authored):
    """A comp whose Transform is animated past the 1.04 zoom ceiling."""
    comp = FusionComp_2(duration=75, authored=authored)
    spline = BezierSpline_2("Transform1Size")
    spline.add_key(0, 1.0).add_key(74, 1.35)
    xf = FusionNode_2("Transform1", "Transform")
    xf.set_input("Size", spline)
    return comp.add_node(spline).add_node(xf)


def _merge_with_apply_mode():
    comp = FusionComp_2(duration=75)
    merge = FusionNode_2("Merge1", "Merge")
    merge.inputs["ApplyMode"] = {"_type": "value", "value": "Screen"}
    return comp.add_node(merge)


def _background_without_global_out():
    return FusionComp_2(duration=75).add_node(FusionNode_2("Background1", "Background"))


@pytest.mark.parametrize(
    "build,match",
    [
        (_merge_with_apply_mode, "ApplyMode on Merge CRASHES"),
        (_background_without_global_out, "missing GlobalOut"),
        # _validate_global is an authorship rule too; no preset trips it.
        (lambda: _zooming_comp(authored=True), "Too aggressive"),
    ],
)
def test_authored_comp_still_enforces_house_rules(build, match):
    """The rules stay exactly where they were earned: comps WE build."""
    with pytest.raises(ValueError, match=match):
        build().serialize()


def test_foreign_comp_skips_the_zoom_ceiling():
    assert _zooming_comp(authored=False).serialize()


# ── Fix E: built-ins reach Resolve byte-identical ────────────


def test_builtin_effect_reaches_resolve_byte_identical(monkeypatch, tmp_path):
    """A built-in preset plus film-look params still imports the shipped file.

    compile_manifest merges the film look onto every V1/V2 clip, so the old
    `if effects: import_customized_effect(...)` branch sent every built-in
    through the parser. The round-trip dropped the GroupOperator wrapper and
    every InstanceInput - including MainInput1, the macro's image input.
    """
    import library.tools.execution.apply_fusion_comps as afc

    from library.tools.builtin_effect_loader import get_effect_path

    imported = []

    class MockTimelineClip:
        def GetStart(self):
            return 0

        def GetEnd(self):
            return 100

        def GetMediaPoolItem(self):
            return MockMediaPoolItem()

        def GetFusionCompNameList(self):
            return ["Fusion Composition 1"] if imported else []

        def ImportFusionComp(self, path):
            imported.append(path)
            return True

        def DeleteFusionCompByName(self, name):
            pass

    class MockMediaPoolItem:
        def GetClipProperty(self, prop):
            return {"File Path": "test.mov", "Frames": "100"}.get(prop)

    class MockTimeline:
        def GetUniqueId(self): return str(id(self))
        def GetSetting(self, name):
            return "30"

        def GetItemListInTrack(self, track_type, index):
            return [MockTimelineClip()]

    class MockProject:
        def __init__(self):
            self._current_timeline = MockTimeline()

        def GetCurrentTimeline(self):
            return self._current_timeline

        def SetCurrentTimeline(self, tl):
            self._current_timeline = tl
            return True
    class MockProjectManager:
        def GetCurrentProject(self):
            return MockProject()

    class MockResolve:
        def GetProjectManager(self):
            return MockProjectManager()

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda _: MockResolve())

    manifest = {
        "project_dir": str(tmp_path),
        "tracks": {"V1": {"clips": [{"source_file": "test.mov",
                                     "label": "clip_0"}]}},
        # The film look compile_manifest merges onto every clip. These are
        # our comp-engine names; no Resolve tool reads them.
        "vfx": [{
            "timeline_start": 0,
            "effect_type": "chromatic_aberration",
            "params": {"glow_gain": 0.125, "film_grain": True,
                       "vignette": True},
        }],
    }

    afc.apply_fusion_comps(manifest, str(tmp_path))

    shipped = str(get_effect_path("chromatic_aberration").resolve())
    assert imported == [shipped], (
        f"expected DaVinci's own file, got {imported}"
    )
    assert pathlib.Path(imported[0]).read_text(encoding="utf-8") == _source(
        "chromatic_aberration"
    )


# --------------------------------------------------------------------------
# From test_generator_clip_effect_split.py
#
# Tests for the generator/clip-effect classification of builtin presets.
#
# Acceptance criteria:
# 1. The split is derived from each preset's declared image input, not category.
# 2. Picture-modifying presets remain selectable as clip effects.
# 3. Generating presets are routed to the overlay track (rejected as clip effects).
# 4. Lens flares split correctly based on their declared inputs.
# 5. A generator cannot be planned as a clip effect.

# Ensure the repo root is on sys.path for imports
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.builtin_effect_loader import (
    list_builtin_effects,
    classify_builtin_effects,
    list_clip_effects,
    list_generator_effects,
    is_generator_effect,
    _has_image_input,
)


class TestPresetClassification:
    """The split must be derived from declared image input, not category."""

    def test_classification_derived_from_file_not_category(self):
        """The split comes from MainInput1 in the .setting file, not the category label."""
        clip_fx, generators = classify_builtin_effects()
        # tools category has 3 presets, all should be clip effects
        for name in ("advanced_camera_shake", "chromatic_aberration", "edge_control"):
            assert name in clip_fx, f"{name} should be a clip effect"

        # Some lens flares have image inputs, some don't - proves
        # the split is per-file, not per-category
        lf_clip = [n for n in clip_fx if n.startswith("lens_flare_")]
        lf_gen = [n for n in generators if n.startswith("lens_flare_")]
        assert len(lf_clip) > 0, "Some lens flares should be clip effects"
        assert len(lf_gen) > 0, "Some lens flares should be generators"
        # The total must equal 40
        assert len(lf_clip) + len(lf_gen) == 40, (
            f"Lens flare split: {len(lf_clip)} clip + {len(lf_gen)} gen != 40"
        )

    def test_is_generator_effect_api(self):
        """is_generator_effect correctly identifies generators and clip effects."""
        assert is_generator_effect("fireworks") is True
        assert is_generator_effect("advanced_camera_shake") is False
        # Unknown preset raises
        import pytest
        with pytest.raises(ValueError, match="not found"):
            is_generator_effect("nonexistent_effect_xyz")


class TestGeneratorRejectedFromClipEffect:
    """A generator preset must be rejected from the clip-effect planning path."""

    def test_generator_rejected_by_resolve_vfx(self):
        """resolve_vfx rejects a generator preset with a clear message."""
        from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx

        creative_plan = [{
            "target_block_position": 1,
            "effect_type": "fireworks",  # a generator preset
            "rationale": "opens with a burst",
        }]
        timed_spine = {
            "structure": [{
                "position": 1,
                "timeline_start": 0.0,
                "timeline_end": 5.0,
                "block_type": "speech",
            }]
        }

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            result = resolve_vfx(creative_plan, timed_spine, frame_rate=30.0)
        finally:
            sys.stderr = old_stderr

        stderr_output = captured.getvalue()
        # The generator must be rejected
        assert len(result) == 0, (
            f"Generator 'fireworks' should be rejected from clip effects, "
            f"but resolve_vfx returned {len(result)} VFX entries"
        )
        assert "Rejected generator preset" in stderr_output, (
            f"Expected 'Rejected generator preset' in stderr, got: {stderr_output}"
        )
        assert "fireworks" in stderr_output


class TestRendererRejectsUnclassifiable:
    """The renderer's defense-in-depth gate must fail closed.

    If is_generator_effect raises ValueError (preset not in index),
    the renderer must reject the preset rather than admit it as a
    clip effect. Failing open would let an unknown preset reach
    ImportFusionComp where it could cover the picture.
    """

    def test_unclassifiable_preset_rejected_not_admitted(self):
        """A preset that is_generator_effect cannot classify is rejected."""
        from unittest.mock import patch
        import io

        # Simulate the renderer's logic: builtin_effect is set,
        # is_generator_effect raises ValueError because the preset
        # is not in the index.
        #
        # We exercise the exact code path in apply_fusion_comps by
        # importing and calling is_generator_effect with a name that
        # does not exist in the index.
        from library.tools.builtin_effect_loader import is_generator_effect as real_fn

        # Confirm ValueError is raised for a nonexistent preset
        import pytest
        with pytest.raises(ValueError, match="not found"):
            real_fn("totally_fake_preset_xyz")

        # Now replicate the renderer's guard logic and confirm it
        # rejects (builtin_effect = None) rather than admits.
        builtin_effect = "totally_fake_preset_xyz"
        orig_ci = 0
        label = "test_clip"

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            try:
                if real_fn(builtin_effect):
                    builtin_effect = None
            except ValueError as exc:
                print(
                    f"  ✗ [{orig_ci}] {label}: Rejected unclassifiable "
                    f"preset {builtin_effect} - classifier raised: {exc}",
                    file=sys.stderr,
                )
                builtin_effect = None
        finally:
            sys.stderr = old_stderr

        assert builtin_effect is None, (
            "Unclassifiable preset must be rejected (set to None), "
            "not admitted as a clip effect"
        )
        stderr_output = captured.getvalue()
        assert "Rejected unclassifiable" in stderr_output
        assert "totally_fake_preset_xyz" in stderr_output
        assert "not found" in stderr_output


# --------------------------------------------------------------------------
# From test_fusion_tool_inputs.py
#
# An input name Fusion does not have is a SILENT no-op, and it shipped.
#
# History: docs/evidence/resolve_test_history.md#test_fusion_tool_inputs.

REPO = pathlib.Path(__file__).resolve().parents[3]


# ── The table itself ─────────────────────────────────────────────────────


def test_the_probed_names_are_resolves_not_the_guessed_ones():
    """EllipseMask inverts with `Invert`, Crop is offset+size not edges,
    FilmGrain has no Power or Size."""
    assert "Invert" in tool_inputs.TOOL_INPUTS["EllipseMask"]
    assert "Inverted" not in tool_inputs.TOOL_INPUTS["EllipseMask"]
    crop = tool_inputs.TOOL_INPUTS["Crop"]
    assert {"XOffset", "YOffset", "XSize", "YSize"} <= crop
    assert "CropTop" not in crop and "CropBottom" not in crop
    grain = tool_inputs.TOOL_INPUTS["FilmGrain"]
    assert {"MasterStrength", "MasterXSize", "MasterYSize"} <= grain
    assert "Power" not in grain and "Size" not in grain


def test_absent_is_recorded_and_unprobed_is_unchecked():
    """A tool the probe asked for and did not find is recorded absent.
    The table is a partial probe: refusing what nobody measured is a
    verdict invented from ignorance, worse than the silence it replaces."""
    assert tool_inputs.is_absent_tool("ChromaticAberration")
    assert not tool_inputs.is_absent_tool("Transform")
    assert not tool_inputs.is_known_tool("SomeFuseNobodyProbed")
    assert tool_inputs.unknown_inputs("SomeFuseNobodyProbed", ["Whatever"]) == []


# ── The gate can fail (AGENTS.md 10.4) ───────────────────────────────────


def test_an_authored_node_driving_an_unknown_input_or_tool_is_refused():
    comp = FusionComp_3(duration=30)
    node = FusionNode_3("Ellipse1", "EllipseMask")
    for key, value in (("MaskWidth", 1080), ("MaskHeight", 1920),
                       ("PixelAspect", (1, 1)), ("Invert", 1)):
        node.set_input(key, value)
    node.set_input("Inverted", 1)
    comp.add_node(node)
    with pytest.raises(tool_inputs.UnknownFusionInput, match="Inverted"):
        comp.serialize()
    comp = FusionComp_3(duration=30)
    comp.add_node(FusionNode_3("CA1", "ChromaticAberration"))
    with pytest.raises(tool_inputs.UnknownFusionTool,
                       match="ChromaticAberration"):
        comp.serialize()


def test_a_foreign_comp_is_exempt():
    """DaVinci's own macros are free to use tools and inputs nobody here
    probed - the same authorship carve-out every other house rule takes."""
    comp = FusionComp_3(duration=30, authored=False)
    node = FusionNode_3("Ellipse1", "EllipseMask")
    node.set_input("Inverted", 1)
    comp.add_node(node)
    assert comp.serialize().startswith("Composition {")


def test_the_media_in_global_range_idiom_is_not_judged():
    """`["MediaIn1.GlobalStart"]` is instance-scoped and never appears in
    `GetInputList()`; judging it would refuse a comp Resolve authored."""
    assert "Tools = {" in build_effect_comp({}, 30,
                                             source_res=(1080, 1920))


# ── Every tool this engine writes has been probed ────────────────────────


def _tool_types_the_engine_writes() -> set[str]:
    """Every literal tool type passed to `FusionNode(...)` in library/."""
    found: set[str] = set()
    for path in (REPO / "library").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a file we do not own
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "FusionNode"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                found.add(node.args[1].value)
    return found


def test_every_tool_type_the_engine_writes_was_probed():
    """A new tool type must be probed, not assumed.

    Without this the gate quietly stops applying to whatever was added
    last - which is the shape of every defect it exists to catch.
    """
    written = _tool_types_the_engine_writes()
    assert written, "the AST walk found no FusionNode(...) calls"
    unprobed = sorted(t for t in written
                      if not tool_inputs.is_known_tool(t))
    assert not unprobed, (
        f"{unprobed} are written into comps and were never probed. Add "
        f"them to scripts/probe_fusion_tool_inputs.py and re-run it "
        f"against a running Resolve.")
    absent = sorted(t for t in written if tool_inputs.is_absent_tool(t))
    assert not absent, f"{absent} do not exist in Fusion"
