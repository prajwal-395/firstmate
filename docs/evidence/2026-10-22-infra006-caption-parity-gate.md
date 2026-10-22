# INFRA-006 lane C - the caption layer had no parity gate

**Measured on:** 2026-10-22, macOS (Apple M5), repository at `f7e6f948e`.
**The incident:** the caption layer draws through the selected graphics
engine, the native renderer (`ren-graphics-fframes`) is the default in
both editions, and the engine is part of the caption render's reuse key
(`library/steps/step_4_05_render_subtitles/step.py`, `_reuse_key`).
Switching engines therefore re-renders every card **over** the artefact
the project shipped - the filename carries the drawing digest, not the
engine - and NOTHING compared the two pictures.

## What that meant

A card re-rendered by a different engine replaced the delivered picture
without a word in the run's record. The two engines do not draw the
same picture: measured on the geo-podcast project's own 24 shipped
caption cards, the native renderer and the browser's `SubtitleOverlay`
agree at a mean ink IoU of **0.9318**, and on one card in that sample
at **0.6776** - a card where the browser now spaces words by their
painted ink and the native renderer spaces them by their advance box.

The lane's own direction - "give the caption layer to the native
renderer" - is safe on this population, and the measurement says so:
the native renderer reproduces **24 of 24** sampled shipped captions
within the ink IoU floor of 0.90, and **96 of 96** on a wider evenly
spaced sample. The browser, today, reproduces **23 of 24**: the one card
it cannot reproduce is a card the native renderer reproduces at 0.93,
because the browser's word-gap rule changed on 2026-10-17, after that
card shipped.

So the native renderer may take the layer. What it could not do is take
the layer **without a check**: the moment an engine, a font or a
renderer version changes underneath a card, the delivered picture
changes with nothing to say so.

## The rule

**A caption card may not change the picture the project shipped.**

The gate is `library/tools/caption_parity_gate.py`, and it is the same
measure the motion-graphics parity lane used
(`tests/unit/graphics_ir/test_native_render_parity.py`): ink IoU per
frame (alpha > 16), averaged over the card, floor **0.90**.

* the card is drawn to a **candidate** path - the artefact that shipped
  is never at risk, not even for the second between the render and the
  comparison;
* with no artefact on disk there is nothing to protect, and the card is
  published as drawn;
* with an artefact on disk the two are compared, and a card **below the
  floor is refused**: the candidate is discarded, the shipped artefact
  stays, and the refusal is said out loud and recorded in the segment's
  entry (`parity_gate`) and in the render ledger
  (`library/tools/caption_asset_gc.py`).

Fail-closed. A card whose drawing content changed - a caption rewrite -
has a different content digest and therefore a different name, so
nothing stands in the way of a caption the captain asked to change.

## What it costs

Per card, same machine, same cards: native **4.32 s**, reference engine
**7.42 s**, the gate's comparison **0.22 s** - **4.54 s a card against
the browser's 7.42 s**, 1.63x faster per card, and 1.72x before the
gate is counted.

## The measurement, and what it does not cover

* The population is 1278 shipped cards; the parity was measured on an
  evenly spaced sample of 96 (96/96 within the floor, mean 0.9415) and
  on 24 cards drawn by BOTH engines (24/24 native, 23/24 reference).
  The population was not measured end to end.
* The 96-card reference run was not collected: macOS revoked this
  process tree's access to `~/Documents` mid-run, so the captain's
  project could no longer be read. The reference comparison stands on
  the 24-card sample.
* A first draw has no shipped artefact to compare against, so the gate
  cannot protect it. Catching one would need the reference engine to
  draw every card as well - the cost the lane exists to remove.
* The native renderer and the browser still disagree on the word gap
  (0.9318 mean IoU on the 24-card sample). The change that closed the
  gap in the native renderer was measured (0.9438 -> 0.9135 mean, 24/24
  -> 20/24 within the floor against the shipped artefacts) and
  **reverted as a regression**: 812 of the 1278 shipped cards were
  drawn by the native renderer itself.

## Where the numbers live

* the measurement: `docs/benchmarks/2026-10-22-infra006-caption-parity.md`
* the record: `tests/fixtures/native_caption_parity/cards.json`
* the bench: `docs/benchmarks/2026-10-22-infra006-caption-parity.py`
* the gate: `library/tools/caption_parity_gate.py`
* the tests: `tests/unit/captions/test_caption_parity_gate.py`,
  `tests/unit/captions/test_native_caption_parity.py`
