import os
import unittest
import yaml
import json

from library.tools.fusion_macro_loader import load_macro
from library.schemas.preset_metadata import PresetEntry

class TestTitleMacros(unittest.TestCase):
    def setUp(self):
        self.workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.presets_dir = os.path.join(self.workspace, "library", "presets", "fusion-macros")
        self.brand_template_path = os.path.join(self.workspace, "library", "templates", "default_brand.yaml")
        
        self.intro_meta_path = os.path.join(self.presets_dir, "intro_lower_third.meta.json")
        self.outro_meta_path = os.path.join(self.presets_dir, "outro_subscribe.meta.json")

    def test_macros_exist_and_agree(self):
        # 1 & 2: Brand template paths, descriptors, and filenames agree and exist
        with open(self.brand_template_path, 'r', encoding='utf-8') as f:
            brand = yaml.safe_load(f)
            
        intro_tmpl = brand.get("content", {}).get("intro_template", "")
        outro_tmpl = brand.get("content", {}).get("outro_template", "")
        
        # Verify brand template paths are the correct filenames
        self.assertEqual(intro_tmpl, "library/presets/fusion-macros/intro_lower_third.setting")
        self.assertEqual(outro_tmpl, "library/presets/fusion-macros/outro_subscribe.setting")
        
        # Verify .setting files exist
        intro_setting = os.path.join(self.workspace, intro_tmpl)
        outro_setting = os.path.join(self.workspace, outro_tmpl)
        self.assertTrue(os.path.exists(intro_setting), f"{intro_setting} does not exist")
        self.assertTrue(os.path.exists(outro_setting), f"{outro_setting} does not exist")
        
        # Verify descriptor file_path matches
        with open(self.intro_meta_path, 'r', encoding='utf-8') as f:
            intro_meta = json.load(f)
        with open(self.outro_meta_path, 'r', encoding='utf-8') as f:
            outro_meta = json.load(f)
            
        self.assertEqual(intro_meta["file_path"], "intro_lower_third.setting")
        self.assertEqual(outro_meta["file_path"], "outro_subscribe.setting")

    def test_macros_take_text_and_branding(self):
        # 3: Both take text and branding from the brand template rather than hard-coding
        # The macros should expose Inputs for Text and BrandColor
        intro_setting = os.path.join(self.presets_dir, "intro_lower_third.setting")
        outro_setting = os.path.join(self.presets_dir, "outro_subscribe.setting")
        
        with open(intro_setting, 'r', encoding='utf-8') as f:
            intro_content = f.read()
            
        with open(outro_setting, 'r', encoding='utf-8') as f:
            outro_content = f.read()
            
        # Check that they expose text parameters
        self.assertIn('Name = "TitleText"', intro_content)
        self.assertIn('Name = "CallToActionText"', outro_content)
        
        # Check that they expose branding color parameters
        self.assertIn('Name = "BrandColorR"', intro_content)
        self.assertIn('Name = "BrandColorG"', intro_content)
        self.assertIn('Name = "BrandColorB"', intro_content)
        
        self.assertIn('Name = "BrandColorR"', outro_content)
        self.assertIn('Name = "BrandColorG"', outro_content)
        self.assertIn('Name = "BrandColorB"', outro_content)

    def test_macro_loader_returns_content(self):
        # 4: fusion_macro_loader returns non-empty for both
        intro_entry = PresetEntry(
            name="Intro Lower Third",
            description="",
            category="fusion-macro",
            tags=[],
            compatibility={},
            file_path=os.path.join(self.presets_dir, "intro_lower_third.setting"),
            mood_match=[],
            energy_match=[]
        )
        outro_entry = PresetEntry(
            name="Outro Subscribe",
            description="",
            category="fusion-macro",
            tags=[],
            compatibility={},
            file_path=os.path.join(self.presets_dir, "outro_subscribe.setting"),
            mood_match=[],
            energy_match=[]
        )
        
        intro_loaded = load_macro(intro_entry)
        outro_loaded = load_macro(outro_entry)
        
        self.assertTrue(bool(intro_loaded))
        self.assertTrue(bool(outro_loaded))
        self.assertTrue(len(intro_loaded.get("raw_content", "")) > 0)
        self.assertTrue(len(outro_loaded.get("raw_content", "")) > 0)

if __name__ == '__main__':
    unittest.main()
