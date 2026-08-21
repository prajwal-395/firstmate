"""How much of the delivery frame the picture fills. One enumeration.

``framing_intent`` is a normalised scalar: **0.0 letterboxes** (the source
is fitted inside the frame and the leftover is black), **1.0 fills** (the
source is scaled until the frame is covered and the crop window follows
the subject), and values between punch in partway.
``compile_manifest._conform_fields`` owns the geometry that turns it into
a zoom and a pan; this module owns only the question of *which number a
clip gets*.

Why the default is FILL
-----------------------
It used to be a heuristic, and the heuristic was inverted.  With no
declaration, ``_conform_fields`` ran a "legacy auto-decision" whose rule
was: *if the primary subject is visible during this clip's source range,
a centre crop might cut them off, so prefer letterbox*.  On a selfie
monologue the subject is visible in every clip, so **every clip
letterboxed, always**.

Measured on the only finished export on disk - project 001,
``exports/Pipeline_Edit.mp4``, 1080x1920 - the A-roll picture occupied
rows 656..1263: 608 of 1920 rows, 31.7% of the frame height, with 61.6%
of all pixels below luma 12 averaged over 55 samples.  The three B-roll
cutaways are portrait-shot and filled the frame, so the video also
flipped between full-bleed and a thin strip three times.

That is not 9:16 delivery.  The captain's standing direction
(``PLAN/series portfolio '26 planning/overall_branding_creative_direction.md``)
says "Aspect ratio: 9:16 (1080x1920)" at line 363, "Every frame should
look deliberate" at 364 and "the frame should never be lazy" at 76.  A
template declares ``delivery_format: vertical_1080x1920`` to say what the
product IS; silently shipping a 16:9 strip inside it is the same class of
defect as the retired source-derived render target that
``delivery_format.py`` exists to close.

The thing the old rule was protecting is real - a blind centre crop
beheads a speaker standing in the left third - and it is now protected
properly rather than by refusing to crop at all.  The crop window follows
``library/tools/subject_framing.subject_center_x``, measured off the
face-centre track ``step_1_04_temporal_index.compute_face_presence``
already banks at 5Hz.  When the footage cannot support a position the
window stays centred, which is what ``subject_framing`` returning None has
always meant.

A series that genuinely wants bars still says so, and one already does:
``library/templates/cinematic_narrative.yaml`` declares
``style.framing_intent: 0.0`` because the letterbox is its look.

Precedence
----------
    spine block ``framing_intent``      per-clip creative choice
      > project.yaml ``pipeline.framing_intent``
      > brand template ``style.framing_intent``
      > :data:`DEFAULT_FRAMING_INTENT`

The project level is the one this module ADDS.  It is the same
project-over-template precedence ``delivery_format_name`` and
``timed_text_overlay.resolve_declaration`` use, and for the same reason: a
series may ship one video framed differently without forking its
template.

A malformed declaration RAISES.  A framing declaration that is silently
dropped is a frame the editor believes shipped.
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
