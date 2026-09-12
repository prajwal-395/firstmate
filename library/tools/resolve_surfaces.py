"""What each DaVinci Resolve surface shows, and which of them ship.

The captain, 2026-09-10, holding a side-by-side of one frame: *"the way
the video looks in fusion is different from the way it shows up in the
timeline on the edit page... i think i know why previously when i told
you to sample the stills from davinci in order to color grade, you made
all the parameters way more than they needed to be"*.

A grade is judged by looking at something.  Resolve offers at least six
things to look at for one frame, they do not agree, and until this
module existed nothing in the repo said which of them is the picture
that ships.  That has now cost two lanes, so it is written down here
once: the roster below is the whole answer, and
`assert_measurable` is the guard that stops the next lane measuring a
preview.

THE ANSWER, IN ONE SENTENCE: the Fusion page and the Edit page hold the
SAME PIXELS and draw them through DIFFERENT VIEWERS, so the difference
the captain saw is a preview artefact and NOT something a grade change
can or should chase.

── What was measured, and how ──────────────────────────────────────────

DaVinci Resolve Studio 21.0.0b.28, macOS 26.3, 2026-09-10, on the
captain's own `Podcast (field test)` / `Reel 09 -
your-website-is-only-20-percent`, timeline frame 300 throughout.  Every
number below came from a file on disk, never from reading a viewer.

1. THE TWO SURFACES HOLD THE SAME PIXELS.  A temporary `Saver` was
   wired to whatever feeds `MediaOut1` in the A-roll clip's comp - that
   is exactly the image the Fusion page viewer draws - and rendered one
   frame; the same timeline frame was rendered through Deliver as a
   16-bit TIFF.  Aligned by the clip's own Edit-page geometry (fit scale
   0.28125 x Zoom 2.3070 = 0.64884, best x-offset found by search, MAE
   1.381/255 residual from the resampling the alignment itself needs):

       luma bin     Fusion output  ->  delivered      difference
       0-8              4.72             4.75          +0.03
       8-16            12.46            12.48          +0.03
       16-32           24.06            24.15          +0.09
       32-48           39.64            39.87          +0.23
       48-64           55.58            55.68          +0.11
       64-96           78.07            77.65          -0.42
       96-128         108.67           108.03          -0.64
       128-160        145.78           145.64          -0.14
       160-192        175.52           175.48          -0.04
       192-224        201.12           201.02          -0.11
       224-256        233.48           233.33          -0.15

   Percentiles agree to 0.1/255 (p1 3.43/3.43, med 43.27/43.20, p99
   186.48/186.54), mean saturation 66.13 against 66.37.  There is no
   tone curve between them.  THE EXPORT ALREADY CARRIES THE FUSION LOOK.

2. THE VIEWERS DIFFER, AND BY HOW MUCH.  One window
   (`screencapture -x -o -l<id>`, so both grabs come through one
   identical capture path), same frame, same clip:

       Edit page viewer against the pixels it is drawing
         midtones  +6.17, +8.28, +9.10, +9.92, +9.94, +8.14 /255
         blacks pinned, saturation 46.41 against the file's 48.41
       Fusion page viewer against the pixels it is drawing
         +1.26, +1.30, +2.14, +3.41, -0.69, -5.18, -3.80 /255
         saturation 64.95 against the file's 65.69

   The Edit page draws the frame with its midtones LIFTED and slightly
   desaturated - flatter, greyer, exactly what the captain reported.
   The Fusion page tracks the file (its residual is the heavy downscale
   its viewer applies: 3840x2160 drawn into 1084x610).

3. WHY.  Resolve's own preference file
   (`~/Library/Preferences/Blackmagic Design/DaVinci Resolve/config.dat`)
   carries `EnableMacDisplayColorProfile = 1` - "Use Mac Display Color
   Profiles for viewers".  The window composites in **Display P3**
   (read off the screenshot) and the delivered TIFF is tagged
   **Rec. 709** (read off the file).  The Edit/Color/Deliver viewers get
   that conversion applied; the Fusion page viewer does not.  Stated as
   the strength it has: the preference is ON and the measured direction
   (lift plus slight desaturation) is what a colour-management step
   between two different spaces does.  A plain ColorSync Rec.709 ->
   Display P3 of the same file does NOT reproduce Resolve's exact curve
   (it darkens by 14-19/255 instead), so the transform is Resolve's own
   and is not reproducible outside it - which is the whole reason a
   viewer cannot be the measuring instrument.

4. FUSION SITS UPSTREAM OF COLOR - verified, not read.  On a throwaway
   project (`fm_surface_order`, created and deleted by the probe), a
   comp whose `MediaOut1` was fed a solid RED `Background` rendered GREY
   through Deliver while Color page node 1 carried a `SetCDL`
   saturation 0 (R=G=B=16.77, mean sat 0.00), and RED again with that
   node bypassed (R 80.68, G 0.00, B 0.00, mean sat 80.75).  The chain
   is source -> Fusion -> Color -> output, so the Fusion page viewer is
   BEFORE the Color page and the Edit page is AFTER it.  Wherever the
   Color page does anything, those two viewers cannot agree even before
   the display transform above.

5. ON REEL 09 THE COLOR PAGE DOES NOTHING.  Bypassing all 75 nodes on
   every picture item (`GetNodeGraph().SetNodeEnabled(i, False)`, with
   the Color page open) moved the delivered frame by mean 0.000/255,
   p99 0, max 7.  The bypass mechanism itself was proved on the
   throwaway project above, so a null result here is a fact about the
   grade and not about the tool.  This is the same near-identity
   `color_page_grade.py` recorded for the captain's `.drx` on its own
   reference frame, now measured on the reel itself.

── The rule this buys ──────────────────────────────────────────────────

**A grade is measured on an EXPORT, never on a viewer.**  Both viewers
are wrong in different directions and neither is reproducible off this
machine.  Of the two, the Fusion page happens to match the file's
numbers - but that is a coincidence of it being unmanaged, not a licence
to trust it, and it stops being true the moment the Color page carries
anything (point 4).

**Sampling the Edit page viewer and grading until it looks right bakes
the display transform into the file.**  That is the mechanism behind the
captain's "way more than they needed to be", and it is why
`assert_measurable` refuses a viewer by name rather than warning.

── HOW EXACT THE INSTRUMENT IS: BIT-EXACT, AND THE SCOPE OF THAT ───────

**An export is BIT-EXACT here: a difference that survives a re-render is
real, not a floor.**

Measured 2026-09-12, DaVinci Resolve Studio 21.1.0.0014, macOS 26.3, on
this machine: one timeline, one 19-frame range, rendered twice through
Deliver as a lossless 16-bit PNG sequence (1080x1920, `rgb48`), the two
passes compared on decoded pixels.  **19 of 19 frames identical, zero
differing.**  So a measurement taken this way owes no allowance to the
renderer, and quoting one is a weaker claim than the instrument
supports.

What that does and does NOT license, stated separately because a rule
that overstates in the safe direction is no better than one that
overstates in the risky direction:

* It licenses **byte equality as the bar** for "did this change the
  picture" - between two renders of the same state, or between a change
  and its control.  A 1/255 mean difference is a finding, not noise.
* It does NOT license reading across INSTRUMENTS.  The rows above still
  hold: a gallery still sits 1.61/255 from a Deliver render of the same
  frame because one is a PNG and the other h.264, and a viewer is not a
  measuring instrument at all.  Bit-exactness is a property of repeating
  the SAME export, not of comparing two different ones.
* It is scoped to **this machine and this Resolve build**, in ONE
  session.  Whether it survives a Resolve restart, a GPU change or
  another machine was not measured and is not claimed.  A lane that
  needs the stronger statement measures it again and writes down what it
  got - which costs two renders and a comparison.
* A comp that was JUST edited or imported may render differently the
  first time and settle afterwards (a spike measured a sub-pixel edge
  map that vanished on a second pass).  That is a reason to **re-render
  and compare the settled pass**, not a reason to accept a floor: a
  difference that does not survive a re-render is not evidence, and one
  that does is.
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
