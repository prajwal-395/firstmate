# AXI-008: qualifying overlays and rendered graphics

The overlay routes were proven on ONE carrier: a native PNG still.  The
punch list asks for the family to be qualified on what a production reel
actually carries - a rendered `.mov` lower third, and an item that
already carries a Fusion composition - across the six operations the
family names: insertion, removal, positioning, resizing, source
replacement and the supported entry motion.

This document says what the qualification MEASURES, where the lane
lives, how the evidence is promoted, and what is still unmeasured.

## The carriers

| carrier | what it is | why it is here |
|---|---|---|
| `png_still` | a native PNG still with an alpha channel | the baseline the other two are compared against, and the only carrier the overlay routes were ever qualified on |
| `rendered_video` | a rendered movie overlay: ProRes 4444 with an alpha plane, 24 frames | the engine's own lower-third format, and what a reel's graphics actually are |
| `fusion_composition` | the same rendered overlay carrying a Fusion composition | the animated lower third a production reel holds, authored by the engine's own comp builder over the item's played window |
| `rendered_video_with_audio` | the same rendered card that ALSO carries an audio stream - the name bar the captain placed by hand | Resolve labels such a pool item "Video + Audio", and the route used to refuse the card for that label alone |
| `dialogue_video` | a real dialogue clip: video, audio, and NO alpha plane | the footage every reel is cut from.  The label does not tell it from the name bar, so the media's own alpha plane has to |

A production asset is used when the machine names one
(`REN_AXI_OVERLAY_MOV`) and its canvas is a tight overlay; the report
records which asset every row was measured on.  A machine with no
rendered overlay measures the PNG carrier only and says so: a missing
asset is UNMEASURED, never a zero.

The card that carries an audio stream is named separately
(`REN_AXI_OVERLAY_AUDIO_MOV`) and copied out of its home; when the
machine names none, the lane renders the same shape from its own movie.
The footage is always the lane's own: a short clip with a tone and no
alpha plane.

A carrier may DECLARE the operations it is measured on.  The two new
carriers are measured on the move: the name bar is what the captain
asked to move, and the footage is what the route has to refuse.  A
narrowing is a statement about the carrier, and the harness's contract
test reads it as one.

## The name bar, and what the label does not say

Resolve labels a media-pool item by the STREAMS the file carries and
not by the kind of media it is: a rendered card with a silent audio
stream reads `Video + Audio`, and the route used to refuse it as
"neither a native PNG still nor a rendered movie overlay".  The gate now
reads the KIND out of the label.

What tells the name bar from the footage is the media's own alpha
plane: a rendered overlay carries one and a dialogue clip does not, and
the route refuses a video without one by name.  Widening the label
therefore does not widen the route to the footage, and the lane
measures both halves.

## The operations, and what each one is expected to do

| operation | production entry | what the lane measures |
|---|---|---|
| `overlay.add` | the placement path, onto an overlay row (V3+) | the item lands at the requested record frame; its rendered contribution is the box the carrier draws at |
| `overlay.remove` | the delete path, on the overlay row | the item is gone and the rendered frame no longer carries its contribution |
| `overlay.position` | the touch-up's `overlay_position` projection, lowered through the layout model and written through EditPatch | the overlay's RENDERED box moved by the requested delivery-pixel delta |
| `overlay.resize` | the touch-up's `overlay_resize` projection, the same way | the overlay's RENDERED box is the requested size, about its own centre |
| `overlay.replace` | the swap path: a new source over the same span | the new source plays at the same span, the transform is carried, and the rendered ink is the new asset's |
| `overlay.entry_motion` | the touch-up's own entry comp, imported onto the staged item | the comp is on the item, the timing is unchanged, and the rendered frames show the ramp |

A target that already carries a Fusion composition is REFUSED BY NAME
for the operations that cannot place it (the comp draws the overlay, so
the native transform is not what places it).  The lane expects that
refusal, and a refusal never produces a qualification record: it is a
recorded outcome.

## The measurement

Every geometry row is judged on RENDERED FRAMES, never on the API
read-back alone.  A stored Pan/Tilt says where Resolve was TOLD to
draw; only the frames say where it drew, and a value past Resolve's
silent clamp reads back the clamp with a successful return.

The instrument is the overlay's own contribution: the frame is rendered
twice - once with the overlay enabled and once with it disabled - and
the difference is what the overlay contributed.  The measured box is
then compared with the REQUESTED box, in delivery-frame pixels, and
with the safe area of the frame that was rendered.

The checks are the acceptance contract's own
(`library/tools/operation_acceptance.py`): `target_identity`,
`requested_timeline_span`, `unrelated_items_unchanged`,
`projected_geometry_matches_request`, `rendered_pixels_match_request`,
`bounding_box_matches_request`, `inside_safe_zone`, `not_clipped` and
`no_unexpected_black_frames`.

The preservation half is a row-by-row comparison of the BEFORE and AFTER
state: identity, record span, source span, enabled state, Fusion comps
and the transform of every item the operation did not address.  An
overlay that moves, resizes or is replaced changes its OWN geometry and
nothing else - the picture, the audio rows and the other overlays are
compared item by item.

## Where the lane lives

- `tests/live/axi_overlay_qualification.py` - the scenario table, the
  live batch, the judges and the report.
- `tests/unit/tools/test_axi_overlay_qualification.py` - the contract
  the harness carries, checked offline: every operation has a scenario
  on every carrier, every scenario names an operation the algebra
  declares, a refusal never produces a row, a pair with no executor is
  reported rather than promoted, the rendered box is compared with the
  requested box, and the denominators are never combined.

The lane writes on a DISPOSABLE copy of the private scratch timeline
inside `Ren Qualification`, and imports the media it places.  It never
writes a captain's project, timeline or media.

## Running it, and promoting the evidence

```sh
python3 -m tests.live.axi_overlay_qualification <stamp>
```

The lane writes its report to
`tests/live/reports/axi-overlay-qualification.json` and one
qualification document per (operation, backend) pair to
`tests/live/reports/registry/`.  The documents are the current schema
(`ren-axi-qualification-result/v2`) and are promoted by the EXISTING
tool, never by a hand edit of the registry file:

```sh
python3 -m library.tools.axi_qualification --import-result \
    tests/live/reports/registry/overlay.position__reel_touchup__axi-overlay.json
```

A row is written only for a pair that HAS an executor.  The overlay
family's pairs that the algebra declares no implementation evidence for
(`overlay.remove`, `overlay.replace`, `overlay.swap_pixels`,
`overlay.entry_motion`) are REPORTED as gaps: promoting them would route
production work to a path the registry cannot vouch for.

### A focused run

`REN_AXI_OVERLAY_SCENARIOS` names the scenarios a run measures (comma
separated), and `REN_AXI_OVERLAY_REPORT` names the report it writes.  A
focused run writes its qualification documents under
`tests/live/reports/registry/focused/`, never over the family's: a
document is the evidence a promotion reads, and evidence from two
carriers is not evidence from one.

### Which code the run measures

The broker runs the MACHINE's checkout, so a lane measuring a BRANCH
names the checkout under test in `REN_AXI_OVERLAY_ENGINE_ROOT` and the
run imports the modules under test from it.  The report records the
engine root it measured.  Unset measures the broker's own checkout,
which is what a run against merged code measures.

## The scoring rule

The denominators are never combined.  The report states, separately:

- **measured** - the operation committed and every check the acceptance
  contract names was satisfied on the rendered frames;
- **refused** - the production entry refused the request by name and
  nothing on the timeline moved;
- **failed** - the operation committed but the acceptance contract was
  not satisfied, or the scenario failed mid-flight;
- **unmeasured** - the scenario produced no row.  Never a zero.

## What is still open

The lane measures the overlay family on disposable Resolve timelines.
The built-reel touch-up path - the staged copy, the conformance pass
and the promotion - is NOT exercised by this lane, so the evidence it
produces is about the touch-up's projection, its write and its
verification, not about the promotion of a promoted reel.  A row's
semantic boundary says so in as many words.

**The name bar on a BUILT reel is not measured here either, and the
reason is worth writing down.**  The graphic is the real one - the
lower third the captain placed by hand, copied out of the reel it
rides - and the move is measured on the rendered frames; what is not
measured is that move on a copy of the reel that carries it.  Two
things stand in the way, and neither is the media-kind gate this lane
fixed:

- the broker's qualification session runs its steps inside the
  qualification project, and a live AXI command refuses to address a
  project that is not open, so the scratch project the built reel
  lives in cannot be reached from a lane that may not switch the
  machine's project;
- the production semantic route on that reel fails closed on the
  RECORDED qualification predicates, not on the media: the reel is
  dense, portrait 1080x1920 and 23.976fps, and the state the overlay
  family was qualified in is a light, landscape, 24fps timeline.  That
  is a boundary of the qualification itself, and it is a different
  defect from the one this lane fixed.

So the honest statement is: the gate now accepts the graphic a
production reel carries, the graphic moves, and the footage is still
refused - measured on the frames, on the real asset - while the move
on a copy of the built reel itself is not yet measured.

## What the live runs found

The lane is the instrument, and the instrument found things.

**A product defect: a resize that changes the aspect was answered with a
different width.**  A resize asked for 160x60 over a 320x90 source; the
projection wrote `ZoomX 0.5` and `ZoomY 0.667`; Resolve holds ONE zoom
for both axes (`resolve_transform`: *"Resolve's ZoomX/ZoomY, one number
for both"*, and independent `ZoomX`/`ZoomY` is not qualified) and
delivered `0.667` - so the overlay was drawn 213 px wide instead of the
160 that was requested.  The route now REFUSES that size by name, with
both aspects in the refusal
(`library/tools/reel_touchup.py`, `_overlay_resize_projection`), and the
defect is named offline in
`tests/unit/resolve/test_overlay_video_media.py`.

**Four fixture defects in the lane itself**, each read out of the
refusals the run recorded rather than guessed:

- the cleanup allowlist named only the carrier in hand, so the still the
  previous scenario left behind was still on the span and the next
  carrier's placement was refused - the lane caused the collision and
  then read it as a product refusal;
- the fixture's overlay row was named `V3`, and the touch-up path
  decides whether a row is continuous program by its NAME - a reel's
  speaker lower thirds ride the `Motion Graphics` row
  (`timeline_layout.SINGLETON_NAMES`), so a row called `V3` is program
  and every removal off it refuses.  The lane now names its overlay row
  the way a production reel names it;
- the ADDITION was gated AFTER the lane had already placed the carrier,
  so the gate saw a collision and refused what the lane had just done.
  The gate is now asked first, on a free span;
- an unresolved recovery from an earlier write blocked every later write
  on the same timeline (*"an unresolved recovery must be recovered or
  abandoned before another write"*).  On a disposable copy the write
  that opened it IS the state under test, so the recovery is abandoned
  and the closure is journalled with its reason.

**Three judging defects**, all of the same shape - a check that read
something else than the thing it named:

- the black-frame check was about the PROGRAMME, not the overlay: a
  frame that is black with the overlay and black without it is a black
  programme, not a black graphic;
- the removal toggled a DELETED item's handle, which is stale; the
  contribution is now measured before the deletion and the frame after
  it is compared with the frame the overlay was switched off in;
- the addressed item was found by NAME, and a source replacement
  RENAMES the item - so the name is not an identity.  The target is
  found by its unique id, which survives the swap.

## What the lane measured, and what it qualified

The live run, on a disposable copy of the private scratch timeline,
judged every geometry row on the RENDERED FRAMES:

| outcome | count |
|---|---|
| measured - the operation committed and every check the acceptance contract names was satisfied on the frames | 13 |
| refused by name - the production entry refused the request and nothing on the timeline moved | 5 |
| failed | 0 |
| unmeasured | 0

Three pairs are now **production-enabled**, inside the state the run
measured - a still or a rendered movie overlay on an overlay row, at
24fps, with no unrelated effects:

- `overlay.add::reel_touchup`
- `overlay.position::reel_touchup`
- `overlay.resize::reel_touchup`

`overlay.remove`, `overlay.replace` and `overlay.entry_motion` keep
their real evidence but have no implementation evidence on their pair,
so they stay NAMED GAPS: the registry would route production work to a
path it cannot vouch for.

### The name bar and the footage: a focused run

The two carriers that tell the widened label apart were run on their
own, twice: once against the MACHINE's checkout (the code on main, and
the defect) and once against the checkout under test (the fix).  The
name bar is the real file - the lower third the captain placed by hand,
copied out of its home - and the footage is the lane's own clip with a
tone and no alpha plane.

| outcome | main's checkout | the checkout under test |
|---|---|---|
| measured | 0 | 1 |
| refused by name | 0 | 1 |
| failed | 2 | 0 |
| unmeasured | 0 | 0 |

On main's checkout both carriers were refused with the SAME reason, and
it was the label's: *"a Video + Audio is neither a native PNG still nor
a rendered movie overlay"* - Resolve labels the name bar `Video +
Audio`, because the label names the streams the file carries and not
the kind of media it is.

On the checkout under test:

- the NAME BAR is measured.  The gate accepts it, the projection
  lowers it, the write commits, and the RENDERED frames show the
  graphic where it was asked to go: the ink box measured on the frames
  is `[534, 409, 876, 485]` and the box the projection asked for is
  `[534, 409, 875, 485]` - one pixel, the exclusive end of the ink.
  All nine acceptance checks pass.
- the FOOTAGE is refused by name, with the reason that says what it is:
  *"targets '...dialogue.mov', which carries no alpha plane - a
  rendered overlay carries an alpha plane and a dialogue clip does
  not, so this media is footage and not an overlay"*.  The timeline did
  not move.

The reports are `tests/live/reports/axi-overlay-name-bar-qualification-before.json`
(the machine's checkout) and `tests/live/reports/axi-overlay-name-bar-qualification.json`
(the checkout under test).  Each names the files it measured, so a
reader can see which code produced which row.
