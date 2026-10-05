# Motion

Timing, pacing, easing and the atomic moves Ren composes. All values
are measured starting points and ranges, not defaults. A project that
declares its own values wins.

## Reading time

A viewer reads about 3 words per second, plus 1 s to notice the text
appeared. A 9-word line needs ~4 s on screen after its entrance
settles. This is the floor for how long any text stays up - shorter
and the viewer misses it, longer and the pace drags.

## Entrances and exits

| Move | Duration | Easing | Distance |
|---|---|---|---|
| Entrance | 0.3-0.6 s | ease-out or spring | 40-120 px + opacity 0 to 1 |
| Exit | 0.2-0.3 s | ease-in | shorter distance or opacity only |
| Hold | 1-3 s | - | - |

Enter from a short distance with opacity. Exit faster, to a shorter
distance or just opacity. The viewer reads an arrival, not a
departure.

## Easing

The easing vocabulary is renderer-neutral:

- `linear` - constant speed. Only for ambient loops and path travel
  where constant speed is the point.
- `ease` - generic smooth. Use when no specific character is needed.
- `cubic-bezier(x1, y1, x2, y2)` - a custom curve. Fast-then-settles
  is `(0.16, 1.0, 0.3, 1.0)`; camera-move smooth is
  `(0.65, 0.0, 0.35, 1.0)`.
- `spring {mass, stiffness, damping}` - physical settle. The spring
  stops at its own settle time; the duration is a ceiling, not a
  promise.
- `steps(n)` - quantized. For mechanical or retro motion.

Spring starting points (adapted from FFrames, MIT):

| Character | mass | stiffness | damping | Feel |
|---|---|---|---|---|
| Snappy | 1.0 | 300 | 26 | UI-like, tiny overshoot |
| Soft | 1.0 | 150 | 18 | noticeable, friendly |
| Bouncy | 1.0 | 220 | 12 | playful, use rarely |

Leave the end time off a spring so it settles on its own. A spring
given a hard stop time cuts off the settle and reads as a broken
tween.

## Staggering

Related items stagger by 60-120 ms (lists, words). Letters by
20-40 ms. A group arriving as one beat reads as intentional; a group
arriving all at once reads as a system event.

Cap group staggers so the whole group has arrived within ~0.5 s.
A stagger that runs longer than that reads as a queue, not a beat.

## What to animate

Animate transform and opacity. Never animate:

- **Font size** - causes jitter and reflow. To grow text, animate
  `scale` from 0.9-0.96 (never from 0 - a zero-scale element is
  invisible and reads as a glitch).
- **Width / height / top / left** - layout properties. They trigger
  reflow and are not compositor-friendly. Use `scale` and `translate`
  proxies, or a mask.
- **Rotation beyond +/-6 degrees** - reads as a mistake unless the
  rotation is the point.

## Word reveals

Split text into words, lay them out with measured widths, stagger
each word 40-60 ms with a small upward spring. The reveal is timed
to the speech - each word lands as it is spoken, or just before.
See `audio-sync.md`.

## Counters

Animate a number from 0 to its target over 1.2-1.8 s with a
fast-then-settles ease. Use a font with tabular figures (or
monospace) so digits do not jiggle. Right-align the number.

## Growing bars

Animate a bar's width from a hairline (0.5 px, never 0 - a
zero-sized element is invalid in some renderers) to its target with
a smooth ease-in-out.

## Draw-on strokes

Animate `stroke-dashoffset` from the path length to 0. Measure the
path length at setup; do not hardcode it.

## Ambient loops

Long (6-12 s), small-amplitude loops keep a hold from looking frozen.
One or two per composition, never on the focal element.

## The one-focal-movement rule

At any moment, one thing draws the eye. Secondary elements move less
and later. If everything moves at once, nothing reads.
