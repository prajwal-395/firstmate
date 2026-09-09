# Grade variants for the captain to choose from - Reel 09 stills

Task `vep-grade-variants-to-choose-from`. Branch `fm/vep-grade-variants-to-choose-from`.
No pipeline code was changed. Nothing was written into `project.yaml` or any
brand template. No variant is recommended as the answer; opinions beside the
images are in section 5, the pick is the captain's.

Scope, as the captain set it 2026-09-09: whatever is chosen lands in
`lucie/geo-podcast`'s `project.yaml` only. It does not become a house default
and does not go into the Lucie brand template. The next project still starts
neutral and still asks.

## 0. The files

All in `data/vep-grade-variants/`, all 1080x1920 (the delivery frame):

| File | What |
|---|---|
| `v00_neutral.jpg` | control: the ungraded picture |
| `v01_warm_lift.jpg` | warmer lift |
| `v02_cool_contrast.jpg` | cooler, contrast-forward |
| `v03_soft_cream.jpg` | soft matte: milky blacks, gentle bloom |
| `v04_teal_split.jpg` | split-tone vivid: warm skin over teal shadows |
| `contact_sheet.jpg` | all five side by side, labelled (preview only, 360px each) |

Judge from the full-resolution files, not the contact sheet.

## 1. The frame chosen, and why

`LC4932.MXF @ 1435.0s` - Akshita mid-sentence, inside Reel 09 moment 9
(`your-website-is-only-20-percent`, master 631-693s). She speaks the reel's
key line here ("that's only 20% of what it does"), so the graded skin the
captain judges is the reel's most-held face.

Why this frame over the Craig angle (`LCATL0013.MXF @ 1424s`): at the 2.30
punch-in her shot keeps skin tone (face, arms, hands), green plant, dark
geometric wall and table texture inside the TV screen. The Craig framing
loses the lamp and the plant at this punch-in and leaves face plus flat
wall - less background for a grade to work against.

The compromise, stated: no source frame contains the TV-frame bezel, because
the bezel is not footage - it is the `TV 4k.png` project asset composited on
V2 above the picture. So the bezel is composited, not photographed: rotated
upright, cover-scaled onto 1080x1920, alpha-over UNGRADED last, exactly as
the timeline layers it (V1 picture, V2 frame, captions above both). The grade
acts on the picture underneath; the bezel's own baked-in black is constant
across all five, which is also the answer to whether it constrains the grade:
it does not tint anything, it only boxes the picture (opaque rails top and
bottom of the delivery frame at this rotation).

## 2. How the stills were built (so the follow-up can reproduce or refute)

- V1 placement uses the engine's own geometry, not hand numbers:
  `tv_frame.screen_window_rect` (window x 18-1061, y 259.5-1661),
  `window_cover_zoom` (minimum 2.3070, drawn zoom = max(2.30, that)),
  `reel_look.punch_in_properties` with the subject at face fractions
  (center_x 0.51, center_y 0.376, measured off the extracted frame) giving
  Zoom 2.3070 / Pan -25.416 / Tilt 0.25, placed with `reel_framing`
  arithmetic (fit x zoom scale, centre at frame/2 + pan/tilt). The drawn
  picture rect (-731, 260, 1761, 1662) covers the engine's window to a pixel.
- The bezel rotation (90 CW) was verified, not assumed: alpha inside the
  computed window averages 0.69 (transparent) against 72.7 whole-frame.
- Grade order: CDL first (as `SetCDL` on the timeline item), then the Fusion
  chain in node order - pivot contrast, glow, grain, vignette.
- The Fusion nodes are APPROXIMATED in numpy (Glow = thresholded blur
  screen-blended; Grain = gaussian noise; Vignette = smoothstep ellipse
  toward black; Contrast = pivot gain around mid-grey with 0 neutral).
  The DECLARATION values below are exact, and every one was validated
  through `house_look.resolve_look` at render time - a partial declaration
  refuses by name, so whatever the captain picks is directly declarable
  under `style.house_look` with no completion needed.
- No captions are drawn (they sit above both layers and are unaffected).

## 3. The value sets - the captain is choosing these numbers

Every variant declares every slot. Zero-effect values (glow gain 0, grain
power 0, vignette blend 0 on v00) are explicit "not drawn", not defaults.

### v00 - neutral (control)

```yaml
name: v00_neutral
cdl: {slope: [1.0, 1.0, 1.0], offset: [0.0, 0.0, 0.0], power: [1.0, 1.0, 1.0], saturation: 1.0}
contrast: 0.0
glow: {gain: 0.0, threshold: 0.75, size: 3.5}
grain: {power: 0.0, size: 1.5}
vignette: {blend: 0.0, soft: 0.35}
```

### v01 - warm lift

```yaml
name: v01_warm_lift
cdl: {slope: [1.04, 1.0, 0.94], offset: [0.015, 0.008, -0.005], power: [1.0, 1.0, 1.02], saturation: 1.08}
contrast: 0.08
glow: {gain: 0.25, threshold: 0.70, size: 4.0}
grain: {power: 0.25, size: 1.5}
vignette: {blend: 0.22, soft: 0.40}
```

### v02 - cool contrast

```yaml
name: v02_cool_contrast
cdl: {slope: [0.96, 1.0, 1.06], offset: [-0.005, 0.0, 0.012], power: [1.0, 1.0, 0.98], saturation: 1.05}
contrast: 0.16
glow: {gain: 0.15, threshold: 0.78, size: 3.0}
grain: {power: 0.30, size: 1.2}
vignette: {blend: 0.30, soft: 0.35}
```

### v03 - soft cream

```yaml
name: v03_soft_cream
cdl: {slope: [1.02, 1.0, 0.97], offset: [0.02, 0.015, 0.01], power: [1.0, 1.0, 1.0], saturation: 0.95}
contrast: 0.0
glow: {gain: 0.35, threshold: 0.60, size: 5.0}
grain: {power: 0.45, size: 2.0}
vignette: {blend: 0.15, soft: 0.50}
```

### v04 - teal split

```yaml
name: v04_teal_split
cdl: {slope: [1.03, 1.0, 0.96], offset: [-0.01, 0.005, 0.02], power: [1.0, 1.0, 1.0], saturation: 1.12}
contrast: 0.12
glow: {gain: 0.20, threshold: 0.72, size: 3.5}
grain: {power: 0.35, size: 1.5}
vignette: {blend: 0.35, soft: 0.30}
```

## 4. What to check before the pick lands in project.yaml

Three things the stills cannot prove, for the follow-up that applies the answer:

1. **Contrast neutral point.** The stills treat declared `contrast` as a
   pivot gain with 0 neutral. The engine passes the value verbatim to
   Fusion's `BrightnessContrast.Contrast`, whose own neutral is 1.0 by
   Fusion's documentation - no declaration carrying contrast has ever been
   delivered through this path (project 001 declares no look), so if the
   first graded timeline render comes back flat, the engine must emit
   `1.0 + c`, not `c`. Verify on the timeline before writing the value in.
2. **Grain at reel scale.** At powers 0.18-0.30 the grain was visible only
   on close inspection; the set was re-rendered at 0.25-0.45, where it reads
   as fine texture on smooth gradients (checked on a 250px chair crop) while
   staying out of the way on skin. Even so: on a phone at arm's length the
   grain choice will be felt more than seen, and the XAVC source already
   carries its own noise. If the captain wants OBVIOUS texture, none of
   these five offers it.
3. **Stills are previews, Resolve is the verdict.** Glow spread, grain
   character and vignette falloff follow Fusion's nodes, not numpy. The
   numbers transfer exactly; the texture transfers approximately.

## 5. Opinions, beside the images (choosing is still the captain's)

- v00 looks wrong for the series in the way ungraded Sony always does:
  greenish shadows, plasticky skin. It is here as the control, not as a
  candidate - picking it means "no grade", which the engine already does.
- v02 is the most phone-legible: the snap separates Akshita from a dark
  set that otherwise swallows her hair. But it is also the coldest, and
  the set reads corporate rather than warm-conversational.
- v01 flatters skin best and keeps the set friendly; its weakness is the
  blacks, which go slightly milky next to v02/v04.
- v03's bloom is lovely on the practicals and terrible nowhere, but the
  lifted blacks will fight the pure-black bezel rails - the picture's
  blacks visibly float above the frame's blacks. If v03 wins, consider
  whether the offset lift survives contact with that bezel.
- v04 has the most point of view and the strongest vignette (0.35); on a
  talking head that never moves, a heavy vignette is a commitment across
  every reel. It is my favourite single image and the riskiest series
  choice.
