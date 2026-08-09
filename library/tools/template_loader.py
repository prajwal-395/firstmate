import json
import yaml
from pathlib import Path

class TemplateLoader:
    def __init__(self, project_dir: str, templates_dir: str = None):
        self.project_dir = Path(project_dir)
        if templates_dir:
            self.templates_dir = Path(templates_dir)
        else:
            self.templates_dir = Path(__file__).resolve().parent.parent / "templates"
            
    def load_template(self, template_name: str = "default_brand") -> dict:
        """Load from project brand.json first, then from templates_dir"""
        brand_json = self.project_dir / "brand.json"
        if brand_json.exists():
            with open(brand_json) as f:
                return json.load(f)
                
        # Try YAML in templates_dir
        yaml_path = self.templates_dir / f"{template_name}.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                return yaml.safe_load(f)
                
        # Try JSON in templates_dir
        json_path = self.templates_dir / f"{template_name}.json"
        if json_path.exists():
            with open(json_path) as f:
                return json.load(f)
                
        return {}
        
    def get_brand_constraints(self, template_name: str, step_id: str) -> str:
        """Inject brand constraints into context for creative steps"""
        template = self.load_template(template_name)
        if not template:
            return ""
            
        constraints = []
        if step_id == "step_2_01_creative_direction":
            if "style" in template:
                constraints.append(f"Style: {json.dumps(template['style'])}")
            if "content" in template:
                constraints.append(f"Content Rules: {json.dumps(template['content'])}")
        elif step_id == "step_4_02_plan_transitions":
            effect = template.get("effect", {})
            if "transition_types" in effect:
                constraints.append(f"Transition Types: {effect['transition_types']}")
            if "transition_duration_ms" in effect:
                constraints.append(f"Transition Duration MS: {effect['transition_duration_ms']}")
        elif step_id == "step_4_03_plan_vfx":
            effect = template.get("effect", {})
            if "vfx_intensity" in effect:
                constraints.append(f"VFX Intensity: {effect['vfx_intensity']}")
                
        if constraints:
            return "\nBrand Constraints:\n- " + "\n- ".join(constraints) + "\n"
        return ""
