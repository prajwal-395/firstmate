import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../library/tools')))
from neural_engine import apply_magic_mask, apply_smart_reframe, apply_super_scale, apply_stabilization

class MockClip:
    def __init__(self, name="TestClip"):
        self.name = name
        self.properties = {}
        self.magic_mask_result = True
        self.smart_reframe_result = True
        self.stabilize_result = True

    def GetName(self):
        return self.name

    def CreateMagicMask(self, mode):
        return self.magic_mask_result
        
    def SmartReframe(self):
        return self.smart_reframe_result
        
    def SetClipProperty(self, key, value):
        if key == "Super Scale" and value in (2, "2"):
            self.properties[key] = value
            return True
        elif key == "Super Scale":
            # Simulate failure for unsupported scaling
            return False
        self.properties[key] = value
        return True
        
    def Stabilize(self):
        return self.stabilize_result


class MockClipNoAPI:
    def GetName(self):
        return "NoAPIClip"

class TestNeuralEngine(unittest.TestCase):
    def test_apply_magic_mask_success(self):
        clip = MockClip()
        self.assertTrue(apply_magic_mask(clip))
        
    def test_apply_magic_mask_failure(self):
        clip = MockClip()
        clip.magic_mask_result = False
        self.assertFalse(apply_magic_mask(clip))
        
    def test_apply_magic_mask_no_api(self):
        clip = MockClipNoAPI()
        self.assertFalse(apply_magic_mask(clip))
        
    def test_apply_smart_reframe(self):
        clip = MockClip()
        self.assertTrue(apply_smart_reframe(clip, "9:16"))
        
    def test_apply_super_scale_success(self):
        clip = MockClip()
        self.assertTrue(apply_super_scale(clip, 2))
        
    def test_apply_super_scale_failure(self):
        clip = MockClip()
        self.assertFalse(apply_super_scale(clip, 4)) # Simulation fails for 4
        
    def test_apply_stabilization(self):
        clip = MockClip()
        self.assertTrue(apply_stabilization(clip))

if __name__ == '__main__':
    unittest.main()
