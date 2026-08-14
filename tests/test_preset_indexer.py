import unittest
import os
import tempfile
import json
from library.tools.preset_indexer import scan_library, find_presets, find_preset_for_mood

class TestPresetIndexer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        
        # Create some mock presets
        os.makedirs(os.path.join(self.test_dir.name, "powergrades"))
        os.makedirs(os.path.join(self.test_dir.name, "fusion-macros"))
        
        # Preset 1
        with open(os.path.join(self.test_dir.name, "powergrades", "moody_blue.meta.json"), "w") as f:
            json.dump({
                "name": "Moody Blue",
                "description": "A dark, moody blue look",
                "category": "powergrade",
                "tags": ["moody", "dark", "cinematic", "blue"],
                "file_path": "moody_blue.drx"
            }, f)
            
        # Preset 2
        with open(os.path.join(self.test_dir.name, "powergrades", "bright_pop.meta.json"), "w") as f:
            json.dump({
                "name": "Bright Pop",
                "description": "High energy bright colors",
                "category": "powergrade",
                "tags": ["bright", "energetic", "pop", "happy"],
                "file_path": "bright_pop.drx"
            }, f)
            
        # Preset 3
        with open(os.path.join(self.test_dir.name, "fusion-macros", "zoom_in.meta.json"), "w") as f:
            json.dump({
                "name": "Zoom In",
                "description": "Dynamic zoom",
                "category": "fusion-macro",
                "tags": ["dynamic", "energetic", "fast"],
                "file_path": "zoom_in.setting"
            }, f)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_scan_library(self):
        index = scan_library(self.test_dir.name)
        self.assertEqual(len(index.presets), 3)
        
        names = [p.name for p in index.presets]
        self.assertIn("Moody Blue", names)
        self.assertIn("Zoom In", names)

    def test_find_presets(self):
        index = scan_library(self.test_dir.name)
        
        # Query by category
        pg_presets = find_presets(index, category="powergrade", tags=[])
        self.assertEqual(len(pg_presets), 2)
        
        # Query by tags
        energetic_presets = find_presets(index, category="", tags=["energetic"])
        self.assertEqual(len(energetic_presets), 2)
        
        # Query by both
        dynamic_macros = find_presets(index, category="fusion-macro", tags=["energetic"])
        self.assertEqual(len(dynamic_macros), 1)
        self.assertEqual(dynamic_macros[0].name, "Zoom In")

    def test_find_preset_for_mood(self):
        index = scan_library(self.test_dir.name)
        
        # Match moody
        p1 = find_preset_for_mood(index, mood="moody", energy="low")
        self.assertIsNotNone(p1)
        self.assertEqual(p1.name, "Moody Blue")
        
        # Match energetic
        p2 = find_preset_for_mood(index, mood="happy", energy="energetic")
        self.assertIsNotNone(p2)
        self.assertEqual(p2.name, "Bright Pop")

    def test_populated_library(self):
        # Scan the actual populated library
        library_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "library", "presets")
        index = scan_library(library_path)
        # 13 since the three fusion-macro TRANSITION descriptors were
        # removed: they pointed at .setting files that do not exist, so
        # selecting one failed and the renderer silently substituted a
        # transition nobody chose.
        self.assertGreaterEqual(len(index.presets), 13)
        
        for p in index.presets:
            self.assertTrue(p.name)
            self.assertTrue(p.category in ["powergrade", "fusion-macro", "lut", "dctl", "fairlight"])
            self.assertTrue(p.file_path)

if __name__ == '__main__':
    unittest.main()
