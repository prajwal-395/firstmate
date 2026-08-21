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


def project_template_name(project_folder: str) -> str:
    """The brand template NAME a project declares in its project.yaml.

    `pipeline.brand_template` is the only place a project says which brand
    it renders under, and for the whole life of the pipeline NOTHING read
    it into the run.  `state["brand_template"]` was populated from exactly
    one source - a `default` on the process manifest's input, which has
    none - so `gather_step_inputs` called `load_brand_template("")` on
    every step of every project and handed back the in-code
    `_get_default_template()`.  A project naming `lucie_client`, or any
    other template, silently rendered with none of its effect slots and
    the run reported SUCCESS.  Same shape as the timed-text slot with no
    reader (section 14 of CLAUDE.md): a declaration nothing reads.

    Returns "" when the project declares none, which
    :func:`resolve_project_template` turns into `default_brand` on disk.
    """
    if not project_folder:
        return ""
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return ""
    if yaml is None:
        raise ImportError("PyYAML is required to read project.yaml.")
    with open(project_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    block = cfg.get("pipeline") or {}
    if not isinstance(block, dict):
        return ""
    return (block.get("brand_template") or "").strip()


def _looks_like_path(reference: str) -> bool:
    return (os.sep in reference
            or reference.lower().endswith((".yaml", ".yml", ".json")))


def resolve_template_reference(reference: str = "",
                               templates_dir: Optional[str] = None
                               ) -> BrandTemplate:
    """The BrandTemplate for either a template NAME or a template PATH.

    Two callers spell the same thing differently and both are legitimate:
    a project.yaml declares a NAME (`cinematic_narrative`), while the
    process manifest's `brand_template` input is documented as a PATH so a
    template may live outside `library/templates/`.  The two are told
    apart structurally - a separator or a yaml/json extension means path -
    rather than by "does it exist", so a mistyped path raises as a
    mistyped path instead of being retried as a template name.

    Either form MISSING raises.  Falling back to the in-code default is
    how a project renders under a brand nobody chose.
    """
    ref = (reference or "").strip()
    if ref and _looks_like_path(ref):
        if not os.path.exists(ref):
            raise FileNotFoundError(
                f"Brand template path {ref!r} does not exist."
            )
        return load_brand_template(ref)
    return resolve_project_template(ref, templates_dir=templates_dir)


def reference_template_name(reference: str = "") -> str:
    """The bare template NAME for either form of reference.

    `TemplateLoader` (library/tools/template_loader.py) resolves by name
    against its own templates dir, so it needs the name half of whatever
    the run is carrying.
    """
    ref = (reference or "").strip()
    if ref and _looks_like_path(ref):
        return os.path.splitext(os.path.basename(ref))[0]
    return ref or DEFAULT_TEMPLATE_NAME


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
