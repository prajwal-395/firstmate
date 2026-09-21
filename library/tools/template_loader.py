import json
import yaml
from pathlib import Path

from library.tools.project_layout import STEP_BY_ID, node_id_for

# The three steps a brand template speaks to, named in the DAG's
# vocabulary because that is what the runner passes.  Checked against the
# step table at import: a directory name written here - the exact mistake
# that kept this channel closed - stops the module from loading instead
# of silently returning nothing.
BRAND_CONSTRAINT_STEPS = ("creative_direction", "plan_transitions", "plan_vfx")

_unknown = [s for s in BRAND_CONSTRAINT_STEPS if s not in STEP_BY_ID]
if _unknown:
    raise RuntimeError(
        f"BRAND_CONSTRAINT_STEPS names steps the DAG does not have: "
        f"{_unknown}. These must be DAG node ids (project_layout.STEPS), "
        f"not step directory names."
    )

class TemplateLoader:
    def __init__(self, project_dir: str, templates_dir: str = None):
        self.project_dir = Path(project_dir)
        # The product ships no templates (captain, 2026-09-21): this
        # directory is an injectable lookup kept for tests and for a
        # project-side folder, and a missing one simply resolves nothing.
        if templates_dir:
            self.templates_dir = Path(templates_dir)
        else:
            self.templates_dir = Path(__file__).resolve().parent.parent / "templates"
            
    def load_template(self, template_name: str = "") -> dict:
        """Load from project brand.json first, then from templates_dir.

        An EMPTY name means the project declared no brand template, and it
        loads NOTHING - not `default_brand`.  The default used to be
        `"default_brand"` and the runner used to pass that name for a
        project that had chosen none, which is how the fallback
        template's energy profile and VFX intensity reached the prompts of
        every template-less project.  See `no_brand_template()` and
        `ABSENT_SLOT_READINGS` in library/tools/brand_registry.py.

        A `brand.json` sitting in the project IS a declaration, so it
        still wins.
        """
        brand_json = self.project_dir / "brand.json"
        if brand_json.exists():
            with open(brand_json) as f:
                return json.load(f)

        if not template_name:
            return {}

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
        """The brand's constraints for one step, as prompt text.

        `step_id` may be spelled either way - `plan_vfx` (the DAG node id
        the runner passes) or `step_4_03_plan_vfx` (the step's own
        manifest id).  `node_id_for` reconciles them against the one
        table that knows both.  It used to be neither: the branches below
        were written against the manifest id and the only caller passes
        the node id, so this returned "" for every step, every template
        and every project from the day it was written.  The steps that
        plan transitions and VFX chose them with no knowledge of what
        their brand permits.
        """
        step = node_id_for(step_id)
        if step not in BRAND_CONSTRAINT_STEPS:
            return ""

        template = self.load_template(template_name)
        if not template:
            return ""

        constraints = []
        if step == "creative_direction":
            if "style" in template:
                constraints.append(f"Style: {json.dumps(template['style'])}")
            if "content" in template:
                constraints.append(f"Content Rules: {json.dumps(template['content'])}")
        elif step == "plan_transitions":
            effect = template.get("effect", {})
            if "transition_types" in effect:
                constraints.append(f"Transition Types: {effect['transition_types']}")
            if "transition_duration_ms" in effect:
                constraints.append(f"Transition Duration MS: {effect['transition_duration_ms']}")
        elif step == "plan_vfx":
            effect = template.get("effect", {})
            if "vfx_intensity" in effect:
                constraints.append(f"VFX Intensity: {effect['vfx_intensity']}")

        if constraints:
            return "\nBrand Constraints:\n- " + "\n- ".join(constraints) + "\n"
        return ""
