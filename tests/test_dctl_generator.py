import os
import unittest
from library.tools.dctl_generator import generate_film_emulation_dctl, generate_look_match_dctl

class TestDCTLGenerator(unittest.TestCase):
    def test_generate_film_emulation_dctl(self):
        params = {
            "highlight_rolloff": 0.6,
            "shadow_lift": 0.08,
            "saturation_curve": 1.2,
            "color_temperature_shift": 0.1
        }
        code = generate_film_emulation_dctl(params)
        
        # Check C-like syntax elements
        self.assertIn("__DEVICE__ float3 transform", code)
        self.assertIn("float lift = 0.08f;", code)
        self.assertIn("float temp = 0.1f;", code)
        self.assertIn("float rolloff = 0.6f;", code)
        self.assertIn("float sat = 1.2f;", code)

    def test_generate_look_match_dctl(self):
        stats = {
            "shadows": [0.1, 0.2, 0.3],
            "midtones": [0.4, 0.5, 0.6],
            "highlights": [0.7, 0.8, 0.9]
        }
        code = generate_look_match_dctl(stats, "test_look")
        
        self.assertIn("__DEVICE__ float3 transform", code)
        self.assertIn("Target Shadows: 0.100 0.200 0.300", code)
        
if __name__ == '__main__':
    unittest.main()
