import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# library/tools is on sys.path via tests/conftest.py, which owns every
# non-root entry so collection order cannot change what a bare name
# binds to; the sibling imports below reach it by absolute package path.
from library.tools import video_segment_analyzer  # noqa: E402
from library.tools.vision_model import VisionModel  # noqa: E402


class TestVideoSegmentAnalyzer(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary dummy video file
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dummy_video = os.path.join(self.temp_dir.name, "dummy.mp4")
        with open(self.dummy_video, "w") as f:
            f.write("dummy content")

    def tearDown(self):
        self.temp_dir.cleanup()


    # Patched on `library.tools.render_qa`, not on the bare `render_qa`:
    # the analyzer used to import its siblings by bare name, which binds a
    # SECOND copy of the 2,300-line render_qa module under its own
    # sys.modules key. Patching one left the other untouched.

    @patch('library.tools.video_segment_analyzer.run_analysis')
    def test_cli_cleanup(self, mock_run_analysis):
        # Ensure dummy video exists
        self.assertTrue(os.path.exists(self.dummy_video))
        
        mock_run_analysis.return_value = {"status": "ok"}
        
        # Simulate CLI execution with --cleanup
        test_args = ["video_segment_analyzer.py", "analyze", "--video", self.dummy_video, "--cleanup"]
        with patch.object(sys, 'argv', test_args):
            # Capture stdout to avoid cluttering test output
            with patch('sys.stdout', new=MagicMock()):
                try:
                    video_segment_analyzer.main()
                except SystemExit:
                    pass
                
        # Verify video was deleted
        self.assertFalse(os.path.exists(self.dummy_video))

    @patch('library.tools.video_segment_analyzer.run_analysis')
    def test_cli_no_cleanup(self, mock_run_analysis):
        # Ensure dummy video exists
        self.assertTrue(os.path.exists(self.dummy_video))
        
        mock_run_analysis.return_value = {"status": "ok"}
        
        # Simulate CLI execution without --cleanup
        test_args = ["video_segment_analyzer.py", "analyze", "--video", self.dummy_video]
        with patch.object(sys, 'argv', test_args):
            # Capture stdout to avoid cluttering test output
            with patch('sys.stdout', new=MagicMock()):
                try:
                    video_segment_analyzer.main()
                except SystemExit:
                    pass
                
        # Verify video was NOT deleted
        self.assertTrue(os.path.exists(self.dummy_video))



if __name__ == '__main__':
    unittest.main()
