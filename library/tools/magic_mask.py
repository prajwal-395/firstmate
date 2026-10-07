"""The Magic Mask recipe: Ren's deterministic route to a seeded mask.

The Resolve Hands plan (sections 4, 6-8) says a known UI route is a
recipe - a recorded page and step list the runner executes
deterministically - and a route nobody has run successfully is a guess,
not a recipe. This module is the recipe's home: it binds three things
no single existing module owns into one falsifiable step list built
from `computer_use.scripts`' anchor vocabulary:

- the Ren-side seed (`mask_seed.MaskSeed`): the subject's interior
  point, mapped to viewer coordinates from Ren's own measurements;
- the driver lane's measured routes (`docs/RESOLVE_CONTROL_PROBE.md`):
  the page switch goes through the AX menu bar
  (`Workspace > Switch to Page`, read back edit -> media 2026-10-05);
  the Magic Mask palette carries no AX node (Qt-drawn, zero matching
  menu commands) but IS OCR-readable, so the palette, the mode and the
  tracker are text anchors; background input reaches nothing Qt-drawn
  (measured 0 pixels changed), so the click steps need the foreground;
- the captain's plan for the label strings ("Magic Mask", "Person",
  "Track Forward"/"Track Backward", modes F/B/BI).

Every step records its anchor's provenance (`why`/`source`): `probe`
where the driver lane measured the route, `plan` where the string
comes from the captain's plan and no run has confirmed it yet. A live
run that confirms a plan-stated anchor promotes it; a live run that
refutes one fixes this file. The per-step expectations are the
falsifiable half: a background click that silently changes nothing
fails `pixels_changed` or the `text_visible` wait instead of
reporting ok.

What this module does NOT do, on purpose: park the playhead (the
caller supplies the timecode - a frame number converts through the
timeline's own frame rate, which this module never guesses), call
`CreateMagicMask` (a scripting-API call, not a UI action - the
caller runs it after the seed transaction commits and judges the
node gain), or select a clip (the scripting API exposes no
selection write; proof timelines carry one clip, and multi-clip
selection is a future recipe step, not a silent assumption here).
"""

from __future__ import annotations

from library.tools.computer_use.scripts import Anchor, Expect, Script, ScriptStep
from library.tools.mask_seed import MaskSeed

#: The API modes `TimelineItem.CreateMagicMask` takes - the same gate
#: `resolve-axi sense mask` enforces, so the recipe never asks the API
#: for a mode it refuses.
MAGIC_MASK_MODES = ("F", "B", "BI")

#: The tracker directions the palette offers, as the plan names them.
TRACK_DIRECTIONS = ("forward", "backward")

#: Palette label -> the OCR string the recipe clicks. Plan-stated until
#: a live run confirms each one; the recipe records that.
TRACK_LABELS = {"forward": "Track Forward", "backward": "Track Backward"}


def seed_script(
    seed: MaskSeed,
    *,
    mode: str = "BI",
    scale_factor: float,
) -> Script:
    """The four UI steps that seed a Magic Mask: page, palette, mode, click.

    `seed` is the Ren-determined seed - its viewer point is where the
    click lands, so a click anywhere else is a different subject. `mode`
    is carried for the caller's API call, and validated here so a
    recipe never names a mode the API refuses. `scale_factor` is the
    main window's measured backing scale (device pixels per point):
    `seed.viewer` is in device pixels - the space a screenshot is
    measured in - while a `position` anchor is an absolute window
    point, so the builder divides. The scale is REQUIRED: there is no
    safe default (1.0 silently double-clicks on Retina), and the
    caller measures it live off `get_windows`. Refuses a non-positive
    scale the same way it refuses a bad mode.
    """
    if mode not in MAGIC_MASK_MODES:
        raise ValueError(
            f"bad Magic Mask mode {mode!r}: one of {list(MAGIC_MASK_MODES)}")
    if not scale_factor or scale_factor <= 0:
        raise ValueError(
            f"bad backing scale {scale_factor!r}: the main window's "
            f"measured backing_scale, positive")
    click = {"x": seed.viewer.x / scale_factor,
             "y": seed.viewer.y / scale_factor}
    return Script(
        goal=(f"Seed the Magic Mask on {seed.subject!r} in clip "
              f"{seed.clip_id} frame {seed.seed_frame} "
              f"(object {seed.object_id}, mode {mode})"),
        steps=[
            ScriptStep(
                action="press",
                anchors=[Anchor(via="ax_path", params={
                    "path": ["Workspace", "Switch to Page", "Color"],
                    "source": "probe",
                })],
                expect=Expect(via="pixels_changed", params={
                    "source": "probe",
                }),
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={
                    "text": "Magic Mask",
                    "source": "plan",
                })],
                expect=Expect(via="text_visible", params={
                    "text": "Person",
                    "source": "plan",
                }),
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={
                    "text": "Person",
                    "source": "plan",
                })],
                expect=Expect(via="text_visible", params={
                    "text": "Person",
                    "source": "plan",
                }),
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(via="position", params={
                    "x": click["x"],
                    "y": click["y"],
                    "why": (f"ren seed {seed.object_id} "
                            f"sample {seed.sample_index}"),
                    "source": "ren",
                })],
                expect=Expect(via="pixels_changed", params={
                    "source": "ren",
                }),
            ),
        ],
    )


def track_script(direction: str) -> Script:
    """The one UI step that drives the palette tracker when the API fails.

    The plan's section 8 fallback: `CreateMagicMask` answered False, so
    press the UI Track Forward / Track Backward instead. The click is
    judged by the mask read-back that follows (slice 3), not here: the
    button persists whether tracking ran or not, so no pixel claim here
    could tell the two apart, and this module does not invent one.
    """
    if direction not in TRACK_DIRECTIONS:
        raise ValueError(
            f"bad track direction {direction!r}: "
            f"one of {list(TRACK_DIRECTIONS)}")
    label = TRACK_LABELS[direction]
    return Script(
        goal=f"Track the Magic Mask {direction} from the seeded frame",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={
                    "text": label,
                    "source": "plan",
                })],
            ),
        ],
    )
