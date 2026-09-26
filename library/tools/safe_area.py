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
The vertical profile is DERIVED, never typed. The apps' own UI is
measured on the captain's phone and laid out on every modelled phone
(``platform_safe_zones``); the project's safe-zone POLICY picks which
platforms and phones it is made for and adds its own rules
(``safe_zone_policy``, declared at ``pipeline.safe_zones``); and the
four insets are that layout's zones, each counted against the edge it
hugs (``SafeLayout.insets``). Under the default policy - every platform
on every phone, the captain's 2026-08-25 ruling that one master serves
them all - that is top 277 (TikTok's tabs under a short Android status
bar), right 212 (LinkedIn's action rail), bottom 334 (TikTok's caption
block) and left 118 (LinkedIn's side crop on a 21:9 phone).

The published map this replaced (top ~120, bottom ~320, right ~120, a
900px universal safe box; the motion-graphics research report section
2.5, from ``iart-ai/tiktok-video-skills``' ``short-form-video`` skill)
put the longest caption lines under the apps' action rails (the
captain, 2026-09-25). A consumer that can use a SPAN rather than an
inset - the rails are a notch, not an edge - asks the layout directly
(``safe_zone_policy.project_layout``).

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


Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**Overlay geometry comes from `library/tools/safe_area.py`, and captions are grouped by measured pixels.**
One enumeration keyed by delivery format, insets stored as FRACTIONS so a 4K vertical or a small test frame needs no second row; an unknown format raises.
Four consumers read it: `subtitle_style.SubtitleStyle.resolve` (the `safeArea`/`captionMaxWidth` props), `generate_motion_props`, `timed_text_overlay` (which refuses a card centred in the platform's UI band) and `plan_subtitles`' grouper.
**Every element `MotionGraphics/index.tsx` draws is positioned from the insets, the progress bar included** - never `bottom: 0`, `width: 100%`, which on a 1080x1920 delivery sits 320px inside the caption and audio-bar band and is covered by the platform's own interface. [why - the profile, and the grouper that never ran](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)
- The subtitle style is resolved at the top of `generate_subtitles` and there is no blind path: `split_into_groups` raises without a `fits_fn`.

**Every element is positioned from the insets** - never `bottom: 0`, `width: 100%`. [why](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)
- **A card fits the BOX, not one line.** The overlay wraps (`flexWrap`), so `fits_in_box`/`MAX_CAPTION_LINES` is the test; grouping against one line halves the words on every card and therefore halves how long each is on screen. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)

**Reconstruct a grouping with `fits_in_box`, never `fits`.** [why](docs/RULE_EVIDENCE.md#the-caption-grouping-reconstruction-used-the-wrong-predicate)
- **The split is BALANCED, not greedy.** A greedy fill leaves the remainder as a runt card, and a card is on screen only until the NEXT card's first word, so nothing downstream can lengthen one. `split_into_groups` solves per block for the partition with the fewest cards under the floor. Model the REAL display duration if you touch it.
- A card with one over-wide word carries `fit_scale` and the render draws THAT CARD smaller; the style's font size is untouched.
- The Remotion studio's `defaultProps` get the insets from `src/safeArea.generated.ts`, projected out of the enumeration by `scripts/generate_safe_area_defaults.py`.
- `tests/test_caption_safe_area.py`.
"""

from dataclasses import dataclass

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
            top=round(self.top * height),
            right=round(self.right * width),
            bottom=round(self.bottom * height),
            left=round(self.left * width),
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

    def as_props(self) -> dict[str, int]:
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
#
# DERIVED, never typed: the insets are the project's safe-zone policy
# (`safe_zone_policy`) resolved over the platform zones MEASURED on the
# captain's phone and laid out on every modelled phone
# (`platform_safe_zones`) - each app element counted against the edge it
# hugs. The published map this replaced (top 120, bottom 320, right 120,
# left 90; `iart-ai/tiktok-video-skills`' short-form-video skill) put the
# captions under the apps' action rails: the rails start 212px in, not
# 120 (the captain, 2026-09-25).
SHORTFORM_VERTICAL = "shortform_vertical"


def _shortform_profile(policy=None) -> "SafeAreaProfile":
    from library.tools.safe_zone_policy import resolve_layout

    layout = resolve_layout(policy)
    width, height = layout.frame
    insets = layout.insets()
    return SafeAreaProfile(
        name=SHORTFORM_VERTICAL,
        top=insets["top"] / height,
        right=insets["right"] / width,
        bottom=insets["bottom"] / height,
        left=insets["left"] / width,
        derived_from=(
            "platform_safe_zones: every app's UI measured on the "
            "captain's phone and laid out on "
            f"{', '.join(layout.policy.platforms)} x "
            f"{len(layout.policy.devices)} phone(s), each element counted "
            "against the edge it hugs (safe_zone_policy.SafeLayout."
            "insets)" + (" under the project's declared policy"
                         if layout.policy.declared else "")),
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
SAFE_AREAS: dict[str, object] = {
    "vertical_1080x1920": SHORTFORM_VERTICAL,
    "vertical_2160x3840": SHORTFORM_VERTICAL,
    "square_1080x1080": _TITLE_SAFE,
    "horizontal_1920x1080": _TITLE_SAFE,
}


class UnknownSafeArea(KeyError):
    """A delivery format exists with no safe-area profile beside it.

    Raised rather than defaulted. Guessing an inset for an unmeasured
    frame is how three different margins came to exist in the first
    place.
    """


def profile_names() -> list[str]:
    """Every delivery format that has a safe-area profile."""
    return sorted(SAFE_AREAS)


def safe_area_profile(format_name: str,
                      project_folder: str | None = None
                      ) -> SafeAreaProfile:
    """The profile for a delivery format name, or a raise naming it.

    A vertical format's profile is derived from ``project_folder``'s
    safe-zone policy (``safe_zone_policy``), or from the default policy
    - every platform on every modelled phone - where none is given.
    """
    key = (format_name or DEFAULT_DELIVERY_FORMAT).strip()
    try:
        entry = SAFE_AREAS[key]
    except KeyError:
        entry = None
    if entry == SHORTFORM_VERTICAL:
        from library.tools.safe_zone_policy import project_policy

        return _shortform_profile(project_policy(project_folder))
    if entry is not None:
        return entry
    raise UnknownSafeArea(
        f"No safe area declared for delivery format {format_name!r}. "
        f"Formats with one: {profile_names()}. Add a row to "
        f"library/tools/safe_area.py - and say where its numbers came "
        f"from, because an invented inset is a caption under the "
        f"platform's own UI with nothing to notice.")


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


def resolve_safe_area(project_folder: str | None = None,
                      templates_dir: str | None = None,
                      width: int | None = None,
                      height: int | None = None) -> SafeAreaInsets:
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
    profile = safe_area_profile(name, project_folder)
    if width is None or height is None:
        fmt_w, fmt_h = resolve_format_name(name)
        width = fmt_w if width is None else width
        height = fmt_h if height is None else height
    return profile.insets(width, height)
