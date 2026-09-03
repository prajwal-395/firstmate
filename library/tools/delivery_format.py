"""The delivery format: the frame the PRODUCT ships in.

Captain's ruling of 2026-08-19. The delivery format is a property of the
product, declared by the brand template, overridable per project, and
defaulting to vertical 1080x1920.

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
the render target comes from here.  ``tests/test_delivery_format.py``
fails if the retired ``project_resolution`` key reappears anywhere.

One enumeration
---------------
Like ``house_look`` and ``transition_vocabulary``, this is a closed
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
                         templates_dir: Optional[str] = None) -> str:
    """The declared format name, by precedence.

    project.yaml ``pipeline.delivery_format``  >  the brand template's
    ``delivery_format``  >  :data:`DEFAULT_DELIVERY_FORMAT`.

    The project override wins because a series may ship one video in a
    different frame without forking its template.
    """
    pipeline_block = _project_pipeline_block(project_folder)

    override = (pipeline_block.get("delivery_format") or "").strip()
    if override:
        resolve_format_name(override)  # raise here, naming the project
        return override

    template_name = (pipeline_block.get("brand_template") or "").strip()
    from library.tools.brand_registry import resolve_project_template
    template = resolve_project_template(template_name, templates_dir=templates_dir)
    declared = (getattr(template, "delivery_format", "") or "").strip()
    if declared:
        resolve_format_name(declared)  # raise here, naming the template
        return declared

    return DEFAULT_DELIVERY_FORMAT


def resolve_delivery_format(project_folder: Optional[str] = None,
                            templates_dir: Optional[str] = None) -> List[int]:
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
        delivery_format_name(project_folder, templates_dir=templates_dir)
    )
    return [width, height]
