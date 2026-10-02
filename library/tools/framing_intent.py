"""How much of the delivery frame the picture fills. One enumeration.

``framing_intent`` is a normalised scalar: **0.0 letterboxes**
(:data:`LETTERBOX` - the source is fitted inside the frame and the
leftover is black), **1.0 fills** (:data:`FILL` - the source is scaled
until the frame is covered and the crop window follows the subject), and
values between punch in partway.  ``compile_manifest._conform_fields``
owns the geometry that turns it into a zoom and a pan; this module owns
only the question of *which number a clip gets*.

Why the default is FILL
-----------------------
:data:`DEFAULT_FRAMING_INTENT` is FILL, and there is no heuristic.  A
template that declares a vertical delivery format says what the product
IS, and silently shipping a landscape strip inside it is a defect.  The
risk a heuristic once guarded against - a blind centre crop beheading a
speaker - is handled by aiming the crop: the window follows
``library/tools/subject_framing.subject_center_x``, measured off the
face-centre track ``step_1_04_temporal_index.compute_face_presence``
banks, and stays centred when the footage cannot support a position.
A series that wants bars declares ``style.framing_intent: 0.0`` in its
own ``brand.json``.

Precedence
----------
    spine block ``framing_intent``      per-clip creative choice
      > project.yaml ``pipeline.framing_intent``
      > brand template ``style.framing_intent``
      > :data:`DEFAULT_FRAMING_INTENT`

:func:`resolve_framing_intent` walks it.  The project level is the same
project-over-template precedence ``delivery_format_name`` and
``timed_text_overlay.resolve_declaration`` use: a series may ship one
video framed differently without forking its template.

``framing_crop_factor`` is the second control, how tight WITHIN fill
(1.0 is no extra crop, bounded by :data:`MAX_CROP_FACTOR`), resolved by
the same chain (:func:`resolve_crop_factor`, ``pipeline.`` /
``style.framing_crop_factor``, :data:`DEFAULT_CROP_FACTOR`).

A malformed declaration RAISES (:func:`validate_framing_intent`,
:func:`validate_crop_factor`).  A framing declaration that is silently
dropped is a frame the editor believes shipped.

Declared is not delivered
-------------------------
A declaration says what the editor WANTS.  What a clip can actually give
is arithmetic, and the two differ in one direction: **a source whose
display aspect already matches the delivery frame has no bars to give.**
It covers the frame at every intent, ``0.0`` included
(:func:`source_covers_frame`).  So one declaration of ``0.0`` on a mixed
edit bars the landscape A-roll and leaves portrait cutaways full-bleed:
the per-clip result is that one choice meeting each source's measured
aspect, not a second creative choice.

:func:`delivered_framing_intent` is that reading, and
``compile_manifest._conform_fields`` records it beside the declaration as
``framing_delivered``, so ``render_qa``'s occupancy gate learns what the
picture was SUPPOSED to look like from what was delivered, not from what
was declared.

The measured letterbox export and the creative-direction lines behind
the FILL default: docs/evidence/framing_intent.md.


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**The frame FILLS by default, and there is no heuristic.**
One enumeration, `library/tools/framing_intent.py`: 0.0 letterboxes, 1.0 fills.
The number comes from the spine block > the project's `pipeline.framing_intent` > the template's `style.framing_intent` > `DEFAULT_FRAMING_INTENT` (1.0).
`tests/test_framing_intent.py`.
Source: spine block > project > template > `DEFAULT_FRAMING_INTENT` (1.0). [why](docs/RULE_EVIDENCE.md#the-letterbox-default)
**A framing DECLARATION is not a framing DELIVERED, and the manifest records both.**
`_conform_fields` writes `framing_intent` and `framing_delivered` on every clip. [why](docs/RULE_EVIDENCE.md#a-declaration-a-clip-cannot-honour)
- **A source whose display aspect already covers the delivery frame has no bars to give**, so it fills at every intent, `0.0` included. `framing_intent.source_covers_frame` is that predicate and `delivered_framing_intent` is the reading. Do not put the coverage arithmetic anywhere else.
- **Which clips letterbox is therefore a MEASUREMENT, not a second creative choice.** A step that chose a framing per clip would be inventing taste where the arithmetic already answers (section 10.5). **Whether the picture is inset at all is the captain's PREFERENCE and belongs in the project's own declaration.**
- **The spine-block level of the chain is reachable and unwritten.** `compile_manifest` really reads `block["framing_intent"]` (`tests/test_compile_manifest.py`), but no handoff asks for one and `spine_contract` does not list the key. On the B-roll side the hook is the PLACEMENT's own key, not the block it covers: a cutaway is different footage.
- **A source that already covers the delivery frame fills at every intent.** `source_covers_frame` is the predicate.
- **Which clips letterbox is a MEASUREMENT, not a second creative choice** (section 10.5).
"""

import os
from typing import Optional

# Fully letterboxed: fit the source inside the frame, bars where it falls
# short.  Not a degraded state - a template may want exactly this.
LETTERBOX = 0.0

# Fully filled: scale until the frame is covered, crop window on the
# subject.
FILL = 1.0

# What a clip gets when nothing declares anything.  See the module
# docstring for why this is FILL and not a heuristic.
DEFAULT_FRAMING_INTENT = FILL

# ── Crop factor ──────────────────────────────────────────────────────
#
# How much ADDITIONAL zoom beyond fill to apply.  1.0 means no extra
# crop; 1.3 means 30% tighter, showing less of the source.  It is a
# separate control from framing_intent because it answers a different
# question:
#
# - framing_intent: how far between letterbox and fill?
# - framing_crop_factor: how tight WITHIN fill?
#
# For a podcast with two speakers in a wide landscape frame, the fill
# conform already zooms to 1920/2160 (if 4K) or 1920/1080 (if 1080p),
# cropping to 31.6-56.25% of the source width.  That often leaves too
# much empty room around the speakers.  The crop factor zooms in
# further, making the visible portion smaller and the speakers larger
# in frame.
#
# Declared in project.yaml as ``pipeline.framing_crop_factor`` or in
# a brand template as ``style.framing_crop_factor``.  The precedence
# chain is the same as framing_intent: project > template > default.
#
# ``_conform_fields`` multiplies ``fill_zoom`` by this factor, so the
# subject-safety check still fires and the backdrop route still engages
# when the tighter crop would cut a face.

DEFAULT_CROP_FACTOR = 1.0
"""No additional crop: the fill conform is used as-is."""

# Upper bound: zooming in more than 2x beyond fill would be extreme
# quality loss on most footage.  Not a threshold - it is a guard on a
# configuration value.
MAX_CROP_FACTOR = 2.0


def validate_framing_intent(value, source: str) -> float:
    """A declared value as a float in 0.0..1.0, or raise naming *source*.

    Out of range is a mistake, not something to clamp at this layer:
    clamping would accept ``framing_intent: 100`` as "fill" and the next
    reader of that project.yaml would believe 100 meant something.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"framing_intent in {source} must be a number between "
            f"{LETTERBOX} (letterbox) and {FILL} (fill), got "
            f"{type(value).__name__}: {value!r}"
        )
    intent = float(value)
    if not (LETTERBOX <= intent <= FILL):
        raise ValueError(
            f"framing_intent in {source} must be between {LETTERBOX} "
            f"(letterbox) and {FILL} (fill), got {intent}"
        )
    return intent


def project_framing_intent(project_folder: Optional[str]) -> Optional[float]:
    """``pipeline.framing_intent`` from a project.yaml, or None.

    None means the project declared none, which is different from 0.0.
    """
    from library.tools.brand_registry import project_pipeline_block

    block = project_pipeline_block(project_folder)
    if "framing_intent" not in block:
        return None
    declared = block.get("framing_intent")
    if declared is None:
        return None
    where = os.path.join(project_folder or "", "project.yaml")
    return validate_framing_intent(declared, where)


def template_framing_intent(template) -> Optional[float]:
    """``style.framing_intent`` off a BrandTemplate, or None."""
    if template is None:
        return None
    style = getattr(template, "style", None)
    declared = getattr(style, "framing_intent", None)
    if declared is None:
        return None
    series = getattr(template, "series_id", None) or "brand template"
    return validate_framing_intent(declared, f"template {series}")


def resolve_framing_intent(block_intent=None,
                           project_folder: Optional[str] = None,
                           template=None) -> float:
    """The framing intent one clip runs under, by precedence.

    ``block_intent`` is the optional ``framing_intent`` key on a spine
    block - a per-clip creative choice, which outranks everything.
    """
    if block_intent is not None:
        return validate_framing_intent(block_intent, "the spine block")

    declared = project_framing_intent(project_folder)
    if declared is not None:
        return declared

    declared = template_framing_intent(template)
    if declared is not None:
        return declared

    return DEFAULT_FRAMING_INTENT


def validate_crop_factor(value, source: str) -> float:
    """A declared value as a float in 1.0..MAX_CROP_FACTOR, or raise.

    Below 1.0 would zoom OUT from fill, which is what framing_intent
    already controls.  Above MAX_CROP_FACTOR is extreme quality loss.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"framing_crop_factor in {source} must be a number between "
            f"{DEFAULT_CROP_FACTOR} (no extra crop) and {MAX_CROP_FACTOR} "
            f"(maximum tightening), got {type(value).__name__}: {value!r}"
        )
    factor = float(value)
    if not (DEFAULT_CROP_FACTOR <= factor <= MAX_CROP_FACTOR):
        raise ValueError(
            f"framing_crop_factor in {source} must be between "
            f"{DEFAULT_CROP_FACTOR} (no extra crop) and {MAX_CROP_FACTOR} "
            f"(maximum tightening), got {factor}"
        )
    return factor


def project_crop_factor(project_folder: Optional[str]) -> Optional[float]:
    """``pipeline.framing_crop_factor`` from project.yaml, or None."""
    from library.tools.brand_registry import project_pipeline_block

    block = project_pipeline_block(project_folder)
    if "framing_crop_factor" not in block:
        return None
    declared = block.get("framing_crop_factor")
    if declared is None:
        return None
    where = os.path.join(project_folder or "", "project.yaml")
    return validate_crop_factor(declared, where)


def template_crop_factor(template) -> Optional[float]:
    """``style.framing_crop_factor`` off a BrandTemplate, or None."""
    if template is None:
        return None
    style = getattr(template, "style", None)
    declared = getattr(style, "framing_crop_factor", None)
    if declared is None:
        return None
    series = getattr(template, "series_id", None) or "brand template"
    return validate_crop_factor(declared, f"template {series}")


def resolve_crop_factor(
    block_crop_factor=None,
    project_folder: Optional[str] = None,
    template=None,
) -> float:
    """The crop factor a clip runs under, by precedence.

    ``block_crop_factor`` is the optional ``framing_crop_factor`` key
    on a spine block - a per-clip choice, which outranks everything.
    """
    if block_crop_factor is not None:
        return validate_crop_factor(block_crop_factor, "the spine block")

    declared = project_crop_factor(project_folder)
    if declared is not None:
        return declared

    declared = template_crop_factor(template)
    if declared is not None:
        return declared

    return DEFAULT_CROP_FACTOR


# How close two scale factors must be before the source counts as already
# covering the frame.  Float arithmetic on integer pixel dimensions, not a
# tolerance for "nearly 9:16" footage: 1920x1080 rotated is exactly
# 1080x1920 and the two scales agree to the last bit, while 1080x1350 is
# 1.42x off and is a genuine letterbox.
_COVERAGE_EPSILON = 1e-6


def source_covers_frame(source_width, source_height,
                        target_width, target_height) -> bool:
    """Whether a source already covers the delivery frame at fit scale.

    The dimensions are DISPLAY dimensions - axes already swapped for a
    rotated clip, which is how an iPhone portrait MOV stored 1920x1080
    becomes 1080x1920.

    When this is True the source has no bars to give: fitting it inside
    the frame and filling the frame are the same transform, so every
    framing intent from ``LETTERBOX`` to ``FILL`` delivers the same
    picture.
    """
    if not source_width or not source_height:
        return False
    if source_width <= 0 or source_height <= 0:
        return False
    if target_width <= 0 or target_height <= 0:
        return False
    fit_scale = min(target_width / source_width, target_height / source_height)
    fill_scale = max(target_width / source_width, target_height / source_height)
    return fill_scale <= fit_scale * (1 + _COVERAGE_EPSILON)


def delivered_framing_intent(declared: float, covers_frame: bool) -> float:
    """What a clip DELIVERS, given what it was told and what it can do.

    ``covers_frame`` is :func:`source_covers_frame` for this clip.  When
    it is True the clip fills whatever anybody declared, so the delivered
    intent is :data:`FILL`; otherwise the declaration is delivered as
    written.  See the module docstring for why the two must be recorded
    separately.
    """
    if covers_frame:
        return FILL
    return validate_framing_intent(declared, "the resolved framing intent")
