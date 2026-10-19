# Motion

Timing, pacing, easing and the atomic moves Ren composes. There are no
house timing values. Use values declared by the project or creative
brief; otherwise choose from the actual copy, measured audio, scene
purpose and renderer constraints, and state the source of the decision.

## Timing

- Keep speech-synced events anchored to transcript timings.
- Give text enough screen time for its actual length and reading role.
- Derive entrances, exits and holds from the scene's pacing and the
  transition it must connect to. Do not fill missing creative values
  from a generic range.
- Staggers are useful when they clarify order. Avoid adding them by
  habit.

## Easing

The renderer-neutral vocabulary is `linear`, `ease`,
`cubic-bezier(x1, y1, x2, y2)`, `spring {mass, stiffness, damping}` and
`steps(n)`. Select the curve that serves the intended movement and
confirm the chosen renderer supports it. A spring's natural settle
must remain visible; do not cut it off with a shorter hard stop.

## What to animate

Prefer compositor properties when they express the requested motion.
Layout changes may reflow content, so inspect the result when changing
width, height or type size. Keep all values in the requested coordinate
space and validate against the rendered frame.

## Speech and data

Use transcript timestamps for word reveals. Data driven motion should
preserve the source values and units. Do not invent a visual magnitude,
pace or emphasis where the direction does not supply one; ask when the
missing choice changes the result materially.
