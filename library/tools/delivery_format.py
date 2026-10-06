"""The delivery format: the frame the PRODUCT ships in.

Captain's ruling of 2026-08-19. The delivery format is a property of the
product, declared by the brand template, overridable per project, and
defaulting to vertical 1080x1920.

The frame has two halves and both are the product's: the GEOMETRY
(``DELIVERY_FORMATS``, below) and the COLOR TRANSFORM
(``OUTPUT_TRANSFORMS``) - the color space and gamma the graded picture
is delivered in. Step 5.01 used to hardcode ``output_color_space`` as
"Rec.709, Gamma 2.4", an engine constant presented as a product intent:
declared by nobody, read by nobody, and wrong for any project that
delivers HDR. The transform is resolved the same way the geometry is -
project ``pipeline.output_transform`` > brand template
``output_transform`` > the SDR default - and the grade spec records the
resolution and its source beside the value.

What it replaced, and why it mattered
-------------------------------------
Step 1.02 derived a ``project_resolution`` from the MODAL SOURCE
RESOLUTION of the footage and the manifest handed that straight to
Resolve as the timeline size.  On project 001 - 17 landscape clips - the
delivery format was therefore 1920x1080, and two whole mechanisms went
quietly dead:

* **Framing became a no-op.**  Letterbox versus subject-tracked fill is a
  decision about fitting a landscape source into a vertical frame.  With
  the target equal to the source there is nothing to fit, so
  ``compile_manifest._conform_fields`` returned "no conform needed" for
  every clip and the whole P1.1/P1.2 mechanism idled.
* **The overlays were already vertical.**  Subtitles and motion graphics
  render at the delivery format; composited onto a landscape timeline
  they appeared as a visible lighter band down the central 1080px of the
  picture.  gemma-4-12b named the aspect-ratio defect unprompted on five
  of eight sampled frames.

So the source resolution is now reported as what it honestly is -
``source_resolution`` on the catalog, a DESCRIPTION of the footage - and
the render target comes from here.  ``tests/unit/picture/test_delivery_format.py``
fails if the retired ``project_resolution`` key reappears anywhere.

One enumeration
---------------
Like ``series_look`` and ``transition_vocabulary``, this is a closed
enumeration and an unknown name RAISES.  A silent fallback is exactly how
a landscape master ships again.  Adding a format means adding a row.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**The delivery format is a property of the PRODUCT, not of the footage.**
One enumeration, `library/tools/delivery_format.py`: a brand template declares `delivery_format`, a project may override with `pipeline.delivery_format`, the default is vertical 1080x1920, and an unknown name raises.
The catalog's `source_resolution` DESCRIBES the footage and is never a render target. [why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)
[why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)
"""

from typing import Dict, List, Optional, Tuple

# name -> (width, height).  Names are self-describing on purpose: the value
# appears in project.yaml and in brand templates, where a reader has no
# type to consult.
DELIVERY_FORMATS: Dict[str, Tuple[int, int]] = {
    "vertical_1080x1920": (1080, 1920),    # 9:16 - Reels / Shorts / TikTok
    "vertical_2160x3840": (2160, 3840),    # 9:16 at 4K
    "square_1080x1080": (1080, 1080),      # 1:1 - feed posts
    "horizontal_1920x1080": (1920, 1080),  # 16:9 - long-form / YouTube
}

# The captain's default.  A template or project that declares nothing
# ships vertical.
DEFAULT_DELIVERY_FORMAT = "vertical_1080x1920"


def format_names() -> List[str]:
    """Every delivery format a template or project may name."""
    return sorted(DELIVERY_FORMATS)


def resolve_format_name(name: str) -> Tuple[int, int]:
    """(width, height) for a declared format name.

    Raises on an unknown name.  An empty name is not a declaration and
    yields the default - that is how "declares nothing" is spelled.
    """
    if not name:
        return DELIVERY_FORMATS[DEFAULT_DELIVERY_FORMAT]
    if not isinstance(name, str):
        raise TypeError(
            f"delivery_format must be one of {format_names()}, "
            f"got {type(name).__name__}: {name!r}"
        )
    key = name.strip()
    if key not in DELIVERY_FORMATS:
        raise ValueError(
            f"Unknown delivery_format {name!r}. "
            f"Known formats: {format_names()}. "
            "Add a row to library/tools/delivery_format.py to introduce one."
        )
    return DELIVERY_FORMATS[key]


def _project_pipeline_block(project_folder: Optional[str]) -> dict:
    """The `pipeline:` mapping of a project's project.yaml, or {}.

    One parse, in library/tools/brand_registry.py - this module, that one
    and framing_intent all read the same block.
    """
    from library.tools.brand_registry import project_pipeline_block
    return project_pipeline_block(project_folder)


def delivery_format_name(project_folder: Optional[str] = None,
                         templates_dir: Optional[str] = None,
                         reel=None,
                         video_preferences: Optional[dict] = None) -> str:
    """The declared format name, by precedence.

    Merged video preferences (``style.yaml`` locked value, then
    ``video.yaml`` shared and per-reel layers - see
    ``library/tools/video_prefs.effective_delivery_format``)  >
    project.yaml ``pipeline.delivery_format``  >  the brand template's
    ``delivery_format``  >  :data:`DEFAULT_DELIVERY_FORMAT`.

    The video preference wins because a locked project value is the
    series' own frame and a per-video value is the video's own frame,
    while the project override below it lets a series ship one video
    in a different frame without forking its template. A video that
    declares nothing reads exactly what it always read. `reel` is the
    addressed reel where the caller has one (the Ren change spec's
    `reel` on the reels path; absent for a single-video project), and
    `video_preferences` is an already-merged mapping that wins over a
    disk read.
    """
    from library.tools import video_prefs as _video_prefs
    preferred = _video_prefs.effective_delivery_format(
        project_folder or "", reel=reel,
        video_preferences=video_preferences)
    if preferred:
        resolve_format_name(preferred)  # raise here, naming the preference
        return preferred

    pipeline_block = _project_pipeline_block(project_folder)

    override = (pipeline_block.get("delivery_format") or "").strip()
    if override:
        resolve_format_name(override)  # raise here, naming the project
        return override

    template_name = (pipeline_block.get("brand_template") or "").strip()
    from library.tools.brand_registry import resolve_project_template
    template = resolve_project_template(
        template_name, templates_dir=templates_dir,
        project_folder=project_folder)
    declared = (getattr(template, "delivery_format", "") or "").strip()
    if declared:
        resolve_format_name(declared)  # raise here, naming the template
        return declared

    return DEFAULT_DELIVERY_FORMAT


def resolve_delivery_format(project_folder: Optional[str] = None,
                            templates_dir: Optional[str] = None,
                            reel=None,
                            video_preferences: Optional[dict] = None
                            ) -> List[int]:
    """``[width, height]`` the render, the overlays and the conform all use.

    This is the ONE call every consumer makes.  It is deliberately a
    function of the project rather than a value threaded through the DAG:
    a value in flight can be renamed, defaulted and lost, which is the
    dominant bug class in this pipeline, and this one was lost that way
    already - ``project_resolution`` was mapped by no DAG edge at all, so
    every ``.get("project_resolution", [1080, 1920])`` in the tree read
    its own fallback.
    """
    width, height = resolve_format_name(
        delivery_format_name(
            project_folder, templates_dir=templates_dir, reel=reel,
            video_preferences=video_preferences)
    )
    return [width, height]


# ── The color half of the delivery frame ─────────────────────────────
#
# The output transform: the color space and gamma the graded picture is
# delivered in. A property of the PRODUCT, declared by the brand
# template's top-level ``output_transform``, overridable per project
# through ``pipeline.output_transform``, defaulting to Rec.709 Gamma 2.4
# (SDR). One enumeration; an unknown name RAISES - a silent fallback is
# how a project ships in a color space nobody chose.

#: Every output transform a template or project may name. Names are
#: self-describing on purpose: the value appears in project.yaml and in
#: brand templates, where a reader has no type to consult.
OUTPUT_TRANSFORMS: Dict[str, Dict[str, str]] = {
    "rec709_gamma24": {"color_space": "Rec.709", "gamma": "Gamma 2.4"},
    "rec709_gamma22": {"color_space": "Rec.709", "gamma": "Gamma 2.2"},
    "rec2020_gamma24": {"color_space": "Rec.2020", "gamma": "Gamma 2.4"},
}

#: The captain's default. A template or project that declares nothing
#: ships Rec.709 Gamma 2.4 - the SDR delivery this pipeline has always
#: rendered. MECHANICAL, like a codec: the color space a finished SDR
#: video is delivered in, not a taste.
DEFAULT_OUTPUT_TRANSFORM = "rec709_gamma24"


def output_transform_names() -> List[str]:
    """Every output transform a template or project may name."""
    return sorted(OUTPUT_TRANSFORMS)


def resolve_output_transform_name(name: str) -> Dict[str, str]:
    """``{color_space, gamma}`` for a declared output transform name.

    Raises on an unknown name. An empty name is not a declaration and
    yields the default - that is how "declares nothing" is spelled.
    """
    if not name:
        return OUTPUT_TRANSFORMS[DEFAULT_OUTPUT_TRANSFORM]
    if not isinstance(name, str):
        raise TypeError(
            f"output_transform must be one of {output_transform_names()}, "
            f"got {type(name).__name__}: {name!r}"
        )
    key = name.strip()
    if key not in OUTPUT_TRANSFORMS:
        raise ValueError(
            f"Unknown output_transform {name!r}. "
            f"Known transforms: {output_transform_names()}. "
            "Add a row to library/tools/delivery_format.py to introduce one."
        )
    return OUTPUT_TRANSFORMS[key]


def resolve_output_transform(template=None,
                             project_folder: Optional[str] = None) -> dict:
    """The output transform this product delivers, and who declared it.

    Precedence: project ``pipeline.output_transform`` > the brand
    template's top-level ``output_transform`` > :data:`DEFAULT_OUTPUT_TRANSFORM`.
    The same shape ``delivery_format_name`` uses for the geometry.

    Args:
        template: the brand template carrying the declaration - a mapping
            with an ``output_transform`` key or a BrandTemplate, or None
            where the caller holds none (the project override still reads).
        project_folder: the project's folder, for the ``pipeline:`` block.

    Returns:
        ``{name, color_space, gamma, output_color_space, source}`` -
        the resolved transform, the human-readable string step 5.01
        records, and ``source`` naming who declared it ("default
        (nothing declared)" where nobody did). The source travels beside
        the value so a reader can tell a declared transform from the
        engine's SDR default.
    """
    pipeline_block = _project_pipeline_block(project_folder)

    override = (pipeline_block.get("output_transform") or "").strip()
    if override:
        resolve_output_transform_name(override)  # raise here, naming the project
        name = override
        source = "project.yaml pipeline.output_transform"
    else:
        declared = ""
        if template is not None:
            if isinstance(template, dict):
                declared = (template.get("output_transform") or "").strip()
            else:
                declared = (getattr(template, "output_transform", "") or "").strip()
        if declared:
            resolve_output_transform_name(declared)  # raise here, naming the template
            name = declared
            source = "brand template output_transform"
        else:
            name = DEFAULT_OUTPUT_TRANSFORM
            source = "default (nothing declared)"

    transform = OUTPUT_TRANSFORMS[name]
    return {
        "name": name,
        "color_space": transform["color_space"],
        "gamma": transform["gamma"],
        "output_color_space": f"{transform['color_space']}, {transform['gamma']}",
        "source": source,
    }


def read_output_transform(manifest: Optional[dict]) -> Optional[str]:
    """The output transform the assembly manifest carries, or None.

    The render reads this to report - and, live, to set - the color
    management the graded picture is delivered in. A manifest that
    carries none is None, never a default: an absent transform and a
    declared one are different facts, and the render must not invent one.
    """
    if not isinstance(manifest, dict):
        return None
    color_grade = manifest.get("color_grade")
    if not isinstance(color_grade, dict):
        return None
    value = color_grade.get("output_color_space")
    return value if isinstance(value, str) and value.strip() else None
