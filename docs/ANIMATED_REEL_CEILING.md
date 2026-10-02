# Animated reel over an existing audio spine: what the pipeline could draw

*Built 2026-09-08, firstmate lane `vep-animated-reel-from-existing-spine`.
One full-frame animated reel over geo-podcast reel 01's audio spine
(`geo-is-comprehension-not-position`, approved, 13 keep ranges). No Resolve
was opened, no reel was rebuilt, no pipeline file was changed. All working
material lives outside the repo (`/tmp/anim-reel/`); this document is the
committed account.*

## The render

- **Spine:** reel 01's 13 keep ranges, cut from `LCATL0011.MXF` (Craig) and
  `LC4930.MXF` (Akshita) and laid on the reel's own timeline clock, gaps
  included - reel seconds 0.181..44.745 of the master, 44.564s of audio.
- **Picture:** 7 `full_frame_card` declarations, all `placement: head`,
  laid back-to-back by the pipeline's own `plan_reel_cards` with
  `body_frames=0` - frames 0..265..524..665..755..842..934..1068, abutting
  exactly, 1068 frames @ 23.976fps = 44.545s. Rendered by the pipeline's
  own `render_reel_cards` (FullFrameCard, opaque ProRes 4444, no
  `--transparent`), validated first by the pipeline's own
  `declared_elements` gate. Audio trimmed per card to the card's exact
  frame authority (`frames/23.976`) and muxed, then concatenated.
- **Files (outside the repo):** `/tmp/anim-reel/`
  `lucie_animated_reel01_master.mov` (ProRes 4444 + pcm_s24le stereo,
  1080x1920, 1068 frames, video 44.544678s / audio 44.544604s) and
  `lucie_animated_reel01_h264.mp4` (viewing proxy, 1.8 MB).
- **Cost:** 44.4s wall clock for the 7 Remotion renders + 7.5s for
  audio/assembly on the captain's machine. A 60-second reel is inside the
  render budget at roughly 1x real time.

## The brand material, and where each value came from

Nothing was invented. Every declared value names its source:

| Declared | Value | Source |
|---|---|---|
| ground | `#000000` | measured off `motion/logo_reveal.mov` and `transition_bumper.mov` (both play on black) |
| emphasis | `#FFAA4D` | Sun Orange, `Lucie Brand Colors.pdf` (also the C in the reveal) |
| body | `#FBF0B8` | secondary cream, style guide (also Craig's own caption accent in `project.yaml`) |
| typeface | Proxima Nova, `font_file: ProximaNova.otf` | `BRAND GUIDELINES/Proxima Nova.otf`, via `_shared/brand-assets/remotion-brand/` |
| motion | `mask` (wipe, like the bumper), `typewriter` (speech-like), `draw` + `fade` (resolve, like the reveal) | vocabulary-native characters matched to the two `.mov` files by watching them |
| copy | the reel's own transcript, verbatim, per card; speaker eyebrows | quotation, never authored |

## The asset mechanism (no hand-editing of paths)

`remotion_brand_linker.find_brand_assets` resolves `_shared` on its own:
with the project's own `brand_assets/` empty, `prep_remotion(project_folder=
geo-podcast)` returned `.../lucie/_shared/brand-assets/remotion-brand` and
staged `ProximaNova.otf` + the logo SVGs/PNG into `public/brand/` - real
output, 5 files. The declaration named `font_file: ProximaNova.otf`, which
`static_font_path` maps to `brand/ProximaNova.otf`. Staging was removed
afterwards (`cleanup_remotion`); the captain's project and `_shared` were
only ever read.

## It draws - the check, run not described

- A canary re-render with the staged font removed REFUSED instead of
  substituting: `FullFrameRenderError: ... Failed to load project font
  Proxima Nova from brand/ProximaNova.otf ...` (404 on
  `public/brand/ProximaNova.otf`). The 7 successful renders therefore drew
  in real Proxima Nova, not a fallback.
- Ink on the finished master (mid-hold frame per card, pixels > 12/255):

| card | frame | ink | rows |
|---|---|---|---|
| 1 | 132 | 2.49% | 710-1009 |
| 2 | 394 | 2.34% | 736-983 |
| 3 | 594 | 1.50% | 682-1035 |
| 4 | 710 | 1.65% | 726-991 |
| 5 | 798 | 1.83% | 726-991 |
| 6 | 888 | 1.26% | 764-953 |
| 7 | 1001 | 3.63% | 626-1077 |

Bounded text bands on every card, not full-frame noise. Four frames were
also read visually (cards 1, 2-entrance, 3 two-speaker turn, 7 closer).
- Audio: speech at mean -25.9 dB / max -4.5 dB inside card 1, digital
  silence (-91 dB) in the inter-span gap the reel itself keeps, closing
  words present at the end. Timing preserved, silences included.
- An assembly bug of mine, caught by the frame count: muxing with
  `-shortest` dropped one frame each from cards 1, 5 and 7 (1065 vs 1068),
  because the audio trim was microseconds shorter than the video. Fixed by
  muxing to the card's exact frame authority without `-shortest`; the
  master reads back 1068 frames.

## What the pipeline refused, verbatim

1. A whole-span single card (44.6s > the 30s cap):
   `FullFrameDeclarationError: full_frame_elements[1] holds for 44.6s. Past
   30.0s it is not a card in front of a reel, it is a segment the plan
   should be choosing as content - the same reading
   bookends.MAX_BOOKEND_SECONDS records.`
2. Mid-reel placement:
   `FullFrameDeclarationError: full_frame_elements[1] has placement='middle';
   a card sits at the head or the tail of the reel. There is no default,
   because where a card sits is an editorial decision, and no mid-reel
   position exists because a card between two keep ranges lands inside a
   sentence.`
3. The brand typeface named with no deliverable file:
   `FullFrameDeclarationError: full_frame_elements[1] declares font_family=
   'Proxima Nova' with font_file=None, which library/tools/render_fonts.py
   cannot deliver. A substituted face is a valid picture of the right size
   that nothing downstream can tell apart.`
4. Model-authored copy (`bind: hook_title`):
   `FullFrameDeclarationError: full_frame_elements[1].runs[1] binds to
   'hook_title', which the pipeline does not produce for a reel. Known
   bindings: opening_line, reel_number, speakers.`
5. An empty quotation at plan time (`opening_line` with no opening facts):
   `FullFrameDeclarationError: full_frame_elements[1] binds a run to
   'opening_line' and reel 1 has nothing there ... An empty run is not
   drawn and not substituted - the card refuses instead.`

Refusals 1-4 came out of `declared_elements`; 5 out of `plan_reel_cards`.
All five are correct behaviour and were left in place.

## What the vocabulary could not express at all

- **The logo.** `FullFrameCard` props carry no image slot, so the staged
  SVGs/PNGs the linker made reachable are undrawable by this composition.
  No eyebrow text substitutes for the wordmark.
- **The existing brand motion.** No Remotion composition reads a video
  file, so `logo_reveal.mov` / `transition_bumper.mov` can only be
  referenced, never composited - and both are 30fps against a 23.976
  timeline, with fixed 3.0s/1.5s lengths that would re-time any reel they
  were concatenated onto.
- **Per-word sync.** `typewriter`/`mask`/`draw` reveals are time-based
  across the card; no composition takes word timestamps, so animation can
  sit inside a card's own seconds but never land on a word.
- **The bumper's tracked-out caps wordmark.** There is no letter-spacing
  prop; `uppercase` was the closest expressible thing.
- **The reveal's draw-on language.** `draw` is a scale-and-blur resolve,
  not a stroke draw; rays, particles and bulb-base builds have no axis.
- **A whole-span element.** The roster has one entry, a card, capped at
  30s and placed head-or-tail because a card between keep ranges cuts a
  sentence. Covering the span took seven head cards back-to-back with
  every boundary on the reel's own edit points - the arithmetic holds, but
  the vocabulary has no word for what was built. A mid-reel card over
  footage stays refused, correctly.

## Tests

`tests/unit/captions/test_full_frame_element.py`: 57 passed (the module this build drove;
no library file was changed, so no wider tier was warranted). The full
suite was not run, per the brief.

## Tests removed 2026-09-23 by the captain's ruling (record, not hedge)

The word-cue routing gap this document found ("no composition takes word
timestamps") and the whole-span vocabulary gap ("no word for what was
built") lose their pixel proofs with the 2026-09-23 deletion. The full
capability-by-capability record lives in
`docs/RENDER_CAPABILITY_CEILING.md` §10; the animated-reel lines are:

- Nothing now verifies that a word-paced reveal lands on the word
  (`typewriter`/`mask`/`draw` word-sync cue routing).
- Nothing now verifies that each segment of a whole-span element draws
  its own copy, or that a segment image reaches pixels.
- Nothing now verifies that a staged whole-picture scene renders through
  the pipeline's own `npx remotion render` and measures on frames.
- Nothing now verifies cued draw emphasis and current-word colour
  emphasis on pixels.
