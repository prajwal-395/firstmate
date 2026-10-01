"""What each DaVinci Resolve surface shows, and which of them ship.

A grade is judged by looking at something, and Resolve offers at least six
things to look at for one frame that do not agree.  `SURFACES` is the
whole roster of which of them is the picture that ships, and
`assert_measurable` is the guard that stops a lane measuring a preview.

THE ANSWER, IN ONE SENTENCE: the Fusion page and the Edit page hold the
SAME PIXELS and draw them through DIFFERENT VIEWERS, so the difference
the captain saw is a preview artefact and NOT something a grade change
can or should chase.

An export is BIT-EXACT on this machine and build: a re-render settles
it, and what survives is real.  (Moved from AGENTS.md 5 verbatim, where
the rule above keeps the index row.)

── The current contract ────────────────────────────────────────────────

**A grade is measured on an EXPORT, never on a viewer.**  The Edit, Color
and Deliver viewers draw the frame through a display colour transform
Resolve applies and nothing outside Resolve reproduces; the Fusion page
viewer sits UPSTREAM of the Color page (source -> Fusion -> Color ->
output), so it stops matching the file the moment the Color page does
anything.  Neither viewer is a measuring instrument.

**Sampling the Edit page viewer and grading until it looks right bakes
the display transform into the file**, which is why `assert_measurable`
REFUSES a viewer by name (`SurfaceNotMeasurable`) rather than warning.

**An export is BIT-EXACT here: a difference that survives a re-render is
real, not a floor.**  What that does and does NOT license:

* It licenses **byte equality as the bar** for "did this change the
  picture" - between two renders of the same state, or between a change
  and its control.  A 1/255 mean difference is a finding, not noise.
* It does NOT license reading across INSTRUMENTS.  A gallery still (PNG)
  and a Deliver render (h.264) of one frame differ by encoding, and a
  viewer is not a measuring instrument at all.  Bit-exactness is a
  property of repeating the SAME export.
* It is scoped to **this machine and this Resolve build**, in ONE
  session.  A lane that needs the stronger statement measures it again
  and writes down what it got - two renders and a comparison.
* A comp that was JUST edited or imported may render differently the
  first time and settle afterwards.  **Re-render and compare the settled
  pass**: a difference that does not survive a re-render is not evidence,
  and one that does is.

Every measurement behind these rules - the Fusion-output vs Deliver
luma table, both viewers against their pixels, the display-profile
preference, the surface-order probe, the 75-node bypass, and the 19-frame
bit-exactness run - is in docs/evidence/resolve_surfaces.md.
"""

from __future__ import annotations

from dataclasses import dataclass


class SurfaceNotMeasurable(RuntimeError):
    """A measurement was taken from a surface that does not ship."""


# ── The roster ──────────────────────────────────────────────────────

FUSION_PAGE_VIEWER = "fusion_page_viewer"
EDIT_PAGE_VIEWER = "edit_page_viewer"
COLOR_PAGE_VIEWER = "color_page_viewer"
GALLERY_STILL = "gallery_still"
DELIVER_RENDER = "deliver_render"
FUSION_SAVER = "fusion_saver"


@dataclass(frozen=True)
class SurfaceFact:
    """One thing in Resolve that shows a frame.

    `ships` is the whole point: True means pixels read from here are the
    pixels the delivered file carries, and a grade may be measured on
    them.  False means the surface adds something of its own, and
    `adds` says what.
    """

    shows: str
    ships: bool
    adds: str
    measured: str


SURFACES = {
    FUSION_PAGE_VIEWER: SurfaceFact(
        shows="the clip's Fusion comp output (MediaOut1), BEFORE the Color page",
        ships=False,
        adds="no colour management (EnableMacDisplayColorProfile does not "
             "reach it), so raw Rec.709 code values are pushed at a "
             "Display P3 panel; and it is upstream of the Color page, so it "
             "omits every grade node",
        measured="+1.3 to -5.2/255 against the file, saturation 64.95 vs 65.69",
    ),
    EDIT_PAGE_VIEWER: SurfaceFact(
        shows="the composited timeline frame, AFTER the Color page",
        ships=False,
        adds="Resolve's display colour management "
             "(EnableMacDisplayColorProfile = 1), which lifts midtones and "
             "slightly desaturates, and is not reproducible outside Resolve",
        measured="+6.2 to +9.9/255 in the midtones, saturation 46.41 vs 48.41",
    ),
    COLOR_PAGE_VIEWER: SurfaceFact(
        shows="the same frame as the Edit page viewer",
        ships=False,
        adds="the same display colour management as the Edit page viewer",
        measured="not measured separately; it takes the same viewer path",
    ),
    GALLERY_STILL: SurfaceFact(
        shows="the GRADED, CONFORMED timeline frame, whatever page is open",
        ships=True,
        adds="nothing",
        measured="mean absolute difference 1.61/255 against a Deliver render "
                 "of the same frame (h.264 quantisation) - "
                 "library/tools/marker_capture.py",
    ),
    DELIVER_RENDER: SurfaceFact(
        shows="the delivered file itself",
        ships=True,
        adds="nothing - this IS the product",
        measured="the reference every other row is measured against",
    ),
    FUSION_SAVER: SurfaceFact(
        shows="a comp's output written to disk by a temporary Saver node",
        ships=False,
        adds="nothing of its own, but it is the comp's output and so is "
             "upstream of the Color page in the same way the Fusion page "
             "viewer is - AND REACHING FOR IT COSTS WIRES: `AddTool` "
             "inserts the Saver into the live flow and deleting it does "
             "not put the wire back "
             "(library/steps/step_6_01_render/probe_resolve_capabilities.py)",
        measured="equal to the Deliver render within +/-0.64/255 per luma bin "
                 "on Reel 09, where the Color page measures as a no-op",
    ),
}
"""Every surface a lane has reached for, and what each one really shows.

A row exists because somebody looked at it, not because Resolve offers
it.  `FUSION_SAVER` is here because it is how the Fusion page's pixels
were obtained for the comparison above, and a later lane will reach for
it again."""


def assert_roster_is_well_formed() -> None:
    """Every row says what it shows, whether it ships, and what it adds."""
    for name, fact in SURFACES.items():
        if not fact.shows or not fact.measured:
            raise ValueError(f"{name} does not say what it shows or how it was measured")
        if fact.ships and fact.adds != "nothing" and not fact.adds.startswith("nothing"):
            raise ValueError(f"{name} claims to ship but also adds {fact.adds!r}")
        if not fact.ships and not fact.adds:
            raise ValueError(f"{name} does not ship and does not say what it adds")


def ships(surface: str) -> bool:
    """Whether pixels read from `surface` are the pixels that are delivered."""
    try:
        return SURFACES[surface].ships
    except KeyError:
        raise SurfaceNotMeasurable(
            f"{surface!r} is not a surface this repo has measured. The roster "
            f"is {sorted(SURFACES)}; add a row with its measurement rather "
            f"than measuring something nothing has characterised."
        ) from None


def assert_measurable(surface: str) -> None:
    """Refuse a measurement taken from a surface that does not ship.

    Raises `SurfaceNotMeasurable` naming what the surface adds, because a
    lane that reaches for a viewer needs to be told what it would have
    been measuring, not merely stopped.
    """
    if ships(surface):
        return
    fact = SURFACES[surface]
    raise SurfaceNotMeasurable(
        f"{surface} shows {fact.shows}, and it adds {fact.adds}. Measured: "
        f"{fact.measured}. Measure a grade on an EXPORT - "
        f"{DELIVER_RENDER} or {GALLERY_STILL} - never on a viewer."
    )
