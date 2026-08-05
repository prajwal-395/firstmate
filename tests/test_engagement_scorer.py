import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../library/tools')))
from engagement_scorer import score_hook, score_flow, score_value, compute_engagement

class TestEngagementScorer(unittest.TestCase):
    def test_score_hook_high(self):
        segment = {"text": "Did you know this is amazing?"}
        prosody = {"energy_rms": 0.9}
        semantic = {}
        score = score_hook(segment, prosody, semantic)
        self.assertGreater(score, 70)

    def test_score_hook_low(self):
        segment = {"text": "Um well I think"}
        prosody = {"energy_rms": 0.2}
        semantic = {}
        score = score_hook(segment, prosody, semantic)
        self.assertLess(score, 50)

    def test_compute_composite(self):
        segment = {"text": "Imagine if you could learn first then apply this important insight"}
        prosody = {"energy_rms": 0.85}
        semantic = {}
        speech_sequence = []
        result = compute_engagement(segment, prosody, semantic, speech_sequence)
        
        self.assertIn("hook", result)
        self.assertIn("flow", result)
        self.assertIn("value", result)
        self.assertIn("composite", result)
        self.assertTrue(0 <= result["composite"] <= 100)

if __name__ == '__main__':
    unittest.main()
