# Overlay Position and Pan/Tilt Rails

HISTORY, kept as evidence: the captain reported the same defect twice - captions in the timeline carrying absurd Pan/Tilt values (they quoted y=-7680), some on screen and some completely off frame. Read off the live timeline on 2026-09-10, the rail Resolve held on that 1080x1920 project was -3840 - HALF what the first repair gated on, which is why that repair refused one card and let 37 ride onto the clamp. That reading does not reproduce - the rail is the 4x law now (`tight_box.MEASURED_RAILS`: Pan 4320 / Tilt 7680 here, captain's call 2026-09-13) - but the SHIPPED_AND_HELD table below is what that day actually measured, and the arithmetic it pins still explains the incident. The measurement below says why, and pins the numbers the clamp gate (`tight_box.placement_holds`) and the 480-pixel canvas floor (`tight_box.MIN_CANVAS_HEIGHT`) were first calibrated against.

THE ROOT CAUSE, as arithmetic. Resolve's per-clip Pan/Tilt move a clip by a fraction of its OWN size - shift = Pan * (clip_dim / timeline_dim) * base_scale, the one measured law in `library/tools/resolve_transform.py` - while Resolve pins the property at a rail it does not report and refuses silently past it. On the captain's 1080x1920 reels every caption box was bottom-anchored with its lower edge at y=1636, so a 152-tall canvas asks for Tilt -7578.9 and gets -3840.

## Overlay intent pins

Moved from `tests/test_overlay_intent.py`. The Reel 09 numbers are the
fixtures: 22 captions the captain pinned to one place, four motion graphics
pinned by segment id. What is asserted is the MECHANISM - segment beats kind
beats computed, and anything malformed refuses - with his values as the data, so
a regression that drops or silently ignores a pin fails there rather than on his
timeline. A pin names a PLACE, not a transform (version 2): the transform is
computed from it against the canvas going down, so a correction to the engine's
model of the Resolve transform moves nothing that was pinned. Version 1 held the
raw Pan/Tilt and is refused: on 2026-09-11 the law was corrected and honouring
the project's own v1 pins verbatim would have moved Reel 13's approved captions
108px.

## One positioning rule

Moved from the module docstring of `tests/test_overlay_positioning_rule.py`.

```text
One positioning rule, and a stored value judged against INTENT.

The captain, 2026-09-11: *"to get the subtitles to the same absolute
positioning on the y-axis in the reel, the actual y-axis value in the
inspector tab is sometimes 0 sometimes -870. so there is something else
at play that is affecting the positioning"*.

He is right, and the rule is one sentence: **Pan/Tilt move a clip by a
fraction of its OWN canvas, not of the frame** - the shift is
``value * (canvas_dim / frame_dim) * base_scale``, with ``base_scale``
1 at ``Scaling=1`` - so an overlay already rendered full-frame is in
position at 0 while the identical caption rendered on a 480-tall tight
canvas needs Tilt -1740 to reach the same screen row.  Both numbers are
honest; the Inspector number is only readable with the clip's own
resolution beside it.

There is no "draw gain".  A 2.0 was briefly recorded here and it was an
arithmetic error: it was calibrated against a CAPTURED Pan/Tilt rather
than one it had set itself, so it paired a real still with a number
that was by then half the value in force, and it halved every tight
placement the engine computed.  The law now lives once, in
``library/tools/resolve_transform.py``, measured by SETTING values and
RENDERING - 16 plates, two builds, four processes.

The measurements pinned here were read off the live project
"Podcast (field test)" on 2026-09-11, all against exported pixels:

- Reel 13 caption @854 (tight, 840x480, stored Tilt -1740.0): its ink
  sits at canvas rows 279..432, and a correlation scan of the artefact
  against a still EXPORTED from that timeline finds the canvas top at
  frame row 1155 and the ink at 1434..1587.
- Reel 13 caption @567 (full-frame, 1080x1920, stored Tilt 0.0): ink at
  frame rows 1415..1572, drawn 1:1.
- Reel 30's first motion graphic (tight, 724x480, stored Tilt 5184.0):
  the rule puts its canvas at frame rows -576..-96, entirely off the
  TOP of the frame, and a correlation scan of an exported still of that
  frame finds the artefact nowhere in it (best MSE 17 267).  Seventeen
  graphics across five reels carry that value and none of them is on
  screen.

The last one is why verification is against INTENT and not against a
read-back: 5184 reads back as exactly 5184, so every gate that asks
"did Resolve hold what I set" passes it.
```
