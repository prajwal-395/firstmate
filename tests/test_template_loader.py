import os
import tempfile
import json
import yaml
from pathlib import Path
from library.tools.template_loader import TemplateLoader

def test_template_loader():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        
        # Write default_brand.yaml
        template_content = {
            "style": {"font": "Helvetica"},
            "content": {"music": "upbeat"},
            "effect": {"transition_types": ["cut", "dissolve"], "vfx_intensity": 0.8}
        }
        with open(templates_dir / "default_brand.yaml", "w") as f:
            yaml.dump(template_content, f)
            
        loader = TemplateLoader(str(project_dir), str(templates_dir))
        
        # Test creative direction step
        cd_constraints = loader.get_brand_constraints("default_brand", "step_2_01_creative_direction")
        assert "Helvetica" in cd_constraints
        assert "upbeat" in cd_constraints
        
        # Test transitions
        tr_constraints = loader.get_brand_constraints("default_brand", "step_4_02_plan_transitions")
        assert "dissolve" in tr_constraints
        
        # Test vfx
        vfx_constraints = loader.get_brand_constraints("default_brand", "step_4_03_plan_vfx")
        assert "0.8" in vfx_constraints
