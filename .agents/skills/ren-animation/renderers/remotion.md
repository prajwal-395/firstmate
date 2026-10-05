# Remotion renderer

Remotion renders video from React. A composition is a React
component tree; the renderer calls `useCurrentFrame()` and the
component returns the frame's JSX. Ren's Remotion compositions live
in `remotion-subtitles/src/compositions/`.

## IR lowering

| IR concept | Remotion lowering |
|---|---|
| `Group` | `<div>` or `<AbsoluteFill>` with CSS `transform` |
| `Text` | `<div>` or `<span>` with CSS `font-*` |
| `Shape` | `<div>` with CSS `background`, `border-radius` |
| `Image` | `<Img src={staticFile(...)}>` |
| `Video` | `<OffthreadVideo src={...}>` |
| `Shader` | Not supported natively; use a CSS `filter` or a canvas |
| `Mask` | CSS `clip-path` or `mask` |
| `Camera` | CSS `transform` on a wrapper `<div>` |
| `Animation` | `interpolate()`, `spring()`, or CSS `transition` |
| `spring` easing | `spring({ config: { stiffness, damping, mass } })` |
| `cubic-bezier` | `Easing.bezier(x1, y1, x2, y2)` |
| `linear` | `Easing.linear` |
| `steps` | `Easing.steps(n)` |

## The composition contract

- **One component tree.** The renderer calls the root component
  for every frame. The component reads `useCurrentFrame()` and
  returns the frame's JSX.
- **Deterministic.** No `Math.random()`, no `Date.now()`, no
  `performance.now()`. The same frame always produces the same
  picture.
- **No layout thrash.** Animating `width`, `height`, `top`, `left`
  or `font-size` causes reflow. Animate `transform` and `opacity`.
- **Fonts must be loaded before the first frame.** Register fonts
  at module scope so the `delayRender` handle is taken before the
  first frame is rasterised.

## Entrance and exit

- Use `interpolate(frame, [start, end], [from, to], { easing })`
  for value tweens.
- Use `spring(frame, { config: { stiffness, damping, mass } })`
  for spring tweens.
- A `cut` entrance is no ramp at all - the element is simply
  present from its start frame.
- Exits are faster than entrances. An exit is often opacity only.

## Text

- Preserve line boxes and word spacing. An emphasised word is a
  `font-size` change, not a `transform: scale()` - a scale grows
  the glyph without reserving layout width, so it collides with
  neighbours.
- Measure text width before laying out. Wrap with a measured
  line-breaker, not a character count.
- The caption box is bounded by the safe-area insets from
  `library/tools/safe_area.py`. Never hardcode a margin.

## Camera

- A camera move is a CSS `transform` on a wrapper `<div>`.
- To zoom into a non-centred point: scale the wrapper, then
  translate by `T = -offset x (scale - 1)`.
- Constant-speed path travel uses `Easing.linear`.

## Shaders

- Not natively supported. Use a CSS `filter` for blur, brightness,
  etc.
- For GPU shaders, use a `<canvas>` element with a WebGL context,
  or render through FFrames instead.

## Known limits

- **No ProRes decode.** Browsers cannot decode ProRes. A ProRes
  asset must be transcoded to VP9 WebM (with alpha) before it
  reaches a Remotion composition. See `docs/BRAND_MOTION_MEASURED.md`.
- **No arbitrary Rust.** Remotion is React/JS only.
- **Commercial license.** Remotion's personal edition is free for
  personal use. Automation products need a commercial license.
  Public builds of Ren exclude Remotion and default to HyperFrames.
- **Single-threaded.** Remotion renders on the main thread. A
  complex composition with many elements may drop frames.

## Verification

```bash
# Render a composition to a PNG sequence
npx remotion render <composition> --sequence --output-dir frames/

# Render a single frame
npx remotion still <composition> out.png --frame=96

# Deterministic gate over a rendered file
python3 -m library.skills.verify_render.skill \
    --video /path/to/master.mp4 \
    --project-folder /path/to/project \
    --step-id <step-id>
```

The `verify_render` skill checks black frames, freeze frames,
loudness, silence, resolution, frame rate and duration. It is the
deterministic gate over any rendered file, regardless of which
renderer produced it.
