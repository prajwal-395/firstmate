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
  menu commands) and its toolbar button is icon-only (OCR-blind on the
  settled page, measured 2026-10-07 - the earlier OCR-readable claim
  was refuted live), so the button is a template anchor and the mode
  and the tracker are text anchors; background input reaches nothing
  Qt-drawn (measured 0 pixels changed), so the click steps need the
  foreground;
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

Every seed and track take names the item's inclusive source-frame trim.
The recipe defaults to that measured trim and refuses it above 120
frames, so a proof cannot silently analyze a full-length source.

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

from dataclasses import dataclass

from library.tools.computer_use.scripts import Anchor, Expect, Script, ScriptStep
from library.tools.computer_use.scripts import EFFECTS_PANEL_PATH
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

# The live tracker measured 3 fps on the captain's clip (2026-10-07).
# A 120-frame ceiling keeps a proof near 40 seconds at that measured
# rate; callers use an already-trimmed clip, and never silently track a
# full source. The captain asked that future proofs cover a few seconds
# and occupy Resolve for no more than a minute or two.
MAX_TRACK_RANGE_FRAMES = 120


@dataclass(frozen=True)
class SourceFrameRange:
    """An inclusive source-frame range carried by a Magic Mask take."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if (not isinstance(self.start, int) or isinstance(self.start, bool)
                or not isinstance(self.end, int) or isinstance(self.end, bool)
                or self.start < 0 or self.end < self.start):
            raise ValueError(
                f"bad source frame range {self.start!r}..{self.end!r}; "
                "expected non-negative inclusive bounds")

    @property
    def frame_count(self) -> int:
        return self.end - self.start + 1

    def label(self) -> str:
        return f"{self.start}..{self.end} inclusive ({self.frame_count} frames)"

    def to_receipt(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}


def parse_source_frame_range(value) -> SourceFrameRange | None:
    """Parse `START:END` or a receipt mapping; bounds are inclusive."""
    if value is None:
        return None
    if isinstance(value, SourceFrameRange):
        return value
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError(
                "--source-frame-range is START:END (inclusive)")
        try:
            start, end = (int(part) for part in parts)
        except ValueError as exc:
            raise ValueError(
                "--source-frame-range is START:END (inclusive integers)") from exc
    elif isinstance(value, dict):
        try:
            start, end = value["start"], value["end"]
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)):
                raise TypeError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"bad source frame range receipt {value!r}") from exc
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        start, end = value
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)):
            raise ValueError(f"bad source frame range {value!r}")
    else:
        raise ValueError(f"bad source frame range {value!r}")
    return SourceFrameRange(start, end)


def source_frame_range_for_item(
    item,
    requested=None,
    *,
    seed_frame: int | None = None,
) -> SourceFrameRange:
    """Resolve a bounded take from one already-trimmed timeline item.

    The default is the item's measured source in/out range. An explicit
    range must match that trim exactly, so a request cannot claim a
    shorter run while Resolve still tracks the full clip. Source out is
    inclusive in `TimelineItem.GetSourceEndFrame()`.
    """
    try:
        actual = SourceFrameRange(int(item.GetSourceStartFrame()),
                                  int(item.GetSourceEndFrame()))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "the target clip did not read back source in/out frames") from exc
    selected = parse_source_frame_range(requested) or actual
    if selected != actual:
        raise ValueError(
            f"requested source frames {selected.label()} do not match the "
            f"timeline item's trimmed range {actual.label()}; trim the "
            "qualification clip to that exact range first")
    if selected.frame_count > MAX_TRACK_RANGE_FRAMES:
        raise ValueError(
            f"source range {selected.label()} exceeds the "
            f"{MAX_TRACK_RANGE_FRAMES}-frame Magic Mask take limit; trim "
            "the qualification clip to a few seconds first")
    if seed_frame is not None and not selected.start <= int(seed_frame) <= selected.end:
        raise ValueError(
            f"seed frame {seed_frame} falls outside source range "
            f"{selected.label()}")
    return selected


def _mask_timeline_item(timeline):
    """The one picture item a deterministic Magic Mask recipe can address."""
    try:
        track_count = int(timeline.GetTrackCount("video"))
        items = [item
                 for index in range(1, track_count + 1)
                 for item in (timeline.GetItemListInTrack("video", index) or [])]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "the target timeline did not read its video items") from exc
    if len(items) != 1:
        raise ValueError(
            f"Magic Mask recipes need one picture item on the timeline; "
            f"read {len(items)}")
    return items[0]


def resolve_source_frame_range(timeline, requested=None, *, seed_frame=None):
    """Resolve and bound the sole picture item's source frame range."""
    item = _mask_timeline_item(timeline)
    return source_frame_range_for_item(
        item, requested, seed_frame=seed_frame)


def _require_bounded_range(source_frame_range: SourceFrameRange,
                           *, seed_frame: int | None = None
                           ) -> SourceFrameRange:
    if not isinstance(source_frame_range, SourceFrameRange):
        raise ValueError("Magic Mask recipes need a measured source frame range")
    if source_frame_range.frame_count > MAX_TRACK_RANGE_FRAMES:
        raise ValueError(
            f"source range {source_frame_range.label()} exceeds the "
            f"{MAX_TRACK_RANGE_FRAMES}-frame Magic Mask take limit")
    if (seed_frame is not None
            and not source_frame_range.start <= seed_frame <= source_frame_range.end):
        raise ValueError(
            f"seed frame {seed_frame} falls outside source range "
            f"{source_frame_range.label()}")
    return source_frame_range


def _color_page_step() -> ScriptStep:
    """The Color page switch shared by both seed routes."""
    return ScriptStep(
        action="press",
        anchors=[Anchor(via="ax_path", params={
            "path": ["Workspace", "Switch to Page", "Color"],
            "source": "probe",
        })],
        expect=Expect(via="pixels_changed", params={
            "source": "probe",
        }),
    )


def _seed_click_step(seed: MaskSeed, click: dict) -> ScriptStep:
    """The seed click itself: the Ren-measured interior point, judged
    by pixels changing. A viewer click with no mask tool behind it
    paints nothing, so the expect doubles as the palette-open proof
    on the warm route."""
    return ScriptStep(
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
    )


def _mode_and_seed_steps(seed: MaskSeed, mode: str, click: dict
                         ) -> list[ScriptStep]:
    """The Person mode select and the seed click shared by both seed
    routes - the mode expect (the Person label) is also the
    palette-open proof the warm route runs on."""
    return [
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
        _seed_click_step(seed, click),
    ]


def seed_script(
    seed: MaskSeed,
    *,
    mode: str = "BI",
    scale_factor: float,
    source_frame_range: SourceFrameRange,
) -> Script:
    """The seven UI steps that seed a Magic Mask: page, library, search,
    type, palette, mode, click.

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

    The library step opens the Effects Library through the shared
    menu route the resolvefx lane measured (`EFFECTS_PANEL_PATH` -
    background server-side press, no focus), because the palette row
    the next step clicks only exists with the Library open. That
    route was measured on the Edit page; its Color-page behavior is
    proved by this recipe's own pixels_changed expect on the next
    take, which fails the step instead of clicking on. Region-scoped
    expects follow when the expects land their window-point regions.
    """
    if mode not in MAGIC_MASK_MODES:
        raise ValueError(
            f"bad Magic Mask mode {mode!r}: one of {list(MAGIC_MASK_MODES)}")
    source_frame_range = _require_bounded_range(
        source_frame_range, seed_frame=seed.seed_frame)
    if not scale_factor or scale_factor <= 0:
        raise ValueError(
            f"bad backing scale {scale_factor!r}: the main window's "
            f"measured backing_scale, positive")
    click = {"x": seed.viewer.x / scale_factor,
             "y": seed.viewer.y / scale_factor}
    return Script(
        goal=(f"Seed the Magic Mask on {seed.subject!r} in clip "
              f"{seed.clip_id} frame {seed.seed_frame} "
              f"within source frames {source_frame_range.start}.."
              f"{source_frame_range.end} inclusive "
              f"(object {seed.object_id}, mode {mode})"),
        steps=[
            _color_page_step(),
            ScriptStep(
                action="menu",
                anchors=[Anchor(via="menu", params={
                    "path": list(EFFECTS_PANEL_PATH),
                    "source": "probe",
                })],
                expect=Expect(via="pixels_changed", params={
                    "source": "probe",
                }),
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(via="template", params={
                    "template": "magic-mask-search",
                    "settle_seconds": 90.0,
                    "settle_interval": 2.0,
                    "source": "ren",
                    "why": ("Library contents paint asynchronously "
                            "after the panel opens (measured: hit once, "
                            "miss minutes later on the same anchor) - "
                            "the poll returns on first hit"),
                })],
                expect=Expect(via="pixels_changed", params={
                    "source": "ren",
                }),
            ),
            ScriptStep(
                action="type",
                anchors=[Anchor(via="type", params={
                    "text": "Magic Mask",
                    "source": "plan",
                })],
                expect=Expect(via="text_visible", params={
                    "text": "Magic Mask",
                    "source": "plan",
                }),
            ),
            ScriptStep(
                action="click",
                anchors=[
                    Anchor(via="template", params={
                        "template": "magic-mask-toolbar",
                        "settle_seconds": 90.0,
                        "settle_interval": 2.0,
                        "source": "ren",
                        "why": ("icon-only toolbar button, OCR-blind "
                                "on the settled page (measured "
                                "2026-10-07); unmatchable for seconds "
                                "after a page switch, present at 90 s "
                                "(paint series) - the poll returns on "
                                "first hit, the bound bites only on "
                                "genuine absence"),
                    }),
                    Anchor(via="ocr", params={
                        "text": "Magic Mask",
                        "source": "plan",
                        "clicks": 2,
                    }),
                ],
                expect=Expect(via="text_visible", params={
                    "text": "Person",
                    "source": "plan",
                }),
            ),
            *_mode_and_seed_steps(seed, mode, click),
        ],
    )


def seed_open_palette_script(
    seed: MaskSeed,
    *,
    mode: str = "BI",
    scale_factor: float,
    source_frame_range: SourceFrameRange,
) -> Script:
    """Seed when the Magic Mask palette is already open with no mask.

    The take reads the palette state first (the `No Mask` panel with
    its draw prompt): open means this route, anything else means the
    full `seed_script`. A second builder, not a branch - the runner
    tries anchors, never conditions, so one recipe cannot serve two
    panel states. Running the full route here would re-apply the
    effect (a second instance on shared qual footage). There is no
    mode step: the open palette renders no Person control (measured
    on the live render - the panel prompts a point draw instead), so
    the route is page, click. Running this route cold fails the seed
    expect instead of painting blind, because a viewer click with no
    mask tool behind it changes nothing. Same validation contract
    as `seed_script`: no safe scale default.
    """
    if mode not in MAGIC_MASK_MODES:
        raise ValueError(
            f"bad Magic Mask mode {mode!r}: one of {list(MAGIC_MASK_MODES)}")
    source_frame_range = _require_bounded_range(
        source_frame_range, seed_frame=seed.seed_frame)
    if not scale_factor or scale_factor <= 0:
        raise ValueError(
            f"bad backing scale {scale_factor!r}: the main window's "
            f"measured backing_scale, positive")
    click = {"x": seed.viewer.x / scale_factor,
             "y": seed.viewer.y / scale_factor}
    return Script(
        goal=(f"Seed the open Magic Mask on {seed.subject!r} in clip "
              f"{seed.clip_id} frame {seed.seed_frame} "
              f"within source frames {source_frame_range.start}.."
              f"{source_frame_range.end} inclusive "
              f"(object {seed.object_id}, mode {mode})"),
        steps=[
            _color_page_step(),
            _seed_click_step(seed, click),
        ],
    )


def track_script(direction: str, source_frame_range: SourceFrameRange) -> Script:
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
    source_frame_range = _require_bounded_range(source_frame_range)
    label = TRACK_LABELS[direction]
    return Script(
        goal=(f"Track the Magic Mask {direction} from the seeded frame "
              f"within source frames {source_frame_range.start}.."
              f"{source_frame_range.end} inclusive"),
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
