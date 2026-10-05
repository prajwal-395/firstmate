# Shaders

GPU shader layers in Ren's Graphics IR. A `Shader` node is a full-frame
layer that runs on the GPU while the frame is drawn. SVG transforms,
clips, masks and opacity apply to the layer like to any element.

## When to use a shader

A shader is for effects that are impractical or impossible in SVG:

- Green-screen keying (chroma key).
- Color grading / LUT application.
- Distortion, blur, glow that must run per-pixel.
- Procedural backgrounds (noise, gradients, patterns).
- Post-processing (vignette, grain, chromatic aberration).

A shader is NOT for:

- Simple opacity or transform animations (use SVG attributes).
- Text (use SVG `<text>`).
- Anything that can be done with an SVG filter on a static subtree.

## Shader languages

Two shader languages are supported by the FFrames backend:

- **SkSL** (Skia Shading Language) - GLSL ES 2 subset. Constant loop
  bounds, no `while`, no dynamic array indexing, no preprocessor.
  Entry point: `half4 main(float2 coord)`.
- **Shadertoy GLSL** - pasted Shadertoy shaders. Entry point:
  `void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Simple
  `#define`s are expanded; macros with arguments and `texture()`
  calls must be rewritten.

## Built-in uniforms

When declared, these uniforms are available:

- `iResolution` - the `<image>` size.
- `iTime` - seconds since the composition started.
- `iTimeDelta` - seconds since the last frame.
- `iFrame` - the frame index.

Pass additional uniforms per-shader: floats, colours, images.

## Images as shader input

A shader can take images as input. Bind an image to a uniform:

- A static image from the project's media.
- A synced video frame (for processing footage on the GPU, e.g.
  green-screen keying).

A missing image is an `Option` - return an empty layer, never a panic.
A panic in a shader stops the render.

## Chroma key example

A green-screen key binds a video frame to a uniform and computes
alpha from green dominance:

```glsl
uniform float3 iResolution;
uniform shader uSrc;
uniform float2 uSrcSize;
half4 main(float2 coord) {
    half4 c = uSrc.eval(coord / iResolution.xy * uSrcSize);
    float a = 1.0 - smoothstep(0.06, 0.2, c.g - max(c.r, c.b));
    return half4(c.rgb * a, a);
}
```

The output is premultiplied - the RGB is multiplied by alpha before
returning. This is the contract the overlay carriage expects.

## Rules

- **Only the GPU backend runs shaders.** The CPU backend draws
  nothing in their place. A shader on a CPU-only machine is a blank
  layer, not a fallback.
- **A shader compile error is logged once and the layer is skipped.**
  Test compilation before rendering the full sequence.
- **Shaders are expensive.** One or two per composition, on the
  layers that need them. A shader on every element will not render
  in real time.
- **The shader's output must be premultiplied alpha.** The overlay
  carriage expects premultiplied RGBA. Straight alpha produces
  fringes and halos.
