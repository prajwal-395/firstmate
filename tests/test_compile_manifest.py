import os
import json
import subprocess
import unittest

class TestCompileManifest(unittest.TestCase):
    def test_compile_manifest_stdin(self):
        test_file_path = os.path.abspath("vid1.mov")
        with open(test_file_path, "w") as f:
            f.write("dummy")

        # Create a mock input based on expected schema
        mock_inputs = {
            "a_roll_assignments": [
                {
                    "clip_id": "clip_1",
                    "source_clip_id": "clip_1",
                    "source_file": test_file_path,
                    "video_in": 0.0,
                    "video_out": 2.0,
                    "timeline_start": 0.0,
                    "timeline_end": 2.0,
                }
            ],
            "b_roll_assignments": [],
            "subtitle_plan": {"subtitles": []},
            "transition_spec": [],
            "enhancement_spec": [],
            "music_ducking": [{"timeline_start": 0.0, "timeline_end": 1.0, "volume_db": -10}],
            "color_grade_spec": {},
            "audio_mix_spec": {},
            "music_selection": {},
            "audio_spine": {
                "structure": [
                    {
                        "block_type": "speech",
                        "position": 1,
                        "source_start": 0.0,
                        "source_end": 2.0,
                        "timeline_start": 0.0,
                        "timeline_end": 2.0,
                        "content": {"clip_id": "clip_1", "link_group_id": "lg_1"}
                    }
                ],
                "frame_rate": 30.0
            },
            "clip_catalog": [
                {
                    "clip_id": "clip_1",
                    "path": test_file_path,
                    "width": 1080,
                    "height": 1920
                }
            ],
            "semantic_analysis": {
                "clips": [
                    {
                        "clip_id": "clip_1",
                        "tags": ["shaky", "interview"],
                        "description": "handheld shot of speaker"
                    }
                ]
            }
        }
        
        from library.steps.step_5_04_compile_manifest.step import compile_manifest
        from unittest.mock import patch
        
        with patch("library.steps.step_5_04_compile_manifest.step.load", side_effect=lambda out_dir, filename: mock_inputs):
            manifest = compile_manifest("dummy")
            
        output_data = {"assembly_manifest": manifest}
        self.assertIn("assembly_manifest", output_data)
        
        manifest = output_data["assembly_manifest"]
        
        # Assert tracks are present
        self.assertIn("tracks", manifest)
        self.assertIn("V1", manifest["tracks"])
        self.assertGreater(len(manifest["tracks"]["V1"]["clips"]), 0)
        
        # Assert neural_engine_directives is populated (stabilize and magic_mask from tags)
        self.assertIn("neural_engine_directives", manifest)
        self.assertIn("speech_1", manifest["neural_engine_directives"])
        directives = manifest["neural_engine_directives"]["speech_1"]
        self.assertTrue(directives.get("stabilize"))
        self.assertTrue(directives.get("magic_mask"))
        
        # Assert music_ducking is properly formatted
        self.assertIn("music_ducking", manifest)
        self.assertIn("ducking_curves", manifest["music_ducking"])
        self.assertEqual(len(manifest["music_ducking"]["ducking_curves"]), 1)

if __name__ == '__main__':
    unittest.main()
