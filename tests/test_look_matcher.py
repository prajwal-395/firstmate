import os
import tempfile
import unittest
from library.tools import look_matcher
from library.tools.look_matcher import (
    LookMatchUnavailable,
    analyze_frame_colors,
    match_clips_to_reference,
)

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

    def test_missing_or_undecodable_file_raises_rather_than_returning_neutral(self):
        with self.assertRaises(Exception):
            analyze_frame_colors("/no/such/frame.png")
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
            missing = os.path.join(d, "never-extracted.png")
            with self.assertRaises(FileNotFoundError):
                match_clips_to_reference(ref, {"clip_001": missing})

if __name__ == '__main__':
    unittest.main()
