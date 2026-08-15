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
            
        # Create empty asset files so the indexer doesn't skip them
        open(os.path.join(self.test_dir.name, "powergrades", "moody_blue.drx"), "w").close()
        open(os.path.join(self.test_dir.name, "powergrades", "bright_pop.drx"), "w").close()
        open(os.path.join(self.test_dir.name, "fusion-macros", "zoom_in.setting"), "w").close()

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
        
        import glob
        for meta_path in glob.glob(os.path.join(library_path, "**", "*.meta.json"), recursive=True):
            with open(meta_path) as f:
                meta = json.load(f)
            asset_file = meta.get("file_path")
            self.assertIsNotNone(asset_file, f"{meta_path} has no file_path")
            
            import library.tools.paths as paths
            parts = asset_file.split("/")
            if len(parts) > 1 and hasattr(paths, parts[0]):
                prefix_val = getattr(paths, parts[0])
                asset_path = os.path.join(str(prefix_val), *parts[1:])
            else:
                asset_path = asset_file if os.path.isabs(asset_file) else os.path.join(os.path.dirname(meta_path), asset_file)
                
            if os.path.isabs(asset_path) and "DaVinci Resolve" in asset_path:
                pass
            else:
                self.assertTrue(os.path.exists(asset_path), f"Asset {asset_path} missing for {meta_path}")
                
                # Existence is not validity. Reject placeholder files.
                with open(asset_path, 'r', encoding='utf-8', errors='ignore') as asset_f:
                    content = asset_f.read(1024)
                if asset_path.endswith('.drx'):
                    self.assertTrue('<?xml' in content, f"Asset {asset_path} does not look like a valid .drx XML file")
                elif asset_path.endswith('.cube'):
                    self.assertTrue('LUT_' in content, f"Asset {asset_path} does not look like a valid .cube LUT file")
        
        # The honest count on this branch is 5 curated descriptors:
        # halation, intro_lower_third, outro_subscribe, film_emulation, and rec709_to_srgb.
        # This floor ensures the index actually found them, preventing the test from passing on an empty directory,
        # so losing curated presets is caught rather than tolerated.
        self.assertGreaterEqual(len(index.presets), 5)
        
        for p in index.presets:
            self.assertTrue(p.name)
            self.assertTrue(p.category in ["powergrade", "fusion-macro", "lut", "dctl", "fairlight"])
            self.assertTrue(p.file_path)



if __name__ == '__main__':
    unittest.main()
