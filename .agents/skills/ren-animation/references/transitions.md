# Transitions

How two scenes connect. A transition tells the viewer how the scenes
relate - "this continues", "next point", "drift with me". Choose the
transition from what the content is doing emotionally, not from what
is technically convenient.

## The contract

1. **Every scene change uses a transition.** A jump cut with no
   transition reads as a mistake unless the cut is the point.
2. **Every scene has an entrance.** Elements animate IN - opacity,
   position, scale. No scene pops fully-formed onto the screen.
3. **The outgoing scene does not exit.** The transition IS the exit.
   Outgoing content must be fully visible when the transition starts.
   Do not fade the outgoing scene out and then run the next scene's
   entrance - that is a jump cut with a dip, not a transition.
4. **Outgoing and incoming animate at the same time T.** The motion
   IS the handoff. Both scenes' tweens share one timeline position.

## Energy mapping

Pick ONE primary transition (60-70% of scene changes) plus 1-2
accents. Never use a different transition for every scene.

| Energy | Primary | Duration | Easing |
|---|---|---|---|
| Calm (wellness, brand story, luxury) | blur crossfade, focus pull | 0.5-0.8 s | sine.inOut, power1 |
| Medium (corporate, explainer) | push slide, staggered blocks | 0.3-0.5 s | power2, power3 |
| High (promos, sports, launch) | zoom through, overexposure | 0.15-0.3 s | power4, expo |

## Mood mapping

| Mood | Transitions | Why |
|---|---|---|
| Warm / inviting | light leak, blur crossfade, focus pull | soft edges, warm washes |
| Cold / clinical | squeeze, zoom out, blinds, shutter | mechanical, compressed |
| Editorial / magazine | push slide, vertical push, diagonal split | like turning a page |
| Tech / futuristic | grid dissolve, staggered blocks, blinds | data, structure |
| Tense / edgy | glitch, VHS, chromatic aberration | instability, breakdown |

## Mechanisms

- **Cross-fade** - outgoing fades 1 to 0, incoming fades 0 to 1,
  over a shared window. The overlap is added to the scene's duration.
  Says "this continues."
- **Push** - outgoing slides -80 px, incoming slides from +80 px.
  Says "next point."
- **Blur crossfade** - outgoing blurs and fades, incoming un-blurs
  and fades in. Says "drift with me."
- **Whip pan** - fast horizontal blur that snaps to the next scene.
  High energy only.

## Implementation rules

- Use `fromTo` with explicit from-states, not `from`. A `from` tween
  animates TO the current CSS value, so pairing it with a CSS
  `opacity: 0` is a 0-to-0 noop and the element never appears.
- The transition window is a shared timeline position. Both scenes'
  tweens start at the same T.
- Check the transition with an onion-skin view (see `verification.md`)
  - a transition with an empty or flashing frame is broken.
- The first frame of a social-format video must not be blank. A
  fade from black at the very start loses viewers.
