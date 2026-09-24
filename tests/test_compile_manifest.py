import os
import json
import subprocess
import unittest

class TestCompileManifest(unittest.TestCase):
    def test_compile_manifest_stdin(self):
        test_file_path = os.path.abspath("vid1.mov")
        with open(test_file_path, "w") as f:
            f.write("dummy")
        # vid2 was cleaned up and vid1 never was, so every run of the
        # suite left a vid1.mov at the repository root.
        self.addCleanup(os.remove, test_file_path)
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
        
        # Handheld wording alone stabilizes nothing: stabilization is a
        # plan-requested treatment (`effect_type == "stabilize"` in step
        # 4.03's plan), never a keyword decision off vision prose
        # (captain, 2026-09-24). This fixture's handheld/shake words
        # used to pin the keyword match; now they pin its absence.
        self.assertIn("neural_engine_directives", manifest)
        directives = manifest["neural_engine_directives"].get("speech_1", {})
        self.assertNotIn("stabilize", directives)
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

        # Smart Reframe is withdrawn: the manifest must not advertise a
        # capability nothing delivers. See library/tools/neural_engine.py.
        self.assertNotIn("smart_reframe", manifest)



    def test_a_spine_block_may_declare_its_own_framing(self):
        """The per-clip level of the precedence chain, through the step.

        `framing_intent.resolve_framing_intent` has always taken a
        `block_intent`, and `compile_manifest` has always read
        `block["framing_intent"]` - but nothing tested that the two meet,
        so "a spine block may declare its own framing" was a claim about
        a resolver rather than about the pipeline. It meets: a landscape
        source told 0.0 by its block letterboxes while the project and
        the template say nothing.

        Nothing WRITES the key today. `mesh_spine`'s handoff does not ask
        for it and the spine contract does not list it, so this is the
        hook a per-clip choice would arrive through, proved reachable.
        """
        import os as _os
        from unittest.mock import patch
        from library.steps.step_5_04_compile_manifest.step import compile_manifest

        src = _os.path.abspath("framed.mov")
        with open(src, "w") as f:
            f.write("dummy")
        self.addCleanup(_os.remove, src)

        def _inputs(declared):
            return {
                "a_roll_assignments": [{
                    "clip_id": "clip_1", "source_clip_id": "clip_1",
                    "source_file": src, "video_in": 4.113, "video_out": 6.027,
                    "timeline_start": 0.0, "timeline_end": 1.914,
                }],
                "b_roll_assignments": [], "b_roll_interjections": [],
                "subtitle_plan": {"subtitles": []}, "transition_spec": [],
                "enhancement_spec": [], "color_grade_spec": {},
                "audio_mix_spec": {}, "music_selection": {},
                "audio_spine": {"frame_rate": 30.0, "structure": [dict(
                    {"block_type": "speech", "position": 1,
                     "clip_id": "clip_1", "source_start": 4.113,
                     "source_end": 6.027, "timeline_start": 0.0,
                     "timeline_end": 1.914,
                     "content": {"clip_id": "clip_1"}},
                    **({} if declared is None
                       else {"framing_intent": declared}))]},
                # Landscape source, so there really are bars to give.
                "clip_catalog": [{"clip_id": "clip_1", "path": src,
                                  "width": 1920, "height": 1080}],
                "semantic_analysis": {"semantic_analysis_documents": [
                    {"clip_id": "clip_1",
                     "analysis": {"motion": "Locked off on a tripod.",
                                  "scene": "A speaker on a city street."},
                     "assessment": {"clip_type": "a-roll"}}]},
            }

        def _v1(declared):
            with patch("library.steps.step_5_04_compile_manifest.step.load",
                       side_effect=lambda out_dir, filename: _inputs(declared)):
                return compile_manifest("dummy")["tracks"]["V1"]["clips"][0]

        undeclared = _v1(None)
        self.assertEqual(undeclared["framing_intent"], 1.0)
        self.assertEqual(undeclared["framing_delivered"], 1.0)
        self.assertTrue(undeclared["needs_conform"])

        declared = _v1(0.0)
        self.assertEqual(declared["framing_intent"], 0.0)
        self.assertEqual(declared["framing_delivered"], 0.0)
        self.assertFalse(declared["needs_conform"])

    def test_conform_fields_fill_by_default_and_letterbox_only_on_request(self):
        """Vision data no longer decides the framing; the declaration does.

        This test used to assert the opposite - that a visible primary
        subject forced letterbox - which is the rule that put project
        001's A-roll into 608 of 1920 rows on every clip. The subject is
        protected by the pan now (library/tools/subject_framing.py), not
        by refusing to crop. See library/tools/framing_intent.py.
        """
        from library.steps.step_5_04_compile_manifest.step import _conform_fields

        # A horizontal 4K source (3840x2160) going into a vertical 1080x1920 timeline.
        # This gives a massive fit_scale (~0.28) and fill_scale (~0.88), forcing a crop.
        clip_meta = {"clip_1": {"width": 3840, "height": 2160}}
        proj_res = [1080, 1920]

        # 1. Nothing declared: fill.
        res = _conform_fields(clip_meta, "clip_1", proj_res)
        self.assertTrue(res["needs_conform"])

        # 2. A subject on screen the whole time no longer letterboxes; it
        #    moves the crop window instead.
        res = _conform_fields(clip_meta, "clip_1", proj_res, subject_center_x=0.30)
        self.assertTrue(res["needs_conform"])
        self.assertGreater(res["framing_pan_x"], 0)

        # 3. Letterbox is still available, and only by declaring it.
        res = _conform_fields(clip_meta, "clip_1", proj_res, framing_intent=0.0)
        self.assertFalse(res["needs_conform"])


    def test_the_bed_is_bounded_by_the_picture_not_by_v1(self):
        """A bed is clamped to the last PICTURE, and a cutaway is picture.

        The clamp used to bound the bed by `max(V1.timeline_out)`. An
        outro cutaway sits on V2, so 001's four-second ending - declared
        `music_behavior: "fade_out"` and given automation at -12 dB by
        step 5.02 - shipped with no bed at all: 4.0s measured at -91.0 dB
        in the render. See AGENTS.md 10.5.
        """
        import os as _os
        import tempfile as _tempfile
        from unittest.mock import patch
        from library.steps.step_5_04_compile_manifest.step import compile_manifest
        from library.tools import music_audit_trail as _audit

        _tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(_tmp.cleanup)
        aroll = _os.path.join(_tmp.name, "bed_aroll.mov")
        broll = _os.path.join(_tmp.name, "bed_broll.mov")
        music = _os.path.join(_tmp.name, "bed_music.wav")
        for path in (aroll, broll, music):
            with open(path, "w") as f:
                f.write("dummy")
            self.addCleanup(_os.remove, path)

        inputs = {
            "a_roll_assignments": [{
                "clip_id": "clip_1", "source_clip_id": "clip_1",
                "source_file": aroll, "video_in": 4.113, "video_out": 6.027,
                "timeline_start": 0.0, "timeline_end": 1.914,
            }],
            # The outro: a non-speech block covered by a V2 cutaway, and
            # the last thing in the edit.
            "b_roll_assignments": [{
                "spine_block_position": 2, "block_type": "transition_slot",
                "clip_id": "clip_2", "source_file": broll,
                "video_in": 0.317, "video_out": 4.317, "duration_seconds": 4.0,
                "timeline_start": 1.914, "timeline_end": 5.914,
                "video_only": True,
            }],
            "b_roll_interjections": [],
            "subtitle_plan": {"subtitles": []}, "transition_spec": [],
            "enhancement_spec": [], "color_grade_spec": {},
            "audio_mix_spec": {},
            "music_selection": {"audio_path": music, "duration_seconds": 180.0},
            "audio_spine": {
                "frame_rate": 30.0,
                "structure": [
                    {"block_type": "speech", "position": 1,
                     "clip_id": "clip_1", "source_start": 4.113,
                     "source_end": 6.027, "timeline_start": 0.0,
                     "timeline_end": 1.914, "music_behavior": "background",
                     "content": {"clip_id": "clip_1", "link_group_id": "lg_1"}},
                    {"block_type": "transition_slot", "position": 2,
                     "clip_id": None, "source_start": None,
                     "source_end": None, "timeline_start": 1.914,
                     "timeline_end": 5.914, "music_behavior": "fade_out",
                     "content": {}},
                ],
            },
            "clip_catalog": [
                {"clip_id": "clip_1", "path": aroll, "width": 1080, "height": 1920},
                {"clip_id": "clip_2", "path": broll, "width": 1080, "height": 1920},
            ],
            "semantic_analysis": {"semantic_analysis_documents": [
                {"clip_id": "clip_1",
                 "analysis": {"motion": "Locked off on a tripod.",
                              "scene": "A speaker on a city street."},
                 "assessment": {"clip_type": "a-roll"}}]},
        }

        # A resolved selection owes its audit sidecar: the pre-render
        # check refuses without it, so this run stages what 2.04's
        # post-bridge writes on a real run. The compile reads the
        # project root off `out_dir`, hence a real directory.
        _audit.write_audit_trail(
            _tmp.name, inputs["music_selection"])
        with patch("library.steps.step_5_04_compile_manifest.step.load",
                   side_effect=lambda out_dir, filename: inputs):
            manifest = compile_manifest(_os.path.join(_tmp.name, "out"))

        v1_end = max(c["timeline_out"] for c in manifest["tracks"]["V1"]["clips"])
        self.assertAlmostEqual(v1_end, 1.914)
        bed = manifest["tracks"]["A2"]["clips"][0]
        # The bed runs to the end of the PICTURE, four seconds past V1.
        self.assertAlmostEqual(bed["timeline_out"], 5.914, places=3)
        self.assertAlmostEqual(
            bed["source_out"] - bed["source_in"], 5.914, places=3)



if __name__ == '__main__':
    unittest.main()
