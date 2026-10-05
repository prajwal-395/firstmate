# HyperFrames renderer

HyperFrames renders video from HTML. A composition is an HTML file
whose DOM declares timing with `data-*` attributes, whose animation
runtime is seekable, and whose media playback is owned by the
framework. Ren's HyperFrames compositions live in
`hyperframes/compositions/`.

## IR lowering

| IR concept | HyperFrames lowering |
|---|---|
| `Group` | `<div>` or `<g>` with CSS `transform` |
| `Text` | `<div>` or `<span>` with CSS `font-*` |
| `Shape` | `<div>` with CSS `background`, `border-radius` |
| `Image` | `<img>` |
| `Video` | `<video>` with `data-start` / `data-duration` |
| `Shader` | WebGL canvas or CSS `filter` |
| `Mask` | CSS `clip-path` or `mask` |
| `Camera` | `transform` on a `.world` wrapper |
| `Animation` | GSAP tween on a paused timeline |
| `spring` easing | Baked per-frame offsets from Remotion's `spring()` (see below) |
| `cubic-bezier` | GSAP `ease: "cubic-bezier(x1,y1,x2,y2)"` |
| `linear` | GSAP `ease: "none"` |
| `steps` | GSAP `ease: "steps(n)"` |

## The composition contract

- **One paused timeline.** Each composition registers exactly one
  `gsap.timeline({ paused: true })` at `window.__timelines["<id>"]`.
  The key must match the root `data-composition-id`.
- **Render length is the root's `data-duration`**, not the
  timeline's length. A timeline that runs past it is cut off; one
  that ends early holds its last frame.
- **Seek-safe both directions.** Use `fromTo` with explicit
  from-states, absolute values (never relative `+=` tweens), and
  `immediateRender: false` when re-owning a target.
- **Deterministic.** No `Math.random()`, no `Date.now()`, no
  `performance.now()`, no `repeat: -1`, no network at render time.
- **No CSS `transition` on animated elements.** They interpolate
  independently of seek and flicker.
- **No `display` or `visibility` tweens on `.clip` elements.** The
  framework owns clip visibility. Animate a child instead.
- **No `width`/`height`/`top`/`left` tweens.** Use `scale`,
  `translate`, `clip-path` or masks.

## Entrance and exit

- Every scene uses entrance animations with `gsap.fromTo()` (not
  `gsap.from()` - a `from` tween animates TO the current CSS value,
  so pairing it with CSS `opacity: 0` is a 0-to-0 noop).
- Exit animations are BANNED except on the final scene. The
  transition IS the exit.
- Outgoing and incoming animate at the same timeline position T.

## Text

- Preserve line boxes, word spacing, readability and final fit.
- If text moves internally, move the glyphs or masked bands, not
  only decorations around the text.
- Snapshot readable frames to verify.

## Camera

- A camera move is a `transform` on a `.world` wrapper containing
  all scene content. Single-element composite transform
  `translate(x,y) scale(S)`.
- To zoom into a non-centred point: scale the outer wrapper,
  counter-translate the inner wrapper by `T = -offset x (scale - 1)`.
- Constant-speed path travel uses `ease: "none"`.

## Spring easing

Ren's HyperFrames compositions do NOT use GSAP `back.out` or a
spring plugin for spring-like moves. `back.out` is an overshoot
ease, not a spring - it has no mass, stiffness or damping, and it
silently changes the motion.

What the compositions do today: the digit-strip spring curve (in
`hyperframes/compositions/MotionGraphics.html`) is sampled from
Remotion's own `spring()` at stage time. `hyperframes_render.py`
bakes the per-digit per-frame y-offsets into `data._hf_digit_offsets`,
and the composition plays them back as discrete per-frame tweens with
`ease: "none"`. The physics is the dependency's, not a second
spelling of it.

Exact spring parity across all backends is owned by the Graphics
IR's reference evaluator (being built in a parallel lane), not by
an approximation in any one backend. Until that evaluator ships,
spring-like moves in HyperFrames are baked from Remotion's `spring()`
at stage time.

## Shaders

- WebGL fragment shaders via a `<canvas>` element.
- CSS `filter` for blur, brightness, etc.
- Shader uniforms are tweened through GSAP `onUpdate`.

## Known limits

- **No ProRes decode.** Browsers cannot decode ProRes. A ProRes
  asset must be transcoded to VP9 WebM (with alpha) before it
  reaches a HyperFrames composition. See `docs/BRAND_MOTION_MEASURED.md`.
- **VP9 alpha measurement.** ffmpeg's native `vp9` decoder drops
  WebM alpha; `libvpx-vp9` recovers it. Measure alpha with the
  correct decoder.
- **No arbitrary Rust.** HyperFrames is HTML/JS only. For GPU
  shaders that need Rust, use FFrames.
- **Single paused timeline.** Multiple GSAP timelines are not
  seek-safe. Register one and nest sub-timelines into it.
- **Fonts must be self-hosted.** A named CSS `font-family` needs an
  in-file `@font-face` to a shipped local file.

## Verification

```bash
npx --no-install hyperframes@<pin> lint
npx --no-install hyperframes@<pin> check
npx --no-install hyperframes@<pin> keyframes .
npx --no-install hyperframes@<pin> keyframes . --selector "<selector>" --shot "<file>" --samples <n>
npx --no-install hyperframes@<pin> snapshot . --at <times>
```

Choose `<selector>` for the real animated subject. Choose `<times>`
for first frame, proof poses, final-minus-hold, and exact final.
