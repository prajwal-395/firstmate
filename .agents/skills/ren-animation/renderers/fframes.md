# FFrames renderer

`ren-graphics-fframes` is an implemented Ren-owned renderer. This file
describes its current scene input, frame output and lowering behavior.
The executable renders frames; it does not provide the inspection CLI
commands proposed in older versions of this skill.

## Runtime interface

One Ren-owned Rust executable:

```
ren-graphics-fframes render scene.json
```

- **Input**: `scene.json` - the Ren Graphics IR (scene graph with
  nodes, transforms, animations, easings).
- **Output**: RGBA PNG frames, one per frame, named by frame index.
- **Rendering**: the FFrames Skia renderer.
- **No FFmpeg encoding**: the runtime renders frames only. Ren's
  overlay carriage encodes the PNG sequence to the delivery codec
  (ProRes 4444 or whatever Resolve consumes). This isolates FFrames
  to rendering, preserves the alpha/premultiplication contract, and
  keeps outputs pixel-testable across engines.

The customer never installs Rust. The compiled binary ships with Ren.

## Why a precompiled runtime

Ren does not compile arbitrary Rust programs on customers' machines.
A toolchain requirement, compile latency, dependency management and
sandbox/security concerns make agent-written Rust a non-starter.
One Ren-owned binary takes the IR, deserializes it, builds the SVG
scene graph, executes animation curves and shaders, and renders
frames on Metal.

## IR lowering

| IR concept | FFrames lowering |
|---|---|
| `Group` | `<g>` with `transform` and `opacity` |
| `Text` | `<text>` with measured layout (`text_width`, `text_break_lines`) |
| `Shape` | `<rect>`, `<circle>`, `<path>` |
| `Image` | `<image href={...}>` |
| `Video` | `<image>` with a synced video frame |
| `Shader` | `fframes::Shader` (SkSL or Shadertoy GLSL) |
| `Mask` / `Filter` | SVG `<defs>` with `url(#id)` references |
| `Camera` | `Transform` on a wrapping `<g>` |
| `Animation` | `frame.animate(&timeline!(...))` |
| `spring` easing | `Easing::Spring { mass, stiffness, damping }` |
| `cubic-bezier` | `Easing::CubicBezier(x1, y1, x2, y2)` |
| `linear` | `Easing::Linear` |
| `steps` | quantized keyframes |

## Animation execution

FFrames animations are keyframe-based. The renderer consumes key times,
values and easing from the authored scene; use values declared by the
project or creative brief, not sample constants from a renderer guide.

- Before the first keyframe the value is `from`; after the last it
  stays at `to`.
- Values: `f32`, `f64`, `Color`, `Transform`.
- Runtime start times (staggering, data-driven):
  `frame.animate_runtime(AnimateRuntimeInput { on_second, from, to, ... })`.
- For springs, pass a generous duration (3.0): the spring stops at
  its own settle time.

## Text handling

FFrames measures text rather than guessing:

- `frame.text_width(ctx, font, "Title")` - the width of a string.
- `frame.text_fit(ctx, font, long_title, 900, TextOverflow::Ellipsis)` -
  shrink to fit with ellipsis.
- `frame.text_break_lines(ctx, text, BreakLinesOpts { ... })` - a
  ready `<text>` with one `<tspan>` per line.

The helpers take `&mut frame` and return `None` when the font is not
loaded. Use the same family, size and weight in the `<text>`
attributes.

## Shaders

`fframes::Shader` runs SkSL or Shadertoy GLSL on the Skia GPU surface
while the frame is drawn. SVG transforms, clips, masks and opacity
apply to the layer like to any element.

Built-in uniforms when declared: `iResolution`, `iTime`,
`iTimeDelta`, `iFrame`. Pass images with `.image("iChannel0", photo)`.

A compile error is logged once and the layer is skipped. Only the
Skia backend runs shaders; the CPU backend draws nothing in their
place.

## Known limits

- **No FFmpeg encoding in the runtime.** Frames are PNG only. Ren's
  carriage encodes.
- **Degenerate SVG shapes may not draw.** Ensure the authored geometry
  remains valid throughout the animation; do not use a zero-sized shape
  as a visible state.
- **A panic in `render_frame` stops the render.** No panics, file
  reads or heavy work in the render loop. Prepare data in the
  constructor.
- **Fonts must be shipped.** Unknown families fall back silently.
  Ship every font file and use its exact family name.
- **Heavy filters on animated subtrees are slow on CPU.** Keep them
  on static subtrees (no `{}` inside) so they are cached, or use
  the Skia backend.

## Inspection

The renderer exposes rendering only. Use the actual rendered frames and
Ren's verification loop (`references/verification.md`) for inspection;
do not assume the binary provides separate inspect, contact-sheet,
onion-skin or audio-analysis commands.
