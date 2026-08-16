import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../library/tools')))
from neural_engine import apply_super_scale, apply_stabilization


class MockMediaPoolItem:
    """Super Scale lives on the MEDIA POOL item, not the timeline item."""

    def __init__(self):
        self.properties = {}

    def SetClipProperty(self, key, value):
        if key == "Super Scale":
            # Resolve accepts an int and rejects anything else, including
            # the string "2".
            if value != 2:
                return False
        self.properties[key] = value
        return True


class MockClip:
    def __init__(self, name="TestClip"):
        self.name = name
        self.properties = {}
        self.stabilize_result = True
        self.media_pool_item = MockMediaPoolItem()

    def GetName(self):
        return self.name

    def GetMediaPoolItem(self):
        return self.media_pool_item

    def SetClipProperty(self, key, value):
        self.properties[key] = value
        return True

    def Stabilize(self):
        return self.stabilize_result


class TestNeuralEngine(unittest.TestCase):
    def test_smart_reframe_has_no_wrapper(self):
        """The wrapper is withdrawn, and a mock must not resurrect it.

        The test that used to sit here asserted that a MockClip whose
        `SmartReframe()` returns True made the wrapper return True. It
        proved the mock, not Resolve: the real caller passed a Timeline,
        and the wrapper's `hasattr` guard is True for every name on a
        Resolve proxy. See the note in neural_engine.py.
        """
        import neural_engine
        self.assertFalse(hasattr(neural_engine, "apply_smart_reframe"))

    def test_apply_super_scale_targets_the_media_pool_item(self):
        clip = MockClip()
        self.assertTrue(apply_super_scale(clip, 2))
        # Set on the media pool item, and NOT on the timeline item, which
        # is where it used to go and be silently refused.
        self.assertEqual(clip.media_pool_item.properties["Super Scale"], 2)
        self.assertNotIn("Super Scale", clip.properties)

    def test_apply_super_scale_uses_the_real_property_names(self):
        clip = MockClip()
        apply_super_scale(clip, 2, sharpness="Medium", noise_reduction="Medium")
        # "SuperScale Sharpness", not "Super Scale Sharpness".
        self.assertIn("SuperScale Sharpness", clip.media_pool_item.properties)
        self.assertIn("SuperScale Noise Reduction", clip.media_pool_item.properties)

    def test_apply_super_scale_failure_is_reported(self):
        clip = MockClip()
        self.assertFalse(apply_super_scale(clip, 4))

    def test_apply_stabilization(self):
        clip = MockClip()
        self.assertTrue(apply_stabilization(clip))

    def test_apply_stabilization_failure_is_reported(self):
        clip = MockClip()
        clip.stabilize_result = False
        self.assertFalse(apply_stabilization(clip))

    def test_magic_mask_has_no_wrapper(self):
        """Withdrawn: CreateMagicMask returns False for every mode."""
        import neural_engine
        self.assertFalse(hasattr(neural_engine, "apply_magic_mask"))


if __name__ == '__main__':
    unittest.main()
