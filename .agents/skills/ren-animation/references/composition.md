# Composition

How to break a programmatic animation into scenes and lay out its
elements. Renderer-neutral: the principles hold whether the backend
is Remotion, HyperFrames or FFrames.

## Scene decomposition

One scene per idea. A scene is a single visual thought that reads in
one glance. The test: if you cannot name the scene's one job in a
few words, it is two scenes.

Scene lengths (measured starting points, not defaults):

| Scene role | Length | Why |
|---|---|---|
| Title / opener | 2.5-4 s | Long enough to read, short enough to not stall |
| Content | 4-8 s | One idea plus its evidence |
| Outro / card | 2-3 s | Land the closing thought |
| Social hook | first 1.5 s | The hook lands before the viewer scrolls |

A first frame that is already something - not a fade from black - is
required for social formats. The viewer decides in the first second.

## Visual hierarchy

One element per scene is fine. Whitespace is the most effective
design tool. When a scene carries more than one element:

- Pick 2-3 x-positions (left edge, centre, 60%) and reuse them
  across scenes. Consistency reads as intentional.
- Left-aligned text blocks read better than centred paragraphs.
  Centre only short titles.
- Vertical rhythm: line height 1.2-1.35x font size; gaps between
  groups 2x the line gap.
- One accent colour for emphasis. Two or three colours plus neutrals
  total. More reads as noise.

## Safe areas

Ren's safe-area fractions come from `library/tools/safe_area.py`,
which derives them from measured platform UI layouts. Never hardcode
a margin in pixels.

- **Landscape 1920x1080**: content inside 5% on every edge
  (broadcast title-safe convention).
- **Portrait 1080x1920**: content inside x 8-92%, y 12-78%. The
  top and bottom bands are covered by platform UI (caption block,
  action rail, status bar).
- **Square 1080x1080**: 5% on every edge.

The fractions are resolved against whatever `(width, height)` the
caller renders at, so a 4K master and a phone-sized render get the
same profile without a second row of numbers.

## Layout grid

Align to a grid. Pick the x-positions once per composition and reuse
them. Elements that share an edge share the same inset. A composition
whose elements each picked their own margin reads as accidental.

## Scene flow

Scenes connect. The connection is either:

- A **cross-fade** - the outgoing scene fades as the incoming one
  fades in, over a shared window. Says "this continues."
- A **push** - the outgoing scene slides one way while the incoming
  scene slides in from the other. Says "next point."
- A **carry** - an element persists across the cut, so the eye
  follows it from one scene to the next.

Choose the transition type from what the content is doing
emotionally, not from what is technically convenient. See
`transitions.md`.

## Holds

A hold (nothing moves) of 1-3 s is good - it lets the viewer absorb.
Keep a little ambient motion somewhere in the frame during a hold so
it does not look frozen. A hold with no motion reads as a frozen
frame, and `verify_render` will flag it.
