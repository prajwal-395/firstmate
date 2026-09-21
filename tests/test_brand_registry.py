import unittest
import os
import tempfile
import json
from library.tools.brand_registry import load_brand_template, query_slots, validate_template
from library.schemas.brand_template import BrandTemplate, StyleSlots, EffectSlots, ContentSlots

class TestBrandRegistry(unittest.TestCase):
    def setUp(self):
        self.valid_data = {
            "series_id": "test_series",
            "style": {
                "color_palette": ["#ff0000", "#00ff00"],
                "energy_profile": "high"
            },
            "effect": {
                "transition_types": ["cut"],
                "vfx_intensity": 0.5,
                "sfx_density": "sparse"
            },
            "content": {
                "music_genre": ["rock"]
            }
        }
        
        self.invalid_data = {
            "series_id": "test_series",
            "style": {
                "energy_profile": "insane" # invalid
            },
            "effect": {
                "vfx_intensity": 2.0, # invalid
                "sfx_density": "extreme" # invalid
            }
        }

    def test_schema_validation(self):
        # Valid
        bt_valid = BrandTemplate.from_dict(self.valid_data)
        errors = validate_template(bt_valid)
        self.assertEqual(len(errors), 0)

        # Invalid
        bt_invalid = BrandTemplate.from_dict(self.invalid_data)
        errors = validate_template(bt_invalid)
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("energy_profile" in e for e in errors))
        self.assertTrue(any("vfx_intensity" in e for e in errors))
        self.assertTrue(any("sfx_density" in e for e in errors))

    def test_integration_slot_isolation(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.valid_data, f)
            temp_path = f.name
            
        try:
            bt = load_brand_template(temp_path)
            
            # Query style
            style_slots = query_slots(bt, "style")
            self.assertIn("color_palette", style_slots)
            self.assertIn("energy_profile", style_slots)
            self.assertNotIn("transition_types", style_slots)
            self.assertEqual(style_slots["energy_profile"], "high")
            
            # Query effect
            effect_slots = query_slots(bt, "effect")
            self.assertIn("transition_types", effect_slots)
            self.assertIn("vfx_intensity", effect_slots)
            self.assertNotIn("color_palette", effect_slots)
            
            # Query content
            content_slots = query_slots(bt, "content")
            self.assertIn("music_genre", content_slots)
            
        finally:
            os.remove(temp_path)

    def test_populated_templates(self):
        # The product ships no templates, so the populated documents under
        # test are the synthetic project copies - same shapes the
        # resolvers validate, with no client's copy among them.
        from tests.brand_fixtures import ALL_SYNTHETIC
        from library.schemas.brand_template import BrandTemplate

        for name, data in ALL_SYNTHETIC.items():
            bt = BrandTemplate.from_dict(data)
            errors = validate_template(bt)
            self.assertEqual(len(errors), 0, f"Template {name} failed validation: {errors}")

    def test_project_brand_json_wins_over_templates_dir(self):
        import json as _json
        import tempfile as _tempfile
        from library.tools.brand_registry import resolve_project_template
        from tests.brand_fixtures import SYNTHETIC_CINEMATIC

        with _tempfile.TemporaryDirectory() as tmpdir:
            brand_json = os.path.join(tmpdir, "brand.json")
            with open(brand_json, "w", encoding="utf-8") as handle:
                _json.dump(SYNTHETIC_CINEMATIC, handle)
            bt = resolve_project_template(
                "any_name_at_all", templates_dir=tmpdir,
                project_folder=tmpdir)
            self.assertEqual(
                query_slots(bt, "effect")["subtitle_style"], "minimal")

if __name__ == '__main__':
    unittest.main()
