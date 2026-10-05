# The LAION aesthetic frame ranker

Tests: `tests/unit/picture/test_picture_quality.py` (the two bounds, stub
scorers) and `tests/qualification/test_frame_ranker_real.py` (the real
weights over a synthetic clip).

FIRSTMATE VERDICT 2026-09-18 over the spike's "do not adopt": pairs 1 and
3 show a clear win in the same direction, and what the ranker replaces is
a systematically bad rule (about one second into a clip, reliably
mid-movement) rather than a good one.  The spike's measurements all stand
- these tests reuse its own numbers, never re-derived ones:

- blur-blindness: Laplacian variance 65 outscored a sharp pipeline pick
  4.55 to 4.09 - so composition alone must never choose a frame;
- hold-point noise: +0.04 over the current hold, preferring the softer
  frame - so the ranker stays out of the ending-freeze path entirely.

## The real pass, 2026-10-04 (the backbone caveat closed)

The spike scored HF `openai/clip-vit-large-patch14`; the head was trained
on OpenCLIP laion2b embeddings.  The adoption verdict rested on pairs the
spike produced, so the question was whether the win survives the backbone
that will actually run.  `extract_ranked_clip_thumbnail` was run over the
geo-podcast clips with BOTH backbones, and the pairs were re-judged blind
(sides randomised, filenames neutral, key read only after deciding).

The shipped code runs `CLIP_MODEL_ID = "openai/clip-vit-large-patch14"` -
the same backbone the spike used - so the thing judged was already the
thing shipping; the laion2b run is the belt-and-braces check the spike's
report section 6 asked for.

| clip | backbone | legacy t1.0 | ranker pick | score delta | ranker wins blind? |
|---|---|---|---|---|---|
| B (LC4930) | shipped | 3.764 @ 335 | 5.031 @ 429 (t56.6) | +1.27 | yes - settled, square to lens vs mid-gesture |
| C (LCATL0011) | shipped | 4.248 @ 331 | 4.625 @ 419 (t134.0) | +0.38 | yes (near-tie; darker angle, as the spike measured) |
| D (LC4931) | shipped | 4.488 @ 437 | 5.094 @ 452 (t143.8) | +0.61 | yes - settled, hands clasped vs looking down |
| A (LC4932 window) | shipped | 3.813 @ 370 | 4.544 @ 392 (t32.4) | +0.73 | informational - not wired into the hold path |
| B | laion2b | 5.064 @ 335 | 5.363 @ 407 (t196.7) | +0.30 | yes - same direction |
| C | laion2b | 4.240 @ 331 | 4.799 @ 419 (t134.0) | +0.56 | no - near-tie, judge picked the legacy side |
| D | laion2b | 4.583 @ 437 | 5.234 @ 371 (t108.0) | +0.65 | yes - smiling, engaged vs looking down |
| A | laion2b | 5.350 @ 370 | 5.387 @ 403 (t6.9) | +0.04 | informational - noise, as the spike measured |

Verdict: the win condition (ranker's side wins both pairs 1 and 3) holds
on BOTH backbones.  The ranker's pick is visibly better - settled, square
to the lens, composed - where the legacy timestamp is reliably
mid-gesture.  The caveat the lane surfaced is closed: the adoption does
not depend on the backbone substitution.  The laion2b scores run higher
(the head was trained on that distribution) but the direction is the same.

The sharpness gate did not have to reject a candidate on this footage (all
9 candidates per clip read above the 80.0 floor - good podcast video);
its teeth are the unit test where 4.55 @ 65 loses to 4.09 @ 322.  Every
winner was measured sharp, and the ranker changed the pick every time
(winner is never the legacy timestamp).

Repro: `aesthetic_spike.py --score` (spike dir) for the raw ranking;
`frame_ranker.extract_ranked_clip_thumbnail` with a loaded
`LaionAestheticScorer` for the production path.  1.6 GB weights,
~0.5-1.7 s/frame CPU depending on machine load.
