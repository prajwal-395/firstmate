import os
import tempfile
import unittest
from library.tools import look_matcher
from library.tools.look_matcher import (
    LookMatchUnavailable,
    analyze_frame_colors,
    compute_match_cdl,
    clamp,
    match_clips_to_reference,
)

class TestLookMatcher(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(clamp(5, 1, 10), 5)
        self.assertEqual(clamp(0, 1, 10), 1)
        self.assertEqual(clamp(15, 1, 10), 10)
        
    def test_compute_match_cdl_identity(self):
        stats = {
            "shadows": [0.1, 0.1, 0.1],
            "midtones": [0.5, 0.5, 0.5],
            "highlights": [0.9, 0.9, 0.9]
        }
        
        cdl = compute_match_cdl(stats, stats)
        
        self.assertAlmostEqual(cdl["slope"][0], 1.0)
        self.assertAlmostEqual(cdl["offset"][0], 0.0)
        self.assertAlmostEqual(cdl["power"][0], 1.0)
        self.assertEqual(cdl["saturation"], 1.0)

    def test_compute_match_cdl_exposure_diff(self):
        ref_stats = {
            "shadows": [0.1, 0.1, 0.1],
            "midtones": [0.4, 0.4, 0.4],
            "highlights": [0.8, 0.8, 0.8]
        }
        
        tgt_stats = {
            "shadows": [0.05, 0.05, 0.05],
            "midtones": [0.2, 0.2, 0.2],
            "highlights": [0.4, 0.4, 0.4]
        }
        
        cdl = compute_match_cdl(ref_stats, tgt_stats)
        
        # Slope = ref_high / tgt_high = 0.8 / 0.4 = 2.0
        self.assertAlmostEqual(cdl["slope"][0], 2.0)
        # Offset = ref_shadow - tgt_shadow = 0.1 - 0.05 = 0.05
        self.assertAlmostEqual(cdl["offset"][0], 0.05)
        
        # Power = log(0.4) / log(0.2)
        import math
        expected_power = math.log(0.4) / math.log(0.2)
        self.assertAlmostEqual(cdl["power"][0], expected_power)


class TestAnalyzeRefusesUnmeasurableFrames(unittest.TestCase):
    """A frame that cannot be measured must refuse the match, never
    stand in for a measured one.

    `analyze_frame_colors` used to catch every failure and return a
    plausible neutral statistic - the same type as its success path -
    which `match_clips_to_reference` then turned into a CDL the grade
    recorded as "AI Look Match CDL applied". A fabricated neutral is
    invented taste wearing a measurement's clothes.
    """

    NEUTRAL = {
        "shadows": [0.1, 0.1, 0.1],
        "midtones": [0.5, 0.5, 0.5],
        "highlights": [0.9, 0.9, 0.9],
    }

    def _red_square(self, directory):
        from PIL import Image
        path = os.path.join(directory, "red.png")
        Image.new("RGB", (16, 16), (200, 20, 20)).save(path)
        return path

    def test_missing_file_raises_rather_than_returning_neutral(self):
        with self.assertRaises(Exception):
            analyze_frame_colors("/no/such/frame.png")

    def test_undecodable_file_raises_rather_than_returning_neutral(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "frame.png")
            with open(bad, "wb") as f:
                f.write(b"this is not an image")
            with self.assertRaises(Exception):
                analyze_frame_colors(bad)

    def test_pillow_missing_raises_a_named_error(self):
        real = look_matcher.PIL_AVAILABLE
        look_matcher.PIL_AVAILABLE = False
        try:
            with self.assertRaises(LookMatchUnavailable):
                analyze_frame_colors("whatever.png")
        finally:
            look_matcher.PIL_AVAILABLE = real

    def test_match_propagates_an_unmeasurable_clip_frame(self):
        with tempfile.TemporaryDirectory() as d:
            ref = self._red_square(d)
            bad = os.path.join(d, "clip.png")
            with open(bad, "wb") as f:
                f.write(b"not a frame either")
            with self.assertRaises(Exception):
                match_clips_to_reference(ref, {"clip_001": bad})

    def test_measured_frame_still_returns_three_zones(self):
        with tempfile.TemporaryDirectory() as d:
            stats = analyze_frame_colors(self._red_square(d))
            self.assertEqual(
                sorted(stats.keys()), ["highlights", "midtones", "shadows"])
            for zone in stats.values():
                self.assertEqual(len(zone), 3)
        
if __name__ == '__main__':
    unittest.main()
