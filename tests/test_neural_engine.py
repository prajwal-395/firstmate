import unittest

# library/tools is on sys.path via tests/conftest.py; the import below
# reaches it by absolute package path instead of a bare name bound by
# collection order.
from library.tools.neural_engine import (  # noqa: E402
    apply_super_scale,
)


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
    def test_apply_super_scale_targets_the_media_pool_item(self):
        clip = MockClip()
        self.assertTrue(apply_super_scale(clip, 2))
        # Set on the media pool item, and NOT on the timeline item, which
        # is where it used to go and be silently refused.
        self.assertEqual(clip.media_pool_item.properties["Super Scale"], 2)
        self.assertNotIn("Super Scale", clip.properties)

        # The real property names: "SuperScale Sharpness", not
        # "Super Scale Sharpness".
        clip = MockClip()
        apply_super_scale(clip, 2, sharpness="Medium", noise_reduction="Medium")
        self.assertIn("SuperScale Sharpness", clip.media_pool_item.properties)
        self.assertIn("SuperScale Noise Reduction", clip.media_pool_item.properties)



if __name__ == '__main__':
    unittest.main()
