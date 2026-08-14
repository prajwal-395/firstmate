import os
import json
from dataclasses import asdict

try:
    import yaml
except ImportError:
    yaml = None

from library.schemas.brand_template import BrandTemplate, StyleSlots, EffectSlots, ContentSlots
from library.tools.transition_vocabulary import PLANNABLE_TYPES

def _get_default_template() -> BrandTemplate:
    return BrandTemplate(
        series_id="default",
        style=StyleSlots(color_palette=["#ffffff", "#000000"], energy_profile="moderate"),
        # The fallback allow-list governs whenever no template resolves, so
        # it has to be the full drawable vocabulary. "dissolve" sat here and
        # is not drawable at all - see library/tools/transition_vocabulary.py.
        effect=EffectSlots(
            transition_types=list(PLANNABLE_TYPES), sfx_density="moderate"
        ),
        content=ContentSlots(music_genre=["ambient"])
    )

def load_brand_template(template_path: str) -> BrandTemplate:
    if not template_path or not os.path.exists(template_path):
        return _get_default_template()

    with open(template_path, 'r', encoding='utf-8') as f:
        if template_path.endswith('.yaml') or template_path.endswith('.yml'):
            if yaml:
                data = yaml.safe_load(f)
            else:
                raise ImportError("PyYAML is required to parse .yaml files.")
        else:
            data = json.load(f)
            
    return BrandTemplate.from_dict(data)

def query_slots(template: BrandTemplate, category: str) -> dict:
    if category == "style":
        return asdict(template.style)
    elif category == "effect":
        return asdict(template.effect)
    elif category == "content":
        return asdict(template.content)
    return {}

def validate_template(template: BrandTemplate) -> list[str]:
    errors = []
    if template.style.energy_profile not in ["calm", "moderate", "high"]:
        errors.append(f"Invalid energy_profile: {template.style.energy_profile}")
    if template.effect.vfx_intensity < 0.0 or template.effect.vfx_intensity > 1.0:
        errors.append(f"Invalid vfx_intensity: {template.effect.vfx_intensity} must be between 0.0 and 1.0")
    if template.effect.sfx_density not in ["sparse", "moderate", "dense"]:
        errors.append(f"Invalid sfx_density: {template.effect.sfx_density}")
    return errors
