"""The safe-frame check: the reframed subject clears the platform's UI. One enumeration.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation - social crops and reframing, platform variants. The reframe
follows the face track, but a tracked face can still land behind the
app's own interface: the right-rail icons, the side strips, the caption
band. This module checks a reframe crop window against the platform's
MEASURED occlusion zones, so a crop that tracked the subject yet parked
it under UI fails before it renders.

What it checks, and what it refuses to
-------------------------------------
The subject track carries a horizontal centre and a width, and no
vertical term - so this check is horizontal only, and says so. A
subject span intersecting a FULL-HEIGHT zone (side strips, icon rails)
is occluded whatever the vertical position, and fails. The top and
bottom bands cannot be judged without a vertical subject position, so
they are reported as unverifiable, never passed and never failed: a
check that guessed the face height would pass crops it cannot see.

The zones are the platform's measured rectangles on the 1080x1920
reference frame (``platform_safe_zones.PLATFORMS``). An unknown
platform name RAISES - a silent fallback is how a crop ships checked
against the wrong app's chrome. The subject box comes from
``subject_framing.subject_box``; a missing box means the footage does
not support an answer, and the verdict says unchecked rather than
clear.

What this module does not do
----------------------------
It does not move the crop, render, or master anything. It does not
judge the vertical bands, and it does not invent a vertical subject
position to judge them with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from library.tools.platform_safe_zones import (
    PLATFORMS,
    REFERENCE_SIZE,
    Zone,
)
from library.tools.reframe import CropWindow


@dataclass(frozen=True)
class ZoneIntrusion:
    """One occlusion zone the subject span overlaps, and by how much."""

    zone: str
    ui: str
    overlap_px: float


@dataclass(frozen=True)
class SafeFrameVerdict:
    """Whether the reframed subject clears the platform's UI.

    ``checked`` is False when there was no subject box to check -
    unchecked is not clear, and reads as such. ``clear`` is None then;
    otherwise True exactly when no full-height zone is intruded.
    ``vertical`` names what this check cannot judge: the top/bottom
    bands, unverifiable without a vertical subject track.
    """

    platform: str
    checked: bool
    clear: Optional[bool]
    intrusions: Tuple[ZoneIntrusion, ...]
    vertical: str


def platform_zones(platform: str) -> Tuple[Zone, ...]:
    """The measured occlusion zones for a named platform.

    Unknown names RAISE, naming the platforms a crop may be checked
    against - checked against the wrong app's chrome is how a subject
    ships behind UI nobody looked for.
    """
    key = (platform or "").strip()
    if key not in PLATFORMS:
        raise ValueError(
            f"Unknown platform {platform!r}. "
            f"Known platforms: {sorted(PLATFORMS)}. "
            "Zones are measured per app; there is no default app."
        )
    return PLATFORMS[key].zones


def full_height_zones(zones: Sequence[Zone]) -> List[Zone]:
    """The zones occlusion holds for whatever the vertical position.

    A zone spanning the reference frame's full height occludes any
    subject whose horizontal span touches it - side strips and icon
    rails. Derived from the rects, never from zone names: a renamed
    zone keeps its geometry.
    """
    _, ref_h = REFERENCE_SIZE
    return [z for z in zones
            if len(z.rect) == 4 and z.rect[1] <= 0 and z.rect[3] >= ref_h]


def full_width_zones(zones: Sequence[Zone]) -> List[Zone]:
    """The zones only a vertical subject position could judge.

    A zone spanning the reference frame's full width - the top cutout
    band, the bottom caption band - occludes only at the subject's
    height, which the subject track does not carry. Named in the
    verdict so the unverifiable stays visible.
    """
    ref_w, _ = REFERENCE_SIZE
    return [z for z in zones
            if len(z.rect) == 4 and z.rect[0] <= 0 and z.rect[2] >= ref_w]


def delivered_span(window: CropWindow, center_x: float, width: float,
                   source_width: float) -> Tuple[float, float]:
    """The subject's horizontal span in delivered reference pixels.

    ``center_x``/``width`` are source-width fractions (the subject
    track's units); the window maps them onto the 1080-wide reference
    frame the zones are measured on. Unclipped: a span outside the
    frame is the reframe's failure, not an occlusion, and clipping
    would hide it.
    """
    ref_w, _ = REFERENCE_SIZE
    scale = ref_w / window.w if window.w > 0 else 0.0
    left = (center_x - width / 2.0) * source_width
    right = (center_x + width / 2.0) * source_width
    return ((left - window.x) * scale, (right - window.x) * scale)


def check_subject_clear(window: CropWindow, center_x: Optional[float],
                        width: Optional[float], source_width: float,
                        platform: str) -> SafeFrameVerdict:
    """Check one crop window's subject against the platform's UI.

    ``center_x``/``width`` are the subject box (None where the footage
    supports no answer). Returns the verdict; raises only on an unknown
    platform or a non-positive source width.
    """
    zones = platform_zones(platform)
    if source_width <= 0:
        raise ValueError(
            f"source_width must be positive, got {source_width}")
    if center_x is None or width is None:
        return SafeFrameVerdict(
            platform=platform.strip(), checked=False, clear=None,
            intrusions=(),
            vertical=("no subject box - the footage supports no position, "
                      "so nothing was checked against the zones"),
        )
    span = delivered_span(window, center_x, width, source_width)
    intrusions = []
    for zone in full_height_zones(zones):
        overlap = (min(span[1], zone.rect[2], REFERENCE_SIZE[0])
                   - max(span[0], zone.rect[0], 0.0))
        if overlap > 0:
            intrusions.append(ZoneIntrusion(
                zone=zone.name, ui=zone.ui, overlap_px=round(overlap, 1)))
    band_names = sorted(z.name for z in full_width_zones(zones))
    return SafeFrameVerdict(
        platform=platform.strip(), checked=True,
        clear=not intrusions,
        intrusions=tuple(intrusions),
        vertical=("horizontal only: the subject track carries no vertical "
                  "term, so these bands are unverifiable: "
                  + (", ".join(band_names) if band_names else "none")),
    )
