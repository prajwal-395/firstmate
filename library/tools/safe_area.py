"""The band the platform paints its own interface over. One enumeration.

Captain's ruling of 2026-08-25: **one master render serves Instagram
Reels, TikTok and YouTube Shorts, and it obeys the STRICTEST safe area
among them.**  That ruling settled the value.  Where the value LIVES was
deferred to the pipeline audit, whose answer is this file, and whose
instruction was explicit: *"Do not implement three more hardcoded
margins."*

There were three, and a fourth place that needed the same number and had
nothing:

* ``SubtitleOverlay/index.tsx`` positioned captions at ``bottom: 200px``
  - 10.4% of a 1920-row frame, inside the band TikTok paints its caption,
  handle and audio bar over.
* ``MotionGraphics/index.tsx`` drew its corner accents at 60px from every
  edge - 5.6% horizontally, well inside the like/comment/share rail.
* ``timed_text_overlay`` documented its ``y`` as normalised against the
  whole delivery frame *"because there is no picture-area or safe-area
  enumeration to resolve against"*.  Now there is.
* ``plan_subtitles.split_into_groups`` grouped captions by a literal
  ``max_chars = 18``, which is a character count with no relation to
  pixels.  The horizontal inset here is the number that grouper needs; if
  the safe area lived only on the render side, captions would be moved up
  and still be clipped left and right.

Where the numbers come from
---------------------------
The vertical profile is the published short-form safe-area map for
1080x1920, recorded in the motion-graphics research report (§2.5, from
``iart-ai/tiktok-video-skills``' ``short-form-video`` skill):

===========================  ==========================================
Zone                         Keep clear
===========================  ==========================================
Universal safe box           centre 900x1400
Top                          ~120px (profile / sound UI)
Bottom                       **~320px** (captions, CTA, hashtags, audio bar)
Right                        ~120px (like / comment / share rail)
===========================  ==========================================

Taken per edge, strictest wins: top 120px, bottom 320px, right 120px, and
left 90px - the left edge is constrained only by the 900px-wide universal
safe box, ``(1080 - 900) / 2``.  The asymmetry is real and is kept rather
than averaged away, because the right rail genuinely is the wider
obstruction.  Content that is CENTRED cannot use it: a centred box can
only be as wide as twice the distance to the nearer edge, which is what
:attr:`SafeAreaInsets.centered_usable_width` returns.

Insets are stored as FRACTIONS of the frame, not pixels, and resolved
against whatever ``(width, height)`` the caller actually renders at.  The
platform's UI is a fraction of the screen, so the 4K vertical format gets
the same profile without a second row of numbers, and a small test
fixture gets a proportionate answer instead of a raise.

Square and horizontal are not short-form platform frames - a feed post
and a 16:9 long-form master have no caption bar and no interaction rail -
so the vertical map does not apply to them and is not pretended to.  They
get the broadcast title-safe convention of 5% on every edge, which is
what a frame with no measured UI map has always been given.

An unknown delivery format RAISES, like every other enumeration in this
tree.  A silent fallback here is a caption under the platform's own UI
with nothing to notice.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from library.tools.delivery_format import (
    DEFAULT_DELIVERY_FORMAT,
    DELIVERY_FORMATS,
    delivery_format_name,
    resolve_format_name,
)


@dataclass(frozen=True)
class SafeAreaProfile:
    """One platform's keep-clear band, as fractions of the frame.

    ``top``/``bottom`` are fractions of frame HEIGHT; ``left``/``right``
    are fractions of frame WIDTH.
    """

    name: str
    top: float
    right: float
    bottom: float
    left: float
    derived_from: str

    def insets(self, width: int, height: int) -> "SafeAreaInsets":
        """The profile in pixels, for a frame of this size."""
        if width <= 0 or height <= 0:
            raise ValueError(
                f"safe area needs a real frame, got {width}x{height}")
        return SafeAreaInsets(
            top=int(round(self.top * height)),
            right=int(round(self.right * width)),
            bottom=int(round(self.bottom * height)),
            left=int(round(self.left * width)),
            width=int(width),
            height=int(height),
            profile=self.name,
        )


@dataclass(frozen=True)
class SafeAreaInsets:
    """A profile resolved against one frame. Every number is a pixel."""

    top: int
    right: int
    bottom: int
    left: int
    width: int
    height: int
    profile: str

    @property
    def usable_width(self) -> int:
        """Width available to content that may sit anywhere across it."""
        return self.width - self.left - self.right

    @property
    def usable_height(self) -> int:
        return self.height - self.top - self.bottom

    @property
    def centered_usable_width(self) -> int:
        """Width available to a box centred in the frame.

        A centred box grows equally in both directions, so it runs into
        the NEARER edge first and can only be twice that distance wide.
        Captions are centred; so are the timed text cards.
        """
        return self.width - 2 * max(self.left, self.right)

    def as_props(self) -> Dict[str, int]:
        """The four pixel insets, for serialising into Remotion props."""
        return {
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "left": self.left,
        }

    def contains_normalised(self, x: float, y: float) -> bool:
        """Whether a 0-1 normalised point lands inside the safe area."""
        px, py = x * self.width, y * self.height
        return (self.left <= px <= self.width - self.right
                and self.top <= py <= self.height - self.bottom)


# The short-form platform map. One profile, two vertical formats: 4K
# vertical is the same product in more pixels, and the fractions carry it.
_SHORTFORM_VERTICAL = SafeAreaProfile(
    name="shortform_vertical",
    top=120 / 1920,      # 0.0625  - profile / sound UI
    right=120 / 1080,    # 0.1111  - like / comment / share rail
    bottom=320 / 1920,   # 0.1667  - caption, CTA, hashtags, audio bar
    left=90 / 1080,      # 0.0833  - the 900px universal safe box
    derived_from=(
        "The published 1080x1920 short-form safe-area map (top ~120px, "
        "bottom ~320px, right ~120px, universal safe box 900x1400), "
        "recorded in the motion-graphics research report section 2.5 from "
        "iart-ai/tiktok-video-skills' short-form-video skill. Strictest "
        "per edge, per the captain's ruling of 2026-08-25 that one master "
        "serves Reels, TikTok and Shorts."
    ),
)

# A frame with no platform UI over it. Not a guess and not a default:
# it is the broadcast title-safe convention, and it is what a format
# gets when nobody has measured a UI map for it.
_TITLE_SAFE = SafeAreaProfile(
    name="title_safe",
    top=0.05,
    right=0.05,
    bottom=0.05,
    left=0.05,
    derived_from=(
        "The 5% title-safe margin broadcast has used since analogue "
        "overscan. Applied to the formats the short-form UI map does not "
        "describe - a square feed post and a 16:9 long-form master carry "
        "no caption bar and no interaction rail."
    ),
)

# Keyed by the names in library/tools/delivery_format.py. A format added
# there must be added here, or resolving its safe area raises.
SAFE_AREAS: Dict[str, SafeAreaProfile] = {
    "vertical_1080x1920": _SHORTFORM_VERTICAL,
    "vertical_2160x3840": _SHORTFORM_VERTICAL,
    "square_1080x1080": _TITLE_SAFE,
    "horizontal_1920x1080": _TITLE_SAFE,
}


class UnknownSafeArea(KeyError):
    """A delivery format exists with no safe-area profile beside it.

    Raised rather than defaulted. Guessing an inset for an unmeasured
    frame is how three different margins came to exist in the first
    place.
    """


def profile_names() -> List[str]:
    """Every delivery format that has a safe-area profile."""
    return sorted(SAFE_AREAS)


def safe_area_profile(format_name: str) -> SafeAreaProfile:
    """The profile for a delivery format name, or a raise naming it."""
    key = (format_name or DEFAULT_DELIVERY_FORMAT).strip()
    try:
        return SAFE_AREAS[key]
    except KeyError:
        raise UnknownSafeArea(
            f"No safe area declared for delivery format {format_name!r}. "
            f"Formats with one: {profile_names()}. Add a row to "
            f"library/tools/safe_area.py - and say where its numbers came "
            f"from, because an invented inset is a caption under the "
            f"platform's own UI with nothing to notice."
        ) from None


def safe_area_for_format(format_name: str) -> SafeAreaInsets:
    """Pixel insets for a named delivery format, at that format's size."""
    width, height = resolve_format_name(format_name)
    return safe_area_profile(format_name).insets(width, height)


def safe_area_for_frame(width: int, height: int) -> SafeAreaInsets:
    """The profile of the delivery format that IS this frame size.

    For a consumer holding pixels and no declaration - the render QA
    measures a finished master and has the frame in front of it.  An
    unknown size RAISES rather than borrowing the nearest profile: an
    invented inset is the defect this module exists to stop.
    """
    for name, (fmt_w, fmt_h) in DELIVERY_FORMATS.items():
        if (fmt_w, fmt_h) == (int(width), int(height)):
            return safe_area_profile(name).insets(width, height)
    raise UnknownSafeArea(
        f"No delivery format is {width}x{height}, so no safe area "
        f"describes it. Known formats: "
        f"{ {n: s for n, s in DELIVERY_FORMATS.items()} }."
    )


def resolve_safe_area(project_folder: Optional[str] = None,
                      templates_dir: Optional[str] = None,
                      width: Optional[int] = None,
                      height: Optional[int] = None) -> SafeAreaInsets:
    """The safe area one project renders under. The call every consumer makes.

    Mirrors ``delivery_format.resolve_delivery_format``: a function of the
    project rather than a value threaded through the DAG, for the same
    reason - a value in flight gets renamed, defaulted and lost.

    ``width``/``height`` override the format's own size for a caller that
    renders at some other scale (a test fixture, a proxy). The PROFILE
    still comes from the declared format; only the frame it is measured
    against changes.
    """
    name = delivery_format_name(project_folder, templates_dir=templates_dir)
    profile = safe_area_profile(name)
    if width is None or height is None:
        fmt_w, fmt_h = resolve_format_name(name)
        width = fmt_w if width is None else width
        height = fmt_h if height is None else height
    return profile.insets(width, height)
