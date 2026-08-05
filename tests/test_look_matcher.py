import os
import unittest
from library.tools.look_matcher import analyze_frame_colors, compute_match_cdl, clamp

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
        
if __name__ == '__main__':
    unittest.main()
