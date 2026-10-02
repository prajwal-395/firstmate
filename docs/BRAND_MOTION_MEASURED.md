# Brand motion in a Remotion render: measured

2026-09-08. The gap: no Remotion composition reads a video file, so the
captain's real brand motion could be referenced but never composited
into a render. Both files live in the Lucie
`_shared/brand-assets/motion/` folder. Nothing here was run against the
captain's Resolve project.

## 1. What the files are (ffprobe, not filenames)

| file | geometry | codec / pix_fmt | rate | frames | duration | audio |
|---|---|---|---|---|---|---|
| `logo_reveal.mov` | 1080x1920 | ProRes 4444 / `yuva444p12le` | 30/1 | 90 | 3.000s | pcm_s16le |
| `transition_bumper.mov` | 1080x1920 | ProRes 4444 / `yuva444p12le` | 30/1 | 45 | 1.500s | pcm_s16le |

Already delivery geometry (1080x1920), already alpha (measured through
`transition_overlay.measure_element`: bumper peak mean alpha 1.358/255,
0 fully-opaque frames - a sparse stamp, not a cover). Both carry an
audio stream each, which is why the slot requires `muted` stated.

## 2. The decode gate: ProRes never reaches a Remotion frame

Procedure: the bumper copied to a scratch dir beside a VP9 WebM
transcode, served over HTTP with Range support, loaded in two `<video>`
elements in the same Chrome build Remotion renders with, driven through
CDP. (First attempt without Range support made both files unseekable -
`seekable` stayed `[0,0]` on a fully-buffered file - so the server was
replaced before any verdict was read.)

| probe | ProRes `.mov` | VP9 WebM transcode |
|---|---|---|
| `canPlayType('video/quicktime')` | `""` | n/a |
| `videoWidth` x `videoHeight` | 0 x 0 | 1080 x 1920 |
| `readyState` | 4 (the AUDIO track) | 4 |
| seek to 0.75s | clock advances, still 0x0 | paints: 3,388 ink px of 129,600 with live alpha |
| `duration` | 1.5 (container) | 1.508 |

The container parses and the audio clock even seeks, but there is NO
decodable video track: an `<OffthreadVideo>` on the raw file renders
nothing, silently. The VP9 transcode paints picture with alpha. So the
slot stages a same-rate VP9 WebM mezzanine (`yuva420p`,
`-auto-alt-ref 0`, constant-quality) and the composition plays THAT.
Same rate keeps the transcode mechanical - no frame created or dropped -
and the codec is forced, because VP9-in-WebM is the only
browser-decodable format that carries alpha.

A second decoder finding, same session: ffmpeg's NATIVE `vp9` decoder
drops WebM alpha (`alphaextract` sees no plane on a file Chrome paints),
while `libvpx-vp9` recovers every frame - 45 of 45 on the bumper
mezzanine. So WebM sources are alpha-measured through libvpx
(`transition_overlay.measure_alpha(..., video_decoder=...)`); with the
default decoder a file the renderer paints would be refused as drawing
nothing. One instrument, not two.

## 3. The conform: three strategies, one built by hand, one not built

The asset is 30fps; the timeline is 24000/1001. `library/tools/brand_motion.py`
names all three in `CONFORM_STRATEGIES` and `require_conform` refuses
anything else, with no default.

**`native_sample` - wall-clock exact, cadence lossy.** Remotion seeks
each composition frame to its timestamp and the browser returns the
nearest source frame; nothing is blended and every shown pixel is
authored. Computed exactly (nearest-frame mapping, 30 -> 24000/1001):

- bumper: 36 timeline frames show 36 distinct source frames; 9 of 45
  are skipped - every 5th (src frames 2, 7, 12, ... 42), max jump 2.
- logo: 72 timeline frames; 18 of 90 skipped on the same 5-step.
- First 12 timeline frames show source frames
  0, 1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14.

Duration is exact (36 frames at 23.976 = 1.5015s); the motion takes a
regular stutter-step. Whether that step is character or cheapness is
the taste call.

**`blended_conform` - cadence smooth, pixels rewritten.** Pre-conform
with frame blending synthesises every output frame: no skips, but the
authored glow softens and fast motion ghosts. Which blender
(frame-averaging vs motion-interpolated) and how much blend IS the
look, so the strategy is named, costed, and NOT BUILT - declaring it
raises `ConformNotBuilt` carrying the two missing values. That refusal
is the taste question, stated as code.

**`resolve_native` - no Remotion render.** Place the original at its own
rate on the Resolve timeline (`transition_overlay` asset mode; the
placer already converts 36 timeline frames to 45 source frames). The
route that already works today, named here so the enumeration is
complete rather than a menu of one.

## 4. The fixed length is not a fourth question

The fear was that a 3.0s / 1.5s fixed asset re-times any reel it
touches. It does not, because the slot never places anything - and both
placements already have rulings:

- OVER the cut, the element is additive
  (`transition_overlay.TIMING_IS_ADDITIVE`): the reel keeps its length,
  its keep ranges and every caption binding. The bumper covers; nothing
  inserts.
- AS a card at head/tail, it concatenates (`content.bookends` via
  `mesh_spine`): the reel absorbs the logo's 3.0s by declaration and
  the cursor shifts by exactly that. Declared shape, not drift.
- Trimming an asset to fit is refused on every route (`OverlayDoesNotFit`,
  the duration-mismatch check, and now `brand_motion_props`, which
  renders the WHOLE file and offers no trim parameter). A reel that
  needs a shorter sting needs a shorter asset - an authoring decision,
  not a render flag.

## 5. What was built, and what remains the captain's

Built without a taste call (`library/tools/brand_motion.py`,
`BrandMotion` in `remotion-subtitles/`, `tests/unit/captions/test_brand_motion.py`):
measure the file, stage a same-rate mezzanine the renderer can read,
verify the mezzanine kept every frame and its alpha, refuse ProRes
staged raw / undeclared conform / unstated sound / mismatched geometry.
After this lane, the remaining questions are purely the captain's:

1. **Conform per asset** - `native_sample` (authored pixels, stepped
   cadence), `blended_conform` (smooth cadence, rewritten pixels - then
   also: which blender, how much blend), or `resolve_native` (no
   Remotion render at all)?
2. **Placement per asset** - bumper over which cuts (anchor + seams are
   the gesture), logo as intro card (+3.0s absorbed) vs overlay vs
   Resolve-only?
3. **Sound per render** - both files carry audio; the props require
   `muted` true/false stated, and the VALUE is theirs.
