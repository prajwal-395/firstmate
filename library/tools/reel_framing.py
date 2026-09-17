"""What picture a BUILT reel timeline actually delivers to the frame.

Nothing on the reels path had ever looked at the picture
------------------------------------------------------
Measured 2026-09-06 on the captain's own Resolve project
``Podcast (field test)``.  The reels product is two nodes -
``build_reels`` then ``verify_reels`` (``library/processes/reels/dag.json``)
- and between them they open no video file and read no pixel:

- ``reel_conformance_verifier`` checks format, item count, picture holes,
  audio holes, caption timing and caption overlap.  All structural.
- ``reel_quality_bar`` judges from the transcript.
- ``reel_opening`` reads the opening WORDS.
- ``reel_proposal`` / ``reel_exchange`` / ``reel_spine`` read the
  transcript.

The engine has real picture analysis - ``step_1_04_temporal_index``
banks face position at 5Hz, ``picture_quality`` measures usable ranges,
``render_qa`` decodes a finished render, ``window_frames`` puts actual
JPEGs in a prompt - and **none of it is reachable from a reel**, because
those live in ``edit_video`` and the reels process runs neither.  On the
podcast project the preflight steps that would have produced them never
ran at all: ``pipeline_output/steps/`` holds ``1_01``, ``1_02``, an EMPTY
``1_03``, ``3_04``, ``3_05`` and ``4_05``.  There is no ``1_04``.

What that let through
---------------------
``reel_build.build_reel_timeline`` creates a 1080x1920 timeline and
appends 3840x2160 master clips with no transform at all.  It never reads
``framing_intent``.  Read off Resolve on 2026-09-06, all 49 timelines in
the project - the 20 harvest reels among them - carry the same 376 video
items with ``ZoomX=ZoomY=1.0, Pan=Tilt=0, Crop*=0, Scaling=0`` on
``timelineInputResMismatchBehavior='scaleToFit'``.

So every frame of every reel is the source fitted inside the frame:
1080 x 607.5 of a 1080x1920 delivery, **31.64% of the frame**, the rest
black.  That is not a subtle number and nothing could see it.

Why that is not (today) a defect, and why it is still the finding
----------------------------------------------------------------
The project adopts ``cinematic_narrative``, which declares
``style.framing_intent: 0.0`` - the letterbox is that template's look.
So what is delivered is what is declared, and this module PASSES the
captain's twenty reels.

It passes by coincidence.  ``build_reel_timeline`` never read the
declaration; Resolve's own default happened to agree with it.  The
engine's ``DEFAULT_FRAMING_INTENT`` is ``FILL``, so a project that
declares nothing - which is every project that does not adopt this one
template - asks for a full frame, gets a 31.6% strip, and nothing
anywhere says so.  A declaration that does not bind is the same defect
whether or not today's value happens to match.

What this module is
-------------------
The delivered picture is EXACT ARITHMETIC, not a judgement.  Given a
source's display size, the delivery frame and the item's own transform,
the rectangle the viewer sees is determined.  This module computes it and
compares it to the rectangle the project DECLARED, through
``library/tools/framing_intent.py`` - the existing enumeration, not a
second spelling of it.

**It invents no number.**  The zoom-to-intent map is the exact inverse of
``compile_manifest._conform_fields``' ``zoom = 1 + (max_zoom - 1) *
intent``; ``LETTERBOX``, ``FILL``, ``DEFAULT_FRAMING_INTENT`` and
``source_covers_frame`` are read from ``framing_intent``; and the
comparison is made between integer pixel rectangles, so the only
tolerance is one pixel of the delivery frame - the resolution of the
medium, which is mechanical (AGENTS.md 10.5).

**It declines rather than guessing.**  Resolve's ``CropLeft`` and friends
are read back as ``0.0`` on every item in the project, so their units are
unverified here.  A non-zero crop makes this module refuse to grade that
item (``crop_unread``) instead of assuming pixels.  AGENTS.md 5: judge a
Resolve call by what it RETURNS.

How the arithmetic is known to be right
---------------------------------------
Not from this module's own confidence.  ``framing_intent.py`` records an
INDEPENDENT measurement, taken before this module existed, of a real
render on project 001: ``render_qa``'s occupancy pass found the A-roll
picture occupying rows 656..1263 of a 1920-row frame - 608 rows, 31.7%.
The formula here predicts 607.5 rows for 16:9 fitted into 9:16.  It
reproduces a measured render to within one pixel row.

The reader is ``reel_conformance_verifier`` (F12).

``tests/test_reel_framing.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from library.tools.resolve_transform import (
    FALLBACK_DRAW_GAIN,
    drawn_centre,
    fit_base_scale,
)

from library.tools.framing_intent import (
    FILL,
    LETTERBOX,
    delivered_framing_intent,
    source_covers_frame,
)

# Resolve's identity transform.  These are not defaults this module
# chooses: they are what the API returns for an item nobody has touched,
# and `resolve_build_timeline._apply_conform` returns without setting
# anything when the conform is a letterbox, so "absent" and "identity"
# are the same state by the renderer's own reckoning.
IDENTITY = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
            "CropLeft": 0.0, "CropRight": 0.0,
            "CropTop": 0.0, "CropBottom": 0.0}

CROP_PROPS = ("CropLeft", "CropRight", "CropTop", "CropBottom")

# One pixel of the delivery frame.  The medium's own resolution, not a
# tolerance for "close enough" framing: two rectangles that round to the
# same integer pixel are the same picture, and two real numbers rounded
# by different rules can land a pixel apart.
PIXEL = 1


class ReelFramingError(ValueError):
    """A framing reading was asked for with something it cannot read."""


@dataclass(frozen=True)
class DeliveredPicture:
    """The rectangle one clip puts on the delivery frame.

    ``left``/``top``/``right``/``bottom`` are delivery-frame pixels and
    may fall outside the frame: that is what a fill crop looks like from
    this side, and clamping them would lose the difference between "fills
    exactly" and "fills and overflows".
    """

    source_width: int
    source_height: int
    frame_width: int
    frame_height: int
    left: int
    top: int
    right: int
    bottom: int
    framing_intent: float
    """Where between LETTERBOX and FILL this clip actually landed."""
    crop_factor: float
    """Zoom BEYOND fill, 1.0 when there is none."""
    stretched: bool
    """ZoomX and ZoomY disagree, so the picture's aspect is not the
    source's.  Exact, and always wrong: nothing in this engine asks for
    a stretch."""
    crop_unread: bool = False
    """A non-zero Resolve crop was present.  This reading is REFUSED -
    see the module docstring."""

    @property
    def covered_fraction(self) -> float:
        """How much of the delivery frame carries picture, 0.0 to 1.0."""
        area = self.frame_width * self.frame_height
        if area <= 0:
            return 0.0
        width = max(0, min(self.right, self.frame_width) - max(self.left, 0))
        height = max(0, min(self.bottom, self.frame_height) - max(self.top, 0))
        return (width * height) / area

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    def as_dict(self) -> dict:
        return {
            "source": [self.source_width, self.source_height],
            "frame": [self.frame_width, self.frame_height],
            "rect": list(self.rect),
            "covered_fraction": round(self.covered_fraction, 4),
            "framing_intent": round(self.framing_intent, 4),
            "crop_factor": round(self.crop_factor, 4),
            "stretched": self.stretched,
            "crop_unread": self.crop_unread,
        }


def display_size(width, height, rotation=0) -> Tuple[int, int]:
    """A source's DISPLAY dimensions - axes swapped for a rotated clip.

    The same rule ``compile_manifest._conform_fields`` applies, and for
    the same reason: an iPhone portrait MOV is stored 1920x1080 and shown
    1080x1920, and every scale below is computed on what is SHOWN.
    """
    if not width or not height:
        raise ReelFramingError(
            "a source with no width or height cannot be framed; the "
            "catalog is where those come from (step 1.02).")
    if abs(int(rotation or 0)) in (90, 270):
        return int(height), int(width)
    return int(width), int(height)


def max_zoom(source_width: int, source_height: int,
             frame_width: int, frame_height: int) -> float:
    """The zoom at which the source stops leaving bars.

    ``fill_scale / fit_scale`` - the same ratio ``_conform_fields`` calls
    ``max_zoom`` and maps the framing intent across.  1.0 when the source
    already covers the frame.
    """
    _assert_positive(source_width, source_height, frame_width, frame_height)
    fit = min(frame_width / source_width, frame_height / source_height)
    fill = max(frame_width / source_width, frame_height / source_height)
    return fill / fit


def _assert_positive(*values) -> None:
    for value in values:
        if not value or value <= 0:
            raise ReelFramingError(
                f"a framing reading needs positive dimensions; got {values!r}.")


def _zoom_to_intent(zoom: float, ceiling: float) -> Tuple[float, float]:
    """``(framing_intent, crop_factor)`` for a zoom, inverting the conform.

    ``compile_manifest._conform_fields`` writes
    ``zoom = 1 + (max_zoom - 1) * intent`` and then multiplies by a crop
    factor when one is declared.  This reads it back the same way round:
    everything up to ``max_zoom`` is intent, everything past it is crop.
    """
    if ceiling <= 1.0:
        # The source covers the frame already, so no zoom expresses an
        # intent - every intent delivers the same picture.
        return FILL, max(1.0, zoom)
    if zoom >= ceiling:
        return FILL, zoom / ceiling
    intent = (zoom - 1.0) / (ceiling - 1.0)
    return max(LETTERBOX, min(FILL, intent)), 1.0


def delivered_picture(source_width, source_height,
                      frame_width: int, frame_height: int,
                      transform: Optional[dict] = None,
                      rotation: int = 0,
                      draw_gain: float = FALLBACK_DRAW_GAIN
                      ) -> DeliveredPicture:
    """What one placed clip actually shows, from its own Resolve transform.

    ``transform`` is ``TimelineItem.GetProperty()`` verbatim.  ``None`` or
    ``{}`` means the item was never touched, which is Resolve's identity
    transform and - by ``_apply_conform``'s own early return - the
    renderer's way of spelling a letterbox.
    """
    source_width, source_height = display_size(source_width, source_height,
                                               rotation)
    _assert_positive(frame_width, frame_height)
    props = dict(IDENTITY)
    props.update(transform or {})

    crop_unread = any(float(props.get(name) or 0.0) != 0.0
                      for name in CROP_PROPS)

    zoom_x = float(props.get("ZoomX") or 1.0)
    zoom_y = float(props.get("ZoomY") or 1.0)
    pan = float(props.get("Pan") or 0.0)
    tilt = float(props.get("Tilt") or 0.0)

    fit = fit_base_scale(source_width, source_height,
                         frame_width, frame_height)
    shown_w = source_width * fit * zoom_x
    shown_h = source_height * fit * zoom_y

    # Pan/Tilt are NOT frame pixels.  One unit moves the clip
    # `source_dim / frame_dim * fit` pixels times the measured draw
    # gain - the same law every overlay is placed by
    # (`library/tools/resolve_transform.py`).  On this project's
    # 3840x2160 into 1080x1920 that is 2.0 px on Pan and 0.6328 px on
    # Tilt under today's gain (1.0 / 0.3164 under the 2026-09-11
    # one), which is why reading Tilt as frame pixels was right on
    # one axis by coincidence and wrong by 3.16x on the other.  The
    # user zoom does NOT enter it: measured, Pan 100 moved the
    # picture 100px at zoom 1.0 and at 2.307 alike (under that gain).
    # `draw_gain` pins which calibration reads a stored transform:
    # production timelines read today's, tests pinning the older one
    # pass 1.0 explicitly.
    centre_x, centre_y = drawn_centre(
        source_width, source_height, frame_width, frame_height,
        pan, tilt, fit, draw_gain)
    left = int(round(centre_x - shown_w / 2.0))
    top = int(round(centre_y - shown_h / 2.0))

    ceiling = max_zoom(source_width, source_height, frame_width, frame_height)
    intent, crop_factor = _zoom_to_intent(max(zoom_x, zoom_y), ceiling)

    return DeliveredPicture(
        source_width=source_width, source_height=source_height,
        frame_width=int(frame_width), frame_height=int(frame_height),
        left=left, top=top,
        right=int(round(left + shown_w)), bottom=int(round(top + shown_h)),
        framing_intent=intent, crop_factor=crop_factor,
        stretched=abs(zoom_x - zoom_y) > 1e-9,
        crop_unread=crop_unread)


def declared_picture(source_width, source_height,
                     frame_width: int, frame_height: int,
                     declared_intent: float,
                     crop_factor: float = 1.0,
                     rotation: int = 0) -> DeliveredPicture:
    """The rectangle a DECLARATION asks for, in the same units.

    Built by running ``_conform_fields``' own formula forwards, so a
    disagreement between this and :func:`delivered_picture` is a real
    difference in the picture rather than two spellings of one geometry.

    The declaration is passed through ``framing_intent`` first: a source
    that already covers the frame delivers FILL whatever anyone declared,
    which is what ``delivered_framing_intent`` exists to say.
    """
    source_width, source_height = display_size(source_width, source_height,
                                               rotation)
    covers = source_covers_frame(source_width, source_height,
                                 frame_width, frame_height)
    intent = delivered_framing_intent(declared_intent, covers)
    ceiling = max_zoom(source_width, source_height, frame_width, frame_height)
    zoom = 1.0 + (ceiling - 1.0) * intent
    if crop_factor and crop_factor > 1.0:
        zoom *= float(crop_factor)
    return delivered_picture(source_width, source_height,
                             frame_width, frame_height,
                             {"ZoomX": zoom, "ZoomY": zoom})


def disagreement(delivered: DeliveredPicture,
                 declared: DeliveredPicture) -> Optional[str]:
    """Why the delivered picture is not the declared one, or None.

    The comparison is between integer pixel rectangles.  A picture that
    lands within :data:`PIXEL` of where the declaration puts it IS the
    declared picture: the two numbers are one real number rounded twice.
    """
    if delivered.crop_unread:
        return ("the item carries a non-zero Resolve crop, whose units "
                "this module has never seen a non-zero value for and "
                "will not assume. Nothing is graded for it.")
    if delivered.stretched:
        return (f"ZoomX and ZoomY disagree, so the picture is stretched: "
                f"{delivered.source_width}x{delivered.source_height} is "
                f"drawn into {delivered.right - delivered.left}x"
                f"{delivered.bottom - delivered.top}.")
    if all(abs(a - b) <= PIXEL
           for a, b in zip(delivered.rect, declared.rect)):
        return None
    return (
        f"the picture fills {delivered.covered_fraction * 100:.2f}% of the "
        f"frame (framing_intent {delivered.framing_intent:.3f}) where the "
        f"project declares {declared.covered_fraction * 100:.2f}% "
        f"(framing_intent {declared.framing_intent:.3f}). Delivered rect "
        f"{delivered.rect}, declared {declared.rect} in a "
        f"{delivered.frame_width}x{delivered.frame_height} frame.")
