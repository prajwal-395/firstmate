---
name: ren-animation
description: >
  Ren's animation language and how to direct it. The renderer-neutral
  Graphics IR (target, property, from, to, start, duration, easing),
  scene decomposition, timing, pacing, easing, typography fitting,
  camera moves, transitions, shaders, audio sync and output
  verification. Use when planning, directing or reviewing any
  programmatic motion Ren produces - motion graphics, subtitle
  animation, timed text, bookend cards, animated explainers - no
  matter which renderer draws it. For backend-specific lowering see
  renderers/.
---

# ren-animation

Ren owns the animation language. Renderers - Remotion, HyperFrames,
FFrames, and later Fusion - are execution backends that lower Ren's
Graphics IR to their own scene model. This skill teaches the language
and the craft; `renderers/` teaches each backend's lowering and its
known limits.

## The Ren Graphics IR

Every animation Ren plans is renderer-neutral:

```
Animation: target: <node>, property: <property>, from: <value>, to: <value>,
           start: <seconds>, duration: <seconds>, easing: <easing>
```

- **target** - a node id in the scene graph (a group, text run, shape,
  image, camera, shader uniform).
- **property** - one of the animatable channels: `translate`, `rotate`,
  `scale`, `skew`, `opacity`, `blur`, `color`, `gradient`, `path`,
  `mask`, `camera.*`, `shader.*`.
- **from / to** - the value range. Numbers, colours, transforms.
- **start / duration** - seconds on the composition clock, not frames.
  The renderer lowers to its own frame grid.
- **easing** - one of `linear`, `ease`, `cubic-bezier(x1,y1,x2,y2)`,
  `spring {mass, stiffness, damping}`, `steps(n)`.

A scene is a tree of nodes. Each node carries `transform`, `opacity`,
`bounds`, `z-index`, `mask`, `clip`, `blend`, `timing` and an
`animations[]` list. Node kinds: `Group`, `Text`, `Shape`, `Image`,
`Video`, `SVG`, `Shader`, `Camera`, `Mask`, `Filter`.

Above the IR sits Ren's semantic vocabulary - `lower_third`,
`quote_card`, `comparison_bars`, `animated_explainer`, `counter`,
`diagram`, `callout`, `progress`, `word_reveal`, `full_frame_card`,
`transition`, `brand_intro` (AGENTS.md 16). The plan names a semantic
element; the IR is how it moves.

## When to reach for this skill

- You are planning a motion graphics segment, subtitle animation,
  timed-text moment, bookend card or animated explainer and need to
  decide WHAT moves, WHEN and HOW.
- You are reviewing a rendered overlay or full-frame animation and
  need to judge whether the motion reads.
- You are choosing parameters for an entrance, exit, stagger,
  camera move or transition.

Do NOT reach for this skill to learn a backend's API - see
`renderers/`. Do NOT reach for it to judge a footage cut - that is
`ren-co-editor` and `verify_timeline`.

## Routing

| Want to… | Read |
|---|---|
| Break a composition into scenes, lay out elements, respect safe areas | `references/composition.md` |
| Choose timing, pacing, easing, staggering, word reveals | `references/motion.md` |
| Size and fit type, line height, weights, wrapping | `references/typography.md` |
| Direct a camera move, zoom, reframe, Ken Burns | `references/camera.md` |
| Choose and time a scene transition | `references/transitions.md` |
| Use a GPU shader layer (keying, grade, effect) | `references/shaders.md` |
| Sync motion to audio - beats, voice, SFX | `references/audio-sync.md` |
| Verify a rendered animation - contact sheets, onion skin, stills | `references/verification.md` |
| Lower the IR to a specific backend | `renderers/fframes.md`, `renderers/hyperframes.md`, `renderers/remotion.md` |

## Principles that hold across every backend

These are renderer-neutral. Each backend lowers them differently;
the craft is the same.

1. **One idea per scene.** A scene is one visual thought. If two
   things must read at once, they are two scenes or one scene with
   one focal element.
2. **One focal movement at a time.** Secondary elements move less and
   later. Everything moving at once reads as noise.
3. **Animate transform and opacity, never layout.** Position, scale,
   rotation and opacity are compositor-friendly. Width, height,
   margin and font-size tweens cause jitter and reflow.
4. **Enter with ease-out or a spring; exit with ease-in, faster.**
   Entrances take 0.3-0.6 s; exits 0.2-0.3 s. The viewer reads an
   arrival, not a departure.
5. **Stagger related items by 60-120 ms.** Words and letters by
   20-40 ms. A group arriving as one beat reads as intentional.
6. **Give the viewer time to read.** About 3 words per second plus
   1 s to notice the text, measured from when the motion settles.
7. **Respect the safe area.** Nothing important inside the outer
   8-10% of the frame. Ren's safe-area fractions come from
   `library/tools/safe_area.py` - never a hardcoded margin.
8. **The final frame is part of the animation.** Hold readable
   states long enough to see. Do not end on black unless asked.
9. **Deterministic.** No wall-clock, no unseeded randomness, no
   network at render time. The same timeline time always produces
   the same frame.

## What this skill never does

- No house look, brand colour, palette or copy. Those are project
  data declared in the brand template and creative brief.
- No hardcoded creative magnitudes. The numbers in the references
  are measured starting points and ranges, not defaults. A project
  that declares its own values wins.
- No backend API tutorials. The IR is the contract; `renderers/`
  maps it to each engine.

## Licensing

The craft knowledge in this skill is adapted from two sources,
used under their licences:

- **FFrames** (MIT) - `fframes/skills/fframes-video/` - pacing,
  easing presets, typography fitting, review checklist, shader usage.
  Adapted, not copied. Licence: `LICENSE.txt` in the FFrames repo.
- **HyperFrames** (Apache-2.0) - `hyperframes-animation`,
  `hyperframes-keyframes`, `hyperframes-core` skills - atomic motion
  rules, keyframe contract, transition energy mapping, camera
  mechanisms. Adapted, not copied. Licence: Apache-2.0.

Ren's own Remotion compositions (`remotion-subtitles/`) and
HyperFrames compositions (`hyperframes/compositions/`) are this
repo's code and are the primary source for how Ren lowers the IR
today.
