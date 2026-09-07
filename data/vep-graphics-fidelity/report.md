# Graphics and Animation Fidelity - Exploration Report

## 1. What the graphics path produced before this change

I rendered a 5-second, 1080x1920, 30fps ProRes 4444 overlay using the existing
MotionGraphics composition with all nine drawable element types.

The baseline render is `baseline_render.mp4` in this directory. A representative
still frame is `baseline_frame75.png`. The props file that produced it is
`baseline_props.json`.

What that render shows:

- **title_lockup**: orange text reading "THE FUTURE OF AI / How Models Learn",
  positioned at top centre. Montserrat 900 weight, uppercase, 3px letter spacing.
  Flat colour with a `0px 4px 12px rgba(0,0,0,0.6)` drop shadow.
- **frame_accents**: four corner brackets, 80px, the same orange, at safe area
  boundaries.
- **progress_bar**: a horizontal bar animating from 0% to 100% width across the
  segment's duration.
- **quote_card**: a left-border accent rule with text: "Intelligence is the ability
  to adapt to change." Attribution below.
- **stat_callout**: "175B / Parameters" centred large.

Everything fades in over 8 frames and fades out over 8 frames. That is the only
motion. The vocabulary defines five entrance characters - cut, fade, slide, scale,
mask, draw - but the component only implemented opacity for all of them. A
`slide` entrance looked identical to a `fade` entrance: it just faded in.

The visual quality is functional but plain. Flat monochrome text on transparent
background, no gradients, no blur, no glow, no spatial movement. Compared to the
references the captain gave, the gap is large - not because the renderer cannot
do better, but because the component never asked it to.

## 2. The three references

### 2.1 GSAP (https://demos.gsap.com/explore/)

**What it is.** GreenSock Animation Platform, a JavaScript animation library now
owned by Webflow. The demo hub is a searchable gallery of animation recipes:
magnetic buttons, scroll-triggered parallax, SVG path morphing, text character
scramble, spring physics.

**Could it render to video here?** Yes, with a wrapper. GSAP timelines are
time-addressable: you can call `timeline.seek(frame / fps)` per frame. Inside
a Remotion component, `useCurrentFrame()` provides the frame number, so a GSAP
timeline can be driven deterministically in headless Chromium.
Remotion's own documentation mentions this pattern.

**What it adds that today cannot do.**
- `MorphSVG`: animate one SVG path shape into another. No CSS equivalent.
- `ScrambleText`: randomise text characters during a reveal. More compelling than
  a plain fade for titles.
- `DrawSVG`: animate SVG stroke-dashoffset to draw a path on screen.
- Complex multi-property easing with `CustomEase` and `CustomWiggle`.

**What it does not add.** Most of the demo gallery is interactive (scroll, hover,
drag, cursor-following). These have no meaning in a rendered video. The useful
subset is timeline-driven effects only.

**Licence.** Proprietary, source-available. All plugins (including former "Club
GSAP" premium) are now free for commercial use. One restriction: cannot use
GSAP to build a product that competes with Webflow's design tool.

**Dependency weight.** `npm i gsap` is zero external dependencies, ~300KB
minified. Each effect needs a React wrapper component to bridge `useCurrentFrame()`
to `gsap.seek()`.

**Integration cost.** Moderate. Each GSAP effect is imperative code, not a
declarative component. A new wrapper must be written per effect, and tested
to confirm deterministic frame-by-frame output in headless Chromium.

### 2.2 Remotion Bits (https://remotion-bits.dev/docs/getting-started/)

**What it is.** An MIT-licensed component registry for Remotion, published at
`github.com/av/remotion-bits`. The model is like shadcn/ui: you either install
the npm package or copy individual component source files into your codebase
using `npx jsrepo add`.

**Does it fit this renderer and version?** Yes, exactly. Remotion Bits requires
`remotion >= 4.0.0`, `react >= 18`. This project runs Remotion 4.0.486,
React 19.2.3. The components are standard React using `useCurrentFrame()`,
`interpolate()`, and `spring()` - the same functions the existing MotionGraphics
composition already imports.

**The full component catalogue** (from the Remotion Bits sidebar, fetched
2026-09-06):

Text Animations:
- AnimatedCounter - per-digit rolling with spring physics
- BasicTypewriter - cursor-blinking character reveal
- BlurIn - word-by-word blur-to-sharp entrance
- CharByChar - staggered character animation
- CLISimulation - terminal-style text appearance
- FadeIn - opacity entrance with offset
- GlitchCycle - continuous chromatic-aberration text
- GlitchIn - one-shot glitch entrance
- MatrixRain - Matrix-style character rain
- MultiTextTypewriter - sequential phrase cycling
- SlideFromLeft - horizontal slide entrance
- TypingCodeBlock - syntax-highlighted typing effect
- VariableSpeedTypewriter - typewriter with speed variation
- WordByWord - word-level staggered reveal

Staggered Motion:
- 3DCardStack, EasingsVisualizer, FractureReassemble, GridStagger,
  ListReveal, MosaicReframe

Background Effects:
- ConicGradient, LinearGradient, RadialGradient

Particles:
- Fireflies, FlyingThroughWords, ParticlesFountain, GridParticles,
  Snow, ScrollingColumns

3D Scenes:
- 3DCarousel, 3DElements, Basic3D, CubeNavigation3D, CursorFlyover,
  KenBurns, 3DTerminal, Transform3DShowcase

Hooks: useViewportRect, useScene3D, useCamera, useActiveStep
Utilities: Transform3D, Interpolation, Geometry, Motion, Random

**The AI agent interface.** Remotion Bits ships an MCP (Model Context Protocol)
server launched with `npx remotion-bits mcp`. It exposes two operations through
the MCP protocol: `find` (search the component catalogue by keyword or
capability) and `fetch` (retrieve the full source code of a component). A CLI
equivalent exists: `npx remotion-bits find "counter"` returns matching component
names and descriptions, and `npx remotion-bits fetch animated-counter` returns
the component's TypeScript source. This means the step 4.06 model could, at
plan time, query Remotion Bits for a component that matches what the piece needs,
fetch its source, and include it in the composition - without a human selecting
or authoring anything.

**Licence.** MIT.

**Dependency weight.** Zero new runtime dependencies if copying component source.
Some components (3D scenes) need `@react-three/fiber` and `three`, which this
project does not use. The text and motion components are pure React + Remotion.

**Integration cost.** Low for text and motion components. Each is a self-contained
`.tsx` file that uses the same `useCurrentFrame()` + `interpolate()` + `spring()`
pattern the existing composition already uses. Copying a component and wiring it
into the `DrawnElement` switch is the same pattern used for `counter_roll` in this
commit.

### 2.3 HyperFrames (https://hyperframes.heygen.com/catalog/blocks/chromatic-radial-split)

**What it is.** An Apache-2.0 video rendering framework from HeyGen. Its model
is "HTML in, video out": compose video with standard HTML, CSS, JavaScript, and
WebGL, using `data-*` attributes for timing (`data-start`, `data-duration`).
The chromatic-radial-split block is a WebGL fragment shader that separates and
converges RGB channels radially as a scene-to-scene transition.

**Does it fit this renderer?** No, not without a second renderer. HyperFrames
is a separate rendering engine (`npx hyperframes render`) with its own headless
Chromium instance. Using it here means running two headless browsers per pipeline
run - one for Remotion, one for HyperFrames.

**What it adds that today cannot do.** WebGL fragment shaders for transitions.
The chromatic-radial-split is visually striking. But transitions in this pipeline
go through Fusion (AGENTS.md section 5: "Transitions go through Fusion. Both
other routes are closed."), not through the overlay renderer. The specific
capability HyperFrames offers (shader transitions) is not the motion graphics
layer's job.

**Licence.** Apache 2.0.

**Dependency weight.** `@hyperframes/cli`, `@hyperframes/shader-transitions`.
New Node.js dependency, new CLI binary, new render pipeline.

**Integration cost.** High. Second renderer, second set of timing conversions,
second output format to composite. And for a capability (transitions) that belongs
to a different pipeline step.

## 3. The ceiling

**The renderer is already at the ceiling.** Remotion 4.0.486 runs headless
Chromium and can render anything a browser can draw: CSS animations, SVG,
Canvas 2D, WebGL, React Three Fiber. The ProRes 4444 output path is working and
produces correct alpha. Render speed is ~30 frames/second at 1080x1920 on this
machine (150 frames in ~5 seconds).

**The gap is entirely in what the component asks the renderer to draw.** The
MotionGraphics composition is a React component. Making it draw more
sophisticated graphics is straightforward React/CSS/SVG code. No renderer change,
no pipeline change, no Resolve change.

**What would reach the ceiling:**

1. **Copy text animation components from Remotion Bits** (AnimatedCounter, BlurIn,
   Typewriter, GlitchIn) and wire them as entrance characters or element types.
   These are self-contained `.tsx` files with MIT licence. Each one replaces a
   flat text render with kinetic typography. Cost: a few hundred lines of React
   per component, tested by rendering a clip and looking at it.

2. **Add the remaining vocabulary elements** (list_build, comparison_bars,
   step_counter). Each is a `DrawnElement` switch arm, same pattern as
   counter_roll. Cost: ~50-80 lines of React per element.

3. **Use the Remotion Bits MCP server at plan time.** Step 4.06's model could
   call `npx remotion-bits find` during planning to discover what animation
   components exist, and `fetch` to retrieve source. This shifts the component
   selection from hardcoded vocabulary to dynamic lookup. Cost: one tool call
   in the bridge prompt, and a decision about whether fetched source goes into
   the composition at render time.

**What stands between here and there:**

- **Brand palette and typography choices.** The system renders in whatever
  colour the plan states. A house style - which colours, which typeface weight
  hierarchy, which entrance character is default - is a creative decision the
  captain has not made and the engine should not make.
- **SVG and icon artwork.** GSAP's strongest unique capability (MorphSVG,
  DrawSVG) requires SVG path data that does not exist in the pipeline. The
  engine does not generate or ship artwork.
- **Object tracking.** The `tracked_label` element needs per-frame bounding
  boxes. Step 1.06 (object_segmentation) is unwired because nothing consumes
  masks. Re-wiring it would add GPU memory pressure (SAM 2).

## 4. Automatable versus needs a human

This is the split the captain most needs. A beautiful animation that needs
hand-authoring per episode does not serve a pipeline that runs itself.

### Fully automatable from pipeline data that already exists

These capabilities need no new pipeline measurements and no human input per
episode. The data is already flowing through DAG edges to step 4.06.

| Capability | Data source | Status |
|---|---|---|
| Animated number roll (`counter_roll`) | Speech analysis identifies stated numbers. `data.start_value` and `data.end_value` in the plan. | **Done in this commit.** |
| Entrance transforms (slide, scale, mask, draw) | LLM plan states `entrance` and `exit` character per element. | **Done in this commit.** |
| Title lockup with copy | LLM writes the copy in `runs[].text`. Timing from `start_seconds` / `duration_seconds`. | Already working. |
| Quote card | LLM writes the quotation. | Already working. |
| Progress bar | Computed from timeline position. No LLM decision. | Already working. |
| Frame accents | Computed from safe area. No LLM decision. | Already working. |
| Lower third | LLM writes name and attribution. | Already working. |
| Context stamp | LLM writes the label. | Already working. |
| Stat callout | LLM writes the figure and unit. | Already working. |
| Beat accent (pulse) | LLM times it to a speech moment or beat. | Already working. |
| Pointer annotation | LLM places it at an anchor. | Already working. |

### Automatable with component work, no new dependencies

These need a React component written and a vocabulary entry, but no new pipeline
data and no human per episode.

| Capability | What to build | Effort |
|---|---|---|
| Typewriter text reveal | Copy Remotion Bits `BasicTypewriter`, wire as a new entrance character. | ~100 lines, 1 day. |
| Per-digit spring counter | Copy Remotion Bits `AnimatedCounter`, replace `counter_roll`'s cubic ease-out with per-digit spring. | ~150 lines, 1 day. |
| List build (staggered items) | New element type drawing an ordered list with per-item staggered entrance. | ~80 lines, 1 day. |
| Comparison bars | New element type drawing horizontal proportional bars from `data.values`. | ~60 lines, 1 day. |
| Step counter ("2 of 5") | New element type. The LLM already identifies enumerated structure in speech. | ~40 lines, half a day. |
| Blur-in text entrance | Copy Remotion Bits `BlurIn`, wire as a new entrance character. | ~60 lines, half a day. |
| Glitch text entrance | Copy Remotion Bits `GlitchIn`, wire as a new entrance character. | ~80 lines, half a day. |

### Needs a human per episode or a creative decision once

| Capability | Why it needs a human | What kind of human |
|---|---|---|
| Brand palette | Which colours define the brand. The engine renders whatever the plan states but has no house colour. | Captain, once. |
| Default entrance character | Whether the house style prefers `slide` or `fade` or `scale` as the default. | Captain, once. |
| Typography choices | Whether the house style wants Montserrat or something else. Whether `display` is 56px or 72px. | Captain, once. |
| SVG illustrations / icons | Artwork the engine does not generate. Needed for GSAP's MorphSVG, DrawSVG, or a channel bug. | Designer, per asset. |
| Object-tracked labels | Needs SAM 2 or similar for per-frame bounding boxes. Step 1.06 is unwired for memory reasons. | Engineering decision (GPU budget). |
| Per-episode authored animations | A GSAP timeline sequence or a custom Remotion component written for one specific episode. Review cost per episode. | Editor or LLM with visual review. |

## 5. What was rendered and where the files are

All renders are in `data/vep-graphics-fidelity/`:

| File | What it is |
|---|---|
| `baseline_render.mp4` | 5s H.264 render of the pre-change component. All nine drawable elements, all using opacity-only fades. |
| `baseline_props.json` | The props file that produced it. |
| `baseline_frame75.png` | Frame 75 of 150. All elements on screen at once. |
| `enhanced_render.mp4` | 6s H.264 render of the post-change component. counter_roll animating 0 to 175B, title with slide entrance, context_stamp with mask entrance, quote_card with draw entrance, lower_third with slide entrance. |
| `enhanced_props.json` | The props file that produced it. |
| `enhanced_frame50.png` | Frame 50 of 180. Counter mid-roll showing 126,321,842,314, title sliding down, context stamp masked in. |
| `enhanced_frame140.png` | Frame 140 of 180. Quote card with draw (blur-defogging) entrance, lower third sliding in with backdrop blur. |

To render either clip yourself:
```sh
cd remotion-subtitles
npx remotion render MotionGraphics output.mov \
  --props ../data/vep-graphics-fidelity/enhanced_props.json \
  --codec prores --prores-profile 4444 --image-format png --transparent
```
