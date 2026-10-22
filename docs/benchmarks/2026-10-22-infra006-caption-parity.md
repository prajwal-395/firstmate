# INFRA-006, lane C - the caption layer on the native renderer, measured

**Measured on:** 2026-10-22, macOS (Apple M5), repository at `f7e6f948e`
(detached worktree of `video-editing-pilot`).
**Population:** the geo-podcast project's own **1278** shipped caption
cards - the props file step 4.05 wrote and the `.mov` artefact it
shipped, under `pipeline_output/steps/4_05_render_subtitles/`. Read
only; every render landed in a scratch folder, and nothing in the
captain's project was written.
**Metric:** ink IoU per frame (alpha > 16), averaged over the card, of
the card **as the engine draws it** against the artefact the project
shipped. The measure is the one
`library/tools/caption_parity_gate.py` makes at publish time, so the
bench and the gate measure the same thing.
**Bench:** `docs/benchmarks/2026-10-22-infra006-caption-parity.py`.
**Record:** `tests/fixtures/native_caption_parity/cards.json`.

## The headline

The lane's claim was **24/24 shipped captions reproduce at IoU >= 0.90
with the native renderer, 1.33x faster per card**. Measured:

| Claim | Measured |
|---|---|
| 24/24 shipped captions reproduce at IoU >= 0.90 | **24/24** on the 24-card sample, **96/96** on the 96-card sample |
| 1.33x faster per card | **1.72x** (mean per card, 24 cards, same machine) |

The native renderer reproduces the delivered caption pictures better
than the browser does today: the reference engine manages **23 of 24**
on the same cards, and the one it cannot reproduce is a card the native
renderer reproduces at 0.93 - because the browser's word-gap rule
changed on 2026-10-17, after those cards shipped.

## What changed

The caption layer already drew through the native renderer
(`library/tools/graphics_renderer.py`: FFrames is the default in both
editions wherever its binary resolves). What it did NOT have was a
check: the engine is part of the caption render's reuse key
(`step_4_05_render_subtitles.step._reuse_key`), so switching engines
re-renders every card **over** the artefact the project shipped - the
filename carries the drawing digest, not the engine - and nothing
compared the two pictures. The engine that drew the new card could
differ from the engine that drew the old one, and the delivered video
would change without a word in the run's record.

Now the caption layer draws through the native renderer **behind a
parity gate** (`library/tools/caption_parity_gate.py`):

* the card is drawn to a **candidate** path, never over the artefact
  that shipped - the shipped artefact is never at risk, not even for
  the second between the render and the comparison;
* with an artefact on disk, the two are compared by INK, frame by
  frame, at the floor the motion-graphics parity measurement used
  (alpha > 16, mean per-frame IoU, **0.90**);
* within the floor the card is published; **below the floor it is
  refused** - the candidate is discarded, the shipped artefact stays,
  and the refusal is said out loud and recorded in the segment's own
  entry (`parity_gate`) and in the render ledger.

Fail-closed, and only one thing is refused: a card whose picture would
change. A card whose drawing content changed (a caption rewrite) has a
different content digest, so it has a different name and nothing to
compare against - the gate does not stand in the way of a caption the
captain asked to change.

## Parity, before and after

The gate does not change any pixels, so the parity of the native
render is the same before and after; what changes is **what gets
published**. The table is the native render against the shipped
artefact, which is the gate's own measure.

| Measure | Sample | Before | After |
|---|---:|---:|---:|
| shipped captions within ink IoU 0.90 | 24 | 24/24 | **24/24** |
| shipped captions within ink IoU 0.90 | 96 | 96/96 | **96/96** |
| mean ink IoU | 24 | 0.9438 | 0.9438 |
| lowest card | 24 | 0.9145 | 0.9145 |
| lowest card | 96 | 0.9057 | 0.9057 |
| cards the gate would refuse | 24 | 0 | **0** |
| cards the gate would refuse | 96 | 0 | **0** |

The "after" column is the same measurement with the gate in place: on
this population the native renderer reproduces every sampled card to the
floor, so the gate refuses none of them and the layer moves to the
native renderer with the delivered pictures intact. The gate is the
tripwire for the cards it cannot reproduce - it fires on the next
engine change that moves a card below the floor, and it fires per card,
naming the card and the measured IoU.

### The two samples are two denominators, and they are not combined

| Sample | Cards | Native within 0.90 | Mean IoU | Lowest |
|---|---:|---:|---:|---:|
| evenly spaced, 24 cards | 24 | 24/24 | 0.9438 | 0.9145 |
| evenly spaced, 96 cards | 96 | 96/96 | 0.9415 | 0.9057 |
| the whole population | 1278 | not measured | - | - |

The population was NOT measured end to end: 1278 native renders is
about 90 minutes of machine time, and the 96-card sample is evenly
spaced over the whole set (the cards were shipped over five weeks by
three different engines), so it spans the population rather than one
batch of it. The one card in the 24 that the reference engine cannot
reproduce was found in the 24-card sample; the 96-card reference run
was not collected (see *Left undone*).

## The reference engine, and what the difference is

The same 24 cards, drawn by the reference engine (Remotion's
`SubtitleOverlay`, as the repository draws it today) and compared with
the same shipped artefacts:

| Engine | Within 0.90 | Mean IoU | Lowest |
|---|---:|---:|---:|
| native (`ren-graphics-fframes`) | **24/24** | **0.9438** | **0.9145** |
| reference (Remotion today) | 23/24 | 0.9184 | 0.6812 |

Native against the reference engine, card by card: mean **0.9318**,
lowest **0.6776** - and that lowest card is the same one the reference
engine cannot reproduce against the shipped artefact. So the two
engines disagree about ONE card in the sample, and on that card the
**shipped artefact agrees with the native renderer** (0.93) and not
with today's browser (0.68).

The reason is a change in the browser, not in the native renderer: the
word-gap rule in
`remotion-subtitles/src/compositions/SubtitleOverlay/AnimatedWord.tsx`
(the painted-gap margin, added 2026-10-17) spaces words by their INK,
while the native renderer spaces them by their advance box at 0.24em.
On a card with several words that is a couple of pixels per gap.

**A change to the native renderer that closed that gap was measured and
REVERTED.** Adopting the browser's painted-gap rule in the IR lowering
(`library/graphics_ir/lowering.py`) moved the native renderer from
0.9438 to 0.9135 mean IoU against the shipped artefacts on the 24-card
sample, and from 24/24 to 20/24 within the floor - because 812 of the
1278 shipped cards were drawn by the native renderer itself, with the
0.24em spacing. The shipped pictures are the yardstick, and the change
was a regression against them. The two engines are therefore **not
interchangeable** today: they differ by about 0.93 IoU on a
multi-word card, and the gate is what keeps that difference from
reaching a delivered video.

## Cost

Per card, on the same machine, the same 24 cards, the same props, with
the machine otherwise idle:

| | Mean per card | Median per card |
|---|---:|---:|
| native renderer | **4.32 s** | **3.80 s** |
| reference engine (Remotion) | 7.42 s | 6.20 s |
| the parity gate's comparison | 0.22 s | - |
| **after: native + gate** | **4.54 s** | **4.02 s** |

So the caption layer on the native renderer behind the gate costs
**4.54 s a card against the reference engine's 7.42 s** - 1.63x faster
per card, and 1.72x faster before the gate is counted. The gate's own
cost is one alpha-plane decode of each artefact (0.22 s a card), read
through `alphaextract` so that a comparison moves one byte per pixel
instead of four: reading the whole RGBA frame costs 0.90 s a card for
the same measure.

The lane's 1.33x claim is met and exceeded on this machine.

## Left undone, measured

* **The 96-card reference run was not collected.** macOS revoked this
  process tree's access to `~/Documents` while the run was in flight, so
  the captain's project could no longer be read and the reference
  engine's 96 cards could not be drawn against it. The reference
  comparison therefore stands on the 24-card sample, and the 96-card
  sample carries the native parity only. The numbers above are what was
  measured; nothing was estimated to fill the gap.
* **The population was not measured end to end** (1278 cards, about 90
  minutes of renders). The 96-card sample is evenly spaced over it.
* **A first draw has nothing to compare against.** The gate protects
  the artefact that shipped; a card the project has never shipped is
  published as drawn, because there is no delivered picture to protect.
  Catching a first draw that differs would need the reference engine to
  draw every card too, which is the cost the lane is trying to remove.
* **The two engines still disagree on the word gap** (about 0.93 IoU on
  a multi-word card), and the change that would close it was measured
  and reverted as a regression against the shipped pictures. Closing it
  the other way - changing the browser to the advance-box spacing -
  would make the browser match 812 shipped cards and is a separate
  change with its own measurement.
