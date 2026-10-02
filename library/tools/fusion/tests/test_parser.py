"""
Tests for the .comp / .setting parser (Layer 0).

Verifies:
1. Round-trip: parse hook_1.comp → inspect nodes → verify structure
2. BezierSpline keyframe parsing with handles
3. SourceOp wiring reconstruction
4. .setting macro parsing (GroupOperator)
5. Parse Resolve's built-in Chromatic Aberration.setting
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
from fusion.nodes import BezierSpline, FusionNode
from fusion.parser import parse_comp, parse_comp_file, parse_setting

# Navigate: tests/ → fusion/ → tools/ → library/ → (workspace root)
_WORKSPACE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
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


if __name__ == "__main__":
    unittest.main()
