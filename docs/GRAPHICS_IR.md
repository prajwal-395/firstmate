# Ren Graphics IR

Ren owns the animation language. FFrames, HyperFrames, Remotion, Fusion and any other backend are execution adapters that lower to and render from this IR.

An entrance is never a GSAP tween, a CSS transform or an FFrames `timeline!`. It is a renderer-neutral animation:

```
Animation: target: title, property: transform.scale,
           from: 0.92, to: 1.0, start: 3.200, duration: 0.420,
           easing: {kind: spring, stiffness: 80, damping: 15, mass: 0.8}
```

## The contract

The IR is the language, engines are adapters. Every engine that renders a scene produces the same picture from the same scene.json, because the evaluator in `library/graphics_ir/evaluator.py` is the reference for what any animated property is at any frame.

## Scene format

A scene is a versioned JSON document (`scene.json`) with a JSON Schema file (`scene.schema.json`) so a non-Python renderer can consume it.

- Times are seconds throughout
- The scene carries an explicit `fps`, `width`, `height` and `duration`
- Colours, fonts and assets are referenced as project data (file paths), never baked-in defaults
- Fonts are referenced by file path so every engine draws the same face

## Node types

Scene, Camera, Ground, Group, Text, Shape, Image, Video, SVG, Shader, Mask, Filter, Effect, Audio.

Each node carries: transform, opacity, bounds, z-index, mask, clip, blend, timing, animations.

## Easing kinds

- `linear` - no easing
- `ease` - power ease-out (power=2 is GSAP power1.out, power=3 is GSAP power2.out)
- `cubic_bezier` - CSS cubic-bezier function
- `spring` - spring physics (same integrator as Remotion/HyperFrames)
- `steps` - stepped easing

## Animatable properties

translate, rotate, scale, skew, opacity, blur, color, gradient, path, mask, camera parameters, shader uniforms.

## Lowering

`library/graphics_ir/lowering.py` lowers each of the five standard compositions' props to an IR scene:

- `lower_subtitle_overlay` - SubtitleOverlay props to scene
- `lower_timed_text_overlay` - TimedTextOverlay props to scene
- `lower_full_frame_card` - FullFrameCard props to scene
- `lower_motion_graphics` - MotionGraphics props to scene
- `lower_post_header` - PostHeader props to scene

The lowered timing and easing match what the HyperFrames compositions' GSAP timelines do today.

## Evaluator

`library/graphics_ir/evaluator.py` samples any animated property at a frame. It is the reference other adapters are checked against.

```python
from library.graphics_ir import evaluate, read_scene

scene = read_scene("scene.json")
state = evaluate(scene, frame=30)
# state["title"]["transform.scale"] == [0.96, 0.96]
```
