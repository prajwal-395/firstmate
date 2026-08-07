import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add library/tools to sys.path to allow importing the modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'library', 'tools')))

import video_segment_analyzer
from vision_model import VisionModel


class TestVideoSegmentAnalyzer(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary dummy video file
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dummy_video = os.path.join(self.temp_dir.name, "dummy.mp4")
        with open(self.dummy_video, "w") as f:
            f.write("dummy content")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch('vision_model.generate')
    @patch('vision_model.load')
    @patch('vision_model.apply_chat_template')
    def test_vision_model_mocked(self, mock_apply, mock_load, mock_generate):
        # Mock mlx_vlm components
        mock_load.return_value = (MagicMock(), MagicMock())
        mock_apply.return_value = "formatted_prompt"
        
        mock_result = MagicMock()
        mock_result.text = "Mocked analysis result"
        mock_generate.return_value = mock_result
        
        # We need to reset the singleton for testing
        VisionModel._instance = None
        
        model = VisionModel()
        
        # Test analyze_image
        with patch('vision_model.load', return_value=(MagicMock(), MagicMock())):
            result = model.analyze_image("dummy.jpg", "prompt")
            self.assertEqual(result, "Mocked analysis result")

        # Test analyze_video
        result = model.analyze_video(self.dummy_video, "prompt")
        self.assertEqual(result, "Mocked analysis result")

    @patch('video_segment_analyzer.get_model')
    @patch('render_qa.analyze_color_histogram')
    @patch('render_qa.measure_lufs')
    def test_analysis_routing_and_output(self, mock_lufs, mock_color, mock_get_model):
        # Mock the deterministic checks
        mock_color.return_value = MagicMock(metric="color_histogram", passed=True, value=[], threshold={}, severity="info", detail="OK")
        mock_lufs.return_value = MagicMock(metric="lufs", passed=True, value={"input_i": -14}, threshold={}, severity="info", detail="OK")
        
        # Mock the vision model
        mock_model = MagicMock()
        mock_model.analyze_video.return_value = "Mocked model output"
        mock_get_model.return_value = mock_model
        
        # Run analysis for color and audio
        results = video_segment_analyzer.run_analysis(
            video_path=self.dummy_video,
            question="How does it look?",
            checks=["color", "audio"]
        )
        
        self.assertEqual(results["video"], self.dummy_video)
        self.assertEqual(results["checks_requested"], ["color", "audio"])
        
        # Verify deterministic checks were called and included
        self.assertIn("color_histogram", results["deterministic_checks"])
        self.assertIn("lufs", results["deterministic_checks"])
        mock_color.assert_called_once()
        mock_lufs.assert_called_once()
        
        # Verify model was called for each check
        self.assertIn("color", results["model_analysis"])
        self.assertIn("audio", results["model_analysis"])
        self.assertEqual(mock_model.analyze_video.call_count, 2)

    @patch('video_segment_analyzer.run_analysis')
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

    @patch('video_segment_analyzer.run_analysis')
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

    @patch('video_segment_analyzer.run_analysis')
    def test_cli_json_output(self, mock_run_analysis):
        mock_run_analysis.return_value = {"test_key": "test_value"}
        
        test_args = ["video_segment_analyzer.py", "analyze", "--video", self.dummy_video]
        with patch.object(sys, 'argv', test_args):
            # Capture stdout to check JSON output
            import io
            captured_stdout = io.StringIO()
            with patch('sys.stdout', new=captured_stdout):
                try:
                    video_segment_analyzer.main()
                except SystemExit:
                    pass
                    
            output = captured_stdout.getvalue()
            parsed_output = json.loads(output)
            self.assertEqual(parsed_output, {"test_key": "test_value"})


if __name__ == '__main__':
    unittest.main()
