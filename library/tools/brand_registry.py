import os
import json
from dataclasses import asdict
from typing import Optional

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

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)

# The template a project gets when its project.yaml names none.
DEFAULT_TEMPLATE_NAME = "default_brand"


def resolve_project_template(template_name: str = "",
                             templates_dir: Optional[str] = None) -> BrandTemplate:
    """The BrandTemplate a project runs under, by NAME rather than path.

    `load_brand_template` takes a filesystem path, so every caller that
    started from a project.yaml `pipeline.brand_template` name had to
    rebuild the same `templates/<name>.yaml` join.  There were two copies
    of that join and they could disagree; this is the one.

    An empty name means the project declared none, which resolves to
    `default_brand` ON DISK - so editing that file really does govern
    every template-less project.  The in-code `_get_default_template()`
    is the last resort for an installation that has no templates
    directory at all.

    Any OTHER name that does not resolve RAISES: a named template that is
    not there is a typo, and silently substituting a default is how a
    project renders under a brand nobody chose.
    """
    base = templates_dir or TEMPLATES_DIR
    name = template_name or DEFAULT_TEMPLATE_NAME
    for ext in (".yaml", ".yml", ".json"):
        candidate = os.path.join(base, f"{name}{ext}")
        if os.path.exists(candidate):
            return load_brand_template(candidate)
    if not template_name or name == DEFAULT_TEMPLATE_NAME:
        return _get_default_template()
    available = (sorted({os.path.splitext(f)[0] for f in os.listdir(base)})
                 if os.path.isdir(base) else [])
    raise FileNotFoundError(
        f"Brand template {template_name!r} not found in {base}. "
        f"Available: {available}"
    )


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
    if template.delivery_format:
        from library.tools.delivery_format import DELIVERY_FORMATS
        if template.delivery_format not in DELIVERY_FORMATS:
            errors.append(
                f"Invalid delivery_format: {template.delivery_format} "
                f"must be one of {sorted(DELIVERY_FORMATS)}"
            )
    if template.style.energy_profile not in ["calm", "moderate", "high"]:
        errors.append(f"Invalid energy_profile: {template.style.energy_profile}")
    if template.effect.vfx_intensity < 0.0 or template.effect.vfx_intensity > 1.0:
        errors.append(f"Invalid vfx_intensity: {template.effect.vfx_intensity} must be between 0.0 and 1.0")
    if template.style.framing_intent is not None:
        if not (0.0 <= template.style.framing_intent <= 1.0):
            errors.append(f"Invalid framing_intent: {template.style.framing_intent} must be between 0.0 and 1.0")
    if template.effect.sfx_density not in ["sparse", "moderate", "dense"]:
        errors.append(f"Invalid sfx_density: {template.effect.sfx_density}")
    return errors
