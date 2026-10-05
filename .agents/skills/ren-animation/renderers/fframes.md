# FFrames renderer

**PLANNED - not yet built.** This file describes the planned
`ren-graphics-fframes` runtime: what it takes, what it outputs, and
how it lowers Ren's Graphics IR. The runtime is being built in
parallel with this skill.

## The planned runtime

One Ren-owned Rust executable:

```
ren-graphics-fframes render scene.json
```

- **Input**: `scene.json` - the Ren Graphics IR (scene graph with
  nodes, transforms, animations, easings).
- **Output**: RGBA PNG frames, one per frame, named by frame index.
- **Rendering**: Skia on the GPU (Metal on macOS, Vulkan elsewhere).
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

FFrames animations are keyframe-based:

```rust
frame.animate(&fframes::timeline!(
    at 0.0, animate 0.92_f32 => 1.0, Easing::Spring { mass: 1.0, stiffness: 180.0, damping: 20.0 },
    at 2.5 => 2.8, animate 0.0_f32 => 60.0, Easing::EaseIn,
))
```

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
- **Shaders need the GPU backend.** CPU-only machines get blank
  shader layers.
- **Zero-sized SVG elements are invalid.** Animate from `0.5`, not
  `0`. A zero-width rect or zero-radius circle is skipped.
- **A panic in `render_frame` stops the render.** No panics, file
  reads or heavy work in the render loop. Prepare data in the
  constructor.
- **Fonts must be shipped.** Unknown families fall back silently.
  Ship every font file and use its exact family name.
- **Heavy filters on animated subtrees are slow on CPU.** Keep them
  on static subtrees (no `{}` inside) so they are cached, or use
  the Skia backend.
- **Codec licensing.** FFrames' cargo features include GPL/nonfree
  FFmpeg components. The runtime uses FFrames as a frame renderer
  only; any future self-contained encoder needs an explicit legal
  audit of the exact linked configuration.

## Inspection commands (planned)

The FFrames CLI provides inspection primitives. When
`ren-graphics-fframes` ships, equivalent commands will exist:

| Command | Proves |
|---|---|
| `inspect` | missing fonts, text leaving canvas, invalid SVG, broken transforms |
| `strip` | contact sheet - layout, rhythm, motion |
| `frame` | full-size PNGs of key moments |
| `onion` | movement path and easing |
| `svg` | a frame's final SVG as text |
| `snapshot` | frame diff against approved PNGs |
| `audio analyze` | loudness, true peak, clipping, silence |

Until the runtime ships, use the FFrames CLI directly (if installed)
or Ren's own verification loop (`references/verification.md`).
