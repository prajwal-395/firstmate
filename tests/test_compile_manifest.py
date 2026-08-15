import os
import json
import subprocess
import unittest

class TestCompileManifest(unittest.TestCase):
    def test_compile_manifest_stdin(self):
        test_file_path = os.path.abspath("vid1.mov")
        with open(test_file_path, "w") as f:
            f.write("dummy")
        broll_file_path = os.path.abspath("vid2.mov")
        with open(broll_file_path, "w") as f:
            f.write("dummy")
        self.addCleanup(os.remove, broll_file_path)

        # Create a mock input based on expected schema
        mock_inputs = {
            "a_roll_assignments": [
                {
                    "clip_id": "clip_1",
                    "source_clip_id": "clip_1",
                    "source_file": test_file_path,
                    "video_in": 17.666,
                    "video_out": 19.548,
                    "timeline_start": 0.0,
                    "timeline_end": 1.882,
                }
            ],
            # B-roll must be exercised with REAL assignments: this fixture
            # used to be [] in four separate test files, which is why a
            # key-mapping bug that zeroed every B-roll clip went unnoticed
            # through four audits.
            "b_roll_assignments": [
                {
                    "spine_block_position": 1,
                    "block_type": "speech",
                    "clip_id": "clip_2",
                    "source_file": broll_file_path,
                    "video_in": 1.204,
                    "video_out": 3.086,
                    "duration_seconds": 1.882,
                    "timeline_start": 0.0,
                    "timeline_end": 1.882,
                    "video_only": True,
                }
            ],
            "b_roll_interjections": [],
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
                        "clip_id": "clip_1",
                        "source_start": 17.666,
                        "source_end": 19.548,
                        "timeline_start": 0.0,
                        "timeline_end": 1.882,
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
                },
                {
                    "clip_id": "clip_2",
                    "path": broll_file_path,
                    "width": 1080,
                    "height": 1920
                }
            ],
            "semantic_analysis": {
                "semantic_analysis_documents": [
                    {
                        # The real document shape: the stability verdict
                        # and the camera prose. `tags`/`description` were
                        # what the reader used to look for and neither
                        # vision schema has ever written them.
                        "clip_id": "clip_1",
                        "analysis": {
                            "motion": "The camera is handheld throughout, "
                                      "with visible shake as the speaker "
                                      "walks.",
                            "scene": "A speaker on a city street.",
                        },
                        "assessment": {
                            "clip_type": "a-roll",
                            "keywords": ["handheld", "speaker"],
                        },
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
        
        # Handheld footage must actually be marked for stabilisation:
        # the lookup this decides off used to join nothing, so no clip in
        # any run was ever stabilised.
        self.assertIn("neural_engine_directives", manifest)
        self.assertIn("speech_1", manifest["neural_engine_directives"])
        directives = manifest["neural_engine_directives"]["speech_1"]
        self.assertTrue(directives.get("stabilize"))
        # Magic Mask is withdrawn: CreateMagicMask returns False for every
        # mode, so a directive for it could never be honoured.
        self.assertNotIn("magic_mask", directives)
        
        # B-roll must survive compilation with its real extents.
        v2_clips = manifest["tracks"]["V2"]["clips"]
        self.assertEqual(len(v2_clips), 1)
        broll = v2_clips[0]
        self.assertEqual(broll["source_file"], broll_file_path)
        self.assertAlmostEqual(broll["source_in"], 1.204)
        self.assertAlmostEqual(broll["source_out"], 3.086)
        self.assertAlmostEqual(broll["timeline_in"], 0.0)
        self.assertAlmostEqual(broll["timeline_out"], 1.882)
        self.assertGreater(broll["timeline_out"] - broll["timeline_in"], 0)

if __name__ == '__main__':
    unittest.main()
