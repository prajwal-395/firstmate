"""
Where a project's brand template and its own declarations come from.

Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**A project's brand template reaches the run through `state["brand_template"]`.**
`library/tools/brand_registry.py` is the whole vocabulary: `project_template_name`, `resolve_template_reference` (raising on missing) and `reference_template_name`. [why](docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run)
The key is spelled three ways and they are not interchangeable:
- `state["brand_template"]` is the REFERENCE string.
- `inputs["brand_template"]` is the RESOLVED TEMPLATE DICT, and reaches only a step whose manifest declares it (step 5.01 does `brand_template.get("style")`).
- `inputs["brand_style"|"brand_effect"|"brand_content"]` are the slot dicts.

**A project that names no brand template gets NOTHING, and every slot's reading of that absence is written down.**
`library/tools/brand_registry.no_brand_template` is what an empty declaration resolves to - every creative slot empty - and `ABSENT_SLOT_READINGS` records what each consumer does with it. `describe_brand_absence()` is printed once per run. [why](docs/RULE_EVIDENCE.md#a-template-nobody-chose)
- **The product ships no templates** (captain, 2026-09-21): a project
  carries its own `brand.json`, which every resolver below reads FIRST.
  An empty declaration still resolves to `no_brand_template()`.
- An absent slot reads as the ABSENCE OF DECORATION, never as a substitute taste: no grade (§12), no exposure normalisation, the whole drawable vocabulary permitted, nothing bounded. Add a slot, add its row. Add a slot, add its row - `tests/test_brand_template_load.py` fails on a slot with no recorded reading.
- **`effect.caption_case` is the one creative value that survives absence**, recorded as an exception rather than left implicit.
- Two slots have NO READER and no template value should state one: `content.music_genre` and `effect.sfx_density`.

**A project's own declarations reach every step through `state["project_config"]`.**
`brand_registry.project_declared_config` reads them off project.yaml, `load_pipeline_state` puts them in state and the runner's whitelist broadcasts them. Only what the project DECLARES is in there; an undeclared key is absent, never filled in.
Only what the project DECLARES is in there; an undeclared key is absent, never filled in.
- `target_duration_seconds` is the one with readers. [why](docs/RULE_EVIDENCE.md#a-template-nobody-chose)
- `library/tools/duration_targets.get_target_duration_zone` returns **None** when neither the project nor a selected template declares a target, and each caller says it did not check. There is no fallback zone.

**A brand's CONSTRAINTS reach three planning steps, and a step has two names.**
`TemplateLoader.get_brand_constraints` gives `creative_direction` a palette and typography, `plan_transitions` the permitted transition vocabulary and `plan_vfx` a VFX intensity - as prompt text, not as an input key.
`library/tools/template_loader.BRAND_CONSTRAINT_STEPS` is that enumeration, checked against the step table at import.
- **The DAG knows `plan_vfx`; the step's manifest and directory know `step_4_03_plan_vfx`, and no rule connects them.** `library/tools/project_layout.node_id_for` is the ONLY translator. [why](docs/RULE_EVIDENCE.md#the-brand-reached-no-planning-step)
- The `agent` request file records `constraints` and concatenates it into `prompt`, because in that mode the file IS the prompt.
- `tests/test_brand_constraints_reach_the_prompt.py`.
"""

import os
import json
from dataclasses import asdict
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

from library.schemas.brand_template import BrandTemplate, StyleSlots, EffectSlots, ContentSlots

# ── What a project that names no brand template gets ──
#
# It gets NOTHING, and that is a decision rather than an omission.
#
# For the whole life of the pipeline an empty declaration resolved to
# `library/templates/default_brand.yaml`, so a project that had chosen no
# brand rendered under one anyway.  On project 001 that unchosen template
# lowercased all 45 caption cards, held both drawn transitions for 500 ms
# against the model's own "quick" and "medium", told step 2.01 the series
# runs at "high" energy and told step 4.03 to plan VFX at an intensity of
# 0.5.  The model said so itself, at 2.01: those values are "a default
# nobody chose for this project rather than a brand decision".
#
# So an absent declaration now declares NOTHING, and every consumer reads
# that absence through a rule that is already written down and is the
# absence of decoration rather than a substitute taste.  The table is the
# statement of what absence means; keep it true when a slot changes.
ABSENT_SLOT_READINGS = {
    "style.series_look": (
        "NO look and NO exposure normalisation: no CDL, no contrast, no "
        "glow, no grain, no vignette.  There is nothing to fall back to - "
        "the engine ships no look values at all, and the clip's measured "
        "luma is recorded without anything acting on it "
        "(AGENTS.md 12, library/tools/series_look.py)"),
    "style.color_palette": (
        "no palette, so each consumer keeps its own colour "
        "(library/tools/brand_palette.py)"),
    "style.energy_profile": (
        "no energy is asserted to any step; the creative direction's own "
        "`target_energy` is the only energy the pipeline reads "
        "(library/tools/energy_reading.py)"),
    "style.framing_intent": (
        "the frame fills, from the one enumeration "
        "(library/tools/framing_intent.py)"),
    "style.tv_frame": (
        "no TV-frame look: V1 plays at its conformed zoom with no "
        "punch-in, no frame asset is placed on V2, and no power "
        "animation is drawn (library/tools/tv_frame.py)"),
    "style.typography": (
        "the `default_subtitles` shape, 160/800 - the preference layer's "
        "explicit `video_prefs.DEFAULT_SUBTITLE_STYLE`, PARKED with the "
        "rest of the style layer, inventoried and deliberately unchanged "
        "(library/tools/subtitle_style.py)"),
    "effect.transition_types": (
        "every drawable type is permitted, because an allow-list is a "
        "permission and not an instruction "
        "(library/tools/transition_vocabulary.PLANNABLE_TYPES)"),
    "effect.transition_duration_ms": (
        "no bound and no declared length, so a drawn transition is held "
        "for as long as the PLAN's own `duration_feel` says "
        "(library/tools/transition_selector.py)"),
    "effect.vfx_intensity": (
        "no intensity constraint reaches step 4.03's prompt "
        "(library/tools/template_loader.py)"),
    "effect.subtitle_style": (
        "the `default_subtitles` shape - the preference layer's explicit "
        "`video_prefs.DEFAULT_SUBTITLE_STYLE`, see style.typography"),
    "effect.caption_case": (
        "lowercase - the preference layer's explicit "
        "`video_prefs.DEFAULT_CAPTION_CASE`. PARKED.  It is the one "
        "creative value that survives an absent template, it is why "
        "every caption card on 001 is lowercase, and which case the copy "
        "is set in is the captain's open decision "
        "(step_4_01_plan_subtitles/step.py)"),
    "effect.timed_text_overlay": (
        "no timed text (library/tools/timed_text_overlay.py)"),
    "content.bookends": "no intro, no outro, no end card (library/tools/bookends.py)",
    "content.closing_lockup": (
        "no closing lines: the closing animation renders the logo-only "
        "version, exactly as before (library/tools/logo_bulb.py)"),
    "content.target_duration_seconds": (
        "no duration zone from the brand; the PROJECT's own "
        "`target_duration_seconds` is the declaration the gates measure "
        "against, and when neither declares one nothing is checked "
        "(library/tools/duration_targets.py)"),
    "delivery_format": (
        "the product's own enumeration decides, and its default is "
        "vertical 1080x1920 - the preference layer's explicit "
        "`video_prefs.DEFAULT_DELIVERY_FORMAT` "
        "(library/tools/delivery_format.py)"),
    "content.music_genre": (
        "NO READER.  Step 2.04's handoff names `brand_content.music_genre` "
        "but no manifest routes brand_content to it, so the slot reaches "
        "no prompt from any template, chosen or not"),
    "effect.sfx_density": (
        "NO READER.  `audio_reactive_sfx.scale_sfx_density` was its only "
        "one and was deleted (AGENTS.md 10.5)"),
}


def no_brand_template() -> BrandTemplate:
    """The template a project that names no brand template runs under.

    Every creative slot is EMPTY.  See `ABSENT_SLOT_READINGS` for what
    each consumer does with that, and `describe_brand_absence()` for the
    line the run prints so the absence is stated rather than inferred.

    `caption_case` keeps its dataclass default and is the one exception,
    recorded as such in the table above.
    """
    return BrandTemplate(
        series_id="",
        style=StyleSlots(),
        # NOT `transition_types=PLANNABLE_TYPES`.  An empty allow-list is
        # what "declares no permission" means, and `filter_allowed` plus
        # `select_transition` already read that as the full drawable
        # vocabulary - writing the vocabulary in here would make an
        # absent declaration indistinguishable from a template that
        # really listed all seven types.
        effect=EffectSlots(),
        content=ContentSlots(),
    )


# The absent slots that change the finished PICTURE or SOUND rather than
# only the prompt.  Named in the run's own output, because "no brand
# template" is otherwise a sentence a reader can pass over.
PICTURE_CONSEQUENCES_OF_ABSENCE = (
    "no look at all: no CDL, no contrast, no glow, no grain and no "
    "vignette, and no exposure normalisation either.  Step 5.01 measures "
    "each clip's luma, records it, and applies nothing (look_notes says "
    "so).  A clip only gets a Fusion comp if the VFX plan or the "
    "subject-safe conform put one there",
    "no palette, so captions and motion graphics keep their own colours",
    "no transition duration bound, so a drawn transition is held for as "
    "long as the plan's own duration_feel says",
    "no bookends, so no intro, outro or end card",
)


def describe_brand_absence() -> str:
    """One paragraph, printed once per run, stating what absence means."""
    return (
        "No brand template: this project's project.yaml declares no "
        "`pipeline.brand_template`, so no brand style, effect or content "
        "slot is in play. It is NOT rendering under default_brand.yaml. "
        "What that changes in the finished video: "
        + "; ".join(PICTURE_CONSEQUENCES_OF_ABSENCE)
        + ". Name a template under `pipeline.brand_template` to declare "
        "any of them. See ABSENT_SLOT_READINGS in "
        "library/tools/brand_registry.py for every slot."
    )


def load_brand_template(template_path: str) -> BrandTemplate:
    """Load a template from a PATH.  No path means no brand template.

    This used to substitute the in-code default, which is the same silent
    substitution `resolve_project_template` stopped doing: a caller that
    asked for a file and got taste back had no way to tell.
    """
    if not template_path or not os.path.exists(template_path):
        return no_brand_template()

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

# The project-side brand file.  A `brand.json` sitting in the project IS a
# declaration: it wins over anything in `templates_dir`, the same way
# `TemplateLoader.load_template` already reads it first.  The product ships
# no templates (captain, 2026-09-21); a project carries its own copy, so
# this is the path every resolver below checks before any other.
BRAND_JSON = "brand.json"


def project_brand_path(project_folder: Optional[str]) -> Optional[str]:
    """The project's own `brand.json`, or None where it has none."""
    if not project_folder:
        return None
    candidate = os.path.join(str(project_folder), BRAND_JSON)
    return candidate if os.path.exists(candidate) else None

# The name `manage_project.py new` used to stamp onto every project.  The
# product ships no templates (captain, 2026-09-21), so nothing resolves it
# anymore; it is kept so the name reads as what it is - a removed file -
# rather than as a typo.  New projects declare no template.
DEFAULT_TEMPLATE_NAME = "default_brand"

# The name a template-less project reports.  It is not a template name;
# nothing resolves it.
NO_BRAND_TEMPLATE_NAME = ""


def resolve_project_template(template_name: str = "",
                             templates_dir: Optional[str] = None,
                             project_folder: Optional[str] = None
                             ) -> BrandTemplate:
    """The BrandTemplate a project runs under, by NAME rather than path.

    `load_brand_template` takes a filesystem path, so every caller that
    started from a project.yaml `pipeline.brand_template` name had to
    rebuild the same `templates/<name>.yaml` join.  There were two copies
    of that join and they could disagree; this is the one.

    The project's own `brand.json` is read FIRST and wins over any named
    template - the same precedence `TemplateLoader.load_template` keeps.
    The product ships no templates (captain, 2026-09-21), so for a real
    project this is the source that answers; the name is kept as the
    declaration of record.

    An EMPTY name with no `brand.json` means the project declared none,
    and it resolves to `no_brand_template()` - no style, no effect, no
    content.  It used to resolve to `default_brand` on disk, which is how
    project 001 rendered under a brand nobody chose; see
    `ABSENT_SLOT_READINGS` above for what each consumer now does with an
    absent slot.

    Any name that resolves nowhere RAISES: a named template with no
    project-side copy is a typo or a removed file, and silently
    substituting a default is how a project renders under a brand nobody
    chose.
    """
    brand_path = project_brand_path(project_folder)
    if brand_path is not None:
        return load_brand_template(brand_path)
    if not template_name:
        return no_brand_template()
    base = templates_dir or TEMPLATES_DIR
    for ext in (".yaml", ".yml", ".json"):
        candidate = os.path.join(base, f"{template_name}{ext}")
        if os.path.exists(candidate):
            return load_brand_template(candidate)
    available = (sorted({os.path.splitext(f)[0] for f in os.listdir(base)})
                 if os.path.isdir(base) else [])
    raise FileNotFoundError(
        f"Brand template {template_name!r} not found in {base}. "
        f"Available: {available}. The product ships no templates - "
        f"carry the brand in the project's own brand.json."
    )


def project_pipeline_block(project_folder: Optional[str]) -> dict:
    """The ``pipeline:`` mapping of a project's project.yaml, or ``{}``.

    Every project-level pipeline declaration - the brand template name,
    the delivery format override, the framing intent - is read from here,
    so the parse lives once.  It was written out twice before this and
    the copies could disagree about what "declares nothing" means.
    """
    if not project_folder:
        return {}
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return {}
    if yaml is None:
        raise ImportError("PyYAML is required to read project.yaml.")
    with open(project_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    block = cfg.get("pipeline") or {}
    return block if isinstance(block, dict) else {}


# The timeline the build writes into.  `ResolveConfig.timeline_name` has
# been in library/schemas/project_config.py since the schema was written
# and NOTHING read it, while step 5.04 wrote the literal "Pipeline_Edit"
# into every manifest and step 6.01 deleted whatever already carried that
# name.  So a project could not say where its own build should go, and a
# re-render destroyed the timeline the captain had been annotating.
# Same shape as `brand_template` before #194: a declaration with no reader.
#
# The default is MECHANICAL - a name, like a path or a codec, not a
# creative value (AGENTS.md 10.5) - and it is the name this pipeline has
# always used, so a project that declares nothing builds where it always did.
DEFAULT_TIMELINE_NAME = "Pipeline_Edit"


def project_timeline_name(project_folder: Optional[str]) -> str:
    """The timeline name a project declares under ``resolve:``.

    Returns :data:`DEFAULT_TIMELINE_NAME` when the project declares none,
    which keeps every existing project building where it always has.
    """
    if not project_folder:
        return DEFAULT_TIMELINE_NAME
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return DEFAULT_TIMELINE_NAME
    if yaml is None:
        raise ImportError("PyYAML is required to read project.yaml.")
    with open(project_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    block = cfg.get("resolve") or {}
    if not isinstance(block, dict):
        return DEFAULT_TIMELINE_NAME
    declared = (block.get("timeline_name") or "").strip()
    return declared or DEFAULT_TIMELINE_NAME


# What a project's own project.yaml may declare about the PRODUCT, as
# opposed to about a brand.  Only `target_duration_seconds` has a reader;
# the other two are what step 1.01 has always emitted and are kept so the
# two producers of `project_config` cannot disagree about its shape.
PROJECT_CONFIG_KEYS = (
    "target_duration_seconds",
    "style_preset",
    "subtitle_style",
)


def project_declared_config(project_folder: Optional[str]) -> dict:
    """The `project_config` a project DECLARES, with nothing added.

    An undeclared key is ABSENT, never filled in.  Step 1.01 used to write
    `target_duration_seconds: 60`, `style_preset: "shortform_vertical"`
    and `subtitle_style: "word_by_word"` for a project that declared none,
    so a length nobody chose was indistinguishable from one the captain
    typed.
    """
    if not project_folder:
        return {}
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return {}
    if yaml is None:
        raise ImportError("PyYAML is required to read project.yaml.")
    with open(project_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        return {}
    return {k: cfg[k] for k in PROJECT_CONFIG_KEYS if cfg.get(k) is not None}


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
    :func:`resolve_project_template` turns into `no_brand_template()`
    unless the project carries its own `brand.json`.
    """
    return (project_pipeline_block(project_folder).get("brand_template") or "").strip()


def _looks_like_path(reference: str) -> bool:
    return (os.sep in reference
            or reference.lower().endswith((".yaml", ".yml", ".json")))


def resolve_template_reference(reference: str = "",
                               templates_dir: Optional[str] = None,
                               project_folder: Optional[str] = None
                               ) -> BrandTemplate:
    """The BrandTemplate for either a template NAME or a template PATH.

    Two callers spell the same thing differently and both are legitimate:
    a project.yaml declares a NAME the project's own `brand.json`
    carries, while the process manifest's `brand_template` input is
    documented as a PATH so a template may live anywhere.  The two are
    told apart structurally - a separator or a yaml/json extension means
    path - rather than by "does it exist", so a mistyped path raises as
    a mistyped path instead of being retried as a template name.

    The project's own `brand.json` wins over either form - a path the
    captain typed is honoured, but a NAME resolves against the project's
    copy first, because the product ships no templates.

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
    return resolve_project_template(ref, templates_dir=templates_dir,
                                    project_folder=project_folder)


def reference_template_name(reference: str = "") -> str:
    """The bare template NAME for either form of reference.

    `TemplateLoader` (library/tools/template_loader.py) resolves by name
    against its own templates dir, so it needs the name half of whatever
    the run is carrying.

    An empty reference answers "" - NOT `default_brand`.  Answering
    `default_brand` here is what sent every template-less project's LLM
    steps the fallback template's constraints: step 2.01 was told the
    series runs at "high" energy and step 4.03 was told to plan at
    intensity 0.5, on a project that had chosen neither.
    """
    ref = (reference or "").strip()
    if ref and _looks_like_path(ref):
        return os.path.splitext(os.path.basename(ref))[0]
    return ref or NO_BRAND_TEMPLATE_NAME


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
    # "" is "declares none", which is now a legitimate state: a
    # template-less project resolves to `no_brand_template()`.
    if template.style.energy_profile not in ["", "calm", "moderate", "high"]:
        errors.append(f"Invalid energy_profile: {template.style.energy_profile}")
    if template.effect.vfx_intensity < 0.0 or template.effect.vfx_intensity > 1.0:
        errors.append(f"Invalid vfx_intensity: {template.effect.vfx_intensity} must be between 0.0 and 1.0")
    if template.style.framing_intent is not None:
        if not (0.0 <= template.style.framing_intent <= 1.0):
            errors.append(f"Invalid framing_intent: {template.style.framing_intent} must be between 0.0 and 1.0")
    if template.style.tv_frame is not None:
        if not isinstance(template.style.tv_frame, dict):
            errors.append(
                f"Invalid tv_frame: must be a mapping with "
                f"'asset', 'punch_in' and 'power' (library/tools/tv_frame.py), "
                f"got {type(template.style.tv_frame).__name__}"
            )
    if template.effect.sfx_density not in ["", "sparse", "moderate", "dense"]:
        errors.append(f"Invalid sfx_density: {template.effect.sfx_density}")
    return errors
